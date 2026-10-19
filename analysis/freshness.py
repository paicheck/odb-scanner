"""Freshness and observation state: was this value actually seen, and when?

WHY THIS EXISTS
---------------
Two failures observed in this project made "the latest row is the current
value" untenable:

  1. The collector *carries values forward* into a battery snapshot for up to
     ``collector.max_value_age_s`` (900 s). A snapshot row can therefore mix a
     value read one second ago with another read fifteen minutes ago, and
     nothing in the row says which is which.
  2. ``chg`` (on-board charger) last answered on 2026-09-20 and has been silent
     since; ``gps`` likewise. An empty charger panel and a charger panel of
     old-but-real readings are different facts, and neither is "current".

So everything that reads a value must ask this module first, and get back an
explicit state plus the timestamp the value was *observed at* — never "now".

The states are mutually exclusive and chosen in this order:

    unknown            the timestamp cannot be parsed; treat as not fresh
    missing            no successful read has ever been recorded for this key
    collector-failure  the collector has tried recently and every attempt failed
    carried-forward    the fresh value is not from the newest read; it came
                       from an earlier row inside the carry-forward window
    repeated           the newest read is recent, but the same value (and raw
                       bytes) has answered unchanged for longer than one poll
                       cycle -- information, not a fault
    stale              a real read, but older than the fresh window
    fresh              a real read within the fresh window

Nothing here interpolates, fills, or re-stamps a value. A gap stays a gap; a
carried-forward value is labelled as such and its original timestamp is
returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

# Imported rows are evidence about the car, not a reading of it by this tool, so
# they are never called fresh (see analysis/provenance.py for the same rule).
IMPORTED_PREFIX = "imported"
SIMULATED_PROVENANCE = "simulated"

# Fresh window. Mirrors collector.max_value_age_s: a value older than the
# carry-forward window cannot be called current.
DEFAULT_FRESH_AFTER_S = 900.0

# A module that has answered before but not for this long is *silent*, not
# merely idle. Six hours sits well above the slow poll interval (60 s) and the
# carry-forward window, so a normal parked car between drives is not flagged.
DEFAULT_SILENT_AFTER_S = 6 * 3600.0

# How many recent rows to inspect when looking for a repeated run.
_LOOKBACK = 120

# A value must answer identically across this many consecutive successful rows,
# and over this many seconds, before it is called `repeated`.
_REPEAT_MIN_ROWS = 5


class ObservationState(str, Enum):
    FRESH = "fresh"
    REPEATED = "repeated"
    CARRIED_FORWARD = "carried-forward"
    STALE = "stale"
    MISSING = "missing"
    COLLECTOR_FAILURE = "collector-failure"
    UNKNOWN = "unknown"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_seconds(ts: str | None, now: datetime | None = None) -> float | None:
    """Age in seconds, or None when the stamp cannot be read. An unreadable
    stamp is not fresh (the same rule as analysis/provenance.py)."""
    parsed = _parse(ts)
    if parsed is None:
        return None
    return (( now or datetime.now(tz=timezone.utc)) - parsed).total_seconds()


@dataclass(frozen=True)
class Observation:
    """The verdict on one measurement: what was seen, when, and how sure we are
    that it is current."""

    key: str
    state: ObservationState
    value: object = None
    text_value: object = None
    ts: str | None = None
    age_s: float | None = None
    reason: str = ""
    sample_count: int = 0
    last_attempt_ts: str | None = None

    @property
    def fresh(self) -> bool:
        """True only for a genuine, recent read. Everything else is not
        evidence that the quantity is at its current value."""
        return self.state in (ObservationState.FRESH,
                              ObservationState.REPEATED)

    @property
    def observed(self) -> bool:
        """True when *some* real read exists (fresh or not) — the value is a
        fact about the past even if it is not current."""
        return self.state not in (ObservationState.MISSING,
                                  ObservationState.COLLECTOR_FAILURE)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "state": self.state.value,
            "value": self.value,
            "text_value": self.text_value,
            "ts": self.ts,
            "age_s": self.age_s,
            "fresh": self.fresh,
            "reason": self.reason,
            "sample_count": self.sample_count,
            "last_attempt_ts": self.last_attempt_ts,
        }


def _classify_rows(rows: list[dict], key: str, now: datetime,
                   fresh_after_s: float) -> Observation:
    """Classify from a list of rows newest-first.

    Each row needs at least ``ts``, ``success``; optional ``value``,
    ``text_value``, ``raw_response``, ``provenance``.
    """
    if not rows:
        return Observation(key, ObservationState.MISSING,
                           reason="no rows recorded for this key")
    newest = rows[0]
    last_attempt = newest.get("ts")
    successes = [r for r in rows if _as_bool(r.get("success", 1))]
    if not successes:
        return Observation(
            key, ObservationState.COLLECTOR_FAILURE, ts=None,
            last_attempt_ts=last_attempt, sample_count=0,
            reason=f"{len(rows)} recent attempts, none succeeded "
                   f"(latest error: {newest.get('error') or 'unknown'})")
    top = successes[0]
    age = age_seconds(top.get("ts"), now)
    if age is None:
        return Observation(key, ObservationState.UNKNOWN, ts=top.get("ts"),
                           last_attempt_ts=last_attempt,
                           sample_count=len(successes),
                           reason="newest success has an unreadable timestamp")
    value = top.get("value")
    text_value = top.get("text_value")
    provenance = str(top.get("provenance") or "")
    if provenance.lower().startswith(IMPORTED_PREFIX):
        return Observation(key, ObservationState.STALE, value=value,
                           text_value=text_value, ts=top.get("ts"), age_s=age,
                           last_attempt_ts=last_attempt,
                           sample_count=len(successes),
                           reason="imported evidence, never a live reading")
    if provenance.lower() == SIMULATED_PROVENANCE:
        return Observation(key, ObservationState.UNKNOWN, value=value,
                           ts=top.get("ts"), age_s=age,
                           last_attempt_ts=last_attempt,
                           sample_count=len(successes),
                           reason="simulated; describes no car")

    if age > fresh_after_s:
        return Observation(key, ObservationState.STALE, value=value,
                           text_value=text_value, ts=top.get("ts"), age_s=age,
                           last_attempt_ts=last_attempt,
                           sample_count=len(successes),
                           reason=f"last read {age:.0f}s ago, older than "
                                  f"{fresh_after_s:.0f}s")

    # A repeated run: identical value AND identical raw bytes over enough rows
    # and enough time. Raw bytes matter -- the same number can legitimately
    # recur, but the same bytes over and over is a frozen or carried value.
    run = 1
    for prev in successes[1:]:
        if prev.get("value") != value:
            break
        if prev.get("raw_response") != top.get("raw_response"):
            break
        run += 1
    oldest_in_run = successes[min(run, len(successes)) - 1]
    span = None
    start = _parse(oldest_in_run.get("ts"))
    end = _parse(top.get("ts"))
    if start and end:
        span = (end - start).total_seconds()
    if run >= _REPEAT_MIN_ROWS and span is not None and span >= fresh_after_s:
        return Observation(
            key, ObservationState.REPEATED, value=value, text_value=text_value,
            ts=top.get("ts"), age_s=age, last_attempt_ts=last_attempt,
            sample_count=len(successes),
            reason=f"same value and raw bytes across {run} reads over "
                   f"{span:.0f}s")
    return Observation(key, ObservationState.FRESH, value=value,
                       text_value=text_value, ts=top.get("ts"), age_s=age,
                       last_attempt_ts=last_attempt, sample_count=len(successes),
                       reason="")


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v not in ("0", "", "false", "False")
    return bool(v)


def _recent_rows(repo, vehicle_id: int, key: str, limit: int = _LOOKBACK):
    """Newest-first rows for one key, including failures and raw bytes."""
    return [dict(r) for r in repo.conn.execute(
        "SELECT ts, value, text_value, success, raw_response, provenance, error "
        "FROM measurements WHERE vehicle_id=? AND key=? "
        "ORDER BY ts DESC, id DESC LIMIT ?",
        (vehicle_id, key, limit),
    ).fetchall()]


def observe_key(repo, vehicle_id: int, key: str, *, now: datetime | None = None,
                fresh_after_s: float = DEFAULT_FRESH_AFTER_S,
                rows: list[dict] | None = None) -> Observation:
    """Freshness verdict for one measurement key."""
    reference = now or datetime.now(tz=timezone.utc)
    if rows is None:
        rows = _recent_rows(repo, vehicle_id, key)
    return _classify_rows(rows, key, reference, fresh_after_s)


def observe_many(repo, vehicle_id: int, keys: list[str], *,
                 now: datetime | None = None,
                 fresh_after_s: float = DEFAULT_FRESH_AFTER_S) -> dict[str, Observation]:
    """Freshness for several keys with a single query per key.

    Deliberately not one giant query: a per-key LIMIT keeps the read bounded
    however many keys are asked for, and keys are few in practice (the caller
    passes the ones on screen).
    """
    reference = now or datetime.now(tz=timezone.utc)
    return {k: observe_key(repo, vehicle_id, k, now=reference,
                           fresh_after_s=fresh_after_s) for k in keys}


# ---------------------------------------------------------------------------
# Module silence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModuleStatus:
    ecu: str
    status: str            # active | idle | silent | failed | imported-only | no-readings
    last_success_ts: str | None
    last_attempt_ts: str | None
    age_s: float | None
    successes: int
    failures: int
    note: str = ""
    live_success_ts: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def module_status(repo, vehicle_id: int, *, now: datetime | None = None,
                  silent_after_s: float = DEFAULT_SILENT_AFTER_S,
                  window_days: int = 30) -> list[ModuleStatus]:
    """Per-ECU answer health: silent, idle, failing, or simply unused?

    The distinction that matters — and that the app used to hide — is between a
    module that stopped answering while the *rest of the car kept answering*
    (`silent`) and one that is quiet only because the whole car is quiet
    (`idle`). Judging a module against wall-clock time alone would call every
    module silent overnight while the car is parked, which is exactly the
    confusion between a silent module and a normal reading the brief warns
    about.

    So a module is `silent` when its last *live* reading is more than
    `silent_after_s` older than the newest live reading anywhere on the
    vehicle. Modules whose only successes are imported Car Scanner evidence are
    `imported-only`, never silent: they never answer live by definition, so
    silence says nothing about them. `no-readings` means no success at all;
    `failed` means it is answering now but mostly with errors.
    """
    reference = now or datetime.now(tz=timezone.utc)
    rows = repo.conn.execute(
        "SELECT ecu, "
        "       MAX(CASE WHEN success=1 THEN ts END) AS last_success, "
        "       MAX(CASE WHEN success=1 AND provenance NOT LIKE 'imported%' "
        "                THEN ts END) AS live_success, "
        "       MAX(ts) AS last_attempt, "
        "       SUM(success) AS ok, SUM(1-success) AS bad, "
        "       SUM(CASE WHEN success=1 AND provenance LIKE 'imported%' "
        "                THEN 1 ELSE 0 END) AS imported_ok "
        "FROM measurements WHERE vehicle_id=? GROUP BY ecu",
        (vehicle_id,),
    ).fetchall()
    live_last = max((r["live_success"] for r in rows if r["live_success"]),
                    default=None)
    # How long ago the whole vehicle (not this module) last produced a live
    # reading.
    quiet_for = age_seconds(live_last, reference)
    out: list[ModuleStatus] = []
    for r in rows:
        last_success = r["last_success"]
        live_success = r["live_success"]
        last_attempt = r["last_attempt"]
        ok = int(r["ok"] or 0)
        bad = int(r["bad"] or 0)
        imported_ok = int(r["imported_ok"] or 0)
        age = age_seconds(live_success, reference)
        if ok == 0:
            status = "no-readings"
            note = (f"module has never produced a successful read "
                    f"({bad} attempts)" if bad else
                    "module has never produced a successful read")
        elif live_success is None:
            status = "imported-only"
            note = (f"{imported_ok} imported Car Scanner rows, no live read; "
                    f"this tool has never read the module itself")
        elif age is not None and age > silent_after_s:
            behind = (age - quiet_for) if quiet_for is not None else age
            if behind > silent_after_s:
                status = "silent"
                note = (f"last live answer {age / 3600.0:.1f} h ago, while "
                        f"other modules answered {quiet_for / 3600.0:.1f} h "
                        f"ago; a silent module is not one reporting zero")
            else:
                status = "idle"
                note = (f"no live reading for {age / 3600.0:.1f} h, but the "
                        f"whole vehicle has been quiet that long too")
        elif bad > ok:
            status = "failed"
            note = "more failed than successful reads recently"
        else:
            status = "active"
            note = ""
        out.append(ModuleStatus(
            ecu=r["ecu"] or "?", status=status,
            last_success_ts=last_success, last_attempt_ts=last_attempt,
            age_s=age, successes=ok, failures=bad, note=note,
            live_success_ts=live_success))
    out.sort(key=lambda s: (s.status not in ("silent", "idle"), s.ecu))
    return out


def silence_summary(statuses: list[ModuleStatus]) -> dict:
    out = {"active": 0, "idle": 0, "silent": 0, "failed": 0,
           "imported-only": 0, "no-readings": 0, "silent_modules": []}
    for s in statuses:
        out[s.status] = out.get(s.status, 0) + 1
        if s.status == "silent":
            out["silent_modules"].append(s.ecu)
    return out
