# Capture mining — positive-response DID inventory (2026-10-15)

Extracted from `btsnoop_hci.log` (a passive Bluetooth HCI capture of Car
Scanner reading an ID.3). Lists every DID that returned a positive `0x62`
ReadDataByIdentifier response, grouped by the CAN id it arrived on.

Analysis is read-only and local; the mining script is reproducible (it reads
only the hex ELM lines, so unrelated phone data cannot match).

## Already in the registry (no action)

- **0x77A energy**: `0x2AB8`, `0x2AB2` (the two confirmed energy DIDs).
- **17FE007B BMS (29-bit)**: the full known set — `0x028C` SoC, `0x1E3B` pack
  voltage, `0x1E0E/0x1E0F` temp extremes, `0x1E32` energy counters,
  `0x1E33/0x1E34` cell extremes, `0x1E36/0x1E38/0x1E39` voltages, `0x1E3C`
  cell sum, `0x1E3D` pack current, `0x1E40..0x1E5D` (108 cell voltages),
  `0x1EAE..0x1EBD` + `0x7425..0x742C` temp points, `0x2A0B`, `0x5170`,
  `0x5171`, `0x5867`, `0x743B`, `0x7448`, `0xF40C`, `0xF40D`, `0x1620`,
  `0x189D`.
- **17FE00B9 DCDC**: `0x465B`.

## New positive responses worth investigating

### Gateway energy family (0x77A, 11-bit) — the "more data" lead
- `0x2A26`, `0x2A28`, `0x2A2F`, `0x2AB4`, `0x2AB5`, `0x2AB6`, `0x2AB7`
  all return positive `0x62` responses from the same gateway module as the
  two energy DIDs. These sit directly alongside `0x2AB2`/`0x2AB8` and are
  candidates for additional energy/battery measurements. Scaling is NOT yet
  known — it must be recovered from the Car Scanner profile (profiles7.db
  index 342) before they can be decoded.

### Vehicle-info module (17FE0076, 29-bit)
- `0x0335`, `0x033B`, `0x0344`, `0x0346`, `0x035E`, `0x0360`, `0x2005`,
  `0x2055`, `0x276D`, `0x29D4`, `0x3DEF`, `0x3DF8`, `0x43B8`, `0x4539`,
  `0x4675`, `0xF817` (plus already-known `0x295A` odometer, `0x0364`).

### DCDC / other 29-bit modules
- 17FE007C: `0x0308`, `0x0805`, `0x2CCC`, `0x3E93`, `0x3E98`, `0x3EE7`,
  `0x3EE9`, `0x3EEA`, `0x3EEB`, `0x40C8`, `0x43B9`, `0x4672`, `0x4FAE`,
  `0x58E1`.
- 18DAF105: `0x0313`, `0x305A`. 18DAF10A: `0xF190`.

### VW identification DIDs (0xF1xx) — read-only ASCII
Read back VIN, hardware/software part numbers and version strings from most
ECUs. `0xF17B`, `0xF17C`, `0xF182`, `0xF186` (VIN), `0xF187`, `0xF189`,
`0xF18A`, `0xF18B`, `0xF18C`, `0xF190`, `0xF191`, `0xF192`, `0xF193`,
`0xF194`, `0xF195`, `0xF197`, `0xF198`, `0xF19B`, `0xF19E`, `0xF1A0`,
`0xF1A1`, `0xF1A2`, `0xF1A3`, `0xF1A9`, `0xF1AA`, `0xF1B1`, `0xF1B7`.
These are static identification strings, not live measurements; the project
already decodes VIN/serial as raw ASCII elsewhere.

### Misc 11-bit (0x77C/0x77D/0x77F/0x79B/0x79C/0x7A5/0x7C1/0x7B0)
- `0x0600`, `0x0800`, `0x1800..0x1803`, `0x42DB`, `0x6156`, `0x6167`,
  `0x6572`, `0xD476`, `0xE323`, `0xE445`, `0xE4C5`, `0xE535`, plus the
  `0xF1xx` identification set above.

## Conclusion

The capture confirms the two energy DIDs and the full known BMS set (which
already had good coverage), and — more importantly — surfaces a cluster of
**additional gateway energy-family DIDs (`0x2A26`, `0x2A28`, `0x2A2F`,
`0x2AB4`–`0x2AB7`)** that return positive responses. Recovering their scaling
from the Car Scanner profile is the concrete next step toward "more data".
