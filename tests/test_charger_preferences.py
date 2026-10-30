"""Charger choice: networks to prefer or never use, public access only, and
the driver's own picks -- avoid this charger, use that one instead.

Pinned: avoided networks and chargers never appear, a preference changes which
charger is chosen but never a shown price and never adds a stop a trip does not
need, public_only leaves out members-only chargers, a chosen charger is used
whenever it can be reached (and ignored when it cannot), each stop lists the
alternatives near it, and the Route page turns all of that into a form and links.
No test reaches the network.
"""
import json

import pytest

from routing import providers
from routing.planner import Charger, PlanSettings, plan_trip
from test_route_planner import CAR, KEYS, _client, _north, db  # noqa: F401

ROAD = _north(400)


def _at(km, name, operator="", price=0.6, power=150, id="", usage=None, lon=0.0):
    return Charger(name, ROAD[km][0], lon, power, operator, price_per_kwh=price,
                   id=id, usage_type=usage)


def _plan(chargers, start_pct=90, road=ROAD, **settings):
    return plan_trip(road, 15000, start_pct, CAR, PlanSettings(**settings), chargers)


# --- networks -------------------------------------------------------------------

def test_an_avoided_network_is_never_used():
    shell = [_at(k, f"S{k}", "Shell Recharge", price=0.3) for k in range(50, 400, 50)]
    ionity = [_at(k, f"I{k}", "IONITY GmbH", price=0.7) for k in range(100, 400, 100)]
    chargers = shell + ionity
    assert any("Shell" in s["operator"] for s in _plan(chargers).stops)
    plan = _plan(chargers, avoid_networks=("shell",))
    assert plan.status == "with_stops"
    assert all(s["operator"] == "IONITY GmbH" for s in plan.stops)


def test_a_preferred_network_wins_but_the_real_price_is_shown():
    chargers = []
    for k in range(50, 400, 50):
        chargers += [_at(k, f"A{k}", "Alpha", price=0.50),
                     _at(k, f"I{k}", "Ionity", price=0.55)]
    assert {s["operator"] for s in _plan(chargers).stops} == {"Alpha"}
    plan = _plan(chargers, prefer_networks=("ionity",))
    assert {s["operator"] for s in plan.stops} == {"Ionity"}
    for s in plan.stops:
        assert s["price_per_kwh"] == 0.55 and s["preferred"] is True
        assert s["cost"] == pytest.approx(0.55 * s["kwh"] / 0.92, rel=0.01)


def test_a_preference_never_adds_a_stop_the_trip_does_not_need():
    road = _north(100)
    plan = plan_trip(road, 3600, 90, CAR, PlanSettings(prefer_networks=("Ionity",)),
                     [Charger("I50", road[50][0], 0.0, 150, "Ionity", price_per_kwh=0.3)])
    assert plan.status == "direct" and plan.stops == []


def test_public_only_leaves_out_members_only_chargers():
    chargers = [_at(k, f"M{k}", usage=4, price=0.3) for k in range(50, 400, 50)] + \
               [_at(k, f"N{k}", usage=7, price=0.3) for k in range(60, 400, 50)] + \
               [_at(k, f"P{k}", usage=u, price=0.7)
                for k, u in zip(range(70, 400, 50), (1, 5, None, 1, 5, None, 1))]
    assert any(s["name"][0] in "MN" for s in _plan(chargers).stops)
    plan = _plan(chargers, public_only=True)
    assert plan.status == "with_stops"
    assert all(s["name"].startswith("P") for s in plan.stops)
    only_members = [c for c in chargers if c.usage_type in (4, 7)]
    assert _plan(only_members, public_only=True).status == "unreachable"


# --- the driver's picks ----------------------------------------------------------

def _hubs():
    every50 = range(50, 400, 50)
    return [_at(k, f"Hub{k}", "Op", price=0.5, id=str(1000 + k)) for k in every50] + \
           [_at(k + 5, f"Dear{k}", "Op", price=0.95, id=str(2000 + k)) for k in every50]


def test_an_avoided_charger_is_never_used_or_offered():
    first = _plan(_hubs()).stops[0]
    plan = _plan(_hubs(), avoid_chargers=(first["id"],))
    assert first["id"] not in [s["id"] for s in plan.stops]
    offered = [a["id"] for s in plan.stops for a in s["alternatives"]]
    assert first["id"] not in offered


def test_a_chosen_charger_is_used_at_its_real_price():
    assert all(not s["name"].startswith("Dear") for s in _plan(_hubs()).stops)
    plan = _plan(_hubs(), use_chargers=("2150",))
    dear = [s for s in plan.stops if s["id"] == "2150"]
    assert dear, "the chosen charger was not used"
    assert dear[0]["price_per_kwh"] == 0.95
    assert dear[0]["cost"] == pytest.approx(0.95 * dear[0]["kwh"] / 0.92, rel=0.01)
    assert plan.cost < 1000, "the search-only bonus leaked into a shown cost"


def test_a_chosen_charger_that_cannot_be_reached_is_ignored():
    far = _at(200, "Far off", price=0.3, id="9", lon=1.0)       # ~70 km off the road
    base = _plan(_hubs())
    plan = _plan(_hubs() + [far], use_chargers=("9",))
    assert [s["id"] for s in plan.stops] == [s["id"] for s in base.stops]


def test_each_stop_lists_the_alternatives_near_it():
    plan = _plan(_hubs())
    stop = plan.stops[0]
    alts = stop["alternatives"]
    assert 1 <= len(alts) <= 4
    assert stop["id"] not in [a["id"] for a in alts]
    gaps = [abs(a["km"] - stop["km"]) for a in alts]
    assert gaps == sorted(gaps) and max(gaps) <= 30
    assert {"id", "name", "operator", "power_kw", "km", "price_per_kwh"} <= set(alts[0])


def test_the_plan_says_which_choices_it_followed():
    text = " ".join(_plan(_hubs(), prefer_networks=("Ionity",), avoid_networks=("Shell",),
                          public_only=True, avoid_chargers=("1",)).assumptions)
    assert "Never uses: Shell" in text and "Prefers Ionity" in text
    assert "Public access only" in text and "1 avoided" in text


def test_open_charge_map_ids_and_access_types_are_kept(monkeypatch):
    poi = {"ID": 123456, "UsageTypeID": 4,
           "AddressInfo": {"Title": "Hub", "Latitude": 51.0, "Longitude": 0.0},
           "OperatorInfo": {"Title": "Ionity"},
           "Connections": [{"ConnectionTypeID": 33, "PowerKW": 350}]}
    c = providers._charger(poi)
    assert (c.id, c.usage_type) == ("123456", 4)
    staff_only = dict(poi, UsageTypeID=6)
    assert providers._charger(staff_only) is None                   # always left out


# --- the Route page --------------------------------------------------------------

@pytest.fixture()
def hubs(monkeypatch):
    def geocode(self, text, near=None, size=5):
        return [providers.Place(f"{text}, England", ROAD[-1][0], 0.0)]

    def route(self, start, end, avoid=()):
        return providers.Route(ROAD, 400.0, 15000)

    monkeypatch.setattr(providers.OpenRouteService, "geocode", geocode)
    monkeypatch.setattr(providers.OpenRouteService, "route", route)
    monkeypatch.setattr(providers.OpenChargeMap, "along", lambda self, p, r: _hubs())


def _data(body):
    return json.loads(body.split('id="route-data">')[1].split("</script>")[0])


def test_the_form_starts_from_config_and_shows_the_choices(db, hubs):  # noqa: F811
    routing = dict(KEYS, prefer_networks=["Ionity"], avoid_networks=["Shell"],
                   public_only=True)
    body = _client(db, routing).get("/route", params={"to": "Leeds"}).text
    assert 'name="prefer" value="Ionity"' in body
    assert 'name="avoid_nets" value="Shell"' in body
    assert 'name="public_only" value="1" checked' in body
    assert "Prefers Ionity" in body and "Never uses: Shell" in body


def test_avoid_and_use_links_plan_again_with_the_pick(db, hubs):  # noqa: F811
    client = _client(db, KEYS)
    stop = _data(client.get("/route", params={"to": "Leeds"}).text)["stops"][0]
    assert "avoid_ids=" + stop["id"] in stop["avoid_url"]
    after = _data(client.get(stop["avoid_url"]).text)["stops"]
    assert stop["id"] not in [s["id"] for s in after]

    alt = stop["alternatives"][0]
    chosen = _data(client.get(alt["use_url"]).text)["stops"]
    assert alt["id"] in [s["id"] for s in chosen]
    assert stop["id"] not in [s["id"] for s in chosen]


def test_hand_edited_picks_are_kept_to_charger_ids(db, hubs):  # noqa: F811
    body = _client(db, KEYS).get("/route", params={
        "to": "Leeds", "avoid_ids": "1050,<script>,x" * 1 + ",../etc"}).text
    assert 'name="avoid_ids" value="1050,x"' in body
    assert "<script>," not in body
