"""Battery and range pages, and the chart-series API they use.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from analysis import battery as battery_analysis
from web.dashboard import (
    _KEY_PATTERN,
    BATTERY_PANELS,
    RANGE_PANELS,
    RANGE_SOC_PAIR_TOLERANCE_S,
    SCATTER_MAX_POINTS,
    _int_param,
)


def register(app, ctx) -> None:
    _profile = ctx._profile
    _registry_for_meta = ctx._registry_for_meta
    _window_days = ctx._window_days
    _windows = ctx._windows
    bms_refusal = ctx.bms_refusal
    chart_gap_s = ctx.chart_gap_s
    chart_series = ctx.chart_series
    panels_context = ctx.panels_context
    render = ctx.render
    repo = ctx.repo
    trend_days = ctx.trend_days
    vehicle_and_id = ctx.vehicle_and_id

    def _cell_sweep_series(vid, days: int):
        """Spread of each full cell sweep, with which cells were lowest/highest.

        Built from the individual `cell_voltages` readings (cell identity
        preserved), not from the BMS min/max DIDs, so the two can be compared.
        Uses the quality record of `cell_delta_mv`, the same quantity.
        """
        from analysis.chart_semantics import build_series
        from decoders.quality import quality_for

        registry = _registry_for_meta()
        expected = sum(1 for s in registry.all() if s.key.startswith("cell_v_"))
        rows = repo.cell_voltage_rows(vid, days) if vid else []
        summary = battery_analysis.cell_sweeps(
            [(r["ts"], r["cell_index"], r["voltage_v"]) for r in rows],
            expected_cells=expected or None)
        sweeps = summary["sweeps"]
        series = build_series(
            "cell_sweep_spread_mv",
            [(s["ts"], s["spread_mv"], "calculated") for s in sweeps],
            name="Cell voltage spread from individual cells (calculated)",
            unit="mV", quality=quality_for(_profile(), "cell_delta_mv"),
            gap_s=chart_gap_s)
        # Downsampled like every other series (spikes kept); each kept point
        # finds its note by its sweep's timestamp. Sending every sweep with
        # its note grew the page by ~100 bytes a minute of collecting.
        notes = {
            s["ts"]: f"lowest cell #{s['min_cell']:03d} {s['min_v']:.3f} V, "
                     f"highest cell #{s['max_cell']:03d} {s['max_v']:.3f} V, "
                     f"{s['cells']} cells"
                     + (f", {s['missing']} missing" if s["missing"] else "")
            for s in sweeps}
        series["point_notes"] = [notes.get(t, "") for t in series["ts"]]
        return series, summary

    @app.get("/battery", response_class=HTMLResponse)
    def battery_page(request: Request):
        from analysis.chart_semantics import axis_for

        _, vid = vehicle_and_id()
        days = _window_days(request)
        trend = (
            battery_analysis.cell_delta_trend(repo, days, vid)
            if vid
            else {"status": "no vehicle"}
        )
        ctx = panels_context(BATTERY_PANELS, days)
        sweep_series, sweeps = _cell_sweep_series(vid, days)
        ctx["series"]["cell_sweep_spread_mv"] = sweep_series
        ctx["panel_data"]["series"]["cell_sweep_spread_mv"] = sweep_series
        ctx["panel_data"]["visible"]["cell_sweep_spread_mv"] = True
        ctx["panels"].insert(4, {
            "id": "cell-spread", "title": "Cell voltage spread per sweep",
            "question": "How far apart were the lowest and highest cells?",
            "note": "CALCULATED from each full sweep of individual cell readings "
                    "(highest − lowest). The tooltip names the cells. No "
                    "imbalance threshold is drawn: the catalogue documents no "
                    "manufacturer limit for this pack.",
            "left": ["cell_sweep_spread_mv"], "right": [],
            "empty": sweep_series["stats"]["n"] == 0})
        latest = sweeps["latest"]
        latest_axis = axis_for("cell_v_000", "V",
                               [c["voltage_v"] for c in latest["cells"]]
                               if latest else [])
        ctx["panel_data"]["cell_bar"] = (
            {"latest": latest, "axis": latest_axis} if latest else None)
        soh = ctx["series"].get("soh_pct") or chart_series(["soh_pct"], days)["soh_pct"]
        return render(
            "battery.html", request, trend=trend, days=days,
            windows=_windows(days),
            sweeps=sweeps, soh=soh, bms_refusal=bms_refusal(), **ctx,
        )

    @app.get("/range", response_class=HTMLResponse)
    def range_page(request: Request):
        """Range estimates, state of charge, distance and vehicle-reported
        consumption -- kept as distinct quantities.

        Nothing here derives consumption from energy and distance: the inputs
        available are either UNKNOWN-confidence or not segmented into
        journeys, so such a figure would look precise without being so.
        """
        from analysis.chart_semantics import counter_delta, pair_nearest, thin_pairs

        _, vid = vehicle_and_id()
        days = _window_days(request)
        ctx = panels_context(RANGE_PANELS, days)
        pairs, distance = [], counter_delta([])
        if vid:
            rows = {k: [] for k in ("range_displayed", "soc_abs", "odometer_km")}
            for r in repo.measurement_series_many(vid, list(rows), days):
                rows[r["key"]].append((r["ts"], r["value"]))
            pairs = pair_nearest(rows["soc_abs"], rows["range_displayed"],
                                 RANGE_SOC_PAIR_TOLERANCE_S)
            distance = counter_delta(rows["odometer_km"])
        shown = thin_pairs(pairs, SCATTER_MAX_POINTS)
        ctx["panel_data"]["scatter"] = {
            "pairs": shown, "tolerance_s": RANGE_SOC_PAIR_TOLERANCE_S,
            "x": {"label": "State of charge (BMS)", "unit_label": "%"},
            "y": {"label": "Estimated range (displayed)", "unit_label": "miles"},
        }
        return render(
            "range.html", request, days=days,
            windows=_windows(days),
            pairs=pairs, pairs_shown=len(shown), distance=distance,
            pair_tolerance_s=RANGE_SOC_PAIR_TOLERANCE_S, **ctx,
        )

    @app.get("/api/chart/series")
    def api_chart_series(request: Request):
        """Annotated series for up to 12 keys, read in one query.

        Each series carries its catalogue unit and display label, axis policy
        and range, raw (pre-downsampling) statistics, plausibility flags,
        recording coverage and per-point provenance.
        """
        from analysis.chart_semantics import shared_x_range

        raw = request.query_params.get("keys") or ""
        keys = [k for k in (p.strip() for p in raw.split(",")) if k]
        if not keys:
            raise HTTPException(status_code=400, detail="keys is required")
        if len(keys) > 12 or not all(_KEY_PATTERN.match(k) for k in keys):
            raise HTTPException(status_code=400,
                                detail="up to 12 keys of [A-Za-z0-9_]")
        days = _window_days(request)
        max_points = _int_param(request, "max_points", 1500, 20, 20000)
        series = chart_series(list(dict.fromkeys(keys)), days, max_points)
        return {"days": days, "gap_s": chart_gap_s,
                "x_range": shared_x_range(series), "series": series}

    # ---- JSON API ------------------------------------------------------------
    @app.get("/api/battery")
    def api_battery(request: Request):
        """Battery snapshots over the trend window, oldest first: all of them
        up to `max_points` (default 2000), else that many spread evenly, the
        first and last kept. A month of 10 s snapshots was 13 MB of JSON."""
        _, vid = vehicle_and_id()
        rows = repo.battery_history(trend_days, vid) if vid else []
        limit = _int_param(request, "max_points", 2000, 2, 100_000)
        if len(rows) > limit:
            rows = [rows[round(i * (len(rows) - 1) / (limit - 1))]
                    for i in range(limit)]
        return [dict(r) for r in rows]
