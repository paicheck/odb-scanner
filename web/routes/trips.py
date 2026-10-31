"""The trip log: this car's drives, what each cost, and what the cold costs.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from analysis import trips as trips_analysis
from analysis import vehicle_profile


def _profile(repo, cfg, vid) -> dict:
    """What this car has shown (analysis/vehicle_profile.py)."""
    from routing.car import DEFAULT_USABLE_KWH, _latest, charge_curve, consumption_check
    usable = _latest(repo, vid, "hv_energy_max")
    _, _, points = charge_curve(repo, vid, cfg)
    return vehicle_profile.build(consumption_check(repo, vid),
                                 usable[1] / 1000.0 if usable else DEFAULT_USABLE_KWH,
                                 points)


def register(app, ctx) -> None:
    _window_days = ctx._window_days
    _windows = ctx._windows
    render = ctx.render
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/trips", response_class=HTMLResponse)
    def trips_page(request: Request):
        _, vid = vehicle_and_id()
        days = _window_days(request)
        log = trips_analysis.trip_log(repo, vid, days) if vid else None
        profile = _profile(repo, ctx.cfg, vid) if vid else None
        return render("trips.html", request, days=days, windows=_windows(days),
                      log=log, profile=profile, min_rate_km=trips_analysis.MIN_RATE_KM,
                      park_min=trips_analysis.PARK_S // 60,
                      gap_min=trips_analysis.MAX_GAP_S // 60)

    @app.get("/api/trips")
    def api_trips(request: Request):
        _, vid = vehicle_and_id()
        days = _window_days(request)
        return trips_analysis.trip_log(repo, vid, days) if vid else {
            "days": days, "drives": [], "count": 0}

    @app.get("/api/vehicle-profile")
    def api_vehicle_profile(request: Request):
        """Measured, inferred and predicted-against-actual, kept apart."""
        _, vid = vehicle_and_id()
        return _profile(repo, ctx.cfg, vid) if vid else {"drives": 0}
