"""Cloud ingest API.

The single door into the cloud MySQL schema. Both writers -- the Python
collector and the Android app -- POST batches of decoded measurements here and
authenticate with a bearer token. The API is the only thing that holds the
database credentials and the only thing that writes, which is what lets the
phone carry a revocable token instead of a database password.

Run it with:

    ODB_CLOUD_API_TOKEN=... ODB_CLOUD_DB_HOST=... \\
    uvicorn cloud.api:create_app --factory --host 0.0.0.0 --port 8080

See ``cloud/README.md`` for deployment.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .config import CloudConfigError, db_settings_from_env
from .db import MySqlStore
from .mapping import InvalidMeasurement, row_from_payload

log = logging.getLogger(__name__)

# A single request may not carry more than this. The batch size is the client's
# to choose; this is the server refusing an unbounded body before it is parsed
# into memory and turned into one enormous INSERT.
MAX_BATCH = 5000

# Length caps mirror the column widths in schema.sql, so an over-long value is
# rejected with a clear 422 instead of being silently truncated by MySQL (which
# would then make two different readings look identical on the next retry).
MAX_BODY_FIELD = 100_000


class SourceIn(BaseModel):
    # Widths again mirror schema.sql (sources/source_id VARCHAR(120),
    # kind VARCHAR(16), label VARCHAR(200)) for the same reason as below: the
    # INSERT uses IGNORE, so an over-long value would be truncated in silence.
    source_id: str = Field(..., max_length=120)
    kind: str = Field("python", max_length=16)
    label: str = Field("", max_length=200)


class MeasurementIn(BaseModel):
    id: str
    ts: str
    key: str
    vin: str | None = None
    ecu: str | None = None
    service: str | None = None
    pid: str | None = None
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    provenance: str | None = None
    decoding_method: str | None = None
    doc_status: str | None = None
    raw_response: str | None = None
    success: bool = True
    error: str | None = None


class IngestRequest(BaseModel):
    source: SourceIn = Field(...)
    measurements: list[MeasurementIn] = Field(default_factory=list)


def _dump(model) -> dict:
    """Pydantic v1/v2 compatible as-dict. FastAPI allows both."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()  # pragma: no cover - only on pydantic v1


def _token_matches(supplied: str, expected: str) -> bool:
    """Constant-time compare that bad input cannot crash.

    Mirrors web/auth.py: secrets.compare_digest raises TypeError on a str with
    non-ASCII characters, so comparing the UTF-8 encodings keeps the timing
    property while turning garbage input into an ordinary mismatch rather than
    a 500.
    """
    if not supplied or not expected:
        return False
    try:
        return secrets.compare_digest(supplied.encode("utf-8"),
                                      expected.encode("utf-8"))
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


def _require_token(request: Request) -> None:
    """Dependency: reject a request whose bearer token is not the configured one."""
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        # A plain header, so a device that cannot easily set Authorization can
        # still authenticate. Same secret, same comparison.
        supplied = request.headers.get("x-odb-token", "").strip()
    if not _token_matches(supplied, request.app.state.token):
        raise HTTPException(status_code=401, detail="invalid or missing API token")


def create_app(store=None, token: str | None = None) -> FastAPI:
    """Build the ingest app.

    ``store`` and ``token`` are injectable so the tests can drive the routes
    against a fake store and a known token, with no MySQL and no environment.
    In production both are read from the environment: the store from the
    ODB_CLOUD_DB_* keys, the token from ODB_CLOUD_API_TOKEN.
    """
    if store is None:
        store = MySqlStore(db_settings_from_env())
    if token is None:
        token = os.environ.get("ODB_CLOUD_API_TOKEN", "").strip()
    if not token:
        # Fail closed. An ingest API with no token would accept any reading from
        # anyone on the internet into the vehicle's permanent archive.
        raise CloudConfigError(
            "ODB_CLOUD_API_TOKEN is not set. The ingest API refuses to start "
            "without a token, because an unauthenticated one would let anyone "
            "write into the vehicle's archive.")

    app = FastAPI(title="odb_scanner cloud ingest", version="1.0.0")
    app.state.store = store
    app.state.token = token

    @app.get("/healthz")
    def healthz() -> dict:
        """Liveness only. Deliberately unauthenticated and data-free."""
        return {"ok": True}

    @app.get("/status")
    def status(request: Request, _: None = Depends(_require_token)) -> dict:
        # Create the tables before counting them. The schema is otherwise only
        # created by the first ingest, but /status is the readiness probe every
        # deploy (and tools/cloud_local_check.py) hits first, so on a fresh
        # database it would answer 500 "table doesn't exist" on a service that
        # is in fact perfectly healthy. The token dependency runs before this
        # body, so an unauthenticated probe still touches no database at all.
        request.app.state.store.ensure_schema()
        return {"ok": True, **request.app.state.store.counts()}

    @app.post("/ingest/measurements")
    def ingest_measurements(payload: IngestRequest, request: Request,
                            _: None = Depends(_require_token)) -> dict:
        if len(payload.measurements) > MAX_BATCH:
            raise HTTPException(
                status_code=413,
                detail=f"batch of {len(payload.measurements)} exceeds the "
                       f"{MAX_BATCH}-row limit; split it into smaller batches")
        if not payload.measurements:
            return {"accepted": 0, "duplicates": 0, "source_id": payload.source.source_id}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            rows = [row_from_payload(payload.source.source_id, _dump(m), now)
                    for m in payload.measurements]
        except InvalidMeasurement as exc:
            # 422 rather than a partial write: a batch that is half invalid is a
            # client bug, and accepting the good half would hide it.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        store_ = request.app.state.store
        store_.ensure_schema()
        store_.touch_source(payload.source.source_id, payload.source.kind,
                            payload.source.label, now)
        inserted, duplicates = store_.insert_measurements(rows)
        log.info("ingest: source=%s received=%d inserted=%d duplicates=%d",
                 payload.source.source_id, len(rows), inserted, duplicates)
        return {
            "accepted": inserted,
            "duplicates": duplicates,
            "source_id": payload.source.source_id,
        }

    return app


if __name__ == "__main__":  # pragma: no cover - manual dev entry point
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0",
                port=int(os.environ.get("PORT", 8080)))
