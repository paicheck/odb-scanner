"""Unified event timeline across every source that carries a timestamp.

WHY THIS EXISTS
---------------
A health investigation needs to line a symptom up against everything else that
happened around it: a collection gap, a charging session boundary, a DTC
occurrence, a cell sweep, an operator note. Those live in five tables
(`diagnostic_events`, `dtcs`/`dtc_occurrences`, `charging_sessions`,
`cell_voltages`, `anomalies`) and had no single view.

Two rules, both learned here:

  * the timeline is *observations with timestamps*, not causes. An event that
    happens after a warning is not, by that fact, the warning's cause -- so the
    entries carry no causal ordering, only time;
  * a collected value is never placed on the timeline as if it were an event.
    Cell sweeps appear as "a sweep happened at T", not as their numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# Order used only to break exact timestamp ties, never to imply causation.
_KIND_ORDER = ("dtc", "charge", "collection", "note", "sweep", "anomaly",
               "adapter", "wake", "sleep")


@dataclass(frozen=True)
class TimelineEvent:
    ts: str
    kind: str
    title: str
    detail: str = ""
    ecu: str | None = None
    ref_id: int | None = None
    severity: str = "info"     # info | notice | warning
    simulated: bool = False    # produced while an adapter identified as the simulator

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _is_simulated(text: str | None) -> bool:
    """True when an event description names the vehicle simulator.

    `tools/purge_synthetic_data.py` deliberately keeps `diagnostic_events` of
    kind 'adapter' as the audit trail, and those include the September
    simulator connections. They are real records of a connection that happened,
    but they describe a simulator, so they never belong on a vehicle-health
    timeline by default."""
    if not text:
        return False
    low = text.lower()
    return "simulator" in low or "sim327" in low


def build_timeline(repo, vehicle_id: int, *, since: str | None = None,
                   until: str | None = None,
                   kinds: set[str] | None = None,
                   include_simulated: bool = False) -> list[TimelineEvent]:
    """Merge every timestamped source into one ordered list, oldest first."""
    events: list[TimelineEvent] = []

    def add(e: TimelineEvent):
        if e.simulated and not include_simulated:
            return
        if kinds is not None and e.kind not in kinds:
            return
        if since and (e.ts or "") < since:
            return
        if until and (e.ts or "") > until:
            return
        events.append(e)

    for r in repo.conn.execute(
        "SELECT id, ts, kind, ecu, description, data FROM diagnostic_events "
        "WHERE vehicle_id=? ORDER BY ts", (vehicle_id,),
    ).fetchall():
        kind = r["kind"] or "note"
        add(TimelineEvent(
            ts=r["ts"], kind="note" if kind == "note" else kind,
            title=r["description"] or kind, detail=(r["data"] or ""),
            ecu=r["ecu"], ref_id=r["id"],
            severity="notice" if kind in ("adapter", "wake", "sleep") else "info",
            simulated=_is_simulated(r["description"])))

    # DTC occurrence times are the real evidence; the DTC's first_seen is a
    # summary. Chained lists (one DTC found on a car with many) read as one line
    # per DTC, not per poll -- dtc_occurrences already de-duplicates that.
    for r in repo.conn.execute(
        "SELECT o.id, o.ts, o.freeze_frame, d.ecu, d.code, d.description, "
        "       d.categories FROM dtc_occurrences o "
        "JOIN dtcs d ON d.id=o.dtc_id WHERE d.vehicle_id=? ORDER BY o.ts",
        (vehicle_id,),
    ).fetchall():
        add(TimelineEvent(
            ts=r["ts"], kind="dtc",
            title=f"{r['code']} on {r['ecu']}",
            detail=r["description"] or "", ecu=r["ecu"], ref_id=r["id"],
            severity="warning"))

    for r in repo.conn.execute(
        "SELECT id, started_at, ended_at, charge_type, energy_estimate_kwh, "
        "       max_power_kw, start_soc, end_soc, status "
        "FROM charging_sessions WHERE vehicle_id=? ORDER BY started_at",
        (vehicle_id,),
    ).fetchall():
        detail = (f"{r['charge_type'] or 'charge'} "
                  f"{r['start_soc'] if r['start_soc'] is not None else '?'}"
                  f"→{r['end_soc'] if r['end_soc'] is not None else '?'}%")
        if r["energy_estimate_kwh"] is not None:
            detail += f", {r['energy_estimate_kwh']:.1f} kWh"
        add(TimelineEvent(ts=r["started_at"], kind="charge",
                          title="Charging started", detail=detail,
                          ref_id=r["id"], severity="notice"))
        if r["ended_at"]:
            add(TimelineEvent(ts=r["ended_at"], kind="charge",
                              title="Charging ended", detail=detail,
                              ref_id=r["id"], severity="notice"))

    for r in repo.conn.execute(
        "SELECT ts, COUNT(*) AS n FROM cell_voltages WHERE vehicle_id=? "
        "GROUP BY ts ORDER BY ts", (vehicle_id,),
    ).fetchall():
        add(TimelineEvent(ts=r["ts"], kind="sweep",
                          title=f"Cell sweep ({r['n']} cells)",
                          ecu="bat_mgmt", severity="info"))

    for r in repo.conn.execute(
        "SELECT id, ts, metric, value, zscore, direction, description "
        "FROM anomalies WHERE vehicle_id=? ORDER BY ts", (vehicle_id,),
    ).fetchall():
        z = f"{r['zscore']:.1f}" if r["zscore"] is not None else "?"
        add(TimelineEvent(
            ts=r["ts"], kind="anomaly",
            title=f"{r['metric']} outlier ({r['direction']} z={z})",
            detail=r["description"] or "", ref_id=r["id"], severity="notice"))

    def sort_key(e: TimelineEvent):
        kind_rank = _KIND_ORDER.index(e.kind) if e.kind in _KIND_ORDER else 99
        return (e.ts or "", kind_rank, e.ref_id or 0)

    events.sort(key=sort_key)
    return events


def markers(events: list[TimelineEvent]) -> list[dict]:
    """Compact markers for a chart: timestamp, label, kind, severity only."""
    return [{"ts": e.ts, "label": e.title, "kind": e.kind,
             "severity": e.severity} for e in events]
