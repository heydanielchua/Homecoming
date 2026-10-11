"""OneMap (Singapore Land Authority): geocoding and drive routing.

Address search is open; routing needs a token from a free account and the
token lasts three days. Set ONEMAP_TOKEN directly, or ONEMAP_EMAIL and
ONEMAP_PASSWORD to have the client mint one. Without a token the client
reports `no_token` and the commute column stays empty, so a missing token
never reads as a short drive.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

from ..config import load_config

log = logging.getLogger(__name__)


class OneMapError(RuntimeError):
    pass


class NoTokenError(OneMapError):
    """No OneMap credentials available, so routing cannot be attempted."""


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float
    postal: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class Route:
    minutes: float
    distance_m: float


class OneMapClient:
    def __init__(self, session: requests.Session | None = None, min_interval: float = 0.2) -> None:
        cfg = load_config()
        self.search_url: str = cfg.onemap("search_url")
        self.route_url: str = cfg.onemap("route_url")
        self.token_url: str = cfg.onemap("token_url")
        self.session = session or requests.Session()
        self.timeout = int(cfg.api("timeout_seconds"))
        self._token: str | None = os.environ.get("ONEMAP_TOKEN") or None
        self._min_interval = min_interval
        self._last_call = 0.0

    # -- plumbing ----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def token(self) -> str:
        """A OneMap token, minted from email/password if one is not set."""
        if self._token:
            return self._token
        email = os.environ.get("ONEMAP_EMAIL")
        password = os.environ.get("ONEMAP_PASSWORD")
        if not (email and password):
            raise NoTokenError(
                "set ONEMAP_TOKEN, or ONEMAP_EMAIL and ONEMAP_PASSWORD, to use routing"
            )
        self._throttle()
        resp = self.session.post(
            self.token_url, json={"email": email, "password": password}, timeout=self.timeout
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise OneMapError(f"no access_token in OneMap token response: {resp.text[:200]}")
        self._token = token
        return token

    def _get(self, url: str, params: dict[str, Any], auth: bool = False) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = self.token()
        self._throttle()
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code in (401, 403) and auth:
            raise OneMapError(f"OneMap rejected the token ({resp.status_code}); it may have expired")
        resp.raise_for_status()
        return resp.json()

    # -- public ------------------------------------------------------------
    def geocode(self, query: str) -> GeoPoint | None:
        """First match for an address or postal code, or None."""
        payload = self._get(
            self.search_url,
            {"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
        )
        results = payload.get("results") or []
        if not results:
            return None
        hit = results[0]
        try:
            return GeoPoint(
                lat=float(hit["LATITUDE"]),
                lon=float(hit["LONGITUDE"]),
                postal=(hit.get("POSTAL") or None),
                address=(hit.get("ADDRESS") or None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OneMapError(f"unexpected OneMap search result for {query!r}: {exc}") from exc

    def drive_route(self, start: GeoPoint, end: GeoPoint) -> Route:
        """Drive time and distance between two points."""
        payload = self._get(
            self.route_url,
            {
                "start": f"{start.lat},{start.lon}",
                "end": f"{end.lat},{end.lon}",
                "routeType": "drive",
            },
            auth=True,
        )
        summary = (payload.get("route_summary") or {})
        seconds = summary.get("total_time")
        metres = summary.get("total_distance")
        if seconds is None or metres is None:
            raise OneMapError(f"no route_summary in OneMap response: {str(payload)[:200]}")
        return Route(minutes=round(float(seconds) / 60.0, 1), distance_m=float(metres))
