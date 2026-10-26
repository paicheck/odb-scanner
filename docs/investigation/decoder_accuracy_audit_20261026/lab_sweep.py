"""Read-only decoder-lab sweep over copies of the stored databases.

The repository's ``Repository`` writes its schema version on open, so this
copies each source database into the temp directory first and never opens the
original for writing.  No vehicle communication.

    python docs/investigation/decoder_accuracy_audit_20261026/lab_sweep.py
"""
from __future__ import annotations

import collections
import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from analysis.decoder_lab import evaluate_all  # noqa: E402
from database.repository import Repository  # noqa: E402

OUT = os.path.join(ROOT, "docs/investigation/decoder_accuracy_audit_20261026")
TMP = os.path.join(tempfile.gettempdir(), "odb_audit_20261026")


def sweep(rel: str) -> dict:
    src = os.path.join(ROOT, rel)
    os.makedirs(TMP, exist_ok=True)
    dst = os.path.join(TMP, os.path.basename(rel))
    shutil.copyfile(src, dst)
    repo = Repository(dst)
    try:
        vehicles = [r["id"] for r in repo.conn.execute(
            "select id from vehicles order by id")]
        results = []
        for vid in vehicles:
            evs = evaluate_all(repo, vid, "meb")
            counts = collections.Counter(e.verdict for e in evs)
            fails = [e for e in evs if e.verdict == "FAIL"]
            results.append({
                "vehicle_id": vid,
                "counts": dict(counts),
                "fails": [e.as_dict() for e in fails],
                "indeterminate": sum(
                    1 for e in evs if e.verdict == "INDETERMINATE"),
                "pass": sum(1 for e in evs if e.verdict == "PASS"),
            })
        return {"db": rel, "vehicles": results}
    finally:
        repo.close()


def integrity(rel: str) -> dict:
    con = sqlite3.connect(f"file:{os.path.join(ROOT, rel)}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("pragma table_info(measurements)")]
        total = con.execute("select count(*) from measurements").fetchone()[0]
        success = con.execute(
            "select count(*) from measurements where success=1").fetchone()[0]
        success_no_raw = con.execute(
            "select count(*) from measurements where success=1 "
            "and (raw_response is null or raw_response='')").fetchone()[0]
        # Keys whose successful rows now have value="" or textual non-numeric
        # values, and keys with no raw bytes at all.
        no_raw_keys = [r[0] for r in con.execute(
            "select distinct key from measurements where success=1 "
            "and (raw_response is null or raw_response='') order by key")]
        json_keys = [r[0] for r in con.execute(
            "select distinct key from measurements where value is null "
            "and text_value is not null and success=1 order by key")]
        return {"db": rel, "columns": cols, "rows": total,
                "success_rows": success, "success_without_raw": success_no_raw,
                "success_keys_without_raw": no_raw_keys,
                "json_valued_keys": json_keys}
    finally:
        con.close()


def main() -> int:
    out = {
        "lab": [sweep(p) for p in ("data/real_car.db", "data/odb_scanner.db")],
        "integrity": [integrity(p) for p in
                      ("data/real_car.db", "data/odb_scanner.db")],
    }
    path = os.path.join(OUT, "lab_sweep.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(path)
    for block in out["lab"]:
        for v in block["vehicles"]:
            print(block["db"], "vehicle", v["vehicle_id"], v["counts"],
                  "fails", len(v["fails"]))
            for f in v["fails"][:12]:
                print("  FAIL", f["key"], f["issues"][:2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
