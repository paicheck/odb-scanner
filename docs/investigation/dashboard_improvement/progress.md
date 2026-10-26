# Dashboard improvement — progress log

Turning the existing dashboard into a diagnostic investigation workbench.

## Context

- Vehicle: 2021 VW ID.3 58 kWh Pro.
- Working tree already had uncommitted user work (decoders, spec, android, cloud,
  an audit dir). **Preserved untouched.** This work only adds files and touches
  `web/`, `analysis/` (new module + one small repository addition) and adds new
  test files.
- Safety: dashboard remains read-only. No vehicle communication is added. All
  new endpoints only read SQLite.

## Baseline (2026-10-26)

- `git status`: on `main`, clean except pre-existing user changes (see above).
- `python -m pytest -m "not hardware" -q` → **792 passed** in ~2m35s.
- `python -m ruff check web analysis tests` → **All checks passed**.

## Existing architecture discovered (VERIFIED)

- FastAPI + Jinja2 + Chart.js (local vendor asset, CSP `script-src 'self'`).
- Single `web/dashboard.py` (`create_app`) with page routes and a JSON API.
- SQLite via `database/repository.py` (`Repository`); schema in
  `database/models.py` (append-only `measurements` fact table).
- Measurement model: `decoders/registry.py` (DIDRegistry), `decoders/quality.py`
  (verification/plausibility gate), `decoders/catalogue.py`, `decoders/meb.py`.
- Provenance: `analysis/provenance.py` (live/imported/stale/simulated/unavailable
  + unverified flag). Freshness: `analysis/freshness.py` (state machine).
- Existing analysis: `analysis/timeline.py` (merged event timeline),
  `analysis/investigation.py` (4-stage evidence-graded investigation),
  `analysis/anomaly.py` + `analysis/baseline.py` (robust outlier detection),
  `analysis/series.py` (server-side downsampling), `analysis/discovery.py`
  (measurement explorer), `analysis/decoder_lab.py` (quality lab).
- AI: `ai/service.py` (local Ollama, structured evidence context, validator).
- Existing pages: Overview, Progress, Measurements, Explore, Status, Battery,
  Faults (DTCs), Charging, Quality, Investigations, AI Analysis, Sources.

## Gaps found

1. `analysis/timeline.py` builds a rich merged event list (DTC occurrences,
   warning on/off transitions, charging, cell sweeps, anomalies, adapter/wake/
   sleep/note) and `/api/timeline` exists, but **no page renders it** and no
   event is clickable.
2. **No event → investigation drill-down.** `associate_events` and
   `explain_with_baseline` exist in `analysis/investigation.py` but are not
   wired into any route; opening an investigation does not auto-populate.
3. The Faults page (`/dtcs`) is a flat summary with **no per-code drill-down**
   to occurrence history, freeze frames, or nearby measurements.
4. No view shows *measurements recorded around a moment* (the event's context).

## Plan (cohesive end-to-end investigation flow)

1. `analysis/context.py` — `measurement_context(repo, vid, center_ts, window_s)`
   returns the measurements recorded closest to an instant (real timestamps,
   provenance preserved; no interpolation).
2. `/timeline` page — filterable merged event timeline (kind, days, simulated);
   every event links to its investigation context.
3. `/event` page — investigation workspace for one moment: the anchor event, the
   events in a ±window, and the measurements recorded nearby, with a form to
   open an investigation anchored there (auto-associates events + observes the
   anchor key).
4. `/dtcs/{dtc_id}` page — per-code drill-down (occurrences, warning
   transitions, freeze frames, nearby measurements) + investigate action.
5. Wire `associate_events`/`observe_key` into investigation creation from an
   event timestamp.

## Decisions

- No schema changes. DTC lookup uses a small new `Repository.dtc(dtc_id)` method
  (additive, consistent with existing `investigation()`).
- Event context uses observation timestamps (`ts`, UTC ISO-8601) throughout.
  Provenance and delta-from-event are shown; no value is re-stamped.

## Implemented

- `analysis/context.py` — `measurement_context(repo, vid, center_ts, window_s)`
  returns one row per key recorded closest to an instant (bounded, real
  timestamps + provenance, no interpolation).
- `web/dashboard.py` — new routes `/timeline`, `/event`, `/dtcs/{dtc_id}`;
  `/dtcs` now links each code to its detail; `POST /investigations/new` accepts
  an optional `anchor_ts` and seeds the new investigation with the anchor
  observation and nearby-event associations.
- `database/repository.py` — `Repository.dtc(dtc_id)` (additive).
- `analysis/dtc.py` — `dtc_summary` now carries each code's `id` so the Faults
  page can link to the drill-down.
- `web/templates/` — new `timeline.html`, `event.html`, `dtc_detail.html`;
  `base.html` nav gains "Timeline"; `dtcs.html` links codes to detail.
- `tests/test_timeline_investigation.py` — 12 tests covering the context
  function, the three new pages, DTC drill-down, investigation seeding, and a
  read-only safety assertion (`tx_log` stays empty after browsing).

## Validation

- `python -m pytest -m "not hardware" -q` → **805 passed** (12 new + baseline).
- `python -m ruff check web analysis tests database` → **All checks passed**.
- Smoke test against a **copy** of `data/odb_scanner.db`: every page (incl.
  `/timeline`, `/event`) renders 200 with the real vehicle data.

## Concurrent working-tree activity (observed, not mine)

While this work ran, another process modified `decoders/quality.py` and
`tests/test_platform.py` and added `docs/investigation/commit_readiness_audit_20261026/`
and `docs/investigation/decoder_accuracy_audit_20261026/`. Those changes are
unrelated to the dashboard work and are left untouched.

## Unresolved

- `build_timeline` fetches all occurrences/events for the vehicle before
  filtering by window in Python (pre-existing). Fine for the current DB size;
  a large DB would want the window pushed into the SQL WHERE clauses. Left as a
  follow-up to avoid touching the shared, well-tested timeline code.

