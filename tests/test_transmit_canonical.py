"""The string that is validated is the string that is transmitted and logged.

Validation used to run on clean_hex(request) -- which drops every non-hex
character -- while _transmit sent the original. "22F190\\r2EF19000" therefore
validated as a single 0x22 read, and the adapter (which ends a command at CR)
received the read followed by a 0x2E write.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagnostic.connection import DiagnosticConnection  # noqa: E402
from diagnostic.elm327 import Elm327Transport  # noqa: E402
from diagnostic.uds import (  # noqa: E402
    ReadOnlyViolationError,
    canonical_request,
    validate_request,
)

SMUGGLED = ["22F190\r2EF19000", "0100\r14FFFF", "22\n1003", "22F190zz",
            "22F190;2E", "22-F1-90", "22\tF190"]


class _Recorder:
    def __init__(self):
        self.written = []

    def send_command(self, command):
        self.written.append(command)
        return ["7E80662F1905A5A5A"]


@pytest.mark.parametrize("payload", SMUGGLED)
def test_anything_but_hex_and_spaces_is_refused(payload):
    with pytest.raises(ReadOnlyViolationError):
        validate_request(payload)


def test_canonical_form_is_upper_case_without_spaces():
    assert canonical_request("22 f1 90") == "22F190"
    assert canonical_request(b"\x22\xf1\x90") == "22F190"


@pytest.mark.parametrize("payload", SMUGGLED)
def test_transmit_refuses_and_sends_nothing(payload):
    t, logged = _Recorder(), []
    conn = DiagnosticConnection(t, tx_logger=lambda **kw: logged.append(kw))
    with pytest.raises(ReadOnlyViolationError):
        conn._transmit(payload, None, "test")
    assert t.written == []
    assert logged == []


def test_transmit_sends_and_logs_the_canonical_string():
    t, logged = _Recorder(), []
    conn = DiagnosticConnection(t, tx_logger=lambda **kw: logged.append(kw))
    conn._transmit("22 f1 90", None, "test")
    assert t.written == ["22F190"]
    assert logged[0]["payload"] == "22F190"


@pytest.mark.parametrize("command", ["22F190\r2EF19000", "ATZ\nATCAF0", "0100\r"])
def test_transport_refuses_multi_line_commands_before_writing(command):
    class _Dev:
        def __init__(self):
            self.data = b""

        def write(self, b):
            self.data += b

    t = Elm327Transport(host="127.0.0.1", tcp_port=1)
    t._dev = _Dev()
    with pytest.raises(ReadOnlyViolationError):
        t.send_command(command)
    assert t._dev.data == b""
