"""Explore, timeline and event pages, and their series/discovery APIs.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from web.dashboard import _int_param


def register(app, ctx) -> None:
    chart_gap_s = ctx.chart_gap_s
    discover_entries = ctx.discover_entries
    key_meta = ctx.key_meta
    observation_states = ctx.observation_states
    render = ctx.render
    repo = ctx.repo
    series_for = ctx.series_for
    trend_days = ctx.trend_days
    vehicle_and_id = ctx.vehicle_and_id

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
        # Bounded like every window parameter (_int_param): a bare int() here
        # turned ?days=x into a 500, and ?days=999999999 into a timedelta
        # overflow further down.
        days = _int_param(request, "days", trend_days, 1, 3650)
        max_points = _int_param(request, "max_points", 1500, 1, 20000)
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
            limit=_int_param(request, "limit", 0, 0, 100_000) or None)
        return {"total": len(entries), "returned": len(rows),
                "facets": fac, "entries": [e.as_dict() for e in rows]}

    @app.get("/api/series")
    def api_series(request: Request):
        params = request.query_params
        key = params.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="key is required")
        days = _int_param(request, "days", trend_days, 1, 3650)
        max_points = _int_param(request, "max_points", 1500, 1, 20000)
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
        days = _int_param(request, "days", trend_days, 1, 3650)
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
                try:
                    lo = (anchor - timedelta(seconds=window_s)).isoformat(
                        timespec="seconds")
                    hi = (anchor + timedelta(seconds=window_s)).isoformat(
                        timespec="seconds")
                except OverflowError:
                    # ts=0001-01-01 or 9999-12-31: parses, but the window
                    # falls outside datetime's range. Same as unparseable.
                    anchor = None
            if anchor is not None:
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
