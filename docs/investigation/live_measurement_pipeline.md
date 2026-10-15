# Car Scanner ELM OBD2 — VW MEB Live-Measurement Pipeline

Reverse-engineering report. Static, read-only analysis of the Android APK.
Target vehicle class: Volkswagen MEB (ID.3 / ID.4 / ID.5 / ID.6 / ID.7 / ID. Buzz,
Enyaq, Elroq, Q4 e-tron, Q6 e-tron, Cupra Born/Tavascan, Porsche Macan EV).

---

## 1. Executive summary

The live-measurement pipeline for MEB vehicles is now **fully recovered from source evidence**, and the
previously-unresolved `hv_energy_content` measurement is **definitively mapped**.

The headline results:

| Question | Answer | Status |
|---|---|---|
| Where are vehicle measurement definitions? | `CarScannerMaui.EmbeddedFiles.profiles7.db` → LZMA → ShiftStream2 → BSON → `List<OBDReaderProfileV2>`, each profile carrying a `ProfilePIDs` JSON blob of `CustomPID` records | **VERIFIED** |
| Is `vagmeb.db` the live-measurement DB? | **No.** It is `List<MQBAdaptationTemplate>` (coding/adaptation). The prior hypothesis in the brief is confirmed. | **VERIFIED** |
| How is a measurement represented? | `CarScannerMaui.OBD2.PIDS.CustomPID` — JSON-deserialised, with `CMD` (request), `SBI`/`DL`/`MUL`/`DIV`/`OFS`/`SIG`/`RBS`/`TP`/`UN`/`FR` | **VERIFIED** |
| How is the ECU addressed? | Not by address in the measurement. Via `BCM` (before-command) `AT` strings per measurement: `ATCRA` + `ATFCSH`, both 11-bit and 29-bit forms | **VERIFIED** |
| Diagnostic service? | **UDS `0x22` ReadDataByIdentifier**, DID appended — `222AB8` = service `22` + DID `2AB8` | **VERIFIED** |
| `hv_energy_content` = ? | `[19.Gate] HV Battery energy content`, `CMD=222AB8`, header `0x710`, `SBI=2`, `DL=2`, `MUL=25`, unit **Wh** | **VERIFIED** |
| `P2AB800` = ? | Legacy Car Scanner PID-name encoding `P` + `2AB8` + `00` → the *same* DID 0x2AB8. Not a separate PID. | **VERIFIED** |
| `P2AB200` = ? | `P` + `2AB2` + `00` → DID **0x2AB2** = `[19.Gate] Maximum energy content of the traction battery`, `MUL=50`, Wh | **VERIFIED** |
| Are 0x2AB2 / 0x2AB8 related? | Yes — same ECU, same header, same request frame; **current** vs **maximum** usable HV battery energy content | **VERIFIED** |
| Does MEB use VWTP? | **No.** VWTP (`CarScannerMaui.OBD2.VWTP20`) targets legacy VAG platforms (PQ/MQB, K-line/11-bit). MEB uses plain UDS over ISO-TP. | **VERIFIED** |
| Byte order? | **Big-endian** for the MEB profile (`RBS=false` on all 308 records). `RBS=true` selects little-endian. | **VERIFIED** |
| Multi-frame? | Handled in-app, not by the ELM (`ATCP17`/`ATCP18` custom flow control) | **VERIFIED** |

Two earlier assumptions in the brief are **corrected** below (§10).

**Outcome:** `live_measurement_evidence.csv` contains all **308** MEB measurements with resolved
ECU label, header, service, DID, byte offset, length, scale, offset, unit, byte order and type.

---

## 2. Scope and safety constraints

This investigation was **static and read-only**.

* No diagnostic request was sent to any vehicle.
* No DTC clearing, no security access (`0x27`), no actuator test, no coding, no adaptation, no flashing.
* The installed app was not modified or repackaged; the Android device was not rooted.
* The original APK and all extracted assemblies are unmodified (hashes in §3).
* All decoding was performed on local copies under
  `C:\Users\kings\Documents\CarScanner_Investigation`.

The Python project `C:\Users\kings\OneDrive\Documents\odb_scanner` was **read only** (one grep) and
**not modified**. Its read-only allow-list and safety tests are untouched.

---

## 3. Source files and hashes (SHA-256)

| File | Size | SHA-256 |
|---|---:|---|
| `original_1.apk` | 76,291,057 | `CAF4F60E50CC1F2EDB308BEB8BA50AF1EC23D3610A12318B1C2161AE245B8BDE` |
| `extracted_assemblies\CarScannerMaui.dll` | 40,492,032 | `4CC192FFD7E358BE162590A22B38A7F9C9F83E0728A24FBC78AB999493D5F261` |
| `extracted_assemblies\CarScannerMaui.Droid.dll` | 332,800 | `A795C322170E5B902FBFED48EC5D89EDC9E517855C3ED7A1100A0C0B57D5A3F4` |
| `libaot-CarScannerMaui.dll.so` | 26,148,528 | `87A8BD91E73F0FD9F28754CA079EF753944B84FC5EDDCC3F1BD558B3F13E9FBD` |
| `libassembly-store.so` | 45,111,648 | `8E27AE5A623EB6EDC47E8C90C3592DA7F5B0DB0384F89A7BA8541489A4D26F67` |
| `cs_profiles.dat` | 756 | `A9983C656F591DE6046B665F1226373DF61DD6922C4FD62D140861EC08E4AC27` |

The APK and assembly-store hashes match the values recorded in the brief exactly.

Derived artefacts (produced by this investigation):

| File | Size | SHA-256 |
|---|---:|---|
| `resources\…profiles7.db` (extracted resource) | 10,032,980 | `4C6737ECBE03C284FD2B87B6917400880C75CD9518D662082E7FBB7BC72FB94B` |
| `resources\…vagmeb.db` (extracted resource) | 26,992 | `A430AEAFD6F83B3116A28105F1F8F76B47B2A55315E46540F7CF2F690A118CE0` |
| `resources\TCWy` (obfuscated string table) | 563 | `7E491D062C249E413C396671664501A152EB6FA8C898454F2E9A5E095C829C87` |
| `resources\jWfF` (Droid string table) | 307 | `D7DDB4556BB5B954DB2665F764BC48CF96E7D5D25E0CBB060F4FDF2B8FE58E6A` |
| `…profiles7.db.lzma.out` (stage 1: LZMA) | 75,975,218 | `86AC7D1F217CA62410048A90F23B9806321729E35F4EC8AA53969853FCD4AFA5` |
| `…profiles7.db.bson` (stage 2: ShiftStream2) | 75,975,218 | `6B777F665C5EB92E5744E15F461878EE9B266249095C55DED87DBBAA3A6A0571` |

`profiles7.db` is 10,032,980 bytes in this build. The earlier desktop investigation recorded
9,919,996 bytes for the same resource name — **different application version**; findings must not be
conflated (see §10.4).

---

## 4. Application and assembly version information

* Package `com.ovz.carscanner`, version `2.2.21`, versionCode `42002021`
* minSdk 23, targetSdk 36 (from the brief; re-confirmed via `apk_contents.csv` / `AndroidManifest.xml`)
* Target framework of the assemblies: `net10.0`-era MAUI (ILSpy 11.1 decompiled cleanly)
* MAUI Android AOT build; `libassembly-store.so` holds the managed assemblies
* `CarScannerMaui.dll` is the single 40.5 MB assembly containing all application logic
* `CarScannerMaui.Droid.dll` holds the Android platform implementations, including
  `NativeShiftStream` — **the only place the measurement resource cipher is implemented**

---

## 5. Relevant types and method names

Types (all in `CarScannerMaui.dll` unless noted):

| Type | Role | Status |
|---|---|---|
| `OBD2.PIDS.PID` | Base measurement: `Name`, `ShortName`, `Command`, `Header`, virtual `Decode(byte[], TimeSpan, string)` | VERIFIED |
| `OBD2.PIDS.CustomPID` | **The live-measurement definition record.** JSON-bound | VERIFIED |
| `OBD2.PIDS.CustomPIDType` | `Formula, ByteSet, BitValue, Action, InternalOBD2, VWTPGroupItem` | VERIFIED |
| `OBD2.PIDS.PIDWithFloatValueFormula` | Formula variant (216 KB decompile) | VERIFIED |
| `OBD2.PIDS.NC_CustomPID` | Subclass (1.6 KB) | VERIFIED |
| `OBD2.VWTP20.VWTPManager` | **Legacy** VAG transport (NOT MEB) | VERIFIED |
| `OBD2.VWTP20.VWTPECU` | VWTP 2.0 channel state machine | VERIFIED |
| `ViewModels.LiveDataPIDModel` | Builds the displayed PID list; `GetRequests()` | VERIFIED |
| `ViewModels.CustomPIDViewModel` | Holds `CurrentProfile.PidCollection` | VERIFIED |
| `ViewModels.PIDEditorListModel` | PID editor lists | VERIFIED |
| `ProfilesV2.OBDReaderProfileV2` | A vehicle profile; `ProfilePIDs` JSON blob | VERIFIED |
| `ProfilesV2.ProfileV2Model` | Loads all profiles (`LoadProfiles`, `d`, `e`, `f`) | VERIFIED |
| `Common.PackageFileReader` | Resource loaders incl. the compressed+crypto+BSON path | VERIFIED |
| `ECUModels.VAGECU`, `VAG29bitECU` | ECU models, DTC + VWTP helpers | VERIFIED |
| `PIDAdapter` | Display adapter: PID → Name/Value/Units string | VERIFIED |
| `Droid.Common.NativeShiftStream` (`CarScannerMaui.Droid.dll`) | Stream cipher variants 1/2/3 | VERIFIED |
| `Droid.Common.c` (`CarScannerMaui.Droid.dll`) | P/Invoke shim → native `transformPtr` | VERIFIED |
| `CarScannerMaui.PIDAdapter` / `RequestProducers.PIDSelectorRequestProducer` | Present but thin | VERIFIED |

Obfuscated helpers that matter:

| Type | Role |
|---|---|
| `a` (`[BabelRename]`) | LZMA-Alone decompressor (`a.a(Stream, Stream)`) |
| `al` | Second, custom LZMA variant (LSB-first range decoder) used by `an.at` containers |
| `an` | `an.at(Stream)` — the AES+LZMA resource **container** decoder |
| `af` / `d` | Babel string tables (resource `TCWy` / `jWfF`) |

---

## 6. Embedded resources and their consumers

4,438 embedded resources; 511 are `CarScannerMaui.EmbeddedFiles.*`, the rest are `.resources`
UI strings. See `resource_inventory.md` for the classified table.

Resources that matter for live measurements:

| Resource | Size | Consumer | Classification |
|---|---:|---|---|
| `CarScannerMaui.EmbeddedFiles.profiles7.db` | 10,032,980 | `ProfileV2Model.LoadProfiles` → `d()`/`e()` → `PackageFileReader` | **THE live-measurement definition database** (1,323 profiles; MEB = index 342) |
| `CarScannerMaui.EmbeddedFiles.vagmeb.db` | 26,992 | `CodingListModel.cs:1105` | Coding/adaptation templates (`MQBAdaptationTemplate`) — **not** live measurements |
| `CarScannerMaui.EmbeddedFiles.vag.vagmodelcodes_crypto.db` | — | `ad.cs:354` | `List<VagProject>` model-code database (`Projects`, as sought in the brief) |
| `CarScannerMaui.EmbeddedFiles.vag.micro.vag` | 6,204,318 | `ad.cs` (`LoadMicroContainers`) | LZMA-only; localized **DTC text** records (`VagMicroItem`) — **not** live measurements |
| `CarScannerMaui.EmbeddedFiles.vag.strings.vag` | 3,222,110 | `ad.cs` | LZMA-only; string tables |
| `dtcdb.*.db` | — | DTC decoders | DTC descriptions |
| `TCWy`, `jWfF` | 563 / 307 | `af`, `d` | Babel string tables |

**VERIFIED — the brief's §4.6 caution is correct.** `vag.micro.vag` feeds `VagMicroItem.ReadFromStream`
(18-byte records → `VagCode,U,RU,DE,EN,S`), which is localized DTC text. It is *not* the
live-measurement database, and `LoadMicroContainers` must not be conflated with measurement loading.

---

## 7. End-to-end call graph

```
UI selection (Live Data / Dashboard item references PID Id)
  └─> LiveDataPIDModel.PIDCollection                     (built by UpdatePIDCollection)
        includes CustomPIDViewModel.CurrentProfile.PidCollection
        and               CustomPIDViewModel.CurrentCustom.PidCollection
        │
        ├─> LiveDataPIDModel.GetRequests(IPID, List<OBDRequest>)      [LiveDataPIDModel.cs:292-343]
        │      for CustomPID:
        │        new OBDRequest(CustomPID.Command, CustomPID.Header,
        │                        CustomPID.BeforeCommand, CustomPID.AfterCommand, Repeat:true)
        │        + recursively GetRequests(CustomPID.RequiredPIDs)     (formula dependencies)
        │
        ├─> OBDRequestQueue / OBDRequestProducer (PIDSelectorRequestProducer.Delegate)
        │
        └─> OBDDataReader  ── sends: BeforeCommand (AT-prefixed), then Command, then AfterCommand
              │            ATCRA / ATFCSH select the ECU; ATSPx selects the protocol
              │            ATCP17/ATCP18 = Car Scanner's own flow control (multi-frame in app)
              │            ATFCSM0/1 = flow-control status masking
              │
              ├─> response marker stripped; for "22…" the 2-byte DID echo is stripped and
              │   only the DID payload is passed on                        [OBDDataReader.cs:5256-5281]
              │
              └─> CarData.Decode(cmd, response_header, request, data, timeStamp)   [CarData.cs:1630]
                    └─> PID.Decode(data, timeStamp, response_header)
                          └─> CustomPID.Decode(byte[], TimeSpan, string)          [CustomPID.cs:910]
                                switch (Type):
                                  Formula          -> DecodeFormula        (mxparser)
                                  ByteSet          -> DecodeByteSet        (BIG-endian)
                                  ByteSet+RBS      -> DecodeReversedByteSet (LITTLE-endian)
                                  BitValue         -> DecodeBitValue
                                  InternalOBD2     -> DecodeInternalOBDII
                                  VWTPGroupItem    -> DecodeVWTPGroupItem (legacy VAG only)
                                then  value *= Multiplier; value /= Divider; value += Offset
                                then  Value -> OnValueChanged() -> PID.ValueChanged
                                      └─> PIDAdapter / dashboard / recorder
```

Legacy (non-MEB) branch, for contrast:

```
CustomPID.Command starts with "VWTP:"
  └─> VWTPManager.SendCommand("VWTP:<UNIT>:<SERVICE><PARAM>")     [VWTPManager.cs:33]
        OBDRequest("VWTP:"+unit+":"+command, "000",
                   "ATCAF0;ATV1;ATSP6;ATCM000",
                   "ATSPDEF;ATCAF1;ATV0;ATAR", repeat)            [VWTPManager.cs:422]
        └─> VWTPECU.SendCommand  → ChannelSetup / OpenConnection / SendRequest
              (raw CAN, VWTP 2.0 multiplexing, block size + ACKs)
```

`CustomPID.VagUnit` = `VWTPManager.CANAddressToUnit(Command.Split(':')[1])`;
`CustomPID.VagGroup` = hex→int of `Command.Split(':')[2].Substring(2)`.

---

## 8. PID → ECU / service / DID mapping

### 8.1 How a measurement identifies itself

`CustomPID` is deserialised by Newtonsoft from the profile's `ProfilePIDs` JSON via the
`[JsonConstructor]` at `CustomPID.cs:552`. Bound names (`CustomPID.cs:158-508`):

| JSON | Property | Meaning |
|---|---|---|
| `CMD` | `PID.Command` (`PID.cs:89`) | The request sent to the ECU |
| `HDR` | `PID.Header` | Expected response CAN header |
| `SBI` | `StartByteId` | Payload byte offset |
| `DL` | `DataLength` | Byte count (0 = "rest of payload", clamped to 4) |
| `MUL` / `DIV` / `OFS` | `Multiplier` / `Divider` / `Offset` | Scaling |
| `SIG` | `IsSigned` | Signed integer interpretation |
| `RBS` | `ReversedByteSet` | Little-endian (vs default big-endian) |
| `TP` | `Type` | `CustomPIDType` |
| `BIT` | `Bit` | Bit index for `BitValue` |
| `UN` | `Units` | `UnitsHelper.Units` enum |
| `FR` | `Formula` | mxparser expression for `Formula` type |
| `BCM` / `ACM` | `BeforeCommand` / `AfterCommand` | AT strings around the request |
| `TVV` | `TextValueVariants` | `int=label;int=label` map |
| `ORD`, `RL`, `MIN`, `MAX`, `TRNS`, `FHID` | misc | ordering, role, limits, translations |

`Divider` is read through a guard that returns **1.0** when the stored value is 0 or non-finite
(`CustomPID.cs:201-216`), so a zero divider can never produce infinity.

### 8.2 How the ECU is identified

The measurement does **not** store a numeric ECU address. Addressing is expressed in `BCM` as
ELM327 AT commands. Two forms appear in the MEB profile:

29-bit (direct ECU), 186 records — `[8C.BMS]`:
```
ATSP7;ATCP17;ATCRA17FE007B;ATFCSH17FC007B;ATFCSD300000;ATFCSM1
ATAR;ATCP18;ATSHDB33F1;ATFCSM0
```
11-bit, 30 records — `[19.Gate]`:
```
ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A
ATFCSM0;ATAR;ATSP7
```

`ATCP17` / `ATCP18` are Car Scanner's own flow-control modes (`ATCP18` is also the restore path),
so **ISO-TP segmentation is performed in the application, not by the ELM327**. `ATFCSD300000`
clears flow-control data; `ATFCSM1`/`ATFCSM0` mask/unmask flow-control status bytes.

### 8.3 MEB ECU map (all 308 measurements)

| Label | Count | Response header | CAN id | `ATCRA` |
|---|---:|---|---|---|
| `[8C.BMS]` | 186 | `FC007B` | 0xFC007B | `17FE007B` |
| `[19.Gate]` | 30 | `710` | 11-bit | `77A` |
| `[08.HVAC]` | 27 | `746` | 11-bit | `7B0` |
| `[51.ElDrive]` | 26 | `FC007C` | 0xFC007C | `17FE007C` |
| `[01.ENG]` | 21 | `FC0076` | 0xFC0076 | `17FE0076` |
| `[C6.Charger]` | 5 | `744` | 11-bit | `7AE` |
| *(empty)* | 5 | — | — | — |
| `[03.ABS]` | 4 | `713` | 11-bit | `77D` |
| `[8105.DC/DC Converter]` | 2 | `FC00B9` | 0xFC00B9 | `17FE00B9` |
| `[75.GPS]` | 2 | `767` | 11-bit | — |

254 distinct DIDs are referenced. Full detail per measurement is in
`live_measurement_evidence.csv`.

**STRONGLY INFERRED:** the `17` prefix on `ATCRA17FE00xx` / `ATFCSH17FC00xx` is a Car Scanner
flow-control flag byte rather than part of the CAN id (the low byte `xx` matches the ECU). This
also explains the `17FC`, `17FE`, `18DA` strings noted in the brief. The precise ELM327 flag
semantics of the 3-hex-digit `ATCRA` values (`77A`, `7B0`, `7AE`, `77D`) remain **UNRESOLVED**;
for reimplementation only the (CRA ↔ response-header) pairing is needed and is tabulated above.

---

## 9. Response decoding and scaling logic

`CustomPID.Decode` (`CustomPID.cs:910-955`) is a `try/catch`-wrapped switch on `Type`.

### 9.1 ByteSet — the common MEB case (250 of 308 records)

`DecodeByteSet` (`CustomPID.cs:1093-1170`):

```
if (data.Length < StartByteId + DataLength) return;      // silently keeps previous value
n = DataLength;  if (n == 0) n = data.Length - StartByteId;   if (n > 4) n = 4;

// BIG-ENDIAN  (RBS = false, the MEB default)
unsigned:  1 -> data[S]
           2 -> data[S]*256     + data[S+1]
           3 -> data[S]*65536   + data[S+1]*256   + data[S+2]
           4 -> big-endian uint32
signed:    1 -> (sbyte)data[S]
           2 -> (short)(data[S]*256 + data[S+1])
           3 -> (sbyte)data[S]*65536 + data[S+1]*256 + data[S+2]
           4 -> big-endian int32

value = n;
value *= Multiplier;
value /= Divider;
value += Offset;
if (Role == Roles.Speed) value *= SharedSettings.Current.SpeedCorrectionFactor;
```

`DecodeReversedByteSet` (`CustomPID.cs:992-1076`) is the same but **little-endian**
(`data[S+1]*256 + data[S]`, `BitConverter.ToUInt32` after `Array.Reverse` on LE hosts).

Errors: the whole `Decode` body is inside `catch (Exception) { }`; a short buffer returns without
touching `Value`, so the UI keeps the previous reading. `SendNaN()` sets `double.NaN` for
explicit no-data. There is no sentinel/NRC handling in `ByteSet` — a negative response
(`7F 22 ..`) simply yields an arbitrary byte-derived value unless the caller filters it.

### 9.2 BitValue
`DecodeBitValue` (`CustomPID.cs:1078`): `Value = GetBit0_7(data[StartByteId], Bit) ? 1.0 : 0.0`.

### 9.3 Formula
`DecodeFormula` (`CustomPID.cs:1173`) builds an mxparser `Expression`. `InitializeEngine`
(`CustomPID.cs:841`) registers `GetBit, BIT, SIGNED, ShortSigned, And, Shr, Shl, MAX, MIN, ABS,
FLOAT32, FLOAT64, int24/32/16, IF, PID, SetVar, GetVar, GetTimestampMs` and removes the built-in
tokens so single letters are free.

**Letter index == byte index.** `DICT_LETTERS` is `A,B,C,…,FZ` (182 entries) and
`BuildDictionaryV2` (`CustomPID.cs:1209`) sets `arguments[k] = source_data[FormulaElements[k]]`,
i.e. **`A` = payload[0], `B` = payload[1], `C` = payload[2], …**. Values are the raw unsigned
bytes.

Dependencies between measurements are expressed as `{Name}` or `PID(<id>)` and resolved into
`RequiredPIDs`; `ReloadFormula` rewrites `{Name}` → `PID(id)` and subscribes to those PIDs'
`ValueChanged`, re-evaluating on each update (`CustomPID.cs:730`, `:809`).

### 9.4 Units and display
`UnitsHelper.GetValue(value, units)` / `GetCaption(units)` convert for display
(`PIDAdapter.cs`). `Units` index 61 = **Wh**, 46 = kW, 10 = volts, 60 = kWh.

---

## 10. Worked example supported by evidence

### 10.1 `hv_energy_content` → `[19.Gate] HV Battery energy content`

Definition record (MEB profile index 342, `resources/profiles/profile_EV_MEB__….pids.json`):

| Field | Value |
|---|---|
| `NM` | `[19.Gate] HV Battery energy content` |
| `CMD` | `222AB8` |
| `HDR` | `710` |
| `SBI` | 2 |
| `DL` | 2 |
| `MUL` / `DIV` / `OFS` | 25.0 / 1.0 / 0.0 |
| `SIG` | false |
| `RBS` | false (big-endian) |
| `TP` | 1 (`ByteSet`) |
| `UN` | 61 (`Wh`) |
| `BCM` | `ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A` |
| `ACM` | `ATFCSM0;ATAR;ATSP7` |

**Request/response sequence (all VERIFIED from code):**

```
ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A
22 2A B8
   ← positive response 62 2A B8 <p0> <p1> <p2> <p3> …
   marker "62" stripped (OBDDataReader.cs:2289 dataWithoutMarker)
   DID 2A B8 consumed (OBDDataReader.cs:5260-5274)
   payload = <p0> <p1> <p2> <p3> …
ATFCSM0;ATAR;ATSP7
```

**Conversion** (`CustomPID.DecodeByteSet`, `StartByteId=2`, `DataLength=2`, big-endian):

```
raw   = payload[2] * 256 + payload[3]
value = raw * 25 / 1 + 0        → displayed in Wh
```

**Arithmetic plausibility check (STRONGLY INFERRED, not a captured frame):** the sibling record
`[19.Gate] Maximum energy content of the traction battery` (`CMD=222AB2`, `SBI=0`, `DL=2`,
`MUL=50`) yields `raw_max * 50` Wh. For `raw_max = 1240` → 62,000 Wh gross, which matches an
ID.3 58 kWh pack. The `2AB8` factor of 25 halves that, so at half charge `raw = 1240` →
31,000 Wh = half of 62,000 Wh. The two factors are mutually consistent.

**No raw response frame was captured**, so no specific numeric reading is asserted. The
transformation above is exact and derived from source.

### 10.2 `P2AB800` / `P2AB200` resolved

* `P2AB800` = `P` + `2AB8` + `00` → **DID 0x2AB8**, i.e. the same measurement as
  `hv_energy_content` above. It is a **legacy Car Scanner PID-name encoding**, not a distinct
  definition. The earlier finding of it "existing as a raw substring at an unaligned offset with
  no consumer" is explained: it lives in an **encoded/BSON/JSON** payload, not in the printable
  string region that was scanned.
* `P2AB200` = `P` + `2AB2` + `00` → **DID 0x2AB2** = `[19.Gate] Maximum energy content of the
  traction battery` (`MUL=50`, Wh, `SBI=0`, `DL=2`).
* Therefore 0x2AB2 and 0x2AB8 are **related measurements** (max vs current) of the same quantity
  from the same ECU (`HDR=710`, same `BCM`/`ACM`), **not alternate encodings of one another**.

### 10.3 `[8C.BMS] DC Battery voltage` — multi-DID / formula example

Two sibling records show multi-frame and formula handling:

```
CMD=221E3B       SBI=0 DL=2 MUL=1 DIV=4  SIG=false RBS=false TP=1 UN=10 (V)
                 → voltage_V = (p0*256 + p1) / 4

CMD=221E3B1E3D   TP=0 (Formula)
FR = "(((((E*16777216+F*65536+G*256+H)-150000)/100)*-1)*((A*256+B)/4))/1000"
     → A,B = payload of DID 1E3B ; E,F,G,H = payload of DID 1E3D
     → power_kW = (current_mA - 150000) / -100 * voltage_V / 1000
```

`A`…`H` are payload indices 0…7 of the concatenated multi-DID response, confirming the
letter↔byte mapping of §9.3 across a `0x22` multi-identifier read.

### 10.4 Corrections to prior assumptions

1. **MEB does not use VWTP.** `VWTPManager`/`VWTPECU` (`ATCAF0;ATV1;ATSP6;ATCM000`, raw CAN,
   VWTP 2.0 multiplexing) address legacy VAG platforms; the `UnitToCANAddress` table has no
   MEB-era addresses. The MEB profile uses UDS `0x22` over ISO-TP with ELM `AT` sequences.
   *Status: the brief treated VWTP as the likely MEB path — corrected.*
2. **`vag.micro.vag` is DTC text, not measurements** — as the brief provisionally suspected.
   *Status: confirmed; the real database is `profiles7.db`.*
3. **`vagmeb.db` is coding/adaptation** (`MQBAdaptationTemplate`), not live measurements — as the
   brief suspected. *Status: confirmed.*
4. **Version mismatch:** `profiles7.db` is 10,032,980 bytes here vs 9,919,996 in the earlier
   desktop artefacts. Findings from the two builds were **not** merged.
5. **`ShiftStream2` transform** — the brief's "subtracting `0x58` … adding `0x45` … with offset
   adjustments" is **confirmed and fully specified**: the offset is the byte index **modulo 256**,
   not the raw index (§11). A model linear in `p` fails; that is why earlier attempts did not
   converge.

---

## 11. Resource decode pipeline (reproduced)

`profiles7.db` → measurement definitions, three stages, all reproduced locally:

1. **LZMA-Alone decompress** — `a.a(Stream, Stream)` (`full_decomp/a.cs`), 5-byte props +
   8-byte little-endian size, LZMA SDK `q` decoder with
   `{dict=8388608, posStateBits=2, litContextBits=3, litPosBits=0, algorithm=2,
   numFastBytes=128, matchFinder="bt4", endMarker=false}`.
   props byte `0x5D`, dict `0x00800000`, declared size 75,975,218.
   Result: **75,975,218 bytes** (exactly the declared size — independent confirmation).
2. **ShiftStream2** — `NativeShiftStream` variant 2 (`PlatformCommonServiceImplementationDroid.GetShiftStream2`
   → `NativeShiftStream.b(stream)`), applied in the **read** direction:

   ```
   for p in range(len(data)):
       pb = p & 0xFF
       out[p] = (data[p] - 0x58 - pb) & 0xFF   if p % 2 == 0
       out[p] = (data[p] + 0x45 + pb) & 0xFF   if p % 2 == 1
   ```

   Validated on `vagmeb.db`: `c8 23 5a b8 …` → `70 69 00 00 …`, a BSON header declaring
   26,992 = the exact resource size, root type `0x03`, with readable keys
   (`OPTS`, `TTL`, `UID`, `NM`, `PWD`, `SBI`, `DL`, `MUL`, `RBS`, `OFS`, `SIG`, `AD`, `RQH`, `RSH`).
   Applied to `profiles7.db`: declared length 75,975,218 = exact buffer size, root type `0x03`,
   first records decode to `"Name":"OBD-II / EOBD"`, `"BrandForDTC"`, `"Universal"`, `"Brands"`.
3. **BSON → JSON** — `Newtonsoft.Json.Bson.BsonReader` with `ReadRootValueAsArray = true`.
   The root element type is `0x03` (document) whose keys are the array indices `"0","1",…`.
   Result: **1,323** `OBDReaderProfileV2` records. Index **342** is
   `"EV MEB: ID.3, ID.4, ID.5, ID.6, ID.7, ID.Buzz, Enyaq, Elroq, Q4 e-tron, Q6 e-tron,
   Cupra Born, Cupra Tavascan, Porsche Macan EV, etc.; Ford Explorer EV, Capri EV"`
   with a 171,289-byte `ProfilePIDs` JSON → **308 `CustomPID` records**.

Then in the app: `OBDReaderProfileV2.ImportProfilePids(profile)` →
`CustomPIDViewModel.CurrentProfile.AddFromString(profile.ProfilePIDs)` →
`JsonConvert.DeserializeObject<ObservableCollection<CustomPID>>` → `CustomPIDViewModel.Save()`
stores it in `SharedSettings.Current.ProfilePIDsCollection`, encrypted with
`EncodeViaBytes(value, (byte)"Car Scanner"[3])` under the Android
`AppSettings` key `ProfilePIDsCollection2`.

**UNRESOLVED (side note):** `ShiftStream2`'s implementation is a native export
(`CarScannerMaui.Droid.Common.c` → `NativeLibrary.GetExport`), and the Droid assembly's Babel
string table (`jWfF`, 307 bytes) is `an.at`-encoded with the *custom* LZMA variant (`al`,
LSB-first range decoder) which liblzma rejects. The library/export names were therefore not
resolved. This no longer blocks the investigation — the transform was recovered and validated
analytically against `vagmeb.db` — but the library name remains unknown.

---

## 12. Confidence summary

| Claim | Status |
|---|---|
| `CustomPID` is the live-measurement record; JSON fields as tabulated | VERIFIED |
| Definitions come from `profiles7.db` → LZMA → ShiftStream2 → BSON | VERIFIED |
| MEB profile is index 342, 308 `CustomPID`s | VERIFIED |
| MEB uses UDS `0x22` + DID, via `ATSP6`/`ATSP7` + `ATCRA`/`ATFCSH` | VERIFIED |
| `hv_energy_content` = DID `0x2AB8`, `SBI=2 DL=2 ×25 Wh`, header `0x710` | VERIFIED |
| `P2AB800`→`0x2AB8`, `P2AB200`→`0x2AB2`; 0x2AB2 = max energy content, `×50 Wh` | VERIFIED |
| ByteSet is big-endian; `RBS=true` → little-endian; scaling `×MUL ÷DIV +OFS` | VERIFIED |
| `62` marker and DID echo stripped before `Decode` | VERIFIED |
| Formula letters `A`…`FZ` map to payload byte index | VERIFIED |
| ShiftStream2 transform (even `−0x58−(p&0xFF)`, odd `+0x45+(p&0xFF)`) | VERIFIED |
| MEB does **not** use VWTP | VERIFIED |
| Multi-frame handled in-app via `ATCP17`/`ATCP18` | VERIFIED |
| `17` prefix on `ATCRA17FE00xx` is a flow-control flag | STRONGLY INFERRED |
| ELM327 protocol number semantics of `ATSP6` vs `ATSP7` | STRONGLY INFERRED (6 = 11-bit, 7 = 29-bit, from header width) |
| `raw_max=1240` ⇒ 62 kWh for an ID.3 58 kWh pack | STRONGLY INFERRED (consistency check, no captured frame) |
| Exact ELM327 flag semantics of 3-digit `ATCRA` (`77A`,`7B0`,`7AE`,`77D`) | UNRESOLVED |
| Native cipher library/export name behind `ShiftStream2` | UNRESOLVED |
| `cs_profiles.dat` purpose | UNRESOLVED (756 B, `CSINT1`; not referenced by any decompiled profile loader) |

---

## 13. Unresolved questions and next experiments

1. **Capture a real `221E3B` / `222AB8` frame** to confirm the `ATSP6`/`ATSP7` and
   `ATCRA`/`ATFCSH` semantics and to validate the `SBI=2` offset for 0x2AB8 empirically.
   Read-only, diagnostic-session-only requests (`0x22` is a non-destructive read).
2. **Resolve the 3-digit `ATCRA` encoding** by locating the ELM327 profile of ELM327 command
   parsing, or by comparing against a known-good MEB capture.
3. **Identify the native cipher library/export** — decode `jWfF` with the custom `al` LZMA
   (a ~150-line Python port of the decompiled decoder would suffice) to read `d.a(25993)` /
   `d.a(26096)`. Low value now that the transform is known; needed only for a from-source proof.
4. **`cs_profiles.dat`** — check whether it is a build-integrity manifest (signature over
   `profiles7.db`) by comparing against `cs_profiles.sig` (71 B).
5. **Remaining profiles** — the other 1,322 profiles decode with the same tooling; extracting a
   table of all `CMD`→`BCM` pairs would give complete ECU coverage for other vehicles.
6. **Compare with a newer build** — the desktop 9,919,996-byte `profiles7.db` should be re-extracted
   and diffed at the `CustomPID` level to see whether MEB DIDs/scalings have changed.

### Recommended next step for `odb_scanner`

Implement DID `0x2AB8` (current) and `0x2AB2` (max) on the gateway with
`ATSP6` + `ATCRA77A` + `ATFCSH710`, read 2 big-endian bytes at payload offset 2 (resp. 0),
multiply by 25 (resp. 50), and report Wh. Keep the existing read-only allow-list and safety
tests unchanged; add `0x22` **read** only, never `0x27`/`0x2E`/`0x31` write services.