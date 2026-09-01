"""UDS (ISO 14229) support — READ-ONLY.

SAFETY ARCHITECTURE
-------------------
This module is the only place in the codebase that constructs UDS requests.
The request builders refuse any service outside the read allow-list:

    0x10  DiagnosticSessionControl (default session only, sub-function 0x01)
    0x22  ReadDataByIdentifier
    0x19  ReadDTCInformation
    0x3E  TesterPresent

Blocked (raises ReadOnlyViolationError): 0x14 ClearDiagnosticInformation,
0x27 SecurityAccess, 0x2E WriteDataByIdentifier, 0x31 RoutineControl,
0x34-0x37 data transfer, 0x2F InputOutputControlByIdentifier, 0x28/0x85
communication control, and every other service. There is deliberately NO
API that allows arbitrary payload transmission.

Every request that IS transmitted must carry a human-readable `purpose`
string; connection.py logs it to the database tx_log together with the raw
response, satisfying "document what is being transmitted and why".
"""
from __future__ import annotations

from .interface import CommunicationError

HEX_DIGITS = set("0123456789abcdefABCDEF")

SERVICES = {
    0x10: "DiagnosticSessionControl",
    0x22: "ReadDataByIdentifier",
    0x19: "ReadDTCInformation",
    0x3E: "TesterPresent",
}

BLOCKED_SERVICES = {
    0x14: "ClearDiagnosticInformation (erases vehicle data - BLOCKED)",
    0x27: "SecurityAccess (unlocks write access - BLOCKED)",
    0x28: "CommunicationControl - BLOCKED",
    0x2A: "ReadDataByPeriodicIdentifier - BLOCKED",
    0x2C: "DynamicallyDefineDataIdentifier - BLOCKED",
    0x2D: "DefinePIDByMemoryAddress - BLOCKED",
    0x2E: "WriteDataByIdentifier (modifies ECU data - BLOCKED)",
    0x2F: "InputOutputControlByIdentifier (actuators - BLOCKED)",
    0x31: "RoutineControl (routines/actuator tests - BLOCKED)",
    0x34: "RequestDownload (flashing - BLOCKED)",
    0x35: "RequestUpload - BLOCKED",
    0x36: "TransferData (flashing - BLOCKED)",
    0x37: "RequestTransferExit - BLOCKED",
    0x83: "AccessTimingParameter - BLOCKED",
    0x84: "LinkControl - BLOCKED",
    0x85: "ControlDTCSetting - BLOCKED",
    0x86: "ResponseOnEvent - BLOCKED",
    0x87: "LinkControl - BLOCKED",
}

NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x72: "generalProgrammingFailure",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


class ReadOnlyViolationError(PermissionError):
    """Raised when anything that could modify the vehicle is attempted."""


class ProtocolError(CommunicationError):
    pass


class NegativeResponseError(CommunicationError):
    def __init__(self, service: int, nrc: int):
        self.service = service
        self.nrc = nrc
        name = NRC.get(nrc, f"unknown(0x{nrc:02X})")
        super().__init__(
            f"UDS negative response: service 0x{service:02X}, "
            f"NRC 0x{nrc:02X} ({name})"
        )


def validate_service(service: int) -> None:
    if service in BLOCKED_SERVICES:
        raise ReadOnlyViolationError(
            f"Service 0x{service:02X} ({BLOCKED_SERVICES[service]}) "
            "is blocked. This system is strictly READ-ONLY."
        )
    if service not in SERVICES:
        raise ReadOnlyViolationError(
            f"Service 0x{service:02X} is not on the read-only allow-list "
            f"{sorted(hex(s) for s in SERVICES)}."
        )


def build_read_did_request(did: int) -> bytes:
    validate_service(0x22)
    if not 0 <= did <= 0xFFFF:
        raise ValueError(f"DID out of range: {did}")
    return bytes([0x22, (did >> 8) & 0xFF, did & 0xFF])


def build_session_request() -> bytes:
    """Default diagnostic session (0x10 0x01). Non-modifying; some ECUs
    require it before answering 0x22."""
    validate_service(0x10)
    return bytes([0x10, 0x01])


def build_tester_present() -> bytes:
    validate_service(0x3E)
    return bytes([0x3E, 0x00])


def clean_hex(line: str) -> str:
    return "".join(c for c in line if c in HEX_DIGITS).upper()


def _valid_frame(raw: bytes) -> bool:
    if not raw:
        return False
    pci = raw[0] >> 4
    if pci == 0:
        n = raw[0] & 0x0F
        return 0 < n <= 7 and len(raw) >= 1 + n
    if pci in (1, 2, 3):
        return len(raw) >= (2 if pci == 1 else 1)
    return False


def line_to_frame(line: str) -> bytes | None:
    """Convert one ELM327 output line into an ISO-TP frame payload.

    Handles both header-on (e.g. '7ED04621E3B0FA0') and header-off
    ('04621E3B0FA0') formats. Returns None if the line is not a frame
    (e.g. 'NO DATA', 'SEARCHING...', 'OK').
    """
    h = clean_hex(line)
    if len(h) < 4:
        return None
    candidates = (h[3:], h) if len(h) > 3 else (h,)
    for cand in candidates:
        try:
            raw = bytes.fromhex(cand)
        except ValueError:
            continue
        if _valid_frame(raw):
            return raw
    return None


def reassemble(frames: list[bytes]) -> bytes | None:
    if not frames:
        return None
    first = frames[0]
    if first[0] >> 4 == 0:
        n = first[0] & 0x0F
        return first[1 : 1 + n]
    if first[0] >> 4 != 1:
        return None
    total = ((first[0] & 0x0F) << 8) | first[1]
    data = bytearray(first[2:])
    seq = 1
    for f in frames[1:]:
        if f[0] >> 4 != 2 or (f[0] & 0x0F) != seq % 16:
            return None
        data.extend(f[1:])
        seq += 1
        if len(data) >= total:
            break
    return bytes(data[:total])


def parse_elm_lines(lines: list[str]) -> bytes | None:
    """Parse cleaned ELM327 response lines into one UDS payload."""
    frames = [f for f in (line_to_frame(ln) for ln in lines) if f]
    return reassemble(frames)


def expect_positive(payload: bytes, service: int) -> bytes:
    if not payload:
        raise ProtocolError("Empty UDS payload")
    if payload[0] == 0x7F:
        nrc = payload[2] if len(payload) > 2 else 0
        raise NegativeResponseError(service, nrc)
    if payload[0] != (service + 0x40) & 0xFF:
        raise ProtocolError(
            f"Unexpected response 0x{payload[0]:02X} to service 0x{service:02X}"
        )
    return payload


def encode_isotp(payload: bytes) -> list[bytes]:
    """Encode a payload into ISO-TP frames (used by tests/simulator).

    First-frame length field is 12 bits: [0x1L, L_low] where L = 12-bit
    length. [documented ISO 15765-2]
    """
    n = len(payload)
    if n <= 7:
        return [bytes([n]) + payload]
    frames = [bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]]
    rest = payload[6:]
    seq = 1
    while rest:
        frames.append(bytes([0x20 | (seq % 16)]) + rest[:7])
        rest = rest[7:]
        seq += 1
    return frames

