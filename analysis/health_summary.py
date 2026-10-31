"""A small, read-only vehicle health summary.

Combines a handful of existing signals (battery capacity trend, fault codes,
latest 12 V reading) into one dict a dashboard or report can render. Every
figure comes from already-stored measurements; nothing new is read from the car.
Each item carries its own status so the caller can tell what is measured from
what is inferred.
"""
from __future__ import annotations

from analysis.health import capacity_trend as _capacity_trend


def _dtc_count(repo, vehicle_id: int) -> int:
    try:
        row = repo.conn.execute(
            "SELECT COUNT(*) FROM diagnostic_events WHERE vehicle_id=? "
            "AND kind='dtc' AND (resolved IS NULL OR resolved=0)",
            (vehicle_id,)).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _latest_12v(repo, vehicle_id: int):
    try:
        r = repo.conn.execute(
            "SELECT value, ts FROM measurements WHERE vehicle_id=? "
            "AND key='control_module_voltage' AND success=1 "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (vehicle_id,)).fetchone()
        if r is None:
            return None
        return {"value": float(r["value"]), "ts": r["ts"]}
    except Exception:
        return None


def health_summary(repo, vehicle_id: int) -> dict:
    ct = _capacity_trend(repo, vehicle_id)
    dtcs = _dtc_count(repo, vehicle_id)
    v12 = _latest_12v(repo, vehicle_id)
    status = "ok"
    if dtcs > 0:
        status = "attention"
    if v12 is not None and v12["value"] < 12.0:
        status = "attention"
    if ct.get("status") == "insufficient":
        status = status if status != "ok" else "unknown"
    return {
        "status": status,
        "dtc_count": dtcs,
        "twelve_volt": v12,
        "capacity_trend": ct,
    }
