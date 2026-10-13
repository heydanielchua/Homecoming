"""F5. Weighted ranking.

Every candidate is scored on price, lease, growth, commute and amenities, each
weighted by a slider we control, and every input is carried along so a rank
can be opened up and argued with.

Four of the five sub-scores are measured on fixed scales from
config/criteria.yml — $510k is a good price whatever else is listed, 35
minutes is the edge of acceptable whoever else is nearer. Only the growth
score is relative, because the PRD defines it that way: a percentile over the
blocks in budget. The effect is that filtering the list does not reshuffle
it, and two flats of different types are judged on the same scale rather than
3-room flats winning the price column by being 3-room flats.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .criteria import Fit, evaluate
from .geo import haversine_m
from .norm import canon_flat_type, flat_type_rooms
from .stats import linear_score

SUB_SCORE_LABELS = {
    "price": "Price",
    "lease": "Lease",
    "growth": "Growth",
    "commute": "Commute",
    "amenities": "Amenities",
}


@dataclass
class SubScore:
    key: str
    label: str
    value: float | None
    unit: str
    percentile: float | None = None
    weight: float = 0.0
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.percentile is not None


@dataclass
class Candidate:
    block_key: str
    block: str
    street_name: str
    town: str | None
    flat_type: str
    lat: float | None = None
    lon: float | None = None
    max_floor_lvl: int | None = None
    n_txn_24m: int = 0
    last_txn_month: str | None = None
    median_price_24m: float | None = None
    min_price_24m: float | None = None
    median_psm_24m: float | None = None
    median_sqm: float | None = None
    town_median_psm: float | None = None
    lease_years_now: float | None = None
    lease_years_at_exit: float | None = None
    growth_score: float | None = None
    growth_signals: dict[str, Any] = field(default_factory=dict)
    exit_low: float | None = None
    exit_mid: float | None = None
    exit_high: float | None = None
    drive_minutes: float | None = None
    commute_status: str | None = None
    commute_by_office: dict[str, Any] = field(default_factory=dict)
    nearest_mrt: str | None = None
    nearest_mrt_m: float | None = None
    sub_scores: list[SubScore] = field(default_factory=list)
    rank_score: float | None = None
    fit: Fit | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            k: v for k, v in vars(self).items()
            if k not in ("sub_scores", "fit", "growth_signals")
        }
        data["sub_scores"] = [vars(s) for s in self.sub_scores]
        data["fit"] = self.fit.to_dict() if self.fit else None
        data["growth_signals"] = self.growth_signals
        return data


def _mrt_stations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, line, lat, lon FROM mrt_station WHERE status = 'open'"
        " AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()


def load_candidates(
    conn: sqlite3.Connection,
    max_price: float | None = None,
    min_rooms: int | None = None,
    towns: list[str] | None = None,
    flat_types: list[str] | None = None,
    block_keys: list[str] | None = None,
) -> list[Candidate]:
    """Every block-and-flat-type that has transacted recently, with its data.

    `max_price` is applied to the cheapest recent sale in the block, which is
    the PRD's question: where has this flat type actually transacted under our
    budget? It is a wide net, not a hard cap — the price criterion is what
    flags a candidate as over budget.

    `block_keys` narrows the load to named blocks, for a caller that wants one
    block rather than the list. Every sub-score is on a fixed scale and the
    growth score is read back from the store, so a candidate loaded alone
    scores exactly as it does loaded with the rest.
    """
    cfg = load_config()
    if max_price is None:
        max_price = cfg.budget * (1 + float(cfg.household["budget_over_tolerance_pct"]) / 100.0)
    if min_rooms is None:
        min_rooms = int(cfg.household["min_flat_type_rooms"])

    sql = """
        SELECT bm.*, b.block, b.street_name, b.town, b.lat, b.lon, b.max_floor_lvl,
               bs.growth_score, bs.signals_json, bs.exit_low, bs.exit_mid, bs.exit_high
        FROM block_metric bm
        JOIN block b ON b.block_key = bm.block_key
        LEFT JOIN block_score bs
               ON bs.block_key = bm.block_key AND bs.flat_type = bm.flat_type
        WHERE 1 = 1
    """
    params: list[Any] = []
    if max_price:
        sql += " AND (bm.min_price_24m IS NULL OR bm.min_price_24m <= ?)"
        params.append(max_price)
    if towns:
        sql += f" AND b.town IN ({', '.join('?' for _ in towns)})"
        params.extend(t.upper() for t in towns)
    if flat_types:
        sql += f" AND bm.flat_type IN ({', '.join('?' for _ in flat_types)})"
        params.extend(canon_flat_type(f) for f in flat_types)
    if block_keys:
        sql += f" AND bm.block_key IN ({', '.join('?' for _ in block_keys)})"
        params.extend(block_keys)

    rows = conn.execute(sql, params).fetchall()

    commutes: dict[str, list[sqlite3.Row]] = {}
    commute_sql = "SELECT * FROM commute"
    commute_params: list[Any] = []
    if block_keys:
        commute_sql += f" WHERE block_key IN ({', '.join('?' for _ in block_keys)})"
        commute_params = list(block_keys)
    for row in conn.execute(commute_sql, commute_params):
        commutes.setdefault(row["block_key"], []).append(row)
    stations = _mrt_stations(conn)
    holding = cfg.holding_years
    purchase_year = int(cfg.household["purchase_year"])

    candidates: list[Candidate] = []
    for row in rows:
        rooms = flat_type_rooms(row["flat_type"])
        if min_rooms and rooms is not None and rooms < min_rooms:
            continue

        lease_months = row["median_lease_months"]
        lease_now = lease_months / 12 if lease_months is not None else None
        lease_exit = None
        if lease_now is not None:
            last_year = int((row["last_txn_month"] or f"{purchase_year}-01")[:4])
            lease_exit = lease_now - max(0, purchase_year - last_year) - holding

        # The commute that matters is the drive to the farther office.
        legs = commutes.get(row["block_key"], [])
        routed = [leg for leg in legs if leg["status"] == "ok" and leg["drive_minutes"]]
        drive = max((leg["drive_minutes"] for leg in routed), default=None)
        status = (
            "ok" if routed and len(routed) == len(legs) and legs
            else (legs[0]["status"] if legs else "not_computed")
        )

        nearest_name, nearest_m = None, None
        if row["lat"] is not None and stations:
            best = min(
                stations,
                key=lambda s: haversine_m(row["lat"], row["lon"], s["lat"], s["lon"]),
            )
            nearest_name = f"{best['name']} ({best['line']})" if best["line"] else best["name"]
            nearest_m = haversine_m(row["lat"], row["lon"], best["lat"], best["lon"])

        candidates.append(Candidate(
            block_key=row["block_key"], block=row["block"], street_name=row["street_name"],
            town=row["town"], flat_type=row["flat_type"], lat=row["lat"], lon=row["lon"],
            max_floor_lvl=row["max_floor_lvl"], n_txn_24m=row["n_txn_24m"],
            last_txn_month=row["last_txn_month"], median_price_24m=row["median_price_24m"],
            min_price_24m=row["min_price_24m"], median_psm_24m=row["median_psm_24m"],
            median_sqm=row["median_sqm"], town_median_psm=row["town_median_psm"],
            lease_years_now=round(lease_now, 1) if lease_now is not None else None,
            lease_years_at_exit=round(lease_exit, 1) if lease_exit is not None else None,
            growth_score=row["growth_score"],
            growth_signals=json.loads(row["signals_json"]) if row["signals_json"] else {},
            exit_low=row["exit_low"], exit_mid=row["exit_mid"], exit_high=row["exit_high"],
            drive_minutes=drive, commute_status=status,
            commute_by_office={
                leg["office_code"]: {
                    "minutes": leg["drive_minutes"],
                    "status": leg["status"],
                    "message": leg["message"],
                } for leg in legs
            },
            nearest_mrt=nearest_name,
            nearest_mrt_m=round(nearest_m) if nearest_m is not None else None,
        ))
    return candidates


def rank(
    candidates: list[Candidate],
    weights: dict[str, float] | None = None,
) -> list[Candidate]:
    """Score and sort candidates. Mutates and returns the same list."""
    cfg = load_config()
    weights = dict(weights) if weights else cfg.weights
    scales = cfg.criteria["rank_scales"]
    budget = cfg.budget
    amenity_limit = float(cfg.growth["amenities_mrt_walk_m"])

    price_scale = scales["price_budget_multiple"]
    price_best = budget * float(price_scale["best"])
    price_worst = budget * float(price_scale["worst"])
    lease_scale = scales["lease_years_at_exit"]
    drive_scale = scales["drive_minutes"]
    mrt_scale = scales["mrt_walk_m"]

    for c in candidates:
        measures = {
            "price": (
                c.median_price_24m,
                f"median recent sale, scored from ${price_best:,.0f} down to "
                f"${price_worst:,.0f}",
                linear_score(c.median_price_24m, price_best, price_worst),
                "no recent sales in this block",
            ),
            "lease": (
                c.lease_years_at_exit,
                f"years of lease left at exit, scored to {lease_scale['best']}",
                linear_score(c.lease_years_at_exit,
                             float(lease_scale["best"]), float(lease_scale["worst"])),
                "no lease information for this block",
            ),
            "growth": (
                c.growth_score,
                "growth score, 0-100, relative to the other blocks in budget",
                (c.growth_score / 100.0) if c.growth_score is not None else None,
                "growth score not computed; run `nhf score`",
            ),
            "commute": (
                c.drive_minutes,
                f"minutes to the farther office, scored from "
                f"{drive_scale['best']} to {drive_scale['worst']}",
                linear_score(c.drive_minutes,
                             float(drive_scale["best"]), float(drive_scale["worst"])),
                "no routed drive time yet",
            ),
            "amenities": (
                c.nearest_mrt_m,
                f"metres to the nearest MRT station, scored from "
                f"{mrt_scale['best']} to {mrt_scale['worst']}",
                linear_score(c.nearest_mrt_m,
                             float(mrt_scale["best"]), float(mrt_scale["worst"])),
                "no MRT station list loaded; run `nhf import-mrt`",
            ),
        }

        sub_scores: list[SubScore] = []
        weight_used = 0.0
        total = 0.0
        for key, (value, unit, score, missing_note) in measures.items():
            weight = float(weights.get(key, 0))
            sub = SubScore(key=key, label=SUB_SCORE_LABELS[key], value=value,
                           unit=unit, weight=weight,
                           note=None if score is not None else missing_note)
            if score is not None:
                sub.percentile = round(score, 4)
                weight_used += weight
                total += weight * score
            sub_scores.append(sub)

        for sub in sub_scores:
            # The price scale deliberately plateaus: below about 85% of budget
            # the difference stops mattering to us, so say that rather than
            # letting a tie look like a coincidence.
            if sub.key == "price" and sub.percentile == 1.0:
                sub.note = f"comfortably within budget (at or under ${price_best:,.0f})"

        if c.nearest_mrt_m is not None and c.nearest_mrt_m > amenity_limit:
            for sub in sub_scores:
                if sub.key == "amenities":
                    sub.note = (
                        f"nearest MRT is {c.nearest_mrt_m:.0f} m away, beyond a "
                        f"{amenity_limit:.0f} m walk"
                    )

        c.sub_scores = sub_scores
        # Re-based on the weight actually available, so a block missing a
        # drive time is not silently punished for it.
        c.rank_score = round(100.0 * total / weight_used, 1) if weight_used else None

    # Ties are common at the top because the price scale plateaus, so break
    # them on price: of two equally good blocks, the cheaper one wins.
    candidates.sort(key=lambda c: (
        c.rank_score is None, -(c.rank_score or 0),
        c.median_price_24m if c.median_price_24m is not None else float("inf"),
    ))
    return candidates


def attach_fit(conn: sqlite3.Connection, candidates: list[Candidate]) -> list[Candidate]:
    """Attach F1 fit badges and red flags to each candidate."""
    for c in candidates:
        c.fit = evaluate(
            block_key=c.block_key,
            town=c.town,
            flat_type=c.flat_type,
            price=c.median_price_24m,
            price_kind="median recent sale",
            storey=None,
            max_floor_lvl=c.max_floor_lvl,
            corner_unit=None,
            drive_minutes=c.drive_minutes,
            commute_status=c.commute_status,
            lease_years_at_exit=c.lease_years_at_exit,
        )
    return candidates


def ranked_list(
    conn: sqlite3.Connection,
    weights: dict[str, float] | None = None,
    **filters: Any,
) -> list[Candidate]:
    """The ranked list the app opens on."""
    candidates = load_candidates(conn, **filters)
    attach_fit(conn, candidates)
    return rank(candidates, weights)
