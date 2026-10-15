"""Write the fixtures shared between the Python and the TypeScript.

Two pieces of arithmetic have to live in both places, because the browser is
where the floor and the facing are typed in and the batch job is where the
block data is: the afternoon sun track with its shading, and which
neighbouring blocks stand in front of a view from a given floor. Having one
of them drift from the other would be invisible — both sides would keep
returning plausible numbers — so the answers are written out here once and
each implementation is tested against the same file.

    python -m tests.make_golden

Run it after changing anything in `nhf/sun.py` or `nhf/view.py`, commit the
result, and re-run both test suites.
"""

from __future__ import annotations

import json
from pathlib import Path

from nhf.sun import (
    FACE_HALF_ANGLE,
    ISLAND_LAT,
    ISLAND_LON,
    LOW_SUN_ALTITUDE,
    MIN_ALTITUDE,
    STOREY_HEIGHT_M,
    Obstruction,
    afternoon_track,
    exposure,
)
from nhf.view import (
    BLOCKED_SLACK_M,
    NOMINAL_BLOCK_LENGTH_M,
    Neighbour,
    view_from,
)

OUT = Path(__file__).resolve().parent / "golden"

YEAR = 2026
STEP_MINUTES = 30

# The cases are the ones that would hide a disagreement: an open west face, a
# face the sun never reaches, and the two shading geometries that matter — a
# neighbour tall enough to block and one too low to, from the same bearing.
SUN_CASES = [
    {"name": "open west face", "facing": "W", "obstructions": []},
    {"name": "east face, no afternoon sun", "facing": "E", "obstructions": []},
    {
        "name": "tall block due west",
        "facing": "W",
        "obstructions": [
            {"bearing": 270.0, "distance_m": 60.0, "height_m": 30.0,
             "half_width": 25.0, "label": "Block 123"},
        ],
    },
    {
        "name": "same block, seen from above it",
        "facing": "W",
        "obstructions": [
            {"bearing": 270.0, "distance_m": 60.0, "height_m": -9.0,
             "half_width": 25.0, "label": "Block 123"},
        ],
    },
    {
        "name": "two neighbours, one to the north-west",
        "facing": "NW",
        "obstructions": [
            {"bearing": 300.0, "distance_m": 45.0, "height_m": 18.0,
             "half_width": 20.0, "label": "Block 124"},
            {"bearing": 250.0, "distance_m": 80.0, "height_m": 40.0,
             "half_width": 15.0, "label": "Block 125"},
        ],
    },
]

# A block of 16 storeys with four neighbours: one taller and close, one taller
# and far, one shorter, and one at the same height. Seen from the 4th floor
# and from the 15th, which is where a view either exists or does not.
VIEW_NEIGHBOURS = [
    {"name": "664A", "distance_m": 55.0, "bearing": 272.0, "storeys": 25,
     "max_floor_lvl": 25},
    {"name": "671", "distance_m": 180.0, "bearing": 95.0, "storeys": 30,
     "max_floor_lvl": 30},
    {"name": "668", "distance_m": 70.0, "bearing": 10.0, "storeys": 4,
     "max_floor_lvl": 4},
    {"name": "669A", "distance_m": 40.0, "bearing": 180.0, "storeys": 16,
     "max_floor_lvl": 16},
    # One block with its footprint measured, so both implementations are held
    # to the real-shape path and not only to the nominal fallback. The span is
    # deliberately off-centre from the bearing: a long slab seen obliquely
    # covers an arc whose middle is not where its centroid is, and reading the
    # centre from the bearing is the mistake this case exists to catch.
    {"name": "670", "distance_m": 120.0, "bearing": 45.0, "storeys": 20,
     "max_floor_lvl": 20, "span": [58.0, 21.5]},
]
VIEW_CASES = [
    {"name": "low floor", "storey": 4},
    {"name": "high floor", "storey": 15},
    {"name": "top floor", "storey": 16},
    {"name": "floor not given", "storey": None},
    # The case that was reporting a clear view in every direction: an empty
    # neighbour list because the block never geocoded, not because there is
    # nothing around it.
    {"name": "block not geocoded", "storey": 8, "located": False},
]


def sun_fixture() -> dict:
    track = afternoon_track(ISLAND_LAT, ISLAND_LON, YEAR, STEP_MINUTES)
    cases = []
    for case in SUN_CASES:
        obstructions = [Obstruction(**o) for o in case["obstructions"]]
        cases.append({
            **case,
            "expected": exposure(case["facing"], track, obstructions).as_dict(),
        })
    return {
        "lat": ISLAND_LAT,
        "lon": ISLAND_LON,
        "year": YEAR,
        "step_minutes": STEP_MINUTES,
        # Every constant the browser needs, and exactly the set `meta.json`
        # publishes — `tests/test_report.py` checks the two agree, so a
        # constant added to one cannot go missing from the other. The
        # TypeScript reads these rather than repeating them, because a
        # constant repeated in two languages is a constant that will differ
        # in one of them.
        "constants": {
            "low_sun_altitude": LOW_SUN_ALTITUDE,
            "min_altitude": MIN_ALTITUDE,
            "face_half_angle": FACE_HALF_ANGLE,
            "storey_height_m": STOREY_HEIGHT_M,
            "nominal_block_length_m": NOMINAL_BLOCK_LENGTH_M,
            "blocked_slack_m": BLOCKED_SLACK_M,
        },
        "track": [s.as_dict() for s in track],
        "cases": cases,
    }


def view_fixture() -> dict:
    neighbours = [Neighbour(**n) for n in VIEW_NEIGHBOURS]
    cases = []
    for case in VIEW_CASES:
        located = case.get("located", True)
        cases.append({
            **case,
            "expected": view_from(
                neighbours if located else [], case["storey"], 16, located,
            ).as_dict(),
        })
    return {
        "block_max_floor": 16,
        # Two shapes of the same four blocks, because the two sides consume
        # different ones. Python builds `Neighbour` objects from constructor
        # arguments; the browser reads what a block file actually ships, which
        # is `as_dict`. Deriving the second from the first keeps one source
        # and stops either side being tested against its own idea of the
        # other's input.
        "neighbours_in": VIEW_NEIGHBOURS,
        "neighbours": [n.as_dict() for n in neighbours],
        "cases": cases,
    }


# Real blocks on real streets, including the two that share 266B and the two
# streets where ST is SAINT rather than STREET.
LINK_BLOCKS = [
    ("669C", "JURONG WEST ST 64", "JURONG WEST"),
    ("266B", "PUNGGOL WAY", "PUNGGOL"),
    ("266B", "COMPASSVALE BOW", "SENGKANG"),
    ("101", "ANG MO KIO AVE 3", "ANG MO KIO"),
    ("220", "BT BATOK EAST AVE 3", "BUKIT BATOK"),
    ("12", "ST GEORGE'S RD", "KALLANG/WHAMPOA"),
    ("1", "LOR 7 TOA PAYOH", "TOA PAYOH"),
    ("406", "TAMPINES ST 41", "TAMPINES"),
    ("18", "C'WEALTH AVE WEST", "QUEENSTOWN"),
    ("519", "PASIR RIS ST 13", "PASIR RIS"),
]

LINK_CASES = [
    "https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF",
    "https://www.99.co/singapore/sale/property/669c-jurong-west-st-64-hdb-eSeF",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-25099211",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-266b-compassvale-bow-25099211",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-266b-25099211",
    "https://www.99.co/singapore/sale/property/101-ang-mo-kio-avenue-3-hdb-xK9q",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-220-bukit-batok-east-avenue-3-1234567",
    "https://www.99.co/singapore/sale/property/12-st-georges-road-hdb-x1",
    "https://www.99.co/singapore/rent/property/1-lorong-7-toa-payoh-hdb-q9",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-406-tampines-street-41-25099211",
    "https://www.99.co/singapore/sale/property/18-commonwealth-avenue-west-hdb-z",
    "https://www.99.co/singapore/sale/property/519-pasir-ris-street-13-hdb-z",
    "propertyguru.com.sg/listing/hdb-for-sale-266b-punggol-way-1",
    "https://www.99.co/singapore/sale/property/669c-jurong-west-street-64-hdb-eSeF?utm_source=x#photos",
    "https://www.propertyguru.com.sg/listing/hdb-for-sale-999z-nowhere-street-1",
    "https://example.com/some/listing/page",
    "",
]


def link_fixture() -> dict:
    """URL in, block out — for both implementations of the same lookup.

    The browser resolves against the sharded index files rather than against
    `nhf.listing`, so the fixture carries those files as well as the answers.
    A TypeScript resolver reading this has everything a real visit would
    have, and nothing it would not.
    """
    from nhf.db import open_db, upsert_many
    from nhf.listing import RESOLVED, build_index, resolve_link
    from nhf.site import block_slug, index_payloads

    conn = open_db(":memory:")
    with conn:
        upsert_many(
            conn, "block", ("block_key", "block", "street_name", "town"),
            [(f"{b}|{s}", b, s, town) for b, s, town in LINK_BLOCKS],
            conflict="REPLACE",
        )
    index = build_index(conn)
    keys = {f"{b}|{s}" for b, s, _ in LINK_BLOCKS}
    shards = {number: payload for number, payload in index_payloads(conn, keys)}

    cases = []
    for url in LINK_CASES:
        result = resolve_link(url, index)
        expected: dict = {"status": result.status}
        if result.status == RESOLVED:
            expected["slug"] = block_slug(result.block, result.street_name)
        elif result.candidates:
            expected["candidates"] = sorted(
                block_slug(*key.split("|", 1)) for key in result.candidates
            )
        cases.append({"url": url, "expected": expected})

    return {
        "blocks": [
            {"block": b, "street": s, "town": t, "slug": block_slug(b, s)}
            for b, s, t in LINK_BLOCKS
        ],
        "index": shards,
        "cases": cases,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, payload in (("sun", sun_fixture()), ("view", view_fixture()),
                          ("links", link_fixture())):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
