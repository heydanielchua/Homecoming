"""Small geo helpers. Distances only; routing is OneMap's job."""

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
