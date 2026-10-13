"""The source clients: paging, retries, and the row mappers.

Records here are shaped like the CKAN payloads the datasets return, so a
change in their field names shows up as a failing test rather than an empty
database.
"""

import pytest
import requests

from nhf.sources import hdb
from nhf.sources.datagov import DataGovClient, DataGovError


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Serves a resource in pages, and can be told to fail first."""

    def __init__(self, records, fail_times=0, status=200):
        self.records = records
        self.fail_times = fail_times
        self.status = status
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise requests.ConnectionError("network blip")
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 100))
        page = self.records[offset:offset + limit]
        return FakeResponse(
            {"success": True, "result": {"records": page, "total": len(self.records)}},
            self.status,
        )


def test_records_pages_until_exhausted(monkeypatch):
    records = [{"i": i} for i in range(25)]
    session = FakeSession(records)
    client = DataGovClient(session=session)
    client.page_size = 10
    assert list(client.records("d_abc")) == records
    assert [c["offset"] for c in session.calls] == [0, 10, 20]


def test_records_respects_limit():
    client = DataGovClient(session=FakeSession([{"i": i} for i in range(50)]))
    client.page_size = 10
    assert len(list(client.records("d_abc", limit=12))) == 12


def test_records_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("nhf.sources.datagov.time.sleep", lambda _s: None)
    client = DataGovClient(session=FakeSession([{"i": 1}], fail_times=2))
    assert list(client.records("d_abc")) == [{"i": 1}]


def test_records_gives_up_and_raises(monkeypatch):
    monkeypatch.setattr("nhf.sources.datagov.time.sleep", lambda _s: None)
    client = DataGovClient(session=FakeSession([{"i": 1}], fail_times=99))
    client.max_retries = 3
    with pytest.raises(DataGovError):
        list(client.records("d_abc"))


def test_records_refuses_unset_resource_id():
    client = DataGovClient(session=FakeSession([]))
    with pytest.raises(DataGovError, match="TODO"):
        list(client.records("TODO"))


# --- row mappers ---------------------------------------------------------
NEW_RESALE = {
    "month": "2024-03", "town": "ANG MO KIO", "flat_type": "4 ROOM",
    "block": "101", "street_name": "ANG MO KIO AVE 3", "storey_range": "10 TO 12",
    "floor_area_sqm": "92", "flat_model": "Improved",
    "lease_commence_date": "1979", "remaining_lease": "54 years 03 months",
    "resale_price": "530000",
}
OLD_RESALE = {
    "month": "2005-06", "town": "BEDOK", "flat_type": "3 ROOM", "block": "210",
    "street_name": "BEDOK NORTH ST 1", "storey_range": "04 TO 06",
    "floor_area_sqm": "67", "flat_model": "NEW GENERATION",
    "lease_commence_date": "1985", "resale_price": "180000",
}


def test_resale_row_from_newest_file():
    row = dict(zip(hdb.RESALE_COLUMNS, hdb.resale_row(NEW_RESALE, "2017 on")))
    assert row["month"] == "2024-03"
    assert row["quarter"] == "2024-Q1"
    assert row["block_key"] == "101|ANG MO KIO AVE 3"
    assert row["storey_low"] == 10 and row["storey_high"] == 12
    assert row["remaining_lease_months"] == 54 * 12 + 3
    assert row["price_per_sqm"] == pytest.approx(530000 / 92, abs=0.01)


def test_resale_row_derives_lease_for_old_file():
    row = dict(zip(hdb.RESALE_COLUMNS, hdb.resale_row(OLD_RESALE, "2000-2012")))
    # 1985 + 99 = 2084, sold mid-2005 -> about 79 years left.
    assert row["remaining_lease_months"] == pytest.approx(79 * 12, abs=12)


def test_resale_row_drops_rows_with_no_price():
    assert hdb.resale_row({**NEW_RESALE, "resale_price": ""}, "x") is None
    assert hdb.resale_row({**NEW_RESALE, "month": ""}, "x") is None


def test_block_row_keeps_residential_and_drops_the_rest():
    record = {
        "blk_no": "101", "street": "ANG MO KIO AVE 3", "max_floor_lvl": "12",
        "year_completed": "1979", "residential": "Y", "bldg_contract_town": "AMK",
        "total_dwelling_units": "120", "3room_sold": "40", "4room_sold": "60",
        "5room_sold": "20",
    }
    row = dict(zip(hdb.BLOCK_COLUMNS, hdb.block_row(record)))
    assert row["block_key"] == "101|ANG MO KIO AVE 3"
    assert row["max_floor_lvl"] == 12 and row["units_4room"] == 60

    carpark = {**record, "residential": "N", "total_dwelling_units": "0"}
    assert hdb.block_row(carpark) is None


def test_price_index_and_town_median_rows():
    assert hdb.price_index_row({"quarter": "2024-Q1", "index": "192.9"}) == ("2024-Q1", 192.9)
    assert hdb.town_median_row(
        {"quarter": "2024-Q1", "town": "BEDOK", "flat_type": "4 ROOM", "price": "550000"}
    ) == ("2024-Q1", "BEDOK", "4 ROOM", 550000.0, "hdb")
    # HDB suppresses thin samples.
    assert hdb.town_median_row(
        {"quarter": "2024-Q1", "town": "BEDOK", "flat_type": "4 ROOM", "price": "-"}
    ) is None


def test_rental_row():
    row = dict(zip(hdb.RENTAL_COLUMNS, hdb.rental_row({
        "rent_approval_date": "2026-06", "town": "BEDOK", "block": "210",
        "street_name": "BEDOK NORTH ST 1", "flat_type": "3-ROOM", "monthly_rent": "2400",
    })))
    assert row["flat_type"] == "3 ROOM" and row["monthly_rent"] == 2400.0


class OversizedPageSession(FakeSession):
    """Answers 413 to any page above `serves`, like HDB property information.

    The real dataset carries 25 columns per block and refuses a page of
    10,000 rows while serving 5,000 happily, which is a property of the row
    width rather than of the resource id.
    """

    def __init__(self, records, serves: int):
        super().__init__(records)
        self.serves = serves
        self.refused: list[int] = []

    def get(self, url, params=None, headers=None, timeout=None):
        limit = int(params.get("limit", 100))
        if limit > self.serves:
            self.calls.append(params)
            self.refused.append(limit)
            return FakeResponse({"error": "too large"}, status=413)
        return super().get(url, params=params, headers=headers, timeout=timeout)


def test_a_413_halves_the_page_instead_of_retrying_it(monkeypatch):
    monkeypatch.setattr("nhf.sources.datagov.time.sleep", lambda _s: None)
    records = [{"i": i} for i in range(1200)]
    session = OversizedPageSession(records, serves=500)
    client = DataGovClient(session=session)
    client.page_size = 2000

    assert list(client.records("d_wide")) == records
    # 2000 and 1000 are refused once each, never retried at the same size.
    assert session.refused == [2000, 1000]
    served = [c["limit"] for c in session.calls if c["limit"] <= 500]
    assert served and all(limit == 500 for limit in served)


def test_the_smaller_page_holds_for_the_rest_of_the_download(monkeypatch):
    monkeypatch.setattr("nhf.sources.datagov.time.sleep", lambda _s: None)
    session = OversizedPageSession([{"i": i} for i in range(900)], serves=250)
    client = DataGovClient(session=session)
    client.page_size = 1000
    list(client.records("d_wide"))
    # One refusal per halving, not one per page.
    assert session.refused == [1000, 500]


def test_a_413_at_the_floor_is_reported_rather_than_looping(monkeypatch):
    monkeypatch.setattr("nhf.sources.datagov.time.sleep", lambda _s: None)
    from nhf.sources.datagov import MIN_PAGE_SIZE

    session = OversizedPageSession([{"i": 1}], serves=1)
    client = DataGovClient(session=session)
    client.page_size = MIN_PAGE_SIZE
    with pytest.raises(DataGovError):
        list(client.records("d_wide"))
