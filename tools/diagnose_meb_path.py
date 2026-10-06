#!/usr/bin/env python3
"""
MEB Diagnostic Path Experiment Script

Run this when the adapter is available (COM3 free) to systematically
test the communication path Car Scanner uses vs what we currently do.

Usage:
    python tools/diagnose_meb_path.py

Each experiment tests one hypothesis. Results are printed and logged.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from diagnostic.elm327 import PINNED_PROTOCOL, Elm327Transport
from diagnostic.uds import (
    payloads_by_source,
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def send_raw(t: Elm327Transport, cmd: str, desc: str = "") -> list[str]:
    """Send raw command, print result."""
    log(f"TX: {cmd}  ({desc})")
    try:
        lines = t.send_command(cmd)
        for ln in lines:
            print(f"  RX: {ln}")
        return lines
    except Exception as e:
        print(f"  ERROR: {e}")
        return []


def test_functional_0100(t: Elm327Transport) -> None:
    """EXP-1: Basic CAN bus health - functional 0100"""
    log("=== EXP-1: Functional 0100 (bus health) ===")
    send_raw(t, "0100", "supported PIDs")


def test_functional_1001(t: Elm327Transport) -> None:
    """EXP-2: Functional UDS default session"""
    log("=== EXP-2: Functional 10 01 (default session) ===")
    lines = send_raw(t, "1001", "UDS default session")
    for src, payload in payloads_by_source(lines).items():
        if payload:
            print(f"  ECU {src}: {payload.hex().upper()}")


def test_functional_22F190(t: Elm327Transport) -> None:
    """EXP-3: Functional VIN read via DID F190"""
    log("=== EXP-3: Functional 22 F190 (VIN) ===")
    lines = send_raw(t, "22F190", "VIN by DID")
    for src, payload in payloads_by_source(lines).items():
        if payload:
            print(f"  ECU {src}: {payload.hex().upper()}")


def test_meb_addressing_negotiation(t: Elm327Transport) -> bool:
    """EXP-4: Verify ATCP 17 + ATSH FC007B negotiation"""
    log("=== EXP-4: MEB addressing negotiation ===")
    # ATCP 17
    lines = send_raw(t, "ATCP 17", "set priority byte 0x17")
    if any("?" in line for line in lines):
        log("FAIL: Adapter refused ATCP 17")
        return False
    # ATSH FC007B
    lines = send_raw(t, "ATSH FC007B", "set 29-bit header 0x17FC007B (BMS)")
    if any("?" in line for line in lines):
        log("FAIL: Adapter refused ATSH FC007B")
        return False
    log("OK: MEB physical addressing negotiated")
    return True


def test_meb_bms_did(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-5: Read BMS DID with MEB physical addressing"""
    log(f"=== EXP-5: BMS DID 0x{did:04X} ({name}) with MEB addressing ===")
    # Switch to BMS module
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable auto-formatting")

    # Build single-frame ISO-TP: length + payload + 0x55 padding
    payload = f"22{did:04X}"
    length = len(payload) // 2
    wire = f"{length:02X}{payload}" + "55" * 8
    wire = wire[:16]
    log(f"Wire frame: {wire}")
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X}")

    # Parse responses
    for _src, p in payloads_by_source(lines).items():
        if p:
            print(f"  ECU {_src}: {p.hex().upper()}")
            if p[0] == 0x62:
                print(f"    POSITIVE: data = {p[3:].hex().upper()}")
            elif p[0] == 0x7F:
                print(f"    NRC 0x{p[2]:02X}")


def test_meb_bms_with_atbi(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-6: BMS DID with ATBI before the module is addressed.

    This is the top candidate. Both reference implementations send ATBI
    (Bypass Initialization) immediately after pinning the protocol, so the
    clone does not run its own protocol-detection sequence and land somewhere
    other than ATSP7. Our _base_init() has no ATBI. If this experiment answers
    where test_meb_bms_did() did not, ATBI is the whole difference.

    ATBI is adapter-local configuration and reaches no ECU, so it is not a
    vehicle request and needs no validate_request().
    """
    log(f"=== EXP-6: BMS DID 0x{did:04X} ({name}) with ATBI ===")
    send_raw(t, "ATBI", "bypass initialization - stay on ATSP7")

    # Reference ordering: ATSH then ATCP (ABRP), or ATCP then ATSH (ours).
    # Both orderings are exercised by test_meb_bms_did, so here we keep ours.
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")

    payload = f"22{did:04X}"
    wire = (f"{len(payload) // 2:02X}{payload}" + "55" * 8)[:16]
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X} after ATBI")
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: BMS DID 0x{did:04X} -> {p.hex().upper()}")
            print("  INTERPRETATION: ATBI was the blocker.")
        elif p and p[0] == 0x7F:
            print(f"  BMS ALIVE but refused: NRC 0x{p[2]:02X}")


def test_meb_bms_with_atbi_session_tp(
        t: Elm327Transport, did: int, name: str) -> None:
    """EXP-8: ATBI + 10 01 + 3E 00, i.e. every reference step combined.

    The belt-and-braces arm. If the BMS only answers once a session is
    established AND the adapter is told not to re-detect, this finds it.
    """
    log(f"=== EXP-8: BMS DID 0x{did:04X} full sequence (ATBI+10 01+3E 00) ===")
    send_raw(t, "ATBI", "bypass init")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")

    # 10 01 = default diagnostic session. Read-only per uds.READ_SUBFUNCTIONS.
    send_raw(t, "0210015555555555555555", "10 01 default session")
    time.sleep(0.1)
    # 3E 00 = TesterPresent. Read-only.
    send_raw(t, "023E005555555555555555", "3E 00 tester present")
    time.sleep(0.1)

    payload = f"22{did:04X}"
    wire = (f"{len(payload) // 2:02X}{payload}" + "55" * 8)[:16]
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X} full sequence")
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: {p.hex().upper()}")
            print("  INTERPRETATION: session and/or tester-present was required.")
        elif p and p[0] == 0x7F:
            print(f"  BMS ALIVE but refused: NRC 0x{p[2]:02X}")


def test_meb_bms_with_session(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-6: BMS DID with default session first"""
    log(f"=== EXP-6: BMS DID 0x{did:04X} ({name}) with 10 01 session ===")
    # Switch to BMS
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")

    # Send default session
    wire = "0210015555555555555555"  # 2 bytes: 10 01 + padding
    send_raw(t, wire, "default session")

    # Wait a bit
    time.sleep(0.1)

    # Now read DID
    payload = f"22{did:04X}"
    length = len(payload) // 2
    wire = f"{length:02X}{payload}" + "55" * 8
    wire = wire[:16]
    send_raw(t, wire, f"BMS DID 0x{did:04X} after session")


def test_meb_bms_with_tester_present(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-7: BMS DID with periodic tester present"""
    log(f"=== EXP-7: BMS DID 0x{did:04X} with 3E 00 tester present ===")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")

    # Session
    send_raw(t, "0210015555555555555555", "session")
    time.sleep(0.1)

    # Tester present
    send_raw(t, "023E005555555555555555", "tester present")
    time.sleep(0.1)

    # Read DID
    payload = f"22{did:04X}"
    length = len(payload) // 2
    wire = f"{length:02X}{payload}" + "55" * 8
    wire = wire[:16]
    send_raw(t, wire, f"BMS DID 0x{did:04X} with TP")


def test_energy_module_11bit(t: Elm327Transport) -> None:
    """EXP-8: Energy module (0x710 -> 0x77A) on 11-bit addressing"""
    log("=== EXP-8: Energy module 0x710 (11-bit) ===")
    # Protocol 6 = 11-bit 500k
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority 0x00")
    send_raw(t, "ATSH 000710", "energy module header")
    send_raw(t, "ATCAF0", "disable CAF")

    # Read hv_energy_content (0x2AB8)
    payload = "222AB8"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AB8")

    # Read hv_energy_max (0x2AB2)
    payload = "222AB2"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AB2")

    # Read aux_12v_voltage (0x2AF7)
    payload = "222AF7"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AF7")

    # Restore protocol 7
    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "restore 29-bit")


def test_dcdc_module_29bit(t: Elm327Transport) -> None:
    """EXP-9: DC/DC module (0x17FC00B9) on 29-bit addressing"""
    log("=== EXP-9: DC/DC module 0x17FC00B9 (29-bit) ===")
    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "29-bit protocol")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC00B9", "DC/DC header")
    send_raw(t, "ATCAF0", "disable CAF")

    # dcdc_current (0x465B)
    payload = "22465B"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "DC/DC 0x465B")

    # dcdc_voltage (0x465D)
    payload = "22465D"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "DC/DC 0x465D")


def test_gateway_routing(t: Elm327Transport) -> None:
    """EXP-10: Try gateway routing - functional request to gateway (0x7E0) for BMS data"""
    log("=== EXP-10: Gateway routing (functional to 0x7E0) ===")
    # Functional addressing (default)
    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "29-bit")
    send_raw(t, "ATCP 18", "functional priority")
    send_raw(t, "ATSH DB33F1", "functional header")
    send_raw(t, "ATCAF1", "enable CAF")

    # Try reading BMS DIDs functionally - some gateways route
    for did, name in [(0x028C, "SoC"), (0x1E3B, "Pack V"), (0x2A0B, "Temp")]:
        payload = f"22{did:04X}"
        send_raw(t, payload, f"Functional DID 0x{did:04X} ({name})")
        time.sleep(0.2)


def test_11bit_bat_mgmt(t: Elm327Transport) -> None:
    """EXP-11: Try BMS on 11-bit addressing (0x7E5 -> 0x7ED)"""
    log("=== EXP-11: BMS on 11-bit (0x7E5) ===")
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority")
    send_raw(t, "ATSH 0007E5", "BMS 11-bit header")
    send_raw(t, "ATCAF0", "disable CAF")

    for did, name in [(0x028C, "SoC"), (0x1E3B, "Pack V"),
                      (0x1E3D, "Current"), (0x2A0B, "Temp")]:
        payload = f"22{did:04X}"
        wire = f"03{payload}5555555555555555"[:16]
        send_raw(t, wire, f"BMS 11-bit DID 0x{did:04X} ({name})")
        time.sleep(0.2)

    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "restore 29-bit")


def test_canfd_check(t: Elm327Transport) -> None:
    """EXP-12: Check if adapter reports CAN-FD capability"""
    log("=== EXP-12: CAN-FD capability check ===")
    send_raw(t, "AT@", "adapter description")
    send_raw(t, "ATRV", "voltage")
    send_raw(t, "ATDP", "protocol detection")
    # Some adapters support ATFD for CAN-FD
    send_raw(t, "ATFD", "CAN-FD query (may be unsupported)")


def run_all_experiments():
    """Run full experiment matrix."""
    cfg = load_config()
    t = Elm327Transport(port=cfg.get("adapter.port", "COM3"))

    try:
        log("Opening adapter...")
        t.open()
        identity = t.initialize()
        log(f"Adapter: {identity}")

        # Run experiments in order
        test_functional_0100(t)
        test_functional_1001(t)
        test_functional_22F190(t)

        meb_ok = test_meb_addressing_negotiation(t)

        if meb_ok:
            # Core BMS DIDs from Car Scanner
            for did, name in [
                (0x028C, "SoC"),
                (0x1E3B, "Pack Voltage"),
                (0x1E3D, "Pack Current"),
                (0x2A0B, "Battery Temp"),
                (0x1E0E, "Max Temp"),
                (0x1E0F, "Min Temp"),
            ]:
                test_meb_bms_did(t, did, name)
                time.sleep(0.2)

            # Top candidate: ATBI. Run this before the session variants,
            # because if it answers there is nothing to learn from adding
            # session/tester-present on top of a working path.
            test_meb_bms_with_atbi(t, 0x028C, "SoC")
            time.sleep(0.2)

            # With session
            test_meb_bms_with_session(t, 0x028C, "SoC")
            time.sleep(0.2)

            # With tester present
            test_meb_bms_with_tester_present(t, 0x028C, "SoC")
            time.sleep(0.2)

            # Everything at once
            test_meb_bms_with_atbi_session_tp(t, 0x028C, "SoC")
            time.sleep(0.2)

            # Energy module (11-bit)
            test_energy_module_11bit(t)
            time.sleep(0.2)

            # DC/DC module (29-bit)
            test_dcdc_module_29bit(t)
            time.sleep(0.2)

            # Gateway routing
            test_gateway_routing(t)
            time.sleep(0.2)

            # 11-bit BMS
            test_11bit_bat_mgmt(t)

        # CAN-FD check
        test_canfd_check(t)

    finally:
        t.close()
        log("Done")


if __name__ == "__main__":
    run_all_experiments()
