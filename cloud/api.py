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
from .mapping import (
    MAX_BATCH,
    MAX_BODY_BYTES,
    SOURCE_ID_MAX,
    SOURCE_KIND_MAX,
    SOURCE_LABEL_MAX,
    InvalidMeasurement,
    row_from_payload,
)

log = logging.getLogger(__name__)


class SourceIn(BaseModel):
    # Widths mirror schema.sql (sources/source_id VARCHAR(120), kind
    # VARCHAR(16), label VARCHAR(200)): the INSERT uses IGNORE, so an over-long
    # value would be truncated in silence. cloud/wsgi.py applies the same caps
    # through mapping.source_from_payload.
    source_id: str = Field(..., max_length=SOURCE_ID_MAX)
    kind: str = Field("python", max_length=SOURCE_KIND_MAX)
    label: str = Field("", max_length=SOURCE_LABEL_MAX)


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


class _BodyLimit(Exception):
    pass


class BodySizeLimitMiddleware:
    """Refuse a request body over MAX_BODY_BYTES before it is parsed.

    The MAX_BATCH check runs only after pydantic has read and parsed the whole
    body, so on its own it does not stop a huge body from being buffered. A
    declared Content-Length over the cap is refused immediately; a chunked body
    is counted as it arrives and refused once it crosses the cap.
    """

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._refuse(send)
                    return

        seen = 0
        exceeded = False
        refused = False

        async def counted_receive():
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    raise _BodyLimit()
            return message

        async def guarded_send(message):
            # FastAPI turns an exception raised while it reads the body into
            # its own 400, so the 413 is substituted for whatever response the
            # app produces once the cap has been crossed.
            nonlocal refused
            if not exceeded:
                await send(message)
            elif not refused:
                refused = True
                await self._refuse(send)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except _BodyLimit:
            pass
        if exceeded and not refused:
            await self._refuse(send)

    async def _refuse(self, send):
        body = (b'{"detail":"request body exceeds the '
                + str(self.max_bytes).encode() + b'-byte limit"}')
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


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
    app.add_middleware(BodySizeLimitMiddleware)

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
