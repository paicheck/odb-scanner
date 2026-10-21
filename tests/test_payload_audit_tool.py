"""The read-only payload audit tool must reproduce the dossier's vectors.

The fixture writes a tiny database, then the tool is pointed at the same file
with ``mode=ro``. That proves the checks are not hard-coded to the real car's
database and that the tool's SQL works on the schema the collector writes.
"""
from __future__ import annotations

import pytest

from tools.audit_payload_findings import (
    audit_charging_voltage,
    audit_co2_hypotheses,
    audit_dcdc_complement,
    audit_odometer_counter,
    audit_pedal,
    audit_windshield_candidate,
    build_report,
)


def _record(repo, ts: str, key: str, pid: str, raw: str,
            value=None, unit: str = "") -> None:
    repo.record_measurement(
        repo.vehicle_id, ts, key, "bat_mgmt", "UDS-0x22", pid, unit,
        "reported", "builtin", "experimental", raw, value=value)


@pytest.fixture()
def audit_db(repo):
    repo.vehicle_id = repo.ensure_vehicle(
        "SYNTHETIC00000000", make="Test", model="Fixture")
    # 0x2A53: two complementary payloads (16 A and 19 A real current).
    _record(repo, "2026-10-18T10:00:00+00:00",
            "dc_dc_converter_reserved_current", "0x2A53", "95020F02F9")
    _record(repo, "2026-10-18T10:01:00+00:00",
            "dc_dc_converter_reserved_current", "0x2A53", "96021202F9")
    # 0x1E39: one sentinel and one real near-zero reading.
    _record(repo, "2026-10-18T18:22:50+00:00",
            "bat_charging_voltage", "0x1E39", "FFFF", value=2047.9673, unit="V")
    _record(repo, "2026-10-18T18:23:50+00:00",
            "bat_charging_voltage", "0x1E39", "0006", value=0.1875, unit="V")
    # 0x5867 and the odometer over the same two captures.
    _record(repo, "2026-10-18T13:53:18+00:00", "odometer_2", "0x5867",
            "0005045A00000000", value=11140.0, unit="km")
    _record(repo, "2026-10-19T19:10:00+00:00", "odometer_2", "0x5867",
            "0005045B00000000", value=11150.0, unit="km")
    _record(repo, "2026-10-18T13:53:18+00:00", "odometer_km", "0x295A",
            "0001B201", value=111009.0, unit="km")
    _record(repo, "2026-10-19T19:10:00+00:00", "odometer_km", "0x295A",
            "0001B20C", value=111020.0, unit="km")
    # 0x27C4: the candidate byte 0 and the independent outside temperature.
    _record(repo, "2026-10-18T13:53:18+00:00", "windshield_temperature",
            "0x27C4", "729FD1988D", value=458444.875, unit="degC")
    _record(repo, "2026-10-18T13:53:18+00:00", "outside_temp", "0x0000",
            "", value=9.0, unit="degC")
    # 0x42DB: the two-byte constant.
    _record(repo, "2026-10-18T13:53:18+00:00", "co2_content_interior",
            "0x42DB", "03FE", value=25400.0, unit="ppm")
    # 0xF449: parked, pedal released.
    _record(repo, "2026-10-18T13:53:18+00:00", "res_pid_0149", "0xF449",
            "00", value=0.0, unit="%")
    return repo


def test_tool_reproduces_the_four_safe_fixes(audit_db):
    conn = audit_db.conn

    assert audit_dcdc_complement(conn)["pass"] is True
    charging = audit_charging_voltage(conn)
    assert charging["pass"] is True
    assert charging["sentinel_rows"][0]["raw"] == "FFFF"
    assert charging["live_decoder_returns_none"] is True

    odo = audit_odometer_counter(conn)
    assert odo["pass"] is True
    assert odo["counter_delta_km"] == pytest.approx(10.0)
    assert odo["odometer_delta_km"] == pytest.approx(11.0)
    assert odo["x100_delta_km"] == pytest.approx(100.0)


def test_tool_reproduces_the_candidate_and_quarantine_evidence(audit_db):
    conn = audit_db.conn

    hvac = audit_windshield_candidate(conn)
    assert hvac["pass"] is True
    assert hvac["pairs"] == 1
    assert hvac["mean_absolute_error_c"] == pytest.approx(2.4)

    co2 = audit_co2_hypotheses(conn)
    assert co2["all_03fe"] is True
    assert co2["byte1_x100_ppm"] == 25400
    assert co2["whole_u16_ppm"] == 1022

    pedal = audit_pedal(conn)
    assert pedal["pass"] is True
    assert pedal["decoded_pct"] == [0.0]


def test_tool_builds_a_read_only_report(audit_db):
    # Close the writer first so the read-only connection sees a complete file.
    audit_db.close()
    report = build_report(audit_db.path)

    assert report["read_only"] is True
    assert set(report["findings"]) == {
        "dcdc_complement",
        "charging_voltage_sentinel",
        "odometer_counter",
        "windshield_candidate",
        "co2_hypotheses",
        "accelerator_pedal",
    }
    assert all(item["pass"] for item in report["findings"].values())
