"""What is near a block, and the rainfall ranking.

The fixture places each thing at a known distance from the cheap Tampines
block, so these are assertions about the right answer. Two of them exist
because the obvious implementation gets them wrong: a park is measured to its
edge and not its middle, and a hawker centre that has not been built yet is
not somewhere you can eat.
"""

from __future__ import annotations

import pytest

from nhf.geo import haversine_m
from nhf.nearby import (
    MAP_LIMITS,
    MIN_RAIN_READINGS,
    SCHOOL_BAND_M,
    WALKING_M,
    nearby_for,
    nearest_point_on_box,
    rain_for,
)

BLOCK = "501|TAMPINES ST 42"


class TestNearby:
    def test_it_finds_the_nearer_of_two_exits(self, full_db):
        """The PRD asks for an exit because that is what you walk to. The
        fixture has two exits of one station, 200 m apart."""
        near = nearby_for(full_db, BLOCK)
        assert near.mrt_exit.name.endswith("Exit A")
        assert near.mrt_station == "TAMPINES MRT STATION"
        assert near.mrt_exit.distance_m == pytest.approx(300, abs=15)

    def test_the_nearest_bus_stop_is_by_number(self, full_db):
        near = nearby_for(full_db, BLOCK)
        assert near.bus_stop.name == "76059"
        assert near.bus_stop.distance_m == pytest.approx(92, abs=10)

    def test_a_hawker_centre_still_being_built_is_a_separate_row(self, full_db):
        """Six of the 129 are under construction. One opening nearby is a
        real change to a neighbourhood, and it is also not lunch today."""
        near = nearby_for(full_db, BLOCK)
        assert near.hawker.name == "Tampines Round Market"
        assert near.hawker_opening.name == "New Tampines Hawker Centre"
        # The one being built is nearer, and still is not the answer.
        assert near.hawker_opening.distance_m < near.hawker.distance_m

    def test_the_stall_count_comes_with_it(self, full_db):
        assert nearby_for(full_db, BLOCK).hawker.size_hint == 43.0

    def test_a_park_is_measured_to_its_edge(self, full_db):
        """The fixture park's centroid is 1.2 km away and its nearest edge is
        200 m. Measuring to the centroid would report a block beside a park
        as nowhere near one."""
        park = nearby_for(full_db, BLOCK).park
        assert park.distance_m == pytest.approx(200, abs=15)
        assert park.to_edge is True
        assert park.size_hint == 184000.0

    def test_primary_schools_are_counted_within_a_kilometre(self, full_db):
        """Two of the three fixture primaries are inside the band; the third
        is at 1.6 km and is not."""
        near = nearby_for(full_db, BLOCK)
        assert near.primary_school.name == "TAMPINES PRIMARY SCHOOL"
        assert near.schools_within_1km == 2

    def test_a_secondary_school_is_not_counted_as_a_primary(self, full_db):
        near = nearby_for(full_db, BLOCK)
        assert "SECONDARY" not in near.primary_school.name

    def test_facilities_are_counted_within_walking_distance(self, full_db):
        near = nearby_for(full_db, BLOCK)
        assert near.facilities == {
            "playground": 1, "fitness_area": 1, "multipurpose_court": 1}
        assert near.nearest_facility["playground"].distance_m < WALKING_M

    def test_car_parks_are_not_an_amenity(self, full_db):
        """The layer is 63% car parks and none of them is a reason to buy."""
        assert "car_park" not in nearby_for(full_db, BLOCK).facilities

    def test_a_far_playground_is_not_walkable(self, full_db):
        """The fixture has one at 900 m, which is not "in walking distance"."""
        assert nearby_for(full_db, BLOCK).facilities["playground"] == 1

    def test_preschools_are_counted(self, full_db):
        assert nearby_for(full_db, BLOCK).preschools_within_walk == 2


class TestMissingIsNotNone:
    def test_an_unloaded_layer_reads_as_unknown_and_says_why(self, db):
        """The PRD's rule. With no amenity layers loaded, "nearest hawker
        centre: none" would be a false statement about Singapore."""
        near = nearby_for(db, BLOCK)
        assert near.hawker is None
        assert near.missing
        assert any("hawker centres have not been loaded" in m for m in near.missing)
        # And the counts stay unknown rather than becoming zero.
        assert near.schools_within_1km is None
        assert near.preschools_within_walk is None

    def test_a_loaded_layer_reports_nothing_missing(self, full_db):
        assert nearby_for(full_db, BLOCK).missing == []

    def test_a_block_with_no_geocode_measures_nothing(self, full_db):
        full_db.execute(
            "UPDATE block SET lat = NULL, lon = NULL WHERE block_key = ?", (BLOCK,))
        near = nearby_for(full_db, BLOCK)
        assert near.mrt_exit is None
        assert "no geocode" in near.missing[0]

    def test_a_count_within_the_band_is_zero_only_when_the_layer_is_there(
        self, full_db
    ):
        """Zero schools is a real answer once the schools are loaded."""
        full_db.execute("DELETE FROM amenity WHERE kind = 'school' AND name LIKE '%PRIMARY%'")
        full_db.execute(
            "INSERT INTO amenity (kind, subtype, name, lat, lon, source)"
            " VALUES ('school', 'SECONDARY', 'ONLY A SECONDARY', 1.4, 103.7, 'fixture')"
        )
        near = nearby_for(full_db, BLOCK)
        assert near.schools_within_1km == 0
        assert near.primary_school is None


class TestRain:
    def test_the_nearest_gauge_is_ranked_against_the_island(self, full_db):
        """A rank, not a total: the readings are sampled, so only the ordering
        between stations survives."""
        rain = rain_for(full_db, 1.3620, 103.9550)
        assert rain.station == "Tampines Road"
        assert (rain.rank, rain.of) == (1, 3)
        assert rain.wetter_than_pct == 100.0
        assert rain.mm_per_1000_readings == 60.0

    def test_a_drier_part_of_the_island_ranks_lower(self, full_db):
        dry = rain_for(full_db, 1.3480, 103.7000)
        assert dry.station == "Jurong West"
        assert dry.rank == 3
        assert dry.wetter_than_pct == 0.0

    def test_the_rank_is_by_rate_not_by_total(self, full_db):
        """Stations land in an unequal number of sampled pages, so comparing
        totals would rank whichever gauge happened to be sampled most."""
        full_db.execute(
            "UPDATE rainfall_station SET total_mm = 5000, readings = 200000"
            " WHERE station_id = 'S200'"
        )
        # 5000/200000 = 0.025 mm per reading, against Tampines' 0.06.
        assert rain_for(full_db, 1.3480, 103.7000).rank == 3

    def test_a_thinly_sampled_gauge_is_not_ranked_as_the_driest_place(self, full_db):
        """The sample catches gauges unevenly. Three real stations came back
        with 33 to 48 readings and 0.0 mm between them, which would rank them
        the driest places in Singapore on two hours of weather."""
        full_db.execute(
            "INSERT INTO rainfall_station (station_id, name, lat, lon, year,"
            " total_mm, readings, source)"
            " VALUES ('S999', 'Barely Sampled', 1.3530, 103.9460, 2024, 0.0, 33, 'fixture')"
        )
        rain = rain_for(full_db, 1.3530, 103.9460)
        # The thin gauge is nearest, and is still not the answer.
        assert rain.station != 'Barely Sampled'
        assert rain.rank is not None

    def test_a_well_sampled_gauge_with_no_rain_is_still_ranked(self, full_db):
        """Dry is a real answer; unsampled is not. The threshold has to tell
        them apart rather than dropping every zero."""
        full_db.execute(
            "INSERT INTO rainfall_station (station_id, name, lat, lon, year,"
            " total_mm, readings, source)"
            " VALUES ('S998', 'Genuinely Dry', 1.3530, 103.9460, 2024, 0.0, 30000, 'fixture')"
        )
        rain = rain_for(full_db, 1.3530, 103.9460)
        assert rain.station == 'Genuinely Dry'
        assert rain.rank == rain.of          # the driest of them all

    def test_a_year_sampled_too_thinly_to_rank_says_so(self, db):
        """Rather than ranking one gauge against itself."""
        db.execute(
            "INSERT INTO rainfall_station (station_id, name, lat, lon, year,"
            " total_mm, readings, source)"
            " VALUES ('S997', 'Thin', 1.35, 103.82, 2024, 1.0, 10, 'fixture')"
        )
        rain = rain_for(db, 1.35, 103.82)
        assert rain.rank is None
        assert "sampled too thinly" in rain.unknown

    def test_no_sample_loaded_says_how_to_load_it(self, db):
        rain = rain_for(db, 1.35, 103.82)
        assert rain.rank is None
        assert "nhf sync --step rainfall" in rain.unknown

    def test_a_block_with_no_geocode_has_no_gauge(self, db):
        assert "no geocode" in rain_for(db, None, None).unknown


class TestMapPoints:
    """What the map is allowed to draw.

    The map is the one place a wrong number becomes a wrong *place*, which
    reads as certainty. So these pin down the two things that would make it
    lie: a point without a real geocode, and a point invented from the
    compass bearing the text rows use.
    """

    def test_every_point_carries_a_real_coordinate(self, full_db):
        near = nearby_for(full_db, BLOCK)
        assert near.points
        for place in near.points:
            assert place.lat is not None and place.lon is not None

    def test_a_point_sits_where_the_amenity_is_not_where_its_compass_says(
        self, full_db
    ):
        """The text rows round a bearing to one of eight compass points. If
        the map ever derived a position from that instead of reading the
        geocode, a thing due north-east by 30 degrees would move. The bus
        stop is the check: its stored coordinate is the one that comes back.
        """
        near = nearby_for(full_db, BLOCK)
        stop = next(p for p in near.points if p.kind == "bus_stop")
        stored = full_db.execute(
            "SELECT lat, lon FROM amenity WHERE kind = 'bus_stop' AND name = ?",
            (stop.name,),
        ).fetchone()
        assert stop.lat == pytest.approx(stored["lat"], abs=1e-9)
        assert stop.lon == pytest.approx(stored["lon"], abs=1e-9)

    def test_a_marker_sits_where_its_own_distance_says_it_is(self, full_db):
        """The one that caught a real bug.

        A park is measured to its edge, because that is what you walk to, but
        its stored coordinate is the centroid of the whole shape. Drawing the
        centroid put Kallang Riverside Park 1,298 m away on the map while the
        row beside it said 796 m — and the map then zoomed out far enough to
        fit the mistake, which is how it was noticed.

        So: every marker must agree with the distance printed next to it.
        """
        near = nearby_for(full_db, BLOCK)
        block = full_db.execute(
            "SELECT lat, lon FROM block WHERE block_key = ?", (BLOCK,)
        ).fetchone()
        for place in near.points:
            drawn = haversine_m(block["lat"], block["lon"], place.lat, place.lon)
            assert drawn == pytest.approx(place.distance_m, abs=1.0), (
                f"{place.kind} {place.name!r} is drawn {drawn:.0f} m away "
                f"but reports {place.distance_m:.0f} m"
            )

    def test_the_nearest_point_on_a_box_is_the_clamp(self):
        box = (1.30, 1.32, 103.80, 103.82)
        # Outside on two sides: both clamp.
        assert nearest_point_on_box(1.25, 103.75, *box) == (1.30, 103.80)
        # Beyond one edge only: the other coordinate is kept.
        assert nearest_point_on_box(1.31, 103.90, *box) == (1.31, 103.82)
        # Inside: the place itself, which is a distance of zero.
        assert nearest_point_on_box(1.31, 103.81, *box) == (1.31, 103.81)

    def test_a_shape_with_no_box_is_left_without_a_clamp(self):
        assert nearest_point_on_box(1.31, 103.81, None, 1.32, 103.80, 103.82) == (
            None,
            None,
        )

    def test_each_kind_is_capped_so_one_layer_cannot_bury_the_rest(self, full_db):
        near = nearby_for(full_db, BLOCK)
        counts: dict[str, int] = {}
        for place in near.points:
            counts[place.kind] = counts.get(place.kind, 0) + 1
        for kind, count in counts.items():
            assert count <= MAP_LIMITS[kind], f"{kind} is over its cap"

    def test_the_points_come_out_nearest_first(self, full_db):
        near = nearby_for(full_db, BLOCK)
        distances = [p.distance_m for p in near.points]
        assert distances == sorted(distances)

    def test_the_serialised_point_is_small_and_rounded(self, full_db):
        near = nearby_for(full_db, BLOCK)
        point = near.points[0].as_point()
        assert set(point) <= {"kind", "name", "lat", "lon", "distance_m", "subtype"}
        # Six places is 0.1 m here, which is finer than the sources are.
        assert len(str(point["lat"]).split(".")[-1]) <= 6
        assert isinstance(point["distance_m"], int)

    def test_a_block_with_no_geocode_draws_nothing(self, db):
        """Rather than drawing the island's centre and calling it the block."""
        near = nearby_for(db, BLOCK)
        assert near.points == []


def test_the_school_band_is_the_one_the_prd_asks_for():
    """Within 1 km of a primary school is the band that moves a resale price,
    which is why it is the band reported rather than our own walk."""
    assert SCHOOL_BAND_M == 1000.0
