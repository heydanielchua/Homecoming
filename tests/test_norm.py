"""The datasets are inconsistent across the four resale files; this is the
layer that absorbs that, so it is worth pinning down."""

import pytest

from nhf.norm import (
    block_key, canon_flat_type, flat_type_rooms, month_to_quarter,
    months_between, parse_lease_commence_year, parse_month, parse_quarter,
    parse_remaining_lease_months, parse_storey_range, remaining_lease_months,
    to_float,
)


@pytest.mark.parametrize("raw,expected", [
    ("2017-01", "2017-01"),
    ("2017-1", "2017-01"),
    ("2017-01-01", "2017-01"),
    ("Jan-2017", "2017-01"),
    ("", None),
    ("nonsense", None),
])
def test_parse_month(raw, expected):
    assert parse_month(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2024-Q1", "2024-Q1"), ("2024Q3", "2024-Q3"), ("1Q2009", "2009-Q1"),
    ("2024-05", "2024-Q2"), ("", None),
])
def test_parse_quarter(raw, expected):
    assert parse_quarter(raw) == expected


def test_month_to_quarter():
    assert month_to_quarter("2026-09") == "2026-Q3"
    assert month_to_quarter("2026-12") == "2026-Q4"


def test_months_between():
    assert months_between("2024-01", "2026-03") == 26


@pytest.mark.parametrize("raw,expected", [
    ("10 TO 12", (10, 12)), ("01 TO 05", (1, 5)), ("13", (13, 13)), ("", (None, None)),
])
def test_parse_storey_range(raw, expected):
    assert parse_storey_range(raw) == expected


def test_remaining_lease_text():
    assert parse_remaining_lease_months("61 years 04 months") == 736
    assert parse_remaining_lease_months("70 years") == 840
    assert parse_remaining_lease_months("61") == 732       # bare number = years
    assert parse_remaining_lease_months("") is None


def test_remaining_lease_derived_from_commencement():
    # The older files carry only a lease commencement year.
    assert remaining_lease_months(1979, "2026-01") == (99 - 47) * 12
    assert remaining_lease_months(None, "2026-01") is None
    assert remaining_lease_months(1930, "2030-01") == 0    # never negative


def test_block_key_normalises_spacing_and_case():
    assert block_key(" 101 ", "ang mo kio  ave 3") == "101|ANG MO KIO AVE 3"


def test_flat_type_rooms_orders_executive_above_five_room():
    assert flat_type_rooms("3 ROOM") == 3
    assert flat_type_rooms("EXECUTIVE") > flat_type_rooms("5 ROOM")
    assert flat_type_rooms("MULTI-GENERATION") > flat_type_rooms("EXECUTIVE")
    assert flat_type_rooms("") is None


def test_canon_flat_type():
    assert canon_flat_type("3-room") == "3 ROOM"
    assert canon_flat_type(" 4 Room ") == "4 ROOM"


def test_to_float_handles_dataset_blanks():
    assert to_float("450,000") == 450000.0
    assert to_float("$450000") == 450000.0
    assert to_float("na") is None
    assert to_float("-") is None
    assert parse_lease_commence_year("1979") == 1979
