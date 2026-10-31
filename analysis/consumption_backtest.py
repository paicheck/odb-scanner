"""Which way of predicting consumption works on this car? A chronological
backtest on the car's own drives.

Each drive is predicted only from drives that ended before it began, and
never from a drive of the same outing (drives less than OUTING_GAP_S apart:
there and back from the shops share their wind, load and driver). Nothing
from a drive's own journey can leak into its prediction. Each candidate
trains on its own window, the way the planner would have used it that day:

* flat: kWh over km of the earlier drives that used energy, as the planner
  measures it without a model (analysis/trips.py `measured_rate`), over
  WINDOW_DAYS;
* model: the linear model (analysis/consumption_model.py), same window;
* physics: the physics-informed model (analysis/energy_model.py), over
  PHYSICS_WINDOW_DAYS -- it knows temperature, so last winter's drives help
  predict this winter's instead of misleading it.

A candidate that cannot be fitted yet falls back to the flat rate, and is
only credited with the drives it really predicted.

Scored per drive, in kWh and as a rate weighted by distance (the sum of
absolute errors per 100 km driven), with the bias, by outside temperature,
average speed, length and (where a drive's climb is known) gradient; and in
arrival charge: the error as % of the usable battery. Each candidate's 80 %
range is checked on every drive it predicted:

* flat and model use the planner's ranges (planner._add_bands): the 10th to
  90th percentile of the earlier drives' per-km errors, times the length;
  ASSUMED_RATE_ERROR either way before there are MIN_PREDICTIONS of them;
* physics uses its own predictive spread, times a calibration learned only
  from the drives before (`calibration`).

The recommended way is the one with the smallest error that beats the flat
rate by MARGIN over at least MIN_PREDICTIONS drives it really predicted. On
less evidence, or a smaller gain, the flat rate stays: it is simpler and
cannot overfit. Pure functions, no I/O.
"""
from __future__ import annotations

from math import sqrt

from analysis.consumption_model import MIN_SAMPLE_KM, DriveSample, _rows, estimate

MIN_PREDICTIONS = 10        # drives a candidate must really have predicted
MARGIN = 0.05               # and beaten the flat rate by this share
WINDOW_DAYS = 90            # routing/car.py CONSUMPTION_DAYS
PHYSICS_WINDOW_DAYS = 365
MAX_PREDICTIONS = 60        # the newest drives; older ones say less about now
OUTING_GAP_S = 2 * 3600.0
ASSUMED_RATE_ERROR = 0.10   # routing/planner.py: the range before any is measured
BAND_Q = (0.1, 0.9)         # the planner's arrival ranges: an 80 % range
CALIBRATION_PRIOR = 10.0    # pseudo-drives at "the spread is right"
CALIBRATION_FLOOR = 0.8     # never narrower than this share of the model's own
CANDIDATES = ("flat", "model", "physics")
USABLE_KWH = 56.0           # only to express errors as % of the battery

TEMP_BANDS = (("below 5 °C", None, 5.0), ("5 to 15 °C", 5.0, 15.0),
              ("15 °C and above", 15.0, None))
SPEED_BANDS = (("town, under 40 km/h", None, 40.0), ("40 to 70 km/h", 40.0, 70.0),
               ("70 km/h and above", 70.0, None))
LENGTH_BANDS = (("5 to 10 km", None, 10.0), ("10 to 40 km", 10.0, 40.0),
                ("40 to 100 km", 40.0, 100.0), ("100 km and more", 100.0, None))
CLIMB_BANDS = (("downhill, < -2 m/km", None, -2.0), ("level", -2.0, 2.0),
               ("uphill, > 2 m/km", 2.0, None))


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


def outings(samples: list[DriveSample], gap_s: float = OUTING_GAP_S) -> dict[int, int]:
    """id(sample) -> outing number: drives less than gap_s apart share one."""
    out: dict[int, int] = {}
    n, last_end = -1, None
    for s in sorted(samples, key=lambda s: s.start_s):
        if last_end is None or s.start_s - last_end > gap_s:
            n += 1
        out[id(s)] = n
        last_end = s.end_s if last_end is None else max(last_end, s.end_s)
    return out


def _quantile(pairs: list[tuple[float, float]], q: float) -> float:
    """The q-quantile of values weighted by their weights: (value, weight)."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    run = 0.0
    for v, w in pairs:
        run += w
        if run >= q * total:
            return v
    return pairs[-1][0]


def calibration(z2: list[float]) -> float:
    """How much wider the physics model's ranges must be, from the squared
    standardised errors of drives it predicted before. Their median, over
    the median of a chi-squared with one degree of freedom (0.455): a few
    drives with a wrong energy reading must not widen every range. Shrunk
    toward 1 with CALIBRATION_PRIOR pseudo-drives, never below
    CALIBRATION_FLOOR."""
    if not z2:
        return 1.0
    s = sorted(z2)
    mid = len(s) // 2
    med = s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])
    k2 = (CALIBRATION_PRIOR + len(s) * med / 0.4549) / (CALIBRATION_PRIOR + len(s))
    return max(CALIBRATION_FLOOR, sqrt(k2))


def _trainers(window_days: float, physics_window_days: float):
    from analysis.energy_model import fit

    def flat(earlier, now_s):
        rate = _flat([e for e in earlier if e.end_s >= now_s - window_days * 86400.0])
        return None if rate is None else (lambda s: (rate * s.distance_km, None))

    def model(earlier, now_s):
        m = estimate([e for e in earlier if e.end_s >= now_s - window_days * 86400.0])
        if m is None:
            return None
        return lambda s: (m.predict(s.temp_c, s.avg_speed_kmh, None, s.hvac_on)[0]
                          * s.distance_km, None)

    def physics(earlier, now_s):
        m = fit([e for e in earlier if e.end_s >= now_s - physics_window_days * 86400.0])
        return None if m is None else m.drive

    return {"flat": flat, "model": model, "physics": physics}


def _band(lo, hi, value, bands):
    return (lo is None or value >= lo) and (hi is None or value < hi)


def _breakdown(rows, key, field, bands) -> list[dict]:
    out = []
    for label, lo, hi in bands:
        errs = [(r[key] - r["actual_kwh"], r["km"]) for r in rows
                if r.get(field) is not None and _band(lo, hi, r[field], bands)]
        out.append({"band": label, **(_score(errs) if errs else {"drives": 0})})
    return out


def evaluate(samples: list[DriveSample], last: int | None = MAX_PREDICTIONS,
             window_days: float = WINDOW_DAYS,
             physics_window_days: float = PHYSICS_WINDOW_DAYS,
             candidates=CANDIDATES, usable_kwh: float = USABLE_KWH) -> dict:
    """Predict each of the newest `last` drives (None: all) from the drives
    before it, by every candidate; score them. `samples` need start_s/end_s;
    drives without them, or too short to have a rate of their own, are not
    predicted (they still count as history)."""
    from analysis.energy_model import t_quantile

    timed = sorted((s for s in samples if s.start_s is not None and s.end_s is not None),
                   key=lambda s: s.end_s)
    targets = [s for s in timed if _rows([s])]
    if last is not None:
        targets = targets[-max(1, last):]
    group = outings(timed)
    train = _trainers(window_days, physics_window_days)
    names = [c for c in candidates if c in train]
    past_err = {c: [] for c in names}    # (per-km error, km) of earlier predictions
    past_z2: list[float] = []
    rows = []
    really = {c: 0 for c in names}
    for s in targets:
        earlier = [e for e in timed
                   if e.end_s <= s.start_s and group[id(e)] != group[id(s)]]
        flat = train["flat"](earlier, s.start_s)
        if flat is None:
            continue
        flat_kwh = flat(s)[0]
        actual = s.energy_kwh
        row = {"end_s": s.end_s, "km": round(s.distance_km, 1),
               "actual_kwh": round(actual, 3), "temp_c": s.temp_c,
               "speed_kmh": s.avg_speed_kmh,
               "climb_m_per_km": (s.net_climb_m / s.distance_km
                                  if s.net_climb_m is not None else None)}
        for c in names:
            pred = flat if c == "flat" else train[c](earlier, s.start_s)
            used = pred is not None
            kwh, sd = pred(s) if used else (flat_kwh, None)
            if used and c != "flat":
                really[c] += 1
            elif c == "flat":
                really[c] += 1
            if c == "physics" and used and sd:
                k = calibration(past_z2)
                q = t_quantile(BAND_Q[1], max(3, len(past_z2)))
                lo, hi = kwh - q * k * sd, kwh + q * k * sd
                past_z2.append(((actual - kwh) / sd) ** 2)
            else:
                errs = past_err[c]
                if len(errs) >= MIN_PREDICTIONS:
                    lo = kwh + s.distance_km * _quantile(errs, BAND_Q[0])
                    hi = kwh + s.distance_km * _quantile(errs, BAND_Q[1])
                else:
                    lo = kwh * (1 - ASSUMED_RATE_ERROR)
                    hi = kwh * (1 + ASSUMED_RATE_ERROR)
            past_err[c].append(((actual - kwh) / s.distance_km, s.distance_km))
            row[f"{c}_kwh"] = round(kwh, 3)
            row[f"{c}_inside"] = lo <= actual <= hi
            row[c] = used
        rows.append(row)

    out = {"predicted": len(rows), "really": really, "drives": rows, "candidates": {}}
    for c in names:
        key = f"{c}_kwh"
        errs = [(r[key] - r["actual_kwh"], r["km"]) for r in rows]
        if not errs:
            out["candidates"][c] = None
            continue
        own = [r for r in rows if r[c]]
        out["candidates"][c] = {
            **_score(errs),
            "really_predicted": really[c],
            "coverage": round(sum(r[f"{c}_inside"] for r in own) / len(own), 3)
            if own else None,
            "coverage_by_length": [
                {"band": label, "drives": len(sel),
                 "coverage": round(sum(r[f"{c}_inside"] for r in sel) / len(sel), 3)
                 if sel else None}
                for label, lo, hi in LENGTH_BANDS
                for sel in [[r for r in own if _band(lo, hi, r["km"], LENGTH_BANDS)]]],
            # What a range planned today would be widened by, and on how many
            # drives that rests (physics only; the others use quantiles).
            "calibration": round(calibration(past_z2), 3) if c == "physics" else None,
            "calibration_drives": len(past_z2) if c == "physics" else None,
            "arrival_mae_pct": round(100.0 * sum(abs(e) for e, _ in errs)
                                     / len(errs) / usable_kwh, 2),
            "arrival_max_pct": round(100.0 * max(abs(e) for e, _ in errs)
                                     / usable_kwh, 2),
            "by_temperature": _breakdown(rows, key, "temp_c", TEMP_BANDS),
            "by_speed": _breakdown(rows, key, "speed_kmh", SPEED_BANDS),
            "by_length": _breakdown(rows, key, "km", LENGTH_BANDS),
            "by_climb": (_breakdown(rows, key, "climb_m_per_km", CLIMB_BANDS)
                         if any(r["climb_m_per_km"] is not None for r in rows) else None),
        }
    out.update(_verdict(out))
    return out


def _verdict(out: dict) -> dict:
    cands = out["candidates"]
    flat = cands.get("flat")
    if not flat:
        return {"verdict": "flat", "reason": "no drive could be predicted yet"}
    f = flat["error_kwh_per_100km"]
    judged = {c: v for c, v in cands.items()
              if c != "flat" and v and v["really_predicted"] >= MIN_PREDICTIONS}
    if not judged:
        most = max((v["really_predicted"] for c, v in cands.items() if c != "flat" and v),
                   default=0)
        return {"verdict": "flat",
                "reason": (f"the models predicted only {most} drive(s) from earlier "
                           f"ones; a model needs {MIN_PREDICTIONS} to be judged")}
    best, v = min(judged.items(), key=lambda kv: kv[1]["error_kwh_per_100km"])
    m = v["error_kwh_per_100km"]
    label = {"model": "the linear model", "physics": "the physics model"}[best]
    if m < f * (1.0 - MARGIN):
        return {"verdict": best,
                "reason": (f"over {out['predicted']} drives, each predicted from the "
                           f"ones before it, {label} was off by {m:.1f} kWh/100 km "
                           f"against the flat rate's {f:.1f}")}
    return {"verdict": "flat",
            "reason": (f"over {out['predicted']} drives the best model was off by "
                       f"{m:.1f} kWh/100 km against the flat rate's {f:.1f}: not "
                       f"enough better (needs {MARGIN * 100:.0f} %)")}


def backtest(samples: list[DriveSample], window_days: float = WINDOW_DAYS,
             last: int = MAX_PREDICTIONS, candidates=CANDIDATES) -> dict:
    """evaluate(), with the summary routing/car.py and the tool read:
    `flat`/`model`/`physics` scores, `drives` with each one's kWh,
    `model_predicted` (the linear model's), `band_coverage` (the linear
    model's planner range), `verdict` and `reason`."""
    got = evaluate(samples, last=last, window_days=window_days, candidates=candidates)
    cands = got["candidates"]

    def short(c):
        v = cands.get(c)
        return None if not v else {k: v[k] for k in (
            "drives", "km", "mae_kwh", "rmse_kwh", "error_kwh_per_100km",
            "bias_kwh_per_100km")}

    drives = [{"end_s": r["end_s"], "km": r["km"], "actual_kwh": r["actual_kwh"],
               "flat_kwh": r["flat_kwh"],
               "model_kwh": r.get("model_kwh", r["flat_kwh"]),
               "model": r.get("model", False),
               "physics_kwh": r.get("physics_kwh", r["flat_kwh"]),
               "physics": r.get("physics", False),
               "temp_c": r["temp_c"], "speed_kmh": r["speed_kmh"]} for r in got["drives"]]
    model = cands.get("model")
    return {"predicted": got["predicted"],
            "model_predicted": got["really"].get("model", 0),
            "physics_predicted": got["really"].get("physics", 0),
            "drives": drives, "flat": short("flat"), "model": short("model"),
            "physics": short("physics"),
            "band_coverage": model["coverage"] if model else None,
            "evaluation": got, "verdict": got["verdict"], "reason": got["reason"]}


def error_spread(result: dict, model: bool | str, low_q: float = 0.1,
                 high_q: float = 0.9) -> dict | None:
    """How much more than predicted (kWh/km; negative: less) this car used on
    the backtest's drives, as the distance-weighted low_q and high_q
    quantiles -- for a candidate's predictions (`model`: "flat", "model",
    "physics"; True means "model", False "flat"). None with fewer than
    MIN_PREDICTIONS drives."""
    name = {True: "model", False: "flat"}.get(model, model)
    key = f"{name}_kwh"
    errs = [((d["actual_kwh"] - d[key]) / d["km"], d["km"])
            for d in result.get("drives") or [] if d["km"] > 0 and key in d]
    if len(errs) < MIN_PREDICTIONS:
        return None
    return {"low": _quantile(errs, low_q), "high": _quantile(errs, high_q),
            "drives": len(errs), "low_q": low_q, "high_q": high_q}
