# Next Steps to Resume Phase C

> **Status 2026-10-15 — Phase C is COMPLETE; do not repeat the work below.**
> The "Immediate actions" listed on this page were carried out and the live
> measurement definitions were found: embedded resource
> `CarScannerMaui.EmbeddedFiles.profiles7.db`, decoded LZMA → ShiftStream2 →
> BSON → `List<OBDReaderProfileV2>`; the MEB profile is index **342** with
> **308** `CustomPID` records. `hv_energy_content` = DID `0x2AB8` on
> `[19.Gate]` (u16be at payload offset 2 × 25 Wh), `hv_energy_max` = DID
> `0x2AB2` (offset 0 × 50 Wh). Both are now implemented and offline-tested in
> `decoders/gateway.py` / `tests/test_gateway_energy.py`.
>
> **The next step is no longer static analysis.** It is the separately
> authorised, read-only in-car validation runbook in
> **`meb_gateway_energy.md` §7**, plus the open questions in §6 of that file.
> Everything below is kept as the original 2026-10-14 plan.

## Artefacts found
All core artefacts from the investigation are present in `C:\Users\kings\AppData\Local\Temp\opencode\`:
- Managed assemblies including `carscanner_dlls/CarScannerMaui.dll` (38.1 MB)
- Scripts: extract_store.py, disasm*.py, inspect_dll.py, find_meas.py, extract_one.py, decrypt.py, bson2json.py, profile_csv.py, reported_rows.py
- Resources: vagmeb.db, vagmeb.dec.bin, vagmeb.json, res_micro_vag (vag.micro.vag), res_strings_vag (vag.strings.vag), carscanner_strings.txt
- Analysis: il_getvagcan.txt (contains GetVAGCanRequests), spot2000.csv

Also extracted Android tree exists under `C:\Users\kings\OneDrive\Documents\Car Scanner` (native .so) but managed DLLs/resources we need are in opencode temp.

## Immediate actions (Phase C)
1. **Inspect the main assembly**: Load/analyze `CarScannerMaui.dll` (in opencode temp) to find methods. Specifically look for:
- LoadVAG_MEB (RVA mentioned: 0x35D158 in task notes - verify)
- GetVAGCanRequests (RVA 0x2274DC per il_getvagcan.txt and task)
- GetVAGCode (0x217F8C), GetVAGKWPRequests (0x2271D8)
- BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 (0x356654)
- ShiftStream2.ShiftReadByte (0x35795C)

2. **Read il_getvagcan.txt fully** to understand GetVAGCanRequests implementation - it contains disasm/IL that matches the reported sequence (ATFCSH, ATFCSD, ATFCSM1, ATCRA, 1003). This gives direct evidence.

3. **Examine resource loading**: Understand how embedded resources (vagmeb.db, vag.micro.vag, vag.strings.vag) are loaded - look up BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 and ShiftStream2 consumers. Don't blindly decrypt - follow the loader.

4. **Trace call graph** from LoadVAG_MEB through GetVAGCanRequests to see how live measurements are built. Label edges verified/inferred/unresolved per task.

5. **Leverage existing decoded outputs**: vagmeb.json and decoded files exist - compare with what code expects. Don't overwrite originals.

## Phase A/B status
- A: Complete - baseline documented, tests pass (38), guard passes, BMS path protected. 
- B: Complete at protocol level with simulated tests; real hardware transcripts missing - validation incomplete but documented.

## Recommendation (single best next action)
Start Phase C by reading `il_getvagcan.txt` completely and inspecting `CarScannerMaui.dll` around the reported RVAs (LoadVAG_MEB, GetVAGCanRequests). Use the existing scripts in opencode temp (inspect_dll.py, disasm*.py) where helpful. This uses existing work and avoids repetition.

**Superseded 2026-10-15:** done — see the banner at the top. The measurement
definitions were located in `profiles7.db`, not in `vag.micro.vag`; the
recovered artefacts now live under
`C:\Users\kings\Documents\CarScanner_Investigation\`.

