"""VW MEB (ID.3 / ID.4 / Enyaq / Born) UDS DID map -- diagnostic-bus profile.

Why a separate map: on MEB the HV battery is not exposed through the OBD
functional addressing that works on the e-Up. The BMS answers only at its
29-bit module address 0x17FC007B (ATCP 17 + ATSH FC007B -- see
Elm327Transport.set_module), and the scale factors differ from the e-Up ones
(pack voltage u16/4, not u16/64). Functionally-read MEB DIDs answer
NRC 0x31 (requestOutOfRange), which is the wall the dashboard reports.

Provenance of the scale factors:

  [evDash]  nickn17/evDash CarVWID3.cpp / CarMEB.cpp (community RE of the
            MEB diagnostics, Apache-2.0 sources)
  [ABRP]    iternio/ev-obd-pids MEB.json (independent implementation)
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
ECU_ENERGY = "energy"    # MEB module 0x710
ECU_DCDC = "dcdc"        # MEB module 0xB9
ECU_VEH = "veh_info"     # MEB module 0x76

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
    """Pack current in A; positive = discharging (evDash convention)."""
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


# -- module decoders -------------------------------------------------------
def _max_energy_wh(raw: bytes) -> float:
    """Rated (maximum) HV energy content in Wh -- the SoH reference value.

    53,200 Wh against a measured 108-cell pack gives the real SoH; Car
    Scanner reports the same field for the same vehicle.
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
        notes="(u32-150000)/100, + = discharge [evDash; CS log: 150198 -> 1.98 A]",
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
        notes="u32 / 1310.77 [evDash CarMEB; CS log: 53200 Wh]",
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
        notes="raw ASCII [evDash CarMEB]",
    ))