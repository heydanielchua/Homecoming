"""F1. Criteria as soft filters.

No unit is ever hidden for missing a criterion. Each one is graded Best,
Acceptable or Flagged, and a miss carries the reason in plain words, so the
list says "far north" or "$35k over budget" rather than quietly dropping a
flat we might have loved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .norm import canon_flat_type, flat_type_rooms

BEST, ACCEPTABLE, FLAGGED, UNKNOWN = "best", "acceptable", "flagged", "unknown"

LEVEL_SCORE = {BEST: 1.0, ACCEPTABLE: 0.6, FLAGGED: 0.0, UNKNOWN: 0.0}


@dataclass
class Check:
    key: str
    label: str
    level: str
    detail: str
    source: str

    @property
    def is_miss(self) -> bool:
        return self.level == FLAGGED


@dataclass
class Fit:
    checks: list[Check] = field(default_factory=list)
    earliest_exit_year: int | None = None
    excluded: bool = False
    exclusion_reason: str | None = None
    manual_checks: list[str] = field(default_factory=list)

    @property
    def misses(self) -> list[Check]:
        return [c for c in self.checks if c.is_miss]

    @property
    def unknowns(self) -> list[Check]:
        return [c for c in self.checks if c.level == UNKNOWN]

    @property
    def badge(self) -> str:
        """One-word summary for the top of a card."""
        if self.excluded:
            return "Excluded"
        if self.misses:
            return f"{len(self.misses)} flag" + ("s" if len(self.misses) > 1 else "")
        if self.unknowns:
            return "Fits, some unknowns"
        if all(c.level == BEST for c in self.checks):
            return "Everything we want"
        return "Fits"

    @property
    def fit_score(self) -> float:
        known = [c for c in self.checks if c.level != UNKNOWN]
        if not known:
            return 0.0
        return sum(LEVEL_SCORE[c.level] for c in known) / len(known)

    def to_dict(self) -> dict[str, Any]:
        return {
            "badge": self.badge,
            "fit_score": round(self.fit_score, 3),
            "earliest_exit_year": self.earliest_exit_year,
            "excluded": self.excluded,
            "exclusion_reason": self.exclusion_reason,
            "manual_checks": self.manual_checks,
            "checks": [vars(c) for c in self.checks],
        }


def _money(amount: float) -> str:
    return f"${amount:,.0f}"


def evaluate(
    *,
    block_key: str,
    town: str | None,
    flat_type: str,
    price: float | None,
    price_kind: str = "fair value",
    storey: int | None = None,
    max_floor_lvl: int | None = None,
    corner_unit: bool | None = None,
    drive_minutes: float | None = None,
    commute_status: str | None = None,
    lease_years_at_exit: float | None = None,
    hardcourt_distance_m: float | None = None,
) -> Fit:
    """Grade one candidate against every criterion in config/criteria.yml."""
    cfg = load_config()
    household = cfg.household
    flags = cfg.flags
    policy = cfg.policy
    fit = Fit()
    flat_type = canon_flat_type(flat_type)

    # --- price ------------------------------------------------------------
    budget = float(household["budget"])
    tolerance = float(household["budget_over_tolerance_pct"]) / 100.0
    ceiling = budget * (1 + tolerance)
    if price is None:
        fit.checks.append(Check(
            "price", "Price", UNKNOWN, "no recent sales, so no price to judge",
            "Resale transactions",
        ))
    elif price <= budget:
        fit.checks.append(Check(
            "price", "Price", BEST,
            f"{_money(price)} {price_kind}, within {_money(budget)}",
            "Resale transactions",
        ))
    elif price <= ceiling:
        fit.checks.append(Check(
            "price", "Price", ACCEPTABLE,
            f"{_money(price - budget)} over budget "
            f"({(price / budget - 1) * 100:.0f}%, tolerance is "
            f"{household['budget_over_tolerance_pct']}%)",
            "Resale transactions",
        ))
    else:
        fit.checks.append(Check(
            "price", "Price", FLAGGED,
            f"{_money(price - budget)} over budget "
            f"({(price / budget - 1) * 100:.0f}%)",
            "Resale transactions",
        ))

    # --- flat type --------------------------------------------------------
    rooms = flat_type_rooms(flat_type)
    minimum = int(household["min_flat_type_rooms"])
    if rooms is None:
        fit.checks.append(Check(
            "flat_type", "Flat type", UNKNOWN, f"unrecognised flat type {flat_type!r}",
            "Resale data",
        ))
    elif flat_type in [canon_flat_type(f) for f in flags["flat_type"]["flagged"]] or rooms < minimum:
        fit.checks.append(Check(
            "flat_type", "Flat type", FLAGGED,
            f"{flat_type.title()} is smaller than {minimum}-room", "Resale data",
        ))
    else:
        fit.checks.append(Check(
            "flat_type", "Flat type", BEST,
            f"{flat_type.title()}, {minimum}-room or larger", "Resale data",
        ))

    # --- floor ------------------------------------------------------------
    fraction = float(flags["floor"]["high_floor_fraction"])
    if storey is None:
        fit.checks.append(Check(
            "floor", "Floor", UNKNOWN, "no floor given for this unit",
            "Listing + HDB property information",
        ))
    elif not max_floor_lvl:
        fit.checks.append(Check(
            "floor", "Floor", UNKNOWN,
            f"storey {storey}, but the block's top floor is unknown",
            "HDB property information",
        ))
    elif storey >= max_floor_lvl:
        fit.checks.append(Check(
            "floor", "Floor", BEST,
            f"top floor of {max_floor_lvl}", "HDB property information",
        ))
    elif storey >= fraction * max_floor_lvl:
        fit.checks.append(Check(
            "floor", "Floor", ACCEPTABLE,
            f"storey {storey} of {max_floor_lvl}, in the top third",
            "HDB property information",
        ))
    else:
        fit.checks.append(Check(
            "floor", "Floor", FLAGGED,
            f"storey {storey} of {max_floor_lvl}, below the top third",
            "HDB property information",
        ))

    # --- unit position ----------------------------------------------------
    if corner_unit is None:
        fit.checks.append(Check(
            "corner", "Unit position", UNKNOWN,
            "tick this by hand once we have the floor plan", "By hand per listing",
        ))
    else:
        fit.checks.append(Check(
            "corner", "Unit position", BEST if corner_unit else ACCEPTABLE,
            "corner unit" if corner_unit else "not a corner unit", "By hand per listing",
        ))

    # --- location ---------------------------------------------------------
    far_north = [t.upper() for t in flags["location"]["far_north_towns"]]
    if not town:
        fit.checks.append(Check("location", "Location", UNKNOWN, "town unknown", "Town field"))
    elif town.upper() in far_north:
        fit.checks.append(Check(
            "location", "Location", FLAGGED, f"Far north ({town.title()})", "Town field",
        ))
    else:
        fit.checks.append(Check(
            "location", "Location", BEST, town.title(), "Town field",
        ))

    # --- commute ----------------------------------------------------------
    limit = float(household["max_drive_minutes"])
    if drive_minutes is None:
        reason = {
            "no_token": "no OneMap token, so drive time is not routed yet",
            "no_geocode": "block is not geocoded yet",
        }.get(commute_status or "", "drive time not computed yet")
        fit.checks.append(Check(
            "commute", "Commute", UNKNOWN, reason, "OneMap drive routing",
        ))
    elif drive_minutes <= limit * 0.7:
        fit.checks.append(Check(
            "commute", "Commute", BEST,
            f"{drive_minutes:.1f} min to the farther office", "OneMap drive routing",
        ))
    elif drive_minutes <= limit:
        fit.checks.append(Check(
            "commute", "Commute", ACCEPTABLE,
            f"{drive_minutes:.1f} min to the farther office, within {limit:.0f}",
            "OneMap drive routing",
        ))
    else:
        fit.checks.append(Check(
            "commute", "Commute", FLAGGED,
            f"{drive_minutes:.1f} min to the farther office, over {limit:.0f}",
            "OneMap drive routing",
        ))

    # --- hardcourt --------------------------------------------------------
    hardcourt_min = float(flags["hardcourt"]["min_distance_m"])
    if hardcourt_distance_m is None:
        fit.checks.append(Check(
            "hardcourt", "Hardcourt", UNKNOWN,
            "OpenStreetMap check is a later phase; confirm at the viewing",
            "OpenStreetMap + viewing",
        ))
    elif hardcourt_distance_m < hardcourt_min:
        fit.checks.append(Check(
            "hardcourt", "Hardcourt", FLAGGED,
            f"hard court about {hardcourt_distance_m:.0f} m away "
            f"(want {hardcourt_min:.0f} m+)",
            "OpenStreetMap + viewing",
        ))
    else:
        fit.checks.append(Check(
            "hardcourt", "Hardcourt", BEST,
            f"nearest hard court about {hardcourt_distance_m:.0f} m away",
            "OpenStreetMap + viewing",
        ))

    # --- lease ------------------------------------------------------------
    want = float(flags["lease"]["min_years_left_at_exit"])
    holding = cfg.holding_years
    if lease_years_at_exit is None:
        fit.checks.append(Check(
            "lease", "Lease", UNKNOWN, "no lease information for this block",
            "Resale data (lease commencement)",
        ))
    elif lease_years_at_exit >= want:
        fit.checks.append(Check(
            "lease", "Lease", BEST,
            f"{lease_years_at_exit:.0f} years left after our {holding}",
            "Resale data (lease commencement)",
        ))
    else:
        fit.checks.append(Check(
            "lease", "Lease", FLAGGED,
            f"only {lease_years_at_exit:.0f} years left after our {holding} "
            f"(want {want:.0f}+)",
            "Resale data (lease commencement)",
        ))

    # --- policy -----------------------------------------------------------
    plus_prime = policy["plus_prime"]
    if plus_prime.get("exclude_by_default") and block_key in set(plus_prime.get("blocks") or []):
        fit.excluded = True
        fit.exclusion_reason = (
            f"Plus or Prime flat: {policy['mop']['plus_prime_years']}-year MOP and the "
            "whole flat can never be rented out, which is the retirement plan"
        )

    mop_years = int(policy["mop"]["standard_years"])
    fit.earliest_exit_year = int(household["purchase_year"]) + mop_years

    if policy["ethnic_integration_policy"].get("manual_check_required"):
        fit.manual_checks.append(
            "Ethnic Integration Policy and SPR quotas for this block: no open API, "
            "check with HDB before offering."
        )
    if not policy["buyer_finance"].get("confirmed", False):
        fit.manual_checks.append(
            "Buyer CPF and loan limits by lease left are placeholder values in "
            "config/policy.yml: confirm with HDB before this drives a decision."
        )
    return fit
