# Python-project integration recommendations

**Status:** proposal only. No production file, decoder, registry, catalogue,
database, test, or profile was changed. All new work is under
`research/measurement_discovery/`.

The project already has the right raw material: `measurements.raw_response`
retains bytes, and the registry has explicit `DIDSpec` entries. The problem is
mostly decoder type/offset and output schema, not missing collectors.

## 1. Integration order

| Order | Change | Files / keys | Risk | Tests |
|---:|---|---|---|---|
| 1 | Big-endian float32 decoder for the 25 eldrive rows | `decoders/meb.py` | Low | Raw vectors; 4-byte length check |
| 2 | Expose cell/sensor indices | `decoders/meb.py`, catalogue schema | Low | Tie-aware cross-check |
| 3 | 12 V / DC-DC derived power | new derived metrics or analysis layer | Low | Arithmetic + state test |
| 4 | Fix `0x1E32` four-field decode | `decoders/meb.py`, aliases | Low/medium | Raw block + short block |
| 5 | Pack-current convention metadata/docs | `decoders/meb.py`, `docs/DATA_CATALOGUE.md` | Low | Same-timestamp `0x1E3D`/`0x035E` |
| 6 | Rename `res_pid_0149` | `decoders/meb.py`, catalogue | Low | Raw 0 → 0% |
| 7 | Quarantine CO2/windshield | catalogue status | Low | Invalid-state tests |
| 8 | ABS/GPS/gateway unknowns | separate collectors | Medium/high | Not before V2/V3 |

## 2. Float32 decoder

### Current shape

The profile rows are `FLOAT32(A,B,C,D)`, but the registry uses a u16 field
decoder (roughly `u16be(raw, sbi) * mul / div + ofs`). The raw payload is four
bytes.

### Proposed implementation

```python
import struct

def _float32_be(raw: bytes, offset: int = 0) -> float:
    if len(raw) < offset + 4:
        raise ValueError(
            f"float32 needs 4 bytes at offset {offset}, got {len(raw)}"
        )
    return struct.unpack(">f", raw[offset:offset + 4])[0]
```

Then register the 25 `51.ElDrive` rows with `_float32_be`. Do **not** keep the
u16 scale/offset for these keys. The one non-float row, `eldrive_odometer`
(`0x086D`), stays as-is.

### Regression vectors

Use the raw payloads already in `data/odb_scanner.db`; do not invent values.

| Key | Raw | Expected |
|---|---|---:|
| `electric_drive_inflow_coolant_temperature` | `41816D8E` | 16.1784935 |
| `stator_temperature` | `415C90A2` | 13.7853107 |
| `rotor_temperature_model` | `415656F0` | 13.3962250 |
| `dc_link_voltage` | `43D897DE` | 433.1864624 |
| `dc_link_voltage_reserve` | `43D8B85D` | 433.4403381 |
| `motor_maximum_current` | `44164000` | 601.0 |
| `motor_minimum_current` | `C3260000` | -166.0 |
| `actual_mechanical_torque_calculated` | `3D8BEA4E` | 0.06831799 |
| `dmcm_stator_voltage_d_direction_actual_value` | `342BFA77` | 1.6016735e-07 |
| `inverter_power_loss_phase_u` | `3D989726` | 0.0745 (verify exact float) |

Also test: empty, 2-byte, 3-byte, NaN, +Inf, -Inf. A short payload must fail,
not silently return a truncated u16.

### Why this is the top change

It affects 25 of 26 electric-drive entries and therefore the data behind the
drive-warning investigation. It is a pure type correction, not a new request.

## 3. Cell and temperature indices

The raw bytes already include the index:

- `0x1E33`/`0x1E34`: voltage at bytes 0–1, 1-based cell number at bytes 2–3.
- `0x1E0E`/`0x1E0F`: temperature at bytes 0–1, 1-based sensor number at bytes
  2–3.

### Proposed output

Either return a structured object:

```python
{"value": 4.00390625, "cell": 17}
```

or add sibling keys:

```text
cell_voltage_max          4.00390625 V
cell_voltage_max_cell     17
cell_voltage_min          3.98901367 V
cell_voltage_min_cell     61
bat_temp_max              12.25 degC
bat_temp_max_sensor       7
bat_temp_min              11.5 degC
bat_temp_min_sensor       10
```

### Tie handling

Extrema frequently tie. The current sweep comparison shows 60–99 cells at the
max in some samples. Do not present a single "the weakest cell" unless the
value is unique. Options:

- expose `tied_cells: [17, 18, ...]`; or
- expose the single index plus `tie_count`; or
- label the UI "one of N cells at this value".

### Regression

Use `offline_crosscheck.csv` as the fixture. Assert 1-based indexing and
tie-aware membership. Do not assert an exact single index where the sweep is
tied.

## 4. Energy counters

Replace the two-value `energy_counters` with four fields:

```text
total_charge_ah        u32 @0  * 0.001820444117116
total_discharge_ah     i32 @4  * 0.001820444117116
total_charge_kwh       u32 @8  * 116508.398 / 1e9
total_discharge_kwh    i32 @12 * 116508.398 / 1e9
```

Keep the existing `accum_charge_kwh`/`accum_discharge_kwh` names only if they
can be mapped to the new kWh fields without breaking consumers; otherwise
deprecate them with a migration note.

### Tests

- Full 16-byte block `01E7CC17FE1B74E70C8071E0F3FF868C`.
- All-zero block.
- First 8 bytes only: must fail, not return 0.
- A block with a negative discharge value: preserve sign and provide magnitude
  separately if the UI needs it.

### Note

`ENERGY_COUNT_WH` is currently a divisor for the kWh values. Do not reuse it for
the Ah fields. The Ah scale is `0.001820444117116` Ah/count.

## 5. Pack-current convention

### Do not invert

The code returns negative while discharging, which agrees with the independent
`0x035E` vehicle-info HV current and with the discharge counter. Car Scanner and
ABRP apply a final `* -1` for display. A future "fix" that negates the code
would make it disagree with the other ECU.

### Proposed metadata

Add a field to the catalogue/schema:

```text
sign_convention = "positive = charge; negative = discharge"
```

and fix the docstring/notes. The value column can stay signed. If a UI wants
"discharge power positive", compute it at the presentation layer and label it
explicitly.

### Tests

- Parked: `0x1E3D`, `0x1E3E`, `0x035E` all negative.
- Charge: all positive (or at least the same sign).
- Drive/regen: sign changes consistently.
- Imported Car Scanner CSV rows must not be treated as raw evidence; they
  already contain Car Scanner's negated value.

## 6. Derived metrics

Add these as computed metrics, not new DIDs. They can live in the analysis layer
or the catalogue as `derived=True`.

| Metric | Formula | Notes |
|---|---|---|
| `aux_12v_power_w` | `aux_12v_voltage * aux_12v_current` | Use magnitude and a sign label; check current convention |
| `dcdc_output_power_w` | `dcdc_gw_voltage * dcdc_gw_current` | Cross-check `0x465B/0x465D` |
| `cell_voltage_spread_mv` | `(cell_voltage_max - cell_voltage_min) * 1000` | Equivalent to existing `cell_delta_mv`; keep one canonical name |
| `cell_soc_spread_pct` | `cell_soc_max - cell_soc_min` | Values currently in `text_value` JSON; promote |
| `battery_temp_spread_c` | `bat_temp_max - bat_temp_min` | Pair with sensor IDs |
| `hv_energy_delta_pct` | `(hv_energy_max - hv_energy_content) / hv_energy_max` | Optional; overlaps SoH |
| `a_c_compressor_on` | `a_c_compressor_power > 50 W` | State flag, not a sensor |

### Schema improvement for `cell_soc_max/min`

`value` is currently null and the numeric value/index are in `text_value` JSON.
Promote them to first-class columns so dashboards and SQL can use them:

```text
cell_soc_max          81.67
cell_soc_max_cell     62
cell_soc_min          80.20
cell_soc_min_cell     60
```

## 7. Renames and aliases

| Current | Proposed | Reason |
|---|---|---|
| `res_pid_0149` | `accelerator_pedal_pct` | `0xF449` is explicitly the accelerator pedal in spot2000/evDash |
| `energy_counters` | `lifetime_energy` (or four explicit keys) | The current name hides four fields and the wrong offsets |
| `dc_dc_converter_reserved_current` | keep, mark unknown | 243–252 A is not a current; do not use |
| `hv_serial_vin` | `bms_vin_unsupported` or remove | NRC `0x31`; VIN is on vehicle-info `0xF802` |

Do not remove old keys without a migration/deprecation note; downstream
dashboards and tests may reference them.

## 8. Quarantine, don't guess

For signals whose field/scale is unresolved:

```text
quality = "unverified"
reason = "field/scale unresolved; value not usable"
value = None
raw = <preserve>
```

Apply this to:

- `windshield_temperature` (five-byte payload, impossible value);
- `co2_content_interior` (byte vs u16 unresolved);
- `odometer_2` (×10 vs ×100 unresolved);
- `dc_dc_converter_reserved_current` (impossible current);
- `bat_charging_voltage` (0/sentinel).

A null with a reason is better than a plausible-looking wrong number.

## 9. Collector/schema recommendations

1. **Keep raw bytes for every read.** Already done; do not drop them when
   compacting.
2. **Record the request context:** ECU, DID, service, header, protocol,
   session, timestamp, ignition state, and software version.
3. **Separate imported CSV from raw hex.** Imported Car Scanner rows have a CSV
   line in `raw_response`, not bytes. Tag the source so analysis does not mix
   them.
4. **Add `evidence_level` and `source` to catalogue entries.** The catalogue
   already has a confidence model; use the levels in `evidence_report.md`.
5. **Add a `sign_convention` field** for current/power signals.
6. **Add `quality`/`reason`** for quarantined values.
7. **Do not poll unsupported DIDs.** `hv_serial_vin` on BMS is 63 attempts,
   0 successes. Mark it unsupported and remove it from the slow loop.
8. **Gate session changes.** Keep `0x10 03` out of the normal collector; ABS
   wheel speeds need a separate approved path if ever added.
9. **Add regression fixtures from real bytes.** Use `offline_crosscheck.csv`
   and the raw examples in `decoder_audit.md`.
10. **De-identify logs before sharing.** VIN, serial and GPS are personal data.

## 10. Test plan

| Test | Fixture | Assertion |
|---|---|---|
| Float32 positive | `41816D8E` | 16.1784935 ± 1e-6 |
| Float32 negative | `C3260000` | -166.0 |
| Float32 near zero | `342BFA77` | 1.6e-7, not 1336.5 |
| Float32 short | `4181` | explicit error |
| Energy full | 16-byte block | four expected values |
| Energy short | 8-byte block | explicit error |
| Cell index | `40100011` | cell 17 |
| Temp sensor | `03100007` | sensor 7 |
| Pack current | `000248FD` | -2.43, not +2.43 |
| Pack current cross-check | `FFED` on `0x035E` | -1.9, same sign |
| CO2 | `03FE` | record both hypotheses; no silent choice |
| Windshield | `7A2321C07D` | quality = unverified |
| Odometer | `0005045B...` | quality = unverified until V3 |
| BMS VIN | NRC `0x31` | unsupported, no retry storm |

## 11. Migration / rollout

1. Add the new decoders behind tests.
2. Run the offline suite; do not touch the live database.
3. Re-decode a copy of the last N sessions and compare old/new distributions.
4. Review the diff with the owner.
5. Enable the new decoders in a branch/release.
6. Keep the old columns for one release with a deprecation note.
7. Update `docs/DATA_CATALOGUE.md` and the catalogue version.
8. Do not backfill/overwrite historical rows; add derived values in the
   analysis layer or a new table.

## 12. What not to do

- Do not "fix" pack current by adding `* -1`.
- Do not change the `0x1E32` offsets without the four-field test.
- Do not keep decoding float32 as u16 "because it is already there".
- Do not add ABS wheel speeds to the normal collector without a separate
  session-change approval.
- Do not send `0x22` to `0x2A26/0x2A28/0x2A2F` just because they answered Car
  Scanner; first recover the payload/context.
- Do not infer a fault from GPS location, gear, or a single cell index.
- Do not treat Car Scanner's `VERIFIED` flag as vehicle-specific proof.
