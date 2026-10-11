"""Row mappers for the HDB datasets on data.gov.sg.

Each mapper turns one CKAN record into a tuple ready for the matching table,
or returns None when the record cannot be used (a suppressed median, a
non-residential block, a row with no price). The field names below are the
ones the datasets publish; they differ a little between the four resale
files, so every lookup falls back.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..norm import (
    block_key,
    canon_flat_type,
    clean,
    month_to_quarter,
    parse_lease_commence_year,
    parse_month,
    parse_quarter,
    parse_remaining_lease_months,
    parse_storey_range,
    remaining_lease_months,
    to_float,
    to_int,
)

RESALE_COLUMNS = (
    "month", "quarter", "town", "flat_type", "flat_model", "block",
    "street_name", "block_key", "storey_range", "storey_low", "storey_high",
    "floor_area_sqm", "lease_commence_year", "remaining_lease_months",
    "resale_price", "price_per_sqm", "source",
)

BLOCK_COLUMNS = (
    "block_key", "block", "street_name", "town", "max_floor_lvl",
    "year_completed", "residential", "total_dwelling_units", "units_1room",
    "units_2room", "units_3room", "units_4room", "units_5room", "units_exec",
    "units_multigen",
)

PRICE_INDEX_COLUMNS = ("quarter", "index_value")
TOWN_MEDIAN_COLUMNS = ("quarter", "town", "flat_type", "median_price", "source")
RENTAL_COLUMNS = (
    "month", "town", "block", "street_name", "block_key", "flat_type", "monthly_rent",
)


def _first(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def resale_row(record: dict[str, Any], source: str) -> tuple | None:
    month = parse_month(_first(record, "month", "Month"))
    price = to_float(_first(record, "resale_price", "price"))
    if not month or not price:
        return None

    block = clean(_first(record, "block", "blk_no", "block_no"))
    street = clean(_first(record, "street_name", "street"))
    flat_type = canon_flat_type(_first(record, "flat_type"))
    area = to_float(_first(record, "floor_area_sqm", "floor_area"))
    storey_range = clean(_first(record, "storey_range", "storey"))
    low, high = parse_storey_range(storey_range)
    lease_year = parse_lease_commence_year(
        _first(record, "lease_commence_date", "lease_commence_year")
    )

    # The newest file states the remaining lease; the older ones do not, so it
    # is derived from the lease commencement year at the month of sale.
    lease_left = parse_remaining_lease_months(_first(record, "remaining_lease"))
    if lease_left is None:
        lease_left = remaining_lease_months(lease_year, month)

    psm = round(price / area, 2) if area else None

    return (
        month, month_to_quarter(month), clean(_first(record, "town")), flat_type,
        clean(_first(record, "flat_model")), block, street, block_key(block, street),
        storey_range, low, high, area, lease_year, lease_left, price, psm, source,
    )


def block_row(record: dict[str, Any]) -> tuple | None:
    """A block from HDB Property Information.

    Blocks with no residential units are dropped: they are markets, carparks
    and pavilions, and we are not buying one.
    """
    block = clean(_first(record, "blk_no", "block", "block_no"))
    street = clean(_first(record, "street", "street_name"))
    if not block or not street:
        return None

    residential = clean(_first(record, "residential")) or "N"
    units_total = to_int(_first(record, "total_dwelling_units")) or 0
    if residential != "Y" and units_total == 0:
        return None

    def sold(prefix: str) -> int | None:
        return to_int(_first(record, f"{prefix}_sold", f"{prefix}_rental"))

    return (
        block_key(block, street), block, street,
        clean(_first(record, "bldg_contract_town", "town")),
        to_int(_first(record, "max_floor_lvl", "max_floor_level")),
        to_int(_first(record, "year_completed")),
        residential, units_total,
        sold("1room"), sold("2room"), sold("3room"), sold("4room"),
        sold("5room"), sold("exec"), sold("multigen"),
    )


def price_index_row(record: dict[str, Any]) -> tuple | None:
    quarter = parse_quarter(_first(record, "quarter", "period", "month"))
    value = to_float(_first(record, "index", "index_value", "value"))
    if not quarter or value is None:
        return None
    return (quarter, value)


def town_median_row(record: dict[str, Any]) -> tuple | None:
    """A quarterly town median. HDB suppresses small samples as "-" or "na"."""
    quarter = parse_quarter(_first(record, "quarter", "period", "month"))
    price = to_float(_first(record, "price", "median_price", "resale_price"))
    town = clean(_first(record, "town"))
    flat_type = canon_flat_type(_first(record, "flat_type"))
    if not quarter or not town or not flat_type or price is None:
        return None
    return (quarter, town, flat_type, price, "hdb")


def rental_row(record: dict[str, Any]) -> tuple | None:
    month = parse_month(_first(record, "rent_approval_date", "month"))
    rent = to_float(_first(record, "monthly_rent", "rent"))
    if not month or not rent:
        return None
    block = clean(_first(record, "block", "blk_no"))
    street = clean(_first(record, "street_name", "street"))
    return (
        month, clean(_first(record, "town")), block, street,
        block_key(block, street), canon_flat_type(_first(record, "flat_type")), rent,
    )


def map_records(records: Iterable[dict[str, Any]], mapper, *args) -> Iterable[tuple]:
    """Apply a mapper, dropping records it cannot use."""
    for record in records:
        row = mapper(record, *args)
        if row is not None:
            yield row
