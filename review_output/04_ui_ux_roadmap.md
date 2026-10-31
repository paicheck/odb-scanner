# 04 UI/UX redesign

## Web information architecture

Proposed top-level navigation:
- Overview (vehicle health + recent activity)
- Measurements (explorer + charts)
- Trips & journeys
- Battery & charging
- Route planner
- Diagnostics (DTCs, ECUs, investigations)
- AI analysis
- Settings

## Proposed dashboard structure

Vehicle-centric overview answers:
1. Is the vehicle healthy?
2. Has anything changed?
3. Anything requiring attention?
4. Battery performance?
5. What has it been doing?
6. What to investigate next?

Panels: health score, alerts, battery capacity trend, 12 V, recent trips, data freshness, last collection.

## Page-by-page improvements

- Overview: health card, alerts list, battery trend mini-chart, recent trips, freshness.
- Measurements: filter by key/ECU, chart with units, anomalies highlighted, time range.
- Trips: list with classification, efficiency, cost, map.
- Battery & charging: capacity trend, charging sessions AC/DC, curve, cost comparison.
- Route: comparison view, scenarios, alternatives, contingency.
- Diagnostics: DTCs with descriptions, investigation workflow.
- AI: evidence-grounded chat/report with citations.

## Android navigation structure

- Home: vehicle health summary + quick actions.
- Battery/charging screen.
- Trips.
- Route planning + nav.
- Diagnostics summary.
- Settings.

Avoid full desktop dashboard on phone; prioritise driving-relevant info.

## Key user workflows

- Investigate alert: alert → evidence → history → baseline → AI → report.
- Plan trip: inputs → comparison → select → navigate.
- Review charging: sessions → compare networks → cost.

## Chart redesign

- Correct axis scaling, units, labels.
- Multi-series with separate scales where needed; avoid misleading mixing.
- Reference ranges, anomaly highlighting, time-range, zoom, event annotations, charging overlays, temperature correlations.

## Diagnostic investigation workflow

1. Identify alert/unusual measurement.
2. Open evidence.
3. Examine history.
4. Compare baseline.
5. See related events.
6. Ask AI (cites measurements, missing evidence).
7. Save/export report.

## Navigation screen concepts

- Next manoeuvre card (instruction, distance, street/exit).
- SOC band (expected/low/high) and arrival.
- Charger card (name, power, ETA, contingency).
- Voice toggle, reroute.

## Accessibility

- Contrast, font scaling, touch targets, screen reader labels, dark mode.

## Visual design principles

- Consistent spacing/typography, clear hierarchy, minimal clutter, evidence-first (show source/freshness).
