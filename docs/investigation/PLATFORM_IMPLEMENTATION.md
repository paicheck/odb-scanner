# Vehicle-health investigation platform — implementation log

Companion to `docs/investigation/PLATFORM_AUDIT.md` (Stage A). This records what
was built, the decisions behind it, the database changes, the tests run with
their **actual** results, how to launch the dashboard, the known limitations, and
the highest-value next steps.

Everything here is **read-only with respect to the vehicle**. No new polling,
no writes, no security access, no actuator tests, no fault clearing, no coding
were added. `python main.py guard-test` still prints *"READ-ONLY guarantee
verified"*.

## 1. What was implemented

### New analysis / decoder modules

| module | job |
|---|---|
| `decoders/quality.py` | Machine-readable measurement-quality registry: decoder verification (verified / provisional / unknown / **invalid**), data kind (numeric / text / json / derived), plausibility ranges, and an explicit `use_in_scoring` policy. Turns the prose "known BAD scalings" list into an enforced gate. |
| `analysis/freshness.py` | Per-key observation state — fresh / repeated / carried-forward / stale / missing / collector-failure / unknown — using each row's own timestamp, plus per-module `active / idle / silent / imported-only / no-readings / failed`. |
| `analysis/series.py` | Server-side downsampling: LTTB and min/max samplers, step "transitions" for text/code signals, and refusal to coerce non-numeric values. |
| `analysis/discovery.py` | One searchable row per catalogue key (quality + freshness + aggregates), with filters and facets. |
| `analysis/baseline.py` | Vehicle-specific robust baselines (median/MAD) computed inside gap-free segments, plus a rate-of-change layer. |
| `analysis/timeline.py` | Unified event timeline across events, DTCs, charging sessions, cell sweeps and anomalies; simulator connections hidden by default. |
| `analysis/investigation.py` | Staged, persisted investigation: observation → association → explanation → diagnosis, with written criteria and an explicit *insufficient-evidence* outcome. |
| `analysis/decoder_lab.py` | Re-decodes the car's own stored raw bytes, checks the catalogue layout against the live decoder and against plausibility, and separates known-invalid keys into a validation workspace. |
| `analysis/anomaly.py` (extended) | Added `scan_robust` / `scan_series_robust` / `scan_all_robust`, gated by the quality registry; the original whole-window detector is kept unchanged. |

### Database (additive)

New tables (`CREATE TABLE IF NOT EXISTS`, `SCHEMA_VERSION` **stays 1**):

* `baselines` — persisted robust baselines with window, method and sample size.
* `investigations` — title, symptom, anchor, criteria, status, conclusion.
* `investigation_evidence` — the staged trail, with a stored `certain` flag.

New repository methods: `save_baseline` / `baselines` / `baseline`,
`open_investigation` / `add_investigation_evidence` / `update_investigation` /
`investigations` / `investigation` / `investigation_evidence`.

### Web

New pages: `/explore` (search + downsampled chart + event markers),
`/status` (module freshness), `/quality` (decoder lab), `/investigations` and
`/investigations/{id}`. New APIs: `/api/discover`, `/api/series`,
`/api/timeline`, `/api/quality`, `/api/investigations`. Navigation updated in
`web/templates/base.html`.

Evidence export (CSV **and** JSON, `Content-Disposition: attachment`):

* `/api/export/series?key=…&days=…&format=csv|json` — the raw stored rows for
  one key, **every column and every row kept** (failed reads, text decodes and
  the raw response bytes included), plus the current observation state and the
  limitations. The chart sampler never touches this path.
* `/api/export/discovery?…&format=csv|json` — the filtered discovery catalogue.
* `/api/export/investigation/{id}?format=json|csv` — a self-contained evidence
  packet: the investigation, its staged evidence (`certain` included), whether
  the criteria were met and why, and the association-is-not-causation caveat.

The Explore and Investigation pages link to these directly.

### AI (optional, unchanged prerequisite status)

`ai/prompts.py` gained `CITATION_RULES` and `investigation_prompt`, and
`build_context` now optionally carries measurement quality, freshness,
provenance and investigation context. `ai/service.py` includes a
platform-evidence packet (excluded keys, module freshness, provenance counts)
that degrades to empty on any error, so AI is never the reason a page fails.

### CLI

`python main.py quality [--persist]` (quality summary + decoder-lab failures;
`--persist` writes evaluations) and `python main.py status` (module freshness +
key anchors).

## 2. Key decisions (and why)

* **Known-invalid keys are excluded, never deleted.** They remain in the
  database and on the decoder-lab page. Excluding them from scoring without a
  workspace to investigate them would hide the problem; the brief forbids both
  hiding and scoring them.
* **No scale factor is changed.** A definition only changes with captured
  evidence (the `analysis/replay.py` discipline). The quality registry labels;
  it does not rewrite.
* **Freshness is relative to the vehicle, not the wall clock.** A module is
  `silent` only when the rest of the car kept answering; otherwise it is `idle`.
  This is what stops a parked car from looking like eleven broken modules.
* **Only this tool's reads validate this tool's decoder.** Imported Car Scanner
  rows carry CSV text in `raw_response`; treating that as UDS bytes wrongly
  failed ~40 cell keys until the decoder lab was restricted to
  `provenance NOT LIKE 'imported%'`.
* **An investigation cannot resolve without written criteria, a certain
  observation, a stated mechanism and enough evidence.** Certainty is stored,
  not recomputed, so a diagnosis cannot be upgraded later by a value going
  stale.
* **Association is never causation.** Timeline association rows are marked
  `certain=False` and carry the sentence "Temporal order is not causation." The
  real database has **no DTC** for the electric-drive warning, so that symptom
  is modelled as a manually recorded symptom anchored on real `eldrive` data.

## 3. Migration instructions

Nothing to run. The additions are `CREATE TABLE IF NOT EXISTS`, so opening the
database with this build creates the three new tables in place and leaves every
existing table and row untouched. `SCHEMA_VERSION` is deliberately unchanged at
1, so `data/odb_scanner.db`, the pre-purge backups and `data/real_car.db` all
keep opening. (This is the same convention documented above `SCHEMA_VERSION` in
`database/models.py` and asserted by `tests/test_definition_store.py`.)

To populate the definition/evaluation store for the decoder lab:

```
python main.py quality --persist
```

## 4. Tests run and actual results

* `python -m pytest -q` → **701 passed** in 127 s (baseline was 658; 43 new
  platform tests added in `tests/test_platform.py`, including the CSV/JSON
  export paths).
* `python main.py guard-test` → **READ-ONLY guarantee verified** (all write
  services blocked).
* `python main.py quality` (real `data/odb_scanner.db`, `meb` profile) →
  282 keys, 19 excluded from scoring; decoder lab **19 FAIL of 282**, and every
  FAIL is exactly one of the 19 known-invalid keys. No false failures once
  imported CSV rows were no longer treated as vehicle responses.
* `python main.py status` (real DB) →
  `1 silent, 7 idle, 3 imported-only, 1 no-readings, 0 active`
  (the last collection was 2026-10-18 18:26 UTC; `chg` is `no-readings` here
  because this database holds no successful on-board-charger read).
* **Real-DB hygiene check** — an early smoke test had written one throwaway
  investigation (id 1) plus 3 evidence rows into `data/odb_scanner.db`. These
  were deleted; `investigations`, `investigation_evidence` and `baselines` are
  all empty again and `measurements` is unchanged at **16,461** / 318 keys, so
  the real-car database contains no synthetic test artefacts.

## 5. How to launch / use the dashboard

```
python main.py serve          # then open http://127.0.0.1:8000/
```

Start at **Status** (is collection running, which modules are silent), then
**Explore** (search hundreds of keys, filter by trust and observation state,
chart with server-side downsampling and event markers), then **Quality** (which
decoders are known bad and why), then **Investigations** (open a symptom, record
observations, finalise — the page states plainly when the evidence is
insufficient).

## 6. Known limitations

* **The electric-drive warning is a symptom, not a stored fault.** The real
  database has no DTC; the platform can anchor and investigate it but cannot
  point at a code the car never recorded.
* **All data in `data/odb_scanner.db` is historical** (last read 2026-10-18).
  Nothing is fresh until collection resumes; every page says so.
* **Only two keys are decoder-VERIFIED** (`hv_energy_content`, `hv_energy_max`).
  The rest are provisional/unknown, and 19 are known invalid.
* **Plausibility ranges cover a subset of keys.** Keys without an established
  range report `NO_RANGE` rather than being flagged — deliberately, so no flag
  rests on an invented bound.
* **The per-cell rollups in discovery** are aggregate rows; per-cell detail is
  one click deeper via `/explore?key=cell_v_NNN`.
* **The min/max sampler** can return up to ~2×`max_points` buckets; the API caps
  `max_points` at 20 000.
* **Imported-only modules** (`19.Gate`, `8C.BMS`, `8105.DC/DC Converter`) have
  never been read live by this tool; their numbers are another tool's evidence.

## 7. Highest-value next improvements

1. **Wire `scan_all_robust` into `main.py analyze`** and a scheduled job, so
   anomalies are computed against robust, gap-aware baselines rather than the
   legacy whole-window z-score (which is still what `analyze` calls).
2. **A recording/continuation path** — the platform is only as good as the
   data, and collection stopped on 2026-10-18; resuming it is the single
   biggest lever.
3. **Investigation templates** for the recurring questions ("what changed around
   time T", "is cell N drifting"), pre-filling the association window and anchor
   keys.
4. **A rendered (PDF/HTML) report** built on top of the JSON evidence packet —
   the packet already carries the citations, sample size and limitations, so
   this is presentation only.
5. **Decoder-fix workflow** — once a captured response exists, a guided path
   that proposes a corrected definition, replays it against stored bytes, and
   only then updates the catalogue with the evidence attached.
