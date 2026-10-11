"""The phone app: the pages must render from the store, and must not pretend
to have data they do not have."""

import pytest

from nhf.web.app import create_app


@pytest.fixture()
def client(fitted_db):
    app = create_app(conn=fitted_db)
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def empty_client(db):
    db.execute("DELETE FROM resale_txn")
    db.commit()
    app = create_app(conn=db)
    app.config.update(TESTING=True)
    return app.test_client()


def test_ranked_list_renders_candidates(client):
    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Ranked blocks" in body
    assert "TAMPINES ST 42" in body.upper()
    assert "How this score was built" in body


def test_ranked_list_shows_flags_in_words(client):
    body = client.get("/").get_data(as_text=True)
    assert "Far north" in body or "over budget" in body or "years left after our" in body


def test_weight_sliders_change_the_page(client):
    cheap_first = client.get("/?w_price=100&w_growth=0&w_lease=0&w_commute=0&w_amenities=0")
    lease_first = client.get("/?w_price=0&w_growth=0&w_lease=100&w_commute=0&w_amenities=0")
    assert cheap_first.status_code == lease_first.status_code == 200
    assert cheap_first.get_data() != lease_first.get_data()


def test_hide_flagged_filter(client):
    everything = client.get("/").get_data(as_text=True)
    filtered = client.get("/?hide_flagged=1").get_data(as_text=True)
    assert everything != filtered


def test_empty_store_says_what_to_run_instead_of_showing_nothing(empty_client):
    body = empty_client.get("/").get_data(as_text=True)
    assert "Nothing to rank yet" in body
    assert "nhf sync" in body
    assert "nothing real to show" in body


def test_block_profile_has_the_prd_sections(client):
    page = client.get("/block/141|TAMPINES ST 42/4 ROOM")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for heading in ("Fair value", "Growth", "Criteria", "Prices here",
                    "Trend against its town", "MRT nearby", "Retirement rental check"):
        assert heading in body, heading
    assert "comparable sale" in body
    assert "Earliest we could sell" in body


def test_block_profile_values_a_specific_unit(client):
    page = client.get("/block/141|TAMPINES ST 42/4 ROOM?storey=12&sqm=93&asking=900000")
    body = page.get_data(as_text=True)
    assert "above" in body            # 900k must be tagged above the range
    assert "Asking $900,000" in body


def test_unknown_block_is_a_404(client):
    assert client.get("/block/999|NOWHERE ROAD/4 ROOM").status_code == 404


def test_map_and_api(client):
    assert client.get("/map").status_code == 200
    rows = client.get("/api/candidates.json").get_json()
    assert rows and all("lat" in r and "rank_score" in r for r in rows)
    assert all(r["url"].startswith("/block/") for r in rows)
    stations = client.get("/api/stations.json").get_json()
    assert any(s["status"] == "open" for s in stations)


def test_shortlist_round_trip(client):
    created = client.post("/listings/new", data={
        "url": "https://www.propertyguru.com.sg/listing/1",
        "block_key": "141|TAMPINES ST 42", "flat_type": "4 ROOM",
        "asking_price": "620000", "storey": "11", "floor_area_sqm": "93",
        "corner_unit": "1", "status": "Watching",
    }, follow_redirects=True)
    assert created.status_code == 200
    body = created.get_data(as_text=True)
    assert "$620,000" in body
    assert "Viewing checklist" in body
    assert "corner unit" in body            # the fit check reads it back

    listing_id = client.application.config["NHF_TEST_CONN"].execute(
        "SELECT id FROM listing ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]

    updated = client.post(f"/listings/{listing_id}", data={
        "block_key": "141|TAMPINES ST 42", "flat_type": "4 ROOM",
        "asking_price": "600000", "storey": "11", "status": "Viewed",
        "viewing_date": "2026-11-02", "notes": "Afternoon sun is brutal",
        "check_corner": "yes", "note_hardcourt": "court two blocks away",
    }, follow_redirects=True)
    assert updated.status_code == 200
    assert "Afternoon sun is brutal" in updated.get_data(as_text=True)

    listed = client.get("/listings").get_data(as_text=True)
    assert "Viewed" in listed

    removed = client.post(f"/listings/{listing_id}/delete", follow_redirects=True)
    assert "Listing removed" in removed.get_data(as_text=True)


def test_listing_shows_the_asking_price_against_fair_value(client):
    client.post("/listings/new", data={
        "block_key": "141|TAMPINES ST 42", "flat_type": "4 ROOM",
        "asking_price": "1200000", "storey": "5",
    }, follow_redirects=True)
    body = client.get("/listings").get_data(as_text=True)
    assert "above" in body


def test_data_page_reports_freshness_and_curves(client):
    body = client.get("/data").get_data(as_text=True)
    assert "Freshness" in body
    assert "Fitted curves" in body
    assert "Lease factor" in body
    assert "resale_txn" in body


def test_data_page_on_an_unsynced_store_says_so(empty_client):
    body = empty_client.get("/data").get_data(as_text=True)
    assert "No sync has run yet" in body


def test_pwa_files(client):
    manifest = client.get("/manifest.webmanifest").get_json()
    assert manifest["display"] == "standalone"
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_auth_refuses_non_loopback_when_no_accounts_exist(fitted_db, monkeypatch):
    monkeypatch.delenv("NHF_USERS", raising=False)
    app = create_app(conn=fitted_db)
    app.config.update(TESTING=True)
    page = app.test_client().get("/", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    assert page.status_code == 403
    assert b"NHF_USERS" in page.data


def test_login_is_required_once_accounts_exist(fitted_db, monkeypatch):
    from nhf.web.auth import hash_password

    monkeypatch.setenv("NHF_USERS", f"daniel:{hash_password('correct horse')}")
    app = create_app(conn=fitted_db)
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()

    assert client.get("/").status_code == 302
    assert client.post("/login", data={"name": "daniel", "password": "wrong"}
                       ).get_data(as_text=True).count("did not match") == 1
    client.post("/login", data={"name": "daniel", "password": "correct horse"})
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/").status_code == 302
