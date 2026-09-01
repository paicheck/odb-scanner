"""HV battery decoders.

Scale-factor sources:
  [OVMS]  Open Vehicle Monitoring System, vehicle_vweup module (MIT licence),
          decoding verified against the VW e-Up / MEB-family BMS.
          http://obd-amigos.linuxtech.net/files/amigos_PIDs.pm (referenced by OVMS)
  [TBD]   Applicability of a DID/scale to the ID.3 58 kWh specifically must
          be confirmed on the vehicle during discovery; until then the value
          is flagged EXPERIMENTAL and raw bytes are always stored.
"""
from __future__ import annotations

from .registry import (
    DIDRegistry,
    DIDSpec,
    DocStatus,
    Provenance,
    u16be,
    u32be,
)

ECU = "bat_mgmt"

# Documented cell-voltage DID range (e-Up/OVMS). The actual number of cell
# groups on an ID.3 58 kWh must be discovered at runtime; unverified DIDs
# answer with NRC 0x31 and are simply recorded as unavailable.
CELL_V_BASE = 0x1E40
CELL_V_COUNT_DOC = 102          # 0x1E40..0x1EA5 inclusive (documented range)
CELL_T_BASE = 0x1EAE
CELL_T_COUNT_DOC = 16           # 0x1EAE..0x1EBD inclusive (documented range)


def _pack_voltage(raw: bytes) -> float:
    return u16be(raw) / 64.0


def _pack_current(raw: bytes) -> float:
    # OVMS convention: zero point at raw 2048 in engineering units after
    # the /5 scale (i.e. raw 10240 = 0 A), 1/5 A per LSB. Positive = discharge.
    return u16be(raw) / 5.0 - 2048.0


def _cell_voltage(raw: bytes) -> float:
    return u16be(raw) / 256.0


def _battery_temp(raw: bytes) -> float:
    return u16be(raw) / 64.0 - 40.0


def _energy_counters(raw: bytes) -> dict:
    return {
        "charged_kwh": u32be(raw, 0) / 3600.0,
        "used_kwh": u32be(raw, 4) / 3600.0,
    }


def _soh_cac(raw: bytes) -> dict:
    # 0x74CB "Ah of HV battery and all cells": first word = battery CAC.
    # Scale /10 Ah is EXPERIMENTAL - raw preserved for re-analysis.
    return {"battery_cac_ah": u16be(raw, 0) / 10.0}


def register_battery(reg: DIDRegistry) -> None:
    reg.register(DIDSpec(
        "pack_voltage", ECU, 0x1E3B, "HV battery pack voltage", "V",
        decode=_pack_voltage, doc_status=DocStatus.DOCUMENTED,
        notes="u16 / 64 V [OVMS]",
    ))
    reg.register(DIDSpec(
        "pack_current", ECU, 0x1E3D, "HV battery current (+=discharge)", "A",
        decode=_pack_current, doc_status=DocStatus.DOCUMENTED,
        notes="(u16 - 2048) / 5 A [OVMS]",
    ))
    reg.register(DIDSpec(
        "soc_abs", ECU, 0x028C, "State of charge (absolute)", "%",
        decode=lambda raw: raw[0] / 2.5, doc_status=DocStatus.DOCUMENTED,
        notes="u8 / 2.5 % [OVMS]",
    ))
    reg.register(DIDSpec(
        "cell_voltage_max", ECU, 0x1E33, "Max cell voltage", "V",
        decode=_cell_voltage, doc_status=DocStatus.DOCUMENTED,
        notes="u16 / 256 V [OVMS]",
    ))
    reg.register(DIDSpec(
        "cell_voltage_min", ECU, 0x1E34, "Min cell voltage", "V",
        decode=_cell_voltage, doc_status=DocStatus.DOCUMENTED,
        notes="u16 / 256 V [OVMS]",
    ))
    reg.register(DIDSpec(
        "battery_temp", ECU, 0x2A0B, "HV battery temperature", "°C",
        decode=_battery_temp, doc_status=DocStatus.EXPERIMENTAL,
        notes="u16 / 64 - 40 °C [OVMS; scale unverified on ID.3]",
    ))
    reg.register(DIDSpec(
        "energy_counters", ECU, 0x1E32, "Lifetime energy counters", "kWh",
        decode=_energy_counters, doc_status=DocStatus.DOCUMENTED,
        notes="u32 charged @0, u32 used @4, / 3600 kWh [OVMS]", slow=True,
    ))
    reg.register(DIDSpec(
        "soh_cac", ECU, 0x74CB, "Battery capacity (CAC)", "Ah",
        decode=_soh_cac, doc_status=DocStatus.EXPERIMENTAL,
        notes="0x74CB first u16 / 10 Ah; SOH% = CAC/nominal (calculated)",
        slow=True,
    ))


def register_cell_specs(reg: DIDRegistry, count: int = CELL_V_COUNT_DOC) -> None:
    for i in range(count):
        reg.register(DIDSpec(
            f"cell_v_{i:03d}", ECU, CELL_V_BASE + i, f"Cell {i + 1} voltage", "V",
            decode=_cell_voltage, doc_status=DocStatus.EXPERIMENTAL,
            notes=f"DID 0x{CELL_V_BASE + i:04X}; documented range for e-Up "
                  "(0x1E40-0x1EA5); count on ID.3 discovered at runtime",
            slow=True,
        ))


def register_cell_temperature_specs(
    reg: DIDRegistry, count: int = CELL_T_COUNT_DOC
) -> None:
    for i in range(count):
        reg.register(DIDSpec(
            f"cell_t_{i:02d}", ECU, CELL_T_BASE + i, f"Cell sensor {i + 1} temp", "°C",
            decode=lambda raw: raw[0] * 0.5 - 50.0,
            doc_status=DocStatus.EXPERIMENTAL,
            notes="u8 * 0.5 - 50 °C [unverified scale]",
            slow=True,
        ))
