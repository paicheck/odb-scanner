"""Local web dashboard (FastAPI + Jinja2 + Chart.js).

READ-ONLY: no endpoint can transmit anything to the vehicle. The dashboard
only reads from SQLite; collection runs as a separate process (main.py collect).

Network-exposed binds require an access token -- see web/auth.py.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
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
    SecurityHeadersMiddleware,
    TokenAuthMiddleware,
    resolve_token,
)

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# The question is the only unbounded user input in the app: it goes into an
# LLM prompt and, on the default model, occupies a GPU for minutes. Without a
# cap a single request can stall the box for anyone else using it.
MAX_QUESTION_CHARS = 1000


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

    app = FastAPI(title="ID.3 Diagnostic System", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    host = bind_host if bind_host is not None else cfg.get("web.host", "127.0.0.1")
    token = resolve_token(cfg, host)
    if token:
        log.warning(
            "Dashboard bound to %s with no visible token configured; access now "
            "requires ?%s=... once. See the startup message for the value.",
            host, QUERY)
    # Order matters: headers wrap everything, then the cookie exchange, then
    # the gate itself. Middleware added last runs first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CookiePromotionMiddleware, token=token)
    app.add_middleware(TokenAuthMiddleware, token=token)

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
        refused through the OBD surface (NRC / no-response), else None. Pages
        use it to explain WHY the battery data is empty instead of looking
        broken -- on such cars the BMS sits on CAN-EV behind the gateway."""
        _, vid = vehicle_and_id()
        if not vid:
            return None
        for row in repo.list_ecus(vid):
            if row["key"] == "bat_mgmt":
                status = str(row["status"] or "")
                if status.startswith("nrc-") or status == "no-response":
                    return status
        return None

    def measurement_sources():
        """Per-measurement provenance: live / imported / stale / unavailable.

        Without this the battery page cannot tell an empty page (a defect) from
        a page full of values imported from a Car Scanner export (evidence), and
        both read as "no live data". See analysis/provenance.py.
        """
        _, vid = vehicle_and_id()
        if not vid:
            return [], {"live": 0, "imported": 0, "stale": 0,
                        "unavailable": 0, "unverified": 0, "total": 0}
        from analysis.provenance import key_states, summary
        from decoders.registry import build_default_registry

        registry = build_default_registry(cfg.get("vehicle.did_profile", "eup"))
        stale_after = float(cfg.get("collector.max_value_age_s", 900.0))
        rows = key_states(repo, vid, registry, stale_after_s=stale_after)
        return rows, summary(rows)

    def render(name: str, request: Request, **extra):
        vehicle, _ = vehicle_and_id()
        ctx = {"vehicle": vehicle,
               "nav": ["Overview", "Battery", "Faults", "Charging",
                       "AI Analysis", "Sources"],
               **extra}
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

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        _, vid = vehicle_and_id()
        days = trend_days
        battery = (battery_analysis.battery_overview(repo, days, vid)
                   if vid else {})
        dtcs = dtc_analysis.dtc_summary(repo.dtc_list(vid)) if vid else {}
        charging = (charging_analysis.session_comparison(repo, vid, charging_days)
                    if vid else {})
        anomalies = [dict(a) for a in repo.anomalies(vid, days)] if vid else []
        latest = repo.latest_measurements(vid, max_age_s=86400) if vid else {}
        lv = latest.get("lv_voltage_obd") or {}
        speed = latest.get("vehicle_speed") or {}
        live = {"lv_voltage_v": lv.get("value"), "lv_voltage_ts": lv.get("ts"),
                "speed_kmh": speed.get("value")}
        return render("overview.html", request, battery=battery, dtcs=dtcs,
                      charging=charging, anomalies=anomalies, days=days,
                      live=live, bms_refusal=bms_refusal())

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
            "soc": [_first_number(r["soc_normal_pct"], r["soc_abs_pct"])
                    for r in rows],
            "pack_v": [r["pack_voltage_v"] for r in rows],
            "temp": [r["battery_temp_c"] for r in rows],
        }
        trend = (battery_analysis.cell_delta_trend(repo, trend_days, vid)
                 if vid else {"status": "no vehicle"})
        lv_rows = (repo.measurement_series(vid, "lv_voltage_obd", trend_days)
                   if vid else [])
        lv_chart = {"ts": [r["ts"] for r in lv_rows],
                    "v": [r["value"] for r in lv_rows]}
        return render("battery.html", request, chart=chart, trend=trend,
                      days=trend_days, lv_chart=lv_chart,
                      bms_refusal=bms_refusal())

    @app.get("/dtcs", response_class=HTMLResponse)
    def dtc_page(request: Request):
        _, vid = vehicle_and_id()
        summary = (dtc_analysis.dtc_summary(repo.dtc_list(vid),
                                            repo.latest_freeze_frames(vid))
                   if vid else {})
        return render("dtcs.html", request, dtcs=summary)

    @app.get("/charging", response_class=HTMLResponse)
    def charging_page(request: Request):
        _, vid = vehicle_and_id()
        sessions = repo.charging_sessions(charging_days, vid) if vid else []
        comparison = (charging_analysis.session_comparison(
            repo, vid, charging_days) if vid else {})
        corr = (charging_analysis.correlate_dtc_with_sessions(
            repo, vid, charging_days) if vid else {})
        return render("charging.html", request,
                      sessions=[dict(s) for s in sessions],
                      comparison=comparison, correlation=corr)

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        _, vid = vehicle_and_id()
        reports = repo.llm_reports(vid) if vid else []
        return render("ai.html", request, questions=DEFAULT_QUESTIONS,
                      reports=[dict(r) for r in reports],
                      ollama_model=svc.llm.model)

    @app.post("/ai/ask", response_class=HTMLResponse)
    def ai_ask(request: Request, question: str = Form(...)):
        question = (question or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Question is {len(question)} characters; the limit is "
                       f"{MAX_QUESTION_CHARS}.")
        try:
            result = svc.ask(question)
            report, warnings, error = result["report"], result["warnings"], None
        except OllamaError as exc:
            report, warnings, error = "", [], str(exc)
        reports_hist = [dict(r) for r in repo.llm_reports(
            vehicle_and_id()[1])]
        return render("ai.html", request, questions=DEFAULT_QUESTIONS,
                      reports=reports_hist, report=report,
                      warnings=warnings, error=error,
                      ollama_model=svc.llm.model)

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
        return {"vehicle_known": bool(vid), "dtc_total": dtcs.get("total", 0),
                "critical_dtcs": critical,
                "ollama_available": svc.llm.available()}

    return app


