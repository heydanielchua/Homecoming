"""The report for one block.

Two things are worth testing here beyond "it builds". The fair value is
precomputed per five-floor band rather than per floor, and that is only
honest if the bands are the fitted curve's own bands — so a band boundary has
to land where the curve's does. And every figure has to carry how sure it is,
with a missing one reading as unknown-and-why rather than as a blank or a
zero, which is the PRD's central rule and the easiest thing to lose.
"""

from __future__ import annotations

import json

import pytest

from nhf.fit import STOREY_BAND_FLOORS, storey_band
from nhf.report import (
    ESTIMATED,
    FACT,
    UNKNOWN,
    WORKED,
    ReportBuilder,
    band_of,
    fig,
    storey_bands,
    unknown,
    verdict,
)

BLOCK = "501|TAMPINES ST 42"          # the deliberately cheap one
BUSY_BLOCK = "141|TAMPINES ST 42"     # an ordinary one, with more sales


@pytest.fixture()
def builder(full_db):
    return ReportBuilder(full_db)


def _figures(payload):
    """Every figure dict anywhere in the report, for the sweeping checks."""
    if isinstance(payload, dict):
        if "v" in payload and "c" in payload:
            yield payload
        for value in payload.values():
            yield from _figures(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _figures(item)


class TestConfidence:
    def test_every_figure_says_how_sure_it_is(self, builder):
        report = builder.build(BLOCK)
        figures = list(_figures(report))
        assert figures
        for figure in figures:
            assert figure["c"] in (FACT, WORKED, ESTIMATED, UNKNOWN), figure

    def test_a_missing_figure_is_never_a_blank_or_a_zero(self, builder):
        report = builder.build(BLOCK)
        for figure in _figures(report):
            if figure["v"] is None:
                assert figure["c"] == UNKNOWN
                assert figure.get("n"), f"no reason given: {figure}"

    def test_a_value_claimed_as_fact_cannot_be_missing(self):
        """The guard inside `fig`: pass None and it demotes itself to unknown
        rather than printing a confident nothing."""
        assert fig(None, FACT)["c"] == UNKNOWN
        assert fig(5, FACT)["c"] == FACT

    def test_an_unknown_carries_its_reason_where_a_value_would_have_been(self):
        u = unknown("the rainfall sample has not been loaded")
        assert u["v"] is None and u["c"] == UNKNOWN and u["n"]

    def test_a_sale_is_a_fact_and_a_fair_value_is_worked_out(self, builder):
        money = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["money"]
        assert money["median_price"]["c"] == FACT
        assert money["vs_town_pct"]["c"] == WORKED

    def test_a_lease_read_off_a_sale_is_a_fact(self, builder):
        lease = builder.build(BLOCK)["lease"]
        assert lease["commence_year"]["c"] == FACT
        assert lease["remaining_years"]["c"] == WORKED

    def test_a_lease_guessed_from_the_completion_year_is_an_estimate(self, full_db):
        """A block with no sale stating a lease start falls back to when it
        was completed, which is a year or two late — and says so."""
        full_db.execute(
            "UPDATE resale_txn SET lease_commence_year = NULL WHERE block_key = ?",
            (BLOCK,))
        lease = ReportBuilder(full_db).build(BLOCK)["lease"]
        assert lease["commence_year"]["c"] == ESTIMATED
        assert "completed" in lease["commence_year"]["n"]


class TestFairValueBands:
    def test_the_bands_are_the_fitted_curves_own_bands(self):
        """The whole reason a band is not an approximation: the storey premium
        is fitted in five-floor steps, so one value per band is exactly as
        fine as the evidence goes."""
        for storey in range(1, 40):
            assert band_of(storey) == storey_band(storey, storey)

    def test_the_bands_cover_the_block(self, builder):
        bands = storey_bands(12)
        assert bands == [1, 6, 11]
        assert band_of(12) == 11
        assert band_of(1) == 1
        assert band_of(6) == 6

    def test_a_floor_not_given_has_no_band(self):
        assert band_of(None) is None
        assert band_of(0) is None

    def test_there_is_a_fair_value_for_every_band(self, builder):
        money = builder.build(BLOCK)["flat_types"]["3 ROOM"]["money"]
        assert set(money["fair_value"]) == {"1", "6", "11"}
        for band in money["fair_value"].values():
            assert band["n_comps"] >= 0
            assert band["scope"] in ("same_block", "nearby_blocks", "town", "none")

    def test_a_higher_band_is_worth_more(self, builder):
        """The fitted storey premium rises with height, so this is a check
        that the band is actually being applied rather than ignored."""
        money = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["money"]
        bands = sorted(money["fair_value"], key=int)
        assert len(bands) > 1, "a block needs more than one band to compare"
        mids = [money["fair_value"][b]["mid"] for b in bands]
        assert all(m for m in mids)
        assert mids == sorted(mids)
        assert mids[0] < mids[-1]

    def test_a_thin_block_says_it_is_thin(self, builder):
        """"How many sales the judgement rests on, so a thin block reads as
        thin." The cheap fixture block has one sale in the window."""
        money = builder.build(BLOCK)["flat_types"]["3 ROOM"]["money"]
        assert money["thin"] is True
        assert "too few" in money["n_sales"]["n"]

    def test_widening_to_nearby_blocks_is_declared(self, builder):
        money = builder.build(BLOCK)["flat_types"]["3 ROOM"]["money"]
        band = money["fair_value"]["1"]
        assert band["scope"] == "nearby_blocks"
        assert any("widened" in note for note in band["notes"])


class TestVerdict:
    def test_an_asking_price_inside_the_range_is_not_a_bargain(self):
        assert verdict(500_000, 450_000, 550_000)["status"] == "within"

    def test_above_and_below_say_by_how_much(self):
        above = verdict(600_000, 450_000, 550_000)
        below = verdict(400_000, 450_000, 550_000)
        assert above["status"] == "above" and above["gap"] == 50_000
        assert below["status"] == "below" and below["gap"] == 50_000
        assert "$50,000" in above["says"]

    def test_no_asking_price_is_a_question_not_a_verdict(self):
        assert verdict(None, 450_000, 550_000)["status"] == "unknown"

    def test_no_range_means_no_verdict(self):
        assert verdict(500_000, None, None)["status"] == "unknown"


class TestTrend:
    def test_the_block_series_is_mix_adjusted(self, builder):
        trend = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["trend"]
        assert trend["adjusted"] is True
        assert "adjusted for the floors and leases" in trend["note"]

    def test_raw_medians_say_so(self, db):
        """Without the fitted curves the medians move with whichever floors
        happened to sell, and the note has to change rather than the number
        quietly meaning something else."""
        from nhf.sync import derive_town_medians

        derive_town_medians(db)
        trend = ReportBuilder(db).build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["trend"]
        assert trend["adjusted"] is False
        assert "raw" in trend["note"]

    def test_a_point_needs_enough_sales_behind_it(self, builder):
        """A quarter of one block's sales is one or two flats, so each point
        is a trailing year and only drawn when the year holds three sales."""
        trend = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["trend"]
        assert trend["block"]
        assert all(point["n"] >= trend["min_sales"] for point in trend["block"])

    def test_the_town_series_is_not_copied_into_the_block(self, builder):
        """It is the same curve for every block in the town, and copying it
        into nine thousand files is the difference between a site of a few
        megabytes and one of a few hundred."""
        trend = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["trend"]
        assert "town" not in trend
        assert "national" not in trend
        town = builder.town_payload("TAMPINES")
        assert town["flat_types"]["4 ROOM"]["trend"]


class TestRent:
    def test_rent_comes_with_the_scope_it_was_measured_over(self, builder):
        rent = builder.build(BLOCK)["flat_types"]["4 ROOM"]["rent"]
        assert rent["scope"] in ("block", "nearby", "town")
        assert rent["median"]["v"] > 0
        assert rent["median"]["u"] == "SGD/month"

    def test_no_rental_data_says_how_to_load_it(self, full_db):
        full_db.execute("DELETE FROM rental_txn")
        rent = ReportBuilder(full_db).build(BLOCK)["flat_types"]["4 ROOM"]["rent"]
        assert rent["median"]["c"] == UNKNOWN
        assert "nhf sync --step rentals" in rent["median"]["n"]


class TestWholeReport:
    def test_an_unknown_block_has_no_report(self, builder):
        assert builder.build("999|NOWHERE RD") is None

    def test_a_block_that_transacts_but_is_not_in_property_information(self, full_db):
        """The resale files mention addresses that HDB's property information
        does not, and a link to one of those should report thin data rather
        than fail."""
        full_db.execute("DELETE FROM block WHERE block_key = ?", (BLOCK,))
        report = ReportBuilder(full_db).build(BLOCK)
        assert report is not None
        assert report["block"] == "501"
        assert report["surroundings"]["max_floor"]["c"] == UNKNOWN
        assert report["flat_types"]

    def test_an_ungeocoded_block_reports_an_unknown_view(self, full_db):
        """Not a clear one. Half the blocks are unplaced during a first sync,
        and every one of them would otherwise claim an open horizon."""
        full_db.execute(
            "UPDATE block SET lat = NULL, lon = NULL WHERE block_key = ?", (BLOCK,))
        report = ReportBuilder(full_db).build(BLOCK)
        assert report["surroundings"]["located"] is False
        view = report["surroundings"]["view_without_floor"]
        assert "no geocode" in view["unknown"]
        assert view["open_directions"] == []
        assert report["nearby"]["mrt_exit"] is None
        assert any("no geocode" in m for m in report["nearby"]["missing"])
        assert "no geocode" in report["rain"]["unknown"]

    def test_a_geocoded_block_is_marked_located(self, builder):
        assert builder.build(BLOCK)["surroundings"]["located"] is True

    def test_the_report_serialises(self, builder):
        """It is written to a file and read by a browser, so anything that
        does not survive json.dumps is a bug here and not there."""
        payload = json.dumps(builder.build(BLOCK))
        assert json.loads(payload)["block_key"] == BLOCK

    def test_the_sales_listed_are_the_ones_the_fair_value_rests_on(self, builder):
        money = builder.build(BUSY_BLOCK)["flat_types"]["4 ROOM"]["money"]
        assert len(money["sales"]) == money["n_sales"]["v"]

    def test_the_meta_payload_carries_what_the_browser_needs(self, builder):
        meta = builder.meta_payload()
        assert meta["national_index"]
        assert meta["sun"]["track"] and meta["sun"]["open_face"]["W"]
        assert meta["curves"]["lease"] and meta["curves"]["storey"]
        assert meta["curves"]["storey_band_floors"] == STOREY_BAND_FLOORS
        assert meta["sun"]["constants"]["storey_height_m"] == 3.0

    def test_the_published_constants_are_the_ones_in_the_golden_fixture(
        self, builder
    ):
        """The browser reads its constants from meta.json and is tested
        against tests/golden/sun.json. A constant in one and not the other
        silently becomes `undefined` in the browser, which reads as every
        neighbour being below you."""
        import json as _json
        from pathlib import Path as _Path

        golden = _json.loads(
            (_Path(__file__).resolve().parent / "golden" / "sun.json").read_text()
        )
        assert builder.meta_payload()["sun"]["constants"] == golden["constants"]

    def test_the_sun_is_not_written_into_every_block(self, builder):
        """One track serves the island: the altitude varies by a quarter of a
        degree from end to end, less than the width of the sun itself."""
        report = builder.build(BLOCK)
        assert "sun_open" not in report["surroundings"]
        assert "track" not in json.dumps(report["surroundings"])
