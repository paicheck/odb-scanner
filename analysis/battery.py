"""Battery analysis: cell-voltage imbalance trend (the headline metric)."""
from __future__ import annotations

from analysis import stats

# A per-day slope is only meaningful if the samples actually span a meaningful
# amount of time. Polling every 5 s for an hour yields x-values around 1e-5 days,
# so a 2 mV wobble divided by that produces slopes in the tens of thousands of
# mV/day -- arithmetically correct, physically meaningless. Refuse to report a
# slope below this span rather than dressing noise up as a trend.
MIN_TREND_SPAN_DAYS = 1.0


def format_span(span_days: float) -> str:
    """Render a day-denominated span at a resolution a human can read.

    `round(span_days, 4)` turns a 2-second span into "0.0 d", which tells the
    user nothing; this picks the unit that actually carries the information.
    """
    if span_days < 1 / 24:
        return f"{span_days * 86400:.0f} s"
    if span_days < 1:
        return f"{span_days * 24:.1f} h"
    return f"{span_days:.1f} d"


def cell_delta_trend(repo, days: int = 30, vehicle_id: int | None = None,
                     min_span_days: float = MIN_TREND_SPAN_DAYS) -> dict:
    """Analyse the (max cell - min cell) delta over the window.

    Example output:
        {"current_mv": 38, "mean_mv": 24, "std_mv": 6, "trend": "increasing",
         "slope_mv_per_day": 0.47, "samples": 112, "span_days": 27.4, }

    ``trend`` is "insufficient_span" (and ``slope_mv_per_day`` is None) when the
    samples cover less than ``min_span_days`` -- typically a brand-new database
    that has only been collected for a few minutes. The descriptive statistics
    are still reported, because they are valid; only the extrapolation is not.
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
    if len(day_xs) != len(deltas):
        # ts_to_days() skips unparseable timestamps, so a partial list would no
        # longer line up with the deltas and the regression would pair the
        # wrong sample with the wrong time. Without trustworthy timestamps there
        # is no trend to report.
        return {"status": "insufficient_data", "samples": len(deltas),
                "window_days": days,
                "detail": "unparseable timestamps in window"}
    span_days = (day_xs[-1] - day_xs[0]) if day_xs else 0.0

    if span_days < min_span_days:
        slope, trend = None, "insufficient_span"
    else:
        slope = stats.linear_regression_slope(deltas, day_xs)  # mV per day
        trend = stats.classify_trend(slope, std, threshold=0.1)
    return {
        "status": "ok",
        "window_days": days,
        "samples": len(deltas),
        "current_mv": round(deltas[-1], 1),
        "mean_mv": round(mean, 1) if mean is not None else None,
        "std_mv": round(std, 1) if std is not None else None,
        "min_mv": round(min(deltas), 1),
        "max_mv": round(max(deltas), 1),
        "span_days": round(span_days, 6),
        "span_text": format_span(span_days),
        "min_span_days": min_span_days,
        "slope_mv_per_day": round(slope, 3) if slope is not None else None,
        "trend": trend,
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
