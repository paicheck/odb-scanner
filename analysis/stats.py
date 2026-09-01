"""Core statistics — pure Python, no heavy dependencies.

The LLM must NEVER invent numbers: everything numeric shown to it and on the
dashboard is computed here from the database.
"""
from __future__ import annotations

import math
import statistics as st
from datetime import datetime


def mean(xs: list[float]) -> float | None:
    return st.fmean(xs) if xs else None


def stdev(xs: list[float]) -> float | None:
    return st.stdev(xs) if len(xs) > 1 else None


def min_max(xs: list[float]) -> tuple[float | None, float | None]:
    return (min(xs), max(xs)) if xs else (None, None)


def rolling_mean(xs: list[float], window: int) -> list[float]:
    out = []
    for i in range(len(xs)):
        chunk = xs[max(0, i - window + 1) : i + 1]
        out.append(st.fmean(chunk))
    return out


def linear_regression_slope(
    ys: list[float], xs: list[float] | None = None
) -> float | None:
    """Least-squares slope. xs default: sample index."""
    n = len(ys)
    if n < 3:
        return None
    if xs is None:
        xs = list(range(n))
    if len(xs) != n:
        raise ValueError("xs/ys length mismatch")
    mx, my = st.fmean(xs), st.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    sx, sy = st.stdev(xs), st.stdev(ys)
    if not sx or not sy:
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)
    return cov / (sx * sy)


def zscore(value: float, mean_: float | None, std_: float | None) -> float | None:
    if mean_ is None or std_ is None or std_ == 0:
        return None
    return (value - mean_) / std_


def classify_trend(
    slope: float | None, noise_std: float | None, threshold: float
) -> str:
    """Classify a series trend: increasing / decreasing / stable / noisy.

    `slope` is per-day change in the metric's unit; `threshold` is the
    per-day change considered meaningful (e.g. 0.1 mV/day for cell delta).
    """
    if slope is None:
        return "insufficient_data"
    if noise_std is not None and abs(slope) < noise_std / 30.0:
        return "noisy (no clear trend)"
    if slope > threshold:
        return "increasing"
    if slope < -threshold:
        return "decreasing"
    return "stable"


def rate_of_change(xs: list[float], per_index: float = 1.0) -> float | None:
    """Average change per step between first and last sample."""
    if len(xs) < 2:
        return None
    return (xs[-1] - xs[0]) / (per_index * (len(xs) - 1))


def ts_to_days(ts_list: list[str]) -> list[float]:
    """Convert ISO timestamps to fractional days since the first sample."""
    times = [datetime.fromisoformat(t) for t in ts_list]
    t0 = times[0]
    return [(t - t0).total_seconds() / 86400.0 for t in times]
