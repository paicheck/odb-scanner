"""Weather lookup for EV energy modelling (read-only, external).

Uses Open-Meteo (free, no API key) for current conditions. All calls are GETs
to a fixed host; failures return None so callers fall back to the default
consumption model. Nothing here talks to the vehicle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10.0


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
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation",
            "timezone": "auto"}, timeout=TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        cur = data.get("current") or {}
        return Weather(
            temp_c=_num(cur.get("temperature_2m")),
            wind_kmh=_num(cur.get("wind_speed_10m")),
            wind_dir_deg=_num(cur.get("wind_direction_10m")),
            precipitation_mm=_num(cur.get("precipitation")),
        )
    except Exception:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def headwind_component(wind_kmh, wind_dir_deg, travel_bearing_deg) -> float:
    """Approximate headwind (positive = headwind) in km/h."""
    if wind_kmh is None or wind_dir_deg is None or travel_bearing_deg is None:
        return 0.0
    import math
    # wind direction is where wind blows FROM; convert to vector.
    diff = math.radians(wind_dir_deg - travel_bearing_deg)
    return wind_kmh * math.cos(diff)
