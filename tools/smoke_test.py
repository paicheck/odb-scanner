"""Offline end-to-end smoke test - no car, no adapter, no Ollama needed.

Drives the WHOLE pipeline in one process against the built-in vehicle
simulator, using a scratch SQLite database:

    simulator -> ELM327 transport -> ECU discovery -> collection + DTC read
      -> statistics + anomalies -> web dashboard -> read-only safety audit

Usage:
    python tools/smoke_test.py                  # 2 collection cycles
    python tools/smoke_test.py --cycles 5
    python tools/smoke_test.py --with-llm       # also ask Ollama (needs Ollama)
    python tools/smoke_test.py --port 35210 --db data/diag_smoke.db

Exit code 0 = every check passed (SKIPs allowed), 1 = at least one FAIL.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DEFAULT_PATH, Config  # noqa: E402
from database.repository import Repository  # noqa: E402

VIN = "WVWZZZE1ZMP087053"

# the ONLY first-bytes allowed on the wire (see README "Safety - READ ONLY")
ALLOWED_TX = {0x01, 0x03, 0x09, 0x10, 0x19, 0x22, 0x3E}
BLOCKED_SERVICES = (0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36,
                    0x37, 0x85)

_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> bool:
    """Record one check. ok=None means SKIP (e.g. optional component)."""
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    _results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return bool(ok)


def scratch_config(port: int, db_path: str) -> Config:
    """config.yaml with the scratch DB and the simulator's TCP port."""
    with open(DEFAULT_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("database", {})["path"] = db_path
    adapter = data.setdefault("adapter", {})
    adapter["type"] = "elm327_tcp"
    adapter["tcp_host"] = "127.0.0.1"
    adapter["tcp_port"] = port
    data.setdefault("collector", {})["read_cell_voltages"] = True
    return Config(data)


def reset_db(db_path: Path) -> None:
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        try:
            path.unlink()
        except OSError:
            pass


# --- stage 1: adapter -> discovery -> collection ------------------------------
def run_adapter_checks(cfg: Config, cycles: int) -> None:
    from collector import Collector

    repo = Repository(cfg.db_path)
    col = Collector(cfg, repo)
    try:
        vin = col.open_and_identify()
        check("adapter connects, VIN decoded", vin == VIN, f"VIN={vin}")

        ecus = col.discover_ecus()
        responders = [k for k, s in ecus.items() if s == "responder"]
        check("ECU discovery (read-only probes)", bool(responders),
              f"{len(responders)} responder(s): {', '.join(responders) or 'none'}")

        snaps: list[dict] = []
        for i in range(cycles):
            snaps.append(col.collect_once())
            if i + 1 < cycles:
                time.sleep(0.2)
        merged = {k: v for snap in snaps for k, v in snap.items()}
        for key, label in (("pack_voltage", "pack voltage"),
                           ("cell_delta_mv", "cell delta"),
                           ("battery_temp", "battery temperature")):
            value = merged.get(key)
            check(f"collect: {label} decoded", value is not None, f"{value}")

        # Regression guard for the idle-link bug: the real collector polls every
        # collector.poll_interval, so the adapter link must survive an idle gap
        # longer than the socket read timeout. The simulator used to close the
        # connection on recv timeout, after which every later cycle silently
        # stored nothing (empty snapshots were logged as "cycle N: {}").
        idle_s = 1.2
        time.sleep(idle_s)
        after_idle = col.collect_once()
        check(f"link survives an idle gap of {idle_s:.1f}s between polls",
              after_idle.get("pack_voltage") is not None,
              f"pack_voltage={after_idle.get('pack_voltage')}")

        dtcs = col.read_and_store_dtcs()
        check("DTC read (OBD-II mode 03 + UDS 0x19 0x02)", True,
              f"{len(dtcs)} DTC(s): "
              + ", ".join(sorted({d['code'] for d in dtcs})))

        def count(table: str) -> int:
            return repo.conn.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0]

        check("measurements persisted", count("measurements") > 0,
              f"{count('measurements')} rows")
        check("per-cell DID sweep persisted", count("cell_voltages") > 0,
              f"{count('cell_voltages')} rows")
        check("battery snapshot persisted", count("battery_measurements") > 0,
              f"{count('battery_measurements')} rows")
        tx_rows = repo.conn.execute("SELECT count(*) FROM tx_log").fetchone()[0]
        check("tx_log safety manifest written", tx_rows > 0,
              f"{tx_rows} tx_log rows")
    finally:
        col.close()
        repo.close()


# --- stage 2: statistics / anomalies ------------------------------------------
def run_analysis_checks(cfg: Config) -> None:
    import main as cli  # exactly the code path `python main.py analyze` uses

    rc = cli.cmd_analyze(cfg)
    check("analysis engine runs", rc == 0, f"exit code {rc}")

    repo = Repository(cfg.db_path)
    try:
        results = repo.conn.execute(
            "SELECT count(*) FROM analysis_results").fetchone()[0]
        anomalies = repo.conn.execute(
            "SELECT count(*) FROM anomalies").fetchone()[0]
        check("analysis results persisted", results > 0, f"{results} rows")
        check("anomaly scan ran", True, f"{anomalies} anomaly rows")
    finally:
        repo.close()


# --- stage 3: web dashboard ---------------------------------------------------
def run_web_checks(cfg: Config) -> None:
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - optional test dependency
        check("web dashboard", None, f"test client unavailable: {exc}")
        return
    try:
        from web.dashboard import create_app
        client = TestClient(create_app(cfg))
        for path in ("/", "/battery", "/charging", "/dtcs", "/ai"):
            response = client.get(path)
            check(f"web {path}", response.status_code == 200,
                  f"HTTP {response.status_code}, {len(response.content)} bytes")
    except Exception as exc:
        check("web dashboard", False, f"{type(exc).__name__}: {exc}")


# --- stage 4: read-only safety audit -----------------------------------------
def run_safety_checks(cfg: Config) -> None:
    from diagnostic import uds
    from diagnostic.uds import ReadOnlyViolationError

    leaked = []
    for sid in BLOCKED_SERVICES:
        try:
            uds.validate_service(sid)
            leaked.append(f"0x{sid:02X}")
        except ReadOnlyViolationError:
            pass
    check("UDS guard blocks every write service", not leaked,
          f"leaked: {leaked}" if leaked else f"{len(BLOCKED_SERVICES)} services")

    builders = sorted(n for n in dir(uds) if n.startswith("build_"))
    check("uds exposes no arbitrary-payload API",
          builders == ["build_read_did_request", "build_session_request",
                       "build_tester_present"], ", ".join(builders))

    repo = Repository(cfg.db_path)
    try:
        rows = repo.conn.execute(
            "SELECT payload, purpose FROM tx_log WHERE direction='TX'").fetchall()
    finally:
        repo.close()
    offenders = []
    for row in rows:
        payload = (row["payload"] or "").strip()
        if len(payload) < 2:
            continue
        try:
            sid = int(payload[:2], 16)
        except ValueError:
            continue  # non-hex (e.g. AT commands) - not a vehicle request
        if sid not in ALLOWED_TX:
            offenders.append(f"{payload} ({row['purpose']})")
    check("only allow-listed services were ever transmitted", not offenders,
          f"{len(rows)} TX rows, offenders: {offenders[:3]}"
          if offenders else f"{len(rows)} TX rows audited")


# --- stage 5: optional local LLM report --------------------------------------
def run_llm_checks(cfg: Config) -> None:
    from ai.ollama import OllamaError
    from ai.service import AnalysisService

    repo = Repository(cfg.db_path)
    try:
        result = AnalysisService(cfg, repo).ask("Are my DTCs related?")
        check("Ollama report generated", bool(result["report"]),
              f"{len(result['report'])} chars, "
              f"{len(result['warnings'])} validator warning(s)")
    except OllamaError as exc:
        check("Ollama report generated", False, str(exc)[:180])
    finally:
        repo.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline end-to-end smoke test (simulator, no car needed)")
    parser.add_argument("--cycles", type=int, default=2,
                        help="collection cycles to run (default 2)")
    parser.add_argument("--port", type=int, default=35210,
                        help="TCP port for the in-process simulator")
    parser.add_argument("--db", default="data/diag_smoke.db",
                        help="scratch database (recreated on every run)")
    parser.add_argument("--with-llm", action="store_true",
                        help="also generate an Ollama report (needs Ollama)")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    reset_db(db_path)
    cfg = scratch_config(args.port, str(db_path))

    print(f"Simulator + scratch DB: {db_path} (tcp://127.0.0.1:{args.port})")
    from simulator.vehicle import SimServer
    with SimServer("127.0.0.1", args.port):
        run_adapter_checks(cfg, args.cycles)
    run_analysis_checks(cfg)
    run_web_checks(cfg)
    run_safety_checks(cfg)
    if args.with_llm:
        run_llm_checks(cfg)
    else:
        check("Ollama report generated", None, "skipped (use --with-llm)")

    failed = [r for r in _results if r[1] == "FAIL"]
    passed = sum(1 for r in _results if r[1] == "PASS")
    skipped = sum(1 for r in _results if r[1] == "SKIP")
    print(f"\n{passed} passed, {len(failed)} failed, {skipped} skipped")
    if failed:
        print("FAILED CHECKS:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    print("RESULT: pipeline OK - simulator, collector, analysis, web, "
          "read-only guard all verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())