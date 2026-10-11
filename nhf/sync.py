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
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .config import TODO, Config, load_config
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
        upsert_many(conn, table, columns, rows, conflict=conflict)
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
    return _load_resource(
        conn, client, reporter, "property_info", cfg.datasets["property_info"],
        "block", hdb.BLOCK_COLUMNS, hdb.block_row, limit=limit, conflict="REPLACE",
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
    """Fill a block's town from its transactions when property info missed it."""
    with conn:
        cur = conn.execute(
            """
            UPDATE block SET town = (
                SELECT t.town FROM resale_txn t
                WHERE t.block_key = block.block_key AND t.town <> ''
                GROUP BY t.town ORDER BY COUNT(*) DESC LIMIT 1
            )
            WHERE (town IS NULL OR town = '')
              AND EXISTS (SELECT 1 FROM resale_txn t WHERE t.block_key = block.block_key)
            """
        )
    return cur.rowcount or 0


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
    if should("future_mrt"):
        load_future_mrt(conn, cfg, reporter)
    if should("geocode"):
        geocode_offices(conn, cfg, reporter, onemap)
        geocode_blocks(conn, reporter, onemap, limit=limit)
    if should("commute"):
        compute_commute(conn, cfg, reporter, onemap, limit=limit)

    return reporter.results
