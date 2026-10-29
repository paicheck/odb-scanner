"""Local web dashboard (FastAPI + Jinja2 + Chart.js).

READ-ONLY: no endpoint can transmit anything to the vehicle. The dashboard
only reads from SQLite; collection runs as a separate process (main.py collect).

Network-exposed binds require an access token -- see web/auth.py.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai.service import AnalysisService
from config import Config
from database.repository import Repository
from web.auth import (
    QUERY,
    CookiePromotionMiddleware,
    HostAllowListMiddleware,
    SameOriginMiddleware,
    SecurityHeadersMiddleware,
    TokenAuthMiddleware,
    allowed_host_names,
    install_access_log_redaction,
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


def _age_seconds(ts):
    """Seconds since an ISO timestamp (naive = UTC), or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _format_age(ts):
    """'just now', '12 min ago', '5 h ago', '3 d ago' -- how old a reading is.

    A latest value without its age reads as current; on a car that is read in
    sessions days apart, "latest" is usually days old, and that is the single
    most important thing to know about it.
    """
    s = _age_seconds(ts)
    if s is None:
        return "age unknown"
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 48 * 3600:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} d ago"


def _is_stale(ts, limit_s=24 * 3600):
    s = _age_seconds(ts)
    return s is None or s > limit_s


_templates.env.filters["fmtnum"] = _format_number
_templates.env.filters["fmtts"] = _format_ts
_templates.env.filters["age"] = _format_age
_templates.env.filters["stale"] = _is_stale

# The AI question goes into an LLM prompt and, on the default model, occupies
# a GPU for minutes, so it gets a tight cap. (The investigation form fields
# have their own caps in web/routes/investigations.py.)
MAX_QUESTION_CHARS = 1000

# Central Europe at a country-scale zoom: the map's opening view while no fix
# has been recorded. Overridden by web.map_default_view.
DEFAULT_MAP_VIEW = [50.0, 10.0, 5]


def _map_view(value) -> list:
    """[lat, lon, zoom] from config, or the default when it is unusable."""
    try:
        lat, lon, zoom = (float(v) for v in value)
    except (TypeError, ValueError):
        return list(DEFAULT_MAP_VIEW)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 1 <= zoom <= 19):
        return list(DEFAULT_MAP_VIEW)
    return [lat, lon, int(zoom)]


# The only third-party origin the dashboard may ever load from: OpenStreetMap's
# standard tile server, for the car-position map (web.map_tiles = "osm").
OSM_TILE_ORIGIN = "https://tile.openstreetmap.org"

# ---------------------------------------------------------------------------
# Chart panels. Each panel answers one question; `assemble_panels` refuses to
# put two units on one axis unless a panel explicitly asks for a labelled
# second axis (`dual`). `visible` limits which series are drawn by default --
# the others stay one checkbox away.
# ---------------------------------------------------------------------------

# Two panels on one shared time axis, not one chart with two y-scales: a dual
# axis invites reading the crossing of two lines as meaningful when it is only
# an artefact of how each scale was fitted.
OVERVIEW_PANELS = [
    {"id": "ov-soc", "title": "State of charge (BMS)",
     "question": "How full was the pack?",
     "keys": ["soc_abs"]},
    {"id": "ov-v", "title": "HV pack voltage",
     "question": "What terminal voltage did the BMS report?",
     "keys": ["pack_voltage"]},
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
# Most points a scatter is given; more is megabytes of page and no more to see.
SCATTER_MAX_POINTS = 2000

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
    # "osm": OpenStreetMap tiles behind the car-position map. "off": no map
    # tiles and no request leaves this machine; the position is shown as
    # coordinates. Viewing tiles tells the tile server roughly where the car is.
    map_tiles = str(cfg.get("web.map_tiles", "osm")).strip().lower()
    if map_tiles not in ("osm", "off"):
        map_tiles = "off"
    # Where the map opens while no position has been recorded: [lat, lon,
    # zoom]. It is a display default and is labelled as such on the card.
    map_default_view = _map_view(cfg.get("web.map_default_view"))
    token = resolve_token(cfg, host)
    # The value actually enforced. `main.py serve` prints from this rather
    # than resolving again: if persisting a generated token failed, a second
    # resolve_token() minted a DIFFERENT one and the printed URL did not work.
    app.state.auth_token = token
    install_access_log_redaction()
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
    #     SecurityHeaders -> HostAllowList -> SameOrigin -> CookiePromotion
    #         -> TokenAuth
    #
    # HostAllowList sits outside SameOrigin because SameOrigin trusts the
    # request's own origin, which is built from the Host header -- exactly what
    # a DNS-rebinding page controls.
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
    app.add_middleware(HostAllowListMiddleware,
                       allowed=allowed_host_names(cfg, host))
    app.add_middleware(SecurityHeadersMiddleware,
                       img_src_extra=OSM_TILE_ORIGIN if map_tiles == "osm" else "")

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

    disk_seen: list = [0.0, None]           # (monotonic time, disk_space())

    def disk_warning() -> dict | None:
        """disk_space() when the drive is low, checked at most once a minute."""
        now = time.monotonic()
        if now - disk_seen[0] > 60:
            disk_seen[:] = [now, repo.disk_space()]
        space = disk_seen[1]
        return space if space and space["low"] else None

    def render(name: str, request: Request, **extra):
        vehicle, _ = vehicle_and_id()
        ctx = {"vehicle": vehicle, "disk": disk_warning(), **extra}
        return _templates.TemplateResponse(request, name, ctx)

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

    # Routes live in web/routes/, one module per page area. They get the
    # shared state and helpers above through `ctx` rather than closing over
    # create_app's locals.
    from web.routes import (
        ai,
        battery,
        charging,
        explore,
        faults,
        investigations,
        overview,
        route,
        trips,
    )

    ctx = SimpleNamespace(
        cfg=cfg,
        map_tiles=map_tiles,
        map_default_view=map_default_view,
        _fresh_window=_fresh_window,
        _profile=_profile,
        _registry_for_meta=_registry_for_meta,
        _window_days=_window_days,
        _windows=_windows,
        bms_refusal=bms_refusal,
        charging_days=charging_days,
        chart_gap_s=chart_gap_s,
        chart_series=chart_series,
        discover_entries=discover_entries,
        key_meta=key_meta,
        measurement_catalog=measurement_catalog,
        measurement_sources=measurement_sources,
        observation_states=observation_states,
        panels_context=panels_context,
        render=render,
        repo=repo,
        series_for=series_for,
        svc=svc,
        trend_days=trend_days,
        vehicle_and_id=vehicle_and_id,
    )
    for routes in (overview, battery, faults, charging, ai, explore, investigations,
                   route, trips):
        routes.register(app, ctx)

    return app
