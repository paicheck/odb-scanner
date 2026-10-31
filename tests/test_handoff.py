"""Handing a trip to Google Maps: the link Google documents, never a silent
truncation, the same on the phone (tests/data/handoff_vectors.json)."""
import os
import sys
from urllib.parse import parse_qs, urlsplit

from routing.handoff import MAX_URL, next_stop_intent, trip_link

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A, B = (51.752, -1.2577), (53.8008, -1.5491)
STOPS = [(52.0 + i * 0.1, -1.3) for i in range(12)]


def _q(url):
    return parse_qs(urlsplit(url).query)


def test_the_link_is_the_one_google_documents():
    h = trip_link(A, STOPS[:2], B, {"motorways": True, "tolls": True})
    q = _q(h.url)
    assert h.url.startswith("https://www.google.com/maps/dir/?api=1&")
    assert q["travelmode"] == ["driving"] and q["dir_action"] == ["navigate"]
    assert q["origin"] == ["51.752000,-1.257700"]
    assert q["destination"] == ["53.800800,-1.549100"]
    assert q["waypoints"] == ["52.000000,-1.300000|52.100000,-1.300000"]
    assert q["avoid"] == ["tolls,highways"]
    # Encoded as the page asks: pipes %7C, commas %2C, nothing raw.
    assert "%7C" in h.url and "|" not in h.url
    assert "," not in h.url.split("?", 1)[1]
    assert (h.stops_sent, h.partial) == (2, False)


def test_more_stops_than_google_maps_takes_is_never_truncated_silently():
    h = trip_link(A, STOPS[:11], B)
    q = _q(h.url)
    assert len(q["waypoints"][0].split("|")) == 9
    assert q["destination"] == ["52.900000,-1.300000"]      # the tenth stop, not Leeds
    assert h.partial and h.stops_sent == 10
    assert "charging stop 10" in h.note


def test_a_mobile_browser_gets_three():
    h = trip_link(A, STOPS[:5], B, max_waypoints=3)
    assert len(_q(h.url)["waypoints"][0].split("|")) == 3 and h.stops_sent == 4


def test_no_origin_lets_google_maps_start_from_the_phone():
    h = trip_link(None, [], B)
    assert "origin" not in _q(h.url) and h.stops_sent == 0


def test_no_link_is_ever_too_long():
    long_stops = [(52.123456 + i * 1e-3, -1.654321) for i in range(9)]
    assert len(trip_link(A, long_stops, B, {"motorways": True, "tolls": True,
                                            "ferries": True}).url) <= MAX_URL


def test_the_navigation_intent_goes_to_one_place():
    assert next_stop_intent(B) == "google.navigation:q=53.800800,-1.549100&mode=d"
    assert next_stop_intent(B, {"tolls": True, "ferries": True}).endswith("&avoid=tf")


def test_the_shared_vectors_are_current():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from export_handoff_vectors import build, render
    path = os.path.join(ROOT, "tests", "data", "handoff_vectors.json")
    with open(path, encoding="utf-8") as f:
        assert f.read() == render(build()), (
            "handoff_vectors.json is stale: python tools/export_handoff_vectors.py")
