# Decoder audit — MEB profile vs. this project

**Scope:** offline audit only. No vehicle connection, no diagnostic request,
no write, no coding/adaptation, no decoder change. This document records what
the evidence supports and what must be tested before any production edit.

**Inputs:** `docs/investigation/live_measurement_evidence.csv` (Car Scanner MEB
profile, 308 rows / 255 DIDs), `decoders/meb.py`, `decoders/registry.py`,
`data/odb_scanner.db` (read-only), `docs/DATA_CATALOGUE.md`, and
`research/measurement_discovery/offline_crosscheck.csv`.

## Summary

| # | Finding | Affected signals | Current effect | Confidence | Proposed action |
|---|---|---|---:|---|---|
| F1 | Car Scanner `FLOAT32(A,B,C,D)` rows are decoded as `u16/10+1` | 25 of 26 electric-drive measurements | Temperatures ~1,680 °C, DC link ~1,738 V, currents ~1,744/4,997 A, losses ~1e8 W | **High** | Add explicit big-endian IEEE-754 float32 decoder; regression vectors; no live request needed |
| F2 | `0x1E32` decoder reads the wrong offsets and units | `energy_counters`, `accum_charge_kwh`, `accum_discharge_kwh` | Lifetime energy ~3,724/3,700 kWh instead of ~24,437/23,460 kWh; Ah counters mislabeled | **High** | Expose four fields (Ah @0/4, kWh @8/12) with correct scales |
| F3 | Cell/temperature extreme indices are discarded | `0x1E33`, `0x1E34`, `0x1E0E`, `0x1E0F` bytes 2–3 | Only the extreme value is exposed; the cell/sensor number is lost | **High** | Add 1-based cell/sensor index outputs; handle ties |
| F4 | Pack-current sign is documented backwards | `pack_current`, `pack_current_2` | Code returns negative at rest, docstring says positive = discharge; Car Scanner negates | **High** | Do **not** invert the arithmetic. Fix the documentation/label and state the convention |
| F5 | Cabin CO2 may use the wrong byte/width | `co2_content_interior` | Constant 25,400 ppm; u16 is 1,022 ppm | Medium | Fresh-air/occupant test before changing |
| F6 | Windshield temperature is impossible | `windshield_temperature` | ~488,500 °C | High that it is wrong; low on the fix | Quarantine; five-byte payload/field test |
| F7 | `0x5867` odometer is ~10× low | `odometer_2` | 11,150 km vs 111,020 km on three other odometers | Medium | Compare four odometers on one drive; do not tune the scale blindly |
| F8 | BMS `0xF802` VIN is unsupported | `hv_serial_vin` | 63 attempts, 0 successes, NRC `0x31` | **High** | Remove/mark unsupported on BMS; keep veh_info `0xF802` |
| F9 | Battery serial ASCII is not decoded | `battery_serial` | Raw bytes logged, value/text null | High | Decode ASCII; low diagnostic value |
| F10 | Several lower-priority anomalies | `bat_charging_voltage`, `dc_dc_converter_reserved_current`, `cell_soc_max/min` | Sentinel values or JSON-only output | Medium | Separate backlog; do not conflate with the drive/LV investigation |

## Resolution log (2026-10-22 payload/capture pass)

| Finding | Status | Evidence / action |
|---|---|---|
| F1 float32 | Resolved earlier | 25 electric-drive rows now use IEEE-754 big-endian; pinned by vectors |
| F2 energy block | Resolved earlier | Four fields at Ah 0/4 and kWh 8/12; raw bytes preserved |
| F3 cell/temp index | Resolved earlier | 1-based indices exposed; tie handling tested |
| F4 pack current | Resolved earlier | Arithmetic unchanged; documentation states negative = discharge |
| F5 CO2 | **Decoder implemented; sensor appears not fitted** | 40/40 responses are `03FE`; the same bytes are the BMS not-fitted sentinel. The decoder returns `None` for `03FE` and whole-u16 ppm for a non-sentinel frame; 25,400 ppm is rejected. Non-sentinel scale still provisional. |
| F6 windshield | **Decoder implemented; label still quarantined** | The old u16 decode is refuted. Byte 0 × 0.1 is implemented (1.17 °C MAE vs outside); cold-soak/defroster still needed to decide windshield vs ambient. |
| F7 `0x5867` | **Resolved as a counter with a known offset** | A 10 km/LSB secondary counter, not the total odometer. The x10 delta matched a ~10.5 km drive; x100 is refuted. It sits a constant 99,869–99,871 km below the main odometer on this car, consistent with a BMS reset; service history still needed. |
| F8 BMS VIN | Resolved earlier | BMS `0xF802` request removed; `veh_info` path returns the VIN. |
| F9 battery serial | Resolved earlier | ASCII decoded into `value`/`text_value`; JSON cell SoC pairs are not undecoded. |
| F10 sentinels | **Partially resolved** | `0x1E39` `FFFF`/`FFFE` now return `None`; `0x2A53` reserved current is raw-only (complement, not a current); `cell_soc_max/min` are JSON by design. |
| F11 ABS wheel speeds | **Decoders + tool ready; not executed** | `0x1800`–`0x1803`, `0x713 -> 0x77D`, `SBI=0 DL=2 ×0.01 km/h`; the passive capture shows `1003` then the four reads. Registered only by `register_abs()`; the default collector does not send them. `tools/read_abs_wheel_speeds.py` is approval-gated and always attempts `1001`. |
| F12 gateway unknowns | **Payloads recovered; raw-only** | `0x2A26`/`0x2A28`/`0x2A2F` exact payloads are archived; `0x2A26` == `0x04A3`; no profile layout/scale. |

Regression tests: `tests/test_payload_research.py`,
`tests/test_payload_audit_tool.py`, `tests/test_passive_capture.py` and
`tests/test_abs_tool.py`. The read-only replay is
`tools/audit_payload_findings.py`.

## Method

1. Extracted every profile row and grouped it by DID and sub-field.
2. Compared each profile row's declared `pid_type`, `SBI`, `DL`, `MUL`,
   `DIV`, `OFS`, `SIG`, and `Formula` with the decoder selected by the registry.
3. Re-decoded the latest stored raw hex payloads from `measurements.raw_response`
   in `data/odb_scanner.db`. Imported Car Scanner CSV rows (non-hex
   `raw_response`) were excluded from byte-level checks.
4. Used independent same-timestamp signals as controls:
   - `0x035E` vehicle-info HV current for pack-current sign.
   - the 108-cell sweep from `cell_voltages` for `0x1E33/0x1E34`.
   - the 24 `cell_t_*` rows for `0x1E0E/0x1E0F`.
   - the other odometers (`0x02BD`, `0x295A`, `0xF817`) for `0x5867`.
5. Treated a plausible-looking number as a hypothesis, not proof.

---

## F1 — Electric-drive `FLOAT32` family

### Evidence

The Car Scanner profile marks 25 of the 26 `51.ElDrive` rows as
`FLOAT32(A,B,C,D)`. The remaining row, `eldrive_odometer` (`0x086D`), is a
ByteSet and is not part of this finding.

The project registers those rows as `u16` values with `/10 + 1`-style
scaling. Raw payloads are four bytes, and interpreting them as IEEE-754
big-endian float32 produces physically coherent values:

| DID | Signal | Raw | Current registry | Float32 | Plausibility |
|---|---|---|---:|---:|---|
| `0x4FAE` | Electric drive inflow coolant temperature | `41816D8E` | 1,677.9 °C | **16.18 °C** | Correct order of magnitude |
| `0x4672` | Stator temperature | `415C90A2` | 1,674.2 °C | **13.79 °C** | Correct |
| `0x2CCC` | Rotor temperature model | `415656F0` | 1,673.6 °C | **13.40 °C** | Correct |
| `0x3E93` | DC link voltage | `43D897DE` | 1,737.8 V | **433.19 V** | Correct for a 58 kWh MEB pack |
| `0x43B9` | DC link voltage reserve | `43D8B85D` | 1,737.8 V | **433.44 V** | Correct |
| `0x3EE7` | Motor maximum current | `44164000` | 1,744.0 A | **601.0 A** | Plausible limit |
| `0x3EE8` | Motor minimum current | `C3260000` | 4,996.8 A | **-166.0 A** | Plausible signed limit |
| `0x3EE9` | Actual mechanical torque calculated | `3D8BEA4E` | 1,576.5 Nm | **0.068 Nm** | Near zero at rest |
| `0x1040` | Inverter power loss phase U | `3D989726` | ~1.0e8 W | **0.0745 W** | Near zero at rest |
| `0x1007` | Stator voltage D actual | `342BFA77` | 1,336.5 V | **1.6e-7 V** | Near zero at rest |

The pattern is consistent across all 25 rows: the *type* is wrong, not the
addressing and not the raw payload. The registry's decoder reads the first two
bytes; the profile's formula consumes all four.

### Why this matters

The electric-drive warning investigation depends on torque, DC-link voltage,
coolant/stator/rotor temperature, motor current and inverter loss. In the
current database these are not merely noisy; they are structurally invalid.
Any correlation with the warning based on those values is unreliable.

### Proposed fix (not applied)

- Add a decoder that checks for at least four bytes and returns
  `struct.unpack(">f", raw[:4])[0]`.
- Keep the raw payload in the database (already done).
- Add regression vectors for at least the DIDs above, including negative and
  near-zero values.
- Do not apply the change to production in this phase. The offline regression
  is sufficient to prove the type; a later approved read-only check can confirm
  two values against ambient/cluster context.

### Caveat

Not every float32 value is independently meaningful just because it is
plausible. `actual_possible_maximum_torque` near zero at rest, for example, may
be a state-dependent limit rather than a sensor. The *decoding* is strongly
supported; the *semantic* interpretation of each row still needs the relevant
operating state.

---

## F2 — `0x1E32` lifetime charge/discharge block

### Evidence

The profile and spot2000 agree on four big-endian u32 fields:

| Offset | Meaning | Scale | Raw example | Correct value | Current decoder |
|---:|---|---|---:|---:|---:|
| 0 | Total accumulated charge, Ah | `0.001820444` | `0x01E7CC17` = 31,968,279 | **58,196.5 Ah** | 3,724.57 kWh (wrong unit) |
| 4 | Total accumulated discharge, Ah (signed) | `0.001820444` | `0xFE1B74E7` = -31,755,033 | **-57,808.3 Ah** | 3,699.73 kWh (wrong unit) |
| 8 | Total accumulated charge, kWh | `116508.398 / 1e9` | `0x0C8071E0` = 209,744,352 | **24,436.98 kWh** | not decoded |
| 12 | Total accumulated discharge, kWh (signed) | `116508.398 / 1e9` | `0xF3FF868C` = -201,357,684 | **-23,459.86 kWh** | not decoded |

Internal arithmetic supports the layout:

- 58,196.5 Ah × ~419.9 V = 24,437.0 kWh (charge side).
- 57,808.3 Ah × ~405.8 V = 23,459.9 kWh (discharge side).
- The ~4% energy difference is plausible for round-trip losses and auxiliary
  consumption over lifetime.

The current decoder reads offsets 0 and 4 and divides by `ENERGY_COUNT_WH`
(8,583.07), which yields 3,724.57 and 3,699.73. It is reading the **Ah**
counters with the **kWh** divisor. Both the offsets and the units are wrong.

### Proposed fix (not applied)

- Expose `total_charge_ah`, `total_discharge_ah`, `total_charge_kwh`,
  `total_discharge_kwh`.
- Preserve signs and provide magnitudes where the UI needs them.
- Regression vectors: the raw block above; a zero block; a block with only the
  first 8 bytes (must fail, not silently return 0).
- One later read-only check on charge/discharge direction.

### Note

`docs/DATA_CATALOGUE.md` already flags `energy_counters` as suspect. This audit
identifies the exact offsets and units. The profile is the primary source; the
internal Ah/kWh arithmetic is an independent consistency check.

---

## F3 — Cell and temperature extreme indices

### Evidence

`0x1E33` and `0x1E34` return four bytes:

- bytes 0–1: extreme voltage, `u16 / 4096` V.
- bytes 2–3: 1-based cell number.

`0x1E0E` and `0x1E0F` return four bytes:

- bytes 0–1: extreme temperature, `u16 / 64` °C.
- bytes 2–3: 1-based sensor number.

The current decoders expose only the first two bytes. The raw bytes were already
logged, so no new vehicle request is needed.

**Width note (2026-10-20):** across all 169 stored samples of these four DIDs,
byte 2 is always `0x00` and the index is carried in byte 3 alone (values are
all ≤ 61 for cells and ≤ 10 for temperature sensors). spot2000's per-DID
`Calculation` column states this exactly — the response is `WW XX YY ZZ` and
"ZZ is the cell # with highest/lowest voltage". The Car Scanner profile's
`SBI=2 DL=2` is therefore a 2-byte framing of a 1-byte value whose high byte is
a zero placeholder; `u16be[2:4]` and `raw[3]` are identical on all current
evidence. The decoder reads `u16be[2:4]` to stay literal to the profile; a
future nonzero byte 2 would be caught by the plausibility range (1..108 / 1..255)
rather than silently misread.

### Offline validation

`offline_crosscheck.csv` compares 31 same-timestamp observations against the
full cell/temperature sweeps. Because extrema often tie, the correct test is
"is the reported index in the tied set?", not "is it the single last sorted
row?". Tie-aware results:

| Field | Matching timestamps | Notes |
|---|---:|---|
| `0x1E33` max cell index | 28 / 31 | Three mismatches at 18:15/18:19, where the aggregate and the per-cell sweep were not synchronous |
| `0x1E34` min cell index | 29 / 31 | Two asynchronous sweeps; min is otherwise exact |
| `0x1E0E` max sensor index | 31 / 31 | All matched within the tied max set |
| `0x1E0F` min sensor index | 30 / 31 | One small timing/rounding mismatch |

This is strong evidence for a 1-based index. It is not proof that every future
value will be unique.

### Why this matters

"Cell voltage max = 4.006 V" is much less actionable than "cell 17 is the
highest, cell 61 is the lowest." The same applies to the hot/cold temperature
sensor. This is the cheapest high-value addition in the catalogue: no new DID,
no session change, no additional risk.

### Proposed fix (not applied)

- Return `{value, index}` for each extreme, or add separate `*_index` keys.
- Document 1-based numbering.
- If multiple cells tie, either report all tied indices or label the single
  index as "one of N". Do not imply a unique weakest cell when the sweep is tied.

---

## F4 — Pack-current sign and documentation

### Evidence

The decoder is:

```python
def _pack_current(raw: bytes) -> float:
    """Pack current in A; positive = discharging."""
    return (u32be(raw) - 150000) / 100.0
```

At 2026-10-19T19:28:42:

- `0x1E3D` raw `000248FD` -> `(149,757 - 150,000) / 100 = -2.43 A`.
- `0x1E3E` raw `000248FD` -> **-2.43 A**.
- `0x035E` vehicle-info HV current raw `FFED` -> **-1.9 A** (signed 16-bit, /10).
- The car was parked; the BMS discharge counter was increasing.

Two independent modules report a negative current at rest while the battery is
supplying the car. That supports **negative = discharge**.

Car Scanner's profile formula is
`((u32 - 150000) / 100) * -1`, so it displays **+2.43 A** for the same bytes.
ABRP also applies a final `* -1`. Those are display conventions, not independent
physical evidence, and the ABRP equation is additionally malformed
(`A<<32` instead of `A<<24`).

### Conclusion

The suspicion that our decoder is inverted is probably **wrong**. Our arithmetic
is physically consistent; the docstring and notes are inconsistent with the
code. A future "fix" that negates the value would make the data disagree with
`0x035E` and with the discharge-counter direction.

### Proposed action (not applied)

- Keep the arithmetic.
- Change the docstring to **negative = discharge** (or document the convention
  explicitly as "positive = charge").
- Add a `sign_convention` metadata field to the measurement/catalogue schema.
- In the UI, show the sign and a label, not just a number.
- Validate with a known charge and a known drive before changing any display
  convention.

### Caveat

The exact zero offset (150,000) and the small resting current are not a
calibrated shunt measurement. The sign conclusion is strong; the absolute
accuracy at ±0.01 A is not established.

---

## F5 — Cabin CO2

### Evidence

- Profile row: DID `0x42DB`, SBI 1, DL 1, MUL 100, unit ppm.
- Raw payload: `03FE`.
- Current decoder: byte 1 = `0xFE` = 254 × 100 = **25,400 ppm**.
- u16 big-endian: `0x03FE` = **1,022 ppm**.

25,400 ppm is implausibly high for a parked cabin and is constant across
samples. 1,022 ppm is a normal cabin concentration. The profile may be wrong
about the field, not just our decoder.

The 2026-10-22 passive capture confirms the response is exactly `62 42 DB 03 FE`
on `0x7B0`, and all 40 stored rows are `03FE`. The same `03FE` is the repeated
not-fitted sentinel in `cell_t_18`–`cell_t_23`. The decoder therefore returns
`None` for `03FE`; a non-sentinel frame is read as a whole u16 ppm value. The
non-sentinel scale is still provisional and the sensor appears not fitted on
this car.

### Proposed test

Read in fresh air with the HVAC off, then with occupants. A non-sentinel value
should move in roughly 400–2,000 ppm as a whole u16; `03FE` must stay `None`.
This is not a drive-warning signal.

---

## F6 — Windshield temperature

### Evidence

- Profile row: `0x27C4`, SBI 0, DL 2, MUL 15.625, OFS -39.5.
- Raw payloads are five bytes, e.g. `7A2321C07D`.
- Current decoded value: **488,507.375 °C**.
- Byte 4 (`0x7D`) × 0.5 = 62.5%, consistent with the separately decoded cabin
  humidity, so the payload is at least partly understood.

No simple 2-byte interpretation of bytes 0–1 with the profile scale is physical.
The 2026-10-15 passive capture contains five-byte payloads `7A2321C07D`,
`719F619091`, and `729FD1988D`; byte 0 × 0.1 gives 12.2, 11.3, and 11.4 °C,
within a few degrees of the same-session outside temperature. Byte 0 × 0.1 is
now implemented. The remaining bytes are not decoded.

### Proposed action

- Keep the key quarantined; do not use it in scoring until the label is decided.
- Log all five bytes and compare with ambient, cabin and HVAC outlet
  temperatures during a cold soak and a defroster test.
- Consider that this vehicle's `0x27C4` payload variant differs from the profile
  author's car. The old u16 scale is refuted; do not revive it.

---

## F7 — `0x5867` odometer

### Evidence

- Profile row: SBI 2, DL 2, ×10.
- Raw `0005045A00000000`: `0x045A` = 1,114; ×10 = **11,140 km**.
- Raw `0005045B00000000`: `0x045B` = 1,115; ×10 = **11,150 km**.
- `0x02BD`, `0x295A`, `0xF817` all read ~**111,009–111,020 km** on the same car.
- The x10 counter increments 1 LSB (10 km) while the odometers move ~10.5 km;
  ×100 would require 100 km per LSB and is refuted by that delta.
- The constant offset is **99,869–99,871 km** (111,009 − 11,140; 111,020 −
  11,150), consistent with a BMS replacement/reset at ~99,870 km. Service
  history is not available, so the absolute zero is still not usable.

This is a 10 km/LSB secondary counter, not a profile scale error. Do not "fix"
it by choosing ×100 to make the absolute numbers agree.

### Proposed test

Log `0x5867`, `0x02BD`, `0x295A`, `0xF817` and `eldrive_odometer` on one drive.
Check increments over a known distance. The 10 km/LSB step should track the
drive, and the ~99,870 km offset should stay constant unless the BMS was reset.
A service record showing no BMS replacement would refute the offset hypothesis.

---

## F8 — BMS `0xF802` VIN

### Evidence

- 63 attempts on `bat_mgmt` `0xF802`, 0 successes.
- NRC `0x31` (`requestOutOfRange`), or no/undecodable response.
- The same DID on `veh_info` returns `WVWZZZE1ZMP087053`.

### Proposed action

Mark the BMS `0xF802` entry unsupported and stop polling it. Keep the
vehicle-info VIN path. This is an addressing/ECU-variant mismatch, not a decoder
problem.

---

## F9 — Battery serial

`0x0500` returns ASCII `32383820565741524D464632363520` = `"288 VWARMF F265 "`.
The DB records success but `value` and `text_value` are null. This is a decode
gap, not a missing response. Low diagnostic value; decode as text if the module
identity is needed for service records.

---

## F10 — Lower-priority anomalies (not part of the main fix)

| Signal | Observation | Status |
|---|---|---|
| `bat_charging_voltage` (`0x1E39`) | Samples are 0 or the `0xFFFF` sentinel (2047.97 V) | Not usable as a voltage without sentinel handling; meaning unclear |
| `dc_dc_converter_reserved_current` (`0x2A53`) | 243–252 A; impossible for a DC-DC current | Field/scale unknown; "reserved" label misleading |
| `cell_soc_max/min` (`0x1E2C/0x1E2D`) | Numeric column null; value/index in `text_value` JSON | Data is present, schema hides it; promote to numeric |
| `12 V voltage` scaling | Car Scanner `u16*0.001+4.0`; spot2000/evDash `u16/1024+4.26` | Difference ~0.006 V at 14.8 V; not a material conflict |
| `a_c_compressor_load` (`0x2604`) | All sampled values 0 | Could be unsupported or state-dependent; not audited further |
| `brake_booster_pressure` (`0x2005`) | All sampled values 0 | Could be unsupported or state-dependent; not audited further |
| `steering_wheel_angle` (`0x0346`) | 4.6–413.3 in samples | Suspicious offset/scale, but outside the drive/LV scope |

These are recorded so they are not lost, not promoted as findings.

## F11 — ABS wheel speeds (`0x1800`–`0x1803`)

The Car Scanner `[03.ABS]` profile declares four DIDs on `0x713 -> 0x77D`,
`SBI=0 DL=2 ×0.01 km/h`. The 2026-10-15 passive capture contains Car Scanner's
sequence: `ATSH713`, `ATSP6`, `ATCRA77D`, `1003`, positive
`77D065003001E01E5`, then `221800`–`221803`. Each response is a 12-byte block;
the parked first u16 is zero (`00000000FF00FFFFFFFFFFFF`).

The four decoders are registered by `register_abs()` and are deliberately
**not** in the default registry, so the normal collector never sends the reads
without the extended session. The dedicated reader
(`tools/read_abs_wheel_speeds.py`) requires `--execute --approve-session-change`,
sends exactly one `1003`, reads only those four DIDs, and always attempts
`1001`. It has **not** been executed against the car. The ordinary UDS
allow-list still refuses `1003`.

## Negative controls / things that look wrong but are not

- **Cell voltage decoder** matches the profile: `u16/4096` on `0x1E40..`.
- **12 V voltage** differs from spot2000/evDash by only ~0.006 V; the two
  scalings are effectively equivalent at 14.8 V. Do not "fix" it on the basis
  of the formula alone.
- **`cell_soc_max/min`** are not missing data; the values are packed into
  `text_value` JSON. The problem is presentation.
- **`eldrive_odometer`** is a ByteSet, not a float32. It reads ~111,020 km and
  matches the other odometers; do not include it in the float32 repair.
- **`energy_counters`** currently returns a JSON object but no `value`; the
  presence of a JSON object does not mean the numbers are right.

## What was not changed

At the time of the original 2026-10-21 audit, no decoder, registry, catalogue,
database, profile, log, test, or production file was modified; all new work was
additive under `research/measurement_discovery/`. The 2026-10-22 resolution log
above records the later, evidence-backed decoder/tool changes. `data/odb_scanner.db`
itself has never been rewritten; every audit, export, validation and passive
mining step opened it read-only or did not touch it.
