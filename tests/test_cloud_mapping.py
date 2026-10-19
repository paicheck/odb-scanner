"""Unit tests for the cloud mapping layer.

Pure functions only: no MySQL, no HTTP, no vehicle. These are the rules that
decide what a client's timestamp, id and value mean once they reach the shared
schema, so they are the cheapest place to catch a normalisation mistake.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.mapping import (  # noqa: E402
    CLOUD_ID_NAMESPACE,
    InvalidMeasurement,
    canonical_uuid,
    cloud_measurement_id,
    parse_ts,
    row_from_payload,
)


# ------------------------------------------------------------------- uuid
def test_canonical_uuid_lowercases_and_accepts_any_uuid_form():
    assert canonical_uuid("B3F1C1E0-6A2B-5D4C-9F01-2B8C7E6D5A44") == \
        "b3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d5a44"


def test_a_non_uuid_id_is_rejected_not_truncated():
    # CHAR(36) would silently cut anything longer, and then the retry would not
    # match the stored row. Better a 422 at the edge.
    with pytest.raises(InvalidMeasurement):
        canonical_uuid("not-a-uuid-but-37-characters-long!!")


def test_measurement_id_is_stable_for_the_same_row():
    a = cloud_measurement_id("python:host", 42)
    b = cloud_measurement_id("python:host", 42)
    assert a == b


def test_measurement_id_differs_across_sources_and_rows():
    assert cloud_measurement_id("python:host", 42) != \
        cloud_measurement_id("python:other", 42)
    assert cloud_measurement_id("python:host", 42) != \
        cloud_measurement_id("python:host", 43)


def test_the_namespace_is_the_frozen_value_retries_depend_on():
    # If this changes, every already-uploaded row looks new on the next sync.
    assert str(CLOUD_ID_NAMESPACE) == "d9a2f0c4-6b1e-4f37-9c58-2a7d5e10b3aa"


# -------------------------------------------------------------- timestamps
def test_zulu_and_offset_and_naive_all_land_on_the_same_utc_instant():
    expected = datetime(2026, 10, 17, 12, 0, 0)
    assert parse_ts("2026-10-17T12:00:00Z") == expected
    assert parse_ts("2026-10-17T12:00:00+00:00") == expected
    assert parse_ts("2026-10-17T12:00:00") == expected          # naive = UTC
    assert parse_ts("2026-10-17 12:00:00") == expected          # space separator


def test_an_offset_is_converted_to_utc_not_kept_as_written():
    assert parse_ts("2026-10-17T14:00:00+02:00") == \
        datetime(2026, 10, 17, 12, 0, 0)


def test_nanosecond_fraction_from_android_is_accepted_and_truncated():
    # Instant.now().toString() can carry 9 fractional digits, which
    # datetime.fromisoformat cannot read on Python 3.10.
    assert parse_ts("2026-10-17T12:00:00.123456789Z") == \
        datetime(2026, 10, 17, 12, 0, 0, 123456)


def test_an_unparseable_timestamp_is_rejected():
    for bad in ("yesterday", "2026-13-01T00:00:00Z", ""):
        with pytest.raises(InvalidMeasurement):
            parse_ts(bad)


# ------------------------------------------------------------ row building
def _payload(**over):
    base = {
        "id": "b3f1c1e0-6a2b-5d4c-9f01-2b8c7e6d5a44",
        "vin": "WVWZZZE1ZMP087053",
        "ts": "2026-10-17T12:00:00Z",
        "ecu": "bms", "service": "UDS-0x22", "pid": "0x2AB8",
        "key": "hvb_energy_content_kwh", "value": 31.4, "unit": "kWh",
        "provenance": "reported", "decoding_method": "scalar",
        "doc_status": "verified", "raw_response": "62 2AB8 0C 44",
        "success": True, "error": None,
    }
    base.update(over)
    return base


def test_row_keeps_the_raw_timestamp_and_the_exact_bytes():
    now = datetime(2026, 10, 17, 12, 0, 5)
    row = row_from_payload("python:host", _payload(), now)
    cols = dict(zip(
        ("id", "source_id", "vin", "ts", "ts_raw", "ecu", "service", "pid",
         "key", "value", "text_value", "unit", "provenance", "decoding_method",
         "doc_status", "raw_response", "success", "error", "received_at"),
        row))
    assert cols["ts"] == datetime(2026, 10, 17, 12, 0, 0)   # normalised
    assert cols["ts_raw"] == "2026-10-17T12:00:00Z"         # never altered
    assert cols["raw_response"] == "62 2AB8 0C 44"          # raw bytes kept
    assert cols["success"] == 1                             # bool -> tinyint
    assert cols["source_id"] == "python:host"
    assert cols["received_at"] == now


def test_a_missing_key_or_timestamp_is_rejected():
    now = datetime(2026, 10, 17, 12, 0, 5)
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(key=""), now)
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(ts=None), now)


def test_a_non_numeric_value_is_rejected():
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(value="hot"), datetime.now())


def test_an_overlong_vin_is_rejected_rather_than_truncated():
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(vin="X" * 18), datetime.now())


def test_an_overlong_provenance_is_rejected_rather_than_truncated():
    """`imported: Car Scanner CSV` is 25 characters and the column was 24.

    Because the INSERT is an INSERT IGNORE, MySQL reported the overflow as a
    warning and stored "imported: Car Scanner CS" -- 266 rows lost the last
    character while the ingest reported success. The edge has to refuse it.
    """
    assert len("imported: Car Scanner CSV") == 25
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(provenance="x" * 65), datetime.now())
    # ...and the real label passes, with room to spare.
    row = row_from_payload("s", _payload(provenance="imported: Car Scanner CSV"),
                           datetime.now())
    assert row[12] == "imported: Car Scanner CSV"


def test_an_overlong_decoding_method_is_rejected_rather_than_truncated():
    with pytest.raises(InvalidMeasurement):
        row_from_payload("s", _payload(decoding_method="x" * 121),
                         datetime.now())


def test_value_may_be_absent_for_a_text_decode():
    row = row_from_payload("s", _payload(value=None, text_value='{"a":1}'),
                           datetime.now())
    assert row[9] is None                      # value
    assert row[10] == '{"a":1}'                # text_value


# ------------------------------------------------------------------ columns
def test_the_schema_is_wide_enough_for_every_label_this_project_writes():
    """A label longer than its column fails the INSERT, and one failed row
    fails its whole batch -- so the archive stops until someone notices.

    `imported: Car Scanner CSV` is 25 characters and `provenance` was declared
    VARCHAR(24). The client now moves a text reading to the right column; this
    checks the other half of the same failure: that the column can hold what is
    put in it.
    """
    import re
    from pathlib import Path

    from cloud.db import GROWN_COLUMNS
    from decoders.registry import Provenance
    from tools.import_carscanner import PROVENANCE

    schema = (Path(__file__).resolve().parents[1] / "cloud" / "schema.sql"
              ).read_text(encoding="utf-8")
    declared = {name: int(size) for name, size in
                re.findall(r"(\w+)\s+VARCHAR\((\d+)\)", schema)}

    labels = [p.value for p in Provenance] + [PROVENANCE]
    for label in labels:
        width = declared["provenance"]
        for _table, column, needed, _ in GROWN_COLUMNS:
            if column == "provenance":
                width = max(width, needed)
        assert len(label) <= width, (
            f"provenance {label!r} is {len(label)} characters but the column "
            f"allows {width}")


def test_grown_columns_name_columns_that_exist_in_the_schema():
    """The widening step must not be a typo that silently never matches."""
    from pathlib import Path

    from cloud.db import GROWN_COLUMNS

    schema = (Path(__file__).resolve().parents[1] / "cloud" / "schema.sql"
              ).read_text(encoding="utf-8")
    for table, column, needed, definition in GROWN_COLUMNS:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema, table
        assert column in schema, column
        assert str(needed) in definition, definition


def test_timestamps_are_compared_in_utc_not_local_time():
    # A guard against a regression that stored wall-clock instead of UTC.
    assert parse_ts("2026-10-17T12:00:00+01:00") == \
        parse_ts("2026-10-17T11:00:00Z")
    assert parse_ts("2026-10-17T12:00:00Z") + timedelta(hours=1) == \
        parse_ts("2026-10-17T13:00:00Z")
