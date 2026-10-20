# Progress log

**Session:** 2026-10-20 (overnight autonomous research pass).

**Safety:** no vehicle connection, no diagnostic request, no write, no
coding/adaptation/security access/routine/actuator test, no fault clearing. The
live database was opened read-only. All originals preserved.

## Log

| Phase | What was done | Output |
|---|---|---|
| 1. Scope and inventory | Read the project, registry, decoders, data catalogue, investigation docs, and current git state. Confirmed the 152-entry baseline and its 8 categories. | `research_plan.md` |
| 2. Profile extraction | Wrote and ran `scripts/analyse_profile.py`; parsed 308 MEB `CustomPID` records from the extracted profile. | `profile_summary.json`, `profile_signals_all.csv`, `profile_signals_readable.tsv` |
| 3. Coverage reconciliation | Compared 308 rows / 255 DIDs against 282 registry specs / 253 DIDs. Identified 250 shared DIDs, 4 real profile-only ABS DIDs, 1 composite pseudo-DID, and 3 registry-only DIDs. | `profile_summary.json`, notes |
| 4. External sources | Fetched spot2000 list, evDash `CarVWID3.cpp`, ABRP `MEB.json`; checked ELMduino issue #207, OVMS, raimuucka. Recorded authority and limitations. | `sources/`, `source_inventory.csv` |
| 5. Decoder audit | Compared profile formula/type/SBI/DL against registry decoders and raw payloads. Found the 25-row float32 family, `0x1E32` offset/unit bug, CO2/windshield/odometer issues, BMS `0xF802` NRC `0x31`, and the pack-current sign contradiction. | `decoder_audit.md` |
| 6. Offline cross-check | Compared `0x1E33`/`0x1E34` cell indices and `0x1E0E`/`0x1E0F` sensor IDs against 31 same-timestamp sweeps, tie-aware. | `offline_crosscheck.csv` |
| 7. Candidate catalogue | Built 35 candidates with DID/layout/evidence/sources/raw examples/validation/safety notes and a weighted priority score. | `candidate_measurements.csv` |
| 8. Baseline inventory | Rebuilt the 152 logical entries from the registry; grouped 108 cell voltages and 24 temperatures; documented known issues. | `baseline_inventory.csv` |
| 8b. Per-signal coverage | Mapped all 305 profile signals to selected/grouped/subfield/decoder-repair/quarantine/alias/ABS/derived statuses with a confidence label. | `profile_coverage.csv` |
| 9. Deliverables | Wrote the executive report, existing-vs-candidate comparison, evidence report, validation roadmap, Car Scanner recommendations, Python integration recommendations, backlog, unresolved questions, handover. | A–G files |
| 10. QC | Verified counts, CSV integrity, scores, unique IDs, and file presence. Corrected the profile-signal count from 303 to 305 after finding that the parser stripped unit suffixes such as `(Ah)`/`(kWh)`. Ran targeted project tests. | **80 passed** |

## Key decisions

1. Use `build_default_registry("meb")` as the authoritative 152-entry baseline;
   it matches the owner's category counts exactly.
2. Treat the extracted Car Scanner profile CSV as the primary profile source.
3. Treat spot2000/evDash as corroboration, not authority; ABRP as a weak
   cross-check only.
4. Do not change any production decoder in this phase, even where the fix is
   obvious.
5. Keep the pack-current arithmetic and correct the documentation; the
   independent `0x035E` current proves the physical sign.
6. Rank decoder repairs and derived metrics above exotic new DIDs because the
   drive/LV investigation needs trustworthy existing data first.

## QC results

- Baseline logical rows: **152** (BMS 39, Gateway energy 30, Climate 27,
  Electric drive 26, Vehicle info 21, OBC 5, DC/DC 2, GPS 2).
- Registry specs: **282**; distinct DIDs: **253**.
- Profile: **308** rows, **305** distinct signals, **255** DIDs.
- Candidate CSV: **35** rows, **29** columns; unique IDs; scores 0–100; one
  rejected item; no duplicate names.
- `python -m pytest tests/test_decoder_vectors.py tests/test_catalogue.py tests/test_meb_path.py -q`
  → **80 passed**.
- No production files edited; `git status` shows only `research/` untracked
  (verify before commit).

## What was not done

- No connection to the vehicle.
- No `0x22`, `0x10 03`, `0x27`, `0x2E`, `0x31`, `0x14`, routine or actuator
  test.
- No decoder, registry, catalogue, database or profile edit.
- No attempt to “fix” CO2, windshield, odometer or pack current by choosing a
  convenient value.
- No inference from the warning to a root cause.
