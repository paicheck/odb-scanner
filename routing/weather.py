"""The weather along a planned route, from Open-Meteo (free, no key; read-only).

along_route() samples the route every SAMPLE_KM, asks for the hourly forecast
at those places in one GET, and gives each sample the hour nearest to when
the car is expected there (leaving now, at the routing provider's driving
time; charging stops are not added). The planner (routing/planner.py
Conditions) turns the wind into energy and gives the temperature to the
car's own consumption model. Network and parsing live here, the arithmetic
there, so the planner stays pure.

Privacy: positions are rounded to GRID_DEG (about 11 km) before they are sent
-- enough for a forecast, and the start is then a district, not a door.
Forecasts are kept CACHE_S per rounded position.

Nothing here talks to the vehicle. Any failure gives no forecast and says why;
the plan then counts no wind and the car's usual conditions.
"""
from __future__ import annotations

import time
from bisect import bisect_left
from dataclasses import dataclass
from typing import Any

import requests

from routing.planner import Conditions, cumulative_km

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10.0
GRID_DEG = 0.1
SAMPLE_KM = 25.0
MAX_SAMPLES = 24
CACHE_S = 1800.0
MAX_HOUR_GAP_S = 5400.0     # a forecast hour further than this from the ETA is none
HOURLY = "temperature_2m,wind_speed_10m,wind_direction_10m"

_cache: dict[tuple[float, float], tuple[float, dict]] = {}


@dataclass
class Weather:
    temp_c: float | None = None
    wind_kmh: float | None = None
    wind_dir_deg: float | None = None
    precipitation_mm: float | None = None
    source: str = "open-meteo"


def fetch(lat: float, lon: float, session: Any = None) -> Weather | None:
    """Current conditions for a point. Returns None on any failure."""
    sess = session or requests
    try:
        r = sess.get(OPEN_METEO_URL, params={
            "latitude": f"{_grid(lat):.1f}", "longitude": f"{_grid(lon):.1f}",
            "current": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation",
            "timezone": "GMT"}, timeout=TIMEOUT_S)
        r.raise_for_status()
        cur = r.json().get("current") or {}
        return Weather(
            temp_c=_num(cur.get("temperature_2m")),
            wind_kmh=_num(cur.get("wind_speed_10m")),
            wind_dir_deg=_num(cur.get("wind_direction_10m")),
            precipitation_mm=_num(cur.get("precipitation")),
        )
    except (requests.RequestException, ValueError, AttributeError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _grid(x: float) -> float:
    return round(round(x / GRID_DEG) * GRID_DEG, 1)


def sample_points(points: list[tuple], drive_s: float, depart_s: float
                  ) -> list[tuple[float, float, float, float]]:
    """(km, lat, lon, expected time) every SAMPLE_KM along the route -- more
    widely spaced on a long one, at most MAX_SAMPLES."""
    cum = cumulative_km(points)
    total = cum[-1]
    step = max(SAMPLE_KM, total / MAX_SAMPLES)
    out, km = [], 0.0
    while km < total - 1e-3 or not out:
        i = min(len(points) - 1, bisect_left(cum, km))
        eta = depart_s + (drive_s * km / total if total > 0 else 0.0)
        out.append((km, points[i][0], points[i][1], eta))
        km += step
    return out


def _hourly(cells: list[tuple[float, float]], session) -> dict:
    """{cell: {"t": [...], "temp": [...], "wind": [...], "dir": [...]}} in one GET."""
    r = session.get(OPEN_METEO_URL, params={
        "latitude": ",".join(f"{c[0]:.1f}" for c in cells),
        "longitude": ",".join(f"{c[1]:.1f}" for c in cells),
        "hourly": HOURLY, "timeformat": "unixtime", "timezone": "GMT",
        "forecast_days": 3}, timeout=TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    data = data if isinstance(data, list) else [data]
    if len(data) != len(cells):
        raise ValueError(f"{len(data)} forecasts for {len(cells)} places")
    out = {}
    for cell, d in zip(cells, data):
        h = d.get("hourly") or {}
        out[cell] = {"t": [float(t) for t in h.get("time") or []],
                     "temp": h.get("temperature_2m") or [],
                     "wind": h.get("wind_speed_10m") or [],
                     "dir": h.get("wind_direction_10m") or []}
    return out


def _at(series: dict, when: float) -> tuple[float | None, float | None, float | None]:
    t = series["t"]
    if not t:
        return None, None, None
    i = bisect_left(t, when)
    j = min((k for k in (i - 1, i) if 0 <= k < len(t)), key=lambda k: abs(t[k] - when))
    if abs(t[j] - when) > MAX_HOUR_GAP_S:
        return None, None, None

    def pick(name):
        values = series[name]
        return _num(values[j]) if j < len(values) else None
    return pick("temp"), pick("wind"), pick("dir")


def along_route(points: list[tuple], drive_s: float, depart_s: float | None = None,
                session: Any = None, now: float | None = None
                ) -> tuple[list[Conditions] | None, str]:
    """(the forecast along the route for the planner, a sentence for the page).
    No forecast (None) when Open-Meteo cannot be reached or says nothing
    about the time of the trip."""
    now = time.time() if now is None else now
    depart_s = now if depart_s is None else depart_s
    samples = sample_points(points, drive_s, depart_s)
    cells = [(_grid(lat), _grid(lon)) for _, lat, lon, _ in samples]
    want = [c for c in dict.fromkeys(cells)
            if c not in _cache or now - _cache[c][0] > CACHE_S]
    if want:
        try:
            got = _hourly(want, session or requests)
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            return None, (f"No weather forecast ({type(exc).__name__}: Open-Meteo did "
                          "not answer usefully); the wind is not counted.")
        if len(_cache) > 2000:
            _cache.clear()
        for c, series in got.items():
            _cache[c] = (now, series)
    out = []
    for (km, _, _, eta), cell in zip(samples, cells):
        temp, wind, wdir = _at(_cache[cell][1], eta)
        if temp is not None or wind is not None:
            out.append(Conditions(km=km, temp_c=temp, wind_kmh=wind, wind_from_deg=wdir))
    if not out:
        return None, ("No weather forecast for the time of this trip; the wind is "
                      "not counted.")
    return out, (f"Weather: Open-Meteo's forecast at {len(out)} points along the way "
                 f"for when you pass them (positions sent rounded to {GRID_DEG}°, "
                 "about 11 km).")
