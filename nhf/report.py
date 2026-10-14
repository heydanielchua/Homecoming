"""One block, everything the government knows about it.

This is the shape the site reads. A report is assembled here once a month for
every block that has sold recently, written out as one small JSON file, and
looked up in the browser — so nothing on this page waits on a government API,
and a flat saved in January shows today's figures when it is opened again
because only the inputs were ever saved.

Three of the four things typed in change the report rather than being part of
it, and they are applied in the browser:

* **asking price** is compared against the fair-value range for the floor;
* **floor** picks the fair-value band, and decides what the view looks at;
* **facing** decides how much afternoon sun arrives.

Which is why the money section carries a fair value per storey band rather
than one number: the fitted storey curve is banded in five-floor steps, so a
band is not an approximation of the floor — it is exactly as fine as the
evidence goes, and precomputing one value per band is the whole answer rather
than a rounding of it.

Every figure carries how sure it is. Four levels, and the difference between
them is the point:

    fact        it sold for $473,000; the next block is 25 storeys
    worked      a fair-value range; a straight-line distance; a sun angle
    estimated   a height band from a plot ratio, shown as a range
    unknown     and why — never a blank and never a zero
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .fairvalue import Valuer
from .fit import STOREY_BAND_FLOORS, load_lease_factors, load_storey_premiums
from .metrics import MixAdjuster, month_minus
from .nearby import nearby_for, rain_for
from .norm import canon_flat_type, month_to_quarter, quarter_index, remaining_lease_months
from .stats import median
from .sun import (
    COMPASS,
    FACE_HALF_ANGLE,
    ISLAND_LAT,
    ISLAND_LON,
    LOW_SUN_ALTITUDE,
    MIN_ALTITUDE,
    STOREY_HEIGHT_M,
    afternoon_track,
    describe,
    exposure,
)
from .view import (
    BLOCKED_SLACK_M,
    NOMINAL_BLOCK_LENGTH_M,
    fit_height_bands,
    neighbours_of,
    parcels_near,
    view_from,
)

FACT, WORKED, ESTIMATED, UNKNOWN = "fact", "worked", "estimated", "unknown"

# How far back the report looks, in months. Two years of sales is what the
# fair value rests on, so the sales it shows are the same two years: a list
# that went back further would imply the older ones counted.
SALES_MONTHS = 24

# How much history the trend line carries. Ten years is long enough to hold a
# full cycle — the 2013-2019 slide and the recovery after it — and short
# enough that the blocks in it are the blocks standing today.
TREND_YEARS = 10

# A quarter of a block's own sales is one or two flats, which is noise. Each
# trend point is therefore a trailing year, and a point is only drawn when
# the year behind it holds this many sales.
TREND_WINDOW_QUARTERS = 4
TREND_MIN_SALES = 3

# Caps, so one block's file stays small. These are generous: a busy block
# sells 30 times in two years and has a dozen neighbours within 300 m.
MAX_SALES_LISTED = 40
MAX_NEIGHBOURS = 30
MAX_PARCELS = 12

RENT_MONTHS = 12
RENT_MIN_SAMPLE = 3
RENT_NEARBY_RADIUS_M = 600.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fig(
    value: Any,
    confidence: str = FACT,
    note: str | None = None,
    unit: str | None = None,
    **extra: Any,
) -> dict:
    """One figure, with how sure it is attached.

    Written as a dict rather than a class because this is the wire format:
    the browser reads `{"v": ..., "c": "fact"}` and renders the confidence as
    a marker next to the number. An unknown carries its reason in the same
    field a value would have used, so there is nowhere for a blank to hide.
    """
    out: dict[str, Any] = {"v": value, "c": confidence}
    if value is None and confidence != UNKNOWN:
        out["c"] = UNKNOWN
    if note:
        out["n"] = note
    if unit:
        out["u"] = unit
    out.update(extra)
    return out


def unknown(reason: str) -> dict:
    """A figure that is not available, and the reason it is not."""
    return {"v": None, "c": UNKNOWN, "n": reason}


def storey_bands(max_floor: int | None) -> list[int]:
    """The five-floor bands a block's floors fall into.

    Keyed by the lower bound — 1, 6, 11, 16 — which is how the fitted storey
    premium table is keyed, so a band here is the same band there.
    """
    top = max_floor if max_floor and max_floor > 0 else 16
    return list(range(1, top + 1, STOREY_BAND_FLOORS))


def band_of(storey: int | None) -> int | None:
    """Which band a floor sits in."""
    if storey is None or storey < 1:
        return None
    return ((storey - 1) // STOREY_BAND_FLOORS) * STOREY_BAND_FLOORS + 1


class ReportBuilder:
    """Holds the lookups so a run can build 9,000 reports without re-reading.

    Everything expensive — the fitted curves, the price index, the plot ratio
    bands, the sun track — is read or computed once here. A single report is
    then a handful of indexed queries.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.valuer = Valuer(conn)
        self.adjuster = MixAdjuster(conn)
        self.lease_factors = load_lease_factors(conn)
        self.storey_premiums = load_storey_premiums(conn)
        self.height_bands = fit_height_bands(conn)
        self.track = afternoon_track()
        self.latest_month = self.valuer.latest_month
        self.latest_quarter = self.valuer.latest_quarter
        self.index = dict(self.valuer.index)
        self._town_psm: dict[tuple[str, str, str], float | None] = {}
        self._town_trend: dict[tuple[str, str], list[dict]] = {}

    # -- money ------------------------------------------------------------
    def sales(self, block_key: str, flat_type: str) -> list[dict]:
        """The block's own recent sales, newest first. Facts, every one."""
        if not self.latest_month:
            return []
        cutoff = month_minus(self.latest_month, SALES_MONTHS)
        rows = self.conn.execute(
            """
            SELECT month, storey_range, storey_low, storey_high, floor_area_sqm,
                   resale_price, price_per_sqm, remaining_lease_months
            FROM resale_txn
            WHERE block_key = ? AND flat_type = ? AND month >= ?
            ORDER BY month DESC, resale_price DESC
            """,
            (block_key, flat_type, cutoff),
        ).fetchall()
        return [
            {
                "month": r["month"],
                "storey": r["storey_range"],
                "sqm": r["floor_area_sqm"],
                "price": r["resale_price"],
                "psm": r["price_per_sqm"],
                "lease_months": r["remaining_lease_months"],
            }
            for r in rows[:MAX_SALES_LISTED]
        ]

    def town_psm(self, town: str, flat_type: str) -> float | None:
        """Median price per sqm for the town, over the same window."""
        if not self.latest_month:
            return None
        key = (town, flat_type, self.latest_month)
        if key not in self._town_psm:
            cutoff = month_minus(self.latest_month, SALES_MONTHS)
            rows = self.conn.execute(
                "SELECT price_per_sqm FROM resale_txn"
                " WHERE town = ? AND flat_type = ? AND month >= ?"
                "   AND price_per_sqm IS NOT NULL",
                (town, flat_type, cutoff),
            ).fetchall()
            self._town_psm[key] = median([r["price_per_sqm"] for r in rows])
        return self._town_psm[key]

    def fair_values(
        self,
        block_key: str,
        flat_type: str,
        max_floor: int | None,
        target_lease_months: int | None,
    ) -> dict[str, dict]:
        """A fair-value range per storey band.

        One call to the valuer per band. The bands are the fitted curve's own
        bands, so this is not a sampling of a continuous function — there is
        no more floor detail in the evidence than this.
        """
        out: dict[str, dict] = {}
        for band in storey_bands(max_floor):
            # The middle of the band, so the comparables are adjusted to a
            # typical floor in it rather than to its lowest.
            storey = band + STOREY_BAND_FLOORS // 2
            value = self.valuer.estimate(
                block_key, flat_type, storey=storey,
                target_lease_months=target_lease_months,
            )
            out[str(band)] = {
                "low": value.low,
                "mid": value.mid,
                "high": value.high,
                "psm_mid": value.psm_mid,
                "n_comps": value.n_comps,
                "scope": value.scope,
                "notes": value.notes,
            }
        return out

    def money(
        self,
        block_key: str,
        flat_type: str,
        town: str,
        max_floor: int | None,
        lease_months: int | None,
    ) -> dict:
        sales = self.sales(block_key, flat_type)
        psms = [s["psm"] for s in sales if s["psm"]]
        prices = [s["price"] for s in sales if s["price"]]
        block_psm = median(psms)
        town = town or ""
        town_median = self.town_psm(town, flat_type)

        n = len(sales)
        if n == 0:
            thin = unknown(
                f"no {flat_type.lower()} flat in this block has sold in the last "
                f"{SALES_MONTHS} months"
            )
            return {
                "n_sales": fig(0, FACT),
                "median_price": thin,
                "median_psm": thin,
                "town_median_psm": (
                    fig(round(town_median, 2), WORKED,
                        f"median of every {flat_type.lower()} sale in {town} "
                        f"over {SALES_MONTHS} months", unit="SGD/sqm")
                    if town_median else unknown("no town sales to compare with")
                ),
                "vs_town_pct": thin,
                "fair_value": self.fair_values(
                    block_key, flat_type, max_floor, lease_months),
                "sales": [],
                "thin": True,
            }

        return {
            "n_sales": fig(
                n, FACT,
                f"{n} sale{'s' if n != 1 else ''} in this block in "
                f"{SALES_MONTHS} months — "
                + ("enough to read a median from" if n >= 5
                   else "too few to be sure of; the range is wide for a reason"),
            ),
            "last_sale": fig(sales[0]["month"], FACT, unit="month"),
            "median_price": fig(round(median(prices)), FACT, unit="SGD"),
            "low_price": fig(round(min(prices)), FACT, unit="SGD"),
            "high_price": fig(round(max(prices)), FACT, unit="SGD"),
            "median_psm": fig(round(block_psm, 2), FACT, unit="SGD/sqm"),
            "median_sqm": fig(median([s["sqm"] for s in sales if s["sqm"]]), FACT,
                              unit="sqm"),
            "town_median_psm": (
                fig(round(town_median, 2), WORKED,
                    f"median of every {flat_type.lower()} sale in {town} over "
                    f"{SALES_MONTHS} months", unit="SGD/sqm")
                if town_median else unknown("no town sales to compare with")
            ),
            "vs_town_pct": (
                fig(round(100.0 * (block_psm / town_median - 1), 1), WORKED,
                    f"this block's price per sqm against {town}'s", unit="%")
                if (town_median and block_psm) else
                unknown("no town median to compare against")
            ),
            "fair_value": self.fair_values(
                block_key, flat_type, max_floor, lease_months),
            "sales": sales,
            "thin": n < 5,
        }

    # -- trend ------------------------------------------------------------
    def _series(self, where: str, params: tuple) -> list[dict]:
        """Mix-adjusted median price per sqm, by trailing-year window.

        Mix adjustment is the whole reason this is worth drawing. A block that
        happened to sell two high floors in one quarter and two low floors in
        the next looks like it fell ten per cent and then recovered, and
        reading that as movement is the single easiest mistake to make here.
        Dividing each sale by its own storey and lease factors compares like
        with like.
        """
        if not self.latest_month:
            return []
        start = month_minus(self.latest_month, TREND_YEARS * 12)
        rows = self.conn.execute(
            f"SELECT quarter, price_per_sqm, flat_type, storey_low, storey_high,"
            f" remaining_lease_months FROM resale_txn WHERE {where}"
            f" AND month >= ? AND price_per_sqm IS NOT NULL",
            (*params, start),
        ).fetchall()
        if not rows:
            return []

        by_quarter: dict[str, list[float]] = {}
        for row in rows:
            value = self.adjuster.adjusted(row)
            if value:
                by_quarter.setdefault(row["quarter"], []).append(value)

        quarters = sorted(by_quarter)
        if not quarters:
            return []
        first, last = quarter_index(quarters[0]), quarter_index(quarters[-1])
        index_by_quarter = {quarter_index(q): q for q in quarters}

        out: list[dict] = []
        for position in range(first, last + 1):
            window: list[float] = []
            for back in range(TREND_WINDOW_QUARTERS):
                quarter = index_by_quarter.get(position - back)
                if quarter:
                    window.extend(by_quarter[quarter])
            if len(window) < TREND_MIN_SALES:
                continue
            label = index_by_quarter.get(position)
            if label is None:
                year, q = divmod(position, 4)
                label = f"{year}-Q{q + 1}"
            out.append({
                "quarter": label,
                "psm": round(median(window), 2),
                "n": len(window),
            })
        return out

    def trend(self, block_key: str, flat_type: str) -> dict:
        """This block's own series.

        The town's series and the national index are the same curve for every
        block in a town, so they are written once per town and once for the
        country rather than copied into nine thousand files. The browser
        draws all three on one chart; only this part of it is about this
        block.
        """
        return {
            "block": self._series(
                "block_key = ? AND flat_type = ?", (block_key, flat_type)),
            "adjusted": self.adjuster.fitted,
            "note": (
                "medians adjusted for the floors and leases that happened to "
                "sell, because the raw ones move with the mix and reading that "
                "as movement is the easiest mistake here"
                if self.adjuster.fitted else
                "the storey and lease curves are not fitted, so these medians "
                "are raw and will move with whichever floors happened to sell"
            ),
            "window_quarters": TREND_WINDOW_QUARTERS,
            "min_sales": TREND_MIN_SALES,
        }

    def town_trend(self, town: str, flat_type: str) -> list[dict]:
        """The town's series, cached because every block in it shares one."""
        cache_key = (town or "", flat_type)
        if cache_key not in self._town_trend:
            self._town_trend[cache_key] = self._series(
                "town = ? AND flat_type = ?", (town or "", flat_type))
        return self._town_trend[cache_key]

    # -- rent -------------------------------------------------------------
    def rent(self, block_key: str, flat_type: str, town: str) -> dict:
        """Median whole-flat rent, from the block out.

        Widened only when the block itself is too thin, and the scope is
        reported either way: a town median and a block median are different
        claims and should not look alike.
        """
        row = self.conn.execute(
            "SELECT MAX(month) AS m FROM rental_txn").fetchone()
        latest = row["m"] if row else None
        if not latest:
            return {
                "median": unknown(
                    "the rental dataset has not been loaded — run "
                    "`nhf sync --step rentals`"),
                "scope": None,
            }
        cutoff = month_minus(latest, RENT_MONTHS)

        def rents(where: str, params: tuple) -> list[float]:
            return [
                r["monthly_rent"] for r in self.conn.execute(
                    f"SELECT monthly_rent FROM rental_txn WHERE {where}"
                    " AND month >= ? AND monthly_rent IS NOT NULL",
                    (*params, cutoff),
                )
            ]

        values = rents("block_key = ? AND flat_type = ?", (block_key, flat_type))
        scope, note = "block", f"this block's own approvals over {RENT_MONTHS} months"

        if len(values) < RENT_MIN_SAMPLE:
            keys = self._nearby_keys(block_key, RENT_NEARBY_RADIUS_M)
            if keys:
                placeholders = ", ".join("?" for _ in keys)
                widened = rents(
                    f"block_key IN ({placeholders}) AND flat_type = ?",
                    (*keys, flat_type),
                )
                if len(widened) > len(values):
                    values, scope = widened, "nearby"
                    note = (
                        f"only {len(rents('block_key = ? AND flat_type = ?', (block_key, flat_type)))}"
                        f" approval(s) in this block, so widened to blocks within "
                        f"{int(RENT_NEARBY_RADIUS_M)} m"
                    )
        if len(values) < RENT_MIN_SAMPLE and town:
            town_values = rents("town = ? AND flat_type = ?", (town, flat_type))
            if len(town_values) > len(values):
                values, scope = town_values, "town"
                note = f"too few approvals nearby, so this is the {town} median"

        if not values:
            return {
                "median": unknown(
                    f"no whole-flat rent approved for a {flat_type.lower()} flat "
                    f"around here in the last {RENT_MONTHS} months"),
                "scope": None,
            }
        return {
            "median": fig(round(median(values)), WORKED, note, unit="SGD/month"),
            "low": fig(round(min(values)), FACT, unit="SGD/month"),
            "high": fig(round(max(values)), FACT, unit="SGD/month"),
            "n": fig(len(values), FACT),
            "scope": scope,
            "latest_month": latest,
        }

    def _nearby_keys(self, block_key: str, radius_m: float) -> list[str]:
        origin = self.conn.execute(
            "SELECT lat, lon, town FROM block WHERE block_key = ?", (block_key,)
        ).fetchone()
        if not origin or origin["lat"] is None:
            return []
        from .geo import haversine_m

        pad = radius_m / 110_000.0
        return [
            row["block_key"] for row in self.conn.execute(
                "SELECT block_key, lat, lon FROM block"
                " WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (origin["lat"] - pad, origin["lat"] + pad,
                 origin["lon"] - pad, origin["lon"] + pad),
            )
            if haversine_m(origin["lat"], origin["lon"], row["lat"], row["lon"]) <= radius_m
        ]

    # -- lease ------------------------------------------------------------
    def lease(self, block_key: str) -> dict:
        row = self.conn.execute(
            "SELECT lease_commence_year, COUNT(*) AS n FROM resale_txn"
            " WHERE block_key = ? AND lease_commence_year IS NOT NULL"
            " GROUP BY lease_commence_year ORDER BY n DESC LIMIT 1",
            (block_key,),
        ).fetchone()
        if not row:
            completed = self.conn.execute(
                "SELECT year_completed FROM block WHERE block_key = ?", (block_key,)
            ).fetchone()
            if completed and completed["year_completed"]:
                year = int(completed["year_completed"])
                months = remaining_lease_months(year, self.latest_month)
                return {
                    "commence_year": fig(
                        year, ESTIMATED,
                        "no sale in this block states a lease start, so the year "
                        "it was completed is standing in for it — the lease "
                        "usually starts a year or two earlier",
                        unit="year",
                    ),
                    "remaining_years": fig(
                        round(months / 12, 1) if months else None, ESTIMATED,
                        "from the completion year, not a stated lease start",
                        unit="years",
                    ),
                }
            return {
                "commence_year": unknown(
                    "nothing in the data says when this block's lease started"),
                "remaining_years": unknown("no lease start to count from"),
            }

        year = int(row["lease_commence_year"])
        months = remaining_lease_months(year, self.latest_month)
        return {
            "commence_year": fig(
                year, FACT, "as stated on this block's own sales", unit="year"),
            "remaining_years": fig(
                round(months / 12, 1) if months else None, WORKED,
                f"99 years from {year}, as at {self.latest_month}", unit="years",
            ),
            "remaining_months": months,
        }

    # -- view, sun, nearby, rain ------------------------------------------
    def surroundings(self, block_key: str, max_floor: int | None) -> dict:
        neighbours = neighbours_of(self.conn, block_key)[:MAX_NEIGHBOURS]
        parcels = parcels_near(
            self.conn, block_key, fitted=self.height_bands)[:MAX_PARCELS]
        taller = [n for n in neighbours if (n.floors or 0) > (max_floor or 0)]

        return {
            "max_floor": (
                fig(max_floor, FACT, "HDB publishes the storey count for every block")
                if max_floor else
                unknown("HDB publishes no storey count for this block")
            ),
            "neighbours": [n.as_dict() for n in neighbours],
            "neighbours_taller": len(taller),
            "neighbour_radius_m": 300,
            "neighbour_note": (
                "HDB blocks only. A private development next door is not in "
                "the property information dataset, so it is a hole in this "
                "rather than a clear view"
            ),
            "parcels": [p.as_dict() for p in parcels],
            "parcels_loaded": bool(
                self.conn.execute(
                    "SELECT 1 FROM land_parcel LIMIT 1").fetchone()
            ),
            "height_bands_fitted": len(self.height_bands),
            "view_without_floor": view_from(neighbours, None).as_dict(),
        }

    # -- the whole thing --------------------------------------------------
    def build(self, block_key: str) -> dict | None:
        block = self.conn.execute(
            "SELECT block_key, block, street_name, town, max_floor_lvl,"
            " year_completed, total_dwelling_units, lat, lon, postal"
            " FROM block WHERE block_key = ?",
            (block_key,),
        ).fetchone()
        if block is None:
            # A block that transacts but is not in property information. The
            # resale files know its address, which is enough for a report
            # that says the storey count is unknown.
            fallback = self.conn.execute(
                "SELECT block_key, block, street_name, town FROM resale_txn"
                " WHERE block_key = ? LIMIT 1", (block_key,),
            ).fetchone()
            if fallback is None:
                return None
            block = {
                "block_key": fallback["block_key"], "block": fallback["block"],
                "street_name": fallback["street_name"], "town": fallback["town"],
                "max_floor_lvl": None, "year_completed": None,
                "total_dwelling_units": None, "lat": None, "lon": None,
                "postal": None,
            }

        lease = self.lease(block_key)
        lease_months = lease.get("remaining_months")

        types = [
            row["flat_type"] for row in self.conn.execute(
                "SELECT flat_type, COUNT(*) AS n FROM resale_txn"
                " WHERE block_key = ? AND flat_type <> ''"
                " GROUP BY flat_type ORDER BY n DESC",
                (block_key,),
            )
        ]
        town = block["town"] or ""
        per_type = {}
        for flat_type in types:
            flat_type = canon_flat_type(flat_type)
            per_type[flat_type] = {
                "money": self.money(
                    block_key, flat_type, town, block["max_floor_lvl"], lease_months),
                "trend": self.trend(block_key, flat_type),
                "rent": self.rent(block_key, flat_type, town),
            }

        return {
            "block_key": block["block_key"],
            "block": block["block"],
            "street": block["street_name"],
            "town": town,
            "lat": block["lat"],
            "lon": block["lon"],
            "postal": block["postal"],
            "year_completed": block["year_completed"],
            "dwelling_units": block["total_dwelling_units"],
            "lease": lease,
            "flat_types": per_type,
            "surroundings": self.surroundings(block_key, block["max_floor_lvl"]),
            "nearby": nearby_for(
                self.conn, block_key, block["lat"], block["lon"]).as_dict(),
            "rain": rain_for(self.conn, block["lat"], block["lon"]).as_dict(),
            "as_of": {
                "month": self.latest_month,
                "quarter": self.latest_quarter,
                "built_at": _now(),
            },
        }


    # -- the two payloads every block shares ------------------------------
    def town_payload(self, town: str) -> dict:
        """What every block in one town has in common.

        Written once per town instead of into each of its blocks' files. A
        mature town has 400 blocks and a forty-point trend line per flat
        type, so this is the difference between a site of a few megabytes and
        one of a few hundred.
        """
        types = [
            row["flat_type"] for row in self.conn.execute(
                "SELECT flat_type, COUNT(*) AS n FROM resale_txn"
                " WHERE town = ? AND flat_type <> '' GROUP BY flat_type"
                " ORDER BY n DESC",
                (town,),
            )
        ]
        return {
            "town": town,
            "flat_types": {
                flat_type: {
                    "trend": self.town_trend(town, flat_type),
                    "median_psm": self.town_psm(town, flat_type),
                }
                for flat_type in map(canon_flat_type, types)
            },
            "as_of": {"month": self.latest_month, "quarter": self.latest_quarter},
        }

    def meta_payload(self) -> dict:
        """What every block in the country has in common.

        The national index, the fitted curves, and the sun. The sun belongs
        here and not in a block's file because the island is 50 km across:
        the sun's altitude varies by a quarter of a degree from end to end,
        which is less than the width of the sun's own disc, so one track is
        every block's track and writing it nine thousand times would say
        otherwise.
        """
        return {
            "built_at": _now(),
            "as_of": {"month": self.latest_month, "quarter": self.latest_quarter},
            "national_index": [
                {"quarter": q, "index": v} for q, v in sorted(self.index.items())
            ],
            "index_note": (
                "HDB Resale Price Index, 1Q2009 = 100. The whole market, every "
                "flat type and town together"
            ),
            "sun": {
                "lat": ISLAND_LAT,
                "lon": ISLAND_LON,
                "note": (
                    "computed for 1.35 degrees north, which is every block on "
                    "the island to within a quarter of a degree of altitude — "
                    "less than the width of the sun itself"
                ),
                "track": [s.as_dict() for s in self.track],
                "open_face": {
                    facing: {
                        **exposure(facing, self.track).as_dict(),
                        "says": describe(exposure(facing, self.track)),
                    }
                    for facing in COMPASS
                },
                "constants": {
                    "low_sun_altitude": LOW_SUN_ALTITUDE,
                    "min_altitude": MIN_ALTITUDE,
                    "face_half_angle": FACE_HALF_ANGLE,
                    "storey_height_m": STOREY_HEIGHT_M,
                    "nominal_block_length_m": NOMINAL_BLOCK_LENGTH_M,
                    "blocked_slack_m": BLOCKED_SLACK_M,
                },
            },
            "curves": {
                "lease": {
                    flat_type: {str(band): factor for band, factor in bands.items()}
                    for flat_type, bands in self.lease_factors.items()
                },
                "storey": {
                    flat_type: {str(band): factor for band, factor in bands.items()}
                    for flat_type, bands in self.storey_premiums.items()
                },
                "storey_band_floors": STOREY_BAND_FLOORS,
                "note": (
                    "fitted from the transactions themselves, not assumed. The "
                    "lease curve understates true decay and is a floor on it: "
                    "HDB towns were built in waves, so dividing by a town "
                    "cohort removes some of the lease signal with the location "
                    "signal"
                ),
            },
            "height_bands": {
                str(gpr): {"low": low, "high": high, "n": n}
                for gpr, (low, high, n) in sorted(self.height_bands.items())
            },
        }


def block_report(conn: sqlite3.Connection, block_key: str) -> dict | None:
    """One report, for a caller that only wants one."""
    return ReportBuilder(conn).build(block_key)


def verdict(asking: float | None, low: float | None, high: float | None) -> dict:
    """Where an asking price sits against a fair-value range.

    Kept here rather than in the browser so the CLI and the site agree on the
    wording, and because "within" is a real answer: a range is a range, and a
    price inside it is not a bargain or a rip-off.
    """
    if asking is None:
        return {"status": "unknown", "says": "no asking price typed in"}
    if low is None or high is None:
        return {"status": "unknown",
                "says": "no fair-value range for this floor, so nothing to compare"}
    if asking < low:
        return {
            "status": "below",
            "says": f"${round(low - asking):,} below the bottom of the range",
            "gap": round(low - asking),
        }
    if asking > high:
        return {
            "status": "above",
            "says": f"${round(asking - high):,} above the top of the range",
            "gap": round(asking - high),
        }
    return {
        "status": "within",
        "says": "inside the range the comparable sales support",
        "gap": 0,
    }
