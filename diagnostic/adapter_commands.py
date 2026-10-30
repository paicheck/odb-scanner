"""Which adapter (AT/ST) lines may be sent, and how raw frames are checked.

uds.validate_request decides which *vehicle* requests may go out. Adapter
commands need their own rule, because several change what reaches the car
without the request looking any different: ATCAF0 turns off ISO-TP
formatting, so a validated "0104" arrives as mode 04, clear DTCs; ATWM and
ATRTR transmit on their own; ATMA listens and acknowledges traffic; ATPP,
ATSD and AT@3 write the adapter's EEPROM; and any OBDLink ST command -- STPX
above all -- can send arbitrary data. Both lists below are allow-lists and
fail closed: a command not on them is refused, not waved through for
starting with "AT".

INTERACTIVE_ADAPTER_COMMANDS is what tools/elm_console.py lets a person type:
nothing that changes framing or flow control.

TRANSPORT_ADAPTER_COMMANDS is what any code may send through Elm327Transport:
the interactive set plus framing (ATCAF0/1), ATBI, the flow-control set-up
the MEB read path uses (ATFCSH, ATFCSD with a flow-control frame, ATFCSM) and
the ATCRA0 that _negotiate_meb sends. Elm327Transport.send_command enforces it
on every line, and checks a vehicle line as what the ECU will receive: a
request under auto-formatting, the payload inside a single ISO-TP frame
under ATCAF0 (validate_raw_frame). FRAMING says which commands decide that.
"""
from __future__ import annotations

import re

from . import uds

_INTERACTIVE_BODY = (
    r"Z|WS|D|I|@1|DP|DPN|RV|CS|PC|AR|CAF1|R1|D[01]|"
    r"[ELSH][01]|AT[0-2]|ST[0-9A-F]{2}|"
    r"[ST]PA?[0-9A-C]|"
    r"SH(?:[0-9A-F]{3}|[0-9A-F]{6}|[0-9A-F]{8})|"
    r"CRA(?:[0-9A-FX]{3}|[0-9A-FX]{8})?|"
    r"CP[0-9A-F]{2}|"
    r"C[FM](?:[0-9A-F]{3}|[0-9A-F]{8})"
)

INTERACTIVE_ADAPTER_COMMANDS = re.compile(rf"^AT(?:{_INTERACTIVE_BODY})$")

TRANSPORT_ADAPTER_COMMANDS = re.compile(
    rf"^AT(?:{_INTERACTIVE_BODY}|"
    r"CAF0|BI|CRA0|FCSM[0-2]|"
    # Receive filter and mask at any width from 3 to 8 digits: they only
    # decide what the adapter listens to. ABRP sends the 5-digit
    # "ATCF 17FE7", which tools/diagnose_meb_path.py reproduces (EXP-14).
    r"C[FM][0-9A-F]{3,8}|"
    r"FCSH(?:[0-9A-F]{3}|[0-9A-F]{6}|[0-9A-F]{8})|"
    # Flow-control data must BE a flow-control frame (first nibble 3): the
    # adapter sends these bytes on the next multi-frame read, so anything else
    # is an arbitrary message to the ECU.
    r"FCSD3[0-9A-F](?:[0-9A-F]{2}){0,7}"
    r")$")

# Commands that set how the next vehicle line is framed: True means raw frames
# (ATCAF0), False auto-formatted requests. ATZ, ATD and ATWS restore defaults.
FRAMING = {"ATCAF0": True, "ATCAF1": False, "ATZ": False, "ATD": False,
           "ATWS": False}


def is_adapter_command(line: str) -> bool:
    """AT (ELM327) and ST (OBDLink) lines address the adapter, not the car."""
    return line.strip()[:2].upper() in ("AT", "ST")


def normalise(line: str) -> str:
    """Upper case, internal spaces removed: 'at sh 7e0' -> 'ATSH7E0'."""
    return "".join(line.split()).upper()


def validate_adapter_command(line: str,
                             allowed: re.Pattern = TRANSPORT_ADAPTER_COMMANDS
                             ) -> str:
    """The normalised command, or ReadOnlyViolationError if not allowed."""
    norm = normalise(line)
    if not allowed.match(norm):
        raise uds.ReadOnlyViolationError(
            f"Refusing adapter command {line.strip()!r}: not on the read-only "
            "allow-list. It can change how requests are framed, send bytes on "
            "its own, or write the adapter's memory. This system is strictly "
            "READ-ONLY.")
    return norm


def validate_raw_frame(line: str) -> str:
    """Check a vehicle line sent under ATCAF0, where it is the CAN frame itself.

    It must be one ISO-TP single frame (first nibble 0, length 1-7) whose
    payload passes validate_request: that payload, not the line, is what the
    ECU acts on. Returns the canonical hex.
    """
    wire = uds.canonical_request(line)
    try:
        frame = bytes.fromhex(wire)
    except ValueError:
        frame = b""
    length = frame[0] & 0x0F if frame else 0
    if (not frame or frame[0] >> 4 != 0 or not 1 <= length <= 7
            or len(frame) < 1 + length):
        raise uds.ReadOnlyViolationError(
            f"Refusing {line!r} under ATCAF0: not a single ISO-TP frame, so the "
            "bytes the ECU receives cannot be validated.")
    uds.validate_request(frame[1:1 + length])
    return wire
