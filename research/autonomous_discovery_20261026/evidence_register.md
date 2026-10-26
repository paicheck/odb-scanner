# Evidence register — 2026-10-26 autonomous discovery pass

Each significant claim is listed with its source, the specific evidence,
confidence, limitations, contradictory evidence, and a falsifying test. No
private vehicle records are reproduced; the live database is only referenced,
not copied.

## Sources used this pass

| # | Source | URL | Accessed | Licence | Nature |
|---|---|---|---|---|---|
| S1 | OBDb/Volkswagen-ID3 signalset v3 (rendered by Pelican) | https://github.com/OBDb/Volkswagen-ID3 · https://pelican.clutch.engineering/cars/volkswagen/id-3/parameters | 2026-10-26 | CC-BY-SA-4.0 | Third-party RE: 318 parameters with DID/module/offset/length/mul/div/addend/unit/range/example reply and per-model-year confirmation |
| S2 | dalathegreat/Battery-Emulator `MEB-BATTERY.cpp` | https://github.com/dalathegreat/Battery-Emulator | 2026-10-26 | (GPL-family) | Third-party RE of the BMS **CAN broadcast** frames (not UDS DIDs) |
| S3 | TUMFTM/uds-decoder `ids.txt` | https://github.com/TUMFTM/uds-decoder | 2026-10-26 | (see repo) | UDS request-id labels for VW ID.3 |
| S4 | Existing project evidence (passive capture, spot2000, evDash, Car Scanner profile) | docs/investigation/, research/measurement_discovery/ | pre-existing | mixed | Already-verified addressing and prior decoder work |

Only S1/S2/S3 are new this pass. S4 is the pre-existing evidence base the new
candidates are cross-referenced against.

## Primary claims and their evidence

### C1 — DID 0x2A25 is the HV battery internal resistance (raw index)

- **Claim:** `0x2A25` on the gateway (0x710→0x77A) returns a single byte that,
  divided by 5, is a raw battery-internal-resistance index in 0–51.
- **Source:** S1, signal `ID3_BAT_INTERNAL_RES_RAW`, `{"len":8, "max":51, "div":5, "unit":"unknown"}`.
- **Confidence:** STRONGLY INFERRED for the identifier/ECU association; PROVISIONAL
  for the scale (the source labels the unit "unknown", so the physical meaning is
  a relative index, not an absolute resistance).
- **Independent corroboration:** the 0x710→0x77A addressing is VERIFIED by this
  project's 2026-10-15 passive capture (S4). The DID sits in the same 0x2Axx
  family as 0x2A0E, 0x2A53 and the observed-but-undecoded 0x2A26/0x2A28/0x2A2F.
- **Limitations:** no project-owned capture; the source does not state the
  physical unit.
- **Falsifying test:** a captured 0x2A25 value outside 0–51 after /5, or one that
  does not move with a known battery-resistance/SOH change.

### C2 — DID 0x2A0E is the 12 V (start/stop) battery resistance

- **Claim:** `0x2A0E` on the gateway returns a single byte that, ×2/10, is the
  12 V (start/stop) battery internal resistance in mΩ (0–10.2 mΩ).
- **Source:** S1, signal `ID3_SSB_R`, `{"len":8, "max":51, "mul":2, "div":10, "unit":"milliohms"}`.
- **Confidence:** STRONGLY INFERRED for identifier/ECU; PROVISIONAL for scale.
- **Independent corroboration:** 0x710→0x77A addressing VERIFIED (S4); a healthy
  12 V AGM battery is a few mΩ, so 0–10.2 mΩ is physically plausible.
- **Limitations:** the source names the battery "start/stop" without stating it
  is the 12 V auxiliary battery; no project-owned capture.
- **Falsifying test:** a captured value outside 0–10.2 mΩ, or one that does not
  rise as the 12 V battery ages under a known load.

### C3 — DID 0x1512 carries HV-battery energy information (three fields)

- **Claim:** `0x1512` returns energy fields: u16/40 kWh (bits 0–15), u8/20 kWh
  (bits 16–23), u16/20 kWh (bits 24–39).
- **Source:** S1, signals `ID3_HVBAT_ENERGY`, `ID3_NV_ENERGY`, `ID3_HIGH_V_EM_HVBAT`.
- **Confidence:** HYPOTHESIS (single source; overlaps the already-VERIFIED
  0x2AB2/0x2AB8 energy DIDs).
- **Falsifying test:** a capture whose fields contradict the contemporaneous
  0x2AB8/0x2AB2 values.

### C4 — DID 0x2A15 is the energy balance of the last drive cycle

- **Claim:** `0x2A15` returns a signed 16-bit Ah balance of the last drive cycle
  (÷100).
- **Source:** S1, signal `ID3_ENERGY_BALANCE_LAST`, `{"len":16, "sign":true, "div":100, "unit":"ampereHours"}`.
- **Confidence:** HYPOTHESIS.
- **Falsifying test:** a known drive whose Ah balance does not match the
  discharge-counter delta.

### C5 — OBC DIDs (0x1507, 0x15D6, 0x15E2, 0x15EE)

- **Claim:** these report charger rail energy, efficiency, coolant temperature
  and total charging duration on the OBC (0x744→0x7AE).
- **Source:** S1 (each "confirmed MY2021").
- **Confidence:** HYPOTHESIS for the definitions, but **UNREACHABLE** on this car:
  the OBC has answered 0 of 211 requests in the logged history.
- **Falsifying test:** n/a until the OBC module itself becomes reachable.

### C6 — GPS 0x0838 ECall battery charge level/status

- **Claim:** `0x0838` on 0x767→0x7D1 returns ECall battery charge status (u8) and
  level (u8 at bit offset 8).
- **Source:** S1. **Confidence:** HYPOTHESIS. Low diagnostic value.

### C7 — BMS CAN-broadcast measurements exist beyond the UDS set (reference only)

- **Claim:** the BMS broadcasts (on 29-bit CAN frames, not UDS DIDs) isolation
  resistance (kΩ ×5), welded-contactor status, HVIL status, balancing-active,
  max charge/discharge power/current limits, battery-heating state, and cell
  temperatures — see `MEB-BATTERY.cpp` (S2).
- **Relevance:** these are valuable diagnostics (contactor/isolation/balancing)
  but are **not reachable** through the read-only UDS `0x22` allow-list; they
  would require raw CAN-bus frame capture, which is a different transport and a
  different approval. Recorded as a **blocked research lead**, not a candidate.
- **Confidence:** STRONGLY INFERRED as a capability (multiple independent RE
  projects decode the same frames), but OUT OF SCOPE for this collector.

## Discrepancies (existing decoder vs OBDb) — no change made

Each needs a known-state capture to resolve; none is changed unilaterally.

| # | Key/DID | Registry | OBDb (S1) | Notes |
|---|---|---|---|---|
| D1 | `inside_temp` / 0x2613 | `u16be/5 − 40` | `s16/10` | materially different for the same raw; both claimed "inside temperature" |
| D2 | `coolant_temps` / 0x189D | unsigned `u16/64` | signed `u16/64` | sign only matters at implausible negative coolant |
| D3 | `bat_temp_max/min` / 0x1E0E/0x1E0F | unsigned `u16/64` | signed `u16/64` | same class as D2 |
| D4 | `windshield_temperature` / 0x27C4 | byte0 ×0.1 | byte0 ×0.576 −38.8 | humidity byte4 ×0.5 **agreed**; the temperature field disagrees (registry matches observed ~12 °C; OBDb gives ~31 °C for the same 0x7A) |
| D5 | `co2_content_interior` / 0x42DB | whole-u16 + `03FE` sentinel | byte1 ×100 | registry treats `03FE` as not-fitted → None; OBDb/Car Scanner would emit 25,400 ppm |
| D6 | `soc_normal` / 0x028C | `raw×0.4425−6.1947` (evDash) | `raw×20.4/46−6.4` | both reproduce ~83.6 % at raw 203; different fit, same meaning |

## Contradictory / weak evidence to keep in mind

- The OBDb signalset is community-maintained and small (1 star, 1 fork); its
  "confirmed for 2021" means a response was observed once on a 2021 ID.3, not
  that a VW document supports the layout.
- `0x27C4` now has **three** competing temperature interpretations (Car Scanner
  u16×15.625−39.5 → refuted; OBDb ×0.576−38.8; this project's byte0×0.1). The
  project's interpretation is the only one that matches the observed value, but
  it is not yet the cold-soak/defroster-proven label.
- `0x2A53` "reserved current" is reported as a real field by OBDb (~248 A), but
  this project's byte-level finding is that bytes 3–4 are the *complement* of
  the real current at bytes 1–2 (sum ≈ 0x0508). Both can be true only if the
  source's field is the same complement bytes, mislabelled. This is why the key
  stays raw-only here.

## Falsifying tests (next safe steps)

All are read-only UDS `0x22`, no session change, no write:

1. Capture 0x2A25 and 0x2A0E once; confirm a value in range and that 0x2A0E
   moves under a known 12 V load.
2. Capture 0x2613 with the cabin at a known ambient (thermometer) to pick
   between `u16/5−40` and `s16/10`.
3. Capture 0x189D/0x1E0E/0x1E0F in a cold-soak state to decide signed vs unsigned.
4. Capture 0x42DB in fresh-air then occupied cabin to confirm the whole-u16 +
   `03FE` sentinel reading.
5. Capture 0x1512 alongside 0x2AB8 to check the energy cross-reference.
