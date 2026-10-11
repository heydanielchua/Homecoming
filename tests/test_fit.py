"""The fitted curves must recover the shape that is actually in the data.

The fixture is built with a known lease decay and a known storey premium, so
the fit can be checked against the right answer rather than just for being
non-empty.
"""

from nhf.fit import (
    fit_lease_factors, fit_storey_premiums, lease_band, lease_factor,
    load_lease_factors, load_storey_premiums, storey_band, storey_factor,
)
from nhf.sync import derive_town_medians


def test_bands():
    assert lease_band(736) == 60          # 61 years 4 months
    assert lease_band(None) is None
    assert storey_band(10, 12) == 11
    assert storey_band(1, 3) == 1
    assert storey_band(None, None) is None


def test_lease_curve_falls_as_the_lease_runs_down(db):
    derive_town_medians(db)
    assert fit_lease_factors(db) > 0
    table = load_lease_factors(db)["ALL"]
    bands = sorted(table)
    factors = [table[b] for b in bands]
    # Monotone by construction, and a real spread: a 45-year lease is worth
    # clearly less per sqm than a near-new one.
    assert factors == sorted(factors)
    assert factors[0] < 0.85 * factors[-1]


def test_lease_curve_tracks_the_decay_in_the_fixture(db):
    """The fixture prices lease as 0.55 + 0.0045 per year of lease left.

    The fit is expected to understate that, and does: dividing by a town
    cohort removes the part of the lease effect that is collinear with the
    town, because HDB towns were built in waves. What the curve must get
    right is the direction and the rough size, and it must never claim more
    decay than the data holds.
    """
    derive_town_medians(db)
    fit_lease_factors(db)
    table = load_lease_factors(db)["ALL"]

    def truth(years):
        return 0.55 + 0.0045 * years

    short, long = 55, 95
    assert short in table and long in table
    expected = truth(short) / truth(long)          # about 0.82
    got = table[short] / table[long]
    assert got < 0.97, "the curve found no lease decay at all"
    assert got >= expected - 0.02, "the curve overstates lease decay"
    assert abs(got - expected) < 0.12, (got, expected)


def test_storey_premium_rises_with_height(db):
    derive_town_medians(db)
    fit_lease_factors(db)
    assert fit_storey_premiums(db) > 0
    table = load_storey_premiums(db)
    low = storey_factor(table, "4 ROOM", 1, 3)
    high = storey_factor(table, "4 ROOM", 13, 15)
    assert high > low


def test_lookups_fall_back_to_the_all_types_curve(db):
    derive_town_medians(db)
    fit_lease_factors(db)
    table = load_lease_factors(db)
    assert lease_factor(table, "NOT A FLAT TYPE", 600) is not None
    assert lease_factor(table, "4 ROOM", None) is None


def test_thin_cohorts_do_not_flatten_the_curve(db):
    """A town with one sale a quarter must not contribute ratios of exactly 1.

    This is what made the first version of the fit return a flat curve.
    """
    from nhf.fit import MIN_COHORT_SAMPLES, _relative_sales

    derive_town_medians(db)
    rows = _relative_sales(db)
    assert rows
    assert MIN_COHORT_SAMPLES > 1
    # If thin cohorts leaked in, almost every ratio would be exactly 1.0.
    exact_ones = sum(1 for r in rows if r["rel"] == 1.0)
    assert exact_ones / len(rows) < 0.2
