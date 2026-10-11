"""A first end-to-end pass over the fixture store, to prove the batch runs."""


def test_fixture_has_transactions(db):
    row = db.execute("SELECT COUNT(*) AS n FROM resale_txn").fetchone()
    assert row["n"] > 500


def test_batch_steps_fill_tables(fitted_db):
    counts = {
        table: fitted_db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in ("town_median", "lease_factor", "storey_premium",
                      "block_metric", "block_score")
    }
    assert all(n > 0 for n in counts.values()), counts
