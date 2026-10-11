"""F4: the growth score, what it does when a signal is missing, and the
value-at-exit range."""

import json

from nhf.growth import GrowthScorer, retirement_rent_check, score_all


def test_every_block_gets_a_score_in_range(fitted_db):
    rows = fitted_db.execute("SELECT growth_score FROM block_score").fetchall()
    assert rows
    scores = [r["growth_score"] for r in rows if r["growth_score"] is not None]
    assert scores
    assert all(0 <= s <= 100 for s in scores)


def test_signals_carry_their_value_and_source(fitted_db):
    row = fitted_db.execute(
        "SELECT signals_json FROM block_score WHERE growth_score IS NOT NULL LIMIT 1"
    ).fetchone()
    signals = json.loads(row["signals_json"])["signals"]
    keys = {s["key"] for s in signals}
    assert keys == {"price_momentum", "catch_up_gap", "lease_runway",
                    "new_transport", "nearby_supply", "planned_development"}
    for signal in signals:
        assert signal["source"], signal
        assert "weight" in signal and "contribution" in signal


def test_a_block_below_its_town_scores_higher_on_catch_up(fitted_db):
    """The fixture has a deliberately cheap Tampines block."""
    metrics = fitted_db.execute("SELECT * FROM block_metric").fetchall()
    scored = {(g.block_key, g.flat_type): g for g in GrowthScorer(fitted_db).score(metrics)}
    cheap = scored[("501|TAMPINES ST 42", "4 ROOM")]
    normal = scored[("141|TAMPINES ST 42", "4 ROOM")]

    def catch_up(growth):
        return next(s for s in growth.signals if s.key == "catch_up_gap")

    assert catch_up(cheap).value > catch_up(normal).value
    assert catch_up(cheap).percentile > catch_up(normal).percentile


def test_a_faster_growing_block_scores_higher_on_momentum(fitted_db):
    metrics = fitted_db.execute("SELECT * FROM block_metric").fetchall()
    scored = {(g.block_key, g.flat_type): g for g in GrowthScorer(fitted_db).score(metrics)}

    def momentum(key):
        growth = scored[(key, "4 ROOM")]
        return next(s for s in growth.signals if s.key == "price_momentum").value

    assert momentum("502|TAMPINES ST 42") > momentum("141|TAMPINES ST 42")


def test_an_unavailable_signal_is_explained_not_scored_as_zero(fitted_db):
    """Planned development has no data layer yet, so it must not drag a score down."""
    metrics = fitted_db.execute("SELECT * FROM block_metric LIMIT 5").fetchall()
    growth = GrowthScorer(fitted_db).score(metrics)[0]
    planned = next(s for s in growth.signals if s.key == "planned_development")
    assert planned.available is False
    assert planned.percentile is None
    assert "later phase" in planned.note


def test_missing_signals_do_not_change_the_scale(fitted_db):
    """With a signal switched off the score still spans 0-100, because the
    weights are re-based on what is available."""
    metrics = fitted_db.execute("SELECT * FROM block_metric").fetchall()
    weights = {"price_momentum": 50, "catch_up_gap": 50}
    scores = [g.growth_score for g in
              GrowthScorer(fitted_db, weights=weights).score(metrics)
              if g.growth_score is not None]
    assert max(scores) > 80 and min(scores) < 20


def test_value_at_exit_is_a_range_including_a_downside(fitted_db):
    metric = fitted_db.execute(
        "SELECT * FROM block_metric WHERE flat_type='4 ROOM'"
        " AND median_lease_months IS NOT NULL LIMIT 1"
    ).fetchone()
    scorer = GrowthScorer(fitted_db)
    low, mid, high = scorer.value_at_exit(500000, metric, "4 ROOM")
    assert low <= mid <= high
    # The downside case must sit below today's value after 20 years of lease decay.
    assert low < 500000
    assert scorer.value_at_exit(None, metric, "4 ROOM") == (None, None, None)


def test_exit_values_are_stored(fitted_db):
    row = fitted_db.execute(
        "SELECT exit_low, exit_mid, exit_high FROM block_score"
        " WHERE exit_mid IS NOT NULL LIMIT 1"
    ).fetchone()
    assert row["exit_low"] <= row["exit_mid"] <= row["exit_high"]


def test_retirement_rent_check_reports_lease_at_letting(fitted_db):
    check = retirement_rent_check(fitted_db, "141|TAMPINES ST 42", "4 ROOM")
    assert check["median_rent"] > 0
    assert check["scope"] in ("block", "town")
    assert check["let_from_year"] == 2047        # 2027 purchase + 20 years
    assert check["lease_years_at_let"] > 0
    assert check["citizens_only"] is True
    assert "quota" in check["quota_note"].lower()


def test_rent_check_says_none_when_there_is_no_rental_data(db):
    db.execute("DELETE FROM rental_txn")
    db.commit()
    check = retirement_rent_check(db, "141|TAMPINES ST 42", "4 ROOM")
    assert check["median_rent"] is None
    assert check["scope"] == "none"


def test_score_all_is_idempotent(fitted_db):
    first = score_all(fitted_db)
    second = score_all(fitted_db)
    assert first == second
    assert fitted_db.execute(
        "SELECT COUNT(*) AS n FROM block_score"
    ).fetchone()["n"] == second
