# 07 Agent backlog

## TASK-001: Collector reconnect and partial-response handling
- Objective: Make collection robust to Bluetooth drops and malformed responses.
- Inspect: `collector.py`, `diagnostic/elm327.py`, `diagnostic/connection.py`.
- Change: reconnect with backoff; validate partial frames; log gaps.
- Constraints: read-only; no vehicle writes.
- Tests: simulated disconnect; malformed response fuzz.
- Acceptance: collection resumes after drop; gaps recorded.
- Dependencies: none.
- Done: tests pass; docs updated.

## TASK-002: Backup verification and restore drill
- Objective: Verify backups integrity and restore procedure.
- Inspect: `main.py` backup, repository.
- Change: add verify command; restore test harness (non-destructive).
- Tests: backup/restore round-trip on temp DB.
- Acceptance: verified backup; documented restore.
- Dependencies: none.

## TASK-003: Config validation
- Objective: Validate config at startup; fail with clear messages.
- Inspect: `config.py`, `config.yaml`.
- Change: schema validation; defaults.
- Tests: invalid config cases.
- Acceptance: clear errors; no crash.
- Dependencies: none.

## TASK-004: Security test suite
- Objective: Add tests for CSRF, host validation, XSS escaping, token handling.
- Inspect: `web/auth.py`, templates.
- Change: tests only (no weakening).
- Acceptance: tests pass; no new vulns.

## TASK-005: Personalised consumption model (shared)
- Objective: Learn consumption coefficients; use in planner with uncertainty.
- Inspect: `routing/planner.py`, `routing/car.py`, `analysis/trips.py`.
- Change: model module; planner params; vector updates.
- Tests: synthetic trips; differential vectors.
- Acceptance: fallback when insufficient data; uncertainty output.
- Dependencies: TASK-001 optional.

## TASK-006: Android routing parity
- Objective: Port preference fields and new vector behaviour to Kotlin.
- Inspect: `android/.../Planner.kt`, `PlannerTest.kt`, `route_vectors.json`.
- Change: data classes + logic; tests.
- Acceptance: all vectors pass.
- Dependencies: vector file stable.

## TASK-007: En-route recalculation skeleton
- Objective: Compare actual SOC to plan; classify deviation; suggest alternative.
- Inspect: routing + Android nav.
- Change: recalc module; UI hooks.
- Tests: scenario simulations.
- Acceptance: deviation classification + recommendation.

## TASK-008: Charger contingency handling
- Objective: Alternative chargers + risk assessment.
- Inspect: planner candidates/alternatives.
- Change: contingency selection; availability confidence field.
- Tests: vector cases.
- Acceptance: alternatives surfaced; risk labels.

## TASK-009: Investigation workflow + AI citations
- Objective: Structured investigations with evidence and cited AI.
- Inspect: `analysis/investigation.py`, `web/routes/investigations.py`, `ai/`.
- Change: evidence stages; citation validation.
- Tests: citation format; missing evidence.
- Acceptance: investigation view with confidence.

## TASK-010: Vehicle health scorecard
- Objective: Combine metrics into health score with evidence.
- Inspect: health/anomaly/battery analysis.
- Change: scoring module + dashboard.
- Tests: scoring logic.
- Acceptance: score + labels + evidence links.

## TASK-011: Chart redesign
- Objective: Units, scales, anomalies, time range, zoom.
- Inspect: templates/static, chart_semantics.
- Change: frontend.
- Tests: rendering smoke.
- Acceptance: no misleading scales.

## TASK-012: Weather integration (read-only external)
- Objective: Use weather in consumption and routing.
- Inspect: providers, planner.
- Change: weather provider; model inputs.
- Tests: mocked provider.
- Acceptance: weather affects predictions with uncertainty.
- Dependencies: TASK-005.
