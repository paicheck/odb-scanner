# Decoder implementation impact

Generated: 2026-10-26T20:08:47+00:00
Database: `data\real_car.db` (read-only)
Profile: `meb`

This report re-decodes stored **real-vehicle** raw bytes with the
current registry. It does not rewrite the database. Stored values
come from the old decoders and are shown only for comparison.

## Summary

- Raw vehicle rows re-decoded: **440**
- Values changed by the repairs: **0**
- Values newly decodable: **0**
- Values that now fail to decode: **0**
- Values unchanged: **440**
- Rows skipped (key no longer in registry): 214
- Rows skipped (non-hex raw payload): 0
- Current registry keys with no stored rows: 138

## Keys with changed or newly decoded values

| key | unit | rows | changed | newly decoded | now failed |
|---|---|---:|---:|---:|---:|
| _none_ | | | | | |

## Examples

## New registry keys (no historical rows yet)

These will populate on the next slow rotation:

- `bat_temp_max_sensor`
- `bat_temp_min_sensor`
- `cell_voltage_max_cell`
- `cell_voltage_min_cell`
- `charge_port_normal`
- `charge_port_rapid`

## New calculated metrics

These are computed from reported values and have no raw DID payload:

- `aux_12v_power_w`
- `battery_temp_spread_c`
- `cell_delta_mv`
- `cell_soc_spread_pct`
- `dcdc_gw_power_kw`
- `dcdc_power_kw`
- `pack_power_kw`
- `soh_pct`

## Interpretation

- A large `changed` count on `dc_link_voltage` / the temperature family
  is expected: those rows stored the old u16/10+1 mis-decode.
- `pack_current` should show no value changes: the arithmetic was
  already correct; only its sign documentation and the Car Scanner
  comparison were wrong.
- `energy_counters` changes are old Ah-as-kWh values replaced by the
  four-field Ah/kWh layout.
- The database is deliberately not rewritten. Raw bytes are preserved
  and this report is the reproducible before/after audit.

CSV: `docs/investigation/decoder_accuracy_audit_20261026/impact_real_car.csv`
