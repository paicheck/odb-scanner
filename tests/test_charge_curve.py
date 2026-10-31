"""This car's own DC charging curve (analysis/charge_curve.py): measured
where its sessions show the car's limit, never mistaking a slow charger for
a slow car. All sessions are synthetic, in temporary databases."""
from datetime import datetime, timedelta, timezone

import pytest

from analysis.charge_curve import measure
from config import Config
from database.repository import Repository
from routing.car import car_energy, charge_curve
from routing.planner import ID3_58_CURVE, CarModel, charge_power_kw, charge_seconds


def _slower(soc):
    """A car 20 % slower than published above 40 %."""
    kw = charge_power_kw(soc, ID3_58_CURVE, 1e9)
    return kw * 0.8 if soc > 40 else kw


def _session(charger_kw, true=_slower, start=10, end=95, temp=22.0):
    return [(s / 2, min(true(s / 2), charger_kw), temp)
            for s in range(start * 2, end * 2)]


def _point(got, soc):
    return next(p for p in got["points"] if p["soc"] == soc)


def test_a_slower_car_is_found_where_it_tapers():
    got = measure([_session(150), _session(150), _session(300)], ID3_58_CURVE)
    for soc in (50, 60, 70, 80):
        p = _point(got, soc)
        assert p["measured"] and p["kw"] == pytest.approx(_slower(soc), rel=0.05)
    assert not _point(got, 30)["measured"]          # at its peak: only "at least"
    assert _point(got, 30)["kw"] == 110
    assert "measured at" in got["basis"]


def test_a_slow_charger_is_not_mistaken_for_a_slow_car():
    got = measure([_session(50), _session(50), _session(50)], ID3_58_CURVE)
    for soc in (30, 40, 50, 60):
        assert _point(got, soc)["kw"] == charge_power_kw(soc, ID3_58_CURVE, 1e9)
        assert not _point(got, soc)["measured"]
    assert _point(got, 80)["measured"]               # 50 kW is more than it takes there


def test_one_session_is_not_enough_and_none_is_nothing():
    assert measure([_session(150)], ID3_58_CURVE) is None
    assert measure([], ID3_58_CURVE) is None


def test_a_spike_and_a_negative_sign_do_not_matter():
    a = _session(150)
    b = [(s, -kw, t) for s, kw, t in _session(150)]     # current read with the other sign
    b[40] = (b[40][0], 900.0, 20.0)                     # one impossible reading
    got = measure([a, b], ID3_58_CURVE)
    assert _point(got, 60)["kw"] == pytest.approx(_slower(60), rel=0.05)


def _db(tmp_path, sessions):
    repo = Repository(str(tmp_path / "c.db"))
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001", make="VW", model="ID.3")
    now = datetime.now(timezone.utc)
    for n, samples in enumerate(sessions):
        t0 = now - timedelta(days=10 + n)
        sid = repo.open_session(vid, t0.isoformat(timespec="seconds"), "DC",
                                samples[0][0])
        for i, (soc, kw, temp) in enumerate(samples):
            repo.add_charging_sample(sid, (t0 + timedelta(seconds=30 * i)).isoformat(
                timespec="seconds"), power_kw=kw, soc=soc, battery_temp_c=temp)
        repo.close_session(sid, (t0 + timedelta(hours=1)).isoformat(timespec="seconds"),
                           samples[-1][0])
    return repo, vid


def test_the_planner_times_stops_on_the_measured_curve(tmp_path):
    repo, vid = _db(tmp_path, [_session(150), _session(150)])
    got = car_energy(repo, vid, Config({}))
    assert "this car's own DC charging" in got["charge_curve_basis"]
    mine = CarModel(56, 0.17, charge_curve=tuple(tuple(p) for p in got["charge_curve"]))
    published = CarModel(56, 0.17)
    assert charge_seconds(20, 80, mine, 150) > \
        charge_seconds(20, 80, published, 150) * 1.1
    repo.close()


def test_a_curve_in_config_wins_and_none_means_the_published_one(tmp_path):
    repo, vid = _db(tmp_path, [_session(150), _session(150)])
    curve, basis, _ = charge_curve(repo, vid, Config(
        {"routing": {"charge_curve": [[0, 100], [80, 40], [100, 5]]}}))
    assert curve == ((0.0, 100.0), (80.0, 40.0), (100.0, 5.0)) and "config" in basis
    repo.close()
    empty = Repository(str(tmp_path / "e.db"))
    vid = empty.ensure_vehicle("WVWZZZE1ZMS000001")
    assert charge_curve(empty, vid, Config({})) == (ID3_58_CURVE, "", None)
    empty.close()


def test_a_start_charge_the_display_disagrees_with_is_flagged(tmp_path):
    from test_consumption_in_route import _put
    repo = Repository(str(tmp_path / "s.db"))
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001")
    now = datetime.now(timezone.utc)
    _put(repo, vid, "hv_energy_max", now - timedelta(minutes=5), 56000.0)
    _put(repo, vid, "hv_energy_content", now - timedelta(minutes=5), 28000.0)   # 50 %
    _put(repo, vid, "soc_normal", now - timedelta(minutes=6), 51.0)
    repo.conn.commit()
    assert car_energy(repo, vid, Config({}))["start_check"] is None
    _put(repo, vid, "soc_normal", now - timedelta(minutes=4), 70.0)
    repo.conn.commit()
    got = car_energy(repo, vid, Config({}))["start_check"]
    assert got and "gives 50 %" in got and "was 70 %" in got
    repo.close()
