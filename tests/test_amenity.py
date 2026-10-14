"""Parks, hawker centres, park facilities, and the rainfall fold.

The fixtures carry the real field names — ADDRESSBLOCKHOUSENUMBER,
NUMBER_OF_COOKED_FOOD_STALLS, SHAPE_1.AREA, CLASS — so a renamed field in a
published layer fails a test instead of quietly emptying a column.
"""

from __future__ import annotations

import json

import pytest

from nhf.db import open_db, upsert_many
from nhf.sources import amenity, rainfall


def _layer(tmp_path, name, features):
    path = tmp_path / f"{name}.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return str(path)


def _point(lon=103.8, lat=1.29):
    return {"type": "Point", "coordinates": [lon, lat]}


SQUARE = [[103.0, 1.0], [103.0, 1.2], [103.2, 1.2], [103.2, 1.0], [103.0, 1.0]]


# --- hawker centres --------------------------------------------------------
def test_a_hawker_centre_keeps_its_stall_count_and_status(tmp_path):
    path = _layer(tmp_path, "hawker", [{
        "type": "Feature", "geometry": _point(),
        "properties": {
            "NAME": "Tiong Bahru Market", "ADDRESSBLOCKHOUSENUMBER": "30",
            "ADDRESSSTREETNAME": "Seng Poh Road", "STATUS": "Existing",
            "NUMBER_OF_COOKED_FOOD_STALLS": 83,
        },
    }])
    row = next(iter(amenity.load_layer(path, "hawker_centre", "test")))
    kind, subtype, name, detail, status, size_hint, lat, lon, *_ = row
    assert kind == "hawker_centre"
    assert name == "Tiong Bahru Market"
    assert detail == "30 Seng Poh Road"
    assert status == "Existing"
    assert size_hint == 83, "a 6-stall centre is not an 83-stall centre"
    assert (lat, lon) == (1.29, 103.8)


def test_a_centre_still_being_built_says_so(tmp_path):
    """Six of the 129 are under construction, which is a different fact from
    one that has opened — and a hawker centre arriving is a real change."""
    path = _layer(tmp_path, "hawker", [{
        "type": "Feature", "geometry": _point(),
        "properties": {"NAME": "Somewhere", "STATUS": "Under Construction"},
    }])
    assert next(iter(amenity.load_layer(path, "hawker_centre", "t")))[4] == "Under Construction"


# --- parks -----------------------------------------------------------------
def test_a_park_keeps_its_area_and_its_extent(tmp_path):
    """For a park the distance that matters is to the edge, so the box is
    load-bearing, not decoration."""
    path = _layer(tmp_path, "parks", [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [SQUARE]},
        "properties": {"NAME": "Big Park", "L_CODE": "NP510",
                       "N_RESERVE": "NATURE RESERVE", "SHAPE_1.AREA": 151032.7},
    }])
    row = next(iter(amenity.load_layer(path, "park", "test")))
    assert row[0] == "park" and row[2] == "Big Park"
    assert row[1] == "NATURE RESERVE" and row[3] == "NP510"
    assert row[5] == pytest.approx(151032.7)
    _, _, _, _, _, _, lat, lon, min_lat, max_lat, min_lon, max_lon, _ = row
    assert (min_lat, max_lat) == (1.0, 1.2) and (min_lon, max_lon) == (103.0, 103.2)
    assert min_lat < lat < max_lat


# --- park facilities -------------------------------------------------------
def test_car_parks_and_noticeboards_are_left_out(tmp_path):
    """Nine thousand of the 14,754 facilities are car parks and 2,619 are
    noticeboards. Keeping them would make "what is nearby" meaningless."""
    feats = [
        {"type": "Feature", "geometry": _point(), "properties": {"CLASS": "CARPARK"}},
        {"type": "Feature", "geometry": _point(), "properties": {"CLASS": "NOTICEBOARD"}},
        {"type": "Feature", "geometry": _point(), "properties": {"CLASS": "MAPBOARD"}},
        {"type": "Feature", "geometry": _point(),
         "properties": {"CLASS": "PLAYGROUND", "NAME": "A playground"}},
    ]
    rows = list(amenity.load_layer(_layer(tmp_path, "fac", feats), "park_facility", "t"))
    assert [r[1] for r in rows] == ["PLAYGROUND"]


def test_the_multipurpose_court_is_kept(tmp_path):
    """This is the hardcourt the PRD asked for, and there are only 16."""
    assert "MULTIPURPOSE COURT" in amenity.USEFUL_FACILITIES


def test_an_unknown_facility_class_is_not_invented(tmp_path):
    feats = [{"type": "Feature", "geometry": _point(),
              "properties": {"CLASS": "SOMETHING NEW"}}]
    rows = list(amenity.load_layer(_layer(tmp_path, "fac", feats), "park_facility", "t"))
    assert rows == [], "an unrecognised class should be dropped, not guessed at"


def test_rows_line_up_with_the_table(tmp_path):
    path = _layer(tmp_path, "hawker", [{
        "type": "Feature", "geometry": _point(),
        "properties": {"NAME": "Market", "STATUS": "Existing",
                       "NUMBER_OF_COOKED_FOOD_STALLS": 40},
    }])
    rows = list(amenity.load_layer(path, "hawker_centre", "test"))
    conn = open_db(":memory:")
    with conn:
        upsert_many(conn, "amenity", amenity.AMENITY_COLUMNS, rows, conflict="IGNORE")
    got = conn.execute("SELECT kind, name, size_hint FROM amenity").fetchone()
    assert (got["kind"], got["name"], got["size_hint"]) == ("hawker_centre", "Market", 40)


def test_an_unknown_layer_kind_is_refused(tmp_path):
    with pytest.raises(ValueError):
        list(amenity.load_layer(_layer(tmp_path, "x", []), "not_a_layer", "t"))


# --- rainfall --------------------------------------------------------------
def _reading(station, value, day="2024-03-01", name="Somewhere"):
    return {
        "station_id": station, "station_name": name, "date": day,
        "timestamp": f"{day}T00:05:00+08:00",
        "location_latitude": "1.2937", "location_longitude": "103.8125",
        "reading_value": value, "reading_unit": "mm",
    }


def test_seven_million_readings_fold_to_one_row_per_station():
    """The whole point: the readings are never kept."""
    stations = rainfall.fold(
        _reading("S77", 0.2 if i % 10 == 0 else 0.0, day=f"2024-03-{i % 28 + 1:02d}")
        for i in range(5000)
    )
    assert list(stations) == ["S77"]
    rows = list(rainfall.station_rows(stations, 2024, "test"))
    assert len(rows) == 1
    assert rows[0][6] == 5000, "every reading counted"
    assert rows[0][7] == 500, "one in ten was wet"


def test_a_dry_reading_is_counted_but_not_called_rain():
    """The gauges report 0 constantly; a tipping bucket's smallest increment
    is 0.2 mm, so anything under that is the gauge idling."""
    stations = rainfall.fold([_reading("S1", 0.0), _reading("S1", 0.1)])
    row = next(iter(rainfall.station_rows(stations, 2024, "t")))
    assert row[6] == 2 and row[7] == 0 and row[8] == 0


def test_a_broken_gauge_is_skipped_rather_than_summed():
    """Out-of-service gauges report negatives and absurd values; a 500 mm
    five-minute total is a fault, not weather."""
    stations = rainfall.fold([
        _reading("S1", 2.0), _reading("S1", -999), _reading("S1", 5000),
    ])
    row = next(iter(rainfall.station_rows(stations, 2024, "t")))
    assert row[5] == pytest.approx(2.0)
    assert row[6] == 1, "only the usable reading counted"


def test_rain_days_count_days_not_readings():
    stations = rainfall.fold([
        _reading("S1", 1.0, day="2024-01-01"),
        _reading("S1", 1.0, day="2024-01-01"),
        _reading("S1", 1.0, day="2024-01-02"),
        _reading("S1", 0.0, day="2024-01-03"),
    ])
    row = next(iter(rainfall.station_rows(stations, 2024, "t")))
    assert row[8] == 2, "two wet days"
    assert row[9] == 3, "three days observed"


def test_station_coordinates_come_along_with_the_readings():
    stations = rainfall.fold([_reading("S77", 1.0, name="Alexandra Road")])
    row = next(iter(rainfall.station_rows(stations, 2024, "t")))
    assert row[1] == "Alexandra Road"
    assert row[2] == pytest.approx(1.2937) and row[3] == pytest.approx(103.8125)


def test_a_reading_without_a_station_is_dropped():
    assert rainfall.fold([_reading("", 1.0)]) == {}


def test_rainfall_rows_line_up_with_the_table():
    stations = rainfall.fold([_reading("S77", 1.0)])
    rows = list(rainfall.station_rows(stations, 2024, "test"))
    conn = open_db(":memory:")
    with conn:
        upsert_many(conn, "rainfall_station", rainfall.STATION_COLUMNS, rows,
                    conflict="REPLACE")
    got = conn.execute("SELECT station_id, year, total_mm FROM rainfall_station").fetchone()
    assert (got["station_id"], got["year"]) == ("S77", 2024)
    assert got["total_mm"] == pytest.approx(1.0)


def test_neither_heavy_step_runs_in_the_monthly_sync():
    """A year of five-minute readings is 704 pages for a figure that changes
    once a year, and the land use layer is 174 MB."""
    import inspect

    from nhf import sync as sync_mod

    source = inspect.getsource(sync_mod.run_sync)
    assert 'wanted and "rainfall" in wanted' in source
    assert 'wanted and "land_use" in wanted' in source
