# Evidence report — additional measurements for the 2021 ID.3

**Vehicle:** 2021 VW ID.3 Pro Performance, 58 kWh, MEB, VIN
`WVWZZZE1ZMP087053`.

**Scope:** offline research only. No vehicle connection, no diagnostic request,
no write/coding/adaptation/security access/routine/actuator test/clear. All
original logs, decoders, databases, profiles and tests are preserved.

## 1. Evidence hierarchy

| Level | Definition | Can it justify a catalogue entry? | Can it justify a production decoder change? |
|---|---|---|---|
| `verified_on_vehicle` | A raw response was observed from this car and the decoded value is physically plausible across states; where possible, an independent same-timestamp signal agrees. | Yes | Not alone; still needs a proposed test and review |
| `supported_by_source` | A primary source (Car Scanner profile or a named open-source project) defines the DID and layout; not yet read on this car. | Yes, as a candidate | No |
| `observed_positive_undecoded` | This car returned a positive `0x62` response, but the payload layout/scaling is unknown. | Yes, as a research candidate | No |
| `inferred` | Deduced from ECU behaviour, a related signal, or arithmetic; no direct definition. | Yes, with explicit caveat | No |
| `speculative` | A list entry or guess with no layout evidence. | Yes, but low priority | No |
| `rejected` | Repeated negative response or demonstrably wrong ECU/DID combination. | Record only | No |

The key rule: **a profile definition is not proof the car responds; a plausible
number is not proof the decoder is right.** Several entries in this report are
explicitly downgraded for that reason.

## 2. Profile evidence

### 2.1 What was extracted

`docs/investigation/live_measurement_evidence.csv` contains **308 `CustomPID`
records** from the installed Car Scanner MEB profile (index 342). The fields
include measurement name, ECU label/address, service, DID, SBI/DL, signedness,
byte order, scale/divider/offset, formula, unit, bit, text variants, and BCM
commands.

After de-duplicating display aliases, the profile contains:

- **305 distinct underlying signals**
- **255 distinct DIDs**
- **5 profile-only DIDs**: four real ABS wheel-speed DIDs (`0x1800`–`0x1803`)
  and one composite pseudo-DID (`0x1E3B;0x1E3D` for derived DC battery power)
- **250 DIDs shared with the Python registry**

The Python registry has **282 specs / 253 distinct DIDs**. It contains three DIDs
not in the profile: `0x0500` (battery serial), `0x210E` (gear), and `0xF802`
(VIN/serial). The first two are observed; the third is invalid **on the BMS**
(NRC `0x31`) and works on vehicle-info `0xF802`.

### 2.2 Why 305 signals do not equal 305 new measurements

The current 152 logical measurements already group:

- 108 cell voltages into `cell_voltages`;
- 24 temperature points into `cell_temperatures`;
- many multi-field DIDs into one logical entry per field.

Examples of multiplicity:

| DID | Profile signals | Current coverage |
|---|---:|---|
| `0x2AF7` | 10 | 10 fields selected |
| `0x2AB7` | 7 | 7 road-category averages selected |
| `0x7448` | 6 | `charge_mode`/`op_mode` selected; port bits not exposed |
| `0x1E32` | 4 | `energy_counters` selected but decoded at wrong offsets |
| `0x15D1` | 4 | 4 fields selected |
| `0x1E33/0x1E34` | 4 | 2 voltages selected; 2 cell indices discarded |
| `0x1E0E/0x1E0F` | 4 | 2 temperatures selected; 2 sensor IDs discarded |
| `0x4EEF` | 3 | 3 fields selected |

The real profile-only *DID* set is therefore small: four ABS wheel speeds. The
larger opportunity is sub-field extraction and derived metrics.

### 2.3 Aliases and duplicates

The profile contains several display aliases that can look like new signals:

- `0x028C`: BMS SoC and displayed/normalized SoC.
- `0x0364`: "additional high-voltage consumers" and "HV auxiliary consumer
  power" — same DID and physical quantity.
- `0x0344`: front/rear rated torque labels on the same DID; the car is RWD, so
  the "front axle" label is misleading.
- `0x2609`/`0x260A`: outside temperature and "calculated from cabin" — different
  DIDs, related values.
- `0x2AB4`: average consumption and internal range on one DID, different
  offsets.
- `0x1E3B;0x1E3D`: a composite pseudo-DID for derived DC battery power, not a
  real UDS DID.
- `0xF40D`: vehicle speed; `0xF40C`: motor speed — not aliases, but easily
  confused.
- `0x1E33/0x1E34`: the same DID carries both the extreme voltage and the cell
  index; the second row is not a new DID.
- `0xF449`: accelerator pedal, currently selected under the opaque
  `res_pid_0149` name.

This matters because counting profile rows without de-duplicating overstates
the "missing" set by a large factor.

## 3. External source assessment

### 3.1 spot2000 MEB UDS PID list

**Status:** medium-authority community list; useful corroboration, not truth.

**Strengths:**

- 160 entries with request/response packages, ATCP/ATSH/ATCRA commands, and
  formulas for many BMS, HVAC, gateway and GPS signals.
- Independently corroborates the `0x1E32` four-field layout: kWh counters at
  offsets 8 and 12, not 0 and 4.
- Names the accelerator pedal `0xF449`.
- Names the HVAC extras: recirculation `0x263B`, PTC air heater `0x0801`,
  seat heating `0x3ADF`/`0x3AE0`.
- Names the GPS extras: `0x22B3` (time) and `0x2430` (multiframe position).

**Weaknesses:**

- Many rows are marked "equation missing" or "need to logg".
- Core list dates from 2021-06-22; latest repository commit 2022-10-26.
- No versioned release or vehicle-specific validation.
- Some "Ready" rows are still just name/layout claims.

**Use:** corroboration and lead generation; never the sole basis for a decoder
change.

### 3.2 evDash `CarVWID3.cpp`

**Status:** medium for addressing/commands and GPS/time parsing; code can have
bugs.

**Strengths:**

- Full command queue for MEB ECUs, including 11-bit HVAC/GPS and 29-bit BMS.
- GPS `0x2430` parser with a real sample payload:
  `6224303138B0333627362E3022450000003438B031322733342E33224E000002A1AA`.
- GPS time `0x22B3` bit masks.
- Confirms `0x263B`, `0x0801`, `0x0800`, `0xF449` command names.

**Weaknesses:**

- No electric-drive float decoding; it does not help validate F1 directly.
- GPS comments say "latitude" for the first field but assign it to longitude;
  the second is latitude. The code assignments are correct for the sample
  (18°E, 48°N), but the comments are swapped.
- No scaling for `0x0801` or seat heating.
- Not a test suite.

**Use:** GPS/time layout and independent command confirmation.

### 3.3 ABRP `volkswagen/MEB.json`

**Status:** low/medium; independent cross-check only.

- Contains only four BMS commands: current, voltage, SoC, charge state.
- Current equation is malformed: `((A<<32+B<<16+C<<8+D-150000)/100)*-1`
  uses `A<<32` instead of `A<<24`. It happens to work while `A == 0` (the usual
  3-byte current range), but it is not a general decoder.
- It applies a final `* -1`, so it displays the opposite sign to the raw
  physical convention supported by `0x035E`.
- It is not a source of new measurements.

**Use:** confirmation that the current sign is controversial and that ABRP is
not an authority.

### 3.4 ELMduino issue #207

**Status:** negative result for measurement definitions.

The issue "Help with Volkswagen MEB (ID.3, Skoda Enyaq etc.) needed" discusses
29-bit header setup and adapter behaviour. It does **not** provide new MEB PIDs
or scaling. It is useful only as context for why 29-bit modules are harder to
reach with generic ELM327 code.

### 3.5 OVMS / raimuucka

- OVMS documentation is mostly VW e-Up, generic OBD-II and platform support,
  not a MEB measurement catalogue. Search was negative for directly usable MEB
  PID definitions.
- The `raimuucka/OBD2-Volkswagen-MEB-EV-CAN-parameters` list is a parallel
  community list, not an authority. It was treated as a lead, not a source of
  truth.

## 4. Decoder evidence

Full detail is in `decoder_audit.md`. The evidence that changes conclusions:

### 4.1 Float32

The profile marks 25 of 26 electric-drive rows as
`FLOAT32(A,B,C,D)`. Raw four-byte payloads decode to plausible values under
IEEE-754 big-endian:

- `41816D8E` -> 16.18 °C (coolant)
- `43D897DE` -> 433.19 V (DC link)
- `44164000` -> 601 A (max current)
- `C3260000` -> -166 A (min current)

The registry applies a u16/10+1 decoder, producing ~1,680 °C and ~1,738 V. This
is a type error, not a noisy sensor.

### 4.2 `0x1E32`

The profile and spot2000 both put:

- Ah counters at offsets 0/4;
- kWh counters at offsets 8/12.

The registry reads offsets 0/4 and divides by the kWh divisor. The four-field
raw block `01E7CC17 FE1B74E7 0C8071E0 F3FF868C` decodes to:

- 58,196.5 Ah charge
- -57,808.3 Ah discharge
- 24,436.98 kWh charge
- -23,459.86 kWh discharge

The implied average voltages (~419.9 V charge, ~405.8 V discharge) are
plausible, and the ~4% difference is consistent with losses. This is strong
internal evidence.

### 4.3 Cell and temperature indices

`0x1E33`/`0x1E34` and `0x1E0E`/`0x1E0F` carry a 1-based cell/sensor number in
bytes 2–3. The current decoders discard it. Tie-aware comparison with the
per-cell/per-sensor sweeps:

- max cell index: 28/31 in the tied max set;
- min cell index: 29/31 (two asynchronous sweeps);
- max temperature sensor: 31/31;
- min temperature sensor: 30/31.

The mismatches are explainable by non-synchronous aggregate/sweep timestamps
and ties, not by a wrong byte order. The 1-based interpretation is much better
than 0-based (which fails the min index consistently).

### 4.4 Pack-current sign

At 2026-10-19T19:28:42:

- `0x1E3D` raw `000248FD` -> -2.43 A.
- `0x1E3E` raw `000248FD` -> -2.43 A.
- `0x035E` raw `FFED` -> -1.9 A.

Two modules, negative at rest, discharge counter rising. Negative = discharge.
The code returns the physical sign. The docstring says "positive = discharging"
and Car Scanner negates the formula. The bug is in the documentation/display,
not the arithmetic. A future "fix" that negates the value would be wrong.

### 4.5 CO2 and windshield

These are downgraded, not fixed:

- CO2: byte 1 × 100 gives a constant 25,400 ppm; u16 gives 1,022 ppm. The
  profile may be wrong. Fresh-air test required.
- Windshield: five-byte payload, current decode ~488,500 °C. Field/scale not
  established. Quarantine.

### 4.6 Odometer

`0x5867` gives 11,150 km; three other odometers give ~111,020 km. ×100 gives
111,500 km, which is close but not exact. This is not enough to change the
scale. Log four odometers on one drive.

## 5. Passive capture evidence

`docs/investigation/capture_positive_did_inventory_20261015.md` records every
positive `0x62` response from a passive Bluetooth HCI capture. The original
`btsnoop_hci.log` is **not present** in this workspace, so only the text
inventory is available.

Useful positive responses not in the profile:

- Gateway energy family: `0x2A26`, `0x2A28`, `0x2A2F` (plus already-known
  `0x2AB4`–`0x2AB7`).
- ABS: `0x1800`–`0x1803`.
- Misc: `0x0600`, `0x6156`, `0x6167`, `0x6572`, `0xD476`, `0xE323`, `0xE445`,
  `0xE4C5`, `0xE535`.
- 18DAF105/10A: `0x0313`, `0x305A`, `0xF190`.

Limits:

- Positive response proves the module answered, not that the value is a
  measurement.
- Payloads/scaling are not retained in the text inventory.
- The same inventory lists many `0xF1xx` identification strings, which are
  static, not live measurements.
- The capture is from Car Scanner, so it does not prove the Python project can
  reach the same module.

## 6. What is not evidence

- A `VERIFIED` flag in a third-party profile.
- A single plausible value.
- A stale value in `data/odb_scanner.db`.
- A `0x62` response with no payload/context.
- A Car Scanner display that contradicts the raw bytes and an independent ECU.
- A community list's "Ready" status without a formula or a raw example.
- A formula that only works while a high byte is zero (ABRP current).
- A model/AI inference from the DID name.

## 7. Contradictions and how they were handled

| Contradiction | Resolution |
|---|---|
| Car Scanner displays positive pack current at rest; `0x035E` is negative | Treat negative = discharge as physical; Car Scanner negates for display. Do not invert our decoder. |
| Profile says `FLOAT32`; registry says u16 | Float32 fits all examples; u16 produces impossible values. F1 high confidence. |
| Profile/spot2000 say kWh at 8/12; registry reads 0/4 | Both independent sources and arithmetic favour 8/12. F2 high confidence. |
| Profile says `0x1E32` 16 bytes; registry expects 8 | The decoder's length guard is useful, but it should consume the full 16-byte block. |
| Profile says cell index at SBI 2; sweep ties rarely match a single sorted row | Tie-aware comparison supports 1-based index; ties explain mismatches. |
| `0x5867` ×10 vs other odometers | Unresolved; ×100 is closer but not proven. Do not tune. |
| `0x27C4` profile scale vs five-byte payload | Unresolved; quarantine. |
| CO2 byte vs u16 | Unresolved; test. |
| BMS `0xF802` registry entry vs NRC `0x31` | Reject on BMS; VIN is on veh_info. |

## 8. Evidence gaps

1. No original btsnoop log in the workspace; only the positive-DID text
   inventory. Gateway `0x2A26/28/2F` payloads cannot be recovered locally.
2. No controlled charge/discharge session for the pack-current sign and energy
   counters, only parked/imported data.
3. No known-good windshield/CO2 state test.
4. No ABS module observation from the Python collector; the four wheel speeds
   are profile-only.
5. No GPS `0x2430`/`0x22B3` observation in the Python DB; the layout is
   source-supported, not vehicle-verified.
6. No OBC response at all (211 requests, 0 successes). The OBC hypothesis is
   outside this catalogue.
7. No raw payloads for the miscellaneous positive DIDs.
8. No independent VW service documentation; all external sources are
   community/open-source.

## 9. Confidence summary

| Claim | Confidence | Basis |
|---|---|---|
| 25 eldrive rows are float32, not u16 | **Very high** | Profile formula + four-byte raw + plausible values across 25 rows |
| `0x1E32` has Ah at 0/4 and kWh at 8/12 | **High** | Profile + spot2000 + internal arithmetic |
| Cell/sensor indices are 1-based at bytes 2–3 | **High** | 31 same-timestamp comparisons, tie-aware |
| Physical pack current is negative on discharge | **High** | Two ECUs at same timestamp + counter direction |
| CO2 current value is wrong | Medium | Physical implausibility; field not proven |
| Windshield current value is wrong | **High** | Impossible value; correct field not proven |
| `0x5867` scale is wrong | Medium | Three odometers disagree; exact scale not proven |
| BMS `0xF802` is unsupported | **High** | 63 attempts, NRC `0x31` |
| ABS wheel speeds exist and respond | Medium | Profile + passive positive response; no values |
| Gateway `0x2A26/28/2F` are useful | Low/medium | Positive response; no layout |
| GPS layout | Medium | evDash parser + sample payload; no vehicle read |

## 10. References

- `docs/investigation/live_measurement_evidence.csv`
- `research/measurement_discovery/profile_summary.json`
- `research/measurement_discovery/profile_signals_all.csv`
- `research/measurement_discovery/offline_crosscheck.csv`
- `research/measurement_discovery/sources/spot2000_VW_MEB_UDS_PIDs_list.csv`
- `research/measurement_discovery/sources/evDash_CarVWID3.cpp`
- `research/measurement_discovery/sources/abrp_MEB.json`
- `research/measurement_discovery/source_inventory.csv`
- `decoders/meb.py`, `decoders/registry.py`, `diagnostic/ecus.py`
- `docs/DATA_CATALOGUE.md`
- `docs/investigation/capture_positive_did_inventory_20261015.md`
- `data/odb_scanner.db` (read-only)
