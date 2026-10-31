# Overnight backlog — 2026-10-31

Branch `overnight-2026-10-31`, from `charger-preferences` at `8fc50cc` (which
holds the unpushed `ai-fallback` work and the previous session's commits).
Nothing is pushed: pushing needs the owner's OK (CLAUDE.md).

Baseline at 01:06: 1,524 Python tests pass; ruff **not** clean (2 unused
imports in `tests/test_health_summary.py`); Android JVM tests not yet run on
this branch.

Status: `done` · `in progress` · `blocked` · `pending` · `dropped`

| ID | Priority | Task | Status |
|---|---|---|---|
| N01 | 1 defect | Health summary counts fault codes from a table/column that does not exist (`diagnostic_events.resolved`), so it always reports 0; 12 V reading has no freshness/provenance; trivial test | done `9bed741` |
| N02 | 1 defect | Planner: per-stretch climb fed into the model's elevation term as well as the potential energy (double count); `_model_rate` swallows every exception; model computed on every Route load but never used | done `84af237` (unused model on Route load → N04) |
| N03 | 2 blocking | Android parity (TASK-006): Kotlin planner ignores the charger-choice settings now in the shared vectors, so PlannerTest likely fails | done `a068f3a` |
| N04 | 3/4 | Consumption model: overfitting (3 drives for 5 coefficients), weighted fit vs unweighted spread, extrapolation, all-or-nothing features; chronological journey-level backtest vs the flat model | done `fd90d89` |
| N05 | 5 | Weather in the route energy model (wind along each stretch, temperature through the model, no double count; network outside pure functions; caching) | done `c951da2` |
| N06 | 4 | Arrival SoC with a confidence interval | done `2837dba` |
| N07 | 3 | Digital twin foundation: vehicle state with observed/inferred/stale distinctions, as-of reconstruction, prediction vs observation | done `29c4d39` |
| N08 | 6 | En-route assessment: projection from the consumption ratio, levels, alternatives; align with the phone's TripMonitor/Replan | done `7d22acc` |
| N09 | 8 | Dashboard: health card and twin state on the overview | done `67303d4` |
| N10 | 7 | Investigations: evidence citations the AI cannot invent | done `9762ae5` |
| N11 | — | Handover: `review_output/overnight_handover.md` | done |
| N12 | — | Intermittent `test_trips` failure: diagnosed (test clock) and fixed | done `6b53b4f` |
| N13 | — | Hardening: decoder fuzz; evidence-is-data rule | done `8b762b9`, `1706d24` |
| N14 | — | Phone parity: wind, arrival ranges, Open-Meteo fetch, shared vectors | done `553cf61` |
| N15 | next | Phone en-route levels: port `analysis/enroute.assess` to Kotlin with shared vectors | pending |
| N16 | owner | Prediction ledger (needs a new table: schema change for the owner to approve) | blocked |
