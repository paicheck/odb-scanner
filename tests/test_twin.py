"""The digital twin's state: observed, inferred or unknown -- as of a moment,
from nothing recorded after it. Synthetic readings in a temporary database."""
from datetime import datetime, timedelta, timezone

import pytest

from analysis.twin import describe, prediction_vs_observation, vehicle_state
from database.repository import Repository
from test_consumption_in_route import _feels_the_cold, _history

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)      # in the past


def _at(minutes):
    return (T + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _put(repo, vid, key, minutes, value, prov="reported", status="verified"):
    repo.record_measurement(vid, _at(minutes), key, "bat_mgmt", "UDS-0x22", "0x0000",
                            "", prov, "builtin", status, f"{value}", value)


@pytest.fixture()
def car(tmp_path):
    repo = Repository(tmp_path / "t.db")
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001")
    yield repo, vid
    repo.close()


def _q(state, name):
    return state["quantities"][name]


def test_a_recent_reading_is_observed_and_current_a_missing_one_unknown(car):
    repo, vid = car
    _put(repo, vid, "soc_normal", -5, 64.0)
    state = vehicle_state(repo, vid, as_of=T)
    soc = _q(state, "soc")
    assert (soc["kind"], soc["value"], soc["current"]) == ("observed", 64.0, True)
    assert soc["age_s"] == 300 and soc["decoder"] == "verified"
    temp = _q(state, "battery_temp")
    assert temp["kind"] == "unknown" and temp["value"] is None


def test_nothing_recorded_after_the_moment_leaks_in(car):
    repo, vid = car
    _put(repo, vid, "soc_normal", -60, 60.0)
    _put(repo, vid, "soc_normal", 60, 40.0)                # an hour after T
    soc = _q(vehicle_state(repo, vid, as_of=T), "soc")
    assert soc["value"] == 60.0 and soc["age_s"] == 3600
    assert soc["current"] is False                         # older than 15 min at T
    later = _q(vehicle_state(repo, vid, as_of=T + timedelta(hours=1)), "soc")
    assert later["value"] == 40.0 and later["current"] is True


def test_each_quantity_is_current_for_as_long_as_it_stays_true(car):
    repo, vid = car
    _put(repo, vid, "hv_energy_max", -7 * 1440, 53200.0)   # a week old
    _put(repo, vid, "speed_kmh", -5, 50.0)                 # five minutes old
    state = vehicle_state(repo, vid, as_of=T)
    assert _q(state, "usable")["current"] is True and _q(state, "usable")["value"] == 53.2
    assert _q(state, "speed")["current"] is False


def test_an_inference_is_never_fresher_than_what_it_rests_on(car):
    repo, vid = car
    _put(repo, vid, "hv_energy_max", -7 * 1440, 53200.0)
    _put(repo, vid, "hv_energy_content", -2, 26600.0)
    pct = _q(vehicle_state(repo, vid, as_of=T), "energy_pct")
    assert (pct["kind"], pct["value"]) == ("inferred", 50.0)
    assert pct["ts"] == _at(-7 * 1440)
    assert "hv_energy_content" in pct["source"]


def test_simulated_readings_are_not_the_car_and_imported_ones_are_not_current(car):
    repo, vid = car
    _put(repo, vid, "soc_normal", -1, 99.0, prov="simulated")
    _put(repo, vid, "battery_temp", -1, 21.0, prov="imported: Car Scanner CSV")
    state = vehicle_state(repo, vid, as_of=T)
    assert _q(state, "soc")["kind"] == "unknown"
    temp = _q(state, "battery_temp")
    assert temp["kind"] == "observed" and temp["current"] is False
    assert "imported" in temp["note"]


# --- what the car is doing ---------------------------------------------------------

def test_driving_from_speed(car):
    repo, vid = car
    _put(repo, vid, "speed_kmh", -0.5, 48.0)
    state = vehicle_state(repo, vid, as_of=T)
    assert state["mode"] == "driving" and "48 km/h" in _q(state, "mode")["note"]


def test_charging_when_the_energy_rises_with_the_car_standing(car):
    repo, vid = car
    for m, wh in ((-25, 30000.0), (-15, 33000.0), (-5, 36000.0)):
        _put(repo, vid, "hv_energy_content", m, wh)
        _put(repo, vid, "odometer_km", m, 12000.0)
    state = vehicle_state(repo, vid, as_of=T)
    assert state["mode"] == "charging"
    assert _q(state, "parked_since")["kind"] == "inferred"


def test_parked_awake_asleep_and_unknown(car):
    repo, vid = car
    for m in range(-300, -100, 10):                        # a drive, then standing
        _put(repo, vid, "odometer_km", m, 12000.0 + (m + 300) / 10)
    for m in range(-100, -2, 10):
        _put(repo, vid, "odometer_km", m, 12020.0)
    state = vehicle_state(repo, vid, as_of=T)
    assert state["mode"] == "parked"
    assert _q(state, "parked_since")["value"] == _at(-100)

    later = T + timedelta(hours=2)
    _put(repo, vid, "adapter_12v_voltage", 115, 12.6)       # the adapter, 5 min before
    asleep = vehicle_state(repo, vid, as_of=later)
    assert asleep["mode"] == "asleep"
    assert "only the adapter" in _q(asleep, "mode")["note"]

    gone = vehicle_state(repo, vid, as_of=T + timedelta(days=1))
    assert gone["mode"] == "unknown" and _q(gone, "mode")["kind"] == "unknown"


def test_the_description_says_what_is_not_current(car):
    repo, vid = car
    _put(repo, vid, "soc_normal", -60, 60.0)
    lines = describe(vehicle_state(repo, vid, as_of=T))
    want = "State of charge (display): 60.0 % (observed, 60 min old, not current)"
    assert want in lines
    assert any(line.startswith("Battery temperature: unknown") for line in lines)


def test_prediction_against_observation_on_recent_drives(tmp_path):
    repo, vid = _history(tmp_path / "h.db", _feels_the_cold)
    got = prediction_vs_observation(repo, vid, last=5)
    assert got["figure"] == "this car's own model"
    assert len(got["drives"]) == 5
    d = got["drives"][-1]
    err = d["predicted_kwh"] - d["observed_kwh"]
    assert d["error_kwh"] == pytest.approx(err, abs=1e-3)
    repo.close()


# --- the API --------------------------------------------------------------------------

def _client(path):
    from fastapi.testclient import TestClient

    from config import Config
    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(path)}, "web": {"host": "127.0.0.1"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      raise_server_exceptions=False)


def test_the_api_reconstructs_an_earlier_moment(car, tmp_path):
    repo, vid = car
    _put(repo, vid, "soc_normal", -60, 60.0)
    _put(repo, vid, "soc_normal", 60, 40.0)
    repo.conn.commit()
    client = _client(tmp_path / "t.db")
    then = client.get("/api/twin", params={"as_of": "2026-10-01T12:00:00Z"}).json()
    assert then["as_of"] == "2026-10-01T12:00:00+00:00"
    assert then["quantities"]["soc"]["value"] == 60.0
    assert "predictions" not in then
    now = client.get("/api/twin").json()
    assert now["quantities"]["soc"]["value"] == 40.0 and "predictions" in now
    assert client.get("/api/twin", params={"as_of": "yesterday"}).status_code == 400
