"""AI report pages (local Ollama or a cloud AI). Read-only: the model never
reaches the car.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

import logging
import threading

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ai.ollama import AIError
from ai.service import DEFAULT_QUESTIONS
from web.dashboard import MAX_QUESTION_CHARS

log = logging.getLogger(__name__)


def register(app, ctx) -> None:
    render = ctx.render
    repo = ctx.repo
    svc = ctx.svc
    vehicle_and_id = ctx.vehicle_and_id
    # Every route is a sync handler on AnyIO's shared worker pool (40 threads),
    # and one generation holds its thread for up to ollama.timeout (300 s). A
    # few dozen concurrent asks would occupy the whole pool and hang every
    # page, so generations are capped and the rest are told to retry.
    generations = threading.BoundedSemaphore(
        max(1, int(svc.cfg.get("ollama.max_concurrent", 1))))

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        _, vid = vehicle_and_id()
        reports = repo.llm_reports(vid) if vid else []
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=[dict(r) for r in reports],
            ai_model=svc.llm.model,
            ai_where=svc.llm.where,
            ai_offsite=getattr(svc.llm, "sends_offsite", False),
        )

    @app.post("/ai/ask", response_class=HTMLResponse)
    def ai_ask(request: Request, question: str = Form(...)):
        question = (question or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Question is empty.")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Question is {len(question)} characters; the limit is "
                f"{MAX_QUESTION_CHARS}.",
            )
        if not generations.acquire(blocking=False):
            raise HTTPException(
                status_code=503,
                detail="A report is already being generated; try again when "
                       "it has finished.",
                headers={"Retry-After": "60"})
        try:
            result = svc.ask(question)
            report, warnings, error = result["report"], result["warnings"], None
        except AIError as exc:
            report, warnings, error = "", [], str(exc)
        except Exception as exc:
            # Building the evidence or storing the report failed. The page
            # still renders, with the error, rather than a bare 500.
            log.exception("report generation failed")
            report, warnings, error = "", [], (
                f"Report generation failed ({type(exc).__name__}: {exc}). "
                "See the dashboard log for details.")
        finally:
            generations.release()
        reports_hist = [dict(r) for r in repo.llm_reports(vehicle_and_id()[1])]
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=reports_hist,
            report=report,
            warnings=warnings,
            error=error,
            ai_model=svc.llm.model,
            ai_where=svc.llm.where,
            ai_offsite=getattr(svc.llm, "sends_offsite", False),
        )
