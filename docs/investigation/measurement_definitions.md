# Measurement definitions (Phase F) - baseline status

> **Update 2026-10-15.** The two gateway energy-content measurements
> (`hv_energy_content` DID 0x2AB8, `hv_energy_max` DID 0x2AB2) were resolved
> from the Car Scanner `profiles7.db` MEB profile (index 342) and are now
> implemented in `decoders/gateway.py`. Read
> **`meb_gateway_energy.md`** before using them — the gateway addressing and
> the 0x2AB8 payload offset are still unconfirmed on the vehicle.

## What we know (verified)
Current registry has battery/charging DIDs (BMS 11-bit UDS 0x22 reads):
- pack_voltage: DID 0x1E3B, ECU bat_mgmt (0x7E5/7ED), decode u16be/64, V, documented [OVMS], experimental on ID.3 per notes but implementation exists.
- pack_current: 0x1E3D, (u16-2048)/5 A.
- soc_abs: 0x028C, /2.5 %.
- cell_voltage_min/max: 0x1E33/0x1E34, /256 V.
- battery_temp: 0x2A0B, /64-40 C (experimental).
- energy_counters: 0x1E32 (slow), 2*u32/3600 kWh.
- soh_cac: 0x74CB (slow), first u16/10 Ah.
- cell_v_* 0x1E40+ (slow, experimental range).
Charging: various DIDs on chg_mgmt/chg.

BMS working path validated by tests/simulator: read DID 0x1E3B returns data, decoded correctly.

## Car Scanner resource findings (from audit report)

> **Superseded in part (2026-10-15).** `vag.micro.vag` is indeed NOT the
> measurement table (that finding stands). But the measurement definitions
> have since been located: embedded resource `profiles7.db`, not
> `vag.micro.vag` or `vagmeb.db`. See `meb_gateway_energy.md`.

### vag.micro.vag structure
- LZMA decompressed + ShiftStream2 decoded = 10,009,474 bytes
- **3-byte little-endian integers** (3,336,491 indices + 1 trailing byte)
- Each index points into `vag.strings.vag` to retrieve a PID string
- NOT a measurement table — it is an index array

### vag.strings.vag structure
- LZMA decompressed + ShiftStream2 decoded = 7,214,595 bytes
- Primary section: 8-byte records `07 P/U + 6 ASCII digits` (2,440 valid of 901,824 slots)
- Secondary format: remaining 99.7% is a different binary structure
- Target PIDs found but in unaligned secondary format

### DIDs found in vag.micro.vag (2-byte LE at various offsets)
- `0x028C` (SoC) - 77 occurrences
- `0x1E3B` (Pack Voltage) - 3 occurrences
- `0x2AB8` / `0x2AB2` (HV Energy Content) - 5 + 2 occurrences
- `0xF50E` (Min SoC) - 10 occurrences
- `0x465B` / `0x465D` (DC-DC) - 6 + 6 occurrences
- `0x09A1` / `0x09A2` (Gateway) - 11 + 18 occurrences

### hv_energy_content chain status
| Link | Status |
|------|--------|
| Display label -> measurement definition | **RESOLVED** — `[19.Gate] HV Battery energy content` |
| Measurement definition -> ECU | **RESOLVED** — `[19.Gate]` gateway, response header `0x710` |
| ECU -> request DID | **RESOLVED** — UDS `0x22`, DID `0x2AB8` (`0x2AB2` for the maximum) |
| Response parsing -> scale/offset | **RESOLVED** — `u16be` at payload offset 2, × 25 Wh |
| Scale/offset -> final value | **RESOLVED off the recovered source; NOT yet confirmed on the car** |

RESOLVED 2026-10-15 by the Car Scanner `profiles7.db` analysis: the live
measurement definitions live in `CarScannerMaui.EmbeddedFiles.profiles7.db`
(embedded resource), decoded LZMA → ShiftStream2 → BSON →
`List<OBDReaderProfileV2>`; the MEB profile is index **342** and carries 308
`CustomPID` records. `0x2AB8` and `0x2AB2` are the current and maximum HV
battery energy content, both from `[19.Gate]`, both unsigned big-endian,
2 bytes, ×25 Wh and ×50 Wh respectively, at payload offsets 2 and 0.

Implemented in `decoders/gateway.py` (`hv_energy_content`, `hv_energy_max`).
Full record, including the unresolved addressing questions, is in
**`meb_gateway_energy.md`**.

### Gateway addressing
- **11-bit `0x792`** remains the only code-verified Gateway request address (BuildList @ 0x1ffc60, GetECUName @ 0x188dd0)
- The `[19.Gate]` measurement records declare response header `0x710` (both `HDR` and `ATFCSH710`)
- `0x710` / `0x77A` in scan list but no label; `18DAD0F1` name-only
- NEW: in that profile `ATCRA = response header + 0x6A` for all five 11-bit ECU classes, so `ATCRA77A` is **not** a response CAN id — see `meb_gateway_energy.md` §4

## Missing (task priority)
- ~~Gateway [19.Gate]: hv_energy_content, hv_energy_max~~ **DONE** (definitions recovered, implemented, offline-tested; still needs the authorised in-car read)
- BMS pack voltage/current/temps/cells - pack voltage/current exist as above; cell temps not registered by default
- DC/DC converter [8105.DC/DC Converter]: voltage and current - DIDs `0x465B`/`0x465D` exist in micro file but no consumer

## Gaps
- ~~For hv_energy_content/max: need ECU identity, DID, scaling, units~~ **DONE** — all four supplied by `profiles7.db` profile 342
- ~~DID->scaling~~ **DONE for these two DIDs** — the profile carries explicit `MUL`/`DIV`/`OFS`/`SBI`/`DL` fields; no inference needed
- Still open: on-car confirmation of the gateway addressing pair and of the `0x2AB8` `SBI=2` offset


## Recommendation
Do not add guessed DIDs. The Car Scanner audit report documents all verified findings.

**Next step (2026-10-15):** the PID→DID consumer *has* been traced — the
definitions come from `profiles7.db` (`ProfileV2Model.LoadProfiles` →
`OBDReaderProfileV2.ImportProfilePids` → `CustomPIDViewModel.AddFromString`).
The remaining step is the authorised, read-only in-car read of the gateway
DIDs listed in `meb_gateway_energy.md` §7.

