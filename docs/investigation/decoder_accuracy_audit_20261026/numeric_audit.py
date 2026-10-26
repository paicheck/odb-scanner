"""Offline numeric, unit and cross-client parity audit.

Read-only: parses the generated spec, the Kotlin decoder and the stored
databases with ``mode=ro``.  Never opens an adapter and never writes a database.

    python docs/investigation/decoder_accuracy_audit_20261026/numeric_audit.py

The output is deliberately a list of *observations*, not verdicts: a
non-round constant or a broad plausibility bound is not a defect until an
independent value says so.  The checks that can refute a decoder (encoding
range disjoint from the declared plausibility range, missing Android case,
non-hex "raw" bytes) are reported separately from the checks that merely
deserve review.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from decoders.catalogue import build_catalogue  # noqa: E402
from decoders.quality import build_quality  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs/investigation/decoder_accuracy_audit_20261026")
SPEC = os.path.join(ROOT, "spec/odb_scanner.json")
ASSET = os.path.join(ROOT, "android/app/src/main/assets/spec.json")
DECODE_KT = os.path.join(
    ROOT, "android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt")
HEX = set("0123456789abcdefABCDEF")


def _measurements(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {m["key"]: m for m in doc["profiles"]["meb"]["measurements"]}


def _encoding_range(dec: dict) -> tuple[float, float]:
    bits = 8 * int(dec["length"])
    if dec.get("signed"):
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    else:
        lo, hi = 0, (1 << bits) - 1
    scale = float(dec.get("scale", 1))
    divisor = float(dec.get("divisor", 1))
    addend = float(dec.get("addend", 0))
    return lo * scale / divisor + addend, hi * scale / divisor + addend


def _is_round(v: float) -> bool:
    """True for a constant with a simple rational representation.

    ``0.0312499784501336`` reduces to 31249/999969 -- a 16-digit constant that
    is really an approximation of 1/32.  A scale such as 0.4425 (177/400) or
    -6.1947 (-61947/10000) is a deliberate fit/decimal constant, not a
    suspicious transcription, so the denominator threshold separates the two
    without pretending that every non-power-of-two value is wrong.
    """
    if v == 0 or not (1e-12 < abs(v) < 1e12):
        return False
    from fractions import Fraction

    return Fraction(v).limit_denominator(1_000_000).denominator <= 10_000


def spec_shape(spec: dict[str, dict]) -> dict:
    scalar = {k: m["decode"] for k, m in spec.items()
              if (m.get("decode") or {}).get("kind") == "scalar"}
    special = collections.Counter(
        m["decode"]["name"] for m in spec.values()
        if (m.get("decode") or {}).get("kind") == "special")
    null = sorted(k for k, m in spec.items() if m.get("decode") is None)
    shapes = collections.Counter(
        (d.get("scale"), d.get("divisor"), d.get("addend"))
        for d in scalar.values())
    return {
        "measurements": len(spec),
        "decode_kinds": dict(collections.Counter(
            (m.get("decode") or {}).get("kind", "null")
            for m in spec.values())),
        "special_usage": dict(special),
        "decode_null": null,
        "scalars": len(scalar),
        "scalar_byte_orders": dict(collections.Counter(
            d["byte_order"] for d in scalar.values())),
        "scalar_lengths": dict(collections.Counter(
            d["length"] for d in scalar.values())),
        "scalar_signed": dict(collections.Counter(
            bool(d["signed"]) for d in scalar.values())),
        "scale_shapes": [
            {"scale": s, "divisor": d, "addend": a, "count": n}
            for (s, d, a), n in shapes.most_common()],
    }


def numeric_observations(spec: dict[str, dict], quality: dict) -> dict:
    scalar = {k: m["decode"] for k, m in spec.items()
              if (m.get("decode") or {}).get("kind") == "scalar"}
    odd_constants: dict[str, list[str]] = collections.defaultdict(list)
    for key, dec in scalar.items():
        for name, value in (("scale", dec.get("scale")),
                            ("divisor", dec.get("divisor")),
                            ("addend", dec.get("addend"))):
            if value in (None, 0, 1) and name != "addend":
                continue
            if name == "addend" and value in (None, 0):
                continue
            if not _is_round(float(value)):
                odd_constants[f"{name}={value}"].append(key)

    disjoint = []
    beyond = []
    for key, dec in scalar.items():
        q = quality.get(key)
        if q is None or q.plausible_min is None or q.plausible_max is None:
            continue
        lo, hi = _encoding_range(dec)
        if q.plausible_max < lo or q.plausible_min > hi:
            disjoint.append({"key": key, "encoding": [lo, hi],
                             "plausible": [q.plausible_min, q.plausible_max]})
        elif q.plausible_min < lo - 1e-9 or q.plausible_max > hi + 1e-9:
            beyond.append({"key": key, "encoding": [lo, hi],
                           "plausible": [q.plausible_min, q.plausible_max]})

    # Unit/name screening.  Listed for review, never changed automatically:
    # the Car Scanner profile itself declares some mismatches (e.g. 0x2AB4
    # "Average battery consumption" is unit A in the source).
    rules = {
        "consumption": {"kWh/100km", "kWh", "Wh"},
        "power": {"W", "kW", "%"},
        "voltage": {"V"},
        "current": {"A"},
        "temperature": {"degC"},
        "humidity": {"%"},
        "pressure": {"bar"},
        "energy": {"Wh", "kWh", "Ah"},
        "range": {"km"},
        "resistance": {"mOhm", "Ohm"},
        "speed": {"km/h", "rpm", "rad/s", "m/s"},
    }
    unit_review = []
    for key, m in spec.items():
        name = m["name"].lower()
        for word, allowed in rules.items():
            if word in name and m["unit"] not in allowed:
                unit_review.append({"key": key, "name": m["name"],
                                    "unit": m["unit"], "word": word})

    numeric_no_unit = sorted(
        k for k, m in spec.items()
        if m["unit"] == "" and (m.get("decode") or {}).get("kind") == "scalar")

    return {
        "odd_scale_constants": {k: sorted(v) for k, v in odd_constants.items()},
        "range_disjoint_from_encoding": disjoint,
        "range_extends_beyond_encoding": beyond,
        "unit_name_review": unit_review,
        "scalar_without_unit": numeric_no_unit,
    }


def parity(spec: dict[str, dict]) -> dict:
    asset = _measurements(ASSET)
    spec_special = sorted(
        {m["decode"]["name"] for m in spec.values()
         if (m.get("decode") or {}).get("kind") == "special"})
    with open(DECODE_KT, encoding="utf-8") as fh:
        kotlin = fh.read()
    kotlin_cases = set(re.findall(r'"([a-z0-9_]+)"\s*->', kotlin))
    shared = sorted(set(spec) & set(asset))
    differing = sorted(k for k in shared
                       if json.dumps(spec[k], sort_keys=True)
                       != json.dumps(asset[k], sort_keys=True))
    return {
        "spec_keys": len(spec),
        "asset_keys": len(asset),
        "keys_only_in_spec": sorted(set(spec) - set(asset)),
        "keys_only_in_asset": sorted(set(asset) - set(spec)),
        "measurements_differing": differing,
        "python_special_names": spec_special,
        "kotlin_special_cases": sorted(kotlin_cases),
        "special_names_without_kotlin_case": [
            n for n in spec_special if n not in kotlin_cases],
        "decode_null_keys": sorted(
            k for k, m in spec.items() if m.get("decode") is None),
        "gear_mapping_kotlin": re.search(
            r"private fun gearCode.*?\n    }", kotlin, re.S).group(0),
    }


def _db_stats(rel: str) -> dict:
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return {"db": rel, "absent": True}
    con = sqlite3.connect(f"file:{path.replace(os.sep, '/')}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute("select count(*) from measurements").fetchone()[0]
        success = con.execute(
            "select count(*) from measurements where success=1").fetchone()[0]
        rows = con.execute(
            "select key, ts, value, text_value, raw_response, provenance, "
            "decoding_method from measurements where success=1 "
            "and raw_response is not null and raw_response <> ''").fetchall()
        bad_hex = []
        imported_text = 0
        odd_hex = []
        lengths = collections.Counter()
        for r in rows:
            raw = str(r["raw_response"]).strip()
            if not raw or any(c not in HEX for c in raw):
                if r["decoding_method"] == "carscanner-export":
                    imported_text += 1
                else:
                    bad_hex.append({"key": r["key"], "ts": r["ts"],
                                    "raw": raw[:80]})
                continue
            if len(raw) % 2:
                odd_hex.append({"key": r["key"], "ts": r["ts"], "raw": raw})
                continue
            lengths[len(raw) // 2] += 1
        dup_conflict = con.execute(
            "select key, ts, count(distinct raw_response) n from measurements "
            "where success=1 and raw_response is not null and raw_response<>'' "
            "and provenance not like 'imported%' "
            "group by key, ts having n > 1").fetchall()
        text_keys = [dict(r) for r in con.execute(
            "select key, count(*) n from measurements where success=1 "
            "and value is null and text_value is not null group by key "
            "order by key")]
        failed_with_raw = con.execute(
            "select count(*) from measurements where success=0 "
            "and raw_response is not null and raw_response<>''").fetchone()[0]
        return {
            "db": rel, "rows": total, "success_rows": success,
            "success_rows_with_raw": len(rows),
            "raw_lengths_bytes": dict(sorted(lengths.items())),
            "non_hex_vehicle_raw": bad_hex[:20],
            "non_hex_vehicle_raw_count": len(bad_hex),
            "imported_carscanner_text_rows": imported_text,
            "odd_length_hex": odd_hex[:20],
            "odd_length_hex_count": len(odd_hex),
            "duplicate_key_ts_with_different_raw": [dict(r) for r in dup_conflict],
            "text_valued_keys": text_keys,
            "failed_rows_with_raw": failed_with_raw,
        }
    finally:
        con.close()


def main() -> int:
    spec = _measurements(SPEC)
    registry = build_default_registry("meb")
    quality = build_quality("meb", registry)
    catalogue = build_catalogue("meb", registry)
    data = {
        "spec": spec_shape(spec),
        "numeric": numeric_observations(spec, quality),
        "parity": parity(spec),
        "catalogue": {
            "keys": len(catalogue),
            "with_layout": sum(1 for d in catalogue.values()
                               if d.layout is not None),
            "without_layout": sum(1 for d in catalogue.values()
                                  if d.layout is None),
        },
        "databases": [_db_stats(p) for p in
                      ("data/real_car.db", "data/odb_scanner.db")],
    }
    path = os.path.join(OUT_DIR, "numeric_audit.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    print(path)
    print("spec:", json.dumps(data["spec"]["decode_kinds"]))
    print("scalars:", data["spec"]["scalars"],
          "lengths:", data["spec"]["scalar_lengths"],
          "signed:", data["spec"]["scalar_signed"])
    print("decode-null:", data["spec"]["decode_null"])
    print("range disjoint:", data["numeric"]["range_disjoint_from_encoding"])
    print("unit review:", [(x["key"], x["unit"], x["word"])
                           for x in data["numeric"]["unit_name_review"]])
    print("special without Kotlin case:",
          data["parity"]["special_names_without_kotlin_case"])
    for block in data["databases"]:
        print(block["db"], "success/raw:",
              block.get("success_rows"), block.get("success_rows_with_raw"),
              "non-hex vehicle:", block.get("non_hex_vehicle_raw_count"),
              "imported text:", block.get("imported_carscanner_text_rows"),
              "dup conflicts:", len(block.get(
                  "duplicate_key_ts_with_different_raw", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
