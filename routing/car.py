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

from analysis.consumption_backtest import PHYSICS_WINDOW_DAYS, backtest, error_spread
from analysis.consumption_model import FEATURE_LABELS, DriveSample, estimate
from analysis.energy_model import fit as fit_physics
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
# The backtest predicts each recent drive from the drives before it: the
# flat rate and the linear model from CONSUMPTION_DAYS, the physics model from
# PHYSICS_WINDOW_DAYS (it knows the temperature, so last winter helps).
MODEL_HISTORY_DAYS = PHYSICS_WINDOW_DAYS + CONSUMPTION_DAYS
# Speed is read under two keys (UDS and the OBD PID); both are the same thing.
SPEED_KEYS = ("speed_kmh", "vehicle_speed")
MIN_SPEED_SAMPLES = 3       # fewer, and a drive's mean v² is a guess
_checks: dict[tuple, dict] = {}


def drive_samples(repo, vid: int, days: float) -> list[DriveSample]:
    """This car's drives of the last `days` -- the trip log's drives
    (analysis/trips.py) -- with their times, average speed, mean of speed²
    (for drag) and outside temperature, as consumption-model samples."""
    since_s = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    lead_days = days + PAIR_WITHIN_S / 86400.0
    drives = [d for d in split_drives(
        _series(repo, vid, "odometer_km", lead_days),
        _series(repo, vid, "hv_energy_content", lead_days))
        if d.end_s >= since_s]
    speed_lookup = _Lookup([r for k in SPEED_KEYS
                            for r in _series(repo, vid, k, lead_days)])
    outside_lookup = _Lookup(_series(repo, vid, "outside_temp", lead_days))
    samples = []
    for d in drives:
        if d.energy_kwh is None or d.distance_km <= 0:
            continue
        speeds = speed_lookup.between(d.start_s, d.end_s)
        temps = outside_lookup.between(d.start_s, d.end_s) or [
            v for v in (outside_lookup.nearest(d.start_s),) if v is not None]
        moving = sum(speeds)
        samples.append(DriveSample(
            distance_km=d.distance_km, energy_kwh=d.energy_kwh,
            temp_c=(sum(temps) / len(temps)) if temps else None,
            avg_speed_kmh=(sum(speeds) / len(speeds)) if speeds else None,
            start_s=d.start_s, end_s=d.end_s,
            v2_kmh2=(sum(v ** 3 for v in speeds) / moving
                     if len(speeds) >= MIN_SPEED_SAMPLES and moving > 0 else None)))
    return samples


def consumption_check(repo, vid: int) -> dict:
    """This car's own consumption model, if it has earned its place.

    {"model": a ConsumptionModel or PhysicsModel or None, "backtest":
    analysis.consumption_backtest result, "basis": one sentence, "spread":
    the RateError quantiles of the predictions of whichever is used}. A model is
    returned only when, predicting each recent drive from the drives before
    it, it beat the flat rate (analysis/consumption_backtest.py); a linear
    model also has to use at least one condition -- one of no conditions is
    the flat rate again. Kept per vehicle until a new odometer reading
    arrives or the day changes."""
    last = repo.conn.execute(
        "SELECT MAX(ts) FROM measurements WHERE vehicle_id=? AND key='odometer_km'",
        (vid,)).fetchone()[0]
    key = (repo.path, vid, last, datetime.now(timezone.utc).date().isoformat())
    if key in _checks:
        return _checks[key]
    samples = drive_samples(repo, vid, MODEL_HISTORY_DAYS)
    result = backtest(samples, window_days=CONSUMPTION_DAYS)
    now_s = datetime.now(timezone.utc).timestamp()
    model = None
    if result["verdict"] == "model":
        model = estimate([s for s in samples
                          if s.end_s >= now_s - CONSUMPTION_DAYS * 86400])
        if model is not None and not model.features_used:
            model = None
            result = dict(result, verdict="flat", reason=(
                "the latest drives support no condition beyond the flat rate"))
    elif result["verdict"] == "physics":
        model = fit_physics([s for s in samples
                             if s.end_s >= now_s - PHYSICS_WINDOW_DAYS * 86400])
        if model is None:
            result = dict(result, verdict="flat", reason=(
                "too few recent drives to fit the physics model on"))
    if model is not None and result["verdict"] == "physics":
        basis = (f"this car's own model (physics: speed, outside temperature and "
                 f"heating, from {model.n_drives} drives, {model.sample_km:.0f} km) "
                 f"plans trips: {result['reason']}")
    elif model is not None:
        used = ", ".join(FEATURE_LABELS[f] for f in model.features_used)
        basis = (f"this car's own model ({used}, from {model.n_drives} drives) plans "
                 f"trips: {result['reason']}")
    else:
        basis = f"flat rate kept: {result['reason']}"
    used_name = result["verdict"] if model is not None else "flat"
    # How far off its predictions were, for the figure the Route page will use.
    spread = error_spread(result, model=used_name)
    out = {"model": model, "backtest": result, "basis": basis, "spread": spread}
    if len(_checks) > 16:
        _checks.clear()
    _checks[key] = out
    return out


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
    model, model_basis = None, "a figure set in config is used as it is"
    spread = None
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

    if not configured:
        check = consumption_check(repo, vid)
        model, model_basis, spread = check["model"], check["basis"], check["spread"]
    return {
        "usable_kwh": round(usable_kwh, 2), "usable_basis": usable_basis,
        "start_pct": round(start_pct, 1), "start_ts": now_ts, "start_basis": now_basis,
        "consumption_kwh_per_100km": round(cons * 100.0, 1),
        "consumption_basis": cons_basis,
        # A dict, so the figures can travel through the page context.
        "consumption_model": model.as_dict() if model else None,
        "consumption_model_basis": model_basis,
        # kWh/km more (negative: less) than predicted on its recent drives,
        # 10th to 90th percentile: the Route page's arrival range.
        "consumption_spread": spread,
    }
