# Candidate inventory — 2026-10-26 autonomous discovery pass

**Vehicle:** 2021 VW ID.3 58 kWh (MEB). **Profile:** `meb`.
**Safety:** offline only. No vehicle connection, no diagnostic request, no
write/coding/adaptation/security-access/routine/actuator test, no fault
clearing. Every candidate here is a read-only UDS `0x22` read on a module the
collector already addresses, unless a "blocked" note says otherwise.

**Method:** the registry was cross-referenced against the
`OBDb/Volkswagen-ID3` signalset v3 (CC-BY-SA-4.0; rendered at
pelican.clutch.engineering/cars/volkswagen/id-3/parameters, "318 parameters").
That source records, per parameter: DID, module, byte offset, length,
multiplier/divisor/addend, unit, a plausible range, a worked example reply, and
a per-model-year confirmation status. It is a third-party reverse-engineering
definition — **not** a VW document and **not** a project-owned capture — so
every candidate below is at most PROVISIONAL until a known-state capture.

## Confidence vocabulary

Mirrors `decoders/catalogue.py` and the discovery brief:

- **VERIFIED** — direct evidence (project capture or independent physical
  cross-check) supports the claim.
- **STRONGLY INFERRED** — several independent pieces of evidence (here: an
  explicit third-party definition *plus* the already-VERIFIED module
  addressing) support the claim, but a key validation step remains.
- **HYPOTHESIS** — plausible, but limited/indirect evidence.
- **UNRESOLVED** — insufficient or contradictory evidence.

Confidence is recorded separately for ECU association, DID association, layout,
scale/units, and diagnostic usefulness (see the per-candidate rows).

## Implemented this pass

| Key | DID | ECU | Unit | Confidence | Status |
|---|---|---|---|---|---|
| `bat_internal_resistance_raw` | 0x2A25 | energy (0x710→0x77A) | "" (raw index) | PROVISIONAL (STRONGLY INFERRED) | **added to registry + catalogue + tests + spec** |
| `aux_battery_resistance_mohm` | 0x2A0E | energy (0x710→0x77A) | mOhm | PROVISIONAL (STRONGLY INFERRED) | **added to registry + catalogue + tests + spec** |

Rationale for implementing only these two: they are the only new DIDs that
(a) live on the gateway module whose 0x710→0x77A addressing is already VERIFIED
by this project's 2026-10-15 passive capture, (b) are confirmed for MY2021 by
the source, (c) are read-only `0x22` on the existing allow-list, and (d) map
directly onto the owner's two open investigations (HV battery ageing/SoH and
the low-voltage/12 V system). They are marked PROVISIONAL, not VERIFIED, and
the decoders raise on short payloads so nothing is silently mis-valued. If the
car returns NRC `0x31`, the collector auto-retires them like any other DID.

## Ranked candidate backlog (documented, not implemented)

Scoring rubric (each 0–5, higher is better): diagnostic usefulness, evidence
strength, validation feasibility (offline), novelty vs existing coverage,
implementation cost (ease). Safety compatibility is a pass/fail gate, not a
score. Only read-only `0x22` candidates pass the gate.

### P1 — highest value (implemented above)

| # | DID | Name | ECU | Layout (from OBDb) | Unit | Evidence | Confidence | Diagnostic value | Blocked by |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0x2A25 | HV battery internal resistance (raw) | 0x710→0x77A | u8 / 5 | raw (0–51) | OBDb ID3_BAT_INTERNAL_RES_RAW, confirmed MY2021 | STRONGLY INFERRED (scale PROVISIONAL) | HV pack ageing / SoH correlation | known-state capture (aged vs fresh, or vs reported SOH) |
| 2 | 0x2A0E | 12 V (start/stop) battery resistance | 0x710→0x77A | u8 × 2 / 10 | mOhm (0–10.2) | OBDb ID3_SSB_R, confirmed MY2020-2021 | STRONGLY INFERRED (scale PROVISIONAL) | 12 V battery ageing (low-voltage investigation) | known-load capture |

### P3 — conditional (needs a test or a lower-priority slot)

| # | DID | Name | ECU | Layout | Unit | Evidence | Confidence | Notes |
|---|---|---|---|---|---|---|---|---|
| 3 | 0x1512 | HV battery energy info (3 fields) | 0x710→0x77A | u16/40 kWh; u8/20 kWh; u16/20 kWh | kWh | OBDb ID3_HVBAT_ENERGY / ID3_NV_ENERGY / ID3_HIGH_V_EM_HVBAT, confirmed MY2021 | HYPOTHESIS | overlaps the already-VERIFIED 0x2AB2/0x2AB8; lower value |
| 4 | 0x2A15 | Energy balance of last drive cycle | 0x710→0x77A | s16 / 100 | Ah | OBDb ID3_ENERGY_BALANCE_LAST, confirmed MY2021 | HYPOTHESIS | drive-cycle energy accounting; needs a known drive |
| 5 | 0x27C3 | Outside humidity | 0x746→0x7B0 | u8[7] / 2 | % | OBDb ID3_HVAC_OUTSIDE_HUM (debug) | HYPOTHESIS | climate; low diagnostic value |
| 6 | 0x2606 | Radiator fan actual #1/#2 | 0x746→0x7B0 | u16/10, u16/10 | % | OBDb ID3_HVAC_FAN1_ACT / FAN2_ACT | HYPOTHESIS | thermal context; module not yet read by collector |

### P4 — low priority / speculative / blocked

| # | DID | Name | ECU | Layout | Unit | Evidence | Confidence | Notes |
|---|---|---|---|---|---|---|---|---|
| 7 | 0x0838 | ECall battery charge level + status | 0x767→0x7D1 | u8, u8 | % / scalar | OBDb ID3_ECALL_SOC / ID3_ECALL_BATTERY_CHARGE_STATUS, confirmed MY2021 | HYPOTHESIS | telematics battery; very low diagnostic value |
| 8 | 0x2611 | Leg deflector temperature | 0x746→0x7B0 | u8 / 3 | °C | OBDb ID3_CLIMATE_LEG_DEFL_T | HYPOTHESIS | comfort |
| 9 | 0x1945 | Washer nozzle heater active | 0x70E→0x778 | u8 | scalar | OBDb ID3_HTR_NOZZLE | HYPOTHESIS | comfort; new module 0x70E |
| 10 | 0x482B | A/C clutch | 0x746→0x7B0 | u8 | scalar | OBDb ID3_CLIMATE_A_C_CLCH | HYPOTHESIS | comfort |
| 11 | 0x1805 / 0x1812 | Steering torque / angle | 0x712→0x77C | s16×0.004395+0.06 / u16/8−750 | deg | OBDb ID3_STEER_WHL_TQ_SENS_1 / ID3_STEER_WHEEL_ANGLE | HYPOTHESIS | dynamics; new module 0x712; ABS/ESC-adjacent — treat conservatively |
| 12 | 0x1507 | Charger rail 1/2/3 energy | 0x744→0x7AE | u24/10 ×3 | kWh | OBDb ID3_CHRGR_RAIL1/2/3_E, confirmed MY2021 | HYPOTHESIS | **blocked**: the OBC (0x744) has never answered this project (211 requests, 0 successes) |
| 13 | 0x15D6 | Charger power efficiency | 0x744→0x7AE | u8/10+75 | (labelled "A") | OBDb ID3_CHRGR_EFFICIENCY | HYPOTHESIS | blocked as above |
| 14 | 0x15E2 | Charger coolant temperature | 0x744→0x7AE | u8−40 | °C | OBDb ID3_CHRGR_COOLANT_T, confirmed MY2021 | HYPOTHESIS | blocked as above |
| 15 | 0x15EE | Charging duration total | 0x744→0x7AE | u32 | min | OBDb ID3_CHRGR_DUR_TOTAL | HYPOTHESIS | blocked as above |

## Existing DIDs the source corroborates (no change made)

The OBDb signalset independently confirms many existing decoders, which
strengthens their PROVISIONAL status without changing them:

- `0x2AB8`/`0x2AB2` energy content/max — OBDb ×25/1000 kWh and ×50/1000 kWh at
  offsets 2/0, matching the VERIFIED decoders (and the passive capture).
- `0x2AF7` 12 V block — all ten fields (voltage, current, SoC, temps, charge/
  discharge totals, ageing, adapted capacity) match the existing offsets/scales.
- `0x2A53` DC-DC — low voltage u8/10, current u16[8:24]−511, "reserved" u16[24:40]−511
  all match. The source also reports the "reserved" field as ~248 A, consistent
  with this project's finding that it is the complement of the real current
  (kept raw-only here).
- `0x1E33`/`0x1E34`, `0x1E0E`/`0x1E0F`, `0x1E2C`/`0x1E2D` — the 16-bit
  cell/sensor/SoC index at bytes 2–3 is confirmed by the source.
- `0x1E32` four-field energy counters — confirmed by the source's offsets.
- `0x7448` charge-port bits — confirmed (AC bit, DC bit, drive/standby bit).

## Discrepancies the source exposes (do not auto-fix)

These are recorded in `evidence_register.md` and need a known-state capture,
not a unilateral decoder change:

1. `0x2613` inside temperature — registry `u16be/5 − 40` vs OBDb `s16/10`.
2. `0x189D` battery coolant inlet/outlet — registry unsigned `/64` vs OBDb signed `/64`.
3. `0x1E0E`/`0x1E0F` battery temp max/min — registry unsigned `/64` vs OBDb signed `/64`.
4. `0x27C4` windshield temperature — registry byte0×0.1 vs OBDb byte0×0.576−38.8;
   both sources agree byte 4 ×0.5 is humidity.
5. `0x42DB` CO2 — registry whole-u16 + `03FE` sentinel vs OBDb (and Car Scanner)
   byte 1 ×100.
6. `0x028C` display SoC — registry evDash fit `raw×0.4425−6.1947` vs OBDb fit
   `raw×20.4/46−6.4` (≈`raw×0.44348−6.4`); both reproduce ~83.6 % at raw 203.

## Falsifying / next safe validation steps

- For 0x2A25 / 0x2A0E: a single read-only `0x22` capture on the real car would
  confirm the layout and give a first value. Cross-check 0x2A0E against a
  known 12 V load (e.g. with and without HVAC/seat heat) and 0x2A25 against the
  reported SOH. **No session change, no write.**
- For the discrepancies: one known-state read each (e.g. cabin at a known
  ambient for 0x2613; fresh-air vs occupied for 0x42DB). The exact commands
  are the existing `tools/scan_gateway.py` / `tools/validate_measurement_capture.py`
  workflow.
- OBC candidates (0x1507/0x15D6/0x15E2/0x15EE): blocked until the OBC module
  itself becomes reachable; that root cause is outside this pass.
