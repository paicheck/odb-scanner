"""The only module that talks to MySQL.

Every connection is opened per request and closed immediately: the ingest rate
is a handful of batches a minute, MySQL connections are cheap at that rate, and
a long-lived connection would need its own reconnect/ping handling to survive a
managed database's idle timeouts -- which is exactly the kind of background
machinery that goes wrong quietly at 3am. Per-request connections have nothing
to go stale.

`pymysql` is imported lazily so this module (and everything that imports the
package) can be imported and unit-tested on a machine with no MySQL driver
installed, which is every developer machine and CI runner.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from .config import DbSettings
from .mapping import MEASUREMENT_COLUMNS

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _statements(sql: str) -> list[str]:
    """Split a .sql file into executable statements.

    Conservative on purpose: it drops `--` comment lines and splits on the
    terminating semicolons. The schema deliberately keeps statements simple
    (no triggers, no routines with embedded semicolons) so this stays correct.
    """
    lines = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


class MySqlStore:
    """Idempotent writes of measurements into the shared cloud schema."""

    def __init__(self, settings: DbSettings):
        self.settings = settings
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # -- connection ----------------------------------------------------------
    def _connect(self):
        import pymysql
        from pymysql.cursors import DictCursor

        kwargs = dict(
            host=self.settings.host,
            port=self.settings.port,
            user=self.settings.user,
            password=self.settings.password,
            database=self.settings.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
        if self.settings.ssl_ca:
            kwargs["ssl_ca"] = self.settings.ssl_ca
        return pymysql.connect(**kwargs)

    def ensure_schema(self) -> None:
        """Create the tables if they are missing. Safe to call concurrently."""
        with self._schema_lock:
            if self._schema_ready:
                return
            statements = _statements(_SCHEMA_PATH.read_text(encoding="utf-8"))
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    for statement in statements:
                        cur.execute(statement)
                conn.commit()
            except Exception:
                # Leaving _schema_ready False means the next request retries,
                # so a database that was briefly unavailable during startup is
                # not permanently declared broken.
                conn.rollback()
                raise
            finally:
                conn.close()
            self._schema_ready = True

    # -- writes --------------------------------------------------------------
    def touch_source(self, source_id: str, kind: str, label: str,
                     now: datetime) -> None:
        """Record that a source is alive, creating it on first contact.

        ON DUPLICATE KEY UPDATE keeps the first_seen of the original row while
        refreshing last_seen, so the table answers "when did this writer first
        appear" as well as "is it still running". VALUES() rather than the
        newer alias syntax because MariaDB does not support the alias form.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sources(source_id, kind, label, first_seen, "
                    "last_seen) VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE kind=VALUES(kind), "
                    "label=VALUES(label), last_seen=VALUES(last_seen)",
                    (source_id, kind, label, now, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_measurements(self, rows: list[tuple]) -> tuple[int, int]:
        """Insert a batch, ignoring ids already present.

        INSERT IGNORE, not upsert: a measurement is an immutable observation, so
        the only correct response to an id that already exists is to leave the
        stored row alone. It also makes the batch idempotent for free -- a
        client that retries after a lost response re-sends exactly the rows that
        did land and nothing changes.

        Returns (inserted, duplicates).
        """
        if not rows:
            return (0, 0)
        columns = ", ".join(f"`{c}`" for c in MEASUREMENT_COLUMNS)
        placeholders = ", ".join(["%s"] * len(MEASUREMENT_COLUMNS))
        sql = f"INSERT IGNORE INTO measurements ({columns}) VALUES ({placeholders})"

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
                inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return (inserted, len(rows) - inserted)

    # -- reads (used by /status) ---------------------------------------------
    def counts(self) -> dict:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM measurements")
                measurements = cur.fetchone()["n"]
                cur.execute("SELECT COUNT(*) AS n FROM sources")
                sources = cur.fetchone()["n"]
        finally:
            conn.close()
        return {"measurements": int(measurements), "sources": int(sources)}
