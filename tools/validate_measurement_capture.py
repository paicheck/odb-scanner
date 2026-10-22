"""Validate a future read-only capture against the current decoders.

WHY THIS EXISTS
---------------
The decoder repairs in this package were proven against stored raw bytes, but
the roadmap still wants one project-owned, read-only capture as independent
confirmation. This tool is the checker that capture feeds into. It never opens
a socket and never writes to the database: it takes a file of raw DID data
records (the exact bytes after the 0x62 marker, as the collector stores them),
re-decodes each with the current registry, and reports PASS/FAIL/INDETERMINATE
per key using the same plausibility ranges the quality registry enforces.

A capture is valid evidence only if it is produced under the read-only rules in
`research/measurement_discovery/validation_capture_protocol.md` -- this tool
checks the bytes, not the protocol that obtained them.

Input (JSON):
    [
      {"key": "dc_link_voltage", "raw": "43D897DE", "ts": "2026-10-20T12:00:00+00:00"},
      {"key": "energy_counters", "raw": "01E7CC17FE1B74E70C8071E0F3FF868C"}
    ]

Usage:
    python tools/validate_measurement_capture.py --in capture.json
    python tools/validate_measurement_capture.py --in capture.json --strict
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decoders.quality import Plausibility, quality_for  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402


@dataclass
class Verdict:
    key: str
    raw: str
    result: str          # PASS | FAIL | INDETERMINATE
    value: object = None
    unit: str = ""
    issue: str = ""
    expected: object = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "raw": self.raw,
            "result": self.result,
            "value": self.value,
            "unit": self.unit,
            "issue": self.issue,
            "expected": self.expected,
        }


def _hex(raw) -> bytes | None:
    if not raw:
        return None
    try:
        return bytes.fromhex(str(raw).strip().replace(" ", ""))
    except ValueError:
        return None


def _json_safe(value) -> object:
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, dict):
        return json.dumps(_json_safe(value), sort_keys=True)
    return str(value)


def evaluate(captures: list[dict], profile: str = "meb",
             strict: bool = False) -> tuple[list[Verdict], dict]:
    registry = build_default_registry(profile)
    specs = {s.key: s for s in registry.all()}
    verdicts: list[Verdict] = []
    for cap in captures:
        key = cap.get("key")
        raw = _hex(cap.get("raw"))
        expected = cap.get("expected")
        spec = specs.get(key) if key else None
        if not key or spec is None:
            verdicts.append(Verdict(key=key or "", raw=cap.get("raw") or "",
                                    result="FAIL", expected=expected,
                                    issue="unknown key: not in the registry"))
            continue
        if raw is None or spec.decode is None:
            verdicts.append(Verdict(key=key, raw=cap.get("raw") or "",
                                    result="INDETERMINATE", expected=expected,
                                    unit=spec.unit or "",
                                    issue="no decodable raw bytes"))
            continue
        try:
            value = spec.decode(raw)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            verdicts.append(Verdict(key=key, raw=cap.get("raw") or "",
                                    result="FAIL", unit=spec.unit or "",
                                    expected=expected,
                                    issue=f"decoder raised: {exc}"))
            continue

        if strict and expected is not None and not _matches(value, expected):
            verdicts.append(Verdict(key=key, raw=cap.get("raw") or "",
                                    result="FAIL", value=_json_safe(value),
                                    unit=spec.unit or "", expected=expected,
                                    issue="does not match the supplied expected"))
            continue

        if isinstance(value, (int, float)):
            q = quality_for(profile, key)
            check, why = q.check(value)
            result = {Plausibility.OK: "PASS",
                      Plausibility.IMPLAUSIBLE: "FAIL",
                      Plausibility.NO_RANGE: "INDETERMINATE",
                      Plausibility.NOT_NUMERIC: "INDETERMINATE",
                      Plausibility.NO_VALUE: "INDETERMINATE"}[check]
            verdicts.append(Verdict(key=key, raw=cap.get("raw") or "",
                                    result=result, value=_json_safe(value),
                                    unit=spec.unit or "", expected=expected,
                                    issue="" if check is Plausibility.OK
                                    else why))
        else:
            # dict/text decoders have no single numeric range; structural.
            verdicts.append(Verdict(key=key, raw=cap.get("raw") or "",
                                    result="PASS", value=_json_safe(value),
                                    unit=spec.unit or "", expected=expected,
                                    issue="structured value decoded"))
    summary = {
        "total": len(verdicts),
        "pass": sum(1 for v in verdicts if v.result == "PASS"),
        "fail": sum(1 for v in verdicts if v.result == "FAIL"),
        "indeterminate": sum(1 for v in verdicts
                             if v.result == "INDETERMINATE"),
    }
    return verdicts, summary


def _matches(value, expected) -> bool:
    if isinstance(value, (int, float)) and isinstance(expected, (int, float)):
        tolerance = max(1e-6, abs(float(expected)) * 1e-6)
        return abs(float(value) - float(expected)) <= tolerance
    if isinstance(value, dict) and isinstance(expected, dict):
        for k in expected:
            if k not in value or not _matches(value[k], expected[k]):
                return False
        return True
    return value == expected


def load_captures(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("captures", [])
    if not isinstance(data, list):
        raise ValueError("capture file must be a JSON array or {captures: [...]}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="path", type=Path, required=True)
    parser.add_argument("--profile", default="meb")
    parser.add_argument("--strict", action="store_true",
                        help="also compare against an optional 'expected' field")
    parser.add_argument("--out", type=Path, default=None,
                        help="write a JSON report here (default: stdout)")
    args = parser.parse_args(argv)
    captures = load_captures(args.path)
    verdicts, summary = evaluate(captures, args.profile, args.strict)
    report = {"summary": summary, "verdicts": [v.as_dict() for v in verdicts]}
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(payload)
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
