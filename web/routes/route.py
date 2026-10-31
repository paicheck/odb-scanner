"""Route planner page: an EV trip from here (or anywhere) to a destination.

Registered by web/dashboard.py:create_app. The heavy lifting is in routing/:
this module only reads the form, calls the providers (OpenRouteService,
OpenChargeMap, and Mapbox for today's traffic when a token is set -- from the
server, so keys never reach the page) and renders the plan. Planning never
touches the vehicle.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)

ROUTE_LINE_MAX = 1500          # map polyline points sent to the page
TRAFFIC_LINE_MAX = 1500        # heavy-traffic polyline points, all stretches together


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


def _names(raw) -> list[str]:
    """Network names from a comma-separated string or a list: trimmed, no
    repeats, at most 20 of at most 60 characters."""
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    out: list[str] = []
    for item in items:
        name = str(item).strip()[:60]
        if name and name.lower() not in (o.lower() for o in out):
            out.append(name)
    return out[:20]


_CHARGER_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def _ids(raw) -> list[str]:
    """Charger IDs from a comma-separated string: only ID-shaped ones, at most
    30, so a hand-edited URL cannot grow without bound."""
    out: list[str] = []
    for item in str(raw or "").split(","):
        item = item.strip()
        if _CHARGER_ID.match(item) and item not in out:
            out.append(item)
    return out[:30]


def _with_picks(params, avoid_ids, use_ids) -> str:
    """This page's URL with the per-trip charger picks replaced."""
    kept = [(k, v) for k, v in params.multi_items() if k not in ("avoid_ids", "use_ids")]
    if avoid_ids:
        kept.append(("avoid_ids", ",".join(avoid_ids)))
    if use_ids:
        kept.append(("use_ids", ",".join(use_ids)))
    return "/route?" + urlencode(kept)


def _add_pick_links(params, plan: dict, avoid_ids, use_ids) -> None:
    """Each stop: a link that plans again without it. Each alternative: one
    that plans again with it and without the stop it would replace."""
    for stop in plan["stops"]:
        sid = stop.get("id") or ""
        if sid:
            stop["avoid_url"] = _with_picks(
                params, avoid_ids + [sid], [i for i in use_ids if i != sid])
        for alt in stop.get("alternatives") or []:
            aid = alt.get("id") or ""
            if aid:
                alt["use_url"] = _with_picks(
                    params,
                    [i for i in avoid_ids if i != aid] + ([sid] if sid else []),
                    [i for i in use_ids if i != sid] + [aid])


def _cfg_num(cfg, key, default, lo, hi):
    """A bounded number from config; the default when absent or unusable."""
    try:
        v = float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def _traffic_lines(points, runs) -> list[dict]:
    """Each heavy-traffic stretch as a polyline for the map: {level, line}."""
    from routing.planner import cumulative_km
    cum = cumulative_km(points)
    total = sum(1 for k in cum for a, b, _ in runs if a <= k <= b) or 1
    step = max(1, -(-total // TRAFFIC_LINE_MAX))
    out = []
    for a, b, level in runs:
        idx = [i for i, k in enumerate(cum) if a <= k <= b][::step]
        line = [[points[i][0], points[i][1]] for i in idx]
        if len(line) >= 2:
            out.append({"level": level, "line": line})
    return out


def register(app, ctx) -> None:
    cfg = ctx.cfg
    map_tiles = ctx.map_tiles
    render = ctx.render
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/route", response_class=HTMLResponse)
    def route_page(request: Request):
        from analysis.position import positions
        from routing import prices, providers
        from routing.car import DEFAULT_CONSUMPTION, DEFAULT_USABLE_KWH, car_energy
        from routing.planner import MODES, CarModel, PlanSettings, plan_trip

        p = request.query_params
        _, vid = vehicle_and_id()
        car = car_energy(repo, vid, cfg) if vid else None
        fix = positions(repo, vid, 30)["latest"] if vid else None
        ors_key, ocm_key = providers.keys(cfg)
        token = providers.mapbox_token(cfg)
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
            "mode": next(m for m in (p.get("mode"), cfg.get("routing.mode"), "balanced")
                         if m in MODES),
            # Ticked boxes once the form has been sent; until then, config's.
            "avoid": ([a for a in providers.AVOID if p.get(f"avoid_{a}")]
                      if p.get("avoid_set")
                      else providers.avoid_list(cfg.get("routing.avoid", []) or [])),
            "way": int(_num(p, "way", 0, 5) or 0),
            # Networks and access, like the road boxes: the form's once sent.
            "prefer": _names(p.get("prefer", "") if p.get("net_set")
                             else cfg.get("routing.prefer_networks", []) or []),
            "avoid_nets": _names(p.get("avoid_nets", "") if p.get("net_set")
                                 else cfg.get("routing.avoid_networks", []) or []),
            "public_only": (bool(p.get("public_only")) if p.get("net_set")
                            else bool(cfg.get("routing.public_only", False))),
            # Per-trip picks, from the stop links.
            "avoid_ids": _ids(p.get("avoid_ids")),
            "use_ids": _ids(p.get("use_ids")),
        }
        form["clear_picks_url"] = _with_picks(p, [], [])
        min_kw = float(cfg.get("routing.min_charger_kw", 50))
        own = prices.own_prices(cfg.get("routing.prices", {}) or {})
        default_price = _cfg_num(cfg, "routing.default_price_per_kwh", 0.79, 0.05, 2.0)
        time_value = _cfg_num(cfg, "routing.time_value_per_hour", 15.0, 0.0, 500.0)
        context = dict(car=car, fix=fix, form=form, plan=None, error=None,
                       origin=None, destination=None, alternatives=[],
                       avoid_names=providers.AVOID, ways=[], traffic_note=None,
                       traffic_lines=[], live_traffic=bool(token),
                       way_query=urlencode([(k, v) for k, v in p.multi_items()
                                            if k != "way"]),
                       min_charger_kw=min_kw, map_tiles=map_tiles, modes=MODES,
                       time_value=time_value, own_prices=own,
                       keys_missing=[n for n, k in (("OpenRouteService", ors_key),
                                                    ("OpenChargeMap", ocm_key)) if not k])
        if not form["to"]:
            return render("route.html", request, **context)

        try:
            ors = providers.OpenRouteService(ors_key, providers.countries(cfg))
            ocm = providers.OpenChargeMap(ocm_key, min_kw=min_kw)
            near = (fix["lat"], fix["lon"]) if fix else None
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
            # The destination is looked for near the start, not near the car.
            dests = ors.geocode(form["to"], near=(origin.lat, origin.lon))
            if not dests:
                raise providers.RoutingError(f"No place found for “{form['to']}”.")
            dest = dests[0]
            context.update(origin=origin, destination=dest, alternatives=dests[1:])
            ways = []
            if token:
                try:
                    ways = providers.Mapbox(token).routes(
                        (origin.lat, origin.lon), (dest.lat, dest.lon), form["avoid"])
                except providers.RoutingError as exc:
                    context["traffic_note"] = (f"No live traffic: {exc} "
                                               "This route is from OpenRouteService.")
            if ways:
                pick = min(form["way"], len(ways) - 1)
                chosen = ways[pick]
                # Mapbox gives no heights: ORS adds them (flat if that fails).
                route = providers.Route(ors.elevate(chosen.points), chosen.distance_km,
                                        chosen.duration_s)
                context["ways"] = [dict(
                    n=i, summary=w.summary or "—", minutes=round(w.duration_s / 60),
                    km=round(w.distance_km), heavy_km=round(w.heavy_km, 1),
                    delay_min=None if w.delay_s is None else round(w.delay_s / 60),
                    chosen=i == pick) for i, w in enumerate(ways)]
                context["traffic_lines"] = _traffic_lines(chosen.points, chosen.runs)
            else:
                route = ors.route((origin.lat, origin.lon), (dest.lat, dest.lon),
                                  form["avoid"])

            settings = PlanSettings(reserve_pct=form["reserve"],
                                    charge_to_pct=form["charge_to"],
                                    min_charger_kw=min_kw, mode=form["mode"],
                                    time_value_per_hour=time_value,
                                    default_price_per_kwh=default_price,
                                    avoid_networks=tuple(form["avoid_nets"]),
                                    prefer_networks=tuple(form["prefer"]),
                                    prefer_discount=_cfg_num(
                                        cfg, "routing.prefer_discount", 0.25, 0.0, 0.9),
                                    public_only=form["public_only"],
                                    avoid_chargers=tuple(form["avoid_ids"]),
                                    use_chargers=tuple(form["use_ids"]))
            # This car's own model plans the trip once its backtest has earned
            # that (routing/car.py), unless the driver changed the figure the
            # form shows -- the form always sends it back.
            mine = car and car.get("consumption_model")
            if mine and abs(form["consumption"]
                            - car["consumption_kwh_per_100km"]) > 0.05:
                mine = None
            model = CarModel(
                usable_kwh=car["usable_kwh"] if car else DEFAULT_USABLE_KWH,
                consumption_kwh_per_km=form["consumption"] / 100.0,
                consumption_model=mine or None)
            speed = (route.distance_km / (route.duration_s / 3600.0)
                     if route.duration_s > 0 else None)
            chargers = ocm.along(route.points, settings.search_radius_km)
            for c in chargers:
                c.price_per_kwh, c.fee, c.price_source = prices.price_for(
                    c.operator, c.usage_cost, own, default_price)
            plan = plan_trip(route.points, route.duration_s, form["start_pct"],
                             model, settings, chargers, avg_speed_kmh=speed)
            step = max(1, -(-len(route.points) // ROUTE_LINE_MAX))
            line = [[pt[0], pt[1]] for pt in route.points[::step]]
            if line[-1] != list(route.points[-1][:2]):
                line.append(list(route.points[-1][:2]))
            planned = plan.as_dict()
            _add_pick_links(p, planned, form["avoid_ids"], form["use_ids"])
            context.update(plan=planned, route_line=line)
        except providers.RoutingError as exc:
            context["error"] = str(exc)
        except Exception as exc:              # a planner bug must not 500
            log.exception("route planning failed")
            context["error"] = f"Planning failed ({type(exc).__name__}: {exc})."
        return render("route.html", request, **context)
