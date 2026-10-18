# Collected data - Volkswagen ID.3 58 kWh (Pro) 2021
VIN WVWZZZE1ZMP087053 | profile=meb | first seen 2026-10-05T18:03:02+00:00

Measurements 2026-10-05T18:03:02+00:00 -> 2026-10-18T18:26:30+00:00 (UTC)
16461 measurement rows across 318 distinct keys, by source:
  - reported: 16113
  - imported: Car Scanner CSV: 266
  - calculated: 55
  - estimated: 27

## Measurements by module

### bat_mgmt - HV battery management (BMS) (39 of 39 read)
- `soc_abs` - State of charge (BMS) (%) - 139 rows
- `soc_normal` - State of charge (display) (%) - 139 rows
- `pack_voltage` - HV pack voltage (V) - 146 rows
- `pack_current` - HV pack current (A) - 143 rows
- `cell_voltage_max` - Cell voltage max (V) - 142 rows
- `cell_voltage_min` - Cell voltage min (V) - 140 rows
- `battery_temp` - HV battery temperature (degC) - 139 rows
- `bat_temp_max` - HV battery temperature max (degC) - 139 rows
- `bat_temp_min` - HV battery temperature min (degC) - 139 rows
- `energy_counters` - Energy counters (kWh) - 87 rows
- `dyn_charge_limit` - Dynamic charge current limit (A) - 139 rows
- `dyn_discharge_limit` - Dynamic discharge current limit (A) - 87 rows
- `charge_mode` - HV charge mode code (code) - 136 rows
- `op_mode` - BMS operation mode (code) - 136 rows
- `coolant_pump` - Battery coolant pump (%) - 87 rows
- `coolant_temps` - Battery cooling liquid (degC) - 136 rows
- `ptc_current` - PTC heater battery current (A) - 144 rows
- `speed_kmh` - Vehicle speed (BMS) (km/h) - 136 rows
- `battery_serial` - HV battery serial - 72 rows
- `hv_serial_vin` - BMS VIN (part) - 63 rows
- `cell_voltages` - Cell voltages (all cells) (V) - 5921 rows across 108 slots
- `cell_temperatures` - Cell temperatures (all sensors) (degC) - 1878 rows across 24 slots
- `odometer_bms` - Odometer (BMS copy) (km) - 34 rows
- `ptc_voltage` - PTC heater voltage (V) - 34 rows
- `bms_pcb_temp` - BMS PCB temperature (degC) - 34 rows
- `ptc_medium_temp_1` - PTC medium temperature 1 (degC) - 34 rows
- `ptc_medium_temp_2` - PTC medium temperature 2 (degC) - 34 rows
- `bat_v_after_contacts` - Battery voltage after contacts (V) - 34 rows
- `bat_v_before_contacts` - Battery voltage before contacts (V) - 34 rows
- `bat_v_after_contacts_2` - Battery voltage (2nd, after contacts) (V) - 34 rows
- `bat_charging_voltage` - Battery charging voltage (V) - 34 rows
- `cell_voltage_sum` - Sum of all cell voltages (V) - 34 rows
- `pack_current_2` - HV pack current (2nd) (A) - 34 rows
- `min_discharge_voltage` - Minimum discharge voltage (V) - 34 rows
- `max_charge_voltage` - Maximum charge voltage (V) - 34 rows
- `odometer_2` - Odometer (BMS copy 2) (km) - 34 rows
- `motor_rpm` - Motor speed (rpm) - 34 rows
- `cell_soc_max` - Cell with the highest SoC (%) - 34 rows
- `cell_soc_min` - Cell with the lowest SoC (%) - 34 rows

### energy - Gateway energy information (30 of 30 read)
- `hv_energy_max` - Max HV energy content (Wh) - 92 rows
- `hv_energy_content` - HV energy content (Wh) - 91 rows
- `aux_12v_voltage` - 12V battery voltage (BMS) (V) - 94 rows
- `aux_12v_current` - 12V battery current (A) - 46 rows
- `aux_12v_soc` - 12V battery state of charge (%) - 46 rows
- `aux_12v_temp` - 12V battery temperature (degC) - 46 rows
- `aux_12v_sensor_temp` - 12V battery sensor temperature (degC) - 46 rows
- `aux_12v_total_charge` - 12V battery total charge (Ah) - 46 rows
- `aux_12v_total_discharge` - 12V battery total discharge (Ah) - 46 rows
- `aux_12v_aging_capacity` - 12V battery aging (capacity) (%) - 46 rows
- `aux_12v_aging_power` - 12V battery aging (power) (%) - 46 rows
- `aux_12v_adapted_capacity` - 12V battery adapted capacity (Ah) - 46 rows
- `range_displayed` - Estimated range (displayed) (km) - 51 rows
- `range_can` - Estimated range (CAN bus) (km) - 51 rows
- `range_internal` - Estimated range (internal) (km) - 51 rows
- `dcdc_gw_voltage` - DC-DC converter low voltage (V) - 51 rows
- `dcdc_gw_current` - DC-DC converter current (A) - 51 rows
- `hv_em_power_limit` - HV-EM power limitation (W) - 51 rows
- `road_avg_consumption_0` - Avg consumption (roads cat 0) (kWh/100km) - 44 rows
- `road_avg_consumption_1` - Avg consumption (roads cat 1) (kWh/100km) - 43 rows
- `road_avg_consumption_2` - Avg consumption (roads cat 2) (kWh/100km) - 43 rows
- `road_avg_consumption_3` - Avg consumption (roads cat 3) (kWh/100km) - 43 rows
- `road_avg_consumption_4` - Avg consumption (roads cat 4) (kWh/100km) - 43 rows
- `road_avg_consumption_5` - Avg consumption (roads cat 5) (kWh/100km) - 43 rows
- `road_avg_consumption_6` - Avg consumption (roads cat 6) (kWh/100km) - 43 rows
- `hv_em_nv_power_limitation` - HV-EM NV Power limitation (W) - 49 rows
- `nv_energy_requirement` - NV Energy requirement (Wh) - 49 rows
- `hv_em_hv_battery_energy_information` - HV-EM HV Battery energy information (Wh) - 49 rows
- `dc_dc_converter_reserved_current` - DC-DC converter reserved current (A) - 49 rows
- `average_battery_consumption_internal_value` - Average battery consumption (internal value) (A) - 49 rows

### dcdc - DC/DC converter (HV->12V) (2 of 2 read)
- `dcdc_current` - DC/DC charging current (A) - 74 rows
- `dcdc_voltage` - DC/DC output voltage (V) - 74 rows

### veh_info - Vehicle info (odometer/gear/VIN) (21 of 21 read)
- `odometer_km` - Odometer (km) - 72 rows
- `gear` - Gear position - 72 rows
- `vin` - VIN (gateway) - 72 rows
- `hv_aux_power` - HV auxiliary consumer power (kW) - 134 rows
- `front_drive_actual_torque` - Front drive actual torque (Nm) - 33 rows
- `rear_drive_actual_torque` - Rear drive actual torque (Nm) - 33 rows
- `front_rear_torque_difference` - Front:Rear torque difference (Nm) - 33 rows
- `rated_torque_on_the_front_axle` - Rated torque on the front axle (Nm) - 33 rows
- `steering_wheel_angle` - Steering wheel angle (deg) - 33 rows
- `hv_battery_current` - HV Battery current (A) - 28 rows
- `additional_high_voltage_consumers_total_current` - Additional high-voltage consumers, total current (A) - 33 rows
- `brake_booster_pressure` - Brake booster pressure (bar) - 33 rows
- `radiator_fan_duty` - Radiator fan duty (%) - 33 rows
- `coolant_circulation_pump_duty` - Coolant circulation pump duty (%) - 33 rows
- `driver_requested_torque` - Driver requested torque (Nm) - 33 rows
- `battery_soc_normalized` - Battery SoC (normalized) (%) - 28 rows
- `motor_rpm_2` - Motor RPM (rpm) - 33 rows
- `dc_dc_converter_coolant_temperature_calculated` - DC-DC converter coolant temperature Calculated (degC) - 33 rows
- `acceleration` - Acceleration (m/s2) - 33 rows
- `power_electronics_voltage_in_traction_mode` - Power electronics: voltage in traction mode (V) - 33 rows
- `total_distance_travelled_lifetime` - Total distance travelled (Lifetime) (km) - 33 rows

### climate - Climate control (27 of 27 read)
- `outside_temp` - Outside temperature (degC) - 87 rows
- `inside_temp` - Inside temperature (degC) - 87 rows
- `a_c_compressor_power_consumption_actual_value` - A/C compressor power consumption: actual value (W) - 51 rows
- `a_c_compressor_speed` - A/C compressor speed (rpm) - 51 rows
- `a_c_compressor_load` - A/C compressor load (Nm) - 51 rows
- `refrigerant_pressure` - Refrigerant pressure (bar) - 51 rows
- `radiator_fan_commanded_duty` - Radiator fan commanded duty (%) - 51 rows
- `outside_temperature_calculated_from_cabin` - Outside temperature (calculated from cabin) (degC) - 43 rows
- `front_left_deflector_temperature` - Front left deflector temperature (degC) - 51 rows
- `front_right_deflector_temperature` - Front right deflector temperature (degC) - 51 rows
- `temperature_after_evaporator` - Temperature after evaporator (degC) - 51 rows
- `fresh_air_recirculation_flap_actual_value_steps` - Fresh air/Recirculation flap actual value steps - 51 rows
- `fresh_air_recirculation_flap_actual_value` - Fresh air/Recirculation flap actual value % (%) - 51 rows
- `fresh_air_recirculation_flap_specified_value_steps` - Fresh air/Recirculation flap specified value steps - 51 rows
- `fresh_air_recirculation_flap_stop_for_fresh_air` - Fresh air/Recirculation flap stop for fresh air - 51 rows
- `fresh_air_recirculation_flap_stop_for_recirculated_air` - Fresh air/Recirculation flap stop for recirculated air - 51 rows
- `front_left_temperature_flap_actual_value` - Front left temperature flap actual value (%) - 51 rows
- `front_right_temperature_flap_actual_value` - Front right temperature flap actual value (%) - 51 rows
- `rear_deflector_temperature` - Rear deflector temperature (degC) - 50 rows
- `air_polution_level_0_clean_air` - Air polution level (0 = clean air) - 50 rows
- `windshield_temperature` - Windshield temperature - 50 rows
- `cabin_humidity` - Cabin humidity (%) - 49 rows
- `co2_content_interior` - CO2 content interior (ppm) - 49 rows
- `a_c_refrigerant_pressure_and_temperature_sensor_1_pressure` - A/C refrigerant pressure and temperature sensor 1: Pressure (bar) - 49 rows
- `a_c_refrigerant_pressure_and_temperature_sensor_1_temperature` - A/C refrigerant pressure and temperature sensor 1: Temperature (degC) - 49 rows
- `a_c_refrigerant_pressure_and_temperature_sensor_1_interior_temperature` - A/C refrigerant pressure and temperature sensor 1: Interior Temperature (degC) - 49 rows
- `res_pid_0149` - RES(PID_0149) (%) - 49 rows

### chg - HV charger (OBC) (5 of 5 read)
- `obc_dc_current` - HV DC current (OBC) (A) - 34 rows
- `obc_cycle_energy` - Cycle energy transformed (kWh) - 34 rows
- `socket_a_voltage` - Charge socket A voltage (V) - 34 rows
- `hv_output_voltage` - Charger HV output voltage (V) - 34 rows
- `socket_a_temp` - Charge socket A temperature (degC) - 34 rows

### eldrive - Electric drive (motor/inverter) (26 of 26 read)
- `torque_calculated_from_measured_currents` - Torque, calculated from measured currents (Nm) - 33 rows
- `ecu_pcb_temperature_1` - ECU PCB Temperature #1 (degC) - 33 rows
- `ecu_pcb_temperature_2` - ECU PCB Temperature #2 (degC) - 33 rows
- `eldrive_odometer` - Odometer (km) - 28 rows
- `dmcm_stator_voltage_d_direction_actual_value` - DMCM stator voltage D direction actual value (V) - 33 rows
- `dmcm_stator_voltage_q_direction_actual_value` - DMCM stator voltage Q direction actual value (V) - 33 rows
- `dmcm_stator_voltage_d_direction_commanded_value` - DMCM stator voltage D direction commanded value (V) - 33 rows
- `dmcm_stator_voltage_q_direction_commanded_value` - DMCM stator voltage Q direction commanded value (V) - 33 rows
- `inverter_power_loss_phase_u` - Inverter power loss phase U (W) - 33 rows
- `inverter_power_loss_phase_v` - Inverter power loss phase V (W) - 33 rows
- `inverter_power_loss_phase_w` - Inverter power loss phase W (W) - 33 rows
- `rotor_temperature_model` - Rotor temperature model (degC) - 33 rows
- `dc_link_voltage` - DC Link voltage (V) - 33 rows
- `dc_link_current` - DC Link current (A) - 33 rows
- `motor_maximum_current` - Motor maximum current (A) - 33 rows
- `motor_minimum_current` - Motor minimum current (A) - 33 rows
- `actual_mechanical_torque_calculated` - Actual mechanical torque calculated (Nm) - 33 rows
- `actual_possible_maximum_torque` - Actual possible maximum torque (Nm) - 33 rows
- `actual_possible_minimum_torque` - Actual possible minimum torque (Nm) - 33 rows
- `desired_link_voltage` - Desired link voltage (V) - 33 rows
- `electric_drive_motor_speed_rad_sec` - Electric drive motor speed (rad/sec) - 33 rows
- `dc_link_voltage_reserve` - DC Link voltage reserve (V) - 33 rows
- `stator_electrical_frequency_rad_sec` - Stator electrical frequency (rad/sec) - 33 rows
- `stator_temperature` - Stator temperature (degC) - 33 rows
- `electric_drive_inflow_coolant_temperature` - Electric drive inflow coolant temperature (degC) - 33 rows
- `actual_mechanical_torque_commanded_value` - Actual mechanical torque commanded value (Nm) - 33 rows

### gps - GPS / telematics (2 of 2 read)
- `number_of_visible_satellites` - Number of visible satellites - 49 rows
- `number_of_tracked_satellites` - Number of tracked satellites - 49 rows

## Also collected, not in the current registry (earlier profile, derived, or imported)
- `accum_charge_kwh` - 3 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `accum_discharge_kwh` - 3 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `bat_inlet_temp` - 2 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `bat_outlet_temp` - 2 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `cell_delta_mv` - 33 rows, provenance=calculated, ecu=bat_mgmt
- `cells_voltage_sum` - 1 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `dcdc8105_current` - 1 rows, provenance=imported: Car Scanner CSV, ecu=8105.DC/DC Converter
- `dcdc8105_voltage` - 1 rows, provenance=imported: Car Scanner CSV, ecu=8105.DC/DC Converter
- `gate_avg_battery_consumption` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat0` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat1` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat2` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat3` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat4` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat5` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_avg_cons_cat6` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_dcdc_reserved_current` - 2 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_aging_capacity` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_aging_power` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_capacity_ah` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_current` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_sensor_temp` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_soc` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_lv_temp` - 7 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_nv_energy_req` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_nv_power_limit` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_power_limit` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_range_can` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_range_displayed` - 3 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `gate_range_internal` - 4 rows, provenance=imported: Car Scanner CSV, ecu=19.Gate
- `lv_voltage_obd` - 137 rows, provenance=reported, ecu=-
- `min_discharge_v` - 1 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `pack_power` - 28 rows, provenance=imported: Car Scanner CSV, ecu=8C.BMS
- `pack_power_kw` - 22 rows, provenance=calculated, ecu=bat_mgmt
- `soh_pct` - 27 rows, provenance=estimated, ecu=bat_mgmt
- `vehicle_speed` - 95 rows, provenance=reported, ecu=-

## Other tables
- `cell_voltages` - 2579 rows. per-cell voltages, one row per cell per sweep
- `battery_measurements` - 125 rows. aggregated battery snapshots (SoC, SoH, CAC, energy counters)
- `charging_sessions` - 0 rows. charging sessions with energy/power/duration
- `charging_samples` - 0 rows. samples taken inside a charging session
- `dtcs` - 0 rows. diagnostic trouble codes seen on the car
- `dtc_occurrences` - 0 rows. each time a DTC was seen, with any freeze frame
- `diagnostic_events` - 104 rows. the collector's own log: connections, wakes, charge boundaries, notes
- `tx_log` - 37730 rows. every request ever sent to the car (the safety manifest)
- `anomalies` - 0 rows. statistically flagged outliers, with their baseline
- `analysis_results` - 0 rows. stored output of the analysis engine
- `ecus` - 14 rows. modules discovered on the car, with their CAN addressing
  - cell sweeps: 24, up to 108 cells per sweep

## Caveats (important for analysis)
- Every decoder is `doc_status = experimentally determined` unless a row below says
  otherwise: the layout was reverse-engineered by observing the car, NOT taken from a
  VW document. Treat every scale factor as unproven until a second source confirms it.
- Known BAD scalings - do not trust these values: eldrive `ecu_pcb_temperature_1`,
  `ecu_pcb_temperature_2`, `stator_temperature`, `rotor_temperature_model`,
  `electric_drive_inflow_coolant_temperature` (all read ~1680 degC instead of ~15-40),
  `dc_link_voltage` / `dc_link_voltage_reserve` (~1738 V when the pack is ~435 V),
  `inverter_power_loss_phase_u/v/w` (~1e8 W), and climate `windshield_temperature`
  (~483476). Several other keys look implausible for the same reason.
- The adapter intermittently fails to reach the 29-bit modules (bat_mgmt, eldrive,
  veh_info, dcdc) for a whole rotation. During such spells values are carried forward
  for up to 900 s, so a present value is not necessarily a fresh reading. Prefer the
  `ts` of each row over assuming current.
- `gear` and `vin` are TEXT, not numbers, despite sharing the value column.
- `cell_soc_max`, `cell_soc_min`, `energy_counters`, `coolant_temps`, `battery_serial`
  and similar return structured data: the numeric column is NULL and the JSON is in
  `text_value`.
- `cell_delta_mv`, `pack_power_kw` and `soh_pct` are DERIVED (calculated/estimated),
  not read from the car.
- Where a key appears in several modules (odometer, motor rpm, HV current) the copies
  are independent readings of the same quantity and may disagree slightly.
- Rows are keyed by VIN, and the collector also writes `provenance`, which is the
  field to filter on: `reported` is a vehicle reading, `imported: ...` is evidence
  from another tool, `calculated`/`estimated` are derived here, and `simulated` means
  a simulator produced it and it describes no car.
