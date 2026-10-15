"""Live collector acceptance: one short run, then read the database back.

The BMS fix (`ATCRA <rx29>`, commit 053f77a) is proven by the experiment matrix
(EXP-15/16 answered `62 02 8C B2` = 71.2 % SoC on 13 Oct) but has never been
exercised through the collector: the last real run (12 Oct) wrote 1972
`no-response` rows and every `battery_measurements` row had NULL for pack
voltage, SoC and cells. "The matrix answered" and "the collector stores HV
battery data in a normal cycle" are different claims, and only the second one
is the product. This tool makes that claim testable in one bounded run:

    open the configured adapter -> discover ECUs -> collect N cycles
      -> read the rows that were written -> check they are physically sound

It deliberately does NOT call `Collector.run()`. `run()` treats a cycle in
which every read failed as a dead adapter and backs off (5 s doubling to a
5-minute ceiling) before retrying, so a sleeping car would make this
"acceptance check" hang for minutes before it failed. Here the car gets a
bounded attempt; a sleeping car is reported as "could not reach the vehicle"
(exit 2), which needs a different fix from "the data is wrong" (exit 1).

Every check is read from the database, not from the collector's in-memory
snapshot, because the dashboard, the analysis engine and the AI report all read
the database. It also matters that the run interleaves 29-bit modules (the
BMS) with 11-bit ones (energy/climate): the clone refused the first 29-bit
frame after an ATSP6 excursion in the matrix (EXP-10), so if that transition is
sticky the BMS reads *after* the energy probe are the ones that will fail --
and this run is the first place that is visible in the product rather than the
bench.

What a pass means:

  * `bat_mgmt` answers a 0x22 probe -- the Track A fix held in a normal cycle
  * `pack_voltage` decoded, plausible (200-500 V; per-cell = /108 in 3.0-4.3 V)
    and equal to the stored raw bytes / 4 (the decoder's own contract)
  * `soc_abs` decoded, 0-100 %, equal to raw / 2.5
  * a per-cell sweep was stored (>= --min-cells cells, each 2.0-4.5 V)
  * a `battery_measurements` row holds pack voltage, SoC and the cell extrema
  * nothing off the read-only allow-list was transmitted in this run

The 5 Oct Car Scanner import (81.2 % SoC -> 431.25..431.75 V) is printed for
context but is NOT a pass/fail target: the car has since dropped to ~71 % SoC,
so its pack voltage has moved. Pass `--expect-voltage` (with `--voltage-tol`)
to assert a specific band when you know the car is at a known charge level.

Usage:
    python tools/collect_acceptance.py                       # adapter from config
    python tools/collect_acceptance.py --port COM4           # try the other port
    python tools/collect_acceptance.py --cycles 2
    python tools/collect_acceptance.py --sim                 # offline self-test
    python tools/collect_acceptance.py --expect-voltage 431.5 --voltage-tol 0.25

Exit codes: 0 = every essential check passed; 1 = an essential check failed;
2 = the adapter or the vehicle could not be reached at all.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DEFAULT_PATH, Config, load_config  # noqa: E402
from database.repository import Repository  # noqa: E402

# The only first-bytes allowed on the wire (README "Safety - READ ONLY"),
# identical to tools/smoke_test.py so the two audits cannot disagree. An
# MEB-addressed request is reframed into a padded ISO-TP single frame before it
# is logged, so the bytes the audit sees there start with the single-frame PCI
# length (0x03 for every 3-byte UDS request this project builds), which is also
# in the set.
ALLOWED_TX = {0x01, 0x03, 0x09, 0x10, 0x19, 0x22, 0x3E}

# Physically plausible windows. Deliberately independent of the current charge
# level, so a pass cannot come from the car happening to sit at the same SoC as
# the 5 Oct Car Scanner reference.
PACK_V_MIN, PACK_V_MAX = 200.0, 500.0
CELL_V_MIN, CELL_V_MAX = 2.0, 4.5
# 0x1E40..0x1EAB; the divider for the per-cell plausibility cross-check.
MEB_CELL_SLOTS = 108


@dataclass
class Result:
    name: str
    status: str          # PASS | FAIL | WARN | SKIP
    detail: str = ""
    essential: bool = True


@dataclass
class Options:
    cycles: int = 2
    min_cells: int = 90
    expect_voltage: float | None = None
    voltage_tol: float = 0.25


# -- run ---------------------------------------------------------------------
def _marks(repo: Repository) -> dict:
    """Highest row id per table, so "this run" can be told from history.

    Row ids rather than timestamps: the collector stamps to second resolution,
    so a run that starts and writes within the same second as an earlier one
    cannot be separated by `ts >=`.
    """
    out = {}
    for table in ("measurements", "battery_measurements", "cell_voltages",
                  "tx_log"):
        row = repo.conn.execute(
            f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}").fetchone()
        out[table] = row["m"]
    return out


def collect(cfg: Config, cycles: int) -> dict:
    """Drive the real Collector briefly and return what it determined.

    Uses `open_and_identify()` + `discover_ecus()` + `collect_once()` directly
    rather than `run()` so a sleeping car fails fast instead of entering the
    reconnect backoff (see the module docstring).
    """
    from collector import Collector

    repo = Repository(cfg.db_path)
    col = Collector(cfg, repo)
    marks = _marks(repo)
    try:
        vin = col.open_and_identify()
        ecus = col.discover_ecus()
        for i in range(max(1, cycles)):
            col.collect_once()
            if i + 1 < cycles:
                time.sleep(0.2)
        # DTCs are not part of the HV-battery claim, but a normal cycle reads
        # them, so exercise it and tolerate a refusal.
        try:
            col.read_and_store_dtcs()
        except Exception:  # a DTC refusal must not fail the battery check
            pass
        return {"vin": vin, "ecus": ecus, "marks": marks,
                "vehicle_id": col.vehicle_id}
    finally:
        col.close()
        repo.close()


# -- evaluate ----------------------------------------------------------------
def _one(repo: Repository, sql: str, args: tuple):
    return repo.conn.execute(sql, args).fetchone()


def imported_reference(repo: Repository, vehicle_id: int) -> dict:
    """Latest imported Car Scanner value per key, for context only."""
    rows = repo.conn.execute(
        "SELECT m.key, m.value, m.ts FROM measurements m "
        "WHERE m.vehicle_id=? AND m.provenance LIKE 'imported%' "
        "AND m.key IN ('pack_voltage','soc_abs','soc_normal') "
        "AND m.value IS NOT NULL "
        "AND m.id = (SELECT m2.id FROM measurements m2 "
        "            WHERE m2.vehicle_id = m.vehicle_id AND m2.key = m.key "
        "              AND m2.provenance LIKE 'imported%' "
        "              AND m2.value IS NOT NULL "
        "            ORDER BY m2.ts DESC, m2.id DESC LIMIT 1)",
        (vehicle_id,),
    ).fetchall()
    return {r["key"]: (r["value"], r["ts"]) for r in rows}


def evaluate(repo: Repository, state: dict, opts: Options) -> list[Result]:
    """Check the rows written by `collect()` against the acceptance criteria."""
    results: list[Result] = []
    vm = state["marks"]["measurements"]
    vid = state["vehicle_id"]

    # 1. Track A held in a normal discovery cycle: the BMS answered 0x028C.
    bat = state["ecus"].get("bat_mgmt", "not probed")
    results.append(Result(
        "BMS answers the 0x028C discovery probe",
        "PASS" if bat == "responder" else "FAIL",
        f"bat_mgmt = {bat}"))

    # 2. Pack voltage: present, plausible, and equal to its own raw bytes / 4.
    row = _one(
        repo,
        "SELECT value, raw_response FROM measurements "
        "WHERE id > ? AND vehicle_id=? AND key='pack_voltage' AND success=1 "
        "ORDER BY id DESC LIMIT 1", (vm, vid))
    if row is None or row["value"] is None:
        results.append(Result(
            "pack_voltage read and decoded", "FAIL",
            "no successful pack_voltage read this run "
            "(the fast BMS DIDs did not answer -- see the hint below)"))
    else:
        v = row["value"]
        raw = (row["raw_response"] or "").upper()
        results.append(Result(
            "pack_voltage decoded and plausible",
            "PASS" if PACK_V_MIN <= v <= PACK_V_MAX else "FAIL",
            f"{v} V (expect {PACK_V_MIN:.0f}-{PACK_V_MAX:.0f} V), raw 0x{raw}"))
        expect = int(raw, 16) / 4.0 if len(raw) == 4 else None
        results.append(Result(
            "pack_voltage decode = u16 / 4 (Car Scanner scale)",
            "PASS" if expect is not None and abs(expect - v) < 1e-6 else "FAIL",
            f"raw 0x{raw} -> {expect} V" if expect is not None
            else f"raw 0x{raw} is not a 2-byte payload"))
        per_cell = v / MEB_CELL_SLOTS
        results.append(Result(
            "pack_voltage consistent with a 108-cell pack",
            "PASS" if 3.0 <= per_cell <= 4.3 else "FAIL",
            f"{per_cell:.3f} V/cell over {MEB_CELL_SLOTS} cells"))
        if opts.expect_voltage is not None:
            lo = opts.expect_voltage - opts.voltage_tol
            hi = opts.expect_voltage + opts.voltage_tol
            results.append(Result(
                f"pack_voltage within {opts.voltage_tol} V of "
                f"{opts.expect_voltage} V",
                "PASS" if lo <= v <= hi else "FAIL",
                f"{v} V vs [{lo}, {hi}]"))

    # 3. State of charge.
    soc = _one(
        repo,
        "SELECT value, raw_response FROM measurements "
        "WHERE id > ? AND vehicle_id=? AND key='soc_abs' AND success=1 "
        "ORDER BY id DESC LIMIT 1", (vm, vid))
    if soc is None or soc["value"] is None:
        results.append(Result(
            "soc_abs read and decoded", "FAIL",
            "no successful soc_abs read this run"))
    else:
        v = soc["value"]
        raw = (soc["raw_response"] or "").upper()
        results.append(Result(
            "soc_abs decoded and in range",
            "PASS" if 0.0 <= v <= 100.0 else "FAIL",
            f"{v} % (expect 0-100), raw 0x{raw}"))
        expect = int(raw, 16) / 2.5 if len(raw) == 2 else None
        results.append(Result(
            "soc_abs decode = u8 / 2.5",
            "PASS" if expect is not None and abs(expect - v) < 1e-6 else "FAIL",
            f"raw 0x{raw} -> {expect} %" if expect is not None
            else f"raw 0x{raw} is not a 1-byte payload"))

    # 4. Per-cell sweep.
    cells = _one(
        repo,
        "SELECT COUNT(*) AS n, COUNT(DISTINCT cell_index) AS distinct_n, "
        "MIN(voltage_v) AS lo, MAX(voltage_v) AS hi "
        "FROM cell_voltages WHERE id > ? AND vehicle_id=?",
        (state["marks"]["cell_voltages"], vid))
    n = cells["n"] or 0
    if n == 0:
        results.append(Result(
            "per-cell sweep stored", "FAIL",
            "no cell_voltages rows this run"))
    else:
        lo, hi = cells["lo"], cells["hi"]
        ok = (n >= opts.min_cells and cells["distinct_n"] == n
              and CELL_V_MIN <= lo and hi <= CELL_V_MAX)
        results.append(Result(
            "per-cell sweep stored and plausible",
            "PASS" if ok else "FAIL",
            f"{n} cells, {lo:.4f}-{hi:.4f} V "
            f"(expect >= {opts.min_cells} cells, each "
            f"{CELL_V_MIN}-{CELL_V_MAX} V)"))

    # 5. The row the dashboard reads.
    snap = _one(
        repo,
        "SELECT * FROM battery_measurements WHERE id > ? AND vehicle_id=? "
        "ORDER BY id DESC LIMIT 1",
        (state["marks"]["battery_measurements"], vid))
    if snap is None:
        results.append(Result(
            "battery_measurements row written", "FAIL",
            "no snapshot row this run"))
    else:
        present = {
            "pack_voltage_v": snap["pack_voltage_v"],
            "soc": snap["soc_abs_pct"] if snap["soc_abs_pct"] is not None
                   else snap["soc_normal_pct"],
            "cell_min_v": snap["cell_min_v"],
            "cell_max_v": snap["cell_max_v"],
        }
        missing = [k for k, v in present.items() if v is None]
        results.append(Result(
            "battery_measurements row holds pack voltage, SoC and cells",
            "PASS" if not missing else "FAIL",
            f"pack_voltage_v={snap['pack_voltage_v']} V, "
            f"soc_abs_pct={snap['soc_abs_pct']} %, "
            f"cell_min_v={snap['cell_min_v']}, cell_max_v={snap['cell_max_v']}"
            + (f" -- NULL: {', '.join(missing)}" if missing else "")))

    # 6. Read-only guarantee, over this run's transmissions.
    rows = repo.conn.execute(
        "SELECT payload, purpose FROM tx_log "
        "WHERE id > ? AND direction='TX'",
        (state["marks"]["tx_log"],)).fetchall()
    offenders = []
    for r in rows:
        payload = (r["payload"] or "").strip()
        if len(payload) < 2:
            continue
        try:
            sid = int(payload[:2], 16)
        except ValueError:
            continue          # an AT command, not a vehicle request
        if sid not in ALLOWED_TX:
            offenders.append(f"{payload} ({r['purpose']})")
    results.append(Result(
        "only read-only services were transmitted",
        "PASS" if not offenders else "FAIL",
        f"{len(rows)} TX frames audited" if not offenders
        else f"offenders: {offenders[:3]}"))

    # 7. Informational: a BMS DID answering NRC 0x31 is the car saying "that
    # identifier is not supported here", which is normal (e.g. hv_serial_vin on
    # the simulator). A read that gets *silence* instead is the Track B /
    # ATSP6-residue symptom and is worth a warning.
    counts = _one(
        repo,
        "SELECT "
        "SUM(CASE WHEN error LIKE 'UDS negative response%' THEN 1 ELSE 0 END) "
        "  AS nrc, "
        "SUM(CASE WHEN error IS NULL OR error NOT LIKE 'UDS negative response%' "
        "    THEN 1 ELSE 0 END) AS silent "
        "FROM measurements "
        "WHERE id > ? AND vehicle_id=? AND ecu='bat_mgmt' AND success=0",
        (vm, vid))
    answered = _one(
        repo,
        "SELECT COUNT(*) AS n FROM measurements "
        "WHERE id > ? AND vehicle_id=? AND ecu='bat_mgmt' AND success=1",
        (vm, vid))
    nrc = counts["nrc"] or 0
    silent = counts["silent"] or 0
    detail = (f"{answered['n']} answered, {nrc} NRC (DID not supported on "
              f"this car)")
    if silent:
        detail += f", {silent} SILENT (the Track B symptom)"
    results.append(Result(
        "no BMS read dropped in silence",
        "WARN" if silent else "PASS", detail, essential=False))

    return results


# -- output ------------------------------------------------------------------
def _print_report(cfg: Config, state: dict, results: list[Result],
                  opts: Options, ref: dict) -> int:
    if cfg.get("adapter.type") == "elm327_tcp":
        where = f"{cfg.get('adapter.tcp_host')}:{cfg.get('adapter.tcp_port')}"
    else:
        where = cfg.get("adapter.port", "")
    print(f"Adapter: {cfg.get('adapter.type')} {where}".rstrip())
    print(f"Database: {cfg.db_path}")
    print(f"VIN: {state['vin']}")
    ecus = ", ".join(f"{k}={v}" for k, v in state["ecus"].items())
    print(f"ECU discovery: {ecus}")
    print()

    for r in results:
        print(f"[{r.status}] {r.name}" + (f" - {r.detail}" if r.detail else ""))

    if ref:
        print("\nCar Scanner reference (imported; NOT a pass/fail target, the")
        print("car has since changed charge):")
        for key in ("pack_voltage", "soc_abs", "soc_normal"):
            if key in ref:
                value, ts = ref[key]
                print(f"  {key:<12} {value} @ {ts}")

    failed = [r for r in results if r.essential and r.status == "FAIL"]
    passed = sum(1 for r in results if r.status == "PASS")
    warned = sum(1 for r in results if r.status == "WARN")
    print(f"\n{passed} passed, {len(failed)} failed, {warned} warning(s)")

    if failed:
        print("FAILED:")
        for r in failed:
            print(f"  - {r.name}: {r.detail}")
        if not any(r.name.startswith("BMS answers") for r in failed) \
                and any(r.name == "pack_voltage read and decoded"
                        for r in failed):
            print("\nHint: discovery found the BMS but the collection cycle did")
            print("not read it. The collector probes the 11-bit energy/climate")
            print("modules between BMS reads; if the clone does not recover")
            print("29-bit framing after an ATSP6 excursion, the BMS reads that")
            print("follow are the ones that fail. See")
            print("docs/MEB_NO_RESPONSE_HANDOFF.md Track B / open question 5.")
        return 1

    print("RESULT: the collector stored physically consistent HV battery data")
    print("(pack voltage, SoC, a per-cell sweep) and stayed read-only.")
    return 0


# -- configuration -----------------------------------------------------------
def sim_config(tcp_port: int, db_path: str) -> Config:
    """config.yaml pointed at the built-in simulator and a scratch database."""
    with open(DEFAULT_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("database", {})["path"] = db_path
    adapter = data.setdefault("adapter", {})
    adapter["type"] = "elm327_tcp"
    adapter["tcp_host"] = "127.0.0.1"
    adapter["tcp_port"] = tcp_port
    collector = data.setdefault("collector", {})
    collector["read_cell_voltages"] = True
    # Long enough that exactly one slow phase (the cell sweep) runs, so the
    # per-cell check is deterministic rather than timing-dependent.
    collector["slow_poll_interval"] = 3600.0
    return Config(data)


def _load(args) -> Config:
    cfg = load_config(args.config)
    if args.port:
        cfg._data.setdefault("adapter", {})["port"] = args.port
    return cfg


def _parse(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live collector acceptance check (reads the DB back)")
    p.add_argument("--config", default=None, help="config file override")
    p.add_argument("--port", default=None,
                   help="override adapter.port (e.g. COM4)")
    p.add_argument("--cycles", type=int, default=2,
                   help="collection cycles (default 2)")
    p.add_argument("--min-cells", type=int, default=90,
                   help="minimum cells for the sweep to pass (default 90)")
    p.add_argument("--expect-voltage", type=float, default=None,
                   help="assert the pack voltage is within --voltage-tol")
    p.add_argument("--voltage-tol", type=float, default=0.25,
                   help="tolerance for --expect-voltage (default 0.25 V)")
    p.add_argument("--sim", action="store_true",
                   help="run against the built-in simulator (offline self-test)")
    p.add_argument("--tcp-port", type=int, default=35211,
                   help="simulator TCP port for --sim (default 35211)")
    p.add_argument("--db", default="data/diag_acceptance.db",
                   help="scratch database for --sim")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    opts = Options(cycles=args.cycles, min_cells=args.min_cells,
                   expect_voltage=args.expect_voltage,
                   voltage_tol=args.voltage_tol)

    from diagnostic.interface import AdapterNotFoundError, CommunicationError

    server = None
    if args.sim:
        from simulator.vehicle import SimServer
        db = Path(args.db)
        db.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{db}{suffix}").unlink()
            except OSError:
                pass
        cfg = sim_config(args.tcp_port, str(db))
        server = SimServer("127.0.0.1", args.tcp_port)
        server.start()
    else:
        cfg = _load(args)

    try:
        try:
            state = collect(cfg, opts.cycles)
        except (AdapterNotFoundError, CommunicationError) as exc:
            print(f"\nCould not reach the adapter/vehicle: {exc}")
            print("This is NOT a data failure. Check, in order:")
            print("  1. adapter plugged in, ignition ON (press start without the")
            print("     brake) or ready mode;")
            print("  2. Windows Bluetooth -> the dongle -> Connect (a"
                  " paired-but-idle")
            print("     SPP port raises 'semaphore timeout' / OSError 22);")
            print("  3. the port is the OUTGOING 'Serial over Bluetooth link'")
            print("     (COM3 here; try --port COM4);")
            print("  4. nothing else holds the port; run"
                  " `python tools/doctor.py --all-ports`.")
            return 2

        with Repository(cfg.db_path) as repo:
            results = evaluate(repo, state, opts)
            ref = imported_reference(repo, state["vehicle_id"])
        return _print_report(cfg, state, results, opts, ref)
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
