"""This car's own energy figures for trip planning, with where each came from.

* Usable capacity: the BMS-rated maximum energy (hv_energy_max, DID 0x2AB2 --
  one of the two VERIFIED decoders). It already reflects battery ageing.
* Energy now: the latest energy content (hv_energy_content, 0x2AB8), with its
  age -- the car is read in sessions, so "now" is usually days old, and the
  planner form lets the driver override the starting charge.
* Consumption: measured from recorded driving when there is enough of it
  (odometer distance against energy-content drop, over stretches with no
  charging between), otherwise a stated default. Each figure says which.
"""
from __future__ import annotations

from database.repository import IMPORTED_PREFIX

DEFAULT_USABLE_KWH = 58.0            # ID.3 Pro 58 kWh: net capacity when new
DEFAULT_CONSUMPTION = 0.17           # kWh/km: ID.3 mixed driving, mild weather
MIN_MEASURED_KM = 50.0               # less driving than this is not a measurement


def _series(repo, vid: int, key: str) -> list[tuple[str, float]]:
    return [(r["ts"], float(r["value"])) for r in repo.conn.execute(
        "SELECT ts, value FROM measurements WHERE vehicle_id=? AND key=? "
        "AND success=1 AND typeof(value) IN ('integer','real') "
        "AND provenance NOT LIKE ? AND provenance <> 'simulated' ORDER BY ts",
        (vid, key, IMPORTED_PREFIX + "%")).fetchall()]


def measured_consumption(odometer, energy) -> tuple[float | None, float]:
    """kWh/km over consecutive (odometer, energy) pairs where the car drove
    and did not charge; (None, km) when there is not enough of it.

    Pairs each odometer reading with the energy reading closest in time
    (within 10 min). A stretch where energy ROSE had charging in it and is
    skipped; so is one with no distance.
    """
    from datetime import datetime

    def t(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()

    pairs = []
    for ts, km in odometer:
        best = min(energy, key=lambda e: abs(t(e[0]) - t(ts)), default=None)
        if best and abs(t(best[0]) - t(ts)) <= 600:
            pairs.append((km, best[1] / 1000.0))      # Wh -> kWh
    km_total = kwh_total = 0.0
    for (km0, e0), (km1, e1) in zip(pairs, pairs[1:]):
        dkm, de = km1 - km0, e0 - e1
        if dkm <= 0 or de < 0:
            continue
        km_total += dkm
        kwh_total += de
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
