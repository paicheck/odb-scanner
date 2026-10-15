# Car Scanner MEB — Evidence Audit Report

**Date**: 2026-10-14  
**Scope**: Reverse-engineering artefacts only (`C:\Users\kings\AppData\Local\Temp\opencode\*`). No production code modified. No vehicle communication.

---

## A. Resource Structure Validation

### A.1 File Sizes & SHA-256

| File | Size (bytes) | SHA-256 |
|------|-------------|---------|
| `res_micro_vag` (raw) | 6,204,318 | 39b19d024851162e0dfe8c4c641f56a64282161cd03755d306701f27eabd36c7 |
| `res_micro_vag.dec` (LZMA) | 10,009,474 | df28d98f4a91628a1c1c0338a7cae565f26ea5bb1bcda4ef7f12eb3c83593924 |
| `res_micro_vag.dec.shifted` (ShiftStream2) | 10,009,474 | 5b8e92c1647f9e3ab2feace5486a82b0c4a002bf54da41c3334c010cbe928986 |
| `res_strings_vag` (raw) | 3,222,110 | c2ae1d758237a2f0559a83767d26eaae2bf9355bef147bef7ef08c8a172cd977 |
| `res_strings_vag.dec` (LZMA) | 7,214,595 | 8e7573ffd7b07ee0d47e68eddd9f68fc13347c659a29de3b6635e1e124eebf3e |
| `res_strings_vag.dec.shifted` (ShiftStream2) | 7,214,595 | 29393c9afb52c3c70e239f07f50785e2db26c48544240b6c05c08a95ca7f727f |

### A.2 Micro Resource (`vag.micro.vag`)

- **LZMA header**: `5d 00 00 80 00` (standard, dict size 8 MB) — decompresses cleanly.
- **ShiftStream2 cipher**: even offset `value - 0x58 - pos`, odd offset `value + 0x45 + pos` — verified by round-trip.
- **Decoded size**: 10,009,474 bytes.
- **Header/footer/padding**: None detected. First byte `0xb6`, last byte `0x3a`. No magic, no section delimiters.
- **Record structure**: **Not 18-byte fixed records**. DIDs (2-byte LE) appear at offsets with modulo-18 distribution across all even residues (0,2,4,6,8,10,12,14,16). The earlier "18-byte record with DID at offset 16" was a sampling artifact from clustered occurrences.
- **Actual structure**: **3-byte little-endian integers** (confirmed by `LoadMicroContainers` IL). Total entries: 10,009,474 / 3 = **3,336,491 indices**.
- **Remainder math**: 10,009,474 % 3 = 1 byte trailing (offset 10,009,473 = `0x3a`).
- **Consumer**: `LoadMicroContainers` (RVA `0x22b5c4`) reads 3-byte LE integers from the decoded stream, calls `IntFrom3Byte` (MDef[14663]), uses each as index into `vstr.db` (ShiftStream2-decoded `vag.strings.vag`).

### A.3 Strings Resource (`vag.strings.vag`)

- **Decoded size**: 7,214,595 bytes.
- **Primary section**: 8-byte records `07 P/U + 6 ASCII digits` (e.g., `07 50 31 34 30 31 30 30` = `P140100`). **2,440 valid records** found before format breaks at offset 16,864.
- **Total 8-byte slots**: 901,824 (7,214,595 / 8, remainder 3). Only 2,440 (0.27%) follow the `P/U` pattern.
- **Secondary format**: Remaining 99.7% is a different binary structure (not plain strings). Target PIDs (`P2AB800`, `P310B00`, `P308900`, `P028C00`, `P1E3B00`, `P465B00`, `P465D00`) **exist as raw substrings** but at unaligned offsets (e.g., `P2AB800` at byte 29,728, record index 3,716). They are **not** in the `07 P/U` section.
- **Conclusion**: The display-name lookup is **not** a simple array indexed by the micro-file indices. The mapping logic is in a different section or a different resource.

---

## B. Identifier & Display-Label Trace

### B.1 String Consumer Code

- **`LoadFromStrings`** (RVA `0x22b3c8`): Opens cached `vstr.db`, wraps in `ShiftStream2` (MDef[22543]), wraps in `StreamReader` (MRef[2399]), reads lines via `ReadString`.
- **`LoadMicroContainers`** (RVA `0x22b5c4`): Reads 3-byte LE ints from `vmc.db` (decoded `vag.micro.vag` via `ShiftStream2`), calls `IntFrom3Byte` (MDef[14663]), then calls `LoadFromStrings(index)` for each index. Stores results into typed collections: `Projects`, `ASAMCollection`, `EventsCollection`.
- **No direct PID→display-name lookup** found in disassembled methods. The `Pxxxxxx` codes in the strings file are **not** consumed by a `GetPidName`/`GetPidDescription` method in the disassembled surface.

### B.2 Target PID Trace

| PID | Found in strings file? | In `07 P/U` section? | Linked to measurement in code? |
|-----|------------------------|----------------------|--------------------------------|
| `P2AB800` | Yes (byte 29,728) | No (unaligned) | **No** — no consumer method references it |
| `P2AB200` | No | — | **No** |
| `P310B00` | Yes (byte 48) | Yes (record 6) | **No** |
| `P308900` | Yes (byte 56) | Yes (record 7) | **No** |
| `P028C00` | No | — | **No** |
| `P1E3B00` | No | — | **No** |
| `P465B00` | No | — | **No** |
| `P465D00` | No | — | **No** |

**Conclusion**: No code path linking a `Pxxxxxx` string to a measurement definition, DID, or scaling factor has been located. The display labels shown in Car Scanner's UI are **not traceable** to the micro-file indices via the current artefacts.

---

## C. Gateway Addressing Verification

### C.1 Address-Table Entries (String Heap)

| Table | Entry | Value | Notes |
|-------|-------|-------|-------|
| VAG 29-bit (17FC/17FE) | — | — | **Gateway absent** from the 80-entry table |
| Normalized 29-bit (18DA) | `GATEWAY (29 bit)` | `18DAD0F1` | Found in string heap at offset 039240639; name precedes value |
| Normalized 29-bit (18DA) | `18DA10F1` … `18DA20F1` | 13 modules | Hard-coded in `BuildList` @ 0x206db4: PCM `10F1`, TCM `18F1`, RCM `58F1`, IPC `60F1`, TCCM `19F1`, ATCM `1CF1`, SCM `15F1`, FLM `70F1`, PBM `2AF1`, RDCM `1AF1`, RLM `2BF1`, SASM `32F1`, VDM `20F1` |
| VAG 29-bit (17FC/17FE) | `19. CAN Gateway` | `17FC0076` / `17FE0076` | **Not Gateway** — this is Vehicle Info (code `0076` = module 19 in 17FC table is `009C` Battery Energy Control 2) |
| 11-bit scan list (`GetVAGCanRequests`) | `0x710` / `0x77A` | `ldc.i4 tok=0x00000710` appears in scan list at `GetVAGCanRequests` | Module listed as `Gateway` in `BuildList` @ 0x1ffc60 at 11-bit `0x792` (see below) |

### C.2 `GetECUName` & `BuildList` Evidence

- **`GetECUName`** (RVA `0x188dd0`): Returns `"Gateway"` for 11-bit identifier `0x792` (seen in switch: `ldc.i4.s 0x792` / `0x00000792` with `ldstr "Gateway"`).
- **`BuildList` (Gateway)** @ 0x1ffc60: Constructs a module list for 11-bit range. Contains entry:
  ```il
  ldstr "Gateway"
  ldc.i4 0x00000792
  ldstr "1001"
  newobj ModuleCtor
  callvirt Add
  ```
  The 11-bit identifier **0x792** is paired with name `"Gateway"`.
- **`GetRequestHeaderForMQBUnit`** (RVA `0x3c27cc`): Hash-based lookup returning request header strings (e.g., `17FC008B`, `17FC0076`). No Gateway entry observed in the hashed branches.

### C.3 18DA Constructor

```il
// .ctor @ 0x202130
ldarg.2          // module-specific byte
ldstr "F1"
call Concat      // "F1" + module_byte → e.g., "10F1"
ldstr "18DA"
ldarg.2
call Concat      // "18DA" + module_byte + "F1" → "18DA10F1"
call Base.ctor
callvirt set_Protocol 7
```
**Meaning**: `18DA` + `<module_byte>` + `F1` (tester source address). Gateway would need its own module byte; **not present in the 13 hard-coded entries**.

### C.3 Address Provenance Summary

| Identifier | Direction | Provenance | Verified as Gateway? |
|------------|-----------|------------|----------------------|
| `18DAD0F1` | 29-bit normalized (target D0, tester F1) | String heap entry "GATEWAY (29 bit)" | **Name only** — no consumer code binds it to Gateway ECU |
| `18DAD2F1` | 29-bit normalized (target D2) | String heap "S-GW3" | Not Gateway |
| `0x710` / `0x77A` | 11-bit (req/resp) | `GetVAGCanRequests` scan list | In scan list, but **no label** attached |
| `0x792` | 11-bit | `BuildList` / `GetECUName` | **Explicitly named "Gateway"** in `BuildList` @ 0x1ffc60 and `GetECUName` switch |
| `17FC0076` / `17FE0076` | 29-bit VAG | BuildList for "19. CAN Gateway" (Vehicle Info, not Gateway) | **Misleading label** — this is Vehicle Info module (code `0076`) |

**Verified**: The **11-bit identifier 0x792** is the only address explicitly bound to the name "Gateway" in executable code (`BuildList`, `GetECUName`). The 29-bit normalized `18DAD0F1` and the scan-list entry `0x710` are **not** bound to "Gateway" in any disassembled consumer.

---

## D. Measurement Trace: `hv_energy_content`

| Link | Status | Evidence |
|------|--------|----------|
| Display label → measurement definition | **Unresolved** | `P2AB800` string exists but no consumer; no `GetPidName` method found |
| Measurement definition → ECU | **Unresolved** | Micro file indices point to strings file, but strings file has no `P2AB800` in indexable section |
| ECU → request DID | **Unresolved** | Micro records contain `0x2AB8`/`0x2AB2` with structured prefixes, but no parser or field map found |
| Request DID → response parsing | **Unresolved** | No DID→scale/offset parser located |
| Response parsing → scale/offset | **Unresolved** | Micro record prefixes (bytes 8-11: `D746`, `FBF6`, `A806`, `EE2F`, `F02F`, `F12F`, `F22F`, `F32F` for Gateway variants) likely encode MUL/OFS but **no decoder found** |
| Scale/offset → final value | **Unresolved** | No decoder method located |

**Missing artefacts**:
1. PID→(DID, ECU) lookup table (not in strings file indexable section)
2. DID→(module, scaling) registry (micro record prefix decoder absent)
3. Gateway address resolver (0x792 vs 0x710 vs 18DAD0F1 — no unification logic found)

---

## E. Unsupported Assumptions (Must Not Enter Production)

1. **18-byte micro-record with DID at offset 16** — falsified by modulo-18 distribution.
2. **`vag.micro.vag` is a flat measurement table indexed by `vag.strings.vag`** — micro file has 3.3M indices, strings file has 2,440 indexable entries; most PIDs missing from indexable section.
3. **`P2AB800` maps to `0x2AB8` DID on Gateway** — string exists but no consumer; no trace to DID `0x2AB8`.
4. **Gateway = `0x710`/`0x77A`** — only in scan list; the only code-bound Gateway address is **11-bit `0x792`**.
5. **Gateway = `18DAD0F1`/`18DAD2F1`** — string heap label only; no consumer binds it to Gateway ECU.
6. **Micro record prefix bytes 8-11 encode MUL/OFS** — plausible but **no decoder found**.
6. **`vag.micro.vag` indices directly address `vag.strings.vag` records** — micro has 3.3M indices, strings indexable section has 2,440 entries; indices exceed range.

---

## F. Next Action

**Trace the `BuildList` / `GetECUName` address `0x792` through the transport layer** to confirm whether the live adapter uses 11-bit `0x792`/`0x79A` (resp = req+8) or remaps to `0x710`/`0x77A`. This is the only code-verified Gateway address.

**Artefacts to preserve**:
- `C:\Users\kings\AppData\Local\Temp\opencode\res_micro_vag.dec.shifted` (ShiftStream2-decoded micro)
- `C:\Users\kings\AppData\Local\Temp\opencode\res_strings_vag.dec.shifted` (ShiftStream2-decoded strings)
- `C:\Users\kings\AppData\Local\Temp\opencode\audit_*.py` (reproducible validation scripts)
- `C:\odb_scanner\docs\CAR_SCANNER_REVERSE_ENGINEERING.md` (full findings)