"""EV trip planner: energy along a route, where to charge, and what it costs.

Pure functions, no I/O: the route geometry and the chargers near it are passed
in. The Android app ports this file line for line (Planner.kt) and both are
held to tests/data/route_vectors.json, so the phone and the dashboard give the
same answer for the same trip.

THE MODEL, AND WHAT IT LEAVES OUT
---------------------------------
Energy for a stretch of road = distance x flat consumption + climbing energy
(m g dh, through a drivetrain efficiency) - recovered descent energy (a
fraction of m g |dh|). Consumption is either measured from this car or a
stated default; speed, temperature, wind and HVAC are not modelled
separately -- they live inside that one consumption figure. The detour from
the route to a charger costs time but its energy is ignored. Every result
carries these assumptions, so it is read as an estimate, which is what it is.

Charging time integrates a charging-power curve (kW available at each SoC,
capped by the charger) in 1 % steps.

CHOOSING THE STOPS
------------------
Every fast charger within search_radius_km of the route is a candidate. A plan
is a sequence of stops, each with a charge level to leave at; it is feasible
when the charge never falls below the reserve. Of the feasible plans, the one
with the lowest total cost is chosen:

    money   price x energy billed (+ any session fee)
  + time    value of time x (charging + a fixed stop overhead + the detour)

The mode sets the value of time: Fastest makes time so expensive that money
only breaks ties; Balanced uses the driver's own value (default 15/hour);
Cheapest counts time at a token 3/hour, so it will not add a stop or an hour
of charging to save pennies. The search is exact over its grid: dynamic
programming over (charger, leave-at level), levels in 5 % steps up to the
charge-to limit; a stop adds at least 10 % (on a 5 % grid the search would
otherwise find one-minute top-ups that save pennies). Feasibility between
stops uses the route's net energy without the full-battery cap on
regeneration; the profile shown is then simulated with the cap, so a long
descent from 100 % reads correctly.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

from analysis.consumption_model import FEATURE_LABELS, ConsumptionModel
from analysis.energy_model import PhysicsModel

G = 9.81
EARTH_RADIUS_KM = 6371.0088
AIR_DENSITY = 1.225                   # kg/m³ at 15 °C, sea level
# A forecast's wind is at 10 m; at a car's height, in open country, it is about
# this share of that (the wind profile's power law, exponent ~0.15).
WIND_AT_ROAD = 0.7

# ID.3 Pro 58 kWh DC charging power by state of charge: (SoC %, max kW).
# Approximate published curve for the 120 kW-capable 58 kWh pack; flat to
# about 30 %, then tapering. Overridable (routing.charge_curve).
ID3_58_CURVE: tuple[tuple[float, float], ...] = (
    (0, 110), (30, 110), (40, 95), (50, 80), (60, 66), (70, 55),
    (80, 43), (90, 25), (100, 7),
)

MODES = ("fastest", "balanced", "cheapest")
FASTEST_TIME_VALUE = 1000.0       # per hour: time dominates, money breaks ties
CHEAPEST_TIME_VALUE = 3.0         # per hour: enough to refuse silly detours
DETOUR_ROAD_FACTOR = 1.4          # road km per straight-line km off the route
DETOUR_SPEED_KMH = 40.0
NEAREST_STRIDE = 10               # coarse step when matching a charger to the route
# Open Charge Map UsageTypeID: public, but only for members or after notice --
# not for a passing car without an account. Left out under public_only. (The
# private and staff-only types are dropped when chargers are fetched.)
MEMBERS_ONLY_USAGE = (4, 7)
USAGE_LABELS = {1: "public", 4: "public, membership required",
                5: "public, pay at location", 7: "public, notice required"}
# Search-only cost of NOT using a charger the driver chose: large enough that
# the plan uses it whenever it can be reached. Never part of a shown cost.
PIN_BONUS = 1e6
# A charger the driver chose counts up to this far from the route (an "add
# this stop" near a town off the way); others only within search_radius_km.
# The detour is still costed as time, both ways.
PIN_MAX_OFFSET_KM = 30.0
ALT_WINDOW_KM = 30.0              # alternatives: this far either side of a stop
ALT_MAX = 4


@dataclass
class CarModel:
    usable_kwh: float                 # energy the pack holds at 100 %
    consumption_kwh_per_km: float     # flat-road consumption
    mass_kg: float = 1950.0           # car + occupants + load
    drivetrain_eff: float = 0.90      # battery -> wheels when climbing
    drag_area_m2: float = 0.63        # Cd x frontal area: ID.3 0.267 x 2.36 m²
    regen_eff: float = 0.60           # share of descent energy recovered
    charge_curve: tuple[tuple[float, float], ...] = ID3_58_CURVE
    # Where charge_curve came from, for the assumptions (blank: the published one).
    charge_curve_basis: str = ""
    # Optional: this car's own model (analysis/consumption_model.py or the
    # physics one, analysis/energy_model.py), or its as_dict(); anything with
    # that predict() will do.
    consumption_model: object = None

    def __post_init__(self):
        if isinstance(self.consumption_model, dict):
            d = self.consumption_model
            physics = d.get("kind") == "physics"
            self.consumption_model = (PhysicsModel.from_dict(d) if physics
                                      else ConsumptionModel.from_dict(d))


@dataclass
class PlanSettings:
    reserve_pct: float = 10.0         # never plan to arrive below this
    charge_to_pct: float = 80.0       # never charge above this
    min_charger_kw: float = 50.0      # ignore chargers slower than this
    search_radius_km: float = 5.0     # charger distance from the route
    mode: str = "balanced"            # "fastest" | "balanced" | "cheapest"
    time_value_per_hour: float = 15.0  # Balanced: what an hour is worth
    default_price_per_kwh: float = 0.79  # for chargers with no known price
    stop_overhead_min: float = 10.0   # pulling in, plugging in, paying
    level_step_pct: float = 5.0       # leave-at levels the search considers
    min_charge_pct: float = 10.0      # a stop adds at least this much
    charging_eff: float = 0.92        # battery kWh per kWh billed
    # Charger choice. Networks match the operator's name, ignoring case, by
    # any part ("ionity" matches "IONITY GmbH"). Chargers are Open Charge Map
    # IDs (Charger.id).
    avoid_networks: tuple[str, ...] = ()   # never used
    prefer_networks: tuple[str, ...] = ()  # cheaper by prefer_discount, when choosing
    prefer_discount: float = 0.25
    public_only: bool = False              # also leave out MEMBERS_ONLY_USAGE
    avoid_chargers: tuple[str, ...] = ()   # never used
    use_chargers: tuple[str, ...] = ()     # used whenever reachable

    def time_value(self) -> float:
        if self.mode == "fastest":
            return FASTEST_TIME_VALUE
        if self.mode == "cheapest":
            return CHEAPEST_TIME_VALUE
        return self.time_value_per_hour


@dataclass
class Charger:
    name: str
    lat: float
    lon: float
    power_kw: float
    operator: str = ""
    address: str = ""
    price_per_kwh: float | None = None    # None: settings.default_price_per_kwh
    fee: float = 0.0                      # per session
    price_source: str = ""
    usage_cost: str = ""                  # Open Charge Map's price text, as listed
    id: str = ""                          # Open Charge Map ID, for avoid / use
    usage_type: int | None = None         # Open Charge Map UsageTypeID
    # How far to trust the listing (shown by routing/charger_search.py; the
    # plan does not use them): when it was last checked, and its points.
    checked: str = ""                     # ISO date, Open Charge Map's last check
    points: int | None = None             # NumberOfPoints


@dataclass
class Plan:
    status: str                       # "direct" | "with_stops" | "unreachable"
    distance_km: float
    drive_s: float
    start_pct: float
    arrival_pct: float | None
    stops: list[dict] = field(default_factory=list)
    profile: list[list[float]] = field(default_factory=list)   # [km, pct]
    unreachable_at_km: float | None = None
    charge_s: float = 0.0
    cost: float = 0.0                 # money for all stops
    mode: str = "balanced"
    assumptions: list[str] = field(default_factory=list)
    weather: dict | None = None       # wind kWh and temperatures, when forecast
    arrival_band: dict | None = None  # low/high arrival % and what they rest on
    last_leg: dict | None = None      # km, kWh from the last stop (or start) to the end

    def as_dict(self) -> dict:
        return {
            "status": self.status, "distance_km": round(self.distance_km, 1),
            "drive_s": round(self.drive_s), "charge_s": round(self.charge_s),
            "start_pct": round(self.start_pct, 1),
            "arrival_pct": None if self.arrival_pct is None
            else round(self.arrival_pct, 1),
            "stops": self.stops, "profile": self.profile,
            "unreachable_at_km": None if self.unreachable_at_km is None
            else round(self.unreachable_at_km, 1),
            "cost": round(self.cost, 2), "mode": self.mode,
            "assumptions": self.assumptions, "weather": self.weather,
            "arrival_band": self.arrival_band, "last_leg": self.last_leg,
        }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def cumulative_km(points: list[tuple]) -> list[float]:
    out = [0.0]
    for (a, b) in zip(points, points[1:]):
        out.append(out[-1] + haversine_km(a[0], a[1], b[0], b[1]))
    return out


def _model_rate(car: CarModel, temp_c, avg_speed_kmh, hvac_on) -> float:
    """The flat-road rate: the car's personal model for these conditions when
    it has one, else consumption_kwh_per_km.

    Hills are never part of this rate. segment_kwh adds a climb's potential
    energy and credits regeneration on the way down; a rate that also grew
    with the gradient would count every hill twice. Errors are not swallowed:
    a model that cannot predict must not quietly plan on another figure.
    """
    m = car.consumption_model
    if m is None:
        return car.consumption_kwh_per_km
    return float(m.predict(temp_c, avg_speed_kmh, None, hvac_on)[0])


# Without a measured spread, consumption is taken to be within this share of
# the planned rate either way: an assumption, and the page says so.
ASSUMED_RATE_ERROR = 0.10


@dataclass
class RateError:
    """How far the real consumption may be from the planned rate, kWh/km:
    `low` (negative: used less) to `high`, e.g. the 10th to 90th percentile
    of this car's own drives against their predictions."""
    low: float
    high: float
    basis: str


@dataclass
class Conditions:
    """The weather from `km` along the route until the next sample: as
    forecast for when the car passes there (routing/weather.py)."""
    km: float
    temp_c: float | None = None
    wind_kmh: float | None = None       # at 10 m
    wind_from_deg: float | None = None  # where it blows from, clockwise from north


def bearing_deg(a: tuple, b: tuple) -> float:
    """Initial compass bearing from point a to point b."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def headwind_kmh(c: Conditions, bearing: float) -> float:
    """The forecast wind's component against the direction of travel
    (negative: from behind), at 10 m."""
    if c.wind_kmh is None or c.wind_from_deg is None:
        return 0.0
    return c.wind_kmh * math.cos(math.radians(c.wind_from_deg - bearing))


def wind_kwh(dist_km: float, speed_kmh: float | None, headwind: float,
             temp_c: float | None, car: CarModel) -> float:
    """The energy a wind adds over a stretch (negative: saves) against calm air.

    Aerodynamic drag goes with the square of the speed through the air, so a
    headwind costs more than the same tailwind saves. Cold air is denser.
    The car's own consumption figure was measured in whatever wind there was;
    averaged over directions that is close to calm (only the small squared
    term remains), so this is counted on top of it."""
    if not headwind or not speed_kmh:
        return 0.0
    v = speed_kmh / 3.6
    va = v + headwind * WIND_AT_ROAD / 3.6
    rho = AIR_DENSITY if temp_c is None else 101325.0 / (287.05 * (temp_c + 273.15))
    force = 0.5 * rho * car.drag_area_m2 * (abs(va) * va - v * v)       # N
    return force * dist_km * 1000.0 / 3.6e6 / car.drivetrain_eff


def segment_kwh(dist_km: float, dh_m: float, car: CarModel,
                temp_c=None, avg_speed_kmh=None, hvac_on=None) -> float:
    """Energy for one stretch of road; negative only through regeneration."""
    rate = _model_rate(car, temp_c, avg_speed_kmh, hvac_on)
    flat = dist_km * rate
    potential = car.mass_kg * G * abs(dh_m) / 3.6e6          # kWh
    if dh_m > 0:
        return flat + potential / car.drivetrain_eff
    return flat - potential * car.regen_eff


def charge_power_kw(pct: float, curve, charger_kw: float) -> float:
    """Power available at a SoC: the car's curve, capped by the charger."""
    pts = sorted(curve)
    if pct <= pts[0][0]:
        kw = pts[0][1]
    elif pct >= pts[-1][0]:
        kw = pts[-1][1]
    else:
        kw = pts[-1][1]
        for (s0, k0), (s1, k1) in zip(pts, pts[1:]):
            if s0 <= pct <= s1:
                kw = k0 + (k1 - k0) * (pct - s0) / (s1 - s0)
                break
    return max(0.5, min(kw, charger_kw))


def charge_seconds(from_pct: float, to_pct: float, car: CarModel,
                   charger_kw: float) -> float:
    """Time to charge between two SoCs, integrated in 1 % steps."""
    if to_pct <= from_pct:
        return 0.0
    step_kwh = car.usable_kwh / 100.0
    seconds, pct = 0.0, from_pct
    while pct < to_pct - 1e-9:
        step = min(1.0, to_pct - pct)
        kw = charge_power_kw(pct + step / 2, car.charge_curve, charger_kw)
        seconds += step * step_kwh / kw * 3600.0
        pct += step
    return seconds


def charge_table(car: CarModel, charger_kw: float) -> list[float]:
    """Seconds to charge from 0 % to each whole percent; the search reads
    charging time off this (interpolated) instead of integrating each time."""
    step_kwh = car.usable_kwh / 100.0
    t = [0.0] * 101
    for s in range(100):
        t[s + 1] = t[s] + step_kwh / charge_power_kw(s + 0.5, car.charge_curve,
                                                     charger_kw) * 3600.0
    return t


def table_seconds(t: list[float], pct: float) -> float:
    pct = min(100.0, max(0.0, pct))
    i = min(99, int(pct))
    return t[i] + (pct - i) * (t[i + 1] - t[i])


def leave_levels(settings: PlanSettings) -> list[float]:
    """Charge levels a stop may leave at: whole steps above the reserve and
    below the charge-to limit, then the limit itself."""
    out = []
    k = 1
    while k * settings.level_step_pct < settings.charge_to_pct - 1e-9:
        x = k * settings.level_step_pct
        if x > settings.reserve_pct + 1e-9:
            out.append(x)
        k += 1
    out.append(settings.charge_to_pct)
    return out


def nearest_point(points: list[tuple], lat: float, lon: float) -> tuple[int, float]:
    """(index, km) of the route point nearest a place: every NEAREST_STRIDE-th
    point first, then every point around the best of those."""
    best_k, best_d = 0, math.inf
    for k in range(0, len(points), NEAREST_STRIDE):
        d = haversine_km(lat, lon, points[k][0], points[k][1])
        if d < best_d:
            best_k, best_d = k, d
    lo = max(0, best_k - NEAREST_STRIDE)
    hi = min(len(points) - 1, best_k + NEAREST_STRIDE)
    for k in range(lo, hi + 1):
        d = haversine_km(lat, lon, points[k][0], points[k][1])
        if d < best_d:
            best_k, best_d = k, d
    return best_k, best_d


@dataclass
class _Candidate:
    idx: int                          # nearest route point
    offset_km: float                  # straight-line distance from it
    charger: Charger
    price: float
    table: list[float]
    level_cost: list[float]           # per leave level: price + time terms
    fixed_cost: float                 # fee + overhead + detour
    choice_price: float               # price as the search sees it (preferences)
    preferred: bool


def network_in(operator: str, names) -> bool:
    """Whether `operator` belongs to one of the networks `names`: any of them
    found in it, ignoring case ("ionity" matches "IONITY GmbH")."""
    op = (operator or "").lower()
    return any(n.strip().lower() in op for n in names if n and n.strip())


def usable(c: Charger, settings: PlanSettings) -> bool:
    """Whether the driver's choices allow charger `c` at all."""
    if c.id and c.id in settings.avoid_chargers:
        return False
    if network_in(c.operator, settings.avoid_networks):
        return False
    return not (settings.public_only and c.usage_type in MEMBERS_ONLY_USAGE)


def _alternatives(cand, cands, cum) -> list[dict]:
    """Up to ALT_MAX other usable chargers within ALT_WINDOW_KM of a stop,
    nearest along the route first: what the driver can pick instead."""
    km = cum[cand.idx]
    near = [d for d in cands if d is not cand
            and abs(cum[d.idx] - km) <= ALT_WINDOW_KM
            and not (d.charger.id and d.charger.id == cand.charger.id)]
    near.sort(key=lambda d: (round(abs(cum[d.idx] - km), 3), -d.charger.power_kw,
                             d.charger.name))
    return [{"id": d.charger.id, "name": d.charger.name,
             "operator": d.charger.operator, "power_kw": d.charger.power_kw,
             "km": round(cum[d.idx], 1), "detour_km": round(d.offset_km, 1),
             "price_per_kwh": round(d.price, 4),
             "access": USAGE_LABELS.get(d.charger.usage_type, ""),
             "preferred": d.preferred} for d in near[:ALT_MAX]]


def plan_trip(points: list[tuple], drive_s: float, start_pct: float,
              car: CarModel, settings: PlanSettings,
              chargers: list[Charger],
              temp_c=None, avg_speed_kmh=None, hvac_on=None,
              weather: list[Conditions] | None = None,
              rate_error: RateError | None = None) -> Plan:
    """Plan a trip along `points` [(lat, lon, elevation_m or None), ...].

    `drive_s` is the routing provider's driving time for the whole route;
    `chargers` are the chargers near it (any order; slow or distant ones are
    dropped here). `weather`, sorted by km, gives each stretch its forecast:
    the wind through wind_kwh, the temperature through the car's own model
    only (a flat figure has no temperature to adjust) -- in place of temp_c.
    `rate_error` sets the arrival range (see _add_bands); without it, the
    rate is assumed right to within ASSUMED_RATE_ERROR.
    """
    if len(points) < 2:
        raise ValueError("a route needs at least two points")
    n = len(points)
    cum = cumulative_km(points)
    cap = car.usable_kwh
    reserve = cap * settings.reserve_pct / 100.0
    tv = settings.time_value()

    weather = sorted(weather or [], key=lambda c: c.km)
    marks = [c.km for c in weather]
    # Drag needs a speed: the one given, else the route's average.
    aero_kmh = avg_speed_kmh or (cum[-1] / (drive_s / 3600.0) if drive_s > 0 else None)
    wind_total, temps = 0.0, []
    seg = [0.0] * n
    net = [0.0] * n                   # net energy used from the start to point k
    for i in range(1, n):
        a, b = points[i - 1], points[i]
        dh = (b[2] - a[2]) if (a[2] is not None and b[2] is not None) else 0.0
        dist = cum[i] - cum[i - 1]
        here = weather[max(0, bisect_right(marks, cum[i - 1]) - 1)] if weather else None
        t = here.temp_c if here is not None and here.temp_c is not None else temp_c
        seg[i] = segment_kwh(dist, dh, car, temp_c=t, avg_speed_kmh=avg_speed_kmh,
                             hvac_on=hvac_on)
        if here is not None:
            w = wind_kwh(dist, aero_kmh, headwind_kmh(here, bearing_deg(a, b)), t, car)
            seg[i] += w
            wind_total += w
            if t is not None:
                temps.append(t)
        net[i] = net[i - 1] + seg[i]
    span = (min(temps), max(temps)) if temps else temp_c
    summary = {"wind_kwh": round(wind_total, 2), "samples": len(weather),
               "temp_min": round(min(temps), 1) if temps else None,
               "temp_max": round(max(temps), 1) if temps else None} if weather else None
    conditions = (span, avg_speed_kmh, hvac_on, summary)
    planned_rate = _model_rate(car, temp_c, avg_speed_kmh, hvac_on)

    levels = leave_levels(settings)
    level_kwh = [cap * x / 100.0 for x in levels]
    min_add = cap * settings.min_charge_pct / 100.0
    tables: dict[float, list[float]] = {}
    cands: list[_Candidate] = []
    for c in chargers:
        if c.power_kw < settings.min_charger_kw or not usable(c, settings):
            continue
        idx, off = nearest_point(points, c.lat, c.lon)
        pinned = bool(c.id) and c.id in settings.use_chargers
        if off > (PIN_MAX_OFFSET_KM if pinned else settings.search_radius_km) \
                or idx == n - 1:
            continue
        table = tables.setdefault(c.power_kw, charge_table(car, c.power_kw))
        price = c.price_per_kwh if c.price_per_kwh is not None \
            else settings.default_price_per_kwh
        # Preferences change what the search weighs, never a shown cost:
        # _simulate prices every stop at `price`.
        preferred = network_in(c.operator, settings.prefer_networks)
        choice = price * (1.0 - settings.prefer_discount) if preferred else price
        detour_s = 2 * off * DETOUR_ROAD_FACTOR / DETOUR_SPEED_KMH * 3600.0
        fixed = c.fee + tv * (settings.stop_overhead_min * 60.0 + detour_s) / 3600.0
        if pinned:
            fixed -= PIN_BONUS
        cands.append(_Candidate(
            idx, off, c, price, table,
            [choice * e / settings.charging_eff + tv * table_seconds(table, x) / 3600.0
             for x, e in zip(levels, level_kwh)],
            fixed, choice, preferred))
    cands.sort(key=lambda d: d.idx)   # stable: equal points keep input order

    # Node 0 is the start; node j >= 1 is cands[j - 1].
    def node_idx(j):
        return 0 if j == 0 else cands[j - 1].idx

    big = math.inf
    # best[j][l] = (cost so far, parent node, parent level); the start has one
    # state (level -1: it leaves with start_pct).
    best: list[list[tuple[float, int, int]]] = [[(big, -1, -1)] * len(levels)
                                                for _ in range(len(cands) + 1)]
    start_e = cap * start_pct / 100.0
    final = None                      # (cost, arrival kWh, node, level)

    def departures(j):
        if j == 0:
            return [(-1, start_e, 0.0)]
        return [(li, level_kwh[li], best[j][li][0]) for li in range(len(levels))
                if best[j][li][0] < big]

    for j in range(len(cands) + 1):
        deps = departures(j)
        if not deps:
            continue
        top = max(e for _, e, _ in deps)
        reach = _reach(j, node_idx, cands, net, top - reserve, n)
        for li, dep, cost in deps:
            dest = reach[-1]
            if dest is not None and dest[1] <= dep - reserve + 1e-9:
                arrive = min(cap, dep - dest[2])
                if final is None or cost < final[0] - 1e-9 or (
                        cost <= final[0] + 1e-9 and arrive > final[1] + 1e-9):
                    final = (cost, arrive, j, li)
            for k, dip, used in reach[:-1]:
                if dip > dep - reserve + 1e-9:
                    break
                cand = cands[k - 1]
                arrive = min(cap, dep - used)
                base = cost + cand.fixed_cost \
                    - cand.choice_price * arrive / settings.charging_eff \
                    - tv * table_seconds(cand.table, arrive / cap * 100.0) / 3600.0
                row = best[k]
                for l2 in range(len(levels)):
                    if level_kwh[l2] < arrive + min_add - 1e-9:
                        continue
                    c2 = base + cand.level_cost[l2]
                    if c2 < row[l2][0] - 1e-9:
                        row[l2] = (c2, j, li)

    if final is not None:
        path = _path(best, final[2], final[3])
        return _add_bands(_simulate(points, cum, seg, cands, path, levels, drive_s,
                                    start_pct, car, settings, None, conditions),
                          car, settings, rate_error, planned_rate)

    # Unreachable: follow the plan that gets furthest, and say where it fails.
    far = (-1.0, big, 0, -1)          # (km reached, cost, node, level)
    for j in range(len(cands) + 1):
        deps = departures(j)
        if not deps:
            continue
        li, dep, cost = max(deps, key=lambda d: (d[1], -d[2]))
        km = cum[_fail_index(node_idx(j), net, dep - reserve, n)]
        if km > far[0] + 1e-9 or (km >= far[0] - 1e-9 and cost < far[1] - 1e-9):
            far = (km, cost, j, li)
    path = _path(best, far[2], far[3])
    return _add_bands(_simulate(points, cum, seg, cands, path, levels, drive_s,
                                start_pct, car, settings, reserve, conditions),
                      car, settings, rate_error, planned_rate)


def _add_bands(plan: Plan, car: CarModel, settings: PlanSettings,
               err: RateError | None, rate: float) -> Plan:
    """Each arrival's range, if the car uses err.low to err.high kWh/km more
    than planned over the leg that leads there. A whole leg carries the spread
    of one drive; a long leg evens out some of it, so the range is on the wide
    side. Stops were chosen for the planned figure; this says how much room
    that leaves.

    `err` may instead be a range of the car's own physics model
    (routing/uncertainty.py ModelRange, PC only): anything with
    band(pct, leg_km, first) -> (low, high), basis, summary() and
    assumption()."""
    if err is not None and hasattr(err, "band"):
        prev = 0.0
        for i, s in enumerate(plan.stops):
            s["arrive_low_pct"], s["arrive_high_pct"] = err.band(
                s["arrive_pct"], s["km"] - prev, i == 0)
            s["arrive_low_below_reserve"] = s["arrive_low_pct"] < settings.reserve_pct
            prev = s["km"]
        if plan.arrival_pct is not None:
            lo, hi = err.band(plan.arrival_pct, plan.distance_km - prev, not plan.stops)
            plan.arrival_band = {"low_pct": lo, "high_pct": hi, "basis": err.basis,
                                 "below_reserve": lo < settings.reserve_pct,
                                 **err.summary()}
        plan.assumptions.append(err.assumption())
        return plan
    if err is None:
        err = RateError(-ASSUMED_RATE_ERROR * rate, ASSUMED_RATE_ERROR * rate,
                        f"assumed: consumption within {ASSUMED_RATE_ERROR * 100:.0f} % "
                        "of the plan either way (not measured on this car)")
    cap = car.usable_kwh

    def band(pct: float, leg_km: float) -> tuple[float, float]:
        return (round(max(0.0, pct - leg_km * err.high / cap * 100.0), 1),
                round(min(100.0, pct - leg_km * err.low / cap * 100.0), 1))

    prev = 0.0
    for s in plan.stops:
        s["arrive_low_pct"], s["arrive_high_pct"] = band(s["arrive_pct"], s["km"] - prev)
        s["arrive_low_below_reserve"] = s["arrive_low_pct"] < settings.reserve_pct
        prev = s["km"]
    if plan.arrival_pct is not None:
        lo, hi = band(plan.arrival_pct, plan.distance_km - prev)
        plan.arrival_band = {"low_pct": lo, "high_pct": hi, "basis": err.basis,
                             "below_reserve": lo < settings.reserve_pct}
    plan.assumptions.append(
        f"Arrival ranges: {err.basis}; that is {err.low * 100:+.1f} to "
        f"{err.high * 100:+.1f} kWh/100 km against the plan over each leg.")
    return plan


def _reach(j, node_idx, cands, net, budget, n):
    """From node j: [(node, deepest dip, net used)] for the later candidates in
    order while the dip stays within `budget`, then the destination as
    (None-able) (n - 1, dip, used) -- None when it is out of reach."""
    a = node_idx(j)
    out = []
    dip = 0.0
    k = a
    nxt = j + 1
    while nxt <= len(cands):
        target = cands[nxt - 1].idx
        while k < target:
            k += 1
            dip = max(dip, net[k] - net[a])
        if dip > budget + 1e-9:
            break
        out.append((nxt, dip, net[target] - net[a]))
        nxt += 1
    dest = None
    if nxt > len(cands):
        while k < n - 1:
            k += 1
            dip = max(dip, net[k] - net[a])
        if dip <= budget + 1e-9:
            dest = (n - 1, dip, net[n - 1] - net[a])
    out.append(dest)
    return out


def _fail_index(a, net, budget, n):
    """First point after `a` where the energy used exceeds `budget`."""
    for k in range(a + 1, n):
        if net[k] - net[a] > budget + 1e-9:
            return k
    return n - 1


def _path(best, j, li):
    """The stops leading to state (j, li), first stop first (start excluded)."""
    out = []
    while j > 0:
        out.append((j, li))
        _, j, li = best[j][li]
    out.reverse()
    return out


def _simulate(points, cum, seg, cands, path, levels, drive_s, start_pct, car,
              settings, fail_reserve, conditions=(None, None, None, None)):
    """Drive the chosen stops point by point (regeneration capped at a full
    battery): the profile, each stop's real arrival, time and cost. With
    `fail_reserve`, keep driving after the last stop until the charge falls
    below it, and report where."""
    n = len(points)
    cap = car.usable_kwh

    def pct(kwh):
        return kwh / cap * 100.0

    energy = cap * start_pct / 100.0
    profile = [(0, energy)]
    stops, charge_total, money_total = [], 0.0, 0.0
    # Each leg's energy is what the battery lost on it (regeneration counted,
    # capped at a full battery), from the start or the last stop's departure.
    leg_kwh_from, leg_km_from = energy, 0.0
    k = 0
    for j, li in path:
        cand = cands[j - 1]
        while k < cand.idx:
            k += 1
            energy = min(cap, energy - seg[k])
            profile.append((k, energy))
        arrive = energy
        depart = max(arrive, cap * levels[li] / 100.0)
        secs = charge_seconds(pct(arrive), pct(depart), car, cand.charger.power_kw)
        added = depart - arrive
        money = cand.charger.fee + cand.price * added / settings.charging_eff
        charge_total += secs
        money_total += money
        c = cand.charger
        stops.append({
            "km": round(cum[cand.idx], 1), "name": c.name,
            "operator": c.operator, "address": c.address,
            "lat": c.lat, "lon": c.lon, "power_kw": c.power_kw,
            "arrive_pct": round(pct(arrive), 1), "depart_pct": round(pct(depart), 1),
            "charge_min": round(secs / 60.0), "kwh": round(added, 1),
            "price_per_kwh": round(cand.price, 4), "fee": round(c.fee, 2),
            "price_source": c.price_source or "default price",
            "cost": round(money, 2), "detour_km": round(cand.offset_km, 1),
            "id": c.id, "access": USAGE_LABELS.get(c.usage_type, ""),
            "preferred": cand.preferred,
            "alternatives": _alternatives(cand, cands, cum),
            "leg_km": round(cum[cand.idx] - leg_km_from, 1),
            "leg_kwh": round(leg_kwh_from - arrive, 2),
        })
        leg_kwh_from, leg_km_from = depart, cum[cand.idx]
        profile.append((cand.idx, depart))      # vertical step: arrive -> depart
        energy = depart
    unreachable_at = None
    while k < n - 1:
        e = min(cap, energy - seg[k + 1])
        if fail_reserve is not None and e < fail_reserve:
            unreachable_at = cum[k + 1]
            break
        k += 1
        energy = e
        profile.append((k, energy))
    if fail_reserve is not None and unreachable_at is None:
        unreachable_at = cum[n - 1]       # only the cap saved it; still say so
    status = "unreachable" if fail_reserve is not None else (
        "with_stops" if stops else "direct")
    return Plan(
        status=status, distance_km=cum[-1], drive_s=drive_s, start_pct=start_pct,
        arrival_pct=None if fail_reserve is not None else pct(energy),
        stops=stops, profile=_profile(profile, cum, cap),
        unreachable_at_km=unreachable_at, charge_s=charge_total, cost=money_total,
        mode=settings.mode, assumptions=assumptions(car, settings, *conditions),
        weather=conditions[3],
        last_leg=None if fail_reserve is not None else {
            "km": round(cum[-1] - leg_km_from, 1),
            "kwh": round(leg_kwh_from - energy, 2)})


def _profile(points: list[tuple[int, float]], cum, cap) -> list[list[float]]:
    """[km, %] pairs for the chart, thinned; a stop appears as two points at
    the same km (arrival, then departure), which draws the charging step."""
    return _thin([[round(cum[k], 2), round(e / cap * 100.0, 1)]
                  for k, e in points])


def _thin(profile: list[list[float]], limit: int = 400) -> list[list[float]]:
    """Keep the chart payload bounded; never drop the charging steps."""
    if len(profile) <= limit:
        return profile
    keep = {0, len(profile) - 1}
    for k in range(1, len(profile)):
        if profile[k][0] == profile[k - 1][0]:          # a charging step
            keep.update({k - 1, k})
    stride = max(1, len(profile) // limit)
    keep.update(range(0, len(profile), stride))
    return [profile[k] for k in sorted(keep)]


def _consumption_assumption(car: CarModel, temp, avg_speed_kmh, hvac_on,
                            windy: bool = False) -> str:
    """`temp` is one temperature or the (lowest, highest) forecast along the way."""
    lo_t, hi_t = temp if isinstance(temp, tuple) else (temp, temp)
    m = car.consumption_model
    if m is None:
        inside = ("speed, temperature and heating are inside this one figure; the "
                  "wind is counted on top" if windy else
                  "speed, temperature, wind and heating are inside this one figure")
        return (f"Consumption {car.consumption_kwh_per_km * 100:.1f} kWh/100 km on the "
                f"flat; {inside}.")
    ranges = getattr(m, "ranges", {})
    used = getattr(m, "features_used", ())
    given = []
    for f, label, lo, hi, unit in (("temp", "outside", lo_t, hi_t, "°C"),
                                   ("speed", "average speed", avg_speed_kmh,
                                    avg_speed_kmh, "km/h")):
        if lo is None or f not in used:
            continue
        text = f"{label} {lo:.0f} {unit}" if round(lo) == round(hi) else \
            f"{label} {lo:.0f} to {hi:.0f} {unit}"
        r_lo, r_hi = ranges.get(f, (lo, hi))
        if lo < r_lo or hi > r_hi:
            text += (f" (counted within the {r_lo:.0f} to {r_hi:.0f} {unit} it has "
                     "been driven at)")
        given.append(text)
    if hvac_on is not None and "hvac" in used:
        given.append("heating/air con " + ("on" if hvac_on else "off"))
    rates = sorted({round(_model_rate(car, t, avg_speed_kmh, hvac_on) * 100, 1)
                    for t in (lo_t, hi_t)})
    figure = f"{rates[0]:.1f}" + (f" to {rates[-1]:.1f}" if len(rates) > 1 else "")
    names = ", ".join(FEATURE_LABELS.get(f, f) for f in used) or "no conditions"
    return (f"Consumption {figure} kWh/100 km on the flat from this car's own "
            f"model (learned from {getattr(m, 'n_drives', 0)} drives; uses {names})"
            + (f" for {', '.join(given)}" if given else "")
            + "; conditions not known count as the car's usual ones.")


def _weather_assumption(car: CarModel, summary: dict) -> str:
    wind = summary["wind_kwh"]
    effect = (f"the wind costs about {wind:.1f} kWh over the trip" if wind > 0.05 else
              f"the wind saves about {-wind:.1f} kWh over the trip" if wind < -0.05 else
              "the wind makes no real difference")
    uses_temp = "temp" in getattr(car.consumption_model, "features_used", ())
    return (f"Weather: the forecast for when you pass each of {summary['samples']} "
            f"points; {effect} (drag area {car.drag_area_m2:.2f} m², wind at the "
            f"car's height {WIND_AT_ROAD * 100:.0f} % of the 10 m forecast; a "
            "headwind costs more than the same tailwind saves). Temperature changes "
            "consumption only through this car's own model"
            + ("." if uses_temp else ", which does not use it, so it is not "
               "counted.") + " Rain and snow are not counted.")


def assumptions(car: CarModel, settings: PlanSettings, temp_c=None,
                avg_speed_kmh=None, hvac_on=None, weather: dict | None = None
                ) -> list[str]:
    mode = {
        "fastest": "Fastest: stops chosen for the least time charging and "
                   "stopping; price only breaks ties.",
        "cheapest": f"Cheapest: stops chosen for the lowest charging bill; time "
                    f"counts at only £{CHEAPEST_TIME_VALUE:.0f}/hour, so a stop is "
                    "not added to save pennies.",
    }.get(settings.mode,
          f"Balanced: stops chosen for the lowest bill plus your time at "
          f"£{settings.time_value_per_hour:.0f}/hour.")
    return [
        f"Usable battery {car.usable_kwh:.1f} kWh.",
        _consumption_assumption(car, temp_c, avg_speed_kmh, hvac_on, bool(weather)),
        f"Hills: {car.mass_kg:.0f} kg, {car.regen_eff * 100:.0f} % of descent "
        "energy recovered.",
        f"Arrive with at least {settings.reserve_pct:.0f} %; charge to at most "
        f"{settings.charge_to_pct:.0f} % at chargers of at least "
        f"{settings.min_charger_kw:.0f} kW within {settings.search_radius_km:.0f} km "
        "of the route; the detour to a charger counts as time, not energy.",
        mode + f" Each stop also counts {settings.stop_overhead_min:.0f} min for "
        f"pulling in and plugging in, and adds at least "
        f"{settings.min_charge_pct:.0f} %.",
        "Prices: your own network prices first, then the price listed on Open "
        "Charge Map (community-entered; can be out of date), else "
        f"£{settings.default_price_per_kwh:.2f}/kWh. Billed energy is the energy "
        f"added divided by {settings.charging_eff:.2f} (charging losses).",
        (f"Charging time follows {car.charge_curve_basis}, capped by the charger's "
         "rated power." if car.charge_curve_basis else
         "Charging time follows the ID.3 58 kWh DC curve, capped by the "
         "charger's rated power; real sessions are often slower."),
    ] + ([_weather_assumption(car, weather)] if weather else []) \
        + _choice_assumptions(settings)


def _choice_assumptions(settings: PlanSettings) -> list[str]:
    out = []
    if settings.avoid_networks:
        out.append("Never uses: " + ", ".join(settings.avoid_networks) + ".")
    if settings.prefer_networks:
        out.append("Prefers " + ", ".join(settings.prefer_networks) + ": their energy "
                   f"counts {settings.prefer_discount * 100:.0f} % cheaper when choosing "
                   "stops; the costs shown are the real prices.")
    if settings.public_only:
        out.append("Public access only: chargers that need a membership or notice "
                   "are left out.")
    if settings.avoid_chargers or settings.use_chargers:
        out.append(f"Your picks: {len(settings.use_chargers)} charger(s) used where "
                   f"reachable, {len(settings.avoid_chargers)} avoided.")
    return out
