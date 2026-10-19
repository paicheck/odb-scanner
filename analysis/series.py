"""Server-side downsampling for chart responses.

WHY THIS EXISTS
---------------
A single cell-voltage key already has thousands of rows over two weeks; a wide
window over `pack_current` or `lv_voltage_obd` has more. Returning every point
to the browser is what makes the brief's "no loading hundreds of thousands of
points" requirement real: the chart library does not need them, the network
should not carry them, and the collector's raw resolution must still be
available for export and for hand-checking.

The split this module implements:

    chart   -> downsampled to `max_points` (shape preserved)
    table   -> the same window, still downsampled but with exact timestamps
    export  -> the raw rows, untouched, by the caller

Two samplers, because one is not enough:

    lttb        Largest-Triangle-Three-Buckets. Good for smooth signals
                (SoC, temperatures); keeps the visual silhouette.
    min_max     Guarantees the extremes inside every bucket survive. LTTB can
                drop a one-sample spike -- exactly the anomaly a health
                investigation is looking for -- so it is the wrong default for
                voltages and fault hunting.

Both are deterministic: the same input and `max_points` give the same output,
so two people looking at the same report see the same chart.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _seconds(ts_list: list[str]) -> list[float]:
    """Timestamps -> seconds since the first, tolerant of naive stamps."""
    base = None
    out: list[float] = []
    for i, ts in enumerate(ts_list):
        try:
            dt = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            # An unparseable stamp cannot be placed on a time axis. Fall back to
            # the sample index so the series stays ordered and monotone rather
            # than silently collapsing to a single x.
            out.append(float(i))
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if base is None:
            base = dt
        out.append((dt - base).total_seconds())
    return out


def lttb(points: list[tuple[float, float]], n_out: int) -> list[tuple[float, float]]:
    """Largest-Triangle-Three-Buckets downsampling.

    `points` is (x, y) ascending in x. Returns at most `n_out` points, always
    including the first and last. Falls back to the input when there is nothing
    to do.
    """
    n = len(points)
    if n_out >= n or n_out < 3:
        return list(points)
    sampled = [points[0]]
    # Bucket boundaries over the interior; the first and last points are kept
    # separately, which is why the stride divides n-2.
    every = (n - 2) / (n_out - 2)
    a = 0
    for i in range(n_out - 2):
        # Average of the *next* bucket, used as the third triangle vertex.
        start = int((i + 1) * every) + 1
        end = min(int((i + 2) * every) + 1, n)
        if start >= n:
            start = n - 1
        if end <= start:
            end = start + 1
        avg_x = sum(p[0] for p in points[start:end]) / (end - start)
        avg_y = sum(p[1] for p in points[start:end]) / (end - start)
        r_start = int(i * every) + 1
        r_end = int((i + 1) * every) + 1
        r_end = min(r_end, n - 1)
        if r_end <= r_start:
            r_end = r_start + 1
        ax, ay = points[a]
        best_area = -1.0
        best = r_start
        for j in range(r_start, r_end):
            px, py = points[j]
            area = abs((ax - avg_x) * (py - ay) - (ax - px) * (avg_y - ay))
            if area > best_area:
                best_area = area
                best = j
        sampled.append(points[best])
        a = best
    sampled.append(points[-1])
    return sampled


def min_max(points: list[tuple[float, float]], n_out: int
            ) -> list[tuple[float, float]]:
    """Bucketed min/max sampler: every bucket contributes its lowest and
    highest sample, so no spike is lost.

    Returns at most roughly `2 * buckets` points, in x order.
    """
    n = len(points)
    if n_out >= n or n_out < 4:
        return list(points)
    # Each bucket yields two points, so target half as many buckets.
    buckets = max(1, n_out // 2)
    out: list[tuple[float, float]] = []
    last_index = -1
    for b in range(buckets):
        start = int(b * n / buckets)
        end = int((b + 1) * n / buckets)
        if end <= start:
            continue
        lo = min(range(start, end), key=lambda j: points[j][1])
        hi = max(range(start, end), key=lambda j: points[j][1])
        for j in sorted({lo, hi}):
            if j != last_index:
                out.append(points[j])
                last_index = j
    if not out or out[-1] != points[-1]:
        out.append(points[-1])
    return out


def downsample(points: list[tuple[float, float]], max_points: int | None,
               method: str = "min_max") -> list[tuple[float, float]]:
    """Dispatch to a sampler. `max_points` None or <= 0 means "no limit"."""
    if max_points is None or max_points <= 0 or len(points) <= max_points:
        return list(points)
    if method == "lttb":
        return lttb(points, max_points)
    return min_max(points, max_points)


def numeric_payload(ts_list: list[str], values: list, *,
                    max_points: int | None = 1500,
                    method: str = "min_max") -> dict:
    """Chart payload for a numeric series.

    Rows where the value is not numeric are skipped rather than coerced: a
    text row (`gear` = "P") has no place on a numeric axis, and turning it into
    0 would draw a reading that was never made.
    """
    pairs: list[tuple[float, float]] = []
    for ts, value in zip(ts_list, values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        pairs.append((ts, float(value)))  # type: ignore[arg-type]
    xs = _seconds([p[0] for p in pairs])
    points = list(zip(xs, [p[1] for p in pairs]))
    kept = downsample(points, max_points, method)
    kept_idx = _map_back(points, kept)
    return {
        "ts": [pairs[i][0] for i in kept_idx],
        "value": [pairs[i][1] for i in kept_idx],
        "raw_points": len(pairs),
        "returned_points": len(kept_idx),
        "downsampled": len(kept_idx) < len(pairs),
        "method": method if len(kept_idx) < len(pairs) else "none",
    }


def _map_back(points: list[tuple[float, float]],
              kept: list[tuple[float, float]]) -> list[int]:
    """Indices into `points` for the kept points.

    Both samplers only ever return points that were in the input, in order, so
    a monotone walk matches them. This avoids rebuilding the (x, y) pairs with
    float equality and keeps duplicate x values unambiguous.
    """
    out: list[int] = []
    j = 0
    for kp in kept:
        while j < len(points) and points[j] != kp:
            j += 1
        if j < len(points):
            out.append(j)
            j += 1
    return out


def transitions(ts_list: list[str], values: list) -> dict:
    """Step series for a text / code signal: only samples where the value
    *changed* are returned, each with the timestamp it changed at.

    A gear selector or a charge-mode code is not a quantity to average; the
    meaningful content is when it changed. The first sample is always included.
    """
    out_ts: list[str] = []
    out_val: list = []
    previous = object()
    for ts, value in zip(ts_list, values):
        if value != previous:
            out_ts.append(ts)
            out_val.append(value)
            previous = value
    return {"ts": out_ts, "value": out_val,
            "raw_points": len(values), "returned_points": len(out_ts),
            "downsampled": len(out_ts) < len(values), "method": "transitions"}
