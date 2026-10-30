"""odb_scanner CLI.

Commands:
    python main.py simulate              start the vehicle simulator (no car needed)
    python main.py discover              connect, read VIN, probe ECUs (read-only)
    python main.py collect [--cycles N]  run collection loop against configured adapter
    python main.py analyze               run statistical analysis, persist results
    python main.py report [--question Q] Ollama diagnostic report
    python main.py serve                 run the web dashboard
    python main.py seed                  load the example diagnostic dataset
    python main.py guard-test            verify the read-only guard blocks writes
    python main.py backup [--out FILE]   snapshot the database (safe while
                                         a collector is running), check it,
                                         copy it to --mirror DIR and keep the
                                         newest --keep N
    python main.py prune [--days N]      trim old history (never automatic)
    python main.py unsupported-dids      list DIDs the collector has retired
                                         from polling (--clear to poll them
                                         again; --ecu KEY to scope it)
    python main.py sync-cloud            upload pending measurements to the
                                         cloud ingest API (see cloud/README.md)
    python main.py ingest                run that ingest API on this PC, into
                                         an SQLite archive (ingest.db_path)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import logging.handlers
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Keep each log file to a size you can actually scroll, with enough history to
# see when a fault started. 2 MB is roughly a week of continuous collecting at
# the default 5 s cycle; three backups is a fortnight.
LOG_ROTATE_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

from config import load_config
from database.repository import Repository
from diagnostic.interface import AdapterNotFoundError, CommunicationError
from version import __version__
from web.auth import masked


def cmd_discover(cfg) -> int:
    from collector import Collector
    from diagnostic import uds
    # `with` because Collector.close() only closes the transport, not the repo.
    with Repository(cfg.db_path) as repo:
        c = Collector(cfg, repo)
        try:
            vin = c.open_and_identify()
            print(f"VIN: {vin}")
            results = c.discover_ecus()
            for key, status in results.items():
                addr = c.ecu_addresses.get(key)
                where = (f"  (answered as ECU {uds.source_label(addr)})"
                         if addr is not None else "")
                print(f"  {key:<10} {status}{where}")
            print("\nNOTE: probes are functional (see "
                  "diagnostic/elm327.py set_header): 'no-response' means no "
                  "ECU answered that DID functionally, 'refused' means the "
                  "adapter explicitly refused the request ('?'), which may "
                  "simply mean it is not reachable with this adapter — see "
                  "README 'What this can and cannot access'.")
            dtcs = c.read_and_store_dtcs()
            print(f"DTCs found: {dtcs or 'none'}")
        finally:
            c.close()
    return 0


def cmd_collect(cfg, cycles: int | None) -> int:
    from collector import Collector
    with Repository(cfg.db_path) as repo:
        sync = _start_cloud_sync(cfg, repo)
        try:
            c = Collector(cfg, repo)
            try:
                c.run(max_cycles=cycles)
            except KeyboardInterrupt:
                print("Stopped.")
            finally:
                c.close()
        finally:
            # Stop the uploader before the repository closes underneath it.
            if sync is not None:
                sync.stop()
    return 0


def _start_cloud_sync(cfg, repo):
    """Start the background cloud uploader, or return None when it is off.

    A misconfigured cloud must not stop collection: the car is what is worth
    capturing, and the local database already holds it. So a bad setting is
    printed and collection proceeds without cloud sync, rather than refusing to
    run at all.

    The uploader runs on its own thread and reads the same Repository from it
    (one connection per thread, by design), so it can never block a vehicle
    read or hold the collector's connection.
    """
    from cloud.client import CloudSyncService
    from cloud.config import CloudConfigError, client_settings

    try:
        settings = client_settings(cfg)
    except CloudConfigError as exc:
        print(f"Cloud logging is off for this run: {exc}")
        return None
    if not settings.enabled:
        return None
    service = CloudSyncService(settings, repo)
    service.start()
    return service


def cmd_sync_cloud(cfg) -> int:
    """Upload pending measurements to the cloud ingest API, once.

    Safe to run repeatedly: the batch id is derived from the local row, so a
    re-run re-sends only what has not been accepted yet and the cloud discards
    anything it already holds.
    """
    from cloud.client import CloudClient, CloudSyncError, sync_pending
    from cloud.config import CloudConfigError, client_settings

    try:
        settings = client_settings(cfg)
    except CloudConfigError as exc:
        print(f"Cloud logging is misconfigured: {exc}")
        return 1
    if not settings.enabled:
        print("Cloud logging is disabled (cloud.enabled is false in "
              "config.yaml / config.local.yaml).")
        return 1

    if settings.api_url.startswith("http://"):
        print("Warning: cloud.api_url uses plain http; the bearer token and "
              "the vehicle's data travel unencrypted. Use https off a LAN.")

    client = CloudClient(settings)
    try:
        with Repository(cfg.db_path) as repo:
            result = sync_pending(repo, client, settings)
    except CloudSyncError as exc:
        # Nothing is lost and the watermark has not moved, so this is a retry
        # instruction, not a data-loss event.
        print(f"Upload failed; nothing was lost and it will retry: {exc}")
        return 1
    finally:
        client.close()

    if not result["batches"]:
        print("Everything is already in the cloud; nothing to upload.")
    else:
        print(f"Uploaded {result['uploaded']} row(s) in {result['batches']} "
              f"batch(es); {result['duplicates']} duplicate(s) already present.")
    return 0


def cmd_report(cfg, question: str | None) -> int:
    from ai.ollama import AIError
    from ai.service import DEFAULT_QUESTIONS, AnalysisService
    q = question or DEFAULT_QUESTIONS[4]
    with Repository(cfg.db_path) as repo:
        svc = AnalysisService(cfg, repo)
        try:
            result = svc.ask(q)
        except AIError as exc:
            hint = ("\nStart Ollama and ensure the model is pulled."
                    if svc.llm.where == "local Ollama" else "")
            print(f"AI error ({svc.llm.where}): {exc}{hint}")
            return 1
    print(result["report"])
    if result["warnings"]:
        print("\nValidator warnings:")
        for w in result["warnings"]:
            print(f"  - {w}")
    return 0


# Shortest token cmd_ingest accepts on a network-reachable bind: anything that
# can reach the port could otherwise guess its way into the car's archive. The
# compose file's development token is 15 characters; secrets.token_urlsafe(32)
# is 43.
INGEST_MIN_TOKEN_CHARS = 32


def cmd_ingest(cfg) -> int:
    """Run the ingest API on this PC, archiving into SQLite (ingest.db_path).

    The same API the MySQL deployment serves (cloud/api.py), on
    cloud.sqlite_store, so this PC can be the archive the collectors upload
    to without MySQL or Docker. Its token is cloud.token -- the one this PC's
    own collector already sends. Loopback by default; a network-reachable
    bind (for the phones, behind HTTPS) refuses a short token.
    """
    import uvicorn

    from cloud.api import create_app
    from cloud.sqlite_store import SqliteStore
    from web.auth import is_loopback

    path = cfg.get("ingest.db_path")
    if not path:
        print("ingest: set ingest.db_path in config.local.yaml to the archive "
              "file, e.g. ~/odb_data/archive.db")
        return 2
    archive = Path(str(path)).expanduser()
    if archive.resolve() == Path(cfg.db_path).expanduser().resolve():
        print(f"ingest: {archive} is the collector's own database; the archive "
              "must be a separate file")
        return 2
    token = str(cfg.get("cloud.token") or "").strip()
    host = str(cfg.get("ingest.host", "127.0.0.1"))
    port = int(cfg.get("ingest.port", 8080))
    if not token:
        print("ingest: cloud.token is empty; the ingest API never runs without "
              "a token")
        return 2
    if not is_loopback(host) and len(token) < INGEST_MIN_TOKEN_CHARS:
        print(f"ingest: {host} is reachable from the network, and a "
              f"{len(token)}-character cloud.token is guessable. Use at least "
              f"{INGEST_MIN_TOKEN_CHARS} (python -c \"import secrets; "
              "print(secrets.token_urlsafe(32))\") and the same on every "
              "uploader.")
        return 2
    print(f"Ingest API: http://{host}:{port}  archive {archive}  "
          f"(token {masked(token)}: cloud.token in config.local.yaml)")
    uvicorn.run(create_app(store=SqliteStore(archive), token=token),
                host=host, port=port, log_level="info")
    return 0


def cmd_serve(cfg) -> int:
    import uvicorn

    from web.dashboard import create_app
    host = str(cfg.get("web.host", "127.0.0.1"))
    port = int(cfg.get("web.port", 8000))
    # create_app is passed the address uvicorn will really bind, so the token
    # decision is made against the socket that is actually opened rather than
    # against whatever the config happens to say.
    app = create_app(cfg, bind_host=host)

    shown = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    print(f"Dashboard: http://{shown}:{port}")
    token = getattr(app.state, "auth_token", None)
    if token:
        # Not the full value: this goes to stdout, which the launcher writes to
        # data/logs/web.out on every start. The full token is printed once, in
        # the warning logged when it is first generated, and lives in
        # config.local.yaml (web.auth_token) -- gitignored.
        print(f"Access token required (network-reachable bind {host}).")
        print(f"Open once:  http://<this-machine>:{port}/?k=<token>  "
              f"(token {masked(token)}: web.auth_token in config.local.yaml)")
        print("Your browser then remembers it. To close the dashboard to the "
              "network, set web.host to 127.0.0.1 instead.")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def cmd_simulate(cfg) -> int:
    from simulator.vehicle import SimServer
    host = cfg.get("simulation.host", "127.0.0.1")
    port = int(cfg.get("simulation.port", 35000))
    vin = cfg.get("simulation.vin")
    print(f"Simulator listening on tcp://{host}:{port} — Ctrl+C to stop")
    if vin:
        print(f"Reporting VIN {vin} (simulation.vin, not vehicle.vin). "
              f"Simulator runs belong in {cfg.simulator_db_path}, never in "
              f"{cfg.db_path}.")
    with SimServer(host, port, vin=vin):
        try:
            while True:
                import time
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_seed(cfg) -> int:
    """Write the example dataset, but never into the car's database.

    The seeded history is plausible enough to be mistaken for measured data
    (that is the point of a demo), so it gets the same treatment as the
    simulator: its own file and its own VIN. Writing it into database.path
    would put a fabricated month of readings into the vehicle's own record
    with nothing to mark it apart.
    """
    from tools.seed_demo_data import seed

    target = cfg.seed_db_path
    if Path(target).resolve() == Path(cfg.db_path).resolve():
        print(f"refusing: seed.db_path and database.path are the same file "
              f"({target}). Example history must not share a file with "
              f"measured data.", file=sys.stderr)
        return 2
    print(f"Seeding example data into {target} (not {cfg.db_path}).")
    seed(target, vin=str(cfg.get("seed.vin", "") or "") or None)
    return 0


def cmd_analyze(cfg) -> int:
    """Run the statistical analysis engine and persist/print results."""
    # `with` so the connection is released on every exit path, including the
    # "no vehicle in database" early return below.
    with Repository(cfg.db_path) as repo:
        row = repo.first_vehicle()
        if not row:
            print("No vehicle in database. Run 'seed' or 'collect' first.")
            return 1
        return _analyze(repo, row["id"], cfg)


def _analyze(repo, vid: int, cfg) -> int:
    from analysis import anomaly
    from analysis import battery as batt
    from analysis import charging as chg
    from analysis import dtc as dtca

    days = int(cfg.get("analysis.trend_window_days", 30))
    charging_days = int(cfg.get("analysis.charging_window_days", 90))
    z = float(cfg.get("analysis.anomaly_zscore", 3.0))

    delta = batt.cell_delta_trend(repo, days, vid)
    print(f"Cell voltage delta ({days} d, {delta.get('samples', 0)} samples):")
    if delta.get("status") == "ok":
        print(f"  current {delta['current_mv']} mV | mean {delta['mean_mv']} mV "
              f"| std {delta['std_mv']} mV")
        slope = delta.get("slope_mv_per_day")
        if slope is None:
            print(f"  trend: {delta['trend']} (needs >= "
                  f"{delta.get('min_span_days')} d of samples, have "
                  f"{delta.get('span_text')})")
        else:
            print(f"  trend: {delta['trend']} ({slope} mV/day)")
    else:
        print(f"  {delta['status']}")

    for metric, desc in [("cell_delta_mv", "cell voltage imbalance"),
                         ("pack_voltage_v", "pack voltage"),
                         ("battery_temp_c", "battery temperature")]:
        n = anomaly.scan_metric(repo, vid, metric, desc, days, z)
        print(f"Anomalies in {metric}: {n} (z >= {z})")

    corr = chg.correlate_dtc_with_sessions(repo, vid, days=charging_days)
    print(f"DTCs linked to charging (within {corr['window_hours']} h of "
          f"charge end): {corr['codes_linked_to_charging'] or 'none'}")

    summary = dtca.dtc_summary(repo.dtc_list(vid))
    print(f"DTCs on record: {summary['total']}")
    for c in summary["codes"]:
        print(f"  {c['ecu']:<10} {c['code']:<10} {','.join(c['categories'])} "
              f"x{c['count']} last {c['last_seen']}")

    repo.add_analysis(vid, "battery-cell-delta", "hv_battery", days, delta)
    repo.add_analysis(vid, "charging-dtc-correlation", "charging",
                      charging_days, corr)
    print("Results persisted to analysis_results / anomalies.")
    return 0


def cmd_quality(cfg, persist: bool = False) -> int:
    """Print the measurement-quality registry and the decoder-lab verdicts.

    Read-only unless --persist is given: the decoder lab re-decodes the car's
    own stored raw bytes, and storing the verdict is a deliberate act.
    """
    with Repository(cfg.db_path) as repo:
        row = repo.first_vehicle()
        if not row:
            print("No vehicle in database.")
            return 1
        vid = row["id"]
        profile = cfg.get("vehicle.did_profile", "eup")

        from analysis import decoder_lab
        from decoders.quality import summary

        s = summary(profile)
        print(f"Measurement quality ({profile}): {s['total']} keys, "
              f"{s['excluded']} excluded from scoring")
        for v, n in sorted(s["by_verification"].items()):
            print(f"  {v:<12} {n}")

        evals = decoder_lab.evaluate_all(repo, vid, profile)
        fails = [e for e in evals if e.verdict == "FAIL"]
        print(f"\nDecoder lab: {len(fails)} FAIL of {len(evals)} evaluated")
        for e in fails:
            print(f"  {e.key:<48} {e.verdict:<14} {e.n_samples:>4} samples")
            for issue in e.issues:
                print(f"      - {issue}")
        if persist:
            n = decoder_lab.persist_evaluations(repo, profile, evals)
            print(f"\nPersisted {n} evaluations to definition_evaluations.")
        else:
            print("\n(print-only; pass --persist to store evaluations)")
    return 0


def cmd_status(cfg) -> int:
    """Print per-module freshness and the observation state of key anchors."""
    with Repository(cfg.db_path) as repo:
        row = repo.first_vehicle()
        if not row:
            print("No vehicle in database.")
            return 1
        vid = row["id"]
        from analysis import freshness

        statuses = freshness.module_status(repo, vid)
        summary = freshness.silence_summary(statuses)
        print(f"Modules: {summary['active']} active, {summary['idle']} idle, "
              f"{summary['silent']} silent, "
              f"{summary['imported-only']} imported-only, "
              f"{summary['no-readings']} no-readings")
        for m in statuses:
            age = f"{m.age_s / 3600:.1f}h" if m.age_s is not None else "—"
            print(f"  {m.ecu:<14} {m.status:<12} last {age:>8}  "
                  f"ok/fail {m.successes}/{m.failures}")
        print("\nKey anchors:")
        fresh_after = float(cfg.get("collector.max_value_age_s", 900.0))
        for key in ("soc_abs", "pack_voltage", "lv_voltage_obd",
                    "battery_temp", "odometer_km", "charge_mode"):
            obs = freshness.observe_key(repo, vid, key,
                                        fresh_after_s=fresh_after)
            print(f"  {key:<16} {obs.state.value:<18} {obs.ts or '—'}")
    return 0


def cmd_guard_test() -> int:
    """Verify the UDS read-only guard.

    Checks the service byte AND the sub-function byte. A service on the
    allow-list is not enough on its own: `1002` (programmingSession) and
    `190A` (stopResponseOnEvent) both use allow-listed service ids while
    changing ECU state, and were let through until the guard started
    looking past byte one.
    """
    from diagnostic import uds
    from diagnostic.uds import ReadOnlyViolationError

    blocked_services = [0x14, 0x27, 0x2E, 0x31, 0x2F, 0x34, 0x36]
    blocked_requests = [
        ("1002", "0x10 0x02 programmingSession"),
        ("1003", "0x10 0x03 extendedDiagnosticSession"),
        ("190A", "0x19 0x0A stopResponseOnEvent"),
        ("2E0102FFFF", "0x2E WriteDataByIdentifier"),
        ("31010001", "0x31 RoutineControl"),
        ("14FFFFFF", "0x14 ClearDiagnosticInformation"),
        ("2F0102", "0x2F InputOutputControlByIdentifier"),
        ("3501", "0x35 RequestUpload"),
    ]
    allowed_requests = [
        ("1001", "0x10 0x01 defaultSession"),
        ("221E3B", "0x22 ReadDataByIdentifier"),
        ("190208", "0x19 0x02 ReadDTCInformation"),
        ("3E00", "0x3E TesterPresent"),
        ("0100", "OBD-II mode 01 (read-only)"),
        ("0902", "OBD-II mode 09 (read-only)"),
    ]
    ok = True
    for sid in blocked_services:
        try:
            uds.validate_service(sid)
            print(f"FAIL: service 0x{sid:02X} was allowed")
            ok = False
        except ReadOnlyViolationError:
            print(f"OK: service 0x{sid:02X} blocked")
    for payload, why in blocked_requests:
        try:
            uds.validate_request(payload)
            print(f"FAIL: request {payload} ({why}) was allowed")
            ok = False
        except ReadOnlyViolationError:
            print(f"OK: {payload} blocked -- {why}")
    for payload, why in allowed_requests:
        try:
            uds.validate_request(payload)
            print(f"OK: {payload} allowed -- {why}")
        except ReadOnlyViolationError as exc:
            print(f"FAIL: {payload} ({why}) wrongly blocked: {exc}")
            ok = False
    ok = _guard_test_transmit_paths() and ok
    print("\nREAD-ONLY guarantee verified." if ok
          else "\nREAD-ONLY GUARANTEE COMPROMISED -- do not connect to a vehicle.")
    return 0 if ok else 1


def _guard_test_transmit_paths() -> bool:
    """Check the paths requests actually take, not just the validator.

    The validator passing was never the whole story: a request validated as one
    string and sent as another, an approved session that fell back to the
    broadcast header, and adapter commands that changed what a validated
    request became on the wire all passed the checks above. These run the real
    DiagnosticConnection and console code against a recording stub, so nothing
    is opened and nothing reaches an adapter.
    """
    from diagnostic import uds
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import ECUS
    from diagnostic.uds import ReadOnlyViolationError
    from tools.elm_console import refusal

    class _Stub:
        meb_addressing = False

        def __init__(self):
            self.sent: list[str] = []

        def send_command(self, command):
            self.sent.append(command)
            return []

    ok = True
    for payload, why in [
        ("14FFFFFF", "0x14 clear DTCs"),
        ("2EF19000", "0x2E write"),
        ("22F190\r2EF19000", "a write smuggled after a CR"),
        ("22F190zz", "non-hex characters"),
    ]:
        stub = _Stub()
        try:
            DiagnosticConnection(stub)._transmit(payload, None, "guard-test")
            print(f"FAIL: transmit path sent {payload!r} ({why})")
            ok = False
        except ReadOnlyViolationError:
            if stub.sent:
                print(f"FAIL: {payload!r} refused but {stub.sent} was sent")
                ok = False
            else:
                print(f"OK: transmit path refused {why}; nothing sent")
    stub = _Stub()
    DiagnosticConnection(stub)._transmit("22 f1 90", None, "guard-test")
    if stub.sent == ["22F190"]:
        print("OK: transmit path sends exactly the canonical string it validated")
    else:
        print(f"FAIL: transmit path sent {stub.sent!r} for '22 f1 90'")
        ok = False

    stub = _Stub()
    abs_ecu = ECUS["brk"]
    try:
        DiagnosticConnection(stub).approved_extended_session(
            abs_ecu, approval=uds.ABS_APPROVAL_TOKEN, purpose="guard-test")
        print("FAIL: approved 0x10 0x03 went out without physical addressing")
        ok = False
    except ReadOnlyViolationError:
        print("OK: approved 0x10 0x03 refused without physical addressing"
              + ("" if not stub.sent else f" (but sent {stub.sent})"))
        ok = ok and not stub.sent

    for cmd in ("ATCAF0", "ATFCSD 0414FFFFFF", "ATFCSM 1",
                "STPX H:7E0, D:14FFFFFF", "1003"):
        if refusal(cmd, raw_unsafe=False):
            print(f"OK: console refuses {cmd!r}")
        else:
            print(f"FAIL: console would send {cmd!r}")
            ok = False
    return ok


def _default_backup_path(cfg) -> Path:
    """Timestamped snapshot beside the database.

    Timestamped so repeated backups never overwrite each other -- a rolling
    "latest.db" is one bad day away from being the only copy of the only copy.
    """
    db = Path(cfg.db_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = db.parent / cfg.get("database.backup_dir", "backups")
    return folder / f"{db.stem}-{stamp}{db.suffix}"


def cmd_backup(cfg, out: str | None = None, keep: int | None = None,
               mirror: str | None = None) -> int:
    """Snapshot the database while it is safe to do so, and check the copy.

    Uses SQLite's online backup API, so this is safe to run while a collector
    is writing: the snapshot is a single consistent point in time rather than
    a file copy that could miss everything still sitting in the WAL.

    Every snapshot is integrity-checked before anything else happens, because
    it is unattended (a systemd timer) and a backup nobody checked is a hope,
    not a backup. A failed check returns 1 so the timer's run is marked
    failed, and nothing is pruned or mirrored. Then the snapshot is copied to
    `mirror` (database.backup_mirror_dir, e.g. an external drive) when that
    folder exists, and the newest `keep` (database.backup_keep) timestamped
    snapshots are kept in each place. Pruning touches only files named the way
    this command names them, and never with --out.
    """
    keep = keep if keep is not None else cfg.get("database.backup_keep")
    if keep is not None and int(keep) < 1:
        print(f"backup: --keep must be at least 1, got {keep}")
        return 2
    mirror = mirror or cfg.get("database.backup_mirror_dir")
    before = _db_size_mb(cfg.db_path)
    with Repository(cfg.db_path) as repo:
        dest = repo.backup(out or _default_backup_path(cfg))
        rows = repo.conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
        tx = repo.conn.execute("SELECT COUNT(*) c FROM tx_log").fetchone()["c"]
    after = _db_size_mb(dest)
    print(f"Backup written: {dest}")
    print(f"  {rows} measurements, {tx} tx_log (safety manifest) rows included")
    print(f"  size {before:.1f} MB -> {after:.1f} MB")
    rc = _check_mirror_prune(dest, _snapshot_name_re(cfg), keep, mirror,
                             prune=not out)
    if rc == 0 and not out:
        rc = _backup_ingest_archive(cfg, dest.parent, keep, mirror)
    if rc == 0 and not mirror:
        print("\nKeep this file somewhere off this machine -- it is the only copy "
              "of\nthe vehicle's measured history (database.backup_mirror_dir "
              "does that for you).")
    return rc


# The ingest archive's snapshots get their own fixed name, so neither set can
# match the other's pruning pattern whatever the two files are called.
INGEST_SNAPSHOT_PREFIX = "ingest-archive"


def _backup_ingest_archive(cfg, folder: Path, keep, mirror) -> int:
    """Snapshot the ingest archive (ingest.db_path) beside the car's, if any.

    It holds what the phones uploaded -- readings with no other copy off the
    phones -- so it gets the same checked, mirrored, pruned snapshot.
    """
    path = cfg.get("ingest.db_path")
    if not path:
        return 0
    archive = Path(str(path)).expanduser()
    if not archive.is_file():
        print(f"  ingest archive {archive} does not exist yet -- nothing to back up")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = folder / f"{INGEST_SNAPSHOT_PREFIX}-{stamp}.db"
    src = sqlite3.connect(Path(archive).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dest))
        try:
            src.backup(target)
        finally:
            target.close()
    finally:
        src.close()
    print(f"Ingest archive backup written: {dest}")
    pattern = re.compile(rf"^{INGEST_SNAPSHOT_PREFIX}-\d{{8}}T\d{{6}}Z\.db$")
    return _check_mirror_prune(dest, pattern, keep, mirror, prune=True)


def _check_mirror_prune(dest: Path, pattern: re.Pattern, keep, mirror,
                        prune: bool) -> int:
    """Integrity-check `dest`; only if it passes, mirror it and prune."""
    problem = _snapshot_problem(dest)
    if problem:
        print(f"\nBACKUP FAILED ITS INTEGRITY CHECK: {dest}: {problem}")
        print("Nothing was pruned or mirrored. Keep the previous snapshots.")
        return 1
    print("  integrity check: ok")
    if mirror:
        copied = _mirror_snapshot(dest, Path(mirror))
        if copied and keep and prune:
            _prune_snapshots(Path(mirror), pattern, int(keep))
    if keep and prune:
        _prune_snapshots(dest.parent, pattern, int(keep))
    return 0


def _snapshot_problem(path: Path) -> str | None:
    """None when `path` passes PRAGMA integrity_check, else what failed.

    Opened read-only and immutable: the snapshot is closed and nobody writes
    it, and this way checking it leaves no -wal/-shm files beside it.
    """
    try:
        con = sqlite3.connect(Path(path).resolve().as_uri()
                              + "?mode=ro&immutable=1", uri=True)
        try:
            rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"{type(exc).__name__}: {exc}"
    return None if rows == ["ok"] else "; ".join(rows[:5])


def _snapshot_name_re(cfg) -> re.Pattern:
    """Names _default_backup_path gives: <db stem>-YYYYmmddTHHMMSSZ<suffix>."""
    db = Path(cfg.db_path)
    return re.compile(rf"^{re.escape(db.stem)}-\d{{8}}T\d{{6}}Z{re.escape(db.suffix)}$")


def _mirror_snapshot(src: Path, folder: Path) -> bool:
    """Copy `src` into `folder`; False (and say why) when that is not possible.

    The folder must already exist. It is usually on a removable drive, and
    creating it while the drive is not mounted would quietly put the "off
    machine" copy on this machine's own disk -- or fail on a root-owned mount
    point. The copy goes to a temporary name, is compared byte-for-byte by
    SHA-256, and only then takes the final name.
    """
    if not folder.is_dir():
        print(f"  mirror: {folder} is not there (drive not connected?) -- "
              "skipped; the snapshot is only on this machine")
        return False
    final = folder / src.name
    partial = folder / (src.name + ".partial")
    try:
        shutil.copyfile(src, partial)
        with open(partial, "rb+") as fh:
            os.fsync(fh.fileno())
        if _sha256(partial) != _sha256(src):
            partial.unlink(missing_ok=True)
            print(f"  mirror: the copy in {folder} did not match -- removed")
            return False
        os.replace(partial, final)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        print(f"  mirror: could not copy to {folder}: {exc}")
        return False
    print(f"  mirror: copied to {final}")
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _prune_snapshots(folder: Path, pattern: re.Pattern, keep: int) -> None:
    """Delete all but the newest `keep` snapshots in `folder` named by
    `pattern`. The timestamp in the name sorts in time order."""
    snapshots = sorted(p for p in folder.iterdir()
                       if p.is_file() and pattern.match(p.name))
    for old in snapshots[:-keep]:
        old.unlink()
        print(f"  pruned {old}")


def cmd_prune(cfg, days: float, hard: bool) -> int:
    """Trim history. Never runs automatically -- deleting data is the user's call."""
    with Repository(cfg.db_path) as repo:
        before = _db_size_mb(cfg.db_path)
        removed = repo.prune(days=days, keep_raw=not hard)
        # Freeing pages needs VACUUM, which cannot run inside a transaction.
        # Both modes: a hard prune deletes the most and used to be the one
        # that never gave the space back.
        repo.vacuum()
        after = _db_size_mb(cfg.db_path)
    what = ("raw payloads" if not hard else "rows")
    print(f"Pruned {what} older than {days:g} days (cutoff {removed['cutoff']}):")
    for key, count in removed.items():
        # isinstance(True, int) is True, so the keep_raw flag would otherwise
        # be reported as a count of one.
        if isinstance(count, int) and not isinstance(count, bool) and count:
            print(f"  {key:22} {count}")
    if hard:
        print("Parsed values inside the window are untouched. Vehicles, ECUs, "
              "sessions, DTCs, reports and tx_log are never pruned.")
    else:
        print("Every parsed value inside the window is untouched, including the "
              "verbatim bytes of recent rows.")
    print("tx_log is never pruned: it is the safety manifest recording every "
          "request ever sent to the vehicle.")
    print(f"Database size: {before:.1f} MB -> {after:.1f} MB")
    return 0


def cmd_unsupported_dids(cfg, clear: bool, ecu: str | None) -> int:
    """List -- or clear -- the DIDs the collector has retired from polling.

    Retiring is how the collector stops re-asking for a DID this car does not
    implement. It is also how a DID that does answer can vanish from collection:
    72 were retired in one pass on 2026-10-17 while the adapter could not reach
    the 29-bit bus, including the odometer and the VIN. So the record is
    inspectable and reversible here rather than being a one-way door.
    """
    # Imported lazily: only this command needs the decoder registry, and the CLI
    # has to stay importable without the optional decoder stack present.
    from decoders.registry import build_default_registry

    with Repository(cfg.db_path) as repo:
        vehicle = repo.first_vehicle()
        if vehicle is None:
            print("No vehicle in the database yet -- nothing has been collected.")
            return 0
        vid = vehicle["id"]
        rows = repo.unsupported_dids(vid)
        if not rows:
            print(f"{vehicle['vin']}: no retired DIDs -- every registered DID is "
                  "being polled.")
            return 0

        names: dict[tuple[str, int], str] = {}
        for spec in build_default_registry(
                cfg.get("vehicle.did_profile", "eup")).all():
            names.setdefault((spec.ecu_key, spec.did), spec.key)

        by_ecu: dict[str, int] = {}
        for r in rows:
            by_ecu[r["ecu_key"]] = by_ecu.get(r["ecu_key"], 0) + 1
        print(f"{vehicle['vin']}: {len(rows)} retired DIDs are SKIPPED "
              f"({len(by_ecu)} modules: "
              + ", ".join(f"{k} {v}" for k, v in sorted(by_ecu.items())) + ")")
        print()
        print(f"  {'module':<10} {'DID':<8} {'decoder key':<34} "
              f"{'last retired':<20} count")
        for r in rows:
            name = names.get((r["ecu_key"], r["did"]), "?")
            print(f"  {r['ecu_key']:<10} 0x{r['did']:04X}   {name:<34} "
                  f"{r['last_seen'][:19]:<20} {r['count']}")

        if not clear:
            print()
            print("These are skipped, not deleted. To poll them again:")
            print("  python main.py unsupported-dids --clear"
                  + (f" --ecu {ecu}" if ecu else ""))
            print("then restart the collector (python tools/start_scanner.py). "
                  "DIDs the car")
            print("really does not implement retire again on their own.")
            return 0

        removed = repo.clear_unsupported_dids(vid, ecu)
        print()
        print(f"Cleared {removed} retired DID(s)"
              + (f" for module {ecu}" if ecu else "") + ".")
        print("Restart the collector to re-poll them: "
              "python tools/start_scanner.py")
    return 0


def _db_size_mb(path: str) -> float:
    """On-disk size including the WAL, which holds recent writes not yet folded in."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(path).with_name(Path(path).name + suffix)
        if p.exists():
            total += p.stat().st_size
    return total / (1024 * 1024)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only ID.3 diagnostics")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("command",
                        choices=["discover", "collect", "analyze", "report",
                                 "serve", "simulate", "seed", "guard-test",
                                 "prune", "backup", "doctor", "sync-cloud",
                                 "ingest",
                                 "unsupported-dids", "quality", "status"])
    parser.add_argument("--days", type=float, default=90.0,
                        help="prune: age in days to keep (default 90)")
    parser.add_argument("--hard", action="store_true",
                        help="prune: delete whole rows instead of only the "
                             "verbatim response payloads")
    parser.add_argument("--out", type=str, default=None,
                        help="backup: destination file (default: a timestamped "
                             "file beside the database)")
    parser.add_argument("--keep", type=int, default=None,
                        help="backup: keep the newest N snapshots here and in "
                             "the mirror (default database.backup_keep; all)")
    parser.add_argument("--mirror", type=str, default=None,
                        help="backup: also copy the snapshot into this existing "
                             "folder (default database.backup_mirror_dir)")
    parser.add_argument("--clear", action="store_true",
                        help="unsupported-dids: clear the retired list so those "
                             "DIDs are polled again")
    parser.add_argument("--ecu", type=str, default=None,
                        help="unsupported-dids: limit --clear to one module")
    parser.add_argument("--cycles", type=int, default=None)
    parser.add_argument("--question", type=str, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--port", type=str, default=None,
                        help="doctor: override adapter.port for this run")
    parser.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"),
                        help="doctor: use a TCP adapter/simulator")
    parser.add_argument("--all-ports", action="store_true",
                        help="doctor: try every serial port until one opens")
    parser.add_argument("--timeout", type=float, default=None,
                        help="doctor: per-command timeout in seconds")
    parser.add_argument("--only-config", action="store_true",
                        help="doctor: print configuration, touch no hardware")
    parser.add_argument("--persist", action="store_true",
                        help="quality: also store the decoder evaluations in "
                             "the definition store (default: print only)")
    return parser


# Log file names per command. These are NOT simply the command name: the
# launcher and the .bat shortcuts tell users to look in
# data/logs/collector.log, web.log and simulator.log, and renaming them to
# collect.log/serve.log/simulate.log would break that muscle memory for no
# benefit.
LOG_COMPONENT = {
    "collect": "collector",
    "serve": "web",
    "simulate": "simulator",
    "discover": "discover",
    "doctor": "doctor",
    "guard-test": "maintenance",
    "analyze": "analyze",
    "report": "report",
    "prune": "maintenance",
    "backup": "maintenance",
    "migrate": "maintenance",
    "seed": "maintenance",
    "unsupported-dids": "maintenance",
    "sync-cloud": "cloud",
    "ingest": "ingest",
    "quality": "maintenance",
    "status": "maintenance",
}


def configure_logging(component: str, log_dir: Path | None = None) -> None:
    """Log to the console AND to a bounded file in data/logs/.

    Console-only logging is fine right up to the moment it matters: an
    always-on collector started detached writes to a scrollback buffer nobody
    will ever read, so the record of why a collection run misbehaved is simply
    gone by the time anyone looks. tools/start_scanner.py already appended the
    child process's output to data/logs/<name>.log, but with no rotation, so
    that file only ever grew -- 182 KB after a day's use, and unbounded after
    that, which makes it useless for diagnosis long before it threatens the
    disk.

    Rotating on write fixes that at the source and covers every entry point
    (collect, serve, analyze) rather than just the launcher. STDERR_CAPTURED
    suppresses the console handler when a launcher is already teeing stderr
    into a file it manages, so the same lines are not written twice.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not os.environ.get("ODB_STDERR_CAPTURED"):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    try:
        # `log_dir` is injectable so tests can prove rotation without writing
        # into the developer's real data/logs/ -- which holds the actual record
        # of past collection runs, and is not the test suite's to overwrite.
        log_dir = Path(log_dir) if log_dir is not None else (
            Path(__file__).resolve().parent / "data" / "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_dir / f"{component}.log", maxBytes=LOG_ROTATE_BYTES,
            backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
        rotating.setFormatter(fmt)
        root.addHandler(rotating)
    except OSError as exc:
        # A read-only install or a locked file must not stop the collector: the
        # console handler above is still there, and losing the file is far
        # better than losing the run.
        root.warning("Could not open a log file in data/logs/ (%s). "
                     "Continuing with console logging only.", exc)


def main() -> int:
    args = build_parser().parse_args()
    configure_logging(LOG_COMPONENT.get(args.command, args.command))

    if args.command == "guard-test":
        return cmd_guard_test()
    if args.command == "simulate":
        return cmd_simulate(load_config(args.config))
    if args.command == "seed":
        return cmd_seed(load_config(args.config))

    cfg = load_config(args.config)
    if args.command == "doctor":
        # Imported lazily: the doctor is a troubleshooting path, and nothing
        # else in the tool should depend on it.
        from tools.doctor import main as doctor_main
        # Forward explicitly rather than rewriting sys.argv: the two parsers
        # must not share global state.
        doctor_argv = ["--config", args.config] if args.config else []
        if args.tcp:
            doctor_argv += ["--tcp", args.tcp[0], str(args.tcp[1])]
        if args.port:
            doctor_argv += ["--port", args.port]
        if args.all_ports:
            doctor_argv += ["--all-ports"]
        if args.timeout is not None:
            doctor_argv += ["--timeout", str(args.timeout)]
        if args.only_config:
            doctor_argv += ["--only-config"]
        return doctor_main(doctor_argv)
    if args.command == "backup":
        return cmd_backup(cfg, args.out, keep=args.keep, mirror=args.mirror)
    if args.command == "prune":
        return cmd_prune(cfg, args.days, args.hard)
    if args.command == "unsupported-dids":
        return cmd_unsupported_dids(cfg, args.clear, args.ecu)
    if args.command == "sync-cloud":
        return cmd_sync_cloud(cfg)
    if args.command == "ingest":
        return cmd_ingest(cfg)
    if args.command == "quality":
        return cmd_quality(cfg, args.persist)
    if args.command == "status":
        return cmd_status(cfg)
    try:
        if args.command == "discover":
            return cmd_discover(cfg)
        if args.command == "collect":
            return cmd_collect(cfg, args.cycles)
        if args.command == "analyze":
            return cmd_analyze(cfg)
        if args.command == "report":
            return cmd_report(cfg, args.question)
        if args.command == "serve":
            return cmd_serve(cfg)
    except AdapterNotFoundError as exc:
        print(f"\nAdapter not reachable: {exc}")
        print("Check: adapter plugged into the OBD port, ignition on / car awake,")
        print("Bluetooth on, and adapter.port is the OUTGOING 'Serial over")
        print("Bluetooth link' COM port (see README 'With the real car').")
        return 1
    except CommunicationError as exc:
        print(f"\nCommunication error: {exc}")
        print("The adapter works but the vehicle is not answering (NO DATA).")
        print("On the ID.3: switch ignition ON — press the start button WITHOUT")
        print("the brake (Zündung an), or go into 'ready' mode — wait ~10 s and")
        print("retry 'python main.py discover'. For raw checks use")
        print("'python tools/elm_console.py' (send 0100 to test the bus).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
