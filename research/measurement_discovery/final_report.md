# Final report — additional diagnostic measurements for the 2021 VW ID.3

**Vehicle:** 2021 VW ID.3 Pro Performance, 58 kWh, MEB, VIN
`WVWZZZE1ZMP087053`.

**Date:** 2026-10-20.

**Status:** research complete for this phase. **No vehicle connection, no
diagnostic request, no write, no coding/adaptation, no security access, no
routine, no actuator test, no fault clearing.** No production decoder,
registry, catalogue, database, profile, log, or test was modified. All new work
is additive under `research/measurement_discovery/`.

**Owner context:** intermittent electric-drive warning and a low-voltage /
communication investigation. The warning is a manually recorded symptom, not a
stored DTC; the real record has no DTC. The warning was reported extinguished
on 2026-10-19. This report therefore focuses on making the next event
observable, not on claiming a diagnosis.

---

## A. Executive summary

### A.1 The baseline is 152 logical measurements

Rebuilding from `build_default_registry("meb")` gives **282 specs**, which
collapse to **152 logical measurements** because the 108 cell voltages and 24
temperature points are grouped:

| Category | Logical entries |
|---|---:|
| BMS | 39 |
| Gateway energy | 30 |
| Climate | 27 |
| Electric drive | 26 |
| Vehicle info | 21 |
| OBC | 5 |
| DC/DC | 2 |
| GPS | 2 |
| **Total** | **152** |

This matches the owner's category counts exactly. The full per-entry inventory,
including member keys and known issues, is in `baseline_inventory.csv`.

### A.2 The installed profile is bigger, but not in the way it first appears

The Car Scanner MEB profile has **308 rows / 255 DIDs / 305 distinct underlying
signals**. It overlaps the Python registry on **250 DIDs**. The only **four real
profile-only DIDs** are the ABS wheel speeds `0x1800`–`0x1803`; the fifth
profile-only "DID" is a composite `0x1E3B;0x1E3D` derived power pseudo-DID.

The registry has three DIDs not in the profile:

- `0x0500` battery serial (observed; ASCII not decoded);
- `0x210E` gear (observed);
- `0xF802` VIN/serial — **invalid on the BMS** (NRC `0x31`), valid on
  vehicle-info.

Most of the 305 signals are not new measurements: they are grouped cell/temp
rows, multi-field DIDs, aliases, or derived rows. The real opportunity is
**field-level extraction and decoder repair**, not a large new DID set.

### A.3 The 35 candidates

| Band | Count | Meaning |
|---|---:|---|
| P1 | 7 | Do first; high relevance and strong evidence |
| P2 | 7 | Next; useful and mostly low risk |
| P3 | 13 | Conditional; needs a test or a higher-risk session |
| P4 | 7 | Low priority or speculative |
| Rejected | 1 | Do not pursue |

Evidence distribution: 11 `verified_on_vehicle`, 9 `supported_by_source`, 7
`inferred`, 4 `observed_positive_undecoded`, 3 `speculative`, 1 `rejected`.

### A.4 The findings that change decisions

1. **25 of 26 electric-drive measurements are decoded as the wrong type.**
   The Car Scanner profile says `FLOAT32(A,B,C,D)`; the project uses u16/10+1.
   Raw `43D897DE` is 433.19 V, not 1,737.8 V; `41816D8E` is 16.18 °C, not
   1,677.9 °C. This is the top fix and it needs no new vehicle request.

2. **The pack-current sign suspicion is probably backwards.** At rest, `0x1E3D`
   and `0x1E3E` return -2.43 A and the independent `0x035E` HV current returns
   -1.9 A, while the discharge counter rises. Negative = discharge. Our
   arithmetic is physically consistent; the docstring and Car Scanner's
   negated display are the inconsistency. **Do not invert the decoder.**

3. **`0x1E32` is read at the wrong offsets and in the wrong units.** The
   profile and spot2000 put Ah at 0/4 and kWh at 8/12. The current decoder reads
   the Ah counters with the kWh divisor, producing ~3,724/3,700 kWh instead of
   ~24,437/23,460 kWh.

4. **The cell/sensor number is already in the raw bytes and is being
   discarded.** `0x1E33`/`0x1E34` and `0x1E0E`/`0x1E0F` carry a 1-based index
   at bytes 2–3. Tie-aware offline comparison matched 28/31 max cell, 29/31 min
   cell, 31/31 max temperature sensor and 30/31 min sensor. This is the cheapest
   high-value diagnostic addition: no new DID, no session change.

5. **The low-voltage investigation is best served by derived power, not new
   DIDs.** 12 V power (V × A) and DC-DC output power (V × A) are arithmetic on
   already selected fields. They make the LV trend interpretable.

6. **ABS wheel speeds are the only genuinely new profile DIDs, but they need
   `0x10 03`.** That is a session change, not a read. Treat them as a separate,
   explicitly approved test, not as a casual profile addition.

7. **The gateway `0x2A26`/`0x2A28`/`0x2A2F` lead is real but blocked on
   payload recovery.** They returned positive `0x62` responses in a passive
   capture, but the original btsnoop log is not in this workspace and the text
   inventory has no scaling. Do not blind-probe them.

8. **CO2, windshield and `0x5867` are not ready for use.** The CO2 byte choice is
   implausible, the windshield value is ~488,500 °C, and `0x5867` disagrees with
   three other odometers. Quarantine/test; do not “fix” by choosing the number
   that looks nicest.

### A.5 Bottom line

The highest-value next step is **not** adding more PIDs. It is:

1. fix the float32 family;
2. expose the cell/sensor indices;
3. add 12 V/DC-DC power;
4. fix the `0x1E32` four-field decode;
5. correct the pack-current documentation without changing its arithmetic;
6. then, and only then, consider ABS/GPS/gateway additions.

---

## B. Existing vs candidate table

The full comparison is in `existing_vs_candidates.md`. The summary:

| Category | Baseline logical entries | Candidates | Main candidate types |
|---|---:|---:|---|
| BMS | 39 | 12 | decoder repair, indices, energy block, charge-port bits, spreads |
| Gateway energy | 30 | 4 | 12 V power, 3 unknown gateway DIDs |
| Climate | 27 | 8 | CO2/windshield repair, recirculation, PTC, seat heating, A/C flag |
| Electric drive | 26 | 1 | 25-signal float32 repair |
| Vehicle info | 21 | 2 | gear, VIN |
| OBC | 5 | 0 | no response; outside this catalogue |
| DC/DC | 2 | 1 | derived output power |
| GPS | 2 | 2 | fix block, time |
| ABS | 0 | 4 | wheel speeds (needs `0x10 03`) |
| Unidentified | — | 1 | positive DIDs with no layout |
| **Total** | **152** | **35** | |

Baseline status of the candidates:

| Status | Count |
|---|---:|
| `not_in_baseline` | 10 |
| `profile_supported_not_selected` | 6 |
| `python_project_only` | 5 |
| `derived` | 4 |
| `decoder_repair` | 4 |
| `already_selected` | 3 |
| `already_selected_subfield_missing` | 3 |

---

## C. Prioritised catalogue

The machine-readable catalogue is `candidate_measurements.csv` (35 rows, 29
columns). It includes the DID, address, layout, unit, baseline status, evidence
level, sources, raw example, decoded example, seven 1–5 scores, validation
method, and safety notes.

### C.1 Scoring

Each candidate is scored 1–5 on:

- relevance to the electric-drive warning (25%);
- relevance to the low-voltage investigation (15%);
- strength of MEB evidence (15%);
- accessibility on this car (15%);
- decoder reliability (10%);
- diagnostic value (10%);
- cost/risk, inverted (10%).

`priority_score = 20 × weighted average`, so the range is 0–100. Bands:
P1 ≥ 80, P2 65–79.9, P3 50–64.9, P4 < 50. Rejected items are kept at the
bottom.

### C.2 Top 10

| Rank | Band | Candidate | Type | Score |
|---:|---|---|---|---:|
| 1 | P1 | Electric-drive float32 family (25 signals) | decoder repair | 91.0 |
| 2 | P1 | Pack current sign and documentation | decoder repair | 83.0 |
| 3 | P1 | 12 V battery power (V × A) | derived | 83.0 |
| 4 | P1 | Cell number of highest/lowest voltage | additional signal | 81.0 |
| 5 | P1 | Cell voltage spread (max − min) | derived | 81.0 |
| 6 | P1 | Cell SOC spread (max − min) | derived | 81.0 |
| 7 | P1 | DC-DC output power (V × A) | derived | 80.0 |
| 8 | P2 | Temperature-sensor number of highest/lowest temperature | additional signal | 76.0 |
| 9 | P2 | Accelerator pedal position (rename `RES(PID_0149)`) | alias | 72.0 |
| 10 | P2 | Battery temperature spread (max − min) | derived | 71.0 |

### C.3 P2/P3 highlights

- `0x1E32` four-field lifetime counters (P2, 68.0) — high-confidence decoder fix.
- Charge-port bits `0x7448` bit 2/3 (P2, 67.0) — cheap, useful context.
- Gear `0x210E` (P2, 67.0) — already collected.
- Gateway `0x2A26/0x2A28/0x2A2F` (P3, 64.0) — research, payload recovery first.
- ABS wheel speeds (P3, 61.0) — real new DIDs, but `0x10 03`.
- GPS fix block (P3, 58.0) — context/location, multiframe.
- CO2/windshield (P4, 48/46) — quarantine.
- Seat heating (P4, 40.0) — comfort only.
- Unidentified DIDs (P4, 25.0) — backlog, not actionable.
- BMS `0xF802` VIN — rejected.

---

## D. Evidence report

Full detail: `evidence_report.md`, with the decoder-specific audit in
`decoder_audit.md`.

### D.1 Strongest evidence

| Claim | Evidence | Confidence |
|---|---|---|
| 25 eldrive rows are float32 | Profile formula + four-byte raw + plausible values across 25 rows | **Very high** |
| `0x1E32` Ah at 0/4, kWh at 8/12 | Profile + spot2000 + internal Ah × voltage arithmetic | **High** |
| Cell/sensor indices are 1-based at bytes 2–3 | 31 same-timestamp comparisons, tie-aware | **High** |
| Physical pack current is negative on discharge | `0x1E3D`, `0x1E3E`, `0x035E` all negative at rest; counter direction | **High** |
| Windshield value is unusable | ~488,500 °C; five-byte payload | **High** that it is wrong |
| BMS `0xF802` unsupported | 63 attempts, 0 successes, NRC `0x31` | **High** |
| CO2 current value is wrong | 25,400 ppm vs 1,022 ppm u16; field not proven | Medium |
| `0x5867` scale is wrong | 11,150 km vs ~111,020 km on three odometers | Medium |
| ABS wheel speeds exist | Profile + passive positive responses; no values | Medium |
| Gateway unknowns are useful | Positive responses; no layout | Low/medium |

### D.2 The sign contradiction, in one table

| Source | At rest | Convention |
|---|---:|---|
| `0x1E3D` raw `000248FD` | -2.43 A | Our decoder |
| `0x1E3E` raw `000248FD` | -2.43 A | Our decoder |
| `0x035E` raw `FFED` | -1.9 A | Vehicle-info HV current |
| BMS discharge counter | rising | Direction of energy flow |
| Car Scanner formula | +2.43 A | Applies `* -1` |
| ABRP equation | +2.43 A | Applies `* -1`; malformed shift |

The physical evidence and the independent ECU agree. The display formulas do
not. **Fix the label, not the arithmetic.**

### D.3 Evidence levels used

`verified_on_vehicle`, `supported_by_source`, `observed_positive_undecoded`,
`inferred`, `speculative`, `rejected`. A positive `0x62` response is not a
decoder; a profile `VERIFIED` flag is not vehicle-specific proof; a stale DB
value is not current state.

### D.4 Timeline / provenance

- Car Scanner profile extraction: existing project artifact, 308 rows.
- spot2000 list: core list 2021-06-22; latest repo commit 2022-10-26.
- evDash: master source fetched 2026-10-20.
- ABRP: `main` source fetched 2026-10-20.
- ELMduino issue #207: opened 2023-12-17, closed; no new PIDs.
- OVMS/raimuucka: no directly usable MEB catalogue.
- Live DB: 2026-10-05 to 2026-10-19 (real record), queried read-only.
- Passive capture inventory: 2026-10-15; original btsnoop not present.

---

## E. Validation roadmap

Full detail: `validation_roadmap.md`.

### E.1 Phases

| Phase | What | Vehicle? |
|---|---|---|
| V0 | Offline regression against stored bytes | No |
| V1 | Recover archived/passive payloads | No |
| V2 | Minimal read-only `0x22` checks | Yes, explicit approval |
| V3 | State comparisons (charge/discharge, HVAC, cold soak) | Yes, explicit approval |
| V4 | Production decoder/catalogue changes | No (code) |
| V5 | Monitoring and trend review | Yes, passive/approved |

### E.2 Highest-value offline tests (can be done now)

1. Float32 vectors for all 25 rows, including negative/near-zero and short
   payloads.
2. `0x1E32` full 16-byte block and short-block failure.
3. Cell/temp index fixtures from `offline_crosscheck.csv`, tie-aware.
4. Pack-current sign: `000248FD` → -2.43, `FFED` → -1.9.
5. CO2: record both byte1 and u16 hypotheses; no unilateral choice.
6. Windshield: flag invalid; preserve five bytes.

### E.3 Vehicle-test gates

- Explicit owner approval and a reviewed DID list.
- Default `0x22` only; no `0x2E`, `0x31`, `0x27`, `0x14`, coding, adaptation,
  routine or actuator test.
- `0x10 03` (ABS) is a separate, high-risk opt-in. Do not combine with other
  modules or any write.
- One variable per experiment; raw logging; abort on a new warning/DTC/module
  drop/unexpected NRC.
- Minimum 3 observations per state for decoder decisions; 10 for sign/scale.

### E.4 First live checks, if approved later

1. Eldrive float reproduction (`0x4FAE`, `0x3E93`).
2. Pack current sign in parked/HVAC/charge states.
3. `0x1E32` before/after a known charge.
4. Cell/temp indices against a sweep.
5. Charge-port bits and recirculation.
6. CO2/windshield only after a known state.

ABS wheel speeds remain deferred until a separate approval.

---

## F. Recommended Car Scanner additions

Full detail: `car_scanner_recommendations.md`.

### F.1 Select existing profile rows now

The installed profile already has the missing sub-fields. Select:

1. `[8C.BMS] HV Battery cell # with highest voltage` (`0x1E33` SBI 2).
2. `[8C.BMS] HV Battery cell # with lowest voltage` (`0x1E34` SBI 2).
3. `[8C.BMS] Battery maximum temperature Sensor ID` (`0x1E0E` SBI 2).
4. `[8C.BMS] Battery minimum temperature Sensor ID` (`0x1E0F` SBI 2).
5. `[8C.BMS] Total accumulated charge (Ah)`.
6. `[8C.BMS] Total accumulated discharge (Ah)`.
7. `[8C.BMS] Total accumulated charge (kWh)`.
8. `[8C.BMS] Total accumulated discharge (kWh)`.
9. `[8C.BMS] Charging: Normal charge port` (bit 2).
10. `[8C.BMS] Charging: Rapid charge port` (bit 3).

Name trap: the rows with `#`/`Sensor ID` are the **indices**; the similarly
named rows without them are the **values**. Select both where useful, but do
not confuse them.

### F.2 Add after validation

- HVAC recirculation `0x263B`.
- GPS fix `0x2430` and time `0x22B3` (multiframe).
- PTC air-heater current `0x0801` (layout unknown).
- Seat heating `0x3ADF`/`0x3AE0` (comfort only).
- Gateway `0x2A26/0x2A28/0x2A2F` only after payload recovery.
- ABS `0x1800`–`0x1803` only with explicit `0x10 03` approval.

### F.3 Do not trust

- Windshield temperature (invalid value).
- CO2 (implausible; unresolved).
- `0x5867` odometer (use `0x02BD`, `0x295A`, `0xF817`).
- BMS `0xF802` (NRC `0x31`).
- Car Scanner's pack-current sign (it negates).
- `RES(PID_0149)` as a mystery PID (it is the accelerator pedal).
- `DC-DC converter reserved current` (243–252 A, not a current).

### F.4 Focused logging set

Split into “drive/HV” and “LV/charging” sessions rather than selecting all 152.
Include eldrive float signals, BMS extrema/indices/spreads, 12 V block, DCDC block,
speed/gear/odometer, and charge-port bits.

---

## G. Python-project integration recommendations

Full detail: `python_integration.md`.

### G.1 Code changes, in order

1. **Float32 decoder** for the 25 eldrive rows. Use
   `struct.unpack(">f", raw[:4])`; reject short payloads.
2. **Cell/sensor index outputs** from `0x1E33`/`0x1E34` and
   `0x1E0E`/`0x1E0F` bytes 2–3; 1-based; tie-aware.
3. **Derived power** `aux_12v_power_w`, `dcdc_output_power_w`; plus
   `cell_voltage_spread_mv`, `cell_soc_spread_pct`, `battery_temp_spread_c`.
4. **`0x1E32` four-field decoder**: Ah @0/4, kWh @8/12, signed discharge,
   correct scales.
5. **Pack-current metadata/docs**: keep the arithmetic; document negative =
   discharge; add `sign_convention`.
6. **Rename** `res_pid_0149` → `accelerator_pedal_pct`.
7. **Quarantine** CO2, windshield, `odometer_2`, `dc_dc_converter_reserved_current`,
   `bat_charging_voltage` with `quality=unverified` and a reason.
8. **Remove/mark unsupported** BMS `hv_serial_vin`; keep vehicle-info VIN.
9. **Promote `cell_soc_max/min`** numeric value/index out of `text_value` JSON.

### G.2 Schema

- `evidence_level`, `source`, `sign_convention`, `quality`, `reason`.
- Keep raw bytes for every read.
- Tag imported CSV rows separately from raw hex.
- Do not poll unsupported DIDs (BMS `0xF802`).
- Keep session changes (`0x10 03`) outside the normal collector.

### G.3 Tests

Use real raw vectors: the float32 table, the 16-byte energy block, the
cell/temp index fixtures, the pack-current pair, and the NRC `0x31` case.
Re-decode a copy of the last N sessions and review distributions before merging.

### G.4 Do not

- Do not add `* -1` to pack current.
- Do not change `0x1E32` offsets without the four-field test.
- Do not keep float32 as u16.
- Do not add ABS to the normal collector without separate approval.
- Do not blindly probe `0x2A26/28/2F`.
- Do not infer a fault from one cell index, GPS location, or gear.

---

## H. Limitations and open questions

1. The original btsnoop HCI log is not present; gateway unknown payloads cannot
   be recovered locally.
2. There is no controlled charge/discharge session for the pack-current sign or
   energy counters; evidence is parked/imported plus an independent ECU.
3. CO2 and windshield need known-state tests.
4. ABS was not observed by the Python collector; the profile and passive
   capture are the only evidence.
5. GPS `0x2430`/`0x22B3` are source-supported, not vehicle-verified.
6. OBC has zero successful responses (211 requests); that root cause is outside
   this catalogue.
7. No VW service documentation was used; all external sources are community /
   open-source.
8. The warning is a manually recorded symptom, not a DTC. Association with any
   measurement is not causation.

Open items are tracked in `research_backlog.md` and `unresolved_questions.md`.

---

## I. QC, preservation and handover

### I.1 QC performed

- Baseline: **152** logical rows, category counts
  BMS 39 / energy 30 / climate 27 / eldrive 26 / vehicle 21 / OBC 5 / DCDC 2 /
  GPS 2.
- Candidate CSV: **35** rows, **29** columns, unique IDs, all scores 0–100,
  one rejected item, no duplicate names.
- Profile counts: 308 rows / 305 signals / 255 DIDs / 250 overlap / 4 real
  profile-only DIDs / 1 composite / 3 registry-only.
- Offline cross-check: 31 same-timestamp observations; tie-aware index match
  as reported.
- Targeted project tests:
  `python -m pytest tests/test_decoder_vectors.py tests/test_catalogue.py tests/test_meb_path.py -q`
  → **80 passed**.
- No production file was edited. `git status` shows only `research/` as
  untracked (plus any pre-existing unrelated state; verify before commit).

### I.2 Preservation

All original logs, decoders, databases, profiles and tests are unchanged. The
new files are additive under `research/measurement_discovery/`. The live DB was
opened read-only (`mode=ro`). No vehicle connection was made.

### I.3 Handover files

| File | Purpose |
|---|---|
| `final_report.md` | This report (A–G) |
| `existing_vs_candidates.md` | Baseline vs candidate comparison |
| `candidate_measurements.csv` | Machine-readable ranked catalogue |
| `baseline_inventory.csv` | 152 logical baseline entries |
| `profile_coverage.csv` | Per-signal status for all 305 profile signals |
| `evidence_report.md` | Evidence hierarchy and source assessment |
| `decoder_audit.md` | Decoder findings, raw examples, proposed fixes |
| `validation_roadmap.md` | V0–V5 tests, gates, abort criteria |
| `car_scanner_recommendations.md` | What to select/add/avoid in Car Scanner |
| `python_integration.md` | Code/schema/test recommendations |
| `offline_crosscheck.csv` | 31-timestamp cell/temp index comparison |
| `source_inventory.csv` | Sources, local copies, hashes, authority, limits |
| `research_backlog.md` | Open research items |
| `progress_log.md` | Session log |
| `unresolved_questions.md` | Questions that need evidence |
| `research_plan.md` | Original plan and evidence-level definitions |
| `scripts/analyse_profile.py` | Reproducible profile analysis |
| `scripts/build_profile_coverage.py` | Per-signal coverage mapping |

### I.4 Recommended next action

If the owner approves only one code change: **fix the float32 family and add
its regression vectors.** If the owner approves one vehicle test: **reproduce
the float32 coolant/DC-link values and the pack-current sign in a parked,
read-only session.** Do not start with ABS or the unknown gateway DIDs.
