"""Overview, sources, measurements, progress, status and quality pages.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse

from analysis import (
    battery as battery_analysis,
)
from analysis import (
    charging as charging_analysis,
)
from analysis import (
    dtc as dtc_analysis,
)
from web.dashboard import OVERVIEW_PANELS, _int_param


def register(app, ctx) -> None:
    cfg = ctx.cfg
    _fresh_window = ctx._fresh_window
    _profile = ctx._profile
    map_tiles = ctx.map_tiles
    map_default_view = ctx.map_default_view
    bms_refusal = ctx.bms_refusal
    charging_days = ctx.charging_days
    chart_gap_s = ctx.chart_gap_s
    measurement_catalog = ctx.measurement_catalog
    measurement_sources = ctx.measurement_sources
    panels_context = ctx.panels_context
    render = ctx.render
    repo = ctx.repo
    svc = ctx.svc
    trend_days = ctx.trend_days
    vehicle_and_id = ctx.vehicle_and_id

    # ---- pages --------------------------------------------------------------
    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        """Where every measurement's value came from.

        Phase 13 of the BMS work: until this existed, an empty battery page and a
        page full of values carried over from a Car Scanner export looked
        identical. Both read as "no live data", and only one is a defect.
        """
        rows, counts = measurement_sources()
        return render("sources.html", request, sources=rows, counts=counts)

    @app.get("/measurements", response_class=HTMLResponse)
    def measurements_page(request: Request):
        groups, chart_groups, counts = measurement_catalog()
        return render(
            "measurements.html",
            request,
            groups=groups,
            chart_groups=chart_groups,
            counts=counts,
            days=trend_days,
            windows=sorted({1, 7, 14, 30, 90, 365, trend_days}),
            max_points=1500,
            method="min_max",
            gap_s=chart_gap_s,
        )

    @app.get("/progress", response_class=HTMLResponse)
    def progress_page(request: Request):
        return render("progress.html", request, p=progress_data())

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        _, vid = vehicle_and_id()
        days = trend_days
        battery = battery_analysis.battery_overview(repo, days, vid) if vid else {}
        dtcs = dtc_analysis.dtc_summary(repo.dtc_list(vid)) if vid else {}
        charging = (
            charging_analysis.session_comparison(repo, vid, charging_days) if vid else {}
        )
        anomalies = [dict(a) for a in repo.anomalies(vid, days)] if vid else []
        from analysis.health_summary import health_summary as _health_summary
        from analysis.twin import vehicle_state
        # The same threshold the collector alerts on.
        low_12v = float(cfg.get("collector.low_12v_voltage", 12.0))
        health = _health_summary(repo, vid, low_12v=low_12v) if vid else {}
        state = vehicle_state(repo, vid) if vid else None

        # Per-key series with their own observation timestamps. The chart used
        # to read the battery snapshot table, which carries values forward and
        # merged normalised and absolute SoC into one line.
        panels = panels_context(OVERVIEW_PANELS, days)
        from analysis import data_quality
        quality = (data_quality.snapshot(repo, vid, days, _profile(), chart_gap_s)
                   if vid else None)
        from analysis import position
        where = position.positions(repo, vid, days) if vid else None
        # The headline tiles read the newest live reading of each NAMED signal
        # -- the same rows the charts draw -- and carry its time. They used to
        # mix sources: "State of charge" showed the display SoC from the last
        # battery snapshot (92.9 % at 14:30) above a chart of the BMS SoC whose
        # latest was 88.8 % at 14:51, with nothing saying which or when. And
        # the 12 V tile went blank once its reading was a day old, which hid
        # the reading instead of saying how old it was.
        latest = repo.latest_measurements(vid, live_only=True) if vid else {}

        def newest(*keys):
            rows = [dict(latest[k], key=k) for k in keys if latest.get(k)]
            return max(rows, key=lambda r: r["ts"]) if rows else {}

        live = {
            "soc": newest("soc_abs"),
            "soc_display": newest("soc_normal"),
            "pack_voltage": newest("pack_voltage"),
            "lv": newest("lv_voltage_obd", "aux_12v_voltage"),
            "speed": newest("vehicle_speed"),
        }
        return render(
            "overview.html",
            request,
            battery=battery,
            dtcs=dtcs,
            charging=charging,
            anomalies=anomalies,
            health=health,
            state=state,
            days=days,
            live=live,
            gap_s=chart_gap_s,
            bms_refusal=bms_refusal(),
            quality=quality,
            where=where,
            map_tiles=map_tiles,
            map_default_view=map_default_view,
            **panels,
        )

    @app.get("/api/health")
    def api_health():
        _, vid = vehicle_and_id()
        dtcs = dtc_analysis.dtc_summary(repo.dtc_list(vid)) if vid else {}
        critical = dtcs.get("by_category", {}).get("potentially critical", 0)
        return {
            "vehicle_known": bool(vid),
            "dtc_total": dtcs.get("total", 0),
            "critical_dtcs": critical,
            "ollama_available": svc.llm.available(),
        }

    def progress_data():
        """Live collection progress: last activity, recent reads, per-ECU
        answer rates, and the latest diagnostic events. Read-only."""
        _, vid = vehicle_and_id()
        if not vid:
            return {"vehicle_known": False}

        window_s = 600  # 10-minute sliding window
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=window_s)).isoformat(timespec="seconds")

        last_ts = repo.conn.execute(
            "SELECT MAX(ts) FROM measurements WHERE vehicle_id=?", (vid,)
        ).fetchone()[0]
        seconds_ago = None
        if last_ts:
            try:
                seconds_ago = round(
                    (now - datetime.fromisoformat(last_ts)).total_seconds(), 1
                )
            except ValueError:
                seconds_ago = None

        totals = {
            # From the summaries: a COUNT(*) here walked every reading on
            # every poll of the progress page.
            "measurements": sum(t["n"] for t in repo.measurement_totals(vid)),
            "battery_snapshots": repo.conn.execute(
                "SELECT COUNT(*) FROM battery_measurements WHERE vehicle_id=?", (vid,)
            ).fetchone()[0],
            "dtcs": repo.conn.execute(
                "SELECT COUNT(*) FROM dtcs WHERE vehicle_id=?", (vid,)
            ).fetchone()[0],
        }
        recent = {
            "success": repo.conn.execute(
                "SELECT COUNT(*) FROM measurements "
                "WHERE vehicle_id=? AND ts>=? AND success=1",
                (vid, cutoff),
            ).fetchone()[0],
            "failed": repo.conn.execute(
                "SELECT COUNT(*) FROM measurements "
                "WHERE vehicle_id=? AND ts>=? AND success=0",
                (vid, cutoff),
            ).fetchone()[0],
            "battery_snapshots": repo.conn.execute(
                "SELECT COUNT(*) FROM battery_measurements WHERE vehicle_id=? AND ts>=?",
                (vid, cutoff),
            ).fetchone()[0],
        }
        ecu_rows = repo.conn.execute(
            "SELECT ecu, SUM(success) AS ok, SUM(1-success) AS bad, MAX(ts) AS last "
            "FROM measurements WHERE vehicle_id=? AND ts>=? "
            "GROUP BY ecu ORDER BY ok DESC",
            (vid, cutoff),
        ).fetchall()
        discovered = {r["key"]: str(r["status"] or "") for r in repo.list_ecus(vid)}
        events = repo.conn.execute(
            "SELECT ts, kind, description FROM diagnostic_events "
            "WHERE vehicle_id=? ORDER BY ts DESC LIMIT 12",
            (vid,),
        ).fetchall()

        return {
            "vehicle_known": True,
            "now": now.isoformat(timespec="seconds"),
            "last_ts": last_ts,
            "seconds_ago": seconds_ago,
            "window_s": window_s,
            "totals": totals,
            "recent": recent,
            "ecus": [
                {
                    "ecu": r["ecu"],
                    "success": r["ok"],
                    "failed": r["bad"],
                    "last": r["last"],
                    "discovery": discovered.get(r["ecu"], ""),
                }
                for r in ecu_rows
            ],
            "events": [
                {"ts": e["ts"], "kind": e["kind"], "description": e["description"]}
                for e in events
            ],
        }

    @app.get("/api/progress")
    def api_progress():
        return progress_data()

    @app.get("/status", response_class=HTMLResponse)
    def status_page(request: Request):
        from analysis import freshness
        from analysis.provenance import key_states
        from analysis.provenance import summary as prov_summary
        from decoders.registry import build_default_registry

        _, vid = vehicle_and_id()
        modules, silence, prov, sources = [], {}, {}, {}
        if vid:
            modules = freshness.module_status(repo, vid)
            silence = freshness.silence_summary(modules)
            registry = build_default_registry(_profile())
            rows = key_states(repo, vid, registry,
                              stale_after_s=_fresh_window())
            prov = prov_summary(rows)
            # Freshness of the anchor keys a reader checks first.
            for k in ("soc_abs", "pack_voltage", "lv_voltage_obd",
                      "battery_temp", "odometer_km", "charge_mode"):
                sources[k] = freshness.observe_key(
                    repo, vid, k, fresh_after_s=_fresh_window()).as_dict()
        return render("status.html", request, modules=modules, silence=silence,
                      provenance=prov, observations=sources)

    @app.get("/quality", response_class=HTMLResponse)
    def quality_page(request: Request):
        from analysis import decoder_lab
        from decoders.quality import summary

        _, vid = vehicle_and_id()
        limit = _int_param(request, "limit", 80, 0, 100_000)
        rows = decoder_lab.lab_rows(repo, vid, _profile()) if vid else []
        # Show problems first, then cap: the page exists to surface failures.
        shown = rows[:limit] if limit > 0 else rows
        counts = summary(_profile())
        by_verdict = {}
        for r in rows:
            by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
        return render("quality.html", request, rows=shown, counts=counts,
                      by_verdict=by_verdict, total=len(rows), limit=limit)

    @app.get("/api/quality")
    def api_quality():
        from analysis import decoder_lab
        from decoders.quality import quality_rows, summary

        _, vid = vehicle_and_id()
        return {
            "summary": summary(_profile()),
            "registry": quality_rows(_profile()),
            "evaluations": decoder_lab.lab_rows(repo, vid, _profile())
            if vid else [],
        }
