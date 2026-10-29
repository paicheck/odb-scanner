"""Where the battery's capacity is heading: a trend in the BMS-rated maximum
energy, and when, at that rate, it would reach the warranty line.

The figure is hv_energy_max (DID 0x2AB2, a verified decoder): the energy the
BMS rates the pack to hold when full. It already reflects ageing, but it moves
in steps when the BMS recalibrates and wanders with temperature, so the trend
is fitted to one median per day with Theil-Sen -- the median of the slopes
between every pair of days -- which a recalibration step or an odd day cannot
drag the way it drags a least-squares line. The 95 % interval is Sen's (1968),
from the same slopes.

Nothing is projected from too little: under MIN_SPAN_DAYS of history, or with
fewer than MIN_DAYS days of readings, the answer is "not enough yet", with how
much there is. A projection is the current rate carried forward -- an
extrapolation, said as one, never a prediction of when the battery fails.
"""
from __future__ import annotations

import math
from datetime import date

from database.repository import IMPORTED_PREFIX
from decoders.bms import NOMINAL_ENERGY_WH_58KWH

MIN_SPAN_DAYS = 60          # less history than this shows no trend
MIN_DAYS = 10               # ...and nor do fewer days with a reading
MAX_POINTS = 400            # beyond this, weekly medians keep the fit quick
WARRANTY_PCT = 70.0         # VW: 70 % of capacity for 8 years / 100,000 mi


def _median(values: list[float]) -> float:
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def theil_sen(points: list[tuple[float, float]], z: float = 1.96
              ) -> tuple[float, float, float] | None:
    """(slope, low, high): the median pairwise slope and Sen's interval."""
    slopes = sorted((y2 - y1) / (x2 - x1)
                    for i, (x1, y1) in enumerate(points)
                    for x2, y2 in points[i + 1:] if x2 != x1)
    n_pairs = len(slopes)
    if n_pairs == 0:
        return None
    n = len(points)
    c = z * math.sqrt(n * (n - 1) * (2 * n + 5) / 18.0)
    lo = max(0, int(math.floor((n_pairs - c) / 2.0)))
    hi = min(n_pairs - 1, int(math.ceil((n_pairs + c) / 2.0)))
    return _median(slopes), slopes[lo], slopes[hi]


def capacity_trend(repo, vehicle_id: int,
                   nominal_wh: float = NOMINAL_ENERGY_WH_58KWH) -> dict:
    """The capacity trend over all of this car's own readings."""
    by_day: dict[str, list[float]] = {}
    for r in repo.conn.execute(
            "SELECT substr(ts, 1, 10) AS day, value FROM measurements "
            "WHERE key='hv_energy_max' AND vehicle_id=? AND success=1 "
            "AND typeof(value) IN ('integer','real') AND value > 0 "
            "AND provenance NOT LIKE ? AND provenance <> 'simulated'",
            (vehicle_id, IMPORTED_PREFIX + "%")):
        by_day.setdefault(r["day"], []).append(float(r["value"]))
    days = sorted(by_day)
    out = {"days_with_readings": len(days), "min_span_days": MIN_SPAN_DAYS,
           "min_days": MIN_DAYS, "warranty_pct": WARRANTY_PCT,
           "nominal_kwh": nominal_wh / 1000.0, "basis": "hv_energy_max"}
    if not days:
        return {**out, "status": "no readings", "span_days": 0}
    first = date.fromisoformat(days[0])
    points = [((date.fromisoformat(d) - first).days, _median(by_day[d]))
              for d in days]
    span = points[-1][0]
    recent = [y for x, y in points if x >= span - 7]
    latest_kwh = _median(recent) / 1000.0
    out.update(span_days=span, first_day=days[0], last_day=days[-1],
               latest_kwh=latest_kwh,
               latest_pct=100.0 * latest_kwh * 1000.0 / nominal_wh)
    if span < MIN_SPAN_DAYS or len(points) < MIN_DAYS:
        return {**out, "status": "insufficient"}
    if len(points) > MAX_POINTS:                       # weekly medians
        weeks: dict[int, list[tuple[float, float]]] = {}
        for x, y in points:
            weeks.setdefault(int(x // 7), []).append((x, y))
        points = [(_median([x for x, _ in w]), _median([y for _, y in w]))
                  for w in weeks.values()]
    slope, lo, hi = theil_sen(points)
    per_year = 365.25 / 1000.0                         # Wh/day -> kWh/year
    out.update(status="ok", kwh_per_year=slope * per_year,
               kwh_per_year_low=lo * per_year, kwh_per_year_high=hi * per_year,
               pct_per_year=100.0 * slope * 365.25 / nominal_wh,
               points=len(points))
    # Only a fall the interval agrees on is carried forward.
    if hi < 0:
        warranty_kwh = nominal_wh / 1000.0 * WARRANTY_PCT / 100.0
        out["years_to_warranty_line"] = max(
            0.0, (latest_kwh - warranty_kwh) / -(slope * per_year))
    return out
