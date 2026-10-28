"""Live traffic and roads to avoid: Mapbox's reply read the way the phone
reads it (tests/data/mapbox_route_sample.json is shared with NavTest.kt), the
requests each service is sent, and the Route page offering the ways there.
No test reaches the network.
"""
import json
import os

import pytest

from config import Config
from routing import providers
from routing.planner import cumulative_km
from test_route_planner import KEYS, _client, _north, _Reply, db, http, services  # noqa: F401

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                      "mapbox_route_sample.json")


def _sample():
    with open(SAMPLE, encoding="utf-8") as f:
        return json.load(f)


# -- reading Mapbox ------------------------------------------------------------------
def test_mapbox_reply_is_read_like_the_phone_reads_it():
    ways = providers.parse_mapbox(_sample())
    assert len(ways) == 2
    w = ways[0]
    assert (w.distance_km, w.duration_s, w.typical_s) == (9.5, 600.0, 480.0)
    assert w.delay_s == 120.0 and w.summary == "A40, M40"
    assert w.points[0] == (51.75, -1.26, None)
    # Segments 2-4 (heavy, heavy, severe) of 8: one stretch, at its worst
    # level -- what NavTest.heavyTrafficIsOneRunAtItsWorstLevel expects too.
    cum = cumulative_km(w.points)
    assert w.runs == [(pytest.approx(cum[2]), pytest.approx(cum[5]), "severe")]
    assert w.heavy_km == pytest.approx(cum[5] - cum[2])
    assert ways[1].summary == "A44, B4027"
    assert ways[1].runs == [] and ways[1].delay_s == 20.0


def test_congestion_runs_when_counts_differ_and_at_the_end():
    cum = [0.0, 1.0, 2.0, 3.0, 4.0]                    # 4 segments
    # Two levels over four segments: each covers two.
    assert providers.congestion_runs(["low", "heavy"], cum) == [(2.0, 4.0, "heavy")]
    assert providers.congestion_runs(["severe", "heavy", "low", "unknown"], cum) == \
        [(0.0, 2.0, "severe")]
    assert providers.congestion_runs([], cum) == []
    assert providers.congestion_runs(["heavy"], [0.0]) == []


def test_no_route_is_a_plain_message():
    with pytest.raises(providers.RoutingError, match="No route found"):
        providers.parse_mapbox({"code": "NoRoute", "message": "No route found"})
    with pytest.raises(providers.RoutingError, match="found no route"):
        providers.parse_mapbox({"code": "Ok", "routes": []})


# -- the requests ---------------------------------------------------------------------
def test_mapbox_is_asked_for_traffic_alternatives_and_roads_to_avoid(http):  # noqa: F811
    calls, replies = http
    replies.append(_Reply(200, _sample()))
    ways = providers.Mapbox("tok").routes((51.75, -1.26), (51.8354, -1.26),
                                          ["tolls", "motorways"])
    method, url, kw = calls[0]
    assert method == "GET"
    assert url == providers.MAPBOX_BASE + "/-1.260000,51.750000;-1.260000,51.835400"
    params = kw["params"]
    assert params["alternatives"] == "true" and params["annotations"] == "congestion"
    assert params["overview"] == "full" and params["geometries"] == "geojson"
    assert params["exclude"] == "motorway,toll"
    assert len(ways) == 2


def test_mapbox_failures_never_show_the_token(http):  # noqa: F811
    calls, replies = http
    token = "pk.secret-token-123"
    replies.append(providers.requests.ConnectionError(f"failed: /x?access_token={token}"))
    with pytest.raises(providers.RoutingError) as e:
        providers.Mapbox(token).routes((51.0, 0.0), (52.0, 0.0))
    assert token not in str(e.value) and e.value.__cause__ is None
    assert "Could not reach api.mapbox.com" in str(e.value)
    replies.append(_Reply(401, "Not Authorized - Invalid Token"))
    with pytest.raises(providers.RoutingError, match="refused the API key"):
        providers.Mapbox("t").routes((51.0, 0.0), (52.0, 0.0))
    with pytest.raises(providers.RoutingError, match="mapbox_token"):
        providers.Mapbox("")


def test_ors_keeps_off_the_roads_asked_and_adds_heights(http):  # noqa: F811
    calls, replies = http
    ors_route = {"features": [{
        "geometry": {"coordinates": [[-1.26, 51.75, 80.0], [-0.1, 51.5, 20.0]]},
        "properties": {"summary": {"distance": 32000, "duration": 2400}}}]}
    replies += [_Reply(200, ors_route), _Reply(200, ors_route)]
    ors = providers.OpenRouteService("k")
    ors.route((51.75, -1.26), (51.5, -0.1), ["ferries", "motorways", "bogus"])
    assert calls[0][2]["json"]["options"] == {"avoid_features": ["highways", "ferries"]}
    ors.route((51.75, -1.26), (51.5, -0.1))
    assert "options" not in calls[1][2]["json"]

    heights = {"geometry": [[-1.26, 51.75, 80.0], [-1.26, 51.8354, 95.0]]}
    replies.append(_Reply(200, heights))
    pts = [(51.75, -1.26, None), (51.8, -1.26, None), (51.8354, -1.26, None)]
    assert ors.elevate(pts) == [(51.75, -1.26, 80.0), (51.8354, -1.26, 95.0)]
    body = calls[2][2]["json"]
    assert calls[2][1].endswith("/elevation/line")
    assert body["format_in"] == "polyline" and body["geometry"][0] == [-1.26, 51.75]
    replies.append(_Reply(500, "boom"))
    assert ors.elevate(pts) == pts                    # flat, not a failed plan


@pytest.mark.parametrize("raw, want", [
    ("tolls, motorways", ["motorways", "tolls"]),
    (["FERRIES", "lanes"], ["ferries"]),
    (None, []),
    ("", []),
])
def test_avoid_list(raw, want):
    assert providers.avoid_list(raw) == want


def test_token_from_config_or_environment(monkeypatch):
    monkeypatch.setenv("ODB_MAPBOX_TOKEN", "env-tok")
    assert providers.mapbox_token(Config({})) == "env-tok"
    cfg = Config({"routing": {"mapbox_token": "cfg-tok"}})
    assert providers.mapbox_token(cfg) == "cfg-tok"


# -- the Route page ---------------------------------------------------------------------
@pytest.fixture()
def traffic(monkeypatch, services):  # noqa: F811
    """Mapbox stubbed: two ways along the stubbed road, the second slower."""
    seen = {}
    road = [(lat, lon, None) for lat, lon, _ in _north(400)]

    def routes(self, start, end, avoid=()):
        seen["avoid"] = list(avoid)
        return [providers.TrafficRoute(road, 400.0, 15600, 15000, "M1",
                                       [(100.0, 104.0, "severe")]),
                providers.TrafficRoute(road, 410.0, 16200, 16000, "A1(M)", [])]

    def elevate(self, points):
        seen["elevated"] = len(points)
        return points
    monkeypatch.setattr(providers.Mapbox, "routes", routes)
    monkeypatch.setattr(providers.OpenRouteService, "elevate", elevate)
    return seen


def test_page_offers_the_ways_there_in_traffic(db, traffic):  # noqa: F811
    client = _client(db, {**KEYS, "mapbox_token": "mapbox-secret", "avoid": ["tolls"]})
    body = client.get("/route", params={"to": "Leeds"}).text
    assert "mapbox-secret" not in body
    assert "Ways there, in today" in body and "A1(M)" in body
    assert "+10 min" in body                            # 15600 s against 15000 s usually
    assert "plan this way" in body and "way=1" in body
    assert traffic == {"avoid": ["tolls"], "elevated": 401}
    data = json.loads(body.split('id="route-data">')[1].split("</script>")[0])
    jam = data["traffic"][0]
    assert jam["level"] == "severe" and len(jam["line"]) >= 2
    other = client.get("/route", params={"to": "Leeds", "way": "1"}).text
    assert '<tr class="chosen"><td>A1(M)</td>' in other


def test_avoid_boxes_reach_the_route_and_start_from_config(db, services, monkeypatch):  # noqa: F811
    seen = []

    def route(self, start, end, avoid=()):
        seen.append(list(avoid))
        return providers.Route(_north(400), 400.0, 15000)
    monkeypatch.setattr(providers.OpenRouteService, "route", route)
    client = _client(db, {**KEYS, "avoid": ["tolls"]})
    assert 'name="avoid_tolls" value="1" checked' in client.get("/route").text
    client.get("/route", params={"to": "Leeds"})                     # a link: config's
    body = client.get("/route", params={"to": "Leeds", "avoid_set": "1",
                                        "avoid_motorways": "1"}).text
    assert seen == [["tolls"], ["motorways"]]
    assert "Driving time without live traffic" in body


def test_mapbox_failure_falls_back_to_ors_and_says_so(db, services, monkeypatch):  # noqa: F811
    def routes(self, start, end, avoid=()):
        raise providers.RoutingError("api.mapbox.com refused the API key (HTTP 401).")
    monkeypatch.setattr(providers.Mapbox, "routes", routes)
    client = _client(db, {**KEYS, "mapbox_token": "t"})
    body = client.get("/route", params={"to": "Leeds"}).text
    assert "No live traffic: api.mapbox.com refused" in body
    assert "Charging stops" in body
