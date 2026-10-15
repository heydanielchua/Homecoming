"""What you would see out of the window, and what could still be built in it.

Two different kinds of claim live here, and the report keeps them apart
because mixing them is how a tool starts lying.

**What stands there now is fact.** HDB publishes `max_floor_lvl` for every
one of its blocks, so "the block 55 metres to the west is 25 storeys and you
are on the 4th" is not an inference — it is two published numbers and some
trigonometry. The only estimate in it is the floor-to-floor height, which the
data does not carry, and the report says so.

**What could be built there is an estimate, and a wide one.** URA publishes
the Master Plan's zoning and its gross plot ratio, and no height at all.
`GPR_B_MN`, URA's own height band field, is filled in on 52 parcels out of
113,212, so it cannot be leaned on. A ratio has to be turned into storeys,
and the honest way to do that is to read it off the blocks that have already
been built at that ratio rather than to assume a site coverage: the fitted
band is the 10th to 90th percentile of the storeys actually standing on
parcels of that plot ratio, with the number of blocks it rests on attached.

Where that fit is not available — the land use layer is 174 MB and is fetched
on demand rather than monthly — there is a documented fallback that divides
the ratio by a plausible range of site coverage, and the band it returns is
wider and labelled as the cruder guess it is.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from math import atan2, degrees

from .geo import angular_span, bearing_deg, compass_point, haversine_m
from .stats import percentile
from .sun import EYE_HEIGHT_M, STOREY_HEIGHT_M, Obstruction, viewer_height_m

# How far away a block still matters to a view. Beyond this a 30-storey slab
# subtends less than six degrees of horizon and reads as skyline rather than
# as something in the way.
NEIGHBOUR_RADIUS_M = 300.0

# What a view is blocked *by*: another flat above yours. Half a metre of
# slack on the comparison, because floor-to-floor height is a nominal figure.
#
# Note which height goes into that comparison. A block's roof stands a storey
# above its own top floor, so comparing your eyes with a neighbour's roof
# would make every block of equal height "taller than you" and report a
# blocked view across half the estate. What blocks a view is somebody's floor
# above your eye line; what shades the sun is the roof above it, and the two
# are a storey apart.
BLOCKED_SLACK_M = 0.5

# A nominal HDB slab is about sixty metres end to end. This is the fallback
# for a block whose footprint did not load: the property information dataset
# gives a point per block and no shape, so without an outline the angular
# width has to come from one stated length rather than from the real thing.
#
# It is a poor substitute and the numbers say so. All seventeen blocks within
# 300 m of 8 Boon Keng Road have footprints, and against them the nominal slab
# is out by 8.8 degrees on average and 24.2 at worst, in both directions: the
# nearest block is over-reported by 24 degrees and one 195 m away is
# under-reported by 17. Where an outline exists the real shape is used.
NOMINAL_BLOCK_LENGTH_M = 60.0

# Site coverage for high-rise residential here runs from about a tenth of the
# plot for point blocks with everything underground to about a quarter for
# slabs. Storeys ~ plot ratio / coverage, so the two ends of that range are
# the two ends of the fallback band.
MIN_SITE_COVERAGE = 0.10
MAX_SITE_COVERAGE = 0.25

# Zoning that could put something tall next door. A parcel already carrying
# its block is not news; a reserve site or an empty residential plot is.
BUILDABLE_USES = {
    "RESIDENTIAL",
    "RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY",
    "RESIDENTIAL / INSTITUTION",
    "COMMERCIAL",
    "COMMERCIAL & RESIDENTIAL",
    "COMMERCIAL / RESIDENTIAL",
    "HOTEL",
    "WHITE",
    "BUSINESS PARK",
    "BUSINESS 1",
    "BUSINESS 2",
    "BUSINESS 1 - WHITE",
    "BUSINESS 2 - WHITE",
    "RESERVE SITE",
}

# Zoning that will not: these are the reassuring neighbours, and saying so is
# as useful as the warning.
PROTECTED_USES = {
    "PARK", "OPEN SPACE", "BEACH AREA", "WATERBODY", "ROAD",
    "CEMETERY", "AGRICULTURE", "SPECIAL USE", "UTILITY",
    "PLACE OF WORSHIP", "EDUCATIONAL INSTITUTION", "HEALTH & MEDICAL CARE",
    "SPORTS & RECREATION", "CIVIC & COMMUNITY INSTITUTION", "TRANSPORT FACILITIES",
}


@dataclass
class Neighbour:
    """Another block, as the view sees it."""

    name: str
    distance_m: float
    bearing: float
    storeys: int | None = None
    max_floor_lvl: int | None = None
    # The horizon this block really covers, as (centre bearing, half-width),
    # measured from its footprint. None where no footprint joined to it.
    span: tuple[float, float] | None = None

    @property
    def floors(self) -> int | None:
        return self.storeys if self.storeys is not None else self.max_floor_lvl

    @property
    def roof_m(self) -> float | None:
        """Top of the structure above its own ground. This is what shades."""
        floors = self.floors
        if not floors:
            return None
        return floors * STOREY_HEIGHT_M

    @property
    def top_floor_m(self) -> float | None:
        """The floor of its highest flat. This is what blocks a view."""
        floors = self.floors
        if not floors:
            return None
        return (floors - 1) * STOREY_HEIGHT_M

    def _raw_half_width_deg(self) -> float:
        if self.span is not None:
            return self.span[1]
        if self.distance_m <= 0:
            return 90.0
        return min(90.0, degrees(atan2(NOMINAL_BLOCK_LENGTH_M / 2.0, self.distance_m)))

    @property
    def half_width_deg(self) -> float:
        """How much horizon it covers, from its footprint where there is one.

        Rounded to the tenth of a degree the block file ships. The browser
        recomputes the blocked arc from that shipped number and can see no
        more precision than it carries, so working to more here only makes
        the two answers differ in the last decimal — which is a failing test
        and no better an answer.
        """
        return round(self._raw_half_width_deg() * 2.0, 1) / 2.0

    @property
    def arc_centre_deg(self) -> float:
        """Where that coverage is centred.

        Not the same as `bearing`, which points at the block's own position
        and is what the table shows. A long slab seen obliquely covers an arc
        whose middle is off to one side of its centroid, and merging arcs
        about the wrong centre is how a blocked figure drifts.
        """
        return round(self.span[0] if self.span is not None else self.bearing, 1)

    @property
    def width_basis(self) -> str:
        return "footprint" if self.span is not None else "nominal"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "distance_m": round(self.distance_m, 1),
            "bearing": round(self.bearing, 1),
            "direction": compass_point(self.bearing),
            "storeys": self.storeys if self.storeys is not None else self.max_floor_lvl,
            "covers_deg": round(self.half_width_deg * 2.0, 1),
            "covers_centre": self.arc_centre_deg,
            "covers_basis": self.width_basis,
        }


@dataclass
class BlockedNeighbour:
    """A neighbour with flats above the floor being considered."""

    neighbour: Neighbour
    rises_m: float            # how far its highest floor is above your eyes
    elevation_deg: float      # the angle you would look up at its top floor

    def as_dict(self) -> dict:
        return {
            **self.neighbour.as_dict(),
            "rises_m": round(self.rises_m, 1),
            "elevation_deg": round(self.elevation_deg, 1),
            "storeys_above": round(self.rises_m / STOREY_HEIGHT_M, 1),
        }


@dataclass
class View:
    """The view from one floor of one block.

    `unknown` is set when the floor was not typed in, and the site shows the
    view and sun rows as unknown rather than picking a floor — which is the
    PRD's rule and the reason this is a field rather than an empty result.
    """

    storey: int | None = None
    eye_height_m: float | None = None
    unknown: str | None = None
    blocked: list[BlockedNeighbour] = field(default_factory=list)
    clear: list[Neighbour] = field(default_factory=list)
    blocked_arc_deg: float = 0.0
    open_directions: list[str] = field(default_factory=list)
    above_everything: bool = False

    @property
    def neighbours(self) -> list[Neighbour]:
        """Every neighbour with a published height, blocking or not."""
        return [b.neighbour for b in self.blocked] + list(self.clear)

    def obstructions(self) -> list[Obstruction]:
        """Every neighbour, as the sun calculation wants them.

        Every one, not just the ones blocking the view, and measured to the
        roof rather than to the top floor: a block level with yours does not
        take your view but its parapet still stops the last few minutes of
        sun. A roof below your eye line comes through as a negative height,
        which can never be in front of a sun that is above the horizon.
        """
        if self.eye_height_m is None:
            return []
        return [
            Obstruction(
                bearing=item.bearing,
                distance_m=item.distance_m,
                height_m=item.roof_m - self.eye_height_m,
                half_width=item.half_width_deg,
                label=f"block {item.name}",
            )
            for item in self.neighbours
            if item.roof_m is not None
        ]

    def as_dict(self) -> dict:
        return {
            "storey": self.storey,
            "eye_height_m": self.eye_height_m,
            "unknown": self.unknown,
            "blocked": [b.as_dict() for b in self.blocked],
            "clear": [n.as_dict() for n in self.clear],
            "blocked_arc_deg": round(self.blocked_arc_deg, 1),
            "open_directions": list(self.open_directions),
            "above_everything": self.above_everything,
        }


def _merge_arcs(arcs: list[tuple[float, float]]) -> float:
    """Total horizon covered, in degrees, with overlaps counted once.

    Two blocks in the same direction do not block twice as much view as one,
    and summing their widths would say they did.
    """
    if not arcs:
        return 0.0
    spans: list[tuple[float, float]] = []
    for centre, half in arcs:
        start = (centre - half) % 360.0
        end = (centre + half) % 360.0
        if start <= end:
            spans.append((start, end))
        else:                                  # wraps through north
            spans.append((start, 360.0))
            spans.append((0.0, end))
    spans.sort()
    total = 0.0
    current_start, current_end = spans[0]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    total += current_end - current_start
    return min(360.0, total)


def view_from(
    neighbours: list[Neighbour],
    storey: int | None,
    block_max_floor: int | None = None,
    located: bool = True,
) -> View:
    """Which neighbours stand above a given floor, and which do not.

    `located` is whether the block has coordinates, and it is a separate
    argument because an empty neighbour list means two different things. A
    geocoded block with nothing within 300 m really does have nothing in the
    way; an ungeocoded one has nothing *measured*, and reporting that as a
    clear view in every direction is the false confidence this whole report
    is built to avoid. The list looks identical in both cases, so the caller
    has to say which it is.

    The mirror of this runs in the browser, because the floor is typed in
    there. `tests/golden/view.json` is what keeps the two in step.
    """
    if not located:
        return View(
            storey=storey,
            unknown=(
                "this block has no geocode, so nothing can be measured around "
                "it — what the view looks at is unknown, not clear"
            ),
        )
    if storey is None or storey < 1:
        return View(
            storey=None,
            unknown="the floor was not given, so what the view looks at is unknown",
            clear=list(neighbours),
        )

    eye = viewer_height_m(storey)
    blocked: list[BlockedNeighbour] = []
    clear: list[Neighbour] = []

    for item in sorted(neighbours, key=lambda n: n.distance_m):
        top_floor = item.top_floor_m
        if top_floor is None:
            # A block with no published storey count is not evidence of a
            # clear view, so it is listed as neither.
            continue
        rises = top_floor - eye
        if rises > BLOCKED_SLACK_M:
            blocked.append(BlockedNeighbour(
                neighbour=item,
                rises_m=rises,
                elevation_deg=degrees(atan2(rises, max(1.0, item.distance_m))),
            ))
        else:
            clear.append(item)

    arc = _merge_arcs(
        [(b.neighbour.arc_centre_deg, b.neighbour.half_width_deg) for b in blocked]
    )
    blocked_points = {compass_point(b.neighbour.bearing) for b in blocked}
    open_points = [
        p for p in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        if p not in blocked_points
    ]

    return View(
        storey=storey,
        eye_height_m=round(eye, 1),
        blocked=blocked,
        clear=clear,
        blocked_arc_deg=arc,
        open_directions=open_points,
        # Only when something is known to be below. A neighbour whose storey
        # count HDB does not publish leaves `clear` empty, and an empty
        # `clear` is not a view over the rooftops.
        above_everything=bool(clear) and not blocked,
    )


def neighbours_of(
    conn: sqlite3.Connection,
    block_key: str,
    radius_m: float = NEIGHBOUR_RADIUS_M,
) -> list[Neighbour]:
    """Every other HDB block within `radius_m`, with its published height.

    Only HDB blocks: private developments are not in the property information
    dataset, so a condominium next door is a hole in this answer and the
    report says as much rather than reporting a clear view.
    """
    origin = conn.execute(
        "SELECT lat, lon FROM block WHERE block_key = ?", (block_key,)
    ).fetchone()
    if not origin or origin["lat"] is None:
        return []

    # A degree of latitude is 110.6 km here and a degree of longitude 111.3,
    # so a box a little larger than the radius prefilters cheaply before the
    # real distance is computed.
    pad = radius_m / 110_000.0
    rows = conn.execute(
        """
        SELECT block, street_name, block_key, lat, lon, max_floor_lvl, outline
        FROM block
        WHERE block_key <> ? AND lat IS NOT NULL AND lon IS NOT NULL
          AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
        """,
        (block_key, origin["lat"] - pad, origin["lat"] + pad,
         origin["lon"] - pad, origin["lon"] + pad),
    ).fetchall()

    out: list[Neighbour] = []
    for row in rows:
        distance = haversine_m(origin["lat"], origin["lon"], row["lat"], row["lon"])
        if distance > radius_m:
            continue
        span = None
        if row["outline"]:
            try:
                span = angular_span(
                    origin["lat"], origin["lon"], json.loads(row["outline"])
                )
            except (ValueError, TypeError):
                span = None
        out.append(Neighbour(
            name=row["block"],
            distance_m=distance,
            bearing=bearing_deg(origin["lat"], origin["lon"], row["lat"], row["lon"]),
            max_floor_lvl=row["max_floor_lvl"],
            span=span,
        ))
    return sorted(out, key=lambda n: n.distance_m)


# --- what could still be built -------------------------------------------
@dataclass
class HeightBand:
    """Storeys a plot ratio could carry, as a range and never a number."""

    low: int | None
    high: int | None
    basis: str                    # fitted | coverage | none
    note: str
    gpr: float | None = None
    n_blocks: int | None = None

    def as_dict(self) -> dict:
        return {
            "low": self.low, "high": self.high, "basis": self.basis,
            "note": self.note, "gpr": self.gpr, "n_blocks": self.n_blocks,
        }


# What URA writes instead of a ratio, and whether it answers the question.
# These are not the same kind of blank and must not collapse into one: a
# parcel marked landed housing is a confident statement that nothing tall is
# going up on it, while one marked EVA is an admission that nobody has
# decided. Reporting both as "unknown" would throw away the better half of
# the layer — LND is 52% of all parcels.
LANDED_STOREYS = (2, 3)


def coverage_band(gpr: float | None, gpr_note: str | None = None) -> HeightBand:
    """The fallback: a ratio divided by a range of plausible site coverage."""
    if not gpr or gpr <= 0:
        note = (gpr_note or "").lower()
        if "landed" in note:
            return HeightBand(
                LANDED_STOREYS[0], LANDED_STOREYS[1], "zoning",
                "zoned for landed housing, which is two or three storeys — so "
                "this is one neighbour that cannot grow into the view",
            )
        if note:
            return HeightBand(
                None, None, "none",
                f"URA publishes no plot ratio for this parcel: {gpr_note}",
            )
        return HeightBand(
            None, None, "none",
            "URA publishes no plot ratio for this parcel, so nothing can be "
            "said about how tall it could be",
        )
    return HeightBand(
        low=max(1, int(gpr / MAX_SITE_COVERAGE)),
        high=max(1, round(gpr / MIN_SITE_COVERAGE)),
        basis="coverage",
        gpr=gpr,
        note=(
            f"estimated from plot ratio {gpr:g} and a site coverage of "
            f"{MIN_SITE_COVERAGE:g} to {MAX_SITE_COVERAGE:g}. URA publishes "
            f"the ratio, not a height"
        ),
    )


def fit_height_bands(
    conn: sqlite3.Connection, min_blocks: int = 20
) -> dict[float, tuple[int, int, int]]:
    """Storeys actually built, by plot ratio, from HDB's own blocks.

    Each HDB block is matched to the land parcel whose bounding box contains
    it, and the storey counts on each ratio become a band. This is a much
    better answer than dividing by an assumed coverage, because it is the
    same city's own practice at that ratio — and it comes with a count, so a
    ratio nobody has built much at reads as thin.

    Returns `{gpr: (p10, p90, n)}`. Empty when the land use layer has not
    been fetched, which is the normal state: it is 174 MB and is loaded by
    `nhf sync --step land_use` rather than monthly.
    """
    rows = conn.execute(
        """
        SELECT p.gpr_numeric AS gpr, b.max_floor_lvl AS floors
        FROM block b
        JOIN land_parcel p
          ON b.lat BETWEEN p.min_lat AND p.max_lat
         AND b.lon BETWEEN p.min_lon AND p.max_lon
        WHERE b.max_floor_lvl IS NOT NULL AND b.max_floor_lvl > 0
          AND p.gpr_numeric IS NOT NULL AND p.gpr_numeric > 0
          AND p.land_use LIKE 'RESIDENTIAL%'
        """
    ).fetchall()

    buckets: dict[float, list[int]] = {}
    for row in rows:
        # Plot ratios are published in steps of about a tenth, so rounding to
        # one decimal groups like with like without smearing 1.4 into 2.1.
        buckets.setdefault(round(float(row["gpr"]), 1), []).append(int(row["floors"]))

    out: dict[float, tuple[int, int, int]] = {}
    for gpr, floors in buckets.items():
        if len(floors) < min_blocks:
            continue
        low = percentile(floors, 10.0)
        high = percentile(floors, 90.0)
        if low and high:
            out[gpr] = (int(round(low)), int(round(high)), len(floors))
    return out


def height_band(
    gpr: float | None,
    fitted: dict[float, tuple[int, int, int]] | None = None,
    gpr_note: str | None = None,
) -> HeightBand:
    """A height band for a plot ratio, fitted if the data allows it."""
    if not gpr or gpr <= 0:
        return coverage_band(gpr, gpr_note)
    if fitted:
        key = round(float(gpr), 1)
        hit = fitted.get(key)
        if hit is None and fitted:
            nearest_key = min(fitted, key=lambda k: abs(k - key))
            if abs(nearest_key - key) <= 0.2:
                hit = fitted[nearest_key]
                key = nearest_key
        if hit:
            low, high, n = hit
            return HeightBand(
                low=low, high=high, basis="fitted", gpr=gpr, n_blocks=n,
                note=(
                    f"estimated from the {n} HDB blocks standing on parcels of "
                    f"plot ratio {key:g}: 10th to 90th percentile of their "
                    f"storey counts. URA publishes the ratio, not a height"
                ),
            )
    return coverage_band(gpr, gpr_note)


@dataclass
class Parcel:
    """A piece of land near the block, and what could go up on it."""

    land_use: str | None
    distance_m: float
    bearing: float
    area_sqm: float | None
    gpr: float | None
    gpr_note: str | None
    band: HeightBand
    buildable: bool

    def as_dict(self) -> dict:
        return {
            "land_use": self.land_use,
            "distance_m": round(self.distance_m, 1),
            "bearing": round(self.bearing, 1),
            "direction": compass_point(self.bearing),
            "area_sqm": round(self.area_sqm) if self.area_sqm else None,
            "gpr": self.gpr,
            "gpr_note": self.gpr_note,
            "band": self.band.as_dict(),
            "buildable": self.buildable,
        }


def parcels_near(
    conn: sqlite3.Connection,
    block_key: str,
    radius_m: float = NEIGHBOUR_RADIUS_M,
    fitted: dict[float, tuple[int, int, int]] | None = None,
    min_area_sqm: float = 1000.0,
) -> list[Parcel]:
    """Land within sight that could carry something tall.

    Small parcels are left out: a 200 square metre sliver zoned residential
    is a setback or a substation plot, not a future tower, and listing it
    would bury the one parcel that matters.
    """
    origin = conn.execute(
        "SELECT lat, lon FROM block WHERE block_key = ?", (block_key,)
    ).fetchone()
    if not origin or origin["lat"] is None:
        return []

    pad = radius_m / 110_000.0
    rows = conn.execute(
        """
        SELECT land_use, gpr_numeric, gpr_note, area_sqm, lat, lon,
               min_lat, max_lat, min_lon, max_lon
        FROM land_parcel
        WHERE max_lat >= ? AND min_lat <= ? AND max_lon >= ? AND min_lon <= ?
        """,
        (origin["lat"] - pad, origin["lat"] + pad,
         origin["lon"] - pad, origin["lon"] + pad),
    ).fetchall()

    out: list[Parcel] = []
    for row in rows:
        use = (row["land_use"] or "").upper()
        if use in PROTECTED_USES:
            continue
        if row["area_sqm"] and row["area_sqm"] < min_area_sqm:
            continue
        distance = haversine_m(origin["lat"], origin["lon"], row["lat"], row["lon"])
        if distance > radius_m:
            continue
        out.append(Parcel(
            land_use=row["land_use"],
            distance_m=distance,
            bearing=bearing_deg(origin["lat"], origin["lon"], row["lat"], row["lon"]),
            area_sqm=row["area_sqm"],
            gpr=row["gpr_numeric"],
            gpr_note=row["gpr_note"],
            band=height_band(row["gpr_numeric"], fitted, row["gpr_note"]),
            buildable=use in BUILDABLE_USES,
        ))
    # Biggest first among the buildable ones: a two-hectare reserve site is
    # the story, wherever it sits in the list by distance.
    return sorted(
        out, key=lambda p: (not p.buildable, -(p.area_sqm or 0.0), p.distance_m)
    )
