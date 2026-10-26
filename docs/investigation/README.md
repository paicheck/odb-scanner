# Car Scanner investigation artifacts

Findings extracted from the Car Scanner Android app (`.NET MAUI`, XABA assembly
store) and used to derive the DID maps in `decoders/`.

**Landmark documents** (not in this directory):

- [`../CAR_SCANNER_REVERSE_ENGINEERING.md`](../CAR_SCANNER_REVERSE_ENGINEERING.md)
  — the primary reverse-engineering document. §14 records the 2026-10-15
  resource-classification audit and is the authority on what each embedded
  resource actually contains.
- [`car_scanner_audit_report.md`](car_scanner_audit_report.md) — earlier
  pass; its "modulo-18 distribution" finding correctly undermined the flat-record
  reading of `vag.micro.vag`, though its replacement schema (flat 3-byte ints)
  is itself superseded — see §14.2 of the reverse-engineering document.

## Files here

| File | Size | Contents |
|------|-----:|----------|
| `live_measurement_pipeline.md` | 29 KB | Full write-up of the `profiles7.db` → `CustomPID` pipeline: call graph, MEB ECU map (308 measurements), scaling logic (`ByteSet` / `BitValue` / `Formula`), worked examples, confidence table. |
| `live_measurement_evidence.csv` | 167 KB | All 308 MEB measurements with resolved `CMD` / `BCM` / `ACM` / `SBI` / `DL` / `MUL` / `DIV` / units. The primary evidence table. |
| `decompilation_index.md` | 10 KB | Index of the ILSpy decompilation runs — which types were extracted, which failed, and where the output landed. |
| `resource_inventory.md` | 8 KB | Every embedded resource with size, format and consumer. |

`live_measurement_pipeline.md` supersedes the shorter synthesis that previously
occupied this filename.

## Heavy artifacts stay on disk

The rest of the investigation is ~1.37 GB across ~10,400 files and is **not** in
git — toolchain, APK binaries and reproducible intermediates. Source:

```
C:\Users\kings\Documents\CarScanner_Investigation\
```

| Path | Why not tracked |
|------|-----------------|
| `original_1.apk` | 73 MB — the source binary. Obtainable from Play Store. |
| `resources\CarScannerMaui.EmbeddedFiles.profiles7.db` | 10 MB — the resource these findings derive from. |
| `resources\*.lzma.out`, `*.bson`, `profiles7.bson` | ~145 MB of decode intermediates, fully reproducible from the 10 MB resource with `lzma.decompress(..., FORMAT_ALONE)` + `unshift()`. |
| `libassembly-store.so`, `libaot-*.so`, `extracted_assemblies\` | APK extraction output (~110 MB). |
| `dotnet-sdk\`, `dotnet-tools\`, `venv\` | Downloaded toolchain (~900 MB). |
| `full_decomp\*.cs`, `decomp\*.cs` | ILSpy output. The two files the classification depends on are `full_decomp\ad.cs` and `full_decomp\CarScannerMaui.DTC.VagDTC\VagMicroItem.cs`. |
| `tools_cs\*.py` | Decode tooling. `bson_profiles.py` carries the verified `unshift()` + BSON walker. Several `dbg*.py` files are scratch and should be pruned. |

## Known discrepancies between these files and the repo

1. **`ProfilePIDs` length.** These files state 171,289 bytes; re-measuring the
   MEB profile against the 10,032,980-byte `profiles7.db` here gives **171,033**.
   Different APK build (the other build's resource is 9,919,996 B), not a parsing
   error. Recorded in §12.7 of the reverse-engineering document.
2. **Gateway addressing.** These files describe the gateway as 11-bit
   `0x710`/`0x77A`. On the actual vehicle that address has **never** answered
   (114/114 no-data). See §14.5 — addressing is unresolved and is the active
   blocker.
3. **Gateway scale.** These files give `0x2AB8` as `u16be × 25`; `decoders/meb.py`
   uses `u32 / 1310.77` and marks that divisor unverified. Unresolved, and
   downstream of item 2.

Items 2 and 3 mean the gateway energy DIDs in `decoders/meb.py` should be treated
as **not yet validated against this car**, regardless of what any document says.