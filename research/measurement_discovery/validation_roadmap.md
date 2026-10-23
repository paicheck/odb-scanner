# Validation roadmap

**Current state:** research only. Nothing in this roadmap may be executed
against the vehicle without a separate, explicit owner approval. The only
approved live connection so far is the 2026-10-22 bounded gateway scan; every
other V2/V3 test below remains blocked.

The roadmap separates offline proof from vehicle tests. Most of the highest-value
findings can be validated without touching the car.

## 0a. Implemented offline (2026-10-20)

Already shipped and proven against stored raw bytes, no vehicle:

- **float32 family** — 25 electric-drive DIDs decode as IEEE-754 big-endian
  `FLOAT32(A,B,C,D)`. Old u16/10+1 values (~1680 °C, ~1738 V, ~1e8 W) were a
  decode error. Catalogue layouts, provenance and plausibility ranges added
  (`decoders/catalogue.py`).
- **`0x1E32` four fields** — signed charge/discharge Ah at bytes 0/4 and
  charge/discharge kWh at bytes 8/12, replacing the two-field energy decode.
- **1-based cell/sensor indices** — `cell_voltage_max_cell`/`min_cell`,
  `bat_temp_max_sensor`/`min_sensor` read the u16 at bytes 2–3.
- **Pack-current sign** — confirmed negative = discharge; arithmetic unchanged.
- **Derived metrics** — `aux_12v_power_w`, `dcdc_power_kw`, `dcdc_gw_power_kw`,
  `cell_soc_spread_pct`, `battery_temp_spread_c` are calculated in
  `collector.py` and surfaced in quality, explore, dashboard and trend/export.
- **Impact replay** — `tools/replay_decoder_impact.py` re-decoded 8,134 stored
  rows and produced `implementation_impact.md/.csv` without rewriting the DB.

The catalogue now declares confidence/provenance/plausibility for the float32
family, the energy block, the index DIDs, the charge-port bits, the battery
serial and the pack-current layout.

## 0b. Deferred (blocked on evidence or approval)

- **ABS wheel speeds** `0x1800`–`0x1803` need `0x10 03` on `0x713`, a session
  change that is not approved. The four decoders and the approval-gated
  `tools/read_abs_wheel_speeds.py` are ready but **not executed**; see the ABS
  note in section 3.
- **Gateway unknowns** `0x2A26` / `0x2A28` / `0x2A2F` now have recovered
  payloads (V1 done), but no profile layout or scale. They must not be
  blind-probed or assigned an invented scale.
- **Quarantined keys** — `co2_content_interior` (sentinel-aware decoder
  implemented; sensor appears not fitted), `windshield_temperature` (byte-0
  decoder implemented; label still needs cold-soak/defroster), `odometer_2`
  (offset known), `bat_charging_voltage` (sentinel `None`) and
  `dc_dc_converter_reserved_current` (raw-only) stay invalid/excluded until
  evidence or policy allows reopening them.
- **Live capture** — `validation_capture_protocol.md` records the exact
  read-only reproduction to run if and when the owner approves it. The
  five-record strict archive supplies independent expected values; the 35-record
  archive remains a plausibility check.

## 0. Principles

1. **Offline first.** If raw bytes already exist, prove the decoder there.
2. **One variable per experiment.** Change one state (charger, HVAC, pedal,
   seat heat) and hold the rest as stable as practical.
3. **Read-only only.** The default allow-list for a future live phase is
   `0x22 ReadDataByIdentifier` on a reviewed DID set. No `0x2E`, `0x31`, `0x27`,
   `0x14`, coding, adaptation, routine or actuator test.
4. **Session changes are separate.** `0x10 03` (extended session) is not a
   read. It must be separately approved, scoped to one module, and never
   combined with a write. ABS is the only candidate that needs it. The prepared
   tool sends exactly one `1003`, reads only `0x1800`–`0x1803`, and always
   attempts `1001`; the ordinary allow-list still refuses `1003`.
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
| V0.7 CO2 | `03FE` | Report both byte1 and u16 hypotheses | Implemented: `03FE` returns `None` (not-fitted sentinel); non-sentinel whole-u16 ppm is provisional; both readings pinned in `tests/test_payload_research.py` |
| V0.8 Windshield | `7A2321C07D` | Current decode flagged invalid; five bytes retained | Implemented: byte 0 × 0.1 decoder; old u16 formula refuted; cold-soak/defroster label test still needed |
| V0.9 Odometer | `0005045B00000000` | Report ×10 and ×100 hypotheses | Implemented: x10 kept; x100 refuted by the 10 km vs ~10.5 km delta; constant 99,869–99,871 km offset from the main odometer |
| V0.10 BMS VIN | NRC `0x31` | Explicit unsupported/rejected | Implemented earlier: BMS request removed; `veh_info` path returns the VIN |

**Deliverable:** regression vectors in the project's test suite, with raw
inputs and expected outputs. **Do not merge a decoder change without them.**

## 2. Phase V1 — archived/passive evidence recovery (done 2026-10-22)

**Goal:** recover missing payloads without the car.

1. Located the original `btsnoop_hci.log` at
   `C:\Users\kings\Documents\CarScanner_Capture\btsnoop_hci.log`; SHA-256
   `eefb5c3ef2781a523692f62484255a70ec7f322a746cde750a4c75c6260496f9`.
2. Ran `tools/mine_passive_capture.py`: 18,468 btsnoop records / 10,417 ACL
   records; recovered `0x2A26`, `0x2A28`, `0x2A2F`, `0x42DB`, `0x5867`,
   `0x1800`–`0x1803`, `0x2AB2`, `0x2AB8`.
   - `0x2A26` = `2D0000002608508C8062904048180040C000400000060000000000000000C057`
   - `0x2A28` = `090000000000108000608000400800400000000000000000000000000000C051`
   - `0x2A2F` = `00` (single byte)
   - `0x2A26` and `0x04A3` are byte-identical (alias/shared block).
3. The selected records, lengths, offsets, and DB evidence are in
   `captures/passive_capture_evidence_20261015.json`; the raw log is **not**
   committed because it contains unrelated phone data.
4. The payloads are documented, but none of the three gateway DIDs has a
   profile layout or scale. They remain raw-only/quarantined; a positive
   response alone is not a decoder.
5. Strict expected values for the two energy DIDs, SoC, pack voltage, and
   odometer are in `captures/strict_expected_values_20261022.json`; the
   validator reports **5/5 PASS** with the expected values retained.

**Deliverable:** a sanitised, hashed payload inventory plus a decoder/MCP
status for each DID. Done.

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
| V2.9 CO2 | `0x42DB` | Fresh air, then occupied | A non-sentinel value moves in 400–2,000 ppm as a whole u16; `03FE` stays `None` (sensor appears not fitted) | Low |
| V2.10 Windshield | `0x27C4` | Cold soak, then defroster | Byte 0 × 0.1 follows windshield/ambient in the expected direction; label decided | Low |
| V2.11 Odometer | `0x5867`, `0x02BD`, `0x295A`, `0xF817`, `0x086D` | One drive of known distance | Increments reconcile at 10 km/LSB; the ~99,870 km offset stays constant unless the BMS was reset | Low |
| V2.12 PTC/seat extras | `0x0801`, `0x3ADF`, `0x3AE0` | Off/on states | Raw length and scaling identified | Low/medium (unknown layout) |

**2026-10-22 live result:** the gateway scan (V2.0) passed 9/9 DIDs with no NRC
and only UDS `0x22`; evidence is archived in
`captures/live_gateway_scan_20261022.json`. The V2.10 `0x27C4` attempt captured
only a baseline (windshield 12.7 °C, outside 12.0 °C) before the session was
stopped, so the windshield-vs-ambient label is still unresolved.

### ABS is not in V2

`0x1800`–`0x1803` require `0x10 03` on `0x713` per the Car Scanner profile.
That is a **session change**, not a read, and it is not approved for this phase.
The decoders and `tools/read_abs_wheel_speeds.py` are prepared but **not
executed**. If it is ever considered:

- separate approval, separate session, one module only;
- no other module in the same session;
- verify the module is the ABS controller and the car is stationary;
- run `--dry-run` first, then `--execute --approve-session-change`;
- the tool sends exactly `1003`, reads only `0x1800`–`0x1803`, and always
  attempts `1001`;
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
