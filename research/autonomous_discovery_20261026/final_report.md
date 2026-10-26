# Final report — autonomous MEB measurement discovery (2026-10-26)

**Vehicle:** 2021 VW ID.3 58 kWh (MEB). **Scope:** offline discovery of
additional diagnostic measurements. **No vehicle communication occurred.**

---

## A. Executive summary

**What was investigated.** The project's measurement registry (287 MEB specs /
253 DIDs), catalogue and quality layer were re-inventoried, then cross-referenced
against a new external source: the `OBDb/Volkswagen-ID3` signalset v3
(CC-BY-SA-4.0), a structured 318-parameter definition with per-model-year
confirmation status. Local evidence (passive capture, spot2000, evDash, Car
Scanner profile, decoder audit) was re-checked, and the two highest-value new
identifiers were implemented, tested, and exported to the shared spec.

**Most valuable discoveries.**

1. **DID `0x2A25` — HV battery internal resistance (raw index, u8/5).** On the
   already-verified gateway (0x710→0x77A), confirmed for MY2021. Directly serves
   the HV battery ageing/SoH investigation. **Implemented.**
2. **DID `0x2A0E` — 12 V (start/stop) battery resistance (u8×2/10 mΩ).** Same
   verified module, confirmed MY2020-2021. Directly serves the low-voltage /
   12 V investigation. **Implemented.**
3. A **six-point discrepancy register** between existing decoders and the new
   source (0x2613 inside temp, 0x189D/0x1E0E/0x1E0F signedness, 0x27C4 windshield,
   0x42DB CO2, 0x028C display-SoC fit). None were changed unilaterally — they are
   documented for known-state validation.
4. A **blocked research lead**: BMS CAN-broadcast frames (isolation resistance,
   contactor/HVIL status, balancing) exist but need raw CAN capture, not the
   read-only UDS `0x22` path — out of scope.

**What changed in the repository.** Two new PROVISIONAL gateway DIDs (0x2A25,
0x2A0E) added to `decoders/meb.py` and `decoders/catalogue.py`, a 14-test
regression module `tests/test_gateway_resistance.py`, and the shared spec
regenerated (`spec/odb_scanner.json` + Android asset). No other decoder was
touched. Research artefacts are under
`research/autonomous_discovery_20261026/`.

**Tests.** Targeted suites pass (196 tests across catalogue/spec/discovery/
gateway/decoder-vectors). Full suite run separately (see §E).

**Most important remaining blockers.** All further validation is car-gated:
a real known-state capture of 0x2A25/0x2A0E, the six discrepancy tests, and the
OBC module (0x744) which has never answered and blocks the charger DIDs.

**Safety.** No vehicle connection, no diagnostic request, no write, no session
change, no actuator/routine/security-access, no fault clearing. The read-only
guard is unchanged.

---

## B. Coverage analysis

### Baseline and final counts

| Item | Baseline | Final | Note |
|---|---|---|---|
| MEB registry specs | 287 | **289** | +2 gateway resistance DIDs |
| distinct DIDs | 253 | **255** | +0x2A25, +0x2A0E |
| catalogue records | 287 | **289** | derived from registry |
| quality records | 296 | **298** | +2 PROVISIONAL |
| excluded (INVALID) | 5 | 5 | unchanged |
| Python tests | 777 | **792 passed** | +14 this pass, +1 concurrent pass |

Reported baseline figures from the brief were reconciled: 287/253/296 were
confirmed; "318 distinct measurement keys" was **not reproducible** from the
registry/spec (287 measurement specs) and is traced to the Pelican/OBDb "318
parameters" count for the ID.3, not to this project's key count.

### New candidates discovered

16 candidate DIDs/fields were recorded in `candidate_inventory.md`, of which
**2 were implemented** (0x2A25, 0x2A0E), 6 are conditional/backlog, and 8 are
low-value or blocked (OBC 0x1507/0x15D6/0x15E2/0x15EE, steering, comfort).

### Existing measurements clarified

- `0x2AB2`/`0x2AB8`, `0x2AF7` (all ten 12 V fields), `0x2A53`, the cell/sensor/
  SoC index DIDs, `0x1E32` and `0x7448` were independently **corroborated** by
  the new source — strengthening their PROVISIONAL status without change.
- `0x2A53` "reserved current" is reported by the source as a real ~248 A field,
  consistent with this project's byte-level finding that it is the *complement*
  of the real current (kept raw-only here).

### Duplicates / conflicting definitions found

- `0x27C4` has three competing temperature interpretations (Car Scanner
  u16×15.625−39.5 → refuted; OBDb ×0.576−38.8; this project byte0×0.1).
- `0x2613` has two materially different formulas (registry vs OBDb).
- Six discrepancy entries are recorded in `evidence_register.md` (D1–D6).

### Categories with good vs weak coverage

- **Good:** BMS SoC/voltage/current, cell voltages, battery temps, energy
  counters, gateway energy, 12 V battery block, DC-DC, electric-drive float32
  family, climate basics.
- **Weak / gapped:** battery internal resistance (was absent — now added),
  contactor/isolation/balancing (CAN-only), OBC/charger (unreachable), GPS
  (only satellite counts), ABS (requires 0x10 03).

---

## C. Prioritised candidate table

The full ranked table is in `candidate_inventory.md`. Leading candidates:

| Name / subsystem | ECU & DID | Type & units | Evidence | Confidence | Diagnostic value | Validation needed | Blocker | Next action |
|---|---|---|---|---|---|---|---|---|
| HV battery internal resistance (raw) | energy 0x2A25 | u8/5, raw index | OBDb confirmed MY2021 + verified addressing | STRONGLY INFERRED (scale PROVISIONAL) | High (HV ageing/SoH) | known-state capture | none (read-only) | implement (done) + capture |
| 12 V battery resistance | energy 0x2A0E | u8×2/10, mΩ | OBDb confirmed MY2020-21 + verified addressing | STRONGLY INFERRED (scale PROVISIONAL) | High (12 V health) | known-load capture | none (read-only) | implement (done) + capture |
| HV battery energy info ×3 | energy 0x1512 | u16/40, u8/20, u16/20 kWh | OBDb | HYPOTHESIS | Med (overlaps 0x2AB8) | capture | none | backlog |
| Energy balance last cycle | energy 0x2A15 | s16/100 Ah | OBDb | HYPOTHESIS | Med | known drive | none | backlog |
| ECall battery level/status | gps 0x0838 | u8, u8 | OBDb | HYPOTHESIS | Low | capture | none | backlog |
| Charger rail/efficiency/temp/duration | chg 0x1507/0x15D6/0x15E2/0x15EE | mixed | OBDb | HYPOTHESIS | Med | — | **OBC unreachable** | blocked |
| Isolation/contactor/balancing | BMS CAN frames | n/a | Battery-Emulator RE | STRONGLY INFERRED capability | High | — | **needs raw CAN** | blocked (different transport) |

### Derived-metric assessment (Phase 7)

Existing derived metrics (already in the collector): `pack_power_kw`,
`cell_delta_mv`, `cell_soc_spread_pct`, `battery_temp_spread_c`, `soh_pct`,
`aux_12v_power_w`, `dcdc_power_kw`, `dcdc_gw_power_kw`.

Additional opportunities assessed this pass:

- **Battery internal-resistance estimate from ΔV/ΔI.** A real mΩ estimate could
  be derived from coincident `pack_voltage` and `pack_current` during a load
  step, and cross-validated against the new 0x2A25 index and 0x2A0E (12 V
  resistance). **Assessed, not implemented**: it requires careful timestamp
  alignment and a known load step, and the 0x2A25 scale is still PROVISIONAL —
  the derived metric must not be built on an unconfirmed input.
- **Energy change over a period** (Δ from the 0x1E32 lifetime counters or
  0x2AB8). **Assessed, not implemented**: valid only across a real
  charge/discharge boundary, not from carried-forward samples.
- **Charging efficiency** (AC input vs DC output). **Blocked**: the OBC (0x744)
  is unreachable, so input energy cannot be read.

All are left as documented opportunities; none was added, because each depends
on either a real capture or a module that does not answer.

---

## D. Implementation summary

New functionality / definitions:

- `decoders/meb.py` — two new `DIDSpec`s (`bat_internal_resistance_raw` 0x2A25,
  `aux_battery_resistance_mohm` 0x2A0E) on the `energy` ECU, `slow=True`,
  `doc_status=EXPERIMENTAL`, decoded via `_cs_field_decoder` (u8/5 and u8×2/10).
- `decoders/catalogue.py` — two `OVERLAY` entries: PROVISIONAL confidence,
  `THIRD_PARTY_DEFINITION` evidence, `address_confidence=VERIFIED` (the 0x710→
  0x77A addressing is confirmed), explicit `ByteLayout`, plausible ranges
  (0–51 raw index and 0–10.2 mΩ), and `falsified_by`/`limitations`.

Generated (not hand-edited): `spec/odb_scanner.json` and the Android asset via
`tools/export_spec.py --sync-android`.

Test-only: `tests/test_gateway_resistance.py` (14 tests: registry, decoder
arithmetic, short payload, catalogue, quality, spec-export, guard).

Documentation-only: `research/autonomous_discovery_20261026/{progress,candidate_inventory,evidence_register,final_report}.md`.

No existing decoder was modified. The read-only guard and Android `Decode.kt`
were not changed by this pass.

---

## E. Test results

Commands executed and outcomes:

| Command | Result |
|---|---|
| `python -m pytest tests -q` (baseline, before changes) | **777 passed** (144 s) |
| `python -m pytest tests/test_gateway_resistance.py -q` | **14 passed** |
| `python -m pytest tests/test_catalogue.py tests/test_spec_export_in_sync.py tests/test_measurement_discovery_followup.py tests/test_decoder_vectors.py tests/test_meb_path.py tests/test_gateway_energy.py tests/test_gateway_resistance.py tests/test_payload_research.py -q` | **196 passed** |
| `python -m ruff check decoders/meb.py decoders/catalogue.py tests/test_gateway_resistance.py` | clean |
| `python -m pytest tests -q` (full suite, post-change) | **792 passed** (187 s) |

The 777→792 growth is 14 tests from this pass
(`tests/test_gateway_resistance.py`) plus 1 from the concurrent decoder-accuracy
pass (`tests/test_catalogue.py`), so the two passes coexist without breaking
the suite.

Note on lint: a concurrent autonomous pass created
`docs/investigation/decoder_accuracy_audit_20261026/` audit scripts
(`dbpeek.py`, `inventory.py`) that fail `ruff check .`. Those are not this
pass's files and were left untouched; this pass's own files lint clean.

---

## F. Outstanding human approvals

The following need a real car or specialist input — **not executed here**:

1. **Known-state capture of 0x2A25 / 0x2A0E** — a single read-only `0x22` read
   on 0x710→0x77A to confirm the layout and record a first value (0x2A0E under
   a known 12 V load; 0x2A25 vs reported SOH).
2. **Discrepancy tests D1–D6** — one known-state read each (0x2613 at known
   ambient; 0x189D/0x1E0E/0x1E0F cold-soak; 0x42DB fresh-air vs occupied;
   0x27C4 cold-soak/defroster; 0x028C cross-check).
3. **OBC reachability** — the charger DIDs are blocked until the 0x744 module
   is reachable (its root cause is separate from this pass).
4. **BMS CAN-broadcast measurements** (isolation, contactor/HVIL, balancing) —
   require raw CAN capture, a different transport and explicit approval.
5. **ABS wheel speeds 0x1800–0x1803** — already prepared, still require the
   separate `0x10 03` approval (unchanged by this pass).

---

## G. Next-run instructions (highest value first)

1. **Capture 0x2A25 and 0x2A0E.** Use the existing read-only workflow
   (`tools/scan_gateway.py` or a one-off `0x22` read on 0x710→0x77A). Confirms
   both decoders and records the first values. Decision point: promote 0x2A0E
   to VERIFIED if it moves under a known 12 V load and matches an independent
   12 V battery tester.
2. **Resolve the six discrepancies** with known-state reads. Priority: 0x2613
   (inside temp) and 0x42DB (CO2), because those currently feed (or are
   excluded from) the climate signal set.
3. **Reassess 0x1512 vs 0x2AB8.** One capture of 0x1512 alongside the already
   VERIFIED 0x2AB8/0x2AB2 decides whether it is a useful redundant energy
   cross-check or redundant noise; promote only if it adds information.

Each requires the owner's explicit approval and a reviewed read-only DID list;
no session change, no write.

---

## Final operating note

This pass prioritised evidence and diagnostic value over DID count: exactly two
well-supported identifiers were implemented, sixteen were catalogued, six
discrepancies were documented, and one out-of-scope capability was recorded as
blocked. The car remained disconnected throughout.
