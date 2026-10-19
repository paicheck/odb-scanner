"""Decoder validation workspace.

WHY THIS EXISTS
---------------
The brief separates two things that were tangled together: *excluding* a
known-bad measurement from health scoring, and *investigating* whether its
decoder can be fixed. Excluding without a place to investigate would hide the
problem; scoring with it would let a wrong scale factor drive the verdict. This
module is that place.

It works from evidence that already exists on disk and never touches the car:

  * a stored `measurements.raw_response` is the exact bytes the vehicle
    returned. Re-decoding them with the live decoder must reproduce the stored
    value -- if it does not, the stored value and the decoder have diverged,
    which is a real defect (a decoder was changed after the rows were written);
  * the catalogue's declared byte layout (see `decoders/catalogue.py`) must
    reproduce what the live decoder does -- `validate_definition` already
    enforces this on a probe record; here it is checked against the car's own
    bytes;
  * the decoded value must fall inside the key's plausibility range
    (`decoders/quality.py`). A key that fails this is recorded FAIL and stays
    out of scoring, with the physical absurdity named.

Verdicts reuse `analysis/replay.py`'s discipline: only real vehicle responses
are evidence. There is no synthetic path in, so a fixture can never make an
evaluation pass.

No definition is ever rewritten here. The output is a set of evaluations a human
reads before deciding to change a decoder -- which is exactly the review the
project's own history shows was missing when two conflicting energy layouts
shipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from analysis.replay import EvidenceKind
from decoders.catalogue import ByteLayout, ByteOrder, build_catalogue
from decoders.quality import Plausibility, Verification, quality_registry
from decoders.registry import build_default_registry


def _apply_layout(layout: ByteLayout, record: bytes) -> float | None:
    chunk = record[layout.offset:layout.offset + layout.length]
    if len(chunk) != layout.length:
        return None
    order = "big" if layout.byte_order is ByteOrder.BIG_ENDIAN else "little"
    n = int.from_bytes(chunk, order, signed=layout.signed)
    return (n * layout.scale / layout.divisor) + layout.addend


@dataclass
class KeyEvaluation:
    key: str
    verdict: str                  # PASS | FAIL | INDETERMINATE
    method: str
    n_samples: int
    issues: list[str] = field(default_factory=list)
    verification: str = Verification.UNKNOWN.value
    evidence_kind: str = EvidenceKind.VEHICLE_RESPONSE.value
    examples: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "verdict": self.verdict,
            "method": self.method,
            "n_samples": self.n_samples,
            "issues": self.issues,
            "verification": self.verification,
            "evidence_kind": self.evidence_kind,
            "examples": self.examples,
        }


def _raw_hex(raw) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().replace(" ", "")
    # Raw bytes are stored hex; anything else cannot be re-decoded and is
    # reported rather than guessed at.
    try:
        bytes.fromhex(text)
    except ValueError:
        return None
    return text


def evaluate_key(repo, vehicle_id: int, key: str, profile: str = "meb", *,
                 sample_limit: int = 25) -> KeyEvaluation:
    """Re-decode a key's stored raw bytes and judge the decoder against them."""
    quality = quality_registry(profile).get(key)
    registry = build_default_registry(profile)
    catalogue = build_catalogue(profile, registry)
    defn = catalogue.get(key)
    verification = (quality.verification.value if quality
                    else Verification.UNKNOWN.value)
    issues: list[str] = []

    if quality is not None and quality.verification is Verification.INVALID:
        issues.append(f"known-invalid decode: {quality.known_issue}")
    if quality is not None and quality.data_kind.value in ("text", "json"):
        return KeyEvaluation(
            key=key, verdict="PASS" if not issues else "FAIL",
            method="structural", n_samples=0, issues=issues,
            verification=verification)

    spec = registry.get(key) if key in {s.key for s in registry.all()} else None
    # Only THIS TOOL's reads count as vehicle responses. An imported Car
    # Scanner row carries the CSV line in raw_response, not UDS bytes; treating
    # it as evidence would flag the import as a decoder failure (it did, for
    # 40-odd cell keys) and, worse, let third-party data validate our decoder.
    # Simulated rows are excluded for the same reason.
    rows = repo.conn.execute(
        "SELECT ts, value, text_value, raw_response FROM measurements "
        "WHERE vehicle_id=? AND key=? AND success=1 AND raw_response IS NOT NULL "
        "AND provenance NOT LIKE 'imported%' "
        "AND provenance <> 'simulated' "
        "ORDER BY ts DESC LIMIT ?", (vehicle_id, key, sample_limit),
    ).fetchall()

    n = 0
    examples: list[dict] = []
    layout_mismatch = 0
    decoder_mismatch = 0
    implausible = 0
    undecodable = 0
    for r in rows:
        hexed = _raw_hex(r["raw_response"])
        if hexed is None:
            undecodable += 1
            continue
        record = bytes.fromhex(hexed)
        stored = r["value"]

        if spec is not None and spec.decode is not None:
            got = spec.decode_value(record)
            if got is None:
                undecodable += 1
                continue
            if stored is not None and isinstance(stored, (int, float)) \
                    and abs(float(got) - float(stored)) > 1e-6:
                decoder_mismatch += 1
                if len(examples) < 5:
                    examples.append({
                        "ts": r["ts"], "raw": hexed, "stored": stored,
                        "redecoded": float(got),
                        "why": "stored value differs from a fresh decode of the "
                               "same bytes -- the decoder changed after the row "
                               "was written"})
            value = got
        elif defn is not None and defn.layout is not None:
            value = _apply_layout(defn.layout, record)
        else:
            # No decoder and no declared layout: the bytes are preserved but
            # there is nothing to check them against.
            value = stored

        if defn is not None and defn.layout is not None and spec is not None \
                and spec.decode is not None:
            laid = _apply_layout(defn.layout, record)
            got = spec.decode_value(record)
            if laid is None or got is None or abs(float(laid) - float(got)) > 1e-9:
                layout_mismatch += 1
                if len(examples) < 5:
                    examples.append({
                        "ts": r["ts"], "raw": hexed,
                        "catalogue_layout": laid, "live_decoder": got,
                        "why": "catalogue layout and live decoder disagree on "
                               "these bytes"})

        if value is not None and quality is not None:
            status, why = quality.check(value)
            if status is Plausibility.IMPLAUSIBLE:
                implausible += 1
                if len(examples) < 5:
                    examples.append({"ts": r["ts"], "raw": hexed,
                                     "value": value, "why": why})
        n += 1

    if n == 0:
        if quality is not None and quality.verification is Verification.INVALID:
            # A known-bad decode is a determination, not something to balance
            # against how many samples happen to be on file.
            verdict = "FAIL"
        else:
            verdict = "INDETERMINATE"
            issues.append("no stored raw bytes to re-decode")
    elif quality is not None and quality.verification is Verification.INVALID:
        verdict = "FAIL"
    elif implausible or layout_mismatch or decoder_mismatch or undecodable:
        verdict = "FAIL"
        if implausible:
            issues.append(f"{implausible}/{n} decoded values outside the "
                          f"plausibility range")
        if layout_mismatch:
            issues.append(f"{layout_mismatch}/{n} rows where the catalogue "
                          f"layout and live decoder disagree")
        if decoder_mismatch:
            issues.append(f"{decoder_mismatch}/{n} rows where the stored value "
                          f"differs from a fresh decode of the same bytes")
        if undecodable:
            issues.append(f"{undecodable} rows not decodable from raw bytes")
    elif quality is not None and quality.plausible_min is None \
            and quality.plausible_max is None:
        verdict = "INDETERMINATE"
        issues.append("no plausibility range established; only structural "
                      "checks ran")
    else:
        verdict = "PASS"
    return KeyEvaluation(
        key=key, verdict=verdict, method="offline_replay", n_samples=n,
        issues=issues, verification=verification, examples=examples[:5])


def evaluate_all(repo, vehicle_id: int, profile: str = "meb", *,
                 limit: int | None = None) -> list[KeyEvaluation]:
    """Evaluate every catalogue key, worst verdict first.

    Ordering puts FAIL before INDETERMINATE before PASS so a reader sees the
    problems without sorting. Within a verdict, keys are alphabetical.
    """
    from decoders.quality import quality_registry
    keys = sorted(quality_registry(profile))
    if limit is not None:
        keys = keys[:limit]
    out = [evaluate_key(repo, vehicle_id, k, profile) for k in keys]
    rank = {"FAIL": 0, "INDETERMINATE": 1, "PASS": 2}
    out.sort(key=lambda e: (rank.get(e.verdict, 3), e.key))
    return out


def lab_rows(repo, vehicle_id: int, profile: str = "meb", *,
             limit: int | None = None) -> list[dict]:
    """Rows for the decoder-lab page: verdict + quality + physical issue."""
    quality = quality_registry(profile)
    rows = []
    for ev in evaluate_all(repo, vehicle_id, profile, limit=limit):
        q = quality.get(ev.key)
        row = ev.as_dict()
        if q is not None:
            row.update({
                "data_kind": q.data_kind.value,
                "use_in_scoring": q.use_in_scoring,
                "known_issue": q.known_issue,
                "unit": q.unit,
                "plausible_min": q.plausible_min,
                "plausible_max": q.plausible_max,
            })
        rows.append(row)
    return rows


def persist_evaluations(repo, profile: str,
                        evaluations: list[KeyEvaluation]) -> int:
    """Sync definitions then store each verdict as a definition evaluation.

    Returns the number of evaluations written. Kept separate from
    `evaluate_all` so a read-only render of the lab page has no side effect;
    only an explicit call writes.
    """
    defs = list(build_catalogue(profile).values())
    versions = repo.sync_definitions(defs)
    written = 0
    for ev in evaluations:
        version = versions.get(ev.key)
        if version is None:
            continue
        repo.record_evaluation(
            ev.key, version, ev.method, ev.verdict, ev.n_samples,
            detail="; ".join(ev.issues))
        written += 1
    return written
