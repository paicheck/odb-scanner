"""The chronological backtest: each drive predicted only from the drives
before it, the model recommended only when it beats the flat rate.

All drives are synthetic; nothing here reads a real database.
"""
import random

from analysis.consumption_backtest import MIN_PREDICTIONS, backtest
from analysis.consumption_model import DriveSample

DAY = 86400.0


def _drives(rate_of, n=120, days=80, seed=1):
    """n drives over `days`, a winter cooling into spring; rate_of(temp, speed)
    plus noise gives each drive's kWh/km."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        start = i * days * DAY / n
        temp = 2 + 14 * i / n + rng.uniform(-4, 4)
        speed = rng.uniform(35, 110)
        km = rng.uniform(8, 60)
        rate = rate_of(temp, speed) + rng.gauss(0, 0.008)
        out.append(DriveSample(km, rate * km, temp_c=temp, avg_speed_kmh=speed,
                               start_s=start, end_s=start + km / speed * 3600))
    return out


def test_a_car_that_feels_the_cold_is_predicted_better_by_the_model():
    got = backtest(_drives(lambda t, v: 0.15 + 0.004 * (15 - t) + 0.0006 * (v - 70)))
    assert got["verdict"] == "model", got["reason"]
    assert got["model"]["error_kwh_per_100km"] < 0.8 * got["flat"]["error_kwh_per_100km"]
    assert got["model_predicted"] >= MIN_PREDICTIONS
    assert 0.5 <= got["band_coverage"] <= 1.0


def test_a_car_that_does_not_keeps_the_flat_rate_and_the_model_does_no_harm():
    got = backtest(_drives(lambda t, v: 0.17))
    assert got["verdict"] == "flat"
    flat, model = got["flat"], got["model"]
    assert model["error_kwh_per_100km"] <= 1.05 * flat["error_kwh_per_100km"]


def test_too_few_drives_are_not_evidence():
    got = backtest(_drives(lambda t, v: 0.15 + 0.004 * (15 - t), n=12))
    assert got["verdict"] == "flat"
    assert "needs" in got["reason"]


def test_a_drive_is_never_predicted_from_itself_or_later_ones():
    drives = _drives(lambda t, v: 0.17)
    before = backtest(drives)["drives"]
    last = drives[-1]
    drives[-1] = DriveSample(last.distance_km, last.energy_kwh * 3, last.temp_c,
                             last.avg_speed_kmh, start_s=last.start_s, end_s=last.end_s)
    after = backtest(drives)["drives"]
    assert after[-1]["flat_kwh"] == before[-1]["flat_kwh"]
    assert after[-1]["model_kwh"] == before[-1]["model_kwh"]
    assert after[-1]["actual_kwh"] != before[-1]["actual_kwh"]
    assert after[:-1] == before[:-1]


def test_only_the_planner_window_trains_a_prediction():
    def at(day, rate):
        return DriveSample(40, 40 * rate, start_s=day * DAY, end_s=day * DAY + 1800)
    old = [at(i, 0.40) for i in range(10)]
    new = [at(200 + i, 0.17) for i in range(10)]
    got = backtest(old + new, window_days=90)
    assert got["drives"][-1]["flat_kwh"] == round(40 * 0.17, 3)


def test_untimed_and_short_drives_are_not_predicted():
    drives = _drives(lambda t, v: 0.17, n=40)
    drives += [DriveSample(3, 0.6, start_s=d.end_s + 1, end_s=d.end_s + 300)
               for d in drives[::4]]
    drives.append(DriveSample(30, 5.0))
    got = backtest(drives)
    assert all(d["km"] >= 5 for d in got["drives"])
    assert len(got["drives"]) <= 40
