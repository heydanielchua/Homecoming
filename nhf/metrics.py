"""Per-block aggregates, computed once by the batch job.

The phone should never do this work. Everything a ranked list needs about a
block — how much it transacts for, how it compares with its town, how fast
it has grown — is computed here and read back as one row.

Medians, not single records: resale prices are indicative and exclude
transfers between relatives, so one odd sale should not move a block.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import upsert_many
from .fit import lease_factor, load_lease_factors, load_storey_premiums, storey_factor
from .norm import month_index, month_to_quarter
from .stats import cagr, median

METRIC_COLUMNS = (
    "block_key", "flat_type", "n_txn_24m", "last_txn_month", "median_price_24m",
    "min_price_24m", "max_price_24m", "median_psm_24m", "median_sqm",
    "median_lease_months", "town_median_psm", "cagr_5y", "cagr_10y",
    "town_cagr_10y", "rpi_cagr_10y", "computed_at",
)

# Growth is measured between two-year windows, because a single year in one
# block can be two sales.
GROWTH_WINDOW_MONTHS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def month_plus(month: str, months: int) -> str:
    idx = month_index(month) + months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def month_minus(month: str, months: int) -> str:
    return month_plus(month, -months)


def latest_month(conn: sqlite3.Connection, as_of: str | None = None) -> str | None:
    """Most recent month with data, never later than `as_of`.

    `as_of` is what lets the backtest score a block using only the data that
    existed at the time.
    """
    if as_of:
        row = conn.execute(
            "SELECT MAX(month) AS m FROM resale_txn WHERE month <= ?", (as_of,)
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()
    return row["m"] if row else None


class MixAdjuster:
    """Takes the storey and lease mix out of a price per sqm.

    Without this, growth measured between two windows is mostly noise: a
    block that happened to sell two high floors in one window and two low
    floors in the next looks like it fell 10%, and the next window pulls it
    back. That is regression to the mean, and it made momentum predict the
    *opposite* of what happened next on test data. Dividing each sale by its
    own storey and lease factors compares like with like, and keeps lease
    decay in the lease signal rather than double-counting it in momentum.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.lease = load_lease_factors(conn)
        self.storey = load_storey_premiums(conn)
        self.fitted = bool(self.lease or self.storey)

    def adjusted(self, row: sqlite3.Row) -> float | None:
        psm = row["price_per_sqm"]
        if psm is None:
            return None
        if not self.fitted:
            return psm
        divisor = 1.0
        storey = storey_factor(
            self.storey, row["flat_type"], row["storey_low"], row["storey_high"]
        )
        lease = lease_factor(self.lease, row["flat_type"], row["remaining_lease_months"])
        if storey:
            divisor *= storey
        if lease:
            divisor *= lease
        return psm / divisor if divisor else psm


def _window_median_psm(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    start: str,
    end: str,
    adjuster: MixAdjuster | None = None,
) -> float | None:
    rows = conn.execute(
        f"SELECT price_per_sqm, flat_type, storey_low, storey_high,"
        f" remaining_lease_months FROM resale_txn WHERE {where}"
        " AND month >= ? AND month <= ? AND price_per_sqm IS NOT NULL",
        (*params, start, end),
    ).fetchall()
    if adjuster is None:
        return median([r["price_per_sqm"] for r in rows])
    return median([adjuster.adjusted(r) for r in rows])


def growth_rate(
    conn: sqlite3.Connection,
    where: str,
    params: tuple,
    end_month: str,
    years: int,
    adjuster: MixAdjuster | None = None,
) -> float | None:
    """Annual growth in price per sqm over `years`, between two-year windows."""
    recent_start = month_minus(end_month, GROWTH_WINDOW_MONTHS)
    past_end = month_minus(end_month, years * 12)
    past_start = month_minus(past_end, GROWTH_WINDOW_MONTHS)
    if month_index(past_end) <= 0:
        return None
    recent = _window_median_psm(conn, where, params, recent_start, end_month, adjuster)
    past = _window_median_psm(conn, where, params, past_start, past_end, adjuster)
    return cagr(past, recent, years)


def rpi_growth(
    conn: sqlite3.Connection, years: int, as_of_quarter: str | None = None
) -> float | None:
    """Growth of the HDB Resale Price Index itself, for comparison."""
    if as_of_quarter:
        rows = conn.execute(
            "SELECT quarter, index_value FROM price_index WHERE quarter <= ?"
            " ORDER BY quarter", (as_of_quarter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT quarter, index_value FROM price_index ORDER BY quarter"
        ).fetchall()
    if len(rows) < 2:
        return None
    latest = rows[-1]
    target_year = int(latest["quarter"][:4]) - years
    past = [r for r in rows if int(r["quarter"][:4]) <= target_year]
    if not past:
        return None
    return cagr(past[-1]["index_value"], latest["index_value"], years)


def compute_block_metrics(
    conn: sqlite3.Connection,
    flat_types: list[str] | None = None,
    as_of: str | None = None,
    store: bool = True,
) -> int | list[dict]:
    """Fill block_metric for every block-and-flat-type that has sold recently.

    With `store=False` the rows are returned instead of written, which is how
    the backtest scores history without touching the live table.
    """
    end_month = latest_month(conn, as_of)
    if not end_month:
        return 0
    start_24m = month_minus(end_month, 24)

    type_filter = ""
    type_params: tuple = ()
    if flat_types:
        placeholders = ", ".join("?" for _ in flat_types)
        type_filter = f" AND flat_type IN ({placeholders})"
        type_params = tuple(flat_types)

    pairs = conn.execute(
        f"""
        SELECT block_key, flat_type, town, COUNT(*) AS n, MAX(month) AS last_month
        FROM resale_txn
        WHERE month >= ? AND month <= ?{type_filter}
        GROUP BY block_key, flat_type
        """,
        (start_24m, end_month, *type_params),
    ).fetchall()

    rpi_10y = rpi_growth(conn, 10, month_to_quarter(end_month))
    adjuster = MixAdjuster(conn)
    town_cache: dict[tuple[str, str], float | None] = {}
    town_psm_cache: dict[tuple[str, str], float | None] = {}
    rows = []
    computed_at = _now()

    for pair in pairs:
        key, flat_type, town = pair["block_key"], pair["flat_type"], pair["town"]
        sales = conn.execute(
            "SELECT resale_price, price_per_sqm, floor_area_sqm, remaining_lease_months"
            " FROM resale_txn WHERE block_key = ? AND flat_type = ?"
            " AND month >= ? AND month <= ?",
            (key, flat_type, start_24m, end_month),
        ).fetchall()
        prices = [s["resale_price"] for s in sales if s["resale_price"]]
        psms = [s["price_per_sqm"] for s in sales if s["price_per_sqm"]]

        cache_key = (town, flat_type)
        if cache_key not in town_psm_cache:
            town_rows = conn.execute(
                "SELECT price_per_sqm FROM resale_txn"
                " WHERE town = ? AND flat_type = ? AND month >= ? AND month <= ?"
                "   AND price_per_sqm IS NOT NULL",
                (town, flat_type, start_24m, end_month),
            ).fetchall()
            town_psm_cache[cache_key] = median([r["price_per_sqm"] for r in town_rows])
            town_cache[cache_key] = growth_rate(
                conn, "town = ? AND flat_type = ?", (town, flat_type), end_month, 10,
                adjuster,
            )

        block_where = "block_key = ? AND flat_type = ?"
        block_params = (key, flat_type)
        rows.append((
            key, flat_type, len(sales), pair["last_month"],
            median(prices), min(prices) if prices else None,
            max(prices) if prices else None, median(psms),
            median([s["floor_area_sqm"] for s in sales if s["floor_area_sqm"]]),
            median([s["remaining_lease_months"] for s in sales
                    if s["remaining_lease_months"] is not None]),
            town_psm_cache[cache_key],
            growth_rate(conn, block_where, block_params, end_month, 5, adjuster),
            growth_rate(conn, block_where, block_params, end_month, 10, adjuster),
            town_cache[cache_key], rpi_10y, computed_at,
        ))

    if not store:
        return [dict(zip(METRIC_COLUMNS, row)) for row in rows]

    with conn:
        conn.execute("DELETE FROM block_metric")
        upsert_many(conn, "block_metric", METRIC_COLUMNS, rows, conflict="REPLACE")
    return len(rows)
