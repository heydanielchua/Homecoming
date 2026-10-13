"""Publish a read-only copy of the app as static files.

GitHub Pages serves files; it cannot run Python. That is survivable here for
one reason: nothing in the app calls a government API while you are looking
at it. `nhf sync` and `nhf build` do all the work in advance and the web
layer only reads what they cached, so the read-only half of the app can be
rendered once and served as plain files.

What that costs, stated plainly because it cannot be undone by a later
commit:

* A free Pages site is public. There is no login, and no password put in
  front of a static file is anything but decoration.
* Nothing can be written back, so the hand-entered shortlist (F7) is not
  exported at all. Run `nhf serve` at home for that.

So the export carries the part that is derived from public government data
and nothing that is ours. The budget never leaves `config/criteria.yml`:
every candidate is exported with its raw numbers — a median price, years of
lease, metres to a station — and the browser scores them, against a budget
typed in on the phone and kept in that phone's local storage. The price
sub-score is therefore absent from the published data rather than present
and reversible, which is what a published `price_budget_multiple` percentile
would be. The offices are left out on the same grounds: a drive time to a
postal code we did not publish is still a statement about where we work.

The remaining four sub-scores are scored on the fixed scales in
`config/criteria.yml` — 80 years of lease, 50 minutes, 1000 m — which are
thresholds rather than anything personal, so they ship with the data and the
sliders keep working.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import load_config
from .ranking import load_candidates, attach_fit, rank

log = logging.getLogger(__name__)

# Scored in the browser from the raw values instead, so the budget that
# defines the price scale is never written into the published data.
WITHHELD_SUB_SCORES = ("price",)

# Pages would otherwise hand the whole directory to Jekyll, which quietly
# drops any file or folder whose name begins with an underscore.
NOJEKYLL = ".nojekyll"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(block_key: str, flat_type: str) -> str:
    """A flat, URL-safe filename for one block-and-flat-type page.

    Block keys carry a pipe and spaces (`101|ANG MO KIO AVE 3`), which are
    legal in a URL but miserable in a filename and worse in a git diff. Every
    page is written at the top level so that one relative link shape works
    from every page, with no `../` arithmetic to get wrong.
    """
    raw = f"{block_key}-{flat_type}".lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return f"block-{cleaned}"


@dataclass
class ExportReport:
    out_dir: str
    candidates: int = 0
    pages: int = 0
    skipped_pages: int = 0
    bytes_written: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


def _public_rank(sub_scores: list[dict]) -> float | None:
    """Re-score a candidate over the sub-scores that are safe to publish.

    This is the app's own rule for a signal with no data, applied to a signal
    deliberately withheld: the weights are re-based on what is available, so
    dropping the price sub-score lowers nothing except the confidence the
    page claims.
    """
    weight_used = 0.0
    total = 0.0
    for sub in sub_scores:
        if sub["key"] in WITHHELD_SUB_SCORES or sub.get("percentile") is None:
            continue
        weight_used += float(sub["weight"])
        total += float(sub["weight"]) * float(sub["percentile"])
    return round(100.0 * total / weight_used, 1) if weight_used else None


def candidate_payload(candidate, has_page: bool) -> dict[str, Any]:
    """One candidate, as the phone will read it.

    The field names the map already fetches are kept as they are, so
    `map.html` keeps working against a file instead of the API.
    """
    data = candidate.to_dict()
    subs = [
        {k: v for k, v in sub.items() if k != "percentile"}
        if sub["key"] in WITHHELD_SUB_SCORES else dict(sub)
        for sub in data["sub_scores"]
    ]
    fit = data.get("fit") or {}
    # The price criterion states the budget outright ("within $600,000") or
    # hands over the subtraction that recovers it ("$35,000 over budget"), so
    # it is dropped rather than reworded.
    misses = [
        check for check in fit.get("checks", [])
        if check.get("level") == "flagged" and check.get("key") not in WITHHELD_SUB_SCORES
    ]
    return {
        # what map.html reads
        "block_key": candidate.block_key,
        "block": candidate.block,
        "street": candidate.street_name,
        "town": candidate.town,
        "flat_type": candidate.flat_type,
        "lat": candidate.lat,
        "lon": candidate.lon,
        "rank_score": _public_rank(data["sub_scores"]),
        "growth_score": candidate.growth_score,
        "median_price": candidate.median_price_24m,
        "drive_minutes": candidate.drive_minutes,
        "lease_years_at_exit": candidate.lease_years_at_exit,
        "badge": fit.get("badge"),
        "misses": [m.get("detail") for m in misses],
        "url": f"{slugify(candidate.block_key, candidate.flat_type)}.html" if has_page else None,
        # what the browser needs to score and explain
        "has_page": has_page,
        "median_sqm": candidate.median_sqm,
        "median_psm": candidate.median_psm_24m,
        "lease_years_now": candidate.lease_years_now,
        "nearest_mrt": candidate.nearest_mrt,
        "nearest_mrt_m": candidate.nearest_mrt_m,
        "n_txn_24m": candidate.n_txn_24m,
        "last_txn_month": candidate.last_txn_month,
        "max_floor_lvl": candidate.max_floor_lvl,
        "commute_status": candidate.commute_status,
        "sub_scores": subs,
        "flags": [
            {"label": m.get("label"), "detail": m.get("detail")} for m in misses
        ],
    }


def _scales_payload(cfg) -> dict[str, Any]:
    """The fixed scales, minus the one that is defined against our budget."""
    scales = {
        key: dict(value)
        for key, value in cfg.criteria["rank_scales"].items()
        if key != "price_budget_multiple"
    }
    scales["price_budget_multiple"] = dict(
        cfg.criteria["rank_scales"]["price_budget_multiple"]
    )
    # Kept as multiples of a budget the browser supplies, never multiplied
    # out here.
    return scales


def _fit_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    def table(name: str, band: str) -> list[dict]:
        try:
            rows = conn.execute(
                f"SELECT {band} AS band, flat_type, factor, n FROM {name}"
                " ORDER BY flat_type, band"
            ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(r) for r in rows]

    return {
        "lease_factor": table("lease_factor", "lease_band"),
        "storey_premium": table("storey_premium", "storey_band"),
        "caveat": (
            "The lease curve is a floor on observed decay, not the whole of it: "
            "HDB towns were built in waves, so lease length is partly collinear "
            "with town, and dividing each sale by its town-and-quarter cohort "
            "removes some of the lease signal with the location signal."
        ),
    }


def _meta_payload(conn: sqlite3.Connection, cfg, report: ExportReport) -> dict[str, Any]:
    from .web.app import freshness

    try:
        fresh = [dict(f) for f in freshness(conn)]
    except Exception as exc:                       # a store without a sync log
        fresh = []
        report.warnings.append(f"could not read data freshness: {exc}")

    counts = {}
    for table in ("resale_txn", "block", "block_metric", "block_score", "mrt_station"):
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = None

    return {
        "built_at": _now(),
        "holding_years": cfg.holding_years,
        "purchase_year": cfg.household["purchase_year"],
        "counts": counts,
        "freshness": fresh,
        "withheld": [
            "the budget and the price sub-score derived from it",
            "both office postal codes and the drive times routed to them",
            "the hand-entered shortlist and its photos",
        ],
        "note": (
            "A read-only publication of public-data results. Nothing here is "
            "written back, and a missing number reads as unknown rather than "
            "being filled in."
        ),
    }


# --- freezing -------------------------------------------------------------
def _rewrite_links(
    html: str,
    block_urls: dict[str, str],
    dead_urls: dict[str, str] | None = None,
) -> str:
    """Point a rendered page at its neighbours on disk.

    Every page is written at the top level, so each absolute path becomes a
    bare filename and no page needs to know how deep it sits.

    A block page links to the other flat types sold in the same block, and
    under `--max-pages` or `--limit` some of those pages are not published.
    Those anchors become plain spans: a link to a 404 is worse than visibly
    not being a link, and the text itself is still worth showing.
    """
    for url, filename in block_urls.items():
        html = html.replace(f'href="{url}"', f'href="{filename}"')

    # Anything still pointing at a block is a page that was not published:
    # a flat type outside a capped run, or one whose block has no recent sale
    # and so was never a candidate. Enumerating those is fragile, so whatever
    # the live map did not rewrite is unlinked here by pattern.
    html = re.sub(
        r'<a\b([^>]*?)href="/block/[^"]*"([^>]*?)>(.*?)</a>',
        lambda m: f"<span{m.group(1)}{m.group(2)}>{m.group(3)}</span>",
        html,
        flags=re.DOTALL,
    )

    replacements = [
        ('href="/static/', 'href="static/'),
        ('src="/static/', 'src="static/'),
        ('href="/manifest.webmanifest"', 'href="manifest.webmanifest"'),
        ("'/api/candidates.json'", "'data/candidates.json'"),
        ("'/api/stations.json'", "'data/stations.json'"),
        ('href="/map"', 'href="map.html"'),
        ('href="/data"', 'href="data.html"'),
        ('href="/"', 'href="index.html"'),
    ]
    for old, new in replacements:
        html = html.replace(old, new)
    return html


def export_site(
    conn: sqlite3.Connection,
    out_dir: str | Path,
    freeze_blocks: bool = True,
    max_pages: int | None = None,
    limit: int | None = None,
) -> ExportReport:
    """Render the read-only app into `out_dir` as plain files."""
    from .web.app import create_app

    cfg = load_config()
    out = Path(out_dir)
    report = ExportReport(out_dir=str(out))

    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)

    # The published set is every block-and-flat-type with a recent sale. The
    # household's budget and minimum flat size are filters the phone applies,
    # not facts about the data, so they are not applied here.
    candidates = load_candidates(conn, max_price=float(10**12), min_rooms=0)
    attach_fit(conn, candidates)
    rank(candidates, cfg.weights)
    if limit:
        candidates = candidates[:limit]
    report.candidates = len(candidates)

    pageable = candidates
    if freeze_blocks and max_pages is not None and len(candidates) > max_pages:
        # Ranked best-first already, so this keeps the pages most likely to be
        # opened and tells the phone not to link the rest.
        pageable = candidates[:max_pages]
        report.skipped_pages = len(candidates) - max_pages
        report.warnings.append(
            f"{report.skipped_pages} candidate(s) over --max-pages have no block "
            "page; they still appear in the list, without a link"
        )
    with_page = {(c.block_key, c.flat_type) for c in pageable} if freeze_blocks else set()

    payload = [
        candidate_payload(c, has_page=(c.block_key, c.flat_type) in with_page)
        for c in candidates
    ]

    app = create_app(conn=conn)
    app.config["NHF_STATIC_EXPORT"] = True

    def write(name: str, text: str) -> None:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        report.bytes_written += len(text.encode("utf-8"))

    write("data/candidates.json", json.dumps(payload, separators=(",", ":")))
    write("data/scales.json", json.dumps({
        "rank_scales": _scales_payload(cfg),
        "weights": dict(cfg.weights),
        "amenities_mrt_walk_m": cfg.growth["amenities_mrt_walk_m"],
    }, indent=1))
    write("data/fit.json", json.dumps(_fit_payload(conn), indent=1))
    write("data/meta.json", json.dumps(_meta_payload(conn, cfg, report), indent=1))
    write(NOJEKYLL, "")

    stations = [
        dict(r) for r in conn.execute(
            "SELECT name, line, status, expected_year, lat, lon FROM mrt_station"
            " WHERE lat IS NOT NULL"
        )
    ]
    write("data/stations.json", json.dumps(stations, separators=(",", ":")))

    # Static assets, copied rather than rendered.
    assets = Path(app.root_path) / "static"
    target = out / "static"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(assets, target)
    report.bytes_written += sum(
        p.stat().st_size for p in target.rglob("*") if p.is_file()
    )

    # Every block URL as Flask would write it, mapped to its file on disk, so
    # the rewrite is an exact match rather than a guess at a pattern. A block
    # page links to the other flat types in its block, which may sit outside
    # the published set, so those are tracked separately and unlinked.
    def block_url(c) -> str:
        return f"/block/{quote(c.block_key, safe='')}/{quote(c.flat_type, safe='')}"

    block_urls = {
        block_url(c): f"{slugify(c.block_key, c.flat_type)}.html" for c in pageable
    }
    dead_urls = {
        block_url(c): c.block_key for c in candidates
        if (c.block_key, c.flat_type) not in with_page
    }

    client = app.test_client()

    def freeze(path: str, filename: str) -> bool:
        resp = client.get(path)
        if resp.status_code != 200:
            report.warnings.append(f"{path} returned {resp.status_code}; not published")
            return False
        write(filename, _rewrite_links(
            resp.get_data(as_text=True), block_urls, dead_urls))
        return True

    for name, path in (("map.html", "/map"), ("data.html", "/data"),
                       ("manifest.webmanifest", "/manifest.webmanifest")):
        freeze(path, name)

    if freeze_blocks:
        for i, c in enumerate(pageable, 1):
            url = f"/block/{quote(c.block_key, safe='')}/{quote(c.flat_type, safe='')}"
            if freeze(url, f"{slugify(c.block_key, c.flat_type)}.html"):
                report.pages += 1
            if i % 250 == 0:
                log.info("frozen %s/%s block pages", i, len(pageable))

    # The front page is the one view that cannot be frozen: its sliders
    # re-rank the whole list, which the server does per request today.
    with app.app_context(), app.test_request_context("/"):
        from flask import render_template
        write("index.html", _rewrite_links(
            render_template("static_index.html", total=len(candidates)),
            block_urls, dead_urls,
        ))

    return report
