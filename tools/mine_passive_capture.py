#!/usr/bin/env python3
"""Extract UDS 0x62 responses from a passive Android btsnoop capture.

WHY THIS EXISTS
---------------
The 2026-10-15 Car Scanner btsnoop log is the only retained evidence for
several gateway, ABS and climate payloads. The raw log also contains unrelated
phone traffic (including personal messages), so it must not be committed or
echoed wholesale. This tool reads the local log, keeps only complete ELM
diagnostic lines, reassembles ISO-TP single/first/consecutive frames, and emits
a small sanitised JSON record set: CAN id, DID, payload bytes, and log offset.

It never opens a socket, a database or an adapter. The input is a local file.

Usage::

    python tools/mine_passive_capture.py --in C:\\path\\btsnoop_hci.log \\
        --did 2A26,2A28,2A2F,04A3,2AB2,2AB8,42DB,27C4,5867,1800,1801,1802,1803
    python tools/mine_passive_capture.py --in ... --json-out evidence.json

The output is deterministic for a given input. Timestamps are offsets from the
first btsnoop record, not wall-clock: the capture's timestamp base is opaque.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

HEX = set("0123456789abcdefABCDEF")
PRINTABLE = re.compile(rb"[\x20-\x7e]{2,}")
BTSNOOP_MAGIC = b"btsnoop\x00"
HCI_UART = 1002


@dataclass(frozen=True)
class HciRecord:
    index: int
    offset_s: float
    direction: str
    payload: bytes


@dataclass(frozen=True)
class Frame:
    can_id: int
    data: bytes
    kind: str
    expected: int | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class Response:
    can_id: int
    did: int
    data: bytes
    offset_s: float
    record_index: int

    def as_dict(self) -> dict:
        return {
            "can_id": f"0x{self.can_id:X}",
            "did": f"0x{self.did:04X}",
            "payload": self.data.hex().upper(),
            "length": len(self.data),
            "offset_s": round(self.offset_s, 6),
            "record": self.record_index,
        }


@dataclass
class _Pending:
    expected: int
    data: bytearray
    next_seq: int
    offset_s: float
    record_index: int


def read_btsnoop(path: str | Path) -> tuple[list[HciRecord], dict]:
    """Return ACL records and a small source manifest. Read-only."""
    raw = Path(path).read_bytes()
    if raw[:8] != BTSNOOP_MAGIC:
        raise ValueError(f"not a btsnoop file: {path}")
    version, datalink = struct.unpack(">II", raw[8:16])
    if datalink != HCI_UART:
        raise ValueError(
            f"unsupported btsnoop datalink {datalink}; expected HCI UART "
            f"({HCI_UART})"
        )

    records: list[HciRecord] = []
    off = 16
    base_ts: int | None = None
    while off + 24 <= len(raw):
        _orig, incl, flags, _drops, ts_hi, ts_lo = struct.unpack(
            ">IIIIII", raw[off:off + 24]
        )
        off += 24
        payload = raw[off:off + incl]
        off += incl
        if len(payload) != incl:
            break
        ts = (ts_hi << 32) | ts_lo
        if base_ts is None:
            base_ts = ts
        # HCI ACL packets begin with 0x02. The 4-byte HCI header is followed by
        # RFCOMM data. Everything else (commands/events) carries no ELM text.
        if payload and payload[0] == 0x02:
            records.append(
                HciRecord(
                    index=len(records),
                    offset_s=(ts - base_ts) / 1_000_000.0,
                    direction="R" if flags & 1 else "S",
                    payload=payload[4:],
                )
            )

    manifest = {
        "file": Path(path).name,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "version": version,
        "datalink": datalink,
        "acl_records": len(records),
    }
    return records, manifest


def lines_from_record(record: HciRecord) -> list[str]:
    """Printable RFCOMM lines, without phone book/XML text."""
    return [chunk.decode("latin-1") for chunk in PRINTABLE.findall(record.payload)]


def parse_frame_line(line: str) -> Frame | None:
    """Parse one ELM header-on line into an ISO-TP frame.

    ELM header-on responses are either ``<3-hex CAN id><even payload>`` or
    ``<8-hex CAN id><even payload>``. The total-length parity selects the form,
    exactly as the ELM327 emits them. Anything else (AT text, XML, partial
    lines split by the RFCOMM buffer) is ignored.
    """
    text = line.strip()
    if len(text) < 9 or any(ch not in HEX for ch in text):
        return None
    if len(text) % 2:          # 3-digit id + even payload
        width = 3
    else:                      # 8-digit id + even payload
        width = 8
    if len(text) <= width:
        return None
    try:
        can_id = int(text[:width], 16)
        data = bytes.fromhex(text[width:])
    except ValueError:
        return None
    if not data:
        return None

    pci = data[0] >> 4
    if pci == 0:
        length = data[0] & 0x0F
        if length == 0 or 1 + length > len(data):
            return None
        return Frame(can_id=can_id, data=data[1:1 + length], kind="SF",
                     expected=length)
    if pci == 1:
        if len(data) < 2:
            return None
        length = ((data[0] & 0x0F) << 8) | data[1]
        if length <= 7:
            return None
        return Frame(can_id=can_id, data=data[2:], kind="FF", expected=length)
    if pci == 2:
        if len(data) < 2:
            return None
        return Frame(can_id=can_id, data=data[1:], kind="CF",
                     sequence=data[0] & 0x0F)
    return None


def extract_responses(
    records: list[HciRecord],
    dids: set[int] | None = None,
) -> list[Response]:
    """Reassemble positive 0x62 responses, optionally filtered by DID."""
    pending: dict[int, _Pending] = {}
    out: list[Response] = []

    def emit(can_id: int, payload: bytes, offset_s: float,
             record_index: int) -> None:
        if len(payload) < 3 or payload[0] != 0x62:
            return
        did = (payload[1] << 8) | payload[2]
        if dids is None or did in dids:
            out.append(Response(
                can_id=can_id, did=did, data=payload[3:],
                offset_s=offset_s, record_index=record_index,
            ))

    for record in records:
        for line in lines_from_record(record):
            frame = parse_frame_line(line)
            if frame is None:
                continue

            if frame.kind == "SF":
                pending.pop(frame.can_id, None)
                emit(frame.can_id, frame.data, record.offset_s, record.index)
                continue

            if frame.kind == "FF":
                pending.pop(frame.can_id, None)
                assert frame.expected is not None
                if len(frame.data) >= frame.expected:
                    emit(frame.can_id, frame.data[:frame.expected],
                         record.offset_s, record.index)
                else:
                    pending[frame.can_id] = _Pending(
                        expected=frame.expected,
                        data=bytearray(frame.data),
                        next_seq=1,
                        offset_s=record.offset_s,
                        record_index=record.index,
                    )
                continue

            # Consecutive frame.
            current = pending.get(frame.can_id)
            if current is None:
                continue
            if frame.sequence != current.next_seq:
                pending.pop(frame.can_id, None)
                continue
            current.data.extend(frame.data)
            current.next_seq = (current.next_seq + 1) & 0x0F
            if len(current.data) >= current.expected:
                payload = bytes(current.data[:current.expected])
                pending.pop(frame.can_id, None)
                emit(frame.can_id, payload, current.offset_s,
                     current.record_index)

    return out


def summarise(responses: list[Response], manifest: dict) -> dict:
    by_did: dict[int, list[Response]] = {}
    for response in responses:
        by_did.setdefault(response.did, []).append(response)
    summary = {}
    for did, items in sorted(by_did.items()):
        payloads: dict[str, int] = {}
        for item in items:
            key = item.data.hex().upper()
            payloads[key] = payloads.get(key, 0) + 1
        summary[f"0x{did:04X}"] = {
            "count": len(items),
            "can_ids": sorted({f"0x{i.can_id:X}" for i in items}),
            "payloads": payloads,
        }
    return {
        "source": manifest,
        "response_count": len(responses),
        "dids": summary,
        "responses": [item.as_dict() for item in responses],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract sanitised UDS 0x62 responses from a passive btsnoop."
    )
    parser.add_argument("--in", dest="input", required=True,
                        help="path to btsnoop_hci.log (read-only)")
    parser.add_argument("--did", help="comma-separated hex DIDs to keep")
    parser.add_argument("--json-out", help="write the JSON report to this path")
    args = parser.parse_args(argv)

    dids = None
    if args.did:
        dids = set()
        for token in args.did.split(","):
            token = token.strip().lower().removeprefix("0x")
            if not token:
                continue
            try:
                dids.add(int(token, 16))
            except ValueError:
                raise SystemExit(f"--did: {token!r} is not hex") from None

    try:
        records, manifest = read_btsnoop(args.input)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    responses = extract_responses(records, dids)
    report = summarise(responses, manifest)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.json_out}")
    else:
        print(text)
    print(
        f"Extracted {len(responses)} positive response(s) from "
        f"{manifest['acl_records']} ACL records.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
