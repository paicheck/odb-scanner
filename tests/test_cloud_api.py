"""Tests for the cloud ingest API routes.

The database is replaced by a fake store, so these run with no MySQL and no
Docker. What is covered is the API's own contract: authentication, the
duplicate count, batch limits, and all-or-nothing rejection of a bad batch.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.api import MAX_BATCH, IngestRequest, create_app  # noqa: E402
from cloud.client import build_payload  # noqa: E402

TOKEN = "test-token-value"


class FakeStore:
    """Stands in for MySqlStore: remembers ids so duplicates can be counted."""

    def __init__(self):
        self.seen = set()
        self.sources_touched = []
        self.schema_calls = 0

    def ensure_schema(self):
        self.schema_calls += 1

    def touch_source(self, source_id, kind, label, now):
        self.sources_touched.append((source_id, kind, label))

    def insert_measurements(self, rows):
        inserted = 0
        for row in rows:
            if row[0] not in self.seen:
                self.seen.add(row[0])
                inserted += 1
        return (inserted, len(rows) - inserted)

    def counts(self):
        return {"measurements": len(self.seen), "sources": 1}


@pytest.fixture()
def store():
    return FakeStore()


@pytest.fixture()
def client(store):
    return TestClient(create_app(store=store, token=TOKEN))


def measurement(**over):
    base = {
        "id": "b3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d5a44",
        "ts": "2026-10-17T12:00:00Z",
        "key": "hvb_energy_content_kwh",
        "vin": "WVWZZZE1ZMP087053",
        "value": 31.4, "unit": "kWh",
        "provenance": "reported", "decoding_method": "scalar",
        "success": True,
    }
    base.update(over)
    return base


def body(*measurements, source_id="python:host"):
    return {
        "source": {"source_id": source_id, "kind": "python", "label": "host"},
        "measurements": list(measurements),
    }


def auth_header():
    return {"Authorization": f"Bearer {TOKEN}"}


# -------------------------------------------------------------------- auth
def test_healthz_is_open_and_data_free(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_ingest_requires_a_token(client):
    r = client.post("/ingest/measurements", json=body(measurement()))
    assert r.status_code == 401


def test_a_wrong_token_is_rejected(client):
    r = client.post("/ingest/measurements", json=body(measurement()),
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_the_plain_header_is_accepted_too(client):
    r = client.post("/ingest/measurements", json=body(measurement()),
                    headers={"X-ODB-Token": TOKEN})
    assert r.status_code == 200


def test_status_requires_a_token_and_reports_counts(client, store):
    assert client.get("/status").status_code == 401
    # An unauthenticated probe must not reach the database, so it creates no
    # schema either.
    assert store.schema_calls == 0
    r = client.get("/status", headers=auth_header())
    assert r.status_code == 200 and r.json()["measurements"] == 0
    # A fresh database has no tables until something creates them; /status is
    # the endpoint a deploy probes first, so it must create them itself rather
    # than 500 "table doesn't exist".
    assert store.schema_calls == 1


# ------------------------------------------------------------------ ingest
def test_a_batch_is_accepted(client, store):
    r = client.post("/ingest/measurements", json=body(measurement()),
                    headers=auth_header())
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "duplicates": 0,
                        "source_id": "python:host"}
    assert store.schema_calls == 1
    assert store.sources_touched == [("python:host", "python", "host")]


def test_re_sending_the_same_row_is_reported_as_a_duplicate(client):
    client.post("/ingest/measurements", json=body(measurement()),
                headers=auth_header())
    r = client.post("/ingest/measurements", json=body(measurement()),
                    headers=auth_header())
    assert r.json() == {"accepted": 0, "duplicates": 1,
                        "source_id": "python:host"}


def test_an_invalid_row_rejects_the_whole_batch(client, store):
    good = measurement()
    bad = measurement(id="c3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d5a45", ts="yesterday")
    r = client.post("/ingest/measurements", json=body(good, bad),
                    headers=auth_header())
    assert r.status_code == 422
    assert store.seen == set()          # nothing was half-written


def test_an_oversized_batch_is_refused(client):
    big = [measurement(id=f"b3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d{ i:05d}")
           for i in range(MAX_BATCH + 1)]
    r = client.post("/ingest/measurements", json=body(*big),
                    headers=auth_header())
    assert r.status_code == 413


def test_an_empty_batch_is_accepted_and_writes_nothing(client, store):
    r = client.post("/ingest/measurements", json=body(), headers=auth_header())
    assert r.status_code == 200
    assert r.json()["accepted"] == 0
    assert store.schema_calls == 0      # no need to touch the database


def test_android_style_timestamps_and_extra_fields_are_accepted(client):
    # The phone sends Instant.now() (nanoseconds, trailing Z) and only the
    # fields it knows; anything unexpected must not break ingest.
    r = client.post("/ingest/measurements",
                    json=body(measurement(ts="2026-10-17T12:00:00.123456789Z",
                                          source="android")),
                    headers=auth_header())
    assert r.status_code == 200


def test_the_python_clients_payload_validates_against_the_api_model():
    """The two halves of the contract, checked against each other.

    `build_payload` (collector) and `IngestRequest` (API) are edited in
    different files; a renamed field would otherwise only fail in production,
    against a live cloud database. Building the real client payload from a row
    shaped exactly like the SQL query returns and validating it here makes that
    drift a test failure instead.
    """
    row = {
        "id": 1, "vin": "WVWZZZE1ZMP087053", "ts": "2026-10-17T12:00:00Z",
        "ecu": "bms", "service": "UDS-0x22", "pid": "0x2AB8",
        "key": "hvb_energy_content_kwh", "value": 31.4, "text_value": None,
        "unit": "kWh", "provenance": "reported", "decoding_method": "scalar",
        "doc_status": "verified", "raw_response": "62 2AB8 0C 44",
        "success": 1, "error": None,
    }
    payload = build_payload("python:host", [row])
    parsed = IngestRequest.model_validate(payload) \
        if hasattr(IngestRequest, "model_validate") else IngestRequest.parse_obj(payload)
    assert parsed.source.source_id == "python:host"
    assert parsed.measurements[0].key == "hvb_energy_content_kwh"
    assert parsed.measurements[0].raw_response == "62 2AB8 0C 44"
