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
from routing import providers  # noqa: E402
from routing.car import car_energy, measured_consumption  # noqa: E402
from routing.planner import (  # noqa: E402
    CarModel,
    Charger,
    PlanSettings,
    charge_seconds,
    plan_trip,
    segment_kwh,
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


def _nowhere(lat, lon, radius_km):
    return []


def test_route_needs_two_points():
    with pytest.raises(ValueError):
        plan_trip([(50.0, 0.0, None)], 0, 80, CAR, PlanSettings(), _nowhere)


def test_never_charges_above_what_the_battery_holds():
    plan = plan_trip(_north(50), 1800, 100, CAR, PlanSettings(), _nowhere)
    assert plan.status == "direct" and max(p[1] for p in plan.profile) <= 100.0


def test_a_stop_is_a_vertical_step_in_the_profile():
    pts = _north(300)
    charger = Charger("Mid", pts[140][0], 0.0, 150)   # the 141 km search finds it
    def near(lat, lon, r):
        return [charger] if abs(lat - charger.lat) < 0.04 else []
    plan = plan_trip(pts, 10000, 60, CAR, PlanSettings(), near)
    assert plan.status == "with_stops"
    km = plan.stops[0]["km"]
    at = [p for p in plan.profile if p[0] == km]
    assert len(at) == 2 and at[0][1] < at[1][1] == 80.0


def test_max_stops_gives_up_rather_than_looping():
    pts = _north(600)
    every = [Charger(f"C{k}", pts[k][0], 0.0, 150) for k in range(10, 600, 10)]

    def near(lat, lon, r):
        return [c for c in every if abs(c.lat - lat) * 111.195 <= r]
    plan = plan_trip(pts, 20000, 50, CAR, PlanSettings(max_stops=1), near)
    assert plan.status == "unreachable" and len(plan.stops) == 1


def test_every_plan_states_its_assumptions():
    plan = plan_trip(_north(20), 900, 80, CAR, PlanSettings(), _nowhere)
    text = " ".join(plan.assumptions)
    for word in ("53.2 kWh", "17.0 kWh/100 km", "detour", "curve"):
        assert word in text


# -- this car's figures -----------------------------------------------------------
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _ts(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _put(repo, vid, key, minutes_ago, value, prov="reported"):
    repo.record_measurement(vid, _ts(minutes_ago), key, "bat_mgmt", "UDS-0x22",
                            "0x0000", "", prov, "builtin", "experimental", "", value)


def test_consumption_needs_enough_driving():
    odo = [(_ts(60), 1000.0), (_ts(30), 1020.0)]
    energy = [(_ts(60), 40000.0), (_ts(30), 36600.0)]
    assert measured_consumption(odo, energy) == (None, 20.0)


def test_consumption_skips_stretches_with_charging():
    odo = [(_ts(300), 1000.0), (_ts(200), 1060.0), (_ts(100), 1060.0), (_ts(0), 1100.0)]
    energy = [(_ts(300), 50000.0), (_ts(200), 39800.0),      # 60 km, 10.2 kWh
              (_ts(100), 45000.0),                           # charged, parked
              (_ts(0), 38200.0)]                             # 40 km, 6.8 kWh
    got, km = measured_consumption(odo, energy)
    assert km == 100.0 and got == pytest.approx(0.17)


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
        {"geometry": {"coordinates": [-0.17, 51.24]}, "properties": {"label": "Redhill"}},
        {"geometry": {}, "properties": {}}]}))
    replies.append(_Reply(200, {"features": [{
        "geometry": {"coordinates": [[-0.17, 51.24, 80.0], [-0.1, 51.5, 20.0]]},
        "properties": {"summary": {"distance": 32000, "duration": 2400}}}]}))
    ors = providers.OpenRouteService("k")
    places = ors.geocode("Redhill", near=(51.0, 0.0))
    assert places == [providers.Place("Redhill", 51.24, -0.17)]
    assert calls[0][2]["params"]["focus.point.lat"] == 51.0
    route = ors.route((51.24, -0.17), (51.5, -0.1))
    assert route.points[0] == (51.24, -0.17, 80.0)
    assert (route.distance_km, route.duration_s) == (32.0, 2400.0)
    body = calls[1][2]["json"]
    assert body["coordinates"] == [[-0.17, 51.24], [-0.1, 51.5]]
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


def test_chargers_keep_ccs_power_and_operator(http):
    _, replies = http
    replies.append(_Reply(200, [
        {"AddressInfo": {"Title": "Services", "Latitude": 51.1, "Longitude": -0.2,
                         "AddressLine1": "M23", "Town": "Crawley"},
         "OperatorInfo": {"Title": "Ionity"},
         "Connections": [{"ConnectionTypeID": 33, "PowerKW": 350},
                         {"ConnectionTypeID": 2, "PowerKW": 400}]},
        {"AddressInfo": {"Title": "Type 2 only", "Latitude": 51.1, "Longitude": -0.2},
         "Connections": [{"ConnectionTypeID": 25, "PowerKW": 22}]},
        {"AddressInfo": {"Title": "Broken"}},
    ]))
    ocm = providers.OpenChargeMap("k")
    got = ocm.near(51.1, -0.2, 5)
    assert got == [Charger("Services", 51.1, -0.2, 350.0, "Ionity", "M23, Crawley")]
    assert ocm.near(51.1, -0.2, 5) is got                 # cached: no second call


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

    def route(self, start, end):
        return providers.Route(pts, 400.0, 15000)

    def near(self, lat, lon, radius_km):
        return [c for c in stops if abs(c.lat - lat) * 111.195 <= radius_km]
    monkeypatch.setattr(providers.OpenRouteService, "geocode", geocode)
    monkeypatch.setattr(providers.OpenRouteService, "route", route)
    monkeypatch.setattr(providers.OpenChargeMap, "near", near)


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
    monkeypatch.setattr(providers.OpenChargeMap, "near", lambda self, a, b, c: [])
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
