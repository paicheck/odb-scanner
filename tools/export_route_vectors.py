"""Write tests/data/route_vectors.json: trip-planner cases with their answers.

The dashboard (routing/planner.py) and the phone (route/Planner.kt) must give
the same plan for the same trip. Both test suites run every case in this file
through their planner and compare against the answers recorded here, which
come from the Python planner. tests/test_route_planner.py fails when the file
is stale; regenerate with

    python tools/export_route_vectors.py

The routes are synthetic (straight lines due north from 50 N, one point per
km) so the file stays small and the arithmetic is easy to check by hand; each
case passes its chargers to the planner in file order. The file also carries
the price-text cases (routing/prices.py <-> route/Prices.kt) and the leave-at
levels.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from routing.planner import (  # noqa: E402
    CarModel,
    Charger,
    Conditions,
    PlanSettings,
    RateError,
    charge_power_kw,
    charge_seconds,
    leave_levels,
    plan_trip,
)
from routing.prices import own_prices, parse_usage_cost, price_for  # noqa: E402
from routing.providers import encode_polyline  # noqa: E402

OUT = os.path.join(ROOT, "tests", "data", "route_vectors.json")
KM_PER_DEG_LAT = 2 * math.pi * 6371.0088 / 360


def _line(km: int, elevation=None) -> list[list]:
    """A straight route due north from (50, 0), one point per km."""
    pts = []
    for k in range(km + 1):
        ele = elevation(k) if elevation else None
        pts.append([round(50.0 + k / KM_PER_DEG_LAT, 6), 0.0,
                    None if ele is None else round(ele, 1)])
    return pts


def _charger_at(points, km, name, kw, offset_km=1.0, price=None, fee=0.0,
                operator="", id="", usage_type=None):
    lat, lon = points[km][0], points[km][1]
    km_per_deg_lon = KM_PER_DEG_LAT * math.cos(math.radians(lat))
    return {"name": name, "lat": lat,
            "lon": round(lon + offset_km / km_per_deg_lon, 6),
            "power_kw": kw, "operator": operator, "address": "",
            "price_per_kwh": price, "fee": fee, "id": id, "usage_type": usage_type}


def _hill(k):
    # Up 300 m over km 20-50, down again over km 50-80.
    if k <= 20:
        return 100.0
    if k <= 50:
        return 100.0 + 10.0 * (k - 20)
    if k <= 80:
        return 400.0 - 10.0 * (k - 50)
    return 100.0


def _cases():
    long = _line(400)
    every50 = [_charger_at(long, k, f"C{k}", kw)
               for k, kw in ((50, 150), (100, 50), (150, 350), (200, 75),
                             (250, 150), (300, 43), (350, 120))]
    yield {"name": "flat 100 km, no charging", "points": _line(100),
           "drive_s": 3600, "start_pct": 80, "chargers": []}
    yield {"name": "hill up and down", "points": _line(100, _hill),
           "drive_s": 3600, "start_pct": 80, "chargers": []}
    yield {"name": "missing elevation counts as flat",
           "points": [p[:2] + [None if i % 3 == 0 else p[2]]
                      for i, p in enumerate(_line(100, _hill))],
           "drive_s": 3600, "start_pct": 80, "chargers": []}
    yield {"name": "400 km with stops", "points": long, "drive_s": 15000,
           "start_pct": 90, "chargers": every50}
    yield {"name": "slow chargers are ignored", "points": long, "drive_s": 15000,
           "start_pct": 90,
           "chargers": [_charger_at(long, k, f"S{k}", 22) for k in range(20, 400, 20)]
           + [_charger_at(long, 230, "Fast230", 100)]}
    yield {"name": "no chargers: unreachable", "points": long, "drive_s": 15000,
           "start_pct": 90, "chargers": []}
    yield {"name": "charger too far from the route", "points": long, "drive_s": 15000,
           "start_pct": 90,
           "chargers": [_charger_at(long, 200, "Far", 150, offset_km=8.0)]}
    yield {"name": "low start, high reserve, charge to 90",
           "points": _line(250), "drive_s": 9000, "start_pct": 40,
           "settings": {"reserve_pct": 20, "charge_to_pct": 90},
           "chargers": [_charger_at(_line(250), k, f"D{k}", 150)
                        for k in range(30, 250, 30)]}
    yield {"name": "heavy consumption",
           "points": long, "drive_s": 15000, "start_pct": 100,
           "car": {"consumption_kwh_per_km": 0.24},
           "chargers": every50}
    priced = []
    for k in range(40, 400, 40):
        priced.append(_charger_at(long, k, f"Fast{k}", 150, price=0.79))
        priced.append(_charger_at(long, k + 3, f"Cheap{k}", 50, price=0.45))
        priced.append(_charger_at(long, k + 6, f"Fee{k}", 100, price=0.30, fee=6.0))
    for mode in ("fastest", "balanced", "cheapest"):
        yield {"name": f"prices: {mode}", "points": long, "drive_s": 15000,
               "start_pct": 90, "settings": {"mode": mode}, "chargers": priced}
    yield {"name": "prices: balanced, time worth 40/h", "points": long,
           "drive_s": 15000, "start_pct": 90,
           "settings": {"mode": "balanced", "time_value_per_hour": 40},
           "chargers": priced}
    yield {"name": "unknown prices use the default", "points": long, "drive_s": 15000,
           "start_pct": 90,
           "settings": {"mode": "cheapest", "default_price_per_kwh": 0.5},
           "chargers": [_charger_at(long, k, f"U{k}", 150) for k in range(60, 400, 60)]
           + [_charger_at(long, 200, "Dear", 150, price=0.95)]}
    yield {"name": "start low, charge at the start", "points": _line(200),
           "drive_s": 7200, "start_pct": 12,
           "chargers": [_charger_at(_line(200), 0, "Here", 150, offset_km=0.5),
                        _charger_at(_line(200), 120, "Later", 150)]}
    yield {"name": "start below the reserve, nothing near", "points": _line(100),
           "drive_s": 3600, "start_pct": 5, "chargers": []}
    # Charger choice: networks, access, the driver's picks.
    nets = []
    for k in range(40, 400, 40):
        nets.append(_charger_at(long, k, f"Alpha{k}", 150, price=0.50,
                                operator="Alpha Charge", id=f"a{k}", usage_type=1))
        nets.append(_charger_at(long, k + 2, f"Ion{k}", 150, price=0.55,
                                operator="IONITY GmbH", id=f"i{k}", usage_type=5))
        nets.append(_charger_at(long, k + 4, f"Club{k}", 150, price=0.35,
                                operator="Club Volt", id=f"c{k}", usage_type=4))
    yield {"name": "networks: none set", "points": long, "drive_s": 15000,
           "start_pct": 90, "chargers": nets}
    yield {"name": "networks: avoid one", "points": long, "drive_s": 15000,
           "start_pct": 90, "settings": {"avoid_networks": ["club volt", "alpha"]},
           "chargers": nets}
    yield {"name": "networks: prefer one", "points": long, "drive_s": 15000,
           "start_pct": 90, "settings": {"prefer_networks": ["ionity"],
                                         "public_only": True},
           "chargers": nets}
    yield {"name": "networks: prefer, bigger discount", "points": long,
           "drive_s": 15000, "start_pct": 90,
           "settings": {"prefer_networks": ["Alpha"], "prefer_discount": 0.6},
           "chargers": nets}
    yield {"name": "access: public only", "points": long, "drive_s": 15000,
           "start_pct": 90, "settings": {"public_only": True}, "chargers": nets}
    yield {"name": "picks: avoid one, use another", "points": long, "drive_s": 15000,
           "start_pct": 90,
           "settings": {"avoid_chargers": ["c120", "a120"], "use_chargers": ["i200"]},
           "chargers": nets}
    # Weather: [km from, temp C or None, wind km/h at 10 m or None, from deg].
    # The road runs due north, so wind from 0 is a headwind.
    yield {"name": "weather: headwind, then calm", "points": long, "drive_s": 15000,
           "start_pct": 90, "chargers": every50,
           "weather": [[0, 4.0, 35.0, 10.0], [200, 6.0, 0.0, None]]}
    yield {"name": "weather: tailwind, cold air", "points": _line(250),
           "drive_s": 9000, "start_pct": 90, "chargers": [],
           "weather": [[0, -8.0, 30.0, 180.0], [120, None, 30.0, 225.0]]}
    yield {"name": "weather: crosswind on a hill", "points": _line(100, _hill),
           "drive_s": 3600, "start_pct": 80, "chargers": [],
           "weather": [[0, 12.0, 40.0, 90.0]]}
    # Arrival ranges: kWh/km more (high) or less (low) than planned.
    yield {"name": "range: measured, lopsided", "points": long, "drive_s": 15000,
           "start_pct": 90, "chargers": every50, "rate_error": [-0.012, 0.031]}
    yield {"name": "range: wide enough to reach below the reserve", "points": _line(250),
           "drive_s": 9000, "start_pct": 90, "chargers": [],
           "rate_error": [-0.01, 0.05]}


def _run(case):
    car = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17)
    for k, v in (case.get("car") or {}).items():
        setattr(car, k, v)
    settings = PlanSettings(**(case.get("settings") or {}))
    chargers = [Charger(**c) for c in case["chargers"]]
    weather = [Conditions(*w) for w in case.get("weather") or []] or None
    err = case.get("rate_error")
    plan = plan_trip([tuple(p) for p in case["points"]], case["drive_s"],
                     case["start_pct"], car, settings, chargers, weather=weather,
                     rate_error=RateError(err[0], err[1], "vector") if err else None
                     ).as_dict()
    band = plan["arrival_band"]
    return {k: plan[k] for k in ("status", "distance_km", "arrival_pct",
                                 "unreachable_at_km", "charge_s", "cost")} | {
        "stops": [{k: s[k] for k in ("km", "name", "arrive_pct", "depart_pct",
                                     "charge_min", "kwh", "cost", "id", "preferred",
                                     "arrive_low_pct", "arrive_high_pct")}
                  | {"alternatives": [a["id"] for a in s["alternatives"]]}
                  for s in plan["stops"]],
        "profile_points": len(plan["profile"]),
        "profile_last": plan["profile"][-1],
        "arrival_band": [band["low_pct"], band["high_pct"], band["below_reserve"]]
        if band else None,
        "wind_kwh": plan["weather"]["wind_kwh"] if plan["weather"] else None,
    }


def build() -> dict:
    car = CarModel(usable_kwh=53.2, consumption_kwh_per_km=0.17)
    cases = []
    for case in _cases():
        case.setdefault("car", {})
        case.setdefault("settings", {})
        case["expect"] = _run(case)
        cases.append(case)
    return {
        "_comment": "Generated by tools/export_route_vectors.py -- do not edit. "
                    "Defaults: car usable_kwh 53.2, consumption 0.17 kWh/km, other "
                    "CarModel/PlanSettings fields at their defaults unless a case "
                    "overrides them. Chargers: all within radius_km, file order.",
        "charge_power": [[pct, kw, round(charge_power_kw(pct, car.charge_curve, kw), 6)]
                         for pct in (0, 25, 35, 55, 79.5, 95, 100)
                         for kw in (50, 150)],
        "charge_seconds": [[a, b, kw, round(charge_seconds(a, b, car, kw), 3)]
                           for a, b, kw in ((10, 80, 150), (10, 80, 50),
                                            (5, 100, 350), (79.5, 80, 150),
                                            (80, 80, 150), (12.3, 47.9, 75))],
        "leave_levels": [
            [r, t, leave_levels(PlanSettings(reserve_pct=r, charge_to_pct=t))]
            for r, t in ((10, 80), (20, 62), (0, 100), (50, 55))],
        "usage_cost": [[text, *(parse_usage_cost(text) or (None, None))]
                       for text in USAGE_COST_TEXTS],
        "price_for": [[op, text, *price_for(op, text, own_prices(OWN), 0.79)]
                      for op, text in (("IONITY GmbH", "£0.69/kWh"),
                                       ("InstaVolt Ltd", "£0.85/kWh"),
                                       ("Shell Recharge", ""),
                                       ("Lidl", "Free to use"),
                                       ("Tesla (including non-tesla)", "£0.60/kWh"))],
        "own_prices": [OWN, own_prices(OWN)],
        "polyline": [[list(p) for p in POLYLINE_EXAMPLE],
                     encode_polyline(POLYLINE_EXAMPLE)],
        "cases": cases,
    }


# Price texts as Open Charge Map lists them (UK rapid chargers, 2026), and
# the awkward ones: pence, fees, DC/AC splits, other currencies, nonsense.
USAGE_COST_TEXTS = (
    "£0.79/kWh", "£0.79/kWh; other tariffs available", "DC: £0.79/kWh",
    "DC: £0.79/kWh,AC: £0.49/kWh",
    "DC (contactless): £0.85/kWh, DC (GRIDSERVE app): £0.79/kWh, AC: £0.49/kWh",
    "£0.20/kWh; min £1.50", "£0.30/kWh;min £1.50",
    "Rapid £1.00 fee then £0.30p per kWh AC socket 50p fee then 30p per kWh",
    "£1.00 + £0.30/kWh", "29p/kWh", "0.69 GBP per kWh", "50p connection fee, 45p/kWh",
    "75p", "Free", "Free to use", "€0.59/kWh", "£12/kWh", "Pay at location", "",
)
OWN = "Ionity = 0.39\nTesla=£0.55\nnonsense\nCheap = 9"
# Google's worked example for the encoded-polyline format.
POLYLINE_EXAMPLE = ((38.5, -120.2), (40.7, -120.95), (43.252, -126.453))


def render(data: dict) -> str:
    """Indented JSON with every innermost list of numbers on one line."""
    text = json.dumps(data, indent=1, ensure_ascii=False)
    return re.sub(r"\[\s*([-\d.eE+]+|null)(?:,\s*(?:[-\d.eE+]+|null))*\s*\]",
                  lambda m: re.sub(r"\s+", "", m.group(0)).replace(",", ", "),
                  text) + "\n"


def main() -> None:
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(build()))
    print(f"wrote {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
