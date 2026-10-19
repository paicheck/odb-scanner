"""Vehicle-specific robust baselines.

WHY THIS EXISTS
---------------
`analysis/anomaly.py` compares every sample against a single mean and standard
deviation of the whole window. That is the wrong tool for a car:

  * a slow, real drift (a pack warming, an SoC falling) sits *inside* its own
    baseline, so the baseline moves with the thing it is supposed to detect;
  * one carried-forward duplicate or one mis-scaled row inflates the standard
    deviation, and then nothing is ever an outlier again;
  * a gap in collection is treated as if the series were continuous.

This module builds a baseline that is robust to all three: median and median
absolute deviation instead of mean/std, computed inside *contiguous* segments
(the series is split wherever the gap between samples exceeds a threshold, so a
trend never spans a comms outage, a charging transition or a fault event), and
persisted with its window and sample size so a report can be reproduced exactly.

A baseline is a statement about how this car usually behaves, so it is stored
per (vehicle, key, window) and never silently reused across windows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from analysis import stats

# Scale factor making the MAD a consistent estimator of sigma for normal data.
MAD_TO_SIGMA = 1.4826

# A gap larger than this splits a series. One hour is far above the poll
# interval (5 s) and the carry-forward window (900 s), so only a genuine
# collection break -- an adapter failure, a parked car, a charging session --
# ends a segment.
DEFAULT_MAX_GAP_S = 3600.0

# Robust z beyond which a sample is called an outlier.
DEFAULT_Z = 4.5


def _parse(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def segments(ts_list: list[str], max_gap_s: float = DEFAULT_MAX_GAP_S
             ) -> list[tuple[int, int]]:
    """Index spans [start, end) of contiguous samples.

    A sample whose timestamp cannot be read is treated as adjacent to its
    neighbour rather than as a gap: dropping it would silently change the
    sample size the baseline rests on, and the caller can see the count.
    """
    if not ts_list:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    previous = _parse(ts_list[0])
    for i in range(1, len(ts_list)):
        current = _parse(ts_list[i])
        if previous is not None and current is not None:
            if (current - previous).total_seconds() > max_gap_s:
                spans.append((start, i))
                start = i
        previous = current if current is not None else previous
    spans.append((start, len(ts_list)))
    return spans


@dataclass(frozen=True)
class RobustBaseline:
    n: int
    median: float | None
    mad: float | None
    sigma: float | None        # MAD scaled to be comparable to a std deviation
    mean: float | None
    std: float | None
    p05: float | None
    p95: float | None

    def as_dict(self) -> dict:
        return {
            "n": self.n, "median": self.median, "mad": self.mad,
            "sigma": self.sigma, "mean": self.mean, "std": self.std,
            "p05": self.p05, "p95": self.p95,
        }

    def robust_z(self, value: float) -> float | None:
        if self.median is None or self.sigma is None or self.sigma == 0:
            return None
        return (value - self.median) / self.sigma


def _percentile(sorted_xs: list[float], q: float) -> float | None:
    if not sorted_xs:
        return None
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = q * (len(sorted_xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = pos - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def robust_stats(values: list[float]) -> RobustBaseline:
    xs = [float(v) for v in values if v is not None]
    n = len(xs)
    if n == 0:
        return RobustBaseline(0, None, None, None, None, None, None, None)
    ordered = sorted(xs)
    median = _percentile(ordered, 0.5)
    mad = None
    if median is not None:
        mad = _percentile(sorted(abs(x - median) for x in xs), 0.5)
    sigma = None
    if mad:
        sigma = mad * MAD_TO_SIGMA
    elif median is not None:
        # A perfectly flat series (or one with a single spike) has MAD 0, which
        # would make every robust z undefined and hide exactly the lone spike
        # worth finding. The mean absolute deviation is still zero for a truly
        # constant series and non-zero as soon as anything departs, so it is a
        # safe fallback scale (scaled to be comparable to a std deviation).
        mean_abs = stats.mean([abs(x - median) for x in xs])
        sigma = mean_abs * 1.2533 if mean_abs else None
    mean = stats.mean(xs)
    std = stats.stdev(xs)
    return RobustBaseline(
        n=n, median=median, mad=mad, sigma=sigma, mean=mean, std=std,
        p05=_percentile(ordered, 0.05), p95=_percentile(ordered, 0.95))


@dataclass(frozen=True)
class Finding:
    ts: str
    value: float
    method: str          # robust-z | rate-of-change
    score: float
    direction: str       # high | low
    baseline: dict
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "ts": self.ts, "value": self.value, "method": self.method,
            "score": self.score, "direction": self.direction,
            "baseline": self.baseline, "reason": self.reason,
        }


def detect_outliers(ts_list: list[str], values: list[float], *,
                    max_gap_s: float = DEFAULT_MAX_GAP_S,
                    z_threshold: float = DEFAULT_Z,
                    min_samples: int = 8) -> tuple[RobustBaseline, list[Finding]]:
    """Layered robust detection inside contiguous segments.

    Returns the whole-series baseline (for reporting) plus one finding per
    outlier. An outlier is never reported when its segment holds fewer than
    `min_samples` values: a "baseline" of three points has no right to call the
    fourth one abnormal, and the brief requires insufficient evidence to be
    reported as such rather than guessed around.
    """
    baseline = robust_stats(values)
    if not ts_list:
        return baseline, []
    findings: list[Finding] = []
    for start, end in segments(ts_list, max_gap_s):
        seg_values = [values[i] for i in range(start, end)
                      if values[i] is not None]
        if len(seg_values) < min_samples:
            continue
        seg_base = robust_stats(seg_values)
        if seg_base.median is None:
            continue
        for i in range(start, end):
            v = values[i]
            if v is None:
                continue
            z = seg_base.robust_z(v)
            if z is not None and abs(z) >= z_threshold:
                findings.append(Finding(
                    ts=ts_list[i], value=float(v), method="robust-z",
                    score=round(z, 3), direction="high" if z > 0 else "low",
                    baseline=seg_base.as_dict(),
                    reason=f"robust z {z:.1f} vs segment median "
                           f"{seg_base.median:.4g} (MAD {seg_base.mad:.4g}, "
                           f"n={seg_base.n})"))
    return baseline, findings


def detect_rate_of_change(ts_list: list[str], values: list[float], *,
                          max_gap_s: float = DEFAULT_MAX_GAP_S,
                          min_samples: int = 8,
                          z_threshold: float = DEFAULT_Z) -> list[Finding]:
    """Second layer: flag steps that are large compared with the series' own
    typical step, catching a jump that is still inside the robust value band
    (e.g. a value that doubles but stays plausible).

    Steps are only taken *within* a segment, so the jump that starts a new
    segment (a real gap) is never itself the anomaly.
    """
    steps = []  # (index of the later sample, delta, dt_s)
    for start, end in segments(ts_list, max_gap_s):
        for i in range(start + 1, end):
            if values[i] is None or values[i - 1] is None:
                continue
            t0, t1 = _parse(ts_list[i - 1]), _parse(ts_list[i])
            dt = (t1 - t0).total_seconds() if t0 and t1 else 1.0
            if dt <= 0:
                continue
            steps.append((i, float(values[i]) - float(values[i - 1]), dt))
    if len(steps) < min_samples:
        return []
    rates = [delta / dt for _, delta, dt in steps]
    base = robust_stats(rates)
    findings: list[Finding] = []
    for (i, delta, dt), rate in zip(steps, rates):
        z = base.robust_z(rate)
        if z is not None and abs(z) >= z_threshold:
            findings.append(Finding(
                ts=ts_list[i], value=float(values[i]), method="rate-of-change",
                score=round(z, 3), direction="high" if rate > 0 else "low",
                baseline=base.as_dict(),
                reason=f"rate {rate:.4g}/s is {z:.1f} robust z from the typical "
                       f"step ({delta:+.4g} over {dt:.0f}s)"))
    return findings
