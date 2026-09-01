"""Anomaly detection: z-score outliers against a trailing baseline."""
from __future__ import annotations

from analysis import stats


def detect_series_anomalies(ts_list: list[str], values: list[float],
                            z_threshold: float = 3.0):
    """Return (ts, value, z) tuples whose z-score exceeds the threshold.

    Baseline uses ALL samples in the series (simple, transparent approach;
    documented as such — not a sophisticated online detector).
    """
    mean = stats.mean(values)
    std = stats.stdev(values)
    if mean is None or std in (None, 0):
        return []
    out = []
    for ts, v in zip(ts_list, values):
        z = stats.zscore(v, mean, std)
        if z is not None and abs(z) >= z_threshold:
            out.append((ts, v, round(z, 2)))
    return out


def scan_metric(repo, vehicle_id: int, metric: str, description: str,
                days: int = 30, z_threshold: float = 3.0,
                battery_field: bool = True) -> int:
    """Detect and persist anomalies for one metric. Returns count found."""
    hist = repo.battery_history(days, vehicle_id)
    if battery_field:
        ts_list = [r["ts"] for r in hist if r[metric] is not None]
        values = [r[metric] for r in hist if r[metric] is not None]
    else:
        rows = repo.measurement_series(metric, _since(days), vehicle_id)
        ts_list = [r["ts"] for r in rows]
        values = [r["value"] for r in rows]
    found = detect_series_anomalies(ts_list, values, z_threshold)
    for ts, v, z in found:
        repo.add_anomaly(
            vehicle_id, ts, metric, v,
            stats.mean(values), stats.stdev(values), z,
            "high" if z > 0 else "low", description,
        )
    return len(found)


def _since(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
