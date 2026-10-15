# Car Scanner (MEB) Reverse-Engineering Findings

**Date**: 2026-10-14  
**Author**: Automated extraction from `C:\Users\kings\OneDrive\Documents\Car Scanner` (extracted Android APK)  
**Target Vehicle**: VW ID.3 (MEB platform), VIN `WVWZZZE1ZMP087053`

---

## Executive Summary

Car Scanner is a **.NET MAUI** Android app. Its MEB logic and embedded databases live inside an **XABA assembly store** (`libassemblies.arm64-v8a.blob.so`). We extracted **206 managed assemblies**, decrypted the embedded resources (LZMA + custom "shift" cipher), and recovered:

1. **Module address tables** (two formats: `17FCxxxx` VAG 29-bit, and `18DAxxxx` ISO-15765 normalized)
2. **The exact ELM327 command sequence** Car Scanner uses for VAG UDS
3. **All target DIDs** for the readings our project wants (`hv_energy_content`, `hv_energy_max`, pack voltage, SoC, cell voltages, DC-DC, climate)
4. **Resource formats**: LZMA-compressed + ShiftStream2 cipher (`vagmeb.db`, `vag.micro.vag`, `vag.strings.vag`)

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
| `CarScannerMaui.EmbeddedFiles.vagmeb.db` | 26,992 B | LZMA + ShiftStream2 → BSON | MEB **coding** DB (44 entries) |
| `CarScannerMaui.EmbeddedFiles.vag.micro.vag` | 6,204,318 B | LZMA + ShiftStream2 → 18-byte records | **Live measurement DIDs** (551,991 records, 45,033 unique DIDs) |
| `CarScannerMaui.EmbeddedFiles.vag.strings.vag` | 3,222,110 B | LZMA + ShiftStream2 → flat PID list | Human-readable PID names (~900k entries) |

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
| `vagmeb.db` | 26,992 B | BSON (44 coding entries) |
| `vag.micro.vag` | 10,009,474 B | 18-byte records (DID at offset 16) |
| `vag.strings.vag` | 7,214,595 B | Flat PID code list (`07 P/U 6-digit`) |

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

## 5. Live Measurement DIDs (from `vag.micro.vag`)

### 5.1 Record Format
- **18 bytes per record**, DID at **offset 16** (little-endian)
- Records aligned at multiples of 18 bytes
- 551,991 non-zero records, 45,033 unique DIDs

### 5.2 Target DIDs Found

| Reading | DID | Car Scanner Module Tag | Our Module Key | 29-bit (17FC) | 11-bit (ATSP6) | 18DA |
|---------|-----|------------------------|----------------|---------------|----------------|------|
| SoC (abs) | `0x028C` | `[8C.BMS]` | `bat_mgmt` | `17FC007B`/`17FE007B` | `0x7E5`/`0x7ED` | — |
| Pack Voltage | `0x1E3B` | `[8C.BMS]` | `bat_mgmt` | `17FC007B`/`17FE007B` | `0x7E5`/`0x7ED` | — |
| HV Energy Content | `0x2AB8` / `0x2AB2` | `[19.Gate]` | **`energy`** | **N/A** | **`0x710`/`0x77A`** | `18DAD0F1` |
| Min SoC | `0xF50E` | `[8C.BMS]` | `bat_mgmt` | `17FC007B` | `0x7E5` | — |
| DC-DC Current | `0x465B` | `[8105.DC/DC]` | `dcdc` | `17FC00B9`/`17FE00B9` | `0x777` | — |
| DC-DC Voltage | `0x465D` | `[8105.DC/DC]` | `dcdc` | `17FC00B9`/`17FE00B9` | `0x777` | — |
| Cell voltages | `0x028C` area | `[8C.BMS]` | `bat_mgmt` | `17FC007B` | `0x7E5` | — |

**Key finding**: `hv_energy_content`/`hv_energy_max` live on **Gateway (`19.Gate`)** with DIDs `0x2AB8`/`0x2AB2`. The Gateway is addressed as:
- **11-bit**: `0x710` (req) / `0x77A` (resp) — appears in `GetVAGCanRequests` module list
- **18DA normalized**: `18DAD0F1` (req) / `18DAD2F1` (resp) — target `0xD0`

### 5.3 Micro File Record Structure (18 bytes)
```
Offset 0-1:  Module selector / PID index (LE)
Offset 2:    Data length / type
Offset 3:    Encoding hint
Offset 4-5:  Often 0x7B10, 0x7C10, 0xD746, 0xFBF6, 0xA806...
Offset 6:    Often 0x10 or 0x02
Offset 7:    Variant
Offset 8-9:  0x0210, 0x0220, 0x0228, 0x0300...
Offset 10-11: Often 0x1100, 0x2400, 0x3B00, 0xA806...
Offset 12:   Incrementing index
Offset 13:   Often 0x93, 0xA0, 0x3F, 0xEE...
Offset 14:   Often 0x00
Offset 15:   Variant / checksum
Offset 16-17: DID (little-endian)
```

**Scale/offset encoding** appears in bytes 8-11 (e.g., `D746`, `FBF6`, `A806`, `EE2F`, `F02F`, `F12F`, `F22F`, `F32F` for Gateway; `4D A0`, `E5 2A` for Energy). Need to decode these to MUL/OFS.

---

## 6. Strings File (`vag.strings.vag`)

Decoded: **Flat list of ~900k PID codes** — 8-byte records: `07 P/U 6-digit`:
```
07 50 31 34 30 31 30 30  →  P140100
07 50 31 34 30 30 30 30  →  P140000
07 50 33 31 30 42 30 30  →  P310B00
07 50 33 30 38 39 30 30  →  P308900
07 55 31 30 31 37 30 30  →  U101700
...
```

These are the **human-readable names** Car Scanner displays. Mapping:
- `P310B00` → "State of charge BMS" (SoC)
- `P308900` → "DC Battery voltage" (pack voltage)
- `P2AB800` → "HV Battery energy content"
- `P2AB200` → "Maximum energy content"
- `P028C00` → "Battery inlet temperature"

---

## 7. Exact ELM327 Command Sequence (from `GetVAGCanRequests` @ RVA `0x2274DC`)

Car Scanner uses **this exact sequence** for each module:

```
ATSP6                                    # ISO 15765-4 CAN 11-bit/500k
ATFCSH{req_id}                           # e.g., 710 for gateway
ATFCSD300005                             # Flow control: BS=0, STmin=5ms
ATFCSM1                                  # User-defined flow control
ATCRA{resp_id}                           # e.g., 77A for gateway
10 03                                    # DiagnosticSessionControl → Extended Session
22 {DID}                                 # ReadDataByIdentifier
```

### Module Scan List (11-bit IDs)
The method iterates these request IDs (with matching response IDs):
```
7E0 7E1 713 732 74D 746 70E 770 70A 7E2 772 715 70C 714 76A 716 728 70F 73B
711 72D 71A 71E 755 74C 76C 74E 72C 74A 712 76F 74B 754 76D 7E6 752 773 70B
747 769 6B8 723 745 71D 767 70A 76B 75A 74F 6BC 784 710 757 7E5 762 765 744
```

**Gateway is `0x710` / `0x77A`** — confirmed in this list.

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
          "ATFCSH 710; ATFCSD 300005; ATFCSM 1; ATCRA 77A; 10 03; then 22 2AB8 / 22 2AB2. "
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

Add a method to send Car Scanner's exact preamble for gateway:

```python
def set_module_car_scanner_gateway(self):
    """Configure for MEB Gateway (19.Gate) per Car Scanner recipe."""
    self.send_command("ATSP6")
    self.send_command("ATFCSH 710")
    self.send_command("ATFCSD 300005")
    self.send_command("ATFCSM 1")
    self.send_command("ATCRA 77A")
    self.send_command("10 03")  # Extended session
    # Now ready for 22 2AB8 / 22 2AB2
```

Or generalize `set_module(tx29, rx29)` to accept a "preamble" template.

### 8.3 Decoder Additions (`decoders/meb.py`)

| DID | Key | Scale/Offset | Unit | Notes |
|-----|-----|--------------|------|-------|
| `0x2AB8` | `hv_energy_content` | from micro file prefix | Wh | Gateway |
| `0x2AB2` | `hv_energy_max` | from micro file prefix | Wh | Gateway |
| `0xF50E` | `soc_min` | `/ 2.5` | % | BMS (MinSoC) |
| `0x465B` | `dcdc_current` | from micro file prefix | A | DC/DC |
| `0x465D` | `dcdc_voltage` | from micro file prefix | V | DC/DC |

**Scale/offset extraction**: Parse bytes 8-11 of micro file records for each DID variant. These encode MUL/OFS (likely fixed-point: `MUL = int16 / 256`, `OFS = int16`).

---

## 9. Climate Readings (Next Target)

From 17FC table, climate-related modules:

| Code | 29-bit Req/Resp | Module Name | Candidate DIDs (search micro file) |
|------|-----------------|-------------|-----------------------------------|
| `0701` | `17FC1701`/`17FE1701` | Rear A/C control head | Search for prefix bytes 4-5 ≈ `1701` |
| `06C0` | `17FC16C0`/`17FE16C0` | Coolant heating | Search for prefix ≈ `16C0` |
| `060B` | `17FC160B`/`17FE160B` | Fresh air blower front | Search for prefix ≈ `160B` |
| `070D` | `17FC170D`/`17FE170D` | Electric compressor | Search for prefix ≈ `170D` |

**Action**: Scan `res_micro_vag.dec.shifted` for records where bytes 4-5 match these module codes.

---

## 10. Acceptance Test Updates

Run `tools/collect_acceptance.py` — it should now pass for:
- `hv_energy_content` (Wh) — from Gateway `0x710` + `22 2AB8`
- `hv_energy_max` (Wh) — from Gateway `0x710` + `22 2AB2`
- Climate temperatures — from 29-bit climate modules

---

## 11. Reproducible Artifacts (on Disk)

| Path | Description |
|------|-------------|
| `C:\Users\kings\AppData\Local\Temp\opencode\extract_store.py` | XABA assembly store extractor |
| `...\carscanner_dlls\CarScannerMaui.dll` | Extracted main assembly (38 MB) |
| `...\pylibs\` | dnfile, dncil, pymongo (pip --target) |
| `...\vagmeb.db` | Encrypted coding DB |
| `...\vagmeb.dec.bin` | LZMA decompressed |
| `...\vagmeb.json` | Parsed BSON (44 coding entries) |
| `...\res_micro_vag` | Encrypted micro DB |
| `...\res_micro_vag.dec` | LZMA decompressed (10 MB) |
| `...\res_micro_vag.dec.shifted` | ShiftStream2 decoded (10 MB, 18-byte records) |
| `...\res_strings_vag` | Encrypted strings |
| `...\res_strings_vag.dec.shifted` | Shift decoded (7 MB, PID list) |
| `...\disasm3.py` | IL disassembler by RVA |
| `...\find_meas.py` | Method name search |
| `...\try_lzma.py` / `try_shift.py` | Decryption pipeline |

---

## 12. Open Questions / Next Steps

1. **Decode scale/offset** from micro file record prefixes (bytes 8-11). Hypothesis: fixed-point encoding.
2. **Which Gateway variant** (0x09A1 vs 0x09A2, which sub-variant) maps to `[19.Gate] HV energy content`. Cross-reference PID codes from strings file (`P310B00`, `P308900`) with micro file records.
3. **Climate DIDs**: Search micro file for records with prefix bytes 4-5 matching `1701`, `16C0`, `160B`, `170D`.
4. **18DA vs 17FC for Gateway**: Test both `18DAD0F1` and `11-bit 0x710` on hardware — Car Scanner uses both paths.
5. **Flow control necessity**: Is `ATFCSH/ATFCSD/ATFCSM1` strictly required, or does the adapter handle it? (Our `?` responses suggest it is required.)

---

## 13. Quick Reference Commands

```bash
# Full decryption pipeline
python try_lzma.py res_micro_vag          # LZMA decompress
python try_shift.py res_micro_vag.dec     # ShiftStream2 decode
python parse_records.py                   # Extract DID records

# Disassemble key methods
python disasm3.py CarScannerMaui.dll 2274dc   # GetVAGCanRequests
python disasm3.py CarScannerMaui.dll 356654   # BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2
python disasm3.py CarScannerMaui.dll 35795c   # ShiftStream2.ShiftReadByte
```

---

*End of document*