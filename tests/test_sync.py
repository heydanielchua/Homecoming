"""The sync job: it must load what it can, skip what it cannot, and say so.

A silent failure is the dangerous one here — a stale or half-loaded store
looks exactly like a cheap town.
"""

from nhf.config import load_config
from nhf.sync import (
    SyncReporter, add_missing_blocks, backfill_block_towns, derive_town_medians,
    load_future_mrt, run_sync, sync_resale,
)
from nhf.db import open_db


class StubClient:
    """Stands in for data.gov.sg: returns canned records per resource id."""

    def __init__(self, by_resource):
        self.by_resource = by_resource
        self.asked = []

    def records(self, resource_id, limit=None):
        self.asked.append(resource_id)
        if resource_id == "TODO":
            raise AssertionError("sync must not call a TODO resource id")
        yield from self.by_resource.get(resource_id, [])


def resale_record(month, block, price, flat_type="4 ROOM"):
    return {
        "month": month, "town": "BEDOK", "flat_type": flat_type, "block": block,
        "street_name": "BEDOK NORTH ST 1", "storey_range": "07 TO 09",
        "floor_area_sqm": "93", "flat_model": "Improved",
        "lease_commence_date": "1985", "resale_price": str(price),
    }


def test_sync_resale_loads_and_logs():
    conn = open_db(":memory:")
    cfg = load_config()
    resources = {
        entry["id"]: [resale_record("2024-0%d" % (i + 1), "210", 500000 + i)
                      for i in range(3)]
        for entry in cfg.resource_ids("resale") if entry["id"] != "TODO"
    }
    client = StubClient(resources)
    reporter = SyncReporter(conn)
    loaded = sync_resale(conn, client, reporter, cfg)

    assert loaded == 3 * len(resources)
    statuses = {r["dataset_key"]: r["status"] for r in reporter.results}
    assert statuses == {"resale": "skipped"} or "resale" in statuses
    # The TODO entry is reported as skipped, not attempted.
    skipped = [r for r in reporter.results if r["status"] == "skipped"]
    assert skipped and "TODO" in skipped[0]["message"]
    assert "TODO" not in client.asked


def test_sync_records_an_error_without_inventing_rows(monkeypatch):
    from nhf.sources.datagov import DataGovError

    class Failing(StubClient):
        def records(self, resource_id, limit=None):
            raise DataGovError("rate limited (429)")

    conn = open_db(":memory:")
    reporter = SyncReporter(conn)
    sync_resale(conn, Failing({}), reporter, load_config())
    errors = [r for r in reporter.results if r["status"] == "error"]
    assert errors and "rate limited" in errors[0]["message"]
    assert conn.execute("SELECT COUNT(*) AS n FROM resale_txn").fetchone()["n"] == 0


def test_derive_town_medians_from_transactions():
    conn = open_db(":memory:")
    client = StubClient({
        entry["id"]: [resale_record("2024-01", "210", p) for p in (400000, 500000, 600000)]
        for entry in load_config().resource_ids("resale") if entry["id"] != "TODO"
    })
    sync_resale(conn, client, SyncReporter(conn), load_config())
    assert derive_town_medians(conn) >= 1
    row = conn.execute(
        "SELECT median_price, source FROM town_median WHERE town='BEDOK'"
    ).fetchone()
    assert row["median_price"] == 500000
    assert row["source"] == "derived"


def test_blocks_that_only_appear_in_transactions_are_kept():
    conn = open_db(":memory:")
    conn.execute(
        "INSERT INTO resale_txn (month, quarter, town, flat_type, block, street_name,"
        " block_key, resale_price) VALUES"
        " ('2024-01','2024-Q1','BEDOK','4 ROOM','210','BEDOK NORTH ST 1',"
        "  '210|BEDOK NORTH ST 1', 500000)"
    )
    conn.commit()
    assert add_missing_blocks(conn) == 1
    assert backfill_block_towns(conn) >= 0
    row = conn.execute("SELECT * FROM block").fetchone()
    assert row["block_key"] == "210|BEDOK NORTH ST 1"
    assert row["town"] == "BEDOK"
    # Top-floor detection must read as unknown, not as "ground floor".
    assert row["max_floor_lvl"] is None


def test_empty_future_mrt_table_is_reported_not_hidden():
    conn = open_db(":memory:")
    reporter = SyncReporter(conn)
    load_future_mrt(conn, load_config(), reporter)
    entry = reporter.results[-1]
    assert entry["dataset_key"] == "future_mrt"
    assert entry["status"] == "skipped"
    assert "no stations yet" in entry["message"]


def test_run_sync_steps_can_be_selected():
    conn = open_db(":memory:")
    results = run_sync(conn, steps=["future_mrt"], client=StubClient({}))
    assert [r["dataset_key"] for r in results] == ["future_mrt"]
