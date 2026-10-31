"""The physics-informed consumption model (analysis/energy_model.py).

All drives are synthetic; nothing here reads a real database."""
import random

import pytest

from analysis.consumption_model import DriveSample
from analysis.energy_model import (
    NAMES, PRIOR, Conditions, PhysicsModel, features, fit, normal_quantile, t_quantile,
)
from analysis.synthetic_journeys import journeys, samples
from routing.planner import CarModel

TRUE = {"base": 0.058, "drag": 0.100, "time": 1.0, "heat": 0.18, "cold": 0.012,
        "warmup": 0.3}


def _drive(rng, t0, theta=TRUE, noise=0.004):
    """A drive whose energy follows the model's own terms exactly, plus noise."""
    km = rng.uniform(6, 120)
    speed = rng.uniform(25, 115)
    temp = rng.uniform(-3, 26)
    dur = km / speed * 3600
    s = DriveSample(km, 0.0, temp_c=temp, avg_speed_kmh=speed, start_s=t0,
                    end_s=t0 + dur, v2_kmh2=speed ** 2 * rng.uniform(1.05, 1.6))
    x = features(Conditions.for_drive(s))
    rate = sum(theta[n] * v for n, v in zip(NAMES, x))
    s.energy_kwh = km * (rate + rng.gauss(0, noise))
    return s


def _drives(n, seed=1, **kw):
    rng = random.Random(seed)
    return [_drive(rng, i * 20000.0, **kw) for i in range(n)]


def test_too_few_drives_fit_nothing():
    assert fit([]) is None
    assert fit(_drives(2)) is None
    assert fit(_drives(3)) is not None


def test_many_drives_recover_the_terms_they_were_made_of():
    m = fit(_drives(400))
    got = dict(zip(NAMES, m.mean))
    for n in ("base", "drag", "time", "heat"):
        assert got[n] == pytest.approx(TRUE[n], rel=0.15), n


def test_a_handful_of_drives_stays_with_physics():
    """Five town drives cannot say what the motorway costs: the prior does."""
    rng = random.Random(4)
    town = []
    for i in range(5):
        s = _drive(rng, i * 20000.0)
        s.avg_speed_kmh, s.v2_kmh2 = 28.0, 28.0 ** 2 * 1.5
        s.end_s = s.start_s + s.distance_km / 28.0 * 3600
        x = features(Conditions.for_drive(s))
        s.energy_kwh = s.distance_km * sum(TRUE[n] * v for n, v in zip(NAMES, x))
        town.append(s)
    m = fit(town)
    drag = m.mean[NAMES.index("drag")]
    assert abs(drag - PRIOR["drag"][0]) < PRIOR["drag"][1]
    fast, _, _ = m.predict(15, 120)
    slow, _, _ = m.predict(15, 50)
    assert fast > slow                       # drag still costs at speed
    # and it knows it does not know: wider at 120 km/h than where it drove
    assert m.rate_sd(Conditions.for_route(15, 120))[1] > \
        m.rate_sd(Conditions.for_route(15, 28))[1]


def test_colder_and_faster_cost_more_per_km():
    m = fit(samples(journeys(120, seed=2)))
    assert m.predict(0, 70)[0] > m.predict(15, 70)[0] > m.predict(22, 70)[0] * 0.98
    assert m.predict(10, 120)[0] > m.predict(10, 90)[0]


def test_a_bad_reading_barely_moves_it():
    clean = _drives(80, seed=6)
    bad = _drives(80, seed=6)
    bad[10].energy_kwh *= 2.5                # a mis-paired energy reading
    bad[20].energy_kwh = -bad[20].energy_kwh  # a charge in a recording gap
    a, b = fit(clean), fit(bad)
    assert b.excluded >= 1 or b.downweighted >= 1
    for t, v in ((5, 50), (15, 110)):
        assert b.predict(t, v)[0] == pytest.approx(a.predict(t, v)[0], rel=0.03)


def test_an_impossible_rate_is_left_out_and_counted():
    drives = _drives(20, seed=7)
    drives[3].energy_kwh = drives[3].distance_km * 0.9      # 90 kWh/100 km
    m = fit(drives)
    assert m.excluded == 1 and m.n_drives == 19


def test_a_long_leg_is_wider_but_not_twenty_times_a_short_one():
    m = fit(samples(journeys(200, seed=3)))
    c = Conditions.for_route(10, 100)
    _, sd10 = m.leg(10, c)
    _, sd200 = m.leg(200, c)
    assert sd200 > sd10
    assert sd200 / 200 < sd10 / 10           # per km, the long leg is tighter


def test_a_wind_forecast_narrows_the_motorway_more_than_town():
    m = fit(samples(journeys(200, seed=3)))
    fast, town = Conditions.for_route(10, 115), Conditions.for_route(10, 30)
    gain_fast = m.leg(150, fast)[1] - m.leg(150, fast, wind_share=0.3)[1]
    gain_town = m.leg(150, town)[1] - m.leg(150, town, wind_share=0.3)[1]
    assert gain_fast > gain_town > 0


def test_unknown_conditions_and_forecast_error_widen_it():
    m = fit(samples(journeys(200, seed=3)))
    known = m.leg(100, Conditions.for_route(5, 80))[1]
    assert m.leg(100, Conditions.for_route(None, 80))[1] > known
    assert m.leg(100, Conditions.for_route(5, 80), temp_sd=2.0)[1] > known


def test_it_survives_storage_and_the_planner_takes_it():
    m = fit(samples(journeys(60, seed=8)))
    back = PhysicsModel.from_dict(m.as_dict())
    assert back.predict(7, 90) == m.predict(7, 90)
    car = CarModel(usable_kwh=56, consumption_kwh_per_km=0.17,
                   consumption_model=m.as_dict())
    assert isinstance(car.consumption_model, PhysicsModel)


def test_quantiles():
    assert normal_quantile(0.9) == pytest.approx(1.2816, abs=1e-4)
    assert normal_quantile(0.5) == pytest.approx(0.0, abs=1e-9)
    assert t_quantile(0.9, 10) == pytest.approx(1.3722, abs=2e-3)
    assert t_quantile(0.9, 3) == pytest.approx(1.6377, abs=2e-2)
    assert t_quantile(0.9, 5000) == pytest.approx(1.2816, abs=1e-3)


def test_no_term_goes_below_zero_and_the_held_ones_are_said():
    """Drives that look as if a cold start gave energy back: no car does that,
    so the term is held at zero rather than making winter look cheap."""
    impossible = dict(TRUE, warmup=-0.8)
    m = fit(_drives(300, seed=9, theta=impossible))
    assert all(v >= 0 for v in m.mean)
    assert "warmup" in m.held and m.learned("warmup") == 0.0
    assert PhysicsModel.from_dict(m.as_dict()).held == m.held
    assert fit(_drives(300, seed=9)).held == ()
