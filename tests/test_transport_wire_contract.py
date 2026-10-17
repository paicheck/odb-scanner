"""The MEB wire form is external truth, not whatever the code happens to do.

The 29-bit path once regressed because its test asserted the implementation
(`assert "ATCAF0" in stub.sent`) instead of the captured ground truth, so the
suite went green when the hand-framed CAF0 line was introduced -- and every
29-bit module then answered '?' on the car. These tests fix the other end of
that: the expected commands live in ``tests/data/car_scanner_wire.json``,
transcribed from the 2026-10-15 passive HCI capture of Car Scanner, and the
transport must reproduce them. Changing the wire form now means editing that
file on purpose, in a diff a reviewer can see.

Two things are checked:

  * the golden contract -- ``set_module`` emits the captured commands for each
    addressing class and never the forbidden ones (ATCAF0 / ATFCSM 0);
  * the exported spec agrees with the transport -- the ``transport`` quirks
    every client loads (tools/export_spec.py) are derived from what
    ``set_module`` actually sends, not hand-written next to it.
"""
import json
from pathlib import Path

import pytest

from diagnostic.ecus import ECUS
from diagnostic.elm327 import Elm327Transport

GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "car_scanner_wire.json")
    .read_text(encoding="utf-8")
)


class _Recorder:
    """A stand-in for the adapter that records every command it is sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_command(self, command: str) -> list[str]:
        self.sent.append(command)
        return ["OK"]


def _transport(proto_to_park_on: str) -> tuple[Elm327Transport, _Recorder]:
    """A transport aimed at nothing, parked on the given protocol.

    Parking on the *other* protocol is deliberate: it makes the next
    ``set_module`` perform a real switch, so the ATSP that a switch requires is
    exercised rather than skipped as "already set".
    """
    t = Elm327Transport(port="TEST")
    recorder = _Recorder()
    t.send_command = recorder.send_command
    t.meb_addressing = True
    t._meb_module = None
    t._atcra = None
    t._caf = None            # unknown, so the preamble must be re-asserted
    t._proto = proto_to_park_on
    return t, recorder


@pytest.mark.parametrize("klass,switch_from", [("29-bit", "6"), ("11-bit", "7")])
def test_set_module_reproduces_the_captured_wire_form(klass, switch_from):
    """Every captured command is sent, and nothing the capture forbids."""
    entry = GOLDEN[klass]
    t, recorder = _transport(switch_from)

    assert t.set_module(int(entry["tx29"], 16), int(entry["rx29"], 16)) is True

    for command in entry["commands_present"]:
        assert command in recorder.sent, (
            f"{klass}: the captured command {command!r} was not sent: "
            f"{recorder.sent}")
    for command in entry["commands_forbidden"]:
        assert command not in recorder.sent, (
            f"{klass}: sent {command!r}, which the capture shows Car Scanner "
            f"never sends for a working read: {recorder.sent}")
    assert t._proto == ("7" if klass == "29-bit" else "6")
    assert t.auto_formatting is True, "MEB reads are always in ATCAF1"


@pytest.mark.parametrize("klass,switch_from", [("29-bit", "6"), ("11-bit", "7")])
def test_read_request_goes_out_bare(klass, switch_from):
    """The request is the bare DID read; the adapter adds the framing."""
    from diagnostic.connection import DiagnosticConnection

    entry = GOLDEN[klass]
    t, recorder = _transport(switch_from)
    conn = DiagnosticConnection(t)
    ecu = next(e for e in ECUS.values() if e.tx29 == int(entry["tx29"], 16))

    conn._transmit(entry["captured_request"], ecu, "wire contract test")

    assert recorder.sent[-1] == entry["captured_request"], (
        f"{klass}: the request did not go out bare: {recorder.sent}")
    # No hand-framed 8-byte line (03-prefixed, 16 hex chars) anywhere.
    assert not any(len(c) == 16 and c.startswith("03") for c in recorder.sent), (
        f"{klass}: a hand-framed line was sent: {recorder.sent}")


def _quirks_from_wire(sent: list[str]) -> dict:
    """The transport quirks implied by the commands actually sent."""
    return {
        "auto_formatting": "ATCAF1" in sent and "ATCAF0" not in sent,
        "flow_control": ("ATFCSD 300000" in sent and "ATFCSM 1" in sent
                         and any(c.startswith("ATFCSH ") for c in sent)),
        "receive_filter": any(c.startswith("ATCRA ") for c in sent),
    }


@pytest.mark.parametrize("key", sorted(ECUS))
def test_exported_transport_quirks_match_the_transport(key):
    """The spec every client loads must describe the transport that exists.

    tools/export_spec.py publishes {auto_formatting, flow_control,
    receive_filter} per ECU for the Kotlin/Android client to obey. If those
    flags are ever changed by hand and drift from what diagnostic/elm327.py
    really does, the phone and the desktop would disagree about the wire form --
    the exact class of drift the single shared spec exists to prevent.
    """
    from tools.export_spec import _transport_quirks

    spec = ECUS[key]
    t, recorder = _transport("7")
    # Park on the protocol this module will NOT want, so set_module performs a
    # real switch and emits its ATSP rather than skipping it as already-set.
    wants_11bit = spec.tx29 is not None and spec.tx29 <= 0x7FF
    t._proto = "7" if wants_11bit else "6"

    assert t.set_module(spec.tx29, spec.rx29) is True
    assert _quirks_from_wire(recorder.sent) == _transport_quirks(spec.tx29), (
        f"{key}: exported quirks do not match the transport's commands: "
        f"{recorder.sent}")
