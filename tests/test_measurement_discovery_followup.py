"""Follow-up work for the measurement-discovery decoder repairs.

Covers the pieces that have no vehicle dependency: the catalogue's float32
support, the derived-metric quality records, and the read-only capture checker
that a future approved capture will feed into.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decoders.quality import (  # noqa: E402
    DataKind,
    quality_for,
    quality_registry,
    use_in_scoring,
)


def test_derived_metrics_are_quality_records_not_readings():
    reg = quality_registry("meb")
    for key in ("aux_12v_power_w", "dcdc_power_kw", "dcdc_gw_power_kw",
                "cell_soc_spread_pct", "battery_temp_spread_c",
                "pack_power_kw", "soh_pct"):
        assert key in reg
        assert reg[key].data_kind is DataKind.DERIVED
        assert reg[key].use_in_scoring is True
        assert use_in_scoring("meb", key) is True


def test_derived_metrics_keep_their_ranges():
    assert quality_for("meb", "cell_soc_spread_pct").plausible_max == 100.0
    assert quality_for("meb", "dcdc_power_kw").plausible_max == 3.0
    assert quality_for("meb", "pack_power_kw").plausible_min is not None


def test_float32_family_is_provisional_and_scorable():
    for key in ("dc_link_voltage", "stator_temperature", "motor_maximum_current"):
        q = quality_for("meb", key)
        assert q.use_in_scoring is True
        assert q.plausible_min is not None and q.plausible_max is not None


def test_capture_checker_pass_and_fail():
    from tools.validate_measurement_capture import evaluate

    caps = [
        {"key": "dc_link_voltage", "raw": "43D897DE"},
        {"key": "electric_drive_inflow_coolant_temperature", "raw": "41816D8E"},
        {"key": "pack_current", "raw": "000248FD"},
        {"key": "bogus_key", "raw": "00"},
    ]
    verdicts, summary = evaluate(caps, "meb", strict=False)
    assert summary["pass"] == 3
    assert summary["fail"] == 1
    assert verdicts[-1].issue.startswith("unknown key")
