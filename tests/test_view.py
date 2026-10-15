"""What blocks the view, and what could still be built in it.

The two halves are tested differently on purpose. What stands there now is
two published numbers and some trigonometry, so it is checked exactly. What
could be built is an estimate, so what is checked is that it comes back as a
range, that it says where the range came from, and above all that URA's `LND`
and `EVA` do not collapse into the same answer — one of them settles the
question and the other admits nobody has decided.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nhf.sun import EYE_HEIGHT_M, STOREY_HEIGHT_M, exposure
from nhf.view import (
    LANDED_STOREYS,
    Neighbour,
    coverage_band,
    fit_height_bands,
    height_band,
    neighbours_of,
    parcels_near,
    view_from,
)

GOLDEN = Path(__file__).resolve().parent / "golden" / "view.json"


def _n(name, distance, bearing, storeys):
    return Neighbour(name=name, distance_m=distance, bearing=bearing, storeys=storeys)


class TestWhatStandsThere:
    def test_a_taller_block_blocks_a_low_floor(self):
        view = view_from([_n("664A", 55.0, 270.0, 25)], storey=4)
        assert len(view.blocked) == 1
        blocked = view.blocked[0]
        # Its highest floor is 24 storeys up; your eyes are 3 storeys up plus
        # eye height.
        assert blocked.rises_m == pytest.approx(
            24 * STOREY_HEIGHT_M - (3 * STOREY_HEIGHT_M + EYE_HEIGHT_M))
        assert blocked.elevation_deg == pytest.approx(48.2, abs=0.2)
        assert "W" not in view.open_directions
        assert blocked.as_dict()["storeys_above"] == pytest.approx(20.5, abs=0.1)

    def test_the_same_block_is_below_a_high_floor(self):
        view = view_from([_n("668", 70.0, 10.0, 4)], storey=15)
        assert view.blocked == []
        assert view.above_everything
        assert "N" in view.open_directions

    def test_a_block_of_equal_height_does_not_take_the_view(self):
        """Its top flat is level with yours, so nobody is looking down at
        you. Comparing your eyes with its roof instead — a storey above its
        own top floor — would report a blocked view across half the estate."""
        view = view_from([_n("669A", 40.0, 180.0, 16)], storey=16)
        assert view.blocked == []
        assert [n.name for n in view.clear] == ["669A"]

    def test_one_storey_taller_does_take_it(self):
        view = view_from([_n("669A", 40.0, 180.0, 17)], storey=16)
        assert len(view.blocked) == 1
        assert view.blocked[0].as_dict()["storeys_above"] == pytest.approx(0.5, abs=0.1)

    def test_two_blocks_in_one_direction_do_not_block_twice_the_horizon(self):
        """Summing the widths would say they did."""
        one = view_from([_n("A", 60.0, 270.0, 30)], storey=4).blocked_arc_deg
        two = view_from(
            [_n("A", 60.0, 270.0, 30), _n("B", 62.0, 272.0, 30)], storey=4
        ).blocked_arc_deg
        assert two == pytest.approx(one, abs=3.0)

    def test_a_nearer_block_covers_more_horizon_than_a_far_one(self):
        near = view_from([_n("A", 40.0, 270.0, 30)], storey=4).blocked_arc_deg
        far = view_from([_n("A", 250.0, 270.0, 30)], storey=4).blocked_arc_deg
        assert near > far * 2

    def test_a_block_with_no_geocode_has_an_unknown_view_not_a_clear_one(self):
        """An empty neighbour list means two different things.

        From a placed block it means there is genuinely nothing within 300 m.
        From one that never geocoded it means nothing was measured — and
        reporting that as "nothing above you, open in every direction" is the
        false confidence this whole report exists to avoid. The list is
        identical in both cases, so the caller has to say which it is.
        """
        unplaced = view_from([], storey=8, located=False)
        assert unplaced.unknown and "no geocode" in unplaced.unknown
        assert unplaced.open_directions == []
        assert not unplaced.above_everything
        assert unplaced.obstructions() == []

        placed = view_from([], storey=8, located=True)
        assert placed.unknown is None
        assert len(placed.open_directions) == 8

    def test_no_floor_means_unknown_not_clear(self):
        """The PRD's rule: leave the floor out and the view rows read unknown.
        An empty `blocked` list would read as a clear view."""
        view = view_from([_n("664A", 55.0, 270.0, 25)], storey=None)
        assert view.unknown and "floor was not given" in view.unknown
        assert view.blocked == []
        assert not view.above_everything

    def test_a_neighbour_with_no_published_height_is_counted_as_neither(self):
        """Not knowing how tall it is is not evidence of a clear view."""
        view = view_from([Neighbour("X", 50.0, 270.0)], storey=4)
        assert view.blocked == [] and view.clear == []
        assert not view.above_everything

    def test_the_blocked_neighbours_become_sun_obstructions(self):
        """The view and the sun are the same geometry, which is the PRD's
        point: the block that ruins the view saves the afternoons."""
        view = view_from([_n("664A", 55.0, 270.0, 25)], storey=4)
        open_face = exposure("W")
        shaded = exposure("W", obstructions=view.obstructions())
        assert shaded.net_low_hours < open_face.net_low_hours
        assert "block 664A" in shaded.shaded_by

    def test_a_west_facing_flat_keeps_its_sun_above_the_neighbour(self):
        """The same equal-height block to the west: from the top floor the
        whole western afternoon arrives, from the 4th floor none of it
        does."""
        west = [_n("W16", 40.0, 270.0, 16)]
        top = exposure("W", obstructions=view_from(west, 16).obstructions())
        low = exposure("W", obstructions=view_from(west, 4).obstructions())
        assert top.net_low_hours > 600
        assert low.net_low_hours == 0.0


class TestWhatCouldBeBuilt:
    def test_landed_housing_is_a_confident_answer(self):
        """URA writes LND on 52% of parcels and it settles the question:
        landed housing is two or three storeys and cannot grow into a view."""
        band = height_band(None, None, "landed housing, so low-rise")
        assert (band.low, band.high) == LANDED_STOREYS
        assert band.basis == "zoning"

    def test_not_yet_evaluated_is_not_an_answer(self):
        """EVA is 11% of parcels, and reporting it like LND would turn an
        admission into a reassurance."""
        band = height_band(None, None, "evaluated at planning stage, not fixed")
        assert band.low is None and band.high is None
        assert band.basis == "none"
        assert "evaluated at planning stage" in band.note

    def test_a_plot_ratio_gives_a_range_and_says_where_from(self):
        band = height_band(2.8)
        assert band.low and band.high and band.high > band.low
        assert band.basis == "coverage"
        assert "2.8" in band.note and "site coverage" in band.note

    def test_a_fitted_band_says_what_it_rests_on(self):
        """Reading the storeys off blocks already built at that ratio is a
        better answer than dividing by an assumed coverage, because it is the
        same city's own practice — and it comes with the number of blocks
        behind it, so a ratio nobody has built much at reads as thin.

        It is usually narrower too: against the real land use layer the fitted
        band is tighter than the coverage guess at 9 of the 11 ratios with
        enough blocks to fit. Not all of them, though — the two widest-ratio
        fits rest on 130 and 41 blocks and come out slightly wider — so that
        is not asserted as a property.
        """
        fitted = height_band(2.8, {2.8: (18, 26, 431)})
        assert fitted.basis == "fitted" and fitted.n_blocks == 431
        assert (fitted.low, fitted.high) == (18, 26)
        assert "431 HDB blocks" in fitted.note
        assert height_band(2.8).basis == "coverage"

    def test_a_nearby_ratio_is_used_but_not_a_distant_one(self):
        fitted = {2.8: (18, 26, 431)}
        assert height_band(2.75, fitted).basis == "fitted"
        assert height_band(1.4, fitted).basis == "coverage"

    def test_no_ratio_at_all_says_so(self):
        band = coverage_band(None)
        assert band.low is None
        assert "no plot ratio" in band.note


class TestAgainstTheStore:
    def test_neighbours_come_with_their_published_heights(self, full_db):
        neighbours = neighbours_of(full_db, "501|TAMPINES ST 42")
        assert neighbours
        assert all(n.distance_m <= 300 for n in neighbours)
        assert any(n.max_floor_lvl for n in neighbours)
        # Sorted by distance, because the near ones are what matter.
        assert neighbours == sorted(neighbours, key=lambda n: n.distance_m)

    def test_a_block_with_no_geocode_has_no_neighbours_rather_than_all_of_them(
        self, full_db
    ):
        full_db.execute(
            "UPDATE block SET lat = NULL, lon = NULL WHERE block_key = ?",
            ("501|TAMPINES ST 42",),
        )
        assert neighbours_of(full_db, "501|TAMPINES ST 42") == []

    def test_parks_are_not_listed_as_developable_land(self, full_db):
        parcels = parcels_near(full_db, "501|TAMPINES ST 42")
        assert parcels
        assert not any((p.land_use or "").upper() == "PARK" for p in parcels)

    def test_a_sliver_of_land_is_left_out(self, full_db):
        """A 400 square metre residential parcel is a setback or a
        substation, not a future tower, and listing it would bury the one
        that matters."""
        parcels = parcels_near(full_db, "501|TAMPINES ST 42")
        assert all((p.area_sqm or 0) >= 1000 for p in parcels)

    def test_the_biggest_buildable_parcel_comes_first(self, full_db):
        parcels = parcels_near(full_db, "501|TAMPINES ST 42")
        assert parcels[0].buildable
        assert parcels[0].area_sqm == max(
            p.area_sqm for p in parcels if p.buildable)

    def test_height_bands_cannot_be_fitted_without_the_land_layer(self, db):
        """The normal state: the layer is 174 MB and is fetched on demand, so
        an empty fit has to be survivable rather than an error."""
        assert fit_height_bands(db) == {}

    def test_height_bands_fit_from_blocks_standing_on_known_ratios(self, full_db):
        fitted = fit_height_bands(full_db, min_blocks=1)
        assert fitted, "the fixture has parcels with ratios and blocks inside them"
        for gpr, (low, high, n) in fitted.items():
            assert low <= high and n >= 1


class TestGoldenFixture:
    """The browser redoes this for the floor that is typed in there."""

    def test_the_golden_fixture_matches(self):
        golden = json.loads(GOLDEN.read_text())
        neighbours = [Neighbour(**n) for n in golden["neighbours_in"]]
        for case in golden["cases"]:
            located = case.get("located", True)
            got = view_from(
                neighbours if located else [],
                case["storey"],
                golden["block_max_floor"],
                located,
            ).as_dict()
            assert got == case["expected"], case["name"]
