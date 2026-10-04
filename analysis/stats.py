"""Core statistics — pure Python, no heavy dependencies.

The LLM must NEVER invent numbers: everything numeric shown to it and on the
dashboard is computed here from the database.
"""
from __future__ import annotations

import math
import statistics as st
from datetime import datetime, timezone


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


def linear_regression_stderr(
    ys: list[float], xs: list[float] | None = None
) -> float | None:
    """Standard error of the slope from an ordinary least-squares fit.

    This is the scale the slope should be judged against: `slope / stderr` is
    how many standard errors the trend sits from zero, so it answers "is this
    slope distinguishable from noise?" without guessing a constant. Returns
    None when it cannot be computed (too few points, no spread in x, or a
    perfectly straight fit, where the residual is zero).
    """
    n = len(ys)
    if n < 3:
        return None
    if xs is None:
        xs = list(range(n))
    if len(xs) != n:
        raise ValueError("xs/ys length mismatch")
    mx, my = st.fmean(xs), st.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if not den:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    resid = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    variance = resid / (n - 2)
    if variance <= 0:
        return None
    return math.sqrt(variance / den)


def classify_trend(
    slope: float | None, noise_std: float | None, threshold: float,
    slope_stderr: float | None = None
) -> str:
    """Classify a series trend: increasing / decreasing / stable / noisy.

    `slope` is per-day change in the metric's unit; `threshold` is the
    per-day change considered meaningful (e.g. 0.1 mV/day for cell delta).

    `slope_stderr` is the standard error of that slope, when known. The slope
    is then called noise when it sits within two standard errors of zero --
    a real test of the fit, and the only one that compares like with like.
    `noise_std` alone cannot do that: it is the spread of the raw values
    (mV per sample) while the slope is mV per day, so the old test divided one
    by an unexplained 30 and called the result a verdict.
    """
    if slope is None:
        return "insufficient_data"
    if slope_stderr is not None:
        # 2 sigma: below this the direction is not established by the data.
        if abs(slope) < 2.0 * slope_stderr:
            return "noisy (no clear trend)"
    elif noise_std is not None and abs(slope) < noise_std / 30.0:
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
    """Convert ISO timestamps to fractional days since the first sample.

    Tolerant by design, because these come from a database that a long-running
    collector has been appending to for months:
      * naive and timezone-aware stamps are normalised to UTC before
        subtracting -- mixing them raises TypeError otherwise;
      * unparseable stamps are dropped rather than aborting the whole analysis;
      * an empty or fully-unparseable input returns [] instead of IndexError.
    Returns [] rather than raising, so callers get "no data" instead of a
    traceback from deep inside a regression.
    """
    parsed: list[datetime] = []
    for t in ts_list:
        try:
            dt = datetime.fromisoformat(t)
        except (TypeError, ValueError):
            continue
        # Treat a naive stamp as UTC so it can be compared with aware ones.
        parsed.append(dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
                      else dt.astimezone(timezone.utc))
    if not parsed:
        return []
    t0 = min(parsed)
    return [(t - t0).total_seconds() / 86400.0 for t in parsed]
