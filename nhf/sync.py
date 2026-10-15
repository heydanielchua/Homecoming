"""The monthly batch job.

Everything the app shows comes from this: four resale files, block property
information, the price index, town medians and rentals go into SQLite, then
blocks are geocoded and drive times to both offices are routed. Each step
writes a sync_log row, so the app can show how old every dataset is (F8) and
a failed sync is visible rather than silent.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .config import CACHE_DIR, TODO, Config, load_config
from .db import upsert_many
from .norm import clean
from .sources import hdb
from .sources.datagov import DataGovClient, DataGovError
from .sources.onemap import NoTokenError, OneMapClient, OneMapError

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SyncReporter:
    """Writes one sync_log row per dataset step."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.results: list[dict[str, Any]] = []

    def record(
        self,
        dataset_key: str,
        status: str,
        rows: int = 0,
        resource_id: str | None = None,
        label: str | None = None,
        started_at: str | None = None,
        message: str | None = None,
    ) -> None:
        entry = {
            "dataset_key": dataset_key,
            "resource_id": resource_id,
            "label": label,
            "status": status,
            "rows": rows,
            "started_at": started_at or _now(),
            "finished_at": _now(),
            "message": message,
        }
        self.conn.execute(
            "INSERT INTO sync_log (dataset_key, resource_id, label, status, rows,"
            " started_at, finished_at, message) VALUES (:dataset_key, :resource_id,"
            " :label, :status, :rows, :started_at, :finished_at, :message)",
            entry,
        )
        self.conn.commit()
        self.results.append(entry)
        level = logging.INFO if status == "ok" else logging.WARNING
        log.log(level, "%s: %s (%s rows) %s", dataset_key, status, rows, message or "")


def _load_resource(
    conn: sqlite3.Connection,
    client: DataGovClient,
    reporter: SyncReporter,
    dataset_key: str,
    entry: dict[str, Any],
    table: str,
    columns: tuple[str, ...],
    mapper: Callable,
    mapper_args: tuple = (),
    limit: int | None = None,
    conflict: str = "IGNORE",
    key: str | None = None,
    update: list[str] | None = None,
) -> int:
    started = _now()
    resource_id = entry.get("id")
    label = entry.get("label")

    if not resource_id or resource_id == TODO:
        reporter.record(
            dataset_key, "skipped", resource_id=resource_id, label=label,
            started_at=started,
            message="resource id still TODO in config/datasets.yml; nothing loaded",
        )
        return 0

    try:
        rows = list(
            hdb.map_records(client.records(resource_id, limit=limit), mapper, *mapper_args)
        )
    except DataGovError as exc:
        reporter.record(
            dataset_key, "error", resource_id=resource_id, label=label,
            started_at=started, message=str(exc),
        )
        return 0

    with conn:
        upsert_many(conn, table, columns, rows, conflict=conflict,
                    key=key, update=update)
    reporter.record(
        dataset_key, "ok", rows=len(rows), resource_id=resource_id, label=label,
        started_at=started,
    )
    return len(rows)


# --- individual steps ----------------------------------------------------
def sync_resale(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    total = 0
    for entry in cfg.resource_ids("resale"):
        total += _load_resource(
            conn, client, reporter, "resale", entry, "resale_txn",
            hdb.RESALE_COLUMNS, hdb.resale_row, (entry.get("label") or entry["id"],),
            limit=limit,
        )
    return total


def sync_blocks(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    """Load HDB property information, keeping the coordinates already found.

    The update is column by column rather than a REPLACE. The `block` table
    holds two things: what HDB publishes, which this step refreshes, and the
    coordinates OneMap took hours to find, which it must not touch. A REPLACE
    deletes the row and re-inserts it with only the published columns set, so
    every monthly sync quietly threw away eleven thousand geocodes and the
    next step spent two hours finding them again.
    """
    return _load_resource(
        conn, client, reporter, "property_info", cfg.datasets["property_info"],
        "block", hdb.BLOCK_COLUMNS, hdb.block_row, limit=limit,
        key="block_key",
        update=[c for c in hdb.BLOCK_COLUMNS if c != "block_key"],
    )


def sync_price_index(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    return _load_resource(
        conn, client, reporter, "price_index", cfg.datasets["price_index"],
        "price_index", hdb.PRICE_INDEX_COLUMNS, hdb.price_index_row,
        limit=limit, conflict="REPLACE",
    )


def sync_rentals(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    return _load_resource(
        conn, client, reporter, "rentals", cfg.datasets["rentals"], "rental_txn",
        hdb.RENTAL_COLUMNS, hdb.rental_row, limit=limit,
    )


def sync_schools(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    """Load MOE's school directory, which is datastore rows and not a layer.

    The amenity table is where every "what is near this block" answer comes
    from, so schools go in beside the parks and the bus stops even though
    they arrive by a different route. They land without coordinates;
    `geocode_amenities` places them from their postal codes afterwards.
    """
    from .sources import amenity

    entry = cfg.datasets.get("schools") or {}
    started = _now()
    resource_id, label = entry.get("id"), entry.get("label")

    if not resource_id or resource_id == TODO:
        reporter.record(
            "schools", "skipped", resource_id=resource_id, label=label,
            started_at=started, message="resource id still TODO in config/datasets.yml",
        )
        return 0

    try:
        rows = list(hdb.map_records(
            client.records(resource_id, limit=limit), amenity.school_row,
            label or resource_id,
        ))
    except DataGovError as exc:
        reporter.record("schools", "error", resource_id=resource_id, label=label,
                        started_at=started, message=str(exc))
        return 0

    # Keep coordinates already geocoded. Re-running the sync should not throw
    # away OneMap calls because MOE republished the directory with a new
    # principal's name, so the places are read out first and written back
    # after the reload rather than carried through the insert.
    placed = {
        row["name"]: row
        for row in conn.execute(
            "SELECT name, lat, lon, geocoded_at FROM amenity"
            " WHERE kind = 'school' AND lat IS NOT NULL AND lon IS NOT NULL"
        )
    }

    with conn:
        conn.execute("DELETE FROM amenity WHERE kind = 'school'")
        upsert_many(conn, "amenity", amenity.AMENITY_COLUMNS, rows, conflict="REPLACE")
        restored = 0
        for row in rows:
            known = placed.get(row[2])
            if not known:
                continue
            conn.execute(
                "UPDATE amenity SET lat = ?, lon = ?, min_lat = ?, max_lat = ?,"
                " min_lon = ?, max_lon = ?, geocoded_at = ?"
                " WHERE kind = 'school' AND name = ?",
                (known["lat"], known["lon"], known["lat"], known["lat"],
                 known["lon"], known["lon"], known["geocoded_at"], row[2]),
            )
            restored += 1

    primaries = sum(1 for r in rows if r[1] == "PRIMARY")
    unplaced = len(rows) - restored
    reporter.record(
        "schools", "ok" if rows else "error", rows=len(rows),
        resource_id=resource_id, label=label, started_at=started,
        message=(
            f"{primaries} primary; {unplaced} still need coordinates "
            "(run `nhf geocode --schools`)" if unplaced else f"{primaries} primary"
        ),
    )
    return len(rows)


def geocode_amenities(
    conn: sqlite3.Connection,
    reporter: SyncReporter,
    client: OneMapClient | None = None,
    kind: str = "school",
    limit: int | None = None,
) -> int:
    """Place the one layer that arrives without coordinates.

    Same geocoder as the blocks, pointed at a postal code instead of a block
    and street. A school that does not resolve keeps a null lat/lon, and the
    report then says the school distance is unknown and why, rather than
    quietly leaving it out of the within-1-km count.
    """
    started = _now()
    client = client or OneMapClient()

    sql = (
        "SELECT id, name, postal, detail FROM amenity"
        " WHERE kind = ? AND (lat IS NULL OR lon IS NULL) AND postal IS NOT NULL"
        " ORDER BY name"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    targets = conn.execute(sql, (kind,)).fetchall()

    done = 0
    failures: list[str] = []
    for row in targets:
        # The postal code is the better query: a school address written
        # "11 WOODLANDS CIRCLE" matches many things, a postal code matches one.
        for query in (row["postal"], row["detail"], row["name"]):
            if not query:
                continue
            try:
                point = client.geocode(str(query))
            except Exception as exc:          # one bad address must not stop 337
                failures.append(f"{row['name']}: {exc}")
                point = None
                break
            if point:
                break
        else:
            point = None
        if not point:
            failures.append(f"{row['name']}: no match for postal {row['postal']}")
            continue
        with conn:
            conn.execute(
                "UPDATE amenity SET lat = ?, lon = ?, min_lat = ?, max_lat = ?,"
                " min_lon = ?, max_lon = ?, geocoded_at = ? WHERE id = ?",
                (point.lat, point.lon, point.lat, point.lat, point.lon, point.lon,
                 _now(), row["id"]),
            )
        done += 1

    reporter.record(
        f"geocode_{kind}", "ok" if done or not targets else "error", rows=done,
        started_at=started,
        message=(
            f"{len(failures)} {kind}(s) did not geocode; first few: "
            + "; ".join(failures[:3])
        ) if failures else None,
    )
    return done


def sync_town_medians(conn, client, reporter, cfg: Config, limit: int | None = None) -> int:
    """HDB's own town medians, with a fallback derived from the transactions.

    The PRD links the dataset but not its resource id. Until that is filled
    in, g_town comes from medians computed here, which is the same quantity
    from the same source data.
    """
    loaded = _load_resource(
        conn, client, reporter, "town_medians", cfg.datasets["town_medians"],
        "town_median", hdb.TOWN_MEDIAN_COLUMNS, hdb.town_median_row,
        limit=limit, conflict="REPLACE",
    )
    if loaded:
        return loaded
    return derive_town_medians(conn, reporter)


def derive_town_medians(conn: sqlite3.Connection, reporter: SyncReporter | None = None) -> int:
    """Quarterly town-and-type medians straight from resale_txn.

    SQLite has no median function, so this takes the middle row of each
    ordered group.
    """
    started = _now()
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT quarter, town, flat_type, resale_price,
                   ROW_NUMBER() OVER (
                       PARTITION BY quarter, town, flat_type ORDER BY resale_price
                   ) AS rn,
                   COUNT(*) OVER (PARTITION BY quarter, town, flat_type) AS n
            FROM resale_txn
            WHERE resale_price IS NOT NULL AND town <> '' AND flat_type <> ''
        )
        SELECT quarter, town, flat_type, AVG(resale_price) AS median_price
        FROM ranked
        WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
        GROUP BY quarter, town, flat_type
        """
    ).fetchall()
    payload = [(r["quarter"], r["town"], r["flat_type"], r["median_price"], "derived")
               for r in rows]
    with conn:
        upsert_many(conn, "town_median", hdb.TOWN_MEDIAN_COLUMNS, payload, conflict="REPLACE")
    if reporter:
        reporter.record(
            "town_medians_derived", "ok", rows=len(payload), started_at=started,
            label="town medians derived from resale transactions",
            message="HDB's own median dataset id is not configured; derived instead",
        )
    return len(payload)


def sync_outlines(
    conn: sqlite3.Connection,
    reporter: SyncReporter,
    cfg: Config,
    path: str | None = None,
    session=None,
    keep_download: bool = False,
) -> int:
    """Fetch HDB building outlines and attach them to the blocks we know.

    Left out of the monthly run for the same reason as the Master Plan: the
    layer changes when buildings are built, not when flats are sold.

    The join is on the postal code, unique in both tables. A block with no
    match keeps a null outline rather than borrowing a neighbour's — about one
    in twenty, mostly blocks this store has no postal code for.
    """
    import requests

    from .sources import outline as outline_src

    started = _now()
    entry = cfg.datasets.get("outlines") or {}
    resource_id, label = entry.get("id"), entry.get("label")

    if not resource_id or resource_id == TODO:
        reporter.record(
            "outlines", "skipped", resource_id=resource_id, label=label,
            started_at=started,
            message="resource id still TODO in config/datasets.yml; nothing loaded",
        )
        return 0

    local = path
    downloaded = False
    try:
        if not local:
            session = session or requests.Session()
            local = str(_fetch_geojson(
                resource_id, CACHE_DIR / f"{resource_id}.geojson", session))
            downloaded = True
        rows = list(outline_src.stream_outlines(local))
    except Exception as exc:
        reporter.record(
            "outlines", "error", resource_id=resource_id, label=label,
            started_at=started, message=str(exc),
        )
        return 0
    finally:
        if downloaded and keep_download is False and local:
            Path(local).unlink(missing_ok=True)

    if not rows:
        reporter.record(
            "outlines", "error", resource_id=resource_id, label=label,
            started_at=started, message="the layer parsed to nothing",
        )
        return 0

    with conn:
        # Cleared first, so a block whose footprint has gone from the layer
        # loses its outline rather than keeping last year's.
        conn.execute("UPDATE block SET outline = NULL")
        conn.executemany(
            "UPDATE block SET outline = ? WHERE postal = ?",
            [(shape, postal) for postal, _, shape in rows],
        )

    matched = conn.execute(
        "SELECT COUNT(*) FROM block WHERE outline IS NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM block").fetchone()[0]
    reporter.record(
        "outlines", "ok", resource_id=resource_id, label=label,
        started_at=started, rows=len(rows),
        message=(
            f"{matched} of {total} blocks matched a footprint by postal code; "
            "the rest keep a null outline rather than a neighbour's"
        ),
    )
    return matched


def sync_land_use(
    conn: sqlite3.Connection,
    reporter: SyncReporter,
    cfg: Config,
    path: str | None = None,
    session=None,
    keep_download: bool = False,
) -> int:
    """Fetch and store the URA Master Plan land use parcels.

    Deliberately left out of the monthly run. The Master Plan is approved
    once and amended occasionally, and the file is 174 MB, so re-fetching it
    every month would move a lot of bytes to learn nothing. `path` loads a
    file already on disk instead of downloading.
    """
    import requests

    from .sources import ura

    started = _now()
    entry = cfg.datasets.get("land_use") or {}
    resource_id, label = entry.get("id"), entry.get("label")

    if not resource_id or resource_id == TODO:
        reporter.record(
            "land_use", "skipped", resource_id=resource_id, label=label,
            started_at=started,
            message="resource id still TODO in config/datasets.yml; nothing loaded",
        )
        return 0

    local = path
    downloaded = False
    try:
        if not local:
            session = session or requests.Session()
            local = str(_fetch_geojson(
                resource_id, CACHE_DIR / f"{resource_id}.geojson", session))
            downloaded = True

        rows = list(ura.stream_parcels(local, source=label or resource_id))
    except Exception as exc:                     # network, disk, or bad JSON
        reporter.record(
            "land_use", "error", resource_id=resource_id, label=label,
            started_at=started, message=str(exc),
        )
        return 0
    finally:
        # Fetch at build time, derive, discard — the PRD's own instruction.
        # 174 MB is the one download worth deleting the moment it has been
        # read, and only the copy this step downloaded: a file handed in with
        # `path` belongs to the caller.
        if downloaded and keep_download is False and local:
            Path(local).unlink(missing_ok=True)

    if not rows:
        reporter.record(
            "land_use", "error", resource_id=resource_id, label=label,
            started_at=started, message="the layer parsed to no parcels at all",
        )
        return 0

    with conn:
        # Every row, not just this source's. The table holds one Master Plan:
        # two of them means every parcel question is answered twice, once from
        # each plan, and nothing in the query layer would notice.
        #
        # Deleting by source was the obvious thing and it is wrong, because
        # the source *is* the label and the label changes with the plan. The
        # 2019 to 2025 swap found this the hard way: the delete matched the
        # new label, removed nothing, and the table came out with 226,606
        # parcels from two plans at once.
        conn.execute("DELETE FROM land_parcel")
        upsert_many(conn, "land_parcel", ura.PARCEL_COLUMNS, rows, conflict="REPLACE")

    without_ratio = sum(1 for r in rows if r[3] is None)
    reporter.record(
        "land_use", "ok", rows=len(rows), resource_id=resource_id, label=label,
        started_at=started,
        message=(
            f"{without_ratio} of {len(rows)} parcels carry no numeric GPR "
            "(landed housing, or not yet evaluated), so a height band is only "
            "available for the rest"
        ),
    )
    return len(rows)


def _fetch_geojson(resource_id: str, dest: Path, session) -> str:
    """Download a GeoJSON layer via the file route, not the datastore.

    The datastore returns nothing for a geospatial dataset; these come from
    api-open.data.gov.sg, which hands back a presigned link. `dest` is under
    the ignored cache directory, because one of these layers is 174 MB and
    GitHub rejects a push carrying a file over 100.
    """
    from .sources import ura

    url = ura.download_url(resource_id, session)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with session.get(url, stream=True, timeout=900) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    return str(dest)


def sync_amenities(
    conn: sqlite3.Connection,
    reporter: SyncReporter,
    cfg: Config,
    paths: dict[str, str] | None = None,
    session=None,
) -> int:
    """Load the parks, hawker centre and park facility layers."""
    import requests

    from .sources import amenity

    import time

    from .sources.ura import RATE_LIMIT_WAIT_SECONDS

    total = 0
    session = session or requests.Session()
    cache = CACHE_DIR
    first = True

    for entry in cfg.datasets.get("amenities") or []:
        started = _now()
        key, resource_id, label = entry.get("key"), entry.get("id"), entry.get("label")

        # One request every ten seconds is what this endpoint allows. The
        # retry inside `download_url` recovers from being refused; pacing the
        # layers means usually not being refused in the first place.
        if not first and not (paths or {}).get(key):
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
        first = False

        if not resource_id or resource_id == TODO:
            reporter.record(
                f"amenity:{key}", "skipped", resource_id=resource_id, label=label,
                started_at=started, message="resource id still TODO in config",
            )
            continue
        try:
            local = (paths or {}).get(key) or _fetch_geojson(
                resource_id, cache / f"{resource_id}.geojson", session
            )
            rows = list(amenity.load_layer(local, key, source=label or resource_id))
        except Exception as exc:
            reporter.record(
                f"amenity:{key}", "error", resource_id=resource_id, label=label,
                started_at=started, message=str(exc),
            )
            continue

        with conn:
            conn.execute("DELETE FROM amenity WHERE kind = ?", (key,))
            upsert_many(conn, "amenity", amenity.AMENITY_COLUMNS, rows, conflict="IGNORE")
        total += len(rows)

        note = None
        if key == "hawker_centre":
            soon = sum(1 for r in rows if (r[4] or "").lower().startswith("under"))
            note = f"{soon} not open yet" if soon else None
        reporter.record(
            f"amenity:{key}", "ok" if rows else "error", rows=len(rows),
            resource_id=resource_id, label=label, started_at=started, message=note,
        )
    return total


def sync_rainfall(
    conn: sqlite3.Connection,
    client: DataGovClient,
    reporter: SyncReporter,
    cfg: Config,
    year: int | None = None,
    limit: int | None = None,
    sample_pages: int | None = 40,
) -> int:
    """Fold a year of five-minute rainfall readings into one row per station.

    Millions of rows in, 89 out. The readings are never stored: the only
    question they answer is whether one part of the island is wetter than
    another.

    By default this samples rather than reads the year whole. 2024 is
    6,406,260 readings, and with no server-side aggregation available that is
    hours of paging for a ranking. Pages are spread across the year and each
    one carries about 60 of the 89 stations, so every gauge is sampled alike
    — which is what makes the comparison between them fair even though the
    totals are not annual totals. `sample_pages=None` reads everything, for
    whoever wants to spend the hours.
    """
    from .sources import rainfall

    started = _now()
    conf = cfg.datasets.get("rainfall") or {}
    year = int(year or conf.get("default_year") or 0)
    resource_id = (conf.get("years") or {}).get(year)
    label = f"NEA historical rainfall ({year})"

    if not resource_id or resource_id == TODO:
        reporter.record(
            "rainfall", "skipped", label=label, started_at=started,
            message=f"no resource id configured for {year}",
        )
        return 0

    sampled = bool(sample_pages) and not limit
    try:
        records = (
            client.sample(resource_id, pages=int(sample_pages))
            if sampled else client.records(resource_id, limit=limit)
        )
        stations = rainfall.fold(records)
    except DataGovError as exc:
        reporter.record("rainfall", "error", resource_id=resource_id, label=label,
                        started_at=started, message=str(exc))
        return 0

    rows = list(rainfall.station_rows(stations, year, label))
    if not rows:
        reporter.record("rainfall", "error", resource_id=resource_id, label=label,
                        started_at=started, message="no station readings came back")
        return 0

    with conn:
        conn.execute("DELETE FROM rainfall_station WHERE year = ?", (year,))
        upsert_many(conn, "rainfall_station", rainfall.STATION_COLUMNS, rows,
                    conflict="REPLACE")

    # Rate per reading, not total: with a sample the totals are not
    # comparable between stations that happened to land in a different number
    # of pages, and the whole point of the layer is the comparison.
    def rate(row: tuple) -> float:
        return (row[5] or 0.0) / row[6] if row[6] else 0.0

    wettest = max(rows, key=rate)
    driest = min(rows, key=rate)
    readings = sum(r[6] or 0 for r in rows)
    reporter.record(
        "rainfall", "ok", rows=len(rows), resource_id=resource_id, label=label,
        started_at=started,
        message=(
            f"{year}: {readings:,} readings "
            f"{'sampled from ' + str(sample_pages) + ' pages' if sampled else 'read whole'}; "
            f"wettest {wettest[1]} at {rate(wettest) * 1000:.2f} mm per 1000 readings, "
            f"driest {driest[1]} at {rate(driest) * 1000:.2f}"
            + ("; a sample ranks stations against each other and is not an annual total"
               if sampled else "")
        ),
    )
    return len(rows)


def load_future_mrt(conn: sqlite3.Connection, cfg: Config, reporter: SyncReporter) -> int:
    """The hand-kept table of announced and under-construction stations.

    An empty table is reported, not hidden: the new-transport signal then
    contributes nothing and the app says the table needs filling in.
    """
    started = _now()
    stations = cfg.future_mrt.get("stations") or []
    rows = [
        (
            clean(s.get("name")), clean(s.get("line")),
            s.get("status") or "announced", s.get("expected_year"),
            s.get("lat"), s.get("lon"), "config/future_mrt.yml",
        )
        for s in stations
    ]
    with conn:
        conn.execute("DELETE FROM mrt_station WHERE source = 'config/future_mrt.yml'")
        upsert_many(
            conn, "mrt_station",
            ("name", "line", "status", "expected_year", "lat", "lon", "source"),
            rows, conflict="REPLACE",
        )
    missing_coords = sum(1 for r in rows if r[4] is None or r[5] is None)
    reporter.record(
        "future_mrt",
        "ok" if rows and not missing_coords else "skipped",
        rows=len(rows), started_at=started, label="LTA upcoming rail (hand-kept)",
        message=(
            "config/future_mrt.yml has no stations yet, so the new-transport signal "
            "scores 0 for every block"
            if not rows else
            f"{missing_coords} station(s) have no coordinates; run `nhf geocode-mrt`"
            if missing_coords else None
        ),
    )
    return len(rows)


def geocode_offices(conn: sqlite3.Connection, cfg: Config, reporter: SyncReporter,
                    client: OneMapClient | None = None) -> int:
    """Put both offices on the map, from their postal codes."""
    started = _now()
    client = client or OneMapClient()
    done = 0
    problems: list[str] = []
    for office in cfg.offices:
        try:
            point = client.geocode(office.postal)
        except (OneMapError, Exception) as exc:  # network failures included
            problems.append(f"{office.code}: {exc}")
            point = None
        with conn:
            conn.execute(
                "INSERT INTO office (code, label, postal, address, lat, lon)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(code) DO UPDATE SET label=excluded.label,"
                " postal=excluded.postal, address=excluded.address,"
                " lat=COALESCE(excluded.lat, office.lat),"
                " lon=COALESCE(excluded.lon, office.lon)",
                (office.code, office.label, office.postal,
                 point.address if point else None,
                 point.lat if point else None, point.lon if point else None),
            )
        if point:
            done += 1
    reporter.record(
        "offices", "ok" if done == len(cfg.offices) else "error", rows=done,
        started_at=started, message="; ".join(problems) or None,
    )
    return done


def geocode_blocks(
    conn: sqlite3.Connection,
    reporter: SyncReporter,
    client: OneMapClient | None = None,
    limit: int | None = None,
    only_missing: bool = True,
) -> int:
    """Geocode each unique block address once and cache it.

    Neither HDB dataset carries coordinates, and there are ~13k blocks, so
    this is run once and then only for blocks that are new.
    """
    started = _now()
    client = client or OneMapClient()

    where = "WHERE lat IS NULL OR lon IS NULL" if only_missing else ""
    sql = f"SELECT block_key, block, street_name FROM block {where} ORDER BY block_key"
    if limit:
        sql += f" LIMIT {int(limit)}"
    targets = conn.execute(sql).fetchall()

    done = 0
    failures: list[str] = []
    for row in targets:
        query = f"{row['block']} {row['street_name']}"
        try:
            point = client.geocode(query)
        except Exception as exc:  # one bad address must not stop 13k others
            failures.append(f"{row['block_key']}: {exc}")
            continue
        if not point:
            failures.append(f"{row['block_key']}: no match")
            continue
        with conn:
            conn.execute(
                "UPDATE block SET lat=?, lon=?, postal=?, geocode_source='onemap',"
                " geocoded_at=? WHERE block_key=?",
                (point.lat, point.lon, point.postal, _now(), row["block_key"]),
            )
        done += 1

    reporter.record(
        "geocode_blocks", "ok" if done else "error" if targets else "ok",
        rows=done, started_at=started,
        message=(
            f"{len(failures)} block(s) did not geocode; first few: "
            + "; ".join(failures[:3])
        ) if failures else None,
    )
    return done


def compute_commute(
    conn: sqlite3.Connection,
    cfg: Config,
    reporter: SyncReporter,
    client: OneMapClient | None = None,
    limit: int | None = None,
    only_missing: bool = True,
) -> int:
    """Drive time from every geocoded block to both offices.

    Without a OneMap token every row is written with status `no_token` rather
    than a guessed number: an unknown commute shows as unknown in the app.
    """
    started = _now()
    client = client or OneMapClient()
    offices = conn.execute(
        "SELECT code, label, lat, lon FROM office WHERE lat IS NOT NULL"
    ).fetchall()
    if not offices:
        reporter.record("commute", "error", started_at=started,
                        message="offices are not geocoded yet; run `nhf geocode --offices`")
        return 0

    sql = """
        SELECT b.block_key, b.lat, b.lon FROM block b
        WHERE b.lat IS NOT NULL AND b.lon IS NOT NULL
    """
    if only_missing:
        sql += """ AND EXISTS (
            SELECT 1 FROM resale_txn t WHERE t.block_key = b.block_key
        ) AND b.block_key NOT IN (
            SELECT block_key FROM commute WHERE status = 'ok'
        )"""
    sql += " ORDER BY b.block_key"
    if limit:
        sql += f" LIMIT {int(limit)}"
    blocks = conn.execute(sql).fetchall()

    from .sources.onemap import GeoPoint

    written = 0
    token_missing = False
    errors: list[str] = []
    for block in blocks:
        for office in offices:
            status, minutes, distance, message = "ok", None, None, None
            try:
                route = client.drive_route(
                    GeoPoint(block["lat"], block["lon"]),
                    GeoPoint(office["lat"], office["lon"]),
                )
                minutes, distance = route.minutes, route.distance_m
            except NoTokenError as exc:
                status, message, token_missing = "no_token", str(exc), True
            except Exception as exc:
                status, message = "error", str(exc)
                errors.append(f"{block['block_key']}->{office['code']}: {exc}")
            with conn:
                conn.execute(
                    "INSERT INTO commute (block_key, office_code, drive_minutes,"
                    " distance_m, status, message, computed_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(block_key, office_code) DO UPDATE SET"
                    " drive_minutes=excluded.drive_minutes,"
                    " distance_m=excluded.distance_m, status=excluded.status,"
                    " message=excluded.message, computed_at=excluded.computed_at",
                    (block["block_key"], office["code"], minutes, distance,
                     status, message, _now()),
                )
            if status == "ok":
                written += 1
        if token_missing:
            break  # no point walking 13k blocks without a token

    reporter.record(
        "commute",
        "ok" if written else "skipped" if token_missing else "error" if errors else "ok",
        rows=written, started_at=started,
        message=(
            "no OneMap token, so drive times are unknown; set ONEMAP_TOKEN or "
            "ONEMAP_EMAIL/ONEMAP_PASSWORD" if token_missing
            else ("; ".join(errors[:3]) if errors else None)
        ),
    )
    return written


def backfill_block_towns(conn: sqlite3.Connection) -> int:
    """Make every block's town match the spelling the transactions use.

    Two problems with one cause. HDB property information abbreviates the
    town — `SB`, `YS`, `WL` — while the resale files spell it out, and every
    comparison in the app is against the spelled-out form: the far-north
    criterion lists SEMBAWANG, WOODLANDS and YISHUN by name, and the town
    filter offers whatever the column happens to hold. Filling only the empty
    towns left 99% of blocks holding a code, so the Woodlands, Sembawang and
    Yishun blocks silently missed the one flag they exist to raise, and the
    filter listed each town twice under two spellings.

    The mapping is read out of the blocks that appear in both datasets rather
    than written down here, so a renamed or newly built town needs no edit.
    Blocks that have never transacted are translated with that mapping, which
    is the only evidence available for them.
    """
    canonical = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT town FROM resale_txn WHERE town IS NOT NULL AND town <> ''"
        )
    }
    if not canonical:
        return 0

    placeholders = ", ".join("?" for _ in canonical)
    params = tuple(canonical)

    # What each unrecognised value turns out to mean, by majority vote of the
    # blocks that carry it and have transactions of their own.
    learned: dict[str, str] = {}
    for code, name, _n in conn.execute(
        f"""
        SELECT b.town AS code, t.town AS name, COUNT(*) AS n
        FROM block b JOIN resale_txn t ON t.block_key = b.block_key
        WHERE b.town IS NOT NULL AND b.town <> ''
          AND b.town NOT IN ({placeholders})
        GROUP BY code, name ORDER BY code, n DESC
        """,
        params,
    ):
        learned.setdefault(code, name)

    changed = 0
    with conn:
        # A block with transactions is told by its own sales, which is better
        # evidence than any mapping.
        cur = conn.execute(
            f"""
            UPDATE block SET town = (
                SELECT t.town FROM resale_txn t
                WHERE t.block_key = block.block_key AND t.town <> ''
                GROUP BY t.town ORDER BY COUNT(*) DESC LIMIT 1
            )
            WHERE (town IS NULL OR town = '' OR town NOT IN ({placeholders}))
              AND EXISTS (SELECT 1 FROM resale_txn t WHERE t.block_key = block.block_key)
            """,
            params,
        )
        changed += cur.rowcount or 0

        # The rest have no sales to read, so the learned mapping is all there is.
        for code, name in learned.items():
            cur = conn.execute(
                "UPDATE block SET town = ? WHERE town = ?", (name, code)
            )
            changed += cur.rowcount or 0

    leftover = [
        row[0] for row in conn.execute(
            f"SELECT DISTINCT town FROM block WHERE town IS NOT NULL AND town <> ''"
            f" AND town NOT IN ({placeholders})",
            params,
        )
    ]
    if leftover:
        log.warning(
            "%s block town value(s) match no town in the transactions and were "
            "left as they are: %s",
            len(leftover), ", ".join(sorted(leftover)[:10]),
        )
    return changed


def add_missing_blocks(conn: sqlite3.Connection) -> int:
    """Create block rows for addresses that transact but are not in property info.

    Keeps a transacting block visible with a null max_floor_lvl (so top-floor
    detection says "unknown") instead of dropping it from the shortlist.
    """
    with conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO block (block_key, block, street_name, town)
            SELECT block_key, block, street_name,
                   (SELECT town FROM resale_txn x WHERE x.block_key = t.block_key
                    AND x.town <> '' LIMIT 1)
            FROM resale_txn t
            WHERE block_key NOT IN (SELECT block_key FROM block)
            GROUP BY block_key
            """
        )
    return cur.rowcount or 0


def run_sync(
    conn: sqlite3.Connection,
    steps: Iterable[str] | None = None,
    limit: int | None = None,
    client: DataGovClient | None = None,
    onemap: OneMapClient | None = None,
) -> list[dict[str, Any]]:
    """Run the monthly sync. `steps` picks a subset by name."""
    cfg = load_config()
    client = client or DataGovClient()
    reporter = SyncReporter(conn)
    wanted = set(steps) if steps else None

    def should(name: str) -> bool:
        return wanted is None or name in wanted

    if should("resale"):
        sync_resale(conn, client, reporter, cfg, limit)
    if should("blocks"):
        sync_blocks(conn, client, reporter, cfg, limit)
        add_missing_blocks(conn)
        backfill_block_towns(conn)
    if should("price_index"):
        sync_price_index(conn, client, reporter, cfg, limit)
    if should("town_medians"):
        sync_town_medians(conn, client, reporter, cfg, limit)
    if should("rentals"):
        sync_rentals(conn, client, reporter, cfg, limit)
    if should("schools"):
        sync_schools(conn, client, reporter, cfg, limit)
    if should("future_mrt"):
        load_future_mrt(conn, cfg, reporter)
    # Only when asked for by name: 174 MB of a plan that changes every few
    # years has no business in a monthly job.
    if wanted and "land_use" in wanted:
        sync_land_use(conn, reporter, cfg)
    if should("amenities"):
        sync_amenities(conn, reporter, cfg)
    # By name only: a year of five-minute readings is 704 pages for a figure
    # that changes once a year.
    if wanted and "rainfall" in wanted:
        sync_rainfall(conn, client, reporter, cfg, limit=limit)
    # By name only: building outlines change when buildings are built.
    if wanted and "outlines" in wanted:
        sync_outlines(conn, reporter, cfg)
    if should("geocode"):
        geocode_offices(conn, cfg, reporter, onemap)
        geocode_blocks(conn, reporter, onemap, limit=limit)
        geocode_amenities(conn, reporter, onemap, kind="school", limit=limit)
    if should("commute"):
        compute_commute(conn, cfg, reporter, onemap, limit=limit)

    return reporter.results
