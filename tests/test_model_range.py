"""Arrival ranges from this car's physics model (routing/uncertainty.py):
wider for longer legs but not in proportion, wider when little is known, and
always saying how confident they are and why.

All drives are synthetic; nothing here reads a real database."""
import pytest

from analysis.energy_model import fit
from analysis.synthetic_journeys import journeys, samples
from routing.planner import CarModel, Charger, PlanSettings, plan_trip
from routing.uncertainty import ModelRange
from test_route_planner import _north

MODEL = fit(samples(journeys(200, seed=21)))


def _range(**kw):
    base = dict(calibration=1.1, calibration_drives=120, temp_c=12.0, speed_kmh=90.0)
    base.update(kw)
    return ModelRange(MODEL, 56.0, **base)


def test_a_longer_leg_is_wider_but_tighter_per_km():
    r = _range()
    lo20, hi20 = r.band(60.0, 20, False)
    lo200, hi200 = r.band(60.0, 200, False)
    assert hi200 - lo200 > hi20 - lo20
    assert (hi200 - lo200) / 200 < (hi20 - lo20) / 20


def test_fewer_drives_behind_the_calibration_widen_it():
    many = _range(calibration_drives=200).band(50.0, 150, False)
    few = _range(calibration_drives=10).band(50.0, 150, False)
    assert few[1] - few[0] > many[1] - many[0]


def test_the_first_leg_carries_the_start_readings_doubt():
    r = _range(start_sd_pct=2.0)
    assert r.band(50.0, 80, True)[0] < r.band(50.0, 80, False)[0]


def test_what_it_has_not_seen_is_said_and_lowers_the_confidence():
    seen_lo = MODEL.ranges["temp"][0]
    r = _range(temp_c=seen_lo - 8, speed_kmh=MODEL.ranges["speed"][1] + 20,
               start_age_h=30)
    r.band(40.0, 3 * MODEL.ranges["km"][1], False)
    text = " ".join(r.checks())
    assert "Colder" in text and "Faster" in text and "Legs up to" in text
    assert "30 h old" in text
    assert r.confidence() == "low"


def test_plenty_of_evidence_and_nothing_unusual_is_good_confidence():
    r = _range(temp_c=12.0, speed_kmh=60.0)
    r.band(50.0, 30, False)
    assert r.checks() == []
    assert r.confidence() == "good"
    assert _range(calibration_drives=5).confidence() == "low"


def test_the_planner_uses_it_for_every_leg_and_says_so():
    road = _north(400)
    chargers = [Charger(f"C{k}", road[k][0], 0.0, 150) for k in range(50, 400, 50)]
    car = CarModel(usable_kwh=56.0, consumption_kwh_per_km=0.17, consumption_model=MODEL)
    rng = _range()
    plan = plan_trip(road, 15000, 90, car, PlanSettings(), chargers, rate_error=rng)
    assert plan.stops
    for s in plan.stops:
        assert s["arrive_low_pct"] < s["arrive_pct"] < s["arrive_high_pct"]
    band = plan.arrival_band
    assert band["method"] == "physics"
    assert band["confidence"] in ("good", "moderate", "low")
    assert band["low_pct"] < plan.arrival_pct < band["high_pct"]
    assert any(a.startswith("Arrival ranges: an 80 % range for each leg")
               for a in plan.assumptions)
    assert len(rng.legs) == len(plan.stops) + 1


def test_the_band_is_centred_on_the_plan():
    lo, hi = _range().band(50.0, 100, False)
    assert (lo + hi) / 2 == pytest.approx(50.0, abs=0.06)
