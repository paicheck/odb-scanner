# 02 Capability roadmap

## 1. Vehicle health monitoring and predictive maintenance

**User problem**: Owners cannot easily tell whether the vehicle is healthy or degrading; alerts are reactive.

**Proposed solution**: A vehicle health scorecard combining DTCs, 12 V health, battery capacity trend, cell imbalance, charging performance, and anomaly counts, with trend deltas vs. baseline.

**Existing functionality to reuse**: `analysis/health.py`, `analysis/anomaly.py`, `analysis/battery.py`, `analysis/charging.py`, `analysis/baseline.py`, `analysis/trips.py`, `analysis/data_quality.py`.

**Required modules/interfaces**: `analysis/health.py` (extend), `database/repository.py` (queries), `web/routes/overview.py` or new `health.py`, templates.

**New data requirements**: Existing measurements; optional new read-only DIDs (cell voltages, temperatures) require discovery and documentation; do not assume availability.

**Feasibility**: High for existing metrics; medium for new DIDs (needs read-only discovery).

**Dependencies**: None for core; DIDs require validated discovery.

**Risks**: False positives; presenting inferred as verified.

**Acceptance criteria**: Health page shows score + evidence links; each conclusion labelled VERIFIED/STRONGLY INFERRED/HYPOTHESIS/UNRESOLVED; tests for scoring logic.

**Testing**: Unit tests for scoring; integration with synthetic data.

**Effort**: M (4-8 weeks).

## 2. Battery degradation & usable capacity estimation

**User problem**: Need to know real usable capacity and degradation vs. warranty.

**Proposed solution**: Estimate usable capacity from full-charge sessions (charge added from low to high SOC) and track trend; compare to warranty line.

**Existing functionality to reuse**: `analysis/battery.py` (capacity trend, warranty projection), charging sessions.

**Required modules**: `analysis/battery.py`, `analysis/charging.py`, repository queries.

**New data**: Charge session samples (already stored); temperature; maybe cell-level DIDs (discovery).

**Feasibility**: Medium (needs careful session detection and temperature normalisation).

**Risks**: Confounding by temperature/charging limits; overfitting.

**Acceptance**: Capacity estimate with confidence interval and method note; tests on synthetic sessions.

**Effort**: M.

## 3. Charging intelligence (AC vs DC, curve learning, cost)

**User problem**: Charging times/costs vary; need comparisons and recommendations.

**Proposed solution**: Parse charging sessions, compare AC vs DC, learn actual charging curve per vehicle, compare networks/costs, predict session duration.

**Existing functionality to reuse**: `analysis/charging.py`, `routing/planner.py` charge curve, prices.

**Required modules**: `analysis/charging.py`, `routing/prices.py`, dashboard charging page.

**New data**: Session boundaries (already), charger IDs/locations (optional).

**Feasibility**: Medium.

**Risks**: Incomplete session detection; price data staleness.

**Acceptance**: Session list with AC/DC, kWh, duration, cost, curve plot; tests.

**Effort**: M.

## 4. 12 V battery health and parasitic drain

**User problem**: 12 V failures are common; early warning valuable.

**Proposed solution**: Track resting voltage, voltage sag, recovery, sleep/wake transitions; estimate health and parasitic drain.

**Existing functionality to reuse**: parked 12 V readings, alerts, collector wake voltage.

**Required modules**: `analysis/battery.py` or new `analysis/aux_battery.py`, collector, alerts.

**New data**: More frequent 12 V samples during sleep (read-only); may need adapter awake.

**Feasibility**: Medium (depends on adapter/car behaviour).

**Risks**: False alarms from temperature; adapter power draw.

**Acceptance**: 12 V trend + alert thresholds; documented uncertainty.

**Effort**: S-M.
## 5. Personalised energy model

**User problem**: Flat consumption ignores temperature/speed/elevation/weather.

**Proposed solution**: Learn per-vehicle consumption as function of temperature, speed, elevation, HVAC proxy, road type; store coefficients with confidence and fallback to default.

**Existing functionality to reuse**: `routing/planner.py` (CarModel consumption), trips, measurements.

**Required modules**: `analysis/` new module + `routing/planner.py` (accept model params), shared vector tests.

**New data**: Trip segments with weather (external API), speed, elevation.

**Feasibility**: Medium (needs data volume).

**Risks**: Overfitting; sparse data.

**Acceptance**: Model used in planner with uncertainty; fallback; tests on synthetic trips.

**Effort**: L.

## 6. Journey intelligence

**User problem**: Trips are not automatically detected/classified; no historical route performance.

**Proposed solution**: Detect trips from ignition/SOC/motion, classify (commute/long/etc.), compute efficiency and cost, compare to prediction.

**Existing functionality to reuse**: `analysis/trips.py`, `analysis/timeline.py`.

**Required modules**: `analysis/trips.py` (extend), repository, dashboard trips.

**New data**: Trip boundaries (existing), optional location (privacy).

**Feasibility**: High.

**Risks**: Privacy of location; incorrect boundaries.

**Acceptance**: Trip list with classification and efficiency; tests.

**Effort**: M.

## 7. AI investigation workflow

**User problem**: AI reports are one-shot; no structured investigation.

**Proposed solution**: Investigations with evidence items, stages, counter-evidence, confidence; AI cites measurements; missing evidence identified.

**Existing functionality to reuse**: `analysis/investigation.py`, `investigation_evidence` table, AI service.

**Required modules**: `web/routes/investigations.py`, `ai/`, templates.

**New data**: Evidence refs already.

**Feasibility**: High.

**Risks**: Hallucination; prompt injection from untrusted data.

**Acceptance**: Investigation view with evidence and confidence; tests for citation format.

**Effort**: M.

## 8. Dashboard UX redesign

**User problem**: Information scattered; no clear health overview.

**Proposed solution**: Vehicle-centric overview answering health/change/attention/battery/activity/next steps; customisable panels.

**Existing functionality to reuse**: All routes/templates.

**Required modules**: `web/dashboard.py`, templates, static.

**Feasibility**: Medium (frontend work).

**Risks**: Scope creep.

**Acceptance**: New overview with panels; responsive; tests for rendering.

**Effort**: L.

## 9. Android UX & navigation

**User problem**: Phone app thinner; parity gaps.

**Proposed solution**: Port routing preferences; richer nav screens; offline cache; notifications.

**Existing functionality to reuse**: Android modules.

**Feasibility**: Medium.

**Risks**: Android Auto template limits.

**Acceptance**: Parity tests pass; nav screens functional.

**Effort**: L.

## 10. Security & reliability hardening

**User problem**: Long-running collector and network exposure need robustness.

**Proposed solution**: Reconnect logic, partial response handling, backup verification, config validation, observability, dependency audit, prompt-injection defenses.

**Existing functionality to reuse**: auth, collector, backup.

**Feasibility**: High.

**Risks**: Complexity.

**Acceptance**: Failure-mode tests; security tests.

**Effort**: M.

