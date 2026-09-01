"""DTC classification.

Categories: informational | communication | intermittent | historical |
active | potentially critical.

Basis:
  * System letter (P/C/B/U) per SAE J2012 [documented]
  * VW 'U1xxx' data-bus codes are communication faults; a missing message
    on a data bus does NOT by itself demonstrate hardware failure [documented]
  * P0A7x/P0A8x hybrid-battery system codes are treated as potentially
    critical [documented SAE J2012-DA range: hybrid/EV battery system]
  * Anything else defaults to 'historical/stored' pending evidence.

DO NOT treat every DTC as a serious fault — classification feeds both the
dashboard and the LLM context.
"""
from __future__ import annotations

import json

# Small table of publicly documented VW/VAG codes. Extend as codes are
# observed; unknown codes remain classified by letter only.
KNOWN_VAG_CODES: dict[str, tuple[str, list[str]]] = {
    "U112300": ("Data bus: received invalid data / missing message",
                ["communication", "informational"]),
    "U111300": ("Data bus: missing message from a control unit",
                ["communication", "informational"]),
    "U112100": ("Data bus: missing message (limit position of function)",
                ["communication", "informational"]),
    "P0A8000": ("Hybrid/EV battery system deterioration",
                ["potentially critical", "powertrain"]),
    "P0A7F00": ("Hybrid/EV battery pack deterioration",
                ["potentially critical", "powertrain"]),
    "P0AA100": ("Hybrid/EV battery voltage system isolation fault",
                ["potentially critical", "powertrain"]),
}


def classify_dtc(code: str, status_byte: int | None = None) -> list[str]:
    cats: list[str] = []
    known = KNOWN_VAG_CODES.get(code)
    if known:
        cats.extend(known[1])
        return cats
    letter = code[:1].upper()
    if letter == "U":
        cats.extend(["communication", "informational"])
    elif letter == "P":
        cats.append("powertrain")
        if code.startswith(("P0A7", "P0A8", "P0AA")):
            cats.append("potentially critical")
    elif letter == "C":
        cats.append("chassis")
    elif letter == "B":
        cats.append("body")
    else:
        cats.append("unknown")
    if status_byte is not None:
        # ISO 14229 DTC status byte bits [documented]:
        # bit2=0 pendingDTC, bit3=0 confirmedDTC, bit0 testFailed (active-ish)
        active = status_byte & 0x01
        confirmed = status_byte & 0x08
        if active:
            cats.append("active")
        elif confirmed:
            cats.append("historical")
        else:
            cats.append("intermittent")
    else:
        cats.append("historical")
    return cats


def describe(code: str) -> tuple[str, bool]:
    """Return (description, is_documented)."""
    known = KNOWN_VAG_CODES.get(code)
    if known:
        return known[0], True
    return "No public description available (code recorded, raw data preserved)", False


def dtc_summary(dtc_rows) -> dict:
    """Aggregate a repo.dtc_list() result for dashboard/LLM."""
    out = {"total": len(dtc_rows), "by_category": {}, "codes": []}
    for r in dtc_rows:
        cats = json.loads(r["categories"]) if isinstance(r["categories"], str) \
            else list(r["categories"])
        for c in cats:
            out["by_category"][c] = out["by_category"].get(c, 0) + 1
        out["codes"].append({
            "ecu": r["ecu"], "code": r["code"],
            "description": r["description"], "categories": cats,
            "first_seen": r["first_seen"], "last_seen": r["last_seen"],
            "count": r["occurrence_count"],
        })
    return out
