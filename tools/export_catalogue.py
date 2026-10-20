"""Regenerate docs/DATA_CATALOGUE.md: what the database holds, in prose.

Written for a reader who will never see the database -- an AI asked to reason
about these readings, or a person deciding whether a number can be trusted. So
it carries what a schema dump cannot:

  * what each key MEANS, taken from the decode registry, which is the only
    place descriptions live;
  * which rows are real readings and which are derived, imported or simulated;
  * the scale factors that are known to be wrong, so nobody re-derives a
    conclusion from them;
  * how much data there is per key, because "never successfully read" and
    "read once" are different problems.

Regenerate after collecting:

    python tools/export_catalogue.py                 # -> docs/DATA_CATALOGUE.md
    python tools/export_catalogue.py --out -
    python tools/export_catalogue.py --db data/real_car.db

The caveat list is maintained here rather than inferred: each line is a finding
from a live investigation, and a generator cannot rediscover that a decoder is
mis-scaled.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from database.repository import Repository  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402
from diagnostic.ecus import ECUS  # noqa: E402

# Non-measurement tables, with the sentence a reader needs to know what one row
# is. `measurements` is deliberately absent: it is the subject of the report.
OTHER_TABLES = (
    ("cell_voltages", "per-cell voltages, one row per cell per sweep"),
    ("battery_measurements",
     "aggregated battery snapshots (SoC, SoH, CAC, energy counters)"),
    ("charging_sessions", "charging sessions with energy/power/duration"),
    ("charging_samples", "samples taken inside a charging session"),
    ("dtcs", "diagnostic trouble codes seen on the car"),
    ("dtc_occurrences", "each time a DTC was seen, with any freeze frame"),
    ("diagnostic_events",
     "the collector's own log: connections, wakes, charge boundaries, notes"),
    ("tx_log", "every request ever sent to the car (the safety manifest)"),
    ("anomalies", "statistically flagged outliers, with their baseline"),
    ("analysis_results", "stored output of the analysis engine"),
    ("ecus", "modules discovered on the car, with their CAN addressing"),
)

CAVEATS = """\
- Every decoder is `doc_status = experimentally determined` unless a row below says
  otherwise: the layout was reverse-engineered by observing the car, NOT taken from a
  VW document. Treat every scale factor as unproven until a second source confirms it.
- Known BAD scalings - do not trust these values: climate `windshield_temperature`
  (~483476; five-byte payload, field/scale unresolved), `co2_content_interior`
  (25,400 ppm under the old byte1 x100 decode), `bat_charging_voltage`
  (0/0xFFFF sentinels), `odometer_2` (~11,150 km at x10 while three other
  odometers read ~111,020 km), and `dc_dc_converter_reserved_current` (not an
  independent current; it repeats the 15 A field).
- The 25-row electric-drive family is now decoded as IEEE-754 float32, not
  u16/10+1. Its old ~1680 degC / ~1738 V / ~1e8 W values were a decode error.
- The adapter intermittently fails to reach the 29-bit modules (bat_mgmt, eldrive,
  veh_info, dcdc) for a whole rotation. During such spells values are carried forward
  for up to 900 s, so a present value is not necessarily a fresh reading. Prefer the
  `ts` of each row over assuming current.
- `gear` and `vin` are TEXT, not numbers, despite sharing the value column.
- `cell_soc_max`, `cell_soc_min`, `energy_counters`, `coolant_temps`, `battery_serial`
  and similar return structured data: the numeric column is NULL and the JSON is in
  `text_value`.
- `cell_delta_mv`, `cell_soc_spread_pct`, `battery_temp_spread_c`,
  `pack_power_kw`, `aux_12v_power_w`, `dcdc_power_kw`, `dcdc_gw_power_kw` and
  `soh_pct` are DERIVED (calculated/estimated), not read from the car.
- Where a key appears in several modules (odometer, motor rpm, HV current) the copies
  are independent readings of the same quantity and may disagree slightly.
- Rows are keyed by VIN, and the collector also writes `provenance`, which is the
  field to filter on: `reported` is a vehicle reading, `imported: ...` is evidence
  from another tool, `calculated`/`estimated` are derived here, and `simulated` means
  a simulator produced it and it describes no car.
"""


# Per-cell specs are 108 voltage slots and 108 temperature slots in the meb
# registry. Listing them individually buries everything else -- the same reason
# the dashboard rolls them up -- so they collapse into one line each, carrying
# how many slots answered.
CELL_GROUPS = (
    ("cell_v_", "cell_voltages", "Cell voltages (all cells)"),
    ("cell_t_", "cell_temperatures", "Cell temperatures (all sensors)"),
)


def _cell_group(key: str) -> tuple[str, str] | tuple[None, None]:
    for prefix, group, name in CELL_GROUPS:
        if key.startswith(prefix):
            return group, name
    return None, None


def _module_entries(specs, counts) -> list[dict]:
    """One entry per line: {key, name, unit, rows, slots}."""
    entries: list[dict] = []
    index: dict[str, int] = {}
    for spec in specs:
        group, group_name = _cell_group(spec.key)
        key = group or spec.key
        if key in index:
            entry = entries[index[key]]
            entry["rows"] += counts.get(spec.key, 0)
            entry["slots"] += 1
            continue
        index[key] = len(entries)
        entries.append({"key": key, "name": group_name or spec.name,
                        "unit": spec.unit, "rows": counts.get(spec.key, 0),
                        "slots": 1})
    return entries


def _module_order(registry):
    """ECU keys in the order the registry defines them, with their label."""
    order: list[str] = []
    for spec in registry.all():
        if spec.ecu_key and spec.ecu_key not in order:
            order.append(spec.ecu_key)
    return order


def _ecu_name(key: str) -> str:
    return ECUS[key].name if key in ECUS else key


def build_report(repo: Repository, profile: str) -> str:
    out: list[str] = []
    vehicle = repo.first_vehicle()
    registry = build_default_registry(profile)
    specs = {spec.key: spec for spec in registry.all()}

    counts = {row["key"]: row["n"] for row in repo.conn.execute(
        "SELECT key, COUNT(*) n FROM measurements GROUP BY key")}
    # The most recent row per key, for the provenance/ecu annotation. Picking a
    # row at all (rather than aggregating) keeps it to what one row says: a key
    # collected from two modules has no single answer, and silently merging them
    # would invent one.
    sources = {row["key"]: dict(row) for row in repo.conn.execute(
        "SELECT m.key, m.provenance, m.ecu FROM measurements m "
        "WHERE m.ts = (SELECT MAX(ts) FROM measurements WHERE key = m.key) "
        "GROUP BY m.key")}
    span = repo.conn.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM measurements").fetchone()
    by_provenance = list(repo.conn.execute(
        "SELECT provenance, COUNT(*) n FROM measurements "
        "GROUP BY provenance ORDER BY n DESC"))

    if vehicle:
        year = vehicle["year"] or ""
        out.append(f"# Collected data - {vehicle['make']} {vehicle['model']} "
                   f"{vehicle['variant']} {year}".rstrip())
        out.append(f"VIN {vehicle['vin']} | profile={profile} | "
                   f"first seen {vehicle['first_seen']}")
    else:
        out.append("# Collected data")
        out.append(f"profile={profile} | no vehicle in the database")
    out.append("")
    if span and span[2]:
        out.append(f"Measurements {span[0]} -> {span[1]} (UTC)")
        out.append(f"{span[2]} measurement rows across {len(counts)} distinct "
                   f"keys, by source:")
        for provenance, n in by_provenance:
            out.append(f"  - {provenance}: {n}")
    else:
        out.append("No measurements.")
    out.append("")

    # --- by module ----------------------------------------------------------
    out.append("## Measurements by module")
    out.append("")
    covered: set[str] = set()
    for ecu_key in _module_order(registry):
        specs = [s for s in registry.all() if s.ecu_key == ecu_key]
        covered.update(s.key for s in specs)
        entries = _module_entries(specs, counts)
        read = sum(1 for entry in entries if entry["rows"])
        out.append(f"### {ecu_key} - {_ecu_name(ecu_key)} "
                   f"({read} of {len(entries)} read)")
        for entry in entries:
            unit = f" ({entry['unit']})" if entry["unit"] else ""
            flag = "" if entry["rows"] else "  **never read**"
            slots = (f" across {entry['slots']} slots" if entry["slots"] > 1
                     else "")
            out.append(f"- `{entry['key']}` - {entry['name']}{unit} - "
                       f"{entry['rows']} rows{slots}{flag}")
        out.append("")

    # --- everything else ----------------------------------------------------
    extra = [(key, n) for key, n in sorted(counts.items())
             if key not in covered]
    if extra:
        out.append("## Also collected, not in the current registry "
                   "(earlier profile, derived, or imported)")
        for key, n in extra:
            row = sources.get(key, {})
            out.append(f"- `{key}` - {n} rows, "
                       f"provenance={row.get('provenance')}, "
                       f"ecu={row.get('ecu') or '-'}")
        out.append("")

    # --- other tables -------------------------------------------------------
    out.append("## Other tables")
    for table, description in OTHER_TABLES:
        n = repo.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        out.append(f"- `{table}` - {n} rows. {description}")
    sweeps = repo.conn.execute(
        "SELECT COUNT(DISTINCT ts) FROM cell_voltages").fetchone()[0]
    if sweeps:
        per = repo.conn.execute(
            "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM cell_voltages "
            "GROUP BY ts)").fetchone()[0]
        out.append(f"  - cell sweeps: {sweeps}, up to {per} cells per sweep")
    out.append("")

    # --- caveats ------------------------------------------------------------
    out.append("## Caveats (important for analysis)")
    out.append(CAVEATS.rstrip())
    never = [s.key for s in registry.all() if not counts.get(s.key)]
    if never:
        out.append(f"- {len(never)} registry key(s) have never been read "
                   f"successfully and are absent rather than zero: "
                   f"{', '.join('`' + k + '`' for k in never[:12])}"
                   + (", ..." if len(never) > 12 else ""))
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate docs/DATA_CATALOGUE.md from the database")
    parser.add_argument("--db", default=None, help="database (default: config)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=str(ROOT / "docs" / "DATA_CATALOGUE.md"),
                        help="output file, or - for stdout")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    db = args.db or cfg.db_path
    with Repository(db) as repo:
        text = build_report(repo, cfg.get("vehicle.did_profile", "meb"))

    if args.out == "-":
        sys.stdout.write(text)
        return 0
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Wrote {args.out} from {db} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
