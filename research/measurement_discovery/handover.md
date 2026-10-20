# Session handover

**Date:** 2026-10-20.

**Task:** produce an evidence-based, prioritised catalogue of additional
diagnostic measurements for the 2021 VW ID.3, without connecting to the car.

**Status:** complete for the research phase. No live work was performed.

## What was delivered

| Deliverable | File |
|---|---|
| A. Executive summary | `final_report.md` §A |
| B. Existing vs candidate table | `existing_vs_candidates.md` |
| C. Prioritised machine-readable catalogue | `candidate_measurements.csv` |
| D. Evidence report | `evidence_report.md` |
| E. Validation roadmap | `validation_roadmap.md` |
| F. Car Scanner recommendations | `car_scanner_recommendations.md` |
| G. Python integration recommendations | `python_integration.md` |
| Supporting: decoder audit | `decoder_audit.md` |
| Supporting: baseline inventory | `baseline_inventory.csv` |
| Supporting: per-signal profile coverage | `profile_coverage.csv` |
| Supporting: offline cross-check | `offline_crosscheck.csv` |
| Supporting: source inventory | `source_inventory.csv` |
| Supporting: backlog / progress / questions | `research_backlog.md`, `progress_log.md`, `unresolved_questions.md` |

## The three things to remember

1. **Fix the float32 family first.** 25 of 26 electric-drive measurements are
   decoded as u16; the profile says `FLOAT32(A,B,C,D)`. This is the largest
   invalid block and it directly affects the drive-warning investigation.
2. **Do not invert pack current.** The raw evidence (`0x1E3D`, `0x1E3E`,
   `0x035E`, discharge counter) supports negative = discharge. Car Scanner and
   ABRP negate for display. The bug is documentation/label, not arithmetic.
3. **The biggest “new” data is already in raw bytes.** Cell/sensor indices, the
   four `0x1E32` fields, and 12 V/DC-DC power are already available or
   computable. The only truly new profile DIDs are the four ABS wheel speeds,
   and they need `0x10 03`.

## Recommended next actions

### If only offline work is allowed

1. Add regression fixtures for the float32 family and `0x1E32`.
2. Add the cell/sensor index outputs with tie-aware tests.
3. Add derived 12 V/DC-DC power and SOC/temperature spreads.
4. Quarantine CO2, windshield, `odometer_2`, `reserved_current`,
   `bat_charging_voltage`.
5. Fix the pack-current docstring without changing the arithmetic.
6. Mark BMS `0xF802` unsupported.

### If a live read-only phase is approved later

1. Reproduce two float32 values (`0x4FAE`, `0x3E93`).
2. Confirm pack-current sign in parked/HVAC/charge states.
3. Read `0x1E32` before/after a known charge.
4. Compare cell/temp indices with a full sweep.
5. Test CO2/windshield in known states.
6. Defer ABS until a separate `0x10 03` approval.

## Do not do

- Do not connect to the car “just to check” without explicit approval and a
  reviewed allow-list.
- Do not send `0x10 03` for ABS casually.
- Do not blind-probe `0x2A26/0x2A28/0x2A2F`.
- Do not add `* -1` to pack current.
- Do not use windshield, CO2 or `0x5867` values for decisions.
- Do not infer a root cause from the warning; there is no DTC and association
  is not causation.
- Do not edit the installed Car Scanner profile while investigating; export it
  first.

## Environment / reproducibility

- Python 3.14.3, invoked as `python`.
- No `.venv` exists; run from the repo root.
- Live DB: `data/odb_scanner.db`, opened read-only.
- `python -m pytest tests/test_decoder_vectors.py tests/test_catalogue.py tests/test_meb_path.py -q`
  → **80 passed**.
- `git status --short` before handover: `?? research/` only (verify; the repo
  may have unrelated pre-existing state).

## Known limitations

- Original `btsnoop_hci.log` is missing; gateway payloads cannot be recovered
  locally.
- No controlled charge/discharge data.
- CO2/windshield/odometer need known-state tests.
- ABS not observed by the Python collector.
- GPS layout is source-supported, not vehicle-verified.
- OBC has no successful responses; its root cause is outside this catalogue.
- All external sources are community/open-source; no VW service documentation.

## Where to start reading

1. `final_report.md` — the decision-level summary.
2. `candidate_measurements.csv` — the ranked catalogue.
3. `decoder_audit.md` — why the top fixes are necessary.
4. `validation_roadmap.md` — what evidence would close each question.
5. `existing_vs_candidates.md` — how the 35 candidates relate to the 152.

## Final safety statement

No vehicle connection was made. No diagnostic request, write, coding,
adaptation, security access, routine, actuator test, or fault clearing was
performed. No production decoder, registry, catalogue, database, profile, log,
or test was modified. All additions are under
`research/measurement_discovery/`.
