# Existing 152 vs. candidate measurements

This is the comparison table for the current Car Scanner selection and the
candidate additions. It is deliberately redundant with
`candidate_measurements.csv`: the CSV is the machine-readable catalogue; this
document is the human-readable cross-reference.

## 1. The baseline: 152 logical measurements

The baseline was rebuilt from `build_default_registry("meb")`, not from the
older prose counts. The registry has **282 specs**, which collapse to **152
logical measurements** because the 108 cell voltages and 24 temperature points
are presented as two grouped entries.

| Category | Logical entries | Registry specs | Notes |
|---|---:|---:|---|
| BMS | **39** | 169 | 37 individual + `cell_voltages` (108) + `cell_temperatures` (24) |
| Gateway energy | **30** | 30 | 12 V block, HV energy, ranges, road categories, 15D1 |
| Climate | **27** | 27 | HVAC, CO2, windshield, cabin, compressor, flaps |
| Electric drive | **26** | 26 | 25 float32 rows + `eldrive_odometer` |
| Vehicle info | **21** | 21 | torque, temperatures, speed, gear, VIN, odometer |
| OBC | **5** | 5 | present in profile, but zero successful samples in the current DB |
| DC/DC | **2** | 2 | 0x465B/0x465D |
| GPS | **2** | 2 | satellites only |
| **Total** | **152** | **282** | Matches the owner's category counts exactly |

### Baseline quality is not uniform

| Issue | Affected baseline entries | Evidence |
|---|---|---:|
| Float32 rows decoded as u16 | 25 electric-drive entries | `decoder_audit.md` F1 |
| `0x1E32` offsets/units | 1 entry (`energy_counters`, plus imported aliases) | F2 |
| Extreme cell/sensor index discarded | 2 entries (`cell_voltage_max/min`, `bat_temp_max/min`) | F3 |
| Pack-current sign documentation | 2 entries (`pack_current`, `pack_current_2`) | F4 |
| CO2 field/width | 1 entry | F5 |
| Windshield field/scale | 1 entry | F6 |
| `0x5867` odometer scale | 1 entry | F7 |
| BMS `0xF802` unsupported | 1 entry | F8 |
| Battery serial not decoded | 1 entry | F9 |

The baseline is complete in *count*, but not every entry is trustworthy. The
catalogue therefore distinguishes "already selected" from "already selected and
correctly decoded".

## 2. Profile coverage: 308 rows vs. 152 entries

| Metric | Count |
|---|---:|
| Car Scanner MEB profile rows | 308 |
| Distinct underlying signals (after deduplicating only true aliases) | 305 |
| Distinct DIDs | 255 |
| DIDs in both profile and registry | 250 |
| Real profile-only DIDs | 4 (`0x1800`–`0x1803`, ABS wheel speeds) |
| Profile-only composite pseudo-DID | 1 (`0x1E3B;0x1E3D`, derived DC battery power) |
| Registry-only DIDs | 3 (`0x0500`, `0x210E`, `0xF802`) |

### Per-signal coverage status (305 rows)

The machine-readable mapping is `profile_coverage.csv`. It labels every profile
signal so a name-similarity guess is not mistaken for a verified decoder match.

| Coverage status | Count | Meaning |
|---|---:|---|
| `selected` | 136 | Direct DID/name match in the 152-entry baseline |
| `selected_grouped_cell_voltage` | 108 | Individual cells grouped into `cell_voltages` |
| `selected_grouped_temperature` | 24 | Individual sensors grouped into `cell_temperatures` |
| `selected_extreme_value` | 4 | `0x1E33`/`0x1E34` and `0x1E0E`/`0x1E0F` values |
| `selected_subfield_missing` | 6 | 2 cell numbers + 2 sensor IDs + 2 charge-port bits |
| `selected_decoder_repair` | 4 | The four `0x1E32` Ah/kWh fields |
| `selected_json_only` | 4 | Cell SOC max/min values and indices packed in JSON |
| `selected_quarantine` | 5 | Windshield, CO2, `0x5867`, `0x1E39`, reserved DC-DC current |
| `selected_alias` | 3 | Same DID/quantity under another display name |
| `derived_or_alias` | 7 | Formula rows / composite pseudo-DID |
| `profile_only_abs` | 4 | The only genuinely new profile DIDs |
| **Total** | **305** | |

Two practical consequences:

- **Profile-supported but not independently selected**: 4 ABS rows, 6 subfield
  rows, and 4 lifetime-counter rows. The first group needs `0x10 03`; the other
  two need only better decoding.
- **Selected but not trustworthy**: 4 decoder-repair rows, 4 JSON-only rows,
  and 5 quarantine rows. Selecting a row is not the same as having a usable
  value.

Important nuance: 305 underlying signals do **not** mean 305 new measurements.
The extra rows are mostly:

- 108 cell voltages and 24 temperature points, which the current presentation
  groups into two entries;
- multi-field DIDs (e.g. 10 fields on `0x2AF7`, 7 road categories on `0x2AB7`);
- aliases with different display names;
- seven derived/alias rows (battery temperature delta, cell voltage delta,
  energy content delta, PTC heater kW, potential energy, and two DC battery
  power variants);
- the four ABS DIDs, which really are absent from the Python registry.

So the honest statement is: **the profile contains only four new real DIDs,
but it contains several discarded sub-fields and useful derived rows.** The
"more data" opportunity is mostly *field-level*, not *DID-level*.

### Registry-only DIDs

| DID | Module | Status |
|---|---|---|
| `0x0500` | BMS | Observed; ASCII battery serial, not decoded |
| `0x210E` | Vehicle info | Observed; gear selector |
| `0xF802` | BMS | **Rejected**: NRC `0x31`; the VIN is on vehicle-info `0xF802` |

## 3. Candidate categories

| Candidate type | Count | Meaning |
|---|---:|---|
| Decoder repair | 6 | Existing selected signal is wrong or partly wrong |
| Additional signal | 17 | New DID or new sub-field |
| Derived | 6 | Arithmetic on already selected signals; no new request |
| Alias | 1 | Already selected under an opaque name |
| Unidentified | 4 | Positive response, no usable layout yet |
| Rejected | 1 | Do not pursue |
| **Total** | **35** | |

| Evidence level | Count |
|---|---:|
| `verified_on_vehicle` | 11 |
| `supported_by_source` | 9 |
| `inferred` | 7 |
| `observed_positive_undecoded` | 4 |
| `speculative` | 3 |
| `rejected` | 1 |

| Priority band | Count | Score range |
|---|---:|---|
| P1 | 7 | ≥ 80 |
| P2 | 7 | 65–79.9 |
| P3 | 13 | 50–64.9 |
| P4 | 7 | < 50 |
| Rejected | 1 | — |

### Baseline status of the candidates

| Status | Count | Meaning |
|---|---:|---|
| `not_in_baseline` | 10 | New DID/family, no current coverage |
| `profile_supported_not_selected` | 6 | 4 ABS + 2 charge-port bits |
| `python_project_only` | 5 | Gear, serial, VIN, cell delta, rejected BMS VIN |
| `derived` | 4 | 12 V power, DCDC power, temp spread, SOC spread |
| `decoder_repair` | 4 | Energy counters, CO2, windshield, odometer |
| `already_selected` | 3 | Eldrive family, pack current, accelerator alias |
| `already_selected_subfield_missing` | 3 | Cell index, temp sensor index, A/C on/off |
| **Total** | **35** | |

## 4. Full candidate table

Scores are a decision aid, not measurements. The weights and evidence levels
are defined in `final_report.md` and `evidence_report.md`. Rank is by score;
rejected items are kept at the bottom so they are not silently forgotten.

| Rank | Band | Candidate | Type | Baseline status | Evidence | Score |
|---:|---|---|---|---|---|---:|
| 1 | P1 | Electric-drive float32 family (25 signals) | decoder_repair | already_selected | verified_on_vehicle | 91.0 |
| 2 | P1 | Pack current sign and documentation | decoder_repair | already_selected | verified_on_vehicle | 83.0 |
| 3 | P1 | 12 V battery power (V x A) | derived | derived | inferred | 83.0 |
| 4 | P1 | Cell number of highest/lowest voltage (0x1E33/0x1E34 bytes 2-3) | additional_signal | already_selected_subfield_missing | verified_on_vehicle | 81.0 |
| 5 | P1 | Cell voltage spread (max - min) | derived | python_project_only | verified_on_vehicle | 81.0 |
| 6 | P1 | Cell SOC spread (max - min) | derived | derived | verified_on_vehicle | 81.0 |
| 7 | P1 | DC-DC output power (V x A) | derived | derived | inferred | 80.0 |
| 8 | P2 | Temperature-sensor number of highest/lowest temperature (0x1E0E/0x1E0F bytes 2-3) | additional_signal | already_selected_subfield_missing | verified_on_vehicle | 76.0 |
| 9 | P2 | Accelerator pedal position (rename RES(PID_0149)) | alias | already_selected | verified_on_vehicle | 72.0 |
| 10 | P2 | Battery temperature spread (max - min) | derived | derived | inferred | 71.0 |
| 11 | P2 | 0x1E32 lifetime charge/discharge block (Ah + kWh) | decoder_repair | decoder_repair | verified_on_vehicle | 68.0 |
| 12 | P2 | Normal/AC charge port connected (0x7448 bit 2) | additional_signal | profile_supported_not_selected | supported_by_source | 67.0 |
| 13 | P2 | Rapid/DC charge port connected (0x7448 bit 3) | additional_signal | profile_supported_not_selected | supported_by_source | 67.0 |
| 14 | P2 | Gear selector position | additional_signal | python_project_only | verified_on_vehicle | 67.0 |
| 15 | P3 | Gateway energy-family DID 0x2A26 (unknown) | unidentified | not_in_baseline | observed_positive_undecoded | 64.0 |
| 16 | P3 | Gateway energy-family DID 0x2A28 (unknown) | unidentified | not_in_baseline | observed_positive_undecoded | 64.0 |
| 17 | P3 | Gateway energy-family DID 0x2A2F (unknown) | unidentified | not_in_baseline | observed_positive_undecoded | 64.0 |
| 18 | P3 | Front left wheel speed | additional_signal | profile_supported_not_selected | supported_by_source | 61.0 |
| 19 | P3 | Front right wheel speed | additional_signal | profile_supported_not_selected | supported_by_source | 61.0 |
| 20 | P3 | Rear left wheel speed | additional_signal | profile_supported_not_selected | supported_by_source | 61.0 |
| 21 | P3 | Rear right wheel speed | additional_signal | profile_supported_not_selected | supported_by_source | 61.0 |
| 22 | P3 | HVAC recirculation state | additional_signal | not_in_baseline | supported_by_source | 59.0 |
| 23 | P3 | 0x5867 odometer scaling/field | decoder_repair | decoder_repair | inferred | 58.0 |
| 24 | P3 | GPS fix block (latitude/longitude/altitude/quality/init) | additional_signal | not_in_baseline | supported_by_source | 58.0 |
| 25 | P3 | PTC air-heater HV current | additional_signal | not_in_baseline | speculative | 53.0 |
| 26 | P3 | VIN (vehicle-info 0xF802) | additional_signal | python_project_only | verified_on_vehicle | 52.0 |
| 27 | P3 | A/C compressor on/off (from 0x0800 power) | derived | already_selected_subfield_missing | inferred | 50.0 |
| 28 | P4 | Cabin CO2: u16 vs byte 1 | decoder_repair | decoder_repair | inferred | 48.0 |
| 29 | P4 | Windshield temperature: field and scale unresolved | decoder_repair | decoder_repair | inferred | 46.0 |
| 30 | P4 | GPS time | additional_signal | not_in_baseline | supported_by_source | 46.0 |
| 31 | P4 | HV battery serial (raw ASCII) | additional_signal | python_project_only | verified_on_vehicle | 45.0 |
| 32 | P4 | Left seat heating current/state | additional_signal | not_in_baseline | speculative | 40.0 |
| 33 | P4 | Right seat heating current/state | additional_signal | not_in_baseline | speculative | 40.0 |
| 34 | P4 | Unidentified positive DIDs (misc 11-bit and 18DAF1xx) | unidentified | not_in_baseline | observed_positive_undecoded | 25.0 |
| 35 | Rejected | BMS 0xF802 VIN (NRC 0x31) | rejected | python_project_only | rejected | 50.0 |

## 5. What is *not* a new measurement

Several tempting candidates turn out to be aliases or derived values:

- **Accelerator pedal** `0xF449` is already selected as `res_pid_0149`;
  rename, do not add a second DID.
- **A/C compressor on/off** is derivable from the already selected `0x0800`
  power; the power row is the measurement.
- **Cell voltage delta** is already `cell_delta_mv` in Python; the profile's
  "Cell voltage delta mV" is a presentation alias.
- **DC battery power** (profile composite `0x1E3B;0x1E3D`) is already
  `pack_power_kw`/`pack_power`; do not add a pseudo-DID.
- **Battery temperature delta** and **energy content delta** are arithmetic on
  selected values.
- **Potential energy over sea level** is a physics calculation, not a vehicle
  measurement; it is not recommended.
- **PTC heater battery kW** was considered, but `ptc_voltage` reads a constant
  14.7 V in the current data, so the product is not established as an HV power.
  It is deliberately left out of the catalogue.

## 6. What "additional" really means here

The highest-value additions are not exotic new DIDs. They are:

1. **Repairing the 25 electric-drive float32 rows** (P1).
2. **Exposing the cell/sensor index already present in raw bytes** (P1).
3. **Computing 12 V and DC-DC power from already selected fields** (P1).
4. **Fixing the `0x1E32` lifetime counters** (P2).
5. **Adding the four ABS wheel speeds** only when the `0x10 03` session risk is
   explicitly accepted (P3).
6. **Recovering the gateway `0x2A26/0x2A28/0x2A2F` payloads** from the passive
   capture (P3 research, not a live probe).

This ordering reflects the owner's electric-drive warning and low-voltage
investigation, not novelty.
