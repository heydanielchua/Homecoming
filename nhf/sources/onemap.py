"""OneMap (Singapore Land Authority): geocoding and drive routing.

Nothing here needs a token. Address search, drive routing and the map tiles
all answer unauthenticated, which was worth discovering: the previous version
of this client raised `NoTokenError` before sending the routing request, so
every drive time in the app read as unknown for want of a credential the
service never asked for.

A token is still used when one is available — set `ONEMAP_TOKEN`, or
`ONEMAP_EMAIL` and `ONEMAP_PASSWORD` to have the client mint one (they last
three days) — because an account raises the rate limit. What changed is the
order: the request goes out either way, and `NoTokenError` is raised only if
OneMap actually answers 401 or 403 on a call made without one. So a missing
token can still show as unknown, but only when it is genuinely the cause.
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

    def token(self) -> str | None:
        """A OneMap token if one can be had, else None.

        Returning None rather than raising is the point: the caller sends the
        request without an Authorization header and lets OneMap decide, which
        it does by answering normally.
        """
        if self._token:
            return self._token
        email = os.environ.get("ONEMAP_EMAIL")
        password = os.environ.get("ONEMAP_PASSWORD")
        if not (email and password):
            return None
        self._throttle()
        try:
            resp = self.session.post(
                self.token_url, json={"email": email, "password": password},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token")
        except (requests.RequestException, ValueError) as exc:
            # Credentials that do not work are not fatal here, because the
            # endpoints answer without a token anyway.
            log.warning("OneMap token request failed, carrying on unauthenticated: %s", exc)
            return None
        if not token:
            log.warning("no access_token in the OneMap token response; carrying on without")
            return None
        self._token = token
        return token

    def _get(self, url: str, params: dict[str, Any], auth: bool = False) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        token = self.token() if auth else None
        if token:
            headers["Authorization"] = token
        self._throttle()
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code in (401, 403) and auth:
            if token:
                raise OneMapError(
                    f"OneMap rejected the token ({resp.status_code}); it may have expired"
                )
            raise NoTokenError(
                f"OneMap answered {resp.status_code} to an unauthenticated request to "
                f"{url}. It did not used to require a credential for this; set "
                "ONEMAP_TOKEN, or ONEMAP_EMAIL and ONEMAP_PASSWORD, if it now does."
            )
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
