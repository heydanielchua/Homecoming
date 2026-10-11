"""Backtest the growth score before trusting it.

The PRD is explicit: score every block using only data up to 2019, then check
whether the high scorers outperformed by 2024. If they did not, the weights
change before the score is used for a real decision.

Two signals are switched off for the backtest, because using them would be
cheating with hindsight: the future-MRT table lists stations announced by
today, not by 2019, and the supply signal is read off block completion years
as they stand now. The backtest therefore grades momentum, catch-up gap and
lease runway — the signals that can be computed honestly from 2019 data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .growth import GrowthScorer
from .metrics import GROWTH_WINDOW_MONTHS, month_plus
from .stats import cagr, median

HINDSIGHT_SIGNALS = ("new_transport", "nearby_supply", "planned_development")


@dataclass
class Quintile:
    rank: int
    n: int
    median_score: float | None
    median_growth: float | None


@dataclass
class BacktestResult:
    as_of: str
    horizon_years: int
    n_blocks: int
    quintiles: list[Quintile] = field(default_factory=list)
    passed: bool | None = None
    message: str = ""
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "horizon_years": self.horizon_years,
            "n_blocks": self.n_blocks,
            "quintiles": [vars(q) for q in self.quintiles],
            "passed": self.passed,
            "message": self.message,
            "weights": self.weights,
        }


def realised_growth(
    conn: sqlite3.Connection,
    block_key: str,
    flat_type: str,
    as_of: str,
    horizon_years: int,
) -> float | None:
    """Actual growth in a block's price per sqm, measured strictly after the cutoff.

    Both windows sit after `as_of`, and neither overlaps the window the score
    was computed from. That matters more than it looks: when the outcome was
    measured from the same sales the score had seen, the noise in those sales
    was in the score's numerator and the outcome's denominator at once, which
    produced a confident negative correlation out of nothing at all.

    With a five-year horizon the two windows are 2020-21 and 2023-24, so the
    rate returned is over the three years between their midpoints.
    """
    from .metrics import MixAdjuster, _window_median_psm

    span_years = horizon_years - GROWTH_WINDOW_MONTHS // 12
    if span_years < 1:
        return None

    base_start = month_plus(as_of, 1)
    base_end = month_plus(as_of, GROWTH_WINDOW_MONTHS)
    end_month = month_plus(as_of, horizon_years * 12)
    end_start = month_plus(end_month, -GROWTH_WINDOW_MONTHS + 1)

    adjuster = MixAdjuster(conn)
    where = "block_key = ? AND flat_type = ?"
    params = (block_key, flat_type)
    before = _window_median_psm(conn, where, params, base_start, base_end, adjuster)
    after = _window_median_psm(conn, where, params, end_start, end_month, adjuster)
    return cagr(before, after, span_years)


def run_backtest(
    conn: sqlite3.Connection,
    as_of: str = "2019-12",
    horizon_years: int = 5,
    flat_types: list[str] | None = None,
    min_blocks: int = 25,
    weights: dict[str, float] | None = None,
) -> BacktestResult:
    """Score blocks as of `as_of`, then grade them on what happened next.

    `weights` tries a different weighting against history, which is what the
    PRD asks for when the score does not separate winners: change the weights
    and re-run, rather than use a score that has not earned it.
    """
    from .metrics import compute_block_metrics

    cfg = load_config()
    weights = {
        key: (0.0 if key in HINDSIGHT_SIGNALS else value)
        for key, value in (weights or cfg.growth_weights).items()
    }

    metric_rows = compute_block_metrics(conn, flat_types=flat_types, as_of=as_of, store=False)
    if not metric_rows:
        return BacktestResult(
            as_of=as_of, horizon_years=horizon_years, n_blocks=0, passed=None,
            weights=weights,
            message=f"no transactions on or before {as_of}, so there is nothing to backtest",
        )

    scorer = GrowthScorer(conn, weights=weights, as_of_year=int(as_of[:4]))
    scored = scorer.score(metric_rows)

    paired: list[tuple[float, float]] = []
    for growth in scored:
        if growth.growth_score is None:
            continue
        outcome = realised_growth(
            conn, growth.block_key, growth.flat_type, as_of, horizon_years
        )
        if outcome is None:
            continue
        paired.append((growth.growth_score, outcome))

    if len(paired) < min_blocks:
        return BacktestResult(
            as_of=as_of, horizon_years=horizon_years, n_blocks=len(paired), passed=None,
            weights=weights,
            message=(
                f"only {len(paired)} block(s) have both a {as_of} score and "
                f"{horizon_years}-year outcome; need {min_blocks} before the result "
                "means anything"
            ),
        )

    paired.sort(key=lambda pair: pair[0])
    size = len(paired) / 5.0
    quintiles: list[Quintile] = []
    for i in range(5):
        chunk = paired[int(i * size): int((i + 1) * size)]
        quintiles.append(
            Quintile(
                rank=i + 1,
                n=len(chunk),
                median_score=median([s for s, _ in chunk]),
                median_growth=median([g for _, g in chunk]),
            )
        )

    bottom, top = quintiles[0].median_growth, quintiles[-1].median_growth
    passed = None
    if bottom is not None and top is not None:
        passed = top > bottom
        gap = (top - bottom) * 100
        message = (
            f"top quintile grew {top * 100:.2f}%/yr against {bottom * 100:.2f}%/yr for the "
            f"bottom ({gap:+.2f} points). "
            + ("The score ranks better than chance on this window."
               if passed else
               "The score did NOT separate winners here: revise the weights before "
               "using it for a real decision.")
        )
    else:
        message = "not enough outcome data in the end quintiles"

    return BacktestResult(
        as_of=as_of, horizon_years=horizon_years, n_blocks=len(paired),
        quintiles=quintiles, passed=passed, message=message, weights=weights,
    )
