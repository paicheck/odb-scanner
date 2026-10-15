# Phase C/D: Missing Car Scanner artifacts

> **Blocker RESOLVED 2026-10-15.** The artefacts were located and the phases
> completed: `C:\Users\kings\Documents\CarScanner_Investigation\` holds the
> extracted assemblies, the decoded `profiles7.db` and the recovered MEB
> profile (index 342). `hv_energy_content`/`hv_energy_max` are recovered and
> implemented — see **`meb_gateway_energy.md`**. The rest of this page is the
> original 2026-10-14 blocker report.

## Issue
The task's "Known findings" reference Car Scanner APK extraction details (CarScannerMaui.dll, RVAs 0x35D158, 0x2274DC, 0x217F8C etc., methods LoadVAG_MEB, GetVAGCanRequests, GetVAGCode, GetVAGKWPRequests, resources vagmeb.db, vag.micro.vag, vag.strings.vag, BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2, ShiftStream2). 

Searches across C:\Users\kings\OneDrive\Documents (including "Car Scanner" extracted folder and odb_scanner repo) found no files containing these exact strings/method names, no .db/.vag resource files, no disassembly reports or CSVs with those names. The .so files under Car Scanner/lib contain no readable occurrences of these symbols.

## Implication
Phases C and D depend on inspecting those extracted assemblies and resources. Without them, we cannot:
- Trace LoadVAG_MEB/GetVAGCanRequests call graph (Phase C)
- Decode vagmeb.db, vag.micro.vag, vag.strings.vag (Phase D)
- Determine actual addressing (17FC/17FE/18DA) from disassembly evidence
- Recover hv_energy_content/hv_energy_max DIDs definitively

## Evidence gathered (this repo baseline)
We have documented the current implementation: 11-bit UDS addressing (ATSP6, 0x7E5/7ED etc), battery DIDs per OVMS/MEB e-Up conventions, read-only enforcement, working transport. All tests pass.

## Recommendation
The task states "Read the existing code, tests, scripts, extracted assemblies, resource files and previous investigation report before proposing or implementing changes." The required extracted assemblies/resources/report are not present in the expected locations. 

Options:
1. If the artifacts were provided separately, they need to be placed in the workspace (or we need their location). 
2. If this is the baseline state and artifacts are expected to be found later, we must proceed cautiously - we cannot fabricate DIDs from strings that don't exist here. 
3. However, the task says "continue work on the existing `odb_scanner` project" - the project as-is is our baseline. The Car Scanner specifics might refer to external knowledge; but the explicit instruction to inspect extracted assemblies suggests those files should exist.

Given constraints (read-only, no guessing), we document the blocker clearly and complete Phases A,B with thorough documentation. For C-E, we cannot satisfy them without the artifacts - so we should report what’s missing and provide concrete next steps (search locations, what to look for). The task allows reporting if criterion cannot be met - better to be precise.
