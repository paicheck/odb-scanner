"""The trip log (analysis/trips.py, /trips).

* split_drives against tests/data/trip_vectors.json: where drives are cut
  (silence, a long stop, a charge, a backwards odometer) and where not (a
  short stop, a descent), worked by hand per case.
* trip_log: each drive's distance, energy, efficiency, speeds, charge and
  outside temperature; only this car's own readings; short drives get no
  efficiency of their own; the cold-weather bands; and the route planner's
  consumption is the trip log's.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import trips  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

VECTORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                       "trip_vectors.json")
with open(VECTORS, encoding="utf-8") as fh:
    CASES = json.load(fh)["cases"]

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_drives_split_where_the_vectors_say(case):
    got = [{"start_ts": d.start_ts, "end_ts": d.end_ts,
            "distance_km": round(d.distance_km, 6),
            "energy_kwh": None if d.energy_kwh is None else round(d.energy_kwh, 6)}
           for d in trips.split_drives(case["odometer"], case["energy"])]
    assert got == case["drives"], case["about"]


def _ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


@pytest.fixture()
def repo(tmp_path):
    r = Repository(str(tmp_path / "t.db"))
    vid = r.ensure_vehicle("WVWZZZE1ZMS000001")
    rows = []

    def put(key, minutes_ago, value, prov="reported"):
        rows.append((vid, _ts(minutes_ago), key, "x", value, prov))

    # A cold 40 km drive two days ago: 200 Wh/km at 2 C.
    km, wh = 1000.0, 50000.0
    for m in range(41):
        ago = 2 * 1440 - m
        put("odometer_km", ago, km + m)
        put("hv_energy_content", ago, wh - 200 * m)
        put("soc_normal", ago, 80 - m * 0.3)
        put("speed_kmh", ago, 60 + (30 if m == 20 else 0))
        put("outside_temp", ago, 2.0)
    # A mild 30 km drive yesterday at 150 Wh/km, 18 C; a stray imported
    # odometer reading inside it must not move anything.
    for m in range(31):
        ago = 1440 - m
        put("odometer_km", ago, 1040 + m)
        put("hv_energy_content", ago, 40000 - 150 * m)
        put("soc_normal", ago, 60 - m * 0.25)
        put("speed_kmh", ago, 60)
        put("outside_temp", ago, 18.0)
    put("odometer_km", 1440 - 15, 9999, prov="imported: Car Scanner CSV")
    put("odometer_km", 1440 - 16, 9999, prov="simulated")
    # A 3 km hop this morning: too short for an efficiency of its own.
    for m in range(4):
        put("odometer_km", 60 - m, 1070 + m)
        put("hv_energy_content", 60 - m, 35000 - 200 * m)
    r.conn.executemany(
        "INSERT INTO measurements(vehicle_id, ts, key, ecu, value, unit, "
        "provenance, decoding_method, success) "
        "VALUES (?, ?, ?, ?, ?, '', ?, 'builtin', 1)", rows)
    r.conn.commit()
    r.vid = vid
    yield r
    r.close()


def test_trip_log_describes_each_drive(repo):
    log = trips.trip_log(repo, repo.vid, 7)
    assert log["count"] == 3
    hop, mild, cold = log["drives"]                       # newest first
    assert (cold["distance_km"], cold["energy_kwh"]) == (40.0, 8.0)
    assert cold["kwh_per_100km"] == pytest.approx(20.0)
    assert cold["mi_per_kwh"] == pytest.approx(62.137 / 20.0, rel=1e-3)
    assert cold["top_kmh"] == 90 and cold["avg_kmh"] == pytest.approx(60.0)
    assert (cold["soc_start"], cold["soc_end"]) == (80, pytest.approx(68.0))
    assert cold["outside_c"] == 2.0 and cold["duration_s"] == 2400
    assert (mild["distance_km"], mild["kwh_per_100km"]) == (30.0, pytest.approx(15.0))
    assert hop["distance_km"] == 3.0 and hop["energy_kwh"] == pytest.approx(0.6)
    assert hop["kwh_per_100km"] is None                    # under 5 km


def test_trip_log_totals_and_what_the_cold_costs(repo):
    log = trips.trip_log(repo, repo.vid, 7)
    assert log["total_km"] == 73.0
    assert log["kwh_per_100km"] == pytest.approx(100 * (8.0 + 4.5 + 0.6) / 73.0)
    bands = {b["label"]: b for b in log["bands"]}
    assert bands["below 5 °C"]["kwh_per_100km"] == pytest.approx(20.0)
    assert bands["above 15 °C"]["kwh_per_100km"] == pytest.approx(15.0)
    assert "5 to 15 °C" not in bands
    # A drive belongs to a window it ends in: yesterday's ended 23.5 h ago.
    assert trips.trip_log(repo, repo.vid, 1)["count"] == 2


def test_the_planner_measures_what_the_trip_log_shows(repo):
    from routing.car import car_energy
    got = car_energy(repo, repo.vid, Config({}))
    rate = trips.trip_log(repo, repo.vid, 90)["kwh_per_100km"]
    assert got["consumption_kwh_per_100km"] == round(rate, 1)
    assert "73 km" in got["consumption_basis"]


def test_trips_page_and_api(repo):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": repo.path}, "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        base_url="http://127.0.0.1:8000")
    page = client.get("/trips?days=7")
    assert page.status_code == 200
    assert "What the cold costs" in page.text and "too short" in page.text
    assert "3.1 mi/kWh" in page.text                       # the cold drive
    api = client.get("/api/trips?days=7").json()
    assert api["count"] == 3 and api["total_km"] == 73.0
    assert 'href="/trips"' in client.get("/").text


def test_no_vehicle_and_no_drives_render(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    path = str(tmp_path / "e.db")
    Repository(path).close()
    cfg = Config({"database": {"path": path}, "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        base_url="http://127.0.0.1:8000")
    assert "No vehicle recorded yet" in client.get("/trips").text
    assert client.get("/api/trips").json()["count"] == 0
