"""Structured record for the MEB experiment matrix (Phase 7).

The matrix prints to stdout. That is enough to run an arm and read the answer,
but not enough to come back to a week later: stdout interleaves all sixteen
arms, names no CAN mode or bitrate, and never records what was *expected* --
so an experiment that answered for a reason nobody anticipated looks identical
to one that answered for the reason it was designed to test.

Each arm is therefore declared as data with the fields the brief asks for:

    hypothesis  what this arm tests, and what the answer would mean
    config      the adapter command sequence, verbatim and in order
    can_mode    ATSPn in effect when the request was sent
    bitrate     CAN bitrate the adapter negotiated
    data_rate   FD data phase bitrate, or "n/a" on a classical bus
    addressing  ATSH header, plus ATCP priority where set
    request     the ISO-TP frame bytes, as sent
    expected    what a correct arm should produce
    actual      what came back
    interpret   what the actual result means

Adapter-state arms are declared as deltas from the baseline arm rather than as
16 restated command sequences. The differences between the arms ARE the
experiment, so writing them out in full would guarantee they stop being
comparable -- and a diff is what shows at a glance that only one variable moved.

Actual and interpretation stay unset until an arm runs. A record with an empty
`actual` is the honest state of an experiment nobody has performed yet, and is
rendered as "not run" rather than as a blank that reads like a result.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# What the adapter negotiates before any arm runs. Every arm inherits this and
# overrides only what it deliberately changes.
BASELINE = {
    "config": ["ATE0", "ATL0", "ATRV", "ATSP6"],
    "can_mode": "ATSP6 (CAN 11/500)",
    "bitrate": "500 kbit/s",
    "data_rate": "n/a (classical CAN)",
    "addressing": "ATSH 7E0 / ATCP 18 (functional)",
}

MEB = {
    "config": ["ATCP 17", "ATSH FC007B", "ATCAF0"],
    "can_mode": "ATSP6 (CAN 11/500)",
    "bitrate": "500 kbit/s",
    "data_rate": "n/a (classical CAN)",
    "addressing": "ATSH FC007B (BMS, 29-bit) / ATCP 17 (priority)",
}


@dataclass
class Arm:
    """One experiment, declared. Run results are filled in by the harness."""

    exp_id: str
    hypothesis: str
    request: str
    expected: str
    config: list[str] = field(default_factory=list)
    can_mode: str = ""
    bitrate: str = ""
    data_rate: str = ""
    addressing: str = ""
    actual: str = ""
    interpret: str = ""
    notes: str = ""

    def ran(self) -> bool:
        return bool(self.actual)


# The matrix, in the order the harness runs it.
ARMS: list[Arm] = [
    Arm(
        exp_id="EXP-1",
        hypothesis="The OBD surface itself works, so silence on the BMS is not "
                   "a dead adapter or a wrong port.",
        request="0100 (functional, Mode 01 supported PIDs)",
        expected="A list of supported PIDs, or 'NO DATA' if the bus is silent.",
        **BASELINE,
    ),
    Arm(
        exp_id="EXP-2",
        hypothesis="The gateway answers a UDS session request, confirming it is "
                   "reachable and awake.",
        request="1001 (functional, default session)",
        expected="50 01 with a source header.",
        **BASELINE,
    ),
    Arm(
        exp_id="EXP-3",
        hypothesis="The gateway serves DID reads, so DID 0xF190 is a known-good "
                   "control for a request that should certainly answer.",
        request="22 F190 (functional, VIN)",
        expected="62 F190 followed by 17 VIN bytes.",
        **BASELINE,
    ),
    Arm(
        exp_id="EXP-4",
        hypothesis="MEB 29-bit addressing is accepted by the adapter.",
        request="ATCP 17 then ATSH FC007B (configuration, not a vehicle request)",
        expected="'OK' from both; a '?' or 'ERROR' means the adapter rejected "
                "the header and no later arm means anything.",
        **{k: v for k, v in MEB.items() if k != "config"},
        config=["ATCP 17", "ATSH FC007B"],
    ),
    Arm(
        exp_id="EXP-5",
        hypothesis="BASELINE. MEB physical addressing alone is sufficient to "
                   "reach the BMS. Every variant arm is scored against whether "
                   "this arm was silent for the same DID -- an arm cannot "
                   "claim credit where the baseline already answered.",
        request="03 22 xx xx 55 x8 (single frame, DID 028C etc.)",
        expected="62 xx xx <data> if the path works; NO DATA is the result the "
                "other arms exist to explain.",
        **MEB,
    ),
    Arm(
        exp_id="EXP-6",
        hypothesis="Top candidate. Both reference implementations send ATBI "
                   "before pinning the protocol; our _base_init() does not, so "
                   "the clone may run its own detection and land somewhere "
                   "other than ATSP7. If this answers where EXP-5 did not, ATBI "
                   "is the entire difference.",
        request="03 22 02 8C 55 x8 after ATBI",
        expected="62 02 8C <data> from FC007B. NO DATA means ATBI is not the "
                "missing piece on its own. ATBI is adapter-local and reaches "
                "no ECU.",
        config=["ATBI"] + MEB["config"], can_mode=MEB["can_mode"],
        bitrate=MEB["bitrate"], data_rate=MEB["data_rate"],
        addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-7",
        hypothesis="The BMS requires the default session before serving DIDs.",
        request="10 01, then 03 22 02 8C 55 x8",
        expected="50 01 then 62 02 8C <data> from FC007B. NO DATA means the "
                "session was not the missing piece.",
        config=MEB["config"] + ["10 01"], can_mode=MEB["can_mode"],
        bitrate=MEB["bitrate"], data_rate=MEB["data_rate"],
        addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-7b",
        hypothesis="The BMS ignores requests without a tester-present frame, "
                   "as gateways sometimes require. NOTE this arm sends the "
                   "session as well, matching the harness, so it changes two "
                   "things: it separates 'session then tester present' from the "
                   "baseline, not tester-present alone.",
        request="10 01, 3E 00, then 03 22 02 8C 55 x8",
        expected="50 01, 7E 00, then 62 02 8C <data> from FC007B. NO DATA "
                "means neither tester-present nor the session changed anything.",
        config=MEB["config"] + ["10 01", "3E 00"], can_mode=MEB["can_mode"],
        bitrate=MEB["bitrate"], data_rate=MEB["data_rate"],
        addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-8",
        hypothesis="ATBI and the session and tester-present steps are all "
                   "required together, so neither alone would show a difference.",
        request="ATBI, 10 01, 3E 00, then 03 22 02 8C 55 x8",
        expected="62 02 8C <data> from FC007B, or NO DATA if the combination is "
                "still not sufficient.",
        config=["ATBI"] + MEB["config"] + ["10 01", "3E 00"],
        can_mode=MEB["can_mode"],
        bitrate=MEB["bitrate"], data_rate=MEB["data_rate"],
        addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-9",
        hypothesis="The energy module is an 11-bit module, not a 29-bit one, "
                   "so 29-bit addressing cannot reach it. spot2000 gives both "
                   "halves of the pair.",
        request="03 22 <energy DID> 55 x8 to ATSH 710, expect 77A",
        expected="62 <DID> <data> with source header 77A. NO DATA would mean the "
                "energy module is not on 11-bit 0x710 either.",
        config=["ATSH 710", "ATCAF0"],
        can_mode="ATSP6 (CAN 11/500)", bitrate="500 kbit/s",
        data_rate="n/a (classical CAN)",
        addressing="ATSH 710 -> response 77A (11-bit)",
    ),
    Arm(
        exp_id="EXP-10",
        hypothesis="The DC/DC converter is 29-bit like the BMS but at a "
                   "different address, so it exercises whether the address "
                   "itself is what differs.",
        request="03 22 46 5B 55 x8 to ATSH 17FC00B9",
        expected="62 46 5B <data> from 17FC00B9. NO DATA means the DC/DC module is "
                "not reachable at 29-bit either.",
        config=["ATCP 17", "ATSH 17FC00B9", "ATCAF0"],
        can_mode=MEB["can_mode"], bitrate=MEB["bitrate"],
        data_rate=MEB["data_rate"],
        addressing="ATSH 17FC00B9 (DC/DC, 29-bit) / ATCP 17",
    ),
    Arm(
        exp_id="EXP-11",
        hypothesis="The gateway will route a functional BMS request on our "
                   "behalf, which would remove the need to address the BMS "
                   "directly.",
        request="03 22 02 8C 55 x8 functional to 7E0",
        expected="Either a routed 62 response or a 7F NRC; both are informative, "
                "NO DATA is not.",
        **BASELINE,
    ),
    Arm(
        exp_id="EXP-12",
        hypothesis="The BMS is reachable on 11-bit OBD addressing too, which "
                   "would make 0x7E5/0x7ED viable.",
        request="03 22 02 8C 55 x8 to ATSH 7E5",
        expected="62 02 8C <data> from 7ED. NO DATA rules out 11-bit OBD addressing "
                "for the BMS, consistent with the MEB map.",
        config=["ATSH 7E5", "ATCAF0"],
        can_mode="ATSP6 (CAN 11/500)", bitrate="500 kbit/s",
        data_rate="n/a (classical CAN)",
        addressing="ATSH 7E5 -> response 7ED (11-bit)",
    ),
    Arm(
        exp_id="EXP-13",
        hypothesis="Rule out a bus-type mismatch: if the adapter reports CAN-FD "
                   "and the car is classical CAN, every MEB arm would be "
                   "explained. ELM327 v1.5 cannot do FD at all, so a 'no' here "
                   "is the expected answer and closes the question.",
        request="AT@I / ATSP protocol list (adapter identification)",
        expected="An ELM327 string with no FD support. Not a vehicle request.",
        config=MEB["config"], can_mode=MEB["can_mode"], bitrate=MEB["bitrate"],
        data_rate=MEB["data_rate"], addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-14",
        hypothesis="The receive filter is not accepting the BMS's response "
                   "header, so a correct request is discarded as noise. The "
                   "datasheet pairs non-standard framing with ATCF; the "
                   "reference implementations set a filter for the module.",
        request="ATCF 17FE7 then 03 22 02 8C 55 x8",
        expected="62 02 8C <data>, where without ATCF it was NO DATA.",
        config=["ATCF 17FE7", "ATCP 17", "ATSH FC007B", "ATCAF0"],
        can_mode=MEB["can_mode"], bitrate=MEB["bitrate"],
        data_rate=MEB["data_rate"], addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-15",
        hypothesis="The receive filter AND mask need pinning to the module's "
                   "response ID rather than left at whatever _base_init() set. "
                   "Note ATCRA is a no-op on this clone per the datasheet "
                   "section already recorded, so silence here is expected and "
                   "does not by itself rule the filter out.",
        request="ATCRA 17FE007B then 03 22 02 8C 55 x8",
        expected="62 02 8C <data>. NO DATA is the expected outcome on a v1.5 clone, "
                "which treats ATCRA as restore-defaults rather than a filter.",
        config=["ATCRA 17FE007B", "ATCP 17", "ATSH FC007B", "ATCAF0"],
        can_mode=MEB["can_mode"], bitrate=MEB["bitrate"],
        data_rate=MEB["data_rate"], addressing=MEB["addressing"],
    ),
    Arm(
        exp_id="EXP-16",
        hypothesis="The difference is a combination: no single-variable arm "
                   "answers because each needs the others too. If this answers "
                   "and EXP-6/8/14/15 do not, the fix is the whole sequence, "
                   "not one command.",
        request="ATBI, ATCF, ATCRA, 10 01, 3E 00, then 03 22 02 8C 55 x8",
        expected="62 02 8C <data>. NO DATA despite every arm working alone would rule "
                "out the combination hypothesis and point at the request framing "
                "or the bitrate instead.",
        config=["ATBI", "ATCF 17FE7", "ATCRA 17FE007B", "ATCP 17",
                "ATSH FC007B", "ATCAF0"],
        can_mode=MEB["can_mode"], bitrate=MEB["bitrate"],
        data_rate=MEB["data_rate"], addressing=MEB["addressing"],
    ),
]

# Arms whose job is to change exactly one thing about the baseline. Kept as a
# checkable invariant rather than as a comment: if an arm silently changes two
# things, a negative result stops meaning anything, because there is no way to
# tell which change was responsible.
SINGLE_VARIANT_OF_BASELINE = {
    "EXP-6": ["ATBI"],
    "EXP-7": ["10 01"],
    "EXP-14": ["ATCF 17FE7"],
    "EXP-15": ["ATCRA 17FE007B"],
}

# Deliberately absent: EXP-7b sends the session as well as tester-present,
# because that is what the harness does. Listing it as a single-variant arm
# would assert something false about it. Its two-command delta is declared here
# instead, and the test suite checks it differs from the baseline in exactly the
# two commands named -- so the arm cannot quietly gain or lose a variable.
DECLARED_DELTAS = {
    **SINGLE_VARIANT_OF_BASELINE,
    "EXP-7b": ["10 01", "3E 00"],
}


def by_id(exp_id: str) -> Arm:
    for arm in ARMS:
        if arm.exp_id == exp_id:
            return arm
    raise KeyError(f"no arm {exp_id!r}")


def unrun() -> list[Arm]:
    return [a for a in ARMS if not a.ran()]


def not_run_text(arm: Arm) -> str:
    """Placeholder for an arm's interpretation.

    An arm that ran but whose result cannot be attributed is not in the same
    position as one nobody tried, and rendering both as "not run" would
    contradict the `Actual response` sitting directly above it.
    """
    return "**not run**" if not arm.ran() else "**not determined**"


def render_markdown(records: dict[str, Arm] | None = None,
                    preamble: str = "") -> str:
    """The full record. Arms that never ran say so, in a way that reads as
    'not run' rather than as an absent result."""
    lines = [
        "# MEB experiment record",
        "",
    ]
    if preamble:
        lines += [
            f"> **{preamble}**",
            ">",
            "> Every arm below was run against the built-in simulator, which",
            "> answers regardless of adapter state. A `62` here shows the harness",
            "> works. It shows nothing about this car.",
            "",
        ]
    lines += [
        "Declared arms for `tools/diagnose_meb_path.py`. `Actual response` and",
        "`Interpretation` are filled by running the matrix against the car; an",
        "arm that has not run says **not run**, which is different from an arm",
        "that ran and returned nothing.",
        "",
        "Every arm inherits the adapter negotiation the tool performs before any",
        "arm. `CAN mode` / `bitrate` / `data bitrate` are recorded because a",
        "wrong bitrate produces the same `NO DATA` as a wrong address, and the",
        "two are otherwise indistinguishable from the outside.",
        "",
        f"{len(ARMS)} arms declared, {len(unrun())} not yet run against the car.",
        "",
    ]
    for arm in ARMS:
        lines += [
            f"## {arm.exp_id}",
            "",
            f"**Hypothesis.** {arm.hypothesis}",
            "",
            f"**Adapter configuration** (in order). "
            f"{' -> '.join(arm.config) or 'none'}",
            "",
            "| field | value |",
            "|---|---|",
            f"| CAN mode | {arm.can_mode} |",
            f"| CAN bitrate | {arm.bitrate} |",
            f"| Data bitrate | {arm.data_rate} |",
            f"| Addressing | {arm.addressing} |",
            f"| Request | `{arm.request}` |",
            f"| Expected response | {arm.expected} |",
            f"| Actual response | {arm.actual or '**not run**'} |",
            f"| Interpretation | {arm.interpret or not_run_text(arm)} |",
        ]
        if arm.notes:
            lines.append(f"| Notes | {arm.notes} |")
        lines.append("")
    return "\n".join(lines)


def to_json() -> str:
    return json.dumps([asdict(a) for a in ARMS], indent=2)


def check() -> list[str]:
    """Consistency problems. Returns human-readable lines; empty means sound.

    Two invariants, both of which silently destroy the matrix's value if broken:

    - A single-variable arm must differ from the baseline in exactly one way.
      Otherwise silence in that arm is uninterpretable.
    - The baseline must exist and must be an arm, because every variant's
      credit is scored against whether it was silent.
    """
    problems = []
    base = next((a for a in ARMS if a.exp_id == "EXP-5"), None)
    if base is None:
        return ["EXP-5 (the baseline) is missing -- every variant's credit is "
                "scored against it"]

    base_cfg = set(base.config)
    for exp_id, delta in DECLARED_DELTAS.items():
        arm = by_id(exp_id)
        added = [c for c in arm.config if c not in base_cfg]
        missing = [c for c in delta if c not in arm.config]
        if missing:
            problems.append(f"{exp_id}: declared delta {missing} is not in its "
                            f"config {arm.config}")
        extra = [c for c in added if c not in delta]
        if extra:
            problems.append(f"{exp_id}: config differs from baseline in "
                            f"{extra} as well as the declared {delta} -- with "
                            f"two changes a negative result explains nothing")
        for field_name, arm_value, base_value in (
                ("addressing", arm.addressing, base.addressing),
                ("bitrate", arm.bitrate, base.bitrate),
                ("can_mode", arm.can_mode, base.can_mode)):
            if arm_value != base_value:
                problems.append(f"{exp_id}: {field_name} differs from the "
                                f"baseline ({arm_value!r} vs {base_value!r}) "
                                f"but is not a declared variable")
    return problems
