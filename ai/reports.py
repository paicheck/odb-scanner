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
    (r"\bthe (battery|BMS|inverter|motor) (is|has) (defective|failed|broken)\b",
     "A hardware-failure claim appeared without noting it is a hypothesis; "
     "confirming hardware failure requires additional evidence."),
]


def validate_report(text: str) -> tuple[str, list[str]]:
    """Return (amended_report, warnings). Never silently deletes LLM output;
    appends caveats where the language over-claims."""
    warnings: list[str] = []
    lowered = text.lower()
    for pattern, caveat in OVERCLAIM_PATTERNS:
        if re.search(pattern, lowered):
            warnings.append(caveat)
    amended = text
    if warnings:
        amended += "\n\nAUTOMATIC CAVEATS:\n" + "\n".join(
            f"- {w}" for w in warnings
        )
    for section in ("LIMITATIONS", "HYPOTHESES"):
        if section not in text.upper():
            warnings.append(
                f"Report is missing a {section} section; treat conclusions "
                "as incomplete."
            )
    return amended, warnings
