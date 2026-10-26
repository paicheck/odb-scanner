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
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
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
STATIC_DIR = Path(__file__).resolve().parent / "static"
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


def _format_number(value, decimals=1):
    """Fixed-precision number for chart summaries; missing stays "—".

    `decimals` comes from `analysis.chart_semantics.decimals_for`, so a summary
    never shows more digits than the axis/tooltip for the same series.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:.{int(decimals)}f}"


def _format_ts(ts):
    """Observation timestamp for display, with its timezone made explicit.

    Stored stamps are ISO-8601; a `+00:00` stamp is shown as UTC, anything
    else keeps its own offset rather than being silently relabelled.
    """
    if not ts:
        return "—"
    ts = str(ts)
    if ts.endswith("+00:00"):
        return ts[:19].replace("T", " ") + " UTC"
    return ts.replace("T", " ")


_templates.env.filters["fmtnum"] = _format_number
_templates.env.filters["fmtts"] = _format_ts

# The question is the only unbounded user input in the app: it goes into an
# LLM prompt and, on the default model, occupies a GPU for minutes. Without a
# cap a single request can stall the box for anyone else using it.
MAX_QUESTION_CHARS = 1000

# ---------------------------------------------------------------------------
# Chart panels. Each panel answers one question; `assemble_panels` refuses to
# put two units on one axis unless a panel explicitly asks for a labelled
# second axis (`dual`). `visible` limits which series are drawn by default --
# the others stay one checkbox away.
# ---------------------------------------------------------------------------

OVERVIEW_PANELS = [
    {"id": "ov-soc-v", "title": "State of charge and pack voltage",
     "question": "How full was the pack, and what voltage did it report?",
     "keys": ["soc_abs", "pack_voltage"], "dual": True,
     "note": "Two units, two labelled axes: SoC (BMS, %) on the left, pack "
             "voltage (V) on the right."},
]

BATTERY_PANELS = [
    {"id": "soc", "title": "State of charge",
     "question": "How full was the pack, by each SoC definition?",
     "keys": ["soc_abs", "soc_normal"],
     "note": "soc_abs is the BMS (absolute) figure and soc_normal the "
             "display (normalised) figure. They are different definitions, "
             "drawn as separate lines and never merged."},
    {"id": "pack-v", "title": "HV pack voltage",
     "question": "What terminal voltage did the BMS report?",
     "keys": ["pack_voltage"]},
    {"id": "pack-i", "title": "HV pack current",
     "question": "How much current flowed, and in which direction?",
     "keys": ["pack_current"],
     "note": "Plotted with the sign as decoded. The catalogue does not "
             "document which sign means charging."},
    {"id": "pack-p", "title": "HV pack power (calculated)",
     "question": "What power did voltage × current imply?",
     "keys": ["pack_power_kw"],
     "note": "CALCULATED by the collector as pack voltage × pack current; "
             "not a vehicle report."},
    {"id": "cell-minmax", "title": "Lowest and highest cell voltage (BMS)",
     "question": "What did the BMS report as the extreme cell voltages?",
     "keys": ["cell_voltage_max", "cell_voltage_min"]},
    {"id": "temp", "title": "HV battery temperatures",
     "question": "How warm was the pack?",
     "keys": ["battery_temp", "bat_temp_max", "bat_temp_min"],
     "note": "Three BMS temperature signals for the same pack. The catalogue "
             "does not document the sensor locations, so they are shown side "
             "by side, not combined."},
    {"id": "energy", "title": "HV energy content (vehicle-reported)",
     "question": "How much energy did the BMS say the pack held?",
     "keys": ["hv_energy_content", "hv_energy_max"],
     "note": "hv_energy_content is the energy currently held; hv_energy_max is "
             "the BMS-rated maximum. Neither is a measured total capacity."},
    {"id": "lv", "title": "12 V system voltage",
     "question": "What did the 12 V system read?",
     "keys": ["lv_voltage_obd", "aux_12v_voltage"],
     "note": "Two independent sources: the OBD-II PID 0x42 module supply "
             "voltage and the BMS 12 V battery reading."},
]

RANGE_PANELS = [
    {"id": "range", "title": "Estimated remaining range (vehicle estimate)",
     "question": "How far did the vehicle predict it could still go?",
     "keys": ["range_displayed", "range_can", "range_internal"],
     "visible": ["range_displayed"],
     "note": "Predictions computed by the vehicle — not distance travelled "
             "and not a measure of battery health. The catalogue marks all "
             "three UNKNOWN (scaling not independently confirmed)."},
    {"id": "range-soc", "title": "State of charge (BMS)",
     "question": "How full was the pack over the same period?",
     "keys": ["soc_abs"]},
    {"id": "odo", "title": "Odometer",
     "question": "How far had the car actually travelled?",
     "keys": ["odometer_km"],
     "note": "Cumulative distance counter: the axis is fitted to the data and "
             "does not start at zero."},
    {"id": "consumption", "title": "Average consumption reported by the vehicle",
     "question": "What consumption figures did the vehicle store?",
     "keys": [f"road_avg_consumption_{i}" for i in range(7)],
     "visible": ["road_avg_consumption_0"],
     "note": "Vehicle-reported figures. What categories 0–6 represent is "
             "UNRESOLVED in the catalogue, so they are shown as separate "
             "series and never averaged together."},
]

# Pairing tolerance for range-vs-SoC: the two DIDs are polled in the same
# collector cycle, so readings more than this far apart are not compared.
RANGE_SOC_PAIR_TOLERANCE_S = 120.0

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,80}$")


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


def _int_param(request: Request, name: str, default: int, lo: int, hi: int) -> int:
    """Read a bounded integer query parameter, falling back to `default`.

    Used by the timeline and event pages for the day/hour window controls. A
    non-numeric or out-of-range value must not 500 the page or open an unbounded
    query; it falls back to the default and is clamped into [lo, hi].
    """
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


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
    # A gap wider than the "value is still fresh" window is a hole in the data,
    # not a straight line between two samples. Charts pass this to the browser
    # so a three-day outage is drawn as a three-day outage.
    chart_gap_s = float(cfg.get("collector.max_value_age_s", 900.0))

    # Static assets are served from this origin. Chart.js used to come from a
    # CDN while the CSP forbade inline scripts, so every chart was silently
    # blocked. Local assets let the CSP stay at `script-src 'self'` and keep the
    # dashboard usable offline.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
                "Range",
                "Faults",
                "Timeline",
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
        # Per-key series with their own observation timestamps. The chart used
        # to read the battery snapshot table, which carries values forward and
        # merged normalised and absolute SoC into one line.
        panels = panels_context(OVERVIEW_PANELS, days)
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
            gap_s=chart_gap_s,
            bms_refusal=bms_refusal(),
            **panels,
        )

    def panels_context(defs: list[dict], days: int) -> dict:
        """Template context for a set of chart panels: annotated series (one
        query), unit-safe panels, a shared time axis and default visibility."""
        from analysis.chart_semantics import assemble_panels, shared_x_range

        keys = list(dict.fromkeys(k for d in defs for k in d["keys"]))
        series = chart_series(keys, days)
        visible = {}
        for d in defs:
            for k in d["keys"]:
                visible[k] = k in d.get("visible", d["keys"])
        return {
            "panels": assemble_panels(defs, series),
            "series": series,
            "panel_data": {"series": series, "visible": visible,
                           "x_range": shared_x_range(series),
                           "gap_s": chart_gap_s},
        }

    def _window_days(request: Request) -> int:
        return _int_param(request, "days", trend_days, 1, 3650)

    def _windows(days: int) -> list[int]:
        """Window choices, always including the one being shown, so the
        selector never claims a different window from the chart."""
        return sorted({1, 7, 14, 30, 90, 365, trend_days, days})

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
            gap_s=chart_gap_s, max_points=0)
        series["point_notes"] = [
            f"lowest cell #{s['min_cell']:03d} {s['min_v']:.3f} V, highest cell "
            f"#{s['max_cell']:03d} {s['max_v']:.3f} V, {s['cells']} cells"
            + (f", {s['missing']} missing" if s["missing"] else "")
            for s in sweeps]
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
        from analysis.chart_semantics import counter_delta, pair_nearest

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
        ctx["panel_data"]["scatter"] = {
            "pairs": pairs, "tolerance_s": RANGE_SOC_PAIR_TOLERANCE_S,
            "x": {"label": "State of charge (BMS)", "unit_label": "%"},
            "y": {"label": "Estimated range (displayed)", "unit_label": "km"},
        }
        return render(
            "range.html", request, days=days,
            windows=_windows(days),
            pairs=pairs, distance=distance,
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

    @app.get("/dtcs", response_class=HTMLResponse)
    def dtc_page(request: Request):
        _, vid = vehicle_and_id()
        summary = (
            dtc_analysis.dtc_summary(repo.dtc_list(vid), repo.latest_freeze_frames(vid))
            if vid
            else {}
        )
        return render("dtcs.html", request, dtcs=summary)

    @app.get("/dtcs/{dtc_id}", response_class=HTMLResponse)
    def dtc_detail(request: Request, dtc_id: int):
        """One fault code's full story: occurrence history, warning-lamp
        transitions, the ECU's freeze-frame snapshot, and the measurements the
        car recorded around its latest occurrence.

        The Faults page used to stop at a flat summary, so there was no way to
        answer "when did this code actually appear and what was the car doing
        at the time?". Raw snapshot bytes stay raw; nothing here interprets a
        manufacturer-defined freeze-frame record it has not been told about.
        """
        from analysis.context import measurement_context

        _, vid = vehicle_and_id()
        row = repo.dtc(dtc_id) if vid else None
        if row is None or row["vehicle_id"] != vid:
            raise HTTPException(status_code=404, detail="No such DTC")

        occurrences = [dict(r) for r in repo.conn.execute(
            "SELECT id, ts, freeze_frame FROM dtc_occurrences "
            "WHERE dtc_id=? ORDER BY ts", (dtc_id,)).fetchall()]

        # Warning-lamp edges for this code, from its own status-byte history.
        # Built from the DTC's own occurrences rather than the whole vehicle's
        # status history so the page does not scan every DTC to describe one.
        history = []
        for o in occurrences:
            status_byte = None
            if o["freeze_frame"]:
                try:
                    status_byte = json.loads(o["freeze_frame"]).get("status_byte")
                except (TypeError, ValueError):
                    status_byte = None
            history.append({"ts": o["ts"], "ecu": row["ecu"], "code": row["code"],
                            "status_byte": status_byte})
        transitions = dtc_analysis.detect_warning_transitions(history)

        categories = (json.loads(row["categories"])
                      if isinstance(row["categories"], str)
                      else list(row["categories"]))

        freeze_frame = repo.latest_freeze_frames(vid).get((row["ecu"], row["code"]))
        snapshot_records = [
            rec for e in (freeze_frame or {}).get("snapshot") or []
            for rec in e.get("records") or []
        ]

        window_s = 6 * 3600.0
        near_last = (measurement_context(repo, vid, row["last_seen"],
                                         window_s=window_s) if vid else [])
        for m in near_last:
            meta = key_meta(m["key"])
            m["name"] = meta["name"]
            m["unit"] = m["unit"] or meta["unit"]

        return render(
            "dtc_detail.html", request, d=dict(row), categories=categories,
            occurrences=occurrences, transitions=transitions,
            snapshot_records=snapshot_records, near_last=near_last,
        )

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
            sample_coverage=cov, panels=panels, series=series,
            panel_data={"series": series, "visible": dict.fromkeys(series, True),
                        "x_range": shared_x_range(series), "gap_s": chart_gap_s},
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
    def api_measurement(key: str, request: Request):
        """Time series for one measurement key, server-side downsampled.

        Cell voltages live in their own table; a `cell_v_NNN` key reads that
        instead of `measurements`. The payload carries the unit and data kind so
        the browser can label the axis and choose a line or a state strip.
        """
        try:
            days = int(request.query_params.get("days", trend_days))
        except (TypeError, ValueError):
            days = trend_days
        days = max(1, min(days, 3650))
        try:
            max_points = int(request.query_params.get("max_points", 1500))
        except (TypeError, ValueError):
            max_points = 1500
        max_points = max(1, min(max_points, 20000))
        method = request.query_params.get("method", "min_max")
        if method not in ("min_max", "lttb"):
            method = "min_max"
        return series_for(key, days, max_points, method)

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

    # Built lazily and reused across chart requests. Building the full MEB map
    # on every keystroke in the measurement picker would be wasted work.
    _meta_registry = None
    _meta_registry_profile = None

    def _registry_for_meta():
        nonlocal _meta_registry, _meta_registry_profile
        profile = _profile()
        if _meta_registry is None or _meta_registry_profile != profile:
            from decoders.registry import build_default_registry
            _meta_registry = build_default_registry(profile)
            _meta_registry_profile = profile
        return _meta_registry

    def key_meta(key: str) -> dict:
        """Display metadata for one measurement key.

        The chart needs the unit and a human name; without them a y-axis is a
        row of bare numbers and a tooltip says "pack_voltage: 401.5" instead of
        "Pack voltage: 401.5 V". Per-cell keys are not in the DID registry, so
        they are described here.
        """
        from decoders.aliases import aliases_for, resolve
        from decoders.quality import quality_for

        key = resolve(key)

        if key.startswith("cell_v_"):
            # cell_v_NNN is the zero-based slot index; every decoder and the
            # importer number cells from 1 (cell_v_000 is "Cell voltage #001").
            # This used to say "Cell 0 voltage", one off from the catalogue.
            try:
                idx = int(key.rsplit("_", 1)[1])
                name = f"Cell voltage #{idx + 1:03d}"
            except ValueError:
                name = "Cell voltage"
            return {"key": key, "name": name, "unit": "V",
                    "unit_source": "catalogue", "unit_conflict": [],
                    "data_kind": "numeric",
                    "aliases": list(aliases_for(key))}

        name, unit, kind = key, "", "numeric"
        try:
            spec = _registry_for_meta().get(key)
            name, unit = spec.name, spec.unit or ""
        except KeyError:
            pass
        q = None
        try:
            q = quality_for(_profile(), key)
            kind = q.data_kind.value
        except Exception:  # pragma: no cover - metadata must never break a chart
            kind = "numeric"
        if name == key and q is not None and q.extras.get("name"):
            # Calculated metrics are not registry DIDs; their human name lives
            # on the quality record (from decoders/bms.COMPUTED_KEYS).
            name = q.extras["name"]
        if not unit and q is not None and q.unit:
            unit = q.unit
        unit_source = "catalogue" if unit else "none"
        if not unit:
            # Keys with no catalogue unit (the OBD-II PID keys such as
            # lv_voltage_obd) used to chart with a bare axis although every
            # stored row names its unit. Fall back to the stored unit only when
            # it is unambiguous, and say so.
            stored = repo.recent_units(key)
            if len(stored) == 1:
                unit, unit_source = stored[0], "stored rows"
        return {"key": key, "name": name, "unit": unit,
                "unit_source": unit_source, "unit_conflict": [],
                "data_kind": kind, "aliases": list(aliases_for(key))}

    def _unit_conflict(key: str, unit: str) -> list[str]:
        """Stored units that disagree with the catalogue unit (reported, never
        silently resolved)."""
        if not unit:
            return []
        return [u for u in repo.recent_units(key) if u != unit]

    def _annotated(key: str, meta: dict, rows, max_points: int, method: str,
                   conflict: bool = True) -> dict:
        from analysis.chart_semantics import build_series
        from decoders.quality import quality_for

        return build_series(
            key, rows, name=meta["name"], unit=meta["unit"],
            quality=quality_for(_profile(), key), gap_s=chart_gap_s,
            max_points=max_points, method=method,
            unit_source=meta.get("unit_source", "catalogue"),
            unit_conflict=_unit_conflict(key, meta["unit"]) if conflict else [])

    def series_for(key: str, days: int, max_points: int, method: str) -> dict:
        """Chart payload for one key, server-side downsampled.

        Text/code keys become a step series (only changes are returned), numeric
        keys go through the sampler and are annotated by
        `analysis/chart_semantics.py` (axis policy, raw statistics,
        plausibility, coverage, per-point provenance). Raw rows are never
        discarded: the export endpoints below serve them untouched. Per-cell
        keys read the `cell_voltages` table instead of `measurements`.
        """
        from analysis import series as series_mod
        from decoders.aliases import resolve

        key = resolve(key)
        meta = key_meta(key)
        _, vid = vehicle_and_id()
        if not vid:
            return {"key": key, **meta, "ts": [], "value": [], "raw_points": 0,
                    "returned_points": 0, "downsampled": False, "method": "none"}

        rows = []
        if vid and key.startswith("cell_v_"):
            try:
                idx = int(key.rsplit("_", 1)[1])
            except ValueError:
                idx = -1
            if idx >= 0:
                # The cell table stores no provenance column; say so rather
                # than claiming "reported".
                rows = [(r["ts"], r["value"], None)
                        for r in repo.cell_voltage_series(vid, idx, days)]
        else:
            rows = [(r["ts"], r["value"], r["provenance"])
                    for r in repo.measurement_series(vid, key, days)]

        if meta["data_kind"] in ("text", "json"):
            payload = series_mod.transitions([r[0] for r in rows],
                                             [r[1] for r in rows])
            payload.update(meta)
        else:
            payload = _annotated(key, meta, rows, max_points, method)
            payload["aliases"] = meta["aliases"]
        payload["key"] = key
        return payload

    def chart_series(keys: list[str], days: int, max_points: int = 1500) -> dict:
        """Annotated numeric series for several keys from ONE query."""
        if not keys:
            return {}
        _, vid = vehicle_and_id()
        rows_by_key: dict[str, list] = {k: [] for k in keys}
        if vid:
            for r in repo.measurement_series_many(vid, keys, days):
                rows_by_key[r["key"]].append((r["ts"], r["value"], r["provenance"]))
        return {k: _annotated(k, key_meta(k), rows_by_key[k], max_points,
                              "min_max", conflict=False)
                for k in keys}

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
            key = chart["key"]
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
            method=method, gap_s=chart_gap_s,
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
        payload["observation"] = observation_states([payload["key"]])
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

    # ---- timeline & event context ------------------------------------------
    @app.get("/timeline", response_class=HTMLResponse)
    def timeline_page(request: Request):
        """Every timestamped event, merged into one chronological list.

        `analysis/timeline.py` already merged DTC occurrences, warning-lamp
        edges, charging sessions, cell sweeps, anomalies and adapter/wake/sleep/
        note events into one ordered stream, but it was only ever surfaced as a
        collapsed marker list on the Explore page and a bare JSON endpoint.
        This page makes the stream browsable: filter by kind and window, and
        follow any event to its own investigation context.
        """
        from analysis.timeline import build_timeline

        _, vid = vehicle_and_id()
        days = _int_param(request, "days", trend_days, 1, 3650)
        include_sim = request.query_params.get("simulated") == "1"
        selected = set(request.query_params.getlist("kind"))

        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds")
        events = (build_timeline(repo, vid, since=since,
                                 include_simulated=include_sim) if vid else [])

        # Facets come from the unfiltered window so the checkboxes show counts
        # that reflect what is actually stored, not what is currently selected.
        kinds: dict[str, int] = {}
        for e in events:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        shown = [e for e in events if not selected or e.kind in selected]
        return render(
            "timeline.html", request, events=[e.as_dict() for e in shown],
            total_events=len(events), shown_events=len(shown),
            kinds=kinds, selected=selected, days=days,
            simulated=include_sim,
        )

    @app.get("/event", response_class=HTMLResponse)
    def event_page(request: Request):
        """The investigation context for one moment: what happened around it.

        Given an event's observation timestamp this shows the event itself, the
        other events in a ±window, and the measurements the car recorded closest
        to that moment -- each with its own real timestamp, provenance and
        distance from the anchor, never re-stamped or interpolated. From here a
        reader can open an investigation anchored on the moment, which records
        the nearby events as *associations* (explicitly not causes).
        """
        from analysis.context import measurement_context
        from analysis.timeline import build_timeline

        _, vid = vehicle_and_id()
        ts = request.query_params.get("ts") or ""
        title = request.query_params.get("title") or ""
        kind = request.query_params.get("kind") or ""
        ecu = request.query_params.get("ecu") or ""
        window_h = _int_param(request, "window_h", 6, 1, 72)

        events = []
        measurements = []
        if vid and ts:
            try:
                anchor = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                anchor = None
            if anchor is not None:
                window_s = window_h * 3600.0
                lo = (anchor - timedelta(seconds=window_s)).isoformat(
                    timespec="seconds")
                hi = (anchor + timedelta(seconds=window_s)).isoformat(
                    timespec="seconds")
                events = build_timeline(repo, vid, since=lo, until=hi)
                measurements = measurement_context(repo, vid, ts,
                                                   window_s=window_s)
                for m in measurements:
                    meta = key_meta(m["key"])
                    m["name"] = meta["name"]
                    m["unit"] = m["unit"] or meta["unit"]
        return render(
            "event.html", request, ts=ts, title=title, kind=kind, ecu=ecu,
            window_h=window_h, events=[e.as_dict() for e in events],
            measurements=measurements,
        )

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
                             criteria: str = Form(""),
                             anchor_ts: str = Form("")):
        from analysis import investigation as inv_mod
        _, vid = vehicle_and_id()
        if not vid:
            raise HTTPException(status_code=400,
                                detail="No vehicle; nothing to investigate.")
        anchor_key = anchor_key.strip() or None
        anchor_ts = anchor_ts.strip() or None
        inv = inv_mod.open_investigation(
            repo, vid, title.strip(), symptom.strip(),
            anchor_key=anchor_key, criteria=criteria.strip())
        # A new investigation opened from the event context is seeded, not left
        # empty: the anchor measurement is observed (with its freshness), and
        # the events in the ±window are recorded as *associations* -- never as
        # causes. `associate_events` tolerates an unparseable anchor by
        # returning [], so a bad timestamp can never take the page down.
        if anchor_key:
            inv_mod.observe_key(repo, vid, anchor_key, _profile(), inv_id=inv.id)
        if anchor_ts:
            inv_mod.associate_events(repo, vid, anchor_ts, inv_id=inv.id)
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
