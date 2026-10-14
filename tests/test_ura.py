"""The URA Master Plan land use layer.

A different shape of source from the rest: a 174 MB GeoJSON file from a
separate host, not paged rows from the datastore. The fixtures here are
shaped like the real features — the same field names, the same GPR codes,
the same unevenly sampled rings — so a change in the layer shows up as a
failing test rather than an empty table.
"""

from __future__ import annotations

import json

import pytest

from nhf.db import open_db
from nhf.sources import ura


# --- GPR, which is a string and often not a number -------------------------
def test_a_numeric_gpr_parses_to_a_ratio():
    assert ura.parse_gpr("2.8") == (2.8, None)
    assert ura.parse_gpr("3") == (3.0, None)


def test_landed_housing_is_an_answer_not_a_gap():
    """LND is 52% of parcels and it tells you the height: low-rise. It must
    not land in the same bucket as "nobody has decided"."""
    value, note = ura.parse_gpr("LND")
    assert value is None
    assert "low-rise" in note


def test_an_unevaluated_parcel_says_so():
    for code in ("EVA", "SDP"):
        value, note = ura.parse_gpr(code)
        assert value is None and note, f"{code} should carry a reason"
    assert ura.parse_gpr("LND")[1] != ura.parse_gpr("EVA")[1]


def test_a_missing_gpr_carries_no_invented_reason():
    assert ura.parse_gpr(None) == (None, None)
    assert ura.parse_gpr("") == (None, None)


def test_an_unknown_code_is_reported_rather_than_swallowed():
    value, note = ura.parse_gpr("ZZZ")
    assert value is None and "ZZZ" in note


# --- geometry --------------------------------------------------------------
SQUARE = [[103.0, 1.0], [103.0, 1.2], [103.2, 1.2], [103.2, 1.0], [103.0, 1.0]]


def test_centroid_of_a_square_is_its_middle():
    lat, lon, *_ = ura.geometry_summary({"type": "Polygon", "coordinates": [SQUARE]})
    assert lat == pytest.approx(1.1, abs=1e-6)
    assert lon == pytest.approx(103.1, abs=1e-6)


def test_the_centroid_ignores_how_finely_an_edge_was_traced():
    """The real rings are very unevenly sampled — thirty points along one
    edge and two along the opposite one. A mean of the vertices is dragged
    towards the detailed edge; the shoelace centroid is not."""
    dense = [[103.0, 1.0]]
    for i in range(1, 40):
        dense.append([103.0, 1.0 + 0.2 * i / 40])
    dense += [[103.0, 1.2], [103.2, 1.2], [103.2, 1.0], [103.0, 1.0]]

    lat, lon, *_ = ura.geometry_summary({"type": "Polygon", "coordinates": [dense]})
    assert lon == pytest.approx(103.1, abs=1e-3), "pulled towards the dense edge"

    mean_lon = sum(p[0] for p in dense) / len(dense)
    assert abs(mean_lon - 103.1) > abs(lon - 103.1), "the mean should be the worse estimate"


def test_the_bounding_box_covers_the_whole_ring():
    _, _, min_lat, max_lat, min_lon, max_lon = ura.geometry_summary(
        {"type": "Polygon", "coordinates": [SQUARE]}
    )
    assert (min_lat, max_lat, min_lon, max_lon) == (1.0, 1.2, 103.0, 103.2)


def test_a_multipolygon_is_summarised_across_its_parts():
    far = [[p[0] + 1, p[1]] for p in SQUARE]
    summary = ura.geometry_summary(
        {"type": "MultiPolygon", "coordinates": [[SQUARE], [far]]}
    )
    lat, lon, _, _, min_lon, max_lon = summary
    assert min_lon == 103.0 and max_lon == 104.2
    assert 103.1 < lon < 104.1, "the centroid should sit between the parts"


def test_geometry_that_cannot_be_summarised_returns_nothing():
    assert ura.geometry_summary({}) is None
    assert ura.geometry_summary({"type": "Point", "coordinates": [103.0, 1.0]}) is None
    assert ura.geometry_summary({"type": "Polygon", "coordinates": [[[103.0, 1.0]]]}) is None


# --- streaming the file ----------------------------------------------------
def _layer(tmp_path, features):
    path = tmp_path / "layer.geojson"
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "name": "G_MP19_LAND_USE_PL", "features": features},
        indent=1,
    ))
    return str(path)


def _feature(objectid, land_use, gpr, ring=None):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring or SQUARE]},
        "properties": {
            "OBJECTID": objectid, "LU_DESC": land_use, "LU_TEXT": " ",
            "GPR": gpr, "WHI_Q_MX": None, "GPR_B_MN": None,
            "INC_CRC": "2481817E4931D054", "FMEL_UPD_D": "20191209171707",
            "SHAPE.AREA": 151032.76, "SHAPE.LEN": 1686.76,
        },
    }


def test_every_feature_in_the_file_becomes_a_row(tmp_path):
    path = _layer(tmp_path, [
        _feature(1, "RESIDENTIAL", "2.8"),
        _feature(2, "RESERVE SITE", "EVA"),
        _feature(3, "ROAD", "NA"),
    ])
    rows = list(ura.stream_parcels(path, source="test"))
    assert len(rows) == 3
    assert [r[1] for r in rows] == ["RESIDENTIAL", "RESERVE SITE", "ROAD"]
    assert rows[0][3] == 2.8 and rows[1][3] is None


def test_streaming_keeps_the_raw_gpr_beside_the_parsed_one(tmp_path):
    path = _layer(tmp_path, [_feature(1, "RESIDENTIAL", "LND")])
    row = next(iter(ura.stream_parcels(path, source="test")))
    assert row[2] == "LND", "the raw value is kept for display"
    assert row[3] is None and "low-rise" in row[4]


def test_rows_line_up_with_the_table(tmp_path):
    path = _layer(tmp_path, [_feature(1, "RESIDENTIAL", "2.8")])
    rows = list(ura.stream_parcels(path, source="test"))
    conn = open_db(":memory:")
    from nhf.db import upsert_many
    with conn:
        upsert_many(conn, "land_parcel", ura.PARCEL_COLUMNS, rows, conflict="REPLACE")
    got = conn.execute("SELECT land_use, gpr_numeric, source FROM land_parcel").fetchone()
    assert got["land_use"] == "RESIDENTIAL"
    assert got["gpr_numeric"] == 2.8
    assert got["source"] == "test"


# --- the sync step ---------------------------------------------------------
def test_an_unset_resource_id_is_reported_as_skipped(monkeypatch, tmp_path):
    from nhf.config import load_config
    from nhf.sync import SyncReporter, sync_land_use

    conn = open_db(":memory:")
    cfg = load_config()
    monkeypatch.setitem(cfg.datasets, "land_use", {"id": "TODO", "label": "x"})
    reporter = SyncReporter(conn)
    assert sync_land_use(conn, reporter, cfg, path=str(tmp_path / "nope")) == 0
    assert reporter.results[-1]["status"] == "skipped"


def test_a_layer_that_parses_to_nothing_is_an_error_not_an_empty_success(tmp_path):
    from nhf.config import load_config
    from nhf.sync import SyncReporter, sync_land_use

    conn = open_db(":memory:")
    reporter = SyncReporter(conn)
    assert sync_land_use(conn, reporter, load_config(),
                         path=_layer(tmp_path, [])) == 0
    assert reporter.results[-1]["status"] == "error"


def test_loading_the_layer_replaces_the_previous_version(tmp_path):
    """A re-fetch of the same plan must not double the parcels."""
    from nhf.config import load_config
    from nhf.sync import SyncReporter, sync_land_use

    conn = open_db(":memory:")
    cfg = load_config()
    path = _layer(tmp_path, [_feature(1, "RESIDENTIAL", "2.8"),
                             _feature(2, "COMMERCIAL", "3.0")])
    for _ in range(2):
        sync_land_use(conn, SyncReporter(conn), cfg, path=path)
    assert conn.execute("SELECT COUNT(*) FROM land_parcel").fetchone()[0] == 2


def test_the_monthly_sync_does_not_fetch_the_master_plan():
    """174 MB of a plan revised every few years has no place in a monthly job."""
    import inspect

    from nhf import sync as sync_mod

    source = inspect.getsource(sync_mod.run_sync)
    assert 'wanted and "land_use" in wanted' in source, \
        "land_use must only run when named explicitly"
