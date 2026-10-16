"""Local web dashboard (FastAPI + Jinja2 + Chart.js).

READ-ONLY: no endpoint can transmit anything to the vehicle. The dashboard
only reads from SQLite; collection runs as a separate process (main.py collect).

Network-exposed binds require an access token -- see web/auth.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
    SameOriginMiddleware,
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
        """Per-measurement provenance: live / imported / stale / unavailable.

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
            return ECUS[key].name if key in ECUS else key

        # key -> ecu for the collapsed table rows (cell rollups belong to BMS)
        key_to_ecu = {s.key: s.ecu_key for s in registry.all()}
        key_to_ecu["cell_voltages"] = "bat_mgmt"
        key_to_ecu["cell_temperatures"] = "bat_mgmt"

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
                "Battery",
                "Faults",
                "Charging",
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

    return app
