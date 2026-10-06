"""Unit tests: protocol decoding, read-only guard, statistics, database."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostic import obd2, uds  # noqa: E402
from diagnostic.uds import ReadOnlyViolationError  # noqa: E402


# --- read-only guard ---------------------------------------------------------
@pytest.mark.parametrize("sid", [0x14, 0x27, 0x2E, 0x31, 0x2F, 0x34, 0x35,
                                 0x36, 0x37, 0x28, 0x85])
def test_blocked_services(sid):
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_service(sid)


def test_allowed_services():
    for sid in (0x10, 0x22, 0x19, 0x3E, 0x01, 0x03, 0x09):
        uds.validate_service(sid)  # must not raise


# --- read-only enforcement on the transmit path -------------------------------
class _RecordingTransport:
    """Minimal transport that records everything handed to the wire."""

    def __init__(self, reply=None):
        self.written: list[str] = []
        self._reply = reply or ["NO DATA"]

    def open(self): ...
    def close(self): ...
    def initialize(self): return "recording-transport"
    def description(self): return "recording"
    def set_header(self, tx):
        self.written.append("ATSH" if tx is None else f"ATSH{tx:03X}")
    def set_receive_address(self, rx): self.written.append(f"ATCRA{rx or ''}")

    def send_command(self, command):
        self.written.append(command)
        return list(self._reply)


def test_transmit_refuses_every_write_service():
    """Regression: the read-only guarantee was only enforced by build_* helpers
    that production code never calls, so _transmit put anything on the wire."""
    from diagnostic.connection import DiagnosticConnection
    for payload in ("2E0102FFFF", "2701", "14FFFFFF", "31010001", "3800",
                    "3501", "2F0102", "2803", "2A0101", "85", "87"):
        t = _RecordingTransport()
        conn = DiagnosticConnection(t)
        with pytest.raises(ReadOnlyViolationError):
            conn._transmit(payload, None, "test")
        assert payload not in t.written, f"{payload} reached the transport"


def test_transmit_allows_read_only_services():
    from diagnostic.connection import DiagnosticConnection
    for payload in ("221E3B", "1001", "3E00", "0902", "0105", "03"):
        t = _RecordingTransport()
        DiagnosticConnection(t)._transmit(payload, None, "test")
        assert payload in t.written


def test_validate_request_fails_closed():
    for bad in ("", "ZZ", "nothex", b""):
        with pytest.raises(ReadOnlyViolationError):
            uds.validate_request(bad)


# --- read-only enforcement on SUB-functions ------------------------------------
# Regression: validate_request checked only the service byte, so any
# sub-function of an allow-listed service went out. 0x10 0x02 opens the ECU
# for programming and 0x10 0x03 changes its diagnostic/security level; both
# defeat the read-only guarantee without ever using a "write" service id.
@pytest.mark.parametrize("payload", [
    "1002",          # programmingSession
    "1003",          # extendedDiagnosticSession
    "1004",          # safetySystemDiagnosticSession
    "1040",          # session variant in the 0x40 sub-function range
    "1002AB",        # programmingSession with operands
])
def test_blocked_session_subfunctions(payload):
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_request(payload)


@pytest.mark.parametrize("payload", ["190A", "1900", "190B", "19FF"])
def test_blocked_dtc_subfunctions(payload):
    """0x19 0x0A stopResponseOnEvent is a control operation, not a read."""
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_request(payload)


@pytest.mark.parametrize("payload", ["1001", "1001A7", "221E3B", "22F190",
                                     "190208", "190404ABCD12FF", "3E00"])
def test_allowed_subfunctions(payload):
    uds.validate_request(payload)  # must not raise


def test_multi_did_read_is_allowed():
    """0x22 may carry several consecutive DIDs; all of it is still a read."""
    uds.validate_request("221E3BFFFC")


@pytest.mark.parametrize("payload", ["10", "19", "22", "3E"])
def test_truncated_reads_are_refused(payload):
    """A service byte with no operands is not a well-formed read request."""
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_request(payload)


def test_overlong_request_is_refused():
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_request("22" + "11" * 32)


def test_odd_length_request_is_refused():
    with pytest.raises(ReadOnlyViolationError):
        uds.validate_request("221E3")


def test_transmit_refuses_state_changing_subfunctions():
    """The choke point must reject them too, not just the module function."""
    from diagnostic.connection import DiagnosticConnection
    for payload in ("1002", "1003", "190A"):
        t = _RecordingTransport()
        conn = DiagnosticConnection(t)
        with pytest.raises(ReadOnlyViolationError):
            conn._transmit(payload, None, "test")
        assert payload not in t.written, f"{payload} reached the transport"


def test_no_header_or_filter_commands_are_ever_sent():
    """Without negotiated MEB addressing, requests must go out functionally.

    Regression: the field clone accepted a 3-digit ATSH under the 29-bit
    protocol and applied it as 0x000007E5 -- a poison id nothing answers
    -- while refusing both the 8-digit ATSH a 29-bit id needs and the
    plain ATSH that would clear it, so once any header was set every
    later read (DID or OBD-II) died with NO DATA until ATZ.

    This is the fallback path. When MEB addressing IS negotiated, set_module
    deliberately sends ATCP/ATSH -- but always paired with the ATZ recovery
    that a refused header needs, and never a narrow ATCRA filter.
    """
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu

    t = _RecordingTransport()
    conn = DiagnosticConnection(t)
    bms = get_ecu("bat_mgmt")
    conn._transmit("221E3B", bms, "DID read")
    conn._transmit("03", None, "mode 03")
    conn._transmit("0902", None, "VIN")
    poisoned = [w for w in t.written if w.startswith(("ATSH", "ATCRA"))]
    assert not poisoned, poisoned


def test_truncated_multiframe_response_is_rejected():
    """A short multi-frame buffer is a truncated read, not a short value.

    Regression: returning it made u32be() yield 0, so lifetime energy
    discharged was recorded as exactly 0 kWh and flagged as an anomaly.
    """
    # First frame announces 11 bytes; only 4 bytes of payload arrive.
    assert uds.reassemble([bytes([0x10, 0x0B, 1, 2, 3])]) is None
    assert uds.reassemble([bytes([0x10, 0x0B, 1, 2, 3]),
                           bytes([0x21, 4])]) is None
    # A complete response still reassembles (FF announces 8 data bytes).
    assert uds.reassemble([bytes([0x10, 0x08, 1, 2, 3]),
                           bytes([0x21, 4]), bytes([0x22, 5, 6, 7, 8])]) == \
        bytes([1, 2, 3, 4, 5, 6, 7, 8])


def test_no_api_for_writes():
    public = [n for n in dir(uds) if n.startswith("build_")]
    assert public == ["build_read_did_request", "build_session_request",
                      "build_tester_present"]


# --- VAG MEB physical addressing ---------------------------------------------
def test_meb_29bit_header_is_stripped_and_attributed():
    """17FExxxx module responses must parse like the 18DA OBD ones.

    The 29-bit branch used to key on the "18D" prefix only, which happens to
    be true of OBD functional ids (18DAF1xx) but not of MEB module traffic
    (17FC007B requests / 17FE007B responses). Those lines fell through to
    "header off" and every BMS read came back as an undecodable frame.
    """
    frame, header = uds.line_to_frame_header("17FE007B0662028CCBAAAAAA")
    assert frame == bytes.fromhex("0662028CCBAAAAAA")
    assert header == 0x17FE007B
    assert uds.source_label(0x17FE007B) == "0x7B"
    # 17xx multi-frame (energy counters, 8 data bytes -> FF/CF/CF)
    lines = ["17FE007B" + f.hex().upper()
             for f in uds.encode_isotp(bytes(range(8)))]
    assert uds.parse_elm_lines(lines) == bytes(range(8))


def test_18db_prefix_still_strips_and_headerless_lines_stay_payloads():
    frame, header = uds.line_to_frame_header("18DB33F106410098180001")
    assert header == 0x18DB33F1
    assert frame == bytes.fromhex("06410098180001")
    # Header-off line: the whole line is the frame, no CAN id to attribute.
    assert uds.line_to_frame_header("06421E3B0FA0AA") == \
        (bytes.fromhex("06421E3B0FA0AA"), None)


class _StubTransport:
    """Stands in for a serial link: records commands, scripts replies."""

    def __init__(self, refusals=(), reply=None):
        self.sent: list[str] = []
        self.refusals = set(refusals)
        self.reply = list(reply) if reply else ["OK"]

    def send_command(self, command):
        self.sent.append(command)
        return ["?"] if command in self.refusals else list(self.reply)


def test_meb_negotiation_sets_module_mode_and_restores_functional():
    from diagnostic.elm327 import Elm327Transport

    stub = _StubTransport()
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t._negotiate_meb()

    assert t.meb_addressing is True
    assert "ATCP 17" in stub.sent and "ATSH FC007B" in stub.sent
    # Functional addressing must be restored, or every OBD-II mode 01/09
    # read would be addressed to the BMS instead of the whole bus.
    assert "ATCP 18" in stub.sent and "ATSH DB33F1" in stub.sent
    assert stub.sent.index("ATSH DB33F1") > stub.sent.index("ATSH FC007B")


def test_meb_negotiation_refused_stays_functional():
    stub = _StubTransport(refusals=("ATCP 17",))
    from diagnostic.elm327 import Elm327Transport
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t._negotiate_meb()

    assert t.meb_addressing is False
    # Must not leave the adapter on a header nothing answers.
    assert "ATSH FC007B" not in stub.sent
    assert t.set_module(0x17FC007B) is False


def test_meb_negotiation_clears_a_stale_flag_when_a_later_probe_refuses():
    """meb_addressing could only ever be set, never cleared.

    After a successful negotiation, a re-negotiation that hit an adapter now
    refusing (an ATZ from _recover(), say) left the flag True with a stale
    module. _transmit() kept taking the MEB branch, set_module() re-issued
    ATCP/ATSH, got refused and called _recover() -- a full adapter reset on
    every single request, for the rest of the session.
    """
    from diagnostic.elm327 import Elm327Transport

    # First negotiation succeeds. (_meb_module is None afterwards because
    # _restore_functional() points the adapter back at the shared OBD header;
    # the flag is what decides the MEB code path.)
    ok = _StubTransport()
    t = Elm327Transport(port="TEST")
    t.send_command = ok.send_command
    t._negotiate_meb()
    assert t.meb_addressing is True

    # The adapter is later recovered and now refuses. The stale flag must not
    # survive that.
    refusing = _StubTransport(refusals=("ATCP 17",))
    t.send_command = refusing.send_command
    t._negotiate_meb()
    assert t.meb_addressing is False, (
        "a refused re-negotiation left meb_addressing=True with a stale "
        "module; every request would now cost an adapter reset")
    assert t._meb_module is None
    assert t.set_module(0x17FC007B) is False


def test_close_clears_the_addressing_state_it_describes():
    """close() released the device but kept meb_addressing/_caf/_proto.

    A request issued between close() and initialize() would take the MEB path
    against a closed transport. _base_init already resets them; close() is the
    other place that has to.
    """
    from diagnostic.elm327 import Elm327Transport

    stub = _StubTransport()
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t._negotiate_meb()
    assert t.meb_addressing is True

    t._dev = object()      # something close() can call .close() on
    t._opened = True
    t.close()
    assert t.meb_addressing is False
    assert t._meb_module is None
    assert t._caf == 0
    assert t._proto is None
    assert t._dev is None and t._opened is False


class _LaggingAdapter:
    """A socket-ish link that answers AFTER the caller has given up.

    This is what a Bluetooth SPP drop looks like from the software side: the
    reply for request N turns up in the buffer after request N has already
    timed out, prompt and all.
    """

    def __init__(self, answers, lag_first=True):
        self.answers = list(answers)
        self.pending = b""
        self.stall_next = lag_first
        self.sent = b""
        self.timeout = None
        # A real adapter only speaks when spoken to, so the double must not
        # hand over an answer just because someone called recv(). Otherwise the
        # drain that legitimately runs before each write would consume the very
        # reply the command was waiting for.
        self.awaiting_reply = False

    # -- socket surface used by Elm327Transport
    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent += data
        self.awaiting_reply = True

    def recv(self, _n):
        if self.awaiting_reply:
            self.awaiting_reply = False
            if self.stall_next:
                # The adapter says nothing at all for this request, so the
                # caller hits its deadline and raises. The real answer is
                # staged afterwards by the test, which is exactly the shape of
                # the bug: it arrives after the request that wanted it gave up.
                self.stall_next = False
                return b""
            self._queue_next()
        if not self.pending:
            # Nothing buffered and nothing owed. A real socket would block
            # here; returning empty is the same thing to the caller.
            return b""
        out, self.pending = self.pending, b""
        return out

    def _queue_next(self):
        if not self.answers:
            return
        answer = self.answers.pop(0)
        # A real adapter terminates every reply with the '>' prompt; the
        # transport breaks on it, so it has to be in the bytes.
        self.pending += (answer + "\r>").encode()


def test_a_late_reply_cannot_be_mistaken_for_the_next_request():
    """The link must not end up permanently one request out of step.

    A timed-out read discarded the bytes it had consumed, but the rest of that
    answer -- including the '>' prompt -- stayed in the driver buffer. The next
    send_command() then found the stale '>' immediately and returned the
    PREVIOUS request's lines as its own answer, and every subsequent read
    inherited the shift until an ATZ happened to resynchronise it.

    The DID echo check in read_did() catches most of this, but the DIDs that
    are re-polled every cycle answer with byte-identical bytes, so a stale
    reply is indistinguishable from a live one -- and it gets stored with the
    current timestamp, defeating the freshness bound that exists to catch
    exactly that.
    """
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.interface import CommunicationError

    answers = [
        "OK",                                            # request 1
        "SPEED12A",                                      # request 2 (late)
        "SPEED64B",                                      # request 3
    ]
    adapter = _LaggingAdapter(answers, lag_first=False)
    t = Elm327Transport(port="TEST")
    t._dev = adapter
    t._opened = True
    t.timeout = 0.3

    assert t.send_command("010C") == ["OK"]

    # Request 2 stalls past the deadline...
    adapter.stall_next = True
    with pytest.raises(CommunicationError):
        t.send_command("010C")
    # ...and only then does its answer land in the buffer, prompt and all.
    adapter._queue_next()
    assert adapter.pending, "test double failed to stage the late answer"

    # Request 3 must get its own answer and nothing else. Without a drain,
    # _read_until_prompt finds the stale '>' first, breaks immediately, and
    # returns ["SPEED12A", "SPEED64B"] -- the previous request's reply handed
    # over as this one's.
    adapter.answers.append("SPEED64B")
    third = t.send_command("010C")
    assert third == ["SPEED64B"], (
        f"stale answer returned for a fresh request: {third} -- the transport "
        "is still offset from the link")


def test_draining_before_a_write_does_not_eat_the_current_answer():
    """The drain must not throw away the reply to the command just sent."""
    from diagnostic.elm327 import Elm327Transport

    adapter = _LaggingAdapter(["OK", "OK", "OK"], lag_first=False)
    t = Elm327Transport(port="TEST")
    t._dev = adapter
    t._opened = True
    t.timeout = 0.5

    assert t.send_command("ATI") == ["OK"]
    assert t.send_command("ATI") == ["OK"]
    assert t.send_command("ATI") == ["OK"]


def test_set_module_switches_header_and_caf_mode():
    stub = _StubTransport()
    from diagnostic.elm327 import Elm327Transport
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t.meb_addressing = True
    t._meb_module = None

    assert t.set_module(0x17FC007B) is True
    assert "ATSH FC007B" in stub.sent
    # Module reads are hand-framed (ATCAF0); the ELM must not re-frame them.
    assert "ATCAF0" in stub.sent

    stub.sent.clear()
    assert t.set_module(0x17FC007B) is True
    assert stub.sent == [], "already on that module: no traffic expected"

    stub.sent.clear()
    assert t.set_module(None) is True
    assert "ATSH DB33F1" in stub.sent
    assert "ATCAF1" in stub.sent, "OBD-II requests need auto-formatting back on"


def test_set_module_switches_protocol_for_11bit_modules():
    """MEB is not one bus: 11-bit modules need ATSP6, 29-bit ones ATSP7."""
    stub = _StubTransport()
    from diagnostic.elm327 import Elm327Transport
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t.meb_addressing = True
    t._meb_module = None

    assert t.set_module(0x00000710) is True          # energy module (11-bit)
    assert "ATSP6" in stub.sent
    # Addressed exactly the way evDash/spot2000 do it: ATCP 00 + 6-digit ATSH.
    assert "ATCP 00" in stub.sent
    assert "ATSH 000710" in stub.sent

    stub.sent.clear()
    assert t.set_module(0x00000746) is True          # another 11-bit module
    assert "ATSP6" not in stub.sent, "already on protocol 6"
    assert "ATSH 000746" in stub.sent

    stub.sent.clear()
    assert t.set_module(0x17FC0076) is True          # back to a 29-bit module
    assert "ATSP7" in stub.sent
    assert "ATSH FC0076" in stub.sent

    # Leaving an 11-bit module for the functional 29-bit OBD-II header has to
    # put the protocol back, or every later OBD-II read dies with NO DATA.
    stub.sent.clear()
    assert t.set_module(0x00000710) is True
    stub.sent.clear()
    assert t.set_module(None) is True
    assert "ATSP7" in stub.sent
    assert "ATSH DB33F1" in stub.sent


def test_set_module_refusal_resets_adapter_and_reports_failure():
    """A latched bad header needs ATZ; the caller must then read functionally."""
    stub = _StubTransport(refusals={"ATSH 000710"})
    from diagnostic.elm327 import Elm327Transport
    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t.meb_addressing = True
    t._meb_module = None

    assert t.set_module(0x00000710) is False
    assert "ATZ" in stub.sent, "only ATZ clears a header the clone latches"
    # After the reset the transport must not claim it is still on the module.
    assert t._meb_module is None


def test_meb_did_read_routes_to_module_and_hand_frames_it():
    """A read for an ECU with a tx29 must go out as a CAF0 single frame."""
    stub = _StubTransport(reply=["17FE007B0662028CCBAAAA"])
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu

    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t.meb_addressing = True
    t._meb_module = None
    conn = DiagnosticConnection(t)
    raw = conn.read_did(get_ecu("bat_mgmt"), 0x028C)

    # Single frame: 62 <DID> <data...>, PCI length 0x06 in front of it.
    assert raw == bytes.fromhex("CBAAAA")
    on_wire = [c for c in stub.sent if not c.startswith("AT")]
    assert on_wire == ["0322028C55555555"], on_wire


def test_functional_read_switches_back_off_the_module():
    stub = _StubTransport(reply=["18DAF10A03410D26"])
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.connection import DiagnosticConnection

    t = Elm327Transport(port="TEST")
    t.send_command = stub.send_command
    t.meb_addressing = True
    t._meb_module = 0x17FC007B          # left on the BMS by a previous read
    t._caf = 0
    conn = DiagnosticConnection(t)
    conn.mode01(0x0D)

    assert "ATCP 18" in stub.sent and "ATSH DB33F1" in stub.sent
    on_wire = [c for c in stub.sent if not c.startswith("AT")]
    assert on_wire == ["010D"], on_wire


def test_meb_decoders_match_car_scanner_values():
    """Every MEB scale factor is pinned to a value from the car's own log.

    Raw bytes come from a Car Scanner ELM OBD2 capture of the ID.3 (2026-10-05);
    expected values are what Car Scanner displayed. If a formula drifts, this
    test fails instead of the dashboard showing plausible nonsense.
    """
    from decoders.registry import build_default_registry

    reg = build_default_registry("meb")

    def val(key, raw):
        if isinstance(raw, (bytes, bytearray)):
            data = bytes(raw)
        else:
            data = bytes.fromhex(raw)
        return reg.get(key).decode_value(data)

    assert val("soc_abs", "CB") == pytest.approx(81.2)       # BMS 81.2 %
    assert val("soc_normal", "CB") == pytest.approx(83.63, abs=0.01)  # dash 83.63 %
    assert val("pack_voltage", "06BD") == pytest.approx(431.25)  # raw 1725
    assert val("pack_current", "00024AB6") == pytest.approx(1.98)
    assert val("cell_voltage_max", "3FF9") == pytest.approx(3.99829)
    assert val("cell_v_000", "0BB5") == pytest.approx(3.997)   # u16/1000 + 1
    assert val("battery_temp", "78") == pytest.approx(20.0)
    assert val("bat_temp_max", "0538") == pytest.approx(20.875)
    assert val("bat_temp_min", "04E8") == pytest.approx(19.625)
    assert val("dyn_charge_limit", "0429") == pytest.approx(213.0)
    assert val("dcdc_current", "00E1") == pytest.approx(14.0625)
    assert val("dcdc_voltage", "1C33") == pytest.approx(14.0996)
    assert val("cell_t_00", "01E0") == pytest.approx(20.0)
    assert val("charge_mode", "04") == 1 and val("charge_mode", "06") == 2
    assert val("charge_mode", "00") == 0 and val("op_mode", "01") == 1
    assert val("gear", "0008") == "P"
    assert val("odometer_km", "0BBF7F") == 769919.0  # 3-byte big-endian
    # Unpopulated cell slots decode to None, never a phantom voltage.
    assert val("cell_v_106", "0FFE") is None
    assert val("cell_v_107", "0FFE") is None
    # 53,200 Wh rated energy (the SoH reference) round-trips through u32.
    assert val("hv_energy_max", "04280A64") == pytest.approx(53200.0)
    # Lifetime counters from the log: 24,422.82 kWh in, 23,447.48 kWh out.
    charged_raw = int(24422.82 * 8583.07123641215)
    used_raw = -int(23447.48 * 8583.07123641215)
    counters = val("energy_counters", charged_raw.to_bytes(4, "big")
                   + (used_raw & 0xFFFFFFFF).to_bytes(4, "big"))
    assert counters["charged_kwh"] == pytest.approx(24422.82, rel=1e-6)
    assert counters["used_kwh"] == pytest.approx(23447.48, rel=1e-6)
    # Coolant: the log showed 22 C inlet and 22 C outlet parked.
    assert val("coolant_temps", "05800580") == \
        {"outlet_c": pytest.approx(22.0), "inlet_c": pytest.approx(22.0)}
    assert val("ptc_current", "00") == 0.0
    assert val("speed_kmh", "00") == 0
    assert val("outside_temp", "80") == pytest.approx(14.0)   # 14 C ambient
    assert val("inside_temp", "0122") == pytest.approx(18.0)  # 18 C cabin
    assert val("hv_aux_power", "0003") == pytest.approx(0.3)   # u16/10
    # The DID's own resolution is 1/1024 V, so compare to that not to 1e-5.
    assert val("aux_12v_voltage", "290A") == pytest.approx(14.52, abs=0.002)


def test_meb_energy_content_divisor_is_declared_unverified():
    """0x2AB8 has no published scale factor; the guess must be labelled.

    If this ever becomes verified, the note has to change with it.
    """
    from decoders.registry import build_default_registry

    spec = build_default_registry("meb").get("hv_energy_content")
    assert "ASSUMED, UNVERIFIED" in spec.notes
    assert spec.ecu_key == "energy"
    assert spec.did == 0x2AB8


def test_meb_registry_has_the_full_cell_bank():
    from decoders.registry import build_default_registry

    reg = build_default_registry("meb")
    cells = [s for s in reg.all() if s.key.startswith("cell_v_")]
    assert len(cells) == 108
    assert cells[0].did == 0x1E40 and cells[-1].did == 0x1EAB
    temps = [s for s in reg.all() if s.key.startswith("cell_t_")]
    assert len(temps) == 18            # 0x1EAE..0x1EBD plus 0x7425/0x7426


def test_soh_falls_back_to_reported_max_energy_content():
    """The MEB BMS exposes no capacity (CAC) DID, so SOH must be derivable
    from the rated max energy content instead of showing nothing."""
    from decoders.bms import soh_pct_from_energy, NOMINAL_ENERGY_WH_58KWH

    assert soh_pct_from_energy(NOMINAL_ENERGY_WH_58KWH) == 100.0
    assert soh_pct_from_energy(53200.0) == pytest.approx(91.7, abs=0.1)
    assert soh_pct_from_energy(None) is None
    assert soh_pct_from_energy(0) is None


def test_eup_profile_is_unchanged_by_the_meb_profile():
    from decoders.registry import build_default_registry

    eup = build_default_registry()          # default must stay e-Up
    assert eup.get("pack_voltage").decode_value(bytes.fromhex("0FA0")) == 62.5
    assert "cell_v_105" not in [s.key for s in eup.all()]   # 102 slots there
    meb = build_default_registry("meb")
    assert meb.get("pack_voltage").did == 0x1E3B
    assert [s.ecu_key for s in meb.all() if s.key == "odometer_km"] == ["veh_info"]


def test_ecu_specs_carry_meb_module_addresses():
    from diagnostic.ecus import get_ecu

    bms = get_ecu("bat_mgmt")
    assert bms.tx29 == 0x17FC007B and bms.rx29 == 0x17FE007B
    assert get_ecu("dcdc").tx29 == 0x17FC00B9
    assert get_ecu("veh_info").tx29 == 0x17FC0076
    # The energy and climate modules are NOT on the 29-bit bus. They answer at
    # plain 11-bit ids, addressed with ATCP 00 + ATSH 000710/000746 and
    # replying at 0x77A / 0x7B0 -- a 29-bit guess here reaches nothing.
    energy = get_ecu("energy")
    assert energy.tx29 == 0x00000710 and energy.rx29 == 0x0000077A
    assert energy.tx29 <= 0x7FF and energy.rx29 <= 0x7FF
    climate = get_ecu("climate")
    assert climate.tx29 == 0x00000746 and climate.rx29 == 0x000007B0
    assert climate.tx29 <= 0x7FF and climate.rx29 <= 0x7FF
    # ECUs with no MEB module address keep functional-only addressing.
    assert get_ecu("chg_mgmt").tx29 is None


# --- ISO-TP / ELM parsing ----------------------------------------------------
def test_line_to_frame_single():
    assert uds.line_to_frame("7ED04621E3B0FA0") == bytes.fromhex("04621E3B0FA0")


def test_line_to_frame_multi():
    frames = uds.encode_isotp(bytes(range(20)))
    lines = ["7ED" + f.hex().upper() for f in frames]
    payload = uds.parse_elm_lines(lines)
    assert payload == bytes(range(20))


def test_parse_elm_lines_no_data():
    assert uds.parse_elm_lines(["NO DATA"]) is None
    assert uds.parse_elm_lines(["SEARCHING...", "UNABLE TO CONNECT"]) is None


# --- 29-bit (MEB) addressing: functional requests, attribution by header ----
# Real ID.3 bus capture: several ECUs answer ONE functional request, each
# frame tagged with its sender's 29-bit id (18DAF1xx).
CAR_0902_LINES = [
    "18DAF1011014490201575657",   # FF from ECU 0x01: 49 02 01 "WVW"
    "18DAF10A037F0911",           # NRC from ECU 0x0A: mode 09 unsupported
    "18DAF101215A5A5A45315A4D",   # CF1 from 0x01: "ZZZE1ZM"
    "18DAF1012250303837303533",   # CF2 from 0x01: "P087053"
]


def test_line_to_frame_29bit_header():
    frame, header = uds.line_to_frame_header("18DAF10506410098180001")
    assert frame == bytes.fromhex("06410098180001")
    assert header == 0x18DAF105
    assert uds.source_label(header) == "0x05"
    # 11-bit lines keep working
    frame, header = uds.line_to_frame_header("7ED04621E3B0FA0")
    assert frame == bytes.fromhex("04621E3B0FA0")
    assert header == 0x7ED
    assert uds.source_label(header) == "0x7ED"


def test_payloads_by_source_keeps_responders_apart():
    payloads = uds.payloads_by_source(CAR_0902_LINES)
    assert payloads[0x18DAF101] == bytes.fromhex(
        "4902015756575A5A5A45315A4D50303837303533")
    assert payloads[0x18DAF10A] == bytes.fromhex("7F0911")


def test_vin_decode_29bit_multi_responder():
    """Regression: with 29-bit header lines the old parser failed per-line
    frame extraction, fell into the indexed-format path and concatenated
    raw frame hex; ISO-TP sequence bytes 0x21/0x22 are printable ASCII and
    leaked into the VIN as '!' and '"', yielding 'WVW!ZZZE1ZM"P0870'."""
    assert obd2.parse_vin_response(CAR_0902_LINES) == "WVWZZZE1ZMP087053"


def test_parse_elm_lines_prefers_positive_responder():
    lines = ["18DAF10506410098180001",   # positive 41 00 ...
             "18DAF10A037F0111"]         # negative 7F 01 11
    assert uds.parse_elm_lines(lines) == bytes.fromhex("410098180001")
    # only negatives -> the first NRC is surfaced, not None
    assert uds.parse_elm_lines(["18DAF10A037F0111"]) == \
        bytes.fromhex("7F0111")


def test_read_did_accepts_any_functional_responder():
    """read_did is sent functionally: the first positive answer with a
    matching DID echo wins, whoever it came from; NRCs surface as such."""
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu

    vin_frames = ["18DAF10A101462F190575657",
                  "18DAF10A215A5A5A45315A4D",
                  "18DAF10A2250303837303533"]
    c = DiagnosticConnection(_RecordingTransport(vin_frames))
    raw = c.read_did(get_ecu("bat_mgmt"), 0xF190)
    assert raw == b"WVWZZZE1ZMP087053"

    refused = DiagnosticConnection(
        _RecordingTransport(["18DAF10A037F2231"]))  # requestOutOfRange
    assert refused.try_read_did(get_ecu("bat_mgmt"), 0xFFFF) is None


# --- DTC freeze-frame snapshots (UDS 0x19 0x04) ------------------------------
def test_encode_vag_dtc_roundtrip():
    for raw in (bytes([0xD1, 0x23, 0x00]), bytes([0x58, 0xAB, 0xCD]),
                bytes([0x01, 0x02, 0x03])):
        assert obd2.encode_vag_dtc(obd2.decode_vag_dtc(*raw)) == raw
    # malformed codes are refused, never guessed onto the wire
    for bad in ("", "U1123", "X112300", "U412300", "U1123GG", None):
        with pytest.raises(ValueError):
            obd2.encode_vag_dtc(bad)


def test_parse_uds_dtc_snapshot_response():
    # 59 04 FF | D1 23 00 2F | rec 01, 1 identifier | DID 1E3B | data 58A0
    out = obd2.parse_uds_dtc_snapshot_response(
        bytes.fromhex("5904FFD123002F01011E3B58A0"))
    assert out == [{"code": "U112300", "status_byte": 0x2F,
                    "records": [{"record": 1, "identifiers": [0x1E3B],
                                 "data": {"1E3B": "58A0"},
                                 "raw": "58A0"}]}]
    # record number 0xFF: ECU stores no snapshot for this DTC
    none_recs = obd2.parse_uds_dtc_snapshot_response(
        bytes.fromhex("5904FFD123002FFF"))
    assert none_recs[0]["records"] == []
    # truncated positives and negatives parse to nothing
    assert obd2.parse_uds_dtc_snapshot_response(bytes.fromhex("5904FF")) == []
    assert obd2.parse_uds_dtc_snapshot_response(bytes.fromhex("7F1931")) == []


def test_read_dtc_snapshots_functional():
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.interface import CommunicationError

    # one ECU answers with a multi-frame snapshot (29-bit headers)
    lines = ["18DAF10A100D5904FFD12300",
             "18DAF10A212F01011E3B58A0"]
    c = DiagnosticConnection(_RecordingTransport(lines))
    snaps = c.read_dtc_snapshots("U112300")
    assert list(snaps) == [0x18DAF10A]
    assert snaps[0x18DAF10A][0]["records"][0]["data"] == {"1E3B": "58A0"}

    # malformed code: refused before anything reaches the wire
    t = _RecordingTransport(lines)
    with pytest.raises(CommunicationError):
        DiagnosticConnection(t).read_dtc_snapshots("banana")
    assert not [w for w in t.written if w.upper().startswith("19")]

    # everybody refuses (NRC 0x31) -> empty dict, no exception
    c3 = DiagnosticConnection(_RecordingTransport(["18DAF10A037F1931"]))
    assert c3.read_dtc_snapshots("U112300") == {}


def test_negative_response():
    with pytest.raises(uds.NegativeResponseError) as exc:
        uds.expect_positive(bytes([0x7F, 0x22, 0x31]), 0x22)
    assert "0x31" in str(exc.value)


# --- OBD-II ------------------------------------------------------------------
def test_vin_decode():
    # header-off ELM format with index prefixes
    vin = obd2.parse_vin_response(["014", "0:4902015756575A5A5A4531",
                                   "1:5A4D503038373035", "2:33000000000000"])
    assert vin == "WVWZZZE1ZMP087053"


def test_vin_decode_headers_on():
    # headers-on ISO-TP: 10 14 49 02 01 57 56 57 / 21 5A 5A 5A 45 31 5A 4D / 22 ...
    lines = ["7E8101449020157565 7", "7E8215A5A5A45315A4D", "7E82250303837303533"]
    vin = obd2.parse_vin_response([ln.replace(" ", "") for ln in lines])
    assert vin == "WVWZZZE1ZMP087053"


def test_vin_year():
    assert obd2.decode_vin_year("WVWZZZE1ZMP087053") == 2021


def test_mode01_voltage():
    # 7E8 06 41 42 36 1A AA AA  (13.85 V control-module voltage)
    data = obd2.parse_mode01(["7E8064142361AAAAA"], 0x42)
    value, unit = obd2.decode_pid(0x42, data)
    assert unit == "V"
    assert 13.0 < value < 14.5


def test_obd2_dtc_decode():
    # P0420: letter P (00b), first digit 0, second digit 4 -> 0x04, then 0x20
    assert obd2.decode_obd2_dtc(0x04, 0x20) == "P0420"


# --- VAG DTC ------------------------------------------------------------------
def test_vag_dtc_decode():
    # U1123 00: letter U (11b), first digit 1, second 1 -> 0xD1, then 0x23,
    # failure-type byte 0x00
    assert obd2.decode_vag_dtc(0xD1, 0x23, 0x00) == "U112300"


def test_parse_uds_dtc_response():
    payload = bytes([0x59, 0x02, 0xFF, 0xD1, 0x23, 0x00, 0x2F])
    dtcs = obd2.parse_uds_dtc_response(payload)
    assert dtcs == [{"code": "U112300", "status_byte": 0x2F}]

# --- statistics ----------------------------------------------------------------
from analysis import stats  # noqa: E402


def test_mean_stdev():
    assert stats.mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert stats.stdev([1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert stats.mean([]) is None


def test_regression_slope():
    ys = [0, 1, 2, 3, 4]
    assert stats.linear_regression_slope(ys) == pytest.approx(1.0)


def test_classify_trend():
    assert stats.classify_trend(0.5, 2.0, 0.1) == "increasing"
    assert stats.classify_trend(-0.5, 2.0, 0.1) == "decreasing"
    assert stats.classify_trend(0.01, 2.0, 0.1) == "noisy (no clear trend)"
    assert stats.classify_trend(0.0, None, 0.1) == "stable"
    assert stats.classify_trend(None, None, 0.1) == "insufficient_data"


def test_trend_verdict_uses_the_slope_standard_error():
    """The slope must be judged against its own uncertainty.

    noise_std is the spread of the raw samples (mV) while the slope is mV/day,
    so dividing one by a constant could not say whether a trend was real.
    """
    # The same slope with a tight fit is a real trend; the old noise_std rule
    # called this "noisy" because 0.5 < 20/30.
    assert stats.classify_trend(0.5, 20.0, 0.1, slope_stderr=0.1) == "increasing"
    # ...and with a loose fit the verdict flips, which noise_std alone could not do.
    assert stats.classify_trend(0.5, 0.0, 0.1, slope_stderr=0.5) == \
        "noisy (no clear trend)"
    # Within two standard errors of zero: the direction is not established.
    assert stats.classify_trend(0.05, 0.0, 0.1, slope_stderr=0.05) == \
        "noisy (no clear trend)"
    assert stats.classify_trend(-0.9, 0.0, 0.1, slope_stderr=0.8) == \
        "noisy (no clear trend)"
    # 2 sigma above zero is a real signal in the other direction.
    assert stats.classify_trend(0.5, 0.0, 0.1, slope_stderr=0.1) == "increasing"
    assert stats.classify_trend(-0.5, 0.0, 0.1, slope_stderr=0.1) == "decreasing"


def test_slope_stderr_is_none_when_it_cannot_be_computed():
    assert stats.linear_regression_stderr([1.0, 2.0]) is None      # n < 3
    assert stats.linear_regression_stderr([1.0, 2.0, 3.0], [1, 1, 1]) is None
    # A perfectly straight line has no residual scatter.
    assert stats.linear_regression_stderr([1.0, 2.0, 3.0, 4.0]) is None
    got = stats.linear_regression_stderr([1.0, 3.0, 2.0, 5.0, 4.0])
    assert got is not None and got > 0


def test_classify_trend_without_stderr_keeps_the_old_fallback():
    # Callers that cannot supply a standard error still get an answer.
    assert stats.classify_trend(0.01, 2.0, 0.1) == "noisy (no clear trend)"
    assert stats.classify_trend(0.5, 2.0, 0.1) == "increasing"


def test_pearson():
    xs = [1, 2, 3, 4, 5]
    assert stats.pearson(xs, [2, 4, 6, 8, 10]) == pytest.approx(1.0)
    assert stats.pearson(xs, [10, 8, 6, 4, 2]) == pytest.approx(-1.0)


def test_zscore_outlier():
    from analysis.anomaly import detect_series_anomalies
    ts = [f"2026-08-{d:02d}T10:00:00+00:00" for d in range(1, 13)]
    vals = [24.0] * 11 + [40.0]  # outlier must exceed z=3 despite std inflation
    out = detect_series_anomalies(ts, vals, z_threshold=3.0)
    assert len(out) == 1
    assert out[0][1] == 40.0


# --- decoder registry -----------------------------------------------------------
def test_soh_uses_the_shared_nominal_constant():
    """collector.py used to hardcode 164.0 next to the real constant in
    decoders/bms.py; the two could silently drift apart."""
    from decoders.bms import NOMINAL_CAC_AH_58KWH, soh_pct_from_cac
    assert soh_pct_from_cac(NOMINAL_CAC_AH_58KWH) == 100.0
    assert soh_pct_from_cac(NOMINAL_CAC_AH_58KWH / 2) == 50.0
    # "unknown" must stay distinguishable from a real 0 %
    assert soh_pct_from_cac(None) is None
    assert soh_pct_from_cac(0) is None


def test_collector_takes_nominal_cac_from_config(tmp_path):
    from collector import Collector
    from decoders.bms import NOMINAL_CAC_AH_58KWH
    cfg = load_config()
    cfg._data["database"]["path"] = str(tmp_path / "c.db")
    cfg._data["adapter"]["type"] = "elm327_tcp"
    cfg._data["battery"]["nominal_cac_ah"] = 200.0
    col = Collector(cfg, Repository(cfg._data["database"]["path"]))
    assert col.nominal_cac_ah == 200.0
    # and it falls back to the published spec when unset
    cfg._data["battery"].pop("nominal_cac_ah")
    fallback = Collector(cfg, Repository(cfg._data["database"]["path"]))
    assert fallback.nominal_cac_ah == NOMINAL_CAC_AH_58KWH


def test_collector_soh_uses_the_configured_nominal(tmp_path):
    """Guards against the collector going back to a literal 164.0: with a
    nominal of 200 Ah, a measured CAC of 100 Ah is 50 %, not 61 %."""
    import json

    from collector import Collector
    cfg = load_config()
    cfg._data["database"]["path"] = str(tmp_path / "soh.db")
    cfg._data["adapter"]["type"] = "elm327_tcp"
    cfg._data["battery"]["nominal_cac_ah"] = 200.0
    repo = Repository(cfg._data["database"]["path"])
    col = Collector(cfg, repo)
    col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
    ts = utcnow()
    repo.record_measurement(
        col.vehicle_id, ts, "soh_cac", "bat_mgmt", "UDS-0x22", "0x1EFC", "Ah",
        "reported", "builtin", "experimental", "0064",
        None, json.dumps({"battery_cac_ah": 100.0}))

    col._battery_snapshot(ts, {})
    row = repo.conn.execute(
        "SELECT value FROM measurements WHERE key='soh_pct' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "soh_pct was not recorded"
    assert row["value"] == pytest.approx(50.0), \
        "collector ignored battery.nominal_cac_ah (used a hard-coded constant?)"
    repo.close()


def test_broken_decoder_is_logged_not_silent(caplog):
    """A decoder that raises is our bug, not a vehicle condition. It must be
    visible in the log rather than looking like 'this DID has no value'."""
    import logging

    from decoders.registry import DIDSpec

    def boom(raw: bytes):
        raise ValueError("bad scale factor")

    spec = DIDSpec(key="k", ecu_key="bat_mgmt", did=0x1E40, name="n", unit="V",
                   decode=boom)
    with caplog.at_level(logging.WARNING, logger="decoders.registry"):
        assert spec.decode_value(b"\x01\x02") is None, "must not raise"
    assert any("k" in r.message for r in caplog.records), \
        "decoder failure was not logged"
    assert any("0102" in r.message for r in caplog.records), \
        "raw bytes not included in the log"


def test_decoder_without_function_returns_none_quietly(caplog):
    """A raw-only DID is normal and must NOT warn."""
    import logging

    from decoders.registry import DIDSpec
    spec = DIDSpec(key="raw_only", ecu_key="chg", did=0x41FC, name="n", unit="")
    with caplog.at_level(logging.WARNING, logger="decoders.registry"):
        assert spec.decode_value(b"\x01") is None
    assert not caplog.records, f"raw-only DID warned: {caplog.records}"
# --- database -------------------------------------------------------------------
from database.repository import Repository, utcnow  # noqa: E402
from analysis.battery import cell_delta_trend  # noqa: E402
from analysis.dtc import classify_dtc  # noqa: E402
from analysis.charging import correlate_dtc_with_sessions  # noqa: E402


def test_repository_roundtrip(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
    ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    repo.record_battery_snapshot(vid, ts, soc_normal_pct=70, cell_delta_mv=28)
    hist = repo.battery_history(30, vid)
    assert len(hist) == 1
    assert hist[0]["soc_normal_pct"] == 70
    repo.record_measurement(vid, ts, "pack_voltage", "bat_mgmt", "UDS-0x22",
                            "0x1E3B", "V", "reported", "builtin", "documented",
                            "DEADBEEF", 355.2)
    latest = repo.latest_measurements(vid)
    assert latest["pack_voltage"]["value"] == 355.2


def test_cell_voltages_keep_their_physical_cell_number(repo):
    """cell_index must be the DID's cell number, not the list position.

    Cells outside the real pack answer NRC 0x31 and are dropped, so positions
    shift: without the mapping, cell_index 6 meant cell 7 on one sweep and
    cell 8 on the next, and any per-cell trend compared different cells.
    """
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = "2026-09-01T10:00:00+00:00"
    # cells 7, 9 and 10 answered; 8 returned NRC 0x31
    repo.record_cell_voltages(vid, ts, [1.078, 1.090, 1.102], [7, 9, 10])
    rows = repo.conn.execute(
        "SELECT cell_index, voltage_v FROM cell_voltages "
        "ORDER BY cell_index").fetchall()
    assert [(r["cell_index"], r["voltage_v"]) for r in rows] == \
        [(7, 1.078), (9, 1.090), (10, 1.102)]


def test_record_cell_voltages_rejects_misaligned_numbers(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    with pytest.raises(ValueError):
        repo.record_cell_voltages(vid, "2026-09-01T10:00:00+00:00",
                                  [1.0, 1.1], [1])


def test_latest_measurements_respects_max_age(repo):
    """A cached value older than max_age_s must not be offered as current."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(
        timespec="seconds")
    recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo.record_measurement(vid, old, "pack_voltage", "bat_mgmt", "UDS-0x22",
                            "0x1E3B", "V", "reported", "builtin", "documented",
                            "1A2B", 350.0)
    assert "pack_voltage" in repo.latest_measurements(vid)
    assert "pack_voltage" not in repo.latest_measurements(vid, max_age_s=60)
    repo.record_measurement(vid, recent, "pack_voltage", "bat_mgmt", "UDS-0x22",
                            "0x1E3B", "V", "reported", "builtin", "documented",
                            "1A2B", 351.0)
    got = repo.latest_measurements(vid, max_age_s=60)
    assert got["pack_voltage"]["value"] == 351.0


def test_raw_response_preserved(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    repo.record_measurement(vid, "2026-09-01T10:00:00+00:00", "k", "ecu",
                            "UDS-0x22", "0x0000", "", "reported", "raw-only",
                            "unknown", "1A2B3C", None, None, success=False,
                            error="NRC 0x31")
    row = repo.conn.execute("SELECT raw_response, success, error FROM "
                            "measurements").fetchone()
    assert row["raw_response"] == "1A2B3C"
    assert row["success"] == 0
    assert row["error"] == "NRC 0x31"


def test_dtc_classification():
    assert "communication" in classify_dtc("U112300")
    assert "potentially critical" in classify_dtc("P0A7F00")
    assert "active" in classify_dtc("P123400", status_byte=0x01)
    assert "historical" in classify_dtc("P123400", status_byte=0x08)


def test_cell_delta_trend(repo):
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    for d in range(10):
        ts = (now - timedelta(days=10 - d)).isoformat(timespec="seconds")
        repo.record_battery_snapshot(vid, ts, cell_delta_mv=20 + d)
    result = cell_delta_trend(repo, 30, vid)
    assert result["status"] == "ok"
    assert result["current_mv"] == 29.0
    assert result["trend"] == "increasing"
    assert result["span_days"] >= 9.0
    assert result["slope_mv_per_day"] is not None


def test_cell_delta_trend_refuses_tiny_sample_span(repo):
    """A per-day slope from samples spanning seconds is noise, not a trend.

    Regression: 3 samples a few seconds apart produced slopes in the tens of
    thousands of mV/day and a confident "decreasing" label.
    """
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    now = datetime.now(timezone.utc)
    for i, delta in enumerate((20, 22, 21)):
        ts = (now + timedelta(seconds=i)).isoformat(timespec="seconds")
        repo.record_battery_snapshot(vid, ts, cell_delta_mv=delta)
    result = cell_delta_trend(repo, 30, vid)
    assert result["status"] == "ok"          # descriptive stats are still valid
    assert result["trend"] == "insufficient_span"
    assert result["slope_mv_per_day"] is None
    assert result["span_days"] < 0.001
    assert result["span_text"] == "2 s"      # not "0.0 d"
    # ...but the underlying statistics are still reported.
    assert result["samples"] == 3
    assert result["mean_mv"] == 21.0


@pytest.mark.parametrize("days,expected", [
    (2.315e-05, "2 s"),      # 2 seconds
    (6 / 24, "6.0 h"),       # 6 hours
    (9.5, "9.5 d"),
])
def test_format_span(days, expected):
    """A 2-second span must not be rendered as "0.0 d"."""
    from analysis.battery import format_span
    assert format_span(days) == expected


def test_cell_delta_trend_min_span_is_configurable(repo):
    """The 1-day floor is a default, and callers can demand more."""
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    now = datetime.now(timezone.utc)
    for i, delta in enumerate((20, 22, 21)):
        ts = (now + timedelta(hours=i * 3)).isoformat(timespec="seconds")
        repo.record_battery_snapshot(vid, ts, cell_delta_mv=delta)
    assert cell_delta_trend(repo, 30, vid)["trend"] == "insufficient_span"
    # 6 h of data: fine against the 1-day default, not against a 7-day floor.
    assert cell_delta_trend(repo, 30, vid, min_span_days=0.01)["trend"] != \
        "insufficient_span"
    assert cell_delta_trend(repo, 30, vid, min_span_days=7)["trend"] == \
        "insufficient_span"


def test_repository_context_manager_closes_connection():
    """`with Repository(...)` must close the connection on every exit path."""
    with tempfile.TemporaryDirectory() as tmp:
        with Repository(os.path.join(tmp, "ctx.db")) as r:
            r.ensure_vehicle("WVWZZZE1ZMP087053")
        with pytest.raises(sqlite3.ProgrammingError):
            r.conn.execute("SELECT 1")


def test_analysis_service_ask_with_empty_database(cfg):
    """An empty DB must not 500 when the LLM is reachable.

    Regression: `vehicle_row["id"]` raised TypeError on None, and
    llm_reports.vehicle_id is NOT NULL, so there is no vehicle to persist to.
    """
    from ai.service import AnalysisService

    class _FakeOllama:
        model = "fake-model"

        def generate(self, prompt, system=None, temperature=None):
            return "No fault codes recorded.\n\nLIMITATIONS\n- none"

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "empty.db")
        with Repository(db) as r:
            svc = AnalysisService(cfg, r)
            svc.llm = _FakeOllama()
            result = svc.ask("Give me a health report.")
            # Read back on the same connection: llm_reports.vehicle_id is
            # NOT NULL, so a stray write would raise IntegrityError here.
            stored = r.conn.execute(
                "SELECT COUNT(*) FROM llm_reports").fetchone()[0]

    assert result["report"].startswith("No fault codes")
    assert any("NOT PERSISTED" in w for w in result["warnings"]), result
    assert stored == 0


def test_analysis_service_ask_persists_when_vehicle_exists(cfg):
    """The normal (non-empty DB) path must still store the report."""
    from ai.service import AnalysisService

    class _FakeOllama:
        model = "fake-model"

        def generate(self, prompt, system=None, temperature=None):
            return "Cells look balanced.\n\nLIMITATIONS\n- none"

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "one_vehicle.db")
        with Repository(db) as r:
            vid = r.ensure_vehicle("WVWZZZE1ZMP087053")
            svc = AnalysisService(cfg, r)
            svc.llm = _FakeOllama()
            result = svc.ask("Are my cells balanced?")
            stored = r.conn.execute(
                "SELECT vehicle_id FROM llm_reports").fetchone()

    assert not any("NOT PERSISTED" in w for w in result["warnings"]), result
    assert stored is not None, "report was not persisted"
    assert stored["vehicle_id"] == vid


def test_cmd_analyze_empty_db_returns_1(cfg):
    """The 'no vehicle' early return must not leak the sqlite connection."""
    from main import cmd_analyze
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "no_vehicle.db")
        cfg._data["database"]["path"] = db
        with Repository(db):          # create schema, add no vehicle
            pass
        assert cmd_analyze(cfg) == 1


def test_cmd_analyze_persists_results(cfg):
    from main import cmd_analyze
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "analyze.db")
        cfg._data["database"]["path"] = db
        with Repository(db) as r:
            vid = r.ensure_vehicle("WVWZZZE1ZMP087053")
            now = datetime.now(timezone.utc)
            for d in range(10):
                ts = (now - timedelta(days=10 - d)).isoformat(timespec="seconds")
                r.record_battery_snapshot(vid, ts, cell_delta_mv=20 + d)
        assert cmd_analyze(cfg) == 0
        with Repository(db) as r:
            n = r.conn.execute(
                "SELECT COUNT(*) FROM analysis_results").fetchone()[0]
    assert n == 2, "battery-cell-delta + charging-dtc-correlation expected"


def test_charging_correlation(repo):
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    now = datetime.now(timezone.utc)
    sid = repo.open_session(vid, now.isoformat(), "AC")
    repo.close_session(sid, (now + timedelta(hours=1)).isoformat())
    repo.upsert_dtc(vid, (now + timedelta(hours=2)).isoformat(), "chg_mgmt",
                    "U112300", "desc", ["communication"])
    corr = correlate_dtc_with_sessions(repo, vid)
    assert corr["codes_linked_to_charging"] == ["U112300"]
    assert corr["links"][0]["gap_hours"] == 1.0
# --- config / simulator end-to-end ----------------------------------------------
from config import load_config, Config, ConfigError  # noqa: E402


def test_config_defaults():
    cfg = load_config()
    assert cfg.ollama_model  # configurable, not hard-coded in code
    assert cfg.get("vehicle.vin") == "WVWZZZE1ZMP087053"
    assert cfg.get("analysis.trend_window_days") == 30
    assert cfg.get("analysis.charging_window_days") == 90


def test_config_local_overlay_deep_merges(tmp_path):
    """config.local.yaml is documented in the README and gitignored, so it has
    to actually be loaded -- and nested sections must merge key by key, not be
    replaced wholesale, or overriding adapter.port would drop adapter.baudrate.
    """
    base = tmp_path / "config.yaml"
    base.write_text("adapter:\n  port: COM3\n  baudrate: 38400\n"
                    "web:\n  port: 8000\n", encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text("adapter:\n  port: COM9\n", encoding="utf-8")

    cfg = load_config(base, local)
    assert cfg.get("adapter.port") == "COM9", "local override not applied"
    assert cfg.get("adapter.baudrate") == 38400, "sibling key lost by merge"
    assert cfg.get("web.port") == 8000, "untouched section changed"


def test_config_missing_local_file_is_not_an_error(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("adapter:\n  port: COM3\n", encoding="utf-8")
    cfg = load_config(base, tmp_path / "does-not-exist.yaml")
    assert cfg.get("adapter.port") == "COM3"


def test_config_non_mapping_local_file_names_the_file(tmp_path):
    """A local file holding a list must not blow up inside the merge."""
    base = tmp_path / "config.yaml"
    base.write_text("adapter:\n  port: COM3\n", encoding="utf-8")
    local = tmp_path / "config.local.yaml"
    local.write_text("- COM9\n- COM10\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(base, local)
    assert "config.local.yaml" in str(e.value)


@pytest.mark.parametrize("body,expect", [
    ("collector:\n  poll_interval: 0\n", "greater than 0"),
    ("collector:\n  poll_interval: -5\n", "greater than 0"),
    ("collector:\n  poll_interval: fast\n", "expected a number"),
    ("collector:\n  max_value_age_s: 10\n", "must exceed"),
    ("collector:\n  slow_poll_interval: 1800\n", "must exceed"),
])
def test_config_rejects_unusable_intervals(tmp_path, body, expect):
    """Fail at load, naming the key, instead of busy-looping in the collector."""
    base = tmp_path / "config.yaml"
    base.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(base, tmp_path / "none.yaml")
    assert expect in str(e.value)


def test_non_battery_anomaly_scan_finds_a_real_outlier(repo):
    """Regression: Repository.measurement_series was defined twice in the same
    class with different signatures, and the second definition silently won.

    analysis/anomaly.py used the (key, since, vehicle_id) form, so every call
    bound a metric NAME to the vehicle_id column and an ISO TIMESTAMP to the
    key column. The query matched nothing, returned no rows, and reported zero
    anomalies -- silently, for every non-battery metric, forever. A 900 km/h
    reading against a 50 km/h baseline must now be flagged.
    """
    from analysis.anomaly import scan_metric
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    for i in range(40):
        # 39 normal readings and one impossible one.
        value = 50.0 if i < 39 else 900.0
        repo.record_measurement(
            vid, f"2026-10-05T10:{i:02d}:00+00:00", "vehicle_speed", "-",
            "OBD-01", "0x0D", "km/h", "reported", "SAE J1979 PID 0x0D",
            "documented", "00", value)
    found = scan_metric(repo, vid, "vehicle_speed", "vehicle speed",
                        days=3650, battery_field=False)
    assert found == 1
    stored = repo.conn.execute(
        "SELECT value, zscore FROM anomalies WHERE metric='vehicle_speed'"
    ).fetchall()
    assert len(stored) == 1
    assert stored[0]["value"] == 900.0


def test_anomaly_rescan_does_not_duplicate_rows(repo):
    """Analysis re-reads the whole window every run, so the same anomaly is
    offered again on every dashboard refresh."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = "2026-09-01T10:00:00+00:00"
    for _ in range(3):
        repo.add_anomaly(vid, ts, "cell_delta_mv", 42.0, 10.0, 2.0, 16.0,
                         "high", "cell delta above baseline")
    rows = repo.conn.execute("SELECT value FROM anomalies WHERE vehicle_id=?",
                             (vid,)).fetchall()
    assert len(rows) == 1
    # and the row tracks the latest calculation rather than being frozen
    repo.add_anomaly(vid, ts, "cell_delta_mv", 43.0, 10.0, 2.0, 16.5,
                     "high", "cell delta above baseline")
    got = repo.conn.execute("SELECT value FROM anomalies WHERE vehicle_id=?",
                            (vid,)).fetchall()
    assert len(got) == 1 and got[0]["value"] == 43.0


def test_dtc_occurrence_count_is_not_a_poll_count(repo):
    """A stored DTC is re-read every cycle; that must not inflate the count."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    kw = dict(description="test", categories=["powertrain"], status="stored")
    repo.upsert_dtc(vid, t0.isoformat(timespec="seconds"), "0x7E",
                    "1A2B3C", **kw)
    # 12 polls 5 s apart -- one continuous fault
    for i in range(12):
        ts = (t0 + timedelta(seconds=5 * i)).isoformat(timespec="seconds")
        repo.upsert_dtc(vid, ts, "0x7E", "1A2B3C", **kw)
    row = repo.conn.execute("SELECT occurrence_count, first_seen, last_seen "
                            "FROM dtcs WHERE vehicle_id=?", (vid,)).fetchone()
    assert row["occurrence_count"] == 1, "poll counted as a new occurrence"
    assert row["first_seen"] == t0.isoformat(timespec="seconds"), "first_seen moved"
    # after the rearm window it counts again as a genuinely new sighting
    later = (t0 + timedelta(minutes=30)).isoformat(timespec="seconds")
    repo.upsert_dtc(vid, later, "0x7E", "1A2B3C", **kw)
    assert repo.conn.execute("SELECT occurrence_count FROM dtcs WHERE vehicle_id=?",
                             (vid,)).fetchone()["occurrence_count"] == 2


def test_a_continuous_dtc_does_not_write_a_row_per_poll(repo):
    """dtc_occurrences grew without bound and could never be pruned.

    The row was inserted unconditionally on every upsert_dtc, while the
    rearm gate only guarded occurrence_count. read_and_store_dtcs() runs every
    cycle, so one stored DTC produced ~17,280 rows a day at the configured 5 s
    poll interval -- each with a near-identical freeze frame, none of them
    reclaimable by prune(), and latest_freeze_frames() scanned the lot on every
    /dtcs page, /charging page and AI question.
    """
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    frame = {"speed": 0, "coolant_c": 20.0}
    kw = dict(description="test", categories=["powertrain"], status="confirmed",
              freeze_frame=frame)
    repo.upsert_dtc(vid, t0.isoformat(timespec="seconds"), "0x7E", "1A2B3C", **kw)

    def occurrences():
        return repo.conn.execute(
            "SELECT COUNT(*) c FROM dtc_occurrences WHERE dtc_id="
            "(SELECT id FROM dtcs WHERE vehicle_id=? AND ecu=? AND code=?)",
            (vid, "0x7E", "1A2B3C")).fetchone()["c"]

    # 200 polls, 5 s apart, all reporting the same frozen snapshot.
    for i in range(1, 200):
        repo.upsert_dtc(vid, (t0 + timedelta(seconds=5 * i)).isoformat(
            timespec="seconds"), "0x7E", "1A2B3C", **kw)
    assert occurrences() == 1, (
        f"{occurrences()} occurrence rows for one continuous fault with an "
        "unchanged freeze frame")

    # A freeze frame that has actually moved is new evidence and is kept.
    moved = dict(frame, speed=57)
    repo.upsert_dtc(vid, (t0 + timedelta(seconds=1000)).isoformat(
        timespec="seconds"), "0x7E", "1A2B3C",
        description="test", categories=["powertrain"], status="confirmed",
        freeze_frame=moved)
    assert occurrences() == 2, "a changed freeze frame was discarded"

    # The latest one is what a reader gets back.
    latest = repo.latest_freeze_frames(vid)
    assert latest[("0x7E", "1A2B3C")] == moved, (
        f"stale freeze frame returned: {latest}")


def test_dtc_occurrences_carry_no_index_on_purpose(repo):
    """An index was added here on audit advice and then removed by measurement.

    latest_freeze_frames() reads and json.loads every row for the vehicle, so it
    is O(rows) regardless of indexing, and an index on (dtc_id, ts) turned its
    sequential scan into random I/O: 2439 ms -> 3492 ms over 518k rows, on the
    query that runs on every /dtcs page, /charging page and AI question. It did
    help the 500-row dtc_occurrences() read (178 ms -> 67 ms), but paying 43% on
    the hot path to speed up the cold one is a regression, not a fix. The real
    repair is that upsert_dtc() no longer writes a row per poll.

    This test exists so nobody "helpfully" adds it back without measuring.
    """
    names = {r[1] for r in repo.conn.execute("PRAGMA index_list(dtc_occurrences)")}
    assert names == set(), (
        f"unexpected index on dtc_occurrences: {names}. If you added one, "
        "re-run the EXPLAIN QUERY PLAN and timing comparison in the comment in "
        "database/models.py first -- an index here measurably slowed the hot "
        "path.")


def test_a_failed_prune_deletes_nothing(repo):
    """Pruning destroys data, so a half-applied prune is worse than none.

    The connection sits at sqlite3's default isolation_level="", which opens a
    transaction implicitly on the first DELETE but does not close it when a
    statement raises. There was no rollback() anywhere, so the deletions from
    the tables before the failing one stayed pending -- and the vacuum() that
    main.py runs immediately afterwards committed them, because switching
    isolation_level is a commit in disguise. The caller got an exception and a
    pruned database, with no way to tell the two apart.
    """
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = "2020-01-01T00:00:00+00:00"
    repo.record_measurement(vid, old, "pack_voltage_v", "bat_mgmt", "22", "2AB7",
                            "V", "MEB profile", "u16/100", "assumed",
                            "6202AB7F4", value=1.0)
    repo.record_measurement(vid, old, "pack_current_a", "bat_mgmt", "22", "2AB8",
                            "A", "MEB profile", "i16/10", "assumed",
                            "6202AB80C8", value=1.0)
    # cell_voltages is deleted third, so the trigger below aborts the prune
    # *after* measurements has already been emptied within the transaction.
    repo.record_cell_voltages(vid, old, [3.9, 3.95], [7, 8])
    repo.conn.commit()
    assert repo.conn.execute(
        "SELECT COUNT(*) c FROM measurements").fetchone()["c"] == 2

    # A real SQLite failure rather than a monkeypatch: RAISE(ABORT) inside a
    # trigger aborts the statement the way a disk error or constraint would.
    repo.conn.execute(
        "CREATE TRIGGER block_cell_delete BEFORE DELETE ON cell_voltages "
        "BEGIN SELECT RAISE(ABORT, 'simulated I/O failure'); END")
    repo.conn.commit()
    try:
        with pytest.raises(sqlite3.DatabaseError):
            repo.prune(days=0, keep_raw=False)
    finally:
        repo.conn.execute("DROP TRIGGER IF EXISTS block_cell_delete")
        repo.conn.commit()

    # Nothing deleted, and no transaction left open for a later commit() to
    # finish off.
    assert repo.conn.execute(
        "SELECT COUNT(*) c FROM measurements").fetchone()["c"] == 2, \
        "a partial prune was left pending"
    assert repo.conn.execute(
        "SELECT COUNT(*) c FROM cell_voltages").fetchone()["c"] == 2
    assert not repo.conn.in_transaction, "an open transaction survived a failure"

    # And vacuum must not resurrect it either way.
    repo.vacuum()
    assert repo.conn.execute(
        "SELECT COUNT(*) c FROM measurements").fetchone()["c"] == 2, \
        "vacuum committed a partial prune"


def test_a_zero_reading_is_not_treated_as_missing(repo):
    """`or` cannot be used to pick a fallback column.

    A flat battery is 0 %, not "no data". The trend chart and the LLM battery
    overview both fell back to the absolute SoC whenever the normalised figure
    was 0, which is precisely when the number must not be fudged.
    """
    from analysis.battery import battery_overview

    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = "2020-01-01T00:00:00+00:00"
    # Normalised genuinely 0, absolute 64 -- the two disagree, so a silent
    # fallback is detectable rather than harmless.
    repo.record_battery_snapshot(vid, old, soc_normal_pct=0.0,
                                 soc_abs_pct=64.0, pack_voltage_v=300.0)
    repo.conn.commit()

    rows = repo.battery_history(3650, vid)
    assert rows, "fixture not stored"
    overview = battery_overview(repo, 3650, vid)
    assert overview["soc_latest_pct"] == 0.0, \
        "a real 0 % SoC was replaced by the fallback column"

    from web.dashboard import _first_number
    assert _first_number(0.0, 64.0) == 0.0
    assert _first_number(None, 64.0) == 64.0
    assert _first_number(None, None) is None
    assert _first_number(0, 0) == 0


def test_concurrent_upserts_do_not_collide(repo):
    """Two threads reaching ensure_vehicle/upsert_dtc at once must not raise.

    Both read-then-wrote: each could find no row, and the loser's INSERT then
    failed on the UNIQUE constraint. ON CONFLICT DO NOTHING makes the second
    insert idempotent instead.
    """
    import threading
    errors = []
    ids = []
    lock = threading.Lock()

    def make_vehicle():
        try:
            got = repo.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
            with lock:
                ids.append(got)
        except Exception as exc:            # pragma: no cover - diagnostics
            errors.append(repr(exc))

    def make_dtc(vid):
        try:
            for _ in range(40):
                repo.upsert_dtc(vid, "2026-09-01T10:00:00+00:00", "0x7E",
                                "1A2B3C", "test", ["powertrain"])
        except Exception as exc:            # pragma: no cover - diagnostics
            errors.append(repr(exc))

    ts = [threading.Thread(target=make_vehicle) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"race raised: {errors[:2]}"
    assert len(set(ids)) == 1, f"vehicle id disagreed: {set(ids)}"
    vid = ids[0]

    ts = [threading.Thread(target=make_dtc, args=(vid,)) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, f"race raised: {errors[:2]}"
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM dtcs WHERE vehicle_id=?", (vid,)).fetchone()[0] == 1
    assert repo.conn.execute(
        "SELECT COUNT(*) FROM vehicles WHERE vin=?",
        ("WVWZZZE1ZMP087053",)).fetchone()[0] == 1


def test_first_vehicle_prefers_the_most_recent_row(repo):
    """Regression: a corrupt-VIN row created by an old buggy run hijacked
    the dashboard, the AI context and `analyze`, because they all picked
    ORDER BY id LIMIT 1 -- the oldest row, with none of the data."""
    repo.ensure_vehicle('WVW!ZZZE1ZM"P0870')    # stray artifact, created first
    repo.ensure_vehicle("WVWZZZE1ZMP087053")    # the real vehicle, later
    row = repo.first_vehicle()
    assert row["vin"] == "WVWZZZE1ZMP087053"


def test_collect_once_records_speed_and_12v(repo, tmp_path):
    """Regression: decoded OBD PIDs not in the recording table were silently
    dropped -- vehicle speed (0x0D) was polled and decoded every cycle but
    never stored. Both PIDs must land in measurements and the snapshot."""
    from collector import Collector
    from diagnostic.interface import CommunicationError
    from diagnostic.uds import NegativeResponseError

    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})

    class _CarConn:
        def functional_probe(self, hexcmd, purpose, ecu=None):
            return {}

        def read_did(self, ecu, did):
            raise NegativeResponseError(0x22, 0x31)

        def mode01(self, pid):
            if pid == 0x0D:
                return bytes([38])            # 38 km/h
            if pid == 0x42:
                return bytes([0x36, 0xB0])    # 14000 mV = 14.0 V
            raise CommunicationError("unsupported")

    col = Collector(cfg, repo)
    col.conn = _CarConn()
    col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    col._cells_readable = False
    snap = col.collect_once()
    assert snap.get("speed_kmh") == 38.0
    assert snap.get("lv_voltage_v") == pytest.approx(14.0, abs=0.01)
    keys = {r["key"] for r in repo.conn.execute(
        "SELECT key FROM measurements WHERE success=1")}
    assert {"vehicle_speed", "lv_voltage_obd"} <= keys
    series = repo.measurement_series(col.vehicle_id, "vehicle_speed")
    assert len(series) == 1 and series[0]["value"] == 38.0


def test_concurrent_threads_do_not_lose_writes(repo):
    """The dashboard serves sync handlers on a threadpool while the collector
    thread writes.

    A single shared sqlite3.Connection cannot be driven from two threads: the
    interleaved use raised InterfaceError and silently discarded writes, so a
    scan measured only a fraction of what was written. Connections are
    per-thread, so every write must land.
    """
    import threading
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    errors = []

    def writer(n):
        try:
            for i in range(150):
                repo.record_measurement(vid, "2026-09-01T10:00:00+00:00",
                                        f"key{n}", "bms", "UDS-0x22", "0x1E3B",
                                        "V", "reported", "builtin", "documented",
                                        "AABB", float(i))
        except Exception as exc:            # pragma: no cover - diagnostics
            errors.append(repr(exc))

    def reader(_n):
        try:
            for _ in range(150):
                repo.latest_measurements(vid)
        except Exception as exc:            # pragma: no cover - diagnostics
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=reader, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"thread errors: {errors[:3]}"
    total = repo.conn.execute(
        "SELECT COUNT(*) FROM measurements WHERE key LIKE 'key%'"
    ).fetchone()[0]
    assert total == 600, f"lost writes: {total}/600"


def test_each_thread_gets_a_connection_with_foreign_keys_on(repo):
    """foreign_keys is per-connection, so a worker thread must set it too."""
    import threading
    seen = {}

    def check():
        seen["fk"] = repo.conn.execute("PRAGMA foreign_keys").fetchone()[0]
        seen["distinct"] = repo.conn is not repo.conn

    t = threading.Thread(target=check)
    t.start()
    t.join()
    assert seen["fk"] == 1, "foreign keys silently off on a worker connection"


def test_schema_mismatch_refuses_to_write_and_preserves_evidence(tmp_path):
    """An older database must be reported, not silently restamped as current.

    CREATE TABLE IF NOT EXISTS leaves existing tables alone, so a version
    mismatch means the columns this build reads may not exist. Stamping the
    current version over the old one destroyed the only sign of divergence.
    """
    import sqlite3 as sq
    from database.repository import Repository, SchemaMismatchError
    db = tmp_path / "old.db"
    repo = Repository(db)
    repo.conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
    repo.conn.commit()
    repo.close()
    with pytest.raises(SchemaMismatchError) as e:
        Repository(db)
    assert "version 0" in str(e.value) and "expects 1" in str(e.value)
    still = sq.connect(db).execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert still == "0", "refusal overwrote the recorded version"


def test_prune_keeps_values_and_drops_only_stale_raw_bytes(repo):
    """Default pruning must not disturb anything the reports read."""
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(
        timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ts in (old, new):
        repo.record_measurement(vid, ts, "pack_voltage", "bat_mgmt", "UDS-0x22",
                                "0x1E3B", "V", "reported", "builtin",
                                "documented", "DEADBEEF", 350.0)
    removed = repo.prune(days=90)
    assert removed["measurements_raw"] == 1
    rows = repo.conn.execute(
        "SELECT raw_response, value FROM measurements ORDER BY ts").fetchall()
    assert len(rows) == 2, "a measurement row was deleted"
    assert rows[0]["raw_response"] is None, "stale raw bytes kept"
    assert rows[0]["value"] == 350.0, "parsed value lost with the raw bytes"
    assert rows[1]["raw_response"] == "DEADBEEF", "recent raw bytes discarded"


def test_hard_prune_removes_old_rows_but_keeps_identity(repo):
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(
        timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ts in (old, new):
        repo.record_measurement(vid, ts, "pack_voltage", "bat_mgmt", "UDS-0x22",
                                "0x1E3B", "V", "reported", "builtin",
                                "documented", "DEADBEEF", 350.0)
    removed = repo.prune(days=90, keep_raw=False)
    assert removed["measurements"] == 1
    left = repo.conn.execute("SELECT ts FROM measurements").fetchall()
    assert [r["ts"] for r in left] == [new]
    assert repo.conn.execute("SELECT id FROM vehicles WHERE id=?",
                             (vid,)).fetchone() is not None, "vehicle pruned"


def test_prune_never_deletes_the_safety_manifest(repo):
    """Regression: `prune --hard` deleted tx_log rows.

    tx_log is the record of every request this tool ever put on the vehicle's
    diagnostic port -- the evidence that the read-only guarantee held. It was
    listed alongside the analytics tables, so the destructive path removed it,
    while main.py's own summary line went on claiming only "sessions, DTCs and
    reports" were protected. Both modes must now leave it alone.
    """
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    old = (datetime.now(timezone.utc) - timedelta(days=500)).isoformat(
        timespec="seconds")
    repo.log_tx(old, direction="TX", ecu="bat_mgmt", payload="221E3B",
                purpose="read-only DID probe")
    repo.record_measurement(vid, old, "pack_voltage", "bat_mgmt", "UDS-0x22",
                            "0x1E3B", "V", "reported", "builtin", "documented",
                            "DEADBEEF", 350.0)

    for keep_raw in (True, False):
        repo.prune(days=1, keep_raw=keep_raw)
        left = repo.conn.execute("SELECT payload FROM tx_log").fetchall()
        assert len(left) == 1, f"tx_log pruned with keep_raw={keep_raw}"
        assert left[0]["payload"] == "221E3B"

    assert repo.prune(days=0, keep_raw=False)["tx_log_kept"] is True


def test_backup_produces_a_readable_consistent_snapshot(tmp_path):
    """The snapshot must be a real database, not a file copy of a WAL."""
    import sqlite3
    src = tmp_path / "live.db"
    repo = Repository(src)
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    for i in range(25):
        repo.record_measurement(vid, f"2026-10-05T10:{i:02d}:00+00:00",
                                "pack_voltage", "bat_mgmt", "UDS-0x22", "0x1E3B",
                                "V", "reported", "builtin", "documented",
                                "ABCD", 350.0 + i)
    repo.log_tx("2026-10-05T10:00:00+00:00", direction="TX", ecu="bat_mgmt",
                payload="221E3B", purpose="read-only DID probe")

    dest = repo.backup(tmp_path / "snapshots" / "copy.db")
    assert dest.exists(), "parent directory was not created"

    # Readable by a completely separate connection, with the same rows.
    con = sqlite3.connect(str(dest))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 25
        assert con.execute("SELECT COUNT(*) FROM tx_log").fetchone()[0] == 1
    finally:
        con.close()

    # Backup twice: the second must overwrite cleanly, not append or fail.
    repo.backup(dest)
    repo.close()


def test_logs_rotate_instead_of_growing_without_bound(tmp_path, monkeypatch):
    """A detached collector's only record was a scrollback buffer nobody reads,
    and the launcher's capture file only ever grew (182 KB after a day). main.py
    now owns a rotating log per component."""
    import logging
    import logging.handlers

    import main as main_mod

    monkeypatch.setattr(main_mod, "LOG_ROTATE_BYTES", 4096)
    monkeypatch.setattr(main_mod, "LOG_BACKUP_COUNT", 2)
    monkeypatch.setenv("ODB_STDERR_CAPTURED", "1")

    root_logger = logging.getLogger()
    saved = list(root_logger.handlers)
    root_logger.handlers.clear()
    try:
        main_mod.configure_logging("collector", log_dir=tmp_path / "logs")
        handlers = root_logger.handlers
        rotating = [h for h in handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert rotating, f"expected a rotating handler, got {handlers}"
        assert rotating[0].maxBytes == 4096
        assert rotating[0].backupCount == 2
        assert rotating[0].baseFilename.endswith("collector.log")

        # No console handler when the launcher already captured stderr, so the
        # same lines are not written twice.
        assert not [h for h in handlers
                    if isinstance(h, logging.StreamHandler)
                    and not isinstance(h, logging.handlers.RotatingFileHandler)]

        # Write well past the limit and prove the file stays bounded and rolls.
        for i in range(400):
            logging.getLogger("t").info("x" * 200 + f" {i}")
        for h in list(handlers):
            h.flush()

        path = Path(rotating[0].baseFilename)
        assert path.stat().st_size <= 4096, "log file exceeded its rotation limit"
        rolled = sorted(p.name for p in path.parent.glob("collector.log.*"))
        assert rolled, "nothing rolled over"
        assert len(rolled) <= 2, f"backup count ignored: {rolled}"
        # Total on-disk log size stays bounded too, not just the current file.
        total = sum(p.stat().st_size for p in path.parent.glob("collector.log*"))
        assert total <= 4096 * 3, f"logs unbounded overall: {total} bytes"
    finally:
        for h in list(root_logger.handlers):
            h.close()
        root_logger.handlers.clear()
        for h in saved:
            root_logger.addHandler(h)


def test_logging_survives_an_unwritable_log_directory(tmp_path, monkeypatch):
    """A read-only install or a locked file must not stop the collector."""
    import logging

    import main as main_mod

    monkeypatch.delenv("ODB_STDERR_CAPTURED", raising=False)
    root_logger = logging.getLogger()
    saved = list(root_logger.handlers)
    root_logger.handlers.clear()
    # Collected by hand rather than via caplog: the warning goes to the root
    # logger through root.warning(), which caplog does not intercept once
    # configure_logging has replaced the handler chain underneath it.
    warnings: list[str] = []
    capture = logging.Handler()
    capture.emit = lambda record: warnings.append(record.getMessage())
    root_logger.addHandler(capture)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    try:
        main_mod.configure_logging("collector", log_dir=blocker)
        # A console handler must still be there, and the failure must be said
        # out loud rather than swallowed.
        assert [h for h in root_logger.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)], "console logging lost"
        assert any("log file" in w.lower() for w in warnings), (
            f"failure not reported: {warnings}")
    finally:
        for h in list(root_logger.handlers):
            h.close()
        root_logger.handlers.clear()
        for h in saved:
            root_logger.addHandler(h)


def test_command_names_map_to_the_documented_log_files():
    """The .bat shortcuts and start_scanner tell users to look at
    data/logs/collector.log; renaming them would break that."""
    import main as main_mod
    assert main_mod.LOG_COMPONENT["collect"] == "collector"
    assert main_mod.LOG_COMPONENT["serve"] == "web"
    assert main_mod.LOG_COMPONENT["simulate"] == "simulator"


def test_backup_dir_is_configurable_and_gitignored(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "data/backups/" in ignored
    assert "backup_dir" in (root / "config.yaml").read_text(encoding="utf-8")


def test_config_missing_base_file_yields_defaults(tmp_path):
    cfg = load_config(tmp_path / "absent.yaml", tmp_path / "absent.local.yaml")
    assert cfg.get("anything.at.all") is None


# --- dashboard access control --------------------------------------------------
def _auth_cfg(tmp_path, host):
    (tmp_path / "c.yaml").write_text(
        f"database:\n  path: {tmp_path / 'd.db'}\n"
        f"web:\n  host: \"{host}\"\n  port: 8000\n", encoding="utf-8")
    return load_config(tmp_path / "c.yaml", tmp_path / "local.yaml")


def test_network_bind_requires_a_token(tmp_path, monkeypatch):
    """config.yaml ships web.host 0.0.0.0 so a tablet can reach the dashboard.

    Nothing authenticated it: the VIN, the fault history and POST /ai/ask were
    all reachable by anything on the network. A network-reachable bind must
    refuse everything without the token, and the refusal must not leak the
    page it is protecting.
    """
    import web.dashboard as dash
    from fastapi.testclient import TestClient
    from web.auth import is_loopback

    token = "s3cret-token-value"
    # Host-aware, like the real resolver: a loopback bind must need no token.
    monkeypatch.setattr(dash, "resolve_token",
                        lambda c, h: None if is_loopback(h) else token)

    cfg = _auth_cfg(tmp_path, "0.0.0.0")
    client = TestClient(dash.create_app(cfg, bind_host="0.0.0.0"),
                        raise_server_exceptions=False)

    for path in ("/", "/battery", "/dtcs", "/charging", "/ai",
                 "/api/health", "/api/battery"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} served without a token"
        assert "WVWZZZE1ZMP" not in r.text, f"{path} leaked vehicle data"
    # POST must be gated too, or the endpoint is merely hidden behind 401 on GET.
    assert client.post("/ai/ask", data={"question": "hi"}).status_code == 401

    assert client.get("/?k=wrong").status_code == 401
    assert client.get("/?k=" + token + "x").status_code == 401


def test_valid_token_is_exchanged_for_a_cookie(tmp_path, monkeypatch):
    import web.dashboard as dash
    from fastapi.testclient import TestClient

    token = "s3cret-token-value"
    monkeypatch.setattr(dash, "resolve_token", lambda c, h: token)
    cfg = _auth_cfg(tmp_path, "0.0.0.0")
    client = TestClient(dash.create_app(cfg, bind_host="0.0.0.0"),
                        raise_server_exceptions=False)

    r = client.get("/?k=" + token, follow_redirects=False)
    assert r.status_code == 303
    # The token must not stay in the address bar: that is browser history,
    # bookmarks, screenshots and the Referer of every outbound link.
    assert "k=" not in (r.headers.get("location") or "")
    assert "odb_token" in r.cookies
    # HttpOnly: unreadable from page JavaScript, which is what an XSS would use.
    assert "HttpOnly" in r.headers.get("set-cookie", "")

    # The cookie alone is then enough -- no token repeated in every URL.
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_loopback_bind_needs_no_token(tmp_path, monkeypatch):
    """Nothing else can open a loopback socket, so there is nobody to auth."""
    import web.dashboard as dash
    from fastapi.testclient import TestClient
    from web.auth import is_loopback

    monkeypatch.setattr(dash, "resolve_token",
                        lambda c, h: None if is_loopback(h) else "tok")
    cfg = _auth_cfg(tmp_path, "127.0.0.1")
    client = TestClient(dash.create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False)
    assert client.get("/").status_code == 200
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("host,loopback", [
    ("127.0.0.1", True), ("127.1.2.3", True), ("::1", True),
    ("localhost", True), ("0.0.0.0", False), ("192.168.1.50", False),
    ("", False), ("10.0.0.5", False),
])
def test_loopback_detection(host, loopback):
    from web.auth import is_loopback
    assert is_loopback(host) is loopback


def test_security_headers_are_present(tmp_path, monkeypatch):
    import web.dashboard as dash
    from fastapi.testclient import TestClient

    monkeypatch.setattr(dash, "resolve_token", lambda c, h: None)
    cfg = _auth_cfg(tmp_path, "127.0.0.1")
    client = TestClient(dash.create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False)
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert "no-store" in h["Cache-Control"]
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]


def test_generated_token_is_written_to_the_config_that_was_loaded(tmp_path):
    """Regression risk: the token must not land in the repository's own
    config.local.yaml when a different config was loaded -- that would share
    one machine's dashboard secret with every other configuration."""
    from web.auth import resolve_token

    cfg = _auth_cfg(tmp_path, "0.0.0.0")
    token = resolve_token(cfg, "0.0.0.0")
    assert token and len(token) >= 32

    written = tmp_path / "local.yaml"
    assert written.exists(), "token was not persisted"
    assert token in written.read_text(encoding="utf-8")
    # And nowhere near the repository's real settings file.
    assert not (Path(__file__).resolve().parents[1] / "config.local.yaml").exists() \
        or token not in (Path(__file__).resolve().parents[1]
                         / "config.local.yaml").read_text(encoding="utf-8")

    # Second call must reuse the persisted token rather than minting another,
    # otherwise the tablet is logged out on every restart.
    assert resolve_token(cfg, "0.0.0.0") == token


def test_configured_token_is_used_verbatim(tmp_path, monkeypatch):
    from web.auth import resolve_token
    monkeypatch.delenv("ODB_TOKEN", raising=False)
    cfg = _auth_cfg(tmp_path, "0.0.0.0")
    cfg._data["web"]["auth_token"] = "  operator-chosen  "
    assert resolve_token(cfg, "0.0.0.0") == "operator-chosen"
    assert not (tmp_path / "local.yaml").exists(), "overwrote an explicit token"


def test_env_token_overrides_generation(tmp_path, monkeypatch):
    from web.auth import resolve_token
    cfg = _auth_cfg(tmp_path, "0.0.0.0")
    monkeypatch.setenv("ODB_TOKEN", "from-environment")
    assert resolve_token(cfg, "0.0.0.0") == "from-environment"
    assert not (tmp_path / "local.yaml").exists()


def test_loopback_bind_never_generates_a_token_file(tmp_path):
    from web.auth import resolve_token
    cfg = _auth_cfg(tmp_path, "127.0.0.1")
    assert resolve_token(cfg, "127.0.0.1") is None
    assert not (tmp_path / "local.yaml").exists()


def test_ai_question_length_is_capped(tmp_path, monkeypatch):
    """The question is the only unbounded user input, and it goes into an LLM
    prompt that occupies a GPU for minutes on the default model."""
    import web.dashboard as dash
    from fastapi.testclient import TestClient

    monkeypatch.setattr(dash, "resolve_token", lambda c, h: None)
    cfg = _auth_cfg(tmp_path, "127.0.0.1")
    client = TestClient(dash.create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False)
    asked = []
    monkeypatch.setattr(dash.svc_cls if hasattr(dash, "svc_cls") else
                        dash.AnalysisService, "ask",
                        lambda self, q: asked.append(q) or
                        {"report": "ok", "warnings": [], "context": {}})
    r = client.post("/ai/ask", data={"question": "x" * 5000})
    assert r.status_code == 400
    assert not asked, "an oversized question reached the model"
    assert client.post("/ai/ask", data={"question": "   "}).status_code == 400


def test_dashboard_closes_its_database_on_shutdown(tmp_path):
    """Repository opened a connection per thread and never closed it, so every
    reload leaked handles and held the SQLite file open -- which is what made
    `main.py prune` fail with PermissionError while the dashboard was running."""
    import web.dashboard as dash
    from fastapi.testclient import TestClient

    closed = []
    real_repo = dash.Repository

    class SpyRepo(real_repo):
        def close(self):
            closed.append(True)
            super().close()

    monkey = dash.Repository
    dash.Repository = SpyRepo
    try:
        cfg = _auth_cfg(tmp_path, "127.0.0.1")
        dash.resolve_token = lambda c, h: None
        app = dash.create_app(cfg, bind_host="127.0.0.1")
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/").status_code == 200
            assert not closed, "closed before shutdown"
        assert closed, "Repository.close() was never called on shutdown"
    finally:
        dash.Repository = monkey


def test_dashboard_windows_come_from_config(monkeypatch):
    """Routes used to hardcode 30/90 and ignore analysis.trend_window_days, so
    the dashboard, the charts and `main.py analyze` could disagree."""
    import web.dashboard as dash
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "win.db")
        seed = Repository(db_path)
        seed.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
        seed.close()          # routes short-circuit on `if vid`, so a row is needed
        cfg = load_config()
        cfg._data["database"]["path"] = db_path
        cfg._data["analysis"]["trend_window_days"] = 7
        cfg._data["analysis"]["charging_window_days"] = 11

        seen: dict[str, list[int]] = {}
        real = dash.Repository

        class SpyRepo(real):
            def battery_history(self, days=30, vehicle_id=None):
                seen.setdefault("trend", []).append(days)
                return super().battery_history(days, vehicle_id)

            def charging_sessions(self, days=90, vehicle_id=None):
                seen.setdefault("charging", []).append(days)
                return super().charging_sessions(days, vehicle_id)

        monkeypatch.setattr(dash, "Repository", SpyRepo)
        with TestClient(dash.create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False) as client:
            for path in ("/", "/battery", "/charging", "/api/battery"):
                assert client.get(path).status_code == 200, path

    assert seen["trend"] and set(seen["trend"]) == {7}, seen
    assert seen["charging"] and set(seen["charging"]) == {11}, seen


@pytest.mark.slow
def test_simulator_end_to_end():
    """Full pipeline against the simulated ELM327: VIN, DID reads, DTCs."""
    from simulator.vehicle import SimServer
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu

    with SimServer("127.0.0.1", 35123):
        t = Elm327Transport(host="127.0.0.1", tcp_port=35123, timeout=5.0)
        c = DiagnosticConnection(t)
        c.open()
        assert c.read_vin() == "WVWZZZE1ZMP087053"
        bms = get_ecu("bat_mgmt")
        raw = c.read_did(bms, 0x1E3B)
        assert len(raw) == 2
        mgmt = get_ecu("chg_mgmt")
        dtcs = c.read_dtcs_uds(mgmt)
        assert dtcs[0]["code"] == "U112300"
        # freeze-frame snapshot for the stored DTC; unknown codes get NRC
        snaps = c.read_dtc_snapshots("U112300")
        assert snaps, "simulator must snapshot its stored DTC"
        entry = next(iter(snaps.values()))[0]
        assert entry["code"] == "U112300"
        rec = entry["records"][0]
        assert rec["identifiers"] == [0x1E3B] and rec["data"]
        assert c.read_dtc_snapshots("P0A8000") == {}
        # unavailable DID -> NRC 0x31 -> try_read_did returns None
        assert c.try_read_did(bms, 0xFFFF) is None
        c.close()


@pytest.mark.slow
def test_simulator_meb_addressing_end_to_end():
    """The whole MEB path against the simulated adapter: negotiate ATCP/ATSH,
    read BMS DIDs at 0x17FC007B, the 11-bit modules at their own ids, and
    check functional OBD-II still works afterwards."""
    from simulator.vehicle import SimServer
    from diagnostic.elm327 import Elm327Transport
    from diagnostic.connection import DiagnosticConnection
    from diagnostic.ecus import get_ecu
    from decoders.meb import register_meb
    from decoders.registry import DIDRegistry

    with SimServer("127.0.0.1", 35124):
        t = Elm327Transport(host="127.0.0.1", tcp_port=35124, timeout=5.0)
        c = DiagnosticConnection(t)
        try:
            c.open()
            assert t.meb_addressing is True, "simulator must accept MEB addressing"

            reg = DIDRegistry()
            register_meb(reg)
            bms = get_ecu("bat_mgmt")
            soc = reg.get("soc_abs").decode_value(c.read_did(bms, 0x028C))
            assert 50.0 < soc < 80.0, soc
            volt = reg.get("pack_voltage").decode_value(c.read_did(bms, 0x1E3B))
            assert 300.0 < volt < 400.0, volt

            # A second module: physical address switches, then answers.
            energy = reg.get("hv_energy_max").decode_value(
                c.read_did(get_ecu("energy"), 0x2AB2))
            assert energy == pytest.approx(53200.0, rel=0.01)
            # Reaching the 11-bit energy module means leaving the 29-bit bus.
            assert t._proto == "6", "an 11-bit module must switch to protocol 6"
            odo = reg.get("odometer_km").decode_value(
                c.read_did(get_ecu("veh_info"), 0x295A))
            assert odo > 0
            dcdc_v = reg.get("dcdc_voltage").decode_value(
                c.read_did(get_ecu("dcdc"), 0x465D))
            assert 10.0 < dcdc_v < 16.0, dcdc_v

            # The 11-bit modules: ATCP 00 + ATSH 000710/000746, answering at
            # 0x77A / 0x7B0.
            content = reg.get("hv_energy_content").decode_value(
                c.read_did(get_ecu("energy"), 0x2AB8))
            assert content == pytest.approx(41200.0, rel=0.01)
            outside = reg.get("outside_temp").decode_value(
                c.read_did(get_ecu("climate"), 0x2609))
            assert outside == pytest.approx(14.0, abs=0.2)

            # Back onto the 29-bit bus for the BMS, protocol included.
            coolant = reg.get("coolant_temps").decode_value(
                c.read_did(bms, 0x189D))
            assert coolant["outlet_c"] > coolant["inlet_c"]
            assert t._proto == "7", "the 29-bit bus needs protocol 7 back"

            # Functional reads must still work after all that module switching.
            assert c.read_vin() == "WVWZZZE1ZMP087053"
            assert c.try_mode01(0x42) is not None
        finally:
            # Always drop the socket: a failed assertion must not leave the
            # simulator's connection thread parked in recv().
            c.close()


# --- AI layer -------------------------------------------------------------------
@pytest.mark.slow
def test_report_validator():
    from ai.reports import validate_report
    text = ("VEHICLE DIAGNOSTIC REPORT\nOBSERVATION\nThe battery is definitely "
            "defective.\nHYPOTHESES\n1. x\nLIMITATIONS\nnone")
    amended, warnings = validate_report(text)
    assert any("definitive" in w for w in warnings)
    assert "AUTOMATIC CAVEATS" in amended


def test_prompt_structure():
    from ai import prompts
    ctx = prompts.build_context({"vin": "X"}, {"cell_delta": {}}, {}, {}, {},
                                [], [], [])
    prompt = prompts.interpret_prompt(ctx, "test question")
    assert "test question" in prompt
    assert "EVIDENCE" in prompt


# --- web layer ------------------------------------------------------------------
def test_collector_adopts_a_session_left_open_by_a_previous_run(repo, tmp_path):
    """Restarting mid-charge must not orphan the open session.

    open_session_id() existed but nothing called it, so a restarted collector
    started with no active session and opened a second one while the first
    stayed 'open' forever -- two concurrent sessions on the dashboard, and the
    orphan never got a duration or energy figure.
    """
    from collector import Collector
    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(
        timespec="seconds")
    existing = repo.open_session(vid, ts, "DC", 40.0)

    col = Collector(cfg, repo)
    col.vehicle_id = vid
    col._adopt_open_session()
    assert col._active_session_id == existing
    # and it must not open a second one
    col._charging_tracking(ts, {"charge_mode": 2, "soc_normal": 41.0})
    assert col._active_session_id == existing
    open_rows = repo.conn.execute(
        "SELECT COUNT(*) FROM charging_sessions WHERE vehicle_id=? AND "
        "status='open'", (vid,)).fetchone()[0]
    assert open_rows == 1


def test_unknown_charge_mode_does_not_close_a_live_session(repo, tmp_path):
    """A missed read is not evidence that charging stopped."""
    from collector import Collector
    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sid = repo.open_session(vid, ts, "DC", 40.0)
    col = Collector(cfg, repo)
    col._active_session_id = sid

    # charge_mode absent from the snapshot and absent from recent history
    col._charging_tracking(ts, {"soc_normal": 42.0})
    still_open = repo.conn.execute(
        "SELECT status FROM charging_sessions WHERE id=?", (sid,)).fetchone()
    assert still_open["status"] == "open", "session closed on an unknown mode"
    assert col._active_session_id == sid


def test_stale_charge_mode_is_not_used_to_close_a_session(repo, tmp_path):
    """The charge_mode fallback must respect the same age bound as the snapshot."""
    from collector import Collector
    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(
        timespec="seconds")
    # a charge_mode of 1 from three days ago must not reopen a session
    repo.record_measurement(vid, stale, "charge_mode", "bms", "UDS-0x22",
                            "0x1E02", "", "reported", "builtin", "documented",
                            "", 1.0)
    col = Collector(cfg, repo)
    col.vehicle_id = vid
    col._charging_tracking(stale, {"soc_normal": 50.0})
    assert col._active_session_id is None, "stale charge_mode opened a session"
    open_rows = repo.conn.execute(
        "SELECT COUNT(*) FROM charging_sessions WHERE vehicle_id=? AND "
        "status='open'", (vid,)).fetchone()[0]
    assert open_rows == 0


def test_cell_sweep_skipped_when_battery_dids_are_refused(repo, tmp_path):
    """Real-car finding (ID.3, 2026-10): battery DIDs answer NRC-31 through
    the gateway's OBD surface -- the BMS is not exposed. The 102-DID cell
    sweep must then be skipped instead of re-collecting guaranteed failures
    on every slow phase, and the reason must be recorded as an event.
    """
    from collector import Collector
    from diagnostic.interface import CommunicationError
    from diagnostic.uds import NegativeResponseError

    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})

    class _RefusingConn:
        """Every functional probe answers NRC requestOutOfRange."""
        def functional_probe(self, hexcmd, purpose, ecu=None):
            return {0x18DAF10A: bytes([0x7F, 0x22, 0x31])}

        def read_did(self, ecu, did):
            raise NegativeResponseError(0x22, 0x31)

        def mode01(self, pid):
            raise CommunicationError("no data")

    col = Collector(cfg, repo)
    col.conn = _RefusingConn()
    col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")

    results = col.discover_ecus()
    assert results["bat_mgmt"] == "nrc-31"
    assert col._cells_readable is False
    notes = [r["description"] for r in repo.conn.execute(
        "SELECT description FROM diagnostic_events WHERE vehicle_id=? "
        "AND kind='note'", (col.vehicle_id,)).fetchall()]
    assert any("Cell sweep disabled" in n for n in notes), notes

    # ...and one collection pass must not touch a single cell DID
    polled: list[str] = []
    col.poll_did = lambda spec, ts: polled.append(spec.key)
    col.collect_once()
    assert polled, "slow pass should still poll non-cell DIDs"
    assert not [k for k in polled if k.startswith("cell_v_")], polled


def test_collector_cadence_matches_the_configured_period():
    """The period must be the configured one, not the interval plus the work."""
    import time as _time
    from collector import Collector

    class _FakeCollector(Collector):
        def open_and_identify(self): return "ok"
        def discover_ecus(self): return []
        def collect_once(self):
            _time.sleep(0.10)          # stand in for a real cycle
            return {}
        def read_and_store_dtcs(self): return []

    cfg = Config({"collector": {"poll_interval": 0.25, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": ":memory:"}})
    col = _FakeCollector(cfg, Repository(":memory:"))
    start = _time.monotonic()
    col.run(max_cycles=4)
    elapsed = _time.monotonic() - start
    # Three gaps at 0.25 s = 0.75 s. Sleeping the interval *after* each 0.10 s
    # of work would give ~1.05 s instead.
    assert 0.70 <= elapsed < 0.95, f"cadence drifted: {elapsed:.2f}s"


@pytest.mark.parametrize("populate", [False, True],
                         ids=["empty-db", "with-vehicle"])
def test_dashboard_pages_render(populate):
    """Every page must render with no vehicle row *and* with one.

    Guards the overview cell-delta card: `battery` is {} without a vehicle, so
    any `battery.cell_delta.<attr>` chain raises UndefinedError -- an
    `is defined` guard cannot help, because evaluating it walks the chain too.
    """
    from web.dashboard import create_app
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "web.db")
        seed = Repository(db_path)
        if populate:
            seed.ensure_vehicle("WVWZZZE1ZMP087053", year=2021)
        seed.close()

        cfg = load_config()
        cfg._data["database"]["path"] = db_path
        # `with` so the app's lifespan shutdown runs and releases the SQLite
        # handle create_app opened. On Windows an open file cannot be deleted,
        # so a bare TestClient left the temp dir undeletable at teardown --
        # these four tests used to fail on PermissionError rather than on
        # anything about the dashboard.
        with TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False) as client:
            for path in ("/", "/battery", "/dtcs", "/charging", "/ai"):
                assert client.get(path).status_code == 200, path


def test_ai_ask_on_empty_db_does_not_500(monkeypatch, cfg):
    """POST /ai/ask with a reachable LLM and no vehicle row must return a page.

    Regression: the route only caught OllamaError, so while Ollama was *down*
    the `vehicle_row["id"]` TypeError was masked; with Ollama up it surfaced
    as a 500.
    """
    from ai.ollama import OllamaClient
    from web.dashboard import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        OllamaClient, "generate",
        lambda self, prompt, system=None, temperature=None:
            "No faults recorded.\n\nLIMITATIONS\n- none",
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "ai_empty.db")
        with Repository(db_path):        # schema only, no vehicle
            pass
        cfg._data["database"]["path"] = db_path
        with TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False) as client:
            resp = client.post("/ai/ask",
                               data={"question": "How is my battery?"})

    assert resp.status_code == 200, resp.status_code
    assert "NOT PERSISTED" in resp.text


# --- collector cadence ----------------------------------------------------------
@pytest.fixture
def offline_collector():
    """Factory for Collectors wired to a TCP adapter that is never opened, so
    the slow-phase gate can be exercised without any I/O.

    Closes every repository it made on teardown. Windows cannot delete an open
    file, so a leaked handle made the temporary directory undeletable and these
    tests failed at cleanup with PermissionError instead of testing anything.
    """
    from collector import Collector
    made: list = []

    def _make(slow_interval: float, tmp) -> "object":
        cfg = load_config()
        cfg._data["database"]["path"] = os.path.join(str(tmp), "cadence.db")
        cfg._data["adapter"]["type"] = "elm327_tcp"
        cfg._data["collector"]["slow_poll_interval"] = slow_interval
        col = Collector(cfg, Repository(cfg._data["database"]["path"]))
        made.append(col)
        return col

    yield _make
    for col in made:
        col.repo.close()


def test_slow_phase_is_gated_not_every_cycle(offline_collector, tmp_path):
    """The 107 slow DIDs must respect collector.slow_poll_interval instead of
    being re-read on every fast poll."""
    col = offline_collector(60.0, tmp_path)
    assert col._slow_phase_due() is True, "first pass must always run"
    assert col._slow_phase_due() is False, "must not re-run immediately"
    assert col._slow_phase_due() is False
    col._last_slow -= 61.0          # pretend 61s elapsed
    assert col._slow_phase_due() is True, "must re-run after the interval"


def test_slow_phase_due_just_after_boot(offline_collector, tmp_path):
    """time.monotonic() starts near 0 on a freshly booted host; the slow phase
    must still run on the very first pass."""
    col = offline_collector(60.0, tmp_path)
    assert col._last_slow < 0, "clock starts negative-relative, not at 0"
    assert col._slow_phase_due() is True


def test_slow_pass_excludes_cell_dids(offline_collector, tmp_path):
    """_sweep_cells() reads the per-cell DIDs; _slow_pass() must not read them
    again in the same phase, or every cell DID is polled twice per cycle."""
    col = offline_collector(60.0, tmp_path)
    cell_keys = {s.key for s in col.registry.all()
                 if s.key.startswith("cell_v_")}
    assert cell_keys, "expected per-cell DIDs to be registered"
    polled: list[str] = []
    col.poll_did = lambda spec, ts: polled.append(spec.key)
    col._slow_pass("ts", {})
    assert polled, "slow pass polled nothing"
    assert not (cell_keys & set(polled)), "cell DIDs double-polled"
    assert len(polled) == len(set(polled)), "a slow DID was polled twice"





# --- collector reliability --------------------------------------------------------
def _tcp_collector(tmp_path, port, timeout=0.2):
    """A Collector pointed at a TCP adapter, so it exercises the real transport
    path -- including its failure path -- without any hardware."""
    from collector import Collector
    cfg = load_config()
    cfg._data["database"]["path"] = str(tmp_path / "dead.db")
    cfg._data["adapter"]["type"] = "elm327_tcp"
    cfg._data["adapter"]["tcp_port"] = port
    cfg._data["adapter"]["timeout"] = timeout
    cfg._data["collector"]["poll_interval"] = 0.05
    cfg._data["collector"]["slow_poll_interval"] = 0.05
    # The per-cell sweep is 100+ DIDs per slow phase. It adds nothing to what
    # these tests check and dominates their runtime.
    cfg._data["collector"]["read_cell_voltages"] = False
    col = Collector(cfg, Repository(cfg._data["database"]["path"]))
    col.vehicle_id = col.repo.ensure_vehicle("WVWZZZE1ZMP087053")
    col.vin = "WVWZZZE1ZMP087053"
    return col


def test_dead_adapter_is_distinguished_from_a_quiet_vehicle(tmp_path):
    """Every read swallows CommunicationError, so an unplugged adapter used to
    look exactly like a sleeping car: collect_once returned {} forever and the
    loop spun at poll_interval writing a failed row for every registered DID
    (146 rows per 3 cycles measured). run() must tell the two apart, because
    only one of them is worth reconnecting for."""
    from collector import DEAD_CYCLE_MIN_ATTEMPTS

    col = _tcp_collector(tmp_path, 39981)          # nothing is listening
    try:
        assert col._cycle_is_dead(col.collect_once()) is True

        # A vehicle that is merely refusing DIDs is NOT a dead adapter.
        col._cycle_attempts, col._cycle_failures = 10, 3
        assert col._cycle_is_dead({}) is False, "refused DIDs read as dead"
        # Nor one that answered something.
        col._cycle_attempts, col._cycle_failures = 10, 0
        assert col._cycle_is_dead({"pack_voltage": 350.0}) is False
        # Nor a cycle too small to judge -- a car implementing none of the
        # registered DIDs must not cause a reconnect storm.
        col._cycle_attempts = col._cycle_failures = DEAD_CYCLE_MIN_ATTEMPTS - 1
        assert col._cycle_is_dead({}) is False
    finally:
        col.repo.close()


def test_run_backs_off_instead_of_spinning_on_a_dead_adapter(tmp_path,
                                                             monkeypatch, caplog):
    """Regression: a dead adapter produced an endless tight loop of failed
    cycles, with no error surfaced and no attempt to reconnect. It must now
    stop cycling, say why, and back off.

    The meaningful property is not how many cycles ran -- a bounded run still
    terminates on its budget -- but that the run spent real time WAITING. The
    old loop slept nothing at all between failed cycles.
    """
    from collector import RECONNECT_BACKOFF_S

    col = _tcp_collector(tmp_path, 39982)
    slept: list[float] = []
    attempts = {"n": 0}

    monkeypatch.setattr(col, "read_and_store_dtcs",
                        lambda: pytest.fail("DTC pass on a dead link"))
    monkeypatch.setattr("collector.time.sleep", lambda s: slept.append(s))
    monkeypatch.setattr(col.conn, "close", lambda: None)

    def fake_open():
        attempts["n"] += 1
        from diagnostic.interface import AdapterNotFoundError
        raise AdapterNotFoundError("still absent")

    monkeypatch.setattr(col.conn, "open", fake_open)
    monkeypatch.setattr(col, "open_and_identify", lambda: "WVWZZZE1ZMP087053")

    import logging
    with caplog.at_level(logging.ERROR):
        col.run(max_cycles=12)

    assert attempts["n"] >= 3, "never tried to reconnect"
    # One reconnect per dead cycle, not several: no extra spinning between them.
    assert attempts["n"] == 12, f"unexpected attempt count: {attempts['n']}"
    # Backoff grows and is bounded.
    assert slept[:4] == sorted(slept[:4]), f"backoff not monotonic: {slept[:5]}"
    assert all(s > 0 for s in slept), "busy-looped during recovery"
    assert slept[0] == pytest.approx(RECONNECT_BACKOFF_S)
    assert slept[1] > slept[0], "backoff never grew"
    from collector import RECONNECT_MAX_BACKOFF_S
    assert max(slept) <= RECONNECT_MAX_BACKOFF_S + 1e-6, f"unbounded: {max(slept)}"
    # The whole point: it waited, rather than cycling flat out.
    assert sum(slept) > 60, f"did not back off, only slept {sum(slept):.0f}s total"
    # And the operator is told, rather than left watching an empty dashboard.
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "answered nothing" in text or "Adapter stopped answering" in text
    # Matched on the description, not merely kind='adapter': open_and_identify
    # already writes a kind='adapter' row for every successful connect, so a
    # kind-only check would pass even if the loss were never recorded.
    assert col.repo.conn.execute(
        "SELECT 1 FROM diagnostic_events WHERE description LIKE '%stopped answering%'"
    ).fetchone() is not None, "adapter loss was not recorded as an event"
    col.repo.close()


def test_recovery_resumes_collection_when_the_adapter_returns(tmp_path,
                                                              monkeypatch):
    """After a successful reconnect the collector must resume normally: reset
    the dead-cycle counter, force a slow pass (the vehicle may have driven or
    charged while blind), and re-probe DIDs that had been refused."""
    col = _tcp_collector(tmp_path, 39983)
    col._last_slow = 10_000.0          # pretend a slow phase just ran
    col._cells_readable = False
    col._dtc_snapshots["U112300"] = {"x": 1}
    col._dead_cycles = 3

    monkeypatch.setattr("collector.time.sleep", lambda s: None)
    monkeypatch.setattr(col.conn, "close", lambda: None)
    monkeypatch.setattr(col.conn, "open", lambda: None)
    monkeypatch.setattr(col, "open_and_identify", lambda: "WVWZZZE1ZMP087053")

    assert col._recover_adapter() is True
    assert col._dead_cycles == 0
    assert col._last_slow < 0, "slow phase not forced after a blind period"
    assert col._cells_readable is True, "cell sweep not re-enabled"
    assert col._dtc_snapshots == {}, "stale DTC snapshots survived a reconnect"
    col.repo.close()


def test_recovery_reports_failure_and_keeps_trying(tmp_path, monkeypatch):
    """A failed reconnect must not be fatal and must not reset the counter --
    resetting it would restart the backoff at 5 s and hammer the port."""
    from diagnostic.interface import AdapterNotFoundError
    col = _tcp_collector(tmp_path, 39984)
    monkeypatch.setattr("collector.time.sleep", lambda s: None)
    monkeypatch.setattr(col.conn, "close", lambda: None)

    def boom():
        raise AdapterNotFoundError("nope")

    monkeypatch.setattr(col.conn, "open", boom)
    assert col._recover_adapter() is False
    assert col._dead_cycles == 1
    assert col._recover_adapter() is False
    assert col._dead_cycles == 2, "backoff state lost between attempts"
    col.repo.close()


# -- session energy integration --------------------------------------------------

def test_session_energy_is_integrated_not_summed(repo):
    """Energy must be power integrated over time, not the sum of samples.

    Summing instantaneous kW values and scaling by the duration multiplies the
    result by the sample count, so a session sampled five times reported five
    times the energy actually delivered.
    """
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    sid = repo.open_session(vid, start.isoformat(), "AC_DC", 20.0)
    # 5 samples 15 min apart spanning 1 h at a constant 6 kW -> 6 kWh
    for i in range(5):
        ts = (start + timedelta(minutes=15 * i)).isoformat(timespec="seconds")
        repo.add_charging_sample(sid, ts, power_kw=6.0, battery_temp_c=22.0)
    repo.close_session(sid, (start + timedelta(hours=1)).isoformat(
        timespec="seconds"), 80.0)
    sess = repo.conn.execute("SELECT duration_s, energy_estimate_kwh, "
                             "max_power_kw FROM charging_sessions WHERE id=?",
                             (sid,)).fetchone()
    assert sess["duration_s"] == 3600
    # 6 kW over 1 h is 6 kWh. The old sum-of-samples formula reported 30.
    assert sess["energy_estimate_kwh"] == pytest.approx(6.0)
    assert sess["max_power_kw"] == pytest.approx(6.0)


def test_session_energy_handles_uneven_sampling(repo):
    """Trapezoidal over the real timestamps, so uneven sampling still totals
    the area under the power curve."""
    from datetime import datetime, timedelta, timezone
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    sid = repo.open_session(vid, start.isoformat(), "AC_DC", 20.0)
    mid = start + timedelta(hours=1)
    end = mid + timedelta(hours=1)
    for t, p in ((start, 10.0), (mid, 20.0), (end, 20.0)):
        repo.add_charging_sample(sid, t.isoformat(timespec="seconds"),
                                 power_kw=p, battery_temp_c=22.0)
    repo.close_session(sid, end.isoformat(timespec="seconds"), 80.0)
    got = repo.conn.execute("SELECT energy_estimate_kwh FROM charging_sessions "
                            "WHERE id=?", (sid,)).fetchone()["energy_estimate_kwh"]
    # trapezoid: 0-1 h at (10+20)/2 = 15 kWh, 1-2 h at 20 kWh -> 35 kWh
    assert got == pytest.approx(35.0)


def test_session_clock_correction_does_not_go_negative(repo):
    """An end stamp before the start must not persist negative duration/energy."""
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    sid = repo.open_session(vid, "2026-09-01T12:00:00+00:00", "AC_DC", 20.0)
    repo.add_charging_sample(sid, "2026-09-01T12:00:00+00:00",
                              power_kw=50.0, battery_temp_c=22.0)
    repo.close_session(sid, "2026-09-01T11:00:00+00:00", 80.0)
    sess = repo.conn.execute("SELECT duration_s, energy_estimate_kwh FROM "
                             "charging_sessions WHERE id=?", (sid,)).fetchone()
    assert sess["duration_s"] == 0.0
    assert sess["energy_estimate_kwh"] is None


# -- tolerant timestamp / DTC parsing -------------------------------------------

def test_ts_to_days_handles_mixed_naive_and_aware():
    # Subtracting a naive from an aware datetime raises TypeError; these must
    # still convert so a window spanning a timezone change is usable.
    assert stats.ts_to_days(["2026-01-01T00:00:00",
                             "2026-01-02T00:00:00+00:00"]) == [0.0, 1.0]


def test_ts_to_days_empty_and_malformed_do_not_raise():
    assert stats.ts_to_days([]) == []
    assert stats.ts_to_days(["not-a-timestamp", ""]) == []


def test_classify_dtc_rejects_empty_input():
    assert classify_dtc("") == ["unknown"]
    assert classify_dtc(None) == ["unknown"]


# -- connection doctor ----------------------------------------------------------

def _doctor_with_fake_adapter(mode):
    """Run the doctor against a fake adapter in `mode`; return (stages, causes)."""
    from tools import fake_adapters
    from tools.doctor import run as run_doctor
    srv, port = fake_adapters.start(mode, port=0)
    try:
        cfg = Config({"adapter": {"type": "elm327_serial",
                                  "port": "COM_DOES_NOT_EXIST"}})
        stages, causes, fixes = run_doctor(
            cfg, tcp=("127.0.0.1", port), timeout=1.0, verbose=False)
    finally:
        srv.shutdown()
        srv.server_close()
    return stages, causes, fixes


def _stage(stages, title):
    return next(s for s in stages if s.title == title)


@pytest.mark.slow
def test_doctor_reports_a_working_link(tmp_path):
    """The good path must reach a connection verdict, not just avoid crashing."""
    from tools import fake_adapters
    from tools.doctor import run as run_doctor
    srv, port = fake_adapters.start("clone", port=0)
    try:
        cfg = Config({"adapter": {"type": "elm327_serial", "port": "COM3"}})
        stages, causes, fixes = run_doctor(
            cfg, tcp=("127.0.0.1", port), timeout=1.0, verbose=False)
    finally:
        srv.shutdown()
        srv.server_close()
    assert _stage(stages, "Port open").status != "FAIL"
    assert _stage(stages, "Adapter identity").status != "FAIL"
    assert any("connecting" in c for c in causes)


@pytest.mark.slow
def test_doctor_blames_the_ignition_when_the_adapter_is_fine():
    """Adapter answers, car silent -> vehicle-side cause, not a tool cause."""
    stages, causes, fixes = _doctor_with_fake_adapter("dead")
    assert _stage(stages, "Adapter identity").status == "OK"
    assert _stage(stages, "Protocol negotiation").status == "FAIL"
    assert any("vehicle-side" in c for c in causes), causes
    assert any("ignition" in f.lower() for f in fixes), fixes


@pytest.mark.slow
def test_doctor_blames_the_link_when_nothing_answers_at_all():
    """Port opens but the adapter never speaks -> wrong port / unpaired."""
    stages, causes, fixes = _doctor_with_fake_adapter("mute")
    assert _stage(stages, "Port open").status == "OK"
    assert _stage(stages, "Adapter identity").status == "FAIL"
    assert any("no ELM327 answered" in c for c in causes), causes
    assert any("Bluetooth" in f for f in fixes), fixes


@pytest.mark.slow
def test_doctor_detects_a_refused_protocol_change():
    """A pinned protocol looks identical to a sleeping car unless the refusal
    is noticed -- and the refusal is the only thing that tells them apart."""
    stages, causes, fixes = _doctor_with_fake_adapter("wrongproto")
    proto = _stage(stages, "Protocol negotiation")
    assert proto.status == "FAIL"
    assert proto.data.get("refused"), "ATSP refusals were not recorded"
    assert any("refused to change protocol" in c for c in causes), causes
    # and explicitly NOT the ignition advice, which would send the user away
    assert not any("ignition" in c.lower() for c in causes), causes


@pytest.mark.slow
def test_doctor_warns_about_an_unknown_adapter_but_still_connects():
    stages, causes, _fixes = _doctor_with_fake_adapter("clone")
    ident = _stage(stages, "Adapter identity")
    assert ident.status == "WARN"
    assert _stage(stages, "OBD-II bus").status != "FAIL"
    assert any("known ELM327 family" in c for c in causes), causes


@pytest.mark.slow
def test_doctor_flags_a_missing_configured_port(tmp_path):
    from diagnostic.interface import detect_serial_ports
    from tools.doctor import Doctor
    doc = Doctor(Config({"adapter": {"type": "elm327_serial",
                                    "port": "COM99"}}))
    stage = doc.stage_ports()
    if detect_serial_ports():
        # Only assert the diagnosis when the port genuinely is absent.
        assert stage.status == "FAIL"
        assert any("COM99 is not present" in p for p in stage.problems)


@pytest.mark.slow
def test_doctor_never_sends_a_write_to_the_vehicle():
    """The doctor must not be able to put a write on the wire, even if edited."""
    from tools.doctor import Doctor
    from diagnostic.uds import ReadOnlyViolationError
    doc = Doctor(Config({"adapter": {"type": "elm327_serial", "port": "COM3"}}))
    class _FakeT:
        sent = []
        def send_command(self, cmd):
            _FakeT.sent.append(cmd)
            return ["OK"]
    doc.transport = _FakeT()
    for bad in ("2E1234", "1403FFFF", "2E10", "3101FFFF", "2701"):
        with pytest.raises(ReadOnlyViolationError):
            doc._vehicle(bad)
    assert _FakeT.sent == [], f"a write reached the transport: {_FakeT.sent}"


def test_doctor_cli_flags_survive_main_parser():
    """`main.py doctor <flag>` must not be rejected by main.py's own parser.

    Regression: the doctor was originally reached by rewriting sys.argv, so
    every flag it documented was rejected before the doctor ever saw it.
    """
    import main as app
    parser = app.build_parser()
    for flag in ("--all-ports", "--only-config", "--timeout"):
        args = parser.parse_args(["doctor", flag] + (["3"] if
                                                     flag == "--timeout" else
                                                     []))
        assert args.command == "doctor"


def test_doctor_main_accepts_explicit_argv(monkeypatch, capsys):
    """main() must take argv rather than reading global sys.argv."""
    from tools import doctor
    assert doctor.main(["--only-config"]) == 0
    assert "adapter.port" in capsys.readouterr().out
