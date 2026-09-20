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
"""
from __future__ import annotations

import argparse
import logging
import sys

from config import load_config
from database.repository import Repository
from diagnostic.interface import AdapterNotFoundError, CommunicationError


def cmd_discover(cfg) -> int:
    from collector import Collector
    c = Collector(cfg, Repository(cfg.db_path))
    try:
        vin = c.open_and_identify()
        print(f"VIN: {vin}")
        results = c.discover_ecus()
        for key, status in results.items():
            print(f"  {key:<10} {status}")
        print("\nNOTE: 'no-response' may simply mean the ECU is not reachable "
              "with this adapter — see README 'What this can and cannot access'.")
        dtcs = c.read_and_store_dtcs()
        print(f"DTCs found: {dtcs or 'none'}")
    finally:
        c.close()
    return 0


def cmd_collect(cfg, cycles: int | None) -> int:
    from collector import Collector
    c = Collector(cfg, Repository(cfg.db_path))
    try:
        c.run(max_cycles=cycles)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        c.close()
    return 0


def cmd_report(cfg, question: str | None) -> int:
    from ai.service import DEFAULT_QUESTIONS, AnalysisService
    from ai.ollama import OllamaError
    svc = AnalysisService(cfg, Repository(cfg.db_path))
    q = question or DEFAULT_QUESTIONS[4]
    try:
        result = svc.ask(q)
    except OllamaError as exc:
        print(f"Ollama error: {exc}\nStart Ollama and ensure the model is pulled.")
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
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.get("web.host", "127.0.0.1"),
                port=int(cfg.get("web.port", 8000)), log_level="info")
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
    from analysis import anomaly, battery as batt, charging as chg, dtc as dtca
    from analysis import stats

    repo = Repository(cfg.db_path)
    row = repo.conn.execute("SELECT id FROM vehicles LIMIT 1").fetchone()
    if not row:
        print("No vehicle in database. Run 'seed' or 'collect' first.")
        return 1
    vid = row["id"]
    days = int(cfg.get("analysis.trend_window_days", 30))
    z = float(cfg.get("analysis.anomaly_zscore", 3.0))

    delta = batt.cell_delta_trend(repo, days, vid)
    print(f"Cell voltage delta ({days} d, {delta.get('samples', 0)} samples):")
    if delta.get("status") == "ok":
        print(f"  current {delta['current_mv']} mV | mean {delta['mean_mv']} mV "
              f"| std {delta['std_mv']} mV")
        print(f"  trend: {delta['trend']} "
              f"({delta['slope_mv_per_day']} mV/day)")
    else:
        print(f"  {delta['status']}")

    for metric, desc in [("cell_delta_mv", "cell voltage imbalance"),
                         ("pack_voltage_v", "pack voltage"),
                         ("battery_temp_c", "battery temperature")]:
        n = anomaly.scan_metric(repo, vid, metric, desc, days, z)
        print(f"Anomalies in {metric}: {n} (z >= {z})")

    corr = chg.correlate_dtc_with_sessions(repo, vid, days=90)
    print(f"DTCs linked to charging (within {corr['window_hours']} h of "
          f"charge end): {corr['codes_linked_to_charging'] or 'none'}")

    summary = dtca.dtc_summary(repo.dtc_list(vid))
    print(f"DTCs on record: {summary['total']}")
    for c in summary["codes"]:
        print(f"  {c['ecu']:<10} {c['code']:<10} {','.join(c['categories'])} "
              f"x{c['count']} last {c['last_seen']}")

    repo.add_analysis(vid, "battery-cell-delta", "hv_battery", days, delta)
    repo.add_analysis(vid, "charging-dtc-correlation", "charging", 90, corr)
    print("Results persisted to analysis_results / anomalies.")
    repo.close()
    return 0


def cmd_guard_test() -> int:
    """Verify the UDS read-only guard."""
    from diagnostic import uds
    from diagnostic.uds import ReadOnlyViolationError
    blocked = [0x14, 0x27, 0x2E, 0x31, 0x2F, 0x34, 0x36]
    ok = True
    for sid in blocked:
        try:
            uds.validate_service(sid)
            print(f"FAIL: service 0x{sid:02X} was allowed")
            ok = False
        except ReadOnlyViolationError:
            print(f"OK: service 0x{sid:02X} blocked")
    return 0 if ok else 1


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Read-only ID.3 diagnostics")
    parser.add_argument("command",
                        choices=["discover", "collect", "analyze", "report",
                                 "serve", "simulate", "seed", "guard-test"])
    parser.add_argument("--cycles", type=int, default=None)
    parser.add_argument("--question", type=str, default=None)
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    if args.command == "guard-test":
        return cmd_guard_test()
    if args.command == "simulate":
        return cmd_simulate(load_config(args.config))
    if args.command == "seed":
        from tools.seed_demo_data import seed
        seed(load_config(args.config).db_path)
        return 0

    cfg = load_config(args.config)
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
