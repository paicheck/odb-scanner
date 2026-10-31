"""Does the personal consumption model predict this car's drives better than
the flat rate? A chronological backtest on the car's own drives.

Each drive is predicted from the drives that ended before it began -- never
from itself or anything later -- and only from those within the planner's
window (routing/car.py, CONSUMPTION_DAYS), the way the planner would have
predicted it that day:

* flat: kWh over km of the earlier drives that used energy, as the planner
  measures it without a model (analysis/trips.py `measured_rate`);
* model: analysis/consumption_model.py fitted to the same earlier drives and
  given the drive's own temperature, speed and heating. Where the earlier
  drives support no model the planner falls back to the flat rate, and so
  does this.

Errors are counted in kWh per drive and as a rate weighted by distance
(the sum of absolute errors per 100 km driven), with the bias. The model is
recommended only when it beats the flat rate by MARGIN over at least
MIN_PREDICTIONS drives it really predicted: on too little evidence, or too
small a gain, the flat rate stays -- it is simpler and cannot overfit.

Pure functions, no I/O.
"""
from __future__ import annotations

from math import sqrt

from analysis.consumption_model import MIN_SAMPLE_KM, DriveSample, _rows, estimate

MIN_PREDICTIONS = 10        # drives the model must really have predicted
MARGIN = 0.05               # and beaten the flat rate by this share
WINDOW_DAYS = 90            # routing/car.py CONSUMPTION_DAYS
MAX_PREDICTIONS = 60        # the newest drives; older ones say less about now


def _flat(earlier: list[DriveSample]) -> float | None:
    km = kwh = 0.0
    for s in earlier:
        if s.energy_kwh is not None and s.energy_kwh > 0 and s.distance_km > 0:
            km += s.distance_km
            kwh += s.energy_kwh
    return kwh / km if km >= MIN_SAMPLE_KM and kwh > 0 else None


def _score(errors: list[tuple[float, float]]) -> dict:
    """errors: (predicted - actual kWh, km) per drive."""
    km = sum(k for _, k in errors)
    n = len(errors)
    return {
        "drives": n,
        "km": round(km, 1),
        "mae_kwh": round(sum(abs(e) for e, _ in errors) / n, 3),
        "rmse_kwh": round(sqrt(sum(e * e for e, _ in errors) / n), 3),
        "error_kwh_per_100km": round(100.0 * sum(abs(e) for e, _ in errors) / km, 2),
        "bias_kwh_per_100km": round(100.0 * sum(e for e, _ in errors) / km, 2),
    }


def backtest(samples: list[DriveSample], window_days: float = WINDOW_DAYS,
             last: int = MAX_PREDICTIONS) -> dict:
    """Predict each of the newest `last` drives from the ones before it.

    `samples` need start_s/end_s; drives without them, or too short to have a
    rate of their own, are not predicted (they still count as history)."""
    timed = sorted((s for s in samples if s.start_s is not None and s.end_s is not None),
                   key=lambda s: s.end_s)
    targets = [s for s in timed if _rows([s])][-max(1, last):]
    window_s = window_days * 86400.0
    flat_err, model_err, drives = [], [], []
    really, inside = 0, 0
    for s in targets:
        earlier = [e for e in timed
                   if e.end_s <= s.start_s and e.end_s >= s.start_s - window_s]
        flat = _flat(earlier)
        if flat is None:
            continue
        actual = s.energy_kwh
        model = estimate(earlier)
        if model is not None:
            rate, lo, hi = model.predict(s.temp_c, s.avg_speed_kmh, None, s.hvac_on)
            really += 1
            inside += lo <= actual / s.distance_km <= hi
        else:
            rate = flat
        flat_err.append((flat * s.distance_km - actual, s.distance_km))
        model_err.append((rate * s.distance_km - actual, s.distance_km))
        drives.append({"end_s": s.end_s, "km": round(s.distance_km, 1),
                       "actual_kwh": round(actual, 3),
                       "flat_kwh": round(flat * s.distance_km, 3),
                       "model_kwh": round(rate * s.distance_km, 3),
                       "model": model is not None})
    out = {"predicted": len(flat_err), "model_predicted": really, "drives": drives,
           "flat": _score(flat_err) if flat_err else None,
           "model": _score(model_err) if model_err else None,
           "band_coverage": round(inside / really, 2) if really else None}
    if really < MIN_PREDICTIONS:
        out["verdict"] = "flat"
        out["reason"] = (f"the model predicted only {really} drive(s) from earlier "
                         f"ones; it needs {MIN_PREDICTIONS} to be judged")
        return out
    f, m = out["flat"]["error_kwh_per_100km"], out["model"]["error_kwh_per_100km"]
    if m < f * (1.0 - MARGIN):
        out["verdict"] = "model"
        out["reason"] = (f"over {len(flat_err)} drives, each predicted from the ones "
                         f"before it, the model was off by {m:.1f} kWh/100 km against "
                         f"the flat rate's {f:.1f}")
    else:
        out["verdict"] = "flat"
        out["reason"] = (f"over {len(flat_err)} drives the model was off by {m:.1f} "
                         f"kWh/100 km against the flat rate's {f:.1f}: not enough "
                         f"better (needs {MARGIN * 100:.0f} %)")
    return out
