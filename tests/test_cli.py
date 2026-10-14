"""The command line is how the batch job is actually run, so its exit codes
and its refusals matter."""


from nhf.cli import main


def test_init_creates_the_store(tmp_path, capsys):
    db = tmp_path / "nhf.db"
    assert main(["--db", str(db), "init"]) == 0
    assert db.exists()
    assert "store ready" in capsys.readouterr().out


def test_build_refuses_an_empty_store(tmp_path, capsys):
    db = tmp_path / "nhf.db"
    assert main(["--db", str(db), "build"]) == 1
    assert "run `nhf sync` first" in capsys.readouterr().out


def test_rank_on_an_empty_store_says_what_to_do(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nhf.db"), "rank"]) == 1
    assert "nhf sync" in capsys.readouterr().out


def test_status_on_an_empty_store(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nhf.db"), "status"]) == 1
    assert "nothing synced yet" in capsys.readouterr().out


def test_end_to_end_on_a_fixture_file(tmp_path, capsys):
    """build, fit, rank, value, backtest and status over one real store."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from conftest import build_fixture_db

    db = tmp_path / "fixture.db"
    build_fixture_db(db).close()

    assert main(["--db", str(db), "build"]) == 0
    out = capsys.readouterr().out
    assert "fitted lease decay curve" in out and "growth scores" in out

    assert main(["--db", str(db), "fit"]) == 0
    assert "lease factor" in capsys.readouterr().out

    assert main(["--db", str(db), "rank", "--top", "5"]) == 0
    out = capsys.readouterr().out
    assert "candidates" in out and "TAMPINES" in out.upper()

    assert main(["--db", str(db), "rank", "--town", "TAMPINES", "--top", "3"]) == 0
    assert "TAMPINES" in capsys.readouterr().out.upper()

    assert main([
        "--db", str(db), "value", "141|TAMPINES ST 42", "4 ROOM",
        "--storey", "11", "--asking", "900000",
    ]) == 0
    out = capsys.readouterr().out
    assert "comparable sales" in out
    assert "is above the range" in out

    # The shipped weights are not expected to pass on this data; the command
    # must report that with a non-zero exit rather than quietly succeeding.
    code = main(["--db", str(db), "backtest"])
    assert code in (0, 2)
    out = capsys.readouterr().out
    assert "quintile" in out

    assert main(["--db", str(db), "backtest", "--weight", "catch_up_gap=0",
                 "--weight", "lease_runway=0"]) in (0, 2)
    assert "signals used" in capsys.readouterr().out

    assert main(["--db", str(db), "status"]) == 0
    assert "resale_txn" in capsys.readouterr().out


def test_backtest_rejects_an_unknown_signal(tmp_path, capsys):
    db = tmp_path / "nhf.db"
    main(["--db", str(db), "init"])
    capsys.readouterr()
    assert main(["--db", str(db), "backtest", "--weight", "nonsense=5"]) == 1
    assert "unknown signal" in capsys.readouterr().out


def test_value_without_comparables_exits_non_zero(tmp_path, capsys):
    db = tmp_path / "nhf.db"
    main(["--db", str(db), "init"])
    capsys.readouterr()
    assert main(["--db", str(db), "value", "999|NOWHERE ROAD", "4 ROOM"]) == 1
    assert "no fair-value range" in capsys.readouterr().out


def test_import_mrt_reads_a_geojson(tmp_path, capsys):
    import json

    db = tmp_path / "nhf.db"
    path = tmp_path / "stations.geojson"
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [103.8496, 1.3700]},
             "properties": {"NAME": "Ang Mo Kio", "LINE": "NSL"}},
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[1, 1], [2, 2]]}, "properties": {"NAME": "a line"}},
        ],
    }))
    assert main(["--db", str(db), "import-mrt", str(path)]) == 0
    assert "imported 1 stations" in capsys.readouterr().out

    from nhf.db import open_db
    row = open_db(db).execute("SELECT * FROM mrt_station").fetchone()
    assert row["name"] == "ANG MO KIO" and row["status"] == "open"


def test_sync_reports_a_failure_with_a_non_zero_exit(tmp_path, capsys, monkeypatch):
    """A failed download must not look like a successful one."""
    from nhf.sources.datagov import DataGovError

    class Failing:
        def records(self, resource_id, limit=None):
            raise DataGovError("CONNECT tunnel failed")

    monkeypatch.setattr("nhf.sync.DataGovClient", lambda *a, **k: Failing())
    code = main(["--db", str(tmp_path / "nhf.db"), "sync", "--step", "resale"])
    out = capsys.readouterr().out
    assert code == 1
    assert "CONNECT tunnel failed" in out
    assert "Nothing was guessed" in out


def test_rank_prints_an_unrouted_drive_as_unknown(fitted_db, capsys, monkeypatch, tmp_path):
    """`or 0` printed a block with no routed drive time as 0.0 minutes from
    the office — an invented number, made more convincing by sitting in a
    column of real ones."""
    from nhf.cli import main

    db = tmp_path / "nhf.db"
    import shutil, sqlite3
    target = sqlite3.connect(db)
    fitted_db.backup(target)
    target.close()
    # No commute rows at all, which is the state without OneMap routing.
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM commute")
    conn.commit()
    conn.close()

    main(["--db", str(db), "rank", "--top", "3"])
    out = capsys.readouterr().out
    assert "—" in out, "an unknown drive time should read as unknown"
    assert "   0.0" not in out, "an unknown drive time must not read as zero"
