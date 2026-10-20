"""Build a per-signal coverage table for the 305 Car Scanner MEB signals.

Read-only / offline.  The mapping is deliberately labelled with a confidence so
that a name-similarity guess is never mistaken for a verified decoder match.

Run from the repository root:

    python research/measurement_discovery/scripts/build_profile_coverage.py
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))


def is_between(value: str, low: int, high: int) -> bool:
    try:
        return low <= int(value, 16) <= high
    except (TypeError, ValueError):
        return False


def main() -> int:
    from decoders.registry import build_default_registry

    reg = build_default_registry("meb")
    by_did: dict[int, list] = defaultdict(list)
    for spec in reg.all():
        if spec.did is not None:
            by_did[spec.did].append(spec)

    def best_registry_key(did_hex: str, name: str) -> tuple[str, str]:
        """Return (key, confidence) for a normal DID+name row."""
        try:
            specs = by_did[int(did_hex, 16)]
        except (TypeError, ValueError):
            return "", "none"
        if not specs:
            return "", "none"
        if len(specs) == 1:
            return specs[0].key, "high"
        scored = sorted(
            (
                (SequenceMatcher(None, name.lower(), s.name.lower()).ratio(), s.key)
                for s in specs
            ),
            reverse=True,
        )
        # A weak best match is a hint, not a proof.
        return scored[0][1], "medium" if scored[0][0] >= 0.45 else "low"

    def classify(row: dict) -> tuple[str, str, str, str]:
        """Return status, baseline_key, confidence, note."""
        did = (row.get("did") or "").strip().upper()
        name = (row.get("base_name") or "").strip()
        sbi = (row.get("start_byte_id") or "").strip()

        # Derived / alias rows have no real DID, or a composite pseudo-DID.
        if not did:
            return (
                "derived_or_alias",
                "none",
                "high",
                "Car Scanner derived/alias row; not a UDS DID request.",
            )
        if ";" in did:
            return (
                "derived_or_alias",
                "none",
                "high",
                "Composite pseudo-DID used by Car Scanner for a derived value.",
            )

        # ABS is the only genuinely new profile DID family.
        if is_between(did, 0x1800, 0x1803):
            suffix = {"1800": "fl", "1801": "fr", "1802": "rl", "1803": "rr"}[did]
            return (
                "profile_only_abs",
                f"abs_wheel_speed_{suffix}",
                "high",
                "Profile-supported; requires 0x10 03 on 0x713; not in the Python registry.",
            )

        # 108 cell voltages.
        if is_between(did, 0x1E40, 0x1EAB):
            return (
                "selected_grouped_cell_voltage",
                "cell_voltages",
                "high",
                "Grouped into the single cell_voltages logical entry.",
            )

        # 24 temperature points.
        if is_between(did, 0x1EAE, 0x1EBD) or is_between(did, 0x7425, 0x742C):
            return (
                "selected_grouped_temperature",
                "cell_temperatures",
                "high",
                "Grouped into the single cell_temperatures logical entry.",
            )

        # Extreme cell/sensor values carry a second sub-field.
        if did in {"1E33", "1E34"}:
            if sbi == "2":
                key = "cell_voltage_max_cell" if did == "1E33" else "cell_voltage_min_cell"
                return (
                    "selected_subfield_missing",
                    key,
                    "high",
                    "1-based cell number at bytes 2-3; currently discarded by the decoder.",
                )
            key = "cell_voltage_max" if did == "1E33" else "cell_voltage_min"
            return ("selected_extreme_value", key, "high", "Extreme voltage is selected.")

        if did in {"1E0E", "1E0F"}:
            if sbi == "2":
                key = "bat_temp_max_sensor" if did == "1E0E" else "bat_temp_min_sensor"
                return (
                    "selected_subfield_missing",
                    key,
                    "high",
                    "1-based sensor number at bytes 2-3; currently discarded by the decoder.",
                )
            key = "bat_temp_max" if did == "1E0E" else "bat_temp_min"
            return ("selected_extreme_value", key, "high", "Extreme temperature is selected.")

        if did in {"1E2C", "1E2D"}:
            key = "cell_soc_max" if did == "1E2C" else "cell_soc_min"
            if sbi == "2":
                key += "_cell"
            return (
                "selected_json_only",
                key,
                "high",
                "Value/index are packed into text_value JSON; numeric column is null.",
            )

        # 0x1E32 has four real sub-fields; the parser previously collapsed them.
        if did == "1E32":
            key = {
                "0": "total_charge_ah",
                "4": "total_discharge_ah",
                "8": "total_charge_kwh",
                "12": "total_discharge_kwh",
            }.get(sbi, "energy_counters")
            return (
                "selected_decoder_repair",
                key,
                "high",
                "Current decoder reads offsets 0/4 with the kWh divisor; see decoder_audit.md F2.",
            )

        if did == "7448":
            if "Normal charge port" in name:
                return (
                    "selected_subfield_missing",
                    "charge_port_normal",
                    "high",
                    "Bit 2; useful charging context, currently not exposed.",
                )
            if "Rapid charge port" in name:
                return (
                    "selected_subfield_missing",
                    "charge_port_rapid",
                    "high",
                    "Bit 3; useful charging context, currently not exposed.",
                )
            return ("selected", "charge_mode;op_mode", "medium", "Operation/charge state row.")

        # Known-bad or unresolved selected rows.
        if did == "27C4" and "Windshield" in name:
            return (
                "selected_quarantine",
                "windshield_temperature",
                "high",
                "Five-byte payload; current value ~488,500 C; field/scale unresolved.",
            )
        if did == "42DB":
            return (
                "selected_quarantine",
                "co2_content_interior",
                "high",
                "Byte1 x100 -> 25,400 ppm; u16 -> 1,022 ppm; field/width unresolved.",
            )
        if did == "5867":
            return (
                "selected_quarantine",
                "odometer_2",
                "high",
                "x10 -> 11,150 km vs ~111,020 km on three other odometers; scale unresolved.",
            )
        if did == "1E39":
            return (
                "selected_quarantine",
                "bat_charging_voltage",
                "high",
                "Samples are 0 or 0xFFFF sentinel; not usable as a voltage.",
            )
        if did == "2A53" and "reserved" in name.lower():
            return (
                "selected_quarantine",
                "dc_dc_converter_reserved_current",
                "high",
                "Decodes 243-252 A; impossible for a DC-DC current; meaning unknown.",
            )

        # Known aliases: same DID/physical quantity under two display names.
        if did == "0364" and "HV Auxiliary consumer power" in name:
            return (
                "selected_alias",
                "hv_aux_power",
                "medium",
                "Same DID/quantity as 'Additional high-voltage consumers, power'.",
            )
        if did == "0344":
            key = "rated_torque_on_the_front_axle" if "front" in name.lower() else "rated_torque_on_the_rear_axle"
            return (
                "selected_alias",
                key,
                "medium",
                "Front/rear labels on the same DID; the car is RWD, so the front label is misleading.",
            )

        key, confidence = best_registry_key(did, name)
        if key:
            return ("selected", key, confidence, "DID/name matched to the registry.")
        return ("profile_only_unmapped", "none", "low", "DID is in the profile but no registry key matched.")

    rows = list(csv.DictReader((OUT / "profile_signals_all.csv").open(encoding="utf-8")))
    out_rows = []
    for idx, row in enumerate(rows, 1):
        status, key, confidence, note = classify(row)
        out_rows.append(
            {
                "signal_id": f"P{idx:03d}",
                "base_name": row["base_name"],
                "variants": row["variants"],
                "ecu_label": row["ecu_label"],
                "ecu_address": row["ecu_address"],
                "did": row["did"],
                "sbi": row["start_byte_id"],
                "dl": row["data_length"],
                "unit": row["unit"],
                "profile_formula": row["formula"],
                "pid_type": row["pid_type"],
                "in_registry": row["in_registry"],
                "coverage_status": status,
                "baseline_key": key,
                "mapping_confidence": confidence,
                "note": note,
            }
        )

    fields = list(out_rows[0].keys())
    path = OUT / "profile_coverage.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    from collections import Counter

    print(f"wrote {path} ({len(out_rows)} signals)")
    print("status:", dict(Counter(r["coverage_status"] for r in out_rows)))
    print("confidence:", dict(Counter(r["mapping_confidence"] for r in out_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
