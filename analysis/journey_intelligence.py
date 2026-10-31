"""Journey intelligence: classify drives and summarise efficiency.

Read-only aggregation over analysis/trips.split_drives.
"""
from __future__ import annotations

from analysis.trips import split_drives


def _series(repo, vid, key, lead):
    from routing.car import _series as _s
    return _s(repo, vid, key, lead)


def journey_summary(repo, vehicle_id: int, days: int = 30) -> dict:
    since_days = days + 10 / 1440.0
    drives = [d for d in split_drives(
        _series(repo, vehicle_id, "odometer_km", since_days),
        _series(repo, vehicle_id, "hv_energy_content", since_days))]
    buckets = {"short": [], "commute": [], "long": []}
    for d in drives:
        km = d.distance_km
        if km < 10:
            buckets["short"].append(d)
        elif km < 80:
            buckets["commute"].append(d)
        else:
            buckets["long"].append(d)
    out = {}
    for name, ds in buckets.items():
        rates = [d.kwh_per_100km for d in ds if d.kwh_per_100km is not None]
        out[name] = {
            "drives": len(ds),
            "km": round(sum(d.distance_km for d in ds), 1),
            "avg_kwh_per_100km": (sum(rates) / len(rates)) if rates else None,
        }
    out["total_drives"] = len(drives)
    out["total_km"] = round(sum(d.distance_km for d in drives), 1)
    return out
