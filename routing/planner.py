"""EV trip planner: energy along a route, and where to charge.

Pure functions, no I/O: the route geometry and a charger lookup are passed in.
The Android app ports this file line for line (Planner.kt) and both are held
to tests/data/route_vectors.json, so the phone and the dashboard give the
same answer for the same trip.

THE MODEL, AND WHAT IT LEAVES OUT
---------------------------------
Energy for a stretch of road = distance x flat consumption + climbing energy
(m g dh, through a drivetrain efficiency) - recovered descent energy (a
fraction of m g |dh|). Consumption is either measured from this car or a
stated default; speed, temperature, wind and HVAC are not modelled
separately -- they live inside that one consumption figure. The detour from
the route to a charger is ignored. Every result carries these assumptions, so
it is read as an estimate, which is what it is.

Charging time integrates a charging-power curve (kW available at each SoC,
capped by the charger) in 1 % steps.
"""
from __future__ import annotations

import math
from collections.abc import Callable
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


@dataclass
class CarModel:
    usable_kwh: float                 # energy the pack holds at 100 %
    consumption_kwh_per_km: float     # flat-road consumption
    mass_kg: float = 1950.0           # car + occupants + load
    drivetrain_eff: float = 0.90      # battery -> wheels when climbing
    regen_eff: float = 0.60           # share of descent energy recovered
    charge_curve: tuple[tuple[float, float], ...] = ID3_58_CURVE


@dataclass
class PlanSettings:
    reserve_pct: float = 10.0         # never plan to arrive below this
    charge_to_pct: float = 80.0       # charge up to this at each stop
    min_charger_kw: float = 50.0      # ignore chargers slower than this
    search_step_km: float = 15.0      # spacing of charger searches
    search_back_km: float = 120.0     # how far back to look for a stop
    search_radius_km: float = 5.0     # charger distance from the route
    max_stops: int = 8


@dataclass
class Charger:
    name: str
    lat: float
    lon: float
    power_kw: float
    operator: str = ""
    address: str = ""


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


def segment_kwh(dist_km: float, dh_m: float, car: CarModel) -> float:
    """Energy for one stretch of road; negative only through regeneration."""
    flat = dist_km * car.consumption_kwh_per_km
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


ChargerLookup = Callable[[float, float, float], list[Charger]]


def plan_trip(points: list[tuple], drive_s: float, start_pct: float,
              car: CarModel, settings: PlanSettings,
              find_chargers: ChargerLookup) -> Plan:
    """Plan a trip along `points` [(lat, lon, elevation_m or None), ...].

    `drive_s` is the routing provider's driving time for the whole route;
    `find_chargers(lat, lon, radius_km)` returns chargers near a point.
    """
    if len(points) < 2:
        raise ValueError("a route needs at least two points")
    cum = cumulative_km(points)
    total_km = cum[-1]
    cap = car.usable_kwh
    reserve_kwh = cap * settings.reserve_pct / 100.0

    def seg(i: int) -> float:
        a, b = points[i - 1], points[i]
        dh = (b[2] - a[2]) if (a[2] is not None and b[2] is not None) else 0.0
        return segment_kwh(cum[i] - cum[i - 1], dh, car)

    def pct(kwh: float) -> float:
        return kwh / cap * 100.0

    # energy[k]: energy on arriving at point k (after charging, at a stop).
    # Kept exact per point so a rewind to a stop never goes through rounding.
    energy = [0.0] * len(points)
    energy[0] = cap * start_pct / 100.0
    profile: list[tuple[int, float]] = [(0, energy[0])]     # (point, kWh)
    stops: list[dict] = []
    charge_total = 0.0
    last_stop_idx = 0
    i = 1
    while i < len(points):
        e = min(cap, energy[i - 1] - seg(i))
        if e >= reserve_kwh:
            energy[i] = e
            profile.append((i, e))
            i += 1
            continue
        # Below the reserve at point i: find a charger behind it, searching
        # back from here in steps, never at or behind the previous stop.
        found = None
        if len(stops) < settings.max_stops:
            found = _find_stop(points, cum, i, cum[last_stop_idx], settings,
                               find_chargers)
        if found is None:
            return _unreachable(cum[i], total_km, drive_s, start_pct, stops,
                                _profile(profile, cum, cap), charge_total,
                                car, settings)
        idx, charger = found
        arrive = energy[idx]
        depart = max(arrive, cap * settings.charge_to_pct / 100.0)
        secs = charge_seconds(pct(arrive), pct(depart), car, charger.power_kw)
        charge_total += secs
        stops.append({
            "km": round(cum[idx], 1), "name": charger.name,
            "operator": charger.operator, "address": charger.address,
            "lat": charger.lat, "lon": charger.lon,
            "power_kw": charger.power_kw,
            "arrive_pct": round(pct(arrive), 1), "depart_pct": round(pct(depart), 1),
            "charge_min": round(secs / 60.0),
        })
        profile = [p for p in profile if p[0] <= idx]
        profile.append((idx, depart))           # vertical step: arrive -> depart
        energy[idx] = depart
        last_stop_idx = idx
        i = idx + 1

    return Plan(
        status="with_stops" if stops else "direct", distance_km=total_km,
        drive_s=drive_s, start_pct=start_pct,
        arrival_pct=pct(energy[-1]), stops=stops,
        profile=_profile(profile, cum, cap), charge_s=charge_total,
        assumptions=assumptions(car, settings))


def _profile(points: list[tuple[int, float]], cum, cap) -> list[list[float]]:
    """[km, %] pairs for the chart, thinned; a stop appears as two points at
    the same km (arrival, then departure), which draws the charging step."""
    return _thin([[round(cum[k], 2), round(e / cap * 100.0, 1)]
                  for k, e in points])


def _index_at(cum: list[float], km: float) -> int:
    best = 0
    for k, c in enumerate(cum):
        if c <= km + 1e-9:
            best = k
    return best


def _find_stop(points, cum, fail_idx, last_stop_km, settings, find_chargers):
    """The best charger reachable before `fail_idx`: search points stepping
    back from just before the failure, highest power first at each point.
    Stops strictly after the previous stop, and never at the start."""
    target = cum[fail_idx] - 1.0
    floor = max(last_stop_km + 1.0, cum[fail_idx] - settings.search_back_km)
    km = target
    while km >= floor:
        idx = min(_index_at(cum, km), fail_idx - 1)
        if idx == 0 or cum[idx] <= last_stop_km:
            break
        lat, lon = points[idx][0], points[idx][1]
        found = [c for c in find_chargers(lat, lon, settings.search_radius_km)
                 if c.power_kw >= settings.min_charger_kw]
        if found:
            found.sort(key=lambda c: (-c.power_kw,
                                      haversine_km(lat, lon, c.lat, c.lon)))
            return idx, found[0]
        km -= settings.search_step_km
    return None


def _unreachable(at_km, total_km, drive_s, start_pct, stops, profile,
                 charge_total, car, settings) -> Plan:
    return Plan(status="unreachable", distance_km=total_km, drive_s=drive_s,
                start_pct=start_pct, arrival_pct=None, stops=stops,
                profile=_thin(profile), unreachable_at_km=at_km,
                charge_s=charge_total, assumptions=assumptions(car, settings))


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
    return [
        f"Usable battery {car.usable_kwh:.1f} kWh.",
        f"Consumption {car.consumption_kwh_per_km * 100:.1f} kWh/100 km on the "
        "flat; speed, temperature, wind and heating are inside this one figure.",
        f"Hills: {car.mass_kg:.0f} kg, {car.regen_eff * 100:.0f} % of descent "
        "energy recovered.",
        f"Arrive with at least {settings.reserve_pct:.0f} %; charge to "
        f"{settings.charge_to_pct:.0f} % at chargers of at least "
        f"{settings.min_charger_kw:.0f} kW within {settings.search_radius_km:.0f} km "
        "of the route; the detour to the charger is not counted.",
        "Charging time follows the ID.3 58 kWh DC curve, capped by the "
        "charger's rated power; real sessions are often slower.",
    ]
