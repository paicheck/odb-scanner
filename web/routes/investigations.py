"""Investigation workbench pages and the CSV/JSON exports.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from web.dashboard import _int_param, _redirect


def register(app, ctx) -> None:
    _fresh_window = ctx._fresh_window
    _profile = ctx._profile
    discover_entries = ctx.discover_entries
    render = ctx.render
    repo = ctx.repo
    trend_days = ctx.trend_days
    vehicle_and_id = ctx.vehicle_and_id

    def _owned_investigation(investigation_id: int, *, writable: bool = False):
        """The investigation row if it exists AND belongs to this vehicle.

        Otherwise 404, before anything is written. Writing evidence for an id
        that does not exist failed a foreign-key check mid-request; the
        dashboard shows one vehicle (first_vehicle()), so another vehicle's
        investigation is "not found" here exactly as /dtcs/{id} and
        /charging/{id} treat other vehicles' rows.

        `writable` also refuses a resolved investigation (409): its conclusion
        was confirmed against the evidence it had, and adding evidence or
        re-finalising afterwards would rewrite that record silently. Open a new
        investigation instead. "insufficient-evidence" stays writable -- adding
        evidence and finalising again is exactly what that status asks for.
        """
        _, vid = vehicle_and_id()
        row = repo.investigation(investigation_id) if vid else None
        if row is None or row["vehicle_id"] != vid:
            raise HTTPException(status_code=404, detail="No such investigation")
        if writable and row["status"] == "resolved":
            raise HTTPException(
                status_code=409,
                detail="This investigation is resolved; open a new one.")
        return row

    @app.get("/investigations", response_class=HTMLResponse)
    def investigations_page(request: Request):
        _, vid = vehicle_and_id()
        rows = [dict(r) for r in repo.investigations(vid)] if vid else []
        # No ?error= parameter: nothing ever redirected with one, so all it
        # did was let a crafted link print arbitrary text in the error box.
        return render("investigations.html", request, investigations=rows)

    @app.get("/investigations/{investigation_id}", response_class=HTMLResponse)
    def investigation_page(request: Request, investigation_id: int):
        from analysis import investigation as inv_mod
        _owned_investigation(investigation_id)
        inv = inv_mod.load_investigation(repo, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="No such investigation")
        ok, reason = inv_mod.criteria_met(inv)
        return render("investigation.html", request, inv=inv,
                      criteria_ok=ok, criteria_reason=reason)

    @app.post("/investigations/new")
    # Every field is stored permanently and rendered on /investigations, so
    # each is capped (422 above the limit). Starlette's own ceiling is ~1 MB
    # per field, which is not a limit anyone means.
    def create_investigation(request: Request,
                             title: str = Form(..., max_length=200),
                             symptom: str = Form(..., max_length=2000),
                             anchor_key: str = Form("", max_length=80),
                             criteria: str = Form("", max_length=2000),
                             anchor_ts: str = Form("", max_length=40)):
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
                              key: str = Form(..., max_length=80)):
        from analysis import investigation as inv_mod
        row = _owned_investigation(investigation_id, writable=True)
        inv_mod.observe_key(repo, row["vehicle_id"], key.strip(), _profile(),
                            inv_id=investigation_id)
        return _redirect(f"/investigations/{investigation_id}")

    @app.post("/investigations/{investigation_id}/finalize")
    def investigation_finalize(request: Request, investigation_id: int,
                               summary: str = Form("", max_length=4000),
                               criteria_confirmed: bool = Form(False)):
        from analysis import investigation as inv_mod
        _owned_investigation(investigation_id, writable=True)
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
    def _disposition(filename: str) -> str:
        """An attachment header that cannot be broken by the name inside it.

        Filenames embed a query-supplied key: `key=a"b` closed the quoted
        string early, and a non-Latin-1 key raised while the header was
        encoded (a 500). Anything outside [A-Za-z0-9._-] becomes '_'.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "export"
        return f'attachment; filename="{safe}"'

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
            headers={"Content-Disposition": _disposition(filename)})

    def _json_download(payload: object, filename: str) -> Response:
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": _disposition(filename)})

    def _stamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @app.get("/api/export/series")
    def export_series(request: Request):
        """Download the raw stored rows for one key (CSV or JSON).

        Unlike the chart payload this keeps every column and every row --
        failed reads, text decodes and the raw response bytes included -- so an
        exported file can be re-examined without the dashboard's sampling.
        """
        from decoders.aliases import resolve

        params = request.query_params
        key = params.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="key is required")
        # The stored key, as series_for() resolves it for the chart: exporting
        # an alias (accelerator_pedal_pct) used to return zero rows while the
        # chart for the same key showed data.
        key = resolve(key)
        _, vid = vehicle_and_id()
        if not vid:
            raise HTTPException(status_code=400, detail="No vehicle.")
        fmt = (params.get("format") or "csv").lower()
        days = _int_param(request, "days", trend_days, 1, 3650)
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
        if fmt == "json":
            return _json_download(payload, f"{key}_{_stamp()}.json")
        return _csv_download(rows, columns, f"{key}_{_stamp()}.csv")

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
        _owned_investigation(investigation_id)
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
