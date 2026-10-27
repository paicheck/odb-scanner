"""Where the car is, as far as its own GPS module has said.

The position comes from DID 0x2430 on the GPS module (decoders/meb.py
_gps_position), stored as JSON in text_value. "Current" here always means
"the newest fix recorded", which is as old as the last collection session --
so the age travels with it, and nothing is extrapolated between fixes.

Only the car's own readings: imported and simulated rows never place the car.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from database.repository import IMPORTED_PREFIX

TRAIL_LIMIT = 500


def _fix(row) -> dict | None:
    try:
        data = json.loads(row["text_value"])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    lat, lon = data.get("lat"), data.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return {"ts": row["ts"], "lat": float(lat), "lon": float(lon),
            "altitude_m": data.get("altitude_m")}


def positions(repo, vehicle_id: int, days: int) -> dict:
    """The newest fix (any age) and the trail of fixes in the last `days`."""
    live = "AND provenance NOT LIKE ? AND provenance <> 'simulated'"
    q = repo.conn.execute
    newest = q(
        f"SELECT ts, text_value FROM measurements WHERE vehicle_id=? "
        f"AND key='gps_position' AND success=1 AND text_value IS NOT NULL "
        f"{live} ORDER BY ts DESC LIMIT 20",
        (vehicle_id, IMPORTED_PREFIX + "%")).fetchall()
    latest = next((f for f in map(_fix, newest) if f), None)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    rows = q(
        f"SELECT ts, text_value FROM measurements WHERE vehicle_id=? "
        f"AND key='gps_position' AND success=1 AND text_value IS NOT NULL "
        f"AND ts>=? {live} ORDER BY ts DESC LIMIT ?",
        (vehicle_id, since, IMPORTED_PREFIX + "%", TRAIL_LIMIT)).fetchall()
    trail = [f for f in map(_fix, reversed(rows)) if f]

    sats = q(
        f"SELECT ts, value FROM measurements WHERE vehicle_id=? "
        f"AND key='number_of_tracked_satellites' AND success=1 "
        f"AND value IS NOT NULL {live} ORDER BY ts DESC LIMIT 1",
        (vehicle_id, IMPORTED_PREFIX + "%")).fetchone()
    attempts = q(
        "SELECT COUNT(*) AS n, SUM(success) AS ok FROM measurements "
        "WHERE vehicle_id=? AND key='gps_position'", (vehicle_id,)).fetchone()

    return {
        "latest": latest,
        "trail": trail,
        "satellites": int(sats["value"]) if sats else None,
        "satellites_ts": sats["ts"] if sats else None,
        "reads": attempts["n"] or 0,
        "reads_ok": attempts["ok"] or 0,
        "days": days,
    }
