"""The experiment matrix's record must be complete and self-consistent before
any of it is pointed at the car.

The failure this guards against is a matrix that cannot be read back. A record
with a missing CAN bitrate or an arm that quietly changed two variables produces
NO DATA for reasons nobody can reconstruct afterwards, and the natural response
to that is to try more commands at random -- which is exactly what this work is
not supposed to do.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.experiment_record import (  # noqa: E402
    ARMS,
    DECLARED_DELTAS,
    MEB,
    SINGLE_VARIANT_OF_BASELINE,
    Arm,
    by_id,
    check,
    render_markdown,
    unrun,
)


def test_record_is_internally_consistent():
    """Single-variable arms differ from the baseline in exactly one way."""
    problems = check()
    assert not problems, "\n".join(problems)


def test_baseline_arm_exists():
    """Every variant is scored against whether EXP-5 was silent for that DID.

    Without it, `claim()` has nothing to gate on and every arm reports itself
    as the reason the BMS answered.
    """
    baseline = by_id("EXP-5")
    assert "BASELINE" in baseline.hypothesis
    assert baseline.can_mode == MEB["can_mode"]


def test_every_arm_declares_every_field_the_brief_asks_for():
    """Hypothesis, config, mode, both bitrates, addressing, request, expected.

    A field left blank renders as a blank cell, which reads as "nothing to
    report" rather than "not recorded". Better to fail here than to leave the
    next person guessing.
    """
    for arm in ARMS:
        assert arm.exp_id.startswith("EXP-"), arm.exp_id
        assert arm.hypothesis.strip(), f"{arm.exp_id}: no hypothesis"
        assert arm.request.strip(), f"{arm.exp_id}: no request recorded"
        assert arm.expected.strip(), f"{arm.exp_id}: no expected response"
        assert arm.config, f"{arm.exp_id}: no adapter configuration"
        assert arm.can_mode.strip(), f"{arm.exp_id}: no CAN mode"
        assert arm.bitrate.strip(), f"{arm.exp_id}: no CAN bitrate"
        assert arm.data_rate.strip(), f"{arm.exp_id}: no data bitrate"
        assert arm.addressing.strip(), f"{arm.exp_id}: no addressing"


def test_data_bitrate_states_that_there_is_no_fd_phase():
    """A classical bus must say so rather than leave the field ambiguous.

    CAN-FD was ruled out -- the ELM327 v1.5 clone cannot speak it and Car
    Scanner read this car through one -- so recording a data bitrate here would
    imply a requirement that does not exist.
    """
    for arm in ARMS:
        assert "n/a" in arm.data_rate.lower() or "classical" in \
            arm.data_rate.lower(), (
            f"{arm.exp_id}: data bitrate should state there is no FD phase, "
            f"got {arm.data_rate!r}")


def test_expected_response_names_a_negative_outcome_where_silence_is_possible():
    """Any arm that can plausibly get NO DATA must say so in advance.

    An arm whose only prediction is a positive response cannot distinguish
    'the hypothesis failed' from 'the harness is broken'. This is why the matrix
    can be self-tested against the simulator at all: silence must be a declared
    outcome, not a surprise.

    The arms exempt here are the ones that target something guaranteed to
    answer -- the functional bus-health and gateway arms -- plus the
    configuration-only arms (EXP-4, EXP-13), which send no DID request and so
    cannot produce a data silence.
    """
    config_only = {"EXP-4", "EXP-13"}
    guaranteed = {"EXP-1", "EXP-2", "EXP-3", "EXP-11"}
    for arm in ARMS:
        if arm.exp_id in config_only or arm.exp_id in guaranteed:
            continue
        assert "no data" in arm.expected.lower(), (
            f"{arm.exp_id}: targets a module and so can get NO DATA, but its "
            f"expected response does not mention it: {arm.expected!r}")


def test_all_twenty_six_arms_in_the_brief_are_declared():
    """The matrix from the brief, with nothing dropped.

    EXP-7 and EXP-7b are the session and tester-present pair, which the brief
    lists separately; collapsing them would lose the ability to tell which of the
    two mattered.
    """
    declared = {a.exp_id for a in ARMS}
    required = {f"EXP-{n}" for n in range(1, 17)} | {"EXP-7b"}
    assert required <= declared, f"missing arms: {sorted(required - declared)}"


def test_unrun_helper_counts_only_arms_without_a_result():
    """`unrun()` is what the markdown header reports, so it must not count an
    arm that has run as still pending."""
    assert len(unrun()) == len(ARMS)
    arm = by_id("EXP-1")
    original = arm.actual
    arm.actual = "41 00 BE 3F A8 11"
    try:
        assert len(unrun()) == len(ARMS) - 1
        assert arm not in unrun()
    finally:
        arm.actual = original
    assert len(unrun()) == len(ARMS)


def test_unrun_arms_are_reported_as_not_run_not_as_blank():
    """An experiment nobody performed must not render like a negative result.

    This is the distinction the record exists to preserve: 'we did not try this'
    and 'we tried it and got nothing' lead to opposite next steps.
    """
    markdown = render_markdown()
    assert "not run" in markdown
    assert "**not run**" in markdown
    # Nothing pretends to have a result yet.
    for arm in ARMS:
        assert not arm.ran(), f"{arm.exp_id} claims a result it never produced"


def test_simulator_run_is_labelled_as_such_in_the_record():
    """A harness self-test must not be mistakable for vehicle evidence.

    The record is the evidence the whole investigation rests on. A simulator
    `62` in the same file as a real run, with nothing marking which produced
    it, would make it look as though the BMS had answered -- which is precisely
    the conclusion this work must not reach without a capture.
    """
    sim = render_markdown(preamble="SIMULATOR run")
    assert "SIMULATOR run" in sim
    assert "answers regardless of adapter state" in sim
    assert "shows nothing about this car" in sim
    # An unlabelled render carries no such claim.
    assert "SIMULATOR run" not in render_markdown()


def test_an_arm_that_ran_never_also_says_not_run():
    """A response with a blank interpretation must not render as "not run".

    An arm that ran and recorded `62 02 8C ...` next to
    `Interpretation: **not run**` contradicts itself, and the two readings are
    not equivalent: "we did not try this" and "we tried it and cannot attribute
    the result" call for opposite next steps.
    """
    from tools.experiment_record import Arm, not_run_text

    ran = Arm(exp_id="EXP-T", hypothesis="h", request="r", expected="e",
              config=["c"], can_mode="m", bitrate="b", data_rate="n/a",
              addressing="a", actual="62 02 8C ...")
    assert ran.ran() and not ran.interpret
    assert not_run_text(ran) == "**not determined**"

    pending = Arm(exp_id="EXP-U", hypothesis="h", request="r", expected="e",
                  config=["c"], can_mode="m", bitrate="b", data_rate="n/a",
                  addressing="a")
    assert not_run_text(pending) == "**not run**"


def test_a_filled_arm_stops_reporting_as_not_run():
    """Once an arm has an actual response it must show it, not 'not run'."""
    arm = Arm(exp_id="EXP-T", hypothesis="h", request="r", expected="e",
              config=["c"], can_mode="m", bitrate="b", data_rate="n/a",
              addressing="a", actual="62 02 8C ...")
    assert arm.ran()
    assert "**not run**" not in render_markdown.__doc__


def test_check_catches_an_arm_that_changed_two_things():
    """The single-variable invariant is enforced, not merely intended.

    An arm that differs from the baseline in two ways cannot explain its own
    silence, so this must be reported rather than left for a reader to notice.
    """
    import tools.experiment_record as rec

    original = next(a for a in rec.ARMS if a.exp_id == "EXP-6")
    original.config = original.config + ["ATCF 17FE7"]
    try:
        problems = check()
        assert problems, "a second change must be reported"
        assert "EXP-6" in "\n".join(problems)
    finally:
        original.config = [c for c in original.config if c != "ATCF 17FE7"]


def test_check_catches_a_variant_that_silently_changed_the_bitrate():
    """A wrong bitrate produces the same NO DATA as a wrong address.

    Two arms with identical symptoms and different causes is the specific
    confusion this record exists to prevent.
    """
    import tools.experiment_record as rec

    arm = next(a for a in rec.ARMS if a.exp_id == "EXP-14")
    original = arm.bitrate
    arm.bitrate = "250 kbit/s"
    try:
        problems = check()
        assert problems
        assert "bitrate" in "\n".join(problems)
    finally:
        arm.bitrate = original


def test_single_variant_map_only_names_arms_that_exist():
    """A stale reference in the delta map would silently check nothing."""
    for exp_id in SINGLE_VARIANT_OF_BASELINE:
        by_id(exp_id)  # raises KeyError if absent
    for exp_id in DECLARED_DELTAS:
        by_id(exp_id)


def test_exp7b_is_not_claimed_single_variant_when_it_is_not():
    """The harness sends session + tester-present for EXP-7b, so it changes two
    things. Calling it single-variant would assert something false about it."""
    assert "EXP-7b" not in SINGLE_VARIANT_OF_BASELINE
    assert DECLARED_DELTAS["EXP-7b"] == ["10 01", "3E 00"]
    arm = by_id("EXP-7b")
    base_cfg = set(by_id("EXP-5").config)
    added = [c for c in arm.config if c not in base_cfg]
    assert added == ["10 01", "3E 00"], (
        f"EXP-7b's delta drifted from its declared {DECLARED_DELTAS['EXP-7b']}: "
        f"got {added}")


def test_baseline_config_varies_exactly_as_declared():
    """Every arm's delta from the baseline must match what it claims to test.

    This is the invariant the whole record rests on. An arm that declares it
    tests ATBI but also sets ATCF cannot explain its own silence, and the
    failure is invisible after the run unless it is checked here.
    """
    base_cfg = set(by_id("EXP-5").config)
    for exp_id, delta in DECLARED_DELTAS.items():
        arm = by_id(exp_id)
        added = [c for c in arm.config if c not in base_cfg]
        assert added == delta, (
            f"{exp_id}: config differs from baseline by {added}, "
            f"declared {delta}")


def test_baseline_config_is_inherited_by_meb_arms():
    """MEB arms must address a module, not the functional header.

    An arm that forgot ATSH would send to 7E0 and record NO DATA, which would
    then read as evidence about the BMS rather than about the missing ATSH.
    """
    for arm in ARMS:
        if arm.exp_id in ("EXP-9", "EXP-12"):
            continue  # 11-bit arms with their own addressing, by design
        if arm.exp_id in ("EXP-1", "EXP-2", "EXP-3", "EXP-11"):
            continue  # functional arms, addressing 7E0 by design
        if arm.exp_id == "EXP-4":
            continue  # config-only: proves the adapter accepts the header
        assert any("ATSH" in c for c in arm.config), (
            f"{arm.exp_id} has no ATSH: {arm.config}")
        assert "ATCAF0" in arm.config, f"{arm.exp_id} has no ATCAF0"


def test_11bit_arms_declare_their_response_header():
    """spot2000's 0x710 -> 0x77A and 0x7E5 -> 0x7ED pairs are the evidence.

    The expected source header belongs in the record so that receiving a
    response from the wrong module is visible as a mismatch rather than
    counted as success.
    """
    assert "77A" in by_id("EXP-9").addressing
    assert "7ED" in by_id("EXP-12").addressing
    assert "77A" in by_id("EXP-9").expected
