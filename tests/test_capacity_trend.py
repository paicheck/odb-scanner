"""The battery capacity trend (analysis/health.py, the Battery page, the AI).

A Theil-Sen slope through one median a day of the BMS-rated maximum energy:
robust to a recalibration step and an odd day, silent until there is enough
history, and carried to the warranty line only when the fall is significant.
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import health  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

TODAY = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0,
                                           microsecond=0)


def test_theil_sen_shrugs_off_an_outlier_and_brackets_its_slope():
    points = [(x, 2.0 * x + 1.0) for x in range(30)]
    points[7] = (7, 500.0)                                  # one wild day
    slope, lo, hi = health.theil_sen(points)
    assert slope == pytest.approx(2.0)
    assert lo <= slope <= hi
    assert health.theil_sen([(1, 1.0)]) is None


def _repo(tmp_path, days, wh_at, prov="reported"):
    r = Repository(str(tmp_path / "h.db"))
    vid = r.ensure_vehicle("WVWZZZE1ZMS000001")
    rows = []
    for d in days:
        for hour in (0, 3):
            ts = (TODAY - timedelta(days=d, hours=hour)).isoformat()
            rows.append((vid, ts, wh_at(d, hour), prov))
    r.conn.executemany(
        "INSERT INTO measurements(vehicle_id, ts, key, ecu, value, unit, "
        "provenance, decoding_method, success) "
        "VALUES (?, ?, 'hv_energy_max', 'energy', ?, 'Wh', ?, 'builtin', 1)", rows)
    r.conn.commit()
    r.vid = vid
    return r


def test_a_steady_fall_is_measured_and_carried_to_the_warranty_line(tmp_path):
    rng = random.Random(1)
    # 54 kWh a year ago, losing 1.2 kWh a year; daily noise of +-100 Wh and a
    # BMS recalibration that read 400 Wh high for a fortnight.
    def wh(d, hour):
        base = 54000 - 1200 * (365 - d) / 365.25
        step = 400 if 100 < d < 115 else 0
        return base + step + rng.uniform(-100, 100)
    r = _repo(tmp_path, range(0, 366, 2), wh)
    got = health.capacity_trend(r, r.vid)
    assert got["status"] == "ok" and got["span_days"] == 364
    assert got["kwh_per_year"] == pytest.approx(-1.2, abs=0.08)
    assert got["kwh_per_year_low"] < got["kwh_per_year"] < got["kwh_per_year_high"] < 0
    # From ~52.8 kWh to 70 % of 58 kWh (40.6) at 1.2 kWh a year: ~10 years.
    assert got["years_to_warranty_line"] == pytest.approx(10.2, abs=1.0)
    r.close()


def test_a_flat_pack_projects_nothing(tmp_path):
    rng = random.Random(2)
    r = _repo(tmp_path, range(0, 120, 3), lambda d, h: 53500 + rng.uniform(-150, 150))
    got = health.capacity_trend(r, r.vid)
    assert got["status"] == "ok"
    assert got["kwh_per_year_low"] < 0 < got["kwh_per_year_high"]
    assert "years_to_warranty_line" not in got
    r.close()


def test_too_little_history_says_so(tmp_path):
    r = _repo(tmp_path, range(0, 30), lambda d, h: 53500)
    got = health.capacity_trend(r, r.vid)
    assert got["status"] == "insufficient" and got["span_days"] == 29
    assert got["latest_kwh"] == pytest.approx(53.5)
    r.close()


def test_imported_readings_are_not_this_cars_history(tmp_path):
    r = _repo(tmp_path, range(0, 200, 2), lambda d, h: 50000 + d,
              prov="imported: Car Scanner CSV")
    assert health.capacity_trend(r, r.vid)["status"] == "no readings"
    r.close()


def test_battery_page_and_ai_evidence_carry_the_trend(tmp_path):
    from fastapi.testclient import TestClient

    from ai.service import AnalysisService
    from web.dashboard import create_app
    r = _repo(tmp_path, range(0, 30), lambda d, h: 53500)
    cfg = Config({"database": {"path": r.path}, "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        base_url="http://127.0.0.1:8000")
    page = client.get("/battery").text
    assert "Capacity trend" in page and "Not enough history yet" in page
    packet = AnalysisService(cfg, r).build_context("How is the battery?")
    assert packet["battery"]["capacity_trend"]["status"] == "insufficient"
    r.close()
