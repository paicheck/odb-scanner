"""DTC classification and description.

Categories: informational | communication | intermittent | historical |
active | potentially critical.

Basis:
  * System letter (P/C/B/U) per SAE J2012 [documented]
  * VW 'U1xxx' data-bus codes are communication faults; a missing message
    on a data bus does NOT by itself demonstrate hardware failure [documented]
  * P0A7x/P0A8x hybrid-battery system codes are treated as potentially
    critical [documented SAE J2012-DA range: hybrid/EV battery system]
  * Anything else defaults to 'historical/stored' pending evidence.

Descriptions come from two layers: a small hand-maintained table of VW/VAG
codes (below), then the Car Scanner DTC databases (analysis/
dtc_descriptions.json, 6 481 standard codes built by tools/build_dtc_lookup.py
from Car Scanner's embedded dtcdb.OBDII.db / dtcdb.VAG.db).

DO NOT treat every DTC as a serious fault — classification feeds both the
dashboard and the LLM context.
"""
from __future__ import annotations

import json
import os

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
    # Codes arrive from the adapter and the database, so an empty or truncated
    # string is reachable; code[:1] on "" would raise IndexError and take the
    # whole DTC page down.
    if not code or not isinstance(code, str):
        return ["unknown"]
    cats: list[str] = []
    known = KNOWN_VAG_CODES.get(code)
    if known:
        cats.extend(known[1])
        # NOTE: this used to `return cats` here, so a known VW code was never
        # classified by its status byte. The lamp-driving bits below therefore
        # did not apply to exactly the codes this car is most likely to emit.
    else:
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
        #   bit0 testFailed                 (active-ish)
        #   bit2 pendingDTC
        #   bit3 confirmedDTC
        #   bit7 warningIndicatorRequested   <- the bit the dashboard lamp reads
        active = status_byte & 0x01
        pending = status_byte & 0x04
        confirmed = status_byte & 0x08
        warning = status_byte & 0x80
        if warning:
            # This is the closest the vehicle will ever come to saying "the
            # warning lamp is on": the ECU is asking for the indicator. It is
            # categorised first and separately because a lamp can be requested
            # for a fault that is neither testFailed nor confirmed yet.
            cats.append("warning-lamp")
        if active:
            cats.append("active")
        elif confirmed:
            cats.append("historical")
        else:
            cats.append("intermittent")
        if pending and not confirmed:
            cats.append("pending")
    else:
        cats.append("historical")
    return cats


def status_label(status_byte: int | None) -> str:
    """One short word for a DTC's ISO 14229 status byte.

    Ordered so the most alerting bit wins: a warning indicator outranks an
    active test failure, which outranks a pending or confirmed code. The
    collector stores this in `dtcs.status` so the dashboard can say plainly
    whether a stored code is currently lighting the dash.
    """
    if status_byte is None:
        return "unknown"
    if status_byte & 0x80:
        return "warning"
    if status_byte & 0x01:
        return "active"
    if status_byte & 0x04:
        return "pending"
    if status_byte & 0x08:
        return "confirmed"
    return "stored"


def describe(code: str) -> tuple[str, bool]:
    """Return (description, is_documented).

    Resolution order:

      1. The hand-maintained KNOWN_VAG_CODES table (exact 6-char UDS match,
         then the 5-char base) -- these carry the VW data-bus interpretation
         that the generic SAE wording lacks.
      2. The Car Scanner DTC databases (analysis/dtc_descriptions.json,
         built by tools/build_dtc_lookup.py from Car Scanner's embedded
         dtcdb.OBDII.db / dtcdb.VAG.db), keyed by the 5-char base code.
      3. A documented "no description" fallback.
    """
    code = (code or "").upper()
    for key in (code, code[:5] if len(code) >= 5 else code):
        known = KNOWN_VAG_CODES.get(key)
        if known:
            return known[0], True
    base = code[:5] if len(code) >= 5 else code
    if base:
        desc = _lookup().get("std", {}).get(base)
        if desc:
            return desc, True
    return "No public description available (code recorded, raw data preserved)", False


_LOOKUP: dict | None = None


def _lookup() -> dict:
    """Load the DTC description JSON once, caching it for the process.

    The file is a data artefact generated by tools/build_dtc_lookup.py; if it
    is missing (e.g. a stripped checkout) the lookup degrades to the
    hand-maintained table only.
    """
    global _LOOKUP
    if _LOOKUP is None:
        path = os.path.join(os.path.dirname(__file__), "dtc_descriptions.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _LOOKUP = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            _LOOKUP = {"std": {}, "vag5": {}}
    return _LOOKUP


def dtc_summary(dtc_rows, freeze_frames: dict | None = None) -> dict:
    """Aggregate a repo.dtc_list() result for dashboard/LLM.

    `freeze_frames` is an optional repo.latest_freeze_frames() mapping
    {(ecu, code): freeze-frame dict}; when present, each code's entry
    carries the latest snapshot records (flattened) so the dashboard and
    the LLM context can show what the ECU recorded at fault time.
    """
    out = {"total": len(dtc_rows), "by_category": {}, "codes": []}
    for r in dtc_rows:
        cats = json.loads(r["categories"]) if isinstance(r["categories"], str) \
            else list(r["categories"])
        for c in cats:
            out["by_category"][c] = out["by_category"].get(c, 0) + 1
        entry = {
            "ecu": r["ecu"], "code": r["code"],
            "description": r["description"], "categories": cats,
            "first_seen": r["first_seen"], "last_seen": r["last_seen"],
            "count": r["occurrence_count"],
        }
        # The row id is the durable link to the per-code drill-down page; older
        # callers that pass a narrow row (no id) simply render without a link.
        if "id" in r.keys():
            entry["id"] = r["id"]
        # `status` is not always selected (older callers pass a narrow row), so
        # it is added only when present rather than assumed.
        if "status" in r.keys():
            entry["status"] = r["status"]
        ff = (freeze_frames or {}).get((r["ecu"], r["code"]))
        if ff:
            entry["status_byte"] = ff.get("status_byte")
            entry["snapshot_records"] = [
                rec for e in ff.get("snapshot") or []
                for rec in e.get("records") or []
            ]
        out["codes"].append(entry)
    return out


def warning_lamp_requested(status_byte: int | None) -> bool:
    """True when the ECU is asking the dashboard to light the warning lamp.

    ISO 14229 DTC status byte bit 7 (`warningIndicatorRequested`) is the bit the
    instrument-cluster lamp reads. It is the closest this project can get to
    "the dash warning is on": no DID in the Car Scanner MEB profile, the
    spot2000 list, or the ABRP JSON exposes the lamp/tell-tale state directly
    (see research/measurement_discovery/unresolved_questions.md).
    """
    return bool(status_byte) and bool(status_byte & 0x80)


def detect_warning_transitions(series: list[dict]) -> list[dict]:
    """Warning-lamp on/off edges from a DTC's status-byte history.

    `series` is a list of rows, each with at least ``ecu``, ``code``, ``ts`` and
    ``status_byte`` (``None`` = unknown), in any order. Rows are grouped by
    ``(ecu, code)`` and walked in time order; a flip of bit 7 (warningIndicator
    Requested) between two consecutive *known* observations of the same DTC is
    reported as ``warning-on`` or ``warning-off``.

    Returns a list of ``{ts, ecu, code, event}`` with ``event`` in
    ``{"warning-on", "warning-off"}``, ordered by time. Two boundaries are
    deliberately NOT reported here, because presence-only bytes cannot show
    them:

    * a DTC first seen already requesting the lamp (it was on before we looked);
    * a DTC that disappears from the read entirely (that is the collector's
      absence signal, surfaced separately as ``dtc-cleared``).
    """
    by_dtc: dict[tuple[str, str], list[dict]] = {}
    for row in series:
        by_dtc.setdefault((row["ecu"], row["code"]), []).append(row)
    events: list[dict] = []
    for (ecu, code), rows in by_dtc.items():
        rows.sort(key=lambda r: (r.get("ts") or "", r.get("ecu", "")))
        prev: int | None = None
        for r in rows:
            cur = r.get("status_byte")
            if prev is not None and cur is not None:
                was_on = bool(prev & 0x80)
                now_on = bool(cur & 0x80)
                if was_on and not now_on:
                    events.append({"ts": r["ts"], "ecu": ecu, "code": code,
                                   "event": "warning-off"})
                elif not was_on and now_on:
                    events.append({"ts": r["ts"], "ecu": ecu, "code": code,
                                   "event": "warning-on"})
            if cur is not None:
                prev = cur
    events.sort(key=lambda e: (e["ts"] or "", e["ecu"], e["code"]))
    return events
