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

import argparse
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
from tools import experiment_record as record

# The arm currently running, so its actual response and interpretation land in
# the right place in the structured record.
_current: record.Arm | None = None

# (command, first response line) for the running arm.
_captured: list[tuple[str, str]] = []


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


# Which DIDs the baseline arm (EXP-5, MEB addressing alone) actually got an
# answer for. Every variant arm compares against this before claiming credit:
# without it, a permissive adapter -- or the simulator, which answers no matter
# what -- makes all fifteen arms print "was required" and the output says
# nothing. The claim is only meaningful when the baseline was SILENT.
_baseline_answered: set[int] = set()


def claim(interpretation: str, did: int, answered: bool) -> None:
    """Report a variant's result, crediting it only against a silent baseline."""
    if not answered:
        if _current and not _current.interpret:
            _current.interpret = (
                "No response, so nothing here is attributable to this arm. "
                "Compare against whether EXP-5 was silent for the same DID.")
        return
    if did in _baseline_answered:
        print("  (baseline EXP-5 already answered this DID, so nothing here "
              "is attributable to this arm)")
        if _current:
            _current.interpret = (
                "Nothing attributable: the baseline answered this DID, so the "
                "BMS path worked without this arm.")
        return
    print(f"  INTERPRETATION: {interpretation}")
    if _current:
        _current.interpret = interpretation


def begin(exp_id: str) -> None:
    """Mark which arm is starting, closing out the previous one.

    Every response the arm produces is captured by send_raw() in between, and
    summarised into the record when the next arm begins or the run ends. An arm
    that ran and was silent is a result and is recorded as such, which is not
    the same as an arm nobody has performed yet.
    """
    finish()
    global _current
    try:
        _current = record.by_id(exp_id)
    except KeyError:
        _current = None
    _captured.clear()


def finish() -> None:
    """Summarise what the running arm actually returned."""
    global _current
    if _current is None:
        return
    if not _captured:
        # Configuration-only arms (EXP-4, EXP-13) send no DID request, so there
        # is no data response to record. Left unset, they keep rendering as not
        # run rather than claiming a result they never measured.
        _current = None
        return
    data = [line for cmd, line in _captured if not cmd.startswith("AT")]
    _current.actual = "; ".join(data)[:400]
    _current = None


def send_raw(t: Elm327Transport, cmd: str, desc: str = "") -> list[str]:
    """Send raw command, print result."""
    log(f"TX: {cmd}  ({desc})")
    try:
        lines = t.send_command(cmd)
        for ln in lines:
            print(f"  RX: {ln}")
        # Capture the first line per command. AT* commands configure the
        # adapter and are excluded from the recorded response; a DID frame's
        # first line is the thing that distinguishes an answer from silence.
        if lines and not cmd.startswith("AT"):
            _captured.append((cmd, lines[0]))
        return lines
    except Exception as e:
        print(f"  ERROR: {e}")
        return []


def test_functional_0100(t: Elm327Transport) -> None:
    """EXP-1: Basic CAN bus health - functional 0100"""
    begin("EXP-1")
    log("=== EXP-1: Functional 0100 (bus health) ===")
    send_raw(t, "0100", "supported PIDs")


def test_functional_1001(t: Elm327Transport) -> None:
    """EXP-2: Functional UDS default session"""
    begin("EXP-2")
    log("=== EXP-2: Functional 10 01 (default session) ===")
    lines = send_raw(t, "1001", "UDS default session")
    for src, payload in payloads_by_source(lines).items():
        if payload:
            print(f"  ECU {src}: {payload.hex().upper()}")


def test_functional_22F190(t: Elm327Transport) -> None:
    """EXP-3: Functional VIN read via DID F190"""
    begin("EXP-3")
    log("=== EXP-3: Functional 22 F190 (VIN) ===")
    lines = send_raw(t, "22F190", "VIN by DID")
    for src, payload in payloads_by_source(lines).items():
        if payload:
            print(f"  ECU {src}: {payload.hex().upper()}")


def test_meb_addressing_negotiation(t: Elm327Transport) -> bool:
    """EXP-4: Verify ATCP 17 + ATSH FC007B negotiation"""
    begin("EXP-4")
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
    begin("EXP-5")
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
    answered = False
    for _src, p in payloads_by_source(lines).items():
        if p:
            print(f"  ECU {_src}: {p.hex().upper()}")
            if p[0] == 0x62:
                print(f"    POSITIVE: data = {p[3:].hex().upper()}")
                answered = True
            elif p[0] == 0x7F:
                print(f"    NRC 0x{p[2]:02X}")
    if answered:
        _baseline_answered.add(did)


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
    begin("EXP-6")
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
    _hit = False
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: BMS DID 0x{did:04X} -> {p.hex().upper()}")
            _hit = True
        elif p and p[0] == 0x7F:
            print(f"  BMS ALIVE but refused: NRC 0x{p[2]:02X}")
    claim("ATBI was the blocker.", did, _hit)


def test_meb_bms_with_atbi_session_tp(
        t: Elm327Transport, did: int, name: str) -> None:
    """EXP-8: ATBI + 10 01 + 3E 00, i.e. every reference step combined.

    The belt-and-braces arm. If the BMS only answers once a session is
    established AND the adapter is told not to re-detect, this finds it.
    """
    begin("EXP-8")
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
    _hit = False
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: {p.hex().upper()}")
            _hit = True
        elif p and p[0] == 0x7F:
            print(f"  BMS ALIVE but refused: NRC 0x{p[2]:02X}")
    claim("session and/or tester-present was required.", did, _hit)


def test_meb_bms_with_flow_control(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-14: BMS DID with ATCF 17FE7 flow control set.

    ABRP sends `ATCF 17FE7`, and Car Scanner's documented per-PID start commands
    include `ATCRA7E8,ATFCSH7E0,ATFCSD300000` -- so two sources that reach this
    BMS set flow control and neither evDash nor we do. Flow control is what the
    ECU is told to do about *its own* transmitted frames, which on a 29-bit bus
    the ELM327 is otherwise guessing at.

    Adapter-local; reaches no ECU.
    """
    begin("EXP-14")
    log(f"=== EXP-14: BMS DID 0x{did:04X} ({name}) with ATCF 17FE7 ===")
    send_raw(t, "ATCF 17FE7", "flow control send header")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")
    payload = f"22{did:04X}"
    wire = (f"{len(payload) // 2:02X}{payload}" + "55" * 8)[:16]
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X} after ATCF")
    _hit = False
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: {p.hex().upper()}")
            _hit = True
    claim("flow control was required.", did, _hit)


def test_meb_bms_with_atcra(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-15: BMS DID with ATCRA pinned to the module's response id.

    ABRP sends `ATCRA17FE007B` and spot2000 states ATCRA per module (BMS =
    17fe007b). Ours sends `ATCRA0`, which is not "accept all" -- ATCRA *sets* a
    filter, so ATCRA0 means "accept only CAN id 0x000". This clone appears to
    ignore it (the post-init warm-up still returns frames), but an adapter that
    honoured it would go deaf.

    Adapter-local; reaches no ECU. Note the repo's set_receive_address() is a
    deliberate no-op because the field clone refuses the plain ATCRA that clears
    a filter -- only ATZ recovers.
    """
    begin("EXP-15")
    log(f"=== EXP-15: BMS DID 0x{did:04X} ({name}) with ATCRA 17FE007B ===")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 17FE007B", "receive filter = BMS response id")
    payload = f"22{did:04X}"
    wire = (f"{len(payload) // 2:02X}{payload}" + "55" * 8)[:16]
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X} after ATCRA")
    _hit = False
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: {p.hex().upper()}")
            _hit = True
    claim("the ATCRA filter was required.", did, _hit)
    send_raw(t, "ATCRA 17FE007B", "restore accept-all-ish filter")


def test_meb_bms_everything(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-16: ATBI + ATCF + ATCRA + session + tester present, all at once.

    The last resort, and the arm most likely to reproduce Car Scanner's path if
    none of the single-variable arms does. If this answers and EXP-6/8/14/15 do
    not, the path needs several of them together and each will need isolating
    against the log rather than by bisection on the car.
    """
    begin("EXP-16")
    log(f"=== EXP-16: BMS DID 0x{did:04X} everything at once ===")
    send_raw(t, "ATBI", "bypass init")
    send_raw(t, "ATCF 17FE7", "flow control send header")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC007B", "BMS header")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 17FE007B", "receive filter")
    send_raw(t, "0210015555555555555555", "10 01 default session")
    time.sleep(0.1)
    send_raw(t, "023E005555555555555555", "3E 00 tester present")
    time.sleep(0.1)
    payload = f"22{did:04X}"
    wire = (f"{len(payload) // 2:02X}{payload}" + "55" * 8)[:16]
    lines = send_raw(t, wire, f"BMS DID 0x{did:04X} everything")
    _hit = False
    for _src, p in payloads_by_source(lines).items():
        if p and p[0] == 0x62:
            print(f"  ANSWERED: {p.hex().upper()}")
            _hit = True
        elif p and p[0] == 0x7F:
            print(f"  BMS ALIVE but refused: NRC 0x{p[2]:02X}")
    claim("the full reference configuration is the path. Isolate against the "
          "log on the next run.", did, _hit)


def test_meb_bms_with_session(t: Elm327Transport, did: int, name: str) -> None:
    """EXP-7: BMS DID with default session first"""
    begin("EXP-7")
    log(f"=== EXP-7: BMS DID 0x{did:04X} ({name}) with 10 01 session ===")
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
    """EXP-7b: BMS DID with periodic tester present"""
    begin("EXP-7b")
    log(f"=== EXP-7b: BMS DID 0x{did:04X} with 3E 00 tester present ===")
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
    """EXP-9: Energy module (0x710 -> 0x77A) on 11-bit addressing"""
    begin("EXP-9")
    log("=== EXP-9: Energy module 0x710 (11-bit) ===")
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
    """EXP-10: DC/DC module (0x17FC00B9) on 29-bit addressing"""
    begin("EXP-10")
    log("=== EXP-10: DC/DC module 0x17FC00B9 (29-bit) ===")
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
    """EXP-11: Try gateway routing - functional request to gateway (0x7E0) for BMS data"""
    begin("EXP-11")
    log("=== EXP-11: Gateway routing (functional to 0x7E0) ===")
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
    """EXP-12: Try BMS on 11-bit addressing (0x7E5 -> 0x7ED)"""
    begin("EXP-12")
    log("=== EXP-12: BMS on 11-bit (0x7E5) ===")
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
    """EXP-13: Check if adapter reports CAN-FD capability"""
    begin("EXP-13")
    log("=== EXP-13: CAN-FD capability check ===")
    send_raw(t, "AT@", "adapter description")
    send_raw(t, "ATRV", "voltage")
    send_raw(t, "ATDP", "protocol detection")
    # Some adapters support ATFD for CAN-FD
    send_raw(t, "ATFD", "CAN-FD query (may be unsupported)")


def run_all_experiments(tcp=None):
    """Run the full experiment matrix.

    tcp=(host, port) talks to the built-in simulator instead of the car. That
    exists so the harness itself can be verified: every experiment below has
    been run at least once, and the ones that should answer a working MEB
    implementation do. A matrix that has never executed is not evidence, it is
    a guess with print statements.
    """
    cfg = load_config()
    if tcp:
        host, port = tcp
        log(f"TARGET: simulator at {host}:{port} (harness self-test)")
        t = Elm327Transport(host=host, tcp_port=int(port),
                            timeout=float(cfg.get("adapter.timeout", 5.0)))
    else:
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

            # Adapter-state arms: flow control and the receive filter. These
            # are the two things Car Scanner demonstrably does that evDash and
            # this project do not.
            test_meb_bms_with_flow_control(t, 0x028C, "SoC")
            time.sleep(0.2)

            test_meb_bms_with_atcra(t, 0x028C, "SoC")
            time.sleep(0.2)

            test_meb_bms_everything(t, 0x028C, "SoC")
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
        # Close out the last arm and write the record, so a run against the car
        # leaves behind something readable a week later rather than a log file.
        finish()
        _write_record(cfg, tcp)
        t.close()
        log("Done")


def _write_record(cfg, tcp) -> None:
    """Persist the record, refusing to let a simulator run write a car result.

    The record is the evidence this whole investigation rests on. A harness
    self-test landing in the same file as a real run would make `docs/` look
    like the BMS had answered, and nothing in the file would say otherwise --
    which is the one failure mode the record exists to prevent. So a --tcp run
    must name its own destination, and the checked-in path stays reserved for
    the car.
    """
    configured = cfg.get("adapter.record_path") or ""
    if tcp:
        out = configured or str(ROOT / "docs" / "experiment_record_sim.md")
        note = ("SIMULATOR run -- harness self-test, not vehicle evidence")
    else:
        out = configured or str(ROOT / "docs" / "experiment_record_results.md")
        note = ""
    try:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(record.render_markdown(
            preamble=note) if note else record.render_markdown(),
            encoding="utf-8")
        log(f"Experiment record written to {out}")
    except OSError as exc:
        # A failed write must not lose the run: the console output above is
        # still the primary result, and the record can be regenerated.
        log(f"WARNING: could not write experiment record: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Systematically test how this ID.3's MEB modules answer. "
                    "Every request is read-only; see uds.py.")
    ap.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"),
                    help="run against the built-in simulator instead of the "
                         "car. Verifies this harness itself before it is "
                         "pointed at real hardware.")
    args = ap.parse_args()
    tcp = (args.tcp[0], int(args.tcp[1])) if args.tcp else None
    run_all_experiments(tcp=tcp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
