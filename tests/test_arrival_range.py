"""Arrival ranges: how much room a plan leaves if the car uses more (or less)
than planned -- measured on this car's own drives where there are enough,
otherwise an assumption that says it is one."""
import html

import pytest

from analysis.consumption_backtest import MIN_PREDICTIONS, error_spread
from routing.planner import (ASSUMED_RATE_ERROR, CarModel, Charger, PlanSettings,
                             RateError, plan_trip)
from test_consumption_in_route import _feels_the_cold, cold_car  # noqa: F401
from test_route_planner import KEYS, _client, _north, services  # noqa: F401

CAR = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17)


def _direct(err=None, km=150, start=90):
    return plan_trip(_north(km), km * 36, start, CAR, PlanSettings(), [], rate_error=err)


def test_without_a_measured_spread_the_range_is_an_assumption():
    plan = _direct()
    band = plan.arrival_band
    swing = 150 * 0.17 * ASSUMED_RATE_ERROR / 53.2 * 100        # % either way
    assert band["low_pct"] == pytest.approx(plan.arrival_pct - swing, abs=0.11)
    assert band["high_pct"] == pytest.approx(plan.arrival_pct + swing, abs=0.11)
    assert band["basis"].startswith("assumed")
    assert any(a.startswith("Arrival ranges: assumed") for a in plan.assumptions)


def test_a_measured_spread_sets_the_range_and_can_be_lopsided():
    plan = _direct(RateError(-0.01, 0.03, "measured: test"))
    band = plan.arrival_band
    assert band["low_pct"] == pytest.approx(plan.arrival_pct - 150 * 0.03 / 53.2 * 100,
                                            abs=0.11)
    assert band["high_pct"] == pytest.approx(plan.arrival_pct + 150 * 0.01 / 53.2 * 100,
                                             abs=0.11)
    assert band["basis"] == "measured: test"


def test_a_range_that_reaches_below_the_reserve_says_so():
    tight = _direct(RateError(-0.01, 0.03, "m"), km=200, start=80)
    assert tight.arrival_pct >= 10 and tight.arrival_band["below_reserve"]
    roomy = _direct(RateError(-0.01, 0.01, "m"), km=50)
    assert not roomy.arrival_band["below_reserve"]


def test_each_stop_has_the_range_of_the_leg_that_leads_to_it():
    road = _north(400)
    chargers = [Charger(f"C{k}", road[k][0], 0.0, 150) for k in range(50, 400, 50)]
    plan = plan_trip(road, 15000, 90, CAR, PlanSettings(), chargers,
                     rate_error=RateError(-0.02, 0.02, "m"))
    prev = 0.0
    for s in plan.stops:
        leg = s["km"] - prev
        want = s["arrive_pct"] - leg * 0.02 / 53.2 * 100
        assert s["arrive_low_pct"] == pytest.approx(want, abs=0.11)
        prev = s["km"]


def test_the_spread_is_the_cars_own_error_percentiles():
    drives = [{"km": 20.0, "actual_kwh": 20 * (0.17 + e), "flat_kwh": 20 * 0.17,
               "model_kwh": 20 * 0.17, "model": True}
              for e in [i / 1000 for i in range(-10, 11)]]           # -0.010..+0.010
    got = error_spread({"drives": drives}, model=False)
    assert got["low"] == pytest.approx(-0.008, abs=0.0011)
    assert got["high"] == pytest.approx(0.008, abs=0.0011)
    assert error_spread({"drives": drives[:MIN_PREDICTIONS - 1]}, model=False) is None


def test_the_route_page_shows_the_measured_range(cold_car, services):  # noqa: F811
    page = _client(cold_car, KEYS).get("/route", params={"to": "Leeds"})
    body = html.unescape(page.text)
    assert "likely " in body
    assert "Arrival ranges: measured: how this car's last 60 drives differed" in body


def test_a_typed_figure_gets_the_assumed_range(cold_car, services):  # noqa: F811
    body = html.unescape(_client(cold_car, KEYS).get(
        "/route", params={"to": "Leeds", "consumption": "25"}).text)
    assert "Arrival ranges: assumed" in body
