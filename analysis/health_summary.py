"""A small, read-only vehicle health summary.

Combines existing signals -- the battery capacity trend, stored fault codes,
the 12 V battery -- into one dict a dashboard or report can render. Every
figure comes from already-stored measurements; nothing is read from the car.

Each item says where it came from and how old it is, so a reader can tell a
current measurement from an old one, and nothing missing is shown as fine:

* Fault codes come from `dtcs`, where the collector stores them with their
  ISO 14229 status (analysis.dtc.status_label). A code lighting the warning
  lamp or failing now needs attention; a pending or confirmed one does if it
  was seen recently; an old stored one is history.
* The 12 V battery is judged only from a resting reading -- the adapter's own
  ATRV while the car is parked (`adapter_12v_voltage`). While the car is awake
  the DC-DC converter holds the rail near 14 V, so a reading then
  (`aux_12v_voltage`) says the system works, not that the battery is healthy.
  A resting reading older than RESTING_12V_MAX_AGE_S is reported but not
  judged.
* Simulated and imported readings are never used: this is about the car.
"""
from __future__ import annotations

from datetime import datetime, timezone

from analysis.health import capacity_trend as _capacity_trend

# A fault seen this recently still matters even if its status says "pending".
RECENT_FAULT_S = 7 * 86400
ATTENTION_STATUSES = ("warning", "active")
RECENT_STATUSES = ("pending", "confirmed")
# Older than this, a resting 12 V reading no longer describes the battery now.
RESTING_12V_MAX_AGE_S = 2 * 86400
LOW_12V_DEFAULT = 12.0      # resting volts: roughly a quarter charged


def _age_s(ts: str | None, now: datetime) -> float | None:
    try:
        then = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (now - then).total_seconds())


def _faults(repo, vehicle_id: int, now: datetime) -> dict:
    rows = [dict(r) for r in repo.dtc_list(vehicle_id)]
    needing = []
    for r in rows:
        age = _age_s(r["last_seen"], now)
        recent = age is not None and age <= RECENT_FAULT_S
        if r["status"] in ATTENTION_STATUSES or (
                r["status"] in RECENT_STATUSES and recent):
            needing.append({"ecu": r["ecu"], "code": r["code"],
                            "status": r["status"], "last_seen": r["last_seen"]})
    return {"stored": len(rows), "needing_attention": needing}


def _reading(rec: dict | None, now: datetime) -> dict | None:
    if not rec or rec.get("value") is None:
        return None
    if str(rec.get("provenance") or "").lower() == "simulated":
        return None
    return {"value": float(rec["value"]), "ts": rec["ts"],
            "age_s": _age_s(rec["ts"], now), "source": "measured"}


def _twelve_volt(latest: dict, now: datetime, low: float) -> dict:
    resting = _reading(latest.get("adapter_12v_voltage"), now)
    awake = _reading(latest.get("aux_12v_voltage"), now)
    out = {"resting": resting, "awake": awake, "low_below": low, "status": "unknown"}
    if resting is None:
        out["note"] = ("No resting reading yet: the collector records one while "
                       "the car is parked and asleep.")
    elif resting["age_s"] is None or resting["age_s"] > RESTING_12V_MAX_AGE_S:
        out["note"] = "The last resting reading is too old to judge the battery now."
    else:
        out["status"] = "low" if resting["value"] < low else "ok"
    return out


def health_summary(repo, vehicle_id: int, low_12v: float = LOW_12V_DEFAULT,
                   now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    trend = _capacity_trend(repo, vehicle_id)
    faults = _faults(repo, vehicle_id, now)
    twelve = _twelve_volt(repo.latest_measurements(vehicle_id, live_only=True),
                          now, low_12v)
    reasons = []
    if faults["needing_attention"]:
        reasons.append(f"{len(faults['needing_attention'])} fault code(s) active, "
                       "lighting the warning lamp, or seen in the last 7 days")
    if twelve["status"] == "low":
        reasons.append(f"12 V battery {twelve['resting']['value']:.2f} V at rest")
    if reasons:
        status = "attention"
    elif twelve["status"] == "unknown" or trend.get("status") != "ok":
        # Nothing wrong found, but not everything could be checked: say so
        # rather than "ok".
        status = "incomplete"
    else:
        status = "ok"
    return {
        "status": status,
        "reasons": reasons,
        "faults": faults,
        "twelve_volt": twelve,
        "capacity_trend": trend,
    }
