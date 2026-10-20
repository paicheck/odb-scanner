"""Offline analysis of the extracted Car Scanner MEB profile vs the registry.

Read-only. Writes nothing to the vehicle and does not import any transport code.
Run from the repository root:

    python research/measurement_discovery/scripts/analyse_profile.py

Outputs a JSON summary to stdout (and optionally to a file) plus a set of CSVs
in research/measurement_discovery/.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]
EVIDENCE_CSV = ROOT / "docs" / "investigation" / "live_measurement_evidence.csv"

sys.path.insert(0, str(ROOT))


def load_profile_rows() -> list[dict]:
    with EVIDENCE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_registry_keys() -> dict:
    from decoders.registry import build_default_registry

    reg = build_default_registry("meb")
    out = {}
    for spec in reg.all():
        out[spec.key] = {
            "did": spec.did,
            "ecu_key": spec.ecu_key,
            "name": spec.name,
            "unit": spec.unit,
            "slow": spec.slow,
            "doc_status": spec.doc_status.value,
        }
    return out


# Only strip Car Scanner's real variant tags.  Do NOT strip unit/qualifier
# parentheticals such as "(Ah)", "(kWh)", "(normalized)" or "(rad/sec)": those
# are part of the signal identity and collapsing them loses real sub-fields.
VARIANT_RE = re.compile(r"\s*\((?:old|Macan EV, Q6 eTron)\)\s*$")


def base_name(name: str) -> str:
    """Strip a Car Scanner variant tag, preserving units and qualifiers."""
    n = name.strip()
    # remove leading [ecu.label] tag
    n = re.sub(r"^\[[^\]]+\]\s*", "", n)
    n = VARIANT_RE.sub("", n)
    return n.strip()


def main() -> int:
    rows = load_profile_rows()
    regd = load_registry_keys()
    reg_dids = defaultdict(list)
    for k, v in regd.items():
        if v["did"] is not None:
            reg_dids[v["did"]].append(k)

    summary = {}
    summary["profile_rows"] = len(rows)

    # ECU label distribution
    ecu_counts = Counter((r.get("ecu_label") or r["ecu_name"]).strip() for r in rows)
    summary["by_ecu_label"] = dict(ecu_counts)

    # distinct DIDs
    dids = Counter()
    for r in rows:
        did = (r.get("did") or "").strip()
        if did:
            dids[did.upper()] += 1
    summary["distinct_dids"] = len(dids)
    summary["did_multiplicity"] = {d: c for d, c in sorted(dids.items()) if c > 1}

    # underlying signals (dedupe variants)
    signals = {}
    for r in rows:
        name = r["measurement_name"].strip()
        did = (r.get("did") or "").strip().upper()
        b = base_name(name)
        key = (b, did)
        signals.setdefault(key, {"names": [], "rows": []})
        signals[key]["names"].append(name)
        signals[key]["rows"].append(r)

    # coverage vs registry
    covered_dids = set()
    for did in sorted(reg_dids):
        covered_dids.add(f"{did:04X}")

    profile_dids = {d for d in dids}
    reg_did_hex = {f"{d:04X}" for d in reg_dids}

    only_profile = sorted(profile_dids - reg_did_hex)
    only_registry = sorted(reg_did_hex - profile_dids)
    both = sorted(profile_dids & reg_did_hex)

    summary["did_in_both"] = len(both)
    summary["did_only_profile"] = len(only_profile)
    summary["did_only_registry"] = len(only_registry)

    # Which registry keys have a matching profile DID but different layout?
    layout_mismatch = []
    for r in rows:
        did = (r.get("did") or "").strip().upper()
        if did in reg_did_hex:
            continue
        layout_mismatch.append(r)

    # Write machine-readable outputs
    OUT.mkdir(parents=True, exist_ok=True)

    # candidates.csv — every profile signal, marked tracked/untracked
    cand_path = OUT / "profile_signals_all.csv"
    fields = ["base_name", "variants", "ecu_label", "ecu_address", "did", "service",
              "start_byte_id", "data_length", "signed", "byte_order", "scale",
              "divider", "offset", "unit", "formula", "pid_type", "evidence_status",
              "role", "in_registry", "registry_key"]
    with cand_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for (b, did), info in sorted(signals.items()):
            r = info["rows"][0]
            inreg = did in reg_did_hex
            reg_keys = ""
            if re.fullmatch(r"[0-9A-F]{4}", did):
                reg_keys = ";".join(sorted(reg_dids.get(int(did, 16), [])))
            w.writerow({
                "base_name": b,
                "variants": " | ".join(info["names"]),
                "ecu_label": (r.get("ecu_label") or r["ecu_name"]).strip(),
                "ecu_address": (r.get("ecu_address") or "").strip(),
                "did": did,
                "service": (r.get("service") or "").strip(),
                "start_byte_id": (r.get("start_byte_id") or "").strip(),
                "data_length": (r.get("data_length") or "").strip(),
                "signed": (r.get("signed") or "").strip(),
                "byte_order": (r.get("byte_order") or "").strip(),
                "scale": (r.get("scale") or "").strip(),
                "divider": (r.get("divider") or "").strip(),
                "offset": (r.get("offset") or "").strip(),
                "unit": (r.get("unit") or "").strip(),
                "formula": (r.get("formula") or "").strip(),
                "pid_type": (r.get("pid_type") or "").strip(),
                "evidence_status": (r.get("evidence_status") or "").strip(),
                "role": (r.get("role") or "").strip(),
                "in_registry": int(inreg),
                "registry_key": reg_keys,
            })
    summary["signals_file"] = str(cand_path)
    summary["distinct_underlying_signals"] = len(signals)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
