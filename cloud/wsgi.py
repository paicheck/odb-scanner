"""Plain-WSGI ingest app for cPanel "Setup Python App" (Passenger).

The same routes and HTTP contract as `cloud/api.py` (FastAPI), but implemented
as a plain WSGI callable so the ONLY dependency is `pymysql`. This is the robust
choice on shared hosting, where installing a compiled dependency (pydantic's
Rust core) can fail.

`cloud/mapping.py` and `cloud/db.py` are reused unchanged, so the
correctness-critical validation, limits and idempotency logic remain shared
with the FastAPI app -- nothing is reimplemented. What pydantic does for
`api.py` (object shapes, source field widths) is done here explicitly via
`mapping.source_from_payload` and the type checks below.

`passenger_wsgi.py` loads the environment and calls `make_application()`;
tests call it with a fake store and a known token.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone

from .config import CloudConfigError, db_settings_from_env
from .db import MySqlStore
from .mapping import (
    MAX_BATCH,
    MAX_BODY_BYTES,
    InvalidMeasurement,
    row_from_payload,
    source_from_payload,
)


def _matches(supplied: str, expected: str) -> bool:
    if not supplied or not expected:
        return False
    try:
        return secrets.compare_digest(supplied.encode("utf-8"),
                                      expected.encode("utf-8"))
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


def _token_from(environ: dict) -> str:
    header = environ.get("HTTP_AUTHORIZATION", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return environ.get("HTTP_X_ODB_TOKEN", "").strip()


def _json(status: str, payload: dict, start_response):
    body = json.dumps(payload).encode("utf-8")
    start_response(status, [("Content-Type", "application/json"),
                            ("Content-Length", str(len(body)))])
    return [body]


def _content_length(environ: dict) -> int:
    try:
        return max(int(environ.get("CONTENT_LENGTH") or 0), 0)
    except ValueError:
        return 0


def make_application(store=None, token: str | None = None):
    """Build the WSGI callable. Fails closed without a token, like api.py."""
    if token is None:
        token = os.environ.get("ODB_CLOUD_API_TOKEN", "").strip()
    if not token:
        raise CloudConfigError(
            "ODB_CLOUD_API_TOKEN is not set (cpanel.env missing or empty). The "
            "ingest API refuses to start without a token.")
    if store is None:
        store = MySqlStore(db_settings_from_env())

    def application(environ: dict, start_response):
        path = (environ.get("PATH_INFO", "") or "/").rstrip("/") or "/"
        method = environ.get("REQUEST_METHOD", "GET")

        if method == "GET" and path == "/healthz":
            return _json("200 OK", {"ok": True}, start_response)

        if not _matches(_token_from(environ), token):
            return _json("401 Unauthorized",
                         {"detail": "invalid or missing API token"}, start_response)

        if method == "GET" and path == "/status":
            store.ensure_schema()
            return _json("200 OK", {"ok": True, **store.counts()}, start_response)

        if method == "POST" and path == "/ingest/measurements":
            return _ingest(environ, start_response)

        return _json("404 Not Found", {"detail": "not found"}, start_response)

    def _ingest(environ: dict, start_response):
        length = _content_length(environ)
        if length > MAX_BODY_BYTES:
            # Refused before reading, so an oversized body is never buffered.
            return _json("413 Payload Too Large",
                         {"detail": f"body of {length} bytes exceeds the "
                                    f"{MAX_BODY_BYTES}-byte limit"}, start_response)
        body = environ["wsgi.input"].read(length) if length else b""
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return _json("422 Unprocessable Entity",
                         {"detail": "body is not valid JSON"}, start_response)
        if not isinstance(payload, dict):
            return _json("422 Unprocessable Entity",
                         {"detail": "body must be a JSON object"}, start_response)

        measurements = payload.get("measurements") or []
        try:
            source_id, kind, label = source_from_payload(payload.get("source"))
            if not isinstance(measurements, list):
                raise InvalidMeasurement("'measurements' must be a list")
            if not all(isinstance(m, dict) for m in measurements):
                raise InvalidMeasurement("every measurement must be an object")
        except InvalidMeasurement as exc:
            return _json("422 Unprocessable Entity", {"detail": str(exc)},
                         start_response)

        if len(measurements) > MAX_BATCH:
            return _json("413 Payload Too Large",
                         {"detail": f"batch of {len(measurements)} exceeds the "
                                    f"{MAX_BATCH}-row limit; split it"}, start_response)
        if not measurements:
            return _json("200 OK", {"accepted": 0, "duplicates": 0,
                                    "source_id": source_id}, start_response)

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            rows = [row_from_payload(source_id, m, now) for m in measurements]
        except InvalidMeasurement as exc:
            return _json("422 Unprocessable Entity", {"detail": str(exc)},
                         start_response)

        store.ensure_schema()
        store.touch_source(source_id, kind, label, now)
        inserted, duplicates = store.insert_measurements(rows)
        return _json("200 OK", {"accepted": inserted, "duplicates": duplicates,
                                "source_id": source_id}, start_response)

    return application
