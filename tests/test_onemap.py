"""OneMap: geocoding, routing, and the three ways a token can reach a request.

The last one matters most here — a cloud environment that injects the
Authorization header never lets the session read the secret, so "no token in
the environment" must not mean "do not try".
"""

import pytest

from nhf.sources.onemap import GeoPoint, NoTokenError, OneMapClient, OneMapError

SEARCH_HIT = {
    "found": 1,
    "results": [{
        "SEARCHVAL": "406 ANG MO KIO AVENUE 10", "LATITUDE": "1.3620",
        "LONGITUDE": "103.8540", "POSTAL": "560406",
        "ADDRESS": "406 ANG MO KIO AVENUE 10 SINGAPORE 560406",
    }],
}
ROUTE_HIT = {"route_summary": {"total_time": 1500, "total_distance": 14200}}


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status at {self.status_code}")

    def json(self):
        return self._payload


class Session:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status
        self.headers_seen = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.headers_seen.append(headers or {})
        return Resp(self.payload, self.status)

    def post(self, url, json=None, timeout=None):
        return Resp({"access_token": "minted-token"})


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for name in ("ONEMAP_TOKEN", "ONEMAP_EMAIL", "ONEMAP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def client(session):
    return OneMapClient(session=session, min_interval=0)


def test_geocode_needs_no_token():
    session = Session(SEARCH_HIT)
    point = client(session).geocode("560406")
    assert point == GeoPoint(1.3620, 103.8540, "560406",
                             "406 ANG MO KIO AVENUE 10 SINGAPORE 560406")
    assert "Authorization" not in session.headers_seen[0]


def test_geocode_returns_none_when_nothing_matches():
    assert client(Session({"found": 0, "results": []})).geocode("nowhere") is None


def test_routing_uses_an_explicit_token(monkeypatch):
    monkeypatch.setenv("ONEMAP_TOKEN", "explicit-token")
    session = Session(ROUTE_HIT)
    route = client(session).drive_route(GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81))
    assert route.minutes == 25.0 and route.distance_m == 14200
    assert session.headers_seen[0]["Authorization"] == "explicit-token"


def test_routing_mints_a_token_from_email_and_password(monkeypatch):
    monkeypatch.setenv("ONEMAP_EMAIL", "a@b.c")
    monkeypatch.setenv("ONEMAP_PASSWORD", "secret")
    session = Session(ROUTE_HIT)
    client(session).drive_route(GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81))
    assert session.headers_seen[0]["Authorization"] == "minted-token"


def test_routing_is_attempted_when_a_proxy_supplies_the_header():
    """No credential in the environment is not a reason to give up.

    The request goes out bare; whatever sits in front of the client adds the
    Authorization header, and OneMap answering 200 proves it did.
    """
    session = Session(ROUTE_HIT)
    route = client(session).drive_route(GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81))
    assert route.minutes == 25.0
    assert "Authorization" not in session.headers_seen[0]


def test_a_401_without_a_token_explains_both_causes():
    with pytest.raises(NoTokenError, match="Bearer"):
        client(Session({}, status=401)).drive_route(
            GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81)
        )


def test_a_401_with_a_token_reports_an_expired_token(monkeypatch):
    monkeypatch.setenv("ONEMAP_TOKEN", "stale")
    with pytest.raises(OneMapError, match="expired"):
        client(Session({}, status=401)).drive_route(
            GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81)
        )


def test_a_route_with_no_summary_is_an_error_not_a_zero():
    with pytest.raises(OneMapError, match="route_summary"):
        client(Session({"status": 1})).drive_route(
            GeoPoint(1.37, 103.85), GeoPoint(1.28, 103.81)
        )
