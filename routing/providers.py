"""Road routing and charger lookup: OpenRouteService, OpenChargeMap, Mapbox.

ORS and OCM need a free API key, read from config (routing.ors_api_key,
routing.ocm_api_key -- put them in config.local.yaml, which is gitignored) or
the environment (ODB_ORS_KEY, ODB_OCM_KEY). Mapbox is optional
(routing.mapbox_token or ODB_MAPBOX_TOKEN): with it, routes come in today's
traffic with up to two alternatives. Calls are made from this server, not
the browser: the start, destination and route are sent to these services
and nowhere else, and a key never reaches a web page.

Every failure -- missing key, network, HTTP error, unexpected reply -- is a
RoutingError with a message fit to show the user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from .planner import Charger, cumulative_km

ORS_BASE = "https://api.openrouteservice.org"
MAPBOX_BASE = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"
ELEVATION_MAX_POINTS = 1500   # ORS elevation/line takes up to 2000
# Roads to keep off, as each service names them.
AVOID = ("motorways", "tolls", "ferries")
AVOID_ORS = {"motorways": "highways", "tolls": "tollways", "ferries": "ferries"}
AVOID_MAPBOX = {"motorways": "motorway", "tolls": "toll", "ferries": "ferry"}
OCM_BASE = "https://api.openchargemap.io/v3/poi/"
TIMEOUT_S = 20.0
CCS2 = 33            # OpenChargeMap ConnectionTypeID: CCS (Type 2)
OCM_MAX_RESULTS = 500     # chargers along one route
OCM_COUNTRY_MAX = 20000   # every fast CCS location in one country (networks.py)
OCM_REFERENCE = "https://api.openchargemap.io/v3/referencedata/"
POLYLINE_MAX_POINTS = 300  # route points sent to OCM (URL length)
# OCM UsageTypeID: 2 private (restricted), 3 privately owned (notice
# required), 6 private (staff, visitors or customers) -- not for a passing car.
PRIVATE_USAGE = {2, 3, 6}


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


@dataclass
class TrafficRoute:
    """One way there from Mapbox, in today's traffic."""
    points: list[tuple[float, float, float | None]]   # no heights: ORS adds them
    distance_km: float
    duration_s: float                  # with today's traffic
    typical_s: float | None            # the same trip in typical traffic
    summary: str                       # main roads, "A40, M40"
    runs: list[tuple[float, float, str]]   # heavy/severe: (from km, to km, level)

    @property
    def delay_s(self) -> float | None:
        if self.typical_s is None:
            return None
        return max(0.0, self.duration_s - self.typical_s)

    @property
    def heavy_km(self) -> float:
        return sum(b - a for a, b, _ in self.runs)


def keys(cfg) -> tuple[str, str]:
    def one(name, env):
        return str(cfg.get(f"routing.{name}", "") or os.environ.get(env, "")).strip()
    return one("ors_api_key", "ODB_ORS_KEY"), one("ocm_api_key", "ODB_OCM_KEY")


def mapbox_token(cfg) -> str:
    raw = cfg.get("routing.mapbox_token", "") or os.environ.get("ODB_MAPBOX_TOKEN", "")
    return str(raw).strip()


def avoid_list(raw) -> list[str]:
    """Config's or the form's roads to avoid, cleaned: a subset of AVOID, in order."""
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    wanted = {str(x).strip().lower() for x in (raw or [])}
    return [a for a in AVOID if a in wanted]


def country_list(raw) -> str:
    """'gb, fr' -> 'GB,FR': the ISO codes place search is limited to ('' = anywhere)."""
    codes = str(raw or "").replace(",", " ").upper().split()
    return ",".join(c for c in codes if c.isalpha() and len(c) in (2, 3))


def countries(cfg) -> str:
    return country_list(cfg.get("routing.countries", ""))


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
    def __init__(self, api_key: str, countries: str = ""):
        if not api_key:
            raise RoutingError(
                "No OpenRouteService key. Get a free key at "
                "https://openrouteservice.org/dev/#/signup and set "
                "routing.ors_api_key in config.local.yaml.")
        self.key = api_key
        self.countries = country_list(countries)

    def geocode(self, text: str, near: tuple[float, float] | None = None,
                size: int = 5) -> list[Place]:
        params = {"api_key": self.key, "text": text, "size": size}
        if near:
            params["focus.point.lat"], params["focus.point.lon"] = near
        # A focus point barely moves the ranking: "Brighton" is Brighton, NY
        # even when searched from England. Only a country limit fixes that.
        if self.countries:
            params["boundary.country"] = self.countries
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

    def route(self, start: tuple[float, float], end: tuple[float, float],
              avoid=()) -> Route:
        body = {"coordinates": [[start[1], start[0]], [end[1], end[0]]],
                "elevation": True, "instructions": False}
        if avoid_list(avoid):
            body["options"] = {"avoid_features": [AVOID_ORS[a]
                                                  for a in avoid_list(avoid)]}
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

    def elevate(self, points: list[tuple]) -> list[tuple]:
        """Heights for a route from another service (Mapbox gives none), on a
        thinned copy of it -- so the planner still counts the hills. On any
        failure the points come back as they were: flat."""
        thin = thin_line(points, ELEVATION_MAX_POINTS)
        body = {"format_in": "polyline", "format_out": "polyline",
                "geometry": [[p[1], p[0]] for p in thin]}
        try:
            data = _get_json("POST", f"{ORS_BASE}/elevation/line", json=body,
                             headers={"Authorization": self.key})
            out = [(float(c[1]), float(c[0]), float(c[2]) if len(c) > 2 else None)
                   for c in data["geometry"]]
        except (RoutingError, KeyError, TypeError, ValueError, IndexError):
            return points
        return out if len(out) >= 2 else points


class Mapbox:
    """Driving routes in today's traffic (Mapbox Directions, driving-traffic):
    the recommended way and up to two alternatives, each with the time it
    would take in typical traffic and its heavy-traffic stretches."""

    def __init__(self, token: str):
        if not token:
            raise RoutingError(
                "No Mapbox token: set routing.mapbox_token in config.local.yaml.")
        self.token = token

    def routes(self, start: tuple[float, float], end: tuple[float, float],
               avoid=()) -> list[TrafficRoute]:
        coords = f"{start[1]:.6f},{start[0]:.6f};{end[1]:.6f},{end[0]:.6f}"
        params = {"alternatives": "true", "geometries": "geojson", "overview": "full",
                  "annotations": "congestion", "access_token": self.token}
        if avoid_list(avoid):
            params["exclude"] = ",".join(AVOID_MAPBOX[a] for a in avoid_list(avoid))
        try:
            data = _get_json("GET", f"{MAPBOX_BASE}/{coords}", params=params)
        except RoutingError as exc:
            # A network error quotes the request, token and all: keep the
            # reason, drop the token (and the chained original).
            msg = str(exc)
            if len(self.token) >= 8:
                msg = msg.replace(self.token, "…")
            raise RoutingError(msg) from None
        return parse_mapbox(data)


def parse_mapbox(data: dict) -> list[TrafficRoute]:
    """Mapbox Directions v5 (geojson, overview=full, annotations=congestion)."""
    if not isinstance(data, dict) or data.get("code", "Ok") != "Ok":
        msg = (data.get("message") or data.get("code")) if isinstance(data, dict) \
            else "bad reply"
        raise RoutingError(f"Mapbox found no route: {msg}")
    out = []
    for r in data.get("routes") or []:
        try:
            points = [(float(c[1]), float(c[0]), None)
                      for c in r["geometry"]["coordinates"]]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if len(points) < 2:
            continue
        legs = r.get("legs") or []
        levels = [str(x) for leg in legs
                  for x in ((leg.get("annotation") or {}).get("congestion") or [])]
        typical = r.get("duration_typical")
        out.append(TrafficRoute(
            points=points,
            distance_km=float(r.get("distance", 0)) / 1000.0,
            duration_s=float(r.get("duration", 0)),
            typical_s=None if typical is None else float(typical),
            summary="; ".join(leg["summary"] for leg in legs if leg.get("summary")),
            runs=congestion_runs(levels, cumulative_km(points))))
    if not out:
        raise RoutingError("Mapbox found no route")
    return out


def congestion_runs(levels: list[str],
                    cum: list[float]) -> list[tuple[float, float, str]]:
    """Heavy or severe stretches from per-segment congestion: (from km, to km,
    worst level). Matches the phone's MapboxParse.runs, on the same sample."""
    if not levels or len(cum) < 2:
        return []
    segs = len(cum) - 1

    def km(i: int) -> float:          # segment i's start, proportionally if counts differ
        pos = i * segs / len(levels)
        k = min(segs - 1, int(pos))
        return cum[k] + (pos - k) * (cum[k + 1] - cum[k])

    out, start, worst = [], -1, ""
    for i in range(len(levels) + 1):
        heavy = i < len(levels) and levels[i] in ("heavy", "severe")
        if heavy:
            if start < 0:
                start, worst = i, levels[i]
            if levels[i] == "severe":
                worst = "severe"
        elif start >= 0:
            out.append((km(start), cum[-1] if i >= len(levels) else km(i), worst))
            start = -1
    return out


class OpenChargeMap:
    """Fast CCS chargers along a route, in one request (OCM's polyline search)."""

    def __init__(self, api_key: str, min_kw: float = 50.0):
        if not api_key:
            raise RoutingError(
                "No OpenChargeMap key. Get a free key at "
                "https://openchargemap.org/site/profile/applications and set "
                "routing.ocm_api_key in config.local.yaml.")
        self.key = api_key
        self.min_kw = min_kw

    def along(self, points: list[tuple], radius_km: float) -> list[Charger]:
        """Every fast CCS charger within radius_km of the route, with its
        listed price text (Charger.usage_cost) for routing.prices to read."""
        params = {"output": "json", "polyline": encode_polyline(thin_line(points)),
                  "distance": radius_km, "distanceunit": "KM",
                  "maxresults": OCM_MAX_RESULTS, "connectiontypeid": CCS2,
                  "minpowerkw": self.min_kw,
                  # Not compact: compact drops OperatorInfo, which prices are
                  # matched on and the stop list shows.
                  "verbose": "false", "key": self.key}
        data = _get_json("GET", OCM_BASE, params=params,
                         headers={"X-API-Key": self.key})
        return [c for c in map(_charger, data if isinstance(data, list) else []) if c]

    def locations_in(self, country: str) -> list[dict]:
        """Every fast CCS location Open Charge Map lists in one country, compact
        (reference data as ids only): for counting networks (routing/networks.py)."""
        params = {"output": "json", "countrycode": country, "maxresults": OCM_COUNTRY_MAX,
                  "compact": "true", "verbose": "false", "connectiontypeid": CCS2,
                  "minpowerkw": self.min_kw, "key": self.key}
        data = _get_json("GET", OCM_BASE, params=params, headers={"X-API-Key": self.key})
        return data if isinstance(data, list) else []

    def reference_data(self) -> dict:
        """Open Charge Map's reference data: the operators' names by id among it."""
        data = _get_json("GET", OCM_REFERENCE, params={"output": "json", "key": self.key},
                         headers={"X-API-Key": self.key})
        return data if isinstance(data, dict) else {}

    def near(self, lat: float, lon: float, radius_km: float) -> list[Charger]:
        """Every fast CCS charger within radius_km of one place (the start,
        the destination, a town the driver names)."""
        params = {"output": "json", "latitude": round(lat, 5), "longitude": round(lon, 5),
                  "distance": radius_km, "distanceunit": "KM",
                  "maxresults": OCM_MAX_RESULTS, "connectiontypeid": CCS2,
                  "minpowerkw": self.min_kw, "verbose": "false", "key": self.key}
        data = _get_json("GET", OCM_BASE, params=params,
                         headers={"X-API-Key": self.key})
        return [c for c in map(_charger, data if isinstance(data, list) else []) if c]


def thin_line(points: list[tuple], limit: int = POLYLINE_MAX_POINTS) -> list[tuple]:
    """Every n-th point and the last, so the polyline fits in a URL."""
    step = max(1, -(-len(points) // limit))
    out = [p for p in points[::step]]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def encode_polyline(points: list[tuple]) -> str:
    """Google's encoded-polyline format, 5 decimals: what OCM's polyline takes."""
    out, plat, plon = [], 0, 0
    for p in points:
        lat, lon = round(p[0] * 1e5), round(p[1] * 1e5)
        for v in (lat - plat, lon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = lat, lon
    return "".join(out)


def _charger(poi: dict) -> Charger | None:
    """A Charger this car can use, or None: no CCS, Tesla-only, private, or
    marked not operational (planned, removed, broken)."""
    try:
        op = (poi.get("OperatorInfo") or {}).get("Title") or ""
        if "tesla-only" in op.lower() or poi.get("UsageTypeID") in PRIVATE_USAGE:
            return None
        if (poi.get("StatusType") or {}).get("IsOperational") is False:
            return None
        addr = poi.get("AddressInfo") or {}
        powers = [float(c.get("PowerKW") or 0) for c in poi.get("Connections") or []
                  if c.get("ConnectionTypeID") == CCS2]
        if not powers:
            return None
        street = ", ".join(x for x in (addr.get("AddressLine1"), addr.get("Town")) if x)
        usage = poi.get("UsageTypeID")
        points = poi.get("NumberOfPoints")
        return Charger(name=addr.get("Title") or "Charger",
                       lat=float(addr["Latitude"]), lon=float(addr["Longitude"]),
                       power_kw=max(powers), operator=op,
                       address=street, usage_cost=str(poi.get("UsageCost") or ""),
                       id=str(poi.get("ID") or ""),
                       usage_type=int(usage) if usage is not None else None,
                       checked=str(poi.get("DateLastVerified")
                                   or poi.get("DateLastStatusUpdate") or ""),
                       points=int(points) if isinstance(points, (int, float)) else None)
    except (KeyError, TypeError, ValueError):
        return None
