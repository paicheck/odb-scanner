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
                                         a collector is running)
    python main.py prune [--days N]      trim old history (never automatic)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import load_config
from database.repository import Repository
from diagnostic.interface import AdapterNotFoundError, CommunicationError
from web.auth import is_loopback, resolve_token


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
                  "ECU answered that DID functionally, which may simply mean "
                  "it is not reachable with this adapter — see README 'What "
                  "this can and cannot access'.")
            dtcs = c.read_and_store_dtcs()
            print(f"DTCs found: {dtcs or 'none'}")
        finally:
            c.close()
    return 0


def cmd_collect(cfg, cycles: int | None) -> int:
    from collector import Collector
    with Repository(cfg.db_path) as repo:
        c = Collector(cfg, repo)
        try:
            c.run(max_cycles=cycles)
        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            c.close()
    return 0


def cmd_report(cfg, question: str | None) -> int:
    from ai.ollama import OllamaError
    from ai.service import DEFAULT_QUESTIONS, AnalysisService
    q = question or DEFAULT_QUESTIONS[4]
    with Repository(cfg.db_path) as repo:
        svc = AnalysisService(cfg, repo)
        try:
            result = svc.ask(q)
        except OllamaError as exc:
            print(f"Ollama error: {exc}\n"
                  "Start Ollama and ensure the model is pulled.")
            return 1
    print(result["report"])
    if result["warnings"]:
        print("\nValidator warnings:")
        for w in result["warnings"]:
            print(f"  - {w}")
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
    if not is_loopback(host):
        token = resolve_token(cfg, host)
        # resolve_token already ran inside create_app and, having found nothing
        # configured, minted and persisted one. Read it back so the printed URL
        # is guaranteed to be the value actually enforced.
        token = token or str(cfg.get("web.auth_token", "")).strip()
        if token:
            print(f"Access token required (network-reachable bind {host}).")
            print(f"Open once:  http://<this-machine>:{port}/?k={token}")
            print("Your browser then remembers it. Stored in config.local.yaml,")
            print("which is gitignored. To close the dashboard to the network,")
            print("set web.host to 127.0.0.1 instead.")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def cmd_simulate(cfg) -> int:
    from simulator.vehicle import SimServer
    host = cfg.get("simulation.host", "127.0.0.1")
    port = int(cfg.get("simulation.port", 35000))
    print(f"Simulator listening on tcp://{host}:{port} — Ctrl+C to stop")
    with SimServer(host, port):
        try:
            while True:
                import time
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
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
    print("\nREAD-ONLY guarantee verified." if ok
          else "\nREAD-ONLY GUARANTINE COMPROMISED -- do not connect to a vehicle.")
    return 0 if ok else 1


def _default_backup_path(cfg) -> Path:
    """Timestamped snapshot beside the database.

    Timestamped so repeated backups never overwrite each other -- a rolling
    "latest.db" is one bad day away from being the only copy of the only copy.
    """
    db = Path(cfg.db_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = db.parent / cfg.get("database.backup_dir", "backups")
    return folder / f"{db.stem}-{stamp}{db.suffix}"


def cmd_backup(cfg, out: str | None = None) -> int:
    """Snapshot the database while it is safe to do so.

    Uses SQLite's online backup API, so this is safe to run while a collector
    is writing: the snapshot is a single consistent point in time rather than
    a file copy that could miss everything still sitting in the WAL.
    """
    before = _db_size_mb(cfg.db_path)
    with Repository(cfg.db_path) as repo:
        dest = repo.backup(out or _default_backup_path(cfg))
        rows = repo.conn.execute("SELECT COUNT(*) c FROM measurements").fetchone()["c"]
        tx = repo.conn.execute("SELECT COUNT(*) c FROM tx_log").fetchone()["c"]
    after = _db_size_mb(dest)
    print(f"Backup written: {dest}")
    print(f"  {rows} measurements, {tx} tx_log (safety manifest) rows included")
    print(f"  size {before:.1f} MB -> {after:.1f} MB")
    print("\nKeep this file somewhere off this machine -- it is the only copy of")
    print("the vehicle's measured history.")
    return 0


def cmd_prune(cfg, days: float, hard: bool) -> int:
    """Trim history. Never runs automatically -- deleting data is the user's call."""
    with Repository(cfg.db_path) as repo:
        before = _db_size_mb(cfg.db_path)
        removed = repo.prune(days=days, keep_raw=not hard)
        if not hard:
            # Freeing pages needs VACUUM, which cannot run inside a transaction.
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
    parser.add_argument("command",
                        choices=["discover", "collect", "analyze", "report",
                                 "serve", "simulate", "seed", "guard-test",
                                 "prune", "backup", "doctor"])
    parser.add_argument("--days", type=float, default=90.0,
                        help="prune: age in days to keep (default 90)")
    parser.add_argument("--hard", action="store_true",
                        help="prune: delete whole rows instead of only the "
                             "verbatim response payloads")
    parser.add_argument("--out", type=str, default=None,
                        help="backup: destination file (default: a timestamped "
                             "file beside the database)")
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
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()

    if args.command == "guard-test":
        return cmd_guard_test()
    if args.command == "simulate":
        return cmd_simulate(load_config(args.config))
    if args.command == "seed":
        from tools.seed_demo_data import seed
        seed(load_config(args.config).db_path)
        return 0

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
        return cmd_backup(cfg, args.out)
    if args.command == "prune":
        return cmd_prune(cfg, args.days, args.hard)
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
