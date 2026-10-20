"""Export the latest stored raw payloads as a validation capture file.

WHY THIS EXISTS
---------------
`tools/validate_measurement_capture.py` checks a capture of raw DID data
records, but a capture normally comes from the car. This tool produces the
same file shape from rows the collector already stored, so the validator can be
exercised offline (a dry run of the approved validation) and so the "collector
-> extract -> validate" loop is closed for the day the hardware is present.

It never touches the vehicle and never writes to the database; it only reads
`measurements.raw_response` and emits a JSON array of `{key, raw, ts}`.

Usage:
    python tools/export_validation_capture.py
    python tools/export_validation_capture.py --keys dc_link_voltage,pack_current
    python tools/export_validation_capture.py --out capture.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config  # noqa: E402

# Keys whose decoder is under active validation and that have stored raw bytes.
# The cell/sensor index DIDs (cell_voltage_max_cell etc.) have no stored rows
# yet and will populate on the next live slow rotation.
DEFAULT_KEYS = (
    "dc_link_voltage",
    "electric_drive_inflow_coolant_temperature",
    "stator_temperature",
    "motor_maximum_current",
    "motor_minimum_current",
    "energy_counters",
    "pack_current",
)


def _hex(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().replace(" ", "")
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return text


def export_captures(db_path: str, keys: tuple[str, ...],
                    limit: int) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: list[dict] = []
    for key in keys:
        rows = con.execute(
            "SELECT ts, raw_response FROM measurements "
            "WHERE key=? AND success=1 AND raw_response IS NOT NULL "
            "AND raw_response<>'' ORDER BY ts DESC LIMIT ?",
            (key, limit),
        ).fetchall()
        for r in rows:
            raw = _hex(r["raw_response"])
            if raw is None:
                continue
            out.append({"key": key, "raw": raw, "ts": r["ts"]})
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS),
                        help="comma-separated keys to export")
    parser.add_argument("--limit", type=int, default=5,
                        help="latest N raw rows per key")
    parser.add_argument("--out", type=Path, default=None,
                        help="write JSON here (default: stdout)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    db = args.db or cfg.db_path
    keys = tuple(k.strip() for k in args.keys.split(",") if k.strip())
    captures = export_captures(db, keys, args.limit)
    payload = json.dumps(captures, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {args.out} ({len(captures)} captures)")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
