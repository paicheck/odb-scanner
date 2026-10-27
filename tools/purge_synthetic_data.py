"""Delete the simulator and demo-harness rows that were written into the car's
database.

Two independent synthetic sources put data into the vehicle's own history, both
keyed to the same VIN, so neither was distinguishable from a real reading once
written:

  * the vehicle simulator (``main.py simulate``), which ``tools/start_scanner.py``
    starts alongside the collector. It reports the same VIN and answers with a
    hard-coded AC voltage of 232 V, 16.0 +/- 2.0 A and exactly 102 populated
    cell slots, and it toggles its charge mode every 120 s.
  * ``tools/seed_demo_data.py``, which fills 31 days of example battery,
    charging and DTC history -- including a deliberately growing cell-voltage
    imbalance -- into whatever database it is given.

How they were separated from the real readings: ``diagnostic_events`` records
the adapter identity on every connection, and every connection before
2026-10-05 named the simulator ("SIM327 v1.5 (odb_scanner simulator)") rather
than the real "ATI ELM327 v1.5". The first real connection is 2026-10-05T18:03Z.
Everything older than the cutoff is therefore synthetic, with one exception:
rows imported from a Car Scanner export are real readings of the car taken by
another tool, and are preserved (``provenance LIKE 'imported%'``). The adapter
connection log itself is kept too -- it is the evidence for this decision.

Dry run by default. ``--apply`` takes a snapshot first and then deletes; the
snapshot is the undo button, so there is no in-place rollback path.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from database.repository import Repository  # noqa: E402

# First real-adapter connection: 2026-10-05T18:03:00+00:00. Rounded down to the
# start of that day, which is the same set of rows -- the last synthetic stamp
# is 2026-09-20T14:26:06+00:00 and the first real one 2026-10-05T19:01:11+00:00,
# ~15 days later -- but reads as a date a human can check.
DEFAULT_CUTOFF = "2026-10-05T00:00:00+00:00"

# (table, timestamp column, extra predicate). The predicate is the exception
# list: imported Car Scanner readings are real, and the adapter log is kept as
# the audit trail. Everything else older than the cutoff came off the simulator
# or out of the demo seeder.
RULES: tuple[tuple[str, str, str | None], ...] = (
    ("cell_voltages", "ts", None),
    ("battery_measurements", "ts", None),
    ("charging_samples", "ts", None),
    ("charging_sessions", "started_at", None),
    ("dtc_occurrences", "ts", None),
    ("dtcs", "first_seen", None),
    ("anomalies", "ts", None),
    ("analysis_results", "ts", None),
    ("measurements", "ts", "provenance NOT LIKE 'imported%'"),
    ("diagnostic_events", "ts", "kind <> 'adapter'"),
    ("tx_log", "ts", None),
)

# Never touched: vehicles (the VIN is real, only its first_seen is not), ecus,
# meta, unsupported_dids and the definition/report tables. They are either the
# car's real identity or already empty.
PROTECTED = ("vehicles", "ecus", "meta", "unsupported_dids",
             "captured_frames", "measurement_definitions",
             "definition_evaluations", "definition_evidence", "llm_reports")

# Rows the collector itself labelled. Deleted whatever their date, because the
# label is direct evidence rather than an inference from the clock: the
# collector sets 'simulated' when the adapter identifies as the simulator, so a
# tagged row is synthetic by the writer's own admission. The `ts >= ?` half
# keeps this disjoint from RULES (which only matches older rows), so the plan is
# a sum of the two rather than an overlap.
TAGGED = (("measurements", "provenance = 'simulated'"),)


def _where(column: str, extra: str | None) -> str:
    clause = f'"{column}" < ?'
    return f"{clause} AND ({extra})" if extra else clause


# charge-start/charge-end events describe a charging session. They are written
# at the session's own boundaries, so an event belonging to a fake session can
# sit AFTER the cutoff: the simulator toggled its charge mode every 120 s and
# left sessions open, and the next real run closed them on 2026-10-16. Phase 1
# removes every pre-cutoff session, which leaves those stamps describing nothing.
#
# A charge event survives only if a surviving session explains it, i.e. it sits
# inside a session that started at or after the cutoff. Phrased that way the
# count is the same before and after phase 1 deletes the sessions, so the plan
# cannot disagree with the apply. `ts >= ?` also keeps the two phases disjoint,
# so the per-table total is a sum rather than an overlap.
_ORPHAN_CHARGE_SQL = (
    "kind IN ('charge-start','charge-end') AND ts >= ? "
    "AND (? IS NULL OR ts < ?)")


def _orphan_charge_count(repo: Repository, cutoff: str) -> int:
    """Charge events that no surviving session explains."""
    first = repo.conn.execute(
        "SELECT MIN(started_at) FROM charging_sessions WHERE started_at >= ?",
        (cutoff,)).fetchone()[0]
    return repo.conn.execute(
        f"SELECT COUNT(*) FROM diagnostic_events WHERE {_ORPHAN_CHARGE_SQL}",
        (cutoff, first, first)).fetchone()[0]


def plan(repo: Repository, cutoff: str) -> dict[str, int]:
    """Rows that WOULD be deleted, per table. Reads only."""
    todo = {
        table: repo.conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE {_where(col, extra)}',
            (cutoff,)).fetchone()[0]
        for table, col, extra in RULES
    }
    for table, predicate in TAGGED:
        todo[table] = todo.get(table, 0) + repo.conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "ts" >= ? AND {predicate}',
            (cutoff,)).fetchone()[0]
    if todo.get("diagnostic_events") is not None:
        todo["diagnostic_events"] += _orphan_charge_count(repo, cutoff)
    return todo


def purge(repo: Repository, cutoff: str, *, keep_tx_log: bool = False) -> dict:
    """Delete the synthetic rows. Caller is expected to have backed up first."""
    rules = [r for r in RULES if not (keep_tx_log and r[0] == "tx_log")]
    removed: dict[str, int] = {}
    # One transaction: a half-purged database would be worse than either end
    # state, because the synthetic and real rows are interleaved in the same
    # tables and no report could say which half survived.
    with repo.conn:
        max_id_before = repo.max_measurement_id()
        for table, col, extra in rules:
            cur = repo.conn.execute(
                f'DELETE FROM "{table}" WHERE {_where(col, extra)}', (cutoff,))
            removed[table] = cur.rowcount
        for table, predicate in TAGGED:
            cur = repo.conn.execute(
                f'DELETE FROM "{table}" WHERE "ts" >= ? AND {predicate}',
                (cutoff,))
            removed[table] = removed.get(table, 0) + cur.rowcount
        # Phase 2, after every pre-cutoff session is gone, so the surviving
        # sessions are exactly `started_at >= cutoff`.
        first = repo.conn.execute(
            "SELECT MIN(started_at) FROM charging_sessions WHERE started_at >= ?",
            (cutoff,)).fetchone()[0]
        cur = repo.conn.execute(
            f"DELETE FROM diagnostic_events WHERE {_ORPHAN_CHARGE_SQL}",
            (cutoff, first, first))
        removed["diagnostic_events"] += cur.rowcount
        # The vehicle row was created by the first simulator connection, so its
        # first_seen claims the car was seen on 2026-09-01. Re-point it at the
        # oldest surviving reading.
        repo.conn.execute(
            "UPDATE vehicles SET first_seen = ("
            "  SELECT MIN(ts) FROM measurements WHERE vehicle_id = vehicles.id"
            ") WHERE first_seen < ? AND EXISTS ("
            "  SELECT 1 FROM measurements WHERE vehicle_id = vehicles.id)",
            (cutoff,))
        # Deleting the newest rows makes their ids reusable; the cloud
        # uploader must know before it uploads the next readings.
        repo.note_measurement_deletes(max_id_before)
    return removed


def _fmt(n: int) -> str:
    return f"{n:,}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete simulator/demo rows from the car's database")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete (default: report only)")
    parser.add_argument("--before", default=DEFAULT_CUTOFF,
                        help=f"cutoff timestamp (default {DEFAULT_CUTOFF})")
    parser.add_argument("--keep-tx-log", action="store_true",
                        help="keep tx_log rows even though they are simulator "
                             "traffic (the safety manifest is normally kept "
                             "forever, so this is the conservative choice)")
    parser.add_argument("--db", default=None, help="database (default: config)")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    db = args.db or cfg.db_path
    cutoff = args.before

    with Repository(db) as repo:
        todo = plan(repo, cutoff)
        total = sum(todo.values())
        print(f"Database: {db}")
        print(f"Cutoff  : {cutoff}  (rows strictly older than this are "
              f"synthetic)")
        print()
        print(f"  {'table':<22} {'now':>10} {'to remove':>10} {'after':>10}")
        for table, cnt in todo.items():
            now = repo.conn.execute(
                f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print(f"  {table:<22} {_fmt(now):>10} {_fmt(cnt):>10} "
                  f"{_fmt(now - cnt):>10}")
        print(f"  {'TOTAL':<22} {'':>10} {_fmt(total):>10}")
        print()
        print(f"  kept: {', '.join(PROTECTED)}")
        print("  kept: measurements imported from a Car Scanner export")
        print("  kept: diagnostic_events of kind 'adapter' (the audit trail "
              "naming the adapter on every connection)")
        print("  also removed: any row already tagged provenance='simulated', "
              "whatever its date")

        if not args.apply:
            print()
            print("Dry run. Re-run with --apply to back up and delete.")
            return 0

        if not total:
            print()
            print("Nothing to remove.")
            return 0

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = Path(db).parent / cfg.get("database.backup_dir", "backups")
        dest = folder / f"{Path(db).stem}-{stamp}-presynthetic{Path(db).suffix}"
        folder.mkdir(parents=True, exist_ok=True)
        written = repo.backup(str(dest))
        print()
        print(f"Backup : {written}")

        removed = purge(repo, cutoff, keep_tx_log=args.keep_tx_log)
        repo.vacuum()

    print(f"Removed {_fmt(sum(removed.values()))} rows.")
    print("Restore by stopping everything and copying the backup over the "
          f"database:\n  {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
