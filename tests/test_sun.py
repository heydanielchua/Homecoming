"""Where the sun is, and whether it reaches a window.

The assertions here are physical facts about Singapore rather than values
copied out of the implementation, which is the only way to tell a correct
solar position algorithm from one that runs: at 1.35 degrees north the sun
passes within a degree and a half of the zenith at the equinox, reaches 65.2
degrees at the December solstice and 67.9 at the June one, and sets at an
azimuth that swings from 247 degrees in December to 293 in June.

The golden fixture at the end is shared with the TypeScript in web/, because
the browser has to redo the shading arithmetic for a floor and a facing that
are only typed in there.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from nhf.sun import (
    COMPASS,
    ISLAND_LAT,
    ISLAND_LON,
    LOW_SUN_ALTITUDE,
    EYE_HEIGHT_M,
    STOREY_HEIGHT_M,
    Obstruction,
    afternoon_track,
    blocks_sun,
    describe,
    exposure,
    solar_position,
    viewer_height_m,
)

GOLDEN = Path(__file__).resolve().parent / "golden" / "sun.json"


def _highest(when: date) -> tuple[float, float]:
    """Solar noon altitude and clock hour, by search rather than by formula."""
    best_hour, best = 0.0, -90.0
    for minute in range(600, 900):
        pos = solar_position(ISLAND_LAT, ISLAND_LON, when, minute / 60.0)
        if pos.altitude > best:
            best_hour, best = minute / 60.0, pos.altitude
    return best, best_hour


def _sunset(when: date) -> tuple[float, float]:
    """The last azimuth and hour at which the sun is still up."""
    last = (0.0, 0.0)
    for minute in range(1000, 1250):
        pos = solar_position(ISLAND_LAT, ISLAND_LON, when, minute / 60.0)
        if pos.altitude > 0:
            last = (pos.azimuth, minute / 60.0)
    return last


class TestSolarPosition:
    """Against the sky, not against the code."""

    def test_the_equinox_sun_passes_almost_overhead(self):
        altitude, _ = _highest(date(2026, 3, 20))
        # 90 minus the latitude, to within the sampling step.
        assert altitude == pytest.approx(88.65, abs=0.3)

    def test_the_december_solstice_noon_sun_is_in_the_south(self):
        """90 - (1.35 + 23.44): the sun is south of us, so a south window
        gets the only real midday sun of the year."""
        altitude, _ = _highest(date(2026, 12, 21))
        assert altitude == pytest.approx(65.2, abs=0.3)
        pos = solar_position(ISLAND_LAT, ISLAND_LON, date(2026, 12, 21), 13.0)
        assert 170 < pos.azimuth < 190

    def test_the_june_solstice_noon_sun_is_in_the_north(self):
        """90 - (23.44 - 1.35), and on the other side of the zenith — which is
        the part a temperate-latitude intuition gets wrong."""
        altitude, _ = _highest(date(2026, 6, 21))
        assert altitude == pytest.approx(67.9, abs=0.3)
        pos = solar_position(ISLAND_LAT, ISLAND_LON, date(2026, 6, 21), 13.0)
        assert pos.azimuth < 15 or pos.azimuth > 345

    def test_sunset_swings_from_south_of_west_to_north_of_west(self):
        december, _ = _sunset(date(2026, 12, 21))
        june, _ = _sunset(date(2026, 6, 21))
        equinox, _ = _sunset(date(2026, 3, 20))
        assert december == pytest.approx(246.6, abs=1.0)
        assert june == pytest.approx(293.4, abs=1.0)
        assert equinox == pytest.approx(270.0, abs=1.0)

    def test_the_sun_sets_within_half_an_hour_of_seven_all_year(self):
        """Singapore's day length barely changes — the whole year's sunsets
        fit in about twenty-five minutes — which is why the afternoon window
        can be a fixed one.

        These are geometric sunsets, so they land two or three minutes before
        the published ones: refraction lifts the disc over the horizon after
        its centre has geometrically set, and none of this is fine enough for
        that to matter.
        """
        # Earliest is 18:47 in November, latest 19:17 in February: half an
        # hour of variation over a whole year, against four hours in London.
        sunsets = [_sunset(date(2026, month, 15))[1] for month in range(1, 13)]
        assert min(sunsets) > 18.7, min(sunsets)
        assert max(sunsets) < 19.35, max(sunsets)
        assert max(sunsets) - min(sunsets) < 0.6

    def test_solar_noon_is_after_one_oclock(self):
        """The island is 16 degrees east of its own time meridian, so the
        afternoon sun runs late. A window ending at 18:00 would miss the
        worst of it."""
        for month in (1, 4, 7, 11):
            _, hour = _highest(date(2026, month, 15))
            assert 12.7 < hour < 13.4, month

    def test_the_azimuth_is_stable_through_the_overhead_passage(self):
        """The arccos form of the azimuth formula loses its precision near
        the zenith and can be ninety degrees out. At 1.35 N the sun passes
        the zenith twice a year, so this is the one place it would break."""
        previous = None
        for minute in range(700, 800):
            pos = solar_position(ISLAND_LAT, ISLAND_LON, date(2026, 3, 20), minute / 60.0)
            if previous is not None:
                step = abs((pos.azimuth - previous + 180) % 360 - 180)
                assert step < 30, f"azimuth jumped {step} degrees at {minute / 60.0}"
            previous = pos.azimuth


class TestFacing:
    def test_an_east_facing_flat_gets_no_afternoon_sun(self):
        """The whole reason "west sun" is the complaint and "east sun" is
        not: after three in the afternoon the sun is behind that wall every
        day of the year."""
        for facing in ("E", "SE", "NE"):
            assert exposure(facing).hours == 0.0

    def test_west_is_the_worst_facing(self):
        hours = {f: exposure(f).low_hours for f in COMPASS}
        assert max(hours, key=hours.get) == "W"
        assert hours["W"] > hours["SW"] > hours["S"]
        assert hours["W"] > hours["NW"] > hours["N"]

    def test_south_sun_is_not_a_west_sun_problem(self):
        """A temperate-climate tool would flag south-facing as the hot one.
        Here it gets a seventh of what west does, and only in December."""
        assert exposure("S").low_hours < exposure("W").low_hours / 5
        assert exposure("S").peak_month == 12
        assert exposure("N").peak_month == 6

    def test_a_bad_facing_is_an_error_not_a_zero(self):
        with pytest.raises(ValueError):
            exposure("WESTISH")


class TestShading:
    def test_a_tall_block_to_the_west_takes_the_afternoon_away(self):
        """The PRD's own observation: the thing that ruins the view saves the
        afternoons."""
        tall = [Obstruction(bearing=270.0, distance_m=60.0, height_m=30.0,
                            half_width=25.0, label="the next block")]
        open_face = exposure("W")
        shaded = exposure("W", obstructions=tall)
        assert shaded.low_hours == open_face.low_hours      # the sun is still there
        assert shaded.blocked_low_hours > 0                 # it just does not arrive
        assert shaded.net_low_hours < open_face.net_low_hours / 2
        assert "the next block" in shaded.shaded_by

    def test_a_low_block_shades_nothing_from_high_up(self):
        low = [Obstruction(bearing=270.0, distance_m=60.0, height_m=-20.0,
                           half_width=25.0, label="a carpark")]
        assert exposure("W", obstructions=low).blocked_hours == 0.0

    def test_a_block_beside_the_sun_does_not_block_it(self):
        """Angular width is what decides it, not distance alone."""
        narrow = [Obstruction(bearing=180.0, distance_m=40.0, height_m=40.0,
                              half_width=10.0, label="to the south")]
        assert exposure("W", obstructions=narrow).blocked_hours == 0.0

    def test_blocks_sun_names_what_is_in_the_way(self):
        items = [Obstruction(bearing=265.0, distance_m=50.0, height_m=25.0,
                             half_width=15.0, label="Block 123")]
        assert blocks_sun(items, azimuth=270.0, altitude=10.0) == "Block 123"
        assert blocks_sun(items, azimuth=270.0, altitude=40.0) is None   # sun above it
        assert blocks_sun(items, azimuth=180.0, altitude=10.0) is None   # wrong side

    def test_a_fully_shaded_face_says_what_shades_it(self):
        wall = [Obstruction(bearing=270.0, distance_m=20.0, height_m=60.0,
                            half_width=90.0, label="Block 1")]
        assert "Block 1" in describe(exposure("W", obstructions=wall))

    def test_a_face_with_no_sun_says_so_without_blaming_a_neighbour(self):
        """East has no afternoon sun to lose, so nothing is shading it."""
        assert "faces away" in describe(exposure("E"))


class TestViewerHeight:
    def test_the_ground_floor_looks_out_from_eye_level(self):
        """Not from the pavement. The eye height is there for one comparison
        that comes up constantly — a neighbouring block of the same height —
        and getting it wrong makes every such block read as taller."""
        assert viewer_height_m(1) == EYE_HEIGHT_M

    def test_each_storey_adds_one_floor_to_floor_height(self):
        assert viewer_height_m(10) == 9 * STOREY_HEIGHT_M + EYE_HEIGHT_M

    def test_an_unknown_storey_stays_unknown(self):
        """The PRD's rule: leave the floor out and the sun rows read unknown."""
        assert viewer_height_m(None) is None
        assert viewer_height_m(0) is None


class TestGoldenFixture:
    """One file, two implementations.

    The browser redoes this arithmetic because the facing and the floor are
    typed in there, so the numbers have two homes and no way to notice a
    drift. This writes the shared fixture; `web/src/lib/sun.test.ts` reads it
    and has to agree.
    """

    def test_the_golden_fixture_matches(self):
        assert GOLDEN.exists(), (
            f"{GOLDEN} is missing — run `python -m tests.make_golden` to write it"
        )
        golden = json.loads(GOLDEN.read_text())

        rebuilt = [s.as_dict() for s in afternoon_track(
            golden["lat"], golden["lon"], golden["year"], golden["step_minutes"]
        )]
        assert rebuilt == golden["track"], (
            "the afternoon track changed; regenerate the fixture and re-run "
            "the TypeScript test so both sides move together"
        )

        for case in golden["cases"]:
            obstructions = [Obstruction(**o) for o in case["obstructions"]]
            got = exposure(
                case["facing"], afternoon_track(
                    golden["lat"], golden["lon"], golden["year"],
                    golden["step_minutes"]),
                obstructions,
            ).as_dict()
            assert got == case["expected"], case["name"]

    def test_the_low_sun_cutoff_is_in_the_fixture(self):
        """The TypeScript needs the same constants, so they travel with it."""
        golden = json.loads(GOLDEN.read_text())
        assert golden["constants"]["low_sun_altitude"] == LOW_SUN_ALTITUDE
        assert golden["constants"]["storey_height_m"] == STOREY_HEIGHT_M
