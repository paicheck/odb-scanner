"""Searchable measurement discovery across the whole catalogue.

WHY THIS EXISTS
---------------
The registry carries hundreds of keys. `/measurements` groups them by module
but offers no search, no filter by trust, and no way to ask the two questions a
real investigation starts with:

    * which keys does this car answer at all? (freshness)
    * which of them may I believe? (quality)

This module joins the three things that were previously in three places --
the decoder catalogue, the quality registry, and the observation state -- into
one row per key, then filters it. It is deliberately pure: `build_entries`
does the one database read; `search_entries` is a plain list filter, so the
dashboard and the tests reason about exactly the same code.

Freshness here is derived from a single aggregate query per key rather than the
120-row lookback in `analysis/freshness.py`. That lookback is what proves a
value is *repeated*; a discovery list does not need that proof and paying for
it hundreds of times over would make the page slow. The full verdict is one
click away and uses the rigorous path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from analysis.freshness import (
    DEFAULT_FRESH_AFTER_S,
    ObservationState,
    age_seconds,
)
from decoders.quality import (
    DataKind,
    Verification,
    quality_registry,
)

_GROUP_PREFIXES = (("cell_v_", "cell_voltages"),
                   ("cell_t_", "cell_temperatures"))

_GROUP_NAMES = {
    "cell_voltages": "Cell voltages (all cells)",
    "cell_temperatures": "Cell temperatures (all sensors)",
}


def group_for(key: str) -> tuple[str, bool]:
    for prefix, group in _GROUP_PREFIXES:
        if key.startswith(prefix):
            return group, True
    return key, False


@dataclass
class KeyEntry:
    key: str
    name: str
    unit: str
    ecu_key: str
    ecu_name: str
    verification: Verification
    data_kind: DataKind
    use_in_scoring: bool
    known_issue: str
    state: ObservationState
    last_success_ts: str | None
    age_s: float | None
    successes: int
    failures: int
    rollup: bool = False
    members: int = 0
    plausible_min: float | None = None
    plausible_max: float | None = None
    search_text: str = field(default="", repr=False)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "ecu_key": self.ecu_key,
            "ecu_name": self.ecu_name,
            "verification": self.verification.value,
            "data_kind": self.data_kind.value,
            "use_in_scoring": self.use_in_scoring,
            "known_issue": self.known_issue,
            "state": self.state.value,
            "last_success_ts": self.last_success_ts,
            "age_s": self.age_s,
            "successes": self.successes,
            "failures": self.failures,
            "rollup": self.rollup,
            "members": self.members,
            "plausible_min": self.plausible_min,
            "plausible_max": self.plausible_max,
        }


def _aggregate(repo, vehicle_id: int) -> dict[str, dict]:
    """One row per key: success/failure counts and the last success stamp."""
    rows = repo.conn.execute(
        "SELECT key, SUM(success) AS ok, SUM(1-success) AS bad, "
        "       MAX(CASE WHEN success=1 THEN ts END) AS last_success "
        "FROM measurements WHERE vehicle_id=? GROUP BY key",
        (vehicle_id,),
    ).fetchall()
    return {r["key"]: {"ok": int(r["ok"] or 0), "bad": int(r["bad"] or 0),
                       "last_success": r["last_success"]} for r in rows}


def _state(agg: dict | None, now: datetime,
           fresh_after_s: float) -> tuple[ObservationState, float | None]:
    if not agg or agg["ok"] == 0:
        if agg and agg["bad"] > 0:
            return ObservationState.COLLECTOR_FAILURE, None
        return ObservationState.MISSING, None
    age = age_seconds(agg["last_success"], now)
    if age is None:
        return ObservationState.UNKNOWN, None
    if age > fresh_after_s:
        return ObservationState.STALE, age
    return ObservationState.FRESH, age


def build_entries(repo, vehicle_id: int, profile: str = "meb", *,
                  now: datetime | None = None,
                  fresh_after_s: float = DEFAULT_FRESH_AFTER_S) -> list[KeyEntry]:
    """One searchable entry per catalogue key (per-cell keys rolled up)."""
    reference = now or datetime.now(tz=timezone.utc)
    quality = quality_registry(profile)
    agg = _aggregate(repo, vehicle_id) if vehicle_id else {}
    from diagnostic.ecus import ECUS

    entries: list[KeyEntry] = []
    rollups: dict[str, KeyEntry] = {}
    for key, q in quality.items():
        display, is_rollup = group_for(key)
        a = agg.get(key)
        state, age = _state(a, reference, fresh_after_s)
        name = q.extras.get("name") or key
        entry = KeyEntry(
            key=display if is_rollup else key,
            name=_GROUP_NAMES.get(display, name) if is_rollup else name,
            unit=q.unit, ecu_key="", ecu_name="",
            verification=q.verification,
            data_kind=q.data_kind, use_in_scoring=q.use_in_scoring,
            known_issue=q.known_issue, state=state,
            last_success_ts=(a or {}).get("last_success"),
            age_s=age, successes=(a or {}).get("ok", 0),
            failures=(a or {}).get("bad", 0),
            rollup=is_rollup, members=1 if is_rollup else 0,
            plausible_min=q.plausible_min, plausible_max=q.plausible_max,
            search_text=f"{key} {display} {name} {q.unit}".lower(),
        )
        if is_rollup:
            prev = rollups.get(display)
            if prev is None:
                rollups[display] = entry
            else:
                # Roll up: any member fresh makes the group fresh; sum counts.
                prev.successes += entry.successes
                prev.failures += entry.failures
                prev.members += 1
                if (prev.last_success_ts or "") < (entry.last_success_ts or ""):
                    prev.last_success_ts = entry.last_success_ts
                    prev.age_s = entry.age_s
                    prev.state = entry.state
                if entry.verification is Verification.INVALID:
                    prev.verification = Verification.INVALID
                    prev.known_issue = prev.known_issue or entry.known_issue
                    prev.use_in_scoring = False
        else:
            entries.append(entry)

    # ECU attribution comes from the catalogue, which is not part of quality.
    from decoders.catalogue import build_catalogue
    cat = build_catalogue(profile)
    for entry in entries + list(rollups.values()):
        if entry.rollup:
            entry.ecu_key = "bat_mgmt"
            entry.ecu_name = ECUS["bat_mgmt"].name if "bat_mgmt" in ECUS else "BMS"
            continue
        d = cat.get(entry.key)
        if d is not None:
            entry.ecu_key = d.ecu_key or ""
            entry.ecu_name = d.ecu_name or entry.ecu_key

    entries.extend(rollups.values())
    entries.sort(key=lambda e: e.key)
    return entries


def search_entries(entries: list[KeyEntry], *,
                   query: str = "", verification: str | None = None,
                   data_kind: str | None = None, scoring: bool | None = None,
                   state: str | None = None, ecu: str | None = None,
                   limit: int | None = None) -> list[KeyEntry]:
    """Filter entries. `query` matches key, display key, name or unit as a
    case-insensitive substring. Empty filters match everything."""
    needle = (query or "").strip().lower()
    out: list[KeyEntry] = []
    for e in entries:
        if needle and needle not in e.search_text:
            continue
        if verification and e.verification.value != verification:
            continue
        if data_kind and e.data_kind.value != data_kind:
            continue
        if scoring is not None and e.use_in_scoring != scoring:
            continue
        if state and e.state.value != state:
            continue
        if ecu and e.ecu_key != ecu:
            continue
        out.append(e)
        if limit is not None and len(out) >= limit:
            break
    return out


def facets(entries: list[KeyEntry]) -> dict:
    """Filter values with their counts, for the discovery UI."""
    out = {"verification": {}, "data_kind": {}, "state": {}, "ecu": {},
           "scoring": {"usable": 0, "excluded": 0}, "total": len(entries)}

    def bump(bucket: str, value: str):
        out[bucket][value] = out[bucket].get(value, 0) + 1

    for e in entries:
        bump("verification", e.verification.value)
        bump("data_kind", e.data_kind.value)
        bump("state", e.state.value)
        bump("ecu", e.ecu_key or "?")
        out["scoring"]["usable" if e.use_in_scoring else "excluded"] += 1
    return out
