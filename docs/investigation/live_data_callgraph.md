# Phase C: Live data call graph

> **Resolved 2026-10-15.** The "cannot conclude" notes below were closed by
> the later analysis: measurement definitions live in
> `CarScannerMaui.EmbeddedFiles.profiles7.db` (MEB profile index **342**,
> 308 `CustomPID` records), loaded by `ProfileV2Model.LoadProfiles` →
> `OBDReaderProfileV2.ImportProfilePids` → `CustomPIDViewModel.AddFromString`,
> and decoded by `CustomPID.Decode` / `DecodeByteSet`. Full call path and the
> gateway measurements are in **`meb_gateway_energy.md`** §3.

## Verified (from existing artefacts)
- GetVAGCanRequests (0x2274dc) sequence: `ATFCSH{0};ATFCSD300005;ATFCSM1;ATCRA{1};1003` confirmed in il_getvagcan.txt. Status: VERIFIED.

## From disasm of LoadVAG_MEB (0x35d158)
- Loads string 'vagmeb.db' and calls resource/stream operations. Early calls involve embedded resource access. Status: INFERRED (resource loading path).

## From disasm of BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 (0x356654)
- Uses 'CarScannerMaui.EmbeddedFiles.' prefix when accessing embedded files. Calls into stream/crypto operations and returns deserialized object. Status: VERIFIED (confirms embedded resource deserialization via this method).

## From known task findings + strings
- ShiftStream2.ShiftReadByte (0x35795c) - stream transform for resource decoding. Status: INFERRED.
- GetVAGCode (0x217F8C), GetVAGKWPRequests (0x2271D8) - present. Status: UNRESOLVED (purpose unclear).
- Resources: vagmeb.db, vag.micro.vag, vag.strings.vag loaded via embedded resource system. We have decoded vagmeb.json and raw res_micro_vag/res_strings_vag. Status: UNRESOLVED whether they directly contain live DIDs for [19.Gate]/[8105] - must follow consumers.

## Call graph (edges)
1. LoadVAG_MEB -> uses BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2 to load embedded resources (including vagmeb.db) - INFERRED
2. Loaded resources may be transformed via ShiftStream2 - INFERRED
3. LoadVAG_MEB -> GetVAGCanRequests (referenced in task) - INFERRED (not directly visible in the short LoadVAG_MEB snippet we got, but task states it)
4. GetVAGCanRequests -> returns CAN request sequences - VERIFIED (from il_getvagcan.txt)

## Conclusion for Phase D
We cannot conclude resources contain live-measurement DIDs for hv_energy_content/max or DC/DC without tracing consumers of the loaded/decoded resource objects. We have the artefacts (DLL, scripts) to continue tracing - but this is static analysis of IL. The next step is deeper tracing: follow what LoadVAG_MEB does with loaded resources and whether those feed live measurement definitions.

However, the task states "Do not conclude that a resource contains DIDs merely because it is large or has an unfamiliar header" - and we have vagmeb.json (decoded) which is small structured data; res_micro_vag/res_strings_vag are larger. We need to understand the actual consumer code.
