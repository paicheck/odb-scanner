"""Charging session intelligence: AC vs DC, counts, energy, duration, cost.

Read-only aggregation over the already-stored charging_sessions table.
"""
from __future__ import annotations


def charging_summary(repo, vehicle_id: int | None = None, days: int = 90) -> dict:
    sessions = repo.charging_sessions(days=days, vehicle_id=vehicle_id)
    by_type: dict[str, dict] = {}
    total_kwh = 0.0
    total_sessions = 0
    for s in sessions:
        if "charge_type" in s.keys():
            ctype = s["charge_type"]
        elif "type" in s.keys():
            ctype = s["type"]
        else:
            ctype = "unknown"
        d = by_type.setdefault(ctype, {"count": 0, "kwh": 0.0, "minutes": 0.0,
                                       "power_kw_sum": 0.0, "power_n": 0})
        d["count"] += 1
        total_sessions += 1
        try:
            kwh = s["energy_estimate_kwh"]
            if kwh is not None:
                d["kwh"] += float(kwh)
                total_kwh += float(kwh)
        except (KeyError, TypeError, ValueError):
            pass
        try:
            dur = s["duration_s"]
            if dur is not None:
                d["minutes"] += float(dur) / 60.0
        except (KeyError, TypeError, ValueError):
            pass
        try:
            power = s["max_power_kw"]
            if power is not None:
                d["power_kw_sum"] += float(power)
                d["power_n"] += 1
        except (KeyError, TypeError, ValueError):
            pass
    for d in by_type.values():
        d["avg_power_kw"] = (d["power_kw_sum"] / d["power_n"]) if d["power_n"] else None
        del d["power_kw_sum"], d["power_n"]
    return {"days": days, "sessions": total_sessions, "kwh": round(total_kwh, 2),
            "by_type": by_type}
