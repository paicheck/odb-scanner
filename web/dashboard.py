"""Local web dashboard (FastAPI + Jinja2 + Chart.js).

READ-ONLY: no endpoint can transmit anything to the vehicle. The dashboard
only reads from SQLite; collection runs as a separate process (main.py collect).

Network-exposed binds require an access token -- see web/auth.py.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ai.ollama import OllamaError
from ai.service import DEFAULT_QUESTIONS, AnalysisService
from analysis import battery as battery_analysis
from analysis import charging as charging_analysis
from analysis import dtc as dtc_analysis
from config import Config
from database.repository import Repository
from web.auth import (
    QUERY,
    CookiePromotionMiddleware,
    SameOriginMiddleware,
    SecurityHeadersMiddleware,
    TokenAuthMiddleware,
    resolve_token,
)

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_value(value):
    """Render one measurement value for the dashboard.

    Most measurements are numbers, but not all of them are: the gear selector
    answers "P" and the VIN is a string, and both are stored as TEXT in
    `measurements.value` -- that column has no declared type, so SQLite keeps
    whatever the decoder produced. Formatting one of those with `%.4g` raised
    `TypeError: must be real number, not str`, which turned the entire
    /measurements and /sources page into a 500. It went unnoticed because it
    only bites once the collector can actually read the modules that answer
    with text: while those readings were missing, there was nothing to format.

    Numbers keep the four-significant-figure rendering. Anything else is shown
    as it was recorded -- displayed, not rounded, not guessed at, not dropped.
    """
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        return format(value, ".4g")
    return str(value)


_templates.env.filters["fmtvalue"] = _format_value

# The question is the only unbounded user input in the app: it goes into an
# LLM prompt and, on the default model, occupies a GPU for minutes. Without a
# cap a single request can stall the box for anyone else using it.
MAX_QUESTION_CHARS = 1000


def _redirect(url: str) -> RedirectResponse:
    """PRG redirect after a form POST, so a refresh does not re-submit."""
    return RedirectResponse(url, status_code=303)


def _first_number(*values):
    """First value that is not None.

    `a or b` is wrong for anything a sensor can legitimately report as zero:
    a flat battery is 0 %, a fully open contactor is 0 A. Those are facts, not
    missing data, and the fallback hides them.
    """
    for value in values:
        if value is not None:
            return value
    return None


def create_app(cfg: Config, bind_host: str | None = None) -> FastAPI:
    """Build the dashboard.

    `bind_host` is the address uvicorn will actually listen on. It decides
    whether the access token is required (see web/auth.py): passing it in
    rather than re-reading the config keeps the decision tied to the socket
    that is really opened, and lets tests exercise both modes without editing
    config. It defaults to the configured web.host, which is what main.py
    passes anyway.
    """
    repo = Repository(cfg.db_path)
    svc = AnalysisService(cfg, repo)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Repository opens a SQLite connection per thread and never got closed,
        # so every reload of the app leaked a handle set until the process
        # exited. On Windows that also holds a lock on the file, which is what
        # made `main.py prune` fail with PermissionError while the dashboard
        # was running.
        yield
        repo.close()

    app = FastAPI(
        title="ID.3 Diagnostic System",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    host = bind_host if bind_host is not None else cfg.get("web.host", "127.0.0.1")
    token = resolve_token(cfg, host)
    if token:
        log.info(
            "Dashboard bound to %s, so access requires ?%s=... once per "
            "browser. See the startup message for the value.",
            host,
            QUERY,
        )
    # Order matters, and Starlette applies these OUTERMOST-LAST: whatever is
    # added last is the first to see a request and the last to touch a
    # response. The intended nesting, outside in, is
    #
    #     SecurityHeaders -> SameOrigin -> CookiePromotion -> TokenAuth
    #
    # so the registrations below are in the reverse of that reading order.
    # This used to be written the other way round, which put SecurityHeaders
    # innermost: it then only decorated responses that came back from a route
    # handler, and the two responses auth generates itself -- the 401 page and
    # the 303 that sets the cookie -- went out bare, with no Cache-Control:
    # no-store and no frame-ancestors on the one page unauthenticated callers
    # are served.
    app.add_middleware(TokenAuthMiddleware, token=token)
    app.add_middleware(CookiePromotionMiddleware, token=token)
    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Analysis windows come from config so the dashboard, the charts and the
    # CLI agree. Previously each route hardcoded its own 30/90 and silently
    # ignored analysis.trend_window_days.
    trend_days = int(cfg.get("analysis.trend_window_days", 30))
    charging_days = int(cfg.get("analysis.charging_window_days", 90))

    def vehicle_and_id():
        row = repo.first_vehicle()
        return (dict(row) if row else {}), (row["id"] if row else None)

    def bms_refusal():
        """The bat_mgmt discovery status when it proves the battery DIDs are
        refused through the OBD surface (NRC / no-response / refused), else None.
        Pages use it to explain WHY the battery data is empty instead of looking
        broken -- on such cars the BMS sits on CAN-EV behind the gateway."""
        _, vid = vehicle_and_id()
        if not vid:
            return None
        for row in repo.list_ecus(vid):
            if row["key"] == "bat_mgmt":
                status = str(row["status"] or "")
                if (
                    status.startswith("nrc-")
                    or status == "no-response"
                    or status == "refused"
                ):
                    return status
        return None

    def measurement_sources():
        """Per-measurement provenance: live / imported / stale / simulated /
        unavailable.

        Without this the battery page cannot tell an empty page (a defect) from
        a page full of values imported from a Car Scanner export (evidence), and
        both read as "no live data". See analysis/provenance.py.
        """
        _, vid = vehicle_and_id()
        if not vid:
            return [], {
                "live": 0,
                "imported": 0,
                "stale": 0,
                "simulated": 0,
                "unavailable": 0,
                "unverified": 0,
                "total": 0,
            }
        from analysis.provenance import key_states, summary
        from decoders.registry import build_default_registry

        registry = build_default_registry(cfg.get("vehicle.did_profile", "eup"))
        stale_after = float(cfg.get("collector.max_value_age_s", 900.0))
        rows = key_states(repo, vid, registry, stale_after_s=stale_after)
        return rows, summary(rows)

    def measurement_catalog():
        """All measurements grouped by ECU, for the /measurements page.

        Returns (table_groups, chart_groups, counts) where:
          table_groups  list of {ecu, name, items:[{key,name,unit,value,ts,
                        state,unverified}]} with per-cell rolls ups collapsed.
          chart_groups  list of {name, specs:[{key,name,unit}]} listing EVERY
                        spec (individual cells included) for the chart picker.
          counts        provenance summary from measurement_sources().
        """
        from decoders.bms import COMPUTED_KEYS
        from decoders.registry import build_default_registry
        from diagnostic.ecus import ECUS

        registry = build_default_registry(cfg.get("vehicle.did_profile", "eup"))
        rows, counts = measurement_sources()

        # Per-cell voltages live in the cell_voltages table, not `measurements`,
        # so key_states() reports the rollup as unavailable. Override it with
        # the latest sweep's real cell data (delta mV + cell count + state).
        _, vid = vehicle_and_id()
        cells = repo.latest_cell_voltages(vid) if vid else []
        if cells:
            vs = [r["voltage_v"] for r in cells]
            sweep_ts = cells[0]["ts"]
            try:
                age = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(sweep_ts)
                ).total_seconds()
            except (ValueError, TypeError):
                age = None
            state = "live" if (age is not None and age <= 900.0) else "stale"
            for row in rows:
                if row["key"] == "cell_voltages":
                    row["value"] = round((max(vs) - min(vs)) * 1000.0, 1)
                    row["ts"] = sweep_ts
                    row["state"] = state
                    row["known"] = len(cells)
                    row["live"] = len(cells) if state == "live" else 0
                    row["unavailable"] = max(0, 108 - len(cells))
                    break

        def ecu_name(key):
            if key == "computed":
                return "Calculated metrics"
            return ECUS[key].name if key in ECUS else key

        # key -> ecu for the collapsed table rows (cell rollups belong to BMS;
        # derived metrics group under a synthetic "computed" ECU)
        key_to_ecu = {s.key: s.ecu_key for s in registry.all()}
        key_to_ecu["cell_voltages"] = "bat_mgmt"
        key_to_ecu["cell_temperatures"] = "bat_mgmt"
        for key in COMPUTED_KEYS:
            key_to_ecu[key] = "computed"

        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(key_to_ecu.get(row["key"], "other"), []).append(row)

        table_groups = [
            {
                "ecu": ecu,
                "name": ecu_name(ecu),
                "rows": sorted(items, key=lambda r: r["name"].lower()),
            }
            for ecu, items in sorted(
                groups.items(), key=lambda kv: ecu_name(kv[0]).lower()
            )
        ]

        chart: dict[str, list[dict]] = {}
        for spec in registry.all():
            chart.setdefault(spec.ecu_key, []).append(
                {"key": spec.key, "name": spec.name, "unit": spec.unit}
            )
        for key, (name, unit) in COMPUTED_KEYS.items():
            chart.setdefault("computed", []).append(
                {"key": key, "name": name, "unit": unit}
            )
        chart_groups = [
            {
                "name": ecu_name(ecu),
                "specs": sorted(specs, key=lambda s: s["name"].lower()),
            }
            for ecu, specs in sorted(
                chart.items(), key=lambda kv: ecu_name(kv[0]).lower()
            )
        ]

        return table_groups, chart_groups, counts

    def render(name: str, request: Request, **extra):
        vehicle, _ = vehicle_and_id()
        ctx = {
            "vehicle": vehicle,
            "nav": [
                "Overview",
                "Progress",
                "Measurements",
                "Explore",
                "Status",
                "Battery",
                "Faults",
                "Charging",
                "Quality",
                "Investigations",
                "AI Analysis",
                "Sources",
            ],
            **extra,
        }
        return _templates.TemplateResponse(request, name, ctx)

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
        latest = repo.latest_measurements(vid, max_age_s=86400) if vid else {}
        lv = latest.get("lv_voltage_obd") or {}
        speed = latest.get("vehicle_speed") or {}
        live = {
            "lv_voltage_v": lv.get("value"),
            "lv_voltage_ts": lv.get("ts"),
            "speed_kmh": speed.get("value"),
        }
        return render(
            "overview.html",
            request,
            battery=battery,
            dtcs=dtcs,
            charging=charging,
            anomalies=anomalies,
            days=days,
            live=live,
            bms_refusal=bms_refusal(),
        )

    @app.get("/battery", response_class=HTMLResponse)
    def battery_page(request: Request):
        _, vid = vehicle_and_id()
        rows = repo.battery_history(trend_days, vid) if vid else []
        chart = {
            "ts": [r["ts"] for r in rows],
            "delta_mv": [r["cell_delta_mv"] for r in rows],
            # `or` would treat a genuine 0 % SoC as missing and silently fall
            # back to the absolute figure, which is the one number on this page
            # that must never be fudged.
            "soc": [_first_number(r["soc_normal_pct"], r["soc_abs_pct"]) for r in rows],
            "pack_v": [r["pack_voltage_v"] for r in rows],
            "temp": [r["battery_temp_c"] for r in rows],
        }
        trend = (
            battery_analysis.cell_delta_trend(repo, trend_days, vid)
            if vid
            else {"status": "no vehicle"}
        )
        lv_rows = (
            repo.measurement_series(vid, "lv_voltage_obd", trend_days) if vid else []
        )
        lv_chart = {"ts": [r["ts"] for r in lv_rows], "v": [r["value"] for r in lv_rows]}
        return render(
            "battery.html",
            request,
            chart=chart,
            trend=trend,
            days=trend_days,
            lv_chart=lv_chart,
            bms_refusal=bms_refusal(),
        )

    @app.get("/dtcs", response_class=HTMLResponse)
    def dtc_page(request: Request):
        _, vid = vehicle_and_id()
        summary = (
            dtc_analysis.dtc_summary(repo.dtc_list(vid), repo.latest_freeze_frames(vid))
            if vid
            else {}
        )
        return render("dtcs.html", request, dtcs=summary)

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
            comparison=comparison,
            correlation=corr,
        )

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        _, vid = vehicle_and_id()
        reports = repo.llm_reports(vid) if vid else []
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=[dict(r) for r in reports],
            ollama_model=svc.llm.model,
        )

    @app.post("/ai/ask", response_class=HTMLResponse)
    def ai_ask(request: Request, question: str = Form(...)):
        question = (question or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Question is {len(question)} characters; the limit is "
                f"{MAX_QUESTION_CHARS}.",
            )
        try:
            result = svc.ask(question)
            report, warnings, error = result["report"], result["warnings"], None
        except OllamaError as exc:
            report, warnings, error = "", [], str(exc)
        reports_hist = [dict(r) for r in repo.llm_reports(vehicle_and_id()[1])]
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=reports_hist,
            report=report,
            warnings=warnings,
            error=error,
            ollama_model=svc.llm.model,
        )

    # ---- JSON API ------------------------------------------------------------
    @app.get("/api/battery")
    def api_battery():
        _, vid = vehicle_and_id()
        rows = repo.battery_history(trend_days, vid) if vid else []
        return [dict(r) for r in rows]

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

    @app.get("/api/measurements/{key}")
    def api_measurement(key: str):
        """Time series for one measurement key (successful reads, oldest first).

        Cell voltages live in their own table; a `cell_v_NNN` key reads that
        instead of `measurements`.
        """
        _, vid = vehicle_and_id()
        if vid and key.startswith("cell_v_"):
            try:
                idx = int(key.rsplit("_", 1)[1])
            except ValueError:
                idx = -1
            rows = repo.cell_voltage_series(vid, idx, trend_days) if idx >= 0 else []
        else:
            rows = repo.measurement_series(vid, key, trend_days) if vid else []
        return {
            "key": key,
            "ts": [r["ts"] for r in rows],
            "value": [r["value"] for r in rows],
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
            "measurements": repo.conn.execute(
                "SELECT COUNT(*) FROM measurements WHERE vehicle_id=?", (vid,)
            ).fetchone()[0],
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

    # ---- platform: discovery / status / quality / investigations ------------
    def _profile():
        return cfg.get("vehicle.did_profile", "eup")

    def _fresh_window():
        return float(cfg.get("collector.max_value_age_s", 900.0))

    def series_for(key: str, days: int, max_points: int, method: str) -> dict:
        """Chart payload for one key, server-side downsampled.

        Text/code keys become a step series (only changes are returned), numeric
        keys go through the sampler. Raw rows are never discarded: the export
        endpoints below serve them untouched.
        """
        from analysis import series as series_mod
        from decoders.quality import quality_registry

        _, vid = vehicle_and_id()
        if not vid:
            return {"key": key, "ts": [], "value": [], "raw_points": 0,
                    "returned_points": 0, "downsampled": False, "method": "none"}
        rows = repo.measurement_series(vid, key, days)
        ts_list = [r["ts"] for r in rows]
        values = [r["value"] for r in rows]
        q = quality_registry(_profile()).get(key)
        if q is not None and q.data_kind.value in ("text", "json"):
            return series_mod.transitions(ts_list, values)
        return series_mod.numeric_payload(ts_list, values, max_points=max_points,
                                          method=method)

    def observation_states(keys: list[str]) -> list[dict]:
        from analysis.freshness import observe_key
        _, vid = vehicle_and_id()
        if not vid:
            return []
        return [observe_key(repo, vid, k, fresh_after_s=_fresh_window()).as_dict()
                for k in keys]

    def discover_entries():
        from analysis import discovery
        _, vid = vehicle_and_id()
        if not vid:
            return [], {}
        entries = discovery.build_entries(repo, vid, _profile(),
                                          fresh_after_s=_fresh_window())
        return entries, discovery.facets(entries)

    @app.get("/explore", response_class=HTMLResponse)
    def explore_page(request: Request):
        from analysis import discovery

        entries, fac = discover_entries()
        params = request.query_params
        query = params.get("q", "")
        verification = params.get("verification") or None
        kind = params.get("kind") or None
        state = params.get("state") or None
        ecu = params.get("ecu") or None
        scoring = params.get("scoring")
        scoring_flag = None
        if scoring in ("1", "true"):
            scoring_flag = True
        elif scoring in ("0", "false"):
            scoring_flag = False
        filtered = discovery.search_entries(
            entries, query=query, verification=verification, data_kind=kind,
            state=state, ecu=ecu, scoring=scoring_flag)

        key = params.get("key") or (filtered[0].key if filtered else None)
        days = int(params.get("days", trend_days))
        max_points = min(int(params.get("max_points", 1500)), 20000)
        method = params.get("method", "min_max")
        chart = {"key": key, "ts": [], "value": [], "raw_points": 0,
                 "returned_points": 0, "downsampled": False, "method": "none"}
        obs = []
        timeline_events = []
        if key:
            chart = series_for(key, days, max_points, method)
            obs = observation_states([key])
            from analysis.timeline import build_timeline, markers
            window = build_timeline(repo, vehicle_and_id()[1],
                                    since=chart["ts"][0] if chart["ts"] else None,
                                    until=chart["ts"][-1] if chart["ts"] else None)
            timeline_events = markers(window)
        return render(
            "explore.html", request,
            entries=filtered, facets=fac, total_entries=len(entries),
            query=query, verification=verification, kind=kind, state=state,
            ecu=ecu, scoring=scoring, key=key, chart=chart, observations=obs,
            markers=timeline_events, days=days, max_points=max_points,
            method=method,
        )

    @app.get("/api/discover")
    def api_discover(request: Request):
        from analysis import discovery
        entries, fac = discover_entries()
        params = request.query_params
        rows = discovery.search_entries(
            entries,
            query=params.get("q", ""),
            verification=params.get("verification") or None,
            data_kind=params.get("kind") or None,
            state=params.get("state") or None,
            ecu=params.get("ecu") or None,
            limit=int(params["limit"]) if params.get("limit") else None)
        return {"total": len(entries), "returned": len(rows),
                "facets": fac, "entries": [e.as_dict() for e in rows]}

    @app.get("/api/series")
    def api_series(request: Request):
        params = request.query_params
        key = params.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="key is required")
        days = int(params.get("days", trend_days))
        max_points = min(int(params.get("max_points", 1500)), 20000)
        method = params.get("method", "min_max")
        payload = series_for(key, days, max_points, method)
        payload["observation"] = observation_states([key])
        return payload

    @app.get("/api/timeline")
    def api_timeline(request: Request):
        from analysis.timeline import build_timeline, markers
        _, vid = vehicle_and_id()
        params = request.query_params
        if not vid:
            return {"events": [], "markers": []}
        days = int(params.get("days", trend_days))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds")
        kinds = set(params["kinds"].split(",")) if params.get("kinds") else None
        events = build_timeline(repo, vid, since=since, kinds=kinds,
                                include_simulated=params.get("simulated") == "1")
        return {"events": [e.as_dict() for e in events],
                "markers": markers(events)}

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
        limit = int(request.query_params.get("limit", 80))
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

    @app.get("/investigations", response_class=HTMLResponse)
    def investigations_page(request: Request):
        _, vid = vehicle_and_id()
        rows = [dict(r) for r in repo.investigations(vid)] if vid else []
        return render("investigations.html", request, investigations=rows,
                      error=request.query_params.get("error"))

    @app.get("/investigations/{investigation_id}", response_class=HTMLResponse)
    def investigation_page(request: Request, investigation_id: int):
        from analysis import investigation as inv_mod
        inv = inv_mod.load_investigation(repo, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="No such investigation")
        ok, reason = inv_mod.criteria_met(inv)
        return render("investigation.html", request, inv=inv,
                      criteria_ok=ok, criteria_reason=reason)

    @app.post("/investigations/new")
    def create_investigation(request: Request, title: str = Form(...),
                             symptom: str = Form(...),
                             anchor_key: str = Form(""),
                             criteria: str = Form("")):
        from analysis import investigation as inv_mod
        _, vid = vehicle_and_id()
        if not vid:
            raise HTTPException(status_code=400,
                                detail="No vehicle; nothing to investigate.")
        inv = inv_mod.open_investigation(
            repo, vid, title.strip(), symptom.strip(),
            anchor_key=(anchor_key.strip() or None), criteria=criteria.strip())
        return _redirect(f"/investigations/{inv.id}")

    @app.post("/investigations/{investigation_id}/observe")
    def investigation_observe(request: Request, investigation_id: int,
                              key: str = Form(...)):
        from analysis import investigation as inv_mod
        _, vid = vehicle_and_id()
        inv_mod.observe_key(repo, vid, key.strip(), _profile(),
                            inv_id=investigation_id)
        return _redirect(f"/investigations/{investigation_id}")

    @app.post("/investigations/{investigation_id}/finalize")
    def investigation_finalize(request: Request, investigation_id: int,
                               summary: str = Form(""),
                               criteria_confirmed: bool = Form(False)):
        from analysis import investigation as inv_mod
        inv_mod.finalize(repo, investigation_id, summary=summary.strip(),
                         criteria_confirmed=criteria_confirmed)
        return _redirect(f"/investigations/{investigation_id}")

    @app.get("/api/investigations")
    def api_investigations():
        from analysis import investigation as inv_mod
        _, vid = vehicle_and_id()
        if not vid:
            return {"investigations": []}
        out = []
        for r in repo.investigations(vid):
            inv = inv_mod.load_investigation(repo, r["id"])
            ok, reason = inv_mod.criteria_met(inv) if inv else (False, "")
            item = dict(r)
            item["criteria_met"] = ok
            item["criteria_reason"] = reason
            item["evidence_count"] = len(inv.evidence) if inv else 0
            out.append(item)
        return {"investigations": out}

    # ---- evidence export (CSV / JSON downloads) -----------------------------
    def _csv_download(rows: list[dict], columns: list[str],
                      filename: str) -> Response:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c) for c in columns})
        return Response(
            content=buf.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def _json_download(payload: object, filename: str) -> Response:
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def _stamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @app.get("/api/export/series")
    def export_series(request: Request):
        """Download the raw stored rows for one key (CSV or JSON).

        Unlike the chart payload this keeps every column and every row --
        failed reads, text decodes and the raw response bytes included -- so an
        exported file can be re-examined without the dashboard's sampling.
        """
        params = request.query_params
        key = params.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="key is required")
        _, vid = vehicle_and_id()
        if not vid:
            raise HTTPException(status_code=400, detail="No vehicle.")
        fmt = (params.get("format") or "csv").lower()
        days = int(params.get("days", trend_days))
        is_cell = key.startswith("cell_v_")
        if is_cell:
            try:
                idx = int(key.rsplit("_", 1)[1])
            except ValueError:
                raise HTTPException(status_code=400,
                                    detail="bad cell key") from None
            rows = [dict(r) for r in repo.raw_cell_series(vid, idx, days)]
            columns = ["ts", "cell_index", "voltage_v"]
        else:
            rows = [dict(r) for r in repo.raw_measurement_series(vid, key, days)]
            columns = ["ts", "ecu", "service", "pid", "key", "value",
                       "text_value", "unit", "provenance", "decoding_method",
                       "doc_status", "raw_response", "success", "error"]
        from analysis.freshness import observe_key
        payload = {
            "key": key, "days": days, "generated_at": _stamp(),
            "row_count": len(rows),
            "observation": observe_key(repo, vid, key,
                                       fresh_after_s=_fresh_window()).as_dict(),
            "limitations": [
                "Rows are the exact stored values, never interpolated; gaps "
                "mean the car did not answer.",
                "success=0 rows are failed reads, kept for honesty.",
            ],
            "rows": rows,
        }
        safe = key.replace("/", "_")
        if fmt == "json":
            return _json_download(payload, f"{safe}_{_stamp()}.json")
        return _csv_download(rows, columns, f"{safe}_{_stamp()}.csv")

    @app.get("/api/export/discovery")
    def export_discovery(request: Request):
        """Download the filtered discovery catalogue as CSV or JSON."""
        from analysis import discovery
        params = request.query_params
        entries, fac = discover_entries()
        rows = discovery.search_entries(
            entries, query=params.get("q", ""),
            verification=params.get("verification") or None,
            data_kind=params.get("kind") or None,
            state=params.get("state") or None,
            ecu=params.get("ecu") or None)
        dicts = [e.as_dict() for e in rows]
        columns = ["key", "name", "unit", "ecu_key", "ecu_name", "verification",
                   "data_kind", "use_in_scoring", "known_issue", "state",
                   "last_success_ts", "age_s", "successes", "failures",
                   "rollup", "members", "plausible_min", "plausible_max"]
        fmt = (params.get("format") or "csv").lower()
        if fmt == "json":
            return _json_download(
                {"generated_at": _stamp(), "count": len(dicts),
                 "facets": fac, "entries": dicts},
                f"discovery_{_stamp()}.json")
        return _csv_download(dicts, columns, f"discovery_{_stamp()}.csv")

    @app.get("/api/export/investigation/{investigation_id}")
    def export_investigation(request: Request, investigation_id: int):
        """Download one investigation as a self-contained evidence packet."""
        from analysis import investigation as inv_mod
        inv = inv_mod.load_investigation(repo, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="No such investigation")
        ok, reason = inv_mod.criteria_met(inv)
        fmt = (request.query_params.get("format") or "json").lower()
        evidence = [e.as_dict() for e in inv.evidence]
        if fmt == "csv":
            columns = ["stage", "kind", "observation", "ts", "ref", "detail",
                       "certain"]
            return _csv_download(evidence, columns,
                                 f"investigation_{investigation_id}_{_stamp()}.csv")
        payload = {
            "generated_at": _stamp(),
            "investigation": {
                "id": inv.id, "title": inv.title, "symptom": inv.symptom,
                "anchor_key": inv.anchor_key, "anchor_ecu": inv.anchor_ecu,
                "criteria": inv.criteria, "status": inv.status,
                "opened_at": inv.opened_at, "updated_at": inv.updated_at,
                "summary": inv.summary, "conclusion": inv.conclusion,
            },
            "criteria_met": ok, "criteria_reason": reason,
            "evidence": evidence,
            "limitations": [
                "Association rows are temporal only; temporal order is not "
                "causation.",
                "certain=false means the value was not freshly observed or is "
                "not scoreable, so it cannot support a diagnosis.",
            ],
        }
        return _json_download(
            payload, f"investigation_{investigation_id}_{_stamp()}.json")

    return app
