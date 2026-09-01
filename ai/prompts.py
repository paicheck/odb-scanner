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
                  ecus: list | None = None, events: list | None = None) -> dict:
    """Assemble the structured evidence packet (compact JSON)."""
    return {
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


def interpret_prompt(context: dict, question: str) -> str:
    return INTERPRET_PROMPT.format(
        question=question,
        context=json.dumps(context, indent=1, default=str),
    )


def health_report_question(days: int) -> str:
    return f"Give me a health report for the last {days} days."
