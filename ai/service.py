"""AI analysis service: builds evidence context, calls the AI, validates.

The LLM sees ONLY the structured JSON context built here — never raw data
dumps and never the ability to send commands. All numbers in the context
come from the database/statistics engine, so the LLM interprets rather than
invents. The AI is the local Ollama unless ai.provider names a cloud service
(ai/cloud.py); for a cloud service the context goes with the VIN removed.
"""
from __future__ import annotations

import logging

from ai import prompts, reports
from ai.cloud import llm_from_config, redact
from ai.ollama import AIError
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


class _Unconfigured:
    """Stands in when ai.* is misconfigured: the dashboard still starts, and
    asking says what is wrong."""
    sends_offsite = False
    where = "not configured"
    model = "none"

    def __init__(self, reason: str):
        self.reason = reason

    def generate(self, *a, **kw):
        raise AIError(self.reason)


class AnalysisService:
    def __init__(self, cfg, repo: Repository):
        self.cfg = cfg
        self.repo = repo
        try:
            self.llm = llm_from_config(cfg)
        except AIError as exc:
            log.warning("AI not configured: %s", exc)
            self.llm = _Unconfigured(str(exc))
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
        platform = self._platform_evidence(vehicle_id)
        capacity = None
        if vehicle_id:
            # Like the platform evidence: a failure here never breaks the AI.
            try:
                from analysis.health import capacity_trend
                capacity = capacity_trend(self.repo, vehicle_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("capacity trend left out of the evidence: %s", exc)
        return prompts.build_context(vehicle, overview, dtcs, charging,
                                     correlation, anomalies, ecus,
                                     capacity=capacity, **platform)

    def _platform_evidence(self, vehicle_id: int | None) -> dict:
        """Quality / freshness / provenance summary for the evidence packet.

        The LLM must be told which measurements have a known-wrong decoder and
        which values are historical, otherwise it will happily call a 1681 °C
        PCB temperature the most alarming finding on the car. A failure here
        must never break the AI path, so it degrades to empty on any error.
        """
        if not vehicle_id:
            return {}
        try:
            from analysis import freshness
            from analysis.provenance import key_states
            from analysis.provenance import summary as prov_summary
            from decoders.quality import quality_registry, summary

            profile = self.cfg.get("vehicle.did_profile", "eup")
            qsum = summary(profile)
            qrows = quality_registry(profile)
            excluded = sorted(k for k, q in qrows.items()
                              if not q.use_in_scoring)
            quality = {
                "counts": qsum,
                "excluded_keys": excluded,
                "note": "excluded_keys have a known-wrong decoder and must not "
                        "be used as findings about the vehicle",
            }
            module = freshness.module_status(self.repo, vehicle_id)
            fresh = {
                "modules": [m.as_dict() for m in module],
                "silence": freshness.silence_summary(module),
            }
            from decoders.registry import build_default_registry
            registry = build_default_registry(profile)
            stale_after = float(self.cfg.get("collector.max_value_age_s", 900.0))
            states = key_states(self.repo, vehicle_id, registry,
                                stale_after_s=stale_after)
            provenance = prov_summary(states)
            return {"quality": quality, "freshness": fresh,
                    "provenance": provenance}
        except Exception:  # pragma: no cover - defensive: never break AI
            log.warning("platform evidence unavailable", exc_info=True)
            return {}

    def ask(self, question: str) -> dict:
        """Generate an LLM diagnostic report. Returns dict with report and
        warnings. Raises AIError when the AI is unavailable."""
        # Resolve the vehicle once. The report used to call first_vehicle()
        # again after generate(), which takes minutes on a real model -- so if a
        # second vehicle appeared in that window (the collector registering one,
        # or an import), the report was stored against a different vehicle than
        # the evidence it was generated from.
        vehicle, vehicle_id = self._resolve_vehicle()
        context = self.build_context(question, vehicle)
        if getattr(self.llm, "sends_offsite", False):
            # Leaving the machine: no VIN in what is sent -- or stored as sent.
            context = redact(context, vehicle.get("vin"))
            question = redact(question, vehicle.get("vin"))
        prompt = prompts.interpret_prompt(context, question)
        temperature = self.cfg.get("ollama.temperature", 0.2)
        model, fell_back = self.llm.model, []
        if hasattr(self.llm, "generate_with_source"):
            # A FallbackChain: record the model that actually wrote the report.
            report, answered_by, fell_back = self.llm.generate_with_source(
                prompt, system=prompts.SYSTEM_PROMPT, temperature=temperature)
            model = answered_by.model
        else:
            report = self.llm.generate(prompt, system=prompts.SYSTEM_PROMPT,
                                       temperature=temperature)
        report, warnings = reports.validate_report(report)
        if fell_back:
            warnings = [f"Written by the backup {answered_by.where} ({model}): "
                        + "; ".join(f"{where} failed ({why})"
                                    for where, why in fell_back)] + list(warnings)
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
                                     model, report, context,
                                     warnings)
        return {"question": question, "report": report,
                "warnings": warnings, "context": context,
                "model": model}
