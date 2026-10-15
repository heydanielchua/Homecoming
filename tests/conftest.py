"""Test fixtures.

The transactions here are generated, not real: the live datasets are
downloaded by `nhf sync` and are far too large to check in. They exist so the
maths can be tested against a known answer — a block that is deliberately
cheap, one that is deliberately fast-growing, one with a short lease — and
they are never loaded by the app.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nhf.db import open_db, upsert_many  # noqa: E402
from nhf.norm import block_key, month_to_quarter, remaining_lease_months  # noqa: E402
from nhf.sources.hdb import RESALE_COLUMNS  # noqa: E402

LATEST_MONTH = "2026-09"
FIRST_YEAR = 2008

# Towns are real, so the far-north rule and the town cohorts can be
# exercised; every number below is invented. Each town gets enough blocks that
# a town-quarter-flat-type cohort is the size it would be in the real data,
# because the fitted curves depend on that.
TOWNS = [
    # town,         blocks, lease start, base psm 2008, town growth, lat,    lon
    ("ANG MO KIO",  10, 1979, 2600, 0.052, 1.3700, 103.8450),
    ("BEDOK",       10, 1985, 2400, 0.050, 1.3300, 103.9300),
    ("CLEMENTI",     8, 1978, 2900, 0.048, 1.3150, 103.7650),
    ("WOODLANDS",   10, 1997, 2000, 0.043, 1.4350, 103.7890),
    ("TAMPINES",    10, 1990, 2200, 0.055, 1.3520, 103.9450),
    ("QUEENSTOWN",   8, 1972, 3300, 0.049, 1.2940, 103.8060),
    ("YISHUN",      10, 1987, 2050, 0.046, 1.4200, 103.8350),
]

STREETS = {
    "ANG MO KIO": "ANG MO KIO AVE 3",
    "BEDOK": "BEDOK NORTH ST 1",
    "CLEMENTI": "CLEMENTI AVE 4",
    "WOODLANDS": "WOODLANDS DR 16",
    "TAMPINES": "TAMPINES ST 42",
    "QUEENSTOWN": "QUEENSTOWN RD",
    "YISHUN": "YISHUN RING RD",
}

# Blocks we assert on by name: a deliberately cheap one, a fast grower, and a
# short-lease one, all in Tampines.
CHEAP_BLOCK = ("501", "TAMPINES ST 42")
FAST_BLOCK = ("502", "TAMPINES ST 42")
SHORT_LEASE_BLOCK = ("503", "TAMPINES ST 42")


def _blocks(rng: "random.Random") -> list[tuple]:
    """(block, street, town, lease, floors, units, lat, lon, psm2008, growth)."""
    out = []
    for town, count, lease, psm, growth, lat, lon in TOWNS:
        street = STREETS[town]
        for i in range(count):
            number = 100 + 10 * TOWNS.index((town, count, lease, psm, growth, lat, lon)) + i
            out.append((
                # Blocks in one town span about thirty years of lease, as a
                # mature HDB town does, so the lease curve has room to fit.
                str(number), street, town, lease + (i * 4) % 30,
                rng.choice([10, 12, 13, 16]), rng.choice([96, 120, 140, 160]),
                lat + 0.0006 * i, lon + 0.0006 * i,
                psm * rng.uniform(0.94, 1.06), growth + rng.uniform(-0.004, 0.004),
            ))
    tampines = next(t for t in TOWNS if t[0] == "TAMPINES")
    out += [
        (CHEAP_BLOCK[0], CHEAP_BLOCK[1], "TAMPINES", 1992, 12, 140,
         tampines[5] + 0.01, tampines[6] + 0.01, tampines[3] * 0.72, tampines[4]),
        (FAST_BLOCK[0], FAST_BLOCK[1], "TAMPINES", 1993, 12, 140,
         tampines[5] + 0.011, tampines[6] + 0.011, tampines[3] * 0.95, tampines[4] + 0.025),
        (SHORT_LEASE_BLOCK[0], SHORT_LEASE_BLOCK[1], "TAMPINES", 1968, 12, 140,
         tampines[5] + 0.012, tampines[6] + 0.012, tampines[3] * 0.9, tampines[4]),
    ]
    return out


FLAT_TYPES = {"3 ROOM": 68.0, "4 ROOM": 93.0, "5 ROOM": 118.0}
STOREY_RANGES = [("01 TO 03", 1, 3), ("04 TO 06", 4, 6), ("07 TO 09", 7, 9),
                 ("10 TO 12", 10, 12), ("13 TO 15", 13, 15)]


def _months(first_year: int, latest: str) -> list[str]:
    end_year, end_month = int(latest[:4]), int(latest[5:7])
    out = []
    for year in range(first_year, end_year + 1):
        for month in range(1, 13):
            if year == end_year and month > end_month:
                break
            out.append(f"{year:04d}-{month:02d}")
    return out


def _lease_factor(years_left: float) -> float:
    """A gentle decay, so the fitted table has something real to find."""
    return min(1.0, 0.55 + 0.0045 * max(0.0, min(99.0, years_left)))


def build_fixture_db(path: str | Path = ":memory:", seed: int = 7):
    """A small, deterministic store shaped like the real one."""
    rng = random.Random(seed)
    conn = open_db(path)
    months = _months(FIRST_YEAR, LATEST_MONTH)

    # Price index: 100 at 1Q2009, rising 3% a year with a dip in 2014-2019.
    index_rows = []
    for year in range(FIRST_YEAR, int(LATEST_MONTH[:4]) + 1):
        for q in range(1, 5):
            t = (year - 2009) + (q - 1) / 4
            value = 100 * (1.03 ** t)
            if 2014 <= year <= 2019:
                value *= 0.93
            index_rows.append((f"{year}-Q{q}", round(value, 1)))
    with conn:
        upsert_many(conn, "price_index", ("quarter", "index_value"), index_rows,
                    conflict="REPLACE")

    blocks = _blocks(rng)
    block_rows = []
    for blk, street, town, lease, floors, units, lat, lon, _psm, _g in blocks:
        block_rows.append((
            block_key(blk, street), blk, street, town, floors, lease + 2, "Y", units,
            0, 0, int(units * 0.3), int(units * 0.5), int(units * 0.2), 0, 0, lat, lon,
        ))
    with conn:
        upsert_many(
            conn, "block",
            ("block_key", "block", "street_name", "town", "max_floor_lvl",
             "year_completed", "residential", "total_dwelling_units", "units_1room",
             "units_2room", "units_3room", "units_4room", "units_5room", "units_exec",
             "units_multigen", "lat", "lon"),
            block_rows, conflict="REPLACE",
        )

    txns = []
    for blk, street, town, lease, floors, _units, _lat, _lon, psm2008, growth in blocks:
        key = block_key(blk, street)
        for month in months:
            # A block sells a few times a year, not every month.
            if rng.random() > 0.3:
                continue
            year = int(month[:4]) + (int(month[5:7]) - 1) / 12
            if year < lease:
                continue  # a flat cannot sell before its lease starts
            t = year - 2008
            market = (1 + growth) ** t
            if 2014 <= year <= 2019:
                market *= 0.95
            for flat_type, area in FLAT_TYPES.items():
                if rng.random() > 0.55:
                    continue
                storey_range, low, high = rng.choice(STOREY_RANGES[:max(1, floors // 3)])
                storey_premium = 1.0 + 0.012 * ((low + high) / 2 - 6)
                left = lease + 99 - year
                psm = psm2008 * market * storey_premium * _lease_factor(left)
                psm *= rng.uniform(0.97, 1.03)
                # 5-room flats carry a slight discount per sqm, as they do.
                psm *= {"3 ROOM": 1.08, "4 ROOM": 1.0, "5 ROOM": 0.94}[flat_type]
                price = round(psm * area, -3)
                txns.append((
                    month, month_to_quarter(month), town, flat_type, "IMPROVED",
                    blk, street, key, storey_range, low, high, area, lease,
                    remaining_lease_months(lease, month), price,
                    round(price / area, 2), "fixture",
                ))
    with conn:
        upsert_many(conn, "resale_txn", RESALE_COLUMNS, txns)

    rentals = []
    for blk, street, town, *_rest in blocks:
        key = block_key(blk, street)
        for flat_type in FLAT_TYPES:
            base = {"3 ROOM": 2200, "4 ROOM": 2800, "5 ROOM": 3300}[flat_type]
            for i in range(6):
                rentals.append((
                    "2026-06", town, blk, street, key, flat_type,
                    base + rng.randint(-200, 200) + i,
                ))
    with conn:
        upsert_many(
            conn, "rental_txn",
            ("month", "town", "block", "street_name", "block_key", "flat_type",
             "monthly_rent"),
            rentals,
        )

    with conn:
        upsert_many(
            conn, "mrt_station",
            ("name", "line", "status", "expected_year", "lat", "lon", "source"),
            [
                ("ANG MO KIO", "NSL", "open", None, 1.3700, 103.8496, "fixture"),
                ("BEDOK", "EWL", "open", None, 1.3240, 103.9300, "fixture"),
                ("CLEMENTI", "EWL", "open", None, 1.3150, 103.7650, "fixture"),
                ("WOODLANDS", "NSL", "open", None, 1.4370, 103.7860, "fixture"),
                ("TAMPINES", "EWL", "open", None, 1.3540, 103.9450, "fixture"),
                ("QUEENSTOWN", "EWL", "open", None, 1.2945, 103.8060, "fixture"),
                ("YISHUN", "NSL", "open", None, 1.4294, 103.8350, "fixture"),
                ("TAMPINES NORTH", "CRL", "under_construction", 2030, 1.3600, 103.9450,
                 "fixture"),
            ],
            conflict="REPLACE",
        )
        upsert_many(
            conn, "office", ("code", "label", "postal", "lat", "lon"),
            [("office_a", "Daniel's office", "109679", 1.2820, 103.8180),
             ("office_b", "Connie's office", "098544", 1.2740, 103.8030)],
            conflict="REPLACE",
        )

    # Routed drive times, as OneMap would have written them.
    commutes = []
    from nhf.geo import haversine_m
    for blk, street, _town, *_r, lat, lon, _psm, _g in blocks:
        key = block_key(blk, street)
        for code, olat, olon in (("office_a", 1.2820, 103.8180), ("office_b", 1.2740, 103.8030)):
            km = haversine_m(lat, lon, olat, olon) / 1000.0
            commutes.append((key, code, round(6 + km * 1.6, 1), km * 1300, "ok",
                             None, "2026-10-01T00:00:00+00:00"))
    with conn:
        upsert_many(
            conn, "commute",
            ("block_key", "office_code", "drive_minutes", "distance_m", "status",
             "message", "computed_at"),
            commutes, conflict="REPLACE",
        )
    return conn


@pytest.fixture()
def db():
    conn = build_fixture_db()
    yield conn
    conn.close()


@pytest.fixture()
def fitted_db(db):
    """The fixture store with every batch step applied."""
    from nhf.fit import fit_lease_factors, fit_storey_premiums
    from nhf.growth import score_all
    from nhf.metrics import compute_block_metrics
    from nhf.sync import derive_town_medians

    derive_town_medians(db)
    fit_lease_factors(db)
    fit_storey_premiums(db)
    compute_block_metrics(db)
    score_all(db)
    return db


# --- the layers the listing report reads ---------------------------------
# Placed around the Tampines fixture blocks, with distances chosen so the
# assertions can be about the right answer rather than about not crashing:
# the MRT exit is nearer than the station centroid would be, the park's edge
# is close while its middle is far, and one hawker centre is still being
# built.
TAMPINES_LAT, TAMPINES_LON = 1.3520, 103.9450


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a point by a distance in metres. Good enough at this latitude."""
    return (lat + north_m / 110_574.0, lon + east_m / 111_320.0)


def add_surroundings(conn):
    """Amenities, land parcels and rainfall gauges around the fixture blocks."""
    from nhf.sources.amenity import AMENITY_COLUMNS

    lat, lon = TAMPINES_LAT + 0.01, TAMPINES_LON + 0.01      # the cheap block
    rows = []

    def point(kind, name, north, east, **kw):
        plat, plon = _offset(lat, lon, north, east)
        rows.append((
            kind, kw.get("subtype"), name, kw.get("detail"), kw.get("status"),
            kw.get("size_hint"), plat, plon, plat, plat, plon, plon,
            kw.get("postal"), "fixture",
        ))

    # Two exits of one station: the near one is what you walk to.
    point("mrt_exit", "TAMPINES MRT STATION Exit A", 300, 0,
          subtype="TAMPINES MRT STATION", detail="Exit A")
    point("mrt_exit", "TAMPINES MRT STATION Exit D", 520, 120,
          subtype="TAMPINES MRT STATION", detail="Exit D")
    point("bus_stop", "76059", 90, 20, detail="B07")
    point("bus_stop", "76061", 240, 60)
    point("hawker_centre", "Tampines Round Market", 420, 10,
          status="Existing", size_hint=43.0)
    point("hawker_centre", "New Tampines Hawker Centre", 260, 30,
          status="Under Construction", size_hint=36.0)
    point("school", "TAMPINES PRIMARY SCHOOL", 600, 100,
          subtype="PRIMARY", postal="529301")
    point("school", "ANGKASA PRIMARY SCHOOL", 900, 200,
          subtype="PRIMARY", postal="529302")
    point("school", "FAR AWAY PRIMARY SCHOOL", 1600, 400,
          subtype="PRIMARY", postal="529303")
    point("school", "TAMPINES SECONDARY SCHOOL", 500, 300,
          subtype="SECONDARY", postal="529304")
    point("preschool", "PCF SPARKLETOTS TAMPINES", 120, 40, detail="PT1001")
    point("preschool", "MY FIRST SKOOL TAMPINES", 300, 90, detail="PT1002")
    point("park_facility", "Playground at Blk 501", 60, 10, subtype="PLAYGROUND")
    point("park_facility", "Fitness corner", 150, 30, subtype="FITNESS AREA")
    point("park_facility", "Hardcourt", 380, 40, subtype="MULTIPURPOSE COURT")
    point("park_facility", "Far playground", 900, 100, subtype="PLAYGROUND")
    point("park_facility", "Carpark B", 50, 5, subtype="CAR PARK")

    # A park whose centroid is a kilometre away but whose nearest edge is two
    # hundred metres: the case that makes the bounding box load-bearing.
    far_lat, far_lon = _offset(lat, lon, 1200, 0)
    edge_lat, _ = _offset(lat, lon, 200, 0)
    top_lat, _ = _offset(lat, lon, 2200, 0)
    west_lon = _offset(lat, lon, 0, -400)[1]
    east_lon = _offset(lat, lon, 0, 400)[1]
    rows.append((
        "park", "NATURE RESERVE", "Long Thin Park", "NP999", None, 184000.0,
        far_lat, far_lon, edge_lat, top_lat, west_lon, east_lon, None, "fixture",
    ))

    with conn:
        upsert_many(conn, "amenity", AMENITY_COLUMNS, rows, conflict="REPLACE")

    # Two gauges: a wet one near the fixture blocks and a dry one on the far
    # side of the island, so a rank has something to rank against.
    with conn:
        upsert_many(
            conn, "rainfall_station",
            ("station_id", "name", "lat", "lon", "year", "total_mm", "readings",
             "wet_readings", "rain_days", "days_observed", "source"),
            [
                ("S100", "Tampines Road", lat + 0.004, lon, 2024,
                 1800.0, 30000, 2400, 160, 360, "fixture"),
                ("S200", "Jurong West", 1.3480, 103.7000, 2024,
                 900.0, 30000, 1300, 120, 360, "fixture"),
                ("S300", "Changi", 1.3400, 103.9800, 2024,
                 1200.0, 30000, 1700, 140, 360, "fixture"),
            ],
            conflict="REPLACE",
        )

    # Land parcels: an empty residential plot to the west with a plot ratio,
    # a park that cannot be built on, and one URA wrote LND against.
    def parcel(objectid, use, gpr_raw, gpr, note, area, north, east, size_m=120):
        plat, plon = _offset(lat, lon, north, east)
        half = size_m / 2
        min_lat, min_lon = _offset(plat, plon, -half, -half)
        max_lat, max_lon = _offset(plat, plon, half, half)
        # The box as a real rectangle, so the fixture's parcels land on the
        # zoning layer the same way the Master Plan's do.
        shape = json.dumps({"type": "Polygon", "coordinates": [[
            [min_lon, min_lat], [max_lon, min_lat],
            [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat],
        ]]}, separators=(",", ":"))
        return (objectid, use, gpr_raw, gpr, note, area, plat, plon,
                min_lat, max_lat, min_lon, max_lon, shape, "fixture")

    with conn:
        from nhf.sources.ura import PARCEL_COLUMNS
        upsert_many(
            conn, "land_parcel", PARCEL_COLUMNS,
            [
                parcel(1, "RESIDENTIAL", "2.8", 2.8, None, 9000.0, 40, -150),
                parcel(2, "RESERVE SITE", "EVA", None,
                       "evaluated at planning stage, not fixed", 14000.0, 120, -260),
                parcel(3, "PARK", "NA", None, "not applicable to this use",
                       20000.0, 200, 0),
                parcel(4, "RESIDENTIAL", "LND", None, "landed housing, so low-rise",
                       5000.0, -180, 90),
                parcel(5, "RESIDENTIAL", "1.4", 1.4, None, 400.0, 30, 60),
            ],
            conflict="REPLACE",
        )
    return conn


@pytest.fixture()
def full_db(db):
    """The fixture store with every dataset and every batch step applied."""
    from nhf.fit import fit_lease_factors, fit_storey_premiums
    from nhf.sync import derive_town_medians

    add_surroundings(db)
    derive_town_medians(db)
    fit_lease_factors(db)
    fit_storey_premiums(db)
    return db
