# Knowledge gaps: odb_scanner vs the Car Scanner MEB profile

**Date**: 2026-10-15
**Method**: cross-referenced `decoders/registry.py` (meb profile) against
`CustomPID` records from `profiles7.db` MEB index 342, and against the response
log in `data/real_car.db`.

## Summary

| | count |
|---|---:|
| our meb specs | 157 → **185** (28 added) |
| our distinct DIDs | 154 → **182** |
| Car Scanner MEB DIDs | 254 |
| DIDs in both | 151 → **179** |
| **DIDs only Car Scanner knows** | **103 → 75** |
| DIDs only we know | 3 |

Three of our decoders **disagree** with the profile. 28 new decoders were added
this pass; 75 gaps remain, 43 of them unreachable until the gateway is fixed.

## What the profile confirmed (no change needed)

Comparing each of our decoders against the profile's declared `SBI`/`DL`/`MUL`/
`DIV`/`OFS` on identical raw bytes, **11 of 14 sampled decoders agree exactly**.

Notably it vindicates a detail that looked like an evDash quirk: our cell-voltage
decoder is `u16/1000 + 1`, and the profile's `0x1E40` record is `MUL=1.0`,
`DIV=1000.0`, `OFS=1.0`. The `+1` is the vendor's own offset.

It also confirms our addressing. Every module address in `diagnostic/ecus.py`
matches a profile `BCM` string:

| module | profile `ATFCSH` / `ATCRA` | ours |
|---|---|---|
| BMS | `17FC007B` / `17FE007B` | matches |
| gateway | `710` / `77A` | matches |
| climate | `746` / `7B0` | matches |
| charger | `744` / `7AE` | matches |
| vehicle info | `17FC0076` / `17FE0076` | matches |

## Open conflicts — need an in-car capture

None of these can be settled offline; each needs one read-only frame.

### 1. `0x1E33` / `0x1E34` — cell index or voltage? (medium)

The profile names these *"HV Battery cell **#** with highest/lowest voltage"*
and declares `SBI=2 DL=2 MUL=1.0` — i.e. **a cell number**, not a voltage.

We decode them as `u16/4096` → volts, citing an evDash observation of
`16377 → 3.9984 V`. Both readings cannot be right:

- as a voltage, 16377/4096 = 3.9984 V — physically sensible for a cell
- as a cell index, 16377 is impossible on a 108-cell pack (valid range 0–107)

The profile is more specific about the offset (byte 2, not 0), which is the kind
of detail that only comes from the real definition. **We have not changed these.**
Our cell min/max also come from the per-cell sweep (`cell_v_000..107`), which is
independent, so nothing downstream depends on the answer yet.

### 2. `0x1E32` energy counters — incompatible layouts (high)

| | offset | width | scale | unit | shape |
|---|---|---|---|---|---|
| profile | **8** | 4 | `×116508.398 / 1e9` | kWh | one value |
| ours | **0 and 4** | 4 each | `/8583.07` | kWh | **two** values |

Different offsets, different scale (≈73,000× apart), different arity. One of us
is wrong. This feeds lifetime-energy stats, so it matters more than its row count
suggests. Unchanged pending a capture.

### 3. `0x028C` — two keys on one DID (low)

The profile has exactly one record: `u8/2.5`, *"State of charge BMS"*. Our
`soc_abs` matches it. Our `soc_normal` is an evDash dash-scale fit
(`raw*0.4425 − 6.1947`) with **no counterpart in the profile** — Car Scanner
displays BMS SoC, not a fitted dash value. Not wrong, but the two keys share a
DID, so provenance labelling is ambiguous. Left as-is; worth a decision.

## Added this pass (28 decoders, all slow)

Only modules our transport **demonstrably reaches** were added — the BMS
(29-bit `0x17FC007B`, 3,506/3,506 answered) and the charger (11-bit `0x744`,
211/211). Gateway and HVAC were deliberately excluded: they have never
answered, so those rows could never be collected.

| key | DID | scale |
|---|---|---|
| `odometer_bms` | 0x02BD | 3 bytes at [1:4] |
| `ptc_voltage` | 0x1621 | u8 ×0.1 |
| `bms_pcb_temp` | 0x1622 | u8 −50 °C |
| `ptc_medium_temp_1/2` | 0x1623/4 | u8 −50 °C |
| `bat_v_after_contacts` | 0x189B | u16 ×0.5 −1000 |
| `bat_v_before_contacts` | 0x1E36 | u16 ×0.03124997845 |
| `bat_v_after_contacts_2` | 0x1E38 | as above |
| `bat_charging_voltage` | 0x1E39 | as above |
| `cell_voltage_sum` | 0x1E3C | u32 ×244140.625/1e9 |
| `pack_current_2` | 0x1E3E | u32 ×0.01 −1500 |
| `min_discharge_voltage` | 0x5170 | u16 |
| `max_charge_voltage` | 0x5171 | u16 |
| `odometer_2` | 0x5867 | u16 at [2:4] ×10 |
| `motor_rpm` | 0xF40C | u16 /4 |
| `obc_dc_current` | 0x103D | u16 ×0.1 |
| `obc_cycle_energy` | 0x103E | u16 ×0.01 |
| `socket_a_voltage` | 0x1525 | u16 |
| `hv_output_voltage` | 0x1DA7 | u16 ×0.1 |
| `socket_a_temp` | 0x1DDA | u16 at [1:3] ×0.1 −55 |
| `cell_soc_max` / `cell_soc_min` | 0x1E2C/2D | u16 ×0.01 **and** u16 at [2:4] |
| `cell_t_18..23` | 0x7427..0x742C | u16 /8 −40 |

### Two subtleties the profile exposed

**`SBI` is load-bearing.** Six of these DIDs start at byte 1 or 2, not 0 —
`odometer_bms` reads bytes[1:4], `socket_a_temp` bytes[1:3],
`odometer_2` bytes[2:4]. Reading from byte 0 yields a plausible wrong number.

**Paired fields.** `0x1E2C`/`0x1E2D` carry a percentage at byte 0 *and* a cell
index at byte 2 — *"Battery max SoC cell %"* and *"... cell #"*. These decode to
a dict with both halves. Reading only byte 0 answers "how full" and silently
drops "which cell", which is the more interesting half.

**Absent temperature sensors.** The profile guards its points from `0x7426` on
with `if((A*256+B)>1020;[NaN];(A*256+B)/8-40)`. We now apply that guard to
**all 24 points** — previously an unpopulated sensor decoded to ~500 °C and would
have poisoned the pack temperature range. This also extended the bank from 18
sensors to 24, which is what the profile names them.

## Remaining gaps (75 DIDs)

| module | n | header | status |
|---|---:|---|---|
| `08.HVAC` | 25 | 746 | **never answers** (0/77) |
| `19.Gate` | 18 | 710 | **never answers** (0/114) |
| `51.ElDrive` | 26 | 17FC007C | untested |
| `01.ENG` | 18 | 17FC0076 | partial (81/195) |
| `03.ABS` | 4 | 713 | untested |
| `75.GPS` | 2 | 767 | untested |

**43 are blocked** behind the gateway addressing (see
`../CAR_SCANNER_REVERSE_ENGINEERING.md` §14.5). The remaining ~32 are on modules
we have never spoken to — `51.ElDrive` (`17FC007C`) is the biggest single
opportunity and sits right next to the BMS header that works.

## One oddity worth noting

The profile attaches 18 records to `[01.ENG]` on header `17FC0076` — front/rear
torque split, steering angle, HV auxiliary current. We call that header
`veh_info` and read odometer/gear/VIN from it. Both may be true (one ECU, many
DIDs), but our naming implies a narrower module than the profile suggests.

There are also **three odometer DIDs** across modules: `0x02BD` (BMS, 3 bytes at
offset 1), `0x5867` (BMS, ×10) and our existing `0x295A` (veh_info). Worth
cross-checking once we can read all three.

## Recommended next step

One ignition-on session of read-only captures against `17FC007B` settles
conflicts 1 and 2 and confirms the 28 new decoders. All three are on the BMS
header that already answers reliably, so it needs no addressing work and no
gateway. Keep it to `0x22` reads — `diagnostic/uds.py` already blocks every
write path.

*Generated from the raw profile; no vehicle communication was performed.*