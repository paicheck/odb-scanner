"""Weather along the route: the forecast fetch (stubbed -- no test reaches the
network), and what the planner does with it."""
import html
import time
from unittest.mock import MagicMock

import pytest
import requests

from routing import weather
from routing.planner import (CarModel, Conditions, PlanSettings, bearing_deg,
                             headwind_kmh, plan_trip, wind_kwh)
from routing.weather import along_route, fetch, sample_points
from test_route_planner import KEYS, _client, _north, db, services  # noqa: F401

CAR = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17)
T0 = 1_800_000_000.0                       # a fixed "now", on the hour


def test_fetch_parses_response():
    sess = MagicMock()
    sess.get.return_value.status_code = 200
    sess.get.return_value.raise_for_status = lambda: None
    sess.get.return_value.json.return_value = {
        "current": {"temperature_2m": 12.3, "wind_speed_10m": 20.0,
                    "wind_direction_10m": 90.0, "precipitation": 0.5}}
    w = fetch(51.0, 0.0, session=sess)
    assert w is not None
    assert w.temp_c == 12.3
    assert w.wind_kmh == 20.0


def test_fetch_returns_none_on_failure():
    sess = MagicMock()
    sess.get.side_effect = requests.ConnectionError("boom")
    assert fetch(51.0, 0.0, session=sess) is None


# --- the physics ------------------------------------------------------------------

def test_headwind_is_the_wind_against_the_direction_of_travel():
    north = bearing_deg((50.0, 0.0), (50.1, 0.0))
    assert north == pytest.approx(0.0, abs=1e-6)
    assert bearing_deg((50.0, 0.0), (50.0, 0.1)) == pytest.approx(90.0, abs=0.1)
    assert headwind_kmh(Conditions(0, wind_kmh=20, wind_from_deg=0), north) == \
        pytest.approx(20)                                       # from ahead
    assert headwind_kmh(Conditions(0, wind_kmh=20, wind_from_deg=180), north) == \
        pytest.approx(-20)                                      # from behind
    assert headwind_kmh(Conditions(0, wind_kmh=20, wind_from_deg=90), north) == \
        pytest.approx(0, abs=1e-9)                              # across
    assert headwind_kmh(Conditions(0), north) == 0.0


def test_a_headwind_costs_more_than_the_same_tailwind_saves():
    head = wind_kwh(100, 100, 30, 10, CAR)
    tail = wind_kwh(100, 100, -30, 10, CAR)
    assert head > 0 > tail and head > -tail
    # 30 km/h at 10 m, 21 km/h at the car, at 100 km/h: a few kWh per 100 km.
    assert 2.0 < head < 6.0
    assert wind_kwh(100, 100, 30, -10, CAR) > head              # denser cold air
    assert wind_kwh(100, None, 30, 10, CAR) == 0.0


# --- the planner with a forecast ------------------------------------------------

def _plan(conditions, car=CAR, **kw):        # 150 km north at 100 km/h
    return plan_trip(_north(150), 5400, 90, car, PlanSettings(), [],
                     weather=conditions, **kw)


def test_a_headwind_along_a_road_north_uses_more_energy():
    calm = _plan(None)
    head = _plan([Conditions(0, 10, 30, 0)])
    tail = _plan([Conditions(0, 10, 30, 180)])
    assert head.arrival_pct < calm.arrival_pct < tail.arrival_pct
    assert head.weather["wind_kwh"] > 0 > tail.weather["wind_kwh"]
    assert calm.weather is None
    assert any("the wind costs about" in a for a in head.assumptions)


def test_each_stretch_gets_the_forecast_from_its_own_km():
    half = _plan([Conditions(0, 10, 0, 0), Conditions(75, 10, 30, 0)])
    whole = _plan([Conditions(0, 10, 30, 0)])
    assert half.weather["wind_kwh"] == pytest.approx(whole.weather["wind_kwh"] / 2,
                                                     rel=0.02)


def test_temperature_counts_only_through_the_cars_own_model():
    cold, warm = [Conditions(0, temp_c=-5)], [Conditions(0, temp_c=20)]
    assert _plan(cold).arrival_pct == _plan(warm).arrival_pct      # flat figure
    text = " ".join(_plan(cold).assumptions)
    assert "which does not use it, so it is not counted" in text

    model = {"base_kwh_per_km": 0.17, "temp_coeff": -0.003,
             "features_used": ["temp"], "means": {"temp": 10.0},
             "ranges": {"temp": [-10.0, 25.0]}, "n_drives": 40}
    modelled = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17,
                        consumption_model=model)
    assert _plan(cold, modelled).arrival_pct < _plan(warm, modelled).arrival_pct
    # The forecast's temperature replaces a single one given; it is not added.
    given = _plan(cold, modelled, temp_c=20)
    assert given.arrival_pct == _plan(cold, modelled).arrival_pct
    assert "outside -5 °C" in " ".join(_plan(cold, modelled).assumptions)


# --- the fetch along a route -------------------------------------------------------

class _Session:
    """Answers like Open-Meteo for every place asked about: hourly from T0,
    `temp` °C, `wind` km/h from `dir`."""

    def __init__(self, temp=8.0, wind=25.0, dir=0.0, fail=None, start=T0):
        self.calls, self.temp, self.wind, self.dir, self.fail = [], temp, wind, dir, fail
        self.start = start

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if self.fail:
            raise self.fail
        n = len(params["latitude"].split(","))
        hours = [self.start + 3600 * h for h in range(72)]
        one = {"hourly": {"time": hours, "temperature_2m": [self.temp] * 72,
                          "wind_speed_10m": [self.wind] * 72,
                          "wind_direction_10m": [self.dir] * 72}}
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = [one] * n if n > 1 else one
        return resp


@pytest.fixture(autouse=True)
def _empty_cache():
    weather._cache.clear()
    yield
    weather._cache.clear()


def test_the_route_is_sampled_with_the_time_the_car_is_there():
    road = _north(200)
    got = sample_points(road, 7200, T0)
    assert [round(k) for k, *_ in got] == [0, 25, 50, 75, 100, 125, 150, 175]
    assert got[4][3] == pytest.approx(T0 + 3600, abs=1)        # halfway, an hour on
    long = sample_points(_north(1000), 36000, T0)
    assert len(long) <= weather.MAX_SAMPLES


def test_positions_are_rounded_before_they_are_sent():
    s = _Session()
    conditions, note = along_route(_north(200), 7200, session=s, now=T0)
    sent = s.calls[0]
    for key in ("latitude", "longitude"):
        for x in sent[key].split(","):
            assert len(x.split(".")[1]) == 1                    # one decimal: ~11 km
    assert "rounded" in note
    assert conditions[0].temp_c == 8.0 and conditions[0].wind_from_deg == 0.0


def test_one_request_then_the_cache():
    s = _Session()
    along_route(_north(200), 7200, session=s, now=T0)
    along_route(_north(200), 7200, session=s, now=T0 + 600)
    assert len(s.calls) == 1
    along_route(_north(200), 7200, session=s, now=T0 + weather.CACHE_S + 1)
    assert len(s.calls) == 2


def test_no_answer_means_no_forecast_and_says_so():
    conditions, note = along_route(_north(200), 7200, now=T0,
                                   session=_Session(fail=requests.Timeout("slow")))
    assert conditions is None and "No weather forecast" in note
    beyond = along_route(_north(200), 7200, depart_s=T0 + 10 * 86400, now=T0,
                         session=_Session())
    assert beyond[0] is None


def test_a_programming_error_is_not_swallowed():
    class Broken(_Session):
        def get(self, *a, **k):
            raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        along_route(_north(200), 7200, session=Broken(), now=T0)


# --- the Route page ------------------------------------------------------------------

def test_the_route_page_counts_the_forecast_wind(db, services, monkeypatch):  # noqa: F811
    # From the north, and the road goes north; the page plans for leaving now.
    s = _Session(wind=30.0, dir=0.0, start=time.time() // 3600 * 3600 - 3600)
    monkeypatch.setattr(weather.requests, "get", s.get)
    body = _client(db, KEYS).get("/route", params={"to": "Leeds"}).text
    assert len(s.calls) == 1
    text = html.unescape(body)
    assert "Open-Meteo's forecast at" in text and "the wind costs about" in text


def test_weather_can_be_switched_off(db, services, monkeypatch):  # noqa: F811
    s = _Session()
    monkeypatch.setattr(weather.requests, "get", s.get)
    client = _client(db, dict(KEYS, weather=False))
    body = client.get("/route", params={"to": "Leeds"}).text
    assert s.calls == []
    assert "forecast" not in body.lower()


def test_without_an_answer_the_page_says_the_wind_is_not_counted(db, services):  # noqa: F811
    body = _client(db, KEYS).get("/route", params={"to": "Leeds"}).text
    assert "No weather forecast" in body             # tests/conftest.py: offline
    assert "Planning failed" not in body
