"""Write the site's data: one small JSON file per block.

The architecture the PRD asks for, in one sentence: a monthly batch job fills
SQLite, SQLite is rendered to static JSON, and the browser looks up the block
that was pasted. Nothing here runs at request time, there is no server and no
runtime database, and that is what makes the site free to host.

The layout is shaped by what one page view needs, which is four small files:

    meta.json                  the national index, the fitted curves and the
                               sun — the same for every block in the country,
                               so written once
    index/<block>.json         every street HDB has a block of that number
                               on, with the spellings a portal might use
    blocks/<slug>.json         one block's report
    towns/<slug>.json          the town's own trend, shared by its blocks

Sharding the alias index by block number is the one decision worth
explaining. Resolution needs 26,501 alias spellings across 10,817 blocks, and
as a single file that is a 1.2 MB download before anything can be looked up.
But a pasted link always carries the block number, so the only aliases a
visit needs are the ones for that number — a few hundred bytes. `669C` is one
file, `266B` is another and holds both Punggol Way and Compassvale Bow, and a
block number nobody has is a 404, which is an honest answer rather than a
wrong one.

Nothing about us is written out, and there is nothing to withhold: this site
holds no budget, no offices and no shortlist. `verify` checks that anyway,
because the cost of being wrong is a published file.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import load_config
from .listing import aliases_for, build_index
from .report import ReportBuilder
from .sources.hdb import canon_flat_type

log = logging.getLogger(__name__)

# Pages hands an undecorated directory to Jekyll, which drops any file whose
# name starts with an underscore.
NOJEKYLL = ".nojekyll"

# How recently a block must have sold to get a report written for it. Blocks
# that have never sold are still indexed — a link to one should resolve and
# then say there are no sales, rather than fail to resolve.
REPORT_MONTHS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    """A flat, URL-safe name. Used for block and town file names."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def block_slug(block: str, street: str) -> str:
    """The file name for one block.

    This is also one of the block's aliases — HDB's own spelling of it — so
    the index and the file names agree by construction rather than by a
    second mapping that could drift.
    """
    return slugify(f"{block}-{street}")


@dataclass
class SiteReport:
    out_dir: str
    blocks_indexed: int = 0
    reports_written: int = 0
    towns_written: int = 0
    zoning_cells: int = 0
    zoning_parcels: int = 0
    building_cells: int = 0
    index_files: int = 0
    aliases: int = 0
    bytes_written: int = 0
    collisions: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "out_dir": self.out_dir,
            "blocks_indexed": self.blocks_indexed,
            "reports_written": self.reports_written,
            "towns_written": self.towns_written,
            "zoning_cells": self.zoning_cells,
            "zoning_parcels": self.zoning_parcels,
            "building_cells": self.building_cells,
            "index_files": self.index_files,
            "aliases": self.aliases,
            "bytes_written": self.bytes_written,
            "collisions": self.collisions,
            "warnings": self.warnings,
        }


def _write(path: Path, payload: Any) -> int:
    """One JSON file, compactly. Returns bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # No indentation and no spaces after separators: these files are read by
    # a browser, not by a person, and the whitespace is a quarter of the
    # bytes across nine thousand of them.
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def reportable_blocks(
    conn: sqlite3.Connection, months: int = REPORT_MONTHS
) -> list[str]:
    """Blocks with a sale recent enough to say anything about.

    The PRD's 9,222: blocks that have sold in the last two years. A report for
    a block whose last sale was in 2009 would be a page of unknowns, and the
    index says so instead.
    """
    latest = conn.execute("SELECT MAX(month) AS m FROM resale_txn").fetchone()
    if not latest or not latest["m"]:
        return []
    from .metrics import month_minus

    cutoff = month_minus(latest["m"], months)
    return [
        row["block_key"] for row in conn.execute(
            "SELECT DISTINCT block_key FROM resale_txn WHERE month >= ?"
            " ORDER BY block_key",
            (cutoff,),
        )
    ]


def all_blocks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every block worth indexing: HDB's own list plus anything that sold."""
    return conn.execute(
        """
        SELECT block_key, block, street_name, town FROM block
        UNION
        SELECT block_key, block, street_name,
               (SELECT town FROM resale_txn x WHERE x.block_key = t.block_key
                 AND x.town <> '' LIMIT 1)
        FROM resale_txn t
        WHERE block_key NOT IN (SELECT block_key FROM block)
        GROUP BY block_key
        """
    ).fetchall()


def index_payloads(
    conn: sqlite3.Connection, with_reports: set[str]
) -> Iterator[tuple[str, dict]]:
    """One payload per block number, holding every street it appears on.

    The aliases travel with the street rather than being recomputed in the
    browser, so there is one source of truth for how `JURONG WEST ST 64` and
    `JURONG WEST STREET 64` relate: this file.
    """
    grouped: dict[str, list[dict]] = {}
    for row in all_blocks(conn):
        block, street = row["block"], row["street_name"]
        if not block or not street:
            continue
        slug = block_slug(block, street)
        # Only the street part of each alias: the block number is the file
        # name, so repeating it on every entry is 26,501 wasted copies.
        prefix = f"{slugify(block)}-"
        aliases = sorted({
            alias[len(prefix):] for alias in aliases_for(block, street)
            if alias.startswith(prefix)
        })
        grouped.setdefault(slugify(block), []).append({
            "street": street,
            "slug": slug,
            "town": row["town"],
            "aliases": aliases,
            "report": row["block_key"] in with_reports,
        })

    for number, entries in sorted(grouped.items()):
        yield number, {
            "block": number.upper(),
            "streets": sorted(entries, key=lambda e: e["street"]),
        }


def freshness(conn: sqlite3.Connection) -> list[dict]:
    """The age of every dataset, from the sync log.

    Shown on the site, because a report built on a six-month-old download is
    a different claim from one built this morning, and the only way to know
    which is to say.
    """
    rows = conn.execute(
        """
        SELECT dataset_key, label, status, rows, finished_at, message
        FROM sync_log
        WHERE id IN (
            SELECT MAX(id) FROM sync_log GROUP BY dataset_key
        )
        ORDER BY dataset_key
        """
    ).fetchall()
    return [
        {
            "dataset": row["dataset_key"],
            "label": row["label"],
            "status": row["status"],
            "rows": row["rows"],
            "at": row["finished_at"],
            "message": row["message"],
        }
        for row in rows
    ]


# --- the zoning layer -----------------------------------------------------
#
# What the Master Plan allows on each parcel, drawn as the map's base layer.
# The text rows under Surroundings already answer "what could still be built"
# in words; this is the same answer as a picture, which is the form the
# question is actually asked in — nobody wonders about a parcel 212 m to the
# east, they wonder about the empty patch they can see.
#
# Shared, not per block. A parcel sits near dozens of blocks, and writing it
# into each of their reports would store the same polygon forty times and make
# a block file five times its size. Instead the island is cut into hundredth-
# of-a-degree cells — about 1.1 km, so a map view touches three or four — and
# a parcel is written into every cell its bounding box reaches.

ZONING_CELL = 100          # cells per degree, so 0.01 deg a side

# The groups the map draws, and what falls into each. Keeping this here rather
# than in the browser means the vocabulary is checked once, at build time,
# against the 33 values URA actually publishes; an unmapped one is a build
# warning rather than a parcel silently drawn as "other" for ever.
ZONE_GROUPS: dict[str, tuple[str, ...]] = {
    "residential": (
        "RESIDENTIAL",
        "RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY",
        "RESIDENTIAL / INSTITUTION",
    ),
    "commercial": (
        "COMMERCIAL", "COMMERCIAL & RESIDENTIAL", "COMMERCIAL / INSTITUTION",
        "HOTEL", "WHITE",
    ),
    "business": (
        "BUSINESS 1", "BUSINESS 2", "BUSINESS PARK",
        "BUSINESS 1 - WHITE", "BUSINESS 2 - WHITE", "BUSINESS PARK - WHITE",
    ),
    "park": ("PARK", "OPEN SPACE", "SPORTS & RECREATION", "BEACH AREA"),
    # The one a buyer should look hardest at: no use fixed yet, so it is the
    # parcel most likely to become something you did not expect.
    "reserve": ("RESERVE SITE", "SPECIAL USE"),
    "civic": (
        "EDUCATIONAL INSTITUTION", "PLACE OF WORSHIP",
        "CIVIC & COMMUNITY INSTITUTION", "HEALTH & MEDICAL CARE",
        "UTILITY", "TRANSPORT FACILITIES", "MASS RAPID TRANSIT",
        "LIGHT RAPID TRANSIT", "PORT / AIRPORT", "CEMETERY", "AGRICULTURE",
    ),
    # Ground, not a zoning decision. These were left out while the only
    # reader was the flat map, which has a basemap under it and certainly
    # has roads; drawing them a second time there buried everything else.
    # The three-dimensional view has no basemap, so without them a city of
    # towers stands on blank ground with no streets to place it against.
    # Written once, into the cells both views read; the flat map skips a
    # group it has no colour for, which is how it ignores these.
    "road": ("ROAD",),
    "water": ("WATERBODY",),
}

_ZONE_OF = {use: group for group, uses in ZONE_GROUPS.items() for use in uses}


def zone_group(land_use: str | None) -> str | None:
    """Which group a parcel is drawn in, or None if it has no land use at all.

    An unrecognised use falls into "civic" rather than disappearing, because
    a parcel drawn in the wrong colour is a question somebody asks, and one
    that is not drawn is a hole nobody notices.
    """
    use = (land_use or "").strip().upper()
    if not use:
        return None
    return _ZONE_OF.get(use, "civic")


def building_cells(conn: sqlite3.Connection) -> Iterator[tuple[str, dict]]:
    """Yield (cell key, payload) for the HDB buildings in each cell.

    Cut the same way as the zoning, and for the same reason: a building sits
    near dozens of blocks, so it is written once per cell rather than once per
    neighbour. Kept in its own file rather than added to the zoning cell,
    because the flat map never needs it and the 2D reader should not pay for
    a layer only the 3D view draws.

    Height is storeys times three metres. HDB publishes the storey count and
    nobody publishes the height, so this is right to about a metre and is not
    a survey — which is why the payload carries the storeys rather than a
    height in metres: the reader can see what it was derived from.
    """
    cells: dict[tuple[int, int], list] = {}
    for row in conn.execute(
        "SELECT block_key, block, street_name, postal, max_floor_lvl, outline "
        "FROM block WHERE outline IS NOT NULL"
    ):
        shape = json.loads(row["outline"])
        lats, lons = _ring_extent(shape)
        if not lats:
            continue
        building = {
            # The postal code is the footprint layer's own key and unique in
            # it, so it is what the browser draws a building once by.
            "i": row["postal"],
            "b": row["block"],
            "m": shape,
        }
        if row["max_floor_lvl"]:
            building["s"] = row["max_floor_lvl"]
        if row["block"] and row["street_name"]:
            building["slug"] = block_slug(row["block"], row["street_name"])
        for y in range(math.floor(min(lats) * ZONING_CELL),
                       math.floor(max(lats) * ZONING_CELL) + 1):
            for x in range(math.floor(min(lons) * ZONING_CELL),
                           math.floor(max(lons) * ZONING_CELL) + 1):
                cells.setdefault((y, x), []).append(building)

    for (y, x), buildings in sorted(cells.items()):
        yield f"{y}_{x}", {"cell": [y, x], "buildings": buildings}


def _ring_extent(shape: dict) -> tuple[list[float], list[float]]:
    """Every latitude and longitude in a GeoJSON polygon."""
    lats: list[float] = []
    lons: list[float] = []

    def walk(coords) -> None:
        if coords and isinstance(coords[0], (int, float)):
            lons.append(coords[0])
            lats.append(coords[1])
            return
        for part in coords or []:
            walk(part)

    walk(shape.get("coordinates"))
    return lats, lons


def zoning_cells(conn: sqlite3.Connection) -> Iterator[tuple[str, dict]]:
    """Yield (cell key, payload) for every cell with something in it."""
    cells: dict[tuple[int, int], list] = {}
    unmapped: set[str] = set()
    for row in conn.execute(
        "SELECT objectid, land_use, gpr_numeric, geom, min_lat, max_lat, min_lon, max_lon "
        "FROM land_parcel WHERE geom IS NOT NULL"
    ):
        group = zone_group(row["land_use"])
        if group is None:
            continue
        use = (row["land_use"] or "").strip().upper()
        if use not in _ZONE_OF:
            unmapped.add(use)
        parcel = {
            # URA's own id, because a parcel is written into every cell its
            # box reaches and the map must draw it once however many cells it
            # loads. Without this the overlaps stack their fill and a parcel
            # spanning four cells comes out four times as dark as its
            # neighbour, which reads as a different zone.
            "i": row["objectid"],
            "g": group,
            "u": titlecase_use(row["land_use"]),
            "m": json.loads(row["geom"]),
        }
        if row["gpr_numeric"] is not None:
            parcel["r"] = row["gpr_numeric"]
        # Into every cell the parcel's box reaches, so a polygon that straddles
        # a boundary is drawn whole from either side rather than cut in half.
        lat0 = math.floor(row["min_lat"] * ZONING_CELL)
        lat1 = math.floor(row["max_lat"] * ZONING_CELL)
        lon0 = math.floor(row["min_lon"] * ZONING_CELL)
        lon1 = math.floor(row["max_lon"] * ZONING_CELL)
        for y in range(lat0, lat1 + 1):
            for x in range(lon0, lon1 + 1):
                cells.setdefault((y, x), []).append(parcel)

    if unmapped:
        log.warning(
            "land use values not in ZONE_GROUPS, drawn as civic: %s",
            ", ".join(sorted(unmapped)),
        )
    for (y, x), parcels in sorted(cells.items()):
        yield f"{y}_{x}", {"cell": [y, x], "parcels": parcels}


def titlecase_use(land_use: str | None) -> str:
    """URA writes in capitals; the map says it in a tooltip."""
    text = (land_use or "").strip()
    if not text:
        return "Unzoned"
    small = {"and", "at", "of", "with", "st"}
    words = []
    for i, word in enumerate(text.lower().split()):
        if word in {"&", "/"}:
            words.append(word)
        elif word in small and i:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def build_site(
    conn: sqlite3.Connection,
    out_dir: str | Path = "dist/data",
    months: int = REPORT_MONTHS,
    limit: int | None = None,
    clean: bool = True,
) -> SiteReport:
    """Render the whole data directory. This is `nhf site`."""
    out = Path(out_dir)
    report = SiteReport(out_dir=str(out))

    if clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / NOJEKYLL).write_text("")

    builder = ReportBuilder(conn)

    keys = reportable_blocks(conn, months)
    if limit:
        keys = keys[:limit]
    # Filled in as reports are written, not before. The index says which
    # blocks have a report, and a block whose report failed to build must not
    # be advertised as having one — that would be a link to a 404.
    written: set[str] = set()

    # -- the shared files -------------------------------------------------
    report.bytes_written += _write(out / "meta.json", builder.meta_payload())

    # -- one report per block ---------------------------------------------
    towns: set[str] = set()
    for key in keys:
        payload = builder.build(key)
        if payload is None:
            report.warnings.append(f"{key}: no report could be built")
            continue
        slug = block_slug(payload["block"], payload["street"])
        payload["slug"] = slug
        payload["town_slug"] = slugify(payload["town"]) if payload["town"] else None
        report.bytes_written += _write(out / "blocks" / f"{slug}.json", payload)
        report.reports_written += 1
        written.add(key)
        if payload["town"]:
            towns.add(payload["town"])
        if report.reports_written % 500 == 0:
            log.info("%s reports written", report.reports_written)

    # -- one file per town ------------------------------------------------
    for town in sorted(towns):
        payload = builder.town_payload(town)
        payload["slug"] = slugify(town)
        report.bytes_written += _write(
            out / "towns" / f"{slugify(town)}.json", payload)
        report.towns_written += 1

    # -- the zoning layer -------------------------------------------------
    for key, payload in zoning_cells(conn):
        report.bytes_written += _write(out / "zoning" / f"{key}.json", payload)
        report.zoning_cells += 1
        report.zoning_parcels += len(payload["parcels"])
    log.info("%s zoning cells written", report.zoning_cells)
    if report.zoning_cells == 0:
        # Land use is fetched by name only — 174 MB of a plan revised every
        # few years has no business in a monthly run — so a fresh store builds
        # a site whose map has no zoning under it. Silence there looks like
        # land that is not zoned, which is the wrong conclusion entirely.
        report.warnings.append(
            "no zoning was written, so the map will have no Master Plan under "
            "it: run `nhf sync --step land_use` to fetch the parcels"
        )

    # -- the buildings ----------------------------------------------------
    for key, payload in building_cells(conn):
        report.bytes_written += _write(out / "buildings" / f"{key}.json", payload)
        report.building_cells += 1
    log.info("%s building cells written", report.building_cells)

    # -- the sharded alias index ------------------------------------------
    for number, payload in index_payloads(conn, written):
        report.bytes_written += _write(out / "index" / f"{number}.json", payload)
        report.index_files += 1
        report.blocks_indexed += len(payload["streets"])
        report.aliases += sum(len(s["aliases"]) for s in payload["streets"])

    # An alias two blocks both claim would resolve to whichever was written
    # first, so it is surfaced as a build warning rather than discovered as a
    # wrong report. The real data has none; a new dataset could introduce one.
    collisions = build_index(conn).collisions
    report.collisions = {k: sorted(set(v)) for k, v in collisions.items()}
    if collisions:
        report.warnings.append(
            f"{len(collisions)} alias(es) are claimed by more than one block "
            "and will not resolve; see collisions"
        )

    # -- the manifest -----------------------------------------------------
    flat_types = sorted({
        canon_flat_type(row["flat_type"]) for row in conn.execute(
            "SELECT DISTINCT flat_type FROM resale_txn WHERE flat_type <> ''")
    })
    report.bytes_written += _write(out / "manifest.json", {
        "built_at": _now(),
        "as_of": {
            "month": builder.latest_month,
            "quarter": builder.latest_quarter,
        },
        "counts": {
            "blocks_indexed": report.blocks_indexed,
            "reports": report.reports_written,
            "towns": report.towns_written,
            "index_files": report.index_files,
            "aliases": report.aliases,
            "zoning_cells": report.zoning_cells,
            "zoning_parcels": report.zoning_parcels,
            "building_cells": report.building_cells,
        },
        "report_window_months": months,
        "towns": sorted(towns),
        "flat_types": flat_types,
        "facings": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        "datasets": freshness(conn),
        "collisions": report.collisions,
        "sources": (
            "HDB resale prices and property information, HDB resale price "
            "index, HDB rentals, URA Master Plan land use, NEA hawker centres "
            "and rainfall, NParks parks and facilities, LTA MRT exits and bus "
            "stops, MOE schools, ECDA pre-schools, OneMap. All from "
            "data.gov.sg under the Singapore Open Data Licence."
        ),
    })
    return report


# --- the check that nothing of ours got published ------------------------
# The old ranked-list export had a real problem here. It published candidates
# scored against a budget without publishing the budget, and a published
# percentile gives a budget away by subtraction, so its check had to hunt for
# values that could be inverted.
#
# This site does not have that problem. It holds no budget, no offices, no
# shortlist and no score, so there is nothing to withhold and nothing to
# invert. What is worth checking is therefore structural: that no field from
# the superseded product has crept back in, and that every published key is
# one this module meant to write.
#
# Note what is deliberately *not* checked: whether the budget's digits appear
# anywhere in the output. They do, and they should — a flat in the fixture
# sells for exactly $650,000, and a resale price is a published government
# fact rather than a statement about what we can afford. A check that grepped
# for them would fail on every honest build, which is the kind of check that
# gets switched off and then misses something real.
FORBIDDEN_FIELDS = (
    # The ranked-list product's own vocabulary. If any of this reappears, a
    # module that was superseded is being published again.
    "growth_score", "signals_json", "shortlist", "sub_scores",
    "exit_low", "exit_mid", "exit_high", "percentile", "weights",
    # Anything about us.
    "budget", "budget_multiple", "office_code", "offices",
    "drive_minutes", "commute", "viewing_date", "eip_checked",
)

# Two words that look like they belong above and do not. `rank` is the
# rainfall ranking — which gauge of the 89 is the wettest — and has nothing to
# do with ranking blocks against each other. `notes` is the valuer's own list
# of what it had to do to reach a number, such as widening the comparables,
# and it is one of the more useful things on the page. Adding either to the
# list above fails every honest build, which is how a check gets switched off.
NOT_FORBIDDEN = ("rank", "notes")

BLOCK_KEYS = {
    "block_key", "block", "street", "town", "lat", "lon", "postal",
    "year_completed", "dwelling_units", "lease", "flat_types",
    "surroundings", "nearby", "rain", "as_of", "slug", "town_slug",
}


def _field_names(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key)
            yield from _field_names(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _field_names(item)


def verify(out_dir: str | Path) -> list[str]:
    """Check the rendered output is the site and nothing else."""
    out = Path(out_dir)
    problems: list[str] = []

    if not (out / "meta.json").exists():
        return [f"{out}/meta.json is missing: the site was not built"]
    if not any((out / "index").glob("*.json")):
        problems.append("no alias index was written, so no link can be resolved")
    blocks = sorted((out / "blocks").glob("*.json")) if (out / "blocks").exists() else []
    if not blocks:
        problems.append("no block reports were written")

    cfg = load_config()
    try:
        postals = {
            str(office.get("postal"))
            for office in (cfg.household.get("offices") or [])
            if office.get("postal")
        }
    except (KeyError, TypeError):
        postals = set()

    # Counted rather than listed: a field that crept back in is in all nine
    # thousand files, and nine thousand identical lines would bury the rest.
    offenders: dict[str, list[str]] = {}
    for path in out.rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        names = set(_field_names(payload))
        for banned in FORBIDDEN_FIELDS:
            if banned in names:
                offenders.setdefault(banned, []).append(str(path.relative_to(out)))
        # An office's postal code would only be here as a routing
        # destination, which this site does not compute. A block's own postal
        # code is a published address, so only the office ones are looked for.
        if postals and path.parent.name == "blocks":
            if str(payload.get("postal")) in postals:
                problems.append(
                    f"{path.relative_to(out)} is at an office postal code; that "
                    "is almost certainly a mix-up rather than a coincidence"
                )

    for banned, paths in sorted(offenders.items()):
        problems.append(
            f"a field named {banned!r} appears in {len(paths)} file(s) "
            f"(first: {paths[0]}); it belongs to the superseded ranked-list "
            "product or to us"
        )

    unexpected: dict[str, int] = {}
    for path in blocks:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in set(payload) - BLOCK_KEYS:
            unexpected[key] = unexpected.get(key, 0) + 1
    for key, count in sorted(unexpected.items()):
        problems.append(
            f"block reports carry an unexpected top-level key {key!r} "
            f"({count} file(s)). Add it to BLOCK_KEYS if it belongs"
        )
    return problems
