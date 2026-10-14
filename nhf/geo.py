"""Small geo helpers: distances, and summarising a GeoJSON shape.

Routing is OneMap's job. Shapes are reduced to a centroid and a bounding
box rather than kept whole, which is what lets the app answer "what sits
near this block" without a geometry library or a spatial index.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


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
