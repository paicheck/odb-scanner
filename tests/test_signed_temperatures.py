"""Temperatures and aux power decode as signed, per the Car Scanner profile.

The profile (research/measurement_discovery/profile_signals_all.csv) declares
0x1E0E/0x1E0F (temperature field, bytes 0-1), both fields of 0x189D and
0x0364 signed. Decoded unsigned, the first sub-zero reading of the winter
became ~1000 C and fed the temperature spread and anomaly baselines.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decoders.quality import quality_for  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

REG = build_default_registry("meb")


@pytest.mark.parametrize("key,raw,want", [
    ("bat_temp_max", "FEC00003", -5.0),
    ("bat_temp_min", "FFC00001", -1.0),
    ("bat_temp_max", "05380003", 20.875),        # CS log value, positive
    ("hv_aux_power", "FFFB", -0.5),
    ("hv_aux_power", "0064", 10.0),
])
def test_signed_scalars(key, raw, want):
    assert REG.get(key).decode_value(bytes.fromhex(raw)) == want


def test_coolant_temps_are_signed():
    got = REG.get("coolant_temps").decode_value(bytes.fromhex("FFC0FF80"))
    assert got == {"outlet_c": -1.0, "inlet_c": -2.0}


def test_short_payload_still_fails_closed():
    assert REG.get("bat_temp_max").decode_value(bytes.fromhex("FE")) is None


def test_hv_aux_power_has_a_plausibility_range():
    q = quality_for("meb", "hv_aux_power")
    assert q.check(6553.1)[0].value == "implausible"
    assert q.check(3.2)[0].value == "ok"


def test_exported_spec_recipes_are_signed():
    spec = json.loads((Path(__file__).resolve().parent.parent / "spec"
                       / "odb_scanner.json").read_text(encoding="utf-8"))
    meb = {m["key"]: m for m in spec["profiles"]["meb"]["measurements"]}
    for key in ("bat_temp_max", "bat_temp_min", "hv_aux_power"):
        assert meb[key]["decode"]["signed"] is True, key
