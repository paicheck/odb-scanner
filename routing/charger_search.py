"""Finding a charging stop: chargers near the route, the start, the
destination or a place, judged for this car, and what the trip becomes with
each one.

The planner (routing/planner.py) chooses stops itself among chargers within
search_radius_km of the route. This is for the driver who wants to choose:
to add a stop, replace one, or see what is around. Each charger found is
judged on

  * where it is: km along the route, the detour (there and back, by road),
    and the charge on arrival -- the plan's charge when passing there, less
    the drive off the route;
  * what charging there is like *for this car*: minutes from that arrival to
    the charge-to level on this car's charging curve, capped by the charger,
    and the average power that gives. A 350 kW charger is no faster than a
    150 kW one for a car that takes 110 kW, and the listing's figure says
    nothing about it, so both are shown;
  * cost: the price the planner would use (your own network price, the
    listing's, or the default), and whether it is known or assumed;
  * the driver's choices: preferred networks, and chargers already picked;
  * how far to trust the listing: when it was last checked, how many points
    it has.

The most promising (least added time by that reckoning) are then planned
for real with the charger pinned (PlanSettings.use_chargers), and the trip's
time, cost and arrival are compared with the current plan. So "add this
stop" shows what it costs before the driver asks for it, and the planner
stays the one place that decides a plan: this module only proposes picks.
Chargers the driver avoids, or below the minimum power, are not offered;
how many were left out is said.

Pure functions; the chargers are passed in (routing/providers.py fetches).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from routing.planner import (
    ALT_WINDOW_KM,
    DETOUR_ROAD_FACTOR,
    DETOUR_SPEED_KMH,
    PIN_MAX_OFFSET_KM,
    USAGE_LABELS,
    charge_seconds,
    cumulative_km,
    haversine_km,
    nearest_point,
    network_in,
    plan_trip,
    usable,
)

AREAS = ("route", "start", "destination", "place")
MAX_WITHIN_KM = 25.0
REPLAN_TOP = 8
LIMIT = 12
STALE_LISTING_DAYS = 2 * 365


def _pct_at(profile: list[list[float]], km: float) -> float | None:
    """The plan's charge when passing `km` (before any charging there); None
    beyond where the profile runs (an unreachable plan stops early)."""
    km = round(km, 2)                    # the profile's own resolution
    for (k0, p0), (k1, p1) in zip(profile, profile[1:]):
        if k0 <= km <= k1 and k1 > k0:
            return p0 + (p1 - p0) * (km - k0) / (k1 - k0)
    if profile and abs(profile[0][0] - km) < 1e-9:
        return profile[0][1]
    return None


def peak_kw(car) -> float:
    return max(kw for _, kw in car.charge_curve)


def average_kw(car, from_pct: float, to_pct: float, charger_kw: float) -> float | None:
    """The average power this car would draw between two charges here."""
    secs = charge_seconds(from_pct, to_pct, car, charger_kw)
    if secs <= 0:
        return None
    return car.usable_kwh * (to_pct - from_pct) / 100.0 / (secs / 3600.0)


def stop_seconds(plan, settings) -> float:
    """A plan's time from setting off to arriving: driving, charging, and for
    each stop the overhead and the detour there and back."""
    out = plan.drive_s + plan.charge_s
    for s in plan.stops:
        out += settings.stop_overhead_min * 60.0
        out += 2 * s["detour_km"] * DETOUR_ROAD_FACTOR / DETOUR_SPEED_KMH * 3600.0
    return out


def _age_days(checked: str, now: datetime) -> float | None:
    if not checked:
        return None
    try:
        dt = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def assess(c, points, cum, plan, car, settings, now: datetime) -> dict:
    """One charger, judged for this car against the current plan."""
    idx, off = nearest_point(points, c.lat, c.lon)
    km = cum[idx]
    cap = car.usable_kwh
    rate = car.consumption_kwh_per_km
    passing = _pct_at(plan.profile, km)
    # Just after a planned stop this would count that stop's charge; for a
    # charger that could take its place, count it as if that stop were not
    # made: its arrival, less what the stretch since uses.
    before = [s for s in plan.stops if s.get("id") != c.id or not c.id]
    before = [s for s in before if 0.05 < km - s["km"] <= ALT_WINDOW_KM]
    if before and passing is not None:
        s = before[-1]
        passing = s["arrive_pct"] - (s["depart_pct"] - passing)
    one_way_km = off * DETOUR_ROAD_FACTOR
    arrive = None if passing is None else passing - one_way_km * rate / cap * 100.0
    target = settings.charge_to_pct
    price = (c.price_per_kwh if c.price_per_kwh is not None
             else settings.default_price_per_kwh)
    out = {
        "id": c.id, "name": c.name, "operator": c.operator, "address": c.address,
        "lat": c.lat, "lon": c.lon, "power_kw": c.power_kw,
        "km": round(km, 1), "off_route_km": round(off, 1),
        "detour_min": round(2 * one_way_km / DETOUR_SPEED_KMH * 60.0),
        "arrive_pct": None if arrive is None else round(arrive, 1),
        "price_per_kwh": round(price, 4),
        "price_source": c.price_source or "default price",
        "access": USAGE_LABELS.get(c.usage_type, ""),
        "preferred": network_in(c.operator, settings.prefer_networks),
        "picked": bool(c.id) and c.id in settings.use_chargers,
        "at": ("start" if idx == 0 else "destination" if idx == len(points) - 1
               else "route"),
        "notes": [],
    }
    notes = out["notes"]
    if arrive is not None and arrive < target:
        secs = charge_seconds(max(0.0, arrive), target, car, c.power_kw)
        added = cap * (target - max(0.0, arrive)) / 100.0
        out["charge_min"] = round(secs / 60.0)
        out["average_kw"] = round(average_kw(car, max(0.0, arrive), target,
                                             c.power_kw), 0)
        out["cost"] = round(c.fee + price * added / settings.charging_eff, 2)
        out["to_pct"] = target
    else:
        out["charge_min"] = out["average_kw"] = out["cost"] = out["to_pct"] = None
    top = peak_kw(car)
    if c.power_kw > top * 1.1:
        notes.append(f"{c.power_kw:.0f} kW listed; this car takes at most {top:.0f}")
    if arrive is None:
        notes.append("beyond where the current plan runs out of charge")
    elif arrive < settings.reserve_pct:
        notes.append(f"you would arrive with {arrive:.0f} %, below your "
                     f"{settings.reserve_pct:.0f} % reserve")
    elif arrive > target - settings.min_charge_pct:
        notes.append(f"you would arrive with {arrive:.0f} %: too full for a stop to add "
                     f"{settings.min_charge_pct:.0f} % under your {target:.0f} % limit")
    if out["at"] == "destination":
        notes.append("at the destination: a charge after you arrive, not a stop")
    elif out["at"] == "start":
        notes.append("at the start: a charge before you set off")
    if off > PIN_MAX_OFFSET_KM:
        notes.append(f"{off:.0f} km off the route: too far for the planner to use")
    if not c.price_source or c.price_source == "default price":
        notes.append(f"price not known: {settings.default_price_per_kwh:.2f}/kWh assumed")
    age = _age_days(getattr(c, "checked", ""), now)
    if age is not None and age > STALE_LISTING_DAYS:
        notes.append(f"listing last checked {age / 365:.0f} years ago")
    if getattr(c, "points", None) == 1:
        notes.append("a single charging point: it may be taken")
    if out["access"] and out["access"] != "public":
        notes.append(out["access"])
    # Rough added time if you stop here, for ordering before the real plans.
    out["rough_min"] = (out["detour_min"] + settings.stop_overhead_min
                        + (out["charge_min"] or 0))
    return out


def _instead_of(found: dict, plan) -> dict | None:
    """The planned stop this charger could stand in for: the nearest one
    along the route within ALT_WINDOW_KM, if any."""
    near = [s for s in plan.stops if s.get("id") and s["id"] != found["id"]
            and abs(s["km"] - found["km"]) <= ALT_WINDOW_KM]
    if not near:
        return None
    s = min(near, key=lambda s: abs(s["km"] - found["km"]))
    return {"id": s["id"], "name": s["name"], "km": s["km"]}


def search(points, drive_s, start_pct, car, settings, chargers, plan, area="route",
           near: tuple[float, float] | None = None, within_km: float = 10.0,
           limit: int = LIMIT, replan_top: int = REPLAN_TOP, now: datetime | None = None,
           **plan_kw) -> dict:
    """Chargers in `area` ("route": within `within_km` of the route; "start",
    "destination", "place": within `within_km` of `near`), judged against
    `plan` (the current plan for these points and settings), best first; the
    first `replan_top` planned for real with the charger pinned. `plan_kw`
    goes to plan_trip unchanged (temp_c, weather, rate_error...)."""
    if area not in AREAS:
        raise ValueError(f"area must be one of {AREAS}")
    within_km = max(0.5, min(MAX_WITHIN_KM, within_km))
    now = now or datetime.now(timezone.utc)
    cum = cumulative_km(points)
    avoided = slow = far = 0
    found = []
    seen = set()
    for c in chargers:
        key = c.id or (c.name, round(c.lat, 5), round(c.lon, 5))
        if key in seen:
            continue
        seen.add(key)
        if area == "route":
            if nearest_point(points, c.lat, c.lon)[1] > within_km:
                far += 1
                continue
        elif near is None or haversine_km(near[0], near[1], c.lat, c.lon) > within_km:
            far += 1
            continue
        if not usable(c, settings):
            avoided += 1
            continue
        if c.power_kw < settings.min_charger_kw:
            slow += 1
            continue
        found.append(assess(c, points, cum, plan, car, settings, now))
    # Ordering: chargers you could reach with your reserve first, then by
    # the rough added time, then the nearest along the way.
    found.sort(key=lambda f: (f["arrive_pct"] is None
                              or f["arrive_pct"] < settings.reserve_pct,
                              f["rough_min"], f["km"]))
    found = found[:max(1, limit)]
    for f in found:
        f["instead_of"] = _instead_of(f, plan)
    base_s = stop_seconds(plan, settings)
    for f in found[:replan_top]:
        if not f["id"] or f["at"] == "destination" or f["picked"]:
            continue
        with_it = plan_trip(points, drive_s, start_pct, car,
                            replace(settings, use_chargers=tuple(settings.use_chargers)
                                    + (f["id"],)),
                            chargers, **plan_kw)
        used = any(s.get("id") == f["id"] for s in with_it.stops)
        f["with_stop"] = {
            "status": with_it.status, "used": used,
            "stops": len(with_it.stops),
            "extra_min": round((stop_seconds(with_it, settings) - base_s) / 60.0),
            "extra_cost": round(with_it.cost - plan.cost, 2),
            "arrival_pct": None if with_it.arrival_pct is None
            else round(with_it.arrival_pct, 1),
        }
    notes = []
    if avoided:
        notes.append(f"{avoided} charger(s) left out: networks, chargers or access "
                     "you avoid")
    if slow:
        notes.append(f"{slow} charger(s) below your {settings.min_charger_kw:.0f} kW "
                     "minimum left out")
    if not found:
        notes.append("No charger found there: try a wider distance.")
    return {"area": area, "within_km": within_km, "near": near, "found": found,
            "notes": notes, "checked": len(seen)}
