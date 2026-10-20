# Validation roadmap

**Current state:** research only. Nothing in this roadmap may be executed
against the vehicle without a separate, explicit owner approval. The present
phase has made **no** connection and sent **no** diagnostic request.

The roadmap separates offline proof from vehicle tests. Most of the highest-value
findings can be validated without touching the car.

## 0. Principles

1. **Offline first.** If raw bytes already exist, prove the decoder there.
2. **One variable per experiment.** Change one state (charger, HVAC, pedal,
   seat heat) and hold the rest as stable as practical.
3. **Read-only only.** The default allow-list for a future live phase is
   `0x22 ReadDataByIdentifier` on a reviewed DID set. No `0x2E`, `0x31`, `0x27`,
   `0x14`, coding, adaptation, routine or actuator test.
4. **Session changes are separate.** `0x10 03` (extended session) is not a
   read. It must be separately approved, scoped to one module, and never
   combined with a write. ABS is the only candidate that needs it.
5. **Raw bytes are the record.** Store timestamp, ECU, DID, request, raw
   response, decoded value, ignition state, ambient, HVAC/charger state, and
   software version. Keep failures and NRCs.
6. **Stop on surprise.** Any new warning, DTC, module drop, unexpected NRC, or
   implausible value ends the test and triggers review.

## 1. Phase V0 — offline regression (can be done now)

**Goal:** prove decoder behaviour against stored bytes; no vehicle.

| Test | Input | Expected | Pass/fail |
|---|---|---|---|
| V0.1 Float32 | Stored `0x4FAE`, `0x4672`, `0x2CCC`, `0x3E93`, `0x43B9`, `0x3EE7`, `0x3EE8`, `0x3EE9`, `0x1040`, `0x1007` raw bytes | 16.18 °C, 13.79 °C, 13.40 °C, 433.19 V, 433.44 V, 601 A, -166 A, 0.068 Nm, 0.0745 W, 1.6e-7 V | Exact float32 values; no u16 fallback |
| V0.2 Float32 length | 3-byte and empty payloads | Reject with a clear error | No silent 0/truncated value |
| V0.3 Energy block | `01E7CC17FE1B74E70C8071E0F3FF868C` | Ah/kWh four-field values in `decoder_audit.md` | Exact values; signed discharge; no 8-byte truncation |
| V0.4 Energy short | First 8 bytes only | Explicit failure, not 0 kWh | No silent 0 |
| V0.5 Cell/temp index | `offline_crosscheck.csv` | 1-based index; tie-aware match | 28/31 max, 29/31 min; 31/31 + 30/31 temp (already reproduced) |
| V0.6 Pack current | `000248FD`, `FFED` | -2.43 A and -1.9 A | Same sign; no final negation |
| V0.7 CO2 | `03FE` | Report both byte1 and u16 hypotheses | Test framework records both; no unilateral choice |
| V0.8 Windshield | `7A2321C07D` | Current decode flagged invalid; five bytes retained | No production use of ~488,500 |
| V0.9 Odometer | `0005045B00000000` | Report ×10 and ×100 hypotheses | No scale change without V3.6 |
| V0.10 BMS VIN | NRC `0x31` | Explicit unsupported/rejected | No retry storm |

**Deliverable:** regression vectors in the project's test suite, with raw
inputs and expected outputs. **Do not merge a decoder change without them.**

## 2. Phase V1 — archived/passive evidence recovery

**Goal:** recover missing payloads without the car.

1. Locate the original `btsnoop_hci.log` (not present in the workspace) or any
   other saved capture.
2. Re-run the extraction for `0x2A26`, `0x2A28`, `0x2A2F`:
   - requesting CAN ID / ATSH;
   - request bytes;
   - response length and payload;
   - repeat count and variation.
3. Recover payloads for `0x0600`, `0x6156`, `0x6167`, `0x6572`, `0xD476`,
   `0xE323`, `0xE445`, `0xE4C5`, `0xE535`, `0x0313`, `0x305A`, `0xF190`.
4. Compare the byte patterns with the profile's known gateway family
   (`0x2AB2`, `0x2AB4`–`0x2AB8`) and with static identification strings.
5. If no capture exists, mark the gateway unknowns as **blocked** and do not
   blind-probe them.

**Deliverable:** a payload inventory with lengths and context. A positive
response alone is not enough to create a decoder.

## 3. Phase V2 — minimal read-only vehicle checks

**Preconditions:** owner approval; reviewed allow-list; adapter and ignition
state recorded; no active warning; a second person or a safe parked location;
abort plan.

**Default scope:** `0x22` only, one DID at a time, on modules already known to
answer. Prefer DIDs whose raw bytes are already in the DB, so the test is a
reproduction, not a discovery.

| Test | DID(s) | State | Success criterion | Risk |
|---|---|---|---|---|
| V2.1 Eldrive float reproduction | `0x4FAE`, `0x3E93` | Parked, cold, no charging | Float32 value within a plausible range and consistent with ambient/cluster; raw length 4 | Low |
| V2.2 Pack current sign | `0x1E3D`, `0x1E3E`, `0x035E` | Parked, then HVAC on, then charge | All three agree in sign at each state; negative while discharging | Low |
| V2.3 Energy block | `0x1E32` | Parked | Four fields change in the expected direction over a charge/discharge | Low |
| V2.4 Cell/temp indices | `0x1E33`, `0x1E34`, `0x1E0E`, `0x1E0F` + one sweep | Parked | Index is in the tied extreme set; 1-based | Low |
| V2.5 Charge-port bits | `0x7448` | Unplugged, AC plugged, DC plugged | Bit 2/3 isolate as expected | Low |
| V2.6 Recirculation | `0x263B` | HVAC fresh vs recirculation | 00 vs 04 | Low |
| V2.7 Accelerator alias | `0xF449` | Parked/ready, pedal released then pressed | 0% then rises | Low (car stationary) |
| V2.8 GPS fix/time | `0x2430`, `0x22B3` | Outdoors, clear sky | Valid lat/lon/alt/time; compare with phone | Low/medium (multiframe) |
| V2.9 CO2 | `0x42DB` | Fresh air, then occupied | 400–1,500 ppm; byte vs u16 decided | Low |
| V2.10 Windshield | `0x27C4` | Cold soak, then defroster | Five bytes logged; plausible field found | Low |
| V2.11 Odometer | `0x5867`, `0x02BD`, `0x295A`, `0xF817`, `0x086D` | One drive of known distance | Increments reconcile; scale decided | Low |
| V2.12 PTC/seat extras | `0x0801`, `0x3ADF`, `0x3AE0` | Off/on states | Raw length and scaling identified | Low/medium (unknown layout) |

### ABS is not in V2

`0x1800`–`0x1803` require `0x10 03` on `0x713` per the Car Scanner profile.
That is a **session change**, not a read, and it is not approved for this phase.
If it is ever considered:

- separate approval, separate session, one module only;
- no other module in the same session;
- verify the module is the ABS controller and the car is stationary;
- read all four wheels at crawl speed in a safe area;
- stop on any ABS/ESC warning or unexpected NRC;
- do not combine with any write/actuator test.

The diagnostic value is moderate (wheel-speed plausibility), but the risk is
higher than every other candidate in this catalogue.

## 4. Phase V3 — state comparisons

These are the tests that decide semantics, not just decoding.

| Test | States | Question |
|---|---|---|
| V3.1 Current sign | Parked, HVAC on, AC charge, DC charge, regen/drive | Is the sign convention stable? Does negative always mean discharge? |
| V3.2 Energy counters | Before/after a known charge and a known drive | Do Ah/kWh counters move consistently? Which is lifetime vs trip? |
| V3.3 Cell balance | Rest, low SoC, high SoC, load | Does cell index track the same weak cell? Does the spread grow? |
| V3.4 Temperature | Cold soak, drive, DC charge, PTC on | Does the sensor index track the hot spot? |
| V3.5 Odometer | Known 10 km route | Which DID increments, in what unit? |
| V3.6 12 V / DC-DC | Off, accessory, drive, charge | Does 12 V power correlate with DC-DC output? Is current sign stable? |
| V3.7 CO2 | Fresh air, closed cabin, occupants | Which byte/width behaves like CO2? |
| V3.8 Windshield | Cold soak, defroster, sun | Which field tracks windshield temperature? |
| V3.9 GPS | Cold start, fix, urban canyon | Does the multiframe parse reliably? |

### Sample size

- At least 3 independent observations per state for a decoder decision.
- At least 10 for a sign/scale decision.
- Record failures and NRCs, not just successes.
- Do not pool imported Car Scanner CSV rows with raw hex rows as if they were
  the same evidence.

## 5. Phase V4 — production decoder/catalogue changes

Only after V0 and the relevant V2/V3 test:

1. Add a regression test with raw bytes and expected value.
2. Make the smallest decoder change.
3. Keep the old value available for one release if the output schema changes.
4. Add `evidence_level`, `sign_convention`, and `source` metadata.
5. Update `docs/DATA_CATALOGUE.md` and the measurement catalogue version.
6. Do not change `0x1E32` offsets, float32 type, or pack-current sign without
   the corresponding test.
7. For quarantine signals (windshield, CO2), expose them as invalid/unknown,
   not as plausible numbers.

### Suggested order

| Order | Change | Why |
|---:|---|---|
| 1 | Float32 decoder family | Largest invalid block; affects the drive warning |
| 2 | Cell/temp index outputs | No new request; high diagnostic value |
| 3 | 12 V and DC-DC power derived metrics | Directly serves the LV investigation |
| 4 | `0x1E32` four-field decoder | High confidence; fixes lifetime energy |
| 5 | Pack-current documentation/sign convention | Prevents a wrong future "fix" |
| 6 | Charge-port bits, accelerator rename | Cheap usability wins |
| 7 | CO2/windshield quarantine + tests | Low priority; avoid false data |
| 8 | Odometer, GPS, ABS, gateway unknowns | Needs more evidence |

## 6. Phase V5 — monitoring

After the decoders are trustworthy:

- Trend the 12 V voltage/current/power and DC-DC output power around any
  warning event.
- Trend cell voltage/SOC spread and temperature spread.
- Keep raw payload retention for at least the drive-warning window.
- Record gear, speed, odometer, charger state and GPS fix as context, not as
  fault signals.
- Do not label a warning as diagnosed from association alone; temporal order is
  not causation.

## 7. Abort criteria

Stop and review if any of the following occurs:

- a new dashboard warning or DTC;
- an ABS/ESC/brake warning during any wheel-speed test;
- a module stops responding mid-test;
- an unexpected negative response other than a known unsupported DID;
- a value changes by an implausible amount when the state did not change;
- the adapter resets or the ignition state changes unexpectedly;
- any request other than the approved `0x22` is about to be sent.

## 8. Approval checklist for a future live phase

- [ ] Owner has explicitly approved the phase and the exact DID list.
- [ ] The test is read-only; no `0x2E`, `0x31`, `0x27`, `0x14`, coding,
      adaptation, routine or actuator test.
- [ ] `0x10 03` is either excluded or separately approved and scoped.
- [ ] Raw logging is on; timestamps and state metadata are captured.
- [ ] A regression/expected-value sheet exists for every DID.
- [ ] Abort criteria are written down.
- [ ] A safe, parked location and a second person (or equivalent) are available.
- [ ] Data will be de-identified before sharing.
