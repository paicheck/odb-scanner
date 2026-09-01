"""Local web dashboard (FastAPI + Jinja2 + Chart.js).

READ-ONLY: no endpoint can transmit anything to the vehicle. The dashboard
only reads from SQLite; collection runs as a separate process (main.py collect).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ai.ollama import OllamaError
from ai.service import AnalysisService, DEFAULT_QUESTIONS
from analysis import battery as battery_analysis
from analysis import charging as charging_analysis
from analysis import dtc as dtc_analysis
from config import Config
from database.repository import Repository

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(cfg: Config) -> FastAPI:
    repo = Repository(cfg.db_path)
    svc = AnalysisService(cfg, repo)
    app = FastAPI(title="ID.3 Diagnostic System", docs_url=None, redoc_url=None)

    def vehicle_and_id():
        row = repo.conn.execute(
            "SELECT * FROM vehicles ORDER BY id LIMIT 1").fetchone()
        return (dict(row) if row else {}), (row["id"] if row else None)

    def render(name: str, request: Request, **extra):
        vehicle, _ = vehicle_and_id()
        ctx = {"vehicle": vehicle, "nav": ["Overview", "Battery", "Faults",
                                           "Charging", "AI Analysis"],
               **extra}
        return _templates.TemplateResponse(request, name, ctx)

    # ---- pages --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        _, vid = vehicle_and_id()
        days = int(cfg.get("analysis.trend_window_days", 30))
        battery = (battery_analysis.battery_overview(repo, days, vid)
                   if vid else {})
        dtcs = dtc_analysis.dtc_summary(repo.dtc_list(vid)) if vid else {}
        charging = (charging_analysis.session_comparison(repo, vid)
                    if vid else {})
        anomalies = [dict(a) for a in repo.anomalies(vid, days)] if vid else []
        return render("overview.html", request, battery=battery, dtcs=dtcs,
                      charging=charging, anomalies=anomalies, days=days)

    @app.get("/battery", response_class=HTMLResponse)
    def battery_page(request: Request):
        _, vid = vehicle_and_id()
        rows = repo.battery_history(30, vid) if vid else []
        chart = {
            "ts": [r["ts"] for r in rows],
            "delta_mv": [r["cell_delta_mv"] for r in rows],
            "soc": [r["soc_normal_pct"] or r["soc_abs_pct"] for r in rows],
            "pack_v": [r["pack_voltage_v"] for r in rows],
            "temp": [r["battery_temp_c"] for r in rows],
        }
        trend = (battery_analysis.cell_delta_trend(repo, 30, vid)
                 if vid else {"status": "no vehicle"})
        return render("battery.html", request, chart=chart, trend=trend)

    @app.get("/dtcs", response_class=HTMLResponse)
    def dtc_page(request: Request):
        _, vid = vehicle_and_id()
        summary = dtc_analysis.dtc_summary(repo.dtc_list(vid)) if vid else {}
        return render("dtcs.html", request, dtcs=summary)

    @app.get("/charging", response_class=HTMLResponse)
    def charging_page(request: Request):
        _, vid = vehicle_and_id()
        sessions = repo.charging_sessions(90, vid) if vid else []
        comparison = (charging_analysis.session_comparison(repo, vid, 90)
                      if vid else {})
        corr = (charging_analysis.correlate_dtc_with_sessions(repo, vid, 90)
                if vid else {})
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
        rows = repo.battery_history(30, vid) if vid else []
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


