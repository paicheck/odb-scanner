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
from dataclasses import dataclass, field

G = 9.81
EARTH_RADIUS_KM = 6371.0088

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
ALT_WINDOW_KM = 30.0              # alternatives: this far either side of a stop
ALT_MAX = 4


@dataclass
class CarModel:
    usable_kwh: float                 # energy the pack holds at 100 %
    consumption_kwh_per_km: float     # flat-road consumption
    mass_kg: float = 1950.0           # car + occupants + load
    drivetrain_eff: float = 0.90      # battery -> wheels when climbing
    regen_eff: float = 0.60           # share of descent energy recovered
    charge_curve: tuple[tuple[float, float], ...] = ID3_58_CURVE
    consumption_model: object = None   # optional personalised model (duck-typed)


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
            "assumptions": self.assumptions,
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


def _model_rate(car: CarModel, temp_c, avg_speed_kmh,
                 elev_gain_m_per_km, hvac_on) -> float:
    m = getattr(car, "consumption_model", None)
    if m is None:
        return car.consumption_kwh_per_km
    predict = getattr(m, "predict", None)
    if callable(predict):
        try:
            return float(predict(temp_c, avg_speed_kmh, elev_gain_m_per_km, hvac_on)[0])
        except Exception:
            pass
    if isinstance(m, dict):
        x = float(m.get("base_kwh_per_km", car.consumption_kwh_per_km))
        if temp_c is not None:
            x += float(m.get("temp_coeff", 0.0)) * temp_c
        if avg_speed_kmh is not None:
            x += float(m.get("speed_coeff", 0.0)) * avg_speed_kmh
        if elev_gain_m_per_km is not None:
            x += float(m.get("elev_coeff", 0.0)) * elev_gain_m_per_km
        if hvac_on is not None and m.get("hvac_coeff"):
            x += float(m.get("hvac_coeff", 0.0)) if hvac_on else 0.0
        return max(0.05, x)
    return car.consumption_kwh_per_km


def segment_kwh(dist_km: float, dh_m: float, car: CarModel,
                temp_c=None, avg_speed_kmh=None,
                elev_gain_m_per_km=None, hvac_on=None) -> float:
    """Energy for one stretch of road; negative only through regeneration."""
    if elev_gain_m_per_km is None and dist_km and dh_m and dh_m > 0:
        elev_gain_m_per_km = dh_m / dist_km
    rate = _model_rate(car, temp_c, avg_speed_kmh, elev_gain_m_per_km, hvac_on)
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
              temp_c=None, avg_speed_kmh=None,
              elev_gain_m_per_km=None, hvac_on=None) -> Plan:
    """Plan a trip along `points` [(lat, lon, elevation_m or None), ...].

    `drive_s` is the routing provider's driving time for the whole route;
    `chargers` are the chargers near it (any order; slow or distant ones are
    dropped here).
    """
    if len(points) < 2:
        raise ValueError("a route needs at least two points")
    n = len(points)
    cum = cumulative_km(points)
    cap = car.usable_kwh
    reserve = cap * settings.reserve_pct / 100.0
    tv = settings.time_value()

    seg = [0.0] * n
    net = [0.0] * n                   # net energy used from the start to point k
    for i in range(1, n):
        a, b = points[i - 1], points[i]
        dh = (b[2] - a[2]) if (a[2] is not None and b[2] is not None) else 0.0
        seg[i] = segment_kwh(cum[i] - cum[i - 1], dh, car,
                             temp_c=temp_c, avg_speed_kmh=avg_speed_kmh,
                             elev_gain_m_per_km=elev_gain_m_per_km,
                             hvac_on=hvac_on)
        net[i] = net[i - 1] + seg[i]

    levels = leave_levels(settings)
    level_kwh = [cap * x / 100.0 for x in levels]
    min_add = cap * settings.min_charge_pct / 100.0
    tables: dict[float, list[float]] = {}
    cands: list[_Candidate] = []
    for c in chargers:
        if c.power_kw < settings.min_charger_kw or not usable(c, settings):
            continue
        idx, off = nearest_point(points, c.lat, c.lon)
        if off > settings.search_radius_km or idx == n - 1:
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
        if c.id and c.id in settings.use_chargers:
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
        return _simulate(points, cum, seg, cands, path, levels, drive_s, start_pct,
                         car, settings, None)

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
    return _simulate(points, cum, seg, cands, path, levels, drive_s, start_pct,
                     car, settings, reserve)


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
              settings, fail_reserve):
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
        })
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
        mode=settings.mode, assumptions=assumptions(car, settings))


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


def assumptions(car: CarModel, settings: PlanSettings) -> list[str]:
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
        f"Consumption {car.consumption_kwh_per_km * 100:.1f} kWh/100 km on the "
        "flat; speed, temperature, wind and heating are inside this one figure.",
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
        "Charging time follows the ID.3 58 kWh DC curve, capped by the "
        "charger's rated power; real sessions are often slower.",
    ] + _choice_assumptions(settings)


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
