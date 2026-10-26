"""Read-only reproduction of the 2026-10-21 unknown-payload findings.

WHY THIS EXISTS
---------------
`research/measurement_discovery/payload_research_20261021.md` records the
evidence and the proposed actions. This tool re-runs the byte-level checks
against a stored database so the findings can be re-verified later without a
car, a notebook, or copy-pasted SQL.

It is deliberately read-only:

  * SQLite is opened with ``mode=ro`` and ``PRAGMA query_only=ON``;
  * it never connects to an adapter or sends a UDS request;
  * it never writes a report file (stdout only), so a redirect is explicit.

Usage::

    python tools/audit_payload_findings.py
    python tools/audit_payload_findings.py --json
    python tools/audit_payload_findings.py --db data/odb_scanner.db

Exit code is 0 when the report was produced. A failed invariant is reported in
the payload and does not hide the other findings; ``--check`` makes a failed
invariant exit 2 for CI use.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Running `python tools/....py` puts tools/ on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# The four regression payloads from the research dossier.
DCDC_VECTORS = (
    "95020F02F9",
    "96021202F9",
    "94021502F3",
    "94021F02EA",
)
DCDC_TARGET = 0x0508
DCDC_TOLERANCE = 3


def connect_ro(path: str | Path) -> sqlite3.Connection:
    """Open a database read-only, with a second guard against writes."""
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    uri = f"file:{resolved.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rows(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT ts, value, text_value, raw_response FROM measurements "
        "WHERE key=? AND success=1 "
        "AND raw_response IS NOT NULL AND raw_response<>'' "
        "ORDER BY ts, id",
        (key,),
    ).fetchall()


def _raw(row: sqlite3.Row) -> bytes:
    try:
        return bytes.fromhex(str(row["raw_response"]))
    except (TypeError, ValueError):
        return b""


def _value_rows(conn: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    """Numeric rows for keys that do not necessarily carry raw bytes."""
    return conn.execute(
        "SELECT ts, value FROM measurements "
        "WHERE key=? AND success=1 AND value IS NOT NULL "
        "ORDER BY ts, id",
        (key,),
    ).fetchall()


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# ---------------------------------------------------------------- checks
def audit_dcdc_complement(conn: sqlite3.Connection) -> dict:
    """0x2A53[1:3] + 0x2A53[3:5] should be ~0x0508, not two currents."""
    rows = _rows(conn, "dc_dc_converter_reserved_current")
    sums = []
    bad = []
    for row in rows:
        raw = _raw(row)
        if len(raw) < 5:
            continue
        total = int.from_bytes(raw[1:3], "big") + int.from_bytes(raw[3:5], "big")
        sums.append(total)
        if abs(total - DCDC_TARGET) > DCDC_TOLERANCE:
            bad.append({"ts": row["ts"], "raw": raw.hex().upper(), "sum": total})
    return {
        "key": "dc_dc_converter_reserved_current",
        "rows": len(sums),
        "target": DCDC_TARGET,
        "tolerance": DCDC_TOLERANCE,
        "min_sum": min(sums) if sums else None,
        "max_sum": max(sums) if sums else None,
        "out_of_tolerance": bad,
        "pass": bool(sums) and not bad,
    }


def audit_charging_voltage(conn: sqlite3.Connection) -> dict:
    """0x1E39 FFFF is a sentinel, not 2,047.97 V."""
    from decoders.registry import build_default_registry

    spec = build_default_registry("meb").get("bat_charging_voltage")
    rows = _rows(conn, "bat_charging_voltage")
    sentinels = []
    values = []
    scale = 0.0312499784501336
    for row in rows:
        raw = _raw(row)
        if len(raw) < 2:
            continue
        two = raw[:2]
        unsigned = int.from_bytes(two, "big")
        values.append(unsigned * scale)
        if two in (b"\xff\xff", b"\xff\xfe"):
            sentinels.append({
                "ts": row["ts"],
                "raw": two.hex().upper(),
                "unsigned_v": round(unsigned * scale, 4),
                "decoder": spec.decode_value(two),
            })
    return {
        "key": "bat_charging_voltage",
        "rows": len(values),
        "sentinel_rows": sentinels,
        "sentinel_would_decode_v": round(65535 * scale, 4),
        "max_unsigned_v": round(max(values), 4) if values else None,
        "live_decoder_returns_none": all(
            item["decoder"] is None for item in sentinels),
        "pass": bool(sentinels) and all(
            item["decoder"] is None for item in sentinels),
    }


def audit_odometer_counter(conn: sqlite3.Connection) -> dict:
    """0x5867 is a 10 km/LSB counter; x10 should track the odometer delta."""
    counter = _rows(conn, "odometer_2")
    odometer = _value_rows(conn, "odometer_km")
    counter_points = []
    for row in counter:
        raw = _raw(row)
        if len(raw) >= 4:
            counter_points.append(
                (row["ts"], int.from_bytes(raw[2:4], "big") * 10.0))
    odo_points = []
    for row in odometer:
        value = _number(row["value"])
        if value is not None:
            odo_points.append((row["ts"], value))
    counter_delta = (counter_points[-1][1] - counter_points[0][1]
                     if len(counter_points) >= 2 else None)
    odo_delta = (odo_points[-1][1] - odo_points[0][1]
                 if len(odo_points) >= 2 else None)
    within = (counter_delta is not None and odo_delta is not None
              and abs(counter_delta - odo_delta) <= 10.0)
    return {
        "key": "odometer_2",
        "counter_rows": len(counter_points),
        "odometer_rows": len(odo_points),
        "counter_first": counter_points[0] if counter_points else None,
        "counter_last": counter_points[-1] if counter_points else None,
        "counter_delta_km": counter_delta,
        "odometer_delta_km": odo_delta,
        "x100_delta_km": (counter_delta * 10
                          if counter_delta is not None else None),
        "pass": within,
    }


def audit_windshield_candidate(conn: sqlite3.Connection) -> dict:
    """Byte 0 x 0.1 should track outside_temp; the u16 decode is refuted."""
    from decoders.registry import build_default_registry

    spec = build_default_registry("meb").get("windshield_temperature")
    outside = {
        row["ts"]: _number(row["value"])
        for row in _value_rows(conn, "outside_temp")
    }
    pairs = []
    old_values = []
    for row in _rows(conn, "windshield_temperature"):
        raw = _raw(row)
        if not raw:
            continue
        old = spec.decode_value(raw)
        if isinstance(old, (int, float)):
            old_values.append(float(old))
        ambient = outside.get(row["ts"])
        if ambient is not None:
            pairs.append((raw[0] * 0.1, ambient))
    errors = [abs(a - b) for a, b in pairs]
    mae = sum(errors) / len(errors) if errors else None
    return {
        "key": "windshield_temperature",
        "pairs": len(pairs),
        "candidate": "u8[0] * 0.1",
        "mean_absolute_error_c": round(mae, 3) if mae is not None else None,
        "max_absolute_error_c": round(max(errors), 3) if errors else None,
        "current_decode_max": round(max(old_values), 3) if old_values else None,
        "pass": bool(pairs) and mae is not None and mae < 3.0,
    }


def audit_co2_hypotheses(conn: sqlite3.Connection) -> dict:
    """0x42DB is constant 03FE: byte-1 x100 or whole-u16, not both."""
    rows = _rows(conn, "co2_content_interior")
    payloads = Counter()
    for row in rows:
        raw = _raw(row)
        if raw:
            payloads[raw.hex().upper()] += 1
    return {
        "key": "co2_content_interior",
        "responses": sum(payloads.values()),
        "payloads": dict(payloads),
        "all_03fe": set(payloads) == {"03FE"},
        "byte1_x100_ppm": 0xFE * 100,
        "whole_u16_ppm": 0x03FE,
        "pass": bool(payloads) and set(payloads) == {"03FE"},
    }


def audit_pedal(conn: sqlite3.Connection) -> dict:
    """0xF449 is XX / 2.55 %; a parked car should read 0 %."""
    raws = []
    decoded = []
    for row in _rows(conn, "res_pid_0149"):
        raw = _raw(row)
        if raw:
            raws.append(raw[0])
            decoded.append(raw[0] / 2.55)
    return {
        "key": "res_pid_0149",
        "alias": "accelerator_pedal_pct",
        "responses": len(raws),
        "distinct_raw": sorted(set(raws)),
        "decoded_pct": sorted({round(v, 3) for v in decoded}),
        "pass": bool(raws) and all(0.0 <= v <= 100.0 for v in decoded),
    }


def build_report(path: str | Path) -> dict:
    conn = connect_ro(path)
    try:
        findings = {
            "dcdc_complement": audit_dcdc_complement(conn),
            "charging_voltage_sentinel": audit_charging_voltage(conn),
            "odometer_counter": audit_odometer_counter(conn),
            "windshield_candidate": audit_windshield_candidate(conn),
            "co2_hypotheses": audit_co2_hypotheses(conn),
            "accelerator_pedal": audit_pedal(conn),
        }
    finally:
        conn.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": str(path),
        "read_only": True,
        "findings": findings,
    }


def _print_human(report: dict) -> None:
    print(f"Payload audit  {report['generated_at']}")
    print(f"Database       {report['database']} (read-only)")
    for name, result in report["findings"].items():
        verdict = "PASS" if result.get("pass") else "CHECK"
        print(f"- {name:24s} {verdict:5s}  {json.dumps(result, sort_keys=True)}")
    failed = [k for k, v in report["findings"].items() if not v.get("pass")]
    print()
    print("All checks passed." if not failed
          else f"Needs review: {', '.join(failed)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-run the 2026-10-21 payload findings against a stored DB "
                    "(read-only; never touches the car).")
    parser.add_argument("--db", default=None,
                        help="SQLite database (default: config database.path)")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON object instead of a summary")
    parser.add_argument("--check", action="store_true",
                        help="exit 2 if any invariant fails")
    args = parser.parse_args(argv)
    if args.db is None:
        # The live database may live outside the repo (database.path in
        # config.local.yaml), so the default follows the configuration.
        from config import load_config
        args.db = load_config().db_path

    try:
        report = build_report(args.db)
    except FileNotFoundError as exc:
        print(f"Database not found: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)

    failed = [k for k, v in report["findings"].items() if not v.get("pass")]
    return 2 if args.check and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
