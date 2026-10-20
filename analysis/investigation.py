"""Evidence-graded fault investigation.

WHY THIS EXISTS
---------------
The brief's sharpest analytical requirement is one this project kept getting
wrong in prose: an observation, an association, an explanation and a confirmed
diagnosis are four different things, and "changed after the warning" is not
"caused the warning". A fault hunt that climbs straight from "the electric-drive
lamp came on" to "the inverter is failing" is guessing with extra steps.

So an investigation here is a small, persisted argument, and each claim has to
declare which of four stages it belongs to:

    observation    a value/fact read from the car, with its own timestamp and
                   freshness (never "now")
    association    something else that happened in the same window. Not a cause;
                   recorded so a human can weigh it, with an explicit note that
                   temporal order is not causation
    explanation    a mechanism that would connect them, stated as a hypothesis
                   with what would falsify it, and whether the supporting values
                   were freshly observed or only historical
    diagnosis      only reachable when the written criteria are met by evidence
                   already recorded -- otherwise the investigation stays
                   `insufficient-evidence` and the brief requires us to say so

Everything is anchored on real measurements and events. Known-invalid
measurements (`decoders/quality.py`) are still shown because they are part of
the story, but they are marked and can never be the basis of a diagnosis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from analysis import baseline as baseline_mod
from analysis import freshness
from analysis.timeline import build_timeline
from decoders.quality import quality_for

STAGES = ("observation", "association", "explanation", "diagnosis")

# An association is only meaningful when its value was actually observed close
# to the symptom. Beyond this the two are independent facts.
DEFAULT_ASSOCIATION_WINDOW_S = 6 * 3600.0

# Below this many pieces of evidence a diagnosis is refused outright, whatever
# the criteria say: three data points are not a pattern.
MIN_EVIDENCE_FOR_DIAGNOSIS = 3


@dataclass
class Evidence:
    stage: str
    kind: str
    observation: str
    ts: str
    ref: str = ""
    detail: str = ""
    certain: bool = True     # False when the value was not freshly observed

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Investigation:
    id: int | None
    title: str
    symptom: str
    anchor_key: str | None
    anchor_ecu: str | None
    criteria: str
    status: str = "open"
    opened_at: str = ""
    updated_at: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    summary: str = ""
    conclusion: str = ""

    def count(self, stage: str) -> int:
        return sum(1 for e in self.evidence if e.stage == stage)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Building evidence
# ---------------------------------------------------------------------------

def _persist(repo, inv_id: int | None, ev: Evidence) -> Evidence:
    if inv_id is not None:
        repo.add_investigation_evidence(
            inv_id, ev.stage, ev.kind, ev.observation, ref=ev.ref,
            detail=ev.detail, ts=ev.ts, certain=ev.certain)
    return ev


def observe_key(repo, vehicle_id: int, key: str, profile: str = "meb", *,
                inv_id: int | None = None, now: datetime | None = None,
                fresh_after_s: float = freshness.DEFAULT_FRESH_AFTER_S
                ) -> Evidence:
    """Stage 1: record what the car actually reported for `key`."""
    obs = freshness.observe_key(repo, vehicle_id, key, now=now,
                                fresh_after_s=fresh_after_s)
    q = quality_for(profile, key)
    bits = [f"{key} = {obs.value if obs.value is not None else obs.text_value}",
            f"state={obs.state.value}"]
    if obs.ts:
        bits.append(f"observed_at={obs.ts}")
    if obs.age_s is not None:
        bits.append(f"age={obs.age_s:.0f}s")
    detail = "; ".join(bits)
    if not q.use_in_scoring:
        detail += f" [EXCLUDED from scoring: {q.known_issue}]"
    elif q.verification.value != "verified":
        detail += f" [decoder {q.verification.value}]"
    certain = obs.fresh and q.use_in_scoring
    return _persist(repo, inv_id, Evidence(
        stage="observation", kind="measurement",
        observation=f"{q.key} observed as {obs.state.value}",
        ts=obs.ts or _iso(now or datetime.now(timezone.utc)),
        ref=f"{key}@{obs.ts}" if obs.ts else key,
        detail=detail + ("" if certain else " [relationship uncertain: value "
                         "was not freshly observed or is not scoreable]"),
        certain=certain))


def associate_events(repo, vehicle_id: int, anchor_ts: str, *,
                     inv_id: int | None = None,
                     window_s: float = DEFAULT_ASSOCIATION_WINDOW_S,
                     kinds: set[str] | None = None) -> list[Evidence]:
    """Stage 2: things that happened within `window_s` of the symptom.

    Each is recorded with the explicit warning that temporal proximity is not
    causation, and with how far away in time it was so a reader can judge.
    """
    anchor = _parse(anchor_ts)
    if anchor is None:
        return []
    lo = _iso(anchor - timedelta(seconds=window_s))
    hi = _iso(anchor + timedelta(seconds=window_s))
    events = build_timeline(repo, vehicle_id, since=lo, until=hi, kinds=kinds)
    out: list[Evidence] = []
    for e in events:
        delta = None
        et = _parse(e.ts)
        if et is not None:
            delta = (et - anchor).total_seconds()
        if delta is None:
            timing = "at the same time"
        elif delta < 0:
            timing = f"{-delta:.0f} s before"
        else:
            timing = f"{delta:.0f} s after"
        out.append(_persist(repo, inv_id, Evidence(
            stage="association", kind=e.kind,
            observation=f"{e.title} at {e.ts}",
            ts=e.ts, ref=f"event:{e.ref_id}" if e.ref_id else e.kind,
            detail=f"{timing} the symptom. Temporal order is not causation.",
            certain=False)))
    return out


def explain_with_baseline(repo, vehicle_id: int, key: str, value: float, *,
                          inv_id: int | None = None,
                          window_days: float = 30.0,
                          ts: str | None = None) -> Evidence:
    """Stage 3: is this value unusual *for this car*, and by how much?

    Uses the robust, gap-aware baseline and records the sample size, so a claim
    resting on a handful of samples says so.
    """
    rows = repo.measurement_series(vehicle_id, key, int(window_days))
    ts_list = [r["ts"] for r in rows]
    values = [r["value"] for r in rows]
    base, findings = baseline_mod.detect_outliers(ts_list, values)
    z = base.robust_z(value)
    stage = "explanation"
    if base.n < 8:
        detail = (f"only {base.n} samples in {window_days:.0f} d; too few to "
                  f"establish what is normal. This is a hypothesis, not a "
                  f"finding.")
    else:
        spread = (f"median {base.median:.4g}, MAD {base.mad:.4g}, "
                  f"n={base.n}, p05-p95 {base.p05:.4g}..{base.p95:.4g}")
        detail = f"vehicle-specific baseline: {spread}"
        if z is not None:
            detail += f"; this value is {z:.1f} robust z from the median"
    return _persist(repo, inv_id, Evidence(
        stage=stage, kind="baseline",
        observation=f"{key}={value} judged against its own history",
        ts=ts or _iso(datetime.now(timezone.utc)),
        ref=f"baseline:{key}@{window_days:.0f}d",
        detail=detail, certain=base.n >= 8))


def add_hypothesis(repo, inv_id: int | None, text: str, *, would_falsify: str,
                   ts: str | None = None) -> Evidence:
    """Stage 3: a candidate mechanism plus what would overturn it."""
    return _persist(repo, inv_id, Evidence(
        stage="explanation", kind="hypothesis", observation=text,
        ts=ts or _iso(datetime.now(timezone.utc)),
        detail=f"would be falsified by: {would_falsify}", certain=False))


def add_counter_evidence(repo, inv_id: int | None, text: str, *,
                         ref: str = "", ts: str | None = None) -> Evidence:
    """Evidence that argues *against* the leading hypothesis.

    The brief requires counter-evidence to be included, not just sought when it
    is convenient, so this is a first-class evidence kind."""
    return _persist(repo, inv_id, Evidence(
        stage="explanation", kind="counter-evidence", observation=text,
        ts=ts or _iso(datetime.now(timezone.utc)), ref=ref,
        detail="recorded against the leading hypothesis", certain=False))


def independent_ecu_readings(repo, vehicle_id: int, key: str, *,
                             inv_id: int | None = None,
                             profile: str = "meb") -> Evidence:
    """Counter-evidence helper: the same quantity seen by another module.

    Where a key appears in several ECUs the copies are independent readings and
    may disagree. Finding a second module that agrees strengthens a
    measurement; finding one that disagrees weakens it. Returns a single
    evidence row summarising what was found (or that nothing was).
    """
    rows = repo.conn.execute(
        "SELECT ecu, COUNT(*) n, MAX(ts) last FROM measurements "
        "WHERE vehicle_id=? AND key=? AND success=1 GROUP BY ecu ORDER BY ecu",
        (vehicle_id, key)).fetchall()
    if len(rows) <= 1:
        return _persist(repo, inv_id, Evidence(
            stage="explanation", kind="counter-evidence",
            observation=f"no independent reading of {key} in other modules",
            ts=_iso(datetime.now(timezone.utc)), ref=key,
            detail="cannot cross-check this quantity against a second ECU",
            certain=False))
    detail = "; ".join(f"{r['ecu']}: {r['n']} rows, last {r['last']}"
                       for r in rows)
    return _persist(repo, inv_id, Evidence(
        stage="explanation", kind="counter-evidence",
        observation=f"{len(rows)} modules report {key}",
        ts=_iso(datetime.now(timezone.utc)), ref=key, detail=detail,
        certain=False))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def open_investigation(repo, vehicle_id: int, title: str, symptom: str, *,
                       anchor_key: str | None = None,
                       anchor_ecu: str | None = None,
                       criteria: str = "") -> Investigation:
    inv_id = repo.open_investigation(
        vehicle_id, title, symptom, criteria=criteria, anchor_key=anchor_key,
        anchor_ecu=anchor_ecu)
    row = repo.investigation(inv_id)
    return Investigation(id=inv_id, title=title, symptom=symptom,
                         anchor_key=anchor_key, anchor_ecu=anchor_ecu,
                         criteria=criteria,
                         opened_at=row["opened_at"] if row else "",
                         updated_at=row["updated_at"] if row else "")


def load_investigation(repo, investigation_id: int) -> Investigation | None:
    row = repo.investigation(investigation_id)
    if row is None:
        return None
    inv = Investigation(
        id=row["id"], title=row["title"], symptom=row["symptom"],
        anchor_key=row["anchor_key"], anchor_ecu=row["anchor_ecu"],
        criteria=row["criteria"] or "", status=row["status"],
        opened_at=row["opened_at"] or "", updated_at=row["updated_at"] or "",
        summary=row["summary"] or "", conclusion=row["conclusion"] or "")
    for r in repo.investigation_evidence(investigation_id):
        inv.evidence.append(Evidence(
            stage=r["stage"], kind=r["kind"], observation=r["observation"],
            ts=r["ts"], ref=r["ref"] or "", detail=r["detail"] or "",
            certain=bool(r["certain"])))
    return inv


def criteria_met(inv: Investigation) -> tuple[bool, str]:
    """Whether the written criteria are satisfied by the recorded evidence.

    Two independent gates, both required:

      * the investigation must have written criteria up front -- a diagnosis
        chosen first and justified afterwards is not a diagnosis;
      * there must be enough evidence, of the right stages, that is not
        uncertain. Known-invalid or stale observations cannot carry a
        diagnosis.
    """
    if not inv.criteria.strip():
        return False, "no resolution criteria were written when the " \
                      "investigation was opened"
    certain_obs = [e for e in inv.evidence
                   if e.stage == "observation" and e.certain]
    if len(certain_obs) < 1:
        return False, "no freshly observed, scoreable measurement supports it"
    if len(inv.evidence) < MIN_EVIDENCE_FOR_DIAGNOSIS:
        return False, (f"only {len(inv.evidence)} evidence rows; at least "
                       f"{MIN_EVIDENCE_FOR_DIAGNOSIS} are required before a "
                       f"conclusion")
    hypotheses = [e for e in inv.evidence if e.kind == "hypothesis"]
    if not hypotheses:
        return False, "no mechanism (explanation) has been stated"
    counter = [e for e in inv.evidence if e.kind == "counter-evidence"]
    return True, ("criteria satisfied" if counter else
                  "criteria satisfied (counter-evidence sought)")


def finalize(repo, inv_id: int, *, summary: str = "",
             criteria_confirmed: bool = False) -> Investigation:
    """Close an investigation honestly.

    `criteria_met` can only check structure (criteria written, enough rows, a
    certain observation, a stated mechanism) -- it cannot read the prose
    criteria to see whether they are actually true. So resolving also requires
    the caller to assert `criteria_confirmed=True`, which the UI does with an
    explicit checkbox. The default is False, so a bare "Finalise" click can
    never upgrade a hypothesis to a diagnosis.

    Sets `resolved` only when both gates pass; otherwise it records
    `insufficient-evidence` and says why.
    """
    inv = load_investigation(repo, inv_id)
    if inv is None:
        raise KeyError(f"no investigation {inv_id}")
    ok, reason = criteria_met(inv)
    if ok and not criteria_confirmed:
        ok = False
        reason = ("the written criteria were not confirmed as satisfied by the "
                  "operator; the structural checks alone cannot establish that")
    status = "resolved" if ok else "insufficient-evidence"
    conclusion = (f"CONFIRMED. {reason}." if ok else
                  f"NOT CONFIRMED. Insufficient evidence: {reason}. The "
                  f"observations and associations above remain, but no "
                  f"diagnosis is claimed.")
    repo.update_investigation(
        inv_id, status=status, summary=summary or inv.summary,
        conclusion=conclusion)
    inv.status = status
    inv.conclusion = conclusion
    return inv
