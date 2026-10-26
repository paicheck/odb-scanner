"""Fault-code (DTC) list and per-code detail pages.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

import json

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from analysis import dtc as dtc_analysis


def register(app, ctx) -> None:
    key_meta = ctx.key_meta
    render = ctx.render
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

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
