"""Route planner page: an EV trip from here (or anywhere) to a destination.

Registered by web/dashboard.py:create_app. The heavy lifting is in routing/:
this module only reads the form, calls the providers (OpenRouteService,
OpenChargeMap -- from the server, so keys never reach the page) and renders
the plan. Planning never touches the vehicle.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

ROUTE_LINE_MAX = 1500          # map polyline points sent to the page


def _num(params, name, lo, hi):
    """A bounded float from the query string, or None when absent/unusable."""
    raw = (params.get(name) or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def _pick(*values):
    return next(v for v in values if v is not None)


def register(app, ctx) -> None:
    cfg = ctx.cfg
    map_tiles = ctx.map_tiles
    render = ctx.render
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/route", response_class=HTMLResponse)
    def route_page(request: Request):
        from analysis.position import positions
        from routing import providers
        from routing.car import DEFAULT_CONSUMPTION, DEFAULT_USABLE_KWH, car_energy
        from routing.planner import CarModel, PlanSettings, plan_trip

        p = request.query_params
        _, vid = vehicle_and_id()
        car = car_energy(repo, vid, cfg) if vid else None
        fix = positions(repo, vid, 30)["latest"] if vid else None
        ors_key, ocm_key = providers.keys(cfg)
        # Every field resolved here -- typed value, else this car, else config,
        # else the default -- so the form shows exactly what was planned with.
        form = {
            "to": (p.get("to") or "").strip()[:200],
            "frm": (p.get("from") or "").strip()[:200],
            "start_pct": _pick(_num(p, "start_pct", 1, 100),
                               car and car["start_pct"], 80.0),
            "consumption": _pick(_num(p, "consumption", 8, 40),
                                 car and car["consumption_kwh_per_100km"],
                                 DEFAULT_CONSUMPTION * 100),
            "reserve": _pick(_num(p, "reserve", 0, 50),
                             float(cfg.get("routing.reserve_pct", 10))),
            "charge_to": _pick(_num(p, "charge_to", 20, 100),
                               float(cfg.get("routing.charge_to_pct", 80))),
        }
        min_kw = float(cfg.get("routing.min_charger_kw", 50))
        context = dict(car=car, fix=fix, form=form, plan=None, error=None,
                       origin=None, destination=None, alternatives=[],
                       min_charger_kw=min_kw, map_tiles=map_tiles,
                       keys_missing=[n for n, k in (("OpenRouteService", ors_key),
                                                    ("OpenChargeMap", ocm_key)) if not k])
        if not form["to"]:
            return render("route.html", request, **context)

        try:
            ors = providers.OpenRouteService(ors_key)
            ocm = providers.OpenChargeMap(ocm_key, min_kw=min_kw)
            near = (fix["lat"], fix["lon"]) if fix else None
            dests = ors.geocode(form["to"], near=near)
            if not dests:
                raise providers.RoutingError(f"No place found for “{form['to']}”.")
            if form["frm"]:
                starts = ors.geocode(form["frm"], near=near)
                if not starts:
                    raise providers.RoutingError(f"No place found for “{form['frm']}”.")
                origin = starts[0]
            elif fix:
                origin = providers.Place("Car's last recorded position",
                                         fix["lat"], fix["lon"])
            else:
                raise providers.RoutingError(
                    "No GPS position recorded for the car yet: enter a start point.")
            dest = dests[0]
            context.update(origin=origin, destination=dest, alternatives=dests[1:])
            route = ors.route((origin.lat, origin.lon), (dest.lat, dest.lon))

            settings = PlanSettings(reserve_pct=form["reserve"],
                                    charge_to_pct=form["charge_to"],
                                    min_charger_kw=min_kw)
            model = CarModel(
                usable_kwh=car["usable_kwh"] if car else DEFAULT_USABLE_KWH,
                consumption_kwh_per_km=form["consumption"] / 100.0)
            plan = plan_trip(route.points, route.duration_s, form["start_pct"],
                             model, settings, ocm.near)
            step = max(1, -(-len(route.points) // ROUTE_LINE_MAX))
            line = [[pt[0], pt[1]] for pt in route.points[::step]]
            if line[-1] != list(route.points[-1][:2]):
                line.append(list(route.points[-1][:2]))
            context.update(plan=plan.as_dict(), route_line=line)
        except providers.RoutingError as exc:
            context["error"] = str(exc)
        except Exception as exc:              # a planner bug must not 500
            log.exception("route planning failed")
            context["error"] = f"Planning failed ({type(exc).__name__}: {exc})."
        return render("route.html", request, **context)
