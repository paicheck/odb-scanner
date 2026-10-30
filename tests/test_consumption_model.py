"""Tests for analysis/consumption_model.py."""
import random

from analysis.consumption_model import DriveSample, estimate


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
