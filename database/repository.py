"""Repository: all database access. Raw responses always preserved."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import SCHEMA_SQL, SCHEMA_VERSION

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utciso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _iso_days_ago(days: float) -> str:
    return utciso(datetime.now(timezone.utc) - timedelta(days=days))


def _parse_ts(ts: str) -> datetime:
    """Parse a stored stamp, normalising naive ones to UTC.

    Every writer in production uses utcnow(), which is aware, so this coercion
    never fires on rows this code wrote. It matters for rows it did not: a
    timestamp backfilled by hand, or written by an importer, arrives naive, and
    subtracting an aware stamp from a naive one raises TypeError. In
    close_session() that exception used to escape the collector entirely and
    leave the session stuck at status='open', where charging_sessions() (which
    filters on 'completed') can never see it again -- _adopt_open_session can
    adopt it but nothing could ever close it.
    """
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class SchemaMismatchError(RuntimeError):
    """The database on disk was written by a build with a different schema."""


class Repository:
    """One per-process handle to the SQLite database.

    Connections are per-thread. The dashboard runs sync handlers on
    Starlette's threadpool while the collector thread writes, and a single
    sqlite3.Connection cannot be used that way: interleaved use raises
    InterfaceError ("bad parameter or other API misuse") and silently drops
    writes, because the connection's implicit transaction state is shared. The
    busy_timeout below only covers lock contention between *processes*; it does
    nothing for two threads driving the same connection object.

    Each thread gets its own connection to the same file, so WAL still allows
    concurrent reads alongside a single writer, and `repo.conn` keeps working
    unchanged for callers and tests.
    """

    def __init__(self, path: str | Path, check_schema: bool = True):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._closed = False
        existing = self._stored_schema_version()
        self.conn.executescript(SCHEMA_SQL)
        if check_schema and existing is not None \
                and existing != SCHEMA_VERSION:
            # CREATE TABLE IF NOT EXISTS leaves an older database's tables
            # exactly as they are, so a version mismatch means the columns this
            # build reads may not be the ones that are there. Stamping the
            # current version over the old one (as this used to do on every
            # open) destroyed the only record that the two had diverged.
            self.close()
            raise SchemaMismatchError(
                f"{self.path}: database schema is version {existing}, but this "
                f"build expects {SCHEMA_VERSION}. Refusing to write, because the "
                f"existing tables may not have the columns this build reads. "
                f"Start from an empty database, or restore a backup and migrate."
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        if self._closed:
            # Deliberately not lazy: quietly reopening would hide the lifecycle
            # bug that closing was meant to surface.
            raise sqlite3.ProgrammingError(
                f"repository for {self.path} is closed")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False,
                                   timeout=30.0)
            conn.row_factory = sqlite3.Row
            # foreign_keys is per-connection and is not persisted in the file,
            # so it has to be set on every connection the process opens.
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def _stored_schema_version(self) -> int | None:
        """Version recorded in an existing database, or None if it is new."""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if row is None:
            return None
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        self._closed = True
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- vehicle / ecus ------------------------------------------------------
    def ensure_vehicle(self, vin: str, make="", model="", variant="", year=None) -> int:
        # Insert-then-read rather than read-then-insert: two threads can both
        # miss the initial SELECT, and the loser's INSERT then died on the UNIQUE
        # constraint. ON CONFLICT DO NOTHING makes the insert idempotent so the
        # second thread simply reads back the row the first one created.
        self.conn.execute(
            "INSERT INTO vehicles(vin, make, model, variant, year, first_seen) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(vin) DO NOTHING",
            (vin, make, model, variant, year, utcnow()),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM vehicles WHERE vin=?", (vin,)).fetchone()["id"]

    def upsert_ecu(self, vehicle_id: int, key: str, name: str, tx: int, rx: int,
                   status: str, doc_status: str) -> None:
        now = utcnow()
        self.conn.execute(
            "INSERT INTO ecus(vehicle_id, key, name, tx_id, rx_id, status, "
            "doc_status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(vehicle_id, key) DO UPDATE SET status=excluded.status, "
            "last_seen=excluded.last_seen",
            (vehicle_id, key, name, tx, rx, status, doc_status, now, now),
        )
        self.conn.commit()

    def list_ecus(self, vehicle_id: int):
        return self.conn.execute(
            "SELECT * FROM ecus WHERE vehicle_id=? ORDER BY key", (vehicle_id,)
        ).fetchall()

    def first_vehicle(self):
        """The vehicle the single-vehicle tools operate on.

        The dashboard, `analyze` and the AI context all assume one vehicle
        per database. Pick the most recently first-seen row (newest id on
        ties): an old stray row -- e.g. one created by a run that misdecoded
        the VIN before the parser fix -- must not hijack them forever,
        which ORDER BY id did.
        """
        return self.conn.execute(
            "SELECT * FROM vehicles ORDER BY first_seen DESC, id DESC LIMIT 1"
        ).fetchone()

    # -- tx log (safety manifest) --------------------------------------------
    def log_tx(self, ts: str | None = None, **kw) -> None:
        self.conn.execute(
            "INSERT INTO tx_log(ts, direction, ecu, payload, purpose) "
            "VALUES (?,?,?,?,?)",
            (ts or utcnow(), kw.get("direction"), kw.get("ecu"),
             kw.get("payload"), kw.get("purpose")),
        )
        self.conn.commit()

    # -- measurements ---------------------------------------------------------
    def record_measurement(self, vehicle_id: int, ts: str, key: str, ecu: str,
                           service: str, pid: str, unit: str, provenance: str,
                           decoding_method: str, doc_status: str,
                           raw_response: str, value=None, text_value=None,
                           success: bool = True, error: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO measurements(vehicle_id, ts, ecu, service, pid, key, "
            "value, text_value, unit, provenance, decoding_method, doc_status, "
            "raw_response, success, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, ecu, service, pid, key, value, text_value, unit,
             provenance, decoding_method, doc_status, raw_response,
             int(success), error),
        )
        self.conn.commit()
        return cur.lastrowid

    def measurement_series(self, vehicle_id: int, key: str, days: int = 30):
        """(ts, value) rows for one measurement key, successful reads only,
        oldest first.

        For charting and anomaly-scanning the non-battery signals (12 V system
        voltage, vehicle speed) that live in `measurements` rather than in the
        battery snapshots.

        This method was previously defined TWICE in this class -- once as
        (key, since, vehicle_id) and once as (vehicle_id, key, days). Python
        keeps the last definition, so analysis/anomaly.py, which used the
        first form, was silently binding its metric name to `vehicle_id` and
        its ISO timestamp to `key`. Every query it issued matched no rows and
        non-battery anomaly detection reported zero findings, always, with no
        error. There is now exactly one definition; the (key, since, ...)
        form has no callers left.
        """
        return self.conn.execute(
            "SELECT ts, value FROM measurements WHERE vehicle_id=? AND key=? "
            "AND success=1 AND value IS NOT NULL AND ts>=? ORDER BY ts",
            (vehicle_id, key, _iso_days_ago(days)),
        ).fetchall()

    def latest_measurements(self, vehicle_id: int,
                            max_age_s: float | None = None) -> dict:
        """Most recent successful value per key.

        `max_age_s` bounds how old a cached value may be and still be returned.
        Without it the result is "the last value that ever succeeded", which
        can be weeks old -- a caller that fills gaps from this will stamp a
        stale reading with the current timestamp and mix epochs in one row.
        Timestamps are all produced by utcnow() (fixed +00:00 offset, second
        resolution), so the lexicographic comparison below is chronological.
        """
        # "Latest" is MAX(ts), not MAX(id). id is insertion order, which only
        # matches time for rows written by a single forward-moving collector.
        # After a backfill -- tools/seed_demo_data.py writes 30 days of history,
        # and any import or manual insert does the same -- the highest id is the
        # OLDEST row, and this returned that as "latest". The collector's own
        # call passes max_age_s so the age clause hid it, but
        # analysis/battery.py calls this with no bound and ships the result to
        # the LLM as latest_raw.
        #
        # ts alone can tie, so order by ts DESC then id DESC: on a tie the
        # later-inserted row is the one that most recently confirmed the value.
        if max_age_s is None:
            cutoff = None
            inner_age = ""
            args: tuple = (vehicle_id,)
        else:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(seconds=max_age_s)).isoformat(
                          timespec="seconds")
            inner_age = "AND m2.ts >= ?"
            args = (vehicle_id, cutoff, cutoff)
        rows = self.conn.execute(
            "SELECT key, value, text_value, unit, provenance, ts, doc_status "
            "FROM measurements m "
            "WHERE vehicle_id=? AND success=1 "
            + ("" if cutoff is None else "AND m.ts >= ? ")
            + "AND id = (SELECT m2.id FROM measurements m2 "
            "             WHERE m2.vehicle_id = m.vehicle_id "
            "               AND m2.key = m.key AND m2.success=1 "
            + inner_age +
            "             ORDER BY m2.ts DESC, m2.id DESC LIMIT 1)",
            args,
        ).fetchall()
        return {r["key"]: dict(r) for r in rows}

    # -- battery snapshots ------------------------------------------------------
    def record_battery_snapshot(self, vehicle_id: int, ts: str, **cols) -> None:
        fields = ["soc_abs_pct", "soc_normal_pct", "pack_voltage_v",
                  "pack_current_a", "pack_power_kw", "cell_min_v", "cell_max_v",
                  "cell_delta_mv", "battery_temp_c", "energy_charged_kwh",
                  "energy_used_kwh", "soh_pct", "cac_ah", "charge_mode"]
        vals = [cols.get(f) for f in fields]
        self.conn.execute(
            f"INSERT INTO battery_measurements(vehicle_id, ts, "
            f"{', '.join(fields)}) VALUES (?, ?, {', '.join('?' * len(fields))})",
            [vehicle_id, ts] + vals,
        )
        self.conn.commit()

    def battery_history(self, days: int = 30, vehicle_id: int | None = None):
        since = _iso_days_ago(days)
        sql = "SELECT * FROM battery_measurements WHERE ts>=?"
        args: list = [since]
        if vehicle_id:
            sql += " AND vehicle_id=?"
            args.append(vehicle_id)
        sql += " ORDER BY ts"
        return self.conn.execute(sql, args).fetchall()

    def backup(self, dest: str | Path) -> Path:
        """Write a consistent snapshot of the database to `dest`.

        Uses SQLite's online backup API rather than copying the file. A plain
        file copy of a WAL database is not a backup: any write committed to
        the -wal sidecar but not yet checkpointed is missing from the copy, so
        a crash right after a collection cycle -- exactly when a backup is
        most wanted -- yields a file that is silently missing recent data. The
        backup API reads through the WAL and holds a read lock for the
        duration, so the snapshot is a single consistent point in time even
        while the collector thread is still writing to the same database.

        The parent directory is created if missing; an existing file at `dest`
        is overwritten (SQLite truncates it first).
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        return dest

    def vacuum(self) -> None:
        """Reclaim space after pruning. Needs no open transaction.

        VACUUM rewrites the whole database into the WAL, so without a
        truncating checkpoint the file on disk appears to have grown -- the
        new copy sits beside the old pages until the WAL is folded back in.
        """
        # Discard rather than commit any transaction left open by a caller.
        # sqlite3 opens a transaction implicitly on the first INSERT/UPDATE/
        # DELETE and, crucially, a FAILED statement does not close it. Setting
        # isolation_level=None below is a commit() in disguise, so a failed
        # prune() left deletions 1..k-1 pending and this call silently
        # persisted them -- half a prune, with nothing in the returned dict to
        # show for it. There was no rollback() anywhere in the codebase. If
        # some caller still holds pending work, it is incomplete by definition.
        if self.conn.in_transaction:
            self.conn.rollback()
        self.conn.isolation_level = None
        try:
            self.conn.execute("VACUUM")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self.conn.isolation_level = ""

    def prune(self, days: float = 90.0, keep_raw: bool = True) -> dict:
        """Trim history, and report what it removed.

        An always-on collector appends a few hundred measurement rows per
        minute, so an unattended database grows without bound -- enough to
        exhaust the tablet's storage, and enough rows that the latest-value
        lookups slow down noticeably.

        keep_raw (the default) drops only the verbatim vehicle bytes on rows
        older than `days`, keeping every parsed value, so trends, reports and
        the dashboard are unaffected and the evidence trail stays in the
        numeric columns. Set it False to delete the rows themselves, which is
        the only way to reclaim the space promptly without leaving a high-water
        mark. Pass 0 to prune everything.

        Nothing is deleted automatically: pruning is destructive, so the caller
        decides when. Anything still inside the window is left alone, including
        rows belonging to an in-progress charging session.

        tx_log is NOT pruned, in either mode. It is the safety manifest -- the
        record of every request this tool ever put on the vehicle's diagnostic
        port, and the only evidence that the read-only guarantee held. Deleting
        it would destroy the audit trail that exists precisely to be kept, and
        `--hard` used to do exactly that while main.py's own summary line went
        on claiming that only "sessions, DTCs and reports" were protected.

        That leaves tx_log the largest table in the database, which is the
        honest cost of the decision rather than a reason to reverse it.
        diagnostic/connection.py logs one TX row and one RX row per request
        while each DID read yields exactly one measurement row, so the manifest
        is structurally ~2x the measurement stream: measured at 2.02x the rows
        and 1.79x the bytes over 12 collector cycles, i.e. roughly 861k rows a
        day at the default 5 s interval. Reclaiming it is the operator's call
        via `backup` and a manual archive, not something --hard may do for
        them.
        """
        cutoff = _iso_days_ago(days)
        # Every table below carries a `ts` column holding an ISO-8601 UTC stamp,
        # so one cutoff applies to all of them. Sessions, DTCs, vehicles and ECUs
        # are deliberately absent: they are small and are the durable record.
        # llm_reports is kept too -- it is the user's own generated history --
        # and so is tx_log, which is the safety manifest.
        tables = ("measurements", "battery_measurements", "cell_voltages",
                  "charging_samples", "anomalies", "diagnostic_events",
                  "analysis_results")
        removed = {t: 0 for t in tables}
        removed["measurements_raw"] = 0
        removed["cutoff"] = cutoff
        removed["keep_raw"] = keep_raw
        # Reported so the CLI can state plainly that the safety manifest was
        # left intact rather than silently omitting it from the totals.
        removed["tx_log_kept"] = True
        # All-or-nothing. The connection is left at sqlite3's default
        # isolation_level="", which opens a transaction implicitly on the first
        # write but does NOT close it when a statement raises -- so a failure
        # partway through the DELETE loop left the earlier tables' rows deleted
        # and pending. Any later commit() on this connection (an unrelated
        # log_tx(), or the vacuum() that main.py runs straight afterwards)
        # persisted that half-prune, and the dict returned to the caller was
        # never produced at all. Pruning destroys data, so it either completes
        # or leaves nothing behind.
        try:
            if keep_raw:
                cur = self.conn.execute(
                    "UPDATE measurements SET raw_response=NULL "
                    "WHERE ts < ? AND raw_response IS NOT NULL", (cutoff,))
                removed["measurements_raw"] = cur.rowcount
            else:
                for table in tables:
                    cur = self.conn.execute(f"DELETE FROM {table} WHERE ts < ?",
                                            (cutoff,))
                    removed[table] = cur.rowcount
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return removed

    def _table_exists(self, name: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None

    def record_cell_voltages(self, vehicle_id: int, ts: str, voltages: list[float],
                         cell_numbers: list[int] | None = None) -> None:
        """Persist one row per cell.

        `cell_numbers` maps each voltage to the physical cell it was read from.
        It is required whenever any cell failed to answer: cells outside the
        real pack return NRC 0x31 and are skipped, so without the mapping
        cell_index would be the position in the surviving list and a given
        cell_index would name a different physical cell on each sweep --
        turning a per-cell trend into comparisons between different cells.
        """
        if cell_numbers is None:
            cell_numbers = list(range(len(voltages)))
        if len(cell_numbers) != len(voltages):
            raise ValueError("cell_numbers must align with voltages")
        self.conn.executemany(
            "INSERT INTO cell_voltages(vehicle_id, ts, cell_index, voltage_v) "
            "VALUES (?,?,?,?)",
            [(vehicle_id, ts, n, v) for n, v in zip(cell_numbers, voltages)],
        )
        self.conn.commit()

    # -- charging sessions -------------------------------------------------------
    def open_session(self, vehicle_id: int, ts: str, charge_type: str,
                     start_soc=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO charging_sessions(vehicle_id, charge_type, started_at, "
            "start_soc, status) VALUES (?,?,?,?, 'open')",
            (vehicle_id, charge_type, ts, start_soc),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_charging_sample(self, session_id: int, ts: str, voltage_v=None,
                            current_a=None, power_kw=None, soc=None,
                            battery_temp_c=None) -> None:
        self.conn.execute(
            "INSERT INTO charging_samples(session_id, ts, voltage_v, current_a, "
            "power_kw, soc, battery_temp_c) VALUES (?,?,?,?,?,?,?)",
            (session_id, ts, voltage_v, current_a, power_kw, soc, battery_temp_c),
        )
        self.conn.commit()

    def close_session(self, session_id: int, ts: str, end_soc=None) -> None:
        sess = self.conn.execute(
            "SELECT * FROM charging_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if sess is None:
            # Subscripting None raised TypeError, which escaped the collector and
            # killed the run. Closing a session that does not exist is a no-op.
            log.warning("close_session: no session with id %s", session_id)
            return
        samples = self.conn.execute(
            "SELECT ts, power_kw, battery_temp_c FROM charging_samples "
            "WHERE session_id=? ORDER BY ts", (session_id,)
        ).fetchall()
        powers = [s["power_kw"] for s in samples if s["power_kw"] is not None]
        temps = [s["battery_temp_c"] for s in samples
                 if s["battery_temp_c"] is not None]
        duration = (_parse_ts(ts) - _parse_ts(sess["started_at"])).total_seconds()
        # A clock correction can put the end before the start. Persisting that
        # would store a negative duration and a negative energy for ever.
        duration = max(0.0, duration)
        # Integrate power over time to get kWh. Summing the samples and scaling
        # by the duration is dimensionally wrong -- it multiplies by the number
        # of samples, so a 12-sample session reported ~12x the energy actually
        # delivered. Trapezoidal over the real sample timestamps, so uneven
        # sampling does not distort the total.
        pts = [(_parse_ts(s["ts"]), s["power_kw"]) for s in samples
               if s["power_kw"] is not None]
        energy = None
        if len(pts) >= 2 and duration > 0:
            energy = 0.0
            for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
                span_h = (t1 - t0).total_seconds() / 3600.0
                if span_h > 0:
                    energy += (p0 + p1) / 2.0 * span_h
        elif pts and duration > 0:
            energy = pts[0][1] * duration / 3600.0
        self.conn.execute(
            "UPDATE charging_sessions SET ended_at=?, end_soc=?, duration_s=?, "
            "energy_estimate_kwh=?, max_power_kw=?, avg_battery_temp_c=?, "
            "status='completed' WHERE id=?",
            (ts, end_soc, duration, energy,
             max(powers) if powers else None,
             sum(temps) / len(temps) if temps else None, session_id),
        )
        self.conn.commit()

    def open_session_id(self, vehicle_id: int):
        row = self.conn.execute(
            "SELECT id FROM charging_sessions WHERE vehicle_id=? AND status='open' "
            "ORDER BY started_at DESC LIMIT 1", (vehicle_id,)
        ).fetchone()
        return row["id"] if row else None

    def charging_sessions(self, days: int = 90, vehicle_id: int | None = None):
        sql = ("SELECT * FROM charging_sessions WHERE status='completed' "
               "AND started_at>=?")
        args: list = [_iso_days_ago(days)]
        if vehicle_id:
            sql += " AND vehicle_id=?"
            args.append(vehicle_id)
        sql += " ORDER BY started_at"
        return self.conn.execute(sql, args).fetchall()

    # -- DTCs ---------------------------------------------------------------
    def upsert_dtc(self, vehicle_id: int, ts: str, ecu: str, code: str,
                   description: str, categories: list[str],
                   status: str = "unknown",
                   freeze_frame: dict | None = None,
                   rearm_s: float = 60.0) -> int:
        row = self.conn.execute(
            "SELECT id, last_seen FROM dtcs WHERE vehicle_id=? AND ecu=? AND code=?",
            (vehicle_id, ecu, code),
        ).fetchone()
        if row:
            # A stored DTC stays readable long after the fault clears, so the
            # poll that reads it sees it on every cycle. Incrementing each time
            # made occurrence_count a count of polls, not of occurrences: at a
            # 5 s interval one continuous fault reported thousands of
            # "occurrences". Only count it again once it has been absent long
            # enough to count as a genuinely new sighting.
            #
            # NOTE: this gate cannot fire in practice. last_seen is refreshed
            # on every poll, so the gap never exceeds one poll interval, and
            # nothing in the collector ever reports a DTC as cleared (the
            # 0x14 clear service is blocked by design, so there is no way to
            # observe the transition). It is kept because it is correct if a
            # caller ever does report absence, but the occurrence row below
            # must NOT depend on it alone -- see the freeze-frame check.
            try:
                gap = (_parse_ts(ts) - _parse_ts(row["last_seen"])).total_seconds()
            except (TypeError, ValueError):
                gap = rearm_s + 1.0
            increment = gap >= rearm_s
            self.conn.execute(
                "UPDATE dtcs SET last_seen=?, occurrence_count="
                "occurrence_count+?, status=? WHERE id=?",
                (ts, 1 if increment else 0, status, row["id"]),
            )
            dtc_id = row["id"]
        else:
            # Same reasoning as ensure_vehicle: two threads can both find no
            # row, and the loser would die on UNIQUE(vehicle_id, ecu, code).
            self.conn.execute(
                "INSERT INTO dtcs(vehicle_id, ecu, code, description, "
                "categories, status, first_seen, last_seen, occurrence_count) "
                "VALUES (?,?,?,?,?,?,?,?,'1') "
                "ON CONFLICT(vehicle_id, ecu, code) DO NOTHING",
                (vehicle_id, ecu, code, description,
                 json.dumps(categories), status, ts, ts),
            )
            dtc_id = self.conn.execute(
                "SELECT id FROM dtcs WHERE vehicle_id=? AND ecu=? AND code=?",
                (vehicle_id, ecu, code)).fetchone()["id"]
            increment = True

        # Write an occurrence row only when the sighting carries new evidence.
        # It used to be written unconditionally, once per poll: a single stored
        # DTC produced ~17,000 rows a day at the configured 5 s interval, each
        # with a near-identical freeze frame, and dtc_occurrences is absent
        # from prune() so none of it could ever be reclaimed. latest_freeze_
        # frames() scanned the whole table on every /dtcs page, /charging page
        # and AI question, growing without bound.
        #
        # "New evidence" means a genuine re-arm, or a freeze frame that differs
        # from the last one stored for this DTC. A continuously-present fault
        # whose snapshot has not moved now costs exactly one row.
        if increment or freeze_frame:
            if not increment:
                previous = self.conn.execute(
                    "SELECT freeze_frame FROM dtc_occurrences WHERE dtc_id=? "
                    "ORDER BY id DESC LIMIT 1", (dtc_id,)
                ).fetchone()
                unchanged = False
                if previous is not None and previous["freeze_frame"]:
                    try:
                        unchanged = json.loads(previous["freeze_frame"]) == freeze_frame
                    except ValueError:
                        unchanged = False   # unparseable: treat as changed
                else:
                    unchanged = previous is not None and not freeze_frame
                increment = not unchanged
            if increment:
                self.conn.execute(
                    "INSERT INTO dtc_occurrences(dtc_id, ts, freeze_frame) "
                    "VALUES (?,?,?)",
                    (dtc_id, ts, json.dumps(freeze_frame) if freeze_frame else None),
                )
        self.conn.commit()
        return dtc_id

    def dtc_list(self, vehicle_id: int):
        return self.conn.execute(
            "SELECT * FROM dtcs WHERE vehicle_id=? ORDER BY last_seen DESC",
            (vehicle_id,),
        ).fetchall()

    def latest_freeze_frames(self, vehicle_id: int) -> dict:
        """Most recent non-empty freeze-frame JSON per (ecu, code).

        Freeze frames live on dtc_occurrences (one per sighting); the
        newest one carries the latest snapshot the ECU reported for that
        DTC, which is what the dashboard and the LLM context want.
        """
        rows = self.conn.execute(
            "SELECT d.ecu, d.code, o.freeze_frame FROM dtc_occurrences o "
            "JOIN dtcs d ON d.id=o.dtc_id "
            "WHERE d.vehicle_id=? AND o.freeze_frame IS NOT NULL "
            "ORDER BY o.ts ASC",
            (vehicle_id,),
        ).fetchall()
        out: dict = {}
        for r in rows:
            try:
                parsed = json.loads(r["freeze_frame"])
            except (TypeError, ValueError):
                continue
            if parsed:
                out[(r["ecu"], r["code"])] = parsed  # ASC order: last wins
        return out

    def dtc_occurrences(self, vehicle_id: int, since: str | None = None):
        sql = ("SELECT o.*, d.code, d.ecu, d.categories FROM dtc_occurrences o "
               "JOIN dtcs d ON d.id=o.dtc_id WHERE d.vehicle_id=?")
        args: list = [vehicle_id]
        if since:
            sql += " AND o.ts>=?"
            args.append(since)
        sql += " ORDER BY o.ts"
        return self.conn.execute(sql, args).fetchall()

    # -- events / analysis / reports / anomalies -------------------------------
    def add_event(self, vehicle_id: int, ts: str, kind: str, description: str,
                  ecu=None, data: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO diagnostic_events(vehicle_id, ts, kind, ecu, "
            "description, data) VALUES (?,?,?,?,?,?)",
            (vehicle_id, ts, kind, ecu, description,
             json.dumps(data) if data else None),
        )
        self.conn.commit()

    def add_analysis(self, vehicle_id: int, analysis_type: str, subject: str,
                     window_days: int, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO analysis_results(vehicle_id, ts, analysis_type, "
            "subject, window_days, result_json) VALUES (?,?,?,?,?,?)",
            (vehicle_id, utcnow(), analysis_type, subject, window_days,
             json.dumps(result, default=str)),
        )
        self.conn.commit()

    def add_llm_report(self, vehicle_id: int, question: str, model: str,
                       report_text: str, context: dict, warnings: list) -> None:
        self.conn.execute(
            "INSERT INTO llm_reports(vehicle_id, ts, question, model, "
            "report_text, context_json, warnings) VALUES (?,?,?,?,?,?,?)",
            (vehicle_id, utcnow(), question, model, report_text,
             json.dumps(context, default=str), json.dumps(warnings)),
        )
        self.conn.commit()

    def llm_reports(self, vehicle_id: int, limit: int = 20):
        return self.conn.execute(
            "SELECT * FROM llm_reports WHERE vehicle_id=? ORDER BY ts DESC "
            "LIMIT ?", (vehicle_id, limit),
        ).fetchall()

    def add_anomaly(self, vehicle_id: int, ts: str, metric: str, value: float,
                    mean: float, std: float, z: float, direction: str,
                    description: str) -> None:
        # Scans re-read the whole window, so the same (ts, metric) is offered
        # again every time the analysis runs -- on a dashboard refresh, for
        # example. Inserting unconditionally duplicated rows until the table was
        # mostly copies. A given metric can only be anomalous at a given
        # timestamp, so upsert on that key and let a rescan refresh the numbers.
        #
        # UPDATE first, then INSERT ... WHERE NOT EXISTS, rather than SELECT
        # then branch. The previous form was a check-then-write: with
        # sqlite3's default isolation_level="", a SELECT runs in autocommit and
        # only the following write opens a transaction, so the gap between them
        # is unprotected. The anomaly scan fans out over metrics in a thread
        # pool, so two threads working the same (ts, metric) both saw "no
        # existing row" and both inserted. The two statements below are each
        # atomic on their own, and SQLite serialises writers at the statement
        # level, so exactly one thread's INSERT sees the row the other just
        # wrote. This also needs no schema change, so it does not put existing
        # databases at risk of a migration failure.
        self.conn.execute(
            "UPDATE anomalies SET value=?, baseline_mean=?, baseline_std=?, "
            "zscore=?, direction=?, description=? "
            "WHERE vehicle_id=? AND ts=? AND metric=?",
            (value, mean, std, z, direction, description, vehicle_id, ts, metric),
        )
        self.conn.execute(
            "INSERT INTO anomalies(vehicle_id, ts, metric, value, "
            "baseline_mean, baseline_std, zscore, direction, description) "
            "SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS "
            "(SELECT 1 FROM anomalies WHERE vehicle_id=? AND ts=? AND metric=?)",
            (vehicle_id, ts, metric, value, mean, std, z, direction,
             description, vehicle_id, ts, metric),
        )
        self.conn.commit()

    def anomalies(self, vehicle_id: int, days: int = 30):
        return self.conn.execute(
            "SELECT * FROM anomalies WHERE vehicle_id=? AND ts>=? ORDER BY ts",
            (vehicle_id, _iso_days_ago(days)),
        ).fetchall()



