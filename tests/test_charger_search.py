"""Finding a charging stop (routing/charger_search.py): judged for this car,
compared with the trip as planned, offered through the existing picks.

All chargers and roads are synthetic; no service is called."""
from datetime import datetime, timedelta, timezone

import pytest

from routing import providers
from routing.charger_search import average_kw, search
from routing.planner import CarModel, Charger, PlanSettings, plan_trip
from test_route_planner import KEYS, _client, _north, db  # noqa: F401

CAR = CarModel(usable_kwh=56.0, consumption_kwh_per_km=0.17)
ROAD = _north(400)
KM_PER_DEG_LON = 111.195 * 0.6428     # cos(50°)


def _at(km, name, kw=150, off_km=1.0, **kw_args):
    return Charger(name, ROAD[km][0], off_km / KM_PER_DEG_LON, kw, id=name, **kw_args)


def _plan(chargers, settings=None, start=90):
    return plan_trip(ROAD, 15000, start, CAR, settings or PlanSettings(), chargers)


def _search(chargers, settings=None, start=90, **kw):
    settings = settings or PlanSettings()
    plan = _plan(chargers, settings, start)
    return plan, search(ROAD, 15000, start, CAR, settings, chargers, plan, **kw)


def _by_name(got):
    return {f["name"]: f for f in got["found"]}


def test_a_bigger_number_on_the_listing_is_not_a_faster_charge_for_this_car():
    # Three chargers side by side, none of them a planned stop: the plan
    # stops at km 250, so each is reached on the way at the same charge.
    _, got = _search([_at(200, "Ultra", 350), _at(200, "Rapid", 150, off_km=1.01),
                      _at(200, "Slowish", 50, off_km=1.02), _at(250, "Planned")],
                     start=100)
    f = _by_name(got)
    assert f["Ultra"]["arrive_pct"] < 50
    assert f["Ultra"]["charge_min"] == pytest.approx(f["Rapid"]["charge_min"], abs=1)
    assert f["Slowish"]["charge_min"] > f["Rapid"]["charge_min"] + 5
    assert f["Ultra"]["average_kw"] <= 110
    assert any("this car takes at most 110" in n for n in f["Ultra"]["notes"])
    assert average_kw(CAR, 20, 80, 350) == pytest.approx(average_kw(CAR, 20, 80, 150))


def test_the_arrival_is_the_plans_charge_there_less_the_detour():
    plan, got = _search([_at(100, "Near", off_km=1.0), _at(100, "Off", off_km=4.0)])
    f = _by_name(got)
    passing = next(p for k, p in plan.profile if k >= 100)
    assert f["Near"]["arrive_pct"] < passing + 0.5
    assert f["Off"]["arrive_pct"] < f["Near"]["arrive_pct"]
    assert f["Off"]["detour_min"] > f["Near"]["detour_min"]


def test_each_promising_one_is_planned_for_real_with_it():
    chargers = [_at(k, f"C{k}") for k in range(50, 400, 50)] + [_at(240, "Beside")]
    plan, got = _search(chargers)
    assert len(plan.stops) == 1
    stop = plan.stops[0]
    other = "Beside" if stop["name"] == "C250" else "C250"
    tried = {f["name"]: f for f in got["found"] if "with_stop" in f}
    assert len(tried) >= 5
    for f in tried.values():
        w = f["with_stop"]
        assert w["status"] == "with_stops"
        if f["arrive_pct"] > 80 - 10:            # too full to stop: said, not used
            assert not w["used"] and any("too full" in n for n in f["notes"])
        else:
            assert w["used"], f["name"]
    # less the drive off the route, which the planner leaves out
    assert tried[stop["name"]]["arrive_pct"] == pytest.approx(stop["arrive_pct"] - 0.4,
                                                              abs=0.1)
    assert tried[stop["name"]]["with_stop"]["extra_min"] == 0
    # A charger near a planned stop can stand in for it.
    assert tried[other]["instead_of"]["id"] == stop["id"]
    # Just past a planned stop, the arrival is counted as if it were not made.
    _, after = _search(chargers + [_at(260, "After")])
    a = _by_name(after)["After"]
    assert a["instead_of"]["id"] == stop["id"] and a["arrive_pct"] < stop["arrive_pct"]


def test_a_stop_a_trip_does_not_need_costs_time():
    short = _north(120)
    plan = plan_trip(short, 4000, 60, CAR, PlanSettings(), [])
    c = Charger("Mid", short[60][0], 0.01, 150, id="mid")
    got = search(short, 4000, 60, CAR, PlanSettings(), [c], plan)
    extra = got["found"][0]["with_stop"]
    assert plan.status == "direct"
    assert extra["used"] and extra["extra_min"] >= 10


def test_what_the_driver_avoids_or_cannot_use_is_left_out_and_said():
    chargers = [_at(100, "Mine", operator="Alpha"), _at(110, "Theirs", operator="Beta"),
                _at(120, "Slow", 22)]
    _, got = _search(chargers, PlanSettings(avoid_networks=("beta",)))
    assert set(_by_name(got)) == {"Mine"}
    text = " ".join(got["notes"])
    assert "1 charger(s) left out" in text and "below your 50 kW minimum" in text


def test_near_a_place_and_at_the_destination():
    end = ROAD[-1]
    chargers = [Charger("AtEnd", end[0], 0.003, 150, id="end"), _at(200, "Middle")]
    _, got = _search(chargers, area="destination", near=(end[0], end[1]), within_km=5)
    assert [f["name"] for f in got["found"]] == ["AtEnd"]
    f = got["found"][0]
    assert f["at"] == "destination" and "with_stop" not in f
    assert any("after you arrive" in n for n in f["notes"])


def test_how_far_to_trust_a_listing_is_said():
    old = (datetime.now(timezone.utc) - timedelta(days=4 * 365)).isoformat()
    chargers = [_at(150, "Old", checked=old, points=1),
                _at(160, "Known", price_per_kwh=0.5, checked=datetime.now(
                    timezone.utc).isoformat(), points=6)]
    chargers[1].price_source = "your price"
    _, got = _search(chargers)
    f = _by_name(got)
    notes = " ".join(f["Old"]["notes"])
    assert "last checked 4 years ago" in notes and "single charging point" in notes
    assert "price not known" in notes
    assert not any("price not known" in n or "years ago" in n
                   for n in f["Known"]["notes"])


def test_a_chosen_charger_off_the_planners_radius_is_used():
    chargers = [_at(200, "Off8", off_km=8.0), _at(220, "On", off_km=1.0)]
    plan, got = _search(chargers, within_km=12)
    assert [s["name"] for s in plan.stops] == ["On"]
    assert _by_name(got)["Off8"]["with_stop"]["used"]


@pytest.fixture()
def search_services(monkeypatch):
    pts = ROAD
    hubs = [Charger(f"Hub {k}", pts[k][0], 0.002, 350 if k == 200 else 150,
                    "Op", id=f"h{k}") for k in range(50, 400, 50)]

    def geocode(self, text, near=None, size=5):
        return [providers.Place(f"{text}, England", pts[-1][0], 0.0)]

    def route(self, start, end, avoid=()):
        return providers.Route(pts, 400.0, 15000)

    def along(self, points, radius_km):
        return list(hubs)

    def near(self, lat, lon, radius_km):
        return [h for h in hubs if abs(h.lat - lat) < radius_km / 111.0]
    monkeypatch.setattr(providers.OpenRouteService, "geocode", geocode)
    monkeypatch.setattr(providers.OpenRouteService, "route", route)
    monkeypatch.setattr(providers.OpenChargeMap, "along", along)
    monkeypatch.setattr(providers.OpenChargeMap, "near", near)


def test_the_route_page_finds_chargers_and_offers_them_as_picks(db, search_services):  # noqa: F811
    client = _client(db, KEYS)
    body = client.get("/route", params={"to": "Leeds"}).text
    assert "Find a charging stop" in body and "add this stop" not in body
    body = client.get("/route", params={"to": "Leeds", "find": "route",
                                        "within": "10"}).text
    assert "add this stop" in body and "use_ids=h" in body
    assert "350 kW listed; this car takes at most 110" in body
    assert "ors-secret-key" not in body and "ocm-secret-key" not in body
    body = client.get("/route", params={"to": "Leeds", "find": "place",
                                        "find_place": "Hubtown", "within": "3"}).text
    assert "near Hubtown, England" in body


def test_near_a_place_asks_the_map_for_that_point_and_reads_the_listing_age(monkeypatch):
    from test_route_planner import _Reply
    calls = []

    def fake(method, url, **kw):
        calls.append(kw)
        return _Reply(200, [{
            "ID": 77, "AddressInfo": {"Title": "Square", "Latitude": 53.8,
                                      "Longitude": -1.55},
            "OperatorInfo": {"Title": "Gridserve"}, "NumberOfPoints": 1,
            "DateLastVerified": "2021-03-04T10:00:00Z",
            "Connections": [{"ConnectionTypeID": 33, "PowerKW": 150}]}])
    monkeypatch.setattr(providers.requests, "request", fake)
    got = providers.OpenChargeMap("k").near(53.80012, -1.549991, 8)
    assert got[0].id == "77" and got[0].points == 1
    assert got[0].checked.startswith("2021-03-04")
    params = calls[0]["params"]
    assert (params["latitude"], params["longitude"], params["distance"]) == \
        (53.80012, -1.54999, 8)
    assert "polyline" not in params
