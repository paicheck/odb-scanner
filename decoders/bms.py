"""BMS management register: computed battery metrics that are NOT vehicle DIDs.

Everything here is CALCULATED or ESTIMATED software output and is labelled as
such everywhere it is displayed (requirement: never present an inferred value
as an official vehicle measurement).
"""
from __future__ import annotations

# Keys computed by the collector from reported DIDs:
COMPUTED_KEYS = {
    "pack_power_kw": ("DC power (calculated U x I)", "kW"),
    "cell_delta_mv": ("Cell voltage delta (max - min, calculated)", "mV"),
    "soh_pct": ("State of health (CAC / nominal, estimated)", "%"),
}

# Nominal usable capacity of the 58 kWh pack (Ah), used ONLY for the
# estimated SOH percentage. This is a published vehicle specification,
# not a measurement.
NOMINAL_CAC_AH_58KWH = 164.0  # approx. gross 58 kWh / ~355 V nominal

DRIVE_SYSTEM_STATUS = (
    "Electric drive / inverter live data (motor rpm, torque, inverter "
    "temperatures) has NO verified UDS DIDs in public documentation for the "
    "ID.3 at this time. Raw CAN monitoring with a CAN-capable adapter is the "
    "planned future path. No DIDs are registered for the drive system to "
    "avoid inventing support. If a DID is experimentally identified later, "
    "register it in decoders/motor.py with doc_status=EXPERIMENTAL."
)
