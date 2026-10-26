"""cPanel "Setup Python App" (Passenger) entry point -- pure WSGI, no framework.

The same routes and HTTP contract as `cloud/api.py` (FastAPI), but implemented
as a plain WSGI callable so the ONLY dependency is `pymysql`. This is the robust
choice on shared hosting, where installing a compiled dependency (pydantic's
Rust core) can fail.

`cloud/mapping.py` and `cloud/db.py` are reused unchanged, so the
correctness-critical validation and idempotency logic remains shared with the
collector -- nothing is reimplemented.

Secrets come from a gitignored `cpanel.env` beside this file (the `ODB_CLOUD_*`
variables that `cloud/config.py` already reads). Create it from
`cpanel.env.example`. This file itself needs no editing.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """Read KEY=VALUE lines into the environment (setdefault: real env wins)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(_HERE / "cpanel.env")

from cloud.config import db_settings_from_env  # noqa: E402
from cloud.db import MySqlStore  # noqa: E402
from cloud.mapping import InvalidMeasurement, row_from_payload  # noqa: E402

MAX_BATCH = 5000  # same limit as cloud/api.py

_token = os.environ.get("ODB_CLOUD_API_TOKEN", "").strip()
if not _token:
    raise RuntimeError(
        "ODB_CLOUD_API_TOKEN is not set (cpanel.env missing or empty). The "
        "ingest API refuses to start without a token.")

_store = MySqlStore(db_settings_from_env())


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


def _read_body(environ: dict) -> bytes:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    return environ["wsgi.input"].read(length) if length else b""


def application(environ: dict, start_response):
    path = (environ.get("PATH_INFO", "") or "/").rstrip("/") or "/"
    method = environ.get("REQUEST_METHOD", "GET")

    if method == "GET" and path == "/healthz":
        return _json("200 OK", {"ok": True}, start_response)

    if not _matches(_token_from(environ), _token):
        return _json("401 Unauthorized",
                     {"detail": "invalid or missing API token"}, start_response)

    if method == "GET" and path == "/status":
        _store.ensure_schema()
        return _json("200 OK", {"ok": True, **_store.counts()}, start_response)

    if method == "POST" and path == "/ingest/measurements":
        try:
            payload = json.loads(_read_body(environ) or b"{}")
        except ValueError:
            return _json("422 Unprocessable Entity",
                         {"detail": "body is not valid JSON"}, start_response)
        if not isinstance(payload, dict):
            return _json("422 Unprocessable Entity",
                         {"detail": "body must be a JSON object"}, start_response)

        source = payload.get("source") or {}
        source_id = str(source.get("source_id") or "")
        measurements = payload.get("measurements") or []

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

        _store.ensure_schema()
        _store.touch_source(source_id, str(source.get("kind") or "python"),
                            str(source.get("label") or ""), now)
        inserted, duplicates = _store.insert_measurements(rows)
        return _json("200 OK", {"accepted": inserted, "duplicates": duplicates,
                                "source_id": source_id}, start_response)

    return _json("404 Not Found", {"detail": "not found"}, start_response)
