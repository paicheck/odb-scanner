# Evidence register — decoder accuracy audit

**Audit:** 2026-10-26
**Vehicle:** 2021 VW ID.3 58 kWh Pro (MEB), profile `meb`
**Repository:** `C:\Users\kings\OneDrive\Documents\odb_scanner`
**HEAD:** `bd86d55dd316c1d5bff659e892719d508189bc3c` — `bd86d55 Add production cloud deploy stack (Docker Compose + Caddy + guide)`

This register records what each claim rests on, how it was produced, and what it
does **not** prove. No adapter was opened and no diagnostic request was sent.

## Evidence quality labels

| Label | Meaning |
|---|---|
| **Vehicle bytes** | A raw DID response captured from this car and stored in `raw_response` |
| **Third-party value** | A value reported by Car Scanner or another tool; real observation, but not a vehicle response |
| **Source definition** | A scale/layout declared by the Car Scanner MEB profile or another published source |
| **Offline derived** | A result computed from the above by a reproducible script in this audit |
| **Static** | A property of the shipped code/spec, not a vehicle measurement |

A third-party value and a source definition can corroborate each other, but
neither becomes a captured vehicle response. The audit keeps that distinction
throughout.

---

## E01 — Repository state and preserved work

**Method:** `git status --short`, `git diff --stat`, `git rev-parse HEAD`,
`git log -1 --format='%h %s'`.

**Observed:**

- HEAD `bd86d55dd316c1d5bff659e892719d508189bc3c`.
- Modified by this audit: `analysis/replay.py`, `decoders/quality.py`,
  `tests/test_catalogue.py`, `tests/test_platform.py`,
  `android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt`,
  `android/app/src/test/kotlin/com/odbscanner/spec/DecodeTest.kt`.
- Pre-existing, preserved: `.gitignore` and the four `cloud/` files.
- Concurrent autonomous-discovery/dashboard work, preserved and not claimed:
  `decoders/meb.py`, `decoders/catalogue.py`,
  `database/repository.py`, `analysis/dtc.py`, the web/dashboard files,
  generated spec/asset changes, `tests/test_gateway_resistance.py`,
  `research/autonomous_discovery_20261026/`, and related investigation
  directories.

**Limitation:** because the concurrent pass ran in the same working tree, a
whole-tree diff cannot attribute every hunk. Attribution in this report is
limited to the six audit files above and the audit directory.

**No commit, push, reset, clean, or checkout was performed.**

---

## E02 — Inventory baseline and current tree

**Artefacts:**

- `docs/investigation/decoder_accuracy_audit_20261026/inventory.py`
  — SHA-256 `1F61147730A45F68BE79664E6E38A42739AF981279818A8D8F76376F1C69A7BF`
- `inventory_baseline.json`
  — SHA-256 `BAE60297B30F6D396A30C018A90B57D86334795309039F870F3C33668F0D7A24`
- `inventory_current.json`
  — SHA-256 `5C970109FB1089D199B3D3DA1C116EF50577C23A5F03393C6C18BD1B0829B2E8`
- `inventory_md.py`
  — SHA-256 `7294F6083CF8AE2A150B2F41AB53E4C9D090576A9C197E03C312F041192535D9`
- `decoder_inventory.md` — generated 289-key inventory

**Command:**

```powershell
python docs/investigation/decoder_accuracy_audit_20261026/inventory.py
```

The script opens databases with `sqlite3.connect("file:...?mode=ro", uri=True)`.
It does not use `Repository` and does not write.

**Key counts:**

| Metric | Baseline | Current |
|---|---:|---:|
| Measurement specs | 287 | 289 |
| Distinct DIDs | 253 | 255 |
| Decoded specs | 286 | 288 |
| Raw-only specs | 1 | 1 |
| Catalogue keys | 287 | 289 |
| Keys with layout | 43 | 45 |
| Keys without layout | 244 | 244 |
| Keys without unit | 12 | 13 |
| Catalogue VERIFIED | 2 | 2 |
| Derived keys | 10 | 9 |
| INVALID keys | 5 | 5 |
| Excluded keys | 5 | 5 |
| Keys named in tests | 78 | 80 |
| Catalogue validation problems | 0 | 0 |

**Delta attribution:** +2 specs, +2 DIDs, +2 layouts, +2 test-named keys, and
+1 unitless key come from the concurrent autonomous pass (`0x2A25`, `0x2A0E`),
not from this audit. The −1 derived key is F3 (`cell_voltage_sum`).

**Limitation:** the baseline JSON was produced before audit edits but after the
working tree already contained preserved unrelated work. It is a baseline for
decoder-visible counts, not a pristine HEAD checkout.

---

## E03 — Test evidence

**Commands and observations:**

| Check | Result |
|---|---|
| Baseline full suite (`python -m pytest -q`) | **777 passed**, 143.59 s |
| Full suite at audit close | **805 passed**, 172.37 s |
| Targeted audit tests | **99 passed**, 13.05 s |
| Spec export sync | **3 passed**, 9.81 s |
| Ruff | `All checks passed!` |
| Android JVM unit tests | **52 tests, 0 failures** (E14) |

The 28-test pytest increase is fully accounted for: 14 tests in the concurrent
`tests/test_gateway_resistance.py`, 12 in the concurrent
`tests/test_timeline_investigation.py`, and 2 Python regression tests from this
audit (F2, F3). No existing test was weakened or deleted.

**Limitation:** a passing test proves the asserted behaviour, not that the
assertion matches the vehicle. F17 records the coverage gap.

---

## E04 — Re-decode impact: `data/real_car.db`

**Artefact:** `impact_real_car.md`
— SHA-256 `67EFCD02C98DCC26B719421BBE3BBFF00D289B70D0DB1F93AD0ED6ED655DD5D4`
(`impact_real_car.csv`
— SHA-256 `125F86ADFF7223C9D5377D0B240E5D89CF9298CC643A190B3F0A1828230A0DA5`).

**Command:**

```powershell
python tools/replay_decoder_impact.py --db data/real_car.db `
  --out docs/investigation/decoder_accuracy_audit_20261026/impact_real_car
```

The tool reads raw bytes with SQLite `mode=ro`, re-decodes with the current
registry, and never writes the source database.

**Result:**

- 440 raw vehicle rows re-decoded;
- **0 changed**, 0 newly decoded, 0 now failed, 440 unchanged;
- 214 rows skipped because their key is no longer in the registry;
- 0 rows skipped for non-hex raw.

**Interpretation:** the current registry reproduces every historical real-car
row it can decode. This is the strongest end-to-end evidence that the current
decoder state is self-consistent with the post-repair captures.

**Limitation:** 440 rows are a subset of all keys; 138 current registry keys have
no stored rows.

---

## E05 — Re-decode impact: `data/odb_scanner.db`

**Artefact:** `impact_odb.md`
— SHA-256 `823434CE33B08AF38F52AA7B93144F12F553F8AABC475777C34B008C18805684`
(`impact_odb.csv`
— SHA-256 `AA94A922AB3E58F96DF65EA5848CCB531E2190E64E24CDD609D4AA05ACE2DFF5`).

**Command:**

```powershell
python tools/replay_decoder_impact.py --db data/odb_scanner.db `
  --out docs/investigation/decoder_accuracy_audit_20261026/impact_odb
```

**Result:**

- 10,419 raw vehicle rows re-decoded;
- **494 changed**, **17 newly decoded**, **41 now failed**;
- 9,867 unchanged;
- 232 rows skipped because the key is no longer in the registry;
- 0 non-hex raw payloads.

The changes are the expected historical repairs, not regressions: the float32
electric-drive family, `0x1E32` energy counters, `0x27C4`, `0x1E39`, `0x42DB`,
and the newly exposed text/JSON rows. The affected rows were written by older
decoders, and the database was deliberately **not** rewritten.

**Limitation:** this report measures drift between old stored values and the
current decoder. It does not prove which side is physically correct for every
row; that is why the passive-capture and replay evidence (E07, E12) is separate.

---

## E06 — Offline decoder-lab sweep

**Artefact:** `lab_sweep.json`
— SHA-256 `241DE82399387643D9C4846B2C41EC500870E5E1776ED4448708EDC7CABBEAED`.

**Command:**

```powershell
python docs/investigation/decoder_accuracy_audit_20261026/lab_sweep.py
```

The script copies each database to
`%TEMP%\odb_audit_20261026\`, opens the copy with `Repository` (which writes
schema metadata), and never opens the original for writing.

**Result:**

| Database | Vehicle | PASS | INDETERMINATE | FAIL |
|---|---:|---:|---:|---:|
| `real_car.db` | 2 | 142 | 142 | 5 |
| `odb_scanner.db` | 1 | 156 | 103 | 30 |

- The 5 real-car failures are exactly the five deliberately INVALID keys.
- The 30 old-database failures are 25 historical stored-vs-current decode
  mismatches plus the same 5 INVALID keys.
- After F3, the lab evaluates **289/289** catalogue keys.

**Limitation:** "INDETERMINATE" means the stored evidence cannot discriminate
the layout. It is not a pass and is not counted as one.

---

## E07 — Replay validation and HV energy replay

**Artefacts:** `replay_validate.md`
— SHA-256 `4054E639A534C76FD855FF7C945DC44F92230BFD5820D518F9A40B8BA4EB1D31`;
`replay_validate.json`
— SHA-256 `D9F5F82C3BA8F8400162157260C89D85760D53D8FDEE9EE516B3A97CE2986479`.

**Command:**

```powershell
python tools/replay_validate.py `
  --out docs/investigation/decoder_accuracy_audit_20261026/replay_validate.md `
  --json-out docs/investigation/decoder_accuracy_audit_20261026/replay_validate.json
```

No `--sync` was used; the command does not write a database.

**Result:** after F2, the report completes with **PASS 7, INDETERMINATE 2**.
The two HV energy layouts replay **42 real-world records at max error 0**.
Catalogue self-validation found **0 problems**.

**Limitation:** the INDETERMINATE entries are the two energy layouts where the
stored third-party values constrain the scale but only the passive capture
(E12) confirms the actual bytes.

---

## E08 — Numeric, unit, and parity audit

**Artefacts:** `numeric_audit.py`
— SHA-256 `FA7B17B5F345D4BC9B04F9C281DC4D18165982B6B9966AE2FC1AEADAF99BAE8A`;
`numeric_audit.json`
— SHA-256 `907E536DD11EF59979E4DA0AA8548B58D810C2A6C511456F9A1B2BE01B431B60`.

**Command:**

```powershell
python docs/investigation/decoder_accuracy_audit_20261026/numeric_audit.py
```

**Findings used in the report:**

- 289 measurements: 118 scalar, 167 special, 4 null.
- All 118 scalar recipes are big-endian; widths 1/2/3/4; 15 signed.
- 25 float32 specials, all 4-byte big-endian at offset 0.
- No scalar's plausibility range is disjoint from its encoding range.
- One odd scale constant: `0.0312499784501336` on two battery-voltage keys,
  within 6.9 × 10⁻⁷ relative error of 1/32, source-declared, unchanged.
- Spec and Android asset: 289 keys each, zero key/body differences.
- All 12 special names used by the spec have a Kotlin case.
- Four `decode: null` keys: `bat_charging_voltage`, `co2_content_interior`,
  `dc_dc_converter_reserved_current`, `windshield_temperature`.

**Limitation:** this is static analysis plus read-only database checks. It
cannot prove a source scale is physically correct.

---

## E09 — Raw payload and database integrity

**Method:** `numeric_audit.py` plus read-only `PRAGMA integrity_check` and
`sqlite3` queries with `mode=ro`.

**Result:**

| Metric | `real_car.db` | `odb_scanner.db` |
|---|---:|---:|
| Rows | 4,057 | 20,798 |
| Successful rows | 923 | 11,067 |
| Successful rows with raw | 920 | 10,917 |
| Imported Car Scanner text rows | 266 | 266 |
| Non-hex vehicle raw | 0 | 0 |
| Odd-length hex | 0 | 0 |
| Duplicate `(key, ts)` with different raw | 0 | 0 |
| Failed rows with raw | 0 | 0 |
| `PRAGMA integrity_check` | `ok` | `ok` |

Raw payload lengths in the collector database include 1, 2, 3, 4, 5, 7, 8, 10,
11, 15, 16, 17, 20, 23, and 28 bytes, consistent with single and multi-frame
responses.

**Limitation:** integrity is not provenance. The 266 imported Car Scanner rows
are third-party values; the audit does not treat them as vehicle responses.

---

## E10 — Gear evidence

**Source:** `inventory.py` output in `inventory_baseline.json` / `_current.json`,
from `data/odb_scanner.db` with `mode=ro`.

**Observed rows:** 21 × `0008 -> P`, 2 × `000C -> B`.
The old Kotlin map returned `"raw 12"` for the B rows. Python returned `"B"` for
both. This is the direct evidence for F1.

**Limitation:** R/N/D were not captured in this dataset. Their codes come from
`_GEAR_CODES`, shared with the source definition; F1 aligns Android to that
definition rather than proving R/N/D on the car.

---

## E11 — Payload research dossier

**Artefact:** `research/measurement_discovery/payload_research_20261021.md`
— SHA-256 `7B618E4A1FC22D8355D195CDEA5941AA3FC884959703FC07F5B90940F1D60C04`.

This dossier contains the per-DID investigations used for F7–F11 and F15:

- `0x2A53`: 124 five-byte payloads; `u16[1:3] + u16[3:5] = 1,287–1,291`
  (~`0x0508`), so bytes 3–5 are a complement, not an independent current.
- `0x5867`: `0x045A -> 11,140 km`, `0x045B -> 11,150 km`; main odometer
  111,009/111,020 km; constant 99,869–99,871 km offset; x10 delta matches,
  x100 refuted.
- `0x27C4`: five-byte payloads `7A2321C07D`, `719F619091`, `729FD1988D`;
  profile u16 formula arithmetically refuted; byte 0 × 0.1 gives 11–13 °C.
- `0x1E39`: profile declares signed; `FFFF`/`FFFE` sentinel; parked samples
  0000/0001/0006.
- `0x42DB`: 40/40 responses are `03FE`; byte-1 ×100 = 25,400 ppm, whole u16 =
  1,022 ppm; `03FE` is also the BMS not-fitted sentinel.
- `0x2A26`, `0x2A28`, `0x2A2F`: no retained payload/layout; correctly
  quarantined.

**Limitation:** the dossier is historical research, not a fresh capture. It
records its own evidence limitations, including missing original HCI payloads
for the three raw-only gateway DIDs.

---

## E12 — Passive gateway energy capture

**Artefact:** `docs/investigation/meb_gateway_energy_passive_capture_20261015.md`
— SHA-256 `00EB6CEC82D5E7CAE48A46CBF7F50943F44AD5EE1CBA9A27B0EEC20F861C7AC0`.

**Captured frames (as quoted in the decoder comments):**

- `62 2A B2 04 28 04 00`: `0x0428 = 1,064`, × 50 = **53,200 Wh**.
- `62 2A B8 00 00 05 7F ...`: `0x057F = 1,407`, × 25 = **35,175 Wh**.
- Addressing: 11-bit `0x710 -> 0x77A`, `ATSH710`, `ATCRA77A`, `ATSP6`.

This confirms the offsets, multipliers, and addressing for `0x2AB2`/`0x2AB8`.
It does not independently confirm gross-vs-usable semantics.

**Limitation:** this is a passive capture of another app's traffic, not a
request sent by this project. That is why the audit did not need vehicle
communication for the layout confirmation.

---

## E13 — Spec / Android asset identity

**Files:**

- `spec/odb_scanner.json`
  — SHA-256 `483054ED324A36C35BE62A470531BDA99972DE0C1C1FD3D1149B344CAC6EF1D6`
  — 265,519 bytes.
- `android/app/src/main/assets/spec.json`
  — same SHA-256 and size.

Both contain 289 keys. The parity check found zero keys only in one file and
zero measurement bodies differing.

**Limitation:** byte identity proves the clients consume the same definitions;
it does not prove those definitions are physically correct.

---

## E14 — Android JVM tests

**Command (from `android/`):**

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat :app:testDebugUnitTest --offline --rerun-tasks
```

**Result:** `BUILD SUCCESSFUL`; **52 tests, 0 failures, 0 errors**, including
`DecodeTest` (11 tests). Reports were read from
`C:\Users\kings\odb-scanner-build\app\test-results\testDebugUnitTest\`.

**Limitation:** these are JVM tests against the generated asset; no Android
device or vehicle was involved.

---

## E15 — Source changes and regression tests

**Files changed by this audit (SHA-256 of current contents):**

| File | SHA-256 |
|---|---|
| `analysis/replay.py` | `91D8E92F28BA4B42E8579A1C6FA684EA148903612622614DF748E45B052FD5F0` |
| `decoders/quality.py` | `1E032B3128712E516FBE9631BD92EB5251FCD6CC56249FFB3D17756F976293C3` |
| `tests/test_catalogue.py` | `8E9BEE016172233EF5A66566EDC097EA371D75F6455843620C89C6DEC4E1F076` |
| `tests/test_platform.py` | `04E1AEDEB97BAC2532C470AA658ACCD2472FD6E7BA191F71A8D7245F9DC83127` |
| `android/.../Decode.kt` | `7F48009C7C09AFC9E84C9F27614BA5B921A38E3138F311D58BA09A28737C74DD` |
| `android/.../DecodeTest.kt` | `0CD91433F765109A39CF5F13DEC9D142AE50754718869F11CBFA7C5CBF581A63` |

**Added tests:**

- `test_text_valued_rows_are_skipped_not_crashed_on` (F2);
- `test_did_backed_cell_voltage_sum_is_not_labelled_derived` (F3);
- `DecodeTest.gearMatchesPython` (F1).

**Limitation:** the source diff is evidence of the change, not of the vehicle
behaviour. F1's captured P/B rows and F2's completed replay provide the
behavioural evidence.

---

## E16 — Concurrent and pre-existing work preserved

The following were present before or alongside this audit and were **not**
removed or reverted:

- `.gitignore` modification adding `cloud/cpanel.env`;
- `cloud/CPANEL.md`, `cloud/cpanel.env.example`, `cloud/passenger_wsgi.py`,
  `cloud/requirements-cpanel.txt`;
- the autonomous-discovery pass's `0x2A25` / `0x2A0E` specs, catalogue overlay,
  generated spec/asset changes, and `tests/test_gateway_resistance.py`;
- dashboard/DTC/timeline work in `web/`, `database/repository.py`,
  `analysis/dtc.py`, and related tests/artefacts.

The current 289-spec counts therefore include two specs that this audit did not
add and does not claim.

**Limitation:** the concurrent pass means some generated artefacts changed while
this audit was running. All hashes here identify the exact contents examined.

---

## E17 — Production database byte-hash caveat

**Current state:** `data/real_car.db` is unchanged at
SHA-256 `181B4D1C1D02612FAC36CFA0205E0AB304C58465123FE1B16D158FB4BFD97DE9`
(2,564,096 bytes). `data/odb_scanner.db` (11,055,104 bytes) is currently held
open by a concurrent dashboard server, which created a WAL/SHM pair. Its main
file hash changes when SQLite checkpoints or rewrites free pages; the last
stable pre-WAL hash observed was
`ED574EA59962ABB232835C11ABA1E44239B972F87F199E2726D7525394647B17`,
and a later read observed
`64ECA8DA4057DE8C1E2E513AEFF776841A6A21BE56CEAE13600BE0219CA84550`.
The byte hash is therefore not a stable immutability measure while the
concurrent server is active.

**Caveat:** the pre-audit session ledger recorded `data/odb_scanner.db` as
`AFD482EC…CE463C`. This audit issued no write to either production database:
`inventory.py`, `numeric_audit.py`, and the integrity checks use `mode=ro`;
`lab_sweep.py` and the impact tools operate on copies or read-only connections;
`Repository` was used only on copies.

A table-by-table comparison through SQLite (which reads the WAL) against the
20:24 working copy (`%TEMP%\odb_audit_20261026\odb_scanner.db`) found **zero
schema differences and zero row differences**. A direct page comparison at the
pre-WAL checkpoint found only pages 1–2 differ, in free-space/header
bookkeeping. That is consistent with SQLite rewriting free-page bookkeeping,
not with a content edit, but the audit cannot attribute the exact writer because
a concurrent dashboard/autonomous process was active in the same tree.

**Conclusion:** no logical database content was changed by this audit; the
byte-level hash is not a reliable immutability proof for this session because of
the concurrent process. `PRAGMA integrity_check` is `ok` on both databases.

---

## E18 — Catalogue validation and lab coverage

**Method:** `validate_catalogue("meb")` inside `inventory.py` and
`lab_sweep.py`.

**Result:** 0 problems. Every declared layout reproduces the decoder that
actually ships under a synthetic probe record. The lab evaluates 289/289
catalogue keys after F3.

**Limitation:** validation checks internal consistency between catalogue layout
and live decoder. It does not validate the layout against the car; that is the
role of replay and captured bytes.

---

## E19 — Targeted post-fix verification

**Commands:**

```powershell
python -m pytest -q tests/test_catalogue.py tests/test_platform.py
python -m pytest -q tests/test_spec_export_in_sync.py
python -m ruff check .
```

**Result:**

- `99 passed` in 13.05 s;
- `3 passed` in 9.81 s;
- `All checks passed!`.

The full suite also completed cleanly at **805 passed** in 172.37 s; it is
recorded in E03 and is not replaced by the targeted run.

---

## E20 — What the evidence does not cover

There is no evidence in this audit for:

- a live charging session (`0x1E39` top-of-range);
- a non-sentinel CO2 frame (`0x42DB`);
- an independent DC-DC load test (`0x2A53[3:5]`);
- service history or a known BMS reset (`0x5867` absolute zero);
- a cold-soak/defroster test (`0x27C4` sensor identity);
- captured gear states R, N, or D;
- fresh payloads for `0x2A26`, `0x2A28`, `0x2A2F`;
- independent calibration for any profile-derived scale.

These are listed as blocked in `progress.md` and `findings.md`, not as resolved
findings. Resolving them requires a separately approved vehicle observation.
