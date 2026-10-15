"""F1: a miss must be visible and carry its reason, never hide a unit.

Everything about money and dates here is written against the configured
budget and purchase year rather than against literals that happen to match
them. `config/criteria.yml` holds worked examples now, not a real household's
figures, so a number baked into a test would make editing the example break
the suite — and the examples are meant to be edited.
"""

from nhf.config import load_config
from nhf.criteria import ACCEPTABLE, BEST, FLAGGED, UNKNOWN, evaluate

_HOUSEHOLD = load_config().household
BUDGET = load_config().budget
PURCHASE_YEAR = int(_HOUSEHOLD["purchase_year"])
OVER_TOLERANCE = BUDGET * _HOUSEHOLD["budget_over_tolerance_pct"] / 100

BASE = dict(
    block_key="141|TAMPINES ST 42", town="TAMPINES", flat_type="4 ROOM",
    price=int(BUDGET * 0.93), storey=12, max_floor_lvl=12, corner_unit=True,
    drive_minutes=22.0, lease_years_at_exit=70.0, hardcourt_distance_m=400.0,
)


def level(fit, key):
    return next(c.level for c in fit.checks if c.key == key)


def detail(fit, key):
    return next(c.detail for c in fit.checks if c.key == key)


def test_a_flat_that_fits_everything_says_so():
    fit = evaluate(**BASE)
    assert not fit.misses
    assert fit.badge == "Everything we want"
    assert fit.fit_score == 1.0


def test_price_grades_over_budget_by_how_far():
    over = int(OVER_TOLERANCE * 0.7)          # over budget, inside tolerance
    within = evaluate(**{**BASE, "price": BUDGET})
    acceptable = evaluate(**{**BASE, "price": BUDGET + over})
    flagged = evaluate(**{**BASE, "price": int(BUDGET + OVER_TOLERANCE * 2)})
    assert level(within, "price") == BEST
    assert level(acceptable, "price") == ACCEPTABLE
    assert f"${over:,} over budget" in detail(acceptable, "price")
    assert level(flagged, "price") == FLAGGED
    # Over budget is flagged, not removed.
    assert flagged.checks


def test_far_north_towns_are_flagged_with_the_town_name():
    fit = evaluate(**{**BASE, "town": "WOODLANDS"})
    assert level(fit, "location") == FLAGGED
    assert detail(fit, "location") == "Far north (Woodlands)"


def test_top_floor_beats_high_floor_beats_the_rest():
    top = evaluate(**{**BASE, "storey": 12, "max_floor_lvl": 12})
    high = evaluate(**{**BASE, "storey": 9, "max_floor_lvl": 12})
    low = evaluate(**{**BASE, "storey": 3, "max_floor_lvl": 12})
    assert level(top, "floor") == BEST
    assert level(high, "floor") == ACCEPTABLE
    assert level(low, "floor") == FLAGGED


def test_floor_is_unknown_when_the_block_height_is_unknown():
    fit = evaluate(**{**BASE, "max_floor_lvl": None})
    assert level(fit, "floor") == UNKNOWN
    assert "unknown" in detail(fit, "floor")


def test_small_flat_types_are_flagged():
    assert level(evaluate(**{**BASE, "flat_type": "2 ROOM"}), "flat_type") == FLAGGED
    assert level(evaluate(**{**BASE, "flat_type": "EXECUTIVE"}), "flat_type") == BEST


def test_commute_over_the_limit_is_flagged_with_the_number():
    fit = evaluate(**{**BASE, "drive_minutes": 41.0})
    assert level(fit, "commute") == FLAGGED
    assert "41.0 min" in detail(fit, "commute")


def test_an_unrouted_commute_reads_as_unknown_not_as_fast():
    fit = evaluate(**{**BASE, "drive_minutes": None, "commute_status": "no_token"})
    assert level(fit, "commute") == UNKNOWN
    assert "OneMap token" in detail(fit, "commute")


def test_short_lease_at_exit_is_flagged():
    fit = evaluate(**{**BASE, "lease_years_at_exit": 42.0})
    assert level(fit, "lease") == FLAGGED
    assert "only 42 years left" in detail(fit, "lease")


def test_a_hardcourt_too_close_is_flagged():
    fit = evaluate(**{**BASE, "hardcourt_distance_m": 40.0})
    assert level(fit, "hardcourt") == FLAGGED
    assert "40 m away" in detail(fit, "hardcourt")


def test_hardcourt_unknown_points_at_the_viewing():
    fit = evaluate(**{**BASE, "hardcourt_distance_m": None})
    assert level(fit, "hardcourt") == UNKNOWN
    assert "viewing" in detail(fit, "hardcourt")


def test_earliest_exit_follows_the_mop_rule():
    fit = evaluate(**BASE)
    # Purchase year plus the five-year MOP, whatever the configured year is.
    assert fit.earliest_exit_year == PURCHASE_YEAR + 5


def test_manual_checks_name_the_policies_with_no_open_data():
    fit = evaluate(**BASE)
    joined = " ".join(fit.manual_checks)
    assert "Ethnic Integration Policy" in joined
    assert "CPF" in joined


def test_plus_prime_blocks_are_excluded_when_configured(monkeypatch):
    from nhf import config

    cfg = config.load_config()
    original = cfg.policy["plus_prime"]["blocks"]
    cfg.policy["plus_prime"]["blocks"] = ["141|TAMPINES ST 42"]
    try:
        fit = evaluate(**BASE)
        assert fit.excluded
        assert "rented out" in fit.exclusion_reason
        assert fit.badge == "Excluded"
    finally:
        cfg.policy["plus_prime"]["blocks"] = original


def test_badge_counts_the_flags():
    fit = evaluate(**{**BASE, "town": "YISHUN", "lease_years_at_exit": 20.0})
    assert fit.badge == "2 flags"
    assert len(fit.misses) == 2
