"""Tests for the plain-WSGI ingest app used by the cPanel deploy.

cloud/wsgi.py promises the same HTTP contract as the FastAPI app, but gets no
help from pydantic, so the shape and width checks pydantic does for api.py are
exercised here explicitly. The store is the same fake as test_cloud_api.py.
"""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.config import CloudConfigError  # noqa: E402
from cloud.mapping import MAX_BATCH, MAX_BODY_BYTES  # noqa: E402
from cloud.wsgi import make_application  # noqa: E402
from tests.test_cloud_api import TOKEN, FakeStore, body, measurement  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def app(store):
    return make_application(store=store, token=TOKEN)


def call(app, method, path, payload=None, *, raw=None, token=TOKEN,
         content_length=None):
    data = raw if raw is not None else (
        json.dumps(payload).encode() if payload is not None else b"")
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(data) if content_length is None else content_length),
        "wsgi.input": io.BytesIO(data),
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    seen = {}

    def start_response(status, headers):
        seen["status"] = int(status.split()[0])

    out = b"".join(app(environ, start_response))
    return seen["status"], json.loads(out)


def ingest(app, payload, **kw):
    return call(app, "POST", "/ingest/measurements", payload, **kw)


# -------------------------------------------------------------------- auth
def test_refuses_to_start_without_a_token(store):
    with pytest.raises(CloudConfigError):
        make_application(store=store, token="")


def test_healthz_is_open(app):
    assert call(app, "GET", "/healthz", token=None) == (200, {"ok": True})


def test_ingest_and_status_require_the_token(app, store):
    assert ingest(app, body(measurement()), token=None)[0] == 401
    assert ingest(app, body(measurement()), token="nope")[0] == 401
    assert call(app, "GET", "/status", token=None)[0] == 401
    assert store.schema_calls == 0


def test_the_plain_header_is_accepted_too(app):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/status",
               "HTTP_X_ODB_TOKEN": TOKEN, "wsgi.input": io.BytesIO()}
    statuses = []
    app(environ, lambda s, h: statuses.append(s))
    assert statuses == ["200 OK"]


# ------------------------------------------------------------------ ingest
def test_a_batch_is_accepted_then_reported_as_a_duplicate(app, store):
    assert ingest(app, body(measurement())) == (
        200, {"accepted": 1, "duplicates": 0, "source_id": "python:host"})
    assert ingest(app, body(measurement()))[1]["duplicates"] == 1
    assert store.sources_touched[0] == ("python:host", "python", "host")


def test_an_oversized_batch_is_refused(app):
    rows = [measurement() for _ in range(MAX_BATCH + 1)]
    assert ingest(app, body(*rows))[0] == 413


def test_an_oversized_body_is_refused_before_it_is_read(app, store):
    status, _ = ingest(app, None, raw=b"", content_length=MAX_BODY_BYTES + 1)
    assert status == 413
    assert store.schema_calls == 0


@pytest.mark.parametrize("field,limit", [("source_id", 120), ("kind", 16),
                                         ("label", 200)])
def test_an_overlong_source_field_is_refused_not_truncated(app, store, field, limit):
    payload = body(measurement())
    payload["source"][field] = "x" * (limit + 1)
    assert ingest(app, payload)[0] == 422
    assert store.sources_touched == [] and store.seen == set()


@pytest.mark.parametrize("payload", [
    {"measurements": [measurement()]},                       # no source
    {"source": "python:host", "measurements": [measurement()]},
    {"source": {"kind": "python"}, "measurements": [measurement()]},
    {"source": {"source_id": 7}, "measurements": [measurement()]},
    {"source": {"source_id": "a"}, "measurements": "not a list"},
    {"source": {"source_id": "a"}, "measurements": ["not an object"]},
    [1, 2, 3],
])
def test_a_malformed_body_is_a_422_not_a_500(app, store, payload):
    assert ingest(app, payload)[0] == 422
    assert store.seen == set()


def test_invalid_json_is_a_422(app):
    assert ingest(app, None, raw=b"{not json")[0] == 422


def test_an_invalid_row_rejects_the_whole_batch(app, store):
    assert ingest(app, body(measurement(), measurement(ts="")))[0] == 422
    assert store.seen == set()


def test_unknown_routes_are_404(app):
    assert call(app, "GET", "/nope")[0] == 404


# ------------------------------------------------------- passenger entry
@pytest.mark.skipif((REPO / "cloud" / "cpanel.env").exists(),
                    reason="a real cpanel.env would leak into the environment")
def test_passenger_entry_point_builds_the_app(monkeypatch):
    monkeypatch.setenv("ODB_CLOUD_API_TOKEN", TOKEN)
    monkeypatch.setenv("ODB_CLOUD_DB_HOST", "localhost")
    monkeypatch.setenv("ODB_CLOUD_DB_USER", "u")
    monkeypatch.setenv("ODB_CLOUD_DB_NAME", "d")
    spec = importlib.util.spec_from_file_location(
        "passenger_wsgi_under_test", REPO / "cloud" / "passenger_wsgi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.application)
