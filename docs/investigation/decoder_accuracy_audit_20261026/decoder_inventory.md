# Decoder inventory — MEB profile (2021 VW ID.3 58 kWh)

Generated from the live registry, catalogue, quality layer and `spec/odb_scanner.json` by `inventory_md.py`. No vehicle was contacted.

- Measurement specs: **289**
- Catalogue keys: **289**
- Distinct DID numbers: **255**
- Distinct (ECU, DID) associations: **255**
- Keys with a declared byte layout: **45**
- Keys without a declared layout: **244**
- Keys named anywhere in `tests/`: **80**; not named: **209**

## Decode shapes

| shape | keys |
|---|---:|
| `special:cell_voltage` | 108 |
| `u16be` | 56 |
| `u8be` | 39 |
| `special:float32` | 25 |
| `special:cell_temp` | 24 |
| `s16be` | 11 |
| `u32be` | 6 |
| `—` | 4 |
| `s32be` | 2 |
| `s8be` | 2 |
| `special:pair` | 2 |
| `u24be` | 2 |
| `special:ascii` | 1 |
| `special:bit2` | 1 |
| `special:bit3` | 1 |
| `special:charge_mode` | 1 |
| `special:coolant_temps` | 1 |
| `special:energy_counters` | 1 |
| `special:gear` | 1 |
| `special:vin` | 1 |

## Confidence and quality

| layer | value | keys |
|---|---|---:|
| catalogue confidence | PROVISIONAL | 45 |
| catalogue confidence | UNKNOWN | 242 |
| catalogue confidence | VERIFIED | 2 |
| verification | invalid | 5 |
| verification | provisional | 51 |
| verification | unknown | 240 |
| verification | verified | 2 |
| data kind | derived | 9 |
| data kind | json | 4 |
| data kind | numeric | 282 |
| data kind | text | 3 |

## Keys that must not feed scoring

| key | DID | unit | reason |
|---|---|---|---|
| `bat_charging_voltage` | `0x1E39` | V | profile declares signed; unsigned 0xFFFF became 2,047.97 V. Sentinel/negative values now decode to None, not a voltage |
| `co2_content_interior` | `0x42DB` | ppm | 40/40 responses are 03FE; byte 1 x 100 gives 25,400 ppm, whole u16 gives 1,022 ppm, and 03FE is also the BMS not-fitted sentinel. 03FE now decodes to None; a non-sentinel 16-bit scale is still unconfirmed |
| `dc_dc_converter_reserved_current` | `0x2A53` | — | bytes 3-4 are the complement of the real current in bytes 1-2 (sum ~0x0508), not an independent current; raw-only, use dcdc_gw_current |
| `odometer_2` | `0x5867` | km | 10 km/LSB secondary distance counter, not the odometer: x10 matched a ~10.5 km drive and x100 is refuted. It equals the main odometer minus 99,869-99,871 km on this car, consistent with a BMS reset at ~99,870 km; service history is unavailable, so the absolute zero is still not usable for distance/SoH |
| `windshield_temperature` | `0x27C4` | degC | the profile u16[0:2] x 15.625 - 39.5 read ~483476 degC; the decoder now uses byte 0 x 0.1 (11-13 degC, ~1.17 degC MAE versus outside) but windshield-vs-ambient still needs a cold-soak/defroster test |

## DIDs that carry more than one key

| DID | keys |
|---|---|
| `0x028C` | `soc_abs`, `soc_normal` |
| `0x15D1` | `hv_em_hv_battery_energy_information`, `hv_em_nv_power_limitation`, `hv_em_power_limit`, `nv_energy_requirement` |
| `0x1E0E` | `bat_temp_max`, `bat_temp_max_sensor` |
| `0x1E0F` | `bat_temp_min`, `bat_temp_min_sensor` |
| `0x1E33` | `cell_voltage_max`, `cell_voltage_max_cell` |
| `0x1E34` | `cell_voltage_min`, `cell_voltage_min_cell` |
| `0x2431` | `number_of_tracked_satellites`, `number_of_visible_satellites` |
| `0x261E` | `fresh_air_recirculation_flap_actual_value`, `fresh_air_recirculation_flap_actual_value_steps` |
| `0x27C4` | `cabin_humidity`, `windshield_temperature` |
| `0x2A53` | `dc_dc_converter_reserved_current`, `dcdc_gw_current`, `dcdc_gw_voltage` |
| `0x2AB4` | `average_battery_consumption_internal_value`, `range_internal` |
| `0x2AB7` | `road_avg_consumption_0`, `road_avg_consumption_1`, `road_avg_consumption_2`, `road_avg_consumption_3`, `road_avg_consumption_4`, `road_avg_consumption_5`, `road_avg_consumption_6` |
| `0x2AF7` | `aux_12v_adapted_capacity`, `aux_12v_aging_capacity`, `aux_12v_aging_power`, `aux_12v_current`, `aux_12v_sensor_temp`, `aux_12v_soc`, `aux_12v_temp`, `aux_12v_total_charge`, `aux_12v_total_discharge`, `aux_12v_voltage` |
| `0x4EEF` | `a_c_refrigerant_pressure_and_temperature_sensor_1_interior_temperature`, `a_c_refrigerant_pressure_and_temperature_sensor_1_pressure`, `a_c_refrigerant_pressure_and_temperature_sensor_1_temperature` |
| `0x7448` | `charge_mode`, `charge_port_normal`, `charge_port_rapid`, `op_mode` |

## Full inventory

`decoder` shows the exported arithmetic (`scale/divisor/addend`) or the special decoder name. `layout` means the catalogue carries a byte layout that `validate_catalogue` checks against the live decoder. `test` means the key name appears somewhere in `tests/`, which is coverage evidence, not proof that the arithmetic is independently validated.

| key | DID | ECU | unit | decoder | confidence | verification | kind | layout | test |
|---|---|---|---|---|---|---|---|---|---|
| `a_c_compressor_load` | `0x2604` | `climate` | Nm | `u8be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `a_c_compressor_power_consumption_actual_value` | `0x0800` | `climate` | W | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `a_c_compressor_speed` | `0x2603` | `climate` | rpm | `u8be x 100/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `a_c_refrigerant_pressure_and_temperature_sensor_1_interior_temperature` | `0x4EEF` | `climate` | degC | `u16be x 10/100 + -40` | UNKNOWN | unknown | numeric | — | — |
| `a_c_refrigerant_pressure_and_temperature_sensor_1_pressure` | `0x4EEF` | `climate` | bar | `u16be x 88/10000 + 0` | UNKNOWN | unknown | numeric | — | — |
| `a_c_refrigerant_pressure_and_temperature_sensor_1_temperature` | `0x4EEF` | `climate` | degC | `u16be x 10/100 + -40` | UNKNOWN | unknown | numeric | — | — |
| `acceleration` | `0x4539` | `veh_info` | m/s2 | `s16be x 0.001/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `actual_mechanical_torque_calculated` | `0x3EE9` | `eldrive` | Nm | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `actual_mechanical_torque_commanded_value` | `0x58E1` | `eldrive` | Nm | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `actual_possible_maximum_torque` | `0x3EEA` | `eldrive` | Nm | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `actual_possible_minimum_torque` | `0x3EEB` | `eldrive` | Nm | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `additional_high_voltage_consumers_total_current` | `0x0360` | `veh_info` | A | `s16be x 0.01/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `air_polution_level_0_clean_air` | `0x26ED` | `climate` | — | `s8be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_adapted_capacity` | `0x2AF7` | `energy` | Ah | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_aging_capacity` | `0x2AF7` | `energy` | % | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_aging_power` | `0x2AF7` | `energy` | % | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_current` | `0x2AF7` | `energy` | A | `s32be x 0.001/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `aux_12v_sensor_temp` | `0x2AF7` | `energy` | degC | `u8be x 1/1 + -40` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_soc` | `0x2AF7` | `energy` | % | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_temp` | `0x2AF7` | `energy` | degC | `u8be x 1/1 + -40` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_total_charge` | `0x2AF7` | `energy` | Ah | `u32be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_total_discharge` | `0x2AF7` | `energy` | Ah | `u32be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `aux_12v_voltage` | `0x2AF7` | `energy` | V | `u16be x 0.001/1 + 4` | UNKNOWN | unknown | numeric | — | yes |
| `aux_battery_resistance_mohm` | `0x2A0E` | `energy` | mOhm | `u8be x 2/10 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `average_battery_consumption_internal_value` | `0x2AB4` | `energy` | A | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `bat_charging_voltage` | `0x1E39` | `bat_mgmt` | V | `—` | PROVISIONAL | invalid | numeric | yes | yes |
| `bat_internal_resistance_raw` | `0x2A25` | `energy` | — | `u8be x 1/5 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `bat_temp_max` | `0x1E0E` | `bat_mgmt` | degC | `u16be x 0.015625/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `bat_temp_max_sensor` | `0x1E0E` | `bat_mgmt` | — | `u16be x 1/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `bat_temp_min` | `0x1E0F` | `bat_mgmt` | degC | `u16be x 0.015625/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `bat_temp_min_sensor` | `0x1E0F` | `bat_mgmt` | — | `u16be x 1/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `bat_v_after_contacts` | `0x189B` | `bat_mgmt` | V | `u16be x 0.5/1 + -1000` | UNKNOWN | unknown | numeric | — | yes |
| `bat_v_after_contacts_2` | `0x1E38` | `bat_mgmt` | V | `u16be x 0.03125/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `bat_v_before_contacts` | `0x1E36` | `bat_mgmt` | V | `u16be x 0.03125/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `battery_serial` | `0x0500` | `bat_mgmt` | — | `special:ascii` | PROVISIONAL | provisional | text | — | yes |
| `battery_soc_normalized` | `0x3DEF` | `veh_info` | % | `s16be x 0.01/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `battery_temp` | `0x2A0B` | `bat_mgmt` | degC | `u8be x 0.5/1 + -40` | PROVISIONAL | provisional | numeric | yes | yes |
| `bms_pcb_temp` | `0x1622` | `bat_mgmt` | degC | `u8be x 1/1 + -50` | UNKNOWN | unknown | numeric | — | yes |
| `brake_booster_pressure` | `0x2005` | `veh_info` | bar | `u16be x 0.001/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `cabin_humidity` | `0x27C4` | `climate` | % | `u8be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `cell_soc_max` | `0x1E2C` | `bat_mgmt` | % | `special:pair` | UNKNOWN | unknown | json | — | yes |
| `cell_soc_min` | `0x1E2D` | `bat_mgmt` | % | `special:pair` | UNKNOWN | unknown | json | — | yes |
| `cell_t_00` | `0x1EAE` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_01` | `0x1EAF` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_02` | `0x1EB0` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_03` | `0x1EB1` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_04` | `0x1EB2` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_05` | `0x1EB3` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_06` | `0x1EB4` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_07` | `0x1EB5` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_08` | `0x1EB6` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_09` | `0x1EB7` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_10` | `0x1EB8` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_11` | `0x1EB9` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_12` | `0x1EBA` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_13` | `0x1EBB` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_14` | `0x1EBC` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_15` | `0x1EBD` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_16` | `0x7425` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_17` | `0x7426` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | — |
| `cell_t_18` | `0x7427` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_19` | `0x7428` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_20` | `0x7429` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_21` | `0x742A` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_22` | `0x742B` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_t_23` | `0x742C` | `bat_mgmt` | degC | `special:cell_temp` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_000` | `0x1E40` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_001` | `0x1E41` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_002` | `0x1E42` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_003` | `0x1E43` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_004` | `0x1E44` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_005` | `0x1E45` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_006` | `0x1E46` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_007` | `0x1E47` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_008` | `0x1E48` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_009` | `0x1E49` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_010` | `0x1E4A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_011` | `0x1E4B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_012` | `0x1E4C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_013` | `0x1E4D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_014` | `0x1E4E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_015` | `0x1E4F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_016` | `0x1E50` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_017` | `0x1E51` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_018` | `0x1E52` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_019` | `0x1E53` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_020` | `0x1E54` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_021` | `0x1E55` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_022` | `0x1E56` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_023` | `0x1E57` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_024` | `0x1E58` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_025` | `0x1E59` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_026` | `0x1E5A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_027` | `0x1E5B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_028` | `0x1E5C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_029` | `0x1E5D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_030` | `0x1E5E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_031` | `0x1E5F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_032` | `0x1E60` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_033` | `0x1E61` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_034` | `0x1E62` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_035` | `0x1E63` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_036` | `0x1E64` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_037` | `0x1E65` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_038` | `0x1E66` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_039` | `0x1E67` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_040` | `0x1E68` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_041` | `0x1E69` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_042` | `0x1E6A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_043` | `0x1E6B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_044` | `0x1E6C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_045` | `0x1E6D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_046` | `0x1E6E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_047` | `0x1E6F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_048` | `0x1E70` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_049` | `0x1E71` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_050` | `0x1E72` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_051` | `0x1E73` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_052` | `0x1E74` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_053` | `0x1E75` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_054` | `0x1E76` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_055` | `0x1E77` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_056` | `0x1E78` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_057` | `0x1E79` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_058` | `0x1E7A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_059` | `0x1E7B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_060` | `0x1E7C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_061` | `0x1E7D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_062` | `0x1E7E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_063` | `0x1E7F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_064` | `0x1E80` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_065` | `0x1E81` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_066` | `0x1E82` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_067` | `0x1E83` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_068` | `0x1E84` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_069` | `0x1E85` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_070` | `0x1E86` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_071` | `0x1E87` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_072` | `0x1E88` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_073` | `0x1E89` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_074` | `0x1E8A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_075` | `0x1E8B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_076` | `0x1E8C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_077` | `0x1E8D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_078` | `0x1E8E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_079` | `0x1E8F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_080` | `0x1E90` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_081` | `0x1E91` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_082` | `0x1E92` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_083` | `0x1E93` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_084` | `0x1E94` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_085` | `0x1E95` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_086` | `0x1E96` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_087` | `0x1E97` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_088` | `0x1E98` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_089` | `0x1E99` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_090` | `0x1E9A` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_091` | `0x1E9B` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_092` | `0x1E9C` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_093` | `0x1E9D` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_094` | `0x1E9E` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_095` | `0x1E9F` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_096` | `0x1EA0` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_097` | `0x1EA1` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_098` | `0x1EA2` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_099` | `0x1EA3` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_100` | `0x1EA4` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_101` | `0x1EA5` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_102` | `0x1EA6` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_103` | `0x1EA7` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_104` | `0x1EA8` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | — |
| `cell_v_105` | `0x1EA9` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_106` | `0x1EAA` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_v_107` | `0x1EAB` | `bat_mgmt` | V | `special:cell_voltage` | UNKNOWN | unknown | numeric | — | yes |
| `cell_voltage_max` | `0x1E33` | `bat_mgmt` | V | `u16be x 0.000244141/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `cell_voltage_max_cell` | `0x1E33` | `bat_mgmt` | — | `u16be x 1/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `cell_voltage_min` | `0x1E34` | `bat_mgmt` | V | `u16be x 0.000244141/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `cell_voltage_min_cell` | `0x1E34` | `bat_mgmt` | — | `u16be x 1/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `cell_voltage_sum` | `0x1E3C` | `bat_mgmt` | V | `u32be x 244141/1e+09 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `charge_mode` | `0x7448` | `bat_mgmt` | code | `special:charge_mode` | UNKNOWN | unknown | numeric | — | yes |
| `charge_port_normal` | `0x7448` | `bat_mgmt` | — | `special:bit2` | PROVISIONAL | provisional | numeric | — | — |
| `charge_port_rapid` | `0x7448` | `bat_mgmt` | — | `special:bit3` | PROVISIONAL | provisional | numeric | — | — |
| `co2_content_interior` | `0x42DB` | `climate` | ppm | `—` | UNKNOWN | invalid | numeric | yes | yes |
| `coolant_circulation_pump_duty` | `0x276D` | `veh_info` | % | `u16be x 0.01/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `coolant_pump` | `0x743B` | `bat_mgmt` | % | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `coolant_temps` | `0x189D` | `bat_mgmt` | degC | `special:coolant_temps` | UNKNOWN | unknown | json | — | yes |
| `dc_dc_converter_coolant_temperature_calculated` | `0x43B8` | `veh_info` | degC | `s16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `dc_dc_converter_reserved_current` | `0x2A53` | `energy` | — | `—` | UNKNOWN | invalid | numeric | — | yes |
| `dc_link_current` | `0x3E98` | `eldrive` | A | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dc_link_voltage` | `0x3E93` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `dc_link_voltage_reserve` | `0x43B9` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dcdc_current` | `0x465B` | `dcdc` | A | `u16be x 0.0625/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `dcdc_gw_current` | `0x2A53` | `energy` | A | `u16be x 1/1 + -511` | UNKNOWN | unknown | numeric | — | yes |
| `dcdc_gw_voltage` | `0x2A53` | `energy` | V | `u8be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `dcdc_voltage` | `0x465D` | `dcdc` | V | `u16be x 0.00195312/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `desired_link_voltage` | `0x3EED` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dmcm_stator_voltage_d_direction_actual_value` | `0x1007` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dmcm_stator_voltage_d_direction_commanded_value` | `0x1009` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dmcm_stator_voltage_q_direction_actual_value` | `0x1008` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `dmcm_stator_voltage_q_direction_commanded_value` | `0x100A` | `eldrive` | V | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `driver_requested_torque` | `0x29D4` | `veh_info` | Nm | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `dyn_charge_limit` | `0x1E1B` | `bat_mgmt` | A | `u16be x 0.2/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `dyn_discharge_limit` | `0x1E1C` | `bat_mgmt` | A | `u16be x 0.2/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `ecu_pcb_temperature_1` | `0x0805` | `eldrive` | degC | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `ecu_pcb_temperature_2` | `0x0806` | `eldrive` | degC | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `eldrive_odometer` | `0x086D` | `eldrive` | km | `u32be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `electric_drive_inflow_coolant_temperature` | `0x4FAE` | `eldrive` | degC | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `electric_drive_motor_speed_rad_sec` | `0x40C8` | `eldrive` | — | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `energy_counters` | `0x1E32` | `bat_mgmt` | kWh | `special:energy_counters` | PROVISIONAL | provisional | json | — | yes |
| `fresh_air_recirculation_flap_actual_value` | `0x261E` | `climate` | % | `u8be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `fresh_air_recirculation_flap_actual_value_steps` | `0x261E` | `climate` | — | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `fresh_air_recirculation_flap_specified_value_steps` | `0x261F` | `climate` | — | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `fresh_air_recirculation_flap_stop_for_fresh_air` | `0x2620` | `climate` | — | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `fresh_air_recirculation_flap_stop_for_recirculated_air` | `0x2621` | `climate` | — | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_drive_actual_torque` | `0x0335` | `veh_info` | Nm | `s16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_left_deflector_temperature` | `0x260C` | `climate` | degC | `u8be x 0.333/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_left_temperature_flap_actual_value` | `0x2622` | `climate` | % | `u8be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_rear_torque_difference` | `0x0343` | `veh_info` | Nm | `s16be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_right_deflector_temperature` | `0x260D` | `climate` | degC | `u8be x 0.333/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `front_right_temperature_flap_actual_value` | `0x2626` | `climate` | % | `u8be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `gear` | `0x210E` | `veh_info` | — | `special:gear` | UNKNOWN | unknown | text | — | yes |
| `hv_aux_power` | `0x0364` | `veh_info` | kW | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `hv_battery_current` | `0x035E` | `veh_info` | A | `s16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `hv_em_hv_battery_energy_information` | `0x15D1` | `energy` | Wh | `u16be x 50/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `hv_em_nv_power_limitation` | `0x15D1` | `energy` | W | `u8be x 50/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `hv_em_power_limit` | `0x15D1` | `energy` | W | `u16be x 50/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `hv_energy_content` | `0x2AB8` | `energy` | Wh | `u16be x 25/1 + 0` | VERIFIED | verified | numeric | yes | yes |
| `hv_energy_max` | `0x2AB2` | `energy` | Wh | `u16be x 50/1 + 0` | VERIFIED | verified | numeric | yes | yes |
| `hv_output_voltage` | `0x1DA7` | `chg` | V | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `inside_temp` | `0x2613` | `climate` | degC | `u16be x 0.2/1 + -40` | UNKNOWN | unknown | numeric | — | yes |
| `inverter_power_loss_phase_u` | `0x1040` | `eldrive` | W | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `inverter_power_loss_phase_v` | `0x1041` | `eldrive` | W | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `inverter_power_loss_phase_w` | `0x1042` | `eldrive` | W | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `max_charge_voltage` | `0x5171` | `bat_mgmt` | V | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `min_discharge_voltage` | `0x5170` | `bat_mgmt` | V | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `motor_maximum_current` | `0x3EE7` | `eldrive` | A | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `motor_minimum_current` | `0x3EE8` | `eldrive` | A | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `motor_rpm` | `0xF40C` | `bat_mgmt` | rpm | `u16be x 1/4 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `motor_rpm_2` | `0x3DF8` | `veh_info` | rpm | `s16be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `number_of_tracked_satellites` | `0x2431` | `gps` | — | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `number_of_visible_satellites` | `0x2431` | `gps` | — | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `nv_energy_requirement` | `0x15D1` | `energy` | Wh | `u8be x 50/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `obc_cycle_energy` | `0x103E` | `chg` | kWh | `u16be x 0.01/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `obc_dc_current` | `0x103D` | `chg` | A | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `odometer_2` | `0x5867` | `bat_mgmt` | km | `u16be x 10/1 + 0` | PROVISIONAL | invalid | numeric | yes | yes |
| `odometer_bms` | `0x02BD` | `bat_mgmt` | km | `u24be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `odometer_km` | `0x295A` | `veh_info` | km | `u24be x 1/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `op_mode` | `0x7448` | `bat_mgmt` | code | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `outside_temp` | `0x2609` | `climate` | degC | `u8be x 0.5/1 + -50` | UNKNOWN | unknown | numeric | — | yes |
| `outside_temperature_calculated_from_cabin` | `0x260A` | `climate` | degC | `u8be x 0.5/1 + -50` | UNKNOWN | unknown | numeric | — | — |
| `pack_current` | `0x1E3D` | `bat_mgmt` | A | `u32be x 0.01/1 + -1500` | PROVISIONAL | provisional | numeric | yes | yes |
| `pack_current_2` | `0x1E3E` | `bat_mgmt` | A | `u32be x 0.01/1 + -1500` | UNKNOWN | unknown | numeric | — | yes |
| `pack_voltage` | `0x1E3B` | `bat_mgmt` | V | `u16be x 0.25/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `power_electronics_voltage_in_traction_mode` | `0x4675` | `veh_info` | V | `s16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `ptc_current` | `0x1620` | `bat_mgmt` | A | `u8be x 0.25/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `ptc_medium_temp_1` | `0x1623` | `bat_mgmt` | degC | `u8be x 1/1 + -50` | UNKNOWN | unknown | numeric | — | — |
| `ptc_medium_temp_2` | `0x1624` | `bat_mgmt` | degC | `u8be x 1/1 + -50` | UNKNOWN | unknown | numeric | — | — |
| `ptc_voltage` | `0x1621` | `bat_mgmt` | V | `u8be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `radiator_fan_commanded_duty` | `0x2607` | `climate` | % | `u8be x 0.4/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `radiator_fan_duty` | `0x2055` | `veh_info` | % | `u16be x 0.01/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `range_can` | `0x2AB5` | `energy` | km | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `range_displayed` | `0x2AB6` | `energy` | km | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `range_internal` | `0x2AB4` | `energy` | km | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `rated_torque_on_the_front_axle` | `0x0344` | `veh_info` | Nm | `s16be x 0.5/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `rear_deflector_temperature` | `0x263D` | `climate` | degC | `u8be x 0.333/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `rear_drive_actual_torque` | `0x033B` | `veh_info` | Nm | `s16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `refrigerant_pressure` | `0x2605` | `climate` | bar | `u8be x 0.2/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `res_pid_0149` | `0xF449` | `climate` | % | `u8be x 1/2.55 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `road_avg_consumption_0` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_1` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_2` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_3` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_4` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_5` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `road_avg_consumption_6` | `0x2AB7` | `energy` | kWh/100km | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `rotor_temperature_model` | `0x2CCC` | `eldrive` | degC | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `soc_abs` | `0x028C` | `bat_mgmt` | % | `u8be x 0.4/1 + 0` | PROVISIONAL | provisional | numeric | yes | yes |
| `soc_normal` | `0x028C` | `bat_mgmt` | % | `u8be x 0.4425/1 + -6.1947` | UNKNOWN | unknown | numeric | — | yes |
| `socket_a_temp` | `0x1DDA` | `chg` | degC | `u16be x 0.1/1 + -55` | UNKNOWN | unknown | numeric | — | yes |
| `socket_a_voltage` | `0x1525` | `chg` | V | `u16be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `speed_kmh` | `0xF40D` | `bat_mgmt` | km/h | `u8be x 1/1 + 0` | UNKNOWN | unknown | numeric | — | yes |
| `stator_electrical_frequency_rad_sec` | `0x4657` | `eldrive` | — | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `stator_temperature` | `0x4672` | `eldrive` | degC | `special:float32` | PROVISIONAL | provisional | numeric | yes | yes |
| `steering_wheel_angle` | `0x0346` | `veh_info` | deg | `u16be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `temperature_after_evaporator` | `0x2612` | `climate` | degC | `s8be x 0.333/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `torque_calculated_from_measured_currents` | `0x0308` | `eldrive` | Nm | `special:float32` | PROVISIONAL | provisional | numeric | yes | — |
| `total_distance_travelled_lifetime` | `0xF817` | `veh_info` | km | `s32be x 0.1/1 + 0` | UNKNOWN | unknown | numeric | — | — |
| `vin` | `0xF802` | `veh_info` | — | `special:vin` | UNKNOWN | unknown | text | — | yes |
| `windshield_temperature` | `0x27C4` | `climate` | degC | `—` | UNKNOWN | invalid | numeric | yes | yes |

## Keys not named in any test

- `a_c_compressor_load`
- `a_c_compressor_power_consumption_actual_value`
- `a_c_compressor_speed`
- `a_c_refrigerant_pressure_and_temperature_sensor_1_interior_temperature`
- `a_c_refrigerant_pressure_and_temperature_sensor_1_pressure`
- `a_c_refrigerant_pressure_and_temperature_sensor_1_temperature`
- `acceleration`
- `actual_mechanical_torque_calculated`
- `actual_mechanical_torque_commanded_value`
- `actual_possible_maximum_torque`
- `actual_possible_minimum_torque`
- `additional_high_voltage_consumers_total_current`
- `air_polution_level_0_clean_air`
- `aux_12v_adapted_capacity`
- `aux_12v_aging_capacity`
- `aux_12v_aging_power`
- `aux_12v_sensor_temp`
- `aux_12v_soc`
- `aux_12v_temp`
- `aux_12v_total_charge`
- `aux_12v_total_discharge`
- `average_battery_consumption_internal_value`
- `bat_v_after_contacts_2`
- `battery_soc_normalized`
- `brake_booster_pressure`
- `cabin_humidity`
- `cell_t_01`
- `cell_t_02`
- `cell_t_03`
- `cell_t_04`
- `cell_t_05`
- `cell_t_06`
- `cell_t_07`
- `cell_t_08`
- `cell_t_09`
- `cell_t_10`
- `cell_t_11`
- `cell_t_12`
- `cell_t_13`
- `cell_t_14`
- `cell_t_15`
- `cell_t_16`
- `cell_t_17`
- `cell_v_004`
- `cell_v_005`
- `cell_v_006`
- `cell_v_007`
- `cell_v_008`
- `cell_v_009`
- `cell_v_010`
- `cell_v_011`
- `cell_v_012`
- `cell_v_013`
- `cell_v_014`
- `cell_v_015`
- `cell_v_016`
- `cell_v_017`
- `cell_v_018`
- `cell_v_019`
- `cell_v_020`
- `cell_v_021`
- `cell_v_022`
- `cell_v_023`
- `cell_v_024`
- `cell_v_025`
- `cell_v_026`
- `cell_v_027`
- `cell_v_028`
- `cell_v_029`
- `cell_v_030`
- `cell_v_031`
- `cell_v_032`
- `cell_v_033`
- `cell_v_034`
- `cell_v_035`
- `cell_v_036`
- `cell_v_037`
- `cell_v_038`
- `cell_v_039`
- `cell_v_040`
- `cell_v_041`
- `cell_v_042`
- `cell_v_043`
- `cell_v_044`
- `cell_v_045`
- `cell_v_046`
- `cell_v_047`
- `cell_v_048`
- `cell_v_049`
- `cell_v_050`
- `cell_v_051`
- `cell_v_052`
- `cell_v_053`
- `cell_v_054`
- `cell_v_055`
- `cell_v_056`
- `cell_v_057`
- `cell_v_058`
- `cell_v_059`
- `cell_v_060`
- `cell_v_061`
- `cell_v_062`
- `cell_v_063`
- `cell_v_064`
- `cell_v_065`
- `cell_v_066`
- `cell_v_067`
- `cell_v_068`
- `cell_v_069`
- `cell_v_070`
- `cell_v_071`
- `cell_v_072`
- `cell_v_073`
- `cell_v_074`
- `cell_v_075`
- `cell_v_076`
- `cell_v_077`
- `cell_v_078`
- `cell_v_079`
- `cell_v_080`
- `cell_v_081`
- `cell_v_082`
- `cell_v_083`
- `cell_v_084`
- `cell_v_085`
- `cell_v_086`
- `cell_v_087`
- `cell_v_088`
- `cell_v_089`
- `cell_v_090`
- `cell_v_091`
- `cell_v_092`
- `cell_v_093`
- `cell_v_094`
- `cell_v_095`
- `cell_v_096`
- `cell_v_097`
- `cell_v_098`
- `cell_v_099`
- `cell_v_100`
- `cell_v_101`
- `cell_v_102`
- `cell_v_103`
- `cell_v_104`
- `charge_port_normal`
- `charge_port_rapid`
- `coolant_circulation_pump_duty`
- `coolant_pump`
- `dc_dc_converter_coolant_temperature_calculated`
- `dc_link_current`
- `dc_link_voltage_reserve`
- `desired_link_voltage`
- `dmcm_stator_voltage_d_direction_actual_value`
- `dmcm_stator_voltage_d_direction_commanded_value`
- `dmcm_stator_voltage_q_direction_actual_value`
- `dmcm_stator_voltage_q_direction_commanded_value`
- `driver_requested_torque`
- `ecu_pcb_temperature_2`
- `electric_drive_motor_speed_rad_sec`
- `fresh_air_recirculation_flap_actual_value`
- `fresh_air_recirculation_flap_actual_value_steps`
- `fresh_air_recirculation_flap_specified_value_steps`
- `fresh_air_recirculation_flap_stop_for_fresh_air`
- `fresh_air_recirculation_flap_stop_for_recirculated_air`
- `front_drive_actual_torque`
- `front_left_deflector_temperature`
- `front_left_temperature_flap_actual_value`
- `front_rear_torque_difference`
- `front_right_deflector_temperature`
- `front_right_temperature_flap_actual_value`
- `hv_battery_current`
- `hv_em_hv_battery_energy_information`
- `hv_em_nv_power_limitation`
- `hv_em_power_limit`
- `hv_output_voltage`
- `inverter_power_loss_phase_v`
- `inverter_power_loss_phase_w`
- `max_charge_voltage`
- `min_discharge_voltage`
- `motor_rpm_2`
- `number_of_tracked_satellites`
- `number_of_visible_satellites`
- `nv_energy_requirement`
- `outside_temperature_calculated_from_cabin`
- `power_electronics_voltage_in_traction_mode`
- `ptc_medium_temp_1`
- `ptc_medium_temp_2`
- `radiator_fan_commanded_duty`
- `radiator_fan_duty`
- `range_can`
- `range_displayed`
- `range_internal`
- `rated_torque_on_the_front_axle`
- `rear_deflector_temperature`
- `rear_drive_actual_torque`
- `refrigerant_pressure`
- `road_avg_consumption_0`
- `road_avg_consumption_1`
- `road_avg_consumption_2`
- `road_avg_consumption_3`
- `road_avg_consumption_4`
- `road_avg_consumption_5`
- `road_avg_consumption_6`
- `rotor_temperature_model`
- `stator_electrical_frequency_rad_sec`
- `steering_wheel_angle`
- `temperature_after_evaporator`
- `torque_calculated_from_measured_currents`
- `total_distance_travelled_lifetime`
