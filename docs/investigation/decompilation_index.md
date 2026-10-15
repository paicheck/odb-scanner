# Decompilation Index

Types inspected during this investigation, the files generated, the important methods,
their callers, and what to inspect next.

Tooling: **ILSpy CLI 11.1.0.9782** (`dotnet-tools\ilspycmd.exe`), .NET SDK 10.0.402.

---

## 1. Decompilation runs performed

### 1.1 Targeted single-type runs (first pass)

Command pattern:

```powershell
& $ILSpy -t <FullTypeName> $Dll | Set-Content <Name>.decompiled.cs -Encoding utf8
```

| Type | Output file | Bytes | Exit |
|---|---|---:|---:|
| `CarScannerMaui.OBD2.PIDS.CustomPID` | `CustomPID.decompiled.cs` | 34,945 | 0 |
| `CarScannerMaui.OBD2.PIDS.NC_CustomPID` | `NC_CustomPID.decompiled.cs` | 1,658 | 0 |
| `CarScannerMaui.OBD2.PIDS.PIDWithFloatValueFormula` | `PIDWithFloatValueFormula.decompiled.cs` | 216,613 | 0 |
| `CarScannerMaui.ECUModels.VAGECU` | `VAGECU.decompiled.cs` | 39,564 | 0 |

### 1.2 Targeted runs (second pass) → `decomp\`

| Type | File | Bytes |
|---|---|---:|
| `CarScannerMaui.OBD2.VWTP20.VWTPManager` | `decomp\VWTPManager.cs` | 7,200 |
| `CarScannerMaui.OBD2.VWTP20.VWTPECU` | `decomp\VWTPECU.cs` | 13,701 |
| `CarScannerMaui.OBD2.VWTP20.IVWTPManager` | `decomp\IVWTPManager.cs` | 208 |
| `CarScannerMaui.OBD2.VWTP20.VWTPFormulaManager` | `decomp\VWTPFormulaManager.cs` | 18,289 |
| `CarScannerMaui.OBD2.PIDS.PID` | `decomp\PID.cs` | 9,382 |
| `CarScannerMaui.OBD2.PIDS.CustomPIDType` | `decomp\CustomPIDType.cs` | 151 |
| `CarScannerMaui.ViewModels.CustomPIDViewModel` | `decomp\CustomPIDViewModel.cs` | 15,068 |
| `CarScannerMaui.ViewModels.LiveDataPIDModel` | `decomp\LiveDataPIDModel.cs` | 10,578 |
| `CarScannerMaui.ViewModels.PIDEditorListModel` | `decomp\PIDEditorListModel.cs` | 6,769 |
| `CarScannerMaui.OBD2.RequestProducers.PIDSelectorRequestProducer` | `decomp\PIDSelectorRequestProducer.cs` | 385 |
| `CarScannerMaui.PIDAdapter` | `decomp\PIDAdapter.cs` | 1,315 |
| `CarScannerMaui.Common.BitHelpers` | `decomp\BitHelpers.cs` | 4,536 |
| `CarScannerMaui.Common.PackageFileReader` | `full_decomp\CarScannerMaui.Common\PackageFileReader.cs` | — |

**Failed run (recorded for completeness):** `-t CarScannerMaui.OBD2.RequestProducers.OBDRequest`
→ **exit 65** (type not found). `OBDRequest` is not in that namespace; it was subsequently located
by member-usage search rather than by direct decompile. Treat `-t` with a guessed namespace as
unreliable — verify with `-l c` first.

### 1.3 Android platform assembly (`CarScannerMaui.Droid.dll`)

| Type | File | Bytes |
|---|---|---:|
| `CarScannerMaui.Droid.Common.NativeShiftStream` | `decomp\NativeShiftStream.cs` | 10,028 |
| `CarScannerMaui.Droid.Common.c` | `decomp\droid_c.cs` | 1,275 |
| `CarScannerMaui.Droid.Services.PlatformCommonServiceImplementationDroid` | `decomp\PlatformCommonServiceImplementationDroid.cs` | 16,328 |
| `d` (Babel string table) | `decomp\droid_d.cs` | 2,045 |

### 1.4 Full-assembly decompile → `full_decomp\`

```powershell
& $ILSpy -p -o full_decomp --ignore-decompilation-errors --disable-updatecheck $Dll
```

Exit 0, **1,279** `.cs` files, namespaced directories. This is the primary search surface.

### 1.5 Resource enumeration

```powershell
& $ILSpy --list-resources $Dll          # 4,438 entries -> embedded_resource_list.txt
& $ILSpy --resource <name> -o resources $Dll
```

> Note: `-l r` as given in the brief **returns 0 lines** and is not a valid entity type for this
> build. The correct switch is `--list-resources`. Recorded so the step is not repeated.

---

## 2. High-value methods and their callers

### 2.1 Definition → request

| Method | Location | Callers |
|---|---|---|
| `LiveDataPIDModel.GetRequests(IPID, List<OBDRequest>, string, string)` | `LiveDataPIDModel.cs:273-364` | `AddFuelConsumptionRequests`, dashboard/live-data request builders, `PIDSelectorRequestProducer.Delegate` |
| `LiveDataPIDModel.UpdatePIDCollection(OBDDataReader)` | `LiveDataPIDModel.cs:60-132` | connection-completed handlers |
| `VWTPManager.BuildRequest(string, string, bool)` | `VWTPManager.cs:422` | `VWTPManager.SendCommand` |
| `VWTPManager.SendCommand(string)` | `VWTPManager.cs:33-54` | `OBDDataReader` command dispatch (prefix `VWTP:`) |

### 2.2 Definition loading

| Method | Location | Callers |
|---|---|---|
| `ProfileV2Model.LoadProfiles(bool)` | `ProfileV2Model.cs:886` | `BrandSelectorPage.cs:134`, `SettingsDashboard.cs:516`, `w.cs:40` |
| `ProfileV2Model.d()` | `ProfileV2Model.cs:949-953` | `LoadProfiles` (primary path) |
| `ProfileV2Model.e()` → `f<T>()` | `ProfileV2Model.cs:958-1017` | `LoadProfiles` (fallback path) |
| `PackageFileReader.BinaryDeserializeFromEmbeddedFileUsingCompressedCryptoStreamWithDiff<T>` | `PackageFileReader.cs:167` | `ProfileV2Model.d()` |
| `PackageFileReader.BinaryDeserializeFromEmbeddedFileUsingCompressedCryptoStream2<T>` | `PackageFileReader.cs:146` | the method above, when no diff file exists |
| `OBDReaderProfileV2.ImportProfilePids(OBDReaderProfileV2)` | `OBDReaderProfileV2.cs:603-655` | profile selection; also `w.cs:153` |
| `CustomPIDViewModel.AddFromString(string)` | `CustomPIDViewModel.cs:475-492` | `ImportProfilePids` |
| `CustomPIDViewModel.Save()` | `CustomPIDViewModel.cs:375-391` | `ImportProfilePids`, `EnsureUniqueIds`, profile apply |
| `CustomPIDViewModel.a()` (load from settings) | `CustomPIDViewModel.cs:407-473` | `LoadSafe`/`LoadSafeAsync`, `ReloadAll` |
| `ad.j()` | `ad.cs:353-357` | VAG model-code DB load |
| `ad` micro loader (`LoadMicroContainers`) | `ad.cs:~300-350` | VAG DTC text |

### 2.3 Decode path

| Method | Location | Notes |
|---|---|---|
| `OBDDataReader` `22`-split loop | `OBDDataReader.cs:5256-5281` | consumes service marker + DID, passes only payload |
| `OBDDataReader` marker strip | `OBDDataReader.cs:2289` (`dataWithoutMarker`) | strips response marker |
| `CarData.Decode(string, string, OBDRequest, byte[], TimeSpan)` | `CarData.cs:1630-1690` | dispatches to `PID.Decode`; parallel for ≥10 PIDs |
| `PID.Decode(byte[], TimeSpan, string)` | `PID.cs:500-504` | virtual base (no-op) |
| `CustomPID.Decode` | `CustomPID.cs:910-955` | the real switch |
| `CustomPID.DecodeByteSet` | `CustomPID.cs:1093-1170` | big-endian, scaling |
| `CustomPID.DecodeReversedByteSet` | `CustomPID.cs:992-1076` | little-endian |
| `CustomPID.DecodeBitValue` | `CustomPID.cs:1078-1091` | |
| `CustomPID.DecodeFormula` | `CustomPID.cs:1173-1197` | mxparser |
| `CustomPID.InitializeEngine` | `CustomPID.cs:841-879` | registers formula functions |
| `CustomPID.BuildDictionaryV2` | `CustomPID.cs:1209-1226` | letter index ⇒ byte index |
| `CustomPID.ReloadFormula` | `CustomPID.cs:730-807` | `{Name}` / `PID(id)` dependency wiring |
| `CustomPID.GetDependencyPidsFromPidFormula` | `CustomPID.cs:1371-1388` | |
| `CustomPID.VagUnit` / `VagGroup` | `CustomPID.cs:344-359` / `327-342` | `VWTP:` only |

### 2.4 Transport / resources

| Method | Location | Notes |
|---|---|---|
| `a.a(Stream, Stream)` | `full_decomp\a.cs` | **LZMA-Alone** decompressor |
| `al.ab(Stream, Stream)` | `full_decomp\al.cs` | second, **custom** LZMA (LSB-first range decoder) |
| `an.at(Stream)` | `full_decomp\an.cs` | AES(+LZMA) container decoder used by `af`/`d` string tables |
| `an.au(byte)` / `an.av(Stream,Stream)` | `full_decomp\an.cs` | cipher selection / copy loop |
| `NativeShiftStream.d(Span<byte>, long, int)` | `decomp\NativeShiftStream.cs:240` | calls native cipher; variant + absolutePosition |
| `CarScannerMaui.Droid.Common.c.e(...)` | `decomp\droid_c.cs` | P/Invoke → `NativeLibrary.GetExport` |
| `PlatformCommonServiceImplementationDroid.GetShiftStream/2/3` | `decomp\…Droid.cs:599-617` | variant 1 / 2 / 3(keyed), each `BufferedStream(…, 65536)` |
| `CodingListModel` `vagmeb.db` load | `CodingListModel.cs:1105` | proves `vagmeb.db` ⇒ `MQBAdaptationTemplate` |

---

## 3. Types that should be inspected next

| Priority | Type | Why |
|---|---|---|
| 1 | `CarScannerMaui.OBD2.OBDRequest` | **Never decompiled directly** (exit 65). Governs request identity/dedup (`Equals`), `SkipCyclesTarget`, `CheckLength`, `Keys`, `PIDs`. Locate its real namespace with `-l c`. |
| 2 | `CarScannerMaui.OBD2.OBDMultiRequest` | `CarData.Decode:1645` special-cases it; explains multi-DID `22` grouping and per-DID payload splitting. |
| 3 | `CarScannerMaui.OBD2.RequestProducers.OBDRequestProducerStatic` | `WorkingModes` gate behind `PIDSelectorRequestProducer`. |
| 4 | `CarScannerMaui.DTCv2.VagUDSSupportedUnitsDetector` | contains `CheckForVWTPGate`, `<ClearDTCVWTP20Async>`, `VWTP20_KWP`/`VWTP20_UDS` — confirms UDS-vs-KWP split per platform. |
| 5 | `CarScannerMaui.Coding.DB.VWTP.VWLongCoding` | `<GetCurrentStateRawData>` — same VWTP plumbing for coding. |
| 6 | `CarScannerMaui.OBD2.VWTP20.VwTp20Worker` | second, newer VWTP engine (`OpenChannelUsingLoopAsync`, `BuildRequestForData`, `SendAndReadDataUsingRequestLoop`) — not yet analysed. |
| 7 | `CarScannerMaui.Common.DiffDecodeStream` + `Crc32` | the profile *diff* path (`d()`); only taken when a cache diff file exists. |
| 8 | `CarScannerMaui.OBD2.ECUModels.VAGECU` / `VAG29bitECU` | ECU identity/addressing; decompiled (`VAGECU.decompiled.cs`) but not yet analysed in depth. |
| 9 | `al.b` / `al.a` / `ag` | needed only to port the custom LZMA and read the `jWfF` / `TCWy` Babel string tables. |
| 10 | Remaining 1,322 profiles in `profiles7.db` | same tooling; would give complete ECU/DID coverage across brands. |

---

## 4. Reusable tooling created

| Path | Purpose |
|---|---|
| `tools_cs\unpack.py` | `an.at` container decoder (XOR → AES-CBC → LZMA) + `af` string-table reader |
| `tools_cs\find_shift2.py`, `solve_shift.py`, `solve_shift2.py` | ShiftStream brute-force attempts (superseded — see note) |
| `tools_cs\validate_shift.py` | BSON-validity scoring of candidate transforms |
| `tools_cs\search\` (C#) | exhaustive sweep, 4 transform families, scored by BSON walk depth — **found nothing**; kept as negative evidence |
| `tools_cs\bson_profiles.py` | **ShiftStream2 transform (authoritative)** + BSON decode |
| `tools_cs\mkbson.py` | LZMA → shift → `.bson` |
| `tools_cs\extract_profiles.py` | BSON → per-profile JSON + `ProfilePIDs` blobs |
| `tools_cs\build_evidence.py` | `live_measurement_evidence.csv` builder |
| `tools_cs\dbg*.py`, `peek*.py`, `an_meb*.py`, `try_profiles.py`, `mkhead.py` | diagnostics |

**Note on the searcher:** four transform families (`A+B*i`, `A+B*i+C*(i&1)`,
`A+B*(i>>1)+C*(i&1)`, and XOR variants) were swept exhaustively over all 2^24 combinations each
with BSON-walk-depth scoring and **all failed**. The reason: the true transform's position term is
`p & 0xFF`, which is not representable in any of those families. Recording this so the sweep is
not repeated.