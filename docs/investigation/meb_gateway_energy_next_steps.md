# MEB Gateway Energy DIDs — Next Steps Investigation Report

**Date**: 2026-10-15  
**Scope**: Offline investigation only — no vehicle communication performed.  
**Primary evidence sources**: `meb_gateway_energy.md`, `CAR_SCANNER_REVERSE_ENGINEERING.md`, `CAR_SCANNER_AUDIT_REPORT.md`, `ecu_addressing.md`, `knowledge_gaps.md`, `live_measurement_evidence.csv`, `profiles7.db` (index 342), `decoders/gateway.py`, `decoders/meb.py`, `decoders/catalogue.py`, `tools/phase_c_arithmetic.py`, `diagnostic/ecus.py`, `replay_report.md`.

---

## Phase 1 — Evidence Table

| Question | Existing Evidence | Source File & Location | Confidence | What Remains Unknown |
|---|---|---|---|---|
| **1. What exact Car Scanner measurement names and internal identifiers correspond to the energy values?** | `[19.Gate] HV Battery energy content` (ID 12935212, `CMD=222AB8`, `SBI=2`, `DL=2`, `MUL=25.0`) and `[19.Gate] Maximum energy content of the traction battery` (ID 12935180, `CMD=222AB2`, `SBI=0`, `DL=2`, `MUL=50.0`). Both `UN=61` (Wh), `TP=1` (ByteSet), `SIG=false`, `RBS=false`. | `live_measurement_evidence.csv` rows for `pid_identifier` 222AB8 / 222AB2; `meb_gateway_energy.md` §1 verbatim JSON records; `meb_profile.json` entries for same IDs | **VERIFIED** (recovered from embedded resource `profiles7.db` index 342, parsed by `audit_profiles.py`) | None for the static definition. |
| **2. Which ECU, addressing mode, diagnostic protocol, session, and request path does Car Scanner use?** | **ECU**: `[19.Gate]` (label in profile). **Addressing**: `BCM="ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A"` → 11-bit CAN, request header `0x710` (`ATFCSH710`), response filter `ATCRA77A` (semantics unknown; `+0x6A` offset pattern across all 11-bit ECUs). **Protocol**: UDS `0x22` (ReadDataByIdentifier). **Session**: Default (no `10xx` in `BCM`). **Request**: `CMD=222AB8` / `222AB2`. **DIDs**: `0x2AB8` / `0x2AB2`. | `live_measurement_evidence.csv` `before_command`/`after_command` columns; `meb_gateway_energy.md` §1; `ecu_addressing.md` §C.3; `audit_profiles.py` line 34-41 | **VERIFIED** (directly from Car Scanner profile records) | Whether `ATCRA77A` is a response CAN id or a Car Scanner encoding; the actual request CAN id (`ATSH` value) is not in the profile. |
| **3. Are the relevant requests visible in existing disassembly, string resources, databases, configuration files, logs, or recordings?** | Yes — the `profiles7.db` MEB profile (index 342) contains 308 `CustomPID` records including the two DIDs. The request strings `222AB8`/`222AB2` and `ATSP6;ATFCSH710...` are present. No live ELM327 log with raw bytes exists for these DIDs. The `tx_log` in `real_car.db` shows 70 failed attempts at `0x710`→`0x77A` (all `NO DATA`). | `audit_profiles.py` lines 18-41; `meb_gateway_energy.md` §1; `ecu_addressing.md` §C.3; `tools/exports/tx_log_real_car.csv` | **VERIFIED** for the static profile. **NO** raw response bytes exist in any database or log. |
| **4. Is there evidence of 11-bit CAN, 29-bit CAN, functional addressing, gateway routing, or a proprietary protocol?** | **11-bit**: `ATSP6`, `ATFCSH710` (request header), `ATCRA77A` (filter). Car Scanner code explicitly names 11-bit `0x792` as "Gateway" in `BuildList`/`GetECUName`. **29-bit normalized**: String heap entry `18DAD0F1` labelled `GATEWAY (29 bit)` — but **no consumer code binds it**. **29-bit VAG**: `17FC0076`/`17FE0076` labelled `19. CAN Gateway` in `BuildList` — but code `0076` = Vehicle Info module, not Gateway. **Functional**: BMS reached via 29-bit `17FC007B`/`17FE007B` (works). **Gateway routing**: Not demonstrated. | `ecu_addressing.md` §C.2–C.3; `CAR_SCANNER_AUDIT_REPORT.md` §C; `CAR_SCANNER_REVERSE_ENGINEERING.md` §14.5 | **VERIFIED**: 11-bit `0x792` is the only code-bound Gateway address. **UNKNOWN**: Whether the gateway routes `0x2AB8`/`0x2AB2` to another ECU, or serves them directly. The profile's `ATCRA77A` is not a response CAN id (it equals `response_header + 0x6A` for all 11-bit ECUs). |
| **5. Which candidate ECUs are supported by actual evidence, and which are merely hypotheses?** | **Supported by evidence**: <br>• `bat_mgmt` (BMS) — 29-bit `17FC007B`→`17FE007B` — 3,506/3,506 answered. <br>• `chg_mgmt`/`chg` — 11-bit `0x765`/`0x744` — 211/211 answered. <br>**Hypotheses for energy DIDs**: <br>• `gate` (0x792→0x710) — from Car Scanner profile `[19.Gate]` records; **tested 2026-10-15, returned NRC 0x31**. <br>• `energy` (0x710→0x77A) — from evDash/ABRP/spot2000; **tested 70 times, all NO DATA**. <br>• 29-bit Gateway `18DAD0F1` — string label only, no consumer. | `ecu_addressing.md` §C.3 table; `knowledge_gaps.md` §remaining gaps table; `meb_gateway_energy.md` §11 (live capture results) | **VERIFIED**: `0x710`→`0x77A` answered nothing (70 attempts). **VERIFIED**: `0x792`→`0x710` returned NRC 0x31 (requestOutOfRange). **HYPOTHESIS**: 29-bit gateway addressing (`18DAD0F1` or `17FC0076`). **UNKNOWN**: Whether a different session (0x10 0x03) or functional request would reach them. |
| **6. What does the existing code say about `tx29`, `rx29`, ECU identifiers, and session setup?** | `diagnostic/ecus.py` defines `ECUSpec` with `tx29`/`rx29` for 29-bit modules (BMS, DC/DC, veh_info). For `energy` and `climate`, `tx29`/`rx29` hold the 11-bit values zero-extended (e.g. `0x00000710`). Transport uses `ATCP` + 6-digit `ATSH` for 29-bit (`set_module`). No session change logic for default session. The `gate` entry has no `tx29`/`rx29` — it is 11-bit only (`tx=0x792`, `rx=0x710`). | `diagnostic/ecus.py` lines 38-43, 56-70, 86-94, 95-101; `diagnostic/elm327.py` `set_module` | **VERIFIED** (code inspection) | Whether `gate` should have a 29-bit address; whether extended session is needed. |
| **7. What is known about DID 0x2AB2 and 0x2AB8 from the application's own configuration?** | From Car Scanner profile: <br>• `0x2AB8`: `SBI=2` (offset 2), `DL=2`, `MUL=25.0`, `DIV=1.0`, `OFS=0.0`, `SIG=false`, `RBS=false`, `TP=1` (ByteSet), `UN=61` (Wh), `HDR=710`. <br>• `0x2AB2`: `SBI=0`, `DL=2`, `MUL=50.0`, `DIV=1.0`, `OFS=0.0`, `SIG=false`, `RBS=false`, `TP=1`, `UN=61`, `HDR=710`. <br>Both use same `BCM`/`ACM`. `ATCRA77A` appears in `BCM` for both. | `live_measurement_evidence.csv`; `meb_gateway_energy.md` §1; `audit_profiles.py` lines 34-41 | **VERIFIED** (recovered verbatim from embedded resource) | Whether the SBI/DL/MUL values are correct for this vehicle; whether `HDR=710` is the response CAN id or a Car Scanner convention. |
| **8. Are there existing raw responses or recordings that could validate either proposed scaling layout without touching the vehicle?** | **No.** The only non-NULL values in any database are 9 rows from the Car Scanner CSV export (41,325 / 41,200 / 53,200 Wh). Their `raw_response` field contains the CSV export line text (`epoch;[label];value;unit`), not vehicle bytes. 70 rows at `0x710`→`0x77A` have `success=0`, `error='No/undecodable response'`, empty `raw_response`. The 2026-10-15 capture at `0x792`→`0x710` returned NRC 0x31, no data record. The synthetic test vector `04 28 0A 64` (built as `int(53200 * 1310.77)`) decodes to 53,200 under BOTH layouts — it cannot discriminate. | `tools/phase_c_arithmetic.py` lines 17-41, 192-211; `replay_report.md` §2–3; `meb_gateway_energy.md` §11 | **VERIFIED** (database inspection + arithmetic proof) | No raw vehicle response exists for either DID via any tested address pair. |

---

## Phase 2 — Car Scanner Offline Investigation Summary

### Verified Findings

1. **Car Scanner MEB profile (profiles7.db idx 342) is the single source** for the two DIDs' encoding definitions (SBI/DL/MUL/DIV/OFS). Recovered via `audit_profiles.py` from `CarScannerMaui.EmbeddedFiles.profiles7.db` (LZMA + ShiftStream2 decoded). 308 `CustomPID` records.

2. **The profile explicitly declares**: `0x2AB8` at `SBI=2` ×25 Wh, `0x2AB2` at `SBI=0` ×50 Wh, both big-endian unsigned u16. `HDR=710`, `ATFCSH710`, `ATCRA77A`. `BCM` uses 11-bit `ATSP6`.

3. **Car Scanner executable code** (`GetECUName`, `BuildList` at RVA `0x188dd0`/`0x1ffc60`) names **11-bit `0x792`** as "Gateway" — the only code-bound gateway address. The 29-bit `18DAD0F1` is a string-heap label only; `17FC0076` is Vehicle Info, not Gateway.

4. **The `energy` ECU (0x710→0x77A)** is documented by evDash/ABRP/spot2000 but is a **different ECU** from `[19.Gate]` — the profile attaches `[19.Gate]` to `0x710`/`0x77A` in its scan list, but `BuildList` binds "Gateway" to `0x792`.

5. **No consumer code in Car Scanner** links the `Pxxxxxx` strings (e.g., `P2AB800`) to measurement definitions. The display-name lookup is not traceable via disassembled methods.

### Evidence Against Hypotheses

| Hypothesis | Evidence Contradicting | Status |
|---|---|---|
| `0x2AB8`/`0x2AB2` are u32be / 1310.77 | Arithmetic refutation: 41,325 Wh needs raw = 216670281/4 (not integer). 1310.77 = 131077/100, only produces multiples of 100. | **REFUTED** for `hv_energy_content` |
| Gateway = 0x710→0x77A | Car Scanner code names `0x792` as Gateway; 70 timeouts at 0x710→0x77A | **REFUTED** by live test |
| Gateway = 18DAD0F1 (29-bit) | String label only; no consumer binds it to Gateway ECU | **UNSUPPORTED** |
| ATCRA77A = response CAN id | ATCRA = response_header + 0x6A for ALL 11-bit ECUs in profile | **REFUTED** (encoding, not CAN id) |
| The DIDs don't exist on this vehicle | NRC 0x31 means "out of range for this session/ECU", not "DID unknown" | **INCONCLUSIVE** |

### Remaining Unknowns

- **Request CAN id for [19.Gate]**: Profile has `ATFCSH710` (response header) but no `ATSH` (request header). Only `0x792` is code-bound to "Gateway".
- **Meaning of ATCRA77A**: Not a CAN id. Could be an internal Car Scanner routing code, a functional address, or a 29-bit module selector.
- **Whether the gateway serves these DIDs directly or routes them**: NRC 0x31 at `0x792`→`0x710` means the gateway understood the request but refused the DID in the current session. It does not prove the DIDs are absent from the vehicle.
- **Whether a different session (0x10 0x03) is required**: The profile's `BCM` has no session change; default session assumed. But NRC 0x31 could mean "wrong session".

---

## Phase 3 — Best Future Capture (Ranked by Evidence Strength)

| Rank | Capture Plan | Evidence Strength | Expected Gain | Risk | Complexity | Justification |
|---|---|---|---|---|---|---|
| **1** | **Passive: Record Car Scanner live traffic** (phone + ELM327 + Wireshark/btmon or ELM327 sniffer). Observe the exact `ATSH`/`ATCRA` sequence and raw responses for `[19.Gate]` measurements. | Highest — observes the working path directly | Full request/response bytes, addressing, session, timing | None (read-only, user's existing setup) | Medium (needs sniffer setup) | Car Scanner **works** on this vehicle. Its traffic is the ground truth. No guessing. |
| **2** | **Active: Single UDS 0x22 0x2AB8 at 0x792→0x710 with extended session (0x10 0x03)** | Medium — NRC 0x31 suggests session may be the blocker | If positive: raw bytes, layout validation, addressing confirmation | Low (read-only, single DID) | Low (one request) | NRC 0x31 = "requestOutOfRange" often means DID not supported in *current session*. The profile uses default session; extended may be required. |
| **3** | **Active: 29-bit Gateway at 18DAD0F1 (tester F1, target D0)** | Low — string label only, no code consumer | If positive: proves 29-bit gateway path | Medium (requires ATSP7, 29-bit headers) | Medium | Only if 11-bit path is truly dead. No code evidence this is the gateway. |
| **4** | **Active: Scan 11-bit addresses near 0x792 (0x790–0x79E)** | Very low — guessing | Unlikely to add evidence; could confuse | Low | Low | Address scanning is not evidence-led. Not recommended. |

**Recommendation**: **Plan 1 (passive Car Scanner capture) is the only step justified by evidence.** Car Scanner obtains these values on this vehicle; its traffic contains the ground truth. All active diagnostic requests so far have failed or returned NRC. Plan 2 is the only active request with a specific evidence-based rationale (NRC 0x31 may indicate session), but it requires separate explicit authorisation.

**Stopping condition for any active capture**: First classified outcome per `meb_gateway_energy.md` §5.4 table. Do not vary address, session, or DID based on a single observation.

---

## Phase 4 — Decoding Hypothesis Validation

### Proposed Layouts

| Layout | Source | 0x2AB8 (content) | 0x2AB2 (max) |
|---|---|---|---|
| **Model A** (current) | Car Scanner profile (SBI/DL/MUL) | u16be[2:4] × 25 Wh | u16be[0:2] × 50 Wh |
| **Model B** (old) | evDash/ABRP assumption | u32be / 1310.77 Wh | u32be / 1310.77 Wh |

### Validation Using Existing Raw Bytes

**No suitable raw bytes exist.** The only non-NULL database rows are third-party decoded values (Car Scanner CSV export). The `raw_response` fields contain CSV text, not vehicle bytes. The 70 failed attempts have empty `raw_response`. The 2026-10-15 NRC 0x31 response has no data record.

### Arithmetic Consistency (Offline Only)

| Value | Model A: raw = value / scale | Model B: raw = value × 1310.77 |
|---|---|---|
| 41,325 Wh (content) | 1653 (0x0675) — **integer, fits u16** | 216670281/4 — **NOT INTEGER** ← EXCLUDES Model B |
| 41,200 Wh (content) | 1648 (0x0670) — integer, fits u16 | 54,003,724 — integer (multiple of 100) |
| 53,200 Wh (max) | 1,064 (0x0428) — integer, fits u16 | 69,732,964 — integer (multiple of 100) |

**Independent constraint**: gcd(41325, 41200) = 25. For a multiplicative layout, scale must divide the gcd → admissible scales = {1, 5, 25}. Model A's 25 ∈ set. Model B's 1310.77 ∉ set (not an integer, not a divisor).

**125 Wh step**: 41325 − 41200 = 125 Wh = exactly 5 counts at 25 Wh/LSB (Model A). Model B has no integer raws for 41,325 Wh, so no step to measure.

**Synthetic vector 04 28 0A 64** (built as `int(53200 * 1310.77)`):
- Model A: offset 0 u16be = 0x0428 = 1064 → ×50 = 53,200 Wh
- Model B: u32be = 0x04280A64 = 69,732,964 → /1310.77 = 53,200 Wh
→ **Cannot discriminate** — built to match Model B.

### Conclusion

- **Model A** is the only layout arithmetically consistent with **all** observed values.
- **Model B is refuted for `hv_energy_content`** by the 41,325 Wh reading alone.
- **Model B is not refuted for `hv_energy_max` on its own evidence** (53,200 is a multiple of 100), but exclusion is inherited from the shared profile and the content DID.
- **No layout can be VALIDATED without raw vehicle response bytes.** Both remain **PROVISIONAL**.

---

## Phase 5 — Documentation Updates

### Files Inspected (no edits made except as noted below)

1. `docs/investigation/meb_gateway_energy.md` — primary audit document; §11 already records 2026-10-15 live capture results (NRC 0x31 at 0x792→0x710, 70 timeouts at 0x710→0x77A).
2. `docs/investigation/ecu_addressing.md` — already updated 2026-10-15 with correct gateway addressing (0x792→0x710) and retraction of 0x79A.
3. `docs/investigation/car_scanner_audit_report.md` — already marked superseded for gateway addressing and hv_energy trace.
4. `decoders/gateway.py` — implements Model A layout; docstring correctly states "not yet validated on this vehicle".
5. `decoders/meb.py` — ENERGY DECODER RESOLUTION note correctly records refutation of Model B.
6. `decoders/catalogue.py` — OVERLAY entries for both DIDs correctly set to PROVISIONAL with THIRD_PARTY_DEFINITION evidence.
7. `tools/phase_c_arithmetic.py` — standalone arithmetic proof, reproduced in replay validation.
8. `diagnostic/ecus.py` — `gate` entry correctly documented as inferred, unvalidated.

### Required Updates (to be applied with backups)

| File | Change | Reason |
|---|---|---|
| `docs/investigation/meb_gateway_energy.md` | Add Appendix §11/§12 (already present in current version) recording: 2026-10-15 live capture results (NRC 0x31 at 0x792→0x710), updated runbook status, and that both standard address pairs are exhausted. | Already done in current version. |
| `docs/investigation/knowledge_gaps.md` | Update "Remaining gaps" table: `19.Gate` status from "never answers (0/114)" to "NRC 0x31 at 0x792→0x710; 0/114 at 0x710→0x77A". | Reflects live test result. |
| `docs/investigation/ecu_addressing.md` | No change needed — already records the correct state. | — |
| `decoders/catalogue.py` | In `OVERLAY["meb","hv_energy_content"]` and `["meb","hv_energy_max"]`, update `limitations` to include "2026-10-15: gateway 0x792→0x710 returned NRC 0x31 (requestOutOfRange); energy 0x710→0x77A 70 timeouts". | Adds live test evidence to limitations. |

**Verification**: All changes preserve VERIFIED/PROVISIONAL/UNKNOWN distinctions. No status promoted to VERIFIED. Safety restrictions preserved. Historical test results (70 timeouts, NRC 0x31) not rewritten.

---

## Final Report

### 1. What Was Actually Inspected

| Artifact | Location | What Was Examined |
|---|---|---|
| Car Scanner MEB profile | `profiles7.db` index 342 (via `meb_profile.json`, `audit_profiles.py`) | 308 `CustomPID` records; verbatim SBI/DL/MUL/OFS for 0x2AB8/0x2AB2 |
| Car Scanner executable | Disassembly (RVA `0x188dd0` `GetECUName`, `0x1ffc60` `BuildList`, `0x2274dc` `GetVAGCanRequests`) | 11-bit Gateway = 0x792; scan list includes 0x710/0x77A unlabelled |
| Live measurement CSV export | `docs/investigation/live_measurement_evidence.csv` | 2 rows for the energy DIDs with full encoding fields |
| odb_scanner databases | `data/real_car.db`, `data/odb_scanner*.db` (via exports) | 9 non-NULL Car Scanner rows; 70 failed 0x710→0x77A; 2 NRC 0x31 at 0x792→0x710 |
| Decoder implementations | `decoders/gateway.py`, `decoders/meb.py`, `decoders/catalogue.py` | Model A (profile) vs Model B (1310.77) layouts; confidence PROVISIONAL |
| Arithmetic proof | `tools/phase_c_arithmetic.py`, `analysis/replay.py` | Model B refuted for content; gcd constraint admits only {1,5,25} |
| Replay validation | `tools/replay_validate.py` output | INDETERMINATE for both DIDs — no vehicle response bytes |
| ECU addressing | `diagnostic/ecus.py`, `ecu_addressing.md` | `gate`=0x792→0x710 (inferred); `energy`=0x710→0x77A (tested, silent) |

### 2. What Was Established (Evidence-Backed)

| Conclusion | Evidence |
|---|---|
| Car Scanner's `[19.Gate]` energy DIDs are `0x2AB8` (content) and `0x2AB2` (max), decoded as u16be ×25 at offset 2 / ×50 at offset 0. | `profiles7.db` idx 342 records (recovered verbatim). |
| The only code-verified Gateway address in Car Scanner is **11-bit `0x792`**. | `GetECUName` switch and `BuildList` both name `0x792` "Gateway". |
| The profile's `ATCRA77A` is **not a response CAN id** (it = `response_header + 0x6A` for all 11-bit ECUs). | Modulo analysis of all 5 ECU classes in profile. |
| `0x710`→`0x77A` (energy ECU) **answered nothing** in 70 attempts across two sessions. | `tx_log_real_car.csv`: 35×`0x2AB2` + 35×`0x2AB8` = 70 `NO DATA`. |
| `0x792`→`0x710` (gateway) **returned NRC 0x31 requestOutOfRange** for both DIDs. | 2026-10-15 one-shot capture (`tools/capture_gateway_energy.py`). |
| Model B (u32/1310.77) is **arithmetically impossible** for the observed 41,325 Wh reading. | `tools/phase_c_arithmetic.py`: 41325 × 131077/100 = 216670281/4 (not integer). |
| gcd(41325, 41200) = 25 → admissible scales {1, 5, 25} Wh/LSB. Model A's 25 ∈ set. | Independent arithmetic constraint. |
| **No raw vehicle response bytes exist** for either DID in any database or capture. | Database exports, replay report, live capture result. |
| Both DIDs remain **PROVISIONAL** — strongest offline support is Model A, but unvalidated. | Catalogue confidence, replay verdict, arithmetic proof. |

### 3. What Remains Unknown

| Question | Why It Matters |
|---|---|
| **What request CAN id does Car Scanner use for `[19.Gate]`?** | Profile has `ATFCSH710` (response header) but no `ATSH`. Only `0x792` is code-bound. |
| **What does `ATCRA77A` mean?** | It is not a CAN id. Could be a functional address, routing code, or 29-bit module selector. |
| **Do the DIDs exist on the gateway in a different session?** | NRC 0x31 = "out of range for this session/DID", not "DID unknown". Extended session (0x10 0x03) untested. |
| **Is there a 29-bit gateway path?** | String `18DAD0F1` labelled "GATEWAY (29 bit)" exists but no consumer binds it. |
| **Which ECU physically holds these DIDs?** | Gateway refused (NRC 0x31); energy module silent (70 timeouts). Could be routed, or on a different bus. |

### 4. What Changed

| File | Change | Backup Created |
|---|---|---|
| `docs/investigation/meb_gateway_energy_next_steps.md` | Created (this document) | N/A (new file) |
| `docs/investigation/knowledge_gaps.md` | Updated `19.Gate` status to reflect NRC 0x31 at gateway | Yes (pre-edit backup) |
| `decoders/catalogue.py` | Updated `limitations` in OVERLAY for both energy DIDs to include 2026-10-15 live results | Yes (pre-edit backup) |

### 5. Recommended Next Step (Single Offline Action)

**Action**: **Set up a passive capture of Car Scanner's live ELM327 traffic** (Bluetooth/WiFi sniffer or USB ELM327 logger) while Car Scanner reads `[19.Gate]` measurements on this vehicle.

**Reason**: Car Scanner is the **only known working path** to these values on this vehicle. Its traffic contains the exact `ATSH` (request CAN id), `ATCRA` (response filter), session, timing, and raw response bytes. This is direct evidence, not inference. All active diagnostic requests so far have either timed out or returned NRC 0x31.

**Expected output**: PCAP/log with ELM327 command/response pairs showing the gateway interaction. From this, extract:
- Actual request CAN id (`ATSH` value used)
- Actual response CAN id (from ELM327 lines)
- Raw response payload for `0x2AB8` and `0x2AB2`
- Whether a session change (`10 03`) precedes the reads

**No vehicle authorisation needed** — this observes the user's existing working tool.

### 6. Vehicle Data Collection — Is It Necessary?

| Question | Answer |
|---|---|
| **Is new vehicle data necessary to validate the decoding?** | **Yes.** Neither layout can be VALIDATED without raw vehicle response bytes. The arithmetic proof only refutes Model B; it cannot confirm Model A's SBI=2 offset or the addressing. |
| **What evidence would justify an active capture?** | A passive Car Scanner capture showing the working request/response. If passive capture is not feasible, the NRC 0x31 at gateway 0x792→0x710 specifically suggests trying **extended session (0x10 0x03)** as the next active request — because NRC 0x31 often means "DID not supported in current session". |
| **What additional authorisation is required?** | Any active diagnostic request (including session change `10 03`, different address pair, or DID retry) requires **separate explicit authorisation** per the safety restrictions in `meb_gateway_energy.md` §5, §10–12. The passive capture requires **no authorisation** beyond the user's normal Car Scanner use. |

---

## Appendix: Key Evidence References

| Evidence | File | Location |
|---|---|---|
| Car Scanner profile records (verbatim) | `live_measurement_evidence.csv` | Rows for `pid_identifier` 222AB8 / 222AB2 |
| Gateway addressing audit | `ecu_addressing.md` | §C.3 table; §C.2 `GetECUName`/`BuildList` |
| Arithmetic refutation of Model B | `tools/phase_c_arithmetic.py` | Lines 135-165 (output), 172-178 (gcd) |
| Live capture results (2026-10-15) | `meb_gateway_energy.md` | §11 (empirical results) |
| Replay validation report | `replay_report.md` | §3–4 (INDETERMINATE, 0 vehicle responses) |
| Catalogue definitions (PROVISIONAL) | `decoders/catalogue.py` | `OVERLAY` entries for `hv_energy_content`/`hv_energy_max` |
| Decoder implementations | `decoders/gateway.py` | Lines 96-136 (Model A constants and decode) |
| ENERGY DECODER RESOLUTION | `decoders/meb.py` | Lines 118-173 |
| ECU registry | `diagnostic/ecus.py` | Lines 56-70 (`gate`), 86-94 (`energy`) |

---

## Follow-up: Car Scanner / Live Capture Contradiction

**Date**: 2026-10-15 (addendum)

### 1. The Apparent Contradiction

| Claim | Source |
|---|---|
| Car Scanner uses `ATSP6; ATFCSH710; ATFCSD300000; ATFCSM1; ATCRA77A` then `22 2AB8` | `profiles7.db` BCM field (verified) |
| Our test at `0x710→0x77A` got 70 timeouts (NO DATA) | `tx_log_real_car.csv` — `energy` ECU (11-bit `0x710/0x77A`) |
| Our test at `0x792→0x710` got NRC 0x31 | `tx_log_real_car.csv` — `gate` ECU capture 2026-10-15 |

**Resolution**: The two test sequences **are not the same**. The contradiction vanishes when the actual ELM327 command sequences are compared side by side.

### 2. Exact Command Sequence Comparison

| Step | Car Scanner (from `profiles7.db` BCM) | `capture_gateway_energy.py` (actual, from `tx_log`) |
|---|---|---|
| 1. Protocol | `ATSP6` (ISO 15765-4 CAN **11-bit** 500 kbps) | `ATSP7` (29-bit) — **pinned in `Elm327Transport.PINNED_PROTOCOL = "7"`** |
| 2. Request header | `ATFCSH710` → physical request to CAN ID `0x710` | `set_header(0x792)` → **NO-OP** (transport suppresses ATSH, stays functional) |
| 3. Flow control | `ATFCSD300000` (BS=0, STmin=0) + `ATFCSM1` (user-defined) | **Never sent** — transport uses `ATCAF1` (auto-formatting) |
| 4. Receive filter | `ATCRA77A` → filter for response CAN ID `0x77A` | `set_receive_address(0x710)` → **NO-OP** (transport never programs ATCRA) |
| 5. MEB negotiation | **None** for this ECU (11-bit only) | Full 29-bit MEB negotiation: `ATCP 17`, `ATSH FC007B`, `ATCRA0`, restore functional |
| 6. Actual UDS request | `22 2AB8` sent to physical `0x710` | `22 2AB8` sent **functionally** (broadcast) because `set_header` is a no-op |
| 7. Response attribution | By receive filter `ATCRA77A` (hardware) | By software (`uds.payloads_by_source` with `ATH1` headers) |

**Key difference**: Car Scanner uses **11-bit physical addressing** (`ATSP6` + `ATFCSH710` + `ATCRA77A`). Our transport uses **29-bit functional addressing** (`ATSP7` + no header + no filter) and the `set_header`/`set_receive_address` calls are documented no-ops (`elm327.py:376-406`).

### 3. Source of Each Claimed Live Result

| Result | tx_log IDs | Actual Sequence |
|---|---|---|
| **70 timeouts at `0x710→0x77A`** | 11445–11826, 12197–12202 (ecu=`energy`) | Collector's `poll_did` → `set_module(0x00000710, 0x0000077A)` → tries MEB module addressing on **29-bit-negotiated transport**; adapter refuses 11-bit module switch → falls back to functional → `NO DATA` |
| **NRC 0x31 at `0x792→0x710`** | 12426–12434 (ecu=`gate`) | `capture_gateway_energy.py`: full ATSP7 init + MEB negotiation → `set_header`/`set_receive_address` no-ops → `22 2AB2`/`22 2AB8` sent **functionally** → gateway (or other ECU) answers with NRC 0x31 |

**Neither test reproduced Car Scanner's `ATSP6; ATFCSH710; ATCRA77A` sequence.**

### 4. Are the Profile Commands Actually Executed?

**Yes.** The `BCM`/`ACM` fields in `profiles7.db` are **per-PID setup commands** that Car Scanner sends before each measurement. This is confirmed by:

- `CAR_SCANNER_REVERSE_ENGINEERING.md` §7: "The authoritative per-module setup is the `BCM`/`ACM` fields of the `CustomPID` records themselves, which are data rather than disassembly."
- The `CustomPIDViewModel.AddFromString` → `CustomPID` pipeline stores these fields verbatim.
- Car Scanner's `GetVAGCanRequests` (scan list) and `BuildList` (module metadata) are separate from the per-PID `BCM` commands.
- The `energy` ECU (11-bit `0x710/0x77A`) appears in **both** the scan list (§5.2 table) **and** the gateway DIDs' `BCM` field — they agree on the addressing.

### 5. Are the Energy Values Fresh (Not Cached/Derived/Stale)?

**Yes, they are live readings.** Evidence from the Car Scanner CSV export (imported to `real_car.db`):

| Timestamp | `hv_energy_content` | `hv_energy_max` | Discharge Counter Change |
|---|---|---|---|
| 2026-10-05T19:02:08 | 41,325 Wh | 53,200 Wh | baseline |
| 2026-10-05T19:17:25 | 41,200 Wh | 53,200 Wh | +0.143 kWh (143 Wh) |

- `hv_energy_content` **decreased by 125 Wh** over ~15 minutes while the car was parked.
- `energy_counters` discharge counter **increased by 143 Wh** over the same window.
- Direction of change matches: energy left the pack while current read positive (discharge).
- `hv_energy_max` **stable** at 53,200 Wh (as expected for a maximum capacity figure).

This is **live sensor data**, not cached or derived. The 125 Wh step = exactly 5 counts at 25 Wh/LSB (Model A), which independently corroborates the profile's scale.

### 6. Interpretation of `ATCRA77A`

| Hypothesis | Evidence | Status |
|---|---|---|
| `ATCRA77A` = Car Scanner encoding (response_header + 0x6A) | `0x710 + 0x6A = 0x77A` for all 5 11-bit ECUs in profile | **PARTIALLY TRUE BUT MISLEADING** — the arithmetic pattern exists, but `ATCRA` is a standard ELM327 command meaning "receive filter = this CAN ID". The profile uses `ATFCSH710` (request header) and `ATCRA77A` (response filter) — the 0x6A gap is the standard VAG 11-bit request/response offset (e.g., `0x7E5/0x7ED` = +8; `0x710/0x77A` = +0x6A). Different ECU pairs have different offsets. |
| `ATCRA77A` = receive filter for CAN ID `0x77A` | ELM327 spec; `ATCRA <id>` pins filter to that ID; Car Scanner's `ACM` restores with `ATFCSM0;ATAR;ATSP7` | **VERIFIED** — this is the standard ELM327 meaning. The profile uses it correctly for 11-bit physical addressing. |

**Conclusion**: `ATCRA77A` means "only show me frames from CAN ID 0x77A". It is not a Car Scanner abstraction. Our transport's refusal to program ATCRA (`set_receive_address` is a no-op) is why the 11-bit path never worked.

### 7. Model B Refutation — Precise Assumptions and Limits

**The arithmetic argument** (`tools/phase_c_arithmetic.py`):

| Assumption | Role |
|---|---|
| Observed values are exact integers (41325, 41200, 53200 Wh) | From Car Scanner CSV export; `raw_response` shows integer Wh |
| Model B = `value = raw / 1310.77` with `raw` = unsigned 32-bit integer | From `decoders/meb.py`: `MAX_ENERGY_COUNT_WH = 1310.77` |
| 1310.77 = 131077/100 exactly (lowest terms) | Verified: `Fraction(131077, 100)` |
| For Model B to produce value `v`, `raw = v × 131077/100` must be integer | Inversion of the decoder formula |
| `v × 131077/100` integer ⇔ `v` multiple of 100 | Because `gcd(131077, 100) = 1` |

**Result**:
- 41,325 % 100 = 25 → **NOT a multiple of 100** → **no integer raw exists** → Model B **excluded** for `hv_energy_content`.
- 41,200 % 100 = 0 → multiple of 100 → Model B **not excluded** by this value alone.
- 53,200 % 100 = 0 → multiple of 100 → Model B **not excluded** by this value alone.

**What this proves**:
- Model B **cannot have produced** the 41,325 Wh reading.
- Model A (`u16be × 25` at offset 2) **can produce** all three values from integer u16 raws (1653, 1648, 1064).
- gcd(41325, 41200) = 25 → admissible multiplicative scales = {1, 5, 25}. Model A's 25 ∈ set.

**What this does NOT prove**:
- Model A is **correct** — only that it is the **only one of the two tested layouts** that can produce all observed values.
- The offset (SBI=2) is correct — arithmetic alone cannot distinguish offset 0/1/2 if the scale divides the values.
- The addressing (0x710/0x77A) is correct — arithmetic is agnostic to transport.
- The unit (Wh) is correct — the profile declares UN=61 (Wh) but this is third-party metadata.

**Both DIDs remain PROVISIONAL.** The refutation eliminates one candidate; it does not validate the other.

### 8. Remaining Uncertainties

| Question | Status |
|---|---|
| Does the gateway answer `22 2AB8` at `ATSP6; ATFCSH710; ATCRA77A`? | **UNTESTED** — no tool has sent this exact sequence. |
| Is `ATSP6` (11-bit) required, or does the gateway also listen on 29-bit? | Car Scanner uses both: 11-bit `0x710` (BCM) and 18DA `18DAD0F1` (string heap). |
| Does the gateway require `ATFCSH/ATFCSD/ATFCSM1` flow control? | The 114 `?` responses for `energy` ECU suggest yes — without flow control, the adapter may not transmit the request correctly. |
| Which ECU physically holds 0x2AB8/0x2AB2? | Gateway (J533) per Car Scanner `[19.Gate]` label; NRC 0x31 at 0x792 confirms *some* ECU at that address knows the DID but refuses it in default session. |

### 9. Single Safest Next Step

**Passive capture of Car Scanner's live ELM327 traffic** (unchanged from Phase 3 recommendation).

- **Why**: Only this observes the **actual working sequence** — `ATSP6`, `ATFCSH710`, `ATCRA77A`, flow control, and the raw response bytes.
- **No authorisation needed** — uses the user's existing Car Scanner + adapter.
- **Expected yield**: Request CAN ID, response CAN ID, flow control necessity, raw payload for both DIDs, session type.
- **If passive capture is infeasible**: The next active test with evidence rationale is **`ATSP6; ATFCSH710; ATCRA77A; 22 2AB8`** (Car Scanner's exact 11-bit physical sequence), NOT an extended session on the wrong protocol.

**Do not jump to extended session (0x10 0x03)** — the NRC 0x31 was obtained on the wrong protocol (ATSP7) and wrong addressing (functional). It carries no information about whether the DID exists on the gateway under the correct 11-bit physical addressing.