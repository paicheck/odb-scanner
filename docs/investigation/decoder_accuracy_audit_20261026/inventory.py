"""Offline decoder-accuracy audit: baseline inventory and static checks.

Read-only.  Never opens an adapter, never writes to a database.  Run from the
repository root:

    python docs/investigation/decoder_accuracy_audit_20261026/inventory.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from decoders.catalogue import (  # noqa: E402
    OVERLAY,
    build_catalogue,
    validate_catalogue,
)
from decoders.quality import build_quality  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402


def _registry():
    return build_default_registry("meb")


def counts() -> dict:
    reg = _registry()
    specs = reg.all()
    cat = build_catalogue("meb", reg)
    qual = build_quality("meb", reg)
    assoc = {(s.ecu_key, s.did) for s in specs if s.did is not None}
    decoded = [s for s in specs if s.decode is not None]
    derived = {k for k, q in qual.items() if q.data_kind.value == "derived"}
    invalid = {k for k, q in qual.items() if q.verification.value == "invalid"}
    excluded = {k for k, q in qual.items() if not q.use_in_scoring}
    no_layout = [k for k, d in cat.items() if d.layout is None]
    no_unit = [k for k, d in cat.items() if not d.unit and d.key not in
               ("gear", "vin", "battery_serial", "cell_voltage_max_cell",
                "cell_voltage_min_cell", "bat_temp_max_sensor",
                "bat_temp_min_sensor")]
    uncertain_scale = [k for k, d in cat.items()
                       if d.layout is not None
                       and d.confidence.value != "VERIFIED"]
    return {
        "specs": len(specs),
        "unique_ecu_did": len(assoc),
        "distinct_dids": len({s.did for s in specs if s.did is not None}),
        "decoded_specs": len(decoded),
        "raw_only_specs": len(specs) - len(decoded),
        "catalogue_keys": len(cat),
        "catalogue_with_layout": len(cat) - len(no_layout),
        "catalogue_without_layout": len(no_layout),
        "catalogue_without_unit": len(no_unit),
        "catalogue_not_verified": sum(
            1 for d in cat.values() if d.confidence.value != "VERIFIED"),
        "catalogue_verified": sum(
            1 for d in cat.values() if d.confidence.value == "VERIFIED"),
        "derived_keys": len(derived),
        "invalid_keys": sorted(invalid),
        "excluded_keys": len(excluded),
        "uncertain_scale_keys": len(uncertain_scale),
        "overlay_entries": len(OVERLAY),
        "dids_with_multiple_keys": {
            f"0x{did:04X}": [s.key for s in specs if s.did == did]
            for did in sorted({s.did for s in specs if s.did is not None})
            if sum(1 for s in specs if s.did == did) > 1
        },
        "validation_problems": validate_catalogue("meb", reg),
    }


def duplicate_ecu_did_different_units() -> list[dict]:
    """Same (ecu, did) used by keys whose declared units conflict in kind."""
    reg = _registry()
    groups = defaultdict(list)
    for s in reg.all():
        groups[(s.ecu_key, s.did)].append(s)
    out = []
    for (ecu, did), specs in groups.items():
        units = {s.unit for s in specs}
        if len(specs) > 1 and len(units) > 1:
            out.append({"ecu": ecu, "did": f"0x{did:04X}",
                        "keys": [s.key for s in specs],
                        "units": sorted(units)})
    return out


def sentinel_rules() -> list[dict]:
    """Every explicit special-value guard in the Python and Kotlin decoders."""
    out = []
    for rel, patterns in (
        ("decoders/meb.py", (
            (r"CELL_UNUSED\s*=\s*(0x[0-9A-Fa-f]+)", "cell slot unused marker"),
            (r"CELL_T_MAX\s*=\s*(\d+)", "cell temperature absent guard"),
            (r'chunk in \(b"\\xff\\xff", b"\\xff\\xfe"\)', "0x1E39 sentinel"),
            (r"value == 0x03FE", "CO2 not-fitted sentinel"),
            (r"value < 0:\n\s+return None", "negative invalid (0x1E39)"),
        )),
        ("android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt", (
            (r"CELL_UNUSED = (0x[0-9A-Fa-f]+)", "cell slot unused marker"),
            (r"CELL_T_MAX = (\d+)", "cell temperature absent guard"),
        )),
    ):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        for pat, meaning in patterns:
            n = len(re.findall(pat, text))
            if n:
                out.append({"file": rel, "rule": meaning, "occurrences": n})
    return out


def db_inventory() -> dict:
    out = {}
    for rel in ("data/odb_scanner.db", "data/real_car.db",
                "data/observations.db"):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table'")]
            info = {"tables": tables}
            if "measurements" in tables:
                cols = [r[1] for r in con.execute(
                    "pragma table_info(measurements)")]
                info["measurements_columns"] = cols
                info["measurements_rows"] = con.execute(
                    "select count(*) from measurements").fetchone()[0]
                info["distinct_keys"] = con.execute(
                    "select count(distinct key) from measurements").fetchone()[0]
                if "success" in cols:
                    info["success_rows"] = con.execute(
                        "select count(*) from measurements where success=1"
                    ).fetchone()[0]
                if "raw_response" in cols:
                    info["rows_with_raw"] = con.execute(
                        "select count(*) from measurements where raw_response "
                        "is not null and raw_response != ''").fetchone()[0]
                if "provenance" in cols:
                    info["provenance"] = dict(con.execute(
                        "select provenance, count(*) from measurements "
                        "group by provenance").fetchall())
            out[rel] = info
        finally:
            con.close()
    return out


def gear_evidence() -> dict:
    """Observed raw values for the gear key, and the two competing maps."""
    path = os.path.join(ROOT, "data/odb_scanner.db")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("pragma table_info(measurements)")]
        raw_col = "raw_response" if "raw_response" in cols else None
        if raw_col is None:
            return {}
        rows = con.execute(
            "select raw_response, value, text_value, ts from measurements "
            "where key='gear' and success=1 limit 20").fetchall()
        return {"rows": rows}
    finally:
        con.close()


def test_coverage() -> dict:
    """Keys named anywhere in tests/ (crude but explicit) vs catalogue keys."""
    cat = build_catalogue("meb")
    text = ""
    for base, _dirs, files in os.walk(os.path.join(ROOT, "tests")):
        for f in files:
            if f.endswith(".py"):
                text += open(os.path.join(base, f), encoding="utf-8",
                             errors="replace").read()
    named = {k for k in cat if re.search(rf"\b{re.escape(k)}\b", text)}
    return {"tested": len(named),
            "untested": len(cat) - len(named),
            "untested_keys": sorted(set(cat) - named)}


def ast_decoder_shapes() -> dict:
    """Count decode functions in meb.py by AST, independent of the registry."""
    path = os.path.join(ROOT, "decoders/meb.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    return {"functions": len(funcs), "names": funcs}


def main() -> int:
    data = {
        "counts": counts(),
        "conflicting_units": duplicate_ecu_did_different_units(),
        "sentinel_rules": sentinel_rules(),
        "databases": db_inventory(),
        "gear": gear_evidence(),
        "test_coverage": test_coverage(),
        "ast": ast_decoder_shapes(),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
