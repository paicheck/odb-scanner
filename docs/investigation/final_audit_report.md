# Final Audit Report

> **Superseded 2026-10-15.** Phases C/D finished: the measurement definitions
> were found in `CarScannerMaui.EmbeddedFiles.profiles7.db` (MEB profile index
> 342), and the `[19.Gate]` energy-content DIDs (`0x2AB8`, `0x2AB2`) are
> implemented and offline-tested. See **`meb_gateway_energy.md`**. Everything
> below is the original 2026-10-14 record.

## Step 1-4: Artefacts & Audit
- Git status: 67bdd7a latest, uncommitted test fix only (tests/test_diagnostic.py) - time-invariant change preserving intent. 38 tests pass, guard-test passes. Phase A documented baseline; B documented transport with simulated tests (real hardware validation incomplete).
- All required Car Scanner artefacts found in C:\Users\kings\AppData\Local\Temp\opencode: CarScannerMaui.dll, scripts (extract_store.py, disasm*.py, inspect_dll.py, find_meas.py, extract_one.py, decrypt.py, bson2json.py), resources (vagmeb.db/dec.bin/json, res_micro_vag, res_strings_vag), analysis (il_getvagcan.txt with verified GetVAGCanRequests sequence ATFCSH{0};ATFCSD300005;ATFCSM1;ATCRA{1};1003, carscanner_strings.txt, spot2000.csv). SHA-256 recorded.
- Previous investigation artefacts exist and are complete.

## Phase A/B completion
- Phase A: Fully complete (baseline, working BMS path covered by simulator regression tests, docs). 
- Phase B: Complete at protocol/parser level with automated simulated tests. Real adapter hardware validation remains incomplete (no raw real-car transcripts) - this is documented. Error classifications implemented via exceptions; tests cover parsing/multi-frame/no-data/negative responses.

## Phase C status
Started - call graph documented. Verified: GetVAGCanRequests sequence confirmed. BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 confirmed for embedded resources. LoadVAG_MEB loads 'vagmeb.db'. Need deeper tracing of consumers (ShiftStream2, how resources feed live measurements) before concluding resources contain DIDs for hv_energy/DC-DC.

## Missing artefacts
None - all required artefacts are present in opencode temp. Originals preserved (not moved/renamed).

## Next steps (to resume Phase C properly)
1. Continue static analysis: read more IL for LoadVAG_MEB to see how loaded resources are used and whether GetVAGCanRequests is called from it. 
2. Examine ShiftStream2 (0x35795c) and how transformed streams are consumed. 
3. Look up GetVAGCode/GetVAGKWPRequests to understand full picture. 
4. Don't assume DIDs - follow actual consumer code. Only proceed to D after establishing consumer.
5. Do not attempt decryption/blind transforms without rationale.

## Single best next action
Resume Phase C by extracting full disasm for LoadVAG_MEB and ShiftStream2 using existing disasm scripts, then trace what objects are constructed from loaded resources. This builds on existing verified evidence without repeating work. No runtime code changes.
