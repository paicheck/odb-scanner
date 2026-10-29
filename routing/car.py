"""This car's own energy figures for trip planning, with where each came from.

* Usable capacity: the BMS-rated maximum energy (hv_energy_max, DID 0x2AB2 --
  one of the two VERIFIED decoders). It already reflects battery ageing.
* Energy now: the latest energy content (hv_energy_content, 0x2AB8), with its
  age -- the car is read in sessions, so "now" is usually days old, and the
  planner form lets the driver override the starting charge.
* Consumption: measured from recorded driving when there is enough of it
  (odometer distance against energy-content drop, over recorded drives with
  no charging and no gap in them), otherwise a stated default. Each figure
  says which.
"""
from __future__ import annotations

from database.repository import IMPORTED_PREFIX

DEFAULT_USABLE_KWH = 58.0            # ID.3 Pro 58 kWh: net capacity when new
DEFAULT_CONSUMPTION = 0.17           # kWh/km: ID.3 mixed driving, mild weather
MIN_MEASURED_KM = 50.0               # less driving than this is not a measurement
PAIR_WITHIN_S = 600                  # odometer and energy read this close count as one
MAX_GAP_S = 15 * 60                  # a longer silence may hide a drive or a charge
STILL_KM = 0.1                       # moved less than this: standing still


def _series(repo, vid: int, key: str) -> list[tuple[str, float]]:
    return [(r["ts"], float(r["value"])) for r in repo.conn.execute(
        "SELECT ts, value FROM measurements WHERE vehicle_id=? AND key=? "
        "AND success=1 AND typeof(value) IN ('integer','real') "
        "AND provenance NOT LIKE ? AND provenance <> 'simulated' ORDER BY ts",
        (vid, key, IMPORTED_PREFIX + "%")).fetchall()]


def measured_consumption(odometer, energy) -> tuple[float | None, float]:
    """kWh/km over this car's recorded drives; (None, km) when there is not
    enough of it.

    Each odometer reading is paired with the energy reading closest in time
    (within PAIR_WITHIN_S). The pairs are cut into drives wherever the
    recording falls silent for longer than MAX_GAP_S -- a drive or a charge
    may have happened unseen in the gap, and a charge hidden there would make
    the car look far more frugal than it is -- and wherever the energy rose
    while the car stood still (charging). Each drive counts whole, net energy
    over net distance, so regeneration on a descent is set against the climb
    before it instead of being dropped as if it were a charge.
    """
    from bisect import bisect_left
    from datetime import datetime

    def t(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()

    e = sorted((t(ts), wh / 1000.0) for ts, wh in energy)      # Wh -> kWh
    et = [x for x, _ in e]
    pairs = []                                                 # (s, km, kWh)
    for at, km in sorted((t(ts), km) for ts, km in odometer):
        i = bisect_left(et, at)
        near = [j for j in (i - 1, i) if 0 <= j < len(e)]
        if near:
            j = min(near, key=lambda j: abs(et[j] - at))
            if abs(et[j] - at) <= PAIR_WITHIN_S:
                pairs.append((at, km, e[j][1]))

    km_total = kwh_total = 0.0
    drive: list[tuple[float, float, float]] = []

    def count(drive):
        nonlocal km_total, kwh_total
        if len(drive) >= 2:
            dkm, de = drive[-1][1] - drive[0][1], drive[0][2] - drive[-1][2]
            if dkm > 0 and de > 0:
                km_total += dkm
                kwh_total += de

    for p in pairs:
        if drive:
            at0, km0, e0 = drive[-1]
            charged = p[1] - km0 < STILL_KM and p[2] > e0
            if p[0] - at0 > MAX_GAP_S or p[1] < km0 or charged:
                count(drive)
                drive = []
        drive.append(p)
    count(drive)
    if km_total < MIN_MEASURED_KM or kwh_total <= 0:
        return None, km_total
    return kwh_total / km_total, km_total


def car_energy(repo, vid: int, cfg) -> dict:
    usable = _series(repo, vid, "hv_energy_max")
    content = _series(repo, vid, "hv_energy_content")
    soc = _series(repo, vid, "soc_normal")
    odo = _series(repo, vid, "odometer_km")

    if usable:
        usable_kwh, usable_basis = usable[-1][1] / 1000.0, \
            "BMS-rated maximum energy (hv_energy_max)"
    else:
        usable_kwh = DEFAULT_USABLE_KWH
        usable_basis = "ID.3 58 kWh nominal (no BMS reading)"

    if content:
        now_kwh, now_ts, now_basis = content[-1][1] / 1000.0, content[-1][0], \
            "energy content (hv_energy_content)"
        start_pct = min(100.0, max(0.0, now_kwh / usable_kwh * 100.0))
    elif soc:
        start_pct, now_ts, now_basis = soc[-1][1], soc[-1][0], "display SoC"
    else:
        start_pct, now_ts, now_basis = 80.0, None, "no reading; assumed"

    configured = cfg.get("routing.consumption_kwh_per_100km")
    measured, km_seen = measured_consumption(odo, content)
    if configured:
        cons, cons_basis = float(configured) / 100.0, "set in config"
    elif measured is not None:
        cons = measured
        cons_basis = f"measured over {km_seen:.0f} km of this car's driving"
    else:
        cons, cons_basis = DEFAULT_CONSUMPTION, (
            f"default -- only {km_seen:.0f} km of driving recorded so far "
            f"(needs {MIN_MEASURED_KM:.0f} km to measure)")

    return {
        "usable_kwh": round(usable_kwh, 2), "usable_basis": usable_basis,
        "start_pct": round(start_pct, 1), "start_ts": now_ts, "start_basis": now_basis,
        "consumption_kwh_per_100km": round(cons * 100.0, 1),
        "consumption_basis": cons_basis,
    }
