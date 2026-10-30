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
    id_namespace,
    pending_rows,
    set_watermark,
    sync_pending,
)
from cloud.config import ClientSettings  # noqa: E402
from database.repository import Repository  # noqa: E402

VIN = "WVWZZZE1ZMS000001"


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
        "python:test-host", pending_rows(repo, 0, 5),
        id_namespace=id_namespace(repo, "python:test-host"))["measurements"]]


# ---------------------------------------------------- value vs text_value
def _text_row(repo, key, value):
    """A reading whose value is text, exactly as the collector stores it."""
    vid = repo.ensure_vehicle(VIN)
    repo.record_measurement(
        vid, "2026-10-17T12:00:09Z", key, "-", "UDS-0x22", "-", "", "reported",
        "scalar", "experimental", "", value, None, True, None)
    repo.conn.commit()


def test_a_text_reading_leaves_value_null_and_keeps_the_text(repo):
    """`gear` is "P" and `vin` is a string, and both live in `value` locally.

    The cloud column is DOUBLE and the payload types `value` as a number, so
    forwarding the local column unchanged is a 422 at the edge -- which is how
    the first sync of this car's history stopped, on `gear`, with 27 batches
    uploaded and the rest held back by the watermark. The row is not malformed;
    it belongs in the other column.
    """
    _text_row(repo, "gear", "P")
    payload = build_payload("python:test-host", pending_rows(repo, 5, 10))
    m = payload["measurements"][0]
    assert m["key"] == "gear"
    assert m["value"] is None
    assert m["text_value"] == "P"


def test_a_numeric_reading_still_travels_as_a_number(repo):
    """The fix must not push numbers into the text column."""
    payload = build_payload("python:test-host", pending_rows(repo, 0, 1))
    assert payload["measurements"][0]["value"] == 30.0
    assert payload["measurements"][0]["text_value"] is None


def test_split_value_moves_only_what_is_not_a_number():
    from cloud.mapping import split_value
    assert split_value(31.4, None) == (31.4, None)
    assert split_value(0, None) == (0.0, None)            # zero is not missing
    assert split_value(None, '{"value": 1}') == (None, '{"value": 1}')
    assert split_value("12.5", None) == (12.5, None)      # a numeric string
    assert split_value(" P ", None) == (None, " P ")      # preserved exactly
    assert split_value("WVWZZZE1ZMS000001", None) == (None, "WVWZZZE1ZMS000001")
    assert split_value(True, None) == (1.0, None)


def test_split_value_carries_a_value_mysql_cannot_store(repo):
    """NaN would fail the INSERT and then wedge every later batch."""
    from cloud.mapping import split_value
    value, text = split_value(float("nan"), None)
    assert value is None and text == "NaN"
    value, text = split_value(float("inf"), None)
    assert value is None and text == "Infinity"
    # A number already accompanied by structured data keeps both: the text
    # column is not overwritten by the numeric one.
    assert split_value(31.4, '{"a": 1}') == (31.4, '{"a": 1}')


# ------------------------------------------------------- cloud id identity
# Cloud ids were uuid5(host source_id, local id). Two databases on one host
# both start at id 1, and SQLite reuses the ids of deleted newest rows, so the
# server's INSERT IGNORE silently dropped real readings as "duplicates".

def _fill(repo, n, start_ts=0):
    vid = repo.ensure_vehicle(VIN)
    for i in range(n):
        repo.record_measurement(
            vid, f"2026-10-18T12:{(start_ts + i) // 60:02d}:{(start_ts + i) % 60:02d}Z",
            "hvb_energy_content_kwh", "bms", "UDS-0x22", "0x2AB8", "kWh",
            "reported", "scalar", "verified", raw_response="62 2AB8 00",
            value=1.0 * i)
    repo.conn.commit()


def _sent_ids(client):
    return [m["id"] for p in client.payloads for m in p["measurements"]]


def test_two_fresh_databases_on_one_host_get_distinct_ids(tmp_path):
    a, b = Repository(tmp_path / "a.db"), Repository(tmp_path / "b.db")
    _fill(a, 3)
    _fill(b, 3)
    ca, cb = FakeClient(), FakeClient()
    sync_pending(a, ca, settings())
    sync_pending(b, cb, settings())
    assert set(_sent_ids(ca)).isdisjoint(_sent_ids(cb))
    a.close()
    b.close()


def test_an_already_uploaded_database_keeps_its_ids(repo):
    # A database that synced before namespaces existed: watermark > 0, no
    # namespace stored. Its rows must keep the ids they were uploaded under.
    legacy = [m["id"] for m in build_payload(
        "python:test-host", pending_rows(repo, 0, 5))["measurements"]]
    set_watermark(repo, 2)
    client = FakeClient()
    sync_pending(repo, client, settings())
    assert _sent_ids(client) == legacy[2:]
    assert id_namespace(repo, "python:test-host") == "python:test-host"


def test_readings_after_a_prune_of_the_newest_rows_are_uploaded(tmp_path):
    repo = Repository(tmp_path / "p.db")
    _fill(repo, 5)
    first = FakeClient()
    sync_pending(repo, first, settings())
    assert get_watermark(repo) == 5
    repo.prune(days=0, keep_raw=False)          # deletes every measurement
    _fill(repo, 3, start_ts=30)                  # SQLite hands out ids 1..3 again
    assert [r["id"] for r in repo.conn.execute("SELECT id FROM measurements")] \
        == [1, 2, 3]
    second = FakeClient()
    sync_pending(repo, second, settings())
    sent = _sent_ids(second)
    assert len(sent) == 3, "new readings were skipped as already uploaded"
    assert set(sent).isdisjoint(_sent_ids(first)), "ids collide with old rows"
    repo.close()
