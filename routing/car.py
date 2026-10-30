"""This car's own energy figures for trip planning, with where each came from.

* Usable capacity: the BMS-rated maximum energy (hv_energy_max, DID 0x2AB2 --
  one of the two VERIFIED decoders). It already reflects battery ageing.
* Energy now: the latest energy content (hv_energy_content, 0x2AB8), with its
  age -- the car is read in sessions, so "now" is usually days old, and the
  planner form lets the driver override the starting charge.
* Consumption: measured from recorded driving when there is enough of it
  (odometer distance against energy-content drop, over the drives of
  analysis/trips.py), otherwise a stated default. Each figure says which.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analysis.consumption_model import DriveSample, estimate
from analysis.trips import PAIR_WITHIN_S, _Lookup, measured_rate, split_drives
from database.repository import IMPORTED_PREFIX

DEFAULT_USABLE_KWH = 58.0            # ID.3 Pro 58 kWh: net capacity when new
DEFAULT_CONSUMPTION = 0.17           # kWh/km: ID.3 mixed driving, mild weather
MIN_MEASURED_KM = 50.0               # less driving than this is not a measurement
# Recent driving only: an ID.3 uses about a quarter more in winter than in
# summer, and a figure from last summer would plan this winter's trips.
CONSUMPTION_DAYS = 90


# This car's own numeric readings: not imported, not simulated.
_MEASURED = ("FROM measurements WHERE vehicle_id=? AND key=? AND success=1 "
             "AND typeof(value) IN ('integer','real') "
             "AND provenance NOT LIKE ? AND provenance <> 'simulated' ")


def _latest(repo, vid: int, key: str) -> tuple[str, float] | None:
    r = repo.conn.execute(
        "SELECT ts, value " + _MEASURED + "ORDER BY ts DESC, id DESC LIMIT 1",
        (vid, key, IMPORTED_PREFIX + "%")).fetchone()
    return (r["ts"], float(r["value"])) if r else None


def _series(repo, vid: int, key: str, days: float) -> list[tuple[str, float]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    return [(r["ts"], float(r["value"])) for r in repo.conn.execute(
        "SELECT ts, value " + _MEASURED + "AND ts >= ? ORDER BY ts",
        (vid, key, IMPORTED_PREFIX + "%", since)).fetchall()]


def measured_consumption(odometer, energy) -> tuple[float | None, float]:
    """kWh/km over this car's recorded drives -- the same drives the trip log
    shows (analysis/trips.py) -- or (None, km) when there is not enough."""
    rate, km = measured_rate(split_drives(odometer, energy))
    if km < MIN_MEASURED_KM or rate is None:
        return None, km
    return rate, km
def measured_consumption_model(repo, vid: int, days: float = CONSUMPTION_DAYS):
    """A personalised consumption model from this car's own drives, or None.

    Uses the same drives as the trip log (analysis/trips.py). Returns a dict
    of coefficients (serialisable) rather than the dataclass, so it can travel
    through the web context and config without import cycles.
    """
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since = since_dt.isoformat(timespec="seconds")
    lead_days = days + PAIR_WITHIN_S / 86400.0
    drives = [d for d in split_drives(
        _series(repo, vid, "odometer_km", lead_days),
        _series(repo, vid, "hv_energy_content", lead_days))
        if d.end_ts >= since]
    speed_lookup = _Lookup(_series(repo, vid, "speed_kmh", lead_days))
    outside_lookup = _Lookup(_series(repo, vid, "outside_temp", lead_days))
    samples = []
    for d in drives:
        if d.distance_km < 5 or d.energy_kwh is None:
            continue
        speeds = speed_lookup.between(d.start_s, d.end_s)
        temps = outside_lookup.between(d.start_s, d.end_s) or [
            v for v in (outside_lookup.nearest(d.start_s),) if v is not None]
        avg_speed = (sum(speeds) / len(speeds)) if speeds else None
        temp = (sum(temps) / len(temps)) if temps else None
        samples.append(DriveSample(
            distance_km=d.distance_km, energy_kwh=d.energy_kwh,
            temp_c=temp, avg_speed_kmh=avg_speed))
    model = estimate(samples)
    if model is None:
        return None
    return {
        "base_kwh_per_km": model.base_kwh_per_km,
        "temp_coeff": model.temp_coeff,
        "speed_coeff": model.speed_coeff,
        "elev_coeff": model.elev_coeff,
        "hvac_coeff": model.hvac_coeff,
        "residual_std": model.residual_std,
        "sample_km": model.sample_km,
        "n_drives": model.n_drives,
        "features_used": list(model.features_used),
    }



def car_energy(repo, vid: int, cfg) -> dict:
    usable = _latest(repo, vid, "hv_energy_max")
    content = _latest(repo, vid, "hv_energy_content")
    soc = _latest(repo, vid, "soc_normal")

    if usable:
        usable_kwh, usable_basis = usable[1] / 1000.0, \
            "BMS-rated maximum energy (hv_energy_max)"
    else:
        usable_kwh = DEFAULT_USABLE_KWH
        usable_basis = "ID.3 58 kWh nominal (no BMS reading)"

    if content:
        now_kwh, now_ts, now_basis = content[1] / 1000.0, content[0], \
            "energy content (hv_energy_content)"
        start_pct = min(100.0, max(0.0, now_kwh / usable_kwh * 100.0))
    elif soc:
        start_pct, now_ts, now_basis = soc[1], soc[0], "display SoC"
    else:
        start_pct, now_ts, now_basis = 80.0, None, "no reading; assumed"

    configured = cfg.get("routing.consumption_kwh_per_100km")
    if configured:
        cons, cons_basis = float(configured) / 100.0, "set in config"
    else:
        measured, km_seen = measured_consumption(
            _series(repo, vid, "odometer_km", CONSUMPTION_DAYS),
            _series(repo, vid, "hv_energy_content", CONSUMPTION_DAYS))
        if measured is not None:
            cons = measured
            cons_basis = (f"measured over {km_seen:.0f} km of this car's "
                          f"driving in the last {CONSUMPTION_DAYS} days")
        else:
            cons, cons_basis = DEFAULT_CONSUMPTION, (
                f"default -- only {km_seen:.0f} km of driving recorded in the "
                f"last {CONSUMPTION_DAYS} days (needs {MIN_MEASURED_KM:.0f} km "
                f"to measure)")

    model = measured_consumption_model(repo, vid)
    return {
        "usable_kwh": round(usable_kwh, 2), "usable_basis": usable_basis,
        "start_pct": round(start_pct, 1), "start_ts": now_ts, "start_basis": now_basis,
        "consumption_kwh_per_100km": round(cons * 100.0, 1),
        "consumption_basis": cons_basis,
        "consumption_model": model,
    }
