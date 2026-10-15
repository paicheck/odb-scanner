"""Offline unit tests for the gateway ([19.Gate]) HV energy-content DIDs.

Covers DID 0x2AB8 (`hv_energy_content`) and DID 0x2AB2 (`hv_energy_max`):

  * request construction (service byte, DID bytes, ECU addressing)
  * response-offset handling (SBI = 2 for 0x2AB8, SBI = 0 for 0x2AB2)
  * two-byte big-endian decoding and the 25 Wh / 50 Wh scaling
  * truncated / malformed / negative / oversized responses
  * no-response and transport timeout
  * the read-only allow-list and rejection of unsupported services

NOTHING in this file talks to a vehicle: every response is either a
hand-built synthetic byte string exercising the decoder, or a loopback
socket that stays silent on purpose. No response sample here was captured
from an ID.3; the byte layouts are the ones the recovered Car Scanner MEB
profile implies (see docs/investigation/meb_gateway_energy.md).
"""
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Project modules imported after the sys.path entry above (the repo runs
# tests without installing the package).
import collector
from database.repository import Repository
from decoders import gateway
from decoders.registry import DIDRegistry, build_default_registry
from diagnostic import uds
from diagnostic.connection import DiagnosticConnection
from diagnostic.ecus import get_ecu
from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import CommunicationError, OBDInterface
from diagnostic.uds import (
    NegativeResponseError,
    ProtocolError,
    ReadOnlyViolationError,
)

GATE = get_ecu("gate")
GATE_TX = 0x792       # request id named in Car Scanner code
GATE_RX = 0x710       # response header declared by the [19.Gate] records
CS_GATE_ATCRA = 0x77A  # Car Scanner ATCRA argument, semantics unresolved

# Synthetic DID data records (the bytes after the 62 marker + DID echo that
# DiagnosticConnection.read_did() returns). Values are chosen to make the
# big-endian order and the scale factor observable.
REC_2AB8 = bytes([0xAA, 0xBB, 0x01, 0x02, 0x03])   # 0x0102 * 25 = 6450 Wh
REC_2AB2 = bytes([0x04, 0xD8, 0xEE])               # 0x04D8 * 50 = 62000 Wh


class FakeTransport(OBDInterface):
    """Records every command line and replays canned response lines.

    Offline stand-in for an ELM327: no socket, no vehicle.
    """

    name = "fake"

    def __init__(self, responses=None):
        self.commands: list[str] = []
        self.responses = responses or {}
        self._open = True

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def send_command(self, command: str) -> list[str]:
        self.commands.append(command)
        reply = self.responses.get(command)
        if isinstance(reply, Exception):
            raise reply
        return list(reply) if reply else ["NO DATA"]

    # convenience: the non-AT (i.e. actually transmitted) commands
    def uds_commands(self) -> list[str]:
        return [c for c in self.commands if not c.upper().startswith("AT")]

    def set_header(self, tx_id: int) -> None:
        self.commands.append(f"ATSH{tx_id & 0x7FF:03X}")

    def set_receive_address(self, rx_id: int | None) -> None:
        self.commands.append(
            "ATCRA" if rx_id is None else f"ATCRA{rx_id & 0x7FF:03X}"
        )


def frame(payload: bytes, header: int = GATE_RX) -> list[str]:
    """Headers-on ELM327 lines carrying one UDS payload.

    Uses the project's own ISO-TP encoder so single-frame and
    first-frame + consecutive-frame payloads are segmented correctly.
    """
    return [f"{header:03X}" + f.hex().upper() for f in uds.encode_isotp(payload)]


def conn_with(responses) -> tuple[FakeTransport, DiagnosticConnection]:
    t = FakeTransport(responses)
    return t, DiagnosticConnection(t)


def registry_with_gateway() -> tuple[DIDRegistry, dict]:
    reg = DIDRegistry()
    gateway.register_gateway(reg)
    return reg, {s.key: s for s in reg.all()}


# --- request construction -----------------------------------------------------
def test_did_numbers_match_recovered_definition():
    assert gateway.DID_HV_ENERGY_CONTENT == 0x2AB8
    assert gateway.DID_HV_ENERGY_MAX == 0x2AB2


def test_build_read_did_request_encodes_service_and_did():
    assert uds.build_read_did_request(0x2AB8) == bytes.fromhex("222AB8")
    assert uds.build_read_did_request(0x2AB2) == bytes.fromhex("222AB2")


def test_build_read_did_request_is_always_service_22():
    # the DID cannot smuggle a different service byte
    assert uds.build_read_did_request(0xFFFF)[0] == 0x22
    assert uds.build_read_did_request(0x0000) == bytes.fromhex("220000")


def test_specs_carry_the_right_did_and_unit():
    _, specs = registry_with_gateway()
    content, maximum = specs["hv_energy_content"], specs["hv_energy_max"]
    assert content.did == 0x2AB8
    assert maximum.did == 0x2AB2
    assert content.ecu_key == maximum.ecu_key == "gate"
    assert content.unit == maximum.unit == "Wh"
    assert maximum.slow, "a static maximum should not be polled every cycle"


def test_gateway_ecu_addressing():
    assert GATE.tx == GATE_TX, "request id must be the code-verified gateway"
    assert GATE.rx == GATE_RX, "filter must use the declared response header"


def test_car_scanner_atcra_argument_is_not_the_response_header():
    """Recorded so the +0x6A anomaly is not silently re-derived later.

    In the recovered MEB profile the 11-bit records use ATCRA = response
    header + 0x6A (Gate 0x710/0x77A, HVAC 0x746/0x7B0, charger
    0x744/0x7AE, ABS 0x713/0x77D, GPS 0x767/0x7D1), so ATCRA77A is not a
    response CAN id and is deliberately NOT copied into ECUS['gate'].rx.
    """
    assert CS_GATE_ATCRA == GATE_RX + 0x6A
    assert GATE.rx != CS_GATE_ATCRA
    assert GATE.rx != GATE_TX + 8, "no +8 pairing is claimed for the gateway"


def test_gateway_read_addresses_the_gateway_ecu():
    """The gateway DID read emits exactly one allow-listed 0x22 -- nothing else.

    Addressing contract CHANGED when this branch merged the newer transport
    (see connection._transmit). The old implementation called
    set_header(ecu.tx)/set_receive_address(ecu.rx) before every read, so this
    test used to assert that 'ATSH792' and 'ATCRA710' were emitted. The merged
    transport is module-addressing driven: it only touches the adapter's
    addressing when the transport negotiated MEB addressing AND the ECUSpec
    carries a 29-bit module address (tx29/rx29). ECUS['gate'] carries none --
    its 11-bit 0x792/0x710 pair is still the unproven, inferred one -- so the
    request goes out functionally and no ATSH/ATCRA is sent at all.

    That is a stricter read-only guarantee than before (one command on the
    wire, not three), but it leaves the response UNFILTERED, so the answering
    CAN id is no longer constrained by ATCRA. See
    docs/investigation/meb_gateway_energy.md 3.1 -- the header is still not
    verified against ECUS['gate'].rx on the read path.
    """
    t, c = conn_with({"222AB8": frame(bytes.fromhex("622AB8") + REC_2AB8)})
    c.read_did(GATE, 0x2AB8)
    assert t.uds_commands() == ["222AB8"], "only the 0x22 read may be sent"
    # Nothing that changes addressing, framing or the session may be emitted.
    assert t.commands == ["222AB8"], (
        f"unexpected extra traffic on the wire: {t.commands}")
    # The request must still be on the read-only allow-list.
    uds.validate_request("222AB8")


def test_gateway_ecu_carries_no_29bit_module_address():
    """`gate` has no tx29/rx29, so it can never be module-addressed.

    This is the concrete reason the read above is unfiltered. If a 29-bit
    gateway address is ever established, adding tx29/rx29 here is what would
    make the transport pin the receive filter for this module -- and it would
    change the assertion above, so the two must be changed together.
    """
    assert GATE.tx29 is None and GATE.rx29 is None


def test_spec_decode_roundtrip_through_read_did():
    """Full offline path: spec -> ECU -> request -> response -> value."""
    reg, specs = registry_with_gateway()
    content = specs["hv_energy_content"]
    _, c = conn_with({"222AB8": frame(bytes.fromhex("622AB8") + REC_2AB8)})
    raw = c.read_did(GATE, content.did)
    assert raw == REC_2AB8
    assert content.decode_value(raw) == 6450.0
    assert reg.get("hv_energy_content").key == content.key


# --- two-byte big-endian decoding --------------------------------------------
def test_content_is_big_endian():
    _, specs = registry_with_gateway()
    decode = specs["hv_energy_content"].decode
    assert decode(bytes([0x00, 0x00, 0x01, 0x02])) == 258.0 * 25.0
    # swapping the two bytes must change the value (little-endian would not)
    assert decode(bytes([0x00, 0x00, 0x02, 0x01])) == 513.0 * 25.0


def test_max_is_big_endian():
    _, specs = registry_with_gateway()
    decode = specs["hv_energy_max"].decode
    assert decode(bytes([0x01, 0x02, 0xFF])) == 258.0 * 50.0
    assert decode(bytes([0x02, 0x01, 0xFF])) == 513.0 * 50.0


# --- Wh scaling ---------------------------------------------------------------
@pytest.mark.parametrize("raw,expected_wh", [
    (bytes([0x00, 0x00, 0x00, 0x00]), 0.0),
    (bytes([0x00, 0x00, 0x00, 0x01]), 25.0),      # 1 * 25 Wh
    (bytes([0xFF, 0xFF, 0x00, 0x01]), 25.0),      # leading bytes ignored
    (bytes([0x00, 0x00, 0x01, 0x00]), 256.0 * 25.0),
    (bytes([0x00, 0x00, 0xFF, 0xFF]), 65535 * 25.0),
])
def test_content_scale_is_25_wh_per_lsb(raw, expected_wh):
    _, specs = registry_with_gateway()
    assert specs["hv_energy_content"].decode(raw) == expected_wh


@pytest.mark.parametrize("raw,expected_wh", [
    (bytes([0x00, 0x00, 0x00]), 0.0),
    (bytes([0x00, 0x01]), 50.0),                  # 1 * 50 Wh
    (bytes([0x04, 0xD8]), 1240 * 50.0),           # 62 kWh gross
    (bytes([0xFF, 0xFF]), 65535 * 50.0),
])
def test_max_scale_is_50_wh_per_lsb(raw, expected_wh):
    _, specs = registry_with_gateway()
    assert specs["hv_energy_max"].decode(raw) == expected_wh


def test_scales_differ_between_the_two_dids():
    """The same raw 0x04D8 must not decode to the same Wh in both DIDs."""
    _, specs = registry_with_gateway()
    pair = bytes([0x04, 0xD8])
    # read at offset 0 (0x2AB2 x 50) vs offset 2 (0x2AB8 x 25)
    assert specs["hv_energy_max"].decode(pair) == 62000.0
    assert specs["hv_energy_content"].decode(pair + pair) == 31000.0
    # a 58 kWh pack needs raw 1160 at 50 Wh/LSB (plausibility, not evidence)
    assert specs["hv_energy_max"].decode(bytes([0x04, 0x88])) == 58000.0


def test_values_are_never_signed():
    """Both DIDs are unsigned: 0xFFFF must not become -1."""
    _, specs = registry_with_gateway()
    assert specs["hv_energy_max"].decode(bytes([0xFF, 0xFF])) > 0
    assert specs["hv_energy_content"].decode(
        bytes([0xFF, 0xFF, 0xFF, 0xFF])) > 0


# --- payload offset handling --------------------------------------------------
def test_content_offset_skips_leading_bytes():
    _, specs = registry_with_gateway()
    decode = specs["hv_energy_content"].decode
    assert decode(bytes([0x00, 0x00, 0x00, 0x10])) == decode(
        bytes([0xAA, 0xBB, 0x00, 0x10]))


def test_max_offset_is_zero():
    _, specs = registry_with_gateway()
    decode = specs["hv_energy_max"].decode
    assert decode(bytes([0x00, 0x10, 0xFF, 0xFF])) == 16 * 50.0


def test_content_offset_respects_trailing_data():
    _, specs = registry_with_gateway()
    assert specs["hv_energy_content"].decode(
        bytes([0x00, 0x00, 0x00, 0x10, 0xAA, 0xBB])) == 400.0


@pytest.mark.parametrize("raw", [
    b"",
    bytes([0x00]),
    bytes([0x00, 0x01]),
    bytes([0x00, 0x00, 0x01]),   # one byte short of offset 2 + len 2
])
def test_truncated_content_record_yields_no_value(raw):
    _, specs = registry_with_gateway()
    assert specs["hv_energy_content"].decode_value(raw) is None
    with pytest.raises(ValueError):
        specs["hv_energy_content"].decode(raw)


@pytest.mark.parametrize("raw", [b"", bytes([0x00])])
def test_truncated_max_record_yields_no_value(raw):
    """0x2AB2 reads at offset 0, so exactly two bytes is sufficient."""
    _, specs = registry_with_gateway()
    assert specs["hv_energy_max"].decode_value(raw) is None
    with pytest.raises(ValueError):
        specs["hv_energy_max"].decode(raw)


def test_max_needs_exactly_two_bytes():
    _, specs = registry_with_gateway()
    assert specs["hv_energy_max"].decode(bytes([0x00, 0x01])) == 50.0
    assert specs["hv_energy_max"].decode(bytes([0x00, 0x01, 0xFF])) == 50.0


def test_offset_error_message_names_the_did_and_the_need():
    _, specs = registry_with_gateway()
    with pytest.raises(ValueError) as exc:
        specs["hv_energy_content"].decode(bytes([0x00, 0x00, 0x01]))
    msg = str(exc.value)
    assert "0x2AB8" in msg and "4" in msg


# --- malformed / negative responses ------------------------------------------
def test_negative_response_is_rejected():
    _, c = conn_with({"222AB8": frame(bytes.fromhex("7F2231"))})
    with pytest.raises(NegativeResponseError) as exc:
        c.read_did(GATE, 0x2AB8)
    assert "0x31" in str(exc.value)


def test_negative_response_without_nrc_is_still_rejected():
    _, c = conn_with({"222AB8": frame(bytes.fromhex("7F22"))})
    with pytest.raises(NegativeResponseError):
        c.read_did(GATE, 0x2AB8)


def test_try_read_did_absorbs_negative_response():
    _, c = conn_with({"222AB8": frame(bytes.fromhex("7F2231"))})
    assert c.try_read_did(GATE, 0x2AB8) is None


def test_unexpected_service_marker_is_rejected():
    _, c = conn_with({"222AB8": frame(bytes.fromhex("592AB8") + REC_2AB8)})
    with pytest.raises(ProtocolError):
        c.read_did(GATE, 0x2AB8)


def test_truncated_response_missing_did_echo():
    _, c = conn_with({"222AB8": frame(bytes.fromhex("62"))})
    with pytest.raises(ProtocolError):
        c.read_did(GATE, 0x2AB8)


def test_did_echo_mismatch_is_rejected():
    """A response for another DID must not be decoded as 0x2AB8."""
    _, c = conn_with({"222AB8": frame(bytes.fromhex("622AB2") + REC_2AB2)})
    with pytest.raises(ProtocolError):
        c.read_did(GATE, 0x2AB8)


def test_multi_frame_response_is_reassembled_before_decoding():
    """ISO-TP: first frame + consecutive frames must reassemble."""
    payload = bytes.fromhex("622AB8") + bytes([0x00, 0x00, 0x04, 0xD8, 0x00])
    lines = [f"{GATE_RX:03X}" + f.hex().upper()
             for f in uds.encode_isotp(payload)]
    assert len(lines) > 1, "this response must exercise multi-frame handling"
    _, specs = registry_with_gateway()
    _, c = conn_with({"222AB8": lines})
    raw = c.read_did(GATE, 0x2AB8)
    assert raw == bytes([0x00, 0x00, 0x04, 0xD8, 0x00])
    assert specs["hv_energy_content"].decode(raw) == 31000.0


def test_unsolicited_traffic_is_not_decoded():
    _, c = conn_with({"222AB8": ["7FF0", "SEARCHING..."]})
    with pytest.raises(CommunicationError):
        c.read_did(GATE, 0x2AB8)


# --- no response / timeout ----------------------------------------------------
@pytest.mark.parametrize("lines", [
    ["NO DATA"],
    ["SEARCHING...", "UNABLE TO CONNECT"],
    ["STOPPED"],
    [],
])
def test_no_response_lines_raise_communication_error(lines):
    _, c = conn_with({"222AB8": lines})
    with pytest.raises(CommunicationError):
        c.read_did(GATE, 0x2AB8)


def test_try_read_did_returns_none_when_ecu_is_silent():
    _, c = conn_with({})
    assert c.try_read_did(GATE, 0x2AB8) is None


def test_transport_error_propagates_and_is_absorbed():
    boom = CommunicationError("Timeout waiting for prompt after: b''")
    _, c = conn_with({"222AB8": boom})
    with pytest.raises(CommunicationError):
        c.read_did(GATE, 0x2AB8)
    assert c.try_read_did(GATE, 0x2AB8) is None


def test_collector_records_unsuccessful_gateway_read():
    """A silent gateway must be stored as failed, with raw preserved."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(str(Path(tmp) / "gw.db"))
        c = collector.Collector(_StubConfig(), repo)
        c.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")
        spec = c.registry.get("hv_energy_content")
        _, conn = conn_with({})
        c.conn = conn
        c.poll_did(spec, "2026-10-15T10:00:00+00:00")
        row = repo.conn.execute(
            "SELECT success, error, raw_response, ecu, service, pid, unit "
            "FROM measurements WHERE key='hv_energy_content'"
        ).fetchone()
        assert row["success"] == 0
        assert row["error"]
        assert row["ecu"] == "gate"
        assert row["service"] == "UDS-0x22"
        assert row["pid"] == "0x2AB8"
        assert row["unit"] == "Wh"
        repo.close()


class _StubConfig:
    def get(self, key, default=None):
        return default


def test_silent_adapter_socket_times_out_without_a_vehicle():
    """Real transport read path, against a loopback peer that never answers."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()
    stop = threading.Event()

    def peer():
        conn, _ = srv.accept()
        stop.wait(2.0)
        conn.close()

    thread = threading.Thread(target=peer, daemon=True)
    thread.start()
    transport = Elm327Transport(host=host, tcp_port=port, timeout=0.4)
    try:
        transport.open()
        with pytest.raises(CommunicationError):
            transport.send_command("222AB8")
    finally:
        transport.close()
        stop.set()
        srv.close()


# --- read-only guard ----------------------------------------------------------
def test_gateway_dids_use_only_allowed_service():
    assert 0x22 in uds.SERVICES


@pytest.mark.parametrize("sid", [0x14, 0x27, 0x2E, 0x2F, 0x31, 0x34, 0x35,
                                 0x36, 0x37, 0x28, 0x85, 0x2A, 0x2C, 0x2D])
def test_write_and_unsafe_services_stay_blocked(sid):
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_service(sid)


@pytest.mark.parametrize("sid", [0x11, 0x1A, 0x21, 0x29, 0x3D, 0x5E, 0x00, 0xFF])
def test_unsupported_services_are_rejected(sid):
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_service(sid)


def test_adding_gateway_dids_did_not_widen_the_uds_api():
    public = [n for n in dir(uds) if n.startswith("build_")]
    assert public == ["build_read_did_request", "build_session_request",
                      "build_tester_present"]


def test_gateway_module_adds_no_request_builder():
    """The gateway decoders must not be able to transmit anything."""
    assert not [n for n in dir(gateway) if n.startswith("build_")]
    assert not [n for n in dir(gateway) if n.startswith("send")]


def test_gateway_reads_only_emit_uds_22_requests():
    _, specs = registry_with_gateway()
    t, c = conn_with({})
    for spec in specs.values():
        c.try_read_did(GATE, spec.did)
    assert t.uds_commands() == ["222AB8", "222AB2"]
    assert all(cmd.startswith("22") for cmd in t.uds_commands())


# --- registry integration -----------------------------------------------------
def test_default_registry_contains_both_gateway_specs():
    reg = build_default_registry()
    assert reg.get("hv_energy_content").did == 0x2AB8
    assert reg.get("hv_energy_max").did == 0x2AB2
    assert {s.key for s in reg.fast()} >= {"hv_energy_content"}
    assert {s.key for s in reg.slow()} >= {"hv_energy_max"}


def test_default_registry_gateway_specs_have_provenance():
    reg = build_default_registry()
    for key in ("hv_energy_content", "hv_energy_max"):
        spec = reg.get(key)
        assert spec.provenance.value == "reported"
        assert spec.doc_status.value == "experimentally determined"
        assert "Car Scanner" in spec.notes


def test_no_duplicate_spec_keys():
    build_default_registry()  # register() raises on duplicates
