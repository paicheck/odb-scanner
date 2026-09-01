"""Charging decoders (OBC + charge management ECU)."""
from __future__ import annotations

from .registry import (
    DIDRegistry,
    DIDSpec,
    DocStatus,
    u16be,
)

ECU_MGMT = "chg_mgmt"
ECU_CHG = "chg"

CHARGE_MODE_CODES = {
    0: "idle/ready",
    1: "AC charging",
    2: "DC charging",
    3: "AC charging (high power)",
}


def _charge_mode(raw: bytes) -> int:
    return raw[0]


def register_charging(reg: DIDRegistry) -> None:
    reg.register(DIDSpec(
        "charge_mode", ECU_MGMT, 0x1DD6, "HV charge mode code", "code",
        decode=_charge_mode, doc_status=DocStatus.EXPERIMENTAL,
        notes=f"byte0; candidate mapping {CHARGE_MODE_CODES} [unverified]",
    ))
    reg.register(DIDSpec(
        "soc_normal", ECU_MGMT, 0x1DD0, "State of charge (normal/display)", "%",
        decode=lambda raw: raw[0] / 2.0, doc_status=DocStatus.EXPERIMENTAL,
        notes="u8 / 2 % [scale unverified]",
    ))
    reg.register(DIDSpec(
        "ac_voltage", ECU_CHG, 0x41FC, "AC charge voltage", "V",
        decode=lambda raw: float(u16be(raw)), doc_status=DocStatus.DOCUMENTED,
        notes="u16, volts [OVMS gen2]",
    ))
    reg.register(DIDSpec(
        "ac_current", ECU_CHG, 0x41FB, "AC charge current", "A",
        decode=lambda raw: raw[0] / 10.0, doc_status=DocStatus.DOCUMENTED,
        notes="u8 / 10 A [OVMS gen2]",
    ))
    reg.register(DIDSpec(
        "dc_voltage", ECU_CHG, 0x41F8, "DC charge voltage", "V",
        decode=lambda raw: u16be(raw) / 10.0, doc_status=DocStatus.EXPERIMENTAL,
        notes="u16 / 10 V [scale unverified]",
    ))
    reg.register(DIDSpec(
        "dc_current", ECU_CHG, 0x41F9, "DC charge current", "A",
        decode=lambda raw: u16be(raw) / 10.0, doc_status=DocStatus.EXPERIMENTAL,
        notes="u16 / 10 A [scale unverified]",
    ))
    reg.register(DIDSpec(
        "ccs_status", ECU_MGMT, 0x1DEF, "CCS charger status", "raw",
        decode=None, doc_status=DocStatus.UNKNOWN,
        notes="Structure not publicly documented; raw bytes preserved",
        slow=True,
    ))
    reg.register(DIDSpec(
        "charge_remaining", ECU_MGMT, 0x1DE4, "Remaining charge time", "min",
        decode=lambda raw: float(u16be(raw)), doc_status=DocStatus.EXPERIMENTAL,
        notes="u16; unit assumed minutes [unverified]", slow=True,
    ))
    reg.register(DIDSpec(
        "lv_powerstate", ECU_MGMT, 0x1DEC, "Low-voltage (12V) power state", "code",
        decode=lambda raw: raw[0], doc_status=DocStatus.EXPERIMENTAL,
        notes="byte0; 0=off/1=on assumed [unverified]", slow=True,
    ))
    reg.register(DIDSpec(
        "socket_status", ECU_CHG, 0x1DDA, "Charge socket status", "raw",
        decode=None, doc_status=DocStatus.UNKNOWN,
        notes="Structure not publicly documented; raw bytes preserved",
    ))
