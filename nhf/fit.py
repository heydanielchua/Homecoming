"""Curves fitted from the transactions themselves, not assumed.

Two lookup tables come out of this:

* lease_factor — value by years of lease left, so the lease decay in the app
  is the decay the market actually shows rather than a straight line;
* storey_premium — value by storey band, which is what lets a top-floor unit
  be compared with a low-floor sale in the same block.

Both are fitted the same way: divide each sale's price per sqm by the median
price per sqm for its town, flat type and quarter, which strips out location
and the market cycle, then take the median of what is left inside each band.

A caveat on the lease curve. HDB towns were built in waves, so lease length
and town are correlated: dividing by a town cohort removes some of the lease
signal along with the location signal. What is left is the spread of lease
lengths *within* a town, which in the real data is wide (a mature town holds
blocks from the 1970s and from the 2010s) but can be narrow in a young one.
The fitted curve is therefore a floor on observed decay, not the whole of it,
and `nhf fit` prints it so it can be eyeballed before it is trusted.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import upsert_many
from .stats import median

LEASE_BAND_YEARS = 5
STOREY_BAND_FLOORS = 5
MIN_BAND_SAMPLES = 20

# A cohort is one town, flat type and quarter. With only a sale or two in it,
# the cohort median is the sale itself and every ratio collapses to 1.0, which
# would quietly flatten both curves. Thin cohorts are left out of the fit.
MIN_COHORT_SAMPLES = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lease_band(months: int | None) -> int | None:
    if months is None:
        return None
    years = months // 12
    return (years // LEASE_BAND_YEARS) * LEASE_BAND_YEARS


def storey_band(low: int | None, high: int | None) -> int | None:
    """Band from the middle of a storey range, keyed by its lower bound."""
    if low is None and high is None:
        return None
    mid = (low + high) / 2 if (low is not None and high is not None) else (low or high)
    return int((max(1, mid) - 1) // STOREY_BAND_FLOORS) * STOREY_BAND_FLOORS + 1


def _relative_sales(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every usable sale with its price per sqm relative to its town cohort.

    The cohort is town + flat type + quarter, so the ratio is free of both
    where the flat is and when it sold.
    """
    return conn.execute(
        """
        WITH usable AS (
            SELECT quarter, town, flat_type, price_per_sqm, remaining_lease_months,
                   storey_low, storey_high
            FROM resale_txn
            WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0
              AND town <> '' AND flat_type <> ''
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY quarter, town, flat_type ORDER BY price_per_sqm
                   ) AS rn,
                   COUNT(*) OVER (PARTITION BY quarter, town, flat_type) AS n
            FROM usable
        ),
        cohort AS (
            SELECT quarter, town, flat_type, AVG(price_per_sqm) AS median_psm,
                   MAX(n) AS cohort_n
            FROM ranked
            WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
            GROUP BY quarter, town, flat_type
            HAVING MAX(n) >= :min_cohort
        )
        SELECT u.flat_type, u.price_per_sqm, u.remaining_lease_months,
               u.storey_low, u.storey_high,
               u.price_per_sqm / c.median_psm AS rel
        FROM usable u
        JOIN cohort c
          ON c.quarter = u.quarter AND c.town = u.town AND c.flat_type = u.flat_type
        WHERE c.median_psm > 0
        """,
        {"min_cohort": MIN_COHORT_SAMPLES},
    ).fetchall()


def fit_lease_factors(conn: sqlite3.Connection) -> int:
    """Fit and store the lease decay table, per flat type and overall.

    Factors are normalised so the longest well-sampled band is 1.0. Only the
    ratio between two bands is ever used, so the choice of reference does not
    affect a valuation.
    """
    buckets: dict[tuple[str, int], list[float]] = {}
    for row in _relative_sales(conn):
        band = lease_band(row["remaining_lease_months"])
        if band is None:
            continue
        buckets.setdefault((row["flat_type"], band), []).append(row["rel"])
        buckets.setdefault(("ALL", band), []).append(row["rel"])

    raw: dict[str, dict[int, tuple[float, int]]] = {}
    for (flat_type, band), values in buckets.items():
        if len(values) < MIN_BAND_SAMPLES:
            continue
        value = median(values)
        if value:
            raw.setdefault(flat_type, {})[band] = (value, len(values))

    rows = []
    fitted_at = _now()
    for flat_type, bands in raw.items():
        reference = bands[max(bands)][0]
        if not reference:
            continue
        # Enforce the one thing we know for certain: a shorter lease is never
        # worth more per sqm than a longer one, all else equal. Without this,
        # a thin band can produce a curve that rises as the lease runs down,
        # which would value an old flat above a new one.
        ceiling = None
        factors: dict[int, tuple[float, int]] = {}
        for band in sorted(bands, reverse=True):
            value, n = bands[band]
            factor = value / reference
            factor = factor if ceiling is None else min(factor, ceiling)
            ceiling = factor
            factors[band] = (factor, n)
        for band, (factor, n) in sorted(factors.items()):
            rows.append((flat_type, band, round(factor, 6), n, fitted_at))

    with conn:
        conn.execute("DELETE FROM lease_factor")
        upsert_many(
            conn, "lease_factor",
            ("flat_type", "lease_band", "factor", "n", "fitted_at"),
            rows, conflict="REPLACE",
        )
    return len(rows)


def fit_storey_premiums(conn: sqlite3.Connection) -> int:
    """Fit and store the storey premium table, after taking lease out.

    Lease is removed first because older blocks are also shorter, so a raw
    storey premium would quietly carry lease decay inside it.
    """
    factors = load_lease_factors(conn)
    buckets: dict[tuple[str, int], list[float]] = {}
    for row in _relative_sales(conn):
        band = storey_band(row["storey_low"], row["storey_high"])
        if band is None:
            continue
        lease_adj = lease_factor(factors, row["flat_type"], row["remaining_lease_months"]) or 1.0
        rel = row["rel"] / lease_adj
        buckets.setdefault((row["flat_type"], band), []).append(rel)
        buckets.setdefault(("ALL", band), []).append(rel)

    raw: dict[str, dict[int, tuple[float, int]]] = {}
    for (flat_type, band), values in buckets.items():
        if len(values) < MIN_BAND_SAMPLES:
            continue
        value = median(values)
        if value:
            raw.setdefault(flat_type, {})[band] = (value, len(values))

    rows = []
    fitted_at = _now()
    for flat_type, bands in raw.items():
        # Normalise on the weighted middle of the block, so a factor reads as
        # "premium over a typical floor".
        reference = median([v for v, _ in bands.values()]) or 1.0
        for band, (value, n) in sorted(bands.items()):
            rows.append((flat_type, band, round(value / reference, 6), n, fitted_at))

    with conn:
        conn.execute("DELETE FROM storey_premium")
        upsert_many(
            conn, "storey_premium",
            ("flat_type", "storey_band", "factor", "n", "fitted_at"),
            rows, conflict="REPLACE",
        )
    return len(rows)


# --- lookups -------------------------------------------------------------
def load_lease_factors(conn: sqlite3.Connection) -> dict[str, dict[int, float]]:
    table: dict[str, dict[int, float]] = {}
    for row in conn.execute("SELECT flat_type, lease_band, factor FROM lease_factor"):
        table.setdefault(row["flat_type"], {})[row["lease_band"]] = row["factor"]
    return table


def load_storey_premiums(conn: sqlite3.Connection) -> dict[str, dict[int, float]]:
    table: dict[str, dict[int, float]] = {}
    for row in conn.execute("SELECT flat_type, storey_band, factor FROM storey_premium"):
        table.setdefault(row["flat_type"], {})[row["storey_band"]] = row["factor"]
    return table


def _nearest_band(bands: dict[int, float], band: int) -> float | None:
    if not bands:
        return None
    if band in bands:
        return bands[band]
    return bands[min(bands, key=lambda b: abs(b - band))]


def _lookup(
    table: dict[str, dict[int, float]], flat_type: str, band: int
) -> float | None:
    """Exact band first, pooled curve before an inexact band.

    A flat-type curve is sparser than the pooled one, so reaching for a
    neighbouring band in it is worse than reading the right band off the
    all-types curve. Both are tried exactly, then both approximately.
    """
    specific = table.get(flat_type) or {}
    pooled = table.get("ALL") or {}
    if band in specific:
        return specific[band]
    if band in pooled:
        return pooled[band]
    return _nearest_band(specific, band) or _nearest_band(pooled, band)


def lease_factor(
    table: dict[str, dict[int, float]],
    flat_type: str,
    remaining_months: int | None,
) -> float | None:
    """Value factor for a lease length, falling back to the all-types curve."""
    band = lease_band(remaining_months)
    if band is None:
        return None
    return _lookup(table, flat_type, band)


def storey_factor(
    table: dict[str, dict[int, float]],
    flat_type: str,
    low: int | None,
    high: int | None = None,
) -> float | None:
    band = storey_band(low, high if high is not None else low)
    if band is None:
        return None
    return _lookup(table, flat_type, band)
