# Decoder accuracy audit — progress ledger

**Session:** 2026-10-26
**Vehicle context:** 2021 VW ID.3 58 kWh Pro (MEB), profile `meb`
**Repository:** `C:\Users\kings\OneDrive\Documents\odb_scanner`
**Branch / HEAD:** `main` / `bd86d55 Add production cloud deploy stack`

## Objective and safety envelope

Audit the accuracy of the MEB decoder set, fix only defects that the stored
evidence demonstrates, and leave everything uncertain labelled as uncertain.

Constraints observed throughout:

- **No vehicle communication.** No adapter was opened, no diagnostic request
  was sent, no session/security/routine/actuator action was attempted.
- `tools/elm_console.py` and `tools/read_abs_wheel_speeds.py` were not run.
- The read-only guard and allow-list were not changed. No alternate request
  path was added.
- Production databases were opened only with `mode=ro` (integrity checks) or
  copied to the temp directory before any `Repository` use, because
  `Repository.__init__` writes schema metadata on open.
- No database, capture, or historical report was rewritten.
- No commit, push, reset, clean, or checkout was performed.
- No synthetic fixture was substituted for captured data.

## Repository state at audit start

Pre-existing work that this audit preserved:

- `M .gitignore` — adds `cloud/cpanel.env`.
- `?? cloud/CPANEL.md`, `?? cloud/cpanel.env.example`,
  `?? cloud/passenger_wsgi.py`, `?? cloud/requirements-cpanel.txt`.

A **separate autonomous-discovery pass** ran concurrently during this audit.
It added two gateway DIDs (`0x2A25`, `0x2A0E`), regenerated
`spec/odb_scanner.json` and the Android asset, and added
`tests/test_gateway_resistance.py`. Those files are preserved and are **not**
claimed as audit changes. Their presence is why the current-tree counts below
are two specs larger than the audit baseline.

## Baseline (before audit edits)

Established with `inventory.py`; stored in `inventory_baseline.json`
(SHA-256 `BAE60297...F0D7A24`).

| Metric | Baseline |
|---|---:|
| Measurement specs | 287 |
| Distinct DID numbers | 253 |
| Distinct (ECU, DID) associations | 253 |
| Decoded specs | 286 |
| Raw-only specs | 1 |
| Catalogue keys | 287 |
| Keys with declared byte layout | 43 |
| Keys without layout | 244 |
| Keys without unit | 12 |
| Catalogue keys not VERIFIED | 285 |
| Catalogue keys VERIFIED | 2 |
| Derived keys | 10 (one was also a DID — see F3) |
| INVALID keys | 5 |
| Keys excluded from scoring | 5 |
| Sentinel-rule occurrences | 7 (5 Python, 2 Kotlin) |
| Keys named in `tests/` | 78 |
| Keys not named in `tests/` | 209 |
| Catalogue validation problems | 0 |

Baseline commands:

- `python -m pytest -q` → **777 passed** in 143.59 s.
- `python -m ruff check .` → **All checks passed**.
- `python -m pytest tests/test_spec_export_in_sync.py -q` → **3 passed**.

## Current tree after audit edits

`inventory_current.json` (SHA-256 `5C970109...829B2E8`).

| Metric | Current | Delta vs baseline |
|---|---:|---:|
| Measurement specs | 289 | +2 (concurrent pass) |
| Distinct DID numbers | 255 | +2 |
| Distinct (ECU, DID) associations | 255 | +2 |
| Decoded specs | 288 | +2 |
| Raw-only specs | 1 | 0 |
| Catalogue keys | 289 | +2 |
| Keys with declared byte layout | 45 | +2 |
| Keys without layout | 244 | 0 |
| Keys without unit | 13 | +1 (new raw index) |
| Catalogue keys not VERIFIED | 287 | +2 |
| Catalogue keys VERIFIED | 2 | 0 |
| Derived keys | 9 | −1 (F3 fix) |
| INVALID keys | 5 | 0 |
| Keys excluded from scoring | 5 | 0 |
| Keys named in `tests/` | 80 | +2 |
| Keys not named in `tests/` | 209 | 0 |
| Catalogue validation problems | 0 | 0 |

Current commands:

- `python -m pytest -q` → **805 passed** in 172.37 s.
- `python -m ruff check .` → **All checks passed**.
- `python -m pytest tests/test_spec_export_in_sync.py -q` → included, passed.
- `gradlew.bat :app:testDebugUnitTest --offline` → **BUILD SUCCESSFUL**,
  **52 tests, 0 failures** (DecodeTest: 11).

The pytest growth is 28 tests (777 → 805), explained by the concurrent passes
and this audit: 14 tests in `tests/test_gateway_resistance.py`, 12 in
`tests/test_timeline_investigation.py` (both concurrent, preserved), and 2
Python regression tests from this audit (F2, F3). The Android gear test is in
the separate 52-test JVM suite.

## Work performed

### 1. Baseline and static inspection

- Read `decoders/meb.py`, `decoders/catalogue.py`, `decoders/quality.py`,
  `decoders/registry.py`, `analysis/replay.py`, `analysis/decoder_lab.py`,
  `collector.py`, `diagnostic/uds.py`, `tools/export_spec.py`,
  `tools/replay_validate.py`, the Android `Decode.kt`/`DecodeTest.kt`, the
  generated spec, fixtures, and the 2026-10-15 / 2026-10-21 research records.
- Inventoried all 287 baseline specs: confidence, layout, unit, data kind,
  sentinel rules, multi-key DIDs, test-name coverage.

### 2. Re-decode the stored evidence

- `tools/replay_decoder_impact.py` on read-only databases:
  - `data/real_car.db`: **440** raw vehicle rows re-decoded, **0 changed**,
    0 newly decoded, 0 newly failed. The current registry reproduces every
    historical real-car row it can decode.
  - `data/odb_scanner.db`: **10,419** raw vehicle rows re-decoded,
    **494 changed**, **17 newly decoded**, **41 now failed**. All are the
    expected historical repairs (float32 family, `0x1E32`, `0x27C4`,
    `0x1E39`, `0x42DB`), not regressions: the rows were stored by older
    decoders.
- `lab_sweep.py` on **copies** of both databases:
  - `real_car.db` vehicle 2: PASS 142, INDETERMINATE 142, FAIL 5.
    The only failures are the five deliberately INVALID keys.
  - `odb_scanner.db` vehicle 1: PASS 156, INDETERMINATE 103, FAIL 30.
    25 failures are historical stored-vs-current decode mismatches; the
    other 5 are the same INVALID keys.
- `tools/replay_validate.py`: after the F2 fix it completes on
  `real_car.db`; verdicts **PASS 7, INDETERMINATE 2**; the two HV energy
  layouts decode 42 real-world records with max error 0.

### 3. Numeric, unit and parity audit

- `numeric_audit.py` → `numeric_audit.json`.
- 118 scalar recipes, all big-endian; widths 1/2/3/4; 15 signed.
- 167 special-decode keys (108 cell voltage, 25 float32, 24 cell
  temperature, plus pair/ASCII/bits/JSON/gear/VIN).
- Four keys export `decode: null`.
- 12 scalar keys have no unit; the list is counts, indices, flags, or
  explicitly raw indices.
- No scalar's plausibility range is disjoint from its encoding range.
- Five ranges extend outside their encoding range; all are intentional
  physical bounds or broader administrative bounds, not decoder errors.
- Android asset and `spec/odb_scanner.json` are byte-identical
  (SHA-256 `483054ED...EF1D6`), and all 12 special names used by the spec have
  a Kotlin `when` case.

### 4. Fixes applied (all with regression tests)

| ID | Defect | Fix | Test |
|---|---|---|---|
| F1 | Kotlin gear map disagreed with Python and sign-extended byte 1 | `Decode.kt` now mirrors `_gear` exactly | `DecodeTest.gearMatchesPython` |
| F2 | Replay crashed on text-valued rows (`float("P")`) | `observations_from_measurements` skips non-numeric rows | `test_text_valued_rows_are_skipped_not_crashed_on` |
| F3 | `cell_voltage_sum` (DID `0x1E3C`) was labelled DERIVED, so the lab skipped it | removed from `_DERIVED_KEYS` | `test_did_backed_cell_voltage_sum_is_not_labelled_derived` |

No INVALID key, scoring exclusion, sentinel rule, byte layout, scale, or
read-only guard was changed.

### 5. Final verification

- Full Python suite: **805 passed** (172.37 s).
- Ruff: **clean**.
- Android JVM unit tests offline: **52 passed, 0 failures**.
- Spec/Android-asset sync: **0 differences**.
- Decoder lab: **289/289** catalogue keys evaluated; only the five INVALID
  keys fail on the real car.

## Artefacts produced

| File | Purpose |
|---|---|
| `progress.md` | This ledger |
| `findings.md` | Findings, severity and disposition |
| `decoder_inventory.md` | Generated full inventory (289 keys) |
| `evidence_register.md` | Evidence items, sources and limits |
| `final_report.md` | Report sections A–K |
| `inventory.py` / `inventory_baseline.json` | Baseline inventory |
| `inventory_current.json` | Post-audit inventory |
| `inventory_md.py` | Generator for the full inventory |
| `numeric_audit.py` / `numeric_audit.json` | Numeric/unit/parity evidence |
| `lab_sweep.py` / `lab_sweep.json` | Offline decoder-lab sweep |
| `impact_real_car.*`, `impact_odb.*` | Re-decode impact reports |
| `replay_validate.md` / `.json` | Replay/validation report |

## Remaining work / blocked

- No further offline decoder defects were demonstrated.
- The five INVALID keys stay INVALID pending evidence that requires a car:
  `0x1E39` charging top range, `0x42DB` non-sentinel scale,
  `0x27C4` windshield-vs-ambient identity, `0x5867` absolute counter zero,
  and `0x2A53[3:5]` independent meaning.
- Android has no special decoder for the three quarantined Python specials
  (`bat_charging_voltage`, `co2_content_interior`, `windshield_temperature`);
  the spec exports `decode: null` for them, so Android returns no value
  rather than a wrong one. Implementing them is a parity improvement, not an
  accuracy fix, and needs a deliberate spec/asset change.
- Test-name coverage remains 80/289. The 209 unnamed keys are listed in
  `decoder_inventory.md`; many have no independent evidence to test against.

## Close-out

- Final artefacts written: `findings.md`, `evidence_register.md`,
  `final_report.md` (sections A–K), plus the generated inventory and audit
  scripts. All are under this directory.
- Full Python suite completed: **805 passed** in 172.37 s. Targeted post-fix
  checks: `tests/test_catalogue.py tests/test_platform.py` → **99 passed**;
  `tests/test_spec_export_in_sync.py` → **3 passed**; `ruff check .` clean.
- Android JVM suite re-run with `--rerun-tasks`: **52 tests, 0 failures**
  (`DecodeTest` 11).
- `data/real_car.db` kept its pre-audit byte hash. `data/odb_scanner.db` is
  held open by a concurrent dashboard server, which created a WAL/SHM pair and
  makes the main-file byte hash unstable. A table-by-table comparison through
  SQLite found zero schema/row differences versus the 20:24 working copy, and a
  pre-WAL page comparison found only free-space/header pages differ. The audit
  issued no production-database writes; the exact writer is attributed to the
  concurrent process only as a caveat, not as a proven fact. See
  `evidence_register.md` E17.
- No commit, push, reset, clean, or checkout. All changes remain uncommitted.
