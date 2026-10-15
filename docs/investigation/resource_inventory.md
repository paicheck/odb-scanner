# Resource Inventory

All embedded resources of `CarScannerMaui.dll` (4,438 total: 512 top-level
`CarScannerMaui.EmbeddedFiles.*` / `TCWy` entries plus 3,926 `.resources` UI-string entries).
Classification against the live-measurement question.

Listing source: `embedded_resource_list.txt` (produced with `ilspycmd --list-resources`).

> Tooling correction: `-l r` from the brief yields **0 lines** in ILSpy 11.1. The valid switch is
> `--list-resources`.

---

## 1. Live-measurement path (CLASSIFIED)

| Resource | Raw size | SHA-256 (extracted copy) | Loading method | Consumer | Classification |
|---|---:|---|---|---|---|
| `CarScannerMaui.EmbeddedFiles.profiles7.db` | 10,032,980 | `4C6737ECBE03C284FD2B87B6917400880C75CD9518D662082E7FBB7BC72FB94B` | `PackageFileReader.BinaryDeserializeFromEmbeddedFileUsingCompressedCryptoStreamWithDiff` (→ `…CompressedCryptoStream2` when no diff cache exists) | `ProfileV2Model.LoadProfiles` → `d()`/`e()` → `f<T>()`; then `OBDReaderProfileV2.ImportProfilePids` | **THE live-measurement definition database.** 1,323 `OBDReaderProfileV2` records; MEB = index 342 → 308 `CustomPID` |
| `CarScannerMaui.EmbeddedFiles.vag.vagmodelcodes_crypto.db` | — | — | `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` | `ad.cs:354` → `List<VagProject>` | Vehicle **model-code / platform** DB (the "Projects" the brief sought) |
| `TCWy` | 563 | `7E491D062C249E413C396671664501A152EB6FA8C898454F2E9A5E095C829C87` | `an.at` (AES-CBC + custom LZMA) then sequential `BinaryReader.ReadString` with key `(pos+63)^0x87F0` | `af.a(int)` Babel string table | String table. Resource name computed by `af.b(0)` = `"CWy` |

Encoding of `profiles7.db` — **reproduced end to end**:

1. LZMA-Alone. props `0x5D` (lc=3, lp=0, pb=2), dict `0x00800000` (8 MB), declared size
   75,975,218. Decoder: LZMA SDK `q` with
   `{posStateBits=2, litContextBits=3, litPosBits=0, numFastBytes=128, matchFinder="bt4"}`.
2. `ShiftStream` **variant 2** (`NativeShiftStream.b`), read direction:

   ```
   out[p] = (in[p] - 0x58 - (p & 0xFF)) & 0xFF     p even
   out[p] = (in[p] + 0x45 + (p & 0xFF)) & 0xFF     p odd
   ```

3. BSON (`Newtonsoft.Json.Bson.BsonReader`, `ReadRootValueAsArray = true`); root element type
   `0x03` (document) keyed `"0","1",…`.

Derived copies:

| Stage | Size | SHA-256 |
|---|---:|---|
| `…profiles7.db.lzma.out` (after LZMA) | 75,975,218 | `86AC7D1F217CA62410048A90F23B9806321729E35F4EC8AA53969853FCD4AFA5` |
| `…profiles7.db.bson` (after ShiftStream2) | 75,975,218 | `6B777F665C5EB92E5744E15F461878EE9B266249095C55DED87DBBAA3A6A0571` |
| `resources\profiles\profiles_index.json` | 181,003 | all 1,323 profile names |
| `resources\profiles\profile_EV_MEB__….json` | 198,429 | MEB profile incl. `ProfilePIDs` |
| `resources\profiles\profile_EV_MEB__….pids.json` | 171,289 | the 308 `CustomPID` records |

**Transform validation** (independent of `profiles7.db`): `vagmeb.db` first four bytes
`c8 23 5a b8` → `70 69 00 00`, a BSON length header equal to the resource size 26,992, followed
by readable keys `OPTS, TTL, VL, UID, NM, DSC, PWD, SBI, DL, MUL, RBS, OFS, SIG, AD, RQH, RSH`.

---

## 2. Reclassified from the brief's hypotheses

| Resource | Size | Loading method | Consumer | Classification |
|---|---:|---|---|---|
| `CarScannerMaui.EmbeddedFiles.vagmeb.db` | 26,992 | `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2` (**ShiftStream2, no LZMA**) | `CodingListModel.cs:1105` → `List<MQBAdaptationTemplate>` → `List<ICodingContainer>` | **Coding/adaptation templates**, *not* live measurements. Brief's suspicion **confirmed**. Contains `AD`/`RQH`/`RSH`/`PWD` and `AD=09A1` rows, which explains the brief's `0x09A1` candidate — but in an *adaptation* context. |
| `CarScannerMaui.EmbeddedFiles.vag.micro.vag` | 6,204,318 | LZMA-only (`5d 00 00 80 00`, declared size 10,009,474) | `ad.cs` → `VagMicroItem.ReadFromStream` (18-byte records → `VagCode,U,RU,DE,EN,S`) | **Localized DTC text**. Brief's §4.6 caution **confirmed**. Not measurements. |
| `CarScannerMaui.EmbeddedFiles.vag.strings.vag` | 3,222,110 | LZMA-only | `ad.cs` | String tables for the above |
| `CarScannerMaui.EmbeddedFiles.vagplatforms.db` | — | `PackageFileReader` | platform detection | Platform/model metadata |
| `CarScannerMaui.EmbeddedFiles.brands.db` | — | `PackageFileReader` | `ProfileV2Model` brand list | Brand list |

Earlier artefacts in `C:\Users\kings\AppData\Local\Temp\opencode`
(`res_micro_vag`, `res_micro_vag.dec`, `res_micro_vag.dec.shifted`) correspond to
`vag.micro.vag`. Their `.dec` (10,009,474 B) matches the declared LZMA size exactly, so the LZMA
stage is confirmed; `.dec.shifted` was produced with a *linear* position term and is **invalid**
(an arithmetic ramp). The correct term is `p & 0xFF`.

---

## 3. Coding / adaptation / flashing (NOT measurements)

Loaded by `CodingListModel` via `BinaryDeserilzeFromEmbeddedFileUsingCryptoStream2`
(**ShiftStream2**):

`a4b9adaptations.db`, `fabianj.db`, `havalh5.db`, `haval_hut.db`, `hondareset.db`,
`hyundaiparkingbrake.db`, `kiabcm.db`, `kiadashboardvariant.db`, `kiaengine2124actuators.db`,
`kiaenginelowactuators.db`, `kiaservice.db`, `kiasmartjunction.db`, `kiatpms.db`, `leafze0.db`,
`mqbadaptations.db`, `mqb_camera_fusion.db`, `mqb_swap_custom_coding.db`, `pq26adaptations.db`,
`pq26newrapid2020.db`, `pq35adaptations.db`, `q7mq8.db`, `rav4.db`, `t32.db`, `tiggo7plus_bcm.db`,
`tiguan1fl.db`, `tlcprado150.db`, `tnffl.db`, `vagmeb.db`, `yeti.db`, `vestang.db`, `vestam86.db`,
`volvo.db`, `Renault.*.db`.

Coding datasets (unencoded / raw): `vag.mlbevo.backup.*`, `vag.mqb.backup.*`, `vag.pq26*.backup.*`,
`vag.a5_3q0_*.bin`, `vag.a5_60sec.*.bin`, `vag.tailgate6d.*.bin`, `vag.a5tja.*.dat`,
`vag.cantonmib2.*.dat`, `vag.mib3sound*.dat`, `vag.mib2x3b00.*.dat`, `vag.dataset0x*.dat`,
`vag.fpa3Q0907530.*`.

---

## 4. DTC descriptions (NOT measurements)

`CarScannerMaui.EmbeddedFiles.dtcdb.<brand>.db` — 45 files including `dtcdb.VAG.db`,
`dtcdb.OBDII.db`, `dtcdb.BMW.db`, `dtcdb.TOYOTA.db`, plus several `???_` obfuscated names.
Consumed by the DTC decoders (`DTCDecoder.DecodeData`).

---

## 5. Platform / Android assets

| Path | Size | Note |
|---|---:|---|
| `assets/cs_profiles.dat` | 756 | begins `CSINT1`; SHA-256 `A9983C65…4AC27` — matches the brief. **UNRESOLVED**: no decompiled profile loader references it. Build-integrity manifest remains the best hypothesis but is **not proven**. |
| `assets/cs_profiles.sig` | 71 | signature companion; comparison against `cs_profiles.dat` would test the manifest hypothesis |
| `assets/gtsr4.pem`, `int-yr*.pem`, `root-yr.pem`, `we*.pem` | — | TLS roots (certificate pinning) |
| `assets/*.ttf`, `.sfd` | — | fonts |

---

## 6. UNRESOLVED resource

| Resource | Size | Note |
|---|---:|---|
| `jWfF` (in `CarScannerMaui.Droid.dll`) | 307 | SHA-256 `D7DDB455…58E6A`. `an.at` container (marker `lic29B50969`, flags 3 = AES-256-CBC + compressed, 32-byte key). Feeds the Droid Babel string table `d`, which supplies the native library name (`d.a(25993)`) and export name (`d.a(26096)`) for the `ShiftStream` cipher. **Content not decoded**: the compression is the custom LZMA variant `al` with an **LSB-first range decoder**, which liblzma rejects. |
| `TCWy` (in `CarScannerMaui.dll`) | 563 | Same container shape; also uses `al`, so the `af` string table is likewise not yet decoded. Not blocking — resource names were matched from the AOT string dump (`CarScannerMaui.EmbeddedFiles.profiles7.db`, `upd_profiles.db`, `temp_profiles.db`) and by direct resource enumeration. |

No non-`lib*` native library exists in the APK (verified against all 1,861 ZIP entries), so the
`NativeLibrary.Load` target is either a runtime-extracted artefact or one of the standard
`lib/arm64-v8a/*` entries. **UNRESOLVED.**

---

## 7. Raw evidence preserved (not overwritten)

* `original_1.apk` — untouched, hash matches the brief.
* `extracted_assemblies\` — untouched.
* `libassembly-store.so`, `libaot-CarScannerMaui.dll.so`, `cs_profiles.dat` — untouched.
* All new artefacts live in `resources\`, `resources\profiles\`, `decomp\`, `full_decomp\`,
  `tools_cs\`.