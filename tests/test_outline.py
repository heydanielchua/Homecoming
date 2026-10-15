"""Building outlines, and the join that gives them a height.

HDB publishes a footprint per building and a storey count per block, in two
different datasets, with no field in common but the postal code. These tests
are mostly about that join: what happens when it misses, and what must never
happen when it does.
"""

from __future__ import annotations

import json

from nhf.db import open_db
from nhf.site import ZONING_CELL, building_cells
from nhf.sources.outline import stream_outlines
from nhf.sync import SyncReporter, sync_outlines


def _feature(postal: str | None, block: str, lon: float, lat: float, size=0.0004):
    ring = [
        [lon, lat], [lon + size, lat],
        [lon + size, lat + size], [lon, lat + size], [lon, lat],
    ]
    props = {"BLK_NO": block, "SHAPE.AREA": 900.0}
    if postal is not None:
        props["POSTAL_COD"] = postal
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _layer(tmp_path, features):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "outlines.geojson"
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, indent=1))
    return str(path)


class TestReadingTheLayer:
    def test_it_yields_a_postal_code_a_block_and_a_shape(self, tmp_path):
        rows = list(stream_outlines(_layer(
            tmp_path, [_feature("330008", "8", 103.8615, 1.3164)])))
        assert len(rows) == 1
        postal, block, shape = rows[0]
        assert postal == "330008"
        assert block == "8"
        assert json.loads(shape)["type"] == "Polygon"

    def test_a_building_with_no_postal_code_is_dropped(self, tmp_path):
        """The postal code is the only join key: a footprint without one
        cannot be given a height, and a shape with no height is not something
        this draws."""
        rows = list(stream_outlines(_layer(
            tmp_path, [_feature(None, "8", 103.8615, 1.3164)])))
        assert rows == []

    def test_a_shape_too_small_to_draw_is_dropped(self, tmp_path):
        rows = list(stream_outlines(_layer(
            tmp_path, [_feature("330008", "8", 103.8615, 1.3164, size=0.0000001)])))
        assert rows == []


class TestTheJoin:
    def test_a_block_takes_the_outline_at_its_own_postal_code(self, db, tmp_path):
        db.execute("UPDATE block SET postal = '529301' WHERE block_key = ?",
                   ("501|TAMPINES ST 42",))
        db.commit()
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path, [_feature("529301", "501", 103.94, 1.35)]))
        got = db.execute(
            "SELECT outline FROM block WHERE block_key = ?",
            ("501|TAMPINES ST 42",)).fetchone()[0]
        assert json.loads(got)["type"] == "Polygon"

    def test_a_block_with_no_match_keeps_nothing_rather_than_a_neighbours(
        self, db, tmp_path
    ):
        """The failure that would matter: a block drawn with the shape of the
        building next door looks right and is a different building."""
        db.execute("UPDATE block SET postal = '529301' WHERE block_key = ?",
                   ("501|TAMPINES ST 42",))
        db.commit()
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path, [_feature("999999", "999", 103.94, 1.35)]))
        got = db.execute(
            "SELECT outline FROM block WHERE block_key = ?",
            ("501|TAMPINES ST 42",)).fetchone()[0]
        assert got is None

    def test_a_reload_clears_an_outline_that_has_gone_from_the_layer(
        self, db, tmp_path
    ):
        """A demolished block must lose its footprint, not keep last year's."""
        db.execute("UPDATE block SET postal = '529301' WHERE block_key = ?",
                   ("501|TAMPINES ST 42",))
        db.commit()
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path / "a", [_feature("529301", "501", 103.94, 1.35)]))
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path / "b", [_feature("888888", "888", 103.94, 1.35)]))
        got = db.execute(
            "SELECT outline FROM block WHERE block_key = ?",
            ("501|TAMPINES ST 42",)).fetchone()[0]
        assert got is None


class TestBuildingCells:
    def test_a_building_is_written_into_every_cell_it_reaches_with_one_id(
        self, db, tmp_path
    ):
        """Same rule as the zoning: the browser draws it once, by id."""
        db.execute("UPDATE block SET postal = '529301' WHERE block_key = ?",
                   ("501|TAMPINES ST 42",))
        db.commit()
        # Straddling 1.35 and 103.95, so it reaches four cells.
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path, [_feature("529301", "501", 103.9495, 1.3495, size=0.001)]))
        cells = dict(building_cells(db))
        holding = [k for k, v in cells.items()
                   if any(b["i"] == "529301" for b in v["buildings"])]
        assert len(holding) == 4, f"expected four cells, got {holding}"

    def test_it_carries_the_storey_count_not_a_height(self, db, tmp_path):
        """Three metres a storey is an assumption this project makes, not a
        measurement HDB publishes. Shipping the storeys keeps the assumption
        where a reader can see it."""
        db.execute(
            "UPDATE block SET postal = '529301', max_floor_lvl = 12 "
            "WHERE block_key = ?", ("501|TAMPINES ST 42",))
        db.commit()
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path, [_feature("529301", "501", 103.94, 1.35)]))
        building = next(
            b for _, v in building_cells(db) for b in v["buildings"]
            if b["i"] == "529301")
        assert building["s"] == 12
        assert "height" not in building and "m_height" not in building

    def test_it_carries_the_slug_so_the_view_can_find_this_block(
        self, db, tmp_path
    ):
        db.execute("UPDATE block SET postal = '529301' WHERE block_key = ?",
                   ("501|TAMPINES ST 42",))
        db.commit()
        sync_outlines(db, SyncReporter(db), _cfg(), path=_layer(
            tmp_path, [_feature("529301", "501", 103.94, 1.35)]))
        building = next(
            b for _, v in building_cells(db) for b in v["buildings"]
            if b["i"] == "529301")
        assert building["slug"] == "501-tampines-st-42"

    def test_the_cells_are_cut_the_same_way_as_the_zoning(self):
        """One grid, so a view fetches the same keys from both directories."""
        assert ZONING_CELL == 100


def _cfg():
    from nhf.config import load_config
    return load_config()
