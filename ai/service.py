"""AI analysis service: builds evidence context, calls Ollama, validates.

The LLM sees ONLY the structured JSON context built here — never raw data
dumps and never the ability to send commands. All numbers in the context
come from the database/statistics engine, so the LLM interprets rather than
invents.
"""
from __future__ import annotations

import logging

from ai import prompts, reports
from ai.ollama import OllamaClient
from analysis import battery as battery_analysis
from analysis import charging as charging_analysis
from analysis import dtc as dtc_analysis
from database.repository import Repository

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

    def _resolve_vehicle(self) -> tuple[dict, int | None]:
        """The vehicle a question is about, and its id. Resolved once per ask."""
        row = self.repo.first_vehicle()
        vehicle = dict(row) if row else {}
        return vehicle, vehicle.get("id")

    def build_context(self, question: str,
                      vehicle: dict | None = None) -> dict:
        """Assemble the evidence packet for `question`.

        `vehicle` may be passed to reuse a resolution the caller has already
        made, so the packet and whatever is later stored alongside it are built
        from the same one.
        """
        days = int(self.cfg.get("analysis.trend_window_days", 30))
        if vehicle is None:
            vehicle, vehicle_id = self._resolve_vehicle()
        else:
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
        # Resolve the vehicle once. The report used to call first_vehicle()
        # again after generate(), which takes minutes on a real model -- so if a
        # second vehicle appeared in that window (the collector registering one,
        # or an import), the report was stored against a different vehicle than
        # the evidence it was generated from.
        vehicle, vehicle_id = self._resolve_vehicle()
        context = self.build_context(question, vehicle)
        prompt = prompts.interpret_prompt(context, question)
        report = self.llm.generate(prompt, system=prompts.SYSTEM_PROMPT,
                                   temperature=self.cfg.get("ollama.temperature", 0.2))
        report, warnings = reports.validate_report(report)
        if vehicle_id is None:
            # llm_reports.vehicle_id is NOT NULL REFERENCES vehicles(id), and an
            # empty database is reachable (fresh install, or a wiped DB). There
            # is nothing meaningful to attach a report to, and generating one
            # from an empty evidence packet is not worth persisting -- so return
            # it to the caller without storing, rather than raising TypeError
            # and turning POST /ai/ask into a 500.
            log.warning("not persisting LLM report: no vehicle in the database")
            warnings = list(warnings) + [
                "NOT PERSISTED: the database contains no vehicle, so this "
                "report could not be stored."]
        else:
            self.repo.add_llm_report(vehicle_id, question,
                                     self.llm.model, report, context,
                                     warnings)
        return {"question": question, "report": report,
                "warnings": warnings, "context": context,
                "model": self.llm.model}
