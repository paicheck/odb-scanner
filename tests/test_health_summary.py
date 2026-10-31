"""The health summary: fault codes from `dtcs`, the 12 V battery judged only
from a fresh resting reading, nothing missing reported as fine.

Regression: the first version counted fault codes in `diagnostic_events`
using a `resolved` column that table does not have; the query failed, an
`except` returned 0, and the summary never reported a stored fault. Its test
only checked that the keys existed.
"""
from datetime import datetime, timedelta, timezone

from analysis.health_summary import health_summary
from database.repository import Repository

VIN = "WVWZZZE1ZMS000001"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _ts(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _volts(repo, vid, key, hours_ago, value, provenance="reported"):
    repo.record_measurement(vid, _ts(hours_ago), key, "adapter", "ATRV", None, "V",
                            provenance, "test", "documented", f"{value}V",
                            value=value)


def _repo(tmp_path):
    repo = Repository(tmp_path / "h.db")
    return repo, repo.ensure_vehicle(VIN)


def test_an_active_fault_needs_attention(tmp_path):
    repo, vid = _repo(tmp_path)
    repo.upsert_dtc(vid, _ts(1), "bms", "P0AA6", "isolation fault", [],
                    status="active")
    got = health_summary(repo, vid, now=NOW)
    assert got["status"] == "attention"
    assert got["faults"]["stored"] == 1
    assert got["faults"]["needing_attention"][0]["code"] == "P0AA6"
    repo.close()


def test_an_old_stored_fault_is_history_not_attention(tmp_path):
    repo, vid = _repo(tmp_path)
    repo.upsert_dtc(vid, _ts(24 * 30), "gateway", "U1123", "old", [],
                    status="stored")
    repo.upsert_dtc(vid, _ts(24 * 30), "gateway", "U0100", "old", [],
                    status="confirmed")
    got = health_summary(repo, vid, now=NOW)
    assert got["faults"]["stored"] == 2
    assert got["faults"]["needing_attention"] == []
    assert got["status"] != "attention"
    repo.close()


def test_a_recent_pending_fault_needs_attention(tmp_path):
    repo, vid = _repo(tmp_path)
    repo.upsert_dtc(vid, _ts(10), "bms", "P0A80", "pending", [], status="pending")
    assert health_summary(repo, vid, now=NOW)["status"] == "attention"
    repo.close()


def test_a_low_fresh_resting_12v_needs_attention(tmp_path):
    repo, vid = _repo(tmp_path)
    _volts(repo, vid, "adapter_12v_voltage", 3, 11.7)
    got = health_summary(repo, vid, now=NOW)
    assert got["twelve_volt"]["status"] == "low"
    assert got["status"] == "attention"
    assert "11.70 V at rest" in got["reasons"][0]
    repo.close()


def test_a_stale_resting_reading_is_shown_but_not_judged(tmp_path):
    repo, vid = _repo(tmp_path)
    _volts(repo, vid, "adapter_12v_voltage", 24 * 5, 11.5)
    got = health_summary(repo, vid, now=NOW)
    assert got["twelve_volt"]["resting"]["value"] == 11.5
    assert got["twelve_volt"]["status"] == "unknown"
    assert got["status"] == "incomplete"
    repo.close()


def test_an_awake_reading_never_judges_the_battery(tmp_path):
    repo, vid = _repo(tmp_path)
    _volts(repo, vid, "aux_12v_voltage", 1, 11.0)      # odd, but not at rest
    got = health_summary(repo, vid, now=NOW)
    assert got["twelve_volt"]["awake"]["value"] == 11.0
    assert got["twelve_volt"]["status"] == "unknown"
    repo.close()


def test_simulated_and_imported_readings_are_not_the_car(tmp_path):
    repo, vid = _repo(tmp_path)
    _volts(repo, vid, "adapter_12v_voltage", 1, 11.0, provenance="simulated")
    _volts(repo, vid, "adapter_12v_voltage", 0.5, 11.0,
           provenance="imported: Car Scanner CSV")
    got = health_summary(repo, vid, now=NOW)
    assert got["twelve_volt"]["resting"] is None
    assert got["status"] == "incomplete"
    repo.close()


def test_nothing_recorded_is_incomplete_not_ok(tmp_path):
    repo, vid = _repo(tmp_path)
    got = health_summary(repo, vid, now=NOW)
    assert got["status"] == "incomplete"
    assert got["faults"] == {"stored": 0, "needing_attention": []}
    repo.close()
