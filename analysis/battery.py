"""Battery analysis: cell-voltage imbalance trend (the headline metric)."""
from __future__ import annotations

from analysis import stats


def cell_delta_trend(repo, days: int = 30, vehicle_id: int | None = None) -> dict:
    """Analyse the (max cell - min cell) delta over the window.

    Example output:
        {"current_mv": 38, "mean_mv": 24, "std_mv": 6, "trend": "increasing",
         "slope_mv_per_day": 0.47, "samples": 112, "first_seen": ..., }
    """
    hist = repo.battery_history(days, vehicle_id)
    rows = [(r["ts"], r["cell_delta_mv"]) for r in hist
            if r["cell_delta_mv"] is not None]
    if len(rows) < 3:
        return {"status": "insufficient_data", "samples": len(rows),
                "window_days": days}
    ts_list = [t for t, _ in rows]
    deltas = [d for _, d in rows]
    mean = stats.mean(deltas)
    std = stats.stdev(deltas)
    day_xs = stats.ts_to_days(ts_list)
    slope = stats.linear_regression_slope(deltas, day_xs)  # mV per day
    return {
        "status": "ok",
        "window_days": days,
        "samples": len(deltas),
        "current_mv": round(deltas[-1], 1),
        "mean_mv": round(mean, 1) if mean is not None else None,
        "std_mv": round(std, 1) if std is not None else None,
        "min_mv": round(min(deltas), 1),
        "max_mv": round(max(deltas), 1),
        "slope_mv_per_day": round(slope, 3) if slope is not None else None,
        "trend": stats.classify_trend(slope, std, threshold=0.1),
        "first_seen": ts_list[0],
        "last_seen": ts_list[-1],
    }


def battery_overview(repo, days: int = 30, vehicle_id: int | None = None) -> dict:
    """Aggregate battery health statistics for dashboard / LLM context."""
    hist = repo.battery_history(days, vehicle_id)
    socs = [r["soc_normal_pct"] or r["soc_abs_pct"] for r in hist
            if (r["soc_normal_pct"] or r["soc_abs_pct"]) is not None]
    volts = [r["pack_voltage_v"] for r in hist if r["pack_voltage_v"] is not None]
    sohs = [r["soh_pct"] for r in hist if r["soh_pct"] is not None]
    latest = repo.latest_measurements(vehicle_id) if vehicle_id else {}
    return {
        "window_days": days,
        "samples": len(hist),
        "soc_latest_pct": socs[-1] if socs else None,
        "pack_voltage_latest_v": volts[-1] if volts else None,
        "soh_latest_pct": sohs[-1] if sohs else None,
        "soc_range_pct": [min(socs), max(socs)] if len(socs) > 1 else None,
        "cell_delta": cell_delta_trend(repo, days, vehicle_id),
        "provenance_note": (
            "soh_pct is ESTIMATED (CAC / nominal capacity), not a vehicle report"
            if sohs else None
        ),
        "latest_raw": latest,
    }
