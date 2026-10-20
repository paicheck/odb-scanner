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
    "cell_soc_spread_pct": ("Cell SoC spread (max - min, calculated)", "%"),
    "battery_temp_spread_c": (
        "Battery temperature spread (max - min, calculated)", "degC"),
    "soh_pct": ("State of health (estimated)", "%"),
    # Low-voltage investigation aids: no DID reports these directly.
    "aux_12v_power_w": ("12 V battery power (calculated U x I)", "W"),
    "dcdc_power_kw": ("DC/DC output power (calculated U x I)", "kW"),
    "dcdc_gw_power_kw": ("Gateway DC-DC power (calculated U x I)", "kW"),
}

# Nominal usable capacity of the 58 kWh pack (Ah), used ONLY for the
# estimated SOH percentage. This is a published vehicle specification,
# not a measurement.
NOMINAL_CAC_AH_58KWH = 164.0  # approx. gross 58 kWh / ~355 V nominal

# Marketable HV energy of the ID.3 58 kWh pack (Wh). Used only for the
# estimated SOH on cars whose BMS does not expose a capacity (CAC) DID --
# the MEB BMS reports the pack's rated max energy content instead, and
# comparing that against the marketed figure gives a rough SoH percentage.
NOMINAL_ENERGY_WH_58KWH = 58000.0


def soh_pct_from_cac(cac_ah: float | None,
                     nominal_ah: float = NOMINAL_CAC_AH_58KWH) -> float | None:
    """Estimated state of health as a percentage of nominal capacity.

    Lives here, next to NOMINAL_CAC_AH_58KWH, so the constant and the formula
    that consumes it cannot drift apart. Returns None when there is nothing to
    estimate from, so callers can distinguish "unknown" from "0 %".
    """
    if not cac_ah or not nominal_ah:
        return None
    return round(100.0 * cac_ah / nominal_ah, 1)


def soh_pct_from_energy(max_energy_wh: float | None,
                        nominal_wh: float = NOMINAL_ENERGY_WH_58KWH
                        ) -> float | None:
    """Estimated SOH from the BMS-reported max energy content.

    Used when the BMS exposes no capacity (CAC) DID -- MEB packs report a
    rated max energy content instead. It is a rated-vs-marketed comparison,
    not a measured capacity, so it is labelled ESTIMATED everywhere and the
    CAC-based figure takes precedence when both are available.
    """
    if not max_energy_wh or not nominal_wh:
        return None
    return round(100.0 * max_energy_wh / nominal_wh, 1)


DRIVE_SYSTEM_STATUS = (
    "Electric drive / inverter live data (motor torque, motor speed, inverter "
    "losses, rotor/stator/PCB temperatures, DC-link voltage/current) is now "
    "registered from the Car Scanner MEB profile (decoders/meb.py, 'eldrive' "
    "ECU, module 0x7C). They are EXPERIMENTAL: the scalings are Car Scanner's "
    "own profile fields, not yet confirmed by this project's capture."
)
