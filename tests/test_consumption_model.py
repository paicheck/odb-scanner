"""Tests for analysis/consumption_model.py."""
import random

import pytest

from analysis.consumption_model import ConsumptionModel, DriveSample, estimate


def _samples(n=20, km_each=20.0, seed=1):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        temp = rng.uniform(-5, 25)
        speed = rng.uniform(40, 90)
        elev = rng.uniform(0, 30)
        rate = 0.15 + 0.002 * max(0, 10 - temp) + 0.0003 * speed + 0.00002 * elev
        out.append(DriveSample(distance_km=km_each, energy_kwh=rate * km_each,
                               temp_c=temp, avg_speed_kmh=speed,
                               elev_gain_m=elev * km_each))
    return out


def test_estimate_returns_model_with_enough_data():
    m = estimate(_samples())
    assert m is not None
    assert m.sample_km >= 50.0
    assert m.n_drives >= 3
    assert "temp" in m.features_used


def test_estimate_returns_none_with_too_little_data():
    assert estimate([DriveSample(10.0, 1.7, temp_c=10.0)] * 3) is None


def test_predict_returns_band():
    m = estimate(_samples())
    assert m is not None
    exp, lo, hi = m.predict(temp_c=5, avg_speed_kmh=60, elev_gain_m_per_km=10)
    assert lo <= exp <= hi
    assert exp > 0


def test_colder_increases_consumption():
    m = estimate(_samples())
    assert m is not None
    warm, _, _ = m.predict(temp_c=20)
    cold, _, _ = m.predict(temp_c=0)
    assert cold > warm


# --- what it must not do (each was true of the first version) -------------------

def _drive(km, rate, **kw):
    return DriveSample(distance_km=km, energy_kwh=rate * km, **kw)


def test_unknown_conditions_mean_the_usual_ones_not_zero():
    # Fitted with speed, the base used to be the rate at 0 km/h: a planner that
    # knew no speed planned on that.
    rng = random.Random(3)
    drives = [_drive(20, 0.10 + 0.001 * v, avg_speed_kmh=v)
              for v in (rng.uniform(30, 110) for _ in range(30))]
    m = estimate(drives)
    assert "speed" in m.features_used
    flat = sum(d.energy_kwh for d in drives) / sum(d.distance_km for d in drives)
    assert m.predict()[0] == pytest.approx(flat, abs=1e-9)


def test_it_never_extrapolates_past_the_drives_it_learned_from():
    m = estimate(_samples())
    coldest = m.ranges["temp"][0]
    assert m.predict(temp_c=-30)[0] == m.predict(temp_c=coldest)[0]


def test_three_drives_give_a_base_rate_not_five_coefficients():
    # Three drives used to fit up to five coefficients exactly and report a
    # spread of 0.01 kWh/km.
    rng = random.Random(4)
    drives = [_drive(30, rng.uniform(0.14, 0.22), temp_c=rng.uniform(0, 20),
                     avg_speed_kmh=rng.uniform(40, 100), hvac_on=bool(i % 2))
              for i in range(3)]
    m = estimate(drives)
    assert m.features_used == ()
    assert m.base_kwh_per_km == pytest.approx(
        sum(d.energy_kwh for d in drives) / sum(d.distance_km for d in drives))
    assert m.residual_std > 0.01


def test_a_drive_without_a_temperature_does_not_cost_the_others_theirs():
    drives = _samples(n=20)
    drives.append(_drive(40, 0.17))              # no temperature, no speed
    m = estimate(drives)
    assert "temp" in m.features_used             # was dropped for every drive


def test_a_feature_with_no_effect_is_left_out():
    rng = random.Random(5)
    drives = [_drive(25, 0.17 + rng.gauss(0, 0.01), avg_speed_kmh=rng.uniform(40, 110))
              for _ in range(40)]
    assert estimate(drives).features_used == ()


def test_a_feature_with_too_little_spread_is_left_out():
    rng = random.Random(6)
    drives = [_drive(25, 0.17 + 0.01 * (t - 15), temp_c=t)
              for t in (15 + rng.uniform(-1, 1) for _ in range(40))]
    assert "temp" not in estimate(drives).features_used


def test_the_spread_is_weighted_by_distance_like_the_fit():
    drives = [_drive(50, 0.17) for _ in range(9)] + [_drive(6, 0.30)]
    m = estimate(drives)
    assert m.residual_std < 0.02                 # unweighted it was ~0.04


def test_the_model_survives_a_trip_through_a_dict():
    m = estimate(_samples())
    back = ConsumptionModel.from_dict(m.as_dict())
    for t in (-10, 5, 30):
        assert back.predict(t, 70) == m.predict(t, 70)
