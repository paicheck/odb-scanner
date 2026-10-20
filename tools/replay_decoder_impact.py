"""Measure what the decoder repairs changed, using only stored raw bytes.

WHY THIS EXISTS
---------------
The registry now decodes 25 electric-drive DIDs as float32, reads four fields
from 0x1E32, and exposes cell/sensor indices. Existing rows in
`data/odb_scanner.db` were written by the OLD decoders, so their `value` /
`text_value` columns still contain the old readings even though
`raw_response` holds the bytes that would decode differently today.

This tool is the audit trail between those two states. It never talks to a car
and never writes to the database:

  * read every stored real-vehicle response with a hex payload;
  * re-decode it with the CURRENT registry;
  * compare the stored value with the fresh value;
  * summarise changes by key, with examples.

Raw bytes remain the source of truth. The output is a CSV and a Markdown
summary that can be committed next to the research package.

Usage:
    python tools/replay_decoder_impact.py
    python tools/replay_decoder_impact.py --db data/odb_scanner.db \
        --out research/measurement_discovery/implementation_impact
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decoders.bms import COMPUTED_KEYS  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

DEFAULT_DB = Path("data/odb_scanner.db")
DEFAULT_OUT = Path("research/measurement_discovery/implementation_impact")


@dataclass
class KeyImpact:
    key: str
    unit: str
    rows: int = 0
    changed: int = 0
    newly_decoded: int = 0
    now_failed: int = 0
    unchanged: int = 0
    old_values: list[float] = field(default_factory=list)
    new_values: list[float] = field(default_factory=list)
    added_fields: set[str] = field(default_factory=set)
    examples: list[dict] = field(default_factory=list)

    def add_example(self, ts: str, raw: str, old, new) -> None:
        if len(self.examples) >= 3:
            return
        self.examples.append({"ts": ts, "raw": raw, "old": old, "new": new})

    def as_row(self) -> dict:
        return {
            "key": self.key,
            "unit": self.unit,
            "rows": self.rows,
            "changed": self.changed,
            "newly_decoded": self.newly_decoded,
            "now_failed": self.now_failed,
            "unchanged": self.unchanged,
            "old_min": _range(self.old_values)[0],
            "old_max": _range(self.old_values)[1],
            "new_min": _range(self.new_values)[0],
            "new_max": _range(self.new_values)[1],
            "added_fields": ",".join(sorted(self.added_fields)),
            "examples": json.dumps(self.examples, ensure_ascii=False),
        }


def _range(values: list[float]) -> tuple[float | None, float | None]:
    finite = [v for v in values if isinstance(v, (int, float))
              and math.isfinite(float(v))]
    if not finite:
        return None, None
    return round(min(finite), 6), round(max(finite), 6)


def _hex(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().replace(" ", "")
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return text


def _old_json(text) -> dict:
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= max(1e-9, abs(float(a)) * 1e-9)
    return a == b


def _decode_impact(spec, raw: str, old_value, old_text):
    """Return (kind, old_summary, new_summary, added_fields).

    kind is one of changed | newly_decoded | now_failed | unchanged.
    """
    if spec is None or spec.decode is None:
        return "unchanged", None, None, set()
    new = spec.decode_value(bytes.fromhex(raw))
    if isinstance(new, dict):
        old = _old_json(old_text)
        common = set(old) & set(new)
        differs = any(
            isinstance(new[k], (int, float))
            and not _same(old.get(k), new[k]) for k in common
        )
        added = set(new) - set(old)
        if not old:
            return "newly_decoded", old or None, new, added
        if differs:
            return "changed", old, new, added
        return "unchanged", old, new, added
    if new is None:
        return ("now_failed" if old_value is not None else "unchanged",
                old_value, new, set())
    if old_value is None:
        return "newly_decoded", old_value, new, set()
    if not _same(old_value, new):
        return "changed", old_value, new, set()
    return "unchanged", old_value, new, set()


def replay(db_path: Path, out_base: Path, profile: str = "meb",
           baseline_ref: str = "e6d9fbd") -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    registry = build_default_registry(profile)
    specs = {s.key: s for s in registry.all()}

    rows = con.execute(
        "SELECT ts, key, unit, value, text_value, raw_response, provenance "
        "FROM measurements "
        "WHERE success=1 AND raw_response IS NOT NULL AND raw_response<>'' "
        "AND provenance NOT LIKE 'imported%' AND provenance<>'simulated' "
        "ORDER BY key, ts"
    ).fetchall()

    impacts: dict[str, KeyImpact] = {}
    skipped_unknown = 0
    skipped_nonhex = 0
    for r in rows:
        key = r["key"]
        spec = specs.get(key)
        if spec is None:
            skipped_unknown += 1
            continue
        raw = _hex(r["raw_response"])
        if raw is None:
            skipped_nonhex += 1
            continue
        impact = impacts.setdefault(
            key, KeyImpact(key=key, unit=r["unit"] or spec.unit or ""))
        impact.rows += 1
        kind, old, new, added = _decode_impact(
            spec, raw, r["value"], r["text_value"])
        impact.added_fields |= added
        if isinstance(old, (int, float)):
            impact.old_values.append(float(old))
        if isinstance(new, (int, float)):
            impact.new_values.append(float(new))
        if kind == "changed":
            impact.changed += 1
            impact.add_example(r["ts"], raw, old, new)
        elif kind == "newly_decoded":
            impact.newly_decoded += 1
            impact.add_example(r["ts"], raw, old, new)
        elif kind == "now_failed":
            impact.now_failed += 1
            impact.add_example(r["ts"], raw, old, new)
        else:
            impact.unchanged += 1

    out_base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_base.with_suffix(".csv")
    md_path = out_base.with_suffix(".md")

    ordered = sorted(impacts.values(), key=lambda i: i.key)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(KeyImpact("", "").as_row()))
        writer.writeheader()
        for impact in ordered:
            writer.writerow(impact.as_row())

    changed = [i for i in ordered if i.changed or i.newly_decoded
               or i.now_failed]
    total_rows = sum(i.rows for i in ordered)
    total_changed = sum(i.changed for i in ordered)
    total_new = sum(i.newly_decoded for i in ordered)
    total_failed = sum(i.now_failed for i in ordered)
    unchanged = sum(i.unchanged for i in ordered)

    new_keys = sorted(set(specs) - _old_registry_keys(baseline_ref, profile))
    no_rows = sorted(set(specs) - set(impacts))
    derived = sorted(COMPUTED_KEYS)
    lines = [
        "# Decoder implementation impact",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Database: `{db_path}` (read-only)",
        f"Profile: `{profile}`",
        "",
        "This report re-decodes stored **real-vehicle** raw bytes with the",
        "current registry. It does not rewrite the database. Stored values",
        "come from the old decoders and are shown only for comparison.",
        "",
        "## Summary",
        "",
        f"- Raw vehicle rows re-decoded: **{total_rows:,}**",
        f"- Values changed by the repairs: **{total_changed:,}**",
        f"- Values newly decodable: **{total_new:,}**",
        f"- Values that now fail to decode: **{total_failed:,}**",
        f"- Values unchanged: **{unchanged:,}**",
        f"- Rows skipped (key no longer in registry): {skipped_unknown:,}",
        f"- Rows skipped (non-hex raw payload): {skipped_nonhex:,}",
        f"- Current registry keys with no stored rows: {len(no_rows):,}",
        "",
        "## Keys with changed or newly decoded values",
        "",
        "| key | unit | rows | changed | newly decoded | now failed |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for i in changed:
        lines.append(
            f"| `{i.key}` | {i.unit or '-'} | {i.rows} | {i.changed} | "
            f"{i.newly_decoded} | {i.now_failed} |")
    if not changed:
        lines.append("| _none_ | | | | | |")

    lines += ["", "## Examples", ""]
    for i in changed:
        if not i.examples:
            continue
        lines.append(f"### `{i.key}`")
        for ex in i.examples:
            lines.append(
                f"- `{ex['ts']}` raw `{ex['raw']}`: "
                f"old `{_short(ex['old'])}` -> new `{_short(ex['new'])}`")
        lines.append("")

    lines += [
        "## New registry keys (no historical rows yet)",
        "",
        "These will populate on the next slow rotation:",
        "",
    ]
    lines += [f"- `{k}`" for k in new_keys] or ["- _none_"]
    lines += [
        "",
        "## New calculated metrics",
        "",
        "These are computed from reported values and have no raw DID payload:",
        "",
    ]
    lines += [f"- `{k}`" for k in derived]
    lines += [
        "",
        "## Interpretation",
        "",
        "- A large `changed` count on `dc_link_voltage` / the temperature family",
        "  is expected: those rows stored the old u16/10+1 mis-decode.",
        "- `pack_current` should show no value changes: the arithmetic was",
        "  already correct; only its sign documentation and the Car Scanner",
        "  comparison were wrong.",
        "- `energy_counters` changes are old Ah-as-kWh values replaced by the",
        "  four-field Ah/kWh layout.",
        "- The database is deliberately not rewritten. Raw bytes are preserved",
        "  and this report is the reproducible before/after audit.",
        "",
        f"CSV: `{csv_path.as_posix()}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    con.close()
    return {
        "rows": total_rows,
        "changed": total_changed,
        "newly_decoded": total_new,
        "now_failed": total_failed,
        "unchanged": unchanged,
        "keys_changed": len(changed),
        "csv": str(csv_path),
        "markdown": str(md_path),
    }


def _old_registry_keys(ref: str, profile: str) -> set[str]:
    """Keys in the pre-implementation spec, when available.

    The default ref is the research commit that preceded the decoder repairs.
    The tool is useful outside a git checkout too; an absent ref simply means
    the "new keys" section is empty rather than fatal.
    """
    import subprocess

    try:
        text = subprocess.check_output(
            ["git", "show", f"{ref}:spec/odb_scanner.json"], text=True,
            stderr=subprocess.DEVNULL)
        old = json.loads(text)
        return {m["key"] for m in old["profiles"][profile]["measurements"]}
    except Exception:
        return set()


def _short(value) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)[:180]
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--profile", default="meb")
    parser.add_argument("--baseline-ref", default="e6d9fbd",
                        help="pre-implementation git ref for the new-key list")
    args = parser.parse_args(argv)
    result = replay(args.db, args.out, args.profile, args.baseline_ref)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
