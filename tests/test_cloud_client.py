"""Tests for the collector-side cloud sync.

Drives the watermark and batching against a real (temporary) SQLite database
and a fake HTTP client, so no network and no MySQL are involved. The point is
the property that matters operationally: an upload that is not acknowledged
must never advance the watermark, and a retry must re-send the same ids.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.client import (  # noqa: E402
    CloudSyncError,
    build_payload,
    get_watermark,
    pending_rows,
    set_watermark,
    sync_pending,
)
from cloud.config import ClientSettings  # noqa: E402
from database.repository import Repository  # noqa: E402

VIN = "WVWZZZE1ZMP087053"


def settings(batch_size=500):
    return ClientSettings(
        enabled=True, api_url="https://cloud.example", token="t",
        source_id="python:test-host", batch_size=batch_size,
        sync_interval_s=1.0, timeout_s=5.0, verify_tls=True,
    )


class FakeClient:
    """Records what would have been sent; can be told to fail."""

    def __init__(self, fail_times=0):
        self.payloads = []
        self.fail_times = fail_times

    def upload(self, payload):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CloudSyncError("simulated outage")
        self.payloads.append(payload)
        return {"accepted": len(payload["measurements"]), "duplicates": 0}

    def close(self):
        pass


@pytest.fixture()
def repo(tmp_path):
    r = Repository(tmp_path / "t.db")
    vid = r.ensure_vehicle(VIN)
    for i in range(5):
        r.record_measurement(
            vid, f"2026-10-17T12:00:0{i}Z", "hvb_energy_content_kwh", "bms",
            "UDS-0x22", "0x2AB8", "kWh", "reported", "scalar", "verified",
            raw_response=f"62 2AB8 0{i}", value=30.0 + i)
    yield r
    r.close()


# ---------------------------------------------------------------- watermark
def test_watermark_starts_at_zero_and_round_trips(repo):
    assert get_watermark(repo) == 0
    set_watermark(repo, 3)
    assert get_watermark(repo) == 3


def test_a_corrupt_watermark_falls_back_to_zero_rather_than_wedging(repo):
    repo.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)",
                      ("cloud_sync.measurements.last_id", "not-a-number"))
    repo.conn.commit()
    assert get_watermark(repo) == 0


# ------------------------------------------------------------------ pending
def test_pending_rows_only_returns_rows_after_the_watermark(repo):
    assert len(pending_rows(repo, 0, 500)) == 5
    assert len(pending_rows(repo, 3, 500)) == 2


def test_the_payload_carries_the_vin_and_the_raw_bytes(repo):
    payload = build_payload("python:test-host", pending_rows(repo, 0, 1))
    assert payload["source"]["source_id"] == "python:test-host"
    assert payload["source"]["kind"] == "python"
    m = payload["measurements"][0]
    assert m["vin"] == VIN
    assert m["raw_response"] == "62 2AB8 00"
    assert m["success"] is True


# --------------------------------------------------------------------- sync
def test_sync_uploads_everything_and_advances_the_watermark(repo):
    client = FakeClient()
    result = sync_pending(repo, client, settings())
    assert result == {"batches": 1, "uploaded": 5, "duplicates": 0}
    assert get_watermark(repo) == 5


def test_a_second_sync_is_a_no_op(repo):
    client = FakeClient()
    sync_pending(repo, client, settings())
    again = sync_pending(repo, client, settings())
    assert again["batches"] == 0
    assert len(client.payloads) == 1        # no second request was made


def test_a_larger_history_is_split_into_batches(repo):
    client = FakeClient()
    result = sync_pending(repo, client, settings(batch_size=2))
    assert result["batches"] == 3           # 2 + 2 + 1
    assert result["uploaded"] == 5


def test_a_failed_upload_does_not_advance_the_watermark(repo):
    client = FakeClient(fail_times=1)
    with pytest.raises(CloudSyncError):
        sync_pending(repo, client, settings())
    assert get_watermark(repo) == 0         # nothing was lost


def test_a_retry_after_a_failure_re_sends_the_same_ids(repo):
    failing = FakeClient(fail_times=1)
    with pytest.raises(CloudSyncError):
        sync_pending(repo, failing, settings())

    retry = FakeClient()
    sync_pending(repo, retry, settings())
    # The retry sent all five rows, with the same ids a first attempt would
    # have used -- which is what makes the cloud's INSERT IGNORE safe.
    assert len(retry.payloads[0]["measurements"]) == 5
    first_ids = [m["id"] for m in retry.payloads[0]["measurements"]]
    assert len(set(first_ids)) == 5
    assert first_ids == [m["id"] for m in build_payload(
        "python:test-host", pending_rows(repo, 0, 5))["measurements"]]
