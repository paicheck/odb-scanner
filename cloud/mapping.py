"""Pure mapping/validation between client payloads and cloud rows.

Kept free of any database or HTTP import so it can be unit-tested (and reused
by both writers) without a MySQL server or a network. The API calls these to
turn a validated request into the exact tuples `cloud/db.py` inserts; the
Python collector calls `cloud_measurement_id` to derive the same stable id for
every retry of the same local row.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# A fixed namespace for deterministic measurement ids. It MUST NOT change:
# it is what makes "the same local row" map to "the same cloud id" across runs,
# so changing it would make every already-uploaded row look brand new on the
# next sync and double the archive. Generated once and frozen here.
CLOUD_ID_NAMESPACE = uuid.UUID("d9a2f0c4-6b1e-4f37-9c58-2a7d5e10b3aa")

# Column order the INSERT in cloud/db.py is built from. One list feeds both the
# column names and the placeholder count, so the two cannot drift apart.
MEASUREMENT_COLUMNS = (
    "id", "source_id", "vin", "ts", "ts_raw", "ecu", "service", "pid", "key",
    "value", "text_value", "unit", "provenance", "decoding_method",
    "doc_status", "raw_response", "success", "error", "received_at",
)

# ISO-8601 with an optional fractional part of ANY length (Android's
# Instant.toString() emits 9 digits, which datetime.fromisoformat cannot read on
# Python 3.10) and an optional 'Z' or numeric offset. A missing offset is read
# as UTC, matching every writer in this project.
_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?(?P<tz>Z|z|[+-]\d{2}:?\d{2})?$"
)


class InvalidMeasurement(ValueError):
    """A payload field is not usable; the request is rejected, not guessed at."""


def cloud_measurement_id(source_id: str, local_id: int) -> str:
    """Derive a stable cloud UUID for a local SQLite measurement row.

    Deterministic (uuid5) rather than random precisely because it is derived
    at UPLOAD time, on every attempt: a random id generated per attempt would
    make every retry after a dropped response a new row, which is the one thing
    idempotency exists to prevent. The local `id` is monotonic and unique
    within a database, so the pair (source_id, id) is unique across sources.
    """
    return str(uuid.uuid5(CLOUD_ID_NAMESPACE, f"{source_id}:measurement:{local_id}"))


def canonical_uuid(text: str) -> str:
    """Canonical lowercase form of a client-supplied id, or raise.

    The column is CHAR(36); a non-UUID id would be silently truncated by MySQL
    and then fail to match on the next retry. Rejecting it here turns a
    confusing duplicate-row bug into a clear 422 at the edge.
    """
    try:
        return str(uuid.UUID(str(text).strip()))
    except (AttributeError, ValueError) as exc:
        raise InvalidMeasurement(f"id {text!r} is not a UUID") from exc


def parse_ts(raw: str) -> datetime:
    """Parse a client timestamp to a naive UTC datetime for DATETIME(3).

    MySQL DATETIME has no zone, so the value stored is UTC and the original
    string is kept beside it in `ts_raw`. Anything unparseable raises rather
    than defaulting to now(): a wrong timestamp is worse than a rejected row,
    because it silently corrupts every trend that reads it.
    """
    text = str(raw or "").strip()
    m = _TS_RE.match(text)
    if not m:
        raise InvalidMeasurement(
            f"timestamp {raw!r} is not ISO-8601 (expected e.g. "
            "2026-10-17T12:00:00Z or 2026-10-17T12:00:00+00:00)")

    frac = (m.group("frac") or "")[:6]           # truncate nanoseconds to micro
    stamp = f"{m.group('date')}T{m.group('time')}"
    if frac:
        stamp += "." + frac
    stamp += _normalise_tz(m.group("tz"))
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError as exc:                     # pragma: no cover - regex guards it
        raise InvalidMeasurement(f"timestamp {raw!r} is not a valid date") from exc
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _normalise_tz(tz: str | None) -> str:
    if not tz:
        return "+00:00"
    if tz in ("Z", "z"):
        return "+00:00"
    if len(tz) == 5:                              # +HHMM -> +HH:MM
        return f"{tz[:3]}:{tz[3:]}"
    return tz


def _text_or_none(value, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if limit is not None and len(text) > limit:
        raise InvalidMeasurement(
            f"value is {len(text)} characters, longer than the {limit} the "
            "column allows")
    return text


def row_from_payload(source_id: str, payload: dict, received_at: datetime) -> tuple:
    """Turn one validated request measurement into a DB column tuple.

    ``payload`` may be a plain dict (from JSON) so this does not depend on the
    API's Pydantic model -- the collector's own tests use dicts directly.
    """
    key = _text_or_none(payload.get("key"))
    if not key:
        raise InvalidMeasurement("measurement is missing 'key'")

    raw_ts = _text_or_none(payload.get("ts"))
    if not raw_ts:
        raise InvalidMeasurement(f"measurement {key!r} is missing 'ts'")

    raw_response = _text_or_none(payload.get("raw_response"))
    text_value = _text_or_none(payload.get("text_value"))

    return (
        canonical_uuid(payload.get("id")),
        source_id,
        _text_or_none(payload.get("vin"), 17),
        parse_ts(raw_ts),
        raw_ts[:32],
        _text_or_none(payload.get("ecu"), 40),
        _text_or_none(payload.get("service"), 40),
        _text_or_none(payload.get("pid"), 40),
        _text_or_none(key, 80),
        _optional_float(payload.get("value")),
        text_value,
        _text_or_none(payload.get("unit"), 24),
        _text_or_none(payload.get("provenance")) or "reported",
        _text_or_none(payload.get("decoding_method")) or "unknown",
        _text_or_none(payload.get("doc_status"), 160),
        raw_response,
        1 if _truthy(payload.get("success", True)) else 0,
        _text_or_none(payload.get("error")),
        received_at,
    )


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidMeasurement(f"value {value!r} is not a number") from exc


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
