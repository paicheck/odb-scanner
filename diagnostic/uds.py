"""UDS (ISO 14229) support — READ-ONLY.

SAFETY ARCHITECTURE
-------------------
This module is the only place in the codebase that constructs UDS requests.
The request builders refuse any service outside the read allow-list:

    0x10  DiagnosticSessionControl (default session ONLY, sub-function 0x01)
    0x22  ReadDataByIdentifier
    0x19  ReadDTCInformation (read sub-functions 0x01-0x09 ONLY)
    0x3E  TesterPresent
    0x01/0x03/0x09  read-only OBD-II modes

The allow-list is enforced on the WHOLE request, not just the service byte:
a service whose sub-functions include a state-changing one is narrowed to
the read sub-functions. `0x10 0x02` (programmingSession) and `0x10 0x03`
(extendedDiagnosticSession) both change ECU behaviour and gate writes, and
`0x19 0x0A` (stopResponseOnEvent) is a control operation -- all three are
now refused even though `0x10` and `0x19` themselves are allow-listed.

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
    # Read-only OBD-II modes. These only ever *read* emissions data, so they
    # cannot modify the vehicle -- but they are genuinely used (VIN via mode 09,
    # live PIDs via mode 01, powertrain DTCs via mode 03) and must be on the
    # allow-list for validate_request() to let them through.
    0x01: "RequestCurrentPowertrainDiagnosticData (mode 01, read-only)",
    0x03: "RequestPowertrainDiagnosticInformation (mode 03, read-only)",
    0x09: "RequestVehicleInformation (mode 09, read-only)",
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

# Sub-functions permitted for services that carry one. A service being on the
# read-only list is NOT sufficient: several of them also have sub-functions
# that change ECU state, and allowing the service byte alone let those
# through. Each entry is the complete set of sub-functions this tool may send.
#
#   0x10 sub-functions (ISO 14229-1):
#     0x01 defaultSession              READ-ONLY, the only one sent here
#     0x02 programmingSession          BLOCKED - opens the ECU for flashing
#     0x03 extendedDiagnosticSession   BLOCKED - changes security level and
#                                              diagnostic behaviour
#     0x04 safetySystemDiagnosticSess  BLOCKED
#     0x40-0x4F are session variants with the same effects: blocked by
#     omission (fail-closed), not by an explicit range.
#
#   0x19 sub-functions: 0x01-0x09 all report stored information (pure reads).
#     0x0A stopResponseOnEvent is a CONTROL operation that changes when the
#     ECU transmits event data, so the list stops at 0x09 rather than being a
#     blanket "allow 0x19".
READ_SUBFUNCTIONS: dict[int, set[int]] = {
    0x10: {0x01},
    0x19: {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09},
}

# Sub-function-bearing services whose request must carry the sub-function byte
# plus at least one operand. `0x22 F1 90` is the shortest genuine DID read;
# `0x3E 00` the shortest tester-present.
MIN_REQUEST_BYTES: dict[int, int] = {
    0x10: 2,
    0x22: 3,
    0x19: 2,
    0x3E: 2,
}

# Nothing this tool sends is longer than a single read request; ISO-TP caps a
# CAN frame at 8 bytes and every allow-listed service here is shorter still.
# A cap costs nothing and stops an over-long payload reaching the wire.
MAX_REQUEST_BYTES = 15

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


def _validate_subfunction(hexed: str, service: int) -> None:
    """Refuse a state-changing sub-function of an allow-listed service."""
    allowed = READ_SUBFUNCTIONS.get(service)
    if allowed is None:
        return
    # A service with a sub-function list but no operands cannot be well formed.
    if len(hexed) < 4:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: service 0x{service:02X} requires a "
            "sub-function byte. This system is strictly READ-ONLY."
        )
    try:
        sub = int(hexed[2:4], 16)
    except ValueError as exc:  # pragma: no cover - clean_hex already filtered
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: sub-function is not a hex byte."
        ) from exc
    # Bit 7 of a sub-function is suppressPosRspMsgIndicationBit, which only
    # silences the positive response. Masking it is what lets a request built
    # from a DID whose high bit happens to be set still validate.
    if (sub & 0x7F) not in allowed:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: sub-function 0x{sub:02X} of service "
            f"0x{service:02X} is not a read. This tool may only send "
            f"{sorted(f'0x{s:02X}' for s in allowed)}. This system is strictly "
            "READ-ONLY."
        )


def validate_request(payload: str | bytes) -> int:
    """Validate a whole outgoing request. Returns the service id.

    This is the enforcement point for the read-only guarantee. `validate_service`
    on its own was not enough: it was only ever called from the build_* helpers,
    which production code does not use, so DiagnosticConnection._transmit could
    put any hex string on the wire. And a service byte on its own was not enough
    either -- see READ_SUBFUNCTIONS. Everything that reaches the vehicle's
    diagnostic port must pass through here first.

    Fails closed: anything that is not a well-formed request whose service AND
    sub-function are both allow-listed is rejected rather than sent.
    """
    hexed = clean_hex(payload) if isinstance(payload, str) else payload.hex()
    if len(hexed) < 2:
        raise ReadOnlyViolationError(
            f"Refusing to transmit an empty or malformed request {hexed!r}: "
            "this system is strictly READ-ONLY."
        )
    try:
        service = int(hexed[:2], 16)
    except ValueError as exc:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: first byte is not a service id."
        ) from exc
    validate_service(service)
    if len(hexed) % 2:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: odd number of hex digits."
        )
    if len(hexed) // 2 > MAX_REQUEST_BYTES:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: {len(hexed) // 2} bytes exceeds the "
            f"{MAX_REQUEST_BYTES}-byte maximum for a read request."
        )
    minimum = MIN_REQUEST_BYTES.get(service)
    if minimum is not None and len(hexed) // 2 < minimum:
        raise ReadOnlyViolationError(
            f"Refusing to transmit {hexed!r}: service 0x{service:02X} needs at "
            f"least {minimum} bytes, got {len(hexed) // 2}."
        )
    _validate_subfunction(hexed, service)
    return service


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


def _strip_header(h: str) -> tuple[str, int | None]:
    """Split cleaned ELM line hex into (frame hex, CAN header value).

    With ATH1 the ELM327 prefixes every frame with the CAN id it arrived
    on: 3 hex digits for 11-bit ids ('7ED...'), 8 digits for 29-bit ids
    ('18DAF105...'). The formats are distinguishable without knowing the
    protocol: frame hex is always an even number of characters, so an
    11-bit-tagged line (3 + 2n) is odd and a 29-bit-tagged line (8 + 2n)
    is even, and ISO 15765-4 29-bit diagnostic ids start with '18D'
    (priority 6; 18DA physical / 18DB functional).
    """
    if len(h) % 2 == 1 and len(h) > 3:
        return h[3:], int(h[:3], 16)                # 11-bit header + frame
    if len(h) % 2 == 0 and len(h) > 8 and h[:2] in ("18", "17"):
        # 29-bit header + frame. '18' = ISO 15765-4 priority 6 (18DA/18DB);
        # '17' = priority 5, used by VAG MEB module diagnostics (17FCxxxx
        # requests / 17FExxxx responses / 1700xxxx modules).
        return h[8:], int(h[:8], 16)
    return h, None                                  # header-off


def line_to_frame_header(line: str) -> tuple[bytes | None, int | None]:
    """Convert one ELM327 output line into (ISO-TP frame, CAN header).

    Handles header-on 11-bit ('7ED04621E3B0FA0'), header-on 29-bit
    ('18DAF10506410098180001') and header-off ('04621E3B0FA0') formats.
    Returns (None, None) if the line is not a frame (e.g. 'NO DATA',
    'SEARCHING...', 'OK', '?').

    The header matters: a functional request can be answered by several
    ECUs at once, and the header (18DAF1xx on 29-bit buses, the rx id on
    11-bit buses) is the only way to tell whose frame is whose.
    """
    h = clean_hex(line)
    if len(h) < 4:
        return None, None
    body, header = _strip_header(h)
    try:
        raw = bytes.fromhex(body)
    except ValueError:
        return None, None
    if _valid_frame(raw):
        return raw, header
    return None, None


def line_to_frame(line: str) -> bytes | None:
    """Convert one ELM327 output line into an ISO-TP frame payload.

    Handles header-on (11-bit and 29-bit) and header-off formats.
    Returns None if the line is not a frame (e.g. 'NO DATA',
    'SEARCHING...', 'OK').
    """
    return line_to_frame_header(line)[0]


def source_label(header: int | None) -> str:
    """Human-facing label for a responding ECU, given its response header.

    29-bit OBD/UDS responses arrive as 18DAF1xx where xx is the ECU's
    logical address; 11-bit responses carry the ECU's rx id directly.
    """
    if header is None:
        return "no-header"
    if header > 0x7FF:
        return f"0x{header & 0xFF:02X}"
    return f"0x{header:03X}"


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
    # Completeness check. ISO-TP permits a sender to abort after the FF and
    # emit fewer CFs than it announced (bus arbitration, ECU timeout), so a
    # short buffer here is NOT a shorter value -- it is a truncated response.
    # Returning it would let a missing tail read as real data: u32be() on a
    # short buffer silently yields 0, so e.g. lifetime energy discharged would
    # be recorded as exactly 0 kWh and flagged as an anomaly. Fail closed.
    if len(data) < total:
        return None
    return bytes(data[:total])


def payloads_by_source(lines: list[str]) -> dict[int | None, bytes | None]:
    """Reassemble cleaned ELM327 lines per responding ECU.

    A functional request can be answered by several ECUs at once; with
    ATH1 every frame carries its sender's CAN id, so frames must be
    grouped per sender *before* ISO-TP reassembly -- interleaving frames
    from two ECUs corrupts both messages. Keys are CAN header values
    (11-bit rx id, 29-bit id, or None for header-off lines), in order of
    first arrival; values are the reassembled payloads (None when a
    sender's frames did not form a complete message).
    """
    groups: dict[int | None, list[bytes]] = {}
    for ln in lines:
        frame, header = line_to_frame_header(ln)
        if frame is not None:
            groups.setdefault(header, []).append(frame)
    return {hdr: reassemble(fs) for hdr, fs in groups.items()}


def parse_elm_lines(lines: list[str]) -> bytes | None:
    """Parse cleaned ELM327 response lines into one UDS payload.

    Frames are reassembled per responding ECU (payloads_by_source). When
    several ECUs answered a functional request, the first positive
    (non-7F) payload is returned; if there is no positive, the first
    negative is returned so callers can surface its NRC.
    """
    payloads = [p for p in payloads_by_source(lines).values() if p]
    if not payloads:
        return None
    for p in payloads:
        if p[0] != 0x7F:
            return p
    return payloads[0]


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

