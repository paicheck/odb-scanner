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
* **Resolution also needs an explicit operator confirmation.** `criteria_met`
  can only check structure (criteria written, ≥3 rows, a certain observation, a
  stated mechanism); it cannot read the prose criteria to see whether they are
  true. So `finalize` defaults to `criteria_confirmed=False`, and the
  Investigation page has an explicit "I confirm the written criteria are
  satisfied" checkbox. A bare Finalise click therefore cannot turn a hypothesis
  into a diagnosis. (This was found while recording the electric-drive
  investigation: the structural gate returned True even though the car reports
  no DTC, which is exactly the criterion that was written.)
* **Association is never causation.** Timeline association rows are marked
  `certain=False` and carry the sentence "Temporal order is not causation." The
  real database has **no DTC** for the electric-drive warning, so that symptom
  is modelled as a manually recorded symptom anchored on real `eldrive` data.
* **The DTC read was widened, not multiplied.** The single functional
  `0x19 0x02` read now asks for status mask `0x8F` instead of `0x08`
  (`collector.dtc_status_mask`, default `8F`). That adds the
  warningIndicatorRequested (`0x80`), pending (`0x04`) and failed (`0x01/0x02`)
  bits, so a fault that lights the dash before it is confirmed becomes visible.
  It is still **one request per cycle** — a wider read, not extra polling — and
  `0x08` reproduces the old confirmed-only behaviour. The status byte is mapped
  to `dtcs.status` (`warning`/`active`/`pending`/`confirmed`/`stored`) and the
  `warning-lamp` category, which the `/dtcs` page displays.
* **A known VW code is now classified by its status byte too.** `classify_dtc`
  used to return early for codes in the hand-maintained VW table, which meant
  the lamp/pending bits never reached exactly the codes this car emits. That
  early return was removed; the curated categories are still applied first.

## 3. Migration instructions

**Tables:** nothing to run. The additions are `CREATE TABLE IF NOT EXISTS`, so
opening the database with this build creates the missing tables in place and
leaves every existing table and row untouched. `SCHEMA_VERSION` is deliberately
unchanged at 1, so `data/odb_scanner.db`, the pre-purge backups and
`data/real_car.db` all keep opening. (This is the same convention documented
above `SCHEMA_VERSION` in `database/models.py` and asserted by
`tests/test_definition_store.py`.)

**One column needed a real migration, and `CREATE TABLE IF NOT EXISTS` could
not provide it.** The live `data/odb_scanner.db` already had an
`investigation_evidence` table from an earlier build, without the `certain`
column. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so the
first evidence write failed with `table investigation_evidence has no column
named certain`. `Repository.__init__` now runs `_apply_additive_migrations`,
which checks `PRAGMA table_info` and issues an idempotent `ALTER TABLE ... ADD
COLUMN certain INTEGER NOT NULL DEFAULT 1` when the column is absent. Existing
rows are kept and default to `certain=1`. It runs automatically on the next open;
no manual SQL is required. A regression test builds an old-schema table, opens
`Repository`, and asserts both the column and a preserved row.

To populate the definition/evaluation store for the decoder lab:

```
python main.py quality --persist
```

## 4. Tests run and actual results

* `python -m pytest -q` → **709 passed** in 128 s (baseline was 658; the
  platform added 45 tests in `tests/test_platform.py`, the DTC-read widening
  added 4 in `tests/test_diagnostic.py` (configurable mask, invalid-mask
  fallback, warning-lamp/pending classification, status-label priority), and
  the migration + finalize-confirmation fixes added 2 more platform tests.)
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
  investigation (id 1) plus 3 evidence rows into `data/odb_scanner.db`; those
  were deleted. The only investigation now in the real DB is genuine: id 1,
  "Electric-drive dash warning extinguished (owner report, 2026-10-19)", with
  28 evidence rows (6 observations, 16 associations, 6 explanations) and status
  **insufficient-evidence**. It records the absence of any DTC, the absence of a
  lamp channel, the unknown onset/clearing time, and the fact that the running
  collector still used the old confirmed-only mask. No synthetic artefact is
  presented as car evidence.
* **`main.py status` / `quality` re-run against the live DB** — measurement
  count is no longer the pre-purge figure; collection is live and the DB grows
  on each cycle, so freshness numbers are point-in-time.

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
  point at a code the car never recorded. The widened `0x8F` read only helps
  from the next collector restart onward — it cannot recover a lamp state that
  was never requested.
* **Collection is live again** (last read `2026-10-19T19:30` UTC at the time of
  writing), but only while the collector process is running. Gaps remain in the
  history (`10-06…10-11`, `10-13…10-14`), and `eldrive` only starts
  `2026-10-16`. A warning that came and went inside a gap was never observed.
* **Confirmed-only history.** Every DTC read before this change used mask
  `0x08`, so the absence of an electric-drive DTC in older data does not prove
  one was never pending or never lit the lamp.
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
   data. Collection is live again at the time of writing, so the single biggest
   lever is keeping it running through the events worth investigating (and
   restarting after this DTC-mask change so the wider read takes effect).
3. **Investigation templates** for the recurring questions ("what changed around
   time T", "is cell N drifting"), pre-filling the association window and anchor
   keys.
4. **A rendered (PDF/HTML) report** built on top of the JSON evidence packet —
   the packet already carries the citations, sample size and limitations, so
   this is presentation only.
5. **Decoder-fix workflow** — once a captured response exists, a guided path
   that proposes a corrected definition, replays it against stored bytes, and
   only then updates the catalogue with the evidence attached.
