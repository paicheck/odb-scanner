"""Import a Car Scanner CSV export as evidence, labelled as imported.

Car Scanner (Android) can read the VAG MEB DIDs this project cannot yet reach,
because COM3 is held by the phone that owns the Bluetooth link. Its export is
therefore the only real-car ground truth available without the adapter, and the
only way to cross-check the decode factors that decoders/meb.py marks
"ASSUMED, UNVERIFIED".

    python tools/import_carscanner.py "C:\\path\\to\\2026-10-05 20-01-08.csv"
    python tools/import_carscanner.py export.csv --dry-run
    python tools/import_carscanner.py export.csv --anchor 2026-10-05T19:01:08+00:00

WHAT THIS DOES NOT DO
    It never touches the adapter. There is no transport import here at all, so
    this tool cannot transmit anything, read-only or otherwise.

TWO THINGS ABOUT THE FORMAT THAT MATTER

1. The export mixes two classes of reading, and they are not equally trustworthy.

   Vendor-specific PIDs carry a Car Scanner ECU tag -- "[8C.BMS] DC Battery
   voltage", "[19.Gate] HV Battery energy content". Those come from the MEB DID
   map and agree with this project's own test vectors (431.25 V pack, 81.2 %
   SoC, 3.997 V cells, 53200 Wh nominal).

   Generic OBD-II PIDs do not -- "Hybrid/EV Battery System Voltage", "Engine
   coolant temperature", "Engine RPM". On an EV most of those are meaningless,
   and in this export they are actively wrong: the generic pack voltage reads
   1023.984 V against a real 431.25 V, and coolant temperature reads 215 C.
   Importing them as measurements would poison the database with numbers that
   look authoritative. They are excluded unless --allow-generic is passed, and
   even then they are stored under their own keys, never mapped onto a MEB DID.

2. SECONDS is a monotonic counter, not a wall clock.

   The export's time column runs 72302.99 .. 73311.80 -- seconds since some
   device epoch that the file does not record. An absolute timestamp therefore
   has to be anchored, and any anchor is an assumption. By default the filename
   (Car Scanner names the export with the session time) is parsed as LOCAL time
   and converted to UTC, and the tool prints exactly what it assumed. Pass
   --anchor to state it yourself. Every imported row keeps the raw SECONDS in
   its raw_response column so the offset can always be recomputed.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from database.repository import Repository  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

# Provenance and doc_status for imported rows. The provenance column is
# unconstrained TEXT and nothing in the codebase filters on its value, so a
# distinct marker is safe -- and necessary: "reported" would claim the vehicle
# told *this* tool the value, which it did not.
PROVENANCE = "imported: Car Scanner CSV"
DOC_STATUS = "inferred"

# Car Scanner PID name -> odb_scanner measurement key, with the unit this
# project stores it in. Only vendor-specific (ECU-tagged) readings are mapped.
#
# A mapping is a claim that the two numbers describe the same quantity, so
# every entry below is checked against the decoder's own notes: where the
# meb.py note cites a real-car figure ("CS log: raw 1725 -> 431.25 V"), the
# export's observed range is printed beside it in the cross-check so the two
# can be compared without opening either file. A unit the export states that
# disagrees with ours is a refusal, not a conversion -- see the speed comment.
#
# Keys here MUST exist in the meb registry, otherwise the cross-check row is
# meaningless; keys this project cannot decode yet live in EXTRA_MAPPINGS.
MAPPINGS: dict[str, tuple[str, str]] = {
    # name                                  (key,             expected unit)
    "[8C.BMS] State of charge BMS":         ("soc_abs",        "%"),
    "[8C.BMS] State of charge Display":     ("soc_normal",     "%"),
    "[8C.BMS] DC Battery voltage":          ("pack_voltage",   "V"),
    "[8C.BMS] DC Battery Current":          ("pack_current",   "A"),
    "[8C.BMS] Battery temperature":         ("battery_temp",   "degC"),
    "[8C.BMS] Battery maximum temperature": ("bat_temp_max",   "degC"),
    "[8C.BMS] Battery minimum temperature": ("bat_temp_min",   "degC"),
    "[8C.BMS] Dynamic limit for charging in ampere":
        ("dyn_charge_limit", "A"),
    "[8C.BMS] PTC heater battery current":  ("ptc_current",    "A"),
    "[8C.BMS] HV Battery cell with highest voltage":
        ("cell_voltage_max", "V"),
    "[8C.BMS] HV Battery cell with lowest voltage":
        ("cell_voltage_min", "V"),
    "[19.Gate] HV Battery energy content":  ("hv_energy_content", "Wh"),
    "[19.Gate] Maximum energy content of the traction battery":
        ("hv_energy_max", "Wh"),
    "[19.Gate] 12V Battery voltage":        ("aux_12v_voltage", "V"),
    "[19.Gate] DC-DC converter current":    ("dcdc_current",   "A"),
    "[19.Gate] DC-DC converter low voltage": ("dcdc_voltage",   "V"),
}

# Readings worth keeping that no DID in this project decodes yet -- chiefly the
# [19.Gate] block named as future work (12 V statistics, DC-DC, range, average
# consumption), whose underlying DIDs are still unknown. They are imported
# under new keys so that when the DIDs are discovered on the car there is a
# real-car reference to decode against, and they are reported separately
# rather than mixed into the cross-check, where a key the registry does not
# have would read as a decoder result.
EXTRA_MAPPINGS: dict[str, tuple[str, str]] = {
    "[8C.BMS] Battery inlet temperature":    ("bat_inlet_temp",   "degC"),
    "[8C.BMS] Battery outlet temperature":   ("bat_outlet_temp",  "degC"),
    "[8C.BMS] DC Battery power":             ("pack_power",       "kW"),
    "[8C.BMS] DC Battery summ of all cells voltage":
        ("cells_voltage_sum", "V"),
    "[8C.BMS] Total accumulated charge (kWh)":
        ("accum_charge_kwh", "kWh"),
    "[8C.BMS] Total accumulated discharge (kWh)":
        ("accum_discharge_kwh", "kWh"),
    "[8C.BMS] Minimum discharge voltage":    ("min_discharge_v",  "V"),
    "[19.Gate] 12V Battery SoC":             ("gate_lv_soc",      "%"),
    "[19.Gate] 12V Battery current":         ("gate_lv_current",  "A"),
    "[19.Gate] 12V Battery temperature":     ("gate_lv_temp",     "degC"),
    "[19.Gate] 12V Battery sensor temperature":
        ("gate_lv_sensor_temp", "degC"),
    "[19.Gate] 12V Battery adapted capacity":
        ("gate_lv_capacity_ah", "Ah"),
    "[19.Gate] 12V Battery aging by capacity":
        ("gate_lv_aging_capacity", "%"),
    "[19.Gate] 12V Battery aging by power":
        ("gate_lv_aging_power", "%"),
    "[19.Gate] DC-DC converter reserved current":
        ("gate_dcdc_reserved_current", "A"),
    "[19.Gate] Estimated electric power reserve (displayed)":
        ("gate_range_displayed", "miles"),
    "[19.Gate] Estimated electric power reserve (CAN)":
        ("gate_range_can", "miles"),
    "[19.Gate] Estimated electric power reserve (internal value)":
        ("gate_range_internal", "miles"),
    "[19.Gate] Average battery consumption (internal value)":
        ("gate_avg_battery_consumption", "A"),
    "[19.Gate] HV-EM Power limitation":      ("gate_power_limit",  "W"),
    "[19.Gate] HV-EM NV Power limitation":   ("gate_nv_power_limit", "W"),
    "[19.Gate] NV Energy requirement":       ("gate_nv_energy_req", "Wh"),
    "[8105.DC/DC Converter] DC-DC current":  ("dcdc8105_current",  "A"),
    "[8105.DC/DC Converter] DC-DC voltage":  ("dcdc8105_voltage",  "V"),
}
for _cat in range(7):
    # "[19.Gate] Average energy consumption on roads category 4"
    EXTRA_MAPPINGS[f"[19.Gate] Average energy consumption on roads "
                   f"category {_cat}"] = (f"gate_avg_cons_cat{_cat}", "kWh")

# Both DC-DC readings are imported; only one of them can be the registry's
# dcdc_current/dcdc_voltage (0x465B/0x465D), and the export gives no DID to
# say which. Rather than guess, the [19.Gate] pair is mapped to the registry
# keys and the [8105.DC/DC Converter] pair to its own keys: the two agree
# within 0.2 A / 0.1 V in this export, which is the evidence that they are the
# same quantity, not the licence to overwrite one with the other.

# Car Scanner reports temperature in U+2103 (DEGREE CELSIUS); this project
# stores "degC". Same quantity, different spelling -- not a unit conversion.
UNIT_ALIASES = {"\u2103": "degC", "°C": "degC"}

# "[8C.BMS] HV Battery cell voltage #003" -> cell_v_002. The registry numbers
# cells from zero (cell_v_000 is "Cell voltage #001"), so the index is NNN-1.
CELL_RE = re.compile(r"^\[8C\.BMS\] HV Battery cell voltage #(\d{3})$")

# A Car Scanner ECU tag, e.g. "[8C.BMS] " or "[19.Gate] ". Its presence is what
# distinguishes a vendor-specific reading from a generic OBD-II PID.
#
# The prefix is alphanumeric, not just digits: 8C, 01, 03, 19, 8105. Anchoring
# on \d+ instead silently drops every [8C.BMS] reading -- pack voltage, both
# SoC readings, both pack currents, the cell temperatures -- into the generic
# bucket, where they are excluded as untrustworthy. The BMS is exactly where
# the trustworthy data lives, so getting this wrong guts the import while it
# still reports success.
ECU_TAG_RE = re.compile(r"^\[([0-9A-Za-z]+\.[A-Za-z0-9 /]+)\]\s*(.*)$")

FILENAME_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ _](\d{2})-(\d{2})-(\d{2})")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Import a Car Scanner CSV export as labelled evidence. "
                    "Never touches the adapter.")
    ap.add_argument("csv_path", help="Car Scanner export (.csv, ';' delimited)")
    ap.add_argument("--config", default=None, help="config.yaml to use")
    ap.add_argument("--db", default=None,
                    help="database path (default: config's database.path)")
    ap.add_argument("--vin", default=None,
                    help="VIN to attach rows to (default: the newest vehicle "
                         "already in the database)")
    ap.add_argument("--anchor", default=None,
                    help="absolute UTC timestamp for the export's first "
                         "SECONDS value, e.g. 2026-10-05T19:01:08+00:00. "
                         "Default: parsed from the filename as local time.")
    ap.add_argument("--allow-generic", action="store_true",
                    help="also import untagged generic OBD-II PIDs. On an EV "
                         "these are usually meaningless and in this export "
                         "some are wrong (pack voltage reads 1023.984 V "
                         "against a real 431.25 V), so they are excluded "
                         "unless you ask for them.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be imported, write nothing")
    return ap.parse_args(argv)


def resolve_anchor(path: Path, override: str | None,
                   first_seconds: float) -> tuple[datetime, str]:
    """The absolute time of the export's first SECONDS value.

    SECONDS is monotonic from an epoch the file never states, so this is always
    an assumption. Returns the anchor and a sentence describing how it was
    derived, because the caller must show the user what was assumed.
    """
    if override:
        anchor = datetime.fromisoformat(override)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        return anchor, (f"--anchor {override} "
                        f"(given by you, taken as {anchor.isoformat()})")

    m = FILENAME_TS_RE.search(path.name)
    if not m:
        raise SystemExit(
            f"Cannot derive a timestamp anchor from {path.name!r}, and no "
            f"--anchor was given.\n"
            f"The export's SECONDS column is a monotonic counter "
            f"({first_seconds:.2f} ..) with no recorded epoch, so an absolute "
            f"time has to come from somewhere.\n"
            f"Pass --anchor 2026-10-05T19:01:08+00:00 (or the real session "
            f"start in UTC).")
    date, hh, mm, ss = m.groups()
    naive = datetime.strptime(f"{date} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S")
    # Car Scanner names the export with the session time in the phone's local
    # zone. Converting rather than relabelling keeps every stored timestamp
    # genuinely UTC, which is what the rest of the database assumes.
    anchor = naive.astimezone(timezone.utc)
    return anchor, (
        f"filename {path.name!r} parsed as LOCAL time "
        f"{naive.isoformat()} -> UTC {anchor.isoformat()}. "
        f"ASSUMED: the filename is the session start and the phone's clock "
        f"was correct. Pass --anchor to state it yourself.")


def read_rows(path: Path) -> list[dict]:
    """Parse the export. Semicolon-delimited, every field quoted."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh, delimiter=";")
        rows = list(reader)
    if not rows:
        raise SystemExit(f"{path} is empty")
    header = [h.strip().upper() for h in rows[0] if h.strip()]
    if header[:4] != ["SECONDS", "PID", "VALUE", "UNITS"]:
        raise SystemExit(
            f"{path} does not look like a Car Scanner export: "
            f"expected SECONDS;PID;VALUE;UNITS, got {rows[0]!r}")
    out = []
    for lineno, row in enumerate(rows[1:], start=2):
        if len(row) < 4 or not row[0].strip():
            continue
        try:
            seconds = float(row[0])
        except ValueError:
            print(f"  line {lineno}: SECONDS {row[0]!r} is not a number, "
                  f"skipped")
            continue
        out.append({
            "lineno": lineno,
            "seconds": seconds,
            "name": row[1].strip(),
            "value_raw": row[2].strip(),
            "unit": UNIT_ALIASES.get(row[3].strip(), row[3].strip()),
            "raw": ";".join(row[:4]),
        })
    if not out:
        raise SystemExit(f"{path} has a header but no data rows")
    return out


def classify(row: dict) -> tuple[str, str | None, str | None]:
    """(kind, ecu_tag, bare_name) for one row.

    kind is "vendor" for an ECU-tagged reading, "cell" for a per-cell voltage,
    "generic" for an untagged OBD-II PID.
    """
    m = CELL_RE.match(row["name"])
    if m:
        return "cell", "8C.BMS", f"cell #{m.group(1)}"
    m = ECU_TAG_RE.match(row["name"])
    if m:
        return "vendor", m.group(1), m.group(2)
    return "generic", None, row["name"]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path(args.csv_path)
    if not path.is_file():
        print(f"No such file: {path}")
        return 1

    rows = read_rows(path)
    seconds = [r["seconds"] for r in rows]
    first, last = min(seconds), max(seconds)
    anchor, anchor_note = resolve_anchor(path, args.anchor, first)

    def stamp(sec: float) -> str:
        return (anchor + timedelta(seconds=sec - first)).isoformat(
            timespec="seconds")

    cfg = load_config(args.config)
    db_path = args.db or cfg.get("database.path")
    registry = build_default_registry(cfg.get("vehicle.did_profile", "eup"))
    known_keys = {s.key for s in registry.all()}

    print(f"Car Scanner export : {path}")
    print(f"  rows             : {len(rows)}")
    print(f"  SECONDS          : {first:.4f} .. {last:.4f} "
          f"(span {last - first:.1f} s)")
    print(f"  time anchor      : {anchor_note}")
    print(f"  database         : {db_path}")
    print(f"  DID profile      : {cfg.get('vehicle.did_profile', 'eup')} "
          f"({len(known_keys)} keys registered)")
    if args.dry_run:
        print("  DRY RUN          : nothing will be written")

    planned: list[dict] = []
    skipped_generic: dict[str, int] = {}
    skipped_unit: list[str] = []
    skipped_unknown: dict[str, int] = {}
    already: list[str] = []

    repo = Repository(db_path)
    try:
        if args.vin:
            vehicle_id = repo.ensure_vehicle(args.vin)
            vin_note = f"--vin {args.vin}"
        else:
            row = repo.first_vehicle()
            if row is None:
                print("\nNo vehicle in the database and no --vin given. "
                      "Nothing to attach these rows to.")
                return 1
            vehicle_id, vin_note = row["id"], f"existing vehicle {row['vin']}"
        print(f"  vehicle          : {vin_note} (id {vehicle_id})")

        for row in rows:
            kind, tag, bare = classify(row)
            from_extra = False
            # Keep the ECU Car Scanner read from. It is unconstrained TEXT and
            # nothing filters on it, and it is the only record of which module
            # reported a value -- the difference between "[19.Gate] DC-DC
            # converter current" and "[8105.DC/DC Converter] DC-DC current",
            # which the export gives no DID to tell apart. Provenance, not the
            # ecu column, is what marks these rows as imported.
            ecu = tag or "generic"

            if kind == "generic":
                if not args.allow_generic:
                    skipped_generic[row["name"]] = \
                        skipped_generic.get(row["name"], 0) + 1
                    continue
                slug = re.sub(r"[^A-Za-z0-9]+", "_", bare).strip("_").lower()
                key = f"cs_generic_{slug}"
            elif kind == "cell":
                num = int(bare.split("#")[1])
                key = f"cell_v_{num - 1:03d}"
            else:
                # Registry-backed mapping first: those rows can be compared
                # against a decoder. Anything Car Scanner reports that this
                # project cannot decode yet is still worth keeping, under its
                # own key, as a real-car reference for when the DID is found.
                mapped = MAPPINGS.get(row["name"])
                if mapped is None:
                    mapped = EXTRA_MAPPINGS.get(row["name"])
                    from_extra = mapped is not None
                if mapped is None:
                    skipped_unknown[row["name"]] = \
                        skipped_unknown.get(row["name"], 0) + 1
                    continue
                key, expected_unit = mapped
                # Refuse a unit mismatch rather than store a number in the
                # wrong unit. "[8C.BMS] Vehicle speed" is mph here and km/h
                # in the registry, which is why it has no mapping at all.
                if row["unit"] and row["unit"] != expected_unit:
                    skipped_unit.append(
                        f"{row['name']}: export says {row['unit']!r}, "
                        f"{key} is stored in {expected_unit!r}")
                    continue
                # A blank unit means the export gave us nothing to check the
                # scale against, so the mapping is unverifiable for this row.
                if not row["unit"] and expected_unit:
                    skipped_unit.append(
                        f"{row['name']}: export gives no unit, "
                        f"{key} is stored in {expected_unit!r}")
                    continue

            try:
                value = float(row["value_raw"])
            except ValueError:
                skipped_unknown[row["name"]] = \
                    skipped_unknown.get(row["name"], 0) + 1
                continue

            ts = stamp(row["seconds"])
            # Re-running must not duplicate. The raw CSV line is a stable
            # natural key for an imported row.
            dup = repo.conn.execute(
                "SELECT 1 FROM measurements WHERE vehicle_id=? AND ts=? "
                "AND key=? AND provenance=? AND raw_response=? LIMIT 1",
                (vehicle_id, ts, key, PROVENANCE, row["raw"])).fetchone()
            if dup:
                already.append(key)
                continue

            planned.append({
                "ts": ts, "key": key, "value": value,
                "unit": row["unit"], "raw": row["raw"],
                "name": row["name"], "kind": kind, "ecu": ecu,
                "in_registry": key in known_keys,
                "from_extra": from_extra,
            })

        print(f"\nWould import {len(planned)} rows "
              f"({sum(1 for p in planned if p['in_registry'])} matching a "
              f"registered DID key, "
              f"{sum(1 for p in planned if not p['in_registry'])} new keys)")
        if already:
            print(f"  already imported : {len(already)} rows skipped "
                  f"(re-run is idempotent)")
        if skipped_unit:
            print(f"\nRefused -- unit missing or disagreeing with ours "
                  f"({len(skipped_unit)}):")
            for line in dict.fromkeys(skipped_unit):
                print(f"  {line}")
        if skipped_unknown:
            print(f"\nNo mapping ({len(skipped_unknown)} distinct vendor "
                  f"readings -- add to MAPPINGS/EXTRA_MAPPINGS if you want "
                  f"them):")
            for name, count in sorted(skipped_unknown.items()):
                print(f"  {count:>4}  {name}")
        if skipped_generic:
            total = sum(skipped_generic.values())
            print(f"\nExcluded generic OBD-II PIDs ({total} rows, "
                  f"{len(skipped_generic)} distinct) -- not vendor-specific, "
                  f"and on an EV several are wrong. --allow-generic to import "
                  f"them under cs_generic_* keys:")
            for name, count in sorted(skipped_generic.items(),
                                      key=lambda kv: -kv[1])[:12]:
                print(f"  {count:>4}  {name}")
            if len(skipped_generic) > 12:
                print(f"  ... and {len(skipped_generic) - 12} more")

        # The cross-check this tool exists for. For every key this project
        # decodes, print what Car Scanner observed on the real car next to the
        # note's own real-car figure, so the scale factors marked
        # "ASSUMED, UNVERIFIED" can be confirmed without opening either file.
        # Nothing here is computed or re-decoded: the export carries decoded
        # values and no raw bytes, so agreement is evidence, not proof.
        specs = {s.key: s for s in registry.all()}
        cross = [p for p in planned
                 if p["in_registry"] and not p["from_extra"]]
        if cross:
            print(f"\nCross-check against the "
                  f"{cfg.get('vehicle.did_profile', 'eup')} decode registry")
            print("  (left: what Car Scanner observed on this car; "
                  "right: what decoders/*.py says)")
            print("  Read this as consistency, not proof: many notes cite a")
            print("  'CS log' as their source, and the export carries decoded")
            print("  values with no raw bytes, so it cannot re-derive them.")
            print("  What it does fix is the real-car figure and its date.")
            by_key: dict[str, list[float]] = {}
            for p in cross:
                by_key.setdefault(p["key"], []).append(p["value"])
            for key in sorted(by_key):
                vals = by_key[key]
                spec = specs.get(key)
                unit = spec.unit if spec else "?"
                status = spec.doc_status.value if spec else "?"
                lo, hi = min(vals), max(vals)
                shown = f"{lo:g}" if lo == hi else f"{lo:g} .. {hi:g}"
                print(f"  {key:22} {shown:>20} {unit:5} "
                      f"[{status}] n={len(vals)}")
                if spec and spec.notes:
                    print(f"    {'':20} {spec.notes}")

        # Readings kept for reference that no DID here decodes yet.
        extra = [p for p in planned if p["from_extra"]]
        if extra:
            print(f"\nImported with no decoder to check them against "
                  f"({len(set(p['key'] for p in extra))} new keys) -- "
                  f"real-car reference for the DIDs still to be discovered:")
            by_key: dict[str, list[float]] = {}
            for p in extra:
                by_key.setdefault(p["key"], []).append(p["value"])
            for key in sorted(by_key):
                vals = by_key[key]
                lo, hi = min(vals), max(vals)
                shown = f"{lo:g}" if lo == hi else f"{lo:g} .. {hi:g}"
                unit = next((p["unit"] for p in extra if p["key"] == key), "")
                print(f"  {key:26} {shown:>20} {unit:5} n={len(vals)}")

        if args.dry_run:
            print("\nDry run -- nothing written.")
            return 0

        written = 0
        for p in planned:
            repo.record_measurement(
                vehicle_id, p["ts"], p["key"],
                p["ecu"],               # the module Car Scanner read from
                "-",                    # service: none, this is a CSV
                "-",                    # pid: Car Scanner gives names, not DIDs
                p["unit"], PROVENANCE,
                "carscanner-export",   # decoding_method
                DOC_STATUS, p["raw"], value=p["value"])
            written += 1
        print(f"\nImported {written} rows with provenance {PROVENANCE!r}.")
        print("Every row keeps its original CSV line in raw_response, so the "
              "SECONDS offset can always be recomputed.")
        if not args.anchor:
            print("NOTE: the timestamps rest on the filename anchor described "
                  "above. Re-import with --anchor if that assumption is wrong.")
        return 0
    finally:
        repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
