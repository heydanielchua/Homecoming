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


def _store_with_abbreviated_towns():
    """Property information abbreviates the town; the resale files spell it out.

    Shaped like the live data: `SB` for Sembawang and `YS` for Yishun on the
    block rows, full names on the transactions.
    """
    conn = open_db(":memory:")
    conn.executescript(
        """
        INSERT INTO resale_txn (month, quarter, town, flat_type, block, street_name,
                                block_key, resale_price) VALUES
          ('2024-01','2024-Q1','SEMBAWANG','4 ROOM','463B','SEMBAWANG DR',
           '463B|SEMBAWANG DR', 500000),
          ('2024-02','2024-Q1','YISHUN','4 ROOM','888','YISHUN AVE 11',
           '888|YISHUN AVE 11', 480000);
        INSERT INTO block (block_key, block, street_name, town) VALUES
          ('463B|SEMBAWANG DR','463B','SEMBAWANG DR','SB'),
          ('888|YISHUN AVE 11','888','YISHUN AVE 11','YS'),
          ('999|SEMBAWANG WAY','999','SEMBAWANG WAY','SB');
        """
    )
    conn.commit()
    return conn


def test_abbreviated_town_codes_are_translated_to_the_spelling_used_elsewhere():
    conn = _store_with_abbreviated_towns()
    assert backfill_block_towns(conn) == 3
    towns = dict(conn.execute("SELECT block_key, town FROM block"))
    assert towns["463B|SEMBAWANG DR"] == "SEMBAWANG"
    assert towns["888|YISHUN AVE 11"] == "YISHUN"


def test_a_block_with_no_sales_is_translated_by_what_the_others_showed():
    """The far-north criterion names towns, so a lone coded block still matters."""
    conn = _store_with_abbreviated_towns()
    backfill_block_towns(conn)
    town = conn.execute(
        "SELECT town FROM block WHERE block_key = '999|SEMBAWANG WAY'"
    ).fetchone()["town"]
    assert town == "SEMBAWANG"


def test_a_code_with_nothing_to_learn_from_is_left_alone_not_guessed():
    conn = open_db(":memory:")
    conn.executescript(
        """
        INSERT INTO resale_txn (month, quarter, town, flat_type, block, street_name,
                                block_key, resale_price) VALUES
          ('2024-01','2024-Q1','BEDOK','4 ROOM','210','BEDOK NORTH ST 1',
           '210|BEDOK NORTH ST 1', 500000);
        INSERT INTO block (block_key, block, street_name, town) VALUES
          ('210|BEDOK NORTH ST 1','210','BEDOK NORTH ST 1','BD'),
          ('1|TENGAH GARDEN WALK','1','TENGAH GARDEN WALK','TG');
        """
    )
    conn.commit()
    backfill_block_towns(conn)
    # Tengah has no resale sales yet, so there is nothing to translate it with.
    assert conn.execute(
        "SELECT town FROM block WHERE block_key = '1|TENGAH GARDEN WALK'"
    ).fetchone()["town"] == "TG"


def test_a_town_already_spelled_out_is_not_touched():
    conn = _store_with_abbreviated_towns()
    conn.execute("UPDATE block SET town = 'SEMBAWANG' WHERE town = 'SB'")
    conn.commit()
    backfill_block_towns(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM block WHERE town = 'SEMBAWANG'"
    ).fetchone()[0] == 2


def test_the_far_north_flag_fires_once_the_town_is_canonical():
    from nhf.criteria import evaluate

    conn = _store_with_abbreviated_towns()
    coded = evaluate(block_key="463B|SEMBAWANG DR", town="SB", flat_type="4 ROOM",
                     price=500000, price_kind="median recent sale")
    assert not [c for c in coded.checks
                if c.key == "location" and c.level == "flagged"], \
        "the abbreviation is why this silently passed"

    backfill_block_towns(conn)
    town = conn.execute(
        "SELECT town FROM block WHERE block_key = '463B|SEMBAWANG DR'"
    ).fetchone()["town"]
    fixed = evaluate(block_key="463B|SEMBAWANG DR", town=town, flat_type="4 ROOM",
                     price=500000, price_kind="median recent sale")
    assert [c for c in fixed.checks
            if c.key == "location" and c.level == "flagged"]


class TestABlockKeepsItsCoordinates:
    """The monthly sync must not undo the geocoding.

    `block` holds two things from two places: what HDB publishes about a
    block, which the sync refreshes every month, and the coordinates OneMap
    took hours to find. An `INSERT OR REPLACE` deletes the row and re-inserts
    it with only the published columns set, so a refresh threw away every
    geocode and the next step spent two hours finding them again. Nothing
    failed and nothing was logged; the only symptom was a sync that never got
    faster.
    """

    def _conn(self):
        from nhf.db import open_db

        return open_db(":memory:")

    def test_a_refresh_updates_hdbs_columns(self):
        from nhf.db import upsert_many
        from nhf.sources.hdb import BLOCK_COLUMNS

        conn = self._conn()
        row = ("1|X RD", "1", "X RD", "TOWN", 12, 1990, "Y", 100,
               0, 0, 30, 50, 20, 0, 0)
        upsert_many(conn, "block", BLOCK_COLUMNS, [row])
        taller = ("1|X RD", "1", "X RD", "TOWN", 16, 1990, "Y", 100,
                  0, 0, 30, 50, 20, 0, 0)
        upsert_many(
            conn, "block", BLOCK_COLUMNS, [taller], key="block_key",
            update=[c for c in BLOCK_COLUMNS if c != "block_key"],
        )
        assert conn.execute(
            "SELECT max_floor_lvl FROM block").fetchone()["max_floor_lvl"] == 16

    def test_a_refresh_leaves_the_geocode_alone(self):
        from nhf.db import upsert_many
        from nhf.sources.hdb import BLOCK_COLUMNS

        conn = self._conn()
        row = ("1|X RD", "1", "X RD", "TOWN", 12, 1990, "Y", 100,
               0, 0, 30, 50, 20, 0, 0)
        upsert_many(conn, "block", BLOCK_COLUMNS, [row])
        conn.execute(
            "UPDATE block SET lat = 1.3, lon = 103.8, postal = '640001',"
            " geocode_source = 'onemap', geocoded_at = '2026-10-01'"
            " WHERE block_key = '1|X RD'"
        )
        upsert_many(
            conn, "block", BLOCK_COLUMNS, [row], key="block_key",
            update=[c for c in BLOCK_COLUMNS if c != "block_key"],
        )
        kept = conn.execute(
            "SELECT lat, lon, postal, geocoded_at FROM block").fetchone()
        assert kept["lat"] == 1.3 and kept["lon"] == 103.8
        assert kept["postal"] == "640001"
        assert kept["geocoded_at"] == "2026-10-01"

    def test_replace_is_what_used_to_lose_it(self):
        """Kept as the counter-example, so the fix is not mistaken for a
        no-op by whoever reads this next."""
        from nhf.db import upsert_many
        from nhf.sources.hdb import BLOCK_COLUMNS

        conn = self._conn()
        row = ("1|X RD", "1", "X RD", "TOWN", 12, 1990, "Y", 100,
               0, 0, 30, 50, 20, 0, 0)
        upsert_many(conn, "block", BLOCK_COLUMNS, [row])
        conn.execute("UPDATE block SET lat = 1.3 WHERE block_key = '1|X RD'")
        upsert_many(conn, "block", BLOCK_COLUMNS, [row], conflict="REPLACE")
        assert conn.execute("SELECT lat FROM block").fetchone()["lat"] is None

    def test_the_sync_step_uses_the_careful_upsert(self, monkeypatch):
        """Against the real step, not just the helper."""
        from nhf.config import load_config
        from nhf.sources.hdb import BLOCK_COLUMNS
        from nhf.sync import SyncReporter, sync_blocks

        conn = self._conn()
        record = {
            "blk_no": "1", "street": "X RD", "bldg_contract_town": "TOWN",
            "max_floor_lvl": "12", "year_completed": "1990", "residential": "Y",
            "total_dwelling_units": "100", "4room_sold": "50",
        }

        class FakeClient:
            def records(self, resource_id, limit=None):
                return iter([record])

        reporter = SyncReporter(conn)
        sync_blocks(conn, FakeClient(), reporter, load_config())
        conn.execute("UPDATE block SET lat = 1.3, lon = 103.8")

        record["max_floor_lvl"] = "16"
        sync_blocks(conn, FakeClient(), reporter, load_config())
        after = conn.execute(
            "SELECT max_floor_lvl, lat FROM block").fetchone()
        assert after["max_floor_lvl"] == 16, "the refresh must still refresh"
        assert after["lat"] == 1.3, "and must not cost two hours of geocoding"
        assert len(BLOCK_COLUMNS) > 1
