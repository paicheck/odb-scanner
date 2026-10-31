"""Which way of predicting consumption works on this car? Read-only.

    python tools/backtest_consumption.py --db ~/odb_data/odb_scanner.db
    python tools/backtest_consumption.py --db PATH --drives      # every drive
    python tools/backtest_consumption.py --synthetic [--seed N] [--days N]

Runs the backtest the Route page runs (routing/car.py consumption_check,
analysis/consumption_backtest.py) on a database opened read-only: each of the
newest drives is predicted from the drives before it (never from its own
outing) by the flat rate, the linear model and the physics model, and the
errors are compared overall and by temperature, speed, length and gradient,
with how often each one's 80 % range held the real figure. Prints the
verdict and the model the Route page would use. Never writes; never talks
to the car. (SQLite may create the empty -wal/-shm files it coordinates
readers with beside a WAL database; the database itself is not changed.)

--synthetic runs the same comparison on a year of synthetic drives with a
known truth (analysis/synthetic_journeys.py) -- every drive predicted, not
just the newest. It shows whether a method works, not how this car drives.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.consumption_model import FEATURE_LABELS  # noqa: E402
from analysis.energy_model import NAMES, PRIOR  # noqa: E402
from routing.car import consumption_check  # noqa: E402

LABELS = {"flat": "flat", "model": "linear", "physics": "physics"}


class ReadOnly:
    """Just enough of a Repository for routing/car.py, opened mode=ro."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row


def _vehicle(repo: ReadOnly, vid: int | None) -> int | None:
    rows = repo.conn.execute("SELECT id FROM vehicles ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    if vid is not None:
        return vid if vid in ids else None
    return ids[0] if len(ids) == 1 else None


def _num(v, fmt):
    return "-" if v is None else format(v, fmt)


def evaluation_lines(ev: dict) -> list[str]:
    """The comparison, as text: one row per method, then each breakdown."""
    cands = {c: v for c, v in ev["candidates"].items() if v}
    if not cands:
        return ["  no drive could be predicted yet"]
    lines = ["  method    off kWh/100km   bias   kWh a drive (rms)   arrival % mean/worst"
             "   80 % range held   really predicted"]
    for c, v in cands.items():
        lines.append(
            f"  {LABELS[c]:<8} {v['error_kwh_per_100km']:9.2f}"
            f"   {v['bias_kwh_per_100km']:+6.2f}"
            f"   {v['mae_kwh']:6.2f} ({v['rmse_kwh']:.2f})        "
            f"{v['arrival_mae_pct']:5.2f} / {v['arrival_max_pct']:5.1f}"
            f"        {_num(v['coverage'] and v['coverage'] * 100, '5.0f')} %"
            f"            {v['really_predicted']}")
    for title, key in (("outside temperature", "by_temperature"),
                       ("average speed", "by_speed"), ("length", "by_length"),
                       ("gradient", "by_climb")):
        first = next(iter(cands.values()))
        if not first.get(key):
            if key == "by_climb":
                lines.append("By gradient: no climb recorded for these drives.")
            continue
        lines.append(f"By {title} (kWh/100 km off: "
                     + " / ".join(LABELS[c] for c in cands) + "):")
        for i, band in enumerate(first[key]):
            if not band["drives"]:
                lines.append(f"  {band['band']:<22} no drives")
                continue
            cells = " / ".join(_num(v[key][i].get("error_kwh_per_100km"), ".2f")
                               for v in cands.values())
            few = "  (too few to say)" if band["drives"] < 5 else ""
            lines.append(f"  {band['band']:<22} {cells}   ({band['drives']} drives){few}")
    lines.append("80 % range held, by length ("
                 + " / ".join(LABELS[c] for c in cands) + "):")
    first = next(iter(cands.values()))
    for i, band in enumerate(first["coverage_by_length"]):
        cells = " / ".join(_num(v["coverage_by_length"][i]["coverage"] and
                                v["coverage_by_length"][i]["coverage"] * 100, ".0f")
                           for v in cands.values())
        lines.append(f"  {band['band']:<22} {cells} %   ({band['drives']} drives)")
    return lines


def model_lines(model) -> list[str]:
    if model is None:
        return []
    if getattr(model, "kind", "") == "physics":
        out = [f"Model in use: physics, from {model.n_drives} drives, "
               f"{model.sample_km:.0f} km ({model.excluded} left out as implausible, "
               f"{model.downweighted} down-weighted). Coefficients against the "
               "physics prior they started from:"]
        units = {"base": "kWh/km", "drag": "kWh/km at 100 km/h", "time": "kW",
                 "heat": "kW per °C below 18", "cold": "kWh/km per 10 °C below 15",
                 "warmup": "kWh per 10 °C below 15, per drive"}
        for i, n in enumerate(NAMES):
            sd = model.cov[i][i] ** 0.5
            m0, s0 = PRIOR[n]
            out.append(f"  {n:<7} {model.mean[i]:8.4f} ± {sd:.4f} {units[n]}"
                       f"   (prior {m0} ± {s0})")
        out.append(f"  spread over a journey {model.journey_var ** 0.5 * 100:.2f} "
                   f"kWh/100 km, wind part {model.wind_var ** 0.5 * 100:.2f} "
                   "at 100 km/h, "
                   f"local {model.local_var ** 0.5 * 100:.2f} per √km")
        return out
    out = [f"Model in use: linear, {model.base_kwh_per_km * 100:.1f} kWh/100 km in "
           f"usual conditions, spread {model.residual_std * 100:.1f}; from "
           f"{model.n_drives} drives, {model.sample_km:.0f} km"]
    for f in model.features_used:
        coeff = getattr(model, {"temp": "temp_coeff", "speed": "speed_coeff",
                                "hvac": "hvac_coeff", "elev": "elev_coeff"}[f])
        lo, hi = model.ranges[f]
        unit = {"temp": "°C", "speed": "km/h", "hvac": "on vs off",
                "elev": "m climbed a km"}[f]
        out.append(f"  {FEATURE_LABELS[f]}: {coeff * 100:+.3f} kWh/100 km per "
                   f"{unit}, around {model.means[f]:.1f} (seen {lo:.1f} to {hi:.1f})")
    return out


def drive_lines(rows: list[dict]) -> list[str]:
    out = ["ended (UTC)        km   actual   flat  linear physics"]
    for d in rows:
        when = datetime.fromtimestamp(d["end_s"], timezone.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"{when}  {d['km']:5.1f}  {d['actual_kwh']:6.2f} {d['flat_kwh']:6.2f} "
                   f"{d.get('model_kwh', d['flat_kwh']):6.2f} "
                   f"{d.get('physics_kwh', d['flat_kwh']):6.2f}"
                   + ("" if d.get("physics") or d.get("model")
                      else "  (flat: no model yet)"))
    return out


def report(repo: ReadOnly, vid: int, drives: bool = False) -> str:
    check = consumption_check(repo, vid)
    bt = check["backtest"]
    ev = bt["evaluation"]
    lines = [f"Vehicle {vid}: {bt['predicted']} drives predicted, each from the drives "
             "before it (never from its own outing)."]
    lines += evaluation_lines(ev)
    lines.append(f"Verdict: {bt['verdict']} -- {check['basis']}")
    lines += model_lines(check["model"])
    if drives:
        lines += drive_lines(ev["drives"])
    return "\n".join(lines)


def synthetic_report(seed: int, days: int, glitches: float, drives: bool) -> str:
    from analysis.consumption_backtest import evaluate
    from analysis.energy_model import fit
    from analysis.synthetic_journeys import journeys, samples
    ss = samples(journeys(days, seed=seed, glitches=glitches))
    ev = evaluate(ss, last=None)
    lines = [f"SYNTHETIC drives (seed {seed}, {days} days, {glitches * 100:.0f} % bad "
             f"readings) -- a known truth, not this car. {ev['predicted']} of "
             f"{len(ss)} drives predicted, each from the drives before it."]
    lines += evaluation_lines(ev)
    lines.append(f"Verdict: {ev['verdict']} -- {ev['reason']}")
    lines += model_lines(fit(ss))
    if drives:
        lines += drive_lines(ev["drives"])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="database to read (opened read-only)")
    ap.add_argument("--vehicle", type=int, help="vehicle id, if there is more than one")
    ap.add_argument("--drives", action="store_true", help="list every predicted drive")
    ap.add_argument("--synthetic", action="store_true",
                    help="judge the methods on synthetic drives instead")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--glitches", type=float, default=0.0,
                    help="share of synthetic drives with a wrong energy reading")
    args = ap.parse_args(argv)
    if args.synthetic:
        print(synthetic_report(args.seed, args.days, args.glitches, args.drives))
        return 0
    if not args.db:
        print("Give --db PATH (read-only) or --synthetic.", file=sys.stderr)
        return 2
    if not os.path.exists(args.db):
        print(f"No database at {args.db}", file=sys.stderr)
        return 2
    repo = ReadOnly(args.db)
    try:
        vid = _vehicle(repo, args.vehicle)
        if vid is None:
            print("Say which vehicle with --vehicle (or it was not found).",
                  file=sys.stderr)
            return 2
        print(report(repo, vid, args.drives))
    finally:
        repo.conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
