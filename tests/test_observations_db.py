"""The observation warehouse must be a faithful, rebuildable view of the data.

These tests pin the promises that make it worth having alongside the live store:
normalised dimensions, raw bytes preserved, capture archives queryable, and no
write ever reaching the source database.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.repository import Repository  # noqa: E402
from tools import build_observations_db as obs  # noqa: E402

VIN = "WVWZZZE1ZMP087053"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_source(path: Path) -> None:
    with Repository(str(path)) as repo:
        vid = repo.ensure_vehicle(VIN, "Volkswagen", "ID.3", "58 kWh (Pro)", 2021)
        repo.upsert_ecu(vid, "bat_mgmt", "HV battery management", 0x7E5, 0x7ED,
                        "responder", "experimentally determined")
        repo.record_measurement(
            vid, "2026-10-21T20:45:37+00:00", "soc_abs", "bat_mgmt",
            "UDS-0x22", "0x028C", "%", "reported", "builtin",
            "experimentally determined", "C7", 79.6, None, True, None)
        repo.record_measurement(
            vid, "2026-10-21T20:45:38+00:00", "imported_signal", "8C.BMS",
            "UDS-0x22", "-", None, "imported: Car Scanner CSV", "imported",
            "unknown", "displayed 12.3 V", 12.3, None, True, None)
        repo.record_battery_snapshot(
            vid, "2026-10-21T20:45:37+00:00", soc_abs_pct=79.6,
            soc_normal_pct=81.8, pack_voltage_v=429.5, pack_current_a=-4.89,
            pack_power_kw=-2.1, cell_min_v=3.958, cell_max_v=3.976,
            cell_delta_mv=18.1, battery_temp_c=9.5, soh_pct=91.7)
        repo.record_cell_voltages(vid, "2026-10-21T20:45:37+00:00",
                                  [3.958, 3.976], [0, 1])
        repo.log_tx(ts="2026-10-21T20:45:37+00:00", direction="TX",
                    ecu="bat_mgmt", payload="222AB2", purpose="read energy")
        repo.conn.execute(
            "INSERT INTO diagnostic_events(vehicle_id, ts, kind, ecu, "
            "description, data) VALUES (?,?,?,?,?,?)",
            (vid, "2026-10-21T20:45:00+00:00", "wake", "bat_mgmt",
             "vehicle awake", None))
        repo.conn.commit()


def _write_captures(tmp_path: Path) -> list[str]:
    live = tmp_path / "live.json"
    live.write_text(json.dumps([
        {"key": "soc_abs", "raw": "C7",
         "ts": "2026-10-21T20:45:37+00:00"},
        {"key": "soc_abs", "raw": "C8",
         "ts": "2026-10-21T20:45:38+00:00"},
    ]), encoding="utf-8")

    gateway = tmp_path / "gateway.json"
    gateway.write_text(json.dumps({
        "tool": "tools/scan_gateway.py",
        "started_utc": "2026-10-22T20:35:57+00:00",
        "results": [{
            "did": "0x2AB2", "label": "Maximum energy",
            "record_hex": "04280400",
            "decoded": [{"key": "hv_energy_max", "value": 53200.0,
                         "unit": "Wh", "name": "Max HV energy content"}],
            "tx": ["222AB2"], "rx": ["622AB204280400"], "error": None,
        }],
    }), encoding="utf-8")

    expected = tmp_path / "strict.json"
    expected.write_text(json.dumps({
        "schema": "odb_scanner.strict_expected_values.v1",
        "captures": [{"key": "hv_energy_max", "raw": "04280400",
                      "expected": 53200.0, "unit": "Wh",
                      "source": "displayed value"}],
    }), encoding="utf-8")

    validation = tmp_path / "live_validation.json"
    validation.write_text(json.dumps({
        "summary": {"total": 1, "pass": 1, "fail": 0, "indeterminate": 0},
        "verdicts": [{"key": "soc_abs", "raw": "C7", "result": "PASS",
                      "value": 79.6, "unit": "%", "issue": "",
                      "expected": None}],
    }), encoding="utf-8")

    passive = tmp_path / "passive.json"
    passive.write_text(json.dumps({
        "capture_date": "2026-10-15",
        "records": [{"can_id": "0x77A", "did": "0x2AB2",
                     "payload": "04280400", "offset_s": 12.5, "record": 1}],
    }), encoding="utf-8")

    return [str(live), str(gateway), str(expected), str(validation),
            str(passive)]


def test_build_creates_normalised_queryable_database(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)
    before = _sha(source)
    out = tmp_path / "obs.db"

    summary = obs.build(source=source, out=out, profile="meb",
                        captures=_write_captures(tmp_path), force=False)

    assert summary["observations"] == 2
    assert summary["vehicles"] == 1
    assert summary["modules"] == 2          # bat_mgmt + the imported 8C.BMS
    assert summary["battery_snapshots"] == 1
    assert summary["cell_voltages"] == 2
    assert summary["tx_log"] == 1
    assert summary["captures"] == 5         # every non-manifest archive
    assert summary["expectation_checks"] == 1
    assert summary["source_unchanged"] is True
    assert _sha(source) == before

    con = sqlite3.connect(out)
    con.row_factory = sqlite3.Row
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Counts describe the warehouse, including dimensions first seen only in
    # imports or capture archives.
    for table in ("module", "signal"):
        assert summary[table + "s"] == con.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    latest = con.execute(
        "SELECT signal_key, value_num, observed_at FROM v_latest_observation "
        "WHERE signal_key='soc_abs'").fetchone()
    assert latest["value_num"] == pytest.approx(79.6)
    assert latest["observed_at"] == "2026-10-21T20:45:37+00:00"

    raw = con.execute(
        "SELECT raw_hex, raw_text FROM observation o JOIN signal s "
        "ON s.signal_id=o.signal_id WHERE s.key='imported_signal'").fetchone()
    assert raw["raw_hex"] is None
    assert raw["raw_text"] == "displayed 12.3 V"

    # The catalogue should win over the observed-only fallback for known keys.
    soc = con.execute(
        "SELECT confidence, unit FROM signal WHERE key='soc_abs'").fetchone()
    assert soc["confidence"] == "PROVISIONAL"
    assert soc["unit"] == "%"

    capture = con.execute(
        "SELECT signal_key, raw_hex, decoded_num, verdict FROM v_capture_record "
        "WHERE capture_file LIKE '%gateway.json'").fetchone()
    assert capture["signal_key"] == "hv_energy_max"
    assert capture["decoded_num"] == pytest.approx(53200.0)
    assert con.execute(
        "SELECT COUNT(*) FROM capture_record WHERE capture_id IN "
        "(SELECT capture_id FROM capture WHERE kind='passive_capture')"
    ).fetchone()[0] >= 1
    con.close()


def test_existing_output_is_refused_unless_forced(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)
    out = tmp_path / "obs.db"
    obs.build(source=source, out=out, captures=[], include_captures=False)

    with pytest.raises(FileExistsError):
        obs.build(source=source, out=out, captures=[], include_captures=False)

    summary = obs.build(source=source, out=out, captures=[],
                        include_captures=False, force=True)
    assert summary["observations"] == 2


def test_source_and_output_must_differ(tmp_path):
    source = tmp_path / "source.db"
    _seed_source(source)
    with pytest.raises(ValueError):
        obs.build(source=source, out=source, captures=[],
                  include_captures=False, force=True)


def test_cli_writes_database_and_prints_summary(tmp_path, capsys):
    source = tmp_path / "source.db"
    _seed_source(source)
    out = tmp_path / "obs.db"

    assert obs.main(["--source", str(source), "--out", str(out),
                     "--no-captures"]) == 0
    text = capsys.readouterr().out
    assert "observations" in text
    assert "integrity:   ok" in text
    assert out.is_file()
