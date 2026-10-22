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


def test_strict_expected_values_are_compared_and_kept_in_the_report():
    from tools.validate_measurement_capture import evaluate

    caps = [
        {"key": "hv_energy_max", "raw": "04280400", "expected": 53200.0},
        {"key": "hv_energy_content", "raw": "0000057F00000000",
         "expected": 35175.0},
    ]
    verdicts, summary = evaluate(caps, "meb", strict=True)
    assert summary == {"total": 2, "pass": 2, "fail": 0, "indeterminate": 0}
    assert [v.expected for v in verdicts] == [53200.0, 35175.0]

    bad, bad_summary = evaluate(
        [{"key": "hv_energy_max", "raw": "04280400", "expected": 50000.0}],
        "meb", strict=True,
    )
    assert bad_summary["fail"] == 1
    assert bad[0].expected == 50000.0
    assert "does not match" in bad[0].issue


def test_capture_export_and_check_round_trip(tmp_path):
    import sqlite3

    from tools.export_validation_capture import export_captures
    from tools.validate_measurement_capture import evaluate

    db = tmp_path / "cap.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE measurements "
        "(key TEXT, ts TEXT, raw_response TEXT, success INTEGER)")
    con.execute(
        "INSERT INTO measurements VALUES "
        "('dc_link_voltage', '2026-10-20T12:00:00+00:00', '43D897DE', 1)")
    con.commit()
    con.close()

    captures = export_captures(str(db), ("dc_link_voltage",), limit=5)
    assert len(captures) == 1
    assert captures[0]["raw"] == "43D897DE"
    _, summary = evaluate(captures, "meb", strict=False)
    assert summary == {"total": 1, "pass": 1, "fail": 0, "indeterminate": 0}
