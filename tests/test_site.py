"""The static data the site reads.

Three things matter here beyond the files existing.

A pasted link has to resolve using only the files a browser would actually
fetch — `index/<block>.json` and nothing else — so the round-trip test walks
exactly that path rather than calling the Python resolver it was built from.
That is the contract the TypeScript implements, and `tests/golden/links.json`
is what holds the two to it.

The island-wide parts have to be written once rather than into every block,
because that is the difference between a site of a few megabytes and one of
a few hundred.

And nothing of ours may be published. On this site that is nearly free: there
is no budget, no office and no shortlist in it to begin with.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nhf.listing import parse_link
from nhf.site import (
    ZONE_GROUPS,
    ZONE_SKIP,
    ZONING_CELL,
    block_slug,
    build_site,
    slugify,
    titlecase_use,
    verify,
    zone_group,
    zoning_cells,
)


@pytest.fixture()
def site(full_db, tmp_path):
    out = tmp_path / "data"
    report = build_site(full_db, out)
    return out, report


def _load(out: Path, *parts: str) -> dict:
    return json.loads(out.joinpath(*parts).read_text())


def resolve_in_browser(url: str, out: Path) -> dict:
    """Resolve a link the way the site does, from the files alone.

    This is deliberately not a call into `nhf.listing`. It walks the shards
    on disk — fetch the one file for the block number in the link, match its
    street aliases against the rest of the path — which is the contract the
    browser has to meet, and the reason the index carries its aliases rather
    than leaving the browser to recompute them.
    """
    parse = parse_link(url)
    found: list[dict] = []
    for position in parse.block_candidates:
        number = parse.tokens[position].lower()
        path = out / "index" / f"{number}.json"
        if not path.exists():
            continue                                   # a 404, in the browser
        payload = json.loads(path.read_text())
        tail = [t.lower() for t in parse.tokens[position + 1:]]
        for end in range(len(tail), 0, -1):
            candidate = "-".join(tail[:end])
            for street in payload["streets"]:
                if candidate in street["aliases"]:
                    found.append(street)
                    break
            else:
                continue
            break
    if len(found) == 1:
        return {"status": "resolved", **found[0]}
    if len(found) > 1:
        return {"status": "ambiguous", "candidates": found}
    return {"status": "unresolved"}


class TestZoning:
    """The Master Plan as the map's base layer.

    Shared rather than per block: a parcel sits near dozens of blocks, and
    writing the polygon into each of their reports would store it dozens of
    times. So it goes into cells, and a parcel that reaches four cells is
    written into four of them — which is the thing these tests are really
    about, because the map must still draw it once.
    """

    def test_every_published_land_use_has_a_group(self):
        """URA publishes 33 values. An unmapped one would be drawn as civic
        for ever without anyone noticing, so the mapping is asserted whole
        rather than sampled."""
        mapped = {use for uses in ZONE_GROUPS.values() for use in uses}
        overlap = mapped & set(ZONE_SKIP)
        assert not overlap, f"drawn and skipped at once: {overlap}"

    def test_roads_and_water_are_left_to_the_basemap(self):
        assert zone_group("ROAD") is None
        assert zone_group("WATERBODY") is None

    def test_the_groups_are_what_the_map_colours(self):
        assert zone_group("RESIDENTIAL") == "residential"
        assert zone_group("COMMERCIAL & RESIDENTIAL") == "commercial"
        assert zone_group("BUSINESS 2") == "business"
        assert zone_group("OPEN SPACE") == "park"
        assert zone_group("RESERVE SITE") == "reserve"

    def test_an_unknown_use_falls_into_civic_rather_than_vanishing(self):
        """A new value in a future Master Plan is drawn in the neutral group,
        not dropped: a parcel missing from the map reads as unzoned land."""
        assert zone_group("SOMETHING URA ADDS IN 2030") == "civic"

    def test_nothing_is_not_a_zone(self):
        assert zone_group(None) is None
        assert zone_group("") is None

    def test_a_parcel_spanning_cells_is_written_into_each_with_one_id(
        self, full_db
    ):
        """And carries the same id in both, which is what lets the browser
        draw it once. Without that the fills stack and a parcel across four
        cells comes out four times as dark as its neighbour."""
        full_db.execute(
            "INSERT INTO land_parcel (objectid, land_use, geom, lat, lon,"
            " min_lat, max_lat, min_lon, max_lon, source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                999001, "PARK",
                json.dumps({"type": "Polygon", "coordinates": [[
                    [103.845, 1.345], [103.856, 1.345],
                    [103.856, 1.356], [103.845, 1.356], [103.845, 1.345],
                ]]}),
                1.35, 103.85, 1.345, 1.356, 103.845, 103.856, "test",
            ),
        )
        cells = {key: payload for key, payload in zoning_cells(full_db)}
        holding = [
            key for key, payload in cells.items()
            if any(p["i"] == 999001 for p in payload["parcels"])
        ]
        # 1.345-1.356 crosses a cell line at 1.35; 103.845-103.856 at 103.85.
        assert len(holding) == 4, f"expected four cells, got {holding}"
        for key in holding:
            parcel = next(
                p for p in cells[key]["parcels"] if p["i"] == 999001
            )
            assert parcel["g"] == "park"

    def test_a_build_with_no_parcels_says_so(self, db, tmp_path):
        """Land use is fetched by name only, so a fresh store builds a map
        with nothing under it. An empty zoning layer looks exactly like land
        that is not zoned, which is the wrong conclusion, so the build says
        which command is missing rather than leaving it to be noticed."""
        report = build_site(db, tmp_path / "site")
        assert report.zoning_cells == 0
        assert any("nhf sync --step land_use" in w for w in report.warnings)

    def test_the_cell_size_is_the_one_the_browser_assumes(self):
        """`ZONING_CELL` is duplicated in web/src/lib/data.ts, because the
        browser works out which cells a view covers before it has read any of
        them. If one side changes, every lookup misses."""
        source = Path("web/src/lib/data.ts").read_text(encoding="utf-8")
        assert f"export const ZONING_CELL = {ZONING_CELL};" in source

    def test_a_parcel_with_no_outline_is_not_written(self, full_db):
        full_db.execute(
            "INSERT INTO land_parcel (objectid, land_use, geom, lat, lon,"
            " min_lat, max_lat, min_lon, max_lon, source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (999002, "PARK", None, 1.30, 103.80,
             1.30, 1.30, 103.80, 103.80, "test"),
        )
        for _, payload in zoning_cells(full_db):
            assert all(p["i"] != 999002 for p in payload["parcels"])

    def test_the_tooltip_reads_as_words_not_as_a_shout(self):
        assert titlecase_use("RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY") == (
            "Residential with Commercial at 1st Storey"
        )
        assert titlecase_use(None) == "Unzoned"


class TestLayout:
    def test_the_four_kinds_of_file_are_written(self, site):
        out, report = site
        assert (out / "meta.json").exists()
        assert (out / "manifest.json").exists()
        assert list((out / "blocks").glob("*.json"))
        assert list((out / "towns").glob("*.json"))
        assert list((out / "index").glob("*.json"))
        assert report.reports_written == len(list((out / "blocks").glob("*.json")))

    def test_pages_is_told_not_to_run_jekyll_over_it(self, site):
        """Without this, Pages drops every file whose name starts with an
        underscore."""
        out, _ = site
        assert (out / ".nojekyll").exists()

    def test_a_block_file_is_named_by_its_own_address(self, site):
        """The file name is also one of the block's aliases, so the index and
        the file names agree by construction rather than by a second mapping
        that could drift."""
        out, _ = site
        slug = block_slug("501", "TAMPINES ST 42")
        assert slug == "501-tampines-st-42"
        payload = _load(out, "blocks", f"{slug}.json")
        assert payload["block"] == "501"
        assert payload["street"] == "TAMPINES ST 42"
        assert payload["slug"] == slug

    def test_the_index_is_sharded_by_block_number(self, site):
        """One file per block number, so a visit downloads a few hundred
        bytes of aliases instead of the whole 26,501 of them."""
        out, _ = site
        payload = _load(out, "index", "501.json")
        assert payload["block"] == "501"
        assert len(payload["streets"]) == 1
        assert payload["streets"][0]["slug"] == "501-tampines-st-42"
        assert "tampines-street-42" in payload["streets"][0]["aliases"]

    def test_the_block_number_is_not_repeated_on_every_alias(self, site):
        """It is the file name. Repeating it is 26,501 wasted copies."""
        out, _ = site
        for street in _load(out, "index", "501.json")["streets"]:
            assert not any(alias.startswith("501-") for alias in street["aliases"])

    def test_a_town_file_holds_the_shared_trend(self, site):
        out, _ = site
        payload = _load(out, "towns", "tampines.json")
        assert payload["town"] == "TAMPINES"
        assert payload["flat_types"]["4 ROOM"]["trend"]

    def test_the_block_points_at_its_town_file(self, site):
        out, _ = site
        payload = _load(out, "blocks", "501-tampines-st-42.json")
        assert payload["town_slug"] == "tampines"
        assert (out / "towns" / "tampines.json").exists()

    def test_the_manifest_says_what_was_built_and_how_fresh_it_is(self, site):
        out, report = site
        manifest = _load(out, "manifest.json")
        assert manifest["counts"]["reports"] == report.reports_written
        assert manifest["as_of"]["month"]
        assert "TAMPINES" in manifest["towns"]
        assert manifest["facings"] == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        assert "data.gov.sg" in manifest["sources"]

    def test_slugify_is_stable_for_the_awkward_street_names(self):
        assert slugify("C'WEALTH AVE WEST") == "c-wealth-ave-west"
        assert slugify("ST GEORGE'S RD") == "st-george-s-rd"
        assert slugify("LOR 1A TOA PAYOH") == "lor-1a-toa-payoh"


class TestSharedOnce:
    def test_the_sun_is_in_meta_and_not_in_any_block(self, site):
        """The island is 50 km across and the sun's altitude varies by a
        quarter of a degree from end to end, so one track is every block's
        track."""
        out, _ = site
        meta = _load(out, "meta.json")
        assert meta["sun"]["track"]
        assert meta["sun"]["open_face"]["W"]["low_hours"] > 600
        for path in (out / "blocks").glob("*.json"):
            assert "open_face" not in path.read_text()

    def test_the_national_index_is_in_meta_and_not_in_any_block(self, site):
        out, _ = site
        assert _load(out, "meta.json")["national_index"]
        for path in (out / "blocks").glob("*.json"):
            assert "national_index" not in path.read_text()

    def test_the_fitted_curves_travel_with_the_data_that_used_them(self, site):
        """The browser does not value anything, but it does show what the
        numbers rest on, and the lease curve's caveat is part of the number."""
        out, _ = site
        curves = _load(out, "meta.json")["curves"]
        assert curves["lease"] and curves["storey"]
        assert curves["storey_band_floors"] == 5
        assert "understates true decay" in curves["note"]

    def test_the_town_trend_is_written_once_per_town(self, site):
        out, report = site
        assert report.towns_written == len(list((out / "towns").glob("*.json")))
        for path in (out / "blocks").glob("*.json"):
            trend = json.loads(path.read_text())["flat_types"]
            for section in trend.values():
                assert "town" not in section["trend"]


class TestResolvingFromTheFilesAlone:
    def test_a_portal_link_resolves_to_a_block_file(self, site):
        out, _ = site
        got = resolve_in_browser(
            "https://www.99.co/singapore/sale/property/"
            "501-tampines-street-42-hdb-eSeF", out)
        assert got["status"] == "resolved"
        assert got["slug"] == "501-tampines-st-42"
        assert (out / "blocks" / f"{got['slug']}.json").exists()

    def test_hdbs_own_spelling_resolves_too(self, site):
        out, _ = site
        got = resolve_in_browser(
            "https://www.propertyguru.com.sg/listing/"
            "hdb-for-sale-501-tampines-st-42-25099211", out)
        assert got["slug"] == "501-tampines-st-42"

    def test_a_block_number_nobody_has_is_a_404(self, site):
        """Which is an honest answer rather than a wrong one, and is why the
        index is sharded this way."""
        out, _ = site
        assert not (out / "index" / "9999.json").exists()
        got = resolve_in_browser(
            "https://www.99.co/singapore/sale/property/"
            "9999-nowhere-street-1-hdb-x", out)
        assert got["status"] == "unresolved"

    def test_a_street_that_does_not_match_does_not_resolve(self, site):
        out, _ = site
        got = resolve_in_browser(
            "https://www.99.co/singapore/sale/property/"
            "501-punggol-way-hdb-x", out)
        assert got["status"] == "unresolved"

    def test_every_written_report_can_be_reached_from_the_index(self, site):
        """The check that matters: a report nothing can resolve to is a file
        nobody will ever see."""
        out, _ = site
        reachable = set()
        for path in (out / "index").glob("*.json"):
            for street in json.loads(path.read_text())["streets"]:
                if street["report"]:
                    reachable.add(street["slug"])
        written = {p.stem for p in (out / "blocks").glob("*.json")}
        assert written == reachable

    def test_the_index_marks_which_blocks_have_a_report(self, site):
        """A block that has not sold in two years is still indexed, so a link
        to it resolves and then says there are no sales."""
        out, _ = site
        flags = {
            street["report"]
            for path in (out / "index").glob("*.json")
            for street in json.loads(path.read_text())["streets"]
        }
        assert True in flags


class TestNothingOfOurs:
    def test_the_built_site_passes_its_own_check(self, site):
        out, _ = site
        assert verify(out) == []

    def test_a_field_from_the_superseded_product_fails_the_check(self, site):
        """The ranked-list export published a growth score. If any of that
        vocabulary comes back, the check has to catch it."""
        out, _ = site
        path = out / "blocks" / "501-tampines-st-42.json"
        payload = json.loads(path.read_text())
        payload["flat_types"]["3 ROOM"]["growth_score"] = 67.1
        path.write_text(json.dumps(payload))
        problems = verify(out)
        assert any("growth_score" in p for p in problems)

    def test_one_bad_field_is_reported_once_and_not_nine_thousand_times(self, site):
        """A field that crept back in is in every file, and nine thousand
        identical lines would bury everything else."""
        out, _ = site
        for path in (out / "blocks").glob("*.json"):
            payload = json.loads(path.read_text())
            payload["nearby"]["drive_minutes"] = 24.0
            path.write_text(json.dumps(payload))
        problems = [p for p in verify(out) if "drive_minutes" in p]
        assert len(problems) == 1
        assert "69 file(s)" in problems[0]

    def test_an_unexpected_top_level_key_is_reported(self, site):
        out, _ = site
        path = out / "blocks" / "501-tampines-st-42.json"
        payload = json.loads(path.read_text())
        payload["our_private_note"] = "we liked this one"
        path.write_text(json.dumps(payload))
        assert any("our_private_note" in p for p in verify(out))

    def test_a_missing_build_is_not_a_pass(self, tmp_path):
        assert verify(tmp_path / "nothing-here")

    def test_a_sale_price_equal_to_the_budget_is_not_a_leak(self, site):
        """Deliberately not checked, and worth a test saying so: a flat that
        sold for exactly the budget is a published government fact, and a
        check that grepped for those digits would fail every honest build."""
        out, _ = site
        path = out / "blocks" / "501-tampines-st-42.json"
        payload = json.loads(path.read_text())
        payload["flat_types"]["3 ROOM"]["money"]["median_price"] = {
            "v": 600000, "c": "fact"}
        path.write_text(json.dumps(payload))
        assert verify(out) == []


class TestLimits:
    def test_a_limit_writes_fewer_reports_but_still_indexes_everything(
        self, full_db, tmp_path
    ):
        out = tmp_path / "data"
        report = build_site(full_db, out, limit=3)
        assert report.reports_written == 3
        assert report.blocks_indexed > 3

    def test_a_rebuild_clears_what_was_there(self, full_db, tmp_path):
        out = tmp_path / "data"
        build_site(full_db, out)
        stale = out / "blocks" / "999-gone-rd.json"
        stale.write_text("{}")
        build_site(full_db, out)
        assert not stale.exists()

    def test_a_store_with_no_sales_writes_an_empty_but_valid_site(self, db, tmp_path):
        db.execute("DELETE FROM resale_txn")
        out = tmp_path / "data"
        report = build_site(db, out)
        assert report.reports_written == 0
        assert (out / "meta.json").exists()
        # And the check says so rather than passing an empty site.
        assert any("no block reports" in p for p in verify(out))
