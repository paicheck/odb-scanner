# Unknown payload research — 2026-10-21

**Status:** read-only desk research. No vehicle connection, no `0x22` probes, no
`data/odb_scanner.db` writes, no decoder changes.

**Scope:** every MEB payload whose meaning or scale is currently unresolved,
quarantined, or deferred:

- the five invalid/quarantined keys (`co2_content_interior`,
  `windshield_temperature`, `odometer_2`, `bat_charging_voltage`,
  `dc_dc_converter_reserved_current`);
- the three gateway DIDs with no retained payload (`0x2A26`, `0x2A28`,
  `0x2A2F`);
- the deferred custom-PID backlog (`0x263B`, `0x2430`, `0x22B3`, `0x0801`,
  `0x3ADF`/`0x3AE0`);
- the "reserved" / unsupported leftovers (`0xF449`, BMS `0xF802`);
- the presentation gaps (`battery_serial`, `cell_soc_max`/`cell_soc_min`).

**Evidence:**

- `data/odb_scanner.db` (opened `mode=ro`): 21 days of raw responses.
- `docs/investigation/live_measurement_evidence.csv`: extraction of the Car
  Scanner `profiles7.db` MEB profile (#342).
- `research/measurement_discovery/sources/evDash_CarVWID3.cpp` (fetched
  2026-10-20).
- `research/measurement_discovery/sources/spot2000_VW_MEB_UDS_PIDs_list.csv`.
- `decoders/meb.py`, `decoders/quality.py`, `decoders/catalogue.py`.

---

## Bottom line

Five keys are quarantined. The raw history now explains or refutes four of them:

| Key | New finding | Confidence |
|---|---|---|
| `dc_dc_converter_reserved_current` | Not a current. Bytes 3–4 are the **complement** of the real current in bytes 1–2: `u16[1:3] + u16[3:5] ≈ 0x0508` in every capture. The 250 A value is an artifact of subtracting 511 from the complement. | High |
| `odometer_2` | A **10 km/LSB distance counter**, not the odometer. It incremented 1 LSB (10 km) while the car moved ~10.5 km. The ×100 "fix" is refuted by that delta. Its absolute value is ~10× below the four agreeing odometers, so it has a different zero/meaning. | High for the delta and ×100 refutation |
| `bat_charging_voltage` | The profile declares a **signed** `uint16`; the decoder is unsigned. `0xFFFF` becomes 2,047.97 V. Signed it is −0.03125 V; the value is almost certainly a sentinel. | High |
| `windshield_temperature` | The profile's `u16[0:2] × 15.625 − 39.5` is refuted. Byte 0 × 0.1 °C tracks the independent outside temperature (mean absolute error 1.17 °C over 37 samples). Candidate layout: `u8[0] × 0.1`. | High that the current decode is wrong; medium-high for the candidate |
| `co2_content_interior` | 40/40 responses are `03FE`. Byte 1 × 100 gives 25,400 ppm (implausible); the whole `u16` is 1,022 ppm. The same `03FE` is the BMS "not fitted" sentinel for unpopulated cell-temperature slots. | High that 25,400 is wrong; **unresolved** between 1,022 ppm and not-fitted |

The three gateway DIDs are still **blocked**: the original `btsnoop_hci.log`
is not in the workspace, the payloads were never retained, and none of the
three appears in the Car Scanner, evDash, or spot2000 sources. Do not
blind-probe them.

Two "unknowns" turn out not to be payload unknowns at all:

- `0xF449` / `res_pid_0149` is the **accelerator pedal position**: one byte,
  `XX / 2.55` %. The decoder already does this and the live `00` = 0 % is
  correct for a parked car. Only the key name is misleading.
- BMS `0xF802` is an **addressing mismatch** (NRC `0x31`), not a payload
  problem. The same DID on `veh_info` returns the VIN.

`battery_serial` and `cell_soc_max`/`cell_soc_min` are presentation gaps, not
undecoded payloads.

The 241 catalogue entries with `Confidence.UNKNOWN` are **not** 241 unknown
payloads. `UNKNOWN` means "no live evidence yet"; the byte layout is usually
in the Car Scanner profile. Only the five quarantined keys, the three gateway
DIDs, and the deferred backlog are genuine payload unknowns.

---

## Method

1. Enumerated every key whose registry decode is `None`, whose quality
   verification is `INVALID`, or whose catalogue confidence is `UNKNOWN`.
2. Pulled every stored raw response for each candidate, grouped identical
   payloads, and looked for invariants (constant sums, fixed bytes, deltas
   between captures).
3. Compared against independent signals on the same timestamp
   (`outside_temp`, `inside_temp`, `cabin_humidity`, the three odometers).
4. Cross-checked the Car Scanner field definitions against evDash and
   spot2000 rather than treating any one profile as authority.
5. Only claimed what the bytes show. Where the bytes support two readings
   (CO2), both are recorded and neither is implemented.

---

## 1. `0x2A53` — the "reserved current" is a complement, not a current

### Payload

Five bytes, e.g. `95 02 0F 02 F9`:

| Bytes | Field | Decode |
|---|---|---|
| 0 | DC-DC low voltage | `u8 × 0.1` = 14.9 V |
| 1–2 | DC-DC current | `u16be − 511` = 0x020F − 511 = 16 A |
| 3–4 | "reserved current" | `u16be − 511` = 0x02F9 − 511 = 250 A |

### The invariant

Across all 124 successful `0x2A53` rows with a 5-byte payload:

| `u16[1:3] + u16[3:5]` | Count |
|---:|---:|
| 1288 (`0x0508`) | 68 |
| 1287 | 19 |
| 1289 | 30 |
| 1290 | 4 |
| 1291 | 3 |

The two fields are complementary: as the real current rises, the second field
falls by the same amount. Examples:

| Raw | First field | Second field | Sum |
|---|---:|---:|---:|
| `95020F02F9` | 527 (16 A) | 761 (250 A) | 1288 |
| `94021502F3` | 533 (22 A) | 755 (244 A) | 1288 |
| `94021F02EA` | 543 (32 A) | 746 (235 A) | 1289 |
| `96021202F9` | 530 (19 A) | 761 (250 A) | 1288 |

The ±1–3 variation is consistent with the two fields being sampled a moment
apart while the load changes.

### Conclusion

- The second field carries **no independent current information**. It is the
  complement of the first field (a redundant encoding / consistency word).
- `dc_dc_converter_reserved_current` = 250 A is an artifact of applying the
  real-current offset (−511) to the complement. It is not a physical current.
- The real DC-DC current is bytes 1–2: 15–32 A over the captures, 0.22–0.48 kW
  at 14.8–15.0 V, which is plausible for the 12 V rail.
- The quality note "same 15 A value as the DC-DC current field" is stale; the
  live value is 243–252 A because the complement is being decoded.

### Safe action

- Stop using `dc_dc_converter_reserved_current` as a current.
- Either drop it from analysis, or keep the raw field only as a consistency
  check: `u16[1:3] + u16[3:5] == 0x0508 ± 3`.
- If it is kept, rename it to something unitless (e.g.
  `dc_dc_current_complement`) and change the unit from `A` to none.

### Regression vectors

| Raw | Expected first current | Expected complement |
|---|---:|---:|
| `95020F02F9` | 16 A | 250 (not a current) |
| `96021202F9` | 19 A | 250 (not a current) |
| `94021502F3` | 22 A | 244 (not a current) |
| `94021F02EA` | 32 A | 235 (not a current) |

The invariant to test is the sum, not the second value.

---

## 2. `0x5867` — a 10 km/LSB counter, not the odometer

### The scale hypothesis

The Car Scanner row is `payload[2..3] uint16 × 10`, key `odometer_2`.

| Timestamp | Raw | `u16[2:4]` | ×10 | `0x02BD` | `0x086D` | `0xF817` |
|---|---|---:|---:|---:|---:|---:|
| 2026-10-18 13:53:18 | `0005045A00000000` | 1114 | 11,140 | 111,009 | 111,009 | 111,009.8 |
| 2026-10-19 19:10:00 | `0005045B00000000` | 1115 | 11,150 | 111,020 | 111,020 | 111,020.3 |

`odometer_km` (`0x295A`) independently reads 111,009 at 18 Oct 13:53 and
111,020 at 19 Oct 19:10, making it the fourth agreeing source.

The car moved ~10.5 km between those captures. The counter incremented **one
LSB**, and the ×10 decode incremented 10 km. That is consistent with a 10 km
LSB.

### Why ×100 is wrong

`decoder_audit.md` floated ×100 because 1,115 × 100 = 111,500 km is within
0.43 % of the other odometers. But ×100 would mean one LSB is 100 km. The car
moved 10.5 km, yet the LSB changed by one. The delta refutes ×100 even though
the absolute value looks closer.

### What the field actually is

- It is a distance counter with a **10 km LSB**.
- Its absolute value (11,150) is ~10× below the four independent odometers
  (111,020). It is not the same zero, so it is not "odometer #2" in the sense
  of a second copy of the total odometer.
- Candidate meanings: a service/lifetime distance counter, a trip counter, or
  a different module's distance accumulator. The 10 km resolution makes it
  unsuitable as an odometer replacement.
- The four agreeing sources (`0x02BD`, `0x086D`, `0x295A`, `0xF817`) are the
  odometers to use.

### Safe action

- Keep `odometer_2` out of odometer calculations.
- Document it as a 10 km/LSB secondary counter, not an odometer.
- Do not change the ×10 scale; it is correct for the field's own LSB.
- A known-distance drive can confirm the 10 km LSB, but it will not explain
  the absolute offset.

### Regression vectors

| Raw | Expected ×10 value | Expected delta vs `0x02BD` |
|---|---:|---|
| `0005045A00000000` | 11,140 | n/a (baseline) |
| `0005045B00000000` | 11,150 | +10 km for ~10.5 km driven |

---

## 3. `0x27C4` — five-byte HVAC payload; byte 0 is the temperature

### Current decode

Profile: `payload[0..1] uint16 × 15.625 − 39.5`.

Raw `72 9F D1 98 8D` → `0x729F` = 29,343 → 29,343 × 15.625 − 39.5 =
**458,444.9 °C**. The value is invalid; the quality registry already excludes it.

### What the bytes show

| Byte | Raw | Candidate | Evidence |
|---|---|---|---|
| 0 | 0x72 = 114 | `× 0.1` = 11.4 °C | Across 37 paired samples, byte 0 × 0.1 vs independent `outside_temp` has **mean absolute error 1.17 °C, max 2.5 °C**. It tracks ambient/windshield temperature. |
| 1 | 0x9F = 159 | unknown HVAC field | Varies 3.2–23.2 °C under ×0.1; no independent match. |
| 2 | 0xD1 = 209 | unknown HVAC field | Varies 0.1–24.2 °C under ×0.1; no independent match. |
| 3 | 0x98 = 152 | unknown HVAC field | Byte 3 × 0.1 does **not** consistently match `inside_temp` (mean absolute error 8.73 °C, max 22.2 °C). Two earlier spot matches were coincidental. |
| 4 | 0x8D = 141 | `× 0.5` = 70.5 % | Matches the profile's `cabin_humidity` field on the same DID exactly (70.5 %). Confirmed. |

The payload is therefore not a single `uint16`; it is a small block of 8-bit
HVAC fields plus humidity. The profile's `u16[0:2]` read is the wrong width.

### Candidate layout

- `u8[0] × 0.1 °C` — windshield/ambient temperature.
- `u8[4] × 0.5 %` — cabin humidity (already separate key).
- Bytes 1–3: other HVAC temperatures/states, not identified.

The byte-0 correlation with outside temperature supports the scale but does
not distinguish "windshield surface" from "ambient air". The key name comes
from the profile; the physical location is not proven.

### Safe action

- Keep the current value invalid.
- Add a **provisional** candidate decoder `u8[0] × 0.1` only after a known-state
  test; do not merge it as verified yet.
- Regression vectors for the candidate:

| Raw | Current (invalid) | Candidate byte 0 ×0.1 | Independent outside |
|---|---:|---:|---:|
| `719F619091` | 454,444.9 | 11.3 | 9.0 |
| `729FD1988D` | 458,444.9 | 11.4 | 9.0 |
| `7A2321C07D` | 488,507.4 | 12.2 | 12.0 |
| `7A62C1BC81` | 489,491.8 | 12.2 | 12.0 |

- A cold-soak vs heated-windshield test would separate windshield from ambient.

---

## 4. `0x1E39` — signed field decoded unsigned; `0xFFFF` is 2,048 V

### Evidence

- Profile row: `[8C.BMS] HV Battery charging voltage`, DID `0x1E39`,
  `payload[0..1] big-endian uint16, **signed**`, `MUL 0.0312499784501336`.
- Registry: `decoders/meb.py:659` puts the key in `_CS_FIELDS`;
  `_CS_FIELDS` has no sign column and the loop at `:784` uses
  `_cs_field_decoder(...)`, which defaults to `sig=False` (`:408-420`).
- One raw capture is `FFFF` (2026-10-18 18:22:50). Unsigned it is
  65,535 × 0.03125 = **2,047.97 V**. Signed it is −1 × 0.03125 =
  **−0.03125 V**.
- The other samples are 0, 0.03125, 0.0625, 0.1875 V — near zero, consistent
  with the car not charging.
- A negative charging voltage is also physically odd, so `0xFFFF` is most
  likely an invalid sentinel, not a real −0.03 V reading.

### Conclusion

The 2,048 V is a decoder artifact: the profile's signedness is not applied.
It is not a physical event and must not be treated as a charging-voltage
excursion.

### Safe action

- Preferred: treat `0xFFFF` (and `0xFFFE`) as `None` / invalid.
- If preserving the profile's signedness, decode as signed and then apply a
  plausibility rule that rejects negative charging voltage.
- Add tests:

| Raw | Unsigned today | Expected |
|---|---:|---|
| `FFFF` | 2,047.97 V | `None` (or −0.03125 V if signed-only is chosen) |
| `0000` | 0.0 V | 0.0 V |
| `0001` | 0.03125 V | 0.03125 V |
| `0006` | 0.1875 V | 0.1875 V |

---

## 5. `0x42DB` — constant `03FE`: 1,022 ppm or not-fitted sentinel?

### Evidence

- 40 successful reads, **all** `03FE`; 18 empty responses. No other value in
  the database.
- Car Scanner: `payload[1..1] uint8 × 100` → `0xFE` = 254 × 100 =
  **25,400 ppm**.
- spot2000: `YY * 100` → same 25,400 ppm.
- Whole `uint16` big-endian: `0x03FE` = **1,022 ppm**, a normal cabin CO2
  concentration.
- The same two-byte value `03FE` is the BMS "not fitted" sentinel for
  unpopulated cell-temperature slots (`cell_t_18`–`cell_t_23` on this 58 kWh
  pack). The ID.3 may not have an interior CO2 sensor.
- A real CO2 sensor should vary with occupancy and ventilation; this value is
  constant over four days and different HVAC states.

### Conclusion

25,400 ppm is not credible. There are two survivors:

1. **Not fitted / invalid**: `03FE` is a sentinel; the sensor is absent or not
   reporting.
2. **Whole `uint16`**: 1,022 ppm is real; the profile's byte-1 field is wrong.

The sentinel explanation is supported by the cross-module pattern and the
constancy, but the 1,022 ppm reading is also plausible. The bytes alone cannot
choose.

### Safe action

- Keep quarantined. Per `validation_roadmap.md` V0.7, record both hypotheses;
  do not change the decoder unilaterally.
- Known-state test: fresh air with HVAC off, then a closed cabin with
  occupants. If the value never moves, it is a sentinel. If it moves in the
  400–2,000 ppm range, the whole-`uint16` reading is real.
- Do not use it as a drive-warning signal either way.

---

## 6. `0xF449` — resolved: accelerator pedal, not a reserved PID

### Evidence

- Registry key `res_pid_0149`, DID `0xF449`, name "Accelerator pedal position",
  `u8 / 2.55`, unit `%` (`decoders/meb.py:2533`).
- spot2000: `0x00000746 03 22 f4 49`, response `62 f4 49 XX`,
  `XX / 2.55 = pedal position in %`.
- evDash lists `22F449` as "Accelerator pedal position, %".
- Live raw `00` → 0 %. The car was parked; 0 % is correct.

### Conclusion

The payload is a single byte, 0–100 % via `/2.55`. The decode is already
correct. The only problem is the `res_pid_0149` name, which makes a resolved
signal look unknown.

### Safe action

- Add a display alias `accelerator_pedal_pct`; do not rename the stored key
  (that would break historical queries).
- A pressed-pedal read can confirm the top of the range; not required for the
  payload layout.

---

## 7. BMS `0xF802` — addressing mismatch, not a payload

- `unsupported_dids` records `bat_mgmt` DID `0xF802`; `decoder_audit.md`
  reports 63 attempts, NRC `0x31` / no response.
- The same DID on `veh_info` returns the VIN (`WVWZZZE1ZMP087053`).
- This is an ECU-variant/addressing issue, not a decoder or payload issue.
- No action beyond keeping the BMS entry unsupported and using the `veh_info`
  path. Already handled.

---

## 8. Gateway `0x2A26`, `0x2A28`, `0x2A2F` — still blocked

### What is known

- The 2026-10-15 passive HCI inventory records positive `0x62` responses on
  `0x710 -> 0x77A`, alongside the confirmed `0x2AB2`/`0x2AB8` energy DIDs.
- The payloads were **not retained** in the text inventory; the original
  `btsnoop_hci.log` is not in the workspace.
- None of the three appears in the Car Scanner MEB profile (#342), evDash, or
  spot2000.
- There is no length, layout, or scaling evidence.
- `tools/scan_gateway.py` deliberately excludes them via `QUARANTINED_DIDS`.

### Conclusion

This is a genuine evidence gap, not an unresolved byte layout. A positive
response alone does not justify a decoder, and a blind `0x22` probe is against
the project's read-only/evidence rules.

### Safe next step

1. Recover the original `btsnoop_hci.log` or another saved capture and extract
   the request/response/length for these three DIDs.
2. If no capture exists, obtain explicit owner approval for a **bounded**
   read-only capture of exactly these three DIDs on the known
   `0x710 -> 0x77A` path.
3. Only then extend the gateway scanner. Until one of those happens, blocked.

---

## 9. Deferred custom-PID backlog

These are not in the current scan set; none has a live payload in this
workspace.

| DID | Signal | Evidence available | Status / next step |
|---|---|---|---|
| `0x263B` | HVAC recirculation | spot2000: 1 byte, `00` = fresh, `04` = manual recirculation. evDash has the same comment but a stub parser. | Layout is simple; needs a read-only fresh/recirc capture to confirm `00`/`04`. |
| `0x2430` | GPS fix block | evDash parser: ASCII latitude/longitude plus altitude at evDash hex offsets 62–65 (payload bytes 28–29) minus 501. Requires multiframe handling. | `0x2431` satellite count is already implemented; `0x2430` is deferred until multiframe parsing is tested. |
| `0x22B3` | GPS time | evDash parser: 4-byte bit-packed time at payload bytes 2–5 (year 5, month 4, day 5, hour 5, minute 6, second 6). | Same multiframe dependency; low priority. |
| `0x0801` | PTC air heater HV current | spot2000 marks it multiframe with no equation; evDash lists it but has no parser. | Needs a PTC-on test and a raw-length check before any equation. |
| `0x3ADF` / `0x3AE0` | Seat heating L/R | Name only in the Car Scanner profile; no layout or equation. | Comfort only; defer. |
| `0x1800`–`0x1803` | ABS wheel speeds | Profile-supported. | Not a payload unknown; requires `0x10 03` on `0x713`, a separately approved session change. |

The common thread: these are **known gaps with a named next test**, not
undecipherable payloads.

---

## 10. Presentation gaps, not payload unknowns

| Key | Raw | Decode | Issue |
|---|---|---|---|
| `battery_serial` (`0x0500`) | `32383820565741524D464632363520` | ASCII `"288 VWARMFF265 "` in `value` | `text_value` is null while other text keys use it. The decode is correct; the field choice is inconsistent. |
| `cell_soc_max` (`0x1E2C`) | `1F180026` | 79.6 %, index 38 | Stored as JSON in `text_value`; numeric `value` is null. Layout is known (u16/100 + u16 index). |
| `cell_soc_min` (`0x1E2D`) | `1E7A0023` | 78.02 %, index 35 | Same JSON/numeric presentation gap. The index is a cell number, not a missing payload. |
| `0x15D1` fields | `01 00 00 1E 33 03 25…` | 0 W, 1,500 W, 2,550 Wh, 40,250 Wh | The four profile layouts match the raw exactly; values are plausible for a parked car. No payload error found. |
| `0x2AF7` 12 V block | short payloads | `<no value>` beyond the available bytes | The decoder refuses to fabricate; this is correct behaviour, not a failure. |

The 0x15D1 fields were checked because they are UNKNOWN-confidence in the
catalogue. Their layouts are not unknown:

- `hv_em_power_limit`: `u16[1:3] × 50` = 0 W while parked.
- `hv_em_nv_power_limitation`: `u8[3] × 50` = 0x1E × 50 = 1,500 W.
- `nv_energy_requirement`: `u8[4] × 50` = 0x33 × 50 = 2,550 Wh.
- `hv_em_hv_battery_energy_information`: `u16[5:7] × 50` = 0x0325 × 50 =
  40,250 Wh.

The 0 W power limit is consistent with the car being parked. No evidence of a
decoder error.

---

## 11. Why "241 UNKNOWN keys" is not 241 unknown payloads

`build_catalogue('meb')` has 287 entries. Of those, 241 are
`Confidence.UNKNOWN`. That is a **verification** state, not a payload state:

- The layout is usually copied from the Car Scanner profile and is already
  decoded by the registry.
- The live data often confirms the value is plausible; what is missing is a
  known-state reference or a second source.
- The five `INVALID` keys in `decoders/quality.py` are the actual payload
  problems. The three gateway DIDs and the deferred backlog are the actual
  evidence gaps.

A full "verify all 241" campaign is a separate, much larger effort: it needs
one known-state sample per key, not another decode. It should not be conflated
with this payload audit.

---

## 12. Summary table

| DID | Key | Raw example | Current decode | Finding | Confidence | Action |
|---|---|---|---|---|---|---|
| `0x2A53` | `dc_dc_converter_reserved_current` | `95020F02F9` | 250 A | Complement of bytes 1–2; sum ≈ 0x0508 | High | Stop using as current; optional consistency check |
| `0x5867` | `odometer_2` | `0005045B00000000` | 11,150 km | 10 km/LSB secondary counter; ×100 refuted by 10.5 km delta | High for delta; medium for meaning | Keep out of odometer calculations |
| `0x27C4` | `windshield_temperature` | `729FD1988D` | 458,444.9 °C | Byte 0 × 0.1 tracks outside temp; profile u16 scale refuted | High current wrong; medium-high candidate | Keep invalid; cold-soak/defroster test |
| `0x1E39` | `bat_charging_voltage` | `FFFF` | 2,047.97 V | Signed field decoded unsigned; sentinel | High | Return `None` for `FFFF` or apply signedness |
| `0x42DB` | `co2_content_interior` | `03FE` | 25,400 ppm | Whole u16 = 1,022 ppm; same value is BMS not-fitted sentinel | High 25,400 wrong; unresolved | Keep quarantined; fresh-air/occupied test |
| `0xF449` | `res_pid_0149` | `00` | 0 % | Accelerator pedal, `/2.55`; decode correct | High | Alias the key; no decode change |
| `bat_mgmt 0xF802` | unsupported | NRC 0x31 | — | Wrong ECU for VIN | High | Keep unsupported; use `veh_info` |
| `0x2A26` | — | not retained | — | No payload/layout | High (no evidence) | Blocked; recover capture or approved bounded probe |
| `0x2A28` | — | not retained | — | No payload/layout | High (no evidence) | Blocked |
| `0x2A2F` | — | not retained | — | No payload/layout | High (no evidence) | Blocked |
| `0x263B` | recirculation | none | — | 1 byte, 00/04 | Medium | Read-only fresh/recirc capture |
| `0x2430` | GPS fix | none | — | evDash ASCII/altitude parser | Medium | Multiframe implementation |
| `0x22B3` | GPS time | none | — | evDash bit-packed time | Medium | Multiframe implementation |
| `0x0801` | PTC HV current | none | — | Multiframe, no equation | Low | PTC-on test |
| `0x3ADF`/`0x3AE0` | seat heat | none | — | Name only | Low | Defer |
| `0x1800`–`0x1803` | ABS wheel speeds | none | — | Profile-supported, session-gated | High (blocked) | Separate `0x10 03` approval |

---

## 13. Recommended offline changes (if approved)

No decoder changes are made by this document. If the owner wants to act on
the findings, the safe order is:

### High confidence, no vehicle needed

1. **`0x2A53` reserved current**: stop treating it as a current. Update the
   quality note and either remove the key from analysis or rename it to a
   unitless complement. Add the sum invariant test.
2. **`0x1E39`**: treat `0xFFFF` as `None` (or apply signedness and reject
   negatives). Add the four raw vectors above.
3. **`0x5867`**: update the quality/catalogue note to "10 km/LSB secondary
   counter; not the odometer"; do not change the scale. Add the two-row delta
   test.
4. **`0xF449`**: add a display alias `accelerator_pedal_pct`; leave the decode
   and stored key alone.

### Provisional, needs a known-state test

5. **`0x27C4`**: implement `u8[0] × 0.1` only as a provisional decoder after a
   cold-soak/defroster capture; keep the current value invalid until then.
6. **`0x42DB`**: do not change. Add a test that records both the byte-1 and
   whole-u16 hypotheses, per V0.7.

### No change

7. Gateway `0x2A26`/`0x2A28`/`0x2A2F`: blocked.
8. Backlog `0x263B`, `0x2430`, `0x22B3`, `0x0801`, `0x3ADF`/`0x3AE0`: defer.
9. ABS `0x1800`–`0x1803`: session approval, not a decoder change.

---

## 14. What would falsify these conclusions

| Finding | Falsified by |
|---|---|
| `0x2A53` complement | A capture where `u16[1:3] + u16[3:5]` is not ≈ 0x0508, or a known-load test where the second field moves independently. |
| `0x5867` 10 km/LSB counter | A known-distance drive where the LSB does not track 10 km, or a capture where the absolute value matches the total odometer. |
| `0x27C4` byte 0 ×0.1 | A cold-soak/defroster test where byte 0 does not follow the windshield/ambient temperature, or a payload variant where byte 0 is static. |
| `0x1E39` signed/sentinel | A capture where `0xFFFF` is followed by a real charging voltage and the field is known to be unsigned. |
| `0x42DB` sentinel vs 1,022 ppm | A fresh-air/occupied test where the value moves in the 400–2,000 ppm range (whole-u16 hypothesis) or stays fixed (sentinel). |
| Gateway blocked | Recovery of the original btsnoop payloads, or an approved bounded capture. |

---

## 15. Reproduction notes

- The DB was opened with `sqlite3.connect("file:data/odb_scanner.db?mode=ro",
  uri=True)`; no writes.
- The complement check used every `pid='0x2A53'` row with a 10-hex-character
  raw payload.
- The odometer comparison used the 18 Oct 13:53:18 and 19 Oct 19:10:00
  captures, where all four distance sources are present.
- The HVAC correlation used 37 timestamps where `windshield_temperature`,
  `inside_temp`, and `outside_temp` were all successful.
- The CO2 count is 40 non-empty `03FE` responses plus 18 empty responses.

---

## 16. Sources

- `data/odb_scanner.db` — live raw evidence (read-only).
- `docs/investigation/live_measurement_evidence.csv` — Car Scanner
  `profiles7.db` MEB profile extraction.
- `research/measurement_discovery/sources/evDash_CarVWID3.cpp` — evDash
  addressing and parsers.
- `research/measurement_discovery/sources/spot2000_VW_MEB_UDS_PIDs_list.csv` —
  spot2000 formulas.
- `decoders/meb.py` — registrations and `_CS_FIELDS` / `_cs_field_decoder`.
- `decoders/quality.py` — invalid/quarantined keys.
- `research/measurement_discovery/decoder_audit.md` — prior audit (F5–F10).
- `research/measurement_discovery/validation_roadmap.md` — V0.7/V0.8/V0.9
  regression plan.
- `docs/investigation/capture_positive_did_inventory_20261015.md` — gateway
  positive-response inventory.
