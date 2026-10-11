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
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
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
) -> int:
    cols = list(columns)
    sql = (
        f"INSERT OR {conflict} INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
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
