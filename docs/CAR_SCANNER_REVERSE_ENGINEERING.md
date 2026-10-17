# Car Scanner (MEB) Reverse-Engineering Findings

**Date**: 2026-10-14  
**Revised**: 2026-10-15 — resource-classification audit (see §14)  
**Author**: Automated extraction from `C:\Users\kings\OneDrive\Documents\Car Scanner` (extracted Android APK)  
**Target Vehicle**: VW ID.3 (MEB platform), VIN `WVWZZZE1ZMP087053`

> **Correction notice (2026-10-15).** An earlier revision of this document classified
> `vag.micro.vag` as the live-measurement DID catalogue and derived 551,991 records /
> 45,033 unique DIDs from it. **That classification was wrong.** `vag.micro.vag` holds
> localized DTC *text* records; the live-measurement definitions live in
> `profiles7.db`. The record format, the 45,033 figure, and the affected sections (§5,
> §8.3, §9, §12) have been corrected. Both figures are now fully explained — see
> §14.3. No prior finding that does not depend on the misclassification is discarded.

---

## Executive Summary

Car Scanner is a **.NET MAUI** Android app. Its MEB logic and embedded databases live inside an **XABA assembly store** (`libassemblies.arm64-v8a.blob.so`). We extracted **206 managed assemblies**, decrypted the embedded resources (LZMA + custom "shift" cipher), and recovered:

1. **Module address tables** (two formats: `17FCxxxx` VAG 29-bit, and `18DAxxxx` ISO-15765 normalized)
2. **The exact ELM327 command sequence** Car Scanner uses for VAG UDS
3. **All target DIDs** for the readings our project wants (`hv_energy_content`, `hv_energy_max`, pack voltage, SoC, cell voltages, DC-DC, climate) — from **`profiles7.db`**
4. **Resource formats**: LZMA-compressed + ShiftStream2 cipher (`profiles7.db`, `vagmeb.db`, `vag.micro.vag`, `vag.strings.vag`)

The four embedded databases do four different jobs. Classifying them correctly matters,
because the original extraction read the wrong one:

| Resource | Actual role | Consumer |
|----------|-------------|----------|
| `profiles7.db` | **Live measurement definitions** (1,323 profiles; MEB = index 342, 308 `CustomPID`) | `ProfileV2Model.LoadProfiles` → `OBDReaderProfileV2.ImportProfilePids` |
| `vagmeb.db` | MEB **coding / adaptation** (long coding) | `CodingListModel` |
| `vag.micro.vag` | **Localized DTC text** (862 containers, 531,345 `VagMicroItem`) | `ad.cs` → `LoadMicroContainers` |
| `vag.strings.vag` | **DTC text string table** the two above index into | `BinaryReader.ReadString` at byte offsets |

---

## 1. App Architecture & Extraction

### 1.1 Input Artifact
```
C:\Users\kings\OneDrive\Documents\Car Scanner\lib\arm64-v8a\libassemblies.arm64-v8a.blob.so
```
- ELF wrapper around XABA .NET assembly store
- 206 managed assemblies extracted (all valid PE)
- Key assembly: **`CarScannerMaui.dll` (≈38 MB)** — contains VAG/MEB logic + embedded resources

### 1.2 Extraction Tooling
```bash
# XABA extractor (port of BishopFox/mauidll)
python extract_store.py libassemblies.arm64-v8a.blob.so carsccanner_dlls/

# Embedded resources accessed via dnfile
pe.net.resources  # 510 InternalResource objects
```

### 1.3 Embedded Resources (Key)
| Resource | Size | Format | Content |
|----------|------|--------|---------|
| `CarScannerMaui.EmbeddedFiles.profiles7.db` | 10,032,980 B | LZMA + ShiftStream2 → BSON | **Live measurement definitions** — 1,323 `OBDReaderProfileV2`; MEB = index 342, 308 `CustomPID` records |
| `CarScannerMaui.EmbeddedFiles.vagmeb.db` | 26,992 B | LZMA + ShiftStream2 → BSON | MEB **coding** DB (44 entries) |
| `CarScannerMaui.EmbeddedFiles.vag.micro.vag` | 6,204,318 B | LZMA + ShiftStream2 → count-prefixed containers | **Localized DTC text** — 862 containers, 531,345 `VagMicroItem` |
| `CarScannerMaui.EmbeddedFiles.vag.strings.vag` | 3,222,110 B | LZMA + ShiftStream2 → offset-indexed strings | DTC description strings the micro records point at |

---

## 2. Decryption Pipeline

### 2.1 LZMA Decompression
All three resources are **LZMA-compressed** (standard `lzma.decompress()` works, format=FORMAT_ALONE).
- Magic header: `5d 00 00 80 00` (LZMA properties: dict size 8 MB)

### 2.2 ShiftStream2 Cipher
After LZMA, a custom byte-wise cipher (`CarScannerMaui.Common.ShiftStream2`):

```csharp
// ShiftReadByte(value, position)
if (position % 2 == 0)       // even offset
    value = (byte)(value - 0x58 - (byte)position);   // 0x58 = 88
else                         // odd offset
    value = (byte)(value + 0x45 + (byte)position);   // 0x45 = 69
return value;
```

**Python decoder**:
```python
def shift_decode(data, even_sub=0x58, odd_add=0x45):
    out = bytearray(len(data))
    for p, x in enumerate(data):
        pb = p & 0xFF
        if p % 2 == 0:
            out[p] = (x - even_sub - pb) & 0xFF
        else:
            out[p] = (x + odd_add + pb) & 0xFF
    return bytes(out)
```

### 2.3 Decrypted Outputs
| File | Decrypted Size | Format |
|------|---------------|--------|
| `profiles7.db` | 75,975,218 B | BSON array of 1,323 `OBDReaderProfileV2` (declared length == buffer length) |
| `vagmeb.db` | 26,992 B | BSON (44 coding entries) |
| `vag.micro.vag` | 10,009,474 B | Count-prefixed containers → 862 containers, 531,345 `VagMicroItem` |
| `vag.strings.vag` | 7,214,595 B | Byte-offset-indexed DTC description strings |

---

## 3. `vagmeb.db` — MEB Coding Database (BSON)

44 entries, keyed `"0"`..`"43"`. Each entry:

```json
{
  "OPTS": [{"TTL":"Off","VL":"00","ROVL":"","Translations":[]}, ...],
  "UID": 10001,
  "NM": "Lane keep assistance status at switch on",
  "DSC": "", "InnerDescription": "",
  "PWD": "20103", "PWH": "", "TRNS": [],
  "VT": 0,
  "AD": "09A1",          // DID address
  "RQH": "74F",          // Request header (3 hex = 11-bit)
  "RSH": "7B9",          // Response header (3 hex = 11-bit)
  "SBI": 0,
  "DL": 1,               // Data length
  "MUL": 1.0, "OFS": 0.0, // Scale / offset
  "SIG": false, "RP": false, "Group": 1, "HasCurrentState": true
}
```

**Key insight**: This is **coding/adaptation** data (long coding), NOT live sensor readings. Uses **11-bit headers** (`RQH`/`RSH` like `74F`/`7B9`).

---

## 4. Module Address Tables

### 4.1 VAG 29-bit Table (`17FCxxxx` / `17FExxxx`)
Recovered from string heap (80 entries). Format: **name precedes its address pair**.

| Code | 17FC (req) | 17FE (resp) | Module Name |
|------|-----------|-------------|-------------|
| `008C` | `17FC007B` | `17FE007B` | Battery management system (BMS) ✓ |
| `8105` | `17FC00B9` | `17FE00B9` | DC Charger ✓ |
| `0051` | `17FC007C` | `17FE007C` | Electro-engine |
| `0701` | `17FC1701` | `17FE1701` | Rear A/C control head |
| `070D` | `17FC170D` | `17FE170D` | Electric compressor |
| `06C0` | `17FC16C0` | `17FE16C0` | Coolant heating |
| `060B` | `17FC160B` | `17FE160B` | Fresh air blower control - front |
| `0637` | `17FC1637` | `17FE1637` | Battery monitoring |
| `06D1`..`06DC` | `17FC16D1`.. | `17FE16D1`.. | Battery cell control modules 1–12 |
| `8113` | `17FC00C6` | `17FE00C6` | HV Battery charging unit 2 |
| `8114` | `17FC00C7` | `17FE00C7` | HV converter 2 |

**Confirmed on hardware**: BMS `008C` → `17FC007B`/`17FE007B` responds with `62 02 8C B2` (SoC 71.2%).

**Critical gap**: **Gateway (`19.Gate`) is NOT in this table**.

### 4.2 Normalized 29-bit Table (`18DAxxxx`)
Different region of string heap, ISO-15765-4 format `18DA<target><source>`:

| Name | 18DA ID | Notes |
|------|---------|-------|
| SSG | `18DA14F2` | |
| CHADeMo | `18DAD7F2` | Charging protocol |
| **GATEWAY** | `18DAD0F1` | **Gateway = target 0xD0** |
| S-GW3 | `18DAD2F1` | |
| S-GW4 | `...` | |

---

## 5. Live Measurement DIDs (from `profiles7.db`)

> **Source corrected 2026-10-15.** This section previously read its DIDs out of
> `vag.micro.vag`. That resource is localized DTC text (§5.4); it contains no DIDs
> and no scale factors. The definitions below are read from the MEB profile's
> `ProfilePIDs` JSON blob in `profiles7.db`.

### 5.1 Record Format

Each measurement is a `CustomPID` record in the profile's `ProfilePIDs` JSON array.
Verified counts: **1,323** profiles; MEB profile at **index 342**
(`Name = "EV MEB: ID.3, ID.4, ID.5, ID.6, ID.7, ID.Buzz, Enyaq, Elroq, Q4 E-tron,
Q6 e-tron, Cupra B..."`); **308 `CustomPID`** records referencing **254 distinct
`0x22` DIDs**.

Fields that matter for decoding a DID (record `Id` 12935009 shown as example):

```json
{
  "CMD": "222AB8",              // the request on the wire: service 22 + DID
  "SBI": 2,                     // start byte offset into the response payload
  "DL": 2,                      // data length in bytes
  "MUL": 25.0, "DIV": 1.0,      // scaling:  value = raw * MUL / DIV + OFS
  "OFS": 0.0, "SIG": false,     // signedness
  "TP": 1,                      // 1 = ByteSet (fixed field), 0 = Formula
  "RBS": false,                 // byte order: false = big-endian, true = LE
  "FR": "...",                  // formula text (TP=0 only)
  "BCM": "ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A",   // per-module setup
  "ACM": "ATFCSM0;ATAR;ATSP7",
  "NM": "[19.Gate] HV Battery energy content",
  "UN": 61                      // unit enum
}
```

Across the 308 records: **250 `TP=1` (ByteSet)**, **56 `TP=0` (Formula)**,
**0 `RBS=true`** — i.e. every MEB record in this build is big-endian.

### 5.2 Target DIDs Found

| Reading | DID | Car Scanner Module Tag | Our Module Key | 29-bit (17FC) | 11-bit (ATSP6) | 18DA |
|---------|-----|------------------------|----------------|---------------|----------------|------|
| SoC (abs) | `0x028C` | `[8C.BMS]` | `bat_mgmt` | `17FC007B`/`17FE007B` | `0x7E5`/`0x7ED` | — |
| Pack Voltage | `0x1E3B` | `[8C.BMS]` | `bat_mgmt` | `17FC007B`/`17FE007B` | `0x7E5`/`0x7ED` | — |
| HV Energy Content | `0x2AB8` | `[19.Gate]` | **`energy`** | **N/A** | **`0x710`/`0x77A`** | `18DAD0F1` |
| HV Energy Max | `0x2AB2` | `[19.Gate]` | **`energy`** | **N/A** | **`0x710`/`0x77A`** | `18DAD0F1` |
| Min SoC | `0xF50E` | `[8C.BMS]` | `bat_mgmt` | `17FC007B` | `0x7E5` | — |
| DC-DC Current | `0x465B` | `[8105.DC/DC]` | `dcdc` | `17FC00B9`/`17FE00B9` | `0x777` | — |
| DC-DC Voltage | `0x465D` | `[8105.DC/DC]` | `dcdc` | `17FC00B9`/`17FE00B9` | `0x777` | — |

**Key finding**: `hv_energy_content`/`hv_energy_max` live on **Gateway (`19.Gate`)**
with DIDs `0x2AB8`/`0x2AB2`. The Gateway is addressed as:
- **11-bit**: `0x710` (req) / `0x77A` (resp) — confirmed by the `BCM`/`ACM` fields of
  both gateway records in `profiles7.db`, not only by the module scan list
- **18DA normalized**: `18DAD0F1` (req) / `18DAD2F1` (resp) — target `0xD0`

**Gateway scaling (verified, and it contradicts our decoder — see §14.5):**

| DID | Name | SBI | DL | MUL | DIV | OFS | Formula |
|-----|------|-----|----|-----|-----|-----|---------|
| `0x2AB8` | `[19.Gate] HV Battery energy content` | 2 | 2 | **25.0** | 1.0 | 0.0 | `u16be(payload[2:4]) × 25` Wh |
| `0x2AB2` | `[19.Gate] Maximum energy content of the traction battery` | 0 | 2 | **50.0** | 1.0 | 0.0 | `u16be(payload[0:2]) × 50` Wh |

Both use `BCM = ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A` and `UN = 61` (Wh).
These are two *related* measurements (current vs maximum) of the same quantity from the
same ECU — not alternate encodings of one another.

### 5.3 What `vag.micro.vag` actually contains

For completeness, the resource the earlier revision misread. Verified schema from
`CarScannerMaui.DTC.VagDTC.VagMicroItem.ReadFromStream` and `ad.cs`:

The stream is **not** a flat array of fixed-width records. It is a count-prefixed
sequence of containers, all integers 3-byte little-endian
(`IntFrom3Byte = b2*65536 + b1*256 + b0`):

```
int32_3  containerCount                       = 862
repeat containerCount times:
    byte      flag                            (1 = one)
    int32_3   nameCount;  nameCount   × int32_3   (project names  -> strings)
    int32_3   asamCount;  asamCount   × int32_3   (ASAM labels    -> strings)
    int32_3   dtcCount;   dtcCount    × 18 bytes    (VagMicroItem)
```

Each `VagMicroItem` is 6 × 3-byte LE ints = **18 bytes**, and offset 16 is *not* a DID:

| Offset | Field | Meaning |
|--------|-------|---------|
| 0–2 | `VagCode` | VAG fault code |
| 3–5 | `U` | universal description → `vag.strings.vag` |
| 6–8 | `RU` | Russian description |
| 9–11 | `DE` | German description |
| 12–14 | `EN` | English description |
| 15–17 | `S` | symbol / prefix string |

The four text fields are **byte offsets into `vag.strings.vag`**, not values. Verified:
100.0% of all `U`, `RU`, `DE`, `EN` offsets (531,345 each) and 99.94% of `S` offsets
(531,039) fall inside the 7,214,595-byte decoded strings table. Resolving them yields
fault descriptions, e.g. `VagCode 0x000A3C` → *"EGR vacuum regulator Solen.val., Short circuit"*.

That is what classifies the resource: it is **localized DTC text**. `ad.cs` selects the
language at runtime (`ad.cs:176-181`) and builds a display string from `RU`/`DE`/`EN`/`U`/`S`.

**Validated counts**: 862 containers, **531,345** `VagMicroItem`, **0 trailing bytes**
consumed — the parse lands exactly on the end of the 10,009,474-byte buffer, which is
what a correct schema should do and a fixed 18-byte grid does not (it would leave 16).

---

## 6. Strings File (`vag.strings.vag`)

> **Reclassified 2026-10-15.** This section previously described the resource as a
> flat list of ~900k `P/U`-prefixed PID codes and treated those codes as measurement
> names. It is not a PID table. It is the **DTC description string table**, indexed by
> **byte offset**, and every offset in it resolves to fault text (§5.3).

Decoded size: **7,214,595 bytes**. The consumers do not scan it for records — they seek:

```csharp
// ad.cs:227-231
private static string f(int a, BinaryReader br) {
    br.BaseStream.Seek(a, SeekOrigin.Begin);
    return br.ReadString();      // .NET 7-bit-length-prefixed UTF-8
}
```

The `P/U`-looking byte runs (`P310B00`, `P2AB800`, …) are **fault-code text inside the
strings**, not structured PID identifiers:

- `P2AB800` = `P` + `2AB8` + `00` — this is Car Scanner's **legacy encoding of the DID
  0x2AB8** appearing inside a DTC description string. It is not a separate definition and
  not a profile record.
- `P310B00` / `P308900` are likewise DTC-code substrings, not `CustomPID` entries.

The authoritative display names for measurements are the `NM` / `SNM` fields of the
`CustomPID` records in `profiles7.db` (§5.1), not this resource.

---

## 7. Exact ELM327 Command Sequence

> **Corrected 2026-10-15.** This was previously attributed solely to
> `GetVAGCanRequests` (RVA `0x2274DC`) and gave `ATFCSD300005`. The authoritative
> per-module setup is the `BCM` / `ACM` fields of the `CustomPID` records themselves,
> which are data rather than disassembly. For both gateway DIDs they read:

```
ATSP6                    # ISO 15765-4 CAN 11-bit
ATFCSH710                # flow-control header = request id 0x710
ATFCSD300000             # flow control: BS=0, STmin=0
ATFCSM1                  # user-defined flow control
ATCRA77A                 # receive filter = response id 0x77A
-- then --
22 2AB8  /  22 2AB2      # ReadDataByIdentifier
```

**Correction:** the STmin byte is `00`, not `05` — the profile says `ATFCSD300000`.

### Module Scan List (11-bit IDs)
`GetVAGCanRequests` (RVA `0x2274DC`) iterates these request IDs:
```
7E0 7E1 713 732 74D 746 70E 770 70A 7E2 772 715 70C 714 76A 716 728 70F 73B
711 72D 71A 71E 755 74C 76C 74E 72C 74A 712 76F 74B 754 76D 7E6 752 773 70B
747 769 6B8 723 745 71D 767 70A 76B 75A 74F 6BC 784 710 757 7E5 762 765 744
```

**Gateway is `0x710` / `0x77A`** — confirmed both here and, independently, by the
`BCM` field of the 0x2AB8 / 0x2AB2 records (§5.2).

**The 29-bit modules use the same form (2026-10-15 capture).** The same capture's
29-bit phase is, verbatim:

```
ATSHFC007B               # request id 0x17FC007B
ATSP7                    # ISO 15765-4 CAN 29-bit
ATCP17                   # priority byte 0x17
ATCRA17FE007B            # receive filter = response id
ATFCSH17FC007B           # flow-control header = the FULL 8-digit request id
ATFCSD300000             # flow control: BS=0, STmin=0
ATFCSM1                  # user-defined flow control
-- then --
221E3B                   # bare ReadDataByIdentifier -> 17FE007B05621E3B0690
```

Note two things the code got wrong for years: the flow-control header is the
**8-digit** request id (`ATFCSH17FC007B` — accepted, even though an 8-digit
`ATSH` is refused), and there is **no `ATCAF0`** — the request is auto-formatted
by the adapter. `diagnostic/elm327.py` `set_module()` now implements exactly
this for both addressing widths.

---

## 8. Integration into odb_scanner

### 8.1 Module Registry Updates (`diagnostic/ecus.py`)

```python
# Gateway / Energy (HV energy content, SoH)
ECUSpec(
    "energy", "Gateway energy information", 0x710, 0x77A,
    doc_status="experimentally determined (Car Scanner reverse-engineering)",
    notes="MEB Gateway (19.Gate): HV energy content (0x2AB8/0x2AB2), SoH source. "
          "Uses 11-bit 0x710/0x77A with Car Scanner's exact flow control: "
          "ATFCSH 710; ATFCSD 300000; ATFCSM 1; ATCRA 77A; then 22 2AB8 / 22 2AB2. "
          "Also reachable via 18DA normalized: 18DAD0F1/18DAD2F1.",
    tx29=0x18DAD0F1, rx29=0x18DAD2F1,
),

# Climate modules (from 17FC table)
ECUSpec("climate_rear_ac", "Rear A/C control head", 0x0000, 0x0000,
        notes="MEB module 0x0701: Rear A/C control head. 29-bit 17FC1701/17FE1701.",
        tx29=0x17FC1701, rx29=0x17FE1701),

ECUSpec("coolant_heating", "Coolant heating", 0x0000, 0x0000,
        notes="MEB module 0x06C0: Coolant heating. 29-bit 17FC16C0/17FE16C0.",
        tx29=0x17FC16C0, rx29=0x17FE16C0),

ECUSpec("fresh_air_blower", "Fresh air blower front", 0x0000, 0x0000,
        notes="MEB module 0x060B: Fresh air blower. 29-bit 17FC160B/17FE160B.",
        tx29=0x17FC160B, rx29=0x17FE160B),
```

### 8.2 Transport Changes (`diagnostic/elm327.py`)

**Implemented.** `set_module(tx29, rx29)` sends Car Scanner's preamble for every
physical module — 11-bit and 29-bit alike — then leaves the request bare:

```python
# 11-bit (energy 0x710, climate 0x746, gps 0x767)
ATSP6; ATSH 710; ATFCSH 710; ATFCSD 300000; ATFCSM 1; ATCRA 77A; ATCAF1
# 29-bit (BMS 0x17FC007B, DC/DC 0x17FC00B9, ...)
ATSP7; ATCP 17; ATSH FC007B; ATCRA 17FE007B;
ATFCSH 17FC007B; ATFCSD 300000; ATFCSM 1; ATCAF1
# then a bare '22 XXXX' -- the adapter frames it
```

The earlier sketch here (a gateway-only `set_module_car_scanner_gateway`) was
generalised, and the `ATCAF0` hand-framed line was removed entirely: the clone
refuses it with `?` (see `docs/MEB_NO_RESPONSE_HANDOFF.md` §1.2, §1.3).

### 8.3 Decoder Additions (`decoders/meb.py`)

Scale factors come from the `MUL` / `DIV` / `OFS` / `SBI` / `DL` fields of the
`CustomPID` record in `profiles7.db` — **not** from `vag.micro.vag` (§5.1).

| DID | Key | Scale from `profiles7.db` | Unit | Notes |
|-----|-----|--------------------------|------|-------|
| `0x2AB8` | `hv_energy_content` | `SBI=2 DL=2 MUL=25 DIV=1` → `u16be(payload[2:4]) × 25` | Wh | Gateway — **conflicts with the decoder currently in `meb.py`, see §14.5** |
| `0x2AB2` | `hv_energy_max` | `SBI=0 DL=2 MUL=50 DIV=1` → `u16be(payload[0:2]) × 50` | Wh | Gateway — same conflict |
| `0xF50E` | `soc_min` | `/ 2.5` | % | BMS (MinSoC) |
| `0x465B` | `dcdc_current` | read `MUL`/`DIV`/`SBI` from the record | A | DC/DC |
| `0x465D` | `dcdc_voltage` | read `MUL`/`DIV`/`SBI` from the record | V | DC/DC |

`RBS=false` on all 308 records, so every MEB field is **big-endian**.

**Scale/offset extraction is now a lookup, not a decode.** The earlier hypothesis here —
that bytes 8–11 of the micro file encode fixed-point `MUL`/`OFS` — is **withdrawn**: those
bytes belong to the `DE` and `EN` string offsets of a `VagMicroItem` (§5.3), so they carry
no scaling information.

---

## 9. Climate Readings (Next Target)

From 17FC table, climate-related modules:

| Code | 29-bit Req/Resp | Module Name |
|------|-----------------|-------------|
| `0701` | `17FC1701`/`17FE1701` | Rear A/C control head |
| `06C0` | `17FC16C0`/`17FE16C0` | Coolant heating |
| `060B` | `17FC160B`/`17FE16B0` | Fresh air blower front |
| `070D` | `17FC170D`/`17FE170D` | Electric compressor |

**Action (revised)**: search the **308 `CustomPID` records of the MEB profile in
`profiles7.db`** for `NM` values naming these subsystems, and read each record's `BCM`
field for its 29-bit addressing. Do **not** search `vag.micro.vag` -- it holds DTC text
and contains no climate DIDs (see 5.3).

---

## 10. Acceptance Test Updates

Run `tools/collect_acceptance.py` — it should now pass for:
- `hv_energy_content` (Wh) — from Gateway `0x710` + `22 2AB8`
- `hv_energy_max` (Wh) — from Gateway `0x710` + `22 2AB2`
- Climate temperatures — from 29-bit climate modules

The two gateway figures depend on which scale the decoders use. That is itself
unresolved (§14.5), so these two acceptance checks are not expected to pass until it is.

---

## 11. Reproducible Artifacts (on Disk)

Primary working set is now `C:\Users\kings\Documents\CarScanner_Investigation`:

| Path | Description |
|------|-------------|
| `resources\CarScannerMaui.EmbeddedFiles.profiles7.db` | **Raw** live-measurement DB (10,032,980 B, sha256 `4c6737ec…fb94b`) |
| `resources\CarScannerMaui.EmbeddedFiles.vag.micro.vag` | **Raw** DTC-text DB (6,204,318 B, sha256 `39b19d02…bd36c7`) |
| `resources\CarScannerMaui.EmbeddedFiles.vag.strings.vag` | **Raw** DTC string table (3,222,110 B) |
| `resources\CarScannerMaui.EmbeddedFiles.vagmeb.db` | **Raw** MEB coding DB (26,992 B) |
| `tools_cs\bson_profiles.py` | `unshift()` + BSON walker; reproduces §5.1 |
| `live_measurement_evidence.csv` | All 308 MEB measurements with resolved fields |
| `live_measurement_pipeline.md` | Fuller write-up of the `profiles7.db` pipeline |
| `full_decomp\CarScannerMaui.DTC.VagDTC\VagMicroItem.cs` | The 18-byte `VagMicroItem` schema (§5.3) |
| `full_decomp\ad.cs` | `LoadMicroContainers` — the `vag.micro.vag` consumer |
| `original_1.apk` | Source APK (76,291,057 B) |

Decoded intermediates (`.lzma.out`, `.bson`, `.dec`) are **reproducible** from the four
raw resources with `lzma.decompress(..., FORMAT_ALONE)` followed by `unshift()`; they need
not be retained.

---

## 12. Open Questions / Next Steps

1. **Gateway addressing — RESOLVED (16 Oct).** `energy` (11-bit `0x710`) and `climate`
   (11-bit `0x746`) now answer: the fix was `ATCAF1` + a bare request + the flow-control
   preamble (`ATFCSH`/`ATFCSD`/`ATFCSM 1`), not the hand-framed `ATCAF0` 8-byte line
   (which the clone refused with `?`). See `docs/MEB_NO_RESPONSE_HANDOFF.md` §1.2.
   `energy` is `0x710→0x77A` (the competing `gate` `0x792→0x710` entry was wrong and has
   been corrected). The `0x2AB2`/`0x2AB8` scale question (item 2) is now answerable on
   live data.
2. **Then resolve the gateway scale** — `profiles7.db` says `0x2AB8` is `u16be × 25`;
   `decoders/meb.py` divides a u32 by 1310.77. Downstream of item 1.
3. ~~Decode scale/offset from micro file record prefixes~~ — **closed; hypothesis
   withdrawn** (§8.3). Scaling is explicit in the `CustomPID` records.
3. **Which Gateway variant** (0x09A1 vs 0x09A2) maps to `[19.Gate] HV Battery energy
   content`. Largely answered: both gateway records name `[19.Gate]` in `NM` and pin
   `ATFCSH710` / `ATCRA77A`.
4. **Climate DIDs**: search the MEB profile's 308 `CustomPID` records (§9).
5. **18DA vs 17FC for Gateway**: Test both `18DAD0F1` and `11-bit 0x710` on hardware —
   Car Scanner uses both paths.
6. **Flow control necessity — answered (16 Oct).** The `ATFCSH/ATFCSD/ATFCSM1` preamble
   is required for the 11-bit modules: it is Car Scanner's wire form, and it is what the
   ELM327 needs to reassemble the gateway's multi-frame replies. A further quirk: `ATFCSM 1`
   must be reset (`ATFCSM 0`) before returning to a 29-bit module, or the clone refuses the
   next 29-bit request (see `MEB_NO_RESPONSE_HANDOFF.md` §9.5).
7. **Build drift**: an earlier build's `profiles7.db` was 9,919,996 B; this one is
   10,032,980 B. The MEB `ProfilePIDs` blob measures 171,033 B here against 171,289 B
   quoted in the external investigation — a different build, not a parsing error.
   Re-diff at the `CustomPID` level before trusting either for scale factors.

---

## 13. Quick Reference Commands

```bash
# Live-measurement pipeline (profiles7.db -> BSON -> OBDReaderProfileV2)
python tools_cs/bson_profiles.py \
    resources/CarScannerMaui.EmbeddedFiles.profiles7.db profiles7.bson
# then read doc["342"]["ProfilePIDs"] and json.loads() it  -> 308 CustomPID

# Disassemble key methods
python disasm3.py CarScannerMaui.dll 2274dc   # GetVAGCanRequests
python disasm3.py CarScannerMaui.dll 356654   # BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2
python disasm3.py CarScannerMaui.dll 35795c   # ShiftStream2.ShiftReadByte
```

---

## 14. Resource-Classification Audit (2026-10-15)

This section records how the classification above was established, so a reviewer can
re-derive it rather than trust it. Scope was documentation only: **no diagnostic
transmission behaviour was modified and no vehicle operation was performed.**

### 14.1 Source files relied on

| Artifact | Role |
|----------|------|
| `full_decomp\CarScannerMaui.DTC.VagDTC\VagMicroItem.cs` | Defines the 18-byte `VagMicroItem` and `IntFrom3Byte` |
| `full_decomp\ad.cs` (`LoadMicroContainers`, RVA `0x22b5c4`) | The `vag.micro.vag` consumer; defines the container framing |
| `ad.cs:176-181` (`d(VagMicroItem)`) | Picks `RU`/`DE`/`EN` by `App.CurrentLanguageCode` — DTC text |
| `ad.cs:207-231` (`e` / `f`) | `Seek(offset)` + `ReadString()` into `vag.strings.vag` |
| `full_decomp\ac.cs` | `List<VagMicroItem> h` — the container's event/DTC list |

### 14.2 The verified `vag.micro.vag` schema

```
int32_3  containerCount                                  -> 862
repeat containerCount:
    byte      flag
    int32_3   n;  n x int32_3     project name offsets  -> strings
    int32_3   n;  n x int32_3     ASAM label offsets     -> strings
    int32_3   n;  n x 18 bytes    VagMicroItem records
```
`int32_3` = `b2*65536 + b1*256 + b0` (little-endian).

**Result: 862 containers, 531,345 `VagMicroItem`, 10,009,474 / 10,009,474 bytes consumed,
0 trailing.** A fixed 18-byte grid instead yields 556,081 slots and 16 trailing bytes.

Offset 16 is **not** a DID: within a `VagMicroItem`, bytes 15–17 are the field `S`, so
bytes 16–17 are the low two bytes of that field's string offset. The DID-like reading was
an artifact of a grid that does not match the real record boundaries.

### 14.3 The 45,033 figure — explained

Both original figures reproduce **exactly** under the flat-18-byte method:

```python
buf   = unshift(lzma.decompress(raw_micro, format=lzma.FORMAT_ALONE))  # 10,009,474 B
slots = len(buf) // 18                                                # 556,081
vals  = [buf[i*18 + 16] | (buf[i*18 + 17] << 8) for i in range(slots)] # LE u16
nz    = [v for v in vals if v != 0]
assert len(nz)      == 551991   # matches the original "551,991 non-zero records"
assert len(set(nz)) ==  45033   # matches the original "45,033 unique DIDs"
```

So the counting method is **recovered**, not reconstructed. But the interpretation was
wrong: on a grid that ignores real record boundaries, offset 16 samples fragments of the
`S` string field of unrelated records. **45,033 is the number of distinct low-2-byte
fragments of a DTC-text string offset, not a count of DIDs.**

### 14.4 Why `profiles7.db` is authoritative

Redone end-to-end from the raw resource, not from a cached intermediate:

```
raw  10,032,980 B  sha256 4c6737ecbe03c284fd2b87b6917400880c75cd9518d662082e7fbb7bc72fb94b
      head 5d 00 00 80 00              (LZMA-Alone)
LZMA 75,975,218 B
unshift          75,975,218 B
BSON declared length 75,975,218 == buffer length   (exact)
root element type 0x03 (array)  ->  1,323 OBDReaderProfileV2
```

Index 342: `Name = "EV MEB: ID.3, ID.4, ID.5, ID.6, ID.7, ID.Buzz, Enyaq, Elroq,
Q4 E-tron, Q6 e-tron, Cupra B..."`, `ProfileHasPids = True`,
`ProfilePIDs` = 171,033-byte JSON → **308 `CustomPID`**, **254 distinct `0x22` DIDs**,
250 `TP=1` / 56 `TP=0`, 0 `RBS=true`.

These records carry `CMD`, `SBI`, `DL`, `MUL`, `DIV`, `OFS`, `BCM`, `ACM` and `NM` — i.e.
exactly the fields a decoder needs, and exactly what `vag.micro.vag` does not contain.

### 14.5 The gateway blocker — addressing, not scale

`profiles7.db` and `decoders/meb.py` disagree on the gateway scale. This audit did **not**
change the code.

| | 0x2AB8 `hv_energy_content` | 0x2AB2 `hv_energy_max` |
|---|---|---|
| `profiles7.db` (verified) | `SBI=2 DL=2 MUL=25 DIV=1` → `u16be × 25` Wh | `SBI=0 DL=2 MUL=50 DIV=1` → `u16be × 50` Wh |
| `decoders/meb.py` (current) | `u32 / 1310.77` (marked ASSUMED, UNVERIFIED) | `u32 / 1310.77` |

Structurally different — 2-byte big-endian with a start offset versus a 4-byte divisor.

**But the scale question cannot be settled yet, because the gateway does not answer at
all on this vehicle.** From `data/real_car.db` `tx_log` (read-only, 2026-10-15):

| ECU | addressing | RX total | no-data (`?`) | answered |
|-----|-----------|---------:|--------------:|---------:|
| `bat_mgmt` | 29-bit `0x17FC007B` | 3,506 | 0 | **3,506** |
| `chg` | 11-bit `0x744` | 211 | 0 | 211 |
| `chg_mgmt` | 11-bit `0x765` | 133 | 0 | 133 |
| `veh_info` | 29-bit `0x17FC0076` | 195 | 114 | 81 |
| `dcdc` | 29-bit `0x17FC00B9` | 77 | 74 | 3 |
| **`energy`** | **11-bit `0x710`** | **114** | **114** | **0** |
| **`climate`** | **11-bit `0x746`** | **77** | **77** | **0** |

Every `energy` and `climate` request returned `?` — **no bytes and no NRC**. A refusal
would have appeared as `7F2231`; there are none for these modules. The 29-bit modules work
(3,506/3,506 for the BMS), so the transport is healthy; it is these two 11-bit
addresses that never answer.

This matters for how the conflict should be read: **the scale is downstream of the
addressing.** No scale can be validated against a DID the car does not answer, so the
scale question is currently unanswerable rather than merely unverified.

Note also that `0x2AB2` cannot discriminate the two scales even in principle on synthetic
data: the simulator frame `622AB204280A64` yields **53,200 Wh under both**
(`u16be(0x0428) × 50 = 53,200` and `u32 / 1310.77 = 53,200`), because it was constructed
from the evDash constant. `0x2AB8` does discriminate (`41,200` vs `51,500 Wh`) — but only
in `data/diag_smoke.db`, which is the **built-in simulator**, not the car. Those bytes are
circular evidence derived from the very assumption under test.

#### 14.5.1 Leading hypothesis — `ecus.py` has two competing gateway entries

`diagnostic/ecus.py` defines both, and they disagree about direction:

| key | tx | rx | status | bound to the decoders? |
|-----|----|----|--------|------------------------|
| `gate` | `0x792` | `0x710` | `inferred (not yet validated on this vehicle)` | no |
| `energy` | `0x710` | `0x77A` | `experimentally determined (evDash/ABRP MEB community)` | **yes** — `decoders/meb.py` uses `ECU_ENERGY` |

The `gate` entry's own notes argue the `energy` mapping is backwards: Car Scanner's
records declare `0x710` as the **response** header (`ATFCSH710`), and `ATCRA77A` is
`0x710 + 0x6A`, not `0x710 + 8` — a Car Scanner-specific encoding, not a CAN response id.
On that reading the gateway *listens* on `0x792` and *answers* on `0x710`. The `energy`
entry instead **sends to `0x710`** — a response id — which would explain silence.

This is a hypothesis, not a finding: no read to `0x792` has been attempted in the logged
history. Settling it needs one authorised read-only probe of `22 2AB8` at
`0x792` (and, separately, `0x772 = 0x710 − 8`) with the receive filter on `0x710` —
deliberately **not** performed here.

Until that is done, `decoders/meb.py` is left as-is and its existing
"DIVISOR ASSUMED, UNVERIFIED" note stands. The highest-value next step is therefore
**addressing, not scaling.**

### 14.6 Classification summary

| Resource | Classification | Confidence | Basis |
|----------|----------------|-----------|-------|
| `profiles7.db` | Live measurement profile definitions | **VERIFIED** | Re-derived end-to-end; BSON length exact; 308 `CustomPID` with decoder fields |
| `vag.micro.vag` | Localized DTC text records | **VERIFIED** | Schema reproduced from `ReadFromStream`/`LoadMicroContainers`; 0 trailing bytes; 100% of text offsets resolve to fault descriptions |
| `vagmeb.db` | MEB coding/adaptation | Verified (unchanged) | 44 BSON coding entries with `RQH`/`RSH`/`PWD` |
| `vag.strings.vag` | DTC description string table, offset-indexed | **VERIFIED** | Consumers `Seek(offset)` + `ReadString()`; not a record grid |

### 14.7 Verification run

`python -m pytest -q` — **439 passed in 76.08s**, before and after this documentation
change. No source file was modified; the working tree change is confined to
`docs/CAR_SCANNER_REVERSE_ENGINEERING.md`.

---

*End of document*
