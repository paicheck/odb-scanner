#!/usr/bin/env python
"""Build the DTC description lookup from Car Scanner's embedded databases.

Car Scanner ships two DTC text databases (a custom binary format, not SQLite):

  * dtcdb.OBDII.db  -- generic SAE J2012 descriptions (P/C/B/U codes)
  * dtcdb.VAG.db    -- VW/Audi/Seat/Skoda descriptions, including both the
                       standard P/C/B/U codes and 7 131 VAG-proprietary
                       5-digit fault codes.

Record format (after a 4-byte little-endian size header):
    1 byte tag (0x02) | NUL-terminated code | 4-byte LE length | description

This tool merges the two into one JSON lookup and writes it to
analysis/dtc_descriptions.json:

  {"std": {"U1123": "..."},   # standard P/C/B/U codes, VAG overrides OBD-II
   "vag5": {"00001": "..."}}  # VAG 5-digit fault codes (not yet wired in)

Read-only, offline; it only reads the extracted .db files, never the car.

Usage: python tools/build_dtc_lookup.py [--source DIR]
"""
from __future__ import annotations

import argparse
import json
import os

DEFAULT_SOURCE = r"C:\Users\kings\Documents\CarScanner_Investigation\full_decomp"
OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "analysis", "dtc_descriptions.json",
)


def parse_db(path: str) -> dict[str, str]:
    data = open(path, "rb").read()
    records: dict[str, str] = {}
    off = 4  # skip the 4-byte little-endian size header
    while off < len(data) - 1:
        # tag byte, then NUL-terminated code, then 4-byte LE length
        off += 1
        end = data.index(0, off)
        code = data[off:end].decode("utf-8", errors="replace")
        off = end + 1
        length = int.from_bytes(data[off:off + 4], "little")
        off += 4
        desc = data[off:off + length].decode("utf-8", errors="replace")
        off += length
        records[code] = desc.rstrip("\x00").strip()
    return records


def build(source: str) -> dict:
    obd = parse_db(os.path.join(source, "CarScannerMaui.EmbeddedFiles.dtcdb.OBDII.db"))
    vag = parse_db(os.path.join(source, "CarScannerMaui.EmbeddedFiles.dtcdb.VAG.db"))

    # Standard P/C/B/U codes (5 chars): merge with VAG taking precedence.
    std = dict(obd)
    for code, desc in vag.items():
        if len(code) == 5 and code[:1] in "PCBU":
            std[code] = desc

    # VAG-proprietary 5-digit fault codes (kept for future use; reaching them
    # requires Car Scanner's GetVAGCode byte-reversal, not yet wired in).
    vag5 = {k: v for k, v in vag.items() if k[:1].isdigit()}

    return {"std": std, "vag5": vag5}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--output", default=OUTPUT)
    args = ap.parse_args()

    lookup = build(args.source)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(lookup, fh, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(args.output)
    print(f"wrote {args.output}: {len(lookup['std'])} standard + "
          f"{len(lookup['vag5'])} VAG-5-digit codes ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
