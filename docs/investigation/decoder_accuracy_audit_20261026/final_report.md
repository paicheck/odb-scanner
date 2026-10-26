# Decoder accuracy audit — final report

**Audit date:** 2026-10-26
**Vehicle:** 2021 VW ID.3 58 kWh Pro (MEB), profile `meb`
**Repository:** `C:\Users\kings\OneDrive\Documents\odb_scanner`
**HEAD:** `bd86d55dd316c1d5bff659e892719d508189bc3c`
**Mode:** fully offline. No adapter was opened, no diagnostic request was sent,
and no vehicle session, security access, routine, or actuator was attempted.

Companion documents:

- [`progress.md`](progress.md) — chronological ledger and commands;
- [`findings.md`](findings.md) — F1–F18, severity and disposition;
- [`decoder_inventory.md`](decoder_inventory.md) — full 289-key inventory;
- [`evidence_register.md`](evidence_register.md) — evidence IDs, hashes,
  limitations.

---

## A. Overall findings

The audit found **three demonstrable defects**, fixed all three, and added a
regression test for each. It found **no evidence of a widespread scale or
endianness error** in the remaining decoder set, but it also found substantial
unvalidated surface: 244 catalogue keys have no declared byte layout, 209 keys
are not named in any test, and only two keys have VERIFIED catalogue
confidence.

### A.1 Current inventory

| Metric | Count |
|---|---:|
| Measurement specs | 289 |
| Distinct DID numbers | 255 |
| Distinct (ECU, DID) associations | 255 |
| Decoded specs | 288 |
| Raw-only specs | 1 |
| Catalogue keys | 289 |
| Keys with declared byte layout | 45 |
| Keys without declared layout | 244 |
| Keys without unit | 13 |
| Catalogue VERIFIED | 2 |
| Catalogue not VERIFIED | 287 |
| Derived keys | 9 |
| INVALID keys | 5 |
| Keys excluded from scoring | 5 |
| Keys named anywhere in `tests/` | 80 |
| Keys not named in `tests/` | 209 |
| Catalogue validation problems | 0 |

The current tree contains **289** specs because a concurrent autonomous-discovery
pass added gateway DIDs `0x2A25` and `0x2A0E`. Those two specs, their catalogue
overlay, the regenerated spec/asset, and `tests/test_gateway_resistance.py` are
preserved and are **not** claimed as audit work. The audit baseline was 287
specs; this report distinguishes baseline, current, and audit-caused deltas
throughout.

### A.2 Quality distribution

The quality layer has **298 entries**: the 289 catalogue keys plus 9 derived
aliases that are calculated in `collector.py` and have no DID payload of their
own. It is not a 298-key catalogue.

| Verification | Keys |
|---|---:|
| VERIFIED | 2 |
| PROVISIONAL | 51 |
| UNKNOWN | 240 |
| INVALID | 5 |
| **Total quality entries** | **298** |

| Data kind | Keys |
|---|---:|
| numeric | 282 |
| derived | 9 |
| JSON | 4 |
| text | 3 |
| **Total quality entries** | **298** |

The two VERIFIED keys are the gateway energy DIDs `hv_energy_content`
(`0x2AB8`) and `hv_energy_max` (`0x2AB2`). Their addressing, frame shape,
offsets, and multipliers are confirmed by captured bytes; the gross-vs-usable
*semantic* meaning remains profile-derived. "VERIFIED" here is a statement about
the decoder arithmetic and addressing, not independent certification of every
physical label.

### A.3 Bottom line

- The current decoder reproduces **every historical real-car row it can decode**
  (440/440 unchanged).
- Historical drift in `data/odb_scanner.db` (494 changed, 17 newly decoded, 41
  now failed) is explained by older stored decoders, not by new regressions.
- The five INVALID keys are quarantined for good evidence-based reasons and were
  not "fixed" by guesswork.
- The remaining risk is coverage and source interpretation, not a demonstrated
  arithmetic defect.

---

## B. Priority defects

### B.1 Fixed

| Priority | ID | Defect | Impact | Fix | Regression test |
|---|---|---|---|---|---|
| P1 | F1 | Android gear map disagreed with Python and sign-extended byte 1 | `000C` (B) displayed as `"raw 12"`; R/N/D map was incompatible | `Decode.kt` now mirrors `_gear` exactly, masks `and 0xFF`, and uses the whole-record fallback | `DecodeTest.gearMatchesPython` |
| P1 | F2 | Replay called `float("P")` on text rows | `tools/replay_validate.py` aborted before results | `observations_from_measurements` parses once and skips non-numeric values | `test_text_valued_rows_are_skipped_not_crashed_on` |
| P2 | F3 | `cell_voltage_sum` (DID `0x1E3C`) was in `_DERIVED_KEYS` | Decoder lab skipped a real DID; no structural check of its stored payload | Removed the singular key; plural `cells_voltage_sum` remains derived | `test_did_backed_cell_voltage_sum_is_not_labelled_derived` |

Why these are demonstrable rather than speculative:

- F1 has two captured `000C -> B` rows and 21 `0008 -> P` rows; the old Kotlin
  output for B is directly wrong.
- F2 is a reproducible crash on text values that legitimately exist in the
  `measurements.value` TEXT column. The fix makes the validator complete
  (PASS 7, INDETERMINATE 2).
- F3 is a categorical misclassification: `0x1E3C` is a read DID. The plural
  name is the imported Car Scanner alias and is genuinely derived.

No byte layout, scale, sentinel rule, INVALID flag, scoring exclusion, or
read-only guard was changed by these fixes.

### B.2 High-risk quarantines retained

| Key | DID | Why it stays INVALID |
|---|---|---|
| `bat_charging_voltage` | `0x1E39` | Signed/sentinel field; `FFFF` previously fabricated 2,047.97 V. Top of charging range needs a charging session. |
| `co2_content_interior` | `0x42DB` | 40/40 stored responses are `03FE`, the BMS not-fitted sentinel; non-sentinel scale unresolved. |
| `dc_dc_converter_reserved_current` | `0x2A53` | Bytes 3–4 are the complement of the real current, not an independent current; decoding it fabricated ~250 A. |
| `odometer_2` | `0x5867` | 10 km/LSB secondary counter; x10 delta fits, x100 refuted; absolute zero is ~99,870 km offset and not usable. |
| `windshield_temperature` | `0x27C4` | Profile u16 layout arithmetically refuted; byte 0 × 0.1 is plausible but sensor identity (windshield vs ambient) is unconfirmed. |

All five remain excluded from scoring. Their decoder paths preserve raw bytes
and return `None` where the evidence says "no value".

---

## C. IEEE-754 and numeric audit

### C.1 Representation

The generated spec contains **118 scalar recipes**, **167 special decodes**, and
**4 null decodes**.

- 118/118 scalar recipes are big-endian.
- Widths: 1 byte (41), 2 bytes (67), 3 bytes (2), 4 bytes (8).
- 15 scalar recipes are signed.
- 25 special `float32` recipes, all 4-byte big-endian at offset 0.
- 108 `cell_voltage` specials, 24 `cell_temp` specials, plus
  pair/ASCII/bits/JSON/gear/VIN/energy/coolant specials.

No scalar was found with little-endian order, an inconsistent byte order, or an
implicit width conversion that could overflow.

### C.2 Float32 family

The 25 electric-drive `float32` keys are decoded by `_f32be`, which:

- requires exactly four bytes at the requested offset;
- unpacks `>f` (big-endian IEEE-754 single precision);
- returns `None` for NaN or infinity;
- raises `IndexError` for a short payload rather than padding it.

`eldrive_odometer` is the documented exception: a 4-byte integer, not a float.

The old `u16 / 10 + 1` interpretation was physically wrong (433.19 V became
~1,738 V; ~16 °C became ~1,682 °C). The current implementation is correct for
the captured bytes. No new float32 defect was found.

### C.3 Integer and energy arithmetic

`0x1E32` uses four signed 32-bit fields at offsets 0/4/8/12. The energy decoder:

- uses `int.from_bytes(..., signed=True)`;
- rejects payloads shorter than 16 bytes with `IndexError`;
- treats charge as positive and discharge as the magnitude of the signed field;
- applies `0.001820444117116` Ah/count and `116508.398 / 1e9` kWh/count.

The old decoder reused the Ah offsets as kWh with evDash's
`8583.07123641215 Wh/count`; that is refuted by the field layout and by the
values (~3,724/3,700 kWh vs ~24,437/23,460 kWh from the same bytes).

### C.4 Unusual constants

One non-round scale was flagged:
`0.0312499784501336` on `bat_v_after_contacts_2` and
`bat_v_before_contacts`. It is within 6.9 × 10⁻⁷ relative error of 1/32 V and is
declared by the Car Scanner source. There is no captured independent voltage
that would justify replacing it. It was left unchanged rather than "cleaned
up".

No scalar's plausibility range is disjoint from its encoding range. Five ranges
extend beyond the encoding range (12 V voltage, two battery temperature
extremes, secondary odometer, speed); these are intentional physical or
administrative bounds, not arithmetic errors.

---

## D. Scaling, units, and sentinels

### D.1 Sentinel rules

Seven explicit sentinel/guard occurrences remain:

| Where | Rule | Meaning |
|---|---|---|
| Python | `CELL_UNUSED = 0x0FFE` | unpopulated cell slot |
| Python | `CELL_T_MAX = 1020` | cell-temperature sensor absent |
| Python | `0x1E39` chunk in `FFFF`/`FFFE` | charging-voltage sentinel |
| Python | `0x1E39` negative value | reject negative charging voltage |
| Python | `0x42DB` value `0x03FE` | CO2 not-fitted sentinel |
| Kotlin | `CELL_UNUSED = 0x0FFE` | unpopulated cell slot |
| Kotlin | `CELL_T_MAX = 1020` | cell-temperature sensor absent |

The guards are not cosmetic. Without the cell-temperature guard, an absent
sensor decodes to ~500 °C and poisons pack statistics. Without the `0x1E39`
guard, `FFFF` becomes 2,047.97 V. Without the CO2 guard, `03FE` becomes 25,400
ppm. Each case returns no value instead.

### D.2 Units

Thirteen catalogue keys have no unit. Twelve were audited as scalar keys:

`air_polution_level_0_clean_air`, `bat_internal_resistance_raw`,
`bat_temp_max_sensor`, `bat_temp_min_sensor`, `cell_voltage_max_cell`,
`cell_voltage_min_cell`,
`fresh_air_recirculation_flap_actual_value_steps`,
`fresh_air_recirculation_flap_specified_value_steps`,
`fresh_air_recirculation_flap_stop_for_fresh_air`,
`fresh_air_recirculation_flap_stop_for_recirculated_air`,
`number_of_tracked_satellites`, `number_of_visible_satellites`.

These are counts, indices, flags, flap steps, or an explicitly raw resistance
index. The thirteenth, `bat_internal_resistance_raw`, is unitless because its
source labels the unit "unknown"; adding "mOhm" would overclaim the evidence.

The unit/name screening flags `average_battery_consumption_internal_value` as
unit `A` for a name containing "consumption". That is how the source declares
this internal value. No evidence supports a replacement scale/unit, so it was
left unchanged and recorded as unresolved rather than guessed.

### D.3 Scale decisions

- `0x2AB2` uses `u16be[0:2] × 50 Wh`; `0x2AB8` uses
  `u16be[2:4] × 25 Wh`. Both offsets and multipliers are confirmed by the
  passive HCI capture.
- The old `u32be / 1310.77` gateway layout is refuted: 41,325 Wh requires a
  non-integer raw count.
- `0x1E32`, the float32 family, `0x2A53`, `0x5867`, `0x27C4`, `0x1E39`, and
  `0x42DB` are covered in B and in `findings.md`.

No scale was changed on the basis of plausibility alone.

---

## E. Derived metrics and quality classification

### E.1 Derived metrics

After F3, nine keys are classified as derived:

`cell_delta_mv`, `cell_soc_spread_pct`, `battery_temp_spread_c`,
`pack_power_kw`, `soh_pct`, `cells_voltage_sum`, `aux_12v_power_w`,
`dcdc_power_kw`, `dcdc_gw_power_kw`.

`cell_voltage_sum` is now numeric because it is read from DID `0x1E3C`; the
plural `cells_voltage_sum` remains the imported Car Scanner alias and is
derived. This is the only data-kind change made by the audit.

### E.2 Quality and scoring

- Five keys are INVALID and five are excluded from scoring. The sets are the
  same five listed in B.2.
- The audit did not promote any key to VERIFIED.
- The audit did not remove an INVALID flag or change a plausibility range.
- The decoder lab now evaluates 289/289 catalogue keys; previously the singular
  cell-voltage sum was invisible to it.

"PROVISIONAL" means the definition has a source but lacks a project-owned
capture or known-state test. "UNKNOWN" means the layout/scale is not
independently established. "INVALID" means the current key is known not to be
usable for its stated quantity.

---

## F. Raw data integrity

### F.1 Payload checks

| Metric | `real_car.db` | `odb_scanner.db` |
|---|---:|---:|
| Total measurement rows | 4,057 | 20,798 |
| Successful rows | 923 | 11,067 |
| Successful rows with raw | 920 | 10,917 |
| Imported Car Scanner text rows | 266 | 266 |
| Non-hex vehicle raw | 0 | 0 |
| Odd-length hex | 0 | 0 |
| Duplicate `(key, ts)` with different raw | 0 | 0 |
| Failed rows with raw | 0 | 0 |
| `PRAGMA integrity_check` | `ok` | `ok` |

The 3 successful rows without raw in `real_car.db` are calculated values; the
150 in `odb_scanner.db` are derived/calculated. Text-valued keys
(`coolant_temps`, `cell_soc_max/min`, `energy_counters`) are kept as text/JSON
and are excluded from the numeric replay path by F2.

### F.2 Historical re-decode

- `real_car.db`: 440 raw vehicle rows re-decoded, **0 changed, 0 newly decoded,
  0 now failed**.
- `odb_scanner.db`: 10,419 raw vehicle rows re-decoded, **494 changed,
  17 newly decoded, 41 now failed**. These are old values produced by older
  decoders; the database was not rewritten.

The real-car result is the clean end-to-end check. The collector-database drift
is the audit trail of repairs that were already implemented before this session
(float32 family, `0x1E32`, `0x27C4`, `0x1E39`, `0x42DB`, text/JSON exposure).

### F.3 Database immutability caveat

`data/real_car.db` retains its pre-audit byte hash
(`181B4D1C…D97DE9`). `data/odb_scanner.db` is currently held open by a
concurrent dashboard server, which created a WAL/SHM pair; its main-file hash
moves when SQLite checkpoints or rewrites free pages (observed
`ED574EA5…647B17`, then `64ECA8DA…84550`). The pre-audit ledger recorded
`AFD482EC…CE463C`.

This audit did not issue a write to either production database. The audit
scripts use `mode=ro`; `Repository` was used only on temporary copies. A
table-by-table comparison through SQLite (including the WAL) against the 20:24
working copy found **zero schema differences and zero row differences**. At the
pre-WAL checkpoint, a page comparison found only pages 1–2 differ, in
free-space/header bookkeeping. That is consistent with a SQLite rewrite by a
concurrent process, not a content edit, but the exact writer cannot be
attributed because another server was active in the same tree. The integrity
check is `ok`. This caveat is recorded rather than hidden; the raw data itself
is unchanged.

---

## G. Android parity

### G.1 Synced definitions

- `spec/odb_scanner.json` and
  `android/app/src/main/assets/spec.json` are byte-identical:
  SHA-256 `483054ED324A36C35BE62A470531BDA99972DE0C1C1FD3D1149B344CAC6EF1D6`,
  265,519 bytes.
- Both contain 289 keys.
- Zero keys exist in only one file; zero measurement bodies differ.
- All 12 special decode names used by the spec have a Kotlin `when` case.

### G.2 Gear parity fix

F1 aligns Kotlin with Python for P/R/N/D/B, masks byte 1 unsigned, and uses the
whole-record fallback. `DecodeTest.gearMatchesPython` covers the captured P/B
values and the byte-1 top-bit case.

### G.3 Quarantine gap

Three Python special decoders have no Kotlin equivalent:

- `bat_charging_voltage`
- `co2_content_interior`
- `windshield_temperature`

Their exported `decode` is `null`, so Android returns no value rather than a
wrong one. This is a parity gap, not an accuracy fix. Implementing them would
require a deliberate spec/asset change and would still leave the underlying
keys quarantined. It is recorded as future work, not silently "fixed".

---

## H. Test evidence

| Check | Result |
|---|---|
| Baseline Python suite | **777 passed** in 143.59 s |
| Full Python suite at audit close | **805 passed** in 172.37 s |
| Targeted audit tests | **99 passed** in 13.05 s |
| Spec export sync | **3 passed** in 9.81 s |
| Ruff | `All checks passed!` |
| Android JVM tests (offline, re-run) | **52 tests, 0 failures** |
| Android `DecodeTest` | 11 tests, 0 failures |
| Catalogue validation | **0 problems** |
| Lab coverage | **289/289** keys evaluated |
| `real_car.db` replay | PASS 142 / INDETERMINATE 142 / FAIL 5 |
| `odb_scanner.db` lab | PASS 156 / INDETERMINATE 103 / FAIL 30 |
| HV energy replay | 42 records, max error 0 |

The 28-test increase from 777 to 805 is fully accounted for: 14 tests in the
concurrent `tests/test_gateway_resistance.py`, 12 in the concurrent
`tests/test_timeline_investigation.py`, and 2 Python regression tests from this
audit (F2, F3). The Android gear test is in the separate 52-test JVM suite. No
existing test was weakened or deleted.

The five failures in the real-car lab are exactly the five INVALID keys. The 30
old-database failures are 25 historical stored-vs-current mismatches plus the
same five INVALID keys.

---

## I. Changed files

### I.1 Audit changes

| File | Change |
|---|---|
| `analysis/replay.py` | F2: skip non-numeric text rows in numeric observations |
| `decoders/quality.py` | F3: remove `cell_voltage_sum` from `_DERIVED_KEYS` |
| `tests/test_catalogue.py` | F2 regression test |
| `tests/test_platform.py` | F3 regression test |
| `android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt` | F1: Python-parity gear decoder |
| `android/app/src/test/kotlin/com/odbscanner/spec/DecodeTest.kt` | F1 regression test |

Audit artefacts added under `docs/investigation/decoder_accuracy_audit_20261026/`:

`progress.md`, `findings.md`, `decoder_inventory.md`, `evidence_register.md`,
`final_report.md`, `inventory.py`, `inventory_baseline.json`,
`inventory_current.json`, `inventory_md.py`, `numeric_audit.py`,
`numeric_audit.json`, `lab_sweep.py`, `lab_sweep.json`, `impact_real_car.*`,
`impact_odb.*`, `replay_validate.md`, `replay_validate.json`.

No commit or push was made.

### I.2 Preserved, not claimed

- Pre-existing `.gitignore` change and `cloud/` deployment files.
- The autonomous-discovery pass's `0x2A25`/`0x2A0E` changes, generated
  spec/asset changes, and `tests/test_gateway_resistance.py`.
- Concurrent dashboard/DTC/timeline work in `web/`, `database/repository.py`,
  `analysis/dtc.py`, and related tests/artefacts.

---

## J. Outstanding questions

These cannot be resolved offline with the stored evidence. No live action was
taken.

| # | Question | Evidence needed | Current disposition |
|---:|---|---|---|
| J1 | What is the top of the `0x1E39` charging-voltage range, and is `FFFF` a sentinel, a signed negative value, or both? | A real charging session | INVALID/excluded |
| J2 | Is `0x42DB` a whole-u16 ppm sensor, a byte-1 ×100 sensor, or not fitted? | Fresh-air/occupied-cabin sequence with a non-`03FE` frame | INVALID/excluded |
| J3 | What does `0x2A53[3:5]` mean independently of the complement? | Known-load DC-DC capture where the field can move independently | INVALID/raw-only |
| J4 | What is the absolute zero of `0x5867`? | Service history or a capture spanning a known BMS reset | INVALID/excluded; x10 delta only |
| J5 | Is `0x27C4` byte 0 the windshield surface or an ambient sensor? | Cold-soak/defroster comparison against known glass/ambient | INVALID/excluded |
| J6 | What are `0x2A26`, `0x2A28`, `0x2A2F`? | Recovery of original HCI payloads or an approved bounded capture | Raw-only/quarantined |
| J7 | Do gear R/N/D decode as defined on this car? | A capture in each selector state | Shared map retained; Android now matches |
| J8 | Is `average_battery_consumption_internal_value` really unit `A`? | Independent consumption measurement or source confirmation | UNKNOWN; not changed |
| J9 | Are the profile-derived scales physically calibrated? | Known-state measurements for the affected keys | PROVISIONAL/UNKNOWN |

The audit deliberately did not convert any of these into a "fix". A plausible
number is not evidence.

---

## K. Next actions

1. **Preserve the three fixes and tests.** Review and commit them separately
   from the concurrent dashboard/autonomous work when the user is ready. Do not
   squash them into unrelated changes.
2. **Schedule only the vehicle tests that answer J1–J7.** They require explicit
   approval; this audit intentionally performed none. A useful minimum set is:
   charging session, cold-soak/defroster, fresh-air/occupied cabin, known-load
   DC-DC, and a known-distance drive with selector states.
3. **Recover the original HCI payloads for `0x2A26`/`0x2A28`/`0x2A2F`** if
   available. Do not invent layouts for them.
4. **Add payload-vector tests when evidence arrives**, especially for the 209
   unnamed keys. Prefer a small number of captured vectors over name-count
   coverage.
5. **Decide the Android quarantine gap deliberately.** If the three Python
   specials are to exist on Android, add them with explicit null/INVALID
   semantics and a spec/asset regeneration; do not make Android guess.
6. **Keep database reads read-only.** If reproducibility matters, record
   hashes before and after on a quiescent tree; the concurrent pass made the
   byte hash unstable in this session even though logical content was unchanged.
7. **Leave the five INVALID keys excluded** until the evidence in J1–J5 exists.
   Removing any exclusion without that evidence would be a regression in
   honesty, not an improvement in coverage.

### Final statement

The offline evidence supports the current MEB decoder implementation for the
DIDs that have captured bytes. Three concrete defects were found and fixed with
regression tests; the historical drift is explained; the five known-bad keys
remain quarantined; and the unresolved questions are explicitly tied to future
vehicle observations rather than papered over with plausible numbers.
