"""Offline replay and comparison for measurement definitions.

THE POINT
---------
A definition is only as good as the evidence behind it, and evidence has to be
re-checkable. This module re-decodes stored bytes under a named definition
version, compares the result against independently known values, and reports
what it could and could not establish. It never talks to a vehicle.

Where the data comes from, and this matters for honesty:

  * `replay_did_record()` is pure arithmetic over bytes. It is as trustworthy
    as the definition it is handed.
  * `Observation` records carry an `evidence_kind`. An observation derived
    from a Car Scanner CSV export is a THIRD-PARTY REPORTED VALUE: real data
    about this vehicle, but decoded by somebody else's software. It is not a
    vehicle response, and it is labelled that way in every report.
  * `SYNTHETIC_FIXTURE` observations are simulator output. They can exercise
    code paths. They can never corroborate a definition, and `compare()`
    refuses to count them towards a PASS.

That distinction is why this module exists: the previous validation of
0x2AB8/0x2AB2 consisted of tests that decoded the simulator's own output with
the same constants the simulator used, which would have passed just as happily
if both had been wrong.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum

from decoders.catalogue import ByteLayout, ByteOrder
from decoders.registry import DIDRegistry


class EvidenceKind(str, Enum):
    """How an observation came to exist. NOT a confidence level."""
    VEHICLE_RESPONSE = "vehicle_response"
    THIRD_PARTY_REPORTED_VALUE = "third-party reported value"
    SYNTHETIC_FIXTURE = "synthetic fixture"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class Observation:
    key: str
    value: float
    evidence_kind: EvidenceKind
    source: str = ""
    ts: str = ""
    raw_hex: str | None = None

    @property
    def is_corroborating(self) -> bool:
        """Only real-world evidence can corroborate a definition."""
        return self.evidence_kind in (EvidenceKind.VEHICLE_RESPONSE,
                                      EvidenceKind.THIRD_PARTY_REPORTED_VALUE)


@dataclass
class ReplayResult:
    key: str
    definition_version: str
    decoded: float | None
    expected: float | None
    verdict: Verdict
    detail: str
    n_samples: int = 0
    evidence_kinds: tuple[str, ...] = ()
    extras: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["evidence_kinds"] = "; ".join(self.evidence_kinds)
        d.update(self.extras)
        return d


def _integer_at(record: bytes, layout: ByteLayout) -> int:
    chunk = record[layout.offset:layout.offset + layout.length]
    if len(chunk) != layout.length:
        raise ValueError(
            f"record {record.hex().upper()} is {len(record)} byte(s); "
            f"layout needs {layout.offset + layout.length} to read "
            f"[{layout.offset}:{layout.offset + layout.length}]")
    order = "big" if layout.byte_order is ByteOrder.BIG_ENDIAN else "little"
    return int.from_bytes(chunk, order, signed=layout.signed)


def replay_did_record(record: bytes, layout: ByteLayout) -> float:
    """value = raw * scale / divisor + addend, per the declared layout.

    Raises rather than returning a number when the record is too short. A
    short record read from the wrong offset produces a plausible-looking
    value that is indistinguishable from a real one once stored, which is the
    failure mode the project's own u32be docstring documents.
    """
    n = _integer_at(record, layout)
    return (n * layout.scale / layout.divisor) + layout.addend


def replay_with_registry(record: bytes, key: str,
                         registry: DIDRegistry) -> tuple[float | None, str]:
    """Decode through the live decoder, returning (value, why-it-failed)."""
    try:
        spec = registry.get(key)
    except KeyError:
        return None, f"no DIDSpec for {key!r}"
    if spec.decode is None:
        return None, f"{key} has no decoder"
    try:
        value = spec.decode(record)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if value is None:
        return None, f"{key} decoder returned None"
    return value, ""


def scale_candidates(observations: Sequence[float]) -> list[int]:
    """Scale factors s such that every observed value is an integral count.

    A scale applied to an integer raw can only reproduce v when s divides v.
    Two or more readings of the same measurement therefore force s to divide
    their gcd. This is the constraint that discriminated the two competing
    layouts for 0x2AB8 without needing a single captured byte.
    """
    if not observations:
        return []
    from math import gcd

    g = 0
    for v in observations:
        g = gcd(g, int(round(v)))
    if g == 0:
        return []
    return [s for s in range(1, g + 1) if g % s == 0]


def layout_admits(layout: ByteLayout, observations: Sequence[Observation]) -> list[int]:
    """Raw counts a layout would need in order to reproduce these values.

    Returns one entry per distinct observed value, or an empty list for values
    the layout cannot reach from any integer raw. The empty entries are the
    useful part: they are what makes a layout refutable without a capture.
    """
    out: list[int] = []
    for v in observations:
        n = v * layout.divisor / layout.scale - layout.addend
        out.append(int(round(n)) if abs(n - round(n)) < 1e-9 else n)  # type: ignore[arg-type]
    return out


def infer_layout(key: str, observations: Sequence[Observation],
                registry: DIDRegistry | None = None,
                width: int = 2, byte_order: ByteOrder = ByteOrder.BIG_ENDIAN,
                signed: bool = False,
                plausible_max: float | None = None) -> ReplayResult:
    """Find the scale a set of real-world values implies, and test it.

    `plausible_max` is optional and only used to reject scales that would make
    the quantity absurd; it is never used to prefer one scale over another.
    """
    reg = registry or DIDRegistry()
    try:
        reg.get(key)
    except KeyError:
        pass
    corroborating = [o for o in observations if o.is_corroborating]
    if not corroborating:
        return ReplayResult(
            key, "", None, None, Verdict.INDETERMINATE,
            "no corroborating observation: nothing to fit a scale to",
            n_samples=len(observations),
            evidence_kinds=tuple(sorted({o.evidence_kind.value
                                         for o in observations})))
    values = [o.value for o in corroborating]
    cands = scale_candidates(values)
    viable = [s for s in cands
              if plausible_max is None or max(values) / s <= plausible_max]
    distinct = sorted({int(round(v)) for v in values})
    if len(viable) == 1:
        verdict = Verdict.PASS
        tail = "a single admissible scale, consistent with every reading"
    else:
        verdict = Verdict.INDETERMINATE
        tail = ("the data do not single out one scale; this cannot confirm any "
                "particular one")
    return ReplayResult(
        key, "",
        decoded=viable[0] if viable else None,
        expected=max(values),
        verdict=verdict,
        detail=(f"admissible scales from {len(corroborating)} corroborating "
                f"value(s) {distinct}: {cands}"
                + (f"; rejected as implausible: "
                   f"{[s for s in cands if s not in viable]}" if viable != cands else "")
                + f"; {tail}"),
        n_samples=len(observations),
        evidence_kinds=tuple(sorted({o.evidence_kind.value for o in observations})),
        extras={"admissible_scales": cands, "viable_scales": viable,
                "distinct_values": distinct,
                "synthetic_observations": len(observations) - len(corroborating)})


def refuted_layouts(key: str, candidates: dict[str, ByteLayout],
                    observations: Sequence[Observation]) -> dict[str, str]:
    """Which candidate layouts cannot produce these values from any integer raw.

    This is the check that settled 0x2AB8: a scale factor applied to an integer
    count reproduces v only when s divides v, so two readings of one DID force
    s | gcd(values). A candidate whose scale is not such a divisor is not
    merely less likely -- it is excluded, with no vehicle required.
    """
    corroborating = [o.value for o in observations if o.is_corroborating]
    out: dict[str, str] = {}
    for name, layout in candidates.items():
        needs = layout_admits(layout, corroborating)
        unreached = [f"{v:g} (needs raw {n:.4f})"
                     for v, n in zip(corroborating, needs)
                     if not isinstance(n, int)]
        if unreached:
            out[name] = ("cannot reproduce: " + ", ".join(unreached)
                         if unreached else "ok")
        else:
            out[name] = (f"can reproduce all {len(corroborating)} readings from "
                         f"raw counts {needs}")
    return out


def compare(key: str, definition_layout: ByteLayout | None,
            definition_version: str, observations: Sequence[Observation],
            registry: DIDRegistry | None = None,
            tolerance: float = 0.5) -> ReplayResult:
    """Decode each observation's bytes and compare with its known value.

    Observations without `raw_hex` are used only to constrain the scale: their
    value is already a decoded number, so re-decoding them proves nothing.
    Synthetic observations are decoded and reported but never counted towards
    a PASS, because a simulator reproducing its own constants is not evidence.
    """
    reg = registry
    kinds = tuple(sorted({o.evidence_kind.value for o in observations}))
    if definition_layout is None:
        return ReplayResult(key, definition_version, None, None,
                            Verdict.INDETERMINATE,
                            "no layout declared: cannot decode", 0, kinds)
    if not observations:
        return ReplayResult(key, definition_version, None, None,
                            Verdict.INDETERMINATE, "no observations supplied",
                            0, kinds)

    decoded_any = 0
    max_err = 0.0
    errors: list[str] = []
    real_replays = 0
    for o in observations:
        if not o.raw_hex:
            continue
        try:
            record = bytes.fromhex(o.raw_hex)
        except ValueError as exc:
            errors.append(f"{o.source or o.ts or o.key}: bad hex ({exc})")
            continue
        try:
            got = replay_did_record(record, definition_layout)
        except ValueError as exc:
            errors.append(f"{o.source or o.ts or '?'}: {exc}")
            continue
        decoded_any += 1
        if reg is not None:
            live, why = replay_with_registry(record, key, reg)
            if live is not None and abs(float(live) - got) > 1e-9:
                errors.append(
                    f"{key}: declared layout gives {got} but the live decoder "
                    f"gives {live} on {o.raw_hex.upper()}")
        err = abs(got - o.value)
        max_err = max(max_err, err)
        if err > tolerance:
            errors.append(
                f"{o.source or o.ts or '?'}: decoded {got} != expected "
                f"{o.value} (err {err:g} > {tolerance:g})")
        if o.is_corroborating:
            real_replays += 1

    synthetic = [o for o in observations
                 if o.evidence_kind is EvidenceKind.SYNTHETIC_FIXTURE]
    if errors:
        verdict = Verdict.FAIL
        detail = "; ".join(errors)
    elif real_replays == 0:
        verdict = Verdict.INDETERMINATE
        detail = (f"decoded {decoded_any} record(s) with no error, but all of "
                  f"them were {len(synthetic)} synthetic fixture(s); a "
                  f"simulator reproducing its own constants is not evidence")
    else:
        verdict = Verdict.PASS
        detail = (f"decoded {decoded_any} record(s), {real_replays} of them "
                  f"from real-world evidence, max error {max_err:g}")

    result = ReplayResult(key, definition_version, None, None, verdict, detail,
                          len(observations), kinds)
    result.extras = {
        "records_decoded": decoded_any,
        "real_world_replays": real_replays,
        "synthetic_records": len(synthetic),
        "max_error": max_err,
    }
    if reg is not None:
        # Cross-check the declared layout against the live decoder even when
        # no bytes were supplied, so a catalogue/decoder disagreement is
        # reported rather than silently tolerated. The marker is masked to the
        # declared width: a fixed 0x1234 overflows a 1-byte layout.
        probe_len = (definition_layout.offset + definition_layout.length)
        probe = bytearray(probe_len)
        marker = 0x1234 & ((1 << (8 * definition_layout.length)) - 1)
        probe[definition_layout.offset:probe_len] = marker.to_bytes(
            definition_layout.length,
            "big" if definition_layout.byte_order is ByteOrder.BIG_ENDIAN
            else "little")
        live, why = replay_with_registry(bytes(probe), key, reg)
        want = replay_did_record(bytes(probe), definition_layout)
        result.extras["live_decoder_agrees"] = (
            live is not None and abs(float(live) - want) < 1e-9)
        if live is None:
            result.extras["live_decoder_note"] = why
    return result


def observations_from_measurements(rows: Iterable, source_label: str,
                                   raw_is_hex: bool = True) -> list[Observation]:
    """Build Observations from `measurements` rows.

    `raw_is_hex` is False for rows whose raw_response holds an imported export
    line rather than bytes -- those become THIRD_PARTY_REPORTED_VALUE with no
    raw_hex, which is exactly what they are.
    """
    out: list[Observation] = []
    for r in rows:
        value = r["value"] if hasattr(r, "keys") else r[2]
        raw = r["raw_response"] if hasattr(r, "keys") else r[-1]
        method = r["decoding_method"] if hasattr(r, "keys") else ""
        ts = r["ts"] if hasattr(r, "keys") else ""
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            # `measurements.value` is TEXT and also holds non-numeric decodes
            # (`gear` is "P", `vin` is a VIN string). An Observation is numeric
            # by contract, so a text row cannot constrain a scale; skip it
            # instead of aborting the whole replay on the first one.
            continue
        kind = (EvidenceKind.THIRD_PARTY_REPORTED_VALUE
                if method == "carscanner-export"
                else EvidenceKind.VEHICLE_RESPONSE)
        hexed = raw if (raw and raw_is_hex
                        and all(c in "0123456789abcdefABCDEF" for c in raw)) else None
        out.append(Observation(key=r["key"] if hasattr(r, "keys") else "",
                               value=numeric, evidence_kind=kind,
                               source=f"{source_label} {ts}".strip(),
                               ts=ts, raw_hex=hexed))
    return out


def to_csv(results: Sequence[ReplayResult]) -> str:
    cols = ["key", "definition_version", "verdict", "n_samples",
            "records_decoded", "real_world_replays", "synthetic_records",
            "max_error", "evidence_kinds", "admissible_scales", "detail"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in results:
        d = r.as_dict()
        d["detail"] = d["detail"].replace("\n", " ")
        w.writerow(d)
    return buf.getvalue()


def to_json(results: Sequence[ReplayResult], indent: int = 2) -> str:
    return json.dumps([r.as_dict() for r in results], indent=indent,
                      default=str, ensure_ascii=False)
