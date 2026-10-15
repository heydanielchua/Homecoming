"""Small geo helpers: distances, and summarising a GeoJSON shape.

Routing is OneMap's job. Shapes are reduced to a centroid and a bounding
box rather than kept whole, which is what lets the app answer "what sits
near this block" without a geometry library or a spatial index.
"""

from __future__ import annotations

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing from the first point to the second, 0..360.

    Needed because "what blocks the view" and "where the afternoon sun comes
    from" are both questions about direction, and the sun's azimuth is
    measured the same way: clockwise from true north.

    Over a few hundred metres at the equator the spherical and the flat
    answer agree to a small fraction of a degree, but the spherical one is no
    harder to write.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    y = sin(dlon) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlon)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def compass_point(bearing: float) -> str:
    """The nearest of the eight compass points, for a bearing."""
    points = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return points[int((bearing % 360.0 + 22.5) // 45.0) % 8]


def nearest(
    lat: float,
    lon: float,
    points: list[dict],
    lat_key: str = "lat",
    lon_key: str = "lon",
) -> tuple[dict | None, float | None]:
    """Closest point and its distance in metres, by straight line."""
    best: dict | None = None
    best_d: float | None = None
    for point in points:
        plat, plon = point.get(lat_key), point.get(lon_key)
        if plat is None or plon is None:
            continue
        d = haversine_m(lat, lon, float(plat), float(plon))
        if best_d is None or d < best_d:
            best, best_d = point, d
    return best, best_d


def distance_to_box_m(
    lat: float,
    lon: float,
    min_lat: float | None,
    max_lat: float | None,
    min_lon: float | None,
    max_lon: float | None,
) -> float | None:
    """Distance to the nearest point of a bounding box, zero if inside.

    This is what makes a park's distance mean anything. Bukit Timah Nature
    Reserve's centroid is over a kilometre from blocks whose nearest fence is
    two hundred metres away, and the centroid distance would report those
    blocks as nowhere near a park.

    The box is an over-estimate of the shape, so this is a lower bound on the
    true distance to the boundary — which is the right direction to be wrong
    in for a park, and is why the figure is reported as worked out rather
    than as fact.
    """
    if None in (min_lat, max_lat, min_lon, max_lon):
        return None
    near_lat = min(max(lat, min_lat), max_lat)
    near_lon = min(max(lon, min_lon), max_lon)
    return haversine_m(lat, lon, near_lat, near_lon)


def _ring_centroid(ring: list) -> tuple[float, float, float]:
    """Area-weighted centroid of one ring, and its area.

    The plain mean of the vertices is pulled towards whichever edge was drawn
    in most detail, and these rings are very unevenly sampled — some edges
    carry thirty points and the opposite side two. The shoelace centroid does
    not care how finely an edge was traced.
    """
    cx = cy = a2 = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = x1 * y2 - x2 * y1
        a2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if a2 == 0:                       # degenerate: fall back to the mean
        n = len(ring) or 1
        return (sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n, 0.0)
    return (cx / (3 * a2), cy / (3 * a2), abs(a2) / 2)


# How far a simplified outline may stray from the real one, in metres.
#
# Five metres is about two pixels at the zoom this is drawn at, and it halves
# the point count: the Master Plan draws parcel edges to fourteen decimal
# places, which is a tenth of a millimetre, and a boundary is not surveyed to
# anything like that. Going to ten saves another six per cent and starts
# clipping the corners off small parcels.
OUTLINE_TOLERANCE_M = 5.0

# Rounding on what is published. Five places is a bit over a metre here, which
# is finer than the tolerance above, so it costs nothing and halves the bytes.
OUTLINE_DECIMALS = 5

# Metres per degree at Singapore's latitude. The island spans a third of a
# degree, so treating these as constant is good to a few centimetres.
_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON = 110320.0


def _segment_distance_m(point, start, end) -> float:
    """Metres from a point to the segment start-end, squared.

    Squared, because this is only ever compared against another squared
    distance and the square root is the expensive part of the loop.
    """
    ax = (start[0] - point[0]) * _M_PER_DEG_LON
    ay = (start[1] - point[1]) * _M_PER_DEG_LAT
    bx = (end[0] - point[0]) * _M_PER_DEG_LON
    by = (end[1] - point[1]) * _M_PER_DEG_LAT
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return ax * ax + ay * ay
    t = -(ax * dx + ay * dy) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx, cy = ax + t * dx, ay + t * dy
    return cx * cx + cy * cy


def simplify_ring(ring: list, tolerance_m: float = OUTLINE_TOLERANCE_M) -> list:
    """Douglas-Peucker, iterative so a long ring cannot blow the stack.

    A closed ring keeps its first and last point, which are the same point, so
    the result still closes.
    """
    if len(ring) < 4:
        return list(ring)
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    limit = tolerance_m * tolerance_m
    while stack:
        first, last = stack.pop()
        worst, at = 0.0, -1
        for i in range(first + 1, last):
            d = _segment_distance_m(ring[i], ring[first], ring[last])
            if d > worst:
                worst, at = d, i
        if worst > limit and at > 0:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))
    return [p for p, k in zip(ring, keep) if k]


def outline(
    geometry: dict,
    tolerance_m: float = OUTLINE_TOLERANCE_M,
    decimals: int = OUTLINE_DECIMALS,
) -> dict | None:
    """A GeoJSON polygon simplified enough to publish, or None.

    Holes are kept. A parcel wrapped around a reservoir is a real hole, and
    filling it in would draw land over water — a small lie, but on a map a
    reader checks against what they can see out of the window.

    A ring that simplifies below a triangle is dropped rather than drawn as a
    line; if that leaves a polygon with no outer ring, the whole parcel is
    dropped, because a parcel drawn wrong is worse than one not drawn.
    """
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates")
    if not coords:
        return None
    if kind == "Polygon":
        polygons = [coords]
    elif kind == "MultiPolygon":
        polygons = list(coords)
    else:
        return None

    out = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            # A small parcel is not a simplification problem, it is a scale
            # problem: five metres of tolerance erases an eight-metre plot
            # entirely. Rather than drop it, the tolerance is halved until the
            # shape survives or it reaches half a metre, which is finer than
            # the Master Plan's own edges mean anything at.
            simple: list = []
            tolerance = tolerance_m
            while tolerance >= 0.5:
                simple = simplify_ring(ring, tolerance)
                if len(simple) >= 4:
                    break
                tolerance /= 2.0
            if len(simple) < 4:
                continue
            rings.append([
                [round(float(x), decimals), round(float(y), decimals)]
                for x, y in simple
            ])
        if rings:
            out.append(rings)
    if not out:
        return None
    if len(out) == 1:
        return {"type": "Polygon", "coordinates": out[0]}
    return {"type": "MultiPolygon", "coordinates": out}


def geometry_summary(
    geometry: dict,
) -> tuple[float, float, float, float, float, float] | None:
    """(lat, lon, min_lat, max_lat, min_lon, max_lon) for a GeoJSON shape.

    Points, polygons and their multi- forms. The bounding box matters as much
    as the centre: a park's centroid can sit a kilometre from the block while
    its nearest edge is two hundred metres away, and the box is what keeps
    that from reading as far.
    """
    kind = (geometry or {}).get("type")
    coords = (geometry or {}).get("coordinates")
    if not coords:
        return None

    if kind == "Point":
        lon, lat = float(coords[0]), float(coords[1])
        return (lat, lon, lat, lat, lon, lon)
    if kind == "MultiPoint":
        rings = [list(coords)]
    elif kind == "Polygon":
        rings = [coords[0]]
    elif kind == "MultiPolygon":
        rings = [poly[0] for poly in coords if poly]
    else:
        return None

    total, wx, wy = 0.0, 0.0, 0.0
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    for ring in rings:
        if len(ring) < 3:
            # Too few points for an area, but the extent is still real.
            for lon, lat in ring:
                min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
                total += 1e-12
                wx += lon * 1e-12
                wy += lat * 1e-12
            continue
        x, y, area = _ring_centroid(ring)
        weight = area or 1e-12
        total += weight
        wx += x * weight
        wy += y * weight
        for lon, lat in ring:
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    if not total or min_lon == float("inf"):
        return None
    return (wy / total, wx / total, min_lat, max_lat, min_lon, max_lon)


def angular_span(
    lat: float, lon: float, geom: dict
) -> tuple[float, float] | None:
    """How much horizon a footprint covers, seen from a point.

    Returns (centre bearing, half-width in degrees), or None if the geometry
    has no usable ring.

    The report's older answer assumed every HDB block was a nominal sixty
    metres end on, because the property information dataset gives a point per
    block and no shape. Measured against the footprints that are now loaded,
    that assumption is out by 8.8 degrees on average and 24.2 at worst for the
    seventeen blocks around one flat in Boon Keng, and it errs in both
    directions — it over-reports a slab seen end on and under-reports one seen
    broadside. A blocked-horizon figure built from it cannot survive being
    drawn next to the real shapes.

    The method is the widest gap between the bearings of the outline's
    vertices: whatever that gap does not cover is what the building covers.
    For a MultiPolygon the gap between two separate wings counts as covered,
    which over-reports a little and is the conservative direction for a
    question about what is in the way.
    """
    kind = geom.get("type")
    if kind == "Polygon":
        polygons = [geom.get("coordinates") or []]
    elif kind == "MultiPolygon":
        polygons = list(geom.get("coordinates") or [])
    else:
        return None

    bearings: list[float] = []
    for rings in polygons:
        if not rings:
            continue
        for point in rings[0]:
            if len(point) < 2:
                continue
            bearings.append(bearing_deg(lat, lon, point[1], point[0]))
    if len(bearings) < 2:
        return None

    bearings.sort()
    widest, at = 0.0, 0
    for i, start in enumerate(bearings):
        end = bearings[(i + 1) % len(bearings)]
        gap = (end - start) % 360.0
        if gap > widest:
            widest, at = gap, i

    span = 360.0 - widest
    # Standing inside the footprint — your own block — covers everything.
    if span >= 359.0:
        return (bearings[0], 90.0)
    centre = (bearings[(at + 1) % len(bearings)] + span / 2.0) % 360.0
    return (centre, min(90.0, span / 2.0))
