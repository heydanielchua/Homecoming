"""What is near a block, and how far.

All of this comes out of one table, because the question has one shape. What
differs is which answer is a fact and which is worked out, and the report
carries that distinction rather than printing eight numbers that look alike:

* An MRT exit, a bus stop, a hawker centre and a school are published as
  points, so the distance to them is a straight line between two published
  coordinates — worked out, since you walk along roads and not along the
  straight line, and the report says the straight line is a lower bound.
* A park is published as a boundary, so the distance is to its nearest edge
  rather than to its middle. Bukit Timah's centroid sits over a kilometre
  from blocks whose nearest fence is two hundred metres away.
* A stall count and a park's area are published figures, and are facts.

What is deliberately not here is dog runs, which the park facilities layer
has three of for the whole country, and which buses call at a stop, which
needs an LTA DataMall key this does not have. Both are in the PRD's list of
things not to promise.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .geo import bearing_deg, compass_point, distance_to_box_m, haversine_m

# How far out to look for each kind of thing. These are the distances at which
# the answer stops being interesting rather than round numbers: past a
# kilometre and a half nothing is walkable, and the within-1-km school count
# is the band the PRD asks for because it is the band that moves a resale
# price.
SEARCH_RADIUS_M = 2000.0
WALKING_M = 400.0
SCHOOL_BAND_M = 1000.0

# The park facility classes worth counting within walking distance. The layer
# is 63% car parks; a car park is not an amenity you choose a flat for.
WALKABLE_FACILITIES = ("PLAYGROUND", "FITNESS AREA", "BBQ PIT", "MULTIPURPOSE COURT")


@dataclass
class Place:
    """One thing near the block."""

    kind: str
    name: str | None
    distance_m: float
    bearing: float
    subtype: str | None = None
    detail: str | None = None
    status: str | None = None
    size_hint: float | None = None
    to_edge: bool = False

    def as_dict(self) -> dict:
        out = {
            "name": self.name,
            "distance_m": round(self.distance_m),
            "direction": compass_point(self.bearing),
        }
        if self.subtype:
            out["subtype"] = self.subtype
        if self.detail:
            out["detail"] = self.detail
        if self.status:
            out["status"] = self.status
        if self.size_hint is not None:
            out["size"] = round(self.size_hint, 1)
        if self.to_edge:
            out["to_edge"] = True
        return out


@dataclass
class Nearby:
    """Everything the Nearby section of the report shows."""

    mrt_exit: Place | None = None
    mrt_station: str | None = None
    bus_stop: Place | None = None
    hawker: Place | None = None
    hawker_opening: Place | None = None
    park: Place | None = None
    primary_school: Place | None = None
    schools_within_1km: int | None = None
    preschools_within_walk: int | None = None
    facilities: dict[str, int] = field(default_factory=dict)
    nearest_facility: dict[str, Place] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mrt_exit": self.mrt_exit.as_dict() if self.mrt_exit else None,
            "mrt_station": self.mrt_station,
            "bus_stop": self.bus_stop.as_dict() if self.bus_stop else None,
            "hawker": self.hawker.as_dict() if self.hawker else None,
            "hawker_opening": (
                self.hawker_opening.as_dict() if self.hawker_opening else None
            ),
            "park": self.park.as_dict() if self.park else None,
            "primary_school": (
                self.primary_school.as_dict() if self.primary_school else None
            ),
            "schools_within_1km": self.schools_within_1km,
            "preschools_within_walk": self.preschools_within_walk,
            "facilities": dict(self.facilities),
            "nearest_facility": {
                k: v.as_dict() for k, v in self.nearest_facility.items()
            },
            "missing": list(self.missing),
        }


def _places(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    kind: str,
    radius_m: float = SEARCH_RADIUS_M,
    subtypes: tuple[str, ...] | None = None,
    to_edge: bool = False,
) -> list[Place]:
    """Everything of one kind within the radius, nearest first."""
    pad = radius_m / 110_000.0
    sql = [
        "SELECT kind, subtype, name, detail, status, size_hint, lat, lon,",
        "       min_lat, max_lat, min_lon, max_lon FROM amenity",
        "WHERE kind = ? AND lat IS NOT NULL AND lon IS NOT NULL",
    ]
    params: list = [kind]
    if to_edge:
        # A shape's centroid can sit outside the search box while its edge is
        # inside it, so the box is what gets filtered on.
        sql.append("AND max_lat >= ? AND min_lat <= ? AND max_lon >= ? AND min_lon <= ?")
        params += [lat - pad, lat + pad, lon - pad, lon + pad]
    else:
        sql.append("AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
        params += [lat - pad, lat + pad, lon - pad, lon + pad]
    if subtypes:
        sql.append(f"AND subtype IN ({', '.join('?' for _ in subtypes)})")
        params += list(subtypes)

    out: list[Place] = []
    for row in conn.execute(" ".join(sql), params):
        if to_edge:
            distance = distance_to_box_m(
                lat, lon, row["min_lat"], row["max_lat"],
                row["min_lon"], row["max_lon"],
            )
            if distance is None:
                distance = haversine_m(lat, lon, row["lat"], row["lon"])
        else:
            distance = haversine_m(lat, lon, row["lat"], row["lon"])
        if distance > radius_m:
            continue
        out.append(Place(
            kind=kind, name=row["name"], distance_m=distance,
            bearing=bearing_deg(lat, lon, row["lat"], row["lon"]),
            subtype=row["subtype"], detail=row["detail"], status=row["status"],
            size_hint=row["size_hint"], to_edge=to_edge,
        ))
    return sorted(out, key=lambda p: p.distance_m)


def _loaded_kinds(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in conn.execute(
            "SELECT DISTINCT kind FROM amenity WHERE lat IS NOT NULL"
        )
    }


# What to say when a layer has not been loaded, so an empty answer reads as
# "not loaded" rather than "there is nothing near this block".
LAYER_NAMES = {
    "mrt_exit": "MRT station exits",
    "bus_stop": "bus stops",
    "hawker_centre": "hawker centres",
    "park": "parks",
    "park_facility": "park facilities",
    "school": "schools",
    "preschool": "pre-schools",
}


def nearby_for(
    conn: sqlite3.Connection,
    block_key: str,
    lat: float | None = None,
    lon: float | None = None,
) -> Nearby:
    """The Nearby section for one block."""
    if lat is None or lon is None:
        row = conn.execute(
            "SELECT lat, lon FROM block WHERE block_key = ?", (block_key,)
        ).fetchone()
        if not row or row["lat"] is None:
            return Nearby(missing=[
                "this block has no geocode, so nothing can be measured from it"
            ])
        lat, lon = row["lat"], row["lon"]

    loaded = _loaded_kinds(conn)
    result = Nearby()
    result.missing = [
        f"{name} have not been loaded yet, so those rows are unknown rather than none"
        for kind, name in LAYER_NAMES.items() if kind not in loaded
    ]

    exits = _places(conn, lat, lon, "mrt_exit")
    if exits:
        result.mrt_exit = exits[0]
        result.mrt_station = exits[0].subtype

    stops = _places(conn, lat, lon, "bus_stop")
    if stops:
        result.bus_stop = stops[0]

    hawkers = _places(conn, lat, lon, "hawker_centre")
    open_hawkers = [
        h for h in hawkers
        if not (h.status or "").lower().startswith("under")
    ]
    building = [
        h for h in hawkers
        if (h.status or "").lower().startswith("under")
    ]
    if open_hawkers:
        result.hawker = open_hawkers[0]
    # Six of the 129 are not built yet, and one opening nearby is a real
    # change to a neighbourhood — but it is not somewhere you can eat today,
    # so it is a separate row rather than the answer to "nearest hawker".
    if building:
        result.hawker_opening = building[0]

    parks = _places(conn, lat, lon, "park", to_edge=True)
    if parks:
        result.park = parks[0]

    schools = _places(conn, lat, lon, "school", radius_m=SCHOOL_BAND_M * 2)
    primaries = [s for s in schools if (s.subtype or "") == "PRIMARY"]
    if primaries:
        result.primary_school = primaries[0]
    if "school" in loaded:
        result.schools_within_1km = sum(
            1 for s in primaries if s.distance_m <= SCHOOL_BAND_M
        )

    if "preschool" in loaded:
        result.preschools_within_walk = len(
            _places(conn, lat, lon, "preschool", radius_m=WALKING_M)
        )

    facilities = _places(
        conn, lat, lon, "park_facility", radius_m=WALKING_M,
        subtypes=WALKABLE_FACILITIES,
    )
    counts: dict[str, int] = {}
    nearest: dict[str, Place] = {}
    for place in facilities:
        key = (place.subtype or "OTHER").lower().replace(" ", "_")
        counts[key] = counts.get(key, 0) + 1
        nearest.setdefault(key, place)
    result.facilities = counts
    result.nearest_facility = nearest

    return result


# --- rainfall ------------------------------------------------------------
@dataclass
class Rain:
    """Where this part of the island sits for wetness.

    A rank and not a total. The readings are sampled rather than read whole —
    a year is over six million rows — so the millimetres are a rate across an
    unequal number of readings per station, and only the ordering between
    stations survives that. The PRD says as much: a relative ranking, not an
    annual total.
    """

    station: str | None = None
    station_id: str | None = None
    distance_m: float | None = None
    rank: int | None = None
    of: int | None = None
    wetter_than_pct: float | None = None
    mm_per_1000_readings: float | None = None
    year: int | None = None
    unknown: str | None = None

    def as_dict(self) -> dict:
        return {
            "station": self.station,
            "station_id": self.station_id,
            "distance_m": round(self.distance_m) if self.distance_m else None,
            "rank": self.rank,
            "of": self.of,
            "wetter_than_pct": self.wetter_than_pct,
            "mm_per_1000_readings": self.mm_per_1000_readings,
            "year": self.year,
            "unknown": self.unknown,
        }


def rain_for(
    conn: sqlite3.Connection,
    lat: float | None,
    lon: float | None,
    year: int | None = None,
) -> Rain:
    """The nearest gauge, and how it ranks against the rest of the island."""
    if lat is None or lon is None:
        return Rain(unknown="this block has no geocode, so no gauge can be found")

    if year is None:
        row = conn.execute("SELECT MAX(year) AS y FROM rainfall_station").fetchone()
        year = row["y"] if row else None
    if not year:
        return Rain(unknown=(
            "the rainfall sample has not been loaded yet — run "
            "`nhf sync --step rainfall`"
        ))

    stations = conn.execute(
        "SELECT station_id, name, lat, lon, total_mm, readings FROM rainfall_station"
        " WHERE year = ? AND readings > 0 AND lat IS NOT NULL",
        (year,),
    ).fetchall()
    if not stations:
        return Rain(year=year, unknown=f"no usable gauge readings for {year}")

    rates = sorted(
        ((s["total_mm"] / s["readings"], s["station_id"]) for s in stations),
        reverse=True,
    )
    order = {sid: i + 1 for i, (_rate, sid) in enumerate(rates)}

    best = min(
        stations,
        key=lambda s: haversine_m(lat, lon, s["lat"], s["lon"]),
    )
    distance = haversine_m(lat, lon, best["lat"], best["lon"])
    rank = order[best["station_id"]]
    rate = best["total_mm"] / best["readings"]
    return Rain(
        station=best["name"],
        station_id=best["station_id"],
        distance_m=distance,
        rank=rank,
        of=len(stations),
        # 1 is the wettest, so "wetter than" counts the gauges below it.
        wetter_than_pct=round(100.0 * (len(stations) - rank) / max(1, len(stations) - 1), 1),
        mm_per_1000_readings=round(rate * 1000, 2),
        year=year,
    )
