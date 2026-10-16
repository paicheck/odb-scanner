#!/usr/bin/env python
"""Export all energy-related measurement data and tx_log to CSV for review."""
from __future__ import annotations

import csv
import os
import sqlite3

ROOT = r"C:\Users\kings\OneDrive\Documents\odb_scanner"
OUT_DIR = os.path.join(ROOT, "tools", "exports")
os.makedirs(OUT_DIR, exist_ok=True)

DBS = [
    "data/real_car.db",
    "data/odb_scanner.db",
    "data/odb_scanner_cdrive_20261015.db",
]

ENERGY_KEYS = ("hv_energy_content", "hv_energy_max", "energy_counters")

for db_rel in DBS:
    db_path = os.path.join(ROOT, db_rel)
    if not os.path.exists(db_path):
        print(f"Skipping {db_rel} (not found)")
        continue

    print(f"Exporting {db_rel}...")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # 1. All measurements for energy keys
    q = """SELECT id, ts, key, ecu, service, pid, value, unit, raw_response,
                  decoding_method, provenance, doc_status, success, error
           FROM measurements
           WHERE key IN ({}) OR key LIKE '%energy%'
           ORDER BY ts, key""".format(",".join("?" * len(ENERGY_KEYS)))
    rows = con.execute(q, ENERGY_KEYS).fetchall()

    base = os.path.splitext(os.path.basename(db_rel))[0]
    out_csv = os.path.join(OUT_DIR, f"measurements_{base}.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "ts", "key", "ecu", "service", "pid", "value", "unit",
                    "raw_response", "decoding_method", "provenance", "doc_status",
                    "success", "error"])
        for r in rows:
            w.writerow([r["id"], r["ts"], r["key"], r["ecu"], r["service"],
                        r["pid"], r["value"], r["unit"], r["raw_response"],
                        r["decoding_method"], r["provenance"], r["doc_status"],
                        r["success"], r["error"]])
    print(f"  {len(rows)} measurement rows -> {out_csv}")

    # 2. tx_log for energy-related traffic
    q2 = """SELECT id, ts, direction, ecu, payload, purpose
            FROM tx_log
            WHERE ecu IN ('energy', 'gate')
               OR payload LIKE '%2AB%' OR purpose LIKE '%energy%'
            ORDER BY ts"""
    tx_rows = con.execute(q2).fetchall()

    out_tx = os.path.join(OUT_DIR, f"tx_log_{base}.csv")
    with open(out_tx, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "ts", "direction", "ecu", "payload", "purpose"])
        for r in tx_rows:
            w.writerow([r["id"], r["ts"], r["direction"], r["ecu"],
                        r["payload"], r["purpose"]])
    print(f"  {len(tx_rows)} tx_log rows -> {out_tx}")

    # 3. ECU table
    ecu_rows = con.execute(
        "SELECT * FROM ecus WHERE key IN ('energy', 'gate')"
    ).fetchall()
    out_ecu = os.path.join(OUT_DIR, f"ecus_{base}.csv")
    with open(out_ecu, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "vehicle_id", "key", "name", "tx_id", "rx_id",
                    "status", "doc_status", "first_seen", "last_seen"])
        for r in ecu_rows:
            w.writerow([r["id"], r["vehicle_id"], r["key"], r["name"],
                        r["tx_id"], r["rx_id"], r["status"], r["doc_status"],
                        r["first_seen"], r["last_seen"]])
    print(f"  {len(ecu_rows)} ECU rows -> {out_ecu}")

    con.close()

print(f"\nAll exports in: {OUT_DIR}")
