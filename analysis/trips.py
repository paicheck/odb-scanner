"""Drives, cut from this car's own readings: how far, how long, what each cost.

Only what the car told this collector counts -- not imported, not simulated.
A drive is a stretch of odometer readings, cut wherever:

* the recording falls silent for longer than MAX_GAP_S -- a drive or a charge
  may have happened unseen in the gap;
* the odometer goes backwards (a glitch, or another car);
* the battery's energy rose while the car stood still (a charge);
* the car stood still for longer than PARK_S. The odometer counts whole
  kilometres, so "still" means no kilometre in that time: an average under
  6 km/h, which is a stop, not traffic.

Each drive is counted whole, net energy over net distance, so regeneration on
a descent is set against the climb before it. The route planner measures its
consumption figure from the same drives (routing/car.py), so the trip log and
the planner cannot disagree about what the car uses.

The odometer's whole kilometres make the distance of a short drive coarse (a
3 km drive is 2 to 4 km), so drives under MIN_RATE_KM get no consumption figure
of their own; they still count in the totals, where the rounding averages out.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from database.repository import IMPORTED_PREFIX

PAIR_WITHIN_S = 600         # odometer and energy read this close count as one
MAX_GAP_S = 15 * 60         # a longer silence may hide a drive or a charge
PARK_S = 10 * 60            # no whole kilometre in this long: the car stood
STILL_KM = 0.1              # moved less than this: standing still
MIN_RATE_KM = 5.0           # shorter drives get no consumption of their own
KM_PER_MILE = 1.609344

# Outside-temperature bands for "what does the cold cost": an ID.3 uses about
# a quarter more energy below 5 C than above 15 C (heating, cold cells).
TEMP_BANDS = (("below 5 °C", None, 5.0), ("5 to 15 °C", 5.0, 15.0),
              ("above 15 °C", 15.0, None))


def _t(ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass
class Drive:
    start_ts: str
    end_ts: str
    start_s: float
    end_s: float
    start_km: float
    end_km: float
    start_kwh: float | None      # battery energy content at each end
    end_kwh: float | None

    @property
    def distance_km(self) -> float:
        return self.end_km - self.start_km

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def energy_kwh(self) -> float | None:
        """Net energy the battery gave (negative: it gained, downhill)."""
        if self.start_kwh is None or self.end_kwh is None:
            return None
        return self.start_kwh - self.end_kwh

    @property
    def kwh_per_100km(self) -> float | None:
        e = self.energy_kwh
        if e is None or self.distance_km < MIN_RATE_KM:
            return None
        return 100.0 * e / self.distance_km


def _pair(points, energy) -> list[tuple[float, str, float, float | None]]:
    """(s, ts, km, kWh or None): each odometer reading with the energy
    reading nearest in time, if one is within PAIR_WITHIN_S."""
    e = sorted((s, wh / 1000.0) for ts, wh in energy
               if (s := _t(ts)) is not None)
    et = [s for s, _ in e]
    out = []
    for s, ts, km in points:
        kwh = None
        i = bisect_left(et, s)
        near = [j for j in (i - 1, i) if 0 <= j < len(e)]
        if near:
            j = min(near, key=lambda j: abs(et[j] - s))
            if abs(et[j] - s) <= PAIR_WITHIN_S:
                kwh = e[j][1]
        out.append((s, ts, km, kwh))
    return out


def split_drives(odometer, energy) -> list[Drive]:
    """Drives from `odometer` [(ts, km)] and `energy` [(ts, Wh)] readings of
    one car, oldest first. See the module docstring for where they are cut."""
    points = sorted((s, ts, float(km)) for ts, km in odometer
                    if (s := _t(ts)) is not None)
    pts = _pair(points, energy)

    runs: list[list] = []
    run: list = []
    for p in pts:
        if run:
            q = run[-1]
            charged = (p[2] - q[2] < STILL_KM and p[3] is not None
                       and q[3] is not None and p[3] > q[3])
            if p[0] - q[0] > MAX_GAP_S or p[2] < q[2] or charged:
                runs.append(run)
                run = []
        run.append(p)
    if run:
        runs.append(run)

    def drive(run, a, b) -> Drive:
        return Drive(run[a][1], run[b][1], run[a][0], run[b][0],
                     run[a][2], run[b][2], run[a][3], run[b][3])

    drives: list[Drive] = []
    for run in runs:
        moves = [i for i in range(1, len(run))
                 if run[i][2] - run[i - 1][2] >= STILL_KM]
        if not moves:
            continue                          # parked for the whole run
        start, last = moves[0] - 1, moves[0]
        for i in moves[1:]:
            if run[i - 1][0] - run[last][0] > PARK_S:
                drives.append(drive(run, start, last))
                start = i - 1
            last = i
        drives.append(drive(run, start, last))
    return drives


def measured_rate(drives: list[Drive]) -> tuple[float | None, float]:
    """(kWh per km, km) over the drives whose energy is known and positive."""
    km = kwh = 0.0
    for d in drives:
        e = d.energy_kwh
        if e is not None and e > 0 and d.distance_km > 0:
            km += d.distance_km
            kwh += e
    return (kwh / km if km > 0 and kwh > 0 else None), km


# ---------------------------------------------------------------------------
# The trip log page
# ---------------------------------------------------------------------------

def _series(repo, vid: int, key: str, since: str) -> list[tuple[str, float]]:
    return [(r["ts"], float(r["value"])) for r in repo.conn.execute(
        "SELECT ts, value FROM measurements WHERE vehicle_id=? AND key=? "
        "AND success=1 AND typeof(value) IN ('integer','real') "
        "AND provenance NOT LIKE ? AND provenance <> 'simulated' AND ts >= ? "
        "ORDER BY ts", (vid, key, IMPORTED_PREFIX + "%", since)).fetchall()]


class _Lookup:
    """Values of one key by time: the nearest, or all inside a span."""

    def __init__(self, rows):
        pts = sorted((s, v) for ts, v in rows if (s := _t(ts)) is not None)
        self.s = [s for s, _ in pts]
        self.v = [v for _, v in pts]

    def nearest(self, at: float, within: float = PAIR_WITHIN_S) -> float | None:
        i = bisect_left(self.s, at)
        near = [j for j in (i - 1, i) if 0 <= j < len(self.s)]
        if not near:
            return None
        j = min(near, key=lambda j: abs(self.s[j] - at))
        return self.v[j] if abs(self.s[j] - at) <= within else None

    def between(self, a: float, b: float) -> list[float]:
        return self.v[bisect_left(self.s, a):bisect_right(self.s, b)]


def _mi(km: float | None) -> float | None:
    return None if km is None else km / KM_PER_MILE


def trip_log(repo, vehicle_id: int, days: int) -> dict:
    """The drives of the last `days` days, newest first, with totals and what
    each outside-temperature band cost."""
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since = since_dt.isoformat(timespec="seconds")
    # Pairing reaches back PAIR_WITHIN_S, so read that much before the window.
    lead = (since_dt - timedelta(seconds=PAIR_WITHIN_S)).isoformat(
        timespec="seconds")
    drives = [d for d in split_drives(
        _series(repo, vehicle_id, "odometer_km", lead),
        _series(repo, vehicle_id, "hv_energy_content", lead))
        if d.end_ts >= since]
    soc = _Lookup(_series(repo, vehicle_id, "soc_normal", lead))
    speed = _Lookup(_series(repo, vehicle_id, "speed_kmh", lead))
    outside = _Lookup(_series(repo, vehicle_id, "outside_temp", lead))

    rows = []
    for d in drives:
        speeds = speed.between(d.start_s, d.end_s)
        temps = outside.between(d.start_s, d.end_s) or [
            v for v in (outside.nearest(d.start_s),) if v is not None]
        rate = d.kwh_per_100km
        rows.append({
            "start_ts": d.start_ts, "end_ts": d.end_ts,
            "duration_s": round(d.duration_s),
            "distance_km": d.distance_km, "distance_mi": _mi(d.distance_km),
            "energy_kwh": d.energy_kwh,
            "kwh_per_100km": rate,
            "mi_per_kwh": (_mi(100.0) / rate if rate and rate > 0 else None),
            "avg_kmh": (3600.0 * d.distance_km / d.duration_s
                        if d.duration_s > 0 else None),
            "top_kmh": max(speeds) if speeds else None,
            "soc_start": soc.nearest(d.start_s), "soc_end": soc.nearest(d.end_s),
            "outside_c": sum(temps) / len(temps) if temps else None,
        })
    rows.reverse()

    rate, km_rated = measured_rate(drives)
    bands = []
    for label, lo, hi in TEMP_BANDS:
        inside = [d for d, r in zip(reversed(drives), rows)
                  if r["outside_c"] is not None
                  and (lo is None or r["outside_c"] >= lo)
                  and (hi is None or r["outside_c"] < hi)]
        if inside:
            band_rate, band_km = measured_rate(inside)
            bands.append({"label": label, "drives": len(inside), "km": band_km,
                          "kwh_per_100km": band_rate * 100 if band_rate else None})
    total_km = sum(d.distance_km for d in drives)
    return {
        "days": days,
        "drives": rows,
        "count": len(rows),
        "total_km": total_km, "total_mi": _mi(total_km),
        "total_s": round(sum(d.duration_s for d in drives)),
        "kwh_per_100km": rate * 100 if rate else None,
        "mi_per_kwh": _mi(1.0) / rate if rate else None,
        "rated_km": km_rated,
        "longest": max(rows, key=lambda r: r["distance_km"]) if rows else None,
        "bands": bands,
    }
