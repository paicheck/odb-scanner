"""The digital twin's state of the car, as JSON: what is observed, inferred
or unknown, as of now or any earlier moment (analysis/twin.py).

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request

from analysis import twin


def _as_of(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=(
            "as_of must be an ISO time, e.g. 2026-10-31T08:00:00Z")) from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def register(app, ctx) -> None:
    repo = ctx.repo
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/api/twin")
    def api_twin(request: Request):
        as_of = _as_of(request.query_params.get("as_of"))
        _, vid = vehicle_and_id()
        if not vid:
            return {"as_of": None, "mode": "unknown", "quantities": {}, "counts": {}}
        state = twin.vehicle_state(repo, vid, as_of=as_of)
        if as_of is None:
            # How the consumption figure did is about now; an earlier moment's
            # backtest would need the drives up to then, which it does not use.
            state["predictions"] = twin.prediction_vs_observation(repo, vid)
        return state
