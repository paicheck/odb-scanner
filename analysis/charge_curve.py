"""This car's own DC charging curve, from its recorded charging sessions.

The planner times every stop on a charging curve: kW the car accepts at each
state of charge, capped by the charger (routing/planner.py). It starts from
the published ID.3 58 kWh curve. This measures the real one, where the
sessions show it, and keeps the published value everywhere else.

What a session can and cannot say. The power drawn at a given SoC is the
lesser of what the car accepts and what the charger gives. A session on a
50 kW charger reads 50 kW at 20 % whether the car could take 50 or 110, so
a low reading is not evidence that the car is slow. So, per session:

  * where the power is clearly below that session's own peak
    (CAR_LIMITED_SHARE), the charger could have given more: the car was
    tapering, and the reading *is* the car's limit there;
  * where it is at the session's peak, it may be the charger's limit: it
    only says the car takes at least that much.

At each point of the published curve's grid, the measured value is the
median of the car-limited readings of at least MIN_SESSIONS_AT_POINT
sessions (each session's median within ±WINDOW_PCT of the point), never
below the highest lower bound seen there. With fewer sessions, the published
value stays. With no point measured, there is no measured curve at all.

Readings: `charging_samples` (pack voltage x current, the display SoC, the
pack temperature) of completed DC sessions. Pack current is a provisional
decoder; its sign is ignored. A cold pack charges slower, and the sessions'
mean pack temperature at each point is reported so a winter curve is not
mistaken for the car's best. Pure functions; repo access only in
`measured_curve_for`.
"""
from __future__ import annotations

from statistics import median

WINDOW_PCT = 5.0
CAR_LIMITED_SHARE = 0.9      # below 90 % of the session's peak: the car's taper
MIN_SESSIONS_AT_POINT = 2
MIN_SAMPLES_IN_WINDOW = 2
SESSION_PEAK_Q = 0.95        # a session's peak: its 95th percentile, not one spike
MAX_PLAUSIBLE_KW = 200.0


def _session_points(samples, grid) -> tuple[dict, dict]:
    """(car-limited kW, at-least kW) at each grid SoC for one session:
    samples are (soc %, kW, battery °C or None)."""
    good = [(s, abs(p), t) for s, p, t in samples
            if s is not None and p is not None and 0 < abs(p) <= MAX_PLAUSIBLE_KW]
    if len(good) < MIN_SAMPLES_IN_WINDOW:
        return {}, {}
    powers = sorted(p for _, p, _ in good)
    peak = powers[min(len(powers) - 1, int(SESSION_PEAK_Q * len(powers)))]
    limited, at_least = {}, {}
    for g in grid:
        near = [(p, t) for s, p, t in good if abs(s - g) <= WINDOW_PCT]
        if len(near) < MIN_SAMPLES_IN_WINDOW:
            continue
        kw = median(p for p, _ in near)
        temps = [t for _, t in near if t is not None]
        temp = sum(temps) / len(temps) if temps else None
        if kw < CAR_LIMITED_SHARE * peak:
            limited[g] = (kw, temp)
        else:
            at_least[g] = (kw, temp)
    return limited, at_least


def measure(sessions, published) -> dict | None:
    """sessions: [[(soc, kW, battery °C), ...], ...], one list per DC
    session. Returns {"curve": [(soc, kW)] -- measured where it could be,
    published elsewhere --, "points": [{soc, kw, published_kw, sessions,
    temp_c, measured}], "sessions": n, "basis": text}, or None when no point
    could be measured."""
    grid = [s for s, _ in published]
    limited: dict = {g: [] for g in grid}
    floors: dict = {g: [] for g in grid}
    used = 0
    for samples in sessions:
        lim, low = _session_points(samples, grid)
        if lim or low:
            used += 1
        for g, v in lim.items():
            limited[g].append(v)
        for g, v in low.items():
            floors[g].append(v)
    points, curve, measured = [], [], 0
    for g, pub in published:
        obs = limited[g]
        floor = max((kw for kw, _ in floors[g]), default=0.0)
        if len(obs) >= MIN_SESSIONS_AT_POINT:
            kw = max(floor, median(kw for kw, _ in obs))
            temps = [t for _, t in obs if t is not None]
            points.append({"soc": g, "kw": round(kw, 1), "published_kw": pub,
                           "sessions": len(obs), "measured": True,
                           "temp_c": (round(sum(temps) / len(temps), 1)
                                      if temps else None)})
            curve.append((g, round(kw, 1)))
            measured += 1
        else:
            points.append({"soc": g, "kw": pub, "published_kw": pub,
                           "sessions": len(obs), "measured": False, "temp_c": None})
            curve.append((g, pub))
    if not measured:
        return None
    return {"curve": curve, "points": points, "sessions": used,
            "measured_points": measured,
            "basis": (f"this car's own DC charging, measured at {measured} of "
                      f"{len(grid)} points from {used} session(s); the published "
                      "ID.3 58 kWh curve elsewhere")}


def measured_curve_for(repo, vehicle_id: int, published, days: int = 365) -> dict | None:
    """measure() over this car's completed DC sessions of the last `days`."""
    sessions = []
    for s in repo.charging_sessions(days=days, vehicle_id=vehicle_id):
        if (s["charge_type"] or "").upper() != "DC":
            continue
        rows = repo.charging_samples(s["id"])
        sessions.append([(r["soc"], r["power_kw"], r["battery_temp_c"]) for r in rows])
    return measure(sessions, published) if sessions else None
