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


BLOCK_URL = "/block/141|TAMPINES ST 42/4 ROOM"


def test_served_block_page_states_where_the_block_ranks(client):
    """The rank is worth showing when the page is rendered on request."""
    html = client.get(BLOCK_URL).get_data(as_text=True)
    assert "on the current weights" in html


def test_published_block_page_states_no_rank_at_all(client):
    """A frozen page cannot: the rank is computed from the weights in force
    at build time, and the total it is out of counts the blocks inside our
    budget, which is a fact about the budget."""
    client.application.config["NHF_STATIC_EXPORT"] = True
    html = client.get(BLOCK_URL).get_data(as_text=True)
    assert "on the current weights" not in html
    assert "Ranked on the front page" in html


def test_publishing_a_block_page_does_not_change_the_block_numbers(client):
    """The published page loads the block alone instead of the whole list,
    which must not alter anything the page says about the block itself."""
    import re

    served = client.get(BLOCK_URL).get_data(as_text=True)
    client.application.config["NHF_STATIC_EXPORT"] = True
    published = client.get(BLOCK_URL).get_data(as_text=True)

    def body(html: str) -> str:
        main = re.search(r"<main[^>]*>(.*)</main>", html, re.DOTALL).group(1)
        # The rank line is the one part meant to differ, and the published
        # page drops the price criterion on purpose.
        main = re.sub(r'<p class="muted">\s*Ranked.*?</p>', "", main, flags=re.DOTALL)
        main = re.sub(r"<li[^>]*>\s*<b>Price</b>.*?</li>", "", main, flags=re.DOTALL)
        return "".join(main.split())

    assert body(served) and body(served) == body(published)


# --- accessibility ---------------------------------------------------------
def _css() -> str:
    from pathlib import Path
    import nhf.web.app as web
    return (Path(web.__file__).parent / "static" / "app.css").read_text()


def test_there_is_a_visible_focus_style():
    """There was none at all, and inputs and buttons are restyled enough that
    the browser default did an uneven job of standing in."""
    css = _css()
    assert ":focus-visible" in css
    assert "--sgds-outline-focus" in css, "use the design system's own ring"


def test_a_keyboard_user_can_skip_the_list(client):
    html = client.get("/").get_data(as_text=True)
    assert 'class="skip-link" href="#main"' in html
    assert 'id="main"' in html, "the skip link needs something to land on"


def test_exactly_one_tab_is_marked_as_the_current_page(client):
    for path in ("/", "/map", "/data"):
        html = client.get(path).get_data(as_text=True)
        assert html.count('aria-current="page"') == 1, f"{path} marks the wrong number"


def test_the_tab_glyphs_are_hidden_from_screen_readers(client):
    """They are decoration; read aloud they are just characters."""
    import re

    html = client.get("/").get_data(as_text=True)
    nav = re.search(r'<nav class="tabbar".*?</nav>', html, re.DOTALL).group(0)
    for glyph in ("▤", "◎", "↻"):
        assert f'<span aria-hidden="true">{glyph}</span>' in nav


def test_the_tab_bar_is_a_named_landmark(client):
    assert 'aria-label="Sections"' in client.get("/").get_data(as_text=True)


def test_the_disclosure_summary_keeps_an_affordance():
    """Laying summary out as a flex box drops the triangle in Chrome and
    Safari, leaving no sign that it opens."""
    css = _css()
    assert "summary::after" in css


def test_choosing_several_towns_uses_checkboxes_not_a_multi_select(client):
    """A multi-select needed precise taps on a phone and never said that more
    than one was allowed."""
    html = client.get("/").get_data(as_text=True)
    assert "multiple" not in html, "a multi-select is back"
    assert 'type="checkbox" name="town"' in html


def test_the_town_filter_still_reads_the_same_query_string(client):
    """The control changed; the contract it submits must not have."""
    import re

    html = client.get("/?town=SEMBAWANG&town=YISHUN").get_data(as_text=True)
    towns = set(re.findall(r'<p class="sub">[^<]*·\s*([A-Za-z ]+?)\s*(?:·|</p>)', html))
    assert towns, "no candidates came back at all"
    assert towns <= {"Sembawang", "Yishun"}, f"filter leaked: {towns}"
