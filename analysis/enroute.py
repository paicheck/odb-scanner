"""En route: is the plan still good, from the charge the car reports now?

Pure functions, no I/O. The same rules and thresholds as the phone, which
runs them every minute on a trip (android/.../route/Replan.kt):

* expected: what the plan's charge profile says at this point;
* ratio: what this drive has really used since the start against what the
  plan expected it to use by here -- measured only before the first stop,
  with no charging since, and once at least MIN_USED_PCT has gone (less is
  noise); clamped to 0.75..1.5 like the phone;
* projected: the charge at the next stop (or the destination) if the rest of
  the way costs `ratio` times the plan. A higher rate widens the gap with
  distance; the gap is not simply carried forward.

Levels, worst first:

    critical  projected below the reserve before the next stop: find a
              charger sooner (the plan's own alternatives within reach are
              listed -- whether they are free is not known)
    adjust    projected within ADJUST_MARGIN_PCT above the reserve: re-plan
    warning   below plan by DRIFT_PCT or more, but the stop still works
    normal    on plan (or above it; then a stop may no longer be needed)

A plan aims to arrive near the reserve, so critical and adjust also need a
projected shortfall against the plan of at least DRIFT_PCT: being on a
tight plan is not a problem.

Nothing here changes a route. It says what it sees and recommends; the
driver, or a re-plan the driver is told about, changes the route.
"""
from __future__ import annotations

# The phone's Replan.kt
DRIFT_PCT = 3.0
ABOVE_PCT = 5.0
MIN_USED_PCT = 3.0
AT_STOP_KM = 3.0
READING_MAX_AGE_S = 15 * 60
RATIO_MIN, RATIO_MAX = 0.75, 1.5
ADJUST_MARGIN_PCT = 5.0
LEVELS = ("normal", "warning", "adjust", "critical")


def expected_pct(profile: list[list[float]], km: float) -> float | None:
    """The plan's charge at `km`; a stop at exactly `km` counts as left
    (Replan.expectedPct)."""
    if not profile:
        return None
    i = -1
    for k, (at, _) in enumerate(profile):
        if at <= km:
            i = k
    if i < 0:
        return profile[0][1]
    if i == len(profile) - 1:
        return profile[-1][1]
    (k0, p0), (k1, p1) = profile[i], profile[i + 1]
    if k1 <= k0:
        return p0
    return p0 + (p1 - p0) * (km - k0) / (k1 - k0)


def _arrive_profile(profile, km: float) -> float | None:
    """The plan's charge arriving at `km` -- before any charging step there."""
    before = [p for at, p in profile if at <= km + 1e-9]
    if not before:
        return None
    same = [p for at, p in profile if abs(at - km) < 1e-9]
    return same[0] if same else expected_pct(profile, km)


def assess(plan: dict, km_along: float, actual_pct: float,
           reading_age_s: float | None = 0.0, reserve_pct: float = 10.0,
           charged_since_plan: bool = False) -> dict:
    """Judge the plan at `km_along` with the car at `actual_pct`.

    `plan` is routing.planner.Plan.as_dict(): profile, stops (km,
    arrive_pct, alternatives), distance_km, start_pct, arrival_pct.
    """
    profile = plan.get("profile") or []
    stops = plan.get("stops") or []
    expected = expected_pct(profile, km_along)
    out = {"km_along": round(km_along, 1), "actual_pct": round(actual_pct, 1),
           "expected_pct": None if expected is None else round(expected, 1),
           "reserve_pct": reserve_pct, "replan": False, "alternatives": [],
           "note": ("Charger availability is never known here: check before "
                    "relying on one.")}
    if expected is None:
        return out | {"level": "normal", "action": "No plan profile to compare with."}
    if reading_age_s is None or reading_age_s > READING_MAX_AGE_S:
        return out | {"level": "normal", "stale_reading": True,
                      "action": "Waiting for a fresh battery reading; nothing judged."}
    at_stop = next((i for i, s in enumerate(stops)
                    if abs(s["km"] - km_along) <= AT_STOP_KM), None)
    if at_stop is not None:
        return out | {"level": "normal", "at_stop": at_stop,
                      "action": f"At charging stop {at_stop + 1}: the plan resumes "
                                "once charging starts."}

    first_stop = stops[0]["km"] if stops else None
    used_plan = plan.get("start_pct", expected) - expected
    used_real = plan.get("start_pct", expected) - actual_pct
    ratio = 1.0
    if (not charged_since_plan and (first_stop is None or km_along < first_stop - 1.0)
            and used_plan >= MIN_USED_PCT and used_real > 0):
        ratio = min(RATIO_MAX, max(RATIO_MIN, used_real / used_plan))

    nxt = next((s for s in stops if s["km"] > km_along), None)
    target_km = nxt["km"] if nxt else plan.get("distance_km", km_along)
    target_pct = _arrive_profile(profile, target_km)
    if target_pct is None:
        target_pct = expected
    projected = actual_pct - (expected - target_pct) * ratio
    deviation = actual_pct - expected
    where = f"charging stop {stops.index(nxt) + 1} ({nxt['name']})" if nxt \
        else "the destination"
    out |= {"deviation_pct": round(deviation, 1), "ratio": round(ratio, 2),
            "target": where, "target_km": round(target_km, 1),
            "planned_target_pct": round(target_pct, 1),
            "projected_target_pct": round(projected, 1)}

    # How far the charge goes before the reserve at this drive's rate, for
    # chargers the plan already knows about.
    per_km = ((expected - target_pct) / (target_km - km_along)) * ratio \
        if target_km > km_along and expected > target_pct else None
    reach_km = (actual_pct - reserve_pct) / per_km if per_km else None
    if reach_km is not None:
        out["reach_km"] = round(max(0.0, reach_km), 1)

    # The plan already aims near the reserve; only a real shortfall against it
    # (DRIFT_PCT, the phone's threshold) moves the level.
    shortfall = target_pct - projected
    out["shortfall_pct"] = round(shortfall, 1)
    if projected < reserve_pct and shortfall >= DRIFT_PCT:
        within = {}
        if reach_km is not None:
            for s in stops:
                for a in [s, *(s.get("alternatives") or [])]:
                    if a is not nxt and km_along < a["km"] <= km_along + reach_km:
                        within.setdefault(a.get("id") or a["name"], a)
        out["alternatives"] = [{"name": a["name"], "km": a["km"],
                                "operator": a.get("operator", "")}
                               for a in sorted(within.values(), key=lambda a: a["km"])]
        known = ("Chargers on this plan within reach are listed." if out["alternatives"]
                 else "No charger on this plan is within reach: re-plan to search "
                      "for one.")
        return out | {"level": "critical", "replan": True, "action": (
            f"At this rate the charge falls below the {reserve_pct:.0f} % reserve before "
            f"{where} ({projected:.0f} % there). Charge sooner: re-plan from here. "
            + known)}
    if projected < reserve_pct + ADJUST_MARGIN_PCT and shortfall >= DRIFT_PCT:
        return out | {"level": "adjust", "replan": True, "action": (
            f"{where[0].upper() + where[1:]} is getting tight: about {projected:.0f} % "
            f"there against {target_pct:.0f} % planned. Re-plan from here.")}
    if deviation <= -DRIFT_PCT or shortfall >= DRIFT_PCT:
        return out | {"level": "warning", "action": (
            f"Charge {actual_pct:.0f} %, {-deviation:.0f} below plan; {where} still "
            f"works at about {projected:.0f} %.")}
    if deviation >= ABOVE_PCT:
        return out | {"level": "normal", "replan": True, "action": (
            f"Charge {actual_pct:.0f} %, {deviation:.0f} above plan: a stop may no "
            "longer be needed. Re-plan to check.")}
    return out | {"level": "normal", "action": "On plan."}
