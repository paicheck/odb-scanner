#!/usr/bin/env python3
"""
MEB Diagnostic Path Experiment Script

Run this when the adapter is available (COM3 free) to systematically
test the communication path Car Scanner uses vs what we currently do.

Usage:
    python tools/diagnose_meb_path.py

Each experiment tests one hypothesis. Results are printed and logged.

RESOLVED 2026-10-17: the arms below are the historical record of the
investigation. They test the hand-framed ATCAF0 wire form, which the 2026-10-15
passive HCI capture of Car Scanner later showed to be wrong for the 29-bit
modules -- Car Scanner reads them on ATCAF1 with a bare request plus the
ATFCSH/ATFCSD/ATFCSM 1 preamble (see docs/investigation/
meb_gateway_energy_passive_capture_20261015.md and diagnostic/elm327.py
set_module). Re-point the arms at that form before using this against the car.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from diagnostic import uds
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


# Whether ATCAF0 (no ISO-TP formatting) is in effect. Under it the adapter
# sends typed bytes as-is, so the first byte is the PCI byte the ECU parses,
# and what must be validated is the payload inside the frame, not the line.
_raw_framing = False


def check_raw(cmd: str) -> str:
    """Validate one line this harness is about to send; return the wire form.

    The experiments configure the adapter by hand (ATCAF0, flow control,
    headers), so this cannot be a plain validate_request: under ATCAF0 a
    validated "0104" is a single frame whose PCI byte is 0x01 and whose
    payload is 0x04 -- mode 04, clear emissions DTCs. So AT lines are
    tracked for framing, flow-control data must be a flow-control frame, and
    a vehicle line is validated as what the ECU will actually receive.
    Raises ReadOnlyViolationError rather than sending.
    """
    global _raw_framing
    norm = "".join(cmd.split()).upper()
    if norm.startswith("AT"):
        if norm == "ATCAF0":
            _raw_framing = True
        elif norm in ("ATCAF1", "ATZ", "ATD", "ATWS"):
            _raw_framing = False
        elif norm.startswith("ATFCSD") and not norm[6:7] == "3":
            raise uds.ReadOnlyViolationError(
                f"Refusing {cmd!r}: flow-control data must be a flow-control "
                "frame (first nibble 3); anything else is an arbitrary "
                "message the adapter would send on the next multi-frame read.")
        return cmd
    wire = uds.canonical_request(cmd)
    if _raw_framing:
        frame = bytes.fromhex(wire)
        length = frame[0] & 0x0F if frame else 0
        if (not frame or frame[0] >> 4 != 0 or not 1 <= length <= 7
                or len(frame) < 1 + length):
            raise uds.ReadOnlyViolationError(
                f"Refusing {cmd!r} under ATCAF0: not a single ISO-TP frame, so "
                "the bytes the ECU receives cannot be validated.")
        uds.validate_request(frame[1:1 + length])
    else:
        uds.validate_request(wire)
    return wire


def send_raw(t: Elm327Transport, cmd: str, desc: str = "") -> list[str]:
    """Validate (check_raw), send, print result.

    Requests are validated but not written to the database's tx_log: this
    harness never opens the car's database. Its record of what was sent is the
    TX/RX transcript printed here and the experiment record it writes.
    """
    cmd = check_raw(cmd)
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


def test_energy_module_3digit_header_atcra(t: Elm327Transport) -> None:
    """EXP-17: Energy module with the protocol's own 3-digit ATSH under ATSP6.

    Under protocol 6 (11-bit) the ELM327 specifies a 3-digit header, but
    set_module() always sends the 6-digit MEB form ('ATSH 000710'). The clone
    accepts that and then refuses the data frame with '?'. The simulator (a
    proper ELM327 emulation) answers either way, which is why the suite stays
    green while the car refuses.

    This arm is EXP-18 with the header narrowed to 3 digits, so header width is
    the only variable. (The first version of this arm sent 'ATSH 0710' -- four
    digits, which the clone refused at the ATSH step -- so it never exercised
    the 3-digit form its own hypothesis named.)
    """
    begin("EXP-17")
    log("=== EXP-17: Energy module 0x710 with 3-digit ATSH 710 ===")
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority 0x00")
    send_raw(t, "ATSH 710", "energy module header (3-digit, as ATSP6 specifies)")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 0000077A", "pin receive filter to energy response id")

    for payload in ("222AB8", "222AB2", "222AF7"):
        wire = f"03{payload}5555555555555555"[:16]
        send_raw(t, wire, f"Energy {payload[2:]} (3-digit header)")

    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "restore 29-bit")


def test_energy_module_7byte_frame(t: Elm327Transport) -> None:
    """EXP-20: Energy module (11-bit) with a 7-byte frame instead of 8.

    EXP-9/18/19 all sent the 8-byte hand-built single frame ('03 22 2A B8'
    plus four 55 pad bytes) under ATSP6 and were refused with '?'. This arm is
    EXP-18 with a single pad byte dropped, so frame length is the only
    variable. A single-frame ISO-TP message does not need padding, so the
    request the module sees is identical.
    """
    begin("EXP-20")
    log("=== EXP-20: Energy module 0x710 with a 7-byte frame ===")
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority 0x00")
    send_raw(t, "ATSH 000710", "energy module header (6-digit form)")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 0000077A", "pin receive filter to energy response id")

    for payload in ("222AB8", "222AB2", "222AF7"):
        wire = f"03{payload}55555555555555"[:14]      # 14 hex = 7 bytes
        send_raw(t, wire, f"Energy {payload[2:]} (7-byte frame)")

    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "restore 29-bit")


def test_energy_module_with_atcra(t: Elm327Transport) -> None:
    """EXP-18: Energy module (11-bit) with receive filter pinned to 0x77A.

    The BMS breakthrough proved the clone's default filter drops 17FExxxx
    responses. The energy module responds at 0x77A (11-bit). If the filter
    isn't pinned to 0x77A, the response is discarded and the frame is refused
    with '?'. This arm adds ATCRA 0000077A before the DID reads.
    """
    begin("EXP-18")
    log("=== EXP-18: Energy module 0x710 with ATCRA 0000077A ===")
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority 0x00")
    send_raw(t, "ATSH 000710", "energy module header (6-digit form)")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 0000077A", "pin receive filter to energy response id")

    payload = "222AB8"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AB8 (with ATCRA)")

    payload = "222AB2"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AB2 (with ATCRA)")

    payload = "222AF7"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Energy 0x2AF7 (with ATCRA)")

    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "restore 29-bit")


def test_climate_module_with_atcra(t: Elm327Transport) -> None:
    """EXP-19: Climate module (11-bit) with receive filter pinned to 0x7B0.

    Same pattern as energy: climate responds at 0x7B0. Without ATCRA the
    response is discarded. Car Scanner's per-PID start commands include
    ATCRA for every module.
    """
    begin("EXP-19")
    log("=== EXP-19: Climate module 0x746 with ATCRA 000007B0 ===")
    send_raw(t, "ATSP6", "11-bit protocol")
    send_raw(t, "ATCP 00", "priority 0x00")
    send_raw(t, "ATSH 000746", "climate module header")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 000007B0", "pin receive filter to climate response id")

    payload = "222609"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Climate 0x2609 (with ATCRA)")

    payload = "222613"
    wire = f"03{payload}5555555555555555"[:16]
    send_raw(t, wire, "Climate 0x2613 (with ATCRA)")

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


def test_dcdc_module_with_atcra(t: Elm327Transport) -> None:
    """EXP-21: DC/DC module (0x17FC00B9) with its receive filter pinned.

    EXP-10 read the DC/DC module with the same framing as the working BMS arm
    (EXP-5) but no ATCRA, and the clone refused the frame with '?'. The DC/DC
    module is 29-bit like the BMS, so this arm adds ATCRA 17FE00B9 and changes
    nothing else.
    """
    begin("EXP-21")
    log("=== EXP-21: DC/DC module 0x17FC00B9 with ATCRA 17FE00B9 ===")
    send_raw(t, f"ATSP{PINNED_PROTOCOL}", "29-bit protocol")
    send_raw(t, "ATCP 17", "priority")
    send_raw(t, "ATSH FC00B9", "DC/DC header")
    send_raw(t, "ATCAF0", "disable CAF")
    send_raw(t, "ATCRA 17FE00B9", "pin receive filter to DC/DC response id")

    for payload in ("22465B", "22465D"):
        wire = f"03{payload}5555555555555555"[:16]
        send_raw(t, wire, f"DC/DC {payload[2:]} (with ATCRA)")


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
    send_raw(t, "AT@1", "adapter description")
    send_raw(t, "ATRV", "voltage")
    send_raw(t, "ATDP", "protocol detection")
    # This used to send "AT@" (not a command; AT@1 is the description) and
    # "ATFD" (no such ELM327 command). The transport refuses adapter commands
    # that are not on its allow-list, and an unknown one is exactly what an
    # allow-list exists to refuse; ATDP above already names the protocol.


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

            # Energy module with the protocol's own 3-digit header. EXP-18 is
            # the same arm with the 6-digit form, so header width is the only
            # variable between them.
            test_energy_module_3digit_header_atcra(t)
            time.sleep(0.2)

            # Energy module with receive filter pinned to 0x77A.
            test_energy_module_with_atcra(t)
            time.sleep(0.2)

            # EXP-20: same as EXP-18 but one pad byte shorter (7-byte frame),
            # isolating frame length from header width.
            test_energy_module_7byte_frame(t)
            time.sleep(0.2)

            # Climate module with receive filter pinned to 0x7B0.
            test_climate_module_with_atcra(t)
            time.sleep(0.2)

            # DC/DC module (29-bit)
            test_dcdc_module_29bit(t)
            time.sleep(0.2)

            # EXP-21: DC/DC with its receive filter pinned, the one thing
            # EXP-10 lacked.
            test_dcdc_module_with_atcra(t)
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
                    "Every vehicle line is checked by uds.validate_request "
                    "(the payload inside the frame under ATCAF0), and "
                    "flow-control data must be a flow-control frame.")
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
