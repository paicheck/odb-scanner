"""Report post-processing: enforce evidence-based hedging on LLM output."""
from __future__ import annotations

import re

# Patterns that indicate over-claiming. Each maps to an appended caveat.
OVERCLAIM_PATTERNS: list[tuple[str, str]] = [
    (r"\b(definitely|certainly|undoubtedly|conclusively)\b",
     "The report used definitive language; the data supports hypotheses, "
     "not proof."),
    (r"\b(proves|proven)\b",
     "'Proves' was used; diagnostic data can support or refute hypotheses "
     "but rarely proves causation."),
    # Matched case-insensitively against the report as written. This used to
    # be searched against text.lower() with "BMS" in the pattern, so "the BMS
    # is defective" could never match, and "the high-voltage battery is
    # defective" slipped past a pattern that only knew "the battery".
    (r"\b(the|its|this)\s+(?:[\w-]+\s+){0,3}"
     r"(battery|pack|bms|inverter|motor|charger|obc|converter|gateway|module|"
     r"ecu|cells?|contactor|sensor)\s+(is|has|was|are|have)\s+(been\s+)?"
     r"(defective|failed|failing|broken|faulty|damaged|dead|bad)\b",
     "A hardware-failure claim appeared without noting it is a hypothesis; "
     "confirming hardware failure requires additional evidence."),
    (r"\b(clearly|definitively)\s+(shows?|confirms?|demonstrates?|establishes?)\b",
     "The report presented an inference as established; the data supports "
     "hypotheses, not proof."),
]

TRUNCATION_WARNING = ("The model's output was cut off at its length limit; "
                      "later sections may be missing.")


def _has_section(text: str, name: str) -> bool:
    """A heading line for `name`, as the prompt asks for -- optionally with
    Markdown markup or a colon. A substring test passed reports that merely
    said "no hypotheses ... no limitations" in a sentence."""
    return re.search(rf"^[\s#*_>\-\d.]*{name}\b[\s*_:]*",
                     text, re.IGNORECASE | re.MULTILINE) is not None


def validate_report(text: str, extra: list[str] | tuple = ()) -> tuple[str, list[str]]:
    """Return (amended_report, warnings). Never silently deletes LLM output;
    appends caveats where the language over-claims.

    Every warning -- over-claiming AND missing sections -- goes into the
    AUTOMATIC CAVEATS block of the amended text, because that text is what is
    stored and what the report history shows; missing-section warnings used to
    be added after the block was built and so never appeared there.
    """
    from .ollama import TRUNCATED_MARKER

    warnings: list[str] = []
    for pattern, caveat in OVERCLAIM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE) and caveat not in warnings:
            warnings.append(caveat)
    if TRUNCATED_MARKER in text:
        warnings.append(TRUNCATION_WARNING)
    # Checks made elsewhere (ai/citations.py) land in the same block.
    warnings.extend(w for w in extra if w not in warnings)
    for section in ("LIMITATIONS", "HYPOTHESES"):
        if not _has_section(text, section):
            warnings.append(
                f"Report is missing a {section} section; treat conclusions "
                "as incomplete."
            )
    amended = text
    if warnings:
        amended += "\n\nAUTOMATIC CAVEATS:\n" + "\n".join(
            f"- {w}" for w in warnings
        )
    return amended, warnings
