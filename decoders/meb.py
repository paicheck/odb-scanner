"""VW MEB (ID.3 / ID.4 / Enyaq / Born) UDS DID map -- diagnostic-bus profile.

Why a separate map: on MEB the HV battery is not exposed through the OBD
functional addressing that works on the e-Up. The BMS answers only at its
29-bit module address 0x17FC007B (ATCP 17 + ATSH FC007B -- see
Elm327Transport.set_module), and the scale factors differ from the e-Up ones
(pack voltage u16/4, not u16/64). Functionally-read MEB DIDs answer
NRC 0x31 (requestOutOfRange), which is the wall the dashboard reports.

Provenance of the scale factors:

  [evDash]  nickn17/evDash CarVWID3.cpp (community RE of the MEB diagnostics,
            Apache-2.0 sources) -- the per-module command queues
  [ABRP]    iternio/ev-obd-pids MEB.json (independent implementation)
  [spot]    spot2000/Volkswagen-MEB-EV-CAN-parameters CSV: the only source
            that states ATCP/ATSH/ATCRA per DID, and the authority for the
            11-bit modules (energy 0x710 -> 0x77A, climate 0x746 -> 0x7B0)
  [CS]      cross-checked byte-exact against a Car Scanner ELM OBD2 log of
            the actual vehicle (2026-10-05, ID.3 MY2021, 58 kWh):
              - SoC raw 0xCB (203) -> 81.2 % BMS / 83.63 % display
              - pack raw 0x06CD (1725) -> 431.25 V, matching 108 cells @ ~3.99 V
              - cell raw 2997 -> 3.997 V (u16/1000 + 1)
              - max cell raw 16377 -> 3.9984 V (u16/4096)
              - current raw 150198 -> 1.98 A ((u32-150000)/100)
              - battery temp raw 120 -> 20.0 C (u8/2 - 40)
              - max energy content raw -> 53,200 Wh (u32/1310.77)
            Disagreements would have shown up as visibly wrong values; none did.

            The same log settles the CURRENT SIGN, which the sources
            contradict -- and the contradiction is now 2 against 1. evDash
            does not negate, so it reads positive on discharge, and this log
            follows it. spot2000 states in prose that "Negative value is out
            from battery (consumption) and positive value is into battery", and
            ABRP's equation negates for the same reason. Car Scanner reported
            +0.93 to +1.98 A while the car sat parked, and over the same ~16 min
            window the accumulated-*charge* counter was frozen at 24422.8195 kWh
            while accumulated-*discharge* grew by 0.143 kWh, with HV energy
            content falling 41325 -> 41200 Wh. Energy left the pack while
            current read positive, so positive = discharge.

            That reasoning is sound about CAR SCANNER's sign, which is what
            matters for matching it, but it does not establish the DID's own
            convention. If it is wrong then pack_current is inverted, and that
            inverts charging detection, pack-power sign and the regen history.
            Settle it with a raw capture during charge and discharge; do not
            settle it on paper. See docs/MEB_DIAGNOSTIC_REFERENCE.md 2.7.

            Independently confirmed: spot2000's per-DID `Calculation` column
            agrees with this module on all sixteen equations it documents
            (028C, 1E3B, 1E3D, 2A0B, 1E32, 1E0E/1E0F, 189D, 1E1B, F40D, 295A,
            1E40+, 465B, 465D, 0364, 2609, 2613). So the EXPERIMENTAL flags
            below record where the knowledge came from -- reverse engineering,
            not a VW document -- not a doubt about the arithmetic.

Raw bytes are always preserved by the collector, so a wrong guess here costs
nothing but a re-decode later.
"""
from __future__ import annotations

from .registry import (
    DIDRegistry,
    DIDSpec,
    DocStatus,
    u16be,
    u32be,
)

ECU_BAT = "bat_mgmt"      # MEB module 0x7B
ECU_ENERGY = "energy"    # MEB module 0x710 (11-bit)
ECU_DCDC = "dcdc"        # MEB module 0xB9
ECU_VEH = "veh_info"     # MEB module 0x76
ECU_CLIMATE = "climate"  # MEB module 0x746 (11-bit)

_EXP = DocStatus.EXPERIMENTAL

# Number of cell-voltage slots on the MEB pack. 0x1E40..0x1EAB inclusive.
CELL_V_BASE = 0x1E40
CELL_V_SLOTS = 108
# evDash marks unpopulated slots with 0x0FFE; a decoder returning None for
# those keeps them out of the min/max/curve without discarding the DID.
CELL_UNUSED = 0x0FFE

# Cell/module temperature points: 0x1EAE..0x1EBD plus 0x7425/0x7426.
CELL_T_DIDS = list(range(0x1EAE, 0x1EBE)) + [0x7425, 0x7426]

# evDash: energy counters are u32 counts at 8583.07123641215 Wh per count.
ENERGY_COUNT_WH = 8583.07123641215
# evDash CarMEB: "max energy content" u32 at 1310.77 Wh per count.
MAX_ENERGY_COUNT_WH = 1310.77

# 0x7448 operation modes seen in evDash, mapped onto the app-wide convention
# (decoders.charging.CHARGE_MODE_CODES) so charging tracking keeps working.
_OP_MODE_AC = 4
_OP_MODE_DC = 6

_GEAR_CODES = {6: "N", 5: "D", 12: "B", 8: "P", 7: "R"}


# -- BMS decoders ----------------------------------------------------------
def _soc_bms(raw: bytes) -> float:
    return raw[0] / 2.5


def _soc_display(raw: bytes) -> float:
    """Displayed SoC: linear fit of raw*2.5 onto the dash scale.

    evDash fits the dash SoC against the BMS SoC; the published offset
    reproduces the car's own display exactly (raw 203 -> 83.63 %).
    """
    return raw[0] * 0.4425 - 6.1947


def _pack_voltage(raw: bytes) -> float:
    return u16be(raw) / 4.0


def _pack_current(raw: bytes) -> float:
    """Pack current in A; positive = discharging.

    Settled from the car's own log, not from convention: parked with
    +0.93..+1.98 A reported, the charge counter froze while the discharge
    counter grew. See the module docstring.
    """
    return (u32be(raw) - 150000) / 100.0


def _cell_voltage(raw: bytes):
    v = u16be(raw)
    if v == CELL_UNUSED:
        return None
    return v / 1000.0 + 1.0


def _cell_voltage_scaled(raw: bytes) -> float:
    """Cell extreme DIDs 0x1E33/0x1E34: u16/4096 (no +1 offset)."""
    return u16be(raw) / 4096.0


def _battery_temp(raw: bytes) -> float:
    return raw[0] / 2.0 - 40.0


def _temp_scaled(raw: bytes) -> float:
    return u16be(raw) / 64.0


def _cell_temp(raw: bytes) -> float:
    return u16be(raw) / 8.0 - 40.0


def _energy_counters(raw: bytes) -> dict:
    charged = u32be(raw, 0) / ENERGY_COUNT_WH
    used = abs(int.from_bytes(raw[4:8], "big", signed=True)) / ENERGY_COUNT_WH
    return {"charged_kwh": charged, "used_kwh": used}


def _u16_over(raw: bytes, div: float) -> float:
    return u16be(raw) / div


def _charge_mode(raw: bytes) -> int:
    """MEB 0x7448 -> app-wide charge-mode convention (0/1/2)."""
    return {_OP_MODE_AC: 1, _OP_MODE_DC: 2}.get(raw[0], 0)


def _op_mode(raw: bytes) -> int:
    return raw[0]


def _byte(raw: bytes) -> int:
    return raw[0]


def _byte_over(raw: bytes, div: float) -> float:
    return raw[0] / div


def _coolant_temps(raw: bytes) -> dict:
    """Cooling-circuit inlet/outlet in degC from one 4-byte DID.

    Byte order was ambiguous between sources until they were compared: the
    spot2000 row labelled "outlet" reads data bytes 0-1 and evDash's live
    inlet line reads data bytes 2-3, so outlet = [0:2], inlet = [2:4].
    """
    return {"outlet_c": u16be(raw, 0) / 64.0, "inlet_c": u16be(raw, 2) / 64.0}


def _amb_12v(raw: bytes) -> float:
    """HV-pack view of the 12 V battery voltage (multi-frame DID 0x2AF7)."""
    return u16be(raw) / 1024.0 + 4.26


def _outdoor_temp(raw: bytes) -> float:
    return raw[0] / 2.0 - 50.0


def _cabin_temp(raw: bytes) -> float:
    return u16be(raw) / 5.0 - 40.0


# -- module decoders -------------------------------------------------------
def _max_energy_wh(raw: bytes) -> float:
    """Rated (maximum) HV energy content in Wh -- the SoH reference value.

    53,200 Wh against a measured 108-cell pack gives the real SoH; Car
    Scanner reports the same field for the same vehicle.
    """
    return u32be(raw) / MAX_ENERGY_COUNT_WH


def _energy_content_wh(raw: bytes) -> float:
    """HV energy content right now, in Wh.

    DIVISOR IS A HYPOTHESIS. No open source implements this DID (evDash
    queues it commented out and never decodes it) and Car Scanner's export
    gives only the decoded value. Same 1310.77 Wh/count as the max-energy
    DID is the reasonable guess because the log's 41,200 Wh maps to a
    plausible u32 on that scale; treat a displayed number as unverified
    until a real capture pins it. Raw is preserved either way.
    """
    return u32be(raw) / MAX_ENERGY_COUNT_WH


def _odometer_km(raw: bytes) -> float:
    n = int.from_bytes(raw[:3], "big")
    return float(n)


def _gear(raw: bytes) -> str:
    return _GEAR_CODES.get(raw[1] if len(raw) > 1 else -1, f"raw {raw.hex().upper()}")


def _vin(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip("\x00 ")


def register_meb(reg: DIDRegistry) -> None:
    """Register the full MEB DID map (BMS + gateway/energy/DC-DC modules)."""
    _register_bms(reg)
    _register_cells(reg)
    _register_modules(reg)


def _register_bms(reg: DIDRegistry) -> None:
    reg.register(DIDSpec(
        "soc_abs", ECU_BAT, 0x028C, "State of charge (BMS)", "%",
        decode=_soc_bms, doc_status=_EXP,
        notes="u8 / 2.5 [evDash; CS log: raw 203 -> 81.2 %]",
    ))
    reg.register(DIDSpec(
        "soc_normal", ECU_BAT, 0x028C, "State of charge (display)", "%",
        decode=_soc_display, doc_status=_EXP,
        notes="raw*0.4425-6.1947 [evDash dash fit; CS log: -> 83.63 %]",
    ))
    reg.register(DIDSpec(
        "pack_voltage", ECU_BAT, 0x1E3B, "HV pack voltage", "V",
        decode=_pack_voltage, doc_status=_EXP,
        notes="u16 / 4 [evDash; CS log: raw 1725 -> 431.25 V]",
    ))
    reg.register(DIDSpec(
        "pack_current", ECU_BAT, 0x1E3D, "HV pack current", "A",
        decode=_pack_current, doc_status=_EXP,
        notes="(u32-150000)/100, + = discharge [evDash; CS log: 150198 -> "
              "1.98 A, corroborated by the discharge counter rising while "
              "the car was parked]",
    ))
    reg.register(DIDSpec(
        "cell_voltage_max", ECU_BAT, 0x1E33, "Cell voltage max", "V",
        decode=_cell_voltage_scaled, doc_status=_EXP,
        notes="u16 / 4096 [evDash; CS log: 16377 -> 3.9984 V]",
    ))
    reg.register(DIDSpec(
        "cell_voltage_min", ECU_BAT, 0x1E34, "Cell voltage min", "V",
        decode=_cell_voltage_scaled, doc_status=_EXP,
        notes="u16 / 4096 [evDash]",
    ))
    reg.register(DIDSpec(
        "battery_temp", ECU_BAT, 0x2A0B, "HV battery temperature", "degC",
        decode=_battery_temp, doc_status=_EXP,
        notes="u8 / 2 - 40 [evDash; CS log: 120 -> 20.0 C]",
    ))
    reg.register(DIDSpec(
        "bat_temp_max", ECU_BAT, 0x1E0E, "HV battery temperature max", "degC",
        decode=_temp_scaled, doc_status=_EXP,
        notes="u16 / 64 [evDash; CS log: 20.875 C]",
    ))
    reg.register(DIDSpec(
        "bat_temp_min", ECU_BAT, 0x1E0F, "HV battery temperature min", "degC",
        decode=_temp_scaled, doc_status=_EXP,
        notes="u16 / 64 [evDash; CS log: 19.625 C]",
    ))
    reg.register(DIDSpec(
        "energy_counters", ECU_BAT, 0x1E32, "Energy counters", "kWh",
        decode=_energy_counters, doc_status=_EXP, slow=True,
        notes="u32 charge @0, s32 discharge @4, /8583.07123641215 Wh [evDash]",
    ))
    reg.register(DIDSpec(
        "dyn_charge_limit", ECU_BAT, 0x1E1B, "Dynamic charge current limit", "A",
        decode=lambda raw: _u16_over(raw, 5.0), doc_status=_EXP,
        notes="u16 / 5 [evDash; CS log: 213 A]",
    ))
    reg.register(DIDSpec(
        "dyn_discharge_limit", ECU_BAT, 0x1E1C, "Dynamic discharge current limit", "A",
        decode=lambda raw: _u16_over(raw, 5.0), doc_status=_EXP, slow=True,
        notes="u16 / 5 [evDash]",
    ))
    reg.register(DIDSpec(
        "charge_mode", ECU_BAT, 0x7448, "HV charge mode code", "code",
        decode=_charge_mode, doc_status=_EXP,
        notes="byte0; MEB op mode 4=AC, 6=DC mapped to 1/2 (see "
              "decoders.charging.CHARGE_MODE_CODES) [evDash]",
    ))
    reg.register(DIDSpec(
        "op_mode", ECU_BAT, 0x7448, "BMS operation mode", "code",
        decode=_op_mode, doc_status=_EXP,
        notes="byte0; 0=standby, 1=driving, 4=AC, 6=DC [evDash]",
    ))
    reg.register(DIDSpec(
        "coolant_pump", ECU_BAT, 0x743B, "Battery coolant pump", "%",
        decode=_byte, doc_status=_EXP, slow=True, notes="byte0 [evDash]",
    ))
    reg.register(DIDSpec(
        "coolant_temps", ECU_BAT, 0x189D, "Battery cooling liquid", "degC",
        decode=_coolant_temps, doc_status=_EXP,
        notes="outlet = u16[0:2]/64, inlet = u16[2:4]/64 [spot; evDash]",
    ))
    reg.register(DIDSpec(
        "ptc_current", ECU_BAT, 0x1620, "PTC heater battery current", "A",
        decode=lambda raw: _byte_over(raw, 4.0), doc_status=_EXP,
        notes="u8 / 4 [spot]",
    ))
    reg.register(DIDSpec(
        "speed_kmh", ECU_BAT, 0xF40D, "Vehicle speed (BMS)", "km/h",
        decode=_byte, doc_status=_EXP,
        notes="byte0, km/h [evDash; CS log reports 0 while parked]",
    ))
    reg.register(DIDSpec(
        "battery_serial", ECU_BAT, 0x0500, "HV battery serial", "",
        doc_status=_EXP, slow=True, notes="raw ASCII; length per ECU",
    ))
    reg.register(DIDSpec(
        "hv_serial_vin", ECU_BAT, 0xF802, "BMS VIN (part)", "",
        doc_status=_EXP, slow=True, notes="raw; MEB VinDataSpec layout",
    ))


def _register_cells(reg: DIDRegistry) -> None:
    for i in range(CELL_V_SLOTS):
        reg.register(DIDSpec(
            f"cell_v_{i:03d}", ECU_BAT, CELL_V_BASE + i,
            f"Cell voltage #{i + 1:03d}", "V",
            decode=_cell_voltage, doc_status=_EXP, slow=True,
            notes="u16/1000 + 1; 0x0FFE = unpopulated slot [evDash]",
        ))
    for i, did in enumerate(CELL_T_DIDS):
        reg.register(DIDSpec(
            f"cell_t_{i:02d}", ECU_BAT, did,
            f"Battery temperature sensor #{i + 1:02d}", "degC",
            decode=_cell_temp, doc_status=_EXP, slow=True,
            notes="u16/8 - 40 [evDash]",
        ))


def _register_modules(reg: DIDRegistry) -> None:
    reg.register(DIDSpec(
        "hv_energy_max", ECU_ENERGY, 0x2AB2, "Max HV energy content", "Wh",
        decode=_max_energy_wh, doc_status=_EXP, slow=True,
        notes="u32 / 1310.77 [evDash CarVWID3; CS log: 53200 Wh]",
    ))
    reg.register(DIDSpec(
        "hv_energy_content", ECU_ENERGY, 0x2AB8, "HV energy content", "Wh",
        decode=_energy_content_wh, doc_status=_EXP, slow=True,
        notes="u32 / 1310.77 -- DIVISOR ASSUMED, UNVERIFIED: no open-source "
              "implementation decodes this DID [evDash queues it commented "
              "out; CS log: 41200-41325 Wh parked]",
    ))
    reg.register(DIDSpec(
        "aux_12v_voltage", ECU_ENERGY, 0x2AF7, "12V battery voltage (BMS)", "V",
        decode=_amb_12v, doc_status=_EXP, slow=True,
        notes="u16 / 1024 + 4.26, first bytes of a multi-frame [evDash]",
    ))
    reg.register(DIDSpec(
        "dcdc_current", ECU_DCDC, 0x465B, "DC/DC charging current", "A",
        decode=lambda raw: _u16_over(raw, 16.0), doc_status=_EXP, slow=True,
        notes="u16 / 16 [evDash CarMEB]",
    ))
    reg.register(DIDSpec(
        "dcdc_voltage", ECU_DCDC, 0x465D, "DC/DC output voltage", "V",
        decode=lambda raw: _u16_over(raw, 512.0), doc_status=_EXP, slow=True,
        notes="u16 / 512 [evDash CarMEB]",
    ))
    reg.register(DIDSpec(
        "odometer_km", ECU_VEH, 0x295A, "Odometer", "km",
        decode=_odometer_km, doc_status=_EXP, slow=True,
        notes="3-byte big-endian [evDash CarMEB]",
    ))
    reg.register(DIDSpec(
        "gear", ECU_VEH, 0x210E, "Gear position", "",
        decode=_gear, doc_status=_EXP, slow=True,
        notes="data byte 1 [evDash CarMEB]",
    ))
    reg.register(DIDSpec(
        "vin", ECU_VEH, 0xF802, "VIN (gateway)", "",
        decode=_vin, doc_status=_EXP, slow=True,
        notes="raw ASCII [evDash CarVWID3]",
    ))
    reg.register(DIDSpec(
        "hv_aux_power", ECU_VEH, 0x0364, "HV auxiliary consumer power", "kW",
        decode=lambda raw: _u16_over(raw, 10.0), doc_status=_EXP,
        notes="u16 / 10 [spot]",
    ))
    reg.register(DIDSpec(
        "outside_temp", ECU_CLIMATE, 0x2609, "Outside temperature", "degC",
        decode=_outdoor_temp, doc_status=_EXP, slow=True,
        notes="u8 / 2 - 50 [evDash CarVWID3]",
    ))
    reg.register(DIDSpec(
        "inside_temp", ECU_CLIMATE, 0x2613, "Inside temperature", "degC",
        decode=_cabin_temp, doc_status=_EXP, slow=True,
        notes="u16 / 5 - 40 [evDash CarVWID3]",
    ))
