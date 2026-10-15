# Car Scanner Live Measurement Pipeline Investigation

## Date: 2026-10-14

> **SUPERSEDED IN PART — 2026-10-15.** The questions this document lists as
> unresolved have been answered: the live measurement definitions come from
> the embedded resource **`CarScannerMaui.EmbeddedFiles.profiles7.db`**
> (LZMA → ShiftStream2 → BSON → `List<OBDReaderProfileV2>`), the MEB profile
> is index **342** with 308 `CustomPID` records, and MEB uses UDS `0x22` over
> ISO-TP (not VWTP20). `hv_energy_content` = DID `0x2AB8` on `[19.Gate]`,
> `u16be` at payload offset 2 × 25 Wh.
>
> **Read `meb_gateway_energy.md` instead** for the current state of the
> gateway measurements, the request path, and what is still unverified.
> The statements below are kept as the original record of the 2026-10-14
> investigation.

## Executive Summary

Car Scanner's live measurement definitions use the `CustomPID` type (TypeDef 983 in CarScannerMaui.dll), which carries decoding parameters: `_StartByteId`, `_DataLength`, `_Multiplier`, `_Divider`, `_Offset`, `_IsSigned`, `_Type`, `_Bit`, `_Formula`, `_Units`, `_BeforeCommand`, `_AfterCommand`, `_VagGroup`, `_VagUnit`.

The `vag.micro.vag` resource was investigated in detail and found to contain:
- **DTC descriptions (VagMicroItem)** — 18-byte records with `VagCode`, `U`, `RU`, `DE`, `EN`, `S` (DTC code + translations + status)
- **String index array** — 3-byte LE indices into `vag.strings.vag` (3,336,491 entries)

Neither of these is a live measurement definition table. The live measurement definitions (PID, DID, scaling, units, requests) are sourced from a **different location** that has not yet been identified.

---

## Verified Findings

### 1. Resource Decoding (VERIFIED)

#### vagueb.db
- **Loader**: `LoadVAG_MEB` at RVA 0x35D158
- **Loader method**: `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` at 0x356654
- **Content**: `MQBAdaptationTemplate[]` (coding/adaptation database)
- **Fields**: `_READ_MODE`, `_WRITE_MODE`, `_Password`, `_ValueType`, `_Address`, `_RequestHeader`, `_ResponseHeader`, `_StartByteId`, `_DataLength`, `_Multiplier`, `_Offset`, `_IsSigned`, `_Protocol`, `_PreWriteCommands`, `_PostWriteCommands`
- **NOT live measurements** — this is the long-coding/adaptation database

#### vag.micro.vag (`res_micro_vag`)
- **Size**: 6,204,318 bytes raw → LZMA 10,009,474 bytes
- **Decode**: LZMA decompress → ShiftStream2 cipher (even: `-0x58-pos`, odd: `+0x45+pos`)
- **Consumer 1**: `ReadFromStream` at 0x22BCCD — reads 18-byte records as `VagMicroItem(VagCode, U, RU, DE, EN, S)` — **DTC descriptions**
- **Consumer 2**: `LoadMicroContainers` at 0x22B5C4 — reads 3-byte LE indices → looks up strings in `vstr.db` via `LoadFromStrings` — string index array
- **Contains DIDs**: 0x028C (77 occ), 0x1E3B (3 occ), 0x2AB8 (5 occ), 0x2AB2 (2 occ), 0x465B (6 occ), 0x465D (6 occ), 0x09A1 (11 occ), 0x09A2 (18 occ), 0xF50E (10 occ)
- **NOT live measurement definitions** — contains DTC data and string indices

#### vag.strings.vag (`res_strings_vag`)
- **Size**: 3,222,110 bytes raw → LZMA 7,214,595 bytes
- **Decode**: LZMA decompress → ShiftStream2 cipher (even: `-0x58-pos`, odd: `+0x45+pos`)
- **Consumer**: `LoadFromStrings` at 0x22B3C8 — opens `vstr.db`, wraps in ShiftStream2, reads UTF-8 string at specified index
- **Primary section**: 8-byte records `07 P/U + 6 ASCII digits` (2,440 valid of 901,824 slots)
- **Secondary format**: 99.7% is a different binary structure (unknown)
- **Target PIDs**: `P2AB800` at byte 29,728 (unaligned), `P310B00` at byte 48, `P308900` at byte 56

### 2. Gateway Addressing (VERIFIED)

| Address | Provenance | Verified as Gateway? |
|---------|-----------|----------------------|
| `0x792` | `BuildList` @ 0x1ffc60, `GetECUName` @ 0x188dd0 | **YES** — only code-verified |
| `0x710` / `0x77A` | `GetVAGCanRequests` scan list | No label attached |
| `18DAD0F1` | String heap "GATEWAY (29 bit)" | Name only, no consumer binds to Gateway |
| `17FC0076` / `17FE0076` | BuildList for "19. CAN Gateway" | **Misleading** — this is Vehicle Info module |

### 3. Live Measurement Definition Type (VERIFIED)

`CarScannerMaui.OBD2.PIDS.CustomPID` (TypeDef 983) fields:
- `_StartByteId`, `_DataLength` — response byte positioning
- `_Multiplier`, `_Divider`, `_Offset`, `_IsSigned` — scaling
- `_Type`, `_Bit`, `_ReversedByteSet` — interpretation
- `_Formula`, `FormulaElements`, `internalFormula` — custom formulas
- `_Units`, `_Value`, `_BeforeCommand`, `_AfterCommand` — request/response handling
- `_VagGroup`, `_VagUnit` — VAG-specific display grouping
- `_RequiredPIDs`, `_RequiresSTCommands` — dependencies

This is the type that carries complete measurement definitions. **The consumer code that populates these fields from a data source has NOT been identified.**

### 4. GetVAGCanRequests — Verified Sequence

```
ATFCSH{0};ATFCSD300005;ATFCSM1;ATCRA{1};1003
```
This is the CAN filter/setup/upload sequence for UDS requests via VAG UDS-on-CAN.

### 5. LoadMicroContainers Consumer Flow (VERIFIED)

```
UnpackContainers(forceDownload)
  → reads vug.strings.vag → vstr.db (ShiftStream2 decode)
  → reads vug.micro.vag → vmc.db (ShiftStream2 decode)
  → reads 3-byte LE index N from vmc.db
  → IntFrom3Byte(N) 
  → LoadFromStrings(N) → seeks in vstr.db at index N → reads UTF-8 string
  → constructs VagMicroItem(VagCode, U, RU, DE, EN, S) for EventsCollection
  → or uses N as Projects capacity / ASAMCollection index
```

The `Projects` collection is a `List<CodingTemplate>` or similar, where each entry's index comes from the 3-byte LE value in `vmc.db`. The strings from `vstr.db` serve as display labels, not measurement definitions.

---

## Critical Unresolved Questions

### Q1: Where are CustomPID instances created from data?

`CustomPID` has a constructor at RVA 0x1C47E0 (MethodDef 11006). The disassembly of this constructor would reveal whether it takes string parameters, byte arrays, or serialized objects, and whether it maps to resource data or hardcoded values.

**Status: UNRESOLVED** — the constructor's parameters and caller search have not been completed.

### Q2: What populates the measurement registry at runtime?

There must be code that iterates over some data source, creates `CustomPID` instances, and populates their fields (StartByteId, Multiplier, Formula, Units, etc.) with values from a resource, database, or hard-coded mapping. This consumer has not been located.

### Q3: Is there a separate PID-to-DID mapping resource?

The audit report confirmed that `hv_energy_content` appears in `shadow_points` but not in `vag.strings.vag` as a PID string. The mapping from display label to DID could be:
- Hard-coded C# dictionary/initialization code
- A different embedded resource not examined yet
- Generated at runtime from ECU diagnostic services
- Parsed from XML/JSON embedded resources (CarScanner ships A2L/ODX-like data)

### Q4: How does the `BuildList`/`GetECUName` PID enumeration work?

The `BuildList` at 0x206DB4 contains hardcoded `18DA` module addresses and the `GetVAGCanRequests` scan list contains `0x710`, `0x746`, `0x74F`, `0x773`. These are 11-bit CAN identifiers. There must be a cross-reference to which DIDs are polled on each ECU.

## HV_Energy_Content Case Study — Current Status

| Link | Status | Evidence Required |
|------|--------|-------------------|
| Display name | UNRESOLVED | Cannot find "hv_energy_content" string in any resource; likely an internal key |
| Internal key → ECU | UNRESOLVED | No code binds an internal key to Gateway (0x792) |
| ECU → DID | UNRESOLVED | DID 0x2AB8 appears in micro file (5 occ) but no consumer links it to Gateway |
| DID → request construction | UNRESOLVED | No code combines DID 0x2AB8 with 0x792 address |
| Response → raw bytes | UNRESOLVED | No response parser for this measurement |
| Raw bytes → scaled value | UNRESOLVED | Scale factor not identified |
| Scaled value → display | UNRESOLVED | Unit and formatting not identified |

## Strongest Verified Discovery (Phase D)

**`vag.micro.vag` is NOT a measurement table.** It contains:
1. DTC descriptions (18-byte `VagMicroItem` records)
2. String index arrays (3-byte LE integers into `vag.strings.vag`)

The actual live measurement definitions (`CustomPID` instances with DIDs, scaling, units, and requests) are created from a **different data source** that has not been identified. This is a critical finding because it means the entire live measurement pipeline is populated from code we have not yet traced.

## Most Important Unresolved Question

**Where are `CustomPID` objects populated with their fields?** — specifically:
- Is there a method that iterates a collection of pre-defined measurement specifications?
- Is it hard-coded C# initialization code, or loaded from a serialized resource?
- What is the relationship between the `Pxxxxxx` PID strings and the `CustomPID` instances?

## Recommended Next Action

**Find the callers and constructor usage of `CustomPID`** (TypeDef 983, Constructor at RVA 0x1C47E0, MethodDef 11006). Search for `newobj` instructions that reference MethodDef 11006 and trace the calling methods to identify the data source that populates the CustomPID fields.

This single investigation would answer:
1. Whether DIDs/scaling are loaded from a resource or hard-coded
2. Which resource (if any) defines `0x2AB8` for Gateway
3. The architecture of the live measurement definition pipeline

---

## Cross-References

| Document | Status |
|----------|--------|
| `car_scanner_audit_report.md` | **Copied** from C:\odb_scanner\docs\ — complete resource audit |
| `ecu_addressing.md` | **Updated** with verified Gateway 0x792 evidence |
| `measurement_definitions.md` | **Updated** with Car Scanner resource findings; hv_energy still unresolved |
| `live_measurement_pipeline.md` | **This document** — end-to-end call graph + gaps |
| `car_scanner_reverse_engineering.md` (C:\odb_scanner\docs) | Referenced — full findings archive |
