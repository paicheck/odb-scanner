"""The synthetic journeys the consumption methods are judged on: a known
truth, measured the way the collector measures the real car.

Nothing here reads or writes a database."""
import math

from analysis.synthetic_journeys import ENERGY_STEP_KWH, journeys, samples


def _rate(ds, pick):
    sel = [d for d in ds if pick(d)]
    return 100 * sum(d.true_kwh for d in sel) / sum(d.sample.distance_km for d in sel)


def test_the_same_seed_gives_the_same_year_and_another_seed_another():
    a, b, c = journeys(30, seed=3), journeys(30, seed=3), journeys(30, seed=4)
    assert [d.sample for d in a] == [d.sample for d in b]
    assert [d.sample for d in a] != [d.sample for d in c]


def test_it_is_measured_the_way_the_collector_measures():
    for d in journeys(40, seed=1):
        s = d.sample
        assert s.distance_km == int(s.distance_km)              # whole km
        # 25 Wh steps at both ends, plus the BMS's own estimate noise
        steps = s.energy_kwh / ENERGY_STEP_KWH
        assert abs(steps - round(steps)) < 1e-6
        assert abs(s.energy_kwh - d.true_kwh) < 0.3
        assert s.start_s < s.end_s and s.v2_kmh2 >= s.avg_speed_kmh ** 2 * 0.99
        # the collector never knows the climb: samples() leaves it out
    assert all(s.net_climb_m is None for s in samples(journeys(10, seed=1)))
    assert any(s.net_climb_m is not None for s in samples(journeys(10, seed=1), True))


def test_the_truth_behaves_like_an_electric_car():
    ds = journeys(365, seed=2)
    warm_town = _rate(ds, lambda d: d.kind == "town" and d.true_temp_c > 15)
    cold_town = _rate(ds, lambda d: d.kind == "town" and d.true_temp_c < 5)
    warm_mway = _rate(ds, lambda d: d.kind == "motorway" and d.true_temp_c > 15)
    cold_mway = _rate(ds, lambda d: d.kind == "motorway" and d.true_temp_c < 8)
    assert 12 < warm_town < 18 and 17 < warm_mway < 24      # kWh/100 km
    assert cold_town > warm_town + 6            # heating per km is worst slowly
    assert cold_mway - warm_mway < cold_town - warm_town
    # Out and back on the same day: the wind reverses.
    pairs = [(a, b) for a, b in zip(ds, ds[1:])
             if a.kind == b.kind == "motorway"
             and b.sample.start_s - a.sample.end_s < 12 * 3600]
    assert pairs and all(
        math.copysign(1, a.headwind_ms) != math.copysign(1, b.headwind_ms)
        for a, b in pairs if abs(a.headwind_ms) > 0.5)


def test_glitches_are_wrong_readings_not_wrong_driving():
    clean = journeys(60, seed=5)
    bad = journeys(60, seed=5, glitches=0.2)
    assert [d.true_kwh for d in clean] == [d.true_kwh for d in bad]
    off = [abs(b.sample.energy_kwh - b.true_kwh) for b in bad]
    assert sum(1 for x in off if x > 1.0) >= 3
