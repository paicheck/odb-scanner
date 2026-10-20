# Recommended Car Scanner additions

**Context:** the installed MEB profile (index 342) already contains most of the
signals discussed here. This is mainly a *selection and labelling* task, not a
request to invent new PIDs. The recommendations are ordered for the owner's
electric-drive warning and low-voltage investigation.

## 1. Select these existing profile rows first

These rows are already in the installed profile. They need no custom PID and no
new decoding. The names are the exact profile labels; the duplicate-looking
names matter.

| Priority | Profile row | DID / field | Why |
|---:|---|---|---|
| 1 | `[8C.BMS] HV Battery cell # with highest voltage` | `0x1E33` SBI 2 | Identifies **which** cell is high, not just the voltage |
| 2 | `[8C.BMS] HV Battery cell # with lowest voltage` | `0x1E34` SBI 2 | Identifies the weak/low cell |
| 3 | `[8C.BMS] Battery maximum temperature Sensor ID` | `0x1E0E` SBI 2 | Identifies the hot sensor |
| 4 | `[8C.BMS] Battery minimum temperature Sensor ID` | `0x1E0F` SBI 2 | Identifies the cold sensor |
| 5 | `[8C.BMS] Total accumulated charge (Ah)` | `0x1E32` SBI 0 | Lifetime charge throughput |
| 6 | `[8C.BMS] Total accumulated discharge (Ah)` | `0x1E32` SBI 4 | Lifetime discharge throughput |
| 7 | `[8C.BMS] Total accumulated charge (kWh)` | `0x1E32` SBI 8 | Lifetime energy in |
| 8 | `[8C.BMS] Total accumulated discharge (kWh)` | `0x1E32` SBI 12 | Lifetime energy out |
| 9 | `[8C.BMS] Charging: Normal charge port` | `0x7448` bit 2 | Separates parked from AC-plugged |
| 10 | `[8C.BMS] Charging: Rapid charge port` | `0x7448` bit 3 | Separates parked from DC-plugged |

### Name traps

- `HV Battery cell # with highest voltage` (SBI 2, no unit) is the **cell
  number**. `HV Battery cell with highest voltage` (SBI 0, volts) is the
  **voltage**. Select both, but do not confuse them.
- The same pattern applies to the lowest cell and to the temperature Sensor ID
  rows.
- `Total accumulated charge (Ah)` is not the same as `(kWh)`. The current Python
  `energy_counters` used the Ah counters with the kWh divisor; Car Scanner's
  separate rows avoid that mistake.

## 2. Derived metrics to add if the app supports formulas

Car Scanner's profile has some derived rows; if the app/version supports custom
formula measurements, these are useful. If not, compute them in the Python
project (see `python_integration.md`).

| Metric | Formula | Inputs | Why |
|---|---|---|---|
| 12 V battery power | `voltage * current` | `0x2AF7` 12 V voltage/current | Direct load/charge indicator for the LV investigation |
| DC-DC output power | `voltage * current` | `0x2A53` voltage/current | Separates a 12 V battery problem from DC-DC load/regulation |
| Cell voltage spread | `max - min` (mV) | `0x1E33`/`0x1E34` | Weak-cell/balance trend |
| Cell SOC spread | `max - min` (%) | `0x1E2C`/`0x1E2D` | Weak-cell trend; already in the raw data |
| Battery temperature spread | `max - min` (°C) | `0x1E0E`/`0x1E0F` | Cooling/flow/sensor imbalance |
| A/C compressor on/off | power > ~50 W | `0x0800` power | Cheap state flag; power is already selected |

Do **not** add a "PTC heater kW" derived signal yet. The current `0x1621`
`ptc_voltage` reads a constant 14.7 V, so the product is not established as an
HV power. Revisit after a PTC-on test.

## 3. Optional additions / custom PIDs

These are not in the installed profile. Add only after the validation step
named. A custom PID is not harmless just because it is a read: wrong headers,
session changes, or multiframe handling can disturb a module.

| Priority | Signal | DID / address | Status | Add when |
|---:|---|---|---|---|
| 1 | HVAC recirculation | `0x263B`, `0x746 -> 0x7B0` | spot2000 + evDash, layout simple | After a fresh/recirc read confirms 00/04 |
| 2 | GPS fix block | `0x2430`, `0x767 -> 0x7D1` | evDash parser + sample | After multiframe parsing is implemented and tested |
| 3 | GPS time | `0x22B3`, `0x767 -> 0x7D1` | evDash parser | Same as GPS; low priority |
| 4 | PTC air-heater HV current | `0x0801`, `0x746 -> 0x7B0` | Name only, no equation | After a known PTC-on test and raw-length check |
| 5 | Seat heating L/R | `0x3ADF`/`0x3AE0`, `0x746 -> 0x7B0` | Name only | Comfort only; defer |
| 6 | Gateway `0x2A26`, `0x2A28`, `0x2A2F` | `0x710 -> 0x77A` | Positive response, no layout | Only after payload recovery; do not blind-probe |
| 7 | ABS wheel speeds | `0x1800`–`0x1803`, `0x713 -> 0x77D` | Profile-supported; needs `0x10 03` | Only with explicit approval for the session change |

### ABS caveat

The four wheel speeds are the only real profile-only DIDs. They are useful for
wheel-speed/ABS/ESC plausibility, but the profile's before-command includes
`1003` (extended session). That is a session change. Do not add them casually
to a logging profile; treat them as a separate, approved test.

## 4. Do not add / do not trust

| Item | Reason |
|---|---|
| Windshield temperature `0x27C4` | Current value ~488,500 °C; field/scale unresolved. Quarantine. |
| CO2 `0x42DB` | Current 25,400 ppm is implausible; byte/width unresolved. Do not use for decisions. |
| Odometer `0x5867` | Reads ~11,150 km vs ~111,020 km on three other odometers. Use `0x02BD`, `0x295A` or `0xF817` instead. |
| BMS VIN `0xF802` | NRC `0x31` on BMS. The VIN is on vehicle-info `0xF802`. |
| Pack current sign from Car Scanner | Car Scanner/ABRP negate the raw value. The physical convention is negative = discharge; use magnitude or a corrected custom label. |
| `RES(PID_0149)` as a mysterious PID | It is the accelerator pedal `0xF449`, 0–100%. Rename if the app allows a label; otherwise remember it. |
| `A/C compressor power` as "on/off" | It is a power value, not a boolean. Use > ~50 W if a boolean is needed. |
| `DC-DC converter reserved current` | Decodes 243–252 A in samples; not a usable current. Ignore until understood. |

## 5. Suggested logging set for the drive-warning investigation

If the owner wants a focused Car Scanner log, use this set rather than all 152:

**Electric drive / HV context**
- `DC Link voltage` (`0x3E93`)
- `DC Link current` (`0x3E98`)
- `Actual mechanical torque calculated` (`0x3EE9`)
- `Actual possible maximum torque` (`0x3EEA`)
- `Actual possible minimum torque` (`0x3EEB`)
- `Motor maximum current` (`0x3EE7`)
- `Motor minimum current` (`0x3EE8`)
- `Stator temperature` (`0x4672`)
- `Electric drive inflow coolant temperature` (`0x4FAE`)
- `ECU PCB Temperature #1/#2` (`0x0805`/`0x0806`)
- `Inverter power loss phase U/V/W` (`0x1040`–`0x1042`)

**Battery**
- Pack voltage, pack current, SoC, op mode/charge mode
- Cell voltage max/min + cell numbers
- Cell SOC max/min + SOC spread
- Battery temperature max/min + sensor IDs + spread
- Dynamic charge/discharge limits
- `0x1E32` four lifetime counters

**Low voltage**
- 12 V voltage, current, SoC, temperature
- 12 V total charge/discharge, aging capacity/power
- DC-DC voltage/current, derived power
- OBD adapter voltage (`lv_voltage_obd`) as a cross-check

**Context**
- Speed, gear, odometer (`0x02BD`/`0x295A`/`0xF817`)
- Outside/inside temperature
- Charge-port bits
- GPS fix (if available)

This set is still large for a phone log. If the app struggles, split it into
"drive/HV" and "LV/charging" sessions rather than dropping the raw bytes.

## 6. Profile hygiene notes

- Prefer the **SBI-qualified** rows (`... Sensor ID`, `... cell #`) over
  duplicate-looking names.
- The profile contains display aliases for the same DID. Selecting both is not
  harmful but wastes log bandwidth and creates duplicate columns.
- Car Scanner's `VERIFIED` flag means the profile author verified the layout,
  not that this car's module responds or that the value is physically correct.
- Keep the Car Scanner profile version/export with any log so the labels can be
  reproduced later.
- Do not edit the installed profile while investigating a warning; an export
  plus an additive custom-PID list is easier to audit.
