"""SQLite storage for the ingest API, so this PC can be the archive without MySQL.

The same four methods as MySqlStore (`ensure_schema`, `touch_source`,
`insert_measurements`, `counts`), so cloud/api.py and cloud/wsgi.py serve it
unchanged, and the same guarantee: a measurement's id is the client's UUID
and the idempotency key, so INSERT OR IGNORE leaves a stored row alone and a
batch retried after a lost response changes nothing.

The archive is its own file and never the collector's database: the PC's own
uploads arrive here next to the phones', exactly as they would in MySQL.

One connection per call, like MySqlStore. uvicorn runs the handlers on a
thread pool and an sqlite3 connection belongs to the thread that opened it, so
a shared connection would need locking; per-call connections need none. WAL
plus a busy timeout let `main.py backup` read the file while a batch lands.
Timestamps are stored as naive-UTC ISO-8601 to the millisecond, matching the
DATETIME(3) columns of cloud/schema.sql.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .mapping import MEASUREMENT_COLUMNS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id  TEXT NOT NULL PRIMARY KEY,
    kind       TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS measurements (
    id              TEXT NOT NULL PRIMARY KEY,
    source_id       TEXT NOT NULL,
    vin             TEXT NOT NULL,
    ts              TEXT NOT NULL,
    ts_raw          TEXT NOT NULL,
    ecu             TEXT,
    service         TEXT,
    pid             TEXT,
    "key"           TEXT NOT NULL,
    value           REAL,
    text_value      TEXT,
    unit            TEXT,
    provenance      TEXT NOT NULL,
    decoding_method TEXT NOT NULL,
    doc_status      TEXT,
    raw_response    TEXT,
    success         INTEGER NOT NULL DEFAULT 1,
    error           TEXT,
    received_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_meas_vin_ts ON measurements(vin, ts);
CREATE INDEX IF NOT EXISTS ix_meas_key_ts ON measurements("key", ts);
CREATE INDEX IF NOT EXISTS ix_meas_source_ts ON measurements(source_id, ts);
"""


def _stamp(value):
    """A datetime as the ISO text this archive stores; anything else as is."""
    if isinstance(value, datetime):
        return value.isoformat(sep="T", timespec="milliseconds")
    return value


class SqliteStore:
    """Idempotent writes of measurements into an SQLite archive file."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=30.0)

    def ensure_schema(self) -> None:
        """Create the file and tables if missing. Safe to call concurrently."""
        with self._schema_lock:
            if self._schema_ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
            finally:
                conn.close()
            self._schema_ready = True

    def touch_source(self, source_id: str, kind: str, label: str,
                     now: datetime) -> None:
        """Record that a source is alive; first_seen survives later contact."""
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO sources(source_id, kind, label, first_seen, "
                    "last_seen) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET kind=excluded.kind, "
                    "label=excluded.label, last_seen=excluded.last_seen",
                    (source_id, kind, label, _stamp(now), _stamp(now)))
        finally:
            conn.close()

    def insert_measurements(self, rows: list[tuple]) -> tuple[int, int]:
        """Insert a batch, ignoring ids already present. (inserted, duplicates)."""
        if not rows:
            return (0, 0)
        columns = ", ".join(f'"{c}"' for c in MEASUREMENT_COLUMNS)
        placeholders = ", ".join(["?"] * len(MEASUREMENT_COLUMNS))
        sql = (f"INSERT OR IGNORE INTO measurements ({columns}) "
               f"VALUES ({placeholders})")
        conn = self._connect()
        try:
            before = conn.total_changes
            with conn:
                conn.executemany(sql, [tuple(_stamp(v) for v in row)
                                       for row in rows])
            inserted = conn.total_changes - before
        finally:
            conn.close()
        return (inserted, len(rows) - inserted)

    def counts(self) -> dict:
        conn = self._connect()
        try:
            measurements = conn.execute(
                "SELECT COUNT(*) FROM measurements").fetchone()[0]
            sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        finally:
            conn.close()
        return {"measurements": int(measurements), "sources": int(sources)}
