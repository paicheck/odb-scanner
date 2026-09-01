"""Unit tests: protocol decoding, read-only guard, statistics, database."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostic import obd2, uds  # noqa: E402
from diagnostic.uds import ReadOnlyViolationError  # noqa: E402


# --- read-only guard ---------------------------------------------------------
@pytest.mark.parametrize("sid", [0x14, 0x27, 0x2E, 0x31, 0x2F, 0x34, 0x35,
                                 0x36, 0x37, 0x28, 0x85])
def test_blocked_services(sid):
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_service(sid)


def test_allowed_services():
    for sid in (0x10, 0x22, 0x19, 0x3E):
        uds.validate_service(sid)  # must not raise


def test_no_api_for_writes():
    public = [n for n in dir(uds) if n.startswith("build_")]
    assert public == ["build_read_did_request", "build_session_request",
                      "build_tester_present"]


# --- ISO-TP / ELM parsing ----------------------------------------------------
def test_line_to_frame_single():
    assert uds.line_to_frame("7ED04621E3B0FA0") == bytes.fromhex("04621E3B0FA0")


def test_line_to_frame_multi():
    frames = uds.encode_isotp(bytes(range(20)))
    lines = ["7ED" + f.hex().upper() for f in frames]
    payload = uds.parse_elm_lines(lines)
    assert payload == bytes(range(20))


def test_parse_elm_lines_no_data():
    assert uds.parse_elm_lines(["NO DATA"]) is None
    assert uds.parse_elm_lines(["SEARCHING...", "UNABLE TO CONNECT"]) is None


def test_negative_response():
    with pytest.raises(uds.NegativeResponseError) as exc:
        uds.expect_positive(bytes([0x7F, 0x22, 0x31]), 0x22)
    assert "0x31" in str(exc.value)


# --- OBD-II ------------------------------------------------------------------
def test_vin_decode():
    # header-off ELM format with index prefixes
    vin = obd2.parse_vin_response(["014", "0:4902015756575A5A5A4531",
                                   "1:5A4D503038373035", "2:33000000000000"])
    assert vin == "WVWZZZE1ZMP087053"


def test_vin_decode_headers_on():
    # headers-on ISO-TP: 10 14 49 02 01 57 56 57 / 21 5A 5A 5A 45 31 5A 4D / 22 ...
    lines = ["7E8101449020157565 7", "7E8215A5A5A45315A4D", "7E82250303837303533"]
    vin = obd2.parse_vin_response([ln.replace(" ", "") for ln in lines])
    assert vin == "WVWZZZE1ZMP087053"


def test_vin_year():
    assert obd2.decode_vin_year("WVWZZZE1ZMP087053") == 2021


def test_mode01_voltage():
    # 7E8 06 41 42 36 1A AA AA  (13.85 V control-module voltage)
    data = obd2.parse_mode01(["7E8064142361AAAAA"], 0x42)
    value, unit = obd2.decode_pid(0x42, data)
    assert unit == "V"
    assert 13.0 < value < 14.5


def test_obd2_dtc_decode():
    # P0420: letter P (00b), first digit 0, second digit 4 -> 0x04, then 0x20
    assert obd2.decode_obd2_dtc(0x04, 0x20) == "P0420"


# --- VAG DTC ------------------------------------------------------------------
def test_vag_dtc_decode():
    # U1123 00: letter U (11b), first digit 1, second 1 -> 0xD1, then 0x23,
    # failure-type byte 0x00
    assert obd2.decode_vag_dtc(0xD1, 0x23, 0x00) == "U112300"


def test_parse_uds_dtc_response():
    payload = bytes([0x59, 0x02, 0xFF, 0xD1, 0x23, 0x00, 0x2F])
    dtcs = obd2.parse_uds_dtc_response(payload)
    assert dtcs == [{"code": "U112300", "status_byte": 0x2F}]

# --- statistics ----------------------------------------------------------------
from analysis import stats  # noqa: E402


def test_mean_stdev():
    assert stats.mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert stats.stdev([1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert stats.mean([]) is None


def test_regression_slope():
    ys = [0, 1, 2, 3, 4]
    assert stats.linear_regression_slope(ys) == pytest.approx(1.0)


def test_classify_trend():
    assert stats.classify_trend(0.5, 2.0, 0.1) == "increasing"
    assert stats.classify_trend(-0.5, 2.0, 0.1) == "decreasing"
    assert stats.classify_trend(0.01, 2.0, 0.1) == "noisy (no clear trend)"
    assert stats.classify_trend(0.0, None, 0.1) == "stable"
    assert stats.classify_trend(None, None, 0.1) == "insufficient_data"


def test_pearson():
    xs = [1, 2, 3, 4, 5]
    assert stats.pearson(xs, [2, 4, 6, 8, 10]) == pytest.approx(1.0)
    assert stats.pearson(xs, [10, 8, 6, 4, 2]) == pytest.approx(-1.0)


def test_zscore_outlier():
    from analysis.anomaly import detect_series_anomalies
    ts = [f"2026-08-{d:02d}T10:00:00+00:00" for d in range(1, 13)]
    vals = [24.0] * 11 + [40.0]  # outlier must exceed z=3 despite std inflation
    out = detect_series_anomalies(ts, vals, z_threshold=3.0)
    assert len(out) == 1
    assert out[0][1] == 40.0
# --- database -------------------------------------------------------------------
from database.repository import Repository  # noqa: E402
from analysis.battery import cell_delta_trend  # noqa: E402
from analysis.dtc import classify_dtc, dtc_summary  # noqa: E402
from analysis.charging import correlate_dtc_with_sessions  # noqa: E402


@pytest.fixture()
def repo():
    with tempfile.TemporaryDirectory() as tmp:
        r = Repository(os.path.join(tmp, "test.db"))
        yield r
        r.close()  # release WAL locks before TemporaryDirectory cleanup


def test_repository_roundtrip(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
    ts = "2026-09-01T10:00:00+00:00"
    repo.record_battery_snapshot(vid, ts, soc_normal_pct=70, cell_delta_mv=28)
    hist = repo.battery_history(30, vid)
    assert len(hist) == 1
    assert hist[0]["soc_normal_pct"] == 70
    repo.record_measurement(vid, ts, "pack_voltage", "bat_mgmt", "UDS-0x22",
                            "0x1E3B", "V", "reported", "builtin", "documented",
                            "DEADBEEF", 355.2)
    latest = repo.latest_measurements(vid)
    assert latest["pack_voltage"]["value"] == 355.2


def test_raw_response_preserved(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    repo.record_measurement(vid, "2026-09-01T10:00:00+00:00", "k", "ecu",
                            "UDS-0x22", "0x0000", "", "reported", "raw-only",
                            "unknown", "1A2B3C", None, None, success=False,
                            error="NRC 0x31")
    row = repo.conn.execute("SELECT raw_response, success, error FROM "
                            "measurements").fetchone()
    assert row["raw_response"] == "1A2B3C"
    assert row["success"] == 0
    assert row["error"] == "NRC 0x31"


def test_dtc_classification():
    assert "communication" in classify_dtc("U112300")
    assert "potentially critical" in classify_dtc("P0A7F00")
    assert "active" in classify_dtc("P123400", status_byte=0x01)
    assert "historical" in classify_dtc("P123400", status_byte=0x08)


def test_cell_delta_trend(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    for d in range(10):
        ts = (now - timedelta(days=10 - d)).isoformat(timespec="seconds")
        repo.record_battery_snapshot(vid, ts, cell_delta_mv=20 + d)
    result = cell_delta_trend(repo, 30, vid)
    assert result["status"] == "ok"
    assert result["current_mv"] == 29.0
    assert result["trend"] == "increasing"


def test_charging_correlation(repo):
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    now = datetime.now(timezone.utc)
    sid = repo.open_session(vid, now.isoformat(), "AC")
    repo.close_session(sid, (now + timedelta(hours=1)).isoformat())
    repo.upsert_dtc(vid, (now + timedelta(hours=2)).isoformat(), "chg_mgmt",
                    "U112300", "desc", ["communication"])
    corr = correlate_dtc_with_sessions(repo, vid)
    assert corr["codes_linked_to_charging"] == ["U112300"]
    assert corr["links"][0]["gap_hours"] == 1.0
# --- config / simulator end-to-end ----------------------------------------------
from config import load_config  # noqa: E402


def test_config_defaults():
    cfg = load_config()
    assert cfg.ollama_model  # configurable, not hard-coded in code
    assert cfg.get("vehicle.vin") == "WVWZZZE1ZMP087053"


def test_simulator_end_to_end():
    """Full pipeline against the simulated ELM327: VIN, DID reads, DTCs."""
    from simulator.vehicle import SimServer
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu

    with SimServer("127.0.0.1", 35123):
        t = Elm327Transport(host="127.0.0.1", tcp_port=35123, timeout=5.0)
        c = DiagnosticConnection(t)
        c.open()
        assert c.read_vin() == "WVWZZZE1ZMP087053"
        bms = get_ecu("bat_mgmt")
        raw = c.read_did(bms, 0x1E3B)
        assert len(raw) == 2
        mgmt = get_ecu("chg_mgmt")
        dtcs = c.read_dtcs_uds(mgmt)
        assert dtcs[0]["code"] == "U112300"
        # unavailable DID -> NRC 0x31 -> try_read_did returns None
        assert c.try_read_did(bms, 0xFFFF) is None
        c.close()
# --- AI layer -------------------------------------------------------------------
def test_report_validator():
    from ai.reports import validate_report
    text = ("VEHICLE DIAGNOSTIC REPORT\nOBSERVATION\nThe battery is definitely "
            "defective.\nHYPOTHESES\n1. x\nLIMITATIONS\nnone")
    amended, warnings = validate_report(text)
    assert any("definitive" in w for w in warnings)
    assert "AUTOMATIC CAVEATS" in amended


def test_prompt_structure():
    from ai import prompts
    ctx = prompts.build_context({"vin": "X"}, {"cell_delta": {}}, {}, {}, {},
                                [], [], [])
    prompt = prompts.interpret_prompt(ctx, "test question")
    assert "test question" in prompt
    assert "EVIDENCE" in prompt



