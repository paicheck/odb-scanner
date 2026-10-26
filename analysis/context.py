"""Measurement context around an instant: what did the car record near a moment?

WHY THIS EXISTS
---------------
The investigation workbench asks a question the schema makes slightly awkward to
answer: "what did the car report around this timestamp?". A diagnostic event has
an observation timestamp; the measurements live in `measurements` with their own
observation timestamps; and there is no single row that says "these are the
values closest to T".

This module answers it with one bounded query. It deliberately does NOT
interpolate, does NOT fabricate a reading at exactly T, and keeps the real
timestamp and provenance of every value so a stale or imported reading can never
masquerade as a reading taken at the event. The returned `delta_s` is the signed
distance of that value's own observation time from the anchor instant — it is
the alignment information, not a claim that the value existed at the anchor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def measurement_context(repo, vehicle_id: int, center_ts: str, *,
                        window_s: float = 6 * 3600.0,
                        limit: int = 60) -> list[dict]:
    """One row per measurement key with a value recorded within ``window_s`` of
    ``center_ts``, choosing the row whose own observation time is closest to the
    anchor.

    Returns a list sorted by absolute distance from the anchor, oldest side
    first, bounded to ``limit`` rows. Each row carries the real observation
    timestamp, the value (numeric or text), the unit and the provenance, plus
    the signed ``delta_s`` from the anchor. An unparseable anchor returns [].
    """
    center = _parse(center_ts)
    if center is None:
        return []
    lo = _iso(center - timedelta(seconds=window_s))
    hi = _iso(center + timedelta(seconds=window_s))
    rows = repo.conn.execute(
        "SELECT key, value, text_value, unit, provenance, ts FROM measurements "
        "WHERE vehicle_id=? AND success=1 AND ts>=? AND ts<=? ORDER BY ts",
        (vehicle_id, lo, hi),
    ).fetchall()

    best: dict[str, dict] = {}
    for r in rows:
        dt = _parse(r["ts"])
        if dt is None:
            continue
        delta = round((dt - center).total_seconds(), 1)
        key = r["key"]
        if key not in best or abs(delta) < abs(best[key]["delta_s"]):
            best[key] = {
                "key": key,
                "value": r["value"],
                "text_value": r["text_value"],
                "unit": r["unit"],
                "provenance": r["provenance"],
                "ts": r["ts"],
                "delta_s": delta,
            }

    return sorted(best.values(), key=lambda d: abs(d["delta_s"]))[:limit]
