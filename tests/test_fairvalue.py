"""F3: the range, the adjustments, and the asking-price tag."""

import pytest

from nhf.fairvalue import ABOVE, BELOW, WITHIN, Valuer


def test_range_is_ordered_and_priced(fitted_db):
    valuer = Valuer(fitted_db)
    estimate = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=10)
    assert estimate.available
    assert estimate.low < estimate.mid < estimate.high
    assert estimate.n_comps > 0
    assert estimate.floor_area_sqm == pytest.approx(93.0)
    assert estimate.psm_mid == pytest.approx(estimate.mid / estimate.floor_area_sqm, rel=0.02)


def test_asking_price_is_tagged_against_the_range(fitted_db):
    valuer = Valuer(fitted_db)
    estimate = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=10)
    assert estimate.tag(estimate.low * 0.8) == BELOW
    assert estimate.tag(estimate.mid) == WITHIN
    assert estimate.tag(estimate.high * 1.2) == ABOVE
    assert estimate.tag(None) is None


def test_a_top_floor_unit_values_above_a_low_one(fitted_db):
    valuer = Valuer(fitted_db)
    low = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=2)
    high = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=12)
    assert high.mid > low.mid


def test_a_shorter_lease_values_below_a_longer_one(fitted_db):
    valuer = Valuer(fitted_db)
    fresh = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=8,
                            target_lease_months=90 * 12)
    worn = valuer.estimate("141|TAMPINES ST 42", "4 ROOM", storey=8,
                           target_lease_months=55 * 12)
    assert worn.mid < fresh.mid


def test_comparables_widen_when_a_block_is_thin(fitted_db):
    """A block with too few sales should borrow from its neighbours, and say so."""
    valuer = Valuer(fitted_db)
    thin = fitted_db.execute(
        "SELECT block_key, flat_type, COUNT(*) AS n FROM resale_txn"
        " WHERE month >= '2024-10' GROUP BY block_key, flat_type"
        " HAVING n BETWEEN 1 AND 2 LIMIT 1"
    ).fetchone()
    if not thin:
        pytest.skip("fixture has no thin block-and-type pair")
    estimate = valuer.estimate(thin["block_key"], thin["flat_type"])
    assert estimate.scope in ("nearby_blocks", "same_block")
    if estimate.scope == "nearby_blocks":
        assert any("widened" in note for note in estimate.notes)


def test_unknown_block_returns_no_range_rather_than_a_guess(fitted_db):
    estimate = Valuer(fitted_db).estimate("999|NOWHERE ROAD", "4 ROOM")
    assert not estimate.available
    assert estimate.low is None and estimate.mid is None
    assert estimate.scope == "none"
    assert estimate.notes


def test_index_adjustment_lifts_an_older_sale(fitted_db):
    valuer = Valuer(fitted_db)
    latest = valuer.latest_quarter
    assert valuer.index_adjustment(latest) == pytest.approx(1.0)
    assert valuer.index_adjustment("2015-Q1") > 1.0
    # A quarter the index does not cover must not silently scale anything.
    assert valuer.index_adjustment("1800-Q1") == 1.0
