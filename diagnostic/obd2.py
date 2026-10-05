"""Standard OBD-II (SAE J1979) modes 01/03/09 — legislated diagnostics.

These work with ANY compliant adapter and are the safest possible reads.
On the ID.3, mode 01 exposes only a limited set of powertrain PIDs (many
generic EV parameters are NOT present); mode 09 02 provides the VIN;
mode 03 returns legislated emissions DTCs only (VW-specific DTCs need UDS).
"""
from __future__ import annotations

from .uds import ProtocolError, payloads_by_source

DTC_LETTERS = {0b00: "P", 0b01: "C", 0b10: "B", 0b11: "U"}


def decode_obd2_dtc(b1: int, b2: int) -> str:
    letter = DTC_LETTERS[(b1 >> 6) & 0b11]
    return f"{letter}{(b1 >> 4) & 0b11}{b1 & 0xF:X}{(b2 >> 4) & 0xF:X}{b2 & 0xF:X}"


def _payload_after(lines: list[str], marker: bytes) -> bytes | None:
    """Reassemble ELM lines per responding ECU, then return the bytes
    starting at `marker` from the first payload that contains it.

    Several ECUs may answer one functional request (and some of them
    negatively); picking per sender is what keeps their frames apart.
    """
    for payload in payloads_by_source(lines).values():
        if payload is None:
            continue
        idx = payload.find(marker)
        if idx >= 0:
            return payload[idx:]
    return None


def parse_vin_response(lines: list[str]) -> str:
    """Parse mode 09 02 output.

    Handles (1) ISO-TP frames (headers-on 11-bit or 29-bit, or headers
    off), reassembled per responding ECU so that several ECUs answering
    one functional request cannot corrupt each other's frames, and (2)
    the header-off ELM format with '014' line-count preamble and
    '0:'/'1:' index prefixes.
    """
    # 1) per-responder ISO-TP reassembly
    payloads = payloads_by_source(lines)
    for payload in payloads.values():
        if not payload:
            continue
        vin = _vin_from_payload(payload)
        if vin:
            return vin
    # 2) header-off indexed format. Also the fallback when "frames" parsed
    # but held no VIN: the indexed format's '0:' prefixes merge into the
    # hex and can fake a short single frame, so path 1 alone must not be
    # trusted to rule the format out. (The corrupt-VIN regression
    # 'WVW!ZZZE1ZM"P0870' came from 29-bit header lines failing path 1
    # entirely and being concatenated raw down here -- those now parse
    # per responder above, so a real VIN never reaches this path.)
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


def encode_vag_dtc(code: str) -> bytes:
    """Inverse of decode_vag_dtc: 'U112300' -> b'\\xD1\\x23\\x00'.

    Raises ValueError on anything malformed. Fail closed: these bytes go
    on the wire inside a 0x19 0x04 request, so a guessed encoding is
    worse than no request at all.
    """
    inverse = {letter: bits for bits, letter in DTC_LETTERS.items()}
    c = (code or "").strip().upper()
    if len(c) != 7 or c[0] not in inverse:
        raise ValueError(f"Malformed DTC code: {code!r}")
    try:
        d1 = int(c[1])
        if not 0 <= d1 <= 3:
            raise ValueError(f"DTC digit out of range: {code!r}")
        b1 = (inverse[c[0]] << 6) | (d1 << 4) | int(c[2], 16)
        b2 = (int(c[3], 16) << 4) | int(c[4], 16)
        b3 = int(c[5:7], 16)
    except ValueError as exc:
        raise ValueError(f"Malformed DTC code: {code!r}") from exc
    return bytes([b1, b2, b3])


def parse_uds_dtc_snapshot_response(payload: bytes) -> list[dict]:
    """Parse UDS 0x19 0x04 (reportDTCSnapshotRecordNumber) positive response.

    Layout [documented ISO 14229]: 59 04 <statusAvailabilityMask>, then
    DTC(3) <statusOfDTC>(1), then the snapshot records for that DTC:
    <recordNumber>(1) <numberOfIdentifiers>(1) and identifier blocks of
    DID(2) + data. A recordNumber of 0xFF means the ECU stores no
    snapshot for this DTC.

    Snapshot DATA content and per-identifier lengths are
    manufacturer-defined (VAG does not frame them), so only
    single-identifier records can be split reliably; everything else is
    preserved raw rather than guessed. Requests are per-DTC, so only the
    first DTC block is parsed (mask-based multi-DTC reads are not used).

    Returns [{'code': str, 'status_byte': int, 'records':
              [{'record': int, 'identifiers': [int, ...],
                'data': {did_hex: raw_hex} | None, 'raw': str}]}]
    """
    if len(payload) < 7 or payload[0] != 0x59 or payload[1] != 0x04:
        return []
    entry = {"code": decode_vag_dtc(payload[3], payload[4], payload[5]),
             "status_byte": payload[6], "records": []}
    body = payload
    i = 7
    while i < len(body):
        record = body[i]
        i += 1
        if record == 0xFF:          # ISO: no snapshot records stored
            break
        if i >= len(body):
            break
        num_ids = body[i]
        i += 1
        identifiers: list[int] = []
        for _ in range(num_ids):
            if i + 2 > len(body):
                break
            identifiers.append((body[i] << 8) | body[i + 1])
            i += 2
        rest = body[i:].hex().upper()
        data = None
        if num_ids == 1 and identifiers and rest:
            # Single identifier: the remaining bytes are unambiguously its
            # data (nothing else can follow within this DTC's record).
            data = {f"{identifiers[0]:04X}": rest}
            i = len(body)
        entry["records"].append({"record": record,
                                 "identifiers": identifiers,
                                 "data": data, "raw": rest})
        if num_ids != 1:
            # Multiple identifiers with unframed data: the next record
            # boundary cannot be located without guessing. Stop here; the
            # raw bytes are preserved (and the full response lives in the
            # tx_log anyway).
            break
    return [entry]

