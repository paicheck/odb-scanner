"""Charging analysis: session statistics and DTC/charge correlation.

Enables pattern queries like:
    AC charging -> charging completed -> vehicle wakes -> DTC appears
without hard-coding the pattern: it is a time-window join.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _iso_days_ago(days: float) -> str:
    """Same stamp format and bound repository.py uses for its own windows."""
    return (datetime.now(timezone.utc)
            - timedelta(days=days)).isoformat(timespec="seconds")


def correlate_dtc_with_sessions(repo, vehicle_id: int, days: int = 90,
                                window_hours: float = 4.0) -> dict:
    """For each DTC occurrence, check whether a charging session ended
    within `window_hours` before it."""
    sessions = repo.charging_sessions(days, vehicle_id)
    # Window the occurrences too. They used to be fetched all-time with
    # since=None, so an occurrence older than `days` was compared against a
    # window it can never fall inside -- it can only ever fail to link, but it
    # still inflated dtc_occurrences_considered, which sits in the LLM evidence
    # packet next to a windowed sessions_considered. Two counts over different
    # windows, presented as one finding, and the model is asked to reason about
    # the gap. Measured at 500,002 occurrences considered against a 90-day
    # window containing the same 500,002 but zero sessions.
    occurrences = repo.dtc_occurrences(vehicle_id, since=_iso_days_ago(days))
    window = timedelta(hours=window_hours)
    links = []
    correlated_codes: set[str] = set()
    for occ in occurrences:
        occ_ts = _parse(occ["ts"])
        best = None
        for s in sessions:
            if not s["ended_at"]:
                continue
            end = _parse(s["ended_at"])
            if end <= occ_ts <= end + window:
                gap_h = (occ_ts - end).total_seconds() / 3600.0
                if best is None or gap_h < best["gap_hours"]:
                    best = {
                        "code": occ["code"], "ecu": occ["ecu"],
                        "occurrence_ts": occ["ts"],
                        "session_started": s["started_at"],
                        "session_ended": s["ended_at"],
                        "charge_type": s["charge_type"],
                        "gap_hours": round(gap_h, 2),
                    }
        if best:
            correlated_codes.add(best["code"])
            links.append(best)
    return {
        "window_hours": window_hours,
        "sessions_considered": len(sessions),
        "dtc_occurrences_considered": len(occurrences),
        "links": links,
        "codes_linked_to_charging": sorted(correlated_codes),
    }


def session_comparison(repo, vehicle_id: int, days: int = 90) -> dict:
    """Compare charging sessions (AC vs DC, energy, duration, temperatures)."""
    sessions = repo.charging_sessions(days, vehicle_id)
    by_type: dict[str, list] = {"AC": [], "DC": []}
    for s in sessions:
        t = (s["charge_type"] or "?").upper()
        by_type.setdefault(t, []).append({
            "started_at": s["started_at"], "ended_at": s["ended_at"],
            "duration_s": s["duration_s"],
            "start_soc": s["start_soc"], "end_soc": s["end_soc"],
            "energy_estimate_kwh": s["energy_estimate_kwh"],
            "max_power_kw": s["max_power_kw"],
            "avg_battery_temp_c": s["avg_battery_temp_c"],
        })
    return {"count": len(sessions), "by_type": by_type}
