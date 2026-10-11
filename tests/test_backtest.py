"""The backtest is the gate on the growth score, so it must be honest about
what it can and cannot conclude."""

from nhf.backtest import HINDSIGHT_SIGNALS, realised_growth, run_backtest


def test_backtest_produces_quintiles(fitted_db):
    result = run_backtest(fitted_db, as_of="2019-12", horizon_years=5)
    assert result.n_blocks > 0
    assert len(result.quintiles) == 5
    assert all(q.n > 0 for q in result.quintiles)
    assert result.message


def test_backtest_switches_off_hindsight_signals(fitted_db):
    result = run_backtest(fitted_db, as_of="2019-12", horizon_years=5)
    for key in HINDSIGHT_SIGNALS:
        assert result.weights[key] == 0.0
    assert result.weights["price_momentum"] > 0


def test_backtest_separates_quintiles_when_a_signal_is_informative(fitted_db):
    """The fixture gives each block a persistent growth rate, so momentum
    measured in 2019 should predict 2019-2024. Weighted on momentum alone,
    the backtest must find that; this is the machinery working."""
    result = run_backtest(
        fitted_db, as_of="2019-12", horizon_years=5,
        weights={"price_momentum": 100, "catch_up_gap": 0, "lease_runway": 0},
    )
    assert result.passed is True, result.message
    assert result.quintiles[-1].median_growth > result.quintiles[0].median_growth


def test_backtest_returns_a_verdict_either_way(fitted_db):
    """With the shipped weights on this fixture the verdict can go either way.

    What matters is that it is stated: a score that fails here must not be
    reported as if it had passed.
    """
    result = run_backtest(fitted_db, as_of="2019-12", horizon_years=5)
    assert result.passed in (True, False)
    if result.passed is False:
        assert "revise the weights" in result.message
    else:
        assert "better than chance" in result.message


def test_realised_growth_uses_only_data_after_the_cutoff(fitted_db):
    """A block needs sales in both windows; where it has them, the growth is
    a plausible annual rate."""
    pair = fitted_db.execute(
        """
        SELECT block_key, flat_type FROM resale_txn
        WHERE month BETWEEN '2017-12' AND '2019-12'
          AND block_key IN (SELECT block_key FROM resale_txn
                            WHERE month BETWEEN '2022-12' AND '2024-12')
        GROUP BY block_key, flat_type HAVING COUNT(*) >= 3 LIMIT 1
        """
    ).fetchone()
    growth = realised_growth(
        fitted_db, pair["block_key"], pair["flat_type"], "2019-12", 5
    )
    assert growth is not None
    assert -0.2 < growth < 0.5
    # A block with no sales after the cutoff must be None, not zero.
    assert realised_growth(fitted_db, "999|NOWHERE ROAD", "4 ROOM", "2019-12", 5) is None


def test_backtest_refuses_to_conclude_from_too_little(db):
    db.execute("DELETE FROM resale_txn WHERE month > '2009-01'")
    db.commit()
    result = run_backtest(db, as_of="2009-01", horizon_years=5)
    assert result.passed is None
    assert "backtest" in result.message or "need" in result.message


def test_backtest_as_of_before_any_data_says_so(db):
    result = run_backtest(db, as_of="1995-01")
    assert result.n_blocks == 0
    assert result.passed is None
    assert "nothing to backtest" in result.message


def test_backtest_serialises(fitted_db):
    data = run_backtest(fitted_db).to_dict()
    assert data["quintiles"] and data["weights"] and data["as_of"]
