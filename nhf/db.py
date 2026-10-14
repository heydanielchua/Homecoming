"""SQLite access. One file, opened per process, read-only from the web app."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else DB_PATH
    if target != Path(":memory:"):
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # A long step (geocoding runs for hours) holds the write lock in bursts,
    # and SQLite's default is to fail a competing write immediately rather
    # than wait. Thirty seconds of patience turns that into a non-event.
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# Columns added to a table after the first version of the schema shipped.
# `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
# a database made by an earlier version would keep the old shape and every
# insert naming a new column would fail. Adding them here, idempotently, is
# what lets an existing data/nhf.db survive a schema change without a resync
# that costs hours of geocoding.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "amenity": {"postal": "TEXT", "geocoded_at": "TEXT"},
}


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> list[str]:
    """Add any of `columns` the table does not have yet. Returns what was added."""
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if not existing:                  # no such table; the schema will create it
        return []
    added = []
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added.append(name)
    return added


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    for table, columns in ADDED_COLUMNS.items():
        ensure_columns(conn, table, columns)
    conn.commit()


def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Connect and make sure the schema is there."""
    conn = connect(path)
    init_db(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def upsert_many(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[str],
    rows: Iterable[tuple],
    conflict: str = "IGNORE",
    key: str | None = None,
    update: Iterable[str] | None = None,
) -> int:
    """Insert rows, with a choice about what a conflict means.

    `conflict` is the blunt instrument: IGNORE keeps what is there, REPLACE
    overwrites the whole row.

    `key` and `update` are the careful one, and the difference matters where a
    table holds columns from two sources. `block` carries HDB's property
    information *and* the coordinates OneMap took hours to find; REPLACE on a
    re-download deletes the row and re-inserts it with only the property
    columns set, which silently discarded every geocode and made the next
    sync re-geocode all eleven thousand blocks. Naming the columns to update
    leaves the rest alone.
    """
    cols = list(columns)
    placeholders = ", ".join("?" for _ in cols)
    if key and update:
        assignments = ", ".join(f"{c} = excluded.{c}" for c in update)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {assignments}"
        )
    else:
        sql = (
            f"INSERT OR {conflict} INTO {table} ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
    cur = conn.executemany(sql, rows)
    return cur.rowcount or 0


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
