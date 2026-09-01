"""Standard OBD-II (SAE J1979) modes 01/03/09 — legislated diagnostics.

These work with ANY compliant adapter and are the safest possible reads.
On the ID.3, mode 01 exposes only a limited set of powertrain PIDs (many
generic EV parameters are NOT present); mode 09 02 provides the VIN;
mode 03 returns legislated emissions DTCs only (VW-specific DTCs need UDS).
"""
from __future__ import annotations

from .uds import ProtocolError, line_to_frame, reassemble

DTC_LETTERS = {0b00: "P", 0b01: "C", 0b10: "B", 0b11: "U"}


def decode_obd2_dtc(b1: int, b2: int) -> str:
    letter = DTC_LETTERS[(b1 >> 6) & 0b11]
    return f"{letter}{(b1 >> 4) & 0b11}{b1 & 0xF:X}{(b2 >> 4) & 0xF:X}{b2 & 0xF:X}"


def _payload_after(lines: list[str], marker: bytes) -> bytes | None:
    """Reassemble ELM lines, then return the bytes starting at `marker`."""
    frames = [f for f in (line_to_frame(ln) for ln in lines) if f]
    payload = reassemble(frames)
    if payload is None:
        return None
    idx = payload.find(marker)
    if idx < 0:
        return None
    return payload[idx:]


def parse_vin_response(lines: list[str]) -> str:
    """Parse mode 09 02 output.

    Handles (1) headers-on ISO-TP frames (ATH1) and (2) the header-off
    ELM format with '014' line-count preamble and '0:'/'1:' index prefixes.
    """
    # 1) ISO-TP frame reconstruction (headers-on)
    frames = [f for f in (line_to_frame(ln) for ln in lines) if f]
    payload = reassemble(frames) if frames else None
    if payload:
        vin = _vin_from_payload(payload)
        if vin:
            return vin
    # 2) header-off indexed format
    from .uds import clean_hex

    hexcat = ""
    for line in lines:
        s = line.strip()
        if ":" in s:
            s = s.split(":", 1)[1]
        h = clean_hex(s)
        if len(h) <= 3:  # '014' line-count preamble, 'OK', etc.
            continue
        hexcat += h
    if not hexcat or len(hexcat) % 2:
        raise ProtocolError(f"Malformed VIN response: {lines!r}")
    try:
        raw = bytes.fromhex(hexcat)
    except ValueError as exc:
        raise ProtocolError(f"Malformed VIN response: {lines!r}") from exc
    vin = _vin_from_payload(raw)
    if not vin:
        raise ProtocolError(f"No mode-09 VIN data in response: {lines!r}")
    return vin


def _vin_from_payload(payload: bytes) -> str | None:
    idx = payload.find(b"\x49\x02")
    if idx < 0:
        return None
    chunk = payload[idx + 3 :]  # skip 49 02 <record count>
    vin = "".join(chr(b) for b in chunk if 32 <= b < 127)
    # frame headers/PCI bytes are non-printable or control chars and are
    # filtered out; the VIN itself is 17 printable characters
    if len(vin) < 17:
        return None
    return vin[:17]


def parse_mode01(lines: list[str], pid: int) -> bytes:
    payload = _payload_after(lines, bytes([0x41, pid]))
    if payload is None:
        raise ProtocolError(f"No mode-01 response for PID 0x{pid:02X}: {lines!r}")
    return payload[2:]


def parse_mode03(lines: list[str]) -> list[str]:
    payload = _payload_after(lines, b"\x43")
    if payload is None:
        return []
    dtcs = []
    body = payload[1:]
    # response: 43 + [(b1,b2) x N]; 0000 bytes are padding/empty
    body = body[: (len(body) // 2) * 2]
    for i in range(0, len(body), 2):
        b1, b2 = body[i], body[i + 1]
        if (b1, b2) == (0, 0):
            continue
        dtcs.append(decode_obd2_dtc(b1, b2))
    return dtcs


def decode_pid(pid: int, data: bytes):
    """Decode common SAE J1979 PIDs. Returns (value, unit) or None.

    Only PIDs likely to exist on a BEV are implemented. PID 0x42
    'Control module voltage' is the closest thing to a 12V measurement
    available via standard OBD-II.
    """
    try:
        if pid == 0x04 and len(data) >= 1:
            return data[0] * 100.0 / 255.0, "%"
        if pid == 0x05 and len(data) >= 1:
            return data[0] - 40.0, "°C"
        if pid == 0x0C and len(data) >= 2:
            return ((data[0] << 8) | data[1]) / 4.0, "rpm"
        if pid == 0x0D and len(data) >= 1:
            return float(data[0]), "km/h"
        if pid == 0x42 and len(data) >= 2:
            return ((data[0] << 8) | data[1]) / 1000.0, "V"
    except (IndexError, ValueError):
        return None
    return None


def decode_vin_year(vin: str) -> int | None:
    """Model year from VIN position 10 (ISO 3779, 2010-2039 code table)."""
    table = {
        "A": 2010, "B": 2011, "C": 2012, "D": 2013, "E": 2014, "F": 2015,
        "G": 2016, "H": 2017, "J": 2018, "K": 2019, "L": 2020, "M": 2021,
        "N": 2022, "P": 2023, "R": 2024, "S": 2025, "T": 2026,
    }
    return table.get(vin[9:10].upper())


def decode_vag_dtc(b1: int, b2: int, b3: int) -> str:
    """Decode a 3-byte VAG/UDS DTC (e.g. b'U1123 00' -> 'U112300').

    Byte layout per ISO 14229 / SAE J2012-DA: 2 bits system letter,
    2 bits first digit, 4 bits second digit, then 8 bits third/fourth
    digits, then the failure-type byte. [documented]
    """
    letter = DTC_LETTERS[(b1 >> 6) & 0b11]
    d1 = (b1 >> 4) & 0b11
    return f"{letter}{d1}{b1 & 0xF:X}{b2 >> 4:X}{b2 & 0xF:X}{b3:02X}"


def parse_uds_dtc_response(payload: bytes) -> list[dict]:
    """Parse UDS 0x19 0x02 (reportDTCByStatusMask) positive response.

    Returns [{'code': ..., 'status_byte': int}, ...]. Layout after the
    59 02 <statusAvailabilityMask> header: repeated 3-byte DTC + 1-byte
    DTC status. [documented ISO 14229]
    """
    if len(payload) < 3 or payload[0] != 0x59 or payload[1] != 0x02:
        return []
    body = payload[3:]
    out = []
    for i in range(0, len(body) - 3, 4):
        b1, b2, b3, status = body[i], body[i + 1], body[i + 2], body[i + 3]
        if (b1, b2, b3) == (0, 0, 0):
            continue
        out.append({"code": decode_vag_dtc(b1, b2, b3), "status_byte": status})
    return out

