"""Road routing and charger lookup: OpenRouteService and OpenChargeMap.

Both need a free API key, read from config (routing.ors_api_key,
routing.ocm_api_key -- put them in config.local.yaml, which is gitignored) or
the environment (ODB_ORS_KEY, ODB_OCM_KEY). Calls are made from this server,
not the browser: the start, destination and route are sent to these services
and nowhere else, and a key never reaches a web page.

Every failure -- missing key, network, HTTP error, unexpected reply -- is a
RoutingError with a message fit to show the user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from .planner import Charger

ORS_BASE = "https://api.openrouteservice.org"
OCM_BASE = "https://api.openchargemap.io/v3/poi/"
TIMEOUT_S = 20.0
CCS2 = 33            # OpenChargeMap ConnectionTypeID: CCS (Type 2)


class RoutingError(RuntimeError):
    """Planning could not be done; the message says why, in plain words."""


@dataclass
class Place:
    label: str
    lat: float
    lon: float


@dataclass
class Route:
    points: list[tuple[float, float, float | None]]   # (lat, lon, elevation_m)
    distance_km: float
    duration_s: float


def keys(cfg) -> tuple[str, str]:
    def one(name, env):
        return str(cfg.get(f"routing.{name}", "") or os.environ.get(env, "")).strip()
    return one("ors_api_key", "ODB_ORS_KEY"), one("ocm_api_key", "ODB_OCM_KEY")


def _get_json(method: str, url: str, **kw):
    try:
        r = requests.request(method, url, timeout=TIMEOUT_S, **kw)
    except requests.RequestException as exc:
        raise RoutingError(f"Could not reach {url.split('/')[2]}: {exc}") from exc
    if r.status_code in (401, 403):
        raise RoutingError(f"{url.split('/')[2]} refused the API key "
                           f"(HTTP {r.status_code}). Check it in config.local.yaml.")
    if r.status_code == 429:
        raise RoutingError(f"{url.split('/')[2]} rate limit reached; try again later.")
    if r.status_code >= 400:
        raise RoutingError(f"{url.split('/')[2]} returned HTTP {r.status_code}: "
                           f"{r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise RoutingError(f"{url.split('/')[2]} returned a non-JSON reply") from exc


class OpenRouteService:
    def __init__(self, api_key: str):
        if not api_key:
            raise RoutingError(
                "No OpenRouteService key. Get a free key at "
                "https://openrouteservice.org/dev/#/signup and set "
                "routing.ors_api_key in config.local.yaml.")
        self.key = api_key

    def geocode(self, text: str, near: tuple[float, float] | None = None,
                size: int = 5) -> list[Place]:
        params = {"api_key": self.key, "text": text, "size": size}
        if near:
            params["focus.point.lat"], params["focus.point.lon"] = near
        data = _get_json("GET", f"{ORS_BASE}/geocode/search", params=params)
        out = []
        for f in (data.get("features") or [])[:size]:
            try:
                lon, lat = f["geometry"]["coordinates"][:2]
                label = f["properties"].get("label", text)
                out.append(Place(label, float(lat), float(lon)))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def route(self, start: tuple[float, float], end: tuple[float, float]) -> Route:
        body = {"coordinates": [[start[1], start[0]], [end[1], end[0]]],
                "elevation": True, "instructions": False}
        data = _get_json("POST", f"{ORS_BASE}/v2/directions/driving-car/geojson",
                         json=body, headers={"Authorization": self.key})
        try:
            feat = data["features"][0]
            coords = feat["geometry"]["coordinates"]
            summary = feat["properties"]["summary"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RoutingError("OpenRouteService found no route") from exc
        points = [(float(c[1]), float(c[0]),
                   float(c[2]) if len(c) > 2 and c[2] is not None else None)
                  for c in coords]
        if len(points) < 2:
            raise RoutingError("OpenRouteService returned an empty route")
        return Route(points, float(summary.get("distance", 0)) / 1000.0,
                     float(summary.get("duration", 0)))


class OpenChargeMap:
    """Fast CCS chargers near a point; results cached per rounded location."""

    def __init__(self, api_key: str, min_kw: float = 50.0):
        if not api_key:
            raise RoutingError(
                "No OpenChargeMap key. Get a free key at "
                "https://openchargemap.org/site/profile/applications and set "
                "routing.ocm_api_key in config.local.yaml.")
        self.key = api_key
        self.min_kw = min_kw
        self._cache: dict[tuple, list[Charger]] = {}

    def near(self, lat: float, lon: float, radius_km: float) -> list[Charger]:
        cache_key = (round(lat, 2), round(lon, 2), radius_km)
        if cache_key in self._cache:
            return self._cache[cache_key]
        params = {"output": "json", "latitude": lat, "longitude": lon,
                  "distance": radius_km, "distanceunit": "KM", "maxresults": 25,
                  "connectiontypeid": CCS2, "minpowerkw": self.min_kw,
                  # Not compact: compact drops OperatorInfo, which the stop
                  # list shows (who runs the charger matters for paying).
                  "verbose": "false", "key": self.key}
        data = _get_json("GET", OCM_BASE, params=params,
                         headers={"X-API-Key": self.key})
        out = [c for c in map(_charger, data if isinstance(data, list) else []) if c]
        self._cache[cache_key] = out
        return out


def _charger(poi: dict) -> Charger | None:
    try:
        addr = poi.get("AddressInfo") or {}
        powers = [float(c.get("PowerKW") or 0) for c in poi.get("Connections") or []
                  if c.get("ConnectionTypeID") == CCS2]
        if not powers:
            return None
        op = (poi.get("OperatorInfo") or {}).get("Title") or ""
        street = ", ".join(x for x in (addr.get("AddressLine1"), addr.get("Town")) if x)
        return Charger(name=addr.get("Title") or "Charger",
                       lat=float(addr["Latitude"]), lon=float(addr["Longitude"]),
                       power_kw=max(powers), operator=op,
                       address=street)
    except (KeyError, TypeError, ValueError):
        return None
