"""Charging-session list and per-session pages.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from analysis import charging as charging_analysis
from analysis.charging import session_cost


def register(app, ctx) -> None:
    charging_days = ctx.charging_days
    chart_gap_s = ctx.chart_gap_s
    render = ctx.render
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/charging", response_class=HTMLResponse)
    def charging_page(request: Request):
        _, vid = vehicle_and_id()
        sessions = repo.charging_sessions(charging_days, vid) if vid else []
        comparison = (
            charging_analysis.session_comparison(repo, vid, charging_days) if vid else {}
        )
        corr = (
            charging_analysis.correlate_dtc_with_sessions(repo, vid, charging_days)
            if vid
            else {}
        )
        return render(
            "charging.html",
            request,
            sessions=[dict(s) for s in sessions],
            costs={s["id"]: session_cost(s, ctx.cfg) for s in sessions},
            tariff_set=ctx.cfg.get("charging.home_tariff_per_kwh") is not None,
            comparison=comparison,
            correlation=corr,
        )

    # Charging samples are written by the collector during a detected session;
    # the table has no provenance or per-field source-key column, so each panel
    # says what it is rather than borrowing a catalogue key's evidence level.
    _SAMPLE_FIELDS = (
        ("soc", "charging_soc", "State of charge (sampled during session)", "%"),
        ("power_kw", "charging_power_kw",
         "Instantaneous power (vehicle side, sampled)", "kW"),
        ("current_a", "charging_current_a", "Current (sampled)", "A"),
        ("voltage_v", "charging_voltage_v", "Voltage (sampled)", "V"),
        ("battery_temp_c", "charging_battery_temp_c",
         "Battery temperature (sampled)", "degC"),
    )

    @app.get("/charging/{session_id}", response_class=HTMLResponse)
    def charging_session_page(request: Request, session_id: int):
        """One charging session: aligned panels on a shared time axis, the
        collector's energy estimate with its method, and sample coverage."""
        from analysis.chart_semantics import (
            build_series,
            coverage,
            shared_x_range,
        )
        from decoders.quality import DataKind, MeasurementQuality, Verification

        _, vid = vehicle_and_id()
        sess = repo.charging_session(session_id) if vid else None
        if sess is None or sess["vehicle_id"] != vid:
            raise HTTPException(status_code=404, detail="No such charging session")
        samples = [dict(r) for r in repo.charging_samples(session_id)]
        series, panels = {}, []
        for field, key, name, unit in _SAMPLE_FIELDS:
            quality = MeasurementQuality(
                key=key, verification=Verification.UNKNOWN,
                data_kind=DataKind.NUMERIC, unit=unit, use_in_scoring=False,
                plausible_basis="not established for charging samples")
            s = build_series(
                key, [(r["ts"], r[field], None) for r in samples], name=name,
                unit=unit, quality=quality, gap_s=chart_gap_s, max_points=0)
            series[key] = s
            panels.append({"id": key, "title": name, "question": "",
                           "note": "", "left": [key], "right": [],
                           "empty": s["stats"]["n"] == 0})
        cov = coverage([r["ts"] for r in samples], chart_gap_s)
        return render(
            "charging_session.html", request, s=dict(sess), samples=samples,
            cost=session_cost(sess, ctx.cfg),
            sample_coverage=cov, panels=panels, series=series,
            panel_data={"series": series, "visible": dict.fromkeys(series, True),
                        "x_range": shared_x_range(series), "gap_s": chart_gap_s},
        )
