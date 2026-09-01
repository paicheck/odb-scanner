"""AI analysis service: builds evidence context, calls Ollama, validates.

The LLM sees ONLY the structured JSON context built here — never raw data
dumps and never the ability to send commands. All numbers in the context
come from the database/statistics engine, so the LLM interprets rather than
invents.
"""
from __future__ import annotations

import json
import logging

from ai import prompts, reports
from ai.ollama import OllamaClient, OllamaError
from analysis import battery as battery_analysis
from analysis import charging as charging_analysis
from analysis import dtc as dtc_analysis
from database.repository import Repository, utcnow

log = logging.getLogger(__name__)

DEFAULT_QUESTIONS = [
    "Have my battery cell voltage differences been getting worse?",
    "What happened before the last electric drive warning?",
    "Are my DTCs related?",
    "Compare my AC charging events with fault occurrences.",
    "Give me a health report for the last 30 days.",
]


class AnalysisService:
    def __init__(self, cfg, repo: Repository):
        self.cfg = cfg
        self.repo = repo
        self.llm = OllamaClient(cfg.ollama_host, cfg.ollama_model,
                                cfg.ollama_timeout)
        self.window_days = int(cfg.get("analysis.trend_window_days", 30))

    def build_context(self, question: str) -> dict:
        days = int(self.cfg.get("analysis.trend_window_days", 30))
        vehicle_row = self.repo.conn.execute(
            "SELECT * FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vehicle = dict(vehicle_row) if vehicle_row else {}
        vehicle_id = vehicle.get("id")
        overview = battery_analysis.battery_overview(self.repo, days, vehicle_id)
        dtcs = dtc_analysis.dtc_summary(self.repo.dtc_list(vehicle_id))
        charging = charging_analysis.session_comparison(self.repo, vehicle_id, days)
        correlation = charging_analysis.correlate_dtc_with_sessions(
            self.repo, vehicle_id, days)
        anomalies = [dict(a) for a in self.repo.anomalies(vehicle_id, days)]
        ecus = [dict(e) for e in self.repo.list_ecus(vehicle_id)]
        return prompts.build_context(vehicle, overview, dtcs, charging,
                                     correlation, anomalies, ecus)

    def ask(self, question: str) -> dict:
        """Generate an LLM diagnostic report. Returns dict with report and
        warnings. Raises OllamaError when Ollama is unavailable."""
        context = self.build_context(question)
        prompt = prompts.interpret_prompt(context, question)
        report = self.llm.generate(prompt, system=prompts.SYSTEM_PROMPT,
                                   temperature=self.cfg.get("ollama.temperature", 0.2))
        report, warnings = reports.validate_report(report)
        vehicle_row = self.repo.conn.execute(
            "SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
        self.repo.add_llm_report(vehicle_row["id"], question,
                                 self.llm.model, report, context, warnings)
        return {"question": question, "report": report,
                "warnings": warnings, "context": context,
                "model": self.llm.model}
