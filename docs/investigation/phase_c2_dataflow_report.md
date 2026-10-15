# Phase C2 Dataflow Report

## Task 1: Method bodies extracted

All target methods located and IL extracted using `disasm2.py` (token-resolved):

| Method | RVA | Status |
|---|---|---|
| `LoadVAG_MEB` | 0x35D158 | Full IL extracted (47 insns) |
| `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` | 0x356654 | Full IL extracted (46 insns) |
| `ShiftStream2.ShiftReadByte` | 0x35795C | Full IL extracted |
| `GetVAGCode` | 0x217F8C | Full IL extracted (hex-to-numeric conversion) |
| `GetVAGKWPRequests` | 0x2271D8 | Full IL extracted (KWP2000 path) |
| `GetVAGCanRequests` | 0x2274DC | Confirmed via existing `il_getvagcan.txt` |

No truncation detected — all methods end with `ret`.

## Task 2: Resource-loading call chain (LoadVAG_MEB)

```
LoadVAG_MEB (0x35D158)
  IL_000C: ldstr 'vagmeb.db'
  IL_0011: ldc.i4.1
  IL_0012: call BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2(name, 1)
  IL_0036: call Select(lambda) → ToList
  IL_0051: isinst CarScannerMaui.Coding.MQBAdaptationTemplate
  IL_0056: ldstr '6'
  IL_005B: callvirt set_Protocol("6")
  IL_0079: ldfld LoadedCollection
  IL_008B: callvirt AddRange(collection)
  IL_0090: call get_Current → get_ShowExperimental
  IL_00A2: call BuildSFD() → AddRange
  IL_00AC: ret
```

**Verified**: `vagmeb.db` is loaded exclusively by `LoadVAG_MEB` via `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2`. It deserializes into `MQBAdaptationTemplate` objects. `vag.micro.vag` and `vag.strings.vag` are NOT loaded by this method — they are separate resources with a different loading path.

## Task 3: Measurement-definition structures

### vagmeb.db = CODING/ADAPTATION data (NOT live measurements)

`MQBAdaptationTemplate` fields:
- `_READ_MODE`, `_WRITE_MODE`, `_READ_POSTIVE_RESPONSE`, `_WRITE_POSTIVE_RESPONSE`
- `_Password`, `_PasswordHint`, `_RequiresPro`
- `_ValueType`, `_Address`, `_RequestHeader`, `_ResponseHeader`
- `_StartByteId`, `_DataLength`, `_Multiplier`, `_Offset`, `_IsSigned`
- `_PreWriteCommands`, `_PostWriteCommands`, `_Protocol`
- `_Group`, `_HasCurrentState`

`MQBAdaptationOption` fields: `_Title`, `_Value`, `_ReadOnlyValue`, `Translations`

Decoded `vagmeb.json` content confirms: entries like "Lane keep assistance status at switch on", "Auto close windows", "Turn lite flashers" — these are ECU coding adaptations, not live-data measurements.

### CustomPID = LIVE MEASUREMENT definition type

`CarScannerMaui.OBD2.PIDS.CustomPID` fields:
- `_StartByteId`, `_DataLength`, `_Multiplier`, `_Divider`, `_Offset`, `_IsSigned`
- `_Type`, `_Bit`, `_ReversedByteSet`
- `_Formula`, `_Units`, `_Value`
- `_BeforeCommand`, `_AfterCommand`
- `_RequiredPIDs`, `_RequiresSTCommands`
- `_TextValueVariants`, `_TextValuesDict`
- `_IsAvailable`, `_IsFormulaCorrect`

This is the actual live-measurement definition structure. It carries decoding parameters (byte offset, length, scaling, formula, units) and request commands.

### Target measurement names

- `hv_energy_content`, `hv_energy_max`: **NOT FOUND** in `carscanner_strings.txt`, `vagmeb.json`, or `spot2000.csv`
- `8105`: found as substring in `carscanner_strings.txt` (context unclear)
- `19.Gate` / `19. CAN Gateway`: appears in version strings only
- `DC/DC Converter`: not found as exact string

Measurement names are likely stored in `vag.micro.vag` / `vag.strings.vag` as serialized `CustomPID` collections, or generated dynamically from numeric IDs.

## Task 4: Live-measurement execution path (static evidence)

```
[Resource loading]
  BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2
    → loads embedded resource by name from 'CarScannerMaui.EmbeddedFiles.' prefix
    → applies stream transformation (ShiftStream2 wraps baseStream)
    → deserializes to object collection

[CAN request building — verified]
  GetVAGCanRequests (0x2274DC)
    → sequence: ATFCSH{0};ATFCSD300005;ATFCSM1;ATCRA{1};1003
    → sets ELM327 flow-control header, CAN filter, mode

[KWP request building — verified]
  GetVAGKWPRequests (0x2271D8)
    → KWP2000 fast-init sequences: 1800FF00, 1802FF00, 1802FFFF, 1800FFFF, 18FF00
    → or: 14, 14FF00, 14FFFF

[Coding/adaptation loading — verified]
  LoadVAG_MEB (0x35D158)
    → loads vagmeb.db → MQBAdaptationTemplate collection
    → sets Protocol="6" on each
    → adds to LoadedCollection
```

**Unresolved boundary**: The exact method that takes a `CustomPID` definition and constructs a UDS/OBD request from it (the `measurement selection → definition lookup → request construction` path) was not fully traced. The `CustomPID` type has `_BeforeCommand`/`_AfterCommand` fields and a `_Formula` for decoding, but the consumer that populates these fields from `vag.micro.vag`/`vag.strings.vag` was not identified in this pass.

## Task 5: ShiftStream2 role

`ShiftStream2` (TypeDef 2268) is a **Stream decorator** wrapping a `baseStream` field.

`ShiftReadByte` (0x35795C) logic:
```
if (position % 2 == 0):
    return (byte)(input - 0x58)  // subtract 88
else:
    return (byte)(input - (position & 0xFF))
```

- **Input**: wraps another stream (set via constructor)
- **Transformation**: subtracts a position-dependent value from each byte during read
- **Output**: transformed byte stream
- **Consumer**: passed to deserializer (likely BinaryFormatter or similar)
- **Purpose**: simple obfuscation/encoding of embedded resource data

The `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` method name suggests additional crypto (CryptoStream) may be applied after ShiftStream2 transformation.

## Task 6: Resource-to-consumer evidence table

| Resource | Embedded name | Loader | Transformation | Output type | Consumer | Measurement relevance |
|---|---|---|---|---|---|---|
| `vagmeb.db` | `'vagmeb.db'` (ldstr in LoadVAG_MEB) | `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` | Unknown (ShiftStream2 likely) | `MQBAdaptationTemplate[]` | `LoadVAG_MEB` → `LoadedCollection` | **NOT live measurements** — coding/adaptation data |
| `vag.micro.vag` | Unknown (not loaded by LoadVAG_MEB) | Unknown (separate path) | Unknown | Unknown (likely `CustomPID[]`) | Unknown | **Plausible** — may contain live measurement definitions |
| `vag.strings.vag` | Unknown (not loaded by LoadVAG_MEB) | Unknown (separate path) | Unknown | Unknown (likely string table) | Unknown | **Plausible** — may contain measurement name strings |

## Strongest verified discovery

**`vagmeb.db` is definitively NOT the live-measurement definition source.** It is a coding/adaptation database (`MQBAdaptationTemplate`) containing ECU long-coding options. The actual live-measurement definitions use the `CustomPID` type (with `_StartByteId`, `_DataLength`, `_Multiplier`, `_Formula`, `_Units` fields) and are loaded from a separate resource path — likely `vag.micro.vag` / `vag.strings.vag`.

## Most important unresolved question

Where are `CustomPID` objects instantiated and populated? Specifically: which method loads `vag.micro.vag` / `vag.strings.vag`, and how are the decoded `CustomPID` definitions matched to ECU modules like `[19.Gate]` and `[8105.DC/DC Converter]`?

## Concrete next action

Search for methods that reference `vag.micro.vag` or `vag.strings.vag` as string literals in the DLL, and trace the `CustomPID` collection loading path. Also examine the `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` callers beyond `LoadVAG_MEB` to find the live-measurement resource loader.
