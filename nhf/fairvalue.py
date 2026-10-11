"""F3. Fair value from comparable sales.

A block's own recent sales are the best guide to what a unit in it is worth,
but they are never quite the unit in question: they sold in a different
quarter, on a different floor, with a different lease left. Each comparable
is adjusted on those three axes before the range is taken, and every
adjustment is reported so a number can be argued with.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .config import load_config
from .fit import lease_factor, load_lease_factors, load_storey_premiums, storey_factor
from .geo import haversine_m
from .norm import canon_flat_type, month_index, remaining_lease_months
from .stats import median, percentile

BELOW, WITHIN, ABOVE = "below", "within", "above"


@dataclass
class Comp:
    month: str
    block_key: str
    storey_range: str
    floor_area_sqm: float
    resale_price: float
    price_per_sqm: float
    remaining_lease_months: int | None
    adjusted_psm: float
    index_adj: float
    storey_adj: float
    lease_adj: float
    distance_m: float | None = None


@dataclass
class FairValue:
    block_key: str
    flat_type: str
    floor_area_sqm: float | None
    storey: int | None
    low: float | None
    mid: float | None
    high: float | None
    psm_low: float | None = None
    psm_mid: float | None = None
    psm_high: float | None = None
    n_comps: int = 0
    scope: str = "none"              # same_block | nearby_blocks | town | none
    as_of_quarter: str | None = None
    comps: list[Comp] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.mid is not None

    def tag(self, asking_price: float | None) -> str | None:
        """Where an asking price sits against the range."""
        if asking_price is None or not self.available:
            return None
        if self.low is not None and asking_price < self.low:
            return BELOW
        if self.high is not None and asking_price > self.high:
            return ABOVE
        return WITHIN

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_key": self.block_key,
            "flat_type": self.flat_type,
            "floor_area_sqm": self.floor_area_sqm,
            "storey": self.storey,
            "low": self.low,
            "mid": self.mid,
            "high": self.high,
            "psm_low": self.psm_low,
            "psm_mid": self.psm_mid,
            "psm_high": self.psm_high,
            "n_comps": self.n_comps,
            "scope": self.scope,
            "as_of_quarter": self.as_of_quarter,
            "notes": self.notes,
        }


class Valuer:
    """Holds the lookups so a page can value many units without re-reading them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        cfg = load_config()
        self.params = cfg.fair_value
        self.lease_factors = load_lease_factors(conn)
        self.storey_premiums = load_storey_premiums(conn)
        self.index = {
            row["quarter"]: row["index_value"]
            for row in conn.execute("SELECT quarter, index_value FROM price_index")
        }
        self.latest_quarter = max(self.index) if self.index else None
        self.latest_month = (
            conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()["m"]
        )

    # -- adjustments -------------------------------------------------------
    def index_adjustment(self, quarter: str) -> float:
        """Bring a past sale to today's market level using the HDB index."""
        if not self.latest_quarter:
            return 1.0
        now = self.index.get(self.latest_quarter)
        then = self.index.get(quarter)
        if not now or not then:
            return 1.0
        return now / then

    def storey_adjustment(self, flat_type: str, target_storey: int | None,
                          comp_low: int | None, comp_high: int | None) -> float:
        if target_storey is None:
            return 1.0
        target = storey_factor(self.storey_premiums, flat_type, target_storey)
        comp = storey_factor(self.storey_premiums, flat_type, comp_low, comp_high)
        if not target or not comp:
            return 1.0
        return target / comp

    def lease_adjustment(self, flat_type: str, target_months: int | None,
                         comp_months: int | None) -> float:
        target = lease_factor(self.lease_factors, flat_type, target_months)
        comp = lease_factor(self.lease_factors, flat_type, comp_months)
        if not target or not comp:
            return 1.0
        return target / comp

    # -- comps -------------------------------------------------------------
    def _cutoff_month(self) -> str | None:
        if not self.latest_month:
            return None
        months = int(self.params["comp_months"])
        idx = month_index(self.latest_month) - months
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"

    def _raw_comps(self, block_keys: list[str], flat_type: str) -> list[sqlite3.Row]:
        cutoff = self._cutoff_month()
        if not block_keys or not cutoff:
            return []
        placeholders = ", ".join("?" for _ in block_keys)
        return self.conn.execute(
            f"""
            SELECT month, quarter, block_key, storey_range, storey_low, storey_high,
                   floor_area_sqm, resale_price, price_per_sqm, remaining_lease_months
            FROM resale_txn
            WHERE block_key IN ({placeholders}) AND flat_type = ? AND month >= ?
              AND price_per_sqm IS NOT NULL
            ORDER BY month DESC
            """,
            (*block_keys, flat_type, cutoff),
        ).fetchall()

    def _nearby_block_keys(self, block_key: str) -> tuple[list[str], dict[str, float]]:
        """Blocks within the configured radius, by straight-line distance."""
        origin = self.conn.execute(
            "SELECT lat, lon, town FROM block WHERE block_key = ?", (block_key,)
        ).fetchone()
        if not origin or origin["lat"] is None:
            return [], {}
        radius = float(self.params["nearby_radius_m"])
        keys: list[str] = []
        distances: dict[str, float] = {}
        for row in self.conn.execute(
            "SELECT block_key, lat, lon FROM block"
            " WHERE lat IS NOT NULL AND lon IS NOT NULL AND town = ?",
            (origin["town"],),
        ):
            d = haversine_m(origin["lat"], origin["lon"], row["lat"], row["lon"])
            if d <= radius:
                keys.append(row["block_key"])
                distances[row["block_key"]] = d
        return keys, distances

    # -- the estimate ------------------------------------------------------
    def estimate(
        self,
        block_key: str,
        flat_type: str,
        storey: int | None = None,
        floor_area_sqm: float | None = None,
        target_lease_months: int | None = None,
        as_of_month: str | None = None,
    ) -> FairValue:
        """Low / mid / high for one unit, from comparable sales.

        `target_lease_months` defaults to the lease left at the purchase year
        in config, because that is when we would be buying.
        """
        flat_type = canon_flat_type(flat_type)
        notes: list[str] = []
        cfg = load_config()

        if target_lease_months is None:
            lease_year = self.conn.execute(
                "SELECT lease_commence_year FROM resale_txn"
                " WHERE block_key = ? AND lease_commence_year IS NOT NULL LIMIT 1",
                (block_key,),
            ).fetchone()
            purchase_month = as_of_month or f"{cfg.household['purchase_year']}-01"
            target_lease_months = (
                remaining_lease_months(lease_year["lease_commence_year"], purchase_month)
                if lease_year else None
            )

        if floor_area_sqm is None:
            row = self.conn.execute(
                "SELECT floor_area_sqm FROM resale_txn"
                " WHERE block_key = ? AND flat_type = ? AND floor_area_sqm IS NOT NULL"
                " ORDER BY month DESC LIMIT 1",
                (block_key, flat_type),
            ).fetchone()
            if row:
                floor_area_sqm = row["floor_area_sqm"]
                notes.append("floor area taken from the block's most recent sale")

        scope = "same_block"
        rows = self._raw_comps([block_key], flat_type)
        distances: dict[str, float] = {}
        if len(rows) < int(self.params["min_comps_same_block"]):
            nearby, distances = self._nearby_block_keys(block_key)
            if nearby:
                widened = self._raw_comps(nearby, flat_type)
                if len(widened) > len(rows):
                    rows, scope = widened, "nearby_blocks"
                    notes.append(
                        f"only {len(self._raw_comps([block_key], flat_type))} sale(s) in this "
                        f"block, so comparables were widened to blocks within "
                        f"{int(self.params['nearby_radius_m'])} m"
                    )
            elif not rows:
                notes.append("this block has no geocode, so comparables could not be widened")

        if not self.index:
            notes.append("no price index loaded, so comparables are not time-adjusted")
        if not self.lease_factors:
            notes.append("lease factor table is not fitted, so no lease adjustment was made")
        if not self.storey_premiums:
            notes.append("storey premium table is not fitted, so no floor adjustment was made")

        comps: list[Comp] = []
        for row in rows:
            index_adj = self.index_adjustment(row["quarter"])
            storey_adj = self.storey_adjustment(
                flat_type, storey, row["storey_low"], row["storey_high"]
            )
            lease_adj = self.lease_adjustment(
                flat_type, target_lease_months, row["remaining_lease_months"]
            )
            comps.append(
                Comp(
                    month=row["month"],
                    block_key=row["block_key"],
                    storey_range=row["storey_range"],
                    floor_area_sqm=row["floor_area_sqm"],
                    resale_price=row["resale_price"],
                    price_per_sqm=row["price_per_sqm"],
                    remaining_lease_months=row["remaining_lease_months"],
                    adjusted_psm=row["price_per_sqm"] * index_adj * storey_adj * lease_adj,
                    index_adj=index_adj,
                    storey_adj=storey_adj,
                    lease_adj=lease_adj,
                    distance_m=distances.get(row["block_key"]),
                )
            )

        if not comps:
            return FairValue(
                block_key=block_key, flat_type=flat_type, floor_area_sqm=floor_area_sqm,
                storey=storey, low=None, mid=None, high=None, scope="none",
                as_of_quarter=self.latest_quarter,
                notes=notes + ["no comparable sales in the look-back window"],
            )

        psms = [c.adjusted_psm for c in comps]
        psm_low = percentile(psms, float(self.params["low_percentile"]))
        psm_mid = median(psms)
        psm_high = percentile(psms, float(self.params["high_percentile"]))
        area = floor_area_sqm

        def price(psm: float | None) -> float | None:
            return round(psm * area, -2) if (psm and area) else None

        if not area:
            notes.append("no floor area known, so only a price per sqm range is shown")

        return FairValue(
            block_key=block_key,
            flat_type=flat_type,
            floor_area_sqm=area,
            storey=storey,
            low=price(psm_low),
            mid=price(psm_mid),
            high=price(psm_high),
            psm_low=round(psm_low, 2) if psm_low else None,
            psm_mid=round(psm_mid, 2) if psm_mid else None,
            psm_high=round(psm_high, 2) if psm_high else None,
            n_comps=len(comps),
            scope=scope,
            as_of_quarter=self.latest_quarter,
            comps=comps[:20],
            notes=notes,
        )
