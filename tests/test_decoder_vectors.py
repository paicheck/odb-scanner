"""Every MEB decoder must reproduce its captured value, and the validator must
be capable of noticing when one does not.

The failure these guard against is not a crash. It is a formula that silently
returns a plausible-looking number that is wrong -- which is exactly what a
0x06CD-instead-of-0x06BD transcription looks like: the note reads fine, the
page renders fine, and the voltage is off by 4 V.

`check` is therefore itself tested with an injected failure, so "the validator
passes" cannot be vacuously true because the comparison silently stopped
happening.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "fixtures"))

from meb_real_car_vectors import CONTESTED, VECTORS  # noqa: E402
from tools.validate_decoders import build_rows, check  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    return build_rows("meb")


def test_every_vector_reproduces_its_expected_value(rows):
    """The table itself: no decoder may disagree with real-car evidence."""
    problems = check(rows)
    assert not problems, "\n".join(problems)


def test_check_detects_a_wrong_decoder():
    """The validator must fail on disagreement, not merely report it.

    A tolerance check that can only ever pass is worse than no check, because it
    is read as verification. Injected here so the failure path is exercised on
    every run rather than only when a decoder is already broken.
    """
    good = [{"key": "pack_voltage", "did": "0x1E3B", "raw": "06BD",
             "decoded": 431.25, "carscanner": 431.25, "abs_err": 0.0,
             "pct_err": 0.0, "in_registry": True}]
    assert check(good) == []

    wrong = [dict(good[0], decoded=435.25,
                  abs_err=4.0, pct_err=abs(4.0 / 431.25 * 100))]
    problems = check(wrong)
    assert problems, "a 4 V / 0.93% error must be reported"
    assert "pack_voltage" in problems[0]


def test_check_detects_a_key_missing_from_the_registry():
    """A vector naming an undefined key validates nothing and must say so.

    Otherwise renaming a DID key in the registry would silently drop coverage:
    the fixture would keep its `captured` confidence and no test would fail.
    """
    orphan = [{"key": "typo_key", "did": "-", "raw": "00", "decoded": None,
               "carscanner": 0.0, "abs_err": None, "pct_err": None,
               "in_registry": False}]
    problems = check(orphan)
    assert problems
    assert "not in the registry" in problems[0]


def test_no_derived_vector_is_presented_as_captured():
    """`derived` vectors must not be self-consistent evidence.

    A vector whose raw bytes were reconstructed from the formula it is meant to
    verify cannot fail -- it encodes the answer. These are regression guards
    against drift and nothing more, and the label is the only thing that keeps
    them from being read as validation.
    """
    derived = [v for v in VECTORS if v[4] == "derived"]
    assert derived, "expected some vectors with no real capture"
    for key, _raw, _expected, evidence, _conf in derived:
        assert "CS log" not in evidence, (
            f"{key} is labelled derived but its evidence cites a capture")


def test_every_contested_decoder_is_flagged_not_silently_resolved():
    """The two known disagreements must reach the table as contested.

    pack_current and hv_energy_content are the two places where the sources
    contradict us. Resolving either by fiat would be the wrong fix; the honest
    outcome is that the table says so every time it is regenerated.
    """
    by_key = {r["key"]: r for r in build_rows("meb")}
    for key in CONTESTED:
        assert key in by_key, f"{key} has a note but no vector"
        assert by_key[key]["contested"], f"{key} is contested but not flagged"


def test_contested_notes_name_the_disagreement_not_just_our_answer():
    """A contested entry has to say who disagrees and what follows if we're
    wrong, or the flag reads as a footnote."""
    for key, note in CONTESTED.items():
        assert len(note) > 80, f"{key}'s contested note is too thin to be useful"
        lowered = note.lower()
        assert any(w in lowered for w in ("disagree", "contradict", "wrong",
                                          "hypothesis", "contested")), \
            f"{key}'s note does not state the nature of the disagreement"


def test_confidence_values_are_from_the_declared_set():
    """An unrecognised confidence label would render as a blank category."""
    allowed = {"captured", "derived", "contested"}
    for key, _raw, _expected, _evidence, conf in VECTORS:
        assert conf in allowed, f"{key} has unknown confidence {conf!r}"


def test_captured_dominates_so_the_table_is_not_mostly_trust():
    """Most decoders should rest on real captures, not on sources we trust.

    If this drifts, the honest response is to go and make captures, not to
    relax the ratio.
    """
    captured = sum(1 for v in VECTORS if v[4] == "captured")
    assert captured / len(VECTORS) >= 0.5, (
        f"only {captured}/{len(VECTORS)} vectors rest on a real capture")
