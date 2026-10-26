# Final report — dashboard → diagnostic investigation workbench

## A. Summary

The existing dashboard already had most of the pieces of an investigation
workbench — a merged event timeline in `analysis/timeline.py`, a four-stage
evidence-graded investigation model in `analysis/investigation.py`, provenance
and freshness state machines, and server-side downsampled charts — but two
things were missing that made the pieces feel disconnected:

1. The event timeline had **no page** (only a collapsed marker list on Explore
   and a bare `/api/timeline` endpoint), and no event was clickable.
2. There was **no path from an event to an investigation**, and
   `associate_events` / `observe_key` were never wired into the UI.

I added a cohesive browse → drill-down → investigate flow on top of the existing
data and analysis code, without a schema change, without a new charting stack,
and without any vehicle communication.

## B. Existing architecture discovered (VERIFIED)

- **Framework:** FastAPI + Jinja2 + Chart.js (local `web/static/vendor/`,
  CSP `script-src 'self'`). All routes live in `web/dashboard.py` (`create_app`).
- **Data access:** `database/repository.py` (`Repository`) over SQLite; schema in
  `database/models.py`. `measurements` is an append-only fact table whose
  `value` column has no declared type (numeric or text decodes).
- **Measurement model:** `decoders/registry.py` (DIDRegistry),
  `decoders/quality.py` (verification + plausibility gate),
  `decoders/catalogue.py`, `decoders/meb.py`.
- **Provenance / freshness:** `analysis/provenance.py`
  (live/imported/stale/simulated/unavailable + unverified flag) and
  `analysis/freshness.py` (fresh/repeated/stale/missing/collector-failure…).
- **Analysis:** `analysis/timeline.py` (merged event stream), `analysis/
  investigation.py` (observation → association → explanation → diagnosis),
  `analysis/anomaly.py` + `baseline.py` (robust outlier detection),
  `analysis/series.py` (lttb + min/max downsampling), `analysis/discovery.py`
  (measurement explorer), `analysis/decoder_lab.py`.
- **AI:** `ai/service.py` (local Ollama, structured evidence context, report
  validator) — **unchanged**.
- **Pages:** Overview, Progress, Measurements, Explore, Status, Battery, Faults,
  Charging, Quality, Investigations, AI Analysis, Sources.

## C. Features implemented

1. **Timeline page (`/timeline`)** — a browsable, filterable merged event list
   (DTC occurrences, warning-lamp on/off edges, charging, cell sweeps,
   anomalies, adapter/wake/sleep/note). Filter by kind (checkboxes with counts),
   time window (days), and a "include simulator events" toggle. Every event
   links to its investigation context.

2. **Event context page (`/event`)** — the investigation workspace for one
   moment: the anchor event, the other events in a ±window, and the
   measurements recorded closest to that instant (one row per key, each with
   its own real timestamp, provenance, and signed distance from the anchor).
   A form opens an investigation anchored there.

3. **Fault drill-down (`/dtcs/{dtc_id}`)** — the Faults page now links each code
   to a detail page showing occurrence history, warning-lamp transitions
   (derived from the ISO 14229 status byte), the ECU's freeze-frame snapshot
   (raw hex, never guessed), and the measurements recorded around the latest
   occurrence.

4. **Investigation seeding** — `POST /investigations/new` accepts an optional
   `anchor_ts`; when present it observes the anchor key (with real freshness)
   and records the nearby events as *associations* (explicitly not causes),
   reusing the existing tested `observe_key` and `associate_events`.

Users access these via the new **Timeline** nav link, the per-code links on the
Faults page, and the "investigate this fault" / "investigate this moment" links.

## D. Data correctness

- **Timestamps:** observation time (`ts`, UTC ISO-8601) throughout; no ingestion
  time is used. `measurement_context` never interpolates or re-stamps a value.
- **Units:** measurement names/units come from the DID registry via the existing
  `key_meta`; the stored unit is a fallback.
- **Provenance:** every nearby measurement shows its provenance string; imported
  and simulated provenance are visually distinct.
- **Freshness:** the investigation seed records the anchor observation's real
  freshness (`certain=False` when stale/non-scoreable), preserving the rule that
  a historical value is not current.
- **Missing vs zero:** absent values render as "—"; a genuine 0 is not treated
  as missing (inherited `_format_value` / `_first_number` behaviour).
- **Invalid/sentinel:** unchanged; the DTC freeze-frame records are kept as raw
  hex specifically because manufacturer-defined content must not be guessed.

## E. Diagnostic investigation

`/timeline` → click an event → `/event` (anchor + ±window events + nearby
measurements) → "Open investigation" → `/investigations/{id}` seeded with the
anchor observation and the nearby events as associations. A fault can also be
reached from `/dtcs` → `/dtcs/{dtc_id}`, which shows its occurrences, warning
edges, raw freeze frame, and nearby measurements, then links to the same event
context. Related measurements link back to `/explore` for charting, so the
timeline/event work reuses the existing chart rather than duplicating it.

## F. Performance

- `measurement_context` is a single bounded, parameterised query (time window +
  `LIMIT`), not an N+1 read.
- The DTC detail builds warning transitions from the code's own occurrences
  rather than scanning the whole vehicle's status history.
- `build_timeline` is reused unchanged; it loads the vehicle's events and
  filters in Python, which is fine at the current DB size (~155 events over a
  year in the production copy). Pushing the window into its SQL `WHERE` is noted
  as a follow-up.
- Measured: all pages render 200 against a copy of the 11 MB production
  database; the full suite runs in ~2m53s.

## G. Privacy and safety

- **Local-first:** all new pages use local Jinja templates and the already-vendor
  Chart.js; no new external dependency or service was added.
- **No vehicle communication:** every new route is a GET (or a form POST that
  only writes to the local `investigations` tables) reading SQLite. Added a
  regression test asserting `tx_log` (the byte-level transmission manifest)
  stays empty after browsing the new pages.
- **Read-only boundary untouched:** no diagnostic/UDS code was modified.
- **Auth/privacy:** new routes sit behind the same middleware stack; no tokens,
  VINs, or GPS coordinates are placed in URLs; user input is parameterised and
  Jinja-autoescaped.
- **AI:** unchanged (local Ollama only); the dashboard remains fully usable
  without it and without a network.

## H. Files changed

- `web/dashboard.py` — `_int_param` helper; `/timeline`, `/event`, `/dtcs/{dtc_id}`
  routes; `create_investigation` gains `anchor_ts`; nav list + "Timeline".
- `web/templates/timeline.html`, `web/templates/event.html`,
  `web/templates/dtc_detail.html` — new pages.
- `web/templates/base.html` — nav link.
- `web/templates/dtcs.html` — per-code links.
- `analysis/context.py` — new `measurement_context`.
- `analysis/dtc.py` — `dtc_summary` carries each code's `id`.
- `database/repository.py` — `Repository.dtc(dtc_id)` (additive).
- `tests/test_timeline_investigation.py` — new tests.
- `docs/investigation/dashboard_improvement/progress.md` — working log.

## I. Tests

- `python -m pytest -m "not hardware" -q` → **805 passed** (baseline was 792;
  +12 new, +1 from concurrent unrelated work).
- `python -m pytest tests/test_timeline_investigation.py -q` → **12 passed**.
- `python -m pytest tests/test_dashboard_render.py tests/test_platform.py -q`
  → **57 passed**.
- `python -m ruff check web analysis tests database` → **All checks passed**.
- Skipped: none. Unexecuted: no tests carry a `hardware` marker; nothing that
  could touch the real vehicle was run.

## J. Limitations

- `build_timeline` filters by window in Python after loading all rows; a very
  large database would want the window pushed into SQL (documented follow-up).
- The event context shows nearby measurements as a table with links to
  `/explore`; it does not yet plot them on the page itself (deliberate: reuse
  the existing chart rather than add a second stack).
- Manufacturer-defined freeze-frame records remain raw hex by design.
- AI report generation and anomaly thresholds were not changed.

## K. Follow-up recommendations

- Push the `since`/`until` window into `build_timeline`'s SQL `WHERE` clauses
  for large databases.
- Optionally plot the nearby-measurement series directly on `/event` once a
  multi-series comparison API exists.
- Surface collection-gap spans on the timeline (already available as
  `collection` events; could be drawn as bands).

## L. Git status

On `main`. My changes are confined to the files in section H plus the new
templates/tests/docs. The working tree also contains **pre-existing and
concurrent unrelated changes** (`decoders/`, `spec/`, `android/`, `cloud/`,
`analysis/replay.py`, `tests/test_catalogue.py`, `tests/test_platform.py`,
`decoders/quality.py`, the `decoder_accuracy_audit_20261026` and
`commit_readiness_audit_20261026` docs, `research/`, `tests/test_gateway_resistance.py`)
that I did not author and left untouched. Nothing was committed or pushed.
