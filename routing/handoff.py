"""Handing a planned trip to Google Maps -- as far as Google supports it.

What Google documents (review_output/navigation_review.md §2, read
2026-10-31):

* Maps URLs (https://developers.google.com/maps/documentation/urls/get-started):
  ``/maps/dir/?api=1`` with ``origin``, ``destination``, ``waypoints``
  (pipe-separated, ``%7C``), ``travelmode``, ``avoid=tolls,highways,ferries``
  and ``dir_action=navigate``; up to 9 waypoints (3 in a mobile browser);
  2,048 characters at most; commas encoded ``%2C``. ``dir_action=navigate``
  starts turn-by-turn only when the origin is the user's current position
  (or near it) -- otherwise Google Maps shows a route preview -- so a trip
  handed over while driving uses the car's position as the origin.
* The Android navigation intent ``google.navigation:q=lat,lng`` starts
  turn-by-turn from the current position to ONE place (no waypoints), with
  ``avoid=`` t/h/f.
* Nothing lets another app change a navigation Google Maps is already
  running: a changed trip can only be handed over again, which the driver
  accepts (route/MapsHandoff.kt on the phone).

More charging stops than Google Maps takes is never truncated silently: the
link then goes as far as the first stop it cannot fit -- as the destination
-- and says so, and the rest is handed on from there.

The phone builds the same links (route/GoogleMaps.kt); both are held to
tests/data/handoff_vectors.json. Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

BASE = "https://www.google.com/maps/dir/?api=1"
MAX_WAYPOINTS = 9            # Google Maps apps and desktop
MAX_WAYPOINTS_BROWSER = 3    # a mobile browser
MAX_URL = 2048
AVOID_URL = (("tolls", "tolls"), ("motorways", "highways"), ("ferries", "ferries"))
AVOID_INTENT = (("tolls", "t"), ("motorways", "h"), ("ferries", "f"))


@dataclass(frozen=True)
class Handoff:
    url: str
    stops_sent: int              # charging stops Google Maps was given
    partial: bool                # it goes only as far as a charging stop
    note: str = ""


def _ll(p) -> str:
    return f"{p[0]:.6f}%2C{p[1]:.6f}"


def _avoid(avoid, table) -> list[str]:
    avoid = avoid or {}
    return [code for name, code in table if avoid.get(name)]


def trip_link(origin, stops, destination, avoid=None, max_waypoints: int = MAX_WAYPOINTS,
              navigate: bool = True) -> Handoff:
    """A Maps URL for the trip: (lat, lon) `origin` (None: Google Maps uses
    the phone's position), charging `stops` in order, `destination`, roads to
    `avoid` ({"tolls": bool, "motorways": bool, "ferries": bool})."""
    stops = list(stops)
    partial = len(stops) > max_waypoints
    if partial:
        waypoints, end = stops[:max_waypoints], stops[max_waypoints]
    else:
        waypoints, end = stops, destination
    while True:
        parts = [BASE, "travelmode=driving"]
        if origin is not None:
            parts.append("origin=" + _ll(origin))
        parts.append("destination=" + _ll(end))
        if waypoints:
            parts.append("waypoints=" + "%7C".join(_ll(w) for w in waypoints))
        codes = _avoid(avoid, AVOID_URL)
        if codes:
            parts.append("avoid=" + "%2C".join(codes))
        if navigate:
            parts.append("dir_action=navigate")
        url = "&".join(parts)
        if len(url) <= MAX_URL or not waypoints:
            break
        # Never in practice (nine stops is ~450 characters), but never over.
        partial = True
        end, waypoints = waypoints[-1], waypoints[:-1]
    sent = len(waypoints) + (1 if partial else 0)
    note = ""
    if partial:
        note = (f"Google Maps takes {max_waypoints} stops from a link: this goes as far "
                f"as charging stop {sent}; the rest is handed over from there.")
    return Handoff(url, sent, partial, note)


def next_stop_intent(target, avoid=None) -> str:
    """The Android intent that navigates from where the phone is to one place."""
    uri = f"google.navigation:q={target[0]:.6f},{target[1]:.6f}&mode=d"
    codes = _avoid(avoid, AVOID_INTENT)
    if codes:
        uri += "&avoid=" + "".join(codes)
    return uri
