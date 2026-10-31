"""Does this car's own consumption model beat the flat rate? Read-only.

    python tools/backtest_consumption.py --db ~/odb_data/odb_scanner.db
    python tools/backtest_consumption.py --db PATH --drives      # every drive

Runs the backtest the Route page runs (routing/car.py consumption_check,
analysis/consumption_backtest.py) on a database opened read-only: each of the
newest drives is predicted from the drives before it, by the flat rate and by
the model, and the errors are compared. Prints the verdict, the errors, and
the model the Route page would use. Never writes; never talks to the car.
(SQLite may create the empty -wal/-shm files it coordinates readers with
beside a WAL database; the database itself is not changed.)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.consumption_model import FEATURE_LABELS  # noqa: E402
from routing.car import consumption_check  # noqa: E402


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


def _row(name: str, s: dict | None) -> str:
    if not s:
        return f"  {name:<6} -"
    return (f"  {name:<6} {s['error_kwh_per_100km']:6.2f} kWh/100 km off, bias "
            f"{s['bias_kwh_per_100km']:+.2f}; {s['mae_kwh']:.2f} kWh a drive "
            f"(rms {s['rmse_kwh']:.2f}) over {s['drives']} drives, {s['km']:.0f} km")


def report(repo: ReadOnly, vid: int, drives: bool = False) -> str:
    check = consumption_check(repo, vid)
    bt, model = check["backtest"], check["model"]
    lines = [f"Vehicle {vid}: {bt['predicted']} drives predicted, "
             f"{bt['model_predicted']} of them by a model.",
             _row("flat", bt["flat"]), _row("model", bt["model"])]
    if bt["band_coverage"] is not None:
        lines.append(f"  the model's band held the actual rate on "
                     f"{bt['band_coverage'] * 100:.0f} % of the drives it predicted "
                     "(one spread each side: about 68 % is right)")
    lines.append(f"Verdict: {bt['verdict']} -- {check['basis']}")
    if model is not None:
        lines.append(f"Model: {model.base_kwh_per_km * 100:.1f} kWh/100 km in usual "
                     f"conditions, spread {model.residual_std * 100:.1f}; from "
                     f"{model.n_drives} drives, {model.sample_km:.0f} km")
        for f in model.features_used:
            coeff = getattr(model, {"temp": "temp_coeff", "speed": "speed_coeff",
                                    "hvac": "hvac_coeff", "elev": "elev_coeff"}[f])
            lo, hi = model.ranges[f]
            unit = {"temp": "°C", "speed": "km/h", "hvac": "on vs off",
                    "elev": "m climbed a km"}[f]
            lines.append(f"  {FEATURE_LABELS[f]}: {coeff * 100:+.3f} kWh/100 km per "
                         f"{unit}, around {model.means[f]:.1f} "
                         f"(seen {lo:.1f} to {hi:.1f})")
    if drives:
        lines.append("ended (UTC)        km   actual  flat   model")
        for d in bt["drives"]:
            when = datetime.fromtimestamp(d["end_s"], timezone.utc).strftime(
                "%Y-%m-%d %H:%M")
            lines.append(f"{when}  {d['km']:5.1f}  {d['actual_kwh']:6.2f} "
                         f"{d['flat_kwh']:6.2f} {d['model_kwh']:6.2f}"
                         + ("" if d["model"] else "  (flat: no model yet)"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="database to read (opened read-only)")
    ap.add_argument("--vehicle", type=int, help="vehicle id, if there is more than one")
    ap.add_argument("--drives", action="store_true", help="list every predicted drive")
    args = ap.parse_args(argv)
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
