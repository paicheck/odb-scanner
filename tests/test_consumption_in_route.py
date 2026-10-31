"""The personal consumption model on the Route page: used only when, on this
car's own drives in time order, it predicted better than the flat rate.

Every drive here is synthetic, written to a temporary database.
"""
import html
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from config import Config
from database.repository import Repository
from routing.car import car_energy
from test_route_planner import KEYS, _client, services  # noqa: F401

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _put(repo, vid, key, when, value, payload=None):
    repo.record_measurement(vid, when.isoformat(timespec="seconds"), key, "bat_mgmt",
                            "UDS-0x22", "0x0000", "", "reported", "builtin",
                            "experimental", "", value, payload)


def _history(path, rate_of, n=100, days=170, seed=2):
    """n drives over the last `days`, winter into summer: 30 minutes each,
    read every 5 minutes, at the temperature and speed rate_of is given."""
    rng = random.Random(seed)
    repo = Repository(path)
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001", make="VW", model="ID.3")
    odo = 10000.0
    for i in range(n):
        start = NOW - timedelta(days=days * (1 - i / n), minutes=60)
        temp = 1 + 17 * i / n + rng.uniform(-4, 4)
        km = rng.uniform(25, 45)
        speed = km * 2                                   # over 30 minutes
        rate = rate_of(temp, speed) + rng.gauss(0, 0.006)
        for step in range(7):
            when = start + timedelta(minutes=5 * step)
            _put(repo, vid, "odometer_km", when, odo + km * step / 6)
            used_wh = rate * km * 1000 * step / 6
            _put(repo, vid, "hv_energy_content", when, 50000.0 - used_wh)
            _put(repo, vid, "speed_kmh", when, speed)
            _put(repo, vid, "outside_temp", when, temp)
        odo += km + 5
    _put(repo, vid, "hv_energy_max", NOW - timedelta(minutes=30), 53200.0)
    _put(repo, vid, "gps_position", NOW - timedelta(minutes=10), None,
         json.dumps({"lat": 50.0, "lon": 0.0, "altitude_m": 80}))
    repo.conn.commit()
    return repo, vid


def _feels_the_cold(temp, speed):
    return 0.15 + 0.004 * (15 - temp) + 0.0008 * (speed - 70)


def test_a_model_that_beat_the_flat_rate_is_offered(tmp_path):
    repo, vid = _history(tmp_path / "c.db", _feels_the_cold)
    got = car_energy(repo, vid, Config({}))
    assert got["consumption_model"] is not None
    assert "temp" in got["consumption_model"]["features_used"]
    # The physics model wins on these drives (it predicts them best); either
    # model says what it uses.
    assert got["consumption_model_basis"].startswith("this car's own model (")
    assert "outside temperature" in got["consumption_model_basis"]
    repo.close()


def test_a_car_with_no_pattern_keeps_the_flat_rate(tmp_path):
    repo, vid = _history(tmp_path / "f.db", lambda t, v: 0.17)
    got = car_energy(repo, vid, Config({}))
    assert got["consumption_model"] is None
    assert got["consumption_model_basis"].startswith("flat rate kept")
    repo.close()


def test_a_figure_in_config_is_never_replaced_by_the_model(tmp_path):
    repo, vid = _history(tmp_path / "c.db", _feels_the_cold)
    got = car_energy(repo, vid, Config({"routing": {"consumption_kwh_per_100km": 19}}))
    assert got["consumption_model"] is None
    assert got["consumption_kwh_per_100km"] == 19.0
    repo.close()


@pytest.fixture()
def cold_car(tmp_path):
    repo, _ = _history(tmp_path / "r.db", _feels_the_cold)
    repo.close()
    return str(tmp_path / "r.db")


def _assumptions(body):
    return html.unescape(body.split('class="route-assumptions">')[1].split("</ul>")[0])


def test_the_route_page_plans_with_the_model_it_earned(cold_car, services):  # noqa: F811
    body = _client(cold_car, KEYS).get("/route", params={"to": "Leeds"}).text
    text = _assumptions(body)
    assert "from this car's own model" in text
    assert "average speed 96 km/h (counted within" in text    # 400 km in 15,000 s
    assert "Consumption model: this car's own model" in html.unescape(body)


def test_a_figure_the_driver_typed_wins_over_the_model(cold_car, services):  # noqa: F811
    body = _client(cold_car, KEYS).get(
        "/route", params={"to": "Leeds", "consumption": "25"}).text
    text = _assumptions(body)
    assert "own model" not in text
    assert "Consumption 25.0 kWh/100 km on the flat" in text
