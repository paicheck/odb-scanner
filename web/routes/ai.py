"""Local-LLM report pages. Read-only: the model never reaches the car.

Registered by web/dashboard.py:create_app."""

from __future__ import annotations

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ai.ollama import OllamaError
from ai.service import DEFAULT_QUESTIONS
from web.dashboard import MAX_QUESTION_CHARS


def register(app, ctx) -> None:
    render = ctx.render
    repo = ctx.repo
    svc = ctx.svc
    vehicle_and_id = ctx.vehicle_and_id

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        _, vid = vehicle_and_id()
        reports = repo.llm_reports(vid) if vid else []
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=[dict(r) for r in reports],
            ollama_model=svc.llm.model,
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
        try:
            result = svc.ask(question)
            report, warnings, error = result["report"], result["warnings"], None
        except OllamaError as exc:
            report, warnings, error = "", [], str(exc)
        reports_hist = [dict(r) for r in repo.llm_reports(vehicle_and_id()[1])]
        return render(
            "ai.html",
            request,
            questions=DEFAULT_QUESTIONS,
            reports=reports_hist,
            report=report,
            warnings=warnings,
            error=error,
            ollama_model=svc.llm.model,
        )
