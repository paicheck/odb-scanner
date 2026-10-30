"""Elm327Transport checks every line it sends, so no caller can go around it.

The read-only guarantee used to rest on every caller validating first:
DiagnosticConnection._transmit did, the diagnostic tools did, but
Elm327Transport.send_command itself sent any single line -- ATMA, ATPP, a raw
2E write -- and only tools/elm_console.py had an adapter-command allow-list.
Now the transport refuses adapter commands off TRANSPORT_ADAPTER_COMMANDS and
checks a vehicle line as what the ECU will receive, by the framing the adapter
has *confirmed*: a refused or lost ATCAF leaves framing unknown, and the
transport re-asserts ATCAF1 before the next request. --raw-unsafe in the
console is the one way round it (`unrestricted`).
"""
import logging

import pytest

from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import CommunicationError
from diagnostic.uds import ABS_APPROVAL_TOKEN, ReadOnlyViolationError


class Device(Elm327Transport):
    """The real transport over a scripted adapter that records each line.

    AT lines answer OK unless `answers` says otherwise; a line in `lose` gets
    no answer at all (a timeout); vehicle lines answer NO DATA.
    """

    def __init__(self, unrestricted=False, **answers):
        super().__init__(host="127.0.0.1", tcp_port=1, timeout=0.1,
                         unrestricted=unrestricted)
        self.answers = {k.replace("_", " "): v for k, v in answers.items()}
        self.lose: set[str] = set()
        self.wire: list[str] = []
        self._dev = object()
        self._opened = True

    def _write(self, text):
        self.wire.append(text.strip())

    def _read_until_prompt(self, timeout):
        cmd = self.wire[-1]
        if cmd in self.lose:
            raise CommunicationError(f"no answer to {cmd}")
        if cmd in self.answers:
            return self.answers[cmd] + "\r>"
        return ("OK" if cmd[:2].upper() in ("AT", "ST") else "NO DATA") + "\r>"


def initialised(**answers) -> Device:
    t = Device(**answers)
    t.send_command("ATZ")
    t.wire.clear()
    return t


# Every adapter command this project sends somewhere (diagnostic/, tools/).
SENT_BY_THE_PROJECT = [
    "ATZ", "ATI", "ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATSP7", "ATSP6",
    "ATCAF1", "ATCAF0", "ATCP 17", "ATCP 18", "ATCP 00", "ATSH FC007B",
    "ATSH DB33F1", "ATSH 710", "ATSH 000710", "ATSH 7E5", "ATCRA", "ATCRA0",
    "ATCRA 17FE007B", "ATCRA 7ED", "ATCRA 0000077A", "ATFCSH 17FC007B",
    "ATFCSH 710", "ATFCSD 300000", "ATFCSM 1", "ATFCSM 0", "ATBI",
    "ATCF 17FE7", "ATRV", "ATDP", "AT@1", "ATWS", "ATD",
]


@pytest.mark.parametrize("cmd", SENT_BY_THE_PROJECT)
def test_every_adapter_command_the_project_sends_is_allowed(cmd):
    t = initialised()
    t.send_command(cmd)
    assert t.wire == [cmd]


@pytest.mark.parametrize("cmd", [
    "ATMA", "ATMR 12", "ATCSM0", "ATR0",               # listen / acknowledge
    "ATPP 2C SV 40", "ATSD 12", "AT@3 ABCDEFGHIJKL",   # adapter EEPROM
    "ATWM 8213F13E", "ATRTR", "ATSI", "ATFI",          # transmit on their own
    "ATV1", "ATCEA", "ATCEA12", "ATAL",                # how frames are built
    "ATBRD 23", "ATBRT 12",                            # baud rate
    "ATFCSD 0414FFFFFF",                               # not a flow-control frame
    "STPX H:7E0, D:14FFFFFF", "STDI", "STI",           # every OBDLink command
    "ATZ 0104", "ATSH 7E0;14", "ATFD", "AT@",          # malformed, unknown
])
def test_dangerous_or_unknown_adapter_commands_never_reach_the_wire(cmd):
    t = initialised()
    with pytest.raises(ReadOnlyViolationError):
        t.send_command(cmd)
    assert t.wire == []


@pytest.mark.parametrize("line, ok", [
    ("22F190", True), ("22 f1 90", True), ("0100", True), ("3E00", True),
    ("3E80", True), ("1902FF", True), ("1001", True),
    ("2EF19000", False), ("1003", False), ("1002", False), ("14FFFFFF", False),
    ("3101FF00", False), ("2701", False), ("3E01", False), ("04", False),
])
def test_vehicle_requests_under_auto_formatting(line, ok):
    t = initialised()
    if ok:
        t.send_command(line)
        assert t.wire == [line]
    else:
        with pytest.raises(ReadOnlyViolationError):
            t.send_command(line)
        assert t.wire == []


def test_under_atcaf0_the_frame_payload_is_what_is_checked():
    t = initialised()
    t.send_command("ATCAF0")
    t.send_command("0322F190")                 # single frame: 22 F1 90
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("0104")                 # the ECU would see mode 04
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("22F190")               # not a frame at all
    t.send_command("ATCAF1")
    t.send_command("22F190")
    assert t.wire == ["ATCAF0", "0322F190", "ATCAF1", "22F190"]


@pytest.mark.parametrize("how", ["refused", "lost"])
def test_an_unconfirmed_atcaf0_leaves_framing_unknown(how):
    """A refused or lost ATCAF0 may or may not have taken effect, so the
    next request is preceded by an ATCAF1 the adapter must confirm."""
    t = initialised(ATCAF0="?")
    if how == "lost":
        t.answers.pop("ATCAF0")
        t.lose.add("ATCAF0")
        with pytest.raises(CommunicationError):
            t.send_command("ATCAF0")
    else:
        assert t.send_command("ATCAF0") == ["?"]
    t.send_command("22F190")
    assert t.wire[-2:] == ["ATCAF1", "22F190"]


def test_a_request_is_not_sent_if_atcaf1_cannot_be_confirmed():
    t = Device()                               # just opened: framing unknown
    t.lose.add("ATCAF1")
    with pytest.raises(CommunicationError):
        t.send_command("22F190")
    assert "22F190" not in t.wire


def test_a_fresh_link_asserts_atcaf1_before_the_first_request():
    t = Device()
    t.send_command("0100")
    assert t.wire == ["ATCAF1", "0100"]


def test_initialise_confirms_framing_without_an_extra_command():
    t = Device(ATZ="ELM327 v1.5")
    t.initialize()
    assert "ATCAF1" in t.wire                  # _base_init's own
    assert t.wire[-1] == "0100" and t.wire.count("ATCAF1") == 1


def test_the_approved_abs_session_needs_its_token():
    t = initialised()
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("1003")
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("1002", approval=ABS_APPROVAL_TOKEN)
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("1003", approval="not-the-token")
    t.send_command("1003", approval=ABS_APPROVAL_TOKEN)
    assert t.wire == ["1003"]
    t.send_command("ATCAF0")
    with pytest.raises(ReadOnlyViolationError):
        t.send_command("021003", approval=ABS_APPROVAL_TOKEN)


def test_the_connection_passes_the_approval_down():
    from diagnostic.connection import DiagnosticConnection
    from tools.read_abs_wheel_speeds import ABS as abs_ecu
    t = initialised()
    t.set_module = lambda tx, rx=None: True    # physical addressing assumed
    t.meb_addressing = True
    conn = DiagnosticConnection(t)
    with pytest.raises(CommunicationError):    # NO DATA: no positive answer
        conn.approved_extended_session(abs_ecu, approval=ABS_APPROVAL_TOKEN,
                                       purpose="test")
    assert "1003" in t.wire, "the transport refused the approved request"


def test_unrestricted_sends_anything_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="diagnostic.elm327"):
        t = Device(unrestricted=True)
    assert any("UNRESTRICTED" in r.getMessage() for r in caplog.records)
    t.send_command("ATMA")
    t.send_command("14FFFFFF")
    assert t.wire == ["ATMA", "14FFFFFF"]
