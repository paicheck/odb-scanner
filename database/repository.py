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

# Columns added to pre-existing tables after those tables first shipped.
# CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a database
# created by an earlier build silently keeps the old column set -- the model and
# the file then disagree, and the failure surfaces as "no such column" at the
# first write. These are additive ALTERs (no data loss) and are idempotent.
_ADDITIVE_COLUMNS = (
    # The real data/odb_scanner.db had investigation_evidence without it.
    ("investigation_evidence", "certain", "INTEGER NOT NULL DEFAULT 1"),
)


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDITIVE_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            log.info("schema: added %s.%s", table, column)

# Rows whose provenance starts with this were NOT measured by this tool. The
# canonical list lives here because the data layer owns the provenance
# vocabulary; analysis/provenance.py imports it rather than restating it, so
# the two cannot drift into disagreeing about what counts as imported.
IMPORTED_PREFIX = "imported"


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


def _can(text: str | None) -> int | None:
    """'0x710' -> 0x710. The catalogue formats ids for humans; the DB stores ints
    so it can index and compare them numerically."""
    if text is None:
        return None
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def _svc(text: str | None) -> int | None:
    return _can(text)


def _did(text: str | None) -> int | None:
    return _can(text)


class _RollbackOnErrorConnection(sqlite3.Connection):
    """A connection that does not keep a failed statement's transaction open.

    sqlite3 opens a transaction implicitly before the first write and keeps it
    until commit() or rollback(). When a write raised -- a foreign-key failure
    on POST /investigations/<missing id>/observe was enough -- nothing rolled it
    back, so that thread's connection kept the database's write lock and every
    other writer, the collector included, got "database is locked" until that
    same thread happened to commit something unrelated (which would then also
    have committed the failed method's partial work). A statement that raises
    now rolls back the open transaction before the error propagates. Nothing
    in this codebase catches a SQLite error and carries on inside the same
    transaction, so no caller relied on the old behaviour.
    """

    def _rollback_pending(self) -> None:
        if self.in_transaction:
            try:
                self.rollback()
            except sqlite3.Error:  # pragma: no cover - the original error wins
                pass

    def execute(self, *args, **kwargs):
        try:
            return super().execute(*args, **kwargs)
        except sqlite3.Error:
            self._rollback_pending()
            raise

    def executemany(self, *args, **kwargs):
        try:
            return super().executemany(*args, **kwargs)
        except sqlite3.Error:
            self._rollback_pending()
            raise


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
        _apply_additive_migrations(self.conn)
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
                                   timeout=30.0,
                                   factory=_RollbackOnErrorConnection)
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

    # -- definition catalogue ------------------------------------------
    # Additive: these tables exist alongside `measurements`, which keeps
    # observations. Nothing here alters an existing table.

    def sync_definitions(self, definitions, ts: str | None = None) -> dict[str, str]:
        """Insert/update catalogue rows; return {key: version} as stored.

        Re-syncing is idempotent: UNIQUE(key, version) plus INSERT ... ON
        CONFLICT DO UPDATE means a repeat run does not duplicate rows, and a
        definition whose content is unchanged keeps its version and therefore
        keeps its `first_seen`. Only a definition whose digest changed is
        retired (active=0) and superseded, so the table reads as a history.
        """
        stamp = ts or utcnow()
        import json as _json

        # One column list, built once and used for both the column list and the
        # placeholder count. A hand-counted "?" list drifted out of step with
        # the schema and raised "39 values for 41 columns"; deriving both from
        # the same list makes that impossible.
        cols = ("key, version, name, unit, confidence, ecu_key, ecu_name, "
                "request_can_id, response_can_id, address_confidence, transport, "
                "session_required, service, did, request_bytes, byte_offset, "
                "byte_length, byte_order, signed, scale, divisor, addend, formula, "
                "response_length_hint, plausible_min, plausible_max, "
                "plausible_basis, provenance, doc_status, evidence_kind, "
                "evidence_source, evidence_artifact, recorded_at, "
                "validation_method, limitations, falsified_by, "
                "competing_interpretations, definition_json, first_seen, "
                "last_seen, active").split(", ")
        ncols = len(cols)
        # Everything except the identity triple is refreshed on conflict, so a
        # corrected definition updates in place and keeps its id. `first_seen`
        # is deliberately excluded: it records when this version first
        # appeared, and must survive a re-sync. `last_seen` IS refreshed, or a
        # repeated sync would leave a stale "last seen" that never advances.
        refreshed = [c for c in cols if c not in ("key", "version", "first_seen")]
        conflict = ", ".join(f"{c}=excluded.{c}" for c in refreshed)

        seen: dict[str, str] = {}
        for d in definitions:
            row = d.as_dict()
            payload = _json.dumps(row, sort_keys=True, ensure_ascii=False)
            values = {
                "key": row["key"], "version": d.version, "name": row["name"],
                "unit": row["unit"], "confidence": row["confidence"],
                "ecu_key": row["ecu_key"], "ecu_name": row["ecu_name"],
                "request_can_id": _can(row["request_can_id"]),
                "response_can_id": _can(row["response_can_id"]),
                "address_confidence": row["address_confidence"],
                "transport": row["transport"],
                "session_required": row["session_required"],
                "service": _can(row["service"]), "did": _can(row["did"]),
                "request_bytes": row["request_bytes"],
                "byte_offset": row["byte_offset"],
                "byte_length": row["byte_length"], "byte_order": row["byte_order"],
                "signed": None if row["signed"] is None else int(row["signed"]),
                "scale": row["scale"], "divisor": row["divisor"],
                "addend": row["addend"], "formula": row["formula"],
                "response_length_hint": row["response_length_hint"],
                "plausible_min": row["plausible_min"],
                "plausible_max": row["plausible_max"],
                "plausible_basis": row["plausible_basis"],
                "provenance": row["provenance"], "doc_status": row["doc_status"],
                "evidence_kind": row["evidence_kind"],
                "evidence_source": row["evidence_source"],
                "evidence_artifact": row["evidence_artifact"],
                "recorded_at": row["recorded_at"],
                "validation_method": row["validation_method"],
                "limitations": row["limitations"],
                "falsified_by": row["falsified_by"],
                "competing_interpretations": row["competing_interpretations"],
                "definition_json": payload,
                "first_seen": stamp, "last_seen": stamp, "active": 1,
            }
            missing = set(cols) - set(values)
            if missing:
                raise KeyError(f"definition {row['key']}: no value for {missing}")
            self.conn.execute(
                f"INSERT INTO measurement_definitions({', '.join(cols)}) "
                f"VALUES ({', '.join(['?'] * ncols)}) "
                f"ON CONFLICT(key, version) DO UPDATE SET {conflict}",
                [values[c] for c in cols])
            seen[row["key"]] = d.version
        # retire superseded versions of keys we just synced
        for key, version in seen.items():
            self.conn.execute(
                "UPDATE measurement_definitions SET active=0 WHERE key=? AND "
                "version<>?", (key, version))
        self.conn.commit()
        return seen

    def record_evidence(self, key: str, version: str, observation: str,
                        kind: str, ts: str | None = None,
                        confidence_before: str | None = None,
                        confidence_after: str | None = None,
                        detail: str | None = None) -> int:
        row = self.conn.execute(
            "SELECT id FROM measurement_definitions WHERE key=? AND version=?",
            (key, version)).fetchone()
        if row is None:
            raise KeyError(f"no definition {key}@{version}; sync first")
        cur = self.conn.execute(
            "INSERT INTO definition_evidence(definition_id, ts, observation, kind,"
            " confidence_before, confidence_after, detail) VALUES (?,?,?,?,?,?,?)",
            (row["id"], ts or utcnow(), observation, kind, confidence_before,
             confidence_after, detail))
        self.conn.commit()
        return cur.lastrowid

    def record_evaluation(self, key: str, version: str, method: str, verdict: str,
                          n_samples: int, detail: str = "",
                          ts: str | None = None) -> int:
        row = self.conn.execute(
            "SELECT id FROM measurement_definitions WHERE key=? AND version=?",
            (key, version)).fetchone()
        if row is None:
            raise KeyError(f"no definition {key}@{version}; sync first")
        cur = self.conn.execute(
            "INSERT INTO definition_evaluations(definition_id, ts, method, verdict,"
            " n_samples, detail) VALUES (?,?,?,?,?,?)",
            (row["id"], ts or utcnow(), method, verdict, n_samples, detail))
        self.conn.commit()
        return cur.lastrowid

    def definition(self, key: str, active_only: bool = True):
        sql = "SELECT * FROM measurement_definitions WHERE key=?"
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY recorded_at DESC, id DESC LIMIT 1"
        return self.conn.execute(sql, (key,)).fetchone()

    def definitions(self, active_only: bool = True,
                    confidence: str | None = None) -> list:
        sql = "SELECT * FROM measurement_definitions WHERE 1=1"
        args: list = []
        if active_only:
            sql += " AND active=1"
        if confidence:
            sql += " AND confidence=?"
            args.append(confidence)
        sql += " ORDER BY key"
        return self.conn.execute(sql, args).fetchall()

    def definition_history(self, key: str) -> list:
        return self.conn.execute(
            "SELECT version, confidence, recorded_at, active FROM "
            "measurement_definitions WHERE key=? ORDER BY recorded_at, id",
            (key,)).fetchall()

    def definition_evidence(self, key: str) -> list:
        return self.conn.execute(
            "SELECT e.ts, e.observation, e.kind, e.confidence_before,"
            " e.confidence_after, e.detail FROM definition_evidence e"
            " JOIN measurement_definitions d ON d.id=e.definition_id"
            " WHERE d.key=? ORDER BY e.ts, e.id", (key,)).fetchall()

    def record_frame(self, vehicle_id: int | None, ts: str, ecu: str,
                     request_bytes: str, response_raw_lines: str | None = None,
                     response_payload: str | None = None,
                     did_record: str | None = None,
                     answer_source_id: int | None = None,
                     session_state: str | None = None, succeeded: bool = False,
                     error: str | None = None, note: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO captured_frames(vehicle_id, ts, ecu, request_bytes,"
            " response_raw_lines, response_payload, did_record, answer_source_id,"
            " session_state, succeeded, error, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, ecu, request_bytes, response_raw_lines,
             response_payload, did_record, answer_source_id, session_state,
             int(succeeded), error, note))
        self.conn.commit()
        return cur.lastrowid

    def frames(self, ecu: str | None = None, did_record: str | None = None,
               limit: int = 1000) -> list:
        sql = "SELECT * FROM captured_frames WHERE 1=1"
        args: list = []
        if ecu:
            sql += " AND ecu=?"
            args.append(ecu)
        if did_record:
            sql += " AND did_record=?"
            args.append(did_record)
        sql += " ORDER BY ts, id LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

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

        Ordered by (ts, id) so rows sharing a timestamp come back in the same
        order every time; `provenance` travels with each row so a chart can
        tell an imported reading from a live one.
        """
        return self.conn.execute(
            "SELECT ts, value, provenance FROM measurements "
            "WHERE vehicle_id=? AND key=? "
            "AND success=1 AND value IS NOT NULL AND ts>=? ORDER BY ts, id",
            (vehicle_id, key, _iso_days_ago(days)),
        ).fetchall()

    def measurement_series_many(self, vehicle_id: int, keys: list[str],
                                days: int = 30):
        """`measurement_series` for several keys in ONE query.

        A chart page with six panels used to cost six round trips; this keeps
        it to one, still bounded by the time window. Rows are ordered by
        (key, ts, id).
        """
        if not keys:
            return []
        marks = ",".join("?" for _ in keys)
        return self.conn.execute(
            "SELECT key, ts, value, provenance FROM measurements "
            f"WHERE vehicle_id=? AND key IN ({marks}) AND success=1 "
            "AND value IS NOT NULL AND ts>=? ORDER BY key, ts, id",
            (vehicle_id, *keys, _iso_days_ago(days)),
        ).fetchall()

    def recent_units(self, key: str, limit: int = 200) -> list[str]:
        """Distinct non-empty units on the newest `limit` rows of a key.

        Used only as a fallback when the catalogue has no unit for a key (the
        OBD-II PID keys), and to report a disagreement between the stored rows
        and the catalogue. Bounded so it never scans a whole history.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT unit FROM (SELECT unit FROM measurements "
            "WHERE key=? ORDER BY ts DESC LIMIT ?) "
            "WHERE unit IS NOT NULL AND unit != ''",
            (key, limit)).fetchall()
        return sorted(r["unit"] for r in rows)

    def cell_voltage_rows(self, vehicle_id: int, days: int = 30):
        """Every per-cell reading in the window, ordered by (ts, cell_index)."""
        return self.conn.execute(
            "SELECT ts, cell_index, voltage_v FROM cell_voltages "
            "WHERE vehicle_id=? AND ts>=? ORDER BY ts, cell_index",
            (vehicle_id, _iso_days_ago(days)),
        ).fetchall()

    def raw_measurement_series(self, vehicle_id: int, key: str, days: int = 30):
        """Every stored column for one key, oldest first, failures included.

        This backs the evidence export, which must not silently drop rows the
        way a chart does: unlike `measurement_series` it keeps failed reads,
        text decodes and the raw response bytes. `success` travels with each
        row so a reader can filter for themselves.
        """
        return self.conn.execute(
            "SELECT ts, ecu, service, pid, key, value, text_value, unit, "
            "provenance, decoding_method, doc_status, raw_response, success, "
            "error FROM measurements WHERE vehicle_id=? AND key=? AND ts>=? "
            "ORDER BY ts, id",
            (vehicle_id, key, _iso_days_ago(days)),
        ).fetchall()

    def raw_cell_series(self, vehicle_id: int, cell_index: int, days: int = 30):
        """Every stored column for one physical cell, oldest first."""
        return self.conn.execute(
            "SELECT ts, cell_index, voltage_v FROM cell_voltages "
            "WHERE vehicle_id=? AND cell_index=? AND ts>=? ORDER BY ts",
            (vehicle_id, cell_index, _iso_days_ago(days)),
        ).fetchall()

    def latest_measurements(self, vehicle_id: int,
                            max_age_s: float | None = None,
                            live_only: bool = False) -> dict:
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
        else:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(seconds=max_age_s)).isoformat(
                          timespec="seconds")
            inner_age = "AND m2.ts >= ?"
        # Carried-forward values feed derived numbers -- pack power, SoH,
        # charging detection -- so a row this tool did not measure must
        # never stand in for one it did. An imported Car Scanner export is
        # evidence about the car, not a reading of it by us, and the age
        # bound alone does not exclude it: a re-import of a session taken
        # an hour ago lands inside any reasonable bound and would be
        # silently re-stamped as ours.
        #
        # Both the outer row and the inner "latest" lookup must exclude it.
        # Excluding only the inner one would let an imported outer row pair
        # with a genuine inner row, mixing two different epochs in one
        # result.
        if live_only:
            outer_prov = "AND m.provenance NOT LIKE ? "
            inner_prov = "AND m2.provenance NOT LIKE ? "
            prov_arg: str | None = IMPORTED_PREFIX + "%"
        else:
            outer_prov = ""
            inner_prov = ""
            prov_arg = None
        # Arg order must follow the clause order in the query below exactly:
        # vehicle_id, [outer cutoff], [outer prov], [inner cutoff], [inner
        # prov]. Each optional clause contributes one binding only when present.
        args: list = [vehicle_id]
        if cutoff is not None:
            args.append(cutoff)
        if prov_arg is not None:
            args.append(prov_arg)
        if cutoff is not None:
            args.append(cutoff)
        if prov_arg is not None:
            args.append(prov_arg)
        rows = self.conn.execute(
            "SELECT key, value, text_value, unit, provenance, ts, doc_status "
            "FROM measurements m "
            "WHERE m.vehicle_id=? AND m.success=1 "
            + ("" if cutoff is None else "AND m.ts >= ? ")
            + outer_prov
            + "AND id = (SELECT m2.id FROM measurements m2 "
            "             WHERE m2.vehicle_id = m.vehicle_id "
            "               AND m2.key = m.key AND m2.success=1 "
            + inner_age + inner_prov +
            "             ORDER BY m2.ts DESC, m2.id DESC LIMIT 1)",
            tuple(args),
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
                max_id_before = self.max_measurement_id()
                for table in tables:
                    cur = self.conn.execute(f"DELETE FROM {table} WHERE ts < ?",
                                            (cutoff,))
                    removed[table] = cur.rowcount
                self.note_measurement_deletes(max_id_before)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return removed

    # -- measurement id reuse --------------------------------------------------
    # measurements.id is a plain INTEGER PRIMARY KEY (no AUTOINCREMENT, and an
    # existing table cannot gain it without a rebuild), so SQLite gives a new
    # row MAX(id)+1. Deleting the NEWEST rows therefore makes their ids come
    # back for the next readings -- and anything keyed on the local id (the
    # cloud uploader's watermark and row ids) would skip those readings or
    # merge them with the deleted ones. Deleters record that here, inside their
    # own transaction, and cloud.client acts on it before the next upload.
    ID_REUSE_KEY = "measurements.ids_reused_above"

    def max_measurement_id(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM measurements").fetchone()[0]

    def note_measurement_deletes(self, max_id_before: int) -> None:
        """Record that ids above the new MAX(id) will be handed out again.

        Call after deleting measurements, before committing; does not commit.
        """
        now_max = self.max_measurement_id()
        if now_max >= max_id_before:
            return
        row = self.conn.execute("SELECT value FROM meta WHERE key=?",
                                (self.ID_REUSE_KEY,)).fetchone()
        if row is not None:
            try:
                now_max = min(now_max, int(row["value"]))
            except (TypeError, ValueError):
                pass
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self.ID_REUSE_KEY, str(now_max)))

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

    def cell_voltage_series(self, vehicle_id: int, cell_index: int,
                            days: int = 30):
        """(ts, value) rows for one physical cell, oldest first.

        Cell voltages live in their own table (not `measurements`), because a
        single sweep writes 108 rows at once. This is the per-cell read the
        measurements page uses to chart an individual cell.
        """
        return self.conn.execute(
            "SELECT ts, voltage_v AS value FROM cell_voltages "
            "WHERE vehicle_id=? AND cell_index=? AND ts>=? ORDER BY ts",
            (vehicle_id, cell_index, _iso_days_ago(days)),
        ).fetchall()

    def latest_cell_voltages(self, vehicle_id: int) -> list[dict]:
        """One row per cell at the most recent sweep.

        'Latest' is the sweep with the highest ts (not the highest id), and
        within it every cell that answered. A cell that returned NRC on that
        sweep is simply absent, so a gap is visible rather than invented.
        """
        latest_ts = self.conn.execute(
            "SELECT MAX(ts) FROM cell_voltages WHERE vehicle_id=?",
            (vehicle_id,)).fetchone()[0]
        if latest_ts is None:
            return []
        return self.conn.execute(
            "SELECT cell_index, voltage_v, ts FROM cell_voltages "
            "WHERE vehicle_id=? AND ts=? ORDER BY cell_index",
            (vehicle_id, latest_ts),
        ).fetchall()

    # -- retired DIDs (NRC 0x31 or consistently silent) -------------------------
    def unsupported_did_keys(self, vehicle_id: int) -> set[tuple[str, int]]:
        """(ecu_key, did) pairs the collector has decided not to poll again.

        Includes both DIDs the car rejected with NRC 0x31 (definitively
        unsupported) and DIDs that have never succeeded and stayed silent
        across enough reads (see collector.py silent-DID retirement).
        """
        return {
            (r["ecu_key"], r["did"])
            for r in self.conn.execute(
                "SELECT ecu_key, did FROM unsupported_dids WHERE vehicle_id=?",
                (vehicle_id,),
            )
        }

    def mark_did_unsupported(self, vehicle_id: int, ecu_key: str, did: int,
                             ts: str) -> None:
        """Record (or bump) a DID the collector is retiring (NRC 0x31 or
        consistently-silent-never-succeeded)."""
        self.conn.execute(
            "INSERT INTO unsupported_dids(vehicle_id, ecu_key, did, first_seen, "
            "last_seen, count) VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(vehicle_id, ecu_key, did) DO UPDATE SET "
            "last_seen=excluded.last_seen, count=unsupported_dids.count+1",
            (vehicle_id, ecu_key, did, ts, ts),
        )
        self.conn.commit()

    def unsupported_dids(self, vehicle_id: int) -> list[sqlite3.Row]:
        """Retired DIDs with the evidence for the retirement, by module.

        The collector skips these. `count` is how many times the retirement was
        recorded and `first_seen`/`last_seen` when -- which is what tells a DID
        the car genuinely refuses (NRC 0x31) apart from one retired during a
        spell when the adapter could not reach its module.
        """
        return self.conn.execute(
            "SELECT ecu_key, did, first_seen, last_seen, count "
            "FROM unsupported_dids WHERE vehicle_id=? ORDER BY ecu_key, did",
            (vehicle_id,),
        ).fetchall()

    def clear_unsupported_dids(self, vehicle_id: int,
                               ecu_key: str | None = None) -> int:
        """Un-retire DIDs so the collector polls them again; returns rows removed.

        Measured history is untouched -- this only forgets a "do not poll"
        decision, so re-polling is always safe. DIDs the car really does not
        implement retire again on their own. Scoped to one module when
        `ecu_key` is given.
        """
        if ecu_key is None:
            cur = self.conn.execute(
                "DELETE FROM unsupported_dids WHERE vehicle_id=?",
                (vehicle_id,))
        else:
            cur = self.conn.execute(
                "DELETE FROM unsupported_dids WHERE vehicle_id=? AND ecu_key=?",
                (vehicle_id, ecu_key))
        self.conn.commit()
        return cur.rowcount

    def ever_succeeded_keys(self, vehicle_id: int) -> set[tuple[str, int]]:
        """(ecu_key, did) pairs that have ever produced a successful read.

        The collector uses this as the guard on the silent-DID retirement: a
        DID that has answered at least once is never retired for being silent,
        because silence is then "the car is asleep" rather than "the DID is
        dead". A DID that has NEVER answered and stays silent is the retire
        candidate.
        """
        rows = self.conn.execute(
            "SELECT ecu, pid FROM measurements WHERE vehicle_id=? AND success=1 "
            "AND service='UDS-0x22' AND pid LIKE '0x%'",
            (vehicle_id,),
        ).fetchall()
        keys: set[tuple[str, int]] = set()
        for r in rows:
            try:
                did = int(r["pid"], 16)
            except (ValueError, TypeError):
                continue
            keys.add((r["ecu"], did))
        return keys

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

    def charging_session(self, session_id: int):
        """One charging session by id, or None."""
        return self.conn.execute(
            "SELECT * FROM charging_sessions WHERE id=?", (session_id,)
        ).fetchone()

    def charging_samples(self, session_id: int):
        """A session's samples, oldest first, ties broken by insertion order."""
        return self.conn.execute(
            "SELECT ts, voltage_v, current_a, power_kw, soc, battery_temp_c "
            "FROM charging_samples WHERE session_id=? ORDER BY ts, id",
            (session_id,)).fetchall()

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

    def dtc(self, dtc_id: int):
        """One DTC row by id, or None.

        Added for the per-code drill-down: the Faults page used to stop at a
        flat summary with no way to open one code's occurrence history and
        freeze frames. Mirrors `investigation()`.
        """
        return self.conn.execute(
            "SELECT * FROM dtcs WHERE id=?", (dtc_id,)
        ).fetchone()

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

    def dtc_status_history(self, vehicle_id: int,
                           since: str | None = None) -> list[dict]:
        """Each stored DTC sighting with its status byte, ordered by time.

        The status byte lives inside the freeze-frame JSON (it is written on
        every UDS occurrence), so it is surfaced here rather than as a column
        the older rows could not have populated. OBD-II DTCs carry no freeze
        frame and are returned with ``status_byte=None``, which the transition
        detector skips rather than reading as an off-edge.
        """
        sql = ("SELECT o.ts, o.freeze_frame, d.ecu, d.code FROM dtc_occurrences o "
               "JOIN dtcs d ON d.id=o.dtc_id WHERE d.vehicle_id=?")
        args: list = [vehicle_id]
        if since:
            sql += " AND o.ts>=?"
            args.append(since)
        sql += " ORDER BY o.ts"
        out: list[dict] = []
        for r in self.conn.execute(sql, args):
            status_byte = None
            if r["freeze_frame"]:
                try:
                    parsed = json.loads(r["freeze_frame"])
                    status_byte = parsed.get("status_byte")
                except (TypeError, ValueError):
                    status_byte = None
            out.append({"ts": r["ts"], "ecu": r["ecu"], "code": r["code"],
                        "status_byte": status_byte})
        return out

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

    # -- baselines ------------------------------------------------------------
    def save_baseline(self, vehicle_id: int, key: str, window_days: float,
                      method: str, stats: dict, notes: str = "",
                      ts: str | None = None) -> None:
        """Upsert the robust baseline for (vehicle, key, window, method).

        Keyed so a recomputation over the same window replaces the row rather
        than accumulating near-identical copies; a different window is a
        different baseline and gets its own row.
        """
        self.conn.execute(
            "INSERT INTO baselines(vehicle_id, key, window_days, method, "
            "median, mad, sigma, mean, std, p05, p95, n, notes, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(vehicle_id, key, window_days, method) DO UPDATE SET "
            "median=excluded.median, mad=excluded.mad, sigma=excluded.sigma, "
            "mean=excluded.mean, std=excluded.std, p05=excluded.p05, "
            "p95=excluded.p95, n=excluded.n, notes=excluded.notes, "
            "computed_at=excluded.computed_at",
            (vehicle_id, key, float(window_days), method,
             stats.get("median"), stats.get("mad"), stats.get("sigma"),
             stats.get("mean"), stats.get("std"), stats.get("p05"),
             stats.get("p95"), int(stats.get("n") or 0), notes or None,
             ts or utcnow()),
        )
        self.conn.commit()

    def baselines(self, vehicle_id: int, key: str | None = None) -> list:
        sql = "SELECT * FROM baselines WHERE vehicle_id=?"
        args: list = [vehicle_id]
        if key:
            sql += " AND key=?"
            args.append(key)
        sql += " ORDER BY key, window_days"
        return self.conn.execute(sql, args).fetchall()

    def baseline(self, vehicle_id: int, key: str, window_days: float,
                 method: str = "robust-median-mad"):
        return self.conn.execute(
            "SELECT * FROM baselines WHERE vehicle_id=? AND key=? AND "
            "window_days=? AND method=?",
            (vehicle_id, key, float(window_days), method)).fetchone()

    # -- investigations -------------------------------------------------------
    def open_investigation(self, vehicle_id: int, title: str, symptom: str,
                           criteria: str = "", anchor_key: str | None = None,
                           anchor_ecu: str | None = None,
                           ts: str | None = None) -> int:
        stamp = ts or utcnow()
        cur = self.conn.execute(
            "INSERT INTO investigations(vehicle_id, title, symptom, anchor_key, "
            "anchor_ecu, opened_at, updated_at, status, criteria) "
            "VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (vehicle_id, title, symptom, anchor_key, anchor_ecu, stamp, stamp,
             criteria or None),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_investigation_evidence(self, investigation_id: int, stage: str,
                                   kind: str, observation: str,
                                   ref: str | None = None,
                                   detail: str | None = None,
                                   ts: str | None = None,
                                   certain: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO investigation_evidence(investigation_id, ts, stage, "
            "kind, ref, observation, detail, certain) VALUES (?,?,?,?,?,?,?,?)",
            (investigation_id, ts or utcnow(), stage, kind, ref, observation,
             detail, int(certain)),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_investigation(self, investigation_id: int, *, status: str | None = None,
                             summary: str | None = None,
                             conclusion: str | None = None,
                             criteria: str | None = None,
                             ts: str | None = None) -> None:
        fields = []
        args: list = []
        for col, value in (("status", status), ("summary", summary),
                           ("conclusion", conclusion), ("criteria", criteria)):
            if value is not None:
                fields.append(f"{col}=?")
                args.append(value)
        fields.append("updated_at=?")
        args.append(ts or utcnow())
        args.append(investigation_id)
        self.conn.execute(
            f"UPDATE investigations SET {', '.join(fields)} WHERE id=?", args)
        self.conn.commit()

    def investigations(self, vehicle_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM investigations WHERE vehicle_id=? "
            "ORDER BY updated_at DESC, id DESC", (vehicle_id,)).fetchall()

    def investigation(self, investigation_id: int):
        return self.conn.execute(
            "SELECT * FROM investigations WHERE id=?",
            (investigation_id,)).fetchone()

    def investigation_evidence(self, investigation_id: int) -> list:
        return self.conn.execute(
            "SELECT * FROM investigation_evidence WHERE investigation_id=? "
            "ORDER BY ts, id", (investigation_id,)).fetchall()



