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
from nhf.view import Neighbour, view_from

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
]
VIEW_CASES = [
    {"name": "low floor", "storey": 4},
    {"name": "high floor", "storey": 15},
    {"name": "top floor", "storey": 16},
    {"name": "floor not given", "storey": None},
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
        "constants": {
            "low_sun_altitude": LOW_SUN_ALTITUDE,
            "min_altitude": MIN_ALTITUDE,
            "face_half_angle": FACE_HALF_ANGLE,
            "storey_height_m": STOREY_HEIGHT_M,
        },
        "track": [s.as_dict() for s in track],
        "cases": cases,
    }


def view_fixture() -> dict:
    neighbours = [Neighbour(**n) for n in VIEW_NEIGHBOURS]
    cases = []
    for case in VIEW_CASES:
        cases.append({
            **case,
            "expected": view_from(neighbours, case["storey"], 16).as_dict(),
        })
    return {
        "block_max_floor": 16,
        "neighbours": VIEW_NEIGHBOURS,
        "cases": cases,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, payload in (("sun", sun_fixture()), ("view", view_fixture())):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
