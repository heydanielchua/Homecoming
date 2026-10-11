"""F4. Growth score, value at exit, and the retirement rental check.

A flat's value moves with the whole market, gains from location upgrades,
and loses ground as its lease runs down. Nothing here forecasts a price. Each
signal is turned into a percentile against the other candidate blocks, the
percentiles are weighted into a 0-100 score, and the score is shown with
every input and its source next to it.

    growth score = sum over signals of weight * percentile(signal)

    value at exit = fair value today
                    * (1 + g_town) ** N
                    * lease factor at exit / lease factor today
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import load_config
from .db import upsert_many
from .fit import lease_factor, load_lease_factors
from .geo import haversine_m
from .norm import remaining_lease_months
from .stats import median, percentile_rank

SIGNAL_SOURCES = {
    "price_momentum": "Resale transactions + HDB Resale Price Index",
    "catch_up_gap": "Resale transactions (block vs town median price per sqm)",
    "lease_runway": "Resale transactions (lease commencement)",
    "new_transport": "LTA upcoming rail (config/future_mrt.yml) + OneMap geocode",
    "nearby_supply": "HDB property information (year completed)",
    "planned_development": "URA Master Plan land use (later phase)",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Signal:
    """One growth signal for one block: its raw value and how it scored."""

    key: str
    value: float | None
    unit: str
    label: str
    source: str
    percentile: float | None = None
    weight: float = 0.0
    available: bool = True
    note: str | None = None

    @property
    def contribution(self) -> float:
        if self.percentile is None:
            return 0.0
        return self.weight * self.percentile


@dataclass
class BlockGrowth:
    block_key: str
    flat_type: str
    growth_score: float | None
    signals: list[Signal] = field(default_factory=list)
    exit_low: float | None = None
    exit_mid: float | None = None
    exit_high: float | None = None
    exit_lease_years: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "signals": [asdict(s) | {"contribution": s.contribution} for s in self.signals],
                "notes": self.notes,
                "exit_lease_years": self.exit_lease_years,
            }
        )


class GrowthScorer:
    """Scores a set of candidate blocks against each other.

    The percentiles are taken over the candidate set, as the PRD's formula
    says ("over all blocks in budget"), so a score answers "better than the
    other things we could buy", not "good in the abstract".
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        weights: dict[str, float] | None = None,
        as_of_year: int | None = None,
    ) -> None:
        self.conn = conn
        cfg = load_config()
        self.cfg = cfg
        # `weights` lets the backtest switch off signals that did not exist in
        # the past; `as_of_year` keeps the supply signal out of the future.
        self.weights = dict(weights) if weights is not None else cfg.growth_weights
        self.params = cfg.growth
        self.holding_years = cfg.holding_years
        self.as_of_year = as_of_year or datetime.now(timezone.utc).year
        self.purchase_year = int(cfg.household["purchase_year"])
        self.lease_factors = load_lease_factors(conn)
        self.future_stations = conn.execute(
            "SELECT name, line, expected_year, lat, lon FROM mrt_station"
            " WHERE status IN ('announced', 'under_construction')"
            "   AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
        self.blocks = {
            row["block_key"]: row
            for row in conn.execute(
                "SELECT block_key, town, lat, lon, year_completed, total_dwelling_units"
                " FROM block"
            )
        }
        self.town_supply = self._town_supply()

    # -- supply ------------------------------------------------------------
    def _town_supply(self) -> dict[str, float]:
        """Share of a town's flats reaching MOP in the next three years.

        A block reaching MOP adds resale supply. MOP is five years after
        completion, so blocks completed in the last two to five years are the
        ones about to compete with us. The PRD marks this signal as "to
        verify"; it is an approximation from completion years, not an HDB
        figure.
        """
        year = self.as_of_year
        mop_years = int(self.cfg.policy["mop"]["standard_years"])
        rows = self.conn.execute(
            """
            SELECT town,
                   SUM(total_dwelling_units) AS total,
                   SUM(CASE WHEN year_completed BETWEEN ? AND ?
                            THEN total_dwelling_units ELSE 0 END) AS reaching_mop
            FROM block
            WHERE town IS NOT NULL AND town <> '' AND total_dwelling_units > 0
            GROUP BY town
            """,
            (year - mop_years, year - mop_years + 3),
        ).fetchall()
        return {
            row["town"]: (row["reaching_mop"] / row["total"]) if row["total"] else 0.0
            for row in rows
        }

    # -- individual signals ------------------------------------------------
    def _momentum(self, metric: sqlite3.Row) -> Signal:
        """Block growth over and above the market, blending 5- and 10-year."""
        rpi = metric["rpi_cagr_10y"]
        parts = []
        for years in self.params["momentum_years"]:
            block_cagr = metric[f"cagr_{years}y"]
            if block_cagr is None:
                continue
            benchmark = rpi if rpi is not None else (metric["town_cagr_10y"] or 0.0)
            parts.append(block_cagr - (benchmark or 0.0))
        value = median(parts) if parts else None
        return Signal(
            key="price_momentum",
            value=round(value, 5) if value is not None else None,
            unit="annual growth above the index",
            label="Price momentum",
            source=SIGNAL_SOURCES["price_momentum"],
            weight=self.weights.get("price_momentum", 0),
            available=value is not None,
            note=None if value is not None else "not enough history in this block",
        )

    def _catch_up(self, metric: sqlite3.Row) -> Signal:
        """How far below its town this block prices, for the same flat type."""
        block_psm, town_psm = metric["median_psm_24m"], metric["town_median_psm"]
        value = None
        if block_psm and town_psm:
            value = (town_psm - block_psm) / town_psm
        return Signal(
            key="catch_up_gap",
            value=round(value, 5) if value is not None else None,
            unit="below town median price per sqm",
            label="Catch-up gap",
            source=SIGNAL_SOURCES["catch_up_gap"],
            weight=self.weights.get("catch_up_gap", 0),
            available=value is not None,
            note=None if value is not None else "no town median for this flat type",
        )

    def exit_lease_years(self, metric: sqlite3.Row) -> float | None:
        """Years of lease left when we leave, after the holding period."""
        months = metric["median_lease_months"]
        if months is None:
            return None
        # Lease observed at the time of those sales, carried to purchase, then
        # to the end of the holding period.
        last_month = metric["last_txn_month"] or f"{self.purchase_year}-01"
        years_to_purchase = max(0, self.purchase_year - int(last_month[:4]))
        return months / 12 - years_to_purchase - self.holding_years

    def _lease_runway(self, metric: sqlite3.Row) -> Signal:
        value = self.exit_lease_years(metric)
        minimum = self.cfg.flags["lease"]["min_years_left_at_exit"]
        return Signal(
            key="lease_runway",
            value=round(value, 1) if value is not None else None,
            unit=f"years of lease left at exit (want {minimum}+)",
            label="Lease runway",
            source=SIGNAL_SOURCES["lease_runway"],
            weight=self.weights.get("lease_runway", 0),
            available=value is not None,
            note=None if value is not None else "no lease information for this block",
        )

    def _new_transport(self, block_key: str) -> Signal:
        """Walk to an announced or under-construction station, sooner is better.

        Score falls with distance and with how far off the opening is.
        """
        block = self.blocks.get(block_key)
        weight = self.weights.get("new_transport", 0)
        if not self.future_stations:
            return Signal(
                key="new_transport", value=None, unit="",
                label="New transport", source=SIGNAL_SOURCES["new_transport"],
                weight=weight, available=False,
                note="config/future_mrt.yml has no stations yet, so this signal is unused",
            )
        if not block or block["lat"] is None:
            return Signal(
                key="new_transport", value=None, unit="m to a future station",
                label="New transport", source=SIGNAL_SOURCES["new_transport"],
                weight=weight, available=False, note="block is not geocoded",
            )

        walk_limit = float(self.params["new_mrt_walk_m"])
        this_year = self.as_of_year
        best = 0.0
        best_station = None
        for station in self.future_stations:
            distance = haversine_m(block["lat"], block["lon"], station["lat"], station["lon"])
            if distance > walk_limit:
                continue
            closeness = 1.0 - distance / walk_limit
            years_away = max(0, (station["expected_year"] or this_year) - this_year)
            soonness = 1.0 / (1.0 + years_away / 5.0)
            score = closeness * soonness
            if score > best:
                best, best_station = score, station
        note = (
            f"{best_station['name']} ({best_station['line']}, "
            f"~{best_station['expected_year']})" if best_station else
            f"no announced station within {int(walk_limit)} m"
        )
        return Signal(
            key="new_transport", value=round(best, 4), unit="0-1, nearer and sooner is higher",
            label="New transport", source=SIGNAL_SOURCES["new_transport"],
            weight=weight, available=True, note=note,
        )

    def _nearby_supply(self, block_key: str) -> Signal:
        """More flats reaching MOP nearby means more competition when we sell."""
        block = self.blocks.get(block_key)
        town = block["town"] if block else None
        share = self.town_supply.get(town) if town else None
        return Signal(
            key="nearby_supply",
            # Negated, so that a higher value is better, like every other signal.
            value=round(-share, 5) if share is not None else None,
            unit="share of town flats reaching MOP in 3 years (negated)",
            label="Nearby supply",
            source=SIGNAL_SOURCES["nearby_supply"],
            weight=self.weights.get("nearby_supply", 0),
            available=share is not None,
            note="approximated from block completion years; to verify against HDB",
        )

    def _planned_development(self, block_key: str) -> Signal:
        return Signal(
            key="planned_development", value=None, unit="",
            label="Planned development", source=SIGNAL_SOURCES["planned_development"],
            weight=self.weights.get("planned_development", 0), available=False,
            note="URA Master Plan layer is a later phase; weight is 0 until it is in",
        )

    # -- scoring -----------------------------------------------------------
    def score(self, metrics: list[sqlite3.Row]) -> list[BlockGrowth]:
        """Score every candidate block against the others in the set."""
        raw: list[tuple[sqlite3.Row, list[Signal]]] = []
        for metric in metrics:
            key = metric["block_key"]
            raw.append((metric, [
                self._momentum(metric),
                self._catch_up(metric),
                self._lease_runway(metric),
                self._new_transport(key),
                self._nearby_supply(key),
                self._planned_development(key),
            ]))

        # Percentile each signal across the candidate set.
        pools: dict[str, list[float]] = {}
        for _, signals in raw:
            for signal in signals:
                if signal.available and signal.value is not None:
                    pools.setdefault(signal.key, []).append(signal.value)

        results: list[BlockGrowth] = []
        for metric, signals in raw:
            notes: list[str] = []
            weight_used = 0.0
            contribution = 0.0
            for signal in signals:
                pool = pools.get(signal.key) or []
                if signal.available and signal.value is not None and pool:
                    signal.percentile = round(percentile_rank(pool, signal.value), 4)
                    weight_used += signal.weight
                    contribution += signal.contribution
                elif signal.weight:
                    notes.append(f"{signal.label} unavailable: {signal.note}")

            # Re-base on the weight actually available, so a missing signal
            # lowers confidence rather than silently lowering the score.
            score = round(100.0 * contribution / weight_used, 1) if weight_used else None
            growth = BlockGrowth(
                block_key=metric["block_key"],
                flat_type=metric["flat_type"],
                growth_score=score,
                signals=signals,
                notes=notes,
            )
            growth.exit_lease_years = self.exit_lease_years(metric)
            results.append(growth)
        return results

    # -- value at exit -----------------------------------------------------
    def value_at_exit(
        self,
        fair_value_today: float | None,
        metric: sqlite3.Row,
        flat_type: str,
    ) -> tuple[float | None, float | None, float | None]:
        """Low / mid / high value at the end of the holding period.

        Three cases, as the PRD asks: the town's own 10-year rate, a flat
        market, and a downside. Lease decay is applied in all three from the
        fitted lease factor table.
        """
        if not fair_value_today:
            return (None, None, None)
        n = self.holding_years
        lease_now = metric["median_lease_months"]
        lease_exit = None if lease_now is None else max(0, lease_now - n * 12)
        factor_now = lease_factor(self.lease_factors, flat_type, lease_now)
        factor_exit = lease_factor(self.lease_factors, flat_type, lease_exit)
        lease_ratio = (factor_exit / factor_now) if (factor_now and factor_exit) else 1.0

        g_town = metric["town_cagr_10y"]
        cases = [
            float(self.params["downside_annual_growth"]),
            float(self.params["flat_annual_growth"]),
            g_town if g_town is not None else float(self.params["flat_annual_growth"]),
        ]
        values = sorted(
            round(fair_value_today * (1 + g) ** n * lease_ratio, -2) for g in cases
        )
        return (values[0], values[1], values[2])


def score_all(conn: sqlite3.Connection, metrics: list[sqlite3.Row] | None = None) -> int:
    """Score every block in block_metric and store the result."""
    if metrics is None:
        metrics = conn.execute("SELECT * FROM block_metric").fetchall()
    if not metrics:
        return 0

    scorer = GrowthScorer(conn)
    results = scorer.score(metrics)
    by_key = {(m["block_key"], m["flat_type"]): m for m in metrics}

    from .fairvalue import Valuer

    valuer = Valuer(conn)
    rows = []
    computed_at = _now()
    for growth in results:
        metric = by_key[(growth.block_key, growth.flat_type)]
        estimate = valuer.estimate(growth.block_key, growth.flat_type)
        low, mid, high = scorer.value_at_exit(estimate.mid, metric, growth.flat_type)
        growth.exit_low, growth.exit_mid, growth.exit_high = low, mid, high
        rows.append((
            growth.block_key, growth.flat_type, growth.growth_score,
            growth.to_json(), low, mid, high, computed_at,
        ))

    with conn:
        conn.execute("DELETE FROM block_score")
        upsert_many(
            conn, "block_score",
            ("block_key", "flat_type", "growth_score", "signals_json",
             "exit_low", "exit_mid", "exit_high", "computed_at"),
            rows, conflict="REPLACE",
        )
    return len(rows)


def retirement_rent_check(
    conn: sqlite3.Connection, block_key: str, flat_type: str
) -> dict[str, Any]:
    """Today's median whole-flat rent nearby, and the lease left when we let it.

    A sanity check that the flat stays rentable when we retire overseas, not
    a yield target.
    """
    cfg = load_config()
    scope = "block"
    rows = conn.execute(
        "SELECT monthly_rent FROM rental_txn WHERE block_key = ? AND flat_type = ?"
        " AND month >= (SELECT MAX(month) FROM rental_txn)",
        (block_key, flat_type),
    ).fetchall()
    if len(rows) < 3:
        block = conn.execute(
            "SELECT town FROM block WHERE block_key = ?", (block_key,)
        ).fetchone()
        if block and block["town"]:
            scope = "town"
            rows = conn.execute(
                "SELECT monthly_rent FROM rental_txn WHERE town = ? AND flat_type = ?",
                (block["town"], flat_type),
            ).fetchall()

    lease_year = conn.execute(
        "SELECT lease_commence_year FROM resale_txn WHERE block_key = ?"
        " AND lease_commence_year IS NOT NULL LIMIT 1",
        (block_key,),
    ).fetchone()
    exit_year = int(cfg.household["purchase_year"]) + cfg.holding_years
    lease_at_exit = (
        remaining_lease_months(lease_year["lease_commence_year"], f"{exit_year}-01")
        if lease_year else None
    )
    policy = cfg.policy["whole_flat_rental"]
    return {
        "median_rent": median([r["monthly_rent"] for r in rows]),
        "n": len(rows),
        "scope": scope if rows else "none",
        "lease_years_at_let": round(lease_at_exit / 12, 1) if lease_at_exit else None,
        "let_from_year": exit_year,
        "citizens_only": policy["citizens_only"],
        "quota_note": (
            "A non-citizen tenant quota applies per block and has no open API: "
            "check it by hand before counting on this."
            if policy.get("non_citizen_quota_per_block") else None
        ),
    }
