"""The static export: what it writes, and what it refuses to write.

The privacy checks here are the point of the file. The export exists so the
read-only app can go on a free host, and every free host that can serve it is
public, so a test that merely proves the files are well-formed would be
testing the easy half.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nhf.export import _public_rank, candidate_payload, export_site, slugify
from nhf.ranking import ranked_list

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- filenames ------------------------------------------------------------
def test_slug_is_safe_for_a_filename_and_a_url():
    slug = slugify("101|ANG MO KIO AVE 3", "4 ROOM")
    assert slug == "block-101-ang-mo-kio-ave-3-4-room"
    assert "|" not in slug and " " not in slug and "/" not in slug


def test_slugs_separate_flat_types_in_the_same_block():
    assert slugify("101|X ROAD", "3 ROOM") != slugify("101|X ROAD", "4 ROOM")


# --- the published score --------------------------------------------------
def test_public_rank_leaves_the_price_sub_score_out_and_rebases():
    subs = [
        {"key": "price", "weight": 25, "percentile": 1.0},
        {"key": "lease", "weight": 20, "percentile": 0.5},
        {"key": "growth", "weight": 25, "percentile": 1.0},
        {"key": "commute", "weight": 15, "percentile": None},
        {"key": "amenities", "weight": 15, "percentile": None},
    ]
    # Only lease and growth are available and publishable: (20*0.5 + 25*1) / 45.
    assert _public_rank(subs) == pytest.approx(100 * 35 / 45, abs=0.1)


def test_public_rank_is_none_when_nothing_publishable_is_available():
    assert _public_rank([
        {"key": "price", "weight": 25, "percentile": 1.0},
        {"key": "lease", "weight": 20, "percentile": None},
    ]) is None


def test_candidate_payload_withholds_the_price_percentile(fitted_db):
    candidates = ranked_list(fitted_db)
    assert candidates, "fixture produced no candidates"
    payload = candidate_payload(candidates[0], has_page=True)
    price = [s for s in payload["sub_scores"] if s["key"] == "price"]
    assert price, "the price sub-score should still be listed"
    assert "percentile" not in price[0], "a scored price encodes the budget"
    # The raw number is fine: it is a published HDB transaction either way.
    assert "median_price" in payload


def test_candidate_payload_keeps_the_fields_the_map_fetches(fitted_db):
    payload = candidate_payload(ranked_list(fitted_db)[0], has_page=True)
    for key in ("block_key", "block", "street", "town", "flat_type", "lat", "lon",
                "rank_score", "growth_score", "median_price", "drive_minutes",
                "lease_years_at_exit", "badge", "misses", "url"):
        assert key in payload, f"map.html reads {key}"


def test_candidate_without_a_page_is_not_linked(fitted_db):
    payload = candidate_payload(ranked_list(fitted_db)[0], has_page=False)
    assert payload["url"] is None and payload["has_page"] is False


# --- the whole export -----------------------------------------------------
@pytest.fixture()
def exported(fitted_db, tmp_path):
    report = export_site(fitted_db, tmp_path / "dist", limit=5, max_pages=5)
    return report, tmp_path / "dist"


def test_export_writes_the_files_a_static_host_needs(exported):
    report, out = exported
    for name in ("index.html", "map.html", "data.html", ".nojekyll",
                 "data/candidates.json", "data/scales.json", "data/fit.json",
                 "data/meta.json", "data/stations.json", "static/app.css",
                 "static/explore.js"):
        assert (out / name).exists(), f"{name} missing from the export"
    assert report.candidates > 0
    assert report.pages > 0


def test_export_writes_one_page_per_candidate(exported):
    report, out = exported
    rows = json.loads((out / "data/candidates.json").read_text())
    linked = [r for r in rows if r["has_page"]]
    for row in linked:
        assert (out / row["url"]).exists(), f"{row['url']} is linked but absent"
    assert report.pages == len(linked)


def test_export_has_no_absolute_links_left(exported):
    _, out = exported
    for page in out.glob("*.html"):
        text = page.read_text()
        for bad in ('href="/', 'src="/static/', "'/api/"):
            assert bad not in text, f"{page.name} still points at {bad!r}"


def test_export_drops_what_a_static_host_cannot_serve(exported):
    _, out = exported
    index = (out / "index.html").read_text()
    assert "Shortlist" not in index, "the shortlist needs a server to write to"
    assert "Sign out" not in index, "there is no session to sign out of"
    assert not list(out.glob("listing*.html"))
    assert not (out / "login.html").exists()


def test_scales_do_not_multiply_the_budget_out(exported):
    _, out = exported
    scales = json.loads((out / "data/scales.json").read_text())
    price = scales["rank_scales"]["price_budget_multiple"]
    # Multiples of a budget supplied by the browser, not dollar amounts.
    assert price["best"] < 2 and price["worst"] < 2


def test_meta_says_what_was_left_out(exported):
    _, out = exported
    meta = json.loads((out / "data/meta.json").read_text())
    assert meta["withheld"], "the export should state what it withholds"
    assert meta["built_at"]


# --- the publish guard ----------------------------------------------------
def test_guard_passes_a_clean_export(exported):
    from scripts.check_export import scan

    _, out = exported
    assert scan(out) == []


def test_guard_catches_the_budget_stated_as_a_budget(exported):
    from nhf.config import load_config
    from scripts.check_export import scan

    _, out = exported
    budget = int(load_config().budget)
    (out / "index.html").write_text(f"<p>Our budget is ${budget:,}</p>")
    assert any("budget" in p for p in scan(out))


def test_guard_catches_the_price_criterion_coming_back(exported):
    from scripts.check_export import scan

    _, out = exported
    # The wording criteria.py produces when a block is over budget. It never
    # names the budget, but price minus this recovers it.
    (out / "index.html").write_text("<li><b>Price</b> $35,000 over budget</li>")
    assert any("over budget" in p for p in scan(out))


def test_guard_allows_a_sale_price_that_happens_to_equal_the_budget(exported):
    from nhf.config import load_config
    from scripts.check_export import scan

    _, out = exported
    budget = int(load_config().budget)
    # A real comparable can land on a round number; the fixture export does.
    # Flagging that would make the guard noise, and noise gets ignored.
    (out / "index.html").write_text(
        f"<table><tr><td>Jun 2025</td><td>${budget:,}</td></tr></table>"
    )
    assert scan(out) == []


def test_guard_catches_a_leaked_office_postal(exported):
    from nhf.config import load_config
    from scripts.check_export import scan

    _, out = exported
    postal = load_config().offices[0].postal
    (out / "map.html").write_text(f"<p>{postal}</p>")
    assert any(postal in p for p in scan(out))


def test_guard_catches_a_scored_price_sub_score(exported):
    from scripts.check_export import scan

    _, out = exported
    rows = json.loads((out / "data/candidates.json").read_text())
    for sub in rows[0]["sub_scores"]:
        if sub["key"] == "price":
            sub["percentile"] = 0.42
    (out / "data/candidates.json").write_text(json.dumps(rows))
    assert any("price" in p for p in scan(out))


def test_guard_rejects_an_empty_directory(tmp_path):
    from scripts.check_export import scan

    empty = tmp_path / "nothing"
    empty.mkdir()
    assert scan(empty)


# --- the design system travels with the export ----------------------------
def test_export_ships_the_design_system_tokens(exported):
    """app.css @imports these. Published without them the site loads and is
    entirely unstyled, which no other test would notice."""
    _, out = exported
    for name in ("root.css", "day.css", "night.css"):
        assert (out / "static" / "sgds" / name).exists(), f"sgds/{name} missing"


def test_every_sgds_token_the_stylesheet_uses_is_defined(exported):
    """A token that resolves to nothing falls back to an empty value, so the
    rule is dropped and the page degrades quietly rather than failing."""
    import re

    _, out = exported
    defined = set()
    for name in ("root.css", "day.css", "night.css"):
        text = (out / "static" / "sgds" / name).read_text()
        defined |= set(re.findall(r"(--sgds-[a-z0-9-]+)\s*:", text))

    used = set(re.findall(
        r"var\((--sgds-[a-z0-9-]+)", (out / "static" / "app.css").read_text()
    ))
    assert used, "app.css should be using the design system"
    assert not (used - defined), f"undefined tokens: {sorted(used - defined)}"


def test_offline_shell_caches_what_the_stylesheet_imports(exported):
    """The service worker exists for the lift lobby; a shell that caches
    app.css without its imports comes back unstyled there."""
    import re

    _, out = exported
    css = (out / "static" / "app.css").read_text()
    imported = re.findall(r'@import\s+"([^"]+)"', css)
    shell = (out / "static" / "sw.js").read_text()
    for ref in imported:
        assert ref.split("/")[-1] in shell, f"{ref} is imported but not cached"


def test_no_stylesheet_imports_a_file_that_is_not_there(exported):
    """Vendoring by hand missed root.css's own @import of responsive.css: the
    tokens it defines silently never loaded, and nothing failed."""
    import re

    _, out = exported
    css_files = list((out / "static").rglob("*.css"))
    assert css_files, "no stylesheets were exported"
    for path in css_files:
        for ref in re.findall(r'@import\s+"([^"]+)"', path.read_text()):
            target = (path.parent / ref).resolve()
            assert target.exists(), f"{path.name} imports missing {ref}"
