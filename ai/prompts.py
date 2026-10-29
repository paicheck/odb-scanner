"""Structured diagnostic context builder + LLM prompts.

The LLM receives COMPACT, STRUCTURED evidence (never raw dumps) and a system
prompt that forces the analyst role, confidence levels and explicit
limitations. A post-generation validator (reports.py) enforces hedging.
"""
from __future__ import annotations

import json

SYSTEM_PROMPT = """You are a diagnostic ANALYST for an electric vehicle \
(VW ID.3). You are NOT an autonomous mechanic and you CANNOT send anything \
to the vehicle.

Rules:
- Base every statement on the EVIDENCE provided. Never invent numbers, PIDs, \
or fault codes.
- Distinguish clearly between values REPORTED by the vehicle and values \
CALCULATED or ESTIMATED by the diagnostic software.
- Use confidence levels: High, Medium, Low. Never claim certainty the data \
does not support.
- Always include a LIMITATIONS section stating what CANNOT be determined \
from the available data.
- Rank hypotheses as: 1. most likely, 2. possible, 3. less likely.
- Communication faults (U-codes) do not by themselves demonstrate hardware \
failure.
- Recommend only SAFE, read-only next steps.

Answer in exactly this structure:
VEHICLE DIAGNOSTIC REPORT
OBSERVATION
HISTORICAL TREND
RELATED EVENTS
DTC ANALYSIS
HYPOTHESES
RECOMMENDED NEXT STEP
LIMITATIONS
"""

INTERPRET_PROMPT = """Analyze this vehicle diagnostic evidence and produce \
the report in the required structure.

QUESTION FROM USER: {question}

EVIDENCE (JSON, computed by the diagnostic software - the numbers are \
authoritative, do not recompute or question them):
{context}

Write the report now."""


def build_context(vehicle: dict, overview: dict, dtcs: dict,
                  charging: dict, correlation: dict, anomalies: list,
                  ecus: list | None = None, events: list | None = None,
                  *, quality: dict | None = None,
                  freshness: dict | None = None,
                  provenance: dict | None = None,
                  investigation: dict | None = None,
                  capacity: dict | None = None) -> dict:
    """Assemble the structured evidence packet (compact JSON).

    The optional arguments carry the platform's newer evidence discipline:
    `quality` (which decoders are trusted / excluded), `freshness` (what was
    actually observed and when), `provenance` (live vs imported vs simulated)
    and `investigation` (the staged, persisted fault hunt). They are additive so
    existing callers (and tests) that pass the first seven arguments keep
    working unchanged.
    """
    context = {
        "vehicle": vehicle,
        "battery": {
            "soc_latest_pct": overview.get("soc_latest_pct"),
            "pack_voltage_latest_v": overview.get("pack_voltage_latest_v"),
            "soh_latest_pct": overview.get("soh_latest_pct"),
            "soh_note": overview.get("provenance_note"),
            "cell_delta_trend": overview.get("cell_delta"),
        },
        "dtcs": dtcs,
        "charging": charging,
        "dtc_charging_correlation": correlation,
        "anomalies": anomalies,
        "accessible_ecus": ecus or [],
        "recent_events": events or [],
    }
    if quality is not None:
        context["measurement_quality"] = quality
    if freshness is not None:
        context["observation_freshness"] = freshness
    if provenance is not None:
        context["provenance"] = provenance
    if investigation is not None:
        context["investigation"] = investigation
    if capacity is not None:
        # analysis/health.capacity_trend: the rated-energy trend and, when the
        # fall is significant, today's rate carried to the warranty line.
        context["battery"]["capacity_trend"] = capacity
    return context


def interpret_prompt(context: dict, question: str) -> str:
    return INTERPRET_PROMPT.format(
        question=question,
        context=json.dumps(context, indent=1, default=str),
    )


# Appended to the analyst rules for evidence-citation requests. Kept separate
# from SYSTEM_PROMPT so the base prompt (and its tests) do not move.
CITATION_RULES = """
Evidence discipline (mandatory):
- Cite the exact measurement key and its observed timestamp for every claim you
  make, in the form key@timestamp. Never cite "latest" without its timestamp.
- State the sample size behind every statistic and the limitations of that
  evidence. If there is too little evidence, say so and refuse to conclude.
- Measurements listed under measurement_quality with use_in_scoring=false have a
  known-wrong decoder. You may mention them as findings about the DATA, never as
  findings about the VEHICLE, and never as the basis of a diagnosis.
- Values whose observation_freshness state is not "fresh" are historical. Do not
  describe them as current.
- Report counter-evidence explicitly, and do not treat temporal order (something
  that happened after a warning) as cause.
"""


def investigation_prompt(context: dict, question: str) -> str:
    return interpret_prompt(context, question) + CITATION_RULES


def health_report_question(days: int) -> str:
    return f"Give me a health report for the last {days} days."
