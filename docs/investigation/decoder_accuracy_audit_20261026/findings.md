# Decoder accuracy audit — findings

**Session:** 2026-10-26
**Vehicle context:** 2021 VW ID.3 58 kWh Pro (MEB), profile `meb`
**Scope:** offline accuracy audit of the MEB decoder registry, catalogue, quality
layer, replay path, spec export, and Android decoder.
**Safety:** no vehicle, adapter, diagnostic session, security access, or live
probe. `tools/elm_console.py` and `tools/read_abs_wheel_speeds.py` were not run.
No production database, raw capture, or historical report was rewritten.

Evidence IDs (E01, E02, …) are defined in
[`evidence_register.md`](evidence_register.md).

## Summary

Three defects were demonstrated and fixed:

| ID | Severity | Defect | Disposition |
|---|---|---|---|
| F1 | High | Android gear map disagreed with Python and sign-extended byte 1 | **Fixed** |
| F2 | High | Replay aborted on the first text-valued measurement (`gear = "P"`) | **Fixed** |
| F3 | Medium | `cell_voltage_sum` (DID `0x1E3C`) was labelled DERIVED, so the lab skipped a real DID | **Fixed** |

The remaining findings are confirmations, retained quarantines, or observations
that lack the evidence needed for a safe change. No byte layout, scale, sentinel
rule, INVALID classification, scoring exclusion, or read-only guard was changed.

The current code reproduces every historical real-car row that it can decode
(440/440 unchanged). The larger drift in `data/odb_scanner.db` is historical:
those rows were written by older decoders and are evidence of the repairs that
already landed before this audit, not new regressions (E04, E05).

---

## F1 — Android gear decoding did not match the Python decoder

**Severity:** High — a wrong value on a safety-relevant selector.
**Disposition:** Fixed in `android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt`.

The Python decoder `_gear` reads data byte 1 unsigned and maps
`8 -> P, 7 -> R, 6 -> N, 5 -> D, 12 -> B`, with the whole record as the
fallback. Android had a different map (`0x08 -> P, 0x04 -> R, 0x10 -> N,
0x20 -> D`) and passed `Byte.toInt()`, which sign-extends values above `0x7F`.

The car's captured rows contain 21 × `0008 -> P` and 2 × `000C -> B`.
Under the old Kotlin map, `000C` became `"raw 12"` rather than `"B"` (E02, E10).
This is a real client-parity defect: the same stored DID produced different
answers on the phone and in the Python tools.

The Kotlin decoder now mirrors Python exactly:

```kotlin
when (record[1].toInt() and 0xFF) {
    8 -> "P"; 7 -> "R"; 6 -> "N"; 5 -> "D"; 12 -> "B"
    else -> "raw " + hex(record)
}
```

Regression test: `DecodeTest.gearMatchesPython` covers P/R/N/D/B, a byte-1
value with the top bit set, and the whole-record fallback. Android suite:
52 tests, 0 failures (E14).

**Residual uncertainty:** gear codes are confirmed only for the states captured
on this car (P and B). R/N/D come from the shared `_GEAR_CODES` definition, not
from a captured row. That was already true of Python; the fix makes Android
agree with it rather than inventing a second mapping.

---

## F2 — Offline replay crashed on text-valued measurement rows

**Severity:** High — the validation tool could not complete on the real car.
**Disposition:** Fixed in `analysis/replay.py`.

`measurements.value` is a SQLite TEXT column. It legitimately contains
non-numeric decodes: `gear` is `"P"`/`"B"`, `vin` is a VIN string, and some
JSON/derived rows carry text. `observations_from_measurements()` called
`float(value)` unconditionally. The first text row raised `ValueError`, so
`tools/replay_validate.py` never reached its results, and any caller replaying
the real database could fail the same way.

The function now parses once and skips rows whose value cannot be represented as
a numeric `Observation`. A text value cannot constrain a numeric scale, so
skipping is the honest behaviour; it does not delete or rewrite the row (E15).

Regression test: `test_text_valued_rows_are_skipped_not_crashed_on` feeds a
gear row, a VIN row, and a numeric `pack_voltage` row, and asserts that only the
numeric observation survives with its raw bytes intact.

After the fix, `tools/replay_validate.py` completes on `real_car.db`:
**PASS 7, INDETERMINATE 2**, with the two HV energy layouts replaying 42
real-world records at **max error 0** (E07). Before the fix it aborted before
section 3.

**Residual uncertainty:** none about the crash. Rows skipped here remain
available to non-numeric analyses; this changes only the numeric replay path.

---

## F3 — `cell_voltage_sum` was misclassified as DERIVED

**Severity:** Medium — a real DID was excluded from structural validation.
**Disposition:** Fixed in `decoders/quality.py`.

`cell_voltage_sum` is a registered measurement from DID `0x1E3C`. It was also
listed in `_DERIVED_KEYS`, so `analysis/decoder_lab.py` treated it as a
collector-calculated metric and skipped it. That means no stored payload for
`0x1E3C` was structurally checked, and a real DID was hidden behind a
calculated-data label.

The plural `cells_voltage_sum` is the imported Car Scanner alias and is
genuinely derived; it remains in `_DERIVED_KEYS`. Only the DID-backed singular
key was removed (E15).

Regression test: `test_did_backed_cell_voltage_sum_is_not_labelled_derived`
asserts `cell_voltage_sum` is `DataKind.NUMERIC` and `cells_voltage_sum` remains
`DataKind.DERIVED`.

After the fix the decoder lab evaluates **289/289** catalogue keys. On the real
car the only failures are the five deliberately INVALID keys (E06).

**Residual uncertainty:** whether `0x1E3C` is exactly the sum of the individual
cell-voltage DIDs is a vehicle-consistency question. This fix only stops the
lab from skipping the key; it does not claim the arithmetic relationship is
verified.

---

## F4 — Electric-drive float32 family: current implementation is correct; historical rows are not

**Severity:** High if regressed; no new defect found.
**Disposition:** Retained. No audit code change.

Twenty-five electric-drive measurements are IEEE-754 big-endian `float32` at
offset 0. `_f32be` requires four bytes, unpacks `>f`, and returns `None` for
NaN/Inf. `eldrive_odometer` is the documented exception: a 4-byte integer, not
a float.

The old registry decoded these as `u16 / 10 + 1`, which turned 433.19 V into
~1,738 V and ~16 °C into ~1,682 °C. The current registry produces physically
sensible values. Re-decoding `data/odb_scanner.db` shows 494 changed values and
41 now-failed values across the old rows; the changes are concentrated in this
float32 family and in the energy/sentinel fixes (E05). The real-car database,
written after those repairs, has **0 changed, 0 newly decoded, 0 now failed**
(E04).

No endian, width, or non-finite defect was found in the current float32 path.

**Residual uncertainty:** float32 values are only as accurate as the source DID
definition. The scales and units are inherited from the Car Scanner profile;
the audit confirms the binary interpretation, not the physical calibration.

---

## F5 — `0x1E32` energy counters: four-field signed layout confirmed; old Ah-as-kWh layout refuted

**Severity:** High if the old decoder were still live.
**Disposition:** Retained. No audit code change.

`0x1E32` carries four signed 32-bit fields:

| offset | field | scale |
|---:|---|---|
| 0 | accumulated charge (Ah) | `0.001820444117116` |
| 4 | accumulated discharge (Ah) | `0.001820444117116` |
| 8 | accumulated charge (kWh) | `116508.398 / 1e9` |
| 12 | accumulated discharge (kWh) | `116508.398 / 1e9` |

The old decoder read offsets 0/4 as kWh with evDash's
`8583.07123641215 Wh/count` divisor, producing ~3,724/3,700 kWh. The current
layout produces ~24,437/23,460 kWh from the same bytes, and 28 stored
`energy_counters` rows changed accordingly (E05). The old interpretation is not
a scale disagreement; it described the wrong physical quantity.

**Residual uncertainty:** the four-field layout is profile-derived. The raw
counter values are internally consistent with a 58 kWh pack's lifetime
throughput, but there is no independent VW service record to certify the
absolute lifetime totals.

---

## F6 — Gateway energy DIDs `0x2AB2` / `0x2AB8`: arithmetic confirmed, gross/usable meaning still profile-based

**Severity:** High if regressed; no new defect found.
**Disposition:** Retained. No audit code change.

The live layouts are:

- `hv_energy_max` (`0x2AB2`): `u16be[0:2] × 50 Wh`
- `hv_energy_content` (`0x2AB8`): `u16be[2:4] × 25 Wh`

A 2026-10-15 passive HCI capture of Car Scanner confirmed the addressing
(`0x710 -> 0x77A`), the frame shapes, offsets, and multipliers:
`62 2A B2 04 28 04 00 -> 1,064 × 50 = 53,200 Wh`, and
`62 2A B8 00 00 05 7F ... -> 0x057F × 25 = 35,175 Wh` (E12).

The old `u32be / 1310.77` layout is refuted precisely: 41,325 Wh would require
raw `216670281/4`, which is not an integer. The real-world replay now decodes 42
records at **max error 0** (E07).

**Residual uncertainty:** the arithmetic, addressing, and offsets are VERIFIED.
Whether `0x2AB2` is gross rated energy and `0x2AB8` is currently usable energy
still rests on the profile names, not an independent VW document. The catalogue
retains that limitation.

---

## F7 — `0x1E39` charging voltage: sentinel and signedness quarantined

**Severity:** High — the old decode fabricated 2,047.97 V.
**Disposition:** INVALID and excluded from scoring; retained quarantine.

The Car Scanner profile declares the field signed. The generic decoder ignored
that and read `FFFF` as unsigned, producing 2,047.97 V. The current special
decoder:

- returns `None` for the `FFFF` / `FFFE` sentinels;
- returns `None` for any negative signed value;
- applies the profile scale `0.0312499784501336` (~1/32 V/count) only to a
  non-negative non-sentinel value.

The parked-car captures are `0000`, `0001`, and `0006`, i.e. 0–0.1875 V, which
is consistent with a charging voltage that is effectively zero while parked
(E11).

**Residual uncertainty:** the top of the charging range is untested. A charging
session is needed to confirm that a real charging voltage is not rejected by the
negative guard, and to determine whether `FFFF` is a sentinel, a signed small
negative value, or both. The key remains INVALID/excluded rather than being
promoted on a guess.

---

## F8 — `0x42DB` CO2: `03FE` is a not-fitted sentinel; non-sentinel scale unresolved

**Severity:** High — the old decode fabricated 25,400 ppm.
**Disposition:** INVALID and excluded from scoring; retained quarantine.

All 40 successful `0x42DB` responses are `03FE`. The profile's
`byte 1 × 100` rule gives 25,400 ppm; the whole-`u16` reading gives 1,022 ppm;
and `03FE` is the same not-fitted marker used by unpopulated BMS cell-temperature
slots. The current decoder returns `None` for `03FE` and a whole-`u16` value for
anything else (E11).

**Residual uncertainty:** no non-sentinel frame exists in the stored evidence.
A fresh-air/occupied-cabin test is needed to decide between the whole-`u16`
scale, a byte-1 scale, and "sensor not fitted". The key remains INVALID and
excluded.

---

## F9 — `0x2A53[3:5]` is a complement, not an independent current

**Severity:** High — the old decode fabricated ~250 A.
**Disposition:** INVALID and raw-only; the real current/voltage keys are retained.

Across 124 successful five-byte `0x2A53` payloads,
`u16[1:3] + u16[3:5]` is 1,287–1,291 (~`0x0508`). Bytes 3–4 are the complement
of the real current in bytes 1–2, not a second current. The old
`dc_dc_converter_reserved_current` arithmetic subtracted an offset and produced
~250 A. The key is now raw-only with no decoder, and `dcdc_gw_current` remains
the usable current (E11).

**Residual uncertainty:** the exact purpose of the complement field is unknown.
It is not needed for any current use case, and decoding it as a current would
be wrong. It stays quarantined.

---

## F10 — `0x5867` `odometer_2`: 10 km/LSB delta confirmed; absolute zero unknown

**Severity:** High — using it as the odometer would corrupt distance/SoH logic.
**Disposition:** INVALID and excluded; the x10 delta is retained, absolute use is
not.

The secondary counter advances 10 km per LSB. A ~10.5 km drive moved it by one
count, so the x10 delta matches and x100 is refuted. Its absolute value equals
the main odometer minus **99,869–99,871 km** on this car, consistent with a BMS
replacement/reset at ~99,870 km (E11).

**Residual uncertainty:** service history is unavailable. The absolute zero is
therefore not usable for distance or SoH even though the delta is. The key stays
INVALID/excluded.

---

## F11 — `0x27C4` windshield temperature: byte 0 is plausible; sensor identity unresolved

**Severity:** High — the profile layout produced ~483,000–488,000 °C.
**Disposition:** INVALID and excluded; candidate decode retained only as a
quarantine-aware value.

The profile's `u16[0:2] × 15.625 − 39.5` is arithmetically refuted by the
captured payloads. Byte 0 × 0.1 gives 11–13 °C, within ~1.17 °C MAE of the
same-session outside temperature across the captured rows. The current special
decoder uses byte 0 × 0.1 and leaves the remaining bytes undecoded (E11).

**Residual uncertainty:** the correlation is strong but not an identity proof.
A cold-soak/defroster test is needed to distinguish a true windshield sensor
from an ambient sensor near the glass. The key remains INVALID/excluded.

---

## F12 — IEEE-754 and integer-width audit: no new defect

**Severity:** Informational.
**Disposition:** Retained.

The generated spec contains 118 scalar recipes and 167 special decodes:

- 118/118 scalar recipes are big-endian;
- widths are 1 byte (41), 2 bytes (67), 3 bytes (2), 4 bytes (8);
- 15 scalar recipes are signed;
- 25 special `float32` recipes are 4-byte big-endian at offset 0;
- four keys export `decode: null`.

No scalar was found with a little-endian declaration, a byte order inconsistent
with its source field, or an implicit width conversion that would overflow. The
three-byte and four-byte integer fields are decoded via explicit
`int.from_bytes` widths, and the signed 32-bit energy fields are decoded with
`signed=True` (E08).

Short payloads are not silently padded: field decoders raise `IndexError`, and
`_f32be` raises on a payload shorter than four bytes and returns `None` for
non-finite values. The replay and lab therefore see a failed decode rather than
a plausible-looking number.

**Residual uncertainty:** this is a static/binary audit. It cannot prove that
every source DID really has the declared width; it proves that the declared
widths are implemented consistently and that no width/endianness contradiction
is present in the shipped spec.

---

## F13 — Scaling constants and plausibility ranges: one odd constant, no unsafe change

**Severity:** Low / observation.
**Disposition:** Retained unchanged.

The only non-round scale flagged by the audit is
`0.0312499784501336` on `bat_v_after_contacts_2` and `bat_v_before_contacts`.
It is within 6.9 × 10⁻⁷ relative error of 1/32 V and is declared by the Car
Scanner source. There is no captured independent voltage that would justify
changing it; rewriting it to exactly 0.03125 would be cosmetic and would break
source parity.

Five plausibility ranges extend beyond their encoding range (12 V voltage,
two battery-temperature extremes, the secondary odometer, and speed). These are
intentional physical/administrative bounds, not decoder defects. **No scalar's
plausibility range is disjoint from its encoding range**, which is the condition
that would actually refute a layout (E08).

`0.4425` and `−6.1947` were also reviewed; they are deliberate decimal/fit
constants for pressure/temperature, not transcription artefacts.

**Residual uncertainty:** a broad plausibility bound can hide a wrong scale.
The audit therefore treats ranges as sanity checks, not proof of accuracy.

---

## F14 — Units and data kinds: intentional omissions, one source-declared mismatch

**Severity:** Low / observation.
**Disposition:** Retained unchanged.

The catalogue currently has 13 keys without a unit: the 12 audited scalar keys
plus the concurrently added raw resistance index
`bat_internal_resistance_raw`. The 12 are counts, cell/sensor indices, flap
steps, satellite counts, clean-air level, or an explicitly raw resistance
index—not physical quantities that need a unit. The raw resistance index is
deliberately unitless because its source labels the unit "unknown".

The unit/name screening flags `average_battery_consumption_internal_value` as
"A" for a name containing "consumption". This is declared that way in the Car
Scanner profile and is an internal value, not a validated consumption unit. It
was left unchanged because there is no evidence for a replacement scale/unit;
changing it would be a guess.

Data kinds after F3 (quality layer, 298 entries including 9 derived aliases):
9 derived, 4 JSON, 282 numeric, 3 text. The plural `cells_voltage_sum` remains
derived; the DID-backed singular key is numeric.

**Residual uncertainty:** source-declared units can be wrong. This audit can
show internal consistency, not source correctness.

---

## F15 — Raw payload integrity: clean, with text rows correctly separated

**Severity:** Informational.
**Disposition:** No change.

`data/real_car.db` has 923 successful rows, 920 with raw bytes (3 calculated
rows have no raw). `data/odb_scanner.db` has 11,067 successful rows, 10,917 with
raw bytes. Both contain 266 imported Car Scanner text rows, which are
third-party reported values, not vehicle responses. Derived/calculated rows have
no raw DID payload by design (E08, E09).

Checks across both databases:

- zero non-hex "vehicle raw" payloads;
- zero odd-length hex payloads;
- zero duplicate `(key, timestamp)` rows with conflicting raw bytes;
- zero failed rows that still carry raw bytes;
- `PRAGMA integrity_check` returns `ok` on both databases.

Text-valued keys (`coolant_temps`, `cell_soc_max/min`, `energy_counters`) are
kept as text/JSON; F2 prevents them from entering the numeric replay path.

The quarantined gateway DIDs `0x2A26`, `0x2A28`, and `0x2A2F` remain raw-only,
with no layout invented for them. That is the correct disposition while the
original HCI payloads are unavailable (E11).

**Residual uncertainty:** logical integrity is not provenance. The 266 imported
Car Scanner rows describe values somebody else's software displayed; they are
not raw vehicle responses and are labelled as such in the replay inventory.

---

## F16 — Android spec parity: synced, with a documented quarantine gap

**Severity:** Medium if it drifted; no new drift found.
**Disposition:** Synced asset retained; gear defect fixed under F1.

At audit close, `spec/odb_scanner.json` and
`android/app/src/main/assets/spec.json` are byte-identical
(SHA-256 `483054ED...EF1D6`), both contain 289 keys, and there are zero keys or
measurement bodies that differ. All 12 special decode names used by the spec
have a Kotlin `when` branch (E08, E13).

Three Python special decoders — `bat_charging_voltage`, `co2_content_interior`,
and `windshield_temperature` — have no Kotlin equivalent. Their exported
`decode` is `null`, so Android returns no value rather than a wrong one. This is
a parity gap, not an accuracy defect, and implementing them would require a
deliberate export/asset change plus evidence beyond the current quarantine.

**Residual uncertainty:** the three quarantined keys will show no value on
Android while Python shows a provisional/excluded value. That asymmetry is
visible but safer than manufacturing a decode on the phone.

---

## F17 — Test-name coverage remains incomplete

**Severity:** Low / process risk.
**Disposition:** No tests added merely to raise the count.

At audit close, **80 of 289** catalogue keys are named somewhere in `tests/`;
**209** are not. The baseline was 78/287. The two-key increase comes from the
concurrent autonomous pass, not from this audit's fixes. A key name in a test is
only weak coverage evidence; it does not prove that the arithmetic was checked
against a captured value.

The 209 unnamed keys are listed in `decoder_inventory.md`. Many have no
independent payload evidence, so inventing tests would create the appearance of
validation without the substance. The three fixed defects do have targeted
regression tests (E15).

**Residual uncertainty:** coverage by name understates tests that exercise a
key indirectly through a layout/registry test, and overstates tests that merely
mention a key. A future payload-vector suite would be more meaningful than a
name-count target.

---

## F18 — Remaining uncertainty requires vehicle observations

**Severity:** Not a code defect; a validation limit.
**Disposition:** Retained and documented; no live test performed.

The following questions cannot be resolved by more static analysis or by the
existing stored rows:

| Item | Needed observation |
|---|---|
| `0x1E39` charging voltage | A real charging session, including the top of the range and the meaning of `FFFF` |
| `0x42DB` CO2 | A fresh-air/occupied-cabin sequence with a non-`03FE` frame |
| `0x2A53[3:5]` complement | A known-load DC-DC capture where the second field can move independently |
| `0x5867` absolute counter | Service history or a capture spanning a known BMS reset |
| `0x27C4` windshield sensor | Cold-soak/defroster comparison against a known glass/ambient temperature |
| `0x2A26` / `0x2A28` / `0x2A2F` | Recovery of the original HCI payloads or an approved bounded capture |
| Gear R/N/D | A capture in each selector state |

No vehicle communication was attempted. These items stay INVALID, raw-only, or
PROVISIONAL rather than being "fixed" on plausibility alone.

---

## Changes made by this audit

Only these production-code/test files were changed by this audit:

| File | Change |
|---|---|
| `analysis/replay.py` | F2: skip non-numeric text rows in numeric observations |
| `decoders/quality.py` | F3: remove `cell_voltage_sum` from `_DERIVED_KEYS` |
| `tests/test_catalogue.py` | F2 regression test |
| `tests/test_platform.py` | F3 regression test |
| `android/app/src/main/kotlin/com/odbscanner/spec/Decode.kt` | F1: Python-parity gear decode |
| `android/app/src/test/kotlin/com/odbscanner/spec/DecodeTest.kt` | F1 regression test |

Everything else in the working tree belongs to pre-existing work or the
concurrent autonomous-discovery/dashboard passes and is preserved, not claimed
as an audit change. The generated spec/asset changed during the concurrent pass;
this audit verified it but did not regenerate it.

## Verification

- Full Python suite at audit close: **805 passed** (baseline 777).
- Targeted replay/quality/spec tests after the fixes: **99 passed**, plus
  **3 passed** spec-sync, with `ruff check .` clean (E19).
- Android JVM unit tests, offline and re-run: **52 tests, 0 failures**
  (DecodeTest 11) (E14).
- Catalogue validation: **0 problems**; lab evaluates **289/289** keys.
- Real-car re-decode: **440 rows, 0 changed, 0 new, 0 failed**.
- Production databases: `integrity_check = ok`; no content change was made by
  this audit (see the hash caveat in the evidence register).
