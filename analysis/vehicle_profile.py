"""What this car has shown: an evidence-based profile, measured facts kept
apart from what a model infers, and every recent drive against what was
predicted for it beforehand.

Three kinds of statement, never mixed:

* measured -- straight from the car's own drives and charging sessions:
  kWh/100 km by speed and by outside temperature, the charging curve where
  sessions show it. Each with how many drives and km it rests on; under
  ENOUGH_DRIVES drives or ENOUGH_KM km a band says "too few to say" instead
  of a figure that looks precise.
* inferred -- what the physics model (analysis/energy_model.py) makes of
  those drives: consumption at a steady speed, what heating draws, a cold
  start, the range. Each with the uncertainty of the coefficients behind it,
  and flagged when the drives have barely moved it from the physics it
  started from ("mostly assumed").
* predicted against actual -- the backtest's drives (analysis/
  consumption_backtest.py): what the method in use would have predicted for
  each before it was driven, and what it took; the error also as % of the
  battery, which is what a driver feels at arrival.

Hills are not in it: the car's drives carry no height, so how much a climb
costs or a descent gives back is the planner's physics, not this car's.

Pure function of the routing/car.py consumption_check result and the
charging curve points; no I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone

from analysis.consumption_backtest import SPEED_BANDS, TEMP_BANDS
from analysis.energy_model import Conditions

ENOUGH_DRIVES = 3
ENOUGH_KM = 30.0
MOSTLY_ASSUMED = 0.3        # coefficient sd narrowed by less than this share
CRUISE_KMH = (80, 100, 120)
LEDGER_ROWS = 20


def _band(samples, field, bands) -> list[dict]:
    out = []
    for label, lo, hi in bands:
        sel = [s for s in samples if getattr(s, field) is not None
               and (lo is None or getattr(s, field) >= lo)
               and (hi is None or getattr(s, field) < hi)
               and s.energy_kwh is not None and s.energy_kwh > 0 and s.distance_km >= 5]
        km = sum(s.distance_km for s in sel)
        kwh = sum(s.energy_kwh for s in sel)
        enough = len(sel) >= ENOUGH_DRIVES and km >= ENOUGH_KM
        out.append({"band": label, "drives": len(sel), "km": round(km, 1),
                    "kwh_per_100km": round(100 * kwh / km, 1) if enough else None,
                    "enough": enough})
    return out


def _inferred(model, usable_kwh) -> list[dict]:
    if model is None or getattr(model, "kind", "") != "physics":
        return []
    out = []

    def cruise(v, t):
        c = Conditions(t, v * v, 1.0 / v)
        rate, _ = model.rate_sd(c)
        return rate, model.coef_sd(c)

    drag_learned = model.learned("drag")
    for t in (15, 0):
        for v in CRUISE_KMH:
            rate, sd = cruise(v, t)
            out.append({"what": f"steady {v} km/h at {t} °C",
                        "value": round(rate * 100, 1),
                        "sd": round(sd * 100, 1), "unit": "kWh/100 km",
                        "range_km": round(usable_kwh / rate),
                        "mostly_assumed": drag_learned < MOSTLY_ASSUMED})
    heat = model.mean[3]
    heat_sd = model.cov[3][3] ** 0.5
    out.append({"what": "heating at 0 °C", "value": round(heat * 18, 2),
                "sd": round(heat_sd * 18, 2), "unit": "kW",
                "mostly_assumed": model.learned("heat") < MOSTLY_ASSUMED})
    warm = model.mean[5]
    out.append({"what": "a cold start at 0 °C (warming the cabin)",
                "value": round(warm * 1.5, 2),
                "sd": round(model.cov[5][5] ** 0.5 * 1.5, 2), "unit": "kWh",
                "mostly_assumed": model.learned("warmup") < MOSTLY_ASSUMED})
    out.append({"what": "auxiliaries and town stop-start",
                "value": round(model.mean[2], 2),
                "sd": round(model.cov[2][2] ** 0.5, 2), "unit": "kW",
                "mostly_assumed": model.learned("time") < MOSTLY_ASSUMED})
    names = {"base": "rolling", "drag": "drag", "time": "auxiliaries",
             "heat": "heating", "cold": "a cold pack", "warmup": "a cold start"}
    for i in out:
        term = next((n for n, word in names.items() if word in i["what"]), None)
        if term in model.held:
            i["held"] = True
    return out


def _ledger(check, usable_kwh) -> dict:
    bt = check.get("backtest") or {}
    ev = bt.get("evaluation") or {}
    rows = ev.get("drives") or []
    used = bt.get("verdict") if check.get("model") is not None else "flat"
    key = f"{used}_kwh"
    lines = []
    for r in rows:
        pred = r.get(key, r["flat_kwh"])
        err = pred - r["actual_kwh"]
        lines.append({"end_s": r["end_s"],
                      "ended": datetime.fromtimestamp(r["end_s"], timezone.utc).isoformat(
                          timespec="seconds"),
                      "km": r["km"], "actual_kwh": r["actual_kwh"],
                      "predicted_kwh": round(pred, 2), "error_kwh": round(err, 2),
                      "arrival_error_pct": round(100 * -err / usable_kwh, 1),
                      "temp_c": r.get("temp_c"), "speed_kmh": r.get("speed_kmh")})
    if not lines:
        return {"method": used, "drives": 0, "rows": []}
    errs = [abs(x["arrival_error_pct"]) for x in lines]
    km = sum(x["km"] for x in lines)
    return {"method": used, "drives": len(lines),
            "arrival_mae_pct": round(sum(errs) / len(errs), 2),
            "arrival_worst_pct": round(max(errs), 1),
            "bias_kwh_per_100km": round(100 * sum(x["error_kwh"] for x in lines) / km, 2)
            if km else None,
            "rows": list(reversed(lines))[:LEDGER_ROWS]}


def build(check: dict, usable_kwh: float, curve_points=None) -> dict:
    """The profile from a routing/car.py consumption_check result. When the
    planner does not use the physics model (it did not earn it), the model
    is still fitted to say what the drives suggest -- marked as not used."""
    from analysis.consumption_backtest import PHYSICS_WINDOW_DAYS
    from analysis.energy_model import fit
    samples = check.get("samples") or []
    model = check.get("model")
    used = model is not None and getattr(model, "kind", "") == "physics"
    if not used and samples:
        newest = max(s.end_s for s in samples)
        model = fit([s for s in samples
                     if s.end_s >= newest - PHYSICS_WINDOW_DAYS * 86400])
        if model is not None and model.n_drives < 10:
            model = None
    rated = [s for s in samples if s.energy_kwh is not None and s.distance_km >= 5]
    cold = _band(samples, "temp_c", TEMP_BANDS)
    out = {
        "drives": len(samples),
        "km": round(sum(s.distance_km for s in samples), 1),
        "rated_drives": len(rated),
        "measured": {"by_speed": _band(samples, "avg_speed_kmh", SPEED_BANDS),
                     "by_temperature": cold},
        "inferred": _inferred(model, usable_kwh),
        "inferred_used": used,
        "inferred_drives": model.n_drives if model is not None else 0,
        "model_basis": check.get("basis"),
        "predictions": _ledger(check, usable_kwh),
        "charging": [p for p in curve_points or [] if p.get("measured")],
        "not_measured": ["Hills: the drives carry no height, so what a climb costs "
                         "and a descent gives back is the planner's physics, not "
                         "this car's."],
    }
    lo, hi = cold[0], cold[-1]
    if lo["enough"] and hi["enough"]:
        out["cold_costs"] = round(lo["kwh_per_100km"] - hi["kwh_per_100km"], 1)
    return out
