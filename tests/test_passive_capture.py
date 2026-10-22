"""Passive btsnoop miner and sanitised evidence archive.

The synthetic btsnoop is deliberately tiny: it proves the ISO-TP single-frame,
first-frame and consecutive-frame paths without depending on the phone capture
or on any personal data. The archive test then pins the facts that were
recovered from the real 2026-10-15 capture.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from tools.mine_passive_capture import (
    extract_responses,
    parse_frame_line,
    read_btsnoop,
)

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = (
    ROOT / "research" / "measurement_discovery" / "captures"
    / "passive_capture_evidence_20261015.json"
)
MANIFEST = ARCHIVE.with_suffix(".manifest.json")
STRICT = (
    ROOT / "research" / "measurement_discovery" / "captures"
    / "strict_expected_values_20261022.json"
)
STRICT_MANIFEST = STRICT.with_suffix(".manifest.json")


def _acl_record(text: str, ts: int, received: bool = True) -> bytes:
    """One btsnoop HCI-UART record carrying printable RFCOMM text."""
    data = text.encode("latin-1")
    # Packet type 0x02, then enough binary framing that the printable run starts
    # after payload[4:]. The exact HCI header is irrelevant to the miner.
    hci = b"\x02\x00\x00" + struct.pack(">H", len(data) + 1) + b"\x00" + data
    flags = 1 if received else 0
    return struct.pack(">IIIIII", len(hci), len(hci), flags, 0, ts >> 32,
                       ts & 0xFFFFFFFF) + hci


def _write_capture(path: Path, lines: list[str]) -> None:
    body = b"btsnoop\x00" + struct.pack(">II", 1, 1002)
    for i, line in enumerate(lines):
        body += _acl_record(line + "\r", 1_000_000 + i * 1_000)
    path.write_bytes(body)


def test_single_frame_extraction_and_did_filter(tmp_path):
    path = tmp_path / "tiny.log"
    _write_capture(path, [
        "77A07622AB204280400",
        "77A04622A2F00",
        "20261015T214001+0100",   # phone/XML-looking noise
    ])
    records, manifest = read_btsnoop(path)
    assert manifest["datalink"] == 1002

    all_responses = extract_responses(records)
    assert [(r.did, r.data.hex().upper()) for r in all_responses] == [
        (0x2AB2, "04280400"),
        (0x2A2F, "00"),
    ]

    only_energy = extract_responses(records, {0x2AB2})
    assert [r.did for r in only_energy] == [0x2AB2]


def test_first_and_consecutive_frames_reassemble(tmp_path):
    path = tmp_path / "multi.log"
    # 0x2A26: declared length 8, six bytes in the first frame, two in CF1.
    _write_capture(path, [
        "77A1008622A26010203",
        "77A210405",
    ])
    records, _ = read_btsnoop(path)
    responses = extract_responses(records, {0x2A26})
    assert len(responses) == 1
    assert responses[0].did == 0x2A26
    assert responses[0].data.hex().upper() == "0102030405"


def test_29bit_header_and_junk_rejection(tmp_path):
    path = tmp_path / "long.log"
    _write_capture(path, [
        "18DAF1050362F190",
        "not a frame",
        "77A037F2231",           # negative response, not a 0x62 payload
    ])
    records, _ = read_btsnoop(path)
    responses = extract_responses(records, {0xF190})
    assert len(responses) == 1
    assert responses[0].can_id == 0x18DAF105
    assert responses[0].data == b""

    assert parse_frame_line("not a frame") is None
    negative = parse_frame_line("77A037F2231")
    assert negative is not None and negative.kind == "SF"


def test_evidence_archive_is_hashed_and_pins_recovered_facts():
    raw = ARCHIVE.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["bytes"] == len(raw)
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["source_capture"]["sha256"] == (
        "eefb5c3ef2781a523692f62484255a70ec7f322a746cde750a4c75c6260496f9"
    )

    d = json.loads(raw)
    assert d["privacy"]["raw_capture_committed"] is False
    assert d["source"]["acl_records"] > 10_000
    assert d["derived"]["gateway_energy_family"]["alias_0x04A3_equals_0x2A26"]
    assert d["derived"]["gateway_energy_family"]["0x2A26"]["payload_hex"] == (
        "2D0000002608508C8062904048180040C000400000060000000000000000C057"
    )
    assert d["derived"]["gateway_energy_family"]["0x2A28"]["payload_hex"] == (
        "090000000000108000608000400800400000000000000000000000000000C051"
    )
    assert d["derived"]["gateway_energy_family"]["0x2A2F"]["payload_hex"] == "00"

    co2 = d["derived"]["co2_content_interior"]
    assert co2["passive_capture_payload_hex"] == "03FE"
    assert co2["db_rows"] == 40
    assert co2["db_payloads"] == {"03FE": 40}
    for cell in ("cell_t_18", "cell_t_19", "cell_t_20", "cell_t_21",
                 "cell_t_22", "cell_t_23"):
        assert co2["cell_temperature_sentinel"][cell]["payload_hex"] == "03FE"

    energy = d["derived"]["energy_content"]
    assert energy["0x2AB2"][0]["decoded_wh"] == 53200
    assert energy["0x2AB8"][0]["field_offset_bytes"] == 2
    assert energy["0x2AB8"][0]["decoded_wh"] == 35175
    assert energy["displayed_values_wh"] == {
        "hv_battery_energy_content": 35175,
        "maximum_energy_content": 53200,
    }

    odo = d["derived"]["odometer_counter"]
    assert odo["field_u16"] == 1114
    assert odo["decoded_km"] == 11140
    assert odo["offset_km_min"] == pytest.approx(99869)
    assert odo["offset_km_max"] == pytest.approx(99871)

    abs_data = d["derived"]["abs_wheel_speeds"]
    assert abs_data["observed_session_change"]["request_hex"] == "1003"
    assert abs_data["observed_session_change"]["response_hex"] == "5003001E01E5"
    for did in ("0x1800", "0x1801", "0x1802", "0x1803"):
        assert abs_data["parked_payloads"][did].startswith("0000")

    ws = d["derived"]["windshield_temperature"]
    assert ws["row_count"] >= 40
    assert ws["rows"][0]["byte0_c"] == pytest.approx(12.9)
    assert ws["rows"][0]["profile_u16_decode_c"] > 400_000


def test_strict_expected_value_archive_passes_and_keeps_expected_values():
    raw = STRICT.read_bytes()
    manifest = json.loads(STRICT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["bytes"] == len(raw)
    assert manifest["sha256"] == hashlib.sha256(raw).hexdigest()

    from tools.validate_measurement_capture import evaluate, load_captures

    captures = load_captures(STRICT)
    verdicts, summary = evaluate(captures, "meb", strict=True)
    assert summary == {"total": 5, "pass": 5, "fail": 0, "indeterminate": 0}
    assert all(verdict.expected is not None for verdict in verdicts)
    assert {verdict.key for verdict in verdicts} == {
        "hv_energy_max", "hv_energy_content", "soc_abs", "pack_voltage",
        "odometer_km",
    }
