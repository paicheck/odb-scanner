# 01 Executive review

## Overall assessment

`odb_scanner` is a mature, safety-conscious, evidence-first diagnostic and journey-management platform for one specific vehicle (2021 VW ID.3 58 kWh). Its strongest assets are:

- A **strictly read-only** vehicle access model with an allow-list, canonical request reduction, and a `tx_log` safety manifest.
- **Provenance-first data**: raw responses, timestamps, decoding method, provenance (reported/calculated/estimated), freshness and data-quality metadata.
- A **shared Python/Kotlin routing core** held to a common vector file, which is unusual and valuable.
- A **local-first AI** design that keeps data on the machine unless explicitly configured otherwise.
- Performance work already done (hourly summaries, covering index), so the dashboard is usable on real data.

The main gaps are not in data collection but in **intelligence, guidance and product coherence**: the system collects rich evidence but does not yet turn it into a continuous, personalised EV journey and health advisor. Android is also behind the PC on routing preferences and navigation richness. Several reliability/security areas (backup verification, collector reconnection, AI prompt injection, migration rollback, observability) deserve hardening before major feature expansion.

## Current architectural strengths

1. **Safety architecture**: `diagnostic/uds.py` + `DiagnosticConnection._transmit` canonicalisation; no arbitrary payload API; LLM has no adapter path.
2. **Data integrity**: raw responses retained; provenance; summary cache with staleness triggers; WAL + foreign keys.
3. **Shared domain logic**: routing pure functions ported between Python and Kotlin with vector regression.
4. **Provider isolation**: ORS/OCM/Mapbox called server-side; keys never reach browser.
5. **Security posture**: token auth, HttpOnly cookie, SameOrigin CSRF, Host allow-list, DNS rebinding protection, CSP.
6. **Performance**: hourly/minute summaries, covering index, linear-time queries.

## Major limitations

- **Single-vehicle, single-user design**: schema and UX assume one car; no multi-vehicle or multi-user model.
- **Routing is static**: planned once from provided charger list; no en-route recalculation using actual SOC/consumption/weather.
- **Android parity lag**: Kotlin planner lacks newer preference fields and vector-tested behaviour; navigation UX thinner than web.
- **Limited historical learning**: consumption model is flat; no personalised segment/weather/temperature models.
- **AI is report-oriented**: no structured tool use, evidence retrieval, or confidence calibration beyond prompts.
- **Backup/restore not fully verified**: backups are created and mirrored but verification and restore drills are not first-class.
- **Observability is light**: logging exists but no metrics/tracing/health endpoints for long-running collector.
- **Migration safety**: SCHEMA_VERSION stays 1 with additive tables; rollback/versioning not explicit.

## Most valuable opportunities

1. **Personalised energy model + arrival SOC uncertainty** (improves routing, range, charging, journey analysis).
2. **En-route recalculation and charging contingency** (turns planner into a real journey assistant).
3. **Vehicle health timeline + predictive maintenance** (leverages existing measurements/anomalies).
4. **Investigation workflow + evidence-grounded AI** (ties alerts → evidence → AI → report).
5. **Hardening & parity** (Android routing, backup verification, collector resilience, security tests).

## Five highest-priority recommendations

1. **P0: Collector resilience & data-integrity hardening** (Bluetooth reconnect, partial responses, transactional writes, backup verification).
2. **P1: Personalised consumption/arrival-SOC model with uncertainty** (shared Python/Kotlin, vector-tested).
3. **P1: Android routing parity + richer navigation screens** (close the gap; enable en-route use).
4. **P1: Investigation workflow + evidence retrieval for AI** (structured, cited, confidence-labelled).
5. **P2: Charging session intelligence** (AC vs DC, curve learning, cost comparison, reliability).

## Biggest technical risks

- **Vehicle safety regression** if allow-list or transmit path is weakened.
- **Android parity drift** as Python planner evolves faster than Kotlin.
- **Overfitting/overconfidence** in energy predictions presented as guarantees.
- **Privacy leakage** via cloud AI or map tiles if not carefully scoped.
- **Database growth/performance** as years of high-frequency readings accumulate.
- **Bluetooth/adapter fragility** in real-world collection.
- **Flaky tests** (e.g. `test_trip_log_totals_and_what_the_cold_costs`) masking regressions.

## Evidence basis

This review is based on repository inspection (source, tests, docs) and safe test execution (1,512 Python tests passing; ruff clean). External service capabilities are noted as assumptions where not verified in-repo.
