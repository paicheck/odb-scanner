"""Route planner: the energy model, charging stops, this car's figures, the
two web services (stubbed -- no test reaches the network) and the Route page.

tests/data/route_vectors.json is shared with the Android planner
(PlannerTest.kt); the first test fails when it no longer matches this code.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402
from routing import prices, providers  # noqa: E402
from routing.car import car_energy, measured_consumption  # noqa: E402
from routing.planner import (  # noqa: E402
    CarModel,
    Charger,
    PlanSettings,
    charge_seconds,
    charge_table,
    leave_levels,
    plan_trip,
    segment_kwh,
    table_seconds,
)

VECTORS = os.path.join(ROOT, "tests", "data", "route_vectors.json")


# -- shared vectors -----------------------------------------------------------
def test_vectors_file_is_current():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from export_route_vectors import build, render
    with open(VECTORS, encoding="utf-8") as f:
        assert f.read() == render(build()), (
            "route_vectors.json is stale: python tools/export_route_vectors.py")


def test_vectors_cover_every_outcome():
    with open(VECTORS, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    statuses = {c["expect"]["status"] for c in cases}
    assert statuses == {"direct", "with_stops", "unreachable"}
    assert any(len(c["expect"]["stops"]) >= 2 for c in cases)


# -- energy model ---------------------------------------------------------------
CAR = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17)


def test_flat_road_is_distance_times_consumption():
    assert segment_kwh(10, 0, CAR) == pytest.approx(1.7)


def test_climb_costs_more_than_descent_gives_back():
    # 1950 kg up 100 m = 0.531 kWh at the wheels; through 90 % = 0.590.
    up = segment_kwh(1, 100, CAR) - 0.17
    down = 0.17 - segment_kwh(1, -100, CAR)
    assert up == pytest.approx(1950 * 9.81 * 100 / 3.6e6 / 0.9)
    assert down == pytest.approx(1950 * 9.81 * 100 / 3.6e6 * 0.6)
    assert up > down > 0


def test_charging_slows_as_the_battery_fills():
    first = charge_seconds(10, 45, CAR, 150)
    second = charge_seconds(45, 80, CAR, 150)
    assert second > first * 1.3
    assert charge_seconds(10, 80, CAR, 50) > charge_seconds(10, 80, CAR, 150)
    assert charge_seconds(80, 80, CAR, 150) == charge_seconds(90, 80, CAR, 150) == 0


def _north(km):
    return [(50.0 + k / 111.195, 0.0, None) for k in range(km + 1)]


def test_route_needs_two_points():
    with pytest.raises(ValueError):
        plan_trip([(50.0, 0.0, None)], 0, 80, CAR, PlanSettings(), [])


def test_never_charges_above_what_the_battery_holds():
    plan = plan_trip(_north(50), 1800, 100, CAR, PlanSettings(), [])
    assert plan.status == "direct" and max(p[1] for p in plan.profile) <= 100.0
    assert plan.cost == 0 and plan.stops == []


def test_a_stop_is_a_vertical_step_in_the_profile():
    pts = _north(300)
    plan = plan_trip(pts, 10000, 60, CAR, PlanSettings(),
                     [Charger("Mid", pts[140][0], 0.0, 150)])
    assert plan.status == "with_stops"
    km = plan.stops[0]["km"]
    at = [p for p in plan.profile if p[0] == km]
    assert len(at) == 2 and at[0][1] < at[1][1]
    assert at[1][1] == plan.stops[0]["depart_pct"]


def test_charges_only_as_much_as_the_trip_needs():
    # 300 km from 60 %: one stop, and leaving at the 80 % limit would only
    # arrive with more to spare -- the plan leaves at a lower level.
    pts = _north(300)
    plan = plan_trip(pts, 10000, 60, CAR, PlanSettings(mode="cheapest"),
                     [Charger("Mid", pts[140][0], 0.0, 150, price_per_kwh=0.79)])
    stop = plan.stops[0]
    assert stop["depart_pct"] < 80
    assert 10 <= plan.arrival_pct < 15
    assert stop["cost"] == pytest.approx(stop["kwh"] * 0.79 / 0.92, abs=0.05)


def _fast_and_cheap(pts):
    out = []
    for k in range(40, 400, 40):
        out.append(Charger(f"Fast{k}", pts[k][0], 0.0, 150, "Ionity", price_per_kwh=0.79))
        out.append(Charger(f"Cheap{k}", pts[k + 3][0], 0.0, 50, "Cheapo",
                           price_per_kwh=0.45))
    return out


def test_modes_trade_money_against_time():
    pts = _north(400)
    plans = {m: plan_trip(pts, 15000, 90, CAR, PlanSettings(mode=m), _fast_and_cheap(pts))
             for m in ("fastest", "balanced", "cheapest")}
    assert all(p.status == "with_stops" for p in plans.values())
    assert plans["fastest"].charge_s < plans["cheapest"].charge_s
    assert plans["cheapest"].cost < plans["fastest"].cost
    assert plans["fastest"].stops[0]["name"].startswith("Fast")
    assert plans["cheapest"].stops[0]["name"].startswith("Cheap")
    # A high enough value of time makes Balanced behave like Fastest.
    rich = plan_trip(pts, 15000, 90, CAR,
                     PlanSettings(mode="balanced", time_value_per_hour=200),
                     _fast_and_cheap(pts))
    assert rich.stops[0]["name"].startswith("Fast")


def test_cheapest_does_not_add_a_stop_to_save_pennies():
    # Charging twice at a charger 1p cheaper costs a second stop's overhead.
    pts = _north(400)
    ch = [Charger(f"A{k}", pts[k][0], 0.0, 150, price_per_kwh=0.60)
          for k in range(50, 400, 50)]
    ch += [Charger(f"B{k}", pts[k + 2][0], 0.0, 150, price_per_kwh=0.59)
           for k in range(50, 400, 50)]
    plan = plan_trip(pts, 15000, 90, CAR, PlanSettings(mode="cheapest"), ch)
    assert len(plan.stops) == 1


def test_no_stop_is_a_token_top_up():
    pts = _north(400)
    for mode in ("fastest", "balanced", "cheapest"):
        plan = plan_trip(pts, 15000, 90, CAR, PlanSettings(mode=mode),
                         _fast_and_cheap(pts))
        assert all(s["depart_pct"] - s["arrive_pct"] >= 10 - 0.05 for s in plan.stops)


def test_unreachable_says_where_and_keeps_the_stops_that_help():
    pts = _north(600)
    plan = plan_trip(pts, 20000, 60, CAR, PlanSettings(),
                     [Charger("Only", pts[100][0], 0.0, 150)])
    assert plan.status == "unreachable" and plan.arrival_pct is None
    assert [s["name"] for s in plan.stops] == ["Only"]
    assert plan.stops[0]["depart_pct"] == 80.0       # as far as it can get
    assert 100 < plan.unreachable_at_km < 600
    assert plan.profile[-1][1] >= 10.0


def test_a_charger_off_the_route_costs_its_detour():
    pts = _north(300)
    near = Charger("Near", pts[140][0], 0.0, 150, price_per_kwh=0.79)
    off = Charger("Off", pts[140][0], 0.06, 150, price_per_kwh=0.79)   # ~4 km away
    plan = plan_trip(pts, 10000, 60, CAR, PlanSettings(), [off, near])
    assert plan.stops[0]["name"] == "Near"
    assert plan_trip(pts, 10000, 60, CAR, PlanSettings(), [off]).stops[0]["detour_km"] > 3


def test_leave_levels_and_charge_table():
    assert leave_levels(PlanSettings()) == [15, 20, 25, 30, 35, 40, 45, 50, 55, 60,
                                            65, 70, 75, 80]
    assert leave_levels(PlanSettings(reserve_pct=20, charge_to_pct=62)) == \
        [25, 30, 35, 40, 45, 50, 55, 60, 62]
    t = charge_table(CAR, 150)
    assert table_seconds(t, 80) - table_seconds(t, 10) == \
        pytest.approx(charge_seconds(10, 80, CAR, 150), rel=1e-9)


def test_every_plan_states_its_assumptions():
    plan = plan_trip(_north(20), 900, 80, CAR, PlanSettings(), [])
    text = " ".join(plan.assumptions)
    for word in ("53.2 kWh", "17.0 kWh/100 km", "detour", "curve", "£15/hour",
                 "Open Charge Map", "£0.79/kWh"):
        assert word in text


# -- this car's figures -----------------------------------------------------------
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _put(repo, vid, key, minutes_ago, value, prov="reported"):
    repo.record_measurement(vid, _ts(minutes_ago), key, "bat_mgmt", "UDS-0x22",
                            "0x0000", "", prov, "builtin", "experimental", "", value)


def _drive(start_min, minutes, km0, km, wh0, wh, step=5):
    """Readings every `step` minutes over a steady drive; oldest first."""
    n = minutes // step
    odo = [(_ts(start_min - i * step), km0 + km * i / n) for i in range(n + 1)]
    energy = [(_ts(start_min - i * step), wh0 - (wh0 - wh) * i / n)
              for i in range(n + 1)]
    return odo, energy


def test_consumption_needs_enough_driving():
    odo, energy = _drive(60, 30, 1000.0, 20.0, 40000.0, 36600.0)
    got, km = measured_consumption(odo, energy)
    assert got is None and km == pytest.approx(20.0)


def test_consumption_skips_charging_while_parked():
    a_odo, a_e = _drive(300, 60, 1000.0, 60.0, 50000.0, 39800.0)    # 10.2 kWh
    parked = [(_ts(235), 1060.0), (_ts(230), 1060.0)]               # charging
    parked_e = [(_ts(235), 42000.0), (_ts(230), 45000.0)]
    b_odo, b_e = _drive(225, 40, 1060.0, 40.0, 45000.0, 38200.0)    # 6.8 kWh
    got, km = measured_consumption(a_odo + parked + b_odo, a_e + parked_e + b_e)
    assert km == pytest.approx(100.0) and got == pytest.approx(0.17)


def test_consumption_ignores_a_gap_that_could_hide_a_charge():
    # Two observed drives with three unrecorded hours between: the car went
    # 150 km and was charged in that time, so energy fell only 2 kWh across it.
    a_odo, a_e = _drive(600, 60, 1000.0, 60.0, 50000.0, 39800.0)
    b_odo, b_e = _drive(360, 40, 1210.0, 40.0, 37800.0, 31000.0)
    got, km = measured_consumption(a_odo + b_odo, a_e + b_e)
    assert km == pytest.approx(100.0) and got == pytest.approx(0.17)


def test_consumption_nets_regeneration_against_the_climb():
    # Up 30 km for 7.5 kWh, then down 30 km regaining 1.5 kWh: 6 kWh for 60 km.
    # Dropping the descent as if it were a charge would give 0.25 kWh/km.
    up_odo, up_e = _drive(300, 30, 1000.0, 30.0, 50000.0, 42500.0)
    down_odo, down_e = _drive(270, 30, 1030.0, 30.0, 42500.0, 44000.0)
    got, km = measured_consumption(up_odo + down_odo[1:], up_e + down_e[1:])
    assert km == pytest.approx(60.0) and got == pytest.approx(0.10)


def test_consumption_pairs_a_month_of_readings_quickly():
    import time
    odo = [(_ts(20000 - i), 1000.0 + i * 0.5) for i in range(20000)]
    energy = [(_ts(20000 - i), 58000.0 - i * 85.0) for i in range(20000)]
    t0 = time.perf_counter()
    got, _ = measured_consumption(odo, energy)
    # Pairing every reading with every other took minutes here; linear takes
    # a second or two. The margin keeps a busy machine from failing it.
    assert time.perf_counter() - t0 < 10.0
    assert got == pytest.approx(0.17)


def test_car_energy_uses_the_bms_and_ignores_imported_rows(tmp_path):
    repo = Repository(str(tmp_path / "c.db"))
    vid = repo.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    _put(repo, vid, "hv_energy_max", 10, 53200.0)
    _put(repo, vid, "hv_energy_content", 10, 26600.0)
    _put(repo, vid, "hv_energy_content", 5, 53200.0, prov="imported: Car Scanner CSV")
    repo.conn.commit()
    got = car_energy(repo, vid, Config({}))
    assert (got["usable_kwh"], got["start_pct"]) == (53.2, 50.0)
    assert got["consumption_kwh_per_100km"] == 17.0
    assert got["consumption_basis"].startswith("default")
    configured = car_energy(
        repo, vid, Config({"routing": {"consumption_kwh_per_100km": 19}}))
    assert (configured["consumption_kwh_per_100km"], configured["consumption_basis"]) == \
        (19.0, "set in config")
    repo.close()


def test_car_energy_measures_recent_driving_only(tmp_path):
    repo = Repository(str(tmp_path / "c.db"))
    vid = repo.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    for days_ago in (120, 2):                   # one drive long ago, one recent
        start = days_ago * 1440
        odo, energy = _drive(start, 60, 1000.0 + days_ago, 60.0,
                             50000.0, 50000.0 - 60 * (250 if days_ago > 90 else 150))
        for (ts, km), (_, wh) in zip(odo, energy):
            for key, value in (("odometer_km", km), ("hv_energy_content", wh)):
                repo.record_measurement(vid, ts, key, "bat_mgmt", "UDS-0x22",
                                        "0x0000", "", "reported", "builtin",
                                        "experimental", "", value)
    got = car_energy(repo, vid, Config({}))
    assert got["consumption_kwh_per_100km"] == 15.0
    assert got["consumption_basis"].endswith("in the last 90 days")
    repo.close()


# -- providers (requests stubbed) ------------------------------------------------
class _Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body) if not isinstance(body, str) else body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


@pytest.fixture()
def http(monkeypatch):
    calls, replies = [], []

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(providers.requests, "request", fake)
    return calls, replies


def test_missing_keys_explain_where_to_get_them():
    with pytest.raises(providers.RoutingError, match="openrouteservice.org"):
        providers.OpenRouteService("")
    with pytest.raises(providers.RoutingError, match="openchargemap.org"):
        providers.OpenChargeMap("")


def test_keys_from_config_or_environment(monkeypatch):
    monkeypatch.setenv("ODB_ORS_KEY", "env-ors")
    monkeypatch.delenv("ODB_OCM_KEY", raising=False)
    cfg = Config({"routing": {"ocm_api_key": "cfg-ocm"}})
    assert providers.keys(cfg) == ("env-ors", "cfg-ocm")


def test_geocode_and_route_parse(http):
    calls, replies = http
    replies.append(_Reply(200, {"features": [
        {"geometry": {"coordinates": [-1.26, 51.75]}, "properties": {"label": "Oxford"}},
        {"geometry": {}, "properties": {}}]}))
    replies.append(_Reply(200, {"features": [{
        "geometry": {"coordinates": [[-1.26, 51.75, 80.0], [-0.1, 51.5, 20.0]]},
        "properties": {"summary": {"distance": 32000, "duration": 2400}}}]}))
    ors = providers.OpenRouteService("k")
    places = ors.geocode("Oxford", near=(51.0, 0.0))
    assert places == [providers.Place("Oxford", 51.75, -1.26)]
    assert calls[0][2]["params"]["focus.point.lat"] == 51.0
    route = ors.route((51.75, -1.26), (51.5, -0.1))
    assert route.points[0] == (51.75, -1.26, 80.0)
    assert (route.distance_km, route.duration_s) == (32.0, 2400.0)
    body = calls[1][2]["json"]
    assert body["coordinates"] == [[-1.26, 51.75], [-0.1, 51.5]]
    assert body["elevation"] is True
    assert calls[1][2]["headers"] == {"Authorization": "k"}
    assert "boundary.country" not in calls[0][2]["params"]     # no limit configured


@pytest.mark.parametrize("raw,want", [
    ("gb", "GB"), (" gb, fr ", "GB,FR"), ("GB FR", "GB,FR"), ("GBR", "GBR"),
    ("", ""), (None, ""), ("UK!, x, england", ""),
])
def test_country_list(raw, want):
    assert providers.country_list(raw) == want


def test_geocode_is_limited_to_the_configured_countries(http):
    calls, replies = http
    replies.append(_Reply(200, {"features": []}))
    cfg = Config({"routing": {"countries": "gb, fr"}})
    providers.OpenRouteService("k", providers.countries(cfg)).geocode("Brighton")
    assert calls[0][2]["params"]["boundary.country"] == "GB,FR"


def test_chargers_along_the_route_keep_power_operator_and_price(http):
    calls, replies = http
    replies.append(_Reply(200, [
        {"AddressInfo": {"Title": "Services", "Latitude": 51.1, "Longitude": -0.2,
                         "AddressLine1": "M23", "Town": "Crawley"},
         "OperatorInfo": {"Title": "Ionity"}, "UsageCost": "£0.69/kWh",
         "Connections": [{"ConnectionTypeID": 33, "PowerKW": 350},
                         {"ConnectionTypeID": 2, "PowerKW": 400}]},
        {"AddressInfo": {"Title": "Type 2 only", "Latitude": 51.1, "Longitude": -0.2},
         "Connections": [{"ConnectionTypeID": 25, "PowerKW": 22}]},
        {"AddressInfo": {"Title": "Broken"}},
        {"AddressInfo": {"Title": "Supercharger", "Latitude": 51.1, "Longitude": -0.2},
         "OperatorInfo": {"Title": "Tesla (Tesla-only charging)"},
         "Connections": [{"ConnectionTypeID": 33, "PowerKW": 250}]},
        {"AddressInfo": {"Title": "Hotel", "Latitude": 51.1, "Longitude": -0.2},
         "UsageTypeID": 6, "Connections": [{"ConnectionTypeID": 33, "PowerKW": 50}]},
        {"AddressInfo": {"Title": "Coming soon", "Latitude": 51.1, "Longitude": -0.2},
         "StatusType": {"IsOperational": False},
         "Connections": [{"ConnectionTypeID": 33, "PowerKW": 150}]},
        {"AddressInfo": {"Title": "Open to all", "Latitude": 51.2, "Longitude": -0.2},
         "OperatorInfo": {"Title": "Tesla (including non-tesla)"}, "UsageTypeID": 5,
         "StatusType": {"IsOperational": True},
         "Connections": [{"ConnectionTypeID": 33, "PowerKW": 250}]},
    ]))
    route = [(51.0 + k / 1000, -0.2, None) for k in range(1000)]
    got = providers.OpenChargeMap("k").along(route, 5)
    assert got == [Charger("Services", 51.1, -0.2, 350.0, "Ionity", "M23, Crawley",
                           usage_cost="£0.69/kWh"),
                   Charger("Open to all", 51.2, -0.2, 250.0,
                           "Tesla (including non-tesla)", usage_type=5)]
    params = calls[0][2]["params"]
    assert (params["distance"], params["connectiontypeid"]) == (5, 33)
    assert len(params["polyline"]) < 3000                 # thinned to fit a URL


def test_polyline_is_googles_format():
    # The worked example in Google's polyline documentation.
    example = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert providers.encode_polyline(example) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    line = [(float(k), 0.0) for k in range(1000)]
    thin = providers.thin_line(line)
    assert len(thin) <= providers.POLYLINE_MAX_POINTS + 1 and thin[-1] == line[-1]


# -- prices ------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [
    ("£0.79/kWh", (0.79, 0.0)),
    ("£0.79/kWh; other tariffs available", (0.79, 0.0)),
    ("DC: £0.79/kWh,AC: £0.49/kWh", (0.79, 0.0)),
    ("DC (contactless): £0.85/kWh, DC (GRIDSERVE app): £0.79/kWh, AC: £0.49/kWh",
     (0.85, 0.0)),
    ("£0.20/kWh; min £1.50", (0.20, 0.0)),
    ("Rapid £1.00 fee then £0.30p per kWh AC socket 50p fee then 30p per kWh",
     (0.30, 1.00)),
    ("£1.00 + £0.30/kWh", (0.30, 1.00)),
    ("29p/kWh", (0.29, 0.0)),
    ("0.69 GBP per kWh", (0.69, 0.0)),
    ("75p", None), ("Free", None), ("€0.59/kWh", None), ("£12/kWh", None),
    ("", None), (None, None),
])
def test_usage_cost_text_is_read(text, want):
    assert prices.parse_usage_cost(text) == want


def test_own_prices_beat_listed_ones_and_free_is_not_believed():
    own = prices.own_prices({"Ionity": 0.39, "Tesla": "£0.55", "bad": "x", "Odd": 9})
    assert own == [("ionity", 0.39), ("tesla", 0.55)]
    assert prices.own_prices("Ionity = 0.39\nnonsense\nGridserve=0.66") == \
        [("ionity", 0.39), ("gridserve", 0.66)]
    assert prices.price_for("IONITY GmbH", "£0.69/kWh", own, 0.79) == \
        (0.39, 0.0, "your price for Ionity")
    assert prices.price_for("InstaVolt Ltd", "£0.85/kWh", own, 0.79) == \
        (0.85, 0.0, "Open Charge Map")
    assert prices.price_for("Shell", "", own, 0.79) == (0.79, 0.0, "default price")
    assert prices.price_for("Lidl", "Free", own, 0.79)[2].startswith("listed as free")


@pytest.mark.parametrize("reply,match", [
    (_Reply(401, "no"), "refused the API key"),
    (_Reply(429, "slow down"), "rate limit"),
    (_Reply(500, "boom"), "HTTP 500"),
    (_Reply(200, "<html>"), "non-JSON"),
    (providers.requests.ConnectionError("offline"), "Could not reach"),
])
def test_failures_become_plain_messages(http, reply, match):
    http[1].append(reply)
    with pytest.raises(providers.RoutingError, match=match):
        providers.OpenRouteService("k").geocode("x")


# -- the Route page ------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "r.db")
    repo = Repository(path)
    vid = repo.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    _put(repo, vid, "hv_energy_max", 10, 53200.0)
    _put(repo, vid, "hv_energy_content", 10, 47880.0)            # 90 %
    repo.record_measurement(vid, _ts(10), "gps_position", "gps", "UDS-0x22", "0x2430",
                            "", "reported", "builtin", "experimental", "", None,
                            json.dumps({"lat": 50.0, "lon": 0.0, "altitude_m": 80}))
    repo.conn.commit()
    repo.close()
    return path


def _client(path, routing=None, tiles="osm"):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": path}, "routing": routing or {},
                  "web": {"host": "127.0.0.1", "map_tiles": tiles}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      raise_server_exceptions=False)


KEYS = {"ors_api_key": "ors-secret-key", "ocm_api_key": "ocm-secret-key"}


@pytest.fixture()
def services(monkeypatch):
    """Stub both services: a 400 km road north, a charger every 50 km."""
    pts = _north(400)
    stops = [Charger(f"Hub <b>{k}</b>", pts[k][0], 0.0, 150, "Op & Co")
             for k in range(50, 400, 50)]

    def geocode(self, text, near=None, size=5):
        if text == "nowhere":
            return []
        return [providers.Place(f"{text}, England", pts[-1][0], 0.0),
                providers.Place(f"{text} (other)", 0.0, 0.0)]

    def route(self, start, end, avoid=()):
        return providers.Route(pts, 400.0, 15000)

    def along(self, points, radius_km):
        return list(stops)
    monkeypatch.setattr(providers.OpenRouteService, "geocode", geocode)
    monkeypatch.setattr(providers.OpenRouteService, "route", route)
    monkeypatch.setattr(providers.OpenChargeMap, "along", along)


def test_page_without_keys_says_how_to_get_them(db):
    body = _client(db).get("/route").text
    assert "free API keys for OpenRouteService and OpenChargeMap" in body
    assert 'name="start_pct"' in body and 'value="90.0"' in body      # from the BMS
    assert '<a href="/route">Route</a>' in body


def test_plan_is_rendered_without_leaking_keys(db, services):
    r = _client(db, KEYS).get("/route", params={"to": "Leeds"})
    assert r.status_code == 200
    body = r.text
    assert "ors-secret-key" not in body and "ocm-secret-key" not in body
    assert "Charging stops" in body
    assert 'id="route-map"' in body and 'id="route-soc"' in body
    assert "Car&#39;s last recorded position" in body and "Leeds, England" in body
    # Third-party names are escaped in the table and in the JSON for the map.
    assert "<b>" not in body.split('id="route-data"')[0].split("<h2>")[1]
    data = json.loads(body.split('id="route-data">')[1].split("</script>")[0])
    assert data["stops"][0]["name"].startswith("Hub <b>")
    assert data["reserve"] == 10.0 and data["profile"][0] == [0.0, 90.0]
    assert "Leeds (other)" in body                       # other matches offered


def test_form_values_override_the_car(db, services):
    body = _client(db, KEYS).get("/route", params={
        "to": "Leeds", "start_pct": "100", "consumption": "12", "reserve": "15"}).text
    assert "Consumption 12.0 kWh/100 km" in body and "Arrive with at least 15 %" in body
    # 400 km at 12 kWh/100 km is 48 kWh; 100 % down to 15 % is 45.2 kWh.
    assert "after 1 charging stop\n" in body


def test_mode_and_own_prices_reach_the_plan(db, services):
    client = _client(db, {**KEYS, "prices": {"Op & Co": 0.40}, "mode": "cheapest"})
    body = client.get("/route", params={"to": "Leeds"}).text
    assert '<option value="cheapest" selected>' in body
    assert "your price for Op &amp; Co" in body and "£0.40/kWh" in body
    assert "Charging cost" in body and "Cheapest: stops chosen" in body
    data = json.loads(body.split('id="route-data">')[1].split("</script>")[0])
    assert data["stops"][0]["price_per_kwh"] == 0.4
    fastest = client.get("/route", params={"to": "Leeds", "mode": "fastest"}).text
    assert '<option value="fastest" selected>' in fastest
    assert "Fastest: stops chosen" in fastest
    junk = client.get("/route", params={"to": "Leeds", "mode": "<script>"}).text
    assert '<option value="cheapest" selected>' in junk        # config default


def test_destination_is_searched_near_the_typed_start(db, services, monkeypatch):
    seen = []

    def geocode(self, text, near=None, size=5):
        seen.append((text, near, self.countries))
        return [providers.Place(text, 53.0 if text == "Leeds" else 51.5, 0.0)]
    monkeypatch.setattr(providers.OpenRouteService, "geocode", geocode)
    _client(db, {**KEYS, "countries": "gb"}).get(
        "/route", params={"to": "Leeds", "from": "London"})
    assert seen[0][0] == "London"
    assert seen[1] == ("Leeds", (51.5, 0.0), "GB")


def test_unreachable_and_unknown_places_are_explained(db, services, monkeypatch):
    client = _client(db, KEYS)
    lost = client.get("/route", params={"to": "nowhere"}).text
    assert "No place found for “nowhere”" in lost
    monkeypatch.setattr(providers.OpenChargeMap, "along", lambda self, a, b: [])
    body = client.get("/route", params={"to": "Leeds"}).text
    assert "would fall below the reserve" in body


def test_service_failure_is_a_message_not_a_500(db, monkeypatch):
    def boom(self, *a, **kw):
        raise providers.RoutingError("api.openrouteservice.org rate limit reached")
    monkeypatch.setattr(providers.OpenRouteService, "geocode", boom)
    r = _client(db, KEYS).get("/route", params={"to": "Leeds"})
    assert r.status_code == 200 and "rate limit reached" in r.text


@pytest.mark.parametrize("query", [
    "to=Leeds&start_pct=x", "to=Leeds&start_pct=999", "to=Leeds&consumption=-1",
    "to=Leeds&reserve=nan", "to=" + "x" * 5000, "to=Leeds&charge_to=inf"])
def test_bad_numbers_never_500(db, services, query):
    assert _client(db, KEYS).get("/route?" + query).status_code == 200
