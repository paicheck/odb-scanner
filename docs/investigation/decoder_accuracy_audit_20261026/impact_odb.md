# Decoder implementation impact

Generated: 2026-10-26T20:08:53+00:00
Database: `data\odb_scanner.db` (read-only)
Profile: `meb`

This report re-decodes stored **real-vehicle** raw bytes with the
current registry. It does not rewrite the database. Stored values
come from the old decoders and are shown only for comparison.

## Summary

- Raw vehicle rows re-decoded: **10,419**
- Values changed by the repairs: **494**
- Values newly decodable: **17**
- Values that now fail to decode: **41**
- Values unchanged: **9,867**
- Rows skipped (key no longer in registry): 232
- Rows skipped (non-hex raw payload): 0
- Current registry keys with no stored rows: 5

## Keys with changed or newly decoded values

| key | unit | rows | changed | newly decoded | now failed |
|---|---|---:|---:|---:|---:|
| `actual_mechanical_torque_calculated` | Nm | 26 | 17 | 0 | 0 |
| `actual_mechanical_torque_commanded_value` | Nm | 26 | 17 | 0 | 0 |
| `actual_possible_maximum_torque` | Nm | 26 | 17 | 0 | 0 |
| `actual_possible_minimum_torque` | Nm | 25 | 17 | 0 | 0 |
| `bat_charging_voltage` | V | 19 | 0 | 0 | 1 |
| `battery_serial` | - | 24 | 0 | 17 | 0 |
| `co2_content_interior` | ppm | 47 | 0 | 0 | 40 |
| `dc_link_current` | A | 26 | 17 | 0 | 0 |
| `dc_link_voltage` | V | 26 | 17 | 0 | 0 |
| `dc_link_voltage_reserve` | V | 26 | 17 | 0 | 0 |
| `desired_link_voltage` | V | 26 | 17 | 0 | 0 |
| `dmcm_stator_voltage_d_direction_actual_value` | V | 26 | 17 | 0 | 0 |
| `dmcm_stator_voltage_d_direction_commanded_value` | V | 26 | 17 | 0 | 0 |
| `dmcm_stator_voltage_q_direction_actual_value` | V | 26 | 17 | 0 | 0 |
| `dmcm_stator_voltage_q_direction_commanded_value` | V | 26 | 17 | 0 | 0 |
| `ecu_pcb_temperature_1` | degC | 26 | 17 | 0 | 0 |
| `ecu_pcb_temperature_2` | degC | 26 | 17 | 0 | 0 |
| `electric_drive_inflow_coolant_temperature` | degC | 26 | 17 | 0 | 0 |
| `electric_drive_motor_speed_rad_sec` | - | 26 | 17 | 0 | 0 |
| `energy_counters` | kWh | 35 | 28 | 0 | 0 |
| `inverter_power_loss_phase_u` | W | 26 | 17 | 0 | 0 |
| `inverter_power_loss_phase_v` | W | 26 | 17 | 0 | 0 |
| `inverter_power_loss_phase_w` | W | 26 | 17 | 0 | 0 |
| `motor_maximum_current` | A | 26 | 17 | 0 | 0 |
| `motor_minimum_current` | A | 26 | 17 | 0 | 0 |
| `rotor_temperature_model` | degC | 26 | 17 | 0 | 0 |
| `stator_electrical_frequency_rad_sec` | - | 26 | 17 | 0 | 0 |
| `stator_temperature` | degC | 26 | 17 | 0 | 0 |
| `torque_calculated_from_measured_currents` | Nm | 27 | 17 | 0 | 0 |
| `windshield_temperature` | degC | 48 | 41 | 0 | 0 |

## Examples

### `actual_mechanical_torque_calculated`
- `2026-10-18T13:53:18+00:00` raw `BD9AE38A`: old `4854.8` -> new `-0.0756293`
- `2026-10-18T13:57:05+00:00` raw `BDA494C6`: old `4855.8` -> new `-0.0803619`
- `2026-10-18T14:00:48+00:00` raw `BE1CBC22`: old `4867.8` -> new `-0.153061`

### `actual_mechanical_torque_commanded_value`
- `2026-10-18T13:53:18+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T13:57:05+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T14:00:48+00:00` raw `00000000`: old `1` -> new `0`

### `actual_possible_maximum_torque`
- `2026-10-18T13:53:18+00:00` raw `BDBC649F`: old `4858.2` -> new `-0.0919888`
- `2026-10-18T13:57:05+00:00` raw `BD87EDCD`: old `4852.9` -> new `-0.0663715`
- `2026-10-18T14:00:48+00:00` raw `BDC1AFE9`: old `4858.7` -> new `-0.0945738`

### `actual_possible_minimum_torque`
- `2026-10-18T13:53:18+00:00` raw `BDB7AA95`: old `4857.7` -> new `-0.0896808`
- `2026-10-18T13:57:05+00:00` raw `BDF1F845`: old `4863.5` -> new `-0.118149`
- `2026-10-18T14:00:48+00:00` raw `BDCE8F41`: old `4860` -> new `-0.100859`

### `bat_charging_voltage`
- `2026-10-18T18:22:50+00:00` raw `FFFF`: old `2047.97` -> new `None`

### `battery_serial`
- `2026-10-18T13:53:18+00:00` raw `32383820565741524D464632363520`: old `None` -> new `288 VWARMFF265`
- `2026-10-18T13:57:05+00:00` raw `32383820565741524D464632363520`: old `None` -> new `288 VWARMFF265`
- `2026-10-18T14:00:48+00:00` raw `32383820565741524D464632363520`: old `None` -> new `288 VWARMFF265`

### `co2_content_interior`
- `2026-10-16T17:09:44+00:00` raw `03FE`: old `25400` -> new `None`
- `2026-10-17T16:47:34+00:00` raw `03FE`: old `25400` -> new `None`
- `2026-10-17T16:51:22+00:00` raw `03FE`: old `25400` -> new `None`

### `dc_link_current`
- `2026-10-18T13:53:18+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T13:57:05+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T14:00:48+00:00` raw `00000000`: old `1` -> new `0`

### `dc_link_voltage`
- `2026-10-18T13:53:18+00:00` raw `43DAA406`: old `1738` -> new `437.281`
- `2026-10-18T13:57:05+00:00` raw `43DAAA0F`: old `1738` -> new `437.329`
- `2026-10-18T14:00:48+00:00` raw `43DAAA6C`: old `1738` -> new `437.331`

### `dc_link_voltage_reserve`
- `2026-10-18T13:53:18+00:00` raw `43DAFC26`: old `1738` -> new `437.97`
- `2026-10-18T13:57:05+00:00` raw `43DAD753`: old `1738` -> new `437.682`
- `2026-10-18T14:00:48+00:00` raw `43DAE42D`: old `1738` -> new `437.783`

### `desired_link_voltage`
- `2026-10-18T13:53:18+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T13:57:05+00:00` raw `00000000`: old `1` -> new `0`
- `2026-10-18T14:00:48+00:00` raw `00000000`: old `1` -> new `0`

### `dmcm_stator_voltage_d_direction_actual_value`
- `2026-10-18T13:53:18+00:00` raw `B595DFEF`: old `4649.5` -> new `-1.11665e-06`
- `2026-10-18T13:57:05+00:00` raw `B5AC4A49`: old `4651.8` -> new `-1.28366e-06`
- `2026-10-18T14:00:48+00:00` raw `B603FECA`: old `4660.5` -> new `-1.96688e-06`

### `dmcm_stator_voltage_d_direction_commanded_value`
- `2026-10-18T13:53:18+00:00` raw `B17BF1AF`: old `4544.5` -> new `-3.66627e-09`
- `2026-10-18T13:57:05+00:00` raw `B1EF239E`: old `4556.1` -> new `-6.95986e-09`
- `2026-10-18T14:00:48+00:00` raw `B20C1128`: old `4559` -> new `-8.15297e-09`

### `dmcm_stator_voltage_q_direction_actual_value`
- `2026-10-18T13:53:18+00:00` raw `39F09FAD`: old `1484.2` -> new `0.000458953`
- `2026-10-18T13:57:05+00:00` raw `B9808870`: old `4749.8` -> new `-0.000245157`
- `2026-10-18T14:00:48+00:00` raw `3A0CB7FE`: old `1487` -> new `0.000536799`

### `dmcm_stator_voltage_q_direction_commanded_value`
- `2026-10-18T13:53:18+00:00` raw `3A56160B`: old `1494.4` -> new `0.000816674`
- `2026-10-18T13:57:05+00:00` raw `B961BBFD`: old `4746.7` -> new `-0.000215277`
- `2026-10-18T14:00:48+00:00` raw `3A0DEA58`: old `1487.1` -> new `0.000541364`

### `ecu_pcb_temperature_1`
- `2026-10-18T13:53:18+00:00` raw `41B7B112`: old `1683.3` -> new `22.9615`
- `2026-10-18T13:57:05+00:00` raw `41B9611A`: old `1683.5` -> new `23.1724`
- `2026-10-18T14:00:48+00:00` raw `41B9CD1D`: old `1683.5` -> new `23.2252`

### `ecu_pcb_temperature_2`
- `2026-10-18T13:53:18+00:00` raw `41C23D46`: old `1684.4` -> new `24.2799`
- `2026-10-18T13:57:05+00:00` raw `41C3154A`: old `1684.5` -> new `24.3854`
- `2026-10-18T14:00:48+00:00` raw `41C3ED4E`: old `1684.5` -> new `24.4909`

### `electric_drive_inflow_coolant_temperature`
- `2026-10-18T13:53:18+00:00` raw `419243B6`: old `1679.6` -> new `18.2831`
- `2026-10-18T13:57:05+00:00` raw `4194607E`: old `1679.8` -> new `18.5471`
- `2026-10-18T14:00:48+00:00` raw `41945F8F`: old `1679.8` -> new `18.5467`

### `electric_drive_motor_speed_rad_sec`
- `2026-10-18T13:53:18+00:00` raw `3A99B2F8`: old `1501.1` -> new `0.00117263`
- `2026-10-18T13:57:05+00:00` raw `39472200`: old `1467.3` -> new `0.000189908`
- `2026-10-18T14:00:48+00:00` raw `BB01163C`: old `4788.3` -> new `-0.00196971`

### `energy_counters`
- `2026-10-18T13:17:57+00:00` raw `01E7C77DFE1B855C0C805292F3FFF5A9`: old `{"charged_kwh": 3724.436174359741, "used_kwh": 3699.237618499868}` -> new `{"charged_ah": 58194.320956703, "charged_kwh": 24436.044742766524, "discharged_ah": 57800.59348260903, "discharged_kwh": 23456.54710644912, "used_kwh": 23456.54710644912}`
- `2026-10-18T13:20:31+00:00` raw `01E7C77DFE1B851D0C805292F3FFF3FC`: old `{"charged_kwh": 3724.436174359741, "used_kwh": 3699.2449585298255}` -> new `{"charged_ah": 58194.320956703, "charged_kwh": 24436.044742766524, "discharged_ah": 57800.70817058841, "discharged_kwh": 23456.597088551865, "used_kwh": 23456.597088551865}`
- `2026-10-18T13:23:01+00:00` raw `01E7C77DFE1B84E10C805292F3FFF261`: old `{"charged_kwh": 3724.436174359741, "used_kwh": 3699.251949034546}` -> new `{"charged_ah": 58194.320956703, "charged_kwh": 24436.044742766524, "discharged_ah": 57800.817397235434, "discharged_kwh": 23456.64497350344, "used_kwh": 23456.64497350344}`

### `inverter_power_loss_phase_u`
- `2026-10-18T13:53:18+00:00` raw `3DC8DB37`: old `1.03657e+08` -> new `0.0980744`
- `2026-10-18T13:57:05+00:00` raw `3C33FCBE`: old `1.01004e+08` -> new `0.0109856`
- `2026-10-18T14:00:48+00:00` raw `3E2441D6`: old `1.04256e+08` -> new `0.160407`

### `inverter_power_loss_phase_v`
- `2026-10-18T13:53:18+00:00` raw `3F8BDF26`: old `1.06613e+08` -> new `1.09275`
- `2026-10-18T13:57:05+00:00` raw `3F6B85C2`: old `1.06401e+08` -> new `0.92001`
- `2026-10-18T14:00:48+00:00` raw `3F6B35AD`: old `1.06399e+08` -> new `0.918788`

### `inverter_power_loss_phase_w`
- `2026-10-18T13:53:18+00:00` raw `3F1027C4`: old `1.05802e+08` -> new `0.563107`
- `2026-10-18T13:57:05+00:00` raw `3EC7DB3B`: old `1.05329e+08` -> new `0.390344`
- `2026-10-18T14:00:48+00:00` raw `3F3C41D1`: old `1.06091e+08` -> new `0.735379`

### `motor_maximum_current`
- `2026-10-18T13:53:18+00:00` raw `44176000`: old `1744.1` -> new `605.5`
- `2026-10-18T13:57:05+00:00` raw `44176000`: old `1744.1` -> new `605.5`
- `2026-10-18T14:00:48+00:00` raw `44176000`: old `1744.1` -> new `605.5`

### `motor_minimum_current`
- `2026-10-18T13:53:18+00:00` raw `C3118000`: old `4994.7` -> new `-145.5`
- `2026-10-18T13:57:05+00:00` raw `C3120000`: old `4994.8` -> new `-146`
- `2026-10-18T14:00:48+00:00` raw `C3120000`: old `4994.8` -> new `-146`

### `rotor_temperature_model`
- `2026-10-18T13:53:18+00:00` raw `416E1C08`: old `1676` -> new `14.8818`
- `2026-10-18T13:57:05+00:00` raw `4170D86F`: old `1676.2` -> new `15.0528`
- `2026-10-18T14:00:48+00:00` raw `41738E01`: old `1676.5` -> new `15.2222`

### `stator_electrical_frequency_rad_sec`
- `2026-10-18T13:53:18+00:00` raw `3C09B4C2`: old `1537.9` -> new `0.00840491`
- `2026-10-18T13:57:05+00:00` raw `3CD82111`: old `1558.6` -> new `0.026383`
- `2026-10-18T14:00:48+00:00` raw `BC3FC38E`: old `4820.1` -> new `-0.0117043`

### `stator_temperature`
- `2026-10-18T13:53:18+00:00` raw `417CA882`: old `1677.4` -> new `15.7911`
- `2026-10-18T13:57:05+00:00` raw `417EAEFD`: old `1677.6` -> new `15.9177`
- `2026-10-18T14:00:48+00:00` raw `417EAEFD`: old `1677.6` -> new `15.9177`

### `torque_calculated_from_measured_currents`
- `2026-10-18T13:53:18+00:00` raw `BE1CB07D`: old `4866.8` -> new `-0.153017`
- `2026-10-18T13:57:05+00:00` raw `BDE7198D`: old `4861.5` -> new `-0.112842`
- `2026-10-18T14:00:48+00:00` raw `BD7F7E3E`: old `4851.1` -> new `-0.0623763`

### `windshield_temperature`
- `2026-10-16T17:09:44+00:00` raw `81E7D1FC60`: old `519570` -> new `12.9`
- `2026-10-17T16:47:34+00:00` raw `7B61E1B092`: old `493476` -> new `12.3`
- `2026-10-17T16:51:22+00:00` raw `7B2241B88A`: old `492492` -> new `12.3`

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

CSV: `docs/investigation/decoder_accuracy_audit_20261026/impact_odb.csv`
