# Measurement discovery — research package (2026-10-20)

**Status:** offline research only. No vehicle connection, no diagnostic request,
no write/coding/adaptation/security access/routine/actuator test, no fault
clearing. No production decoder, registry, catalogue, database, profile, log or
test was modified. Everything here is additive.

> **2026-10-22 update:** the safe fixes identified by
> `payload_research_20261021.md` are now implemented in commit `b8b1c70`:
> signed/sentinel handling for `0x1E39`, raw-only `0x2A53[3:5]`, relabelled
> `0x5867` (10 km/LSB counter), the `accelerator_pedal_pct` alias for `0xF449`,
> and a read-only `tools/audit_payload_findings.py`. The 2026-10-20 package
> below remains the original evidence snapshot; later status is in
> `validation_roadmap.md` and `decoder_audit.md`.

**Start with:** `final_report.md` (executive summary and deliverables A–G), then
`candidate_measurements.csv` (ranked catalogue).

## Fast facts

- Baseline: **152 logical measurements** / **282 registry specs** across 8
  categories (BMS 39, Gateway energy 30, Climate 27, Electric drive 26, Vehicle
  info 21, OBC 5, DC/DC 2, GPS 2).
- Car Scanner profile: **308 rows / 305 distinct signals / 255 DIDs**.
- Catalogued candidates: **35** (7 P1, 7 P2, 13 P3, 7 P4, 1 rejected).
- Profile-only real DIDs: **4** ABS wheel speeds (`0x1800`–`0x1803`), which need
  `0x10 03` and are deferred.
- Registry-only DIDs: `0x0500` (battery serial), `0x210E` (gear), `0xF802`
  (VIN; **NRC 0x31 on BMS**, valid on vehicle-info).

## Top findings

1. **25 of 26 electric-drive signals are decoded as u16 instead of float32.**
   Example: `0x3E93` raw `43D897DE` is 433.19 V, not 1,737.8 V. Fix this first.
2. **Do not invert pack current.** `0x1E3D`, `0x1E3E` and the independent
   `0x035E` are all negative at rest while the discharge counter rises. Negative
   = discharge. Car Scanner/ABRP negate for display; our documentation is wrong,
   not the arithmetic.
3. **`0x1E32` is decoded at the wrong offsets/units.** Ah counters are at 0/4,
   kWh counters at 8/12. Current output ~3,724/3,700 kWh; correct ~24,437/23,460
   kWh.
4. **Cell/sensor numbers are already in the raw bytes.** `0x1E33`/`0x1E34` and
   `0x1E0E`/`0x1E0F` carry a 1-based index at bytes 2–3; the decoders discard it.
5. **12 V and DC-DC power are derived metrics, not new DIDs.** Add them to serve
   the low-voltage investigation.
6. **CO2, windshield and `0x5867` are quarantined.** Their current values are
   implausible or inconsistent; do not use them until tested.

## Files

| File | Purpose |
|---|---|
| `final_report.md` | Executive summary + deliverables A–G |
| `existing_vs_candidates.md` | 152 baseline vs 35 candidates; per-signal coverage |
| `candidate_measurements.csv` | Ranked machine-readable catalogue |
| `baseline_inventory.csv` | 152 logical baseline entries |
| `profile_coverage.csv` | 305 profile signals with coverage status |
| `profile_summary.json` | Profile/DID coverage counts |
| `profile_signals_all.csv` | Deduplicated profile signal table |
| `profile_signals_readable.tsv` | Readable profile signal table |
| `evidence_report.md` | Evidence hierarchy, source assessment, contradictions |
| `decoder_audit.md` | Decoder findings, raw examples, proposed fixes, resolution log |
| `validation_roadmap.md` | Offline + future read-only tests, gates, abort criteria |
| `payload_research_20261021.md` | Unknown-payload dossier, raw vectors, proposed known-state tests, implementation status |
| `captures/` | 2026-10-21 offline validation archive (35/35 pass) and reports |
| `car_scanner_recommendations.md` | What to select/add/avoid in Car Scanner |
| `python_integration.md` | Code/schema/test recommendations |
| `offline_crosscheck.csv` | 31-timestamp cell/temp index comparison |
| `source_inventory.csv` | Sources, local copies, hashes, authority, limits |
| `research_backlog.md` | Open research items |
| `progress_log.md` | Session log and QC results |
| `unresolved_questions.md` | Questions that need evidence |
| `handover.md` | Handover summary |
| `scripts/analyse_profile.py` | Reproducible profile analysis |
| `scripts/build_profile_coverage.py` | Reproducible per-signal coverage mapping |

## QC

- Baseline 152 rows; candidate CSV 35 rows / 29 columns, unique IDs, scores
  recomputed exactly, one rejected item.
- Profile 308 rows / 305 signals / 255 DIDs; coverage statuses sum to 305.
- `python -m pytest tests/test_decoder_vectors.py tests/test_catalogue.py tests/test_meb_path.py -q`
  → **80 passed**.
- `git status --short` → only `?? research/` (plus any unrelated pre-existing
  state; verify before commit).
