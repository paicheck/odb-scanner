# 06 Implementation plan

## Phase 0: Critical hardening (P0)

Objectives: ensure reliability/security before new features.
Tasks:
- Collector reconnect + partial response handling.
- Backup verification + restore drill.
- Config validation.
- Security tests (CSRF/host/XSS).
Modules: collector.py, diagnostic/, database/repository.py, web/auth.py, tests.
Dependencies: none.
Tests: failure-mode, security.
Effort: 3-5 weeks.
Risks: regressions in collection.

## Phase 1: Foundational improvements (P1)

Objectives: shared energy model + uncertainty + parity.
Tasks:
- Personalised consumption model (shared Python/Kotlin).
- Uncertainty bands in planner + vectors.
- Android routing parity (preferences) + tests.
Modules: routing/planner.py, routing/car.py, analysis/, android/Planner.kt, tests.
Dependencies: Phase 0.
Tests: differential vectors.
Effort: 6-10 weeks.

## Phase 2: Route guidance and energy intelligence (P1)

Objectives: en-route recalculation, contingency, weather/traffic.
Tasks:
- En-route recalculation skeleton.
- Charger contingency + availability confidence.
- Weather/traffic integration (Open-Meteo, Mapbox).
Modules: routing/, providers, Android nav.
Dependencies: Phase 1.
Tests: scenario simulations.
Effort: 8-12 weeks.

## Phase 3: Dashboard and Android UX (P2)

Objectives: vehicle-centric overview, investigation workflow, charts.
Tasks:
- Overview redesign.
- Investigation workflow + AI citations.
- Chart improvements.
- Android UX screens.
Dependencies: Phase 1 (data).
Effort: 6-10 weeks.

## Phase 4: Advanced diagnostics and AI (P2)

Objectives: health scorecard, battery degradation, charging intelligence, AI tooling.
Tasks:
- Health score.
- Capacity estimation.
- Charging session intelligence.
- Structured AI investigations.
Effort: 8-12 weeks.

## Phase 5: Experimental (P3)

Objectives: predictive maintenance, advanced ML, new DIDs discovery.
Tasks:
- Read-only DID discovery protocol.
- Predictive models (with validation).
Effort: open-ended.

## Parallelisable work

- Phase 0 security tests can run in parallel with collector work.
- Docs/UI can proceed in parallel with Phase 1 model work.
- Android parity can be done by separate agent alongside Python changes if contract agreed first.
