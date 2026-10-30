"""The ABS reader is the one approved 0x10 0x03 path; keep it narrow.

The live hardware path is not exercised here. These tests pin the plan, the
token gate, the default-session return, and the fact that the ordinary
read-only validator still refuses 0x10 0x03.
"""
from __future__ import annotations

import pytest

from diagnostic.connection import DiagnosticConnection
from diagnostic.uds import (
    ABS_APPROVAL_TOKEN,
    NegativeResponseError,
    ProtocolError,
    ReadOnlyViolationError,
    validate_approved_extended_session,
    validate_request,
)
from tools.read_abs_wheel_speeds import ABS, build_plan, main, read_wheels


class _RecordingTransport:
    meb_addressing = True

    def __init__(self, reply):
        self.written: list[str] = []
        self.approvals: list[str | None] = []
        self.reply = list(reply)
        self.module = None

    def set_module(self, tx, rx=None):
        self.module = (tx, rx)
        return True

    def send_command(self, command, approval=None):
        # The real transport checks every line itself, so the approved
        # request must arrive with its token (Elm327Transport.send_command).
        self.written.append(command)
        self.approvals.append(approval)
        return list(self.reply)


def test_ordinary_validator_still_refuses_extended_session():
    with pytest.raises(ReadOnlyViolationError):
        validate_request("1003")


def test_approved_validator_only_accepts_the_exact_request_and_token():
    assert validate_approved_extended_session("10 03", ABS_APPROVAL_TOKEN) == 0x10
    with pytest.raises(ReadOnlyViolationError):
        validate_approved_extended_session("1003", "wrong")
    for bad in ("1001", "1002", "1003AB", "1004"):
        with pytest.raises(ReadOnlyViolationError):
            validate_approved_extended_session(bad, ABS_APPROVAL_TOKEN)


def test_approved_session_sends_1003_and_parses_positive_response():
    t = _RecordingTransport(["77D065003001E01E5"])
    conn = DiagnosticConnection(t)

    payload = conn.approved_extended_session(
        ABS, approval=ABS_APPROVAL_TOKEN, purpose="test"
    )

    assert payload.hex().upper() == "5003001E01E5"
    assert "1003" in t.written
    assert t.module == (0x713, 0x77D)


def test_wrong_token_never_reaches_the_wire():
    t = _RecordingTransport(["77D065003001E01E5"])
    conn = DiagnosticConnection(t)

    with pytest.raises(ReadOnlyViolationError):
        conn.approved_extended_session(ABS, approval="wrong", purpose="test")

    assert t.written == []


def test_default_session_uses_1001():
    t = _RecordingTransport(["77D06500100000000"])
    conn = DiagnosticConnection(t)

    payload = conn.default_session(ABS)
    assert payload.hex().upper() == "500100000000"
    assert "1001" in t.written


def test_plan_has_one_session_change_four_reads_and_a_return():
    plan = build_plan()
    assert [item["request"] for item in plan] == [
        "1003", "221800", "221801", "221802", "221803", "1001",
    ]
    assert [item.get("did") for item in plan if item["step"] == "read"] == [
        "0x1800", "0x1801", "0x1802", "0x1803",
    ]


class _FakeConn:
    def __init__(self, fail_did: int | None = None, session_error=None):
        self.calls: list[object] = []
        self.fail_did = fail_did
        self.session_error = session_error
        self.tx_logger = lambda **kw: None

    def approved_extended_session(self, ecu, *, approval, purpose):
        assert approval == ABS_APPROVAL_TOKEN
        self.calls.append("session")
        self.tx_logger(direction="TX", ecu=ecu.key, payload="1003",
                       purpose=purpose)
        if self.session_error is not None:
            raise self.session_error
        self.tx_logger(direction="RX", ecu=ecu.key, payload="5003",
                       purpose="response")
        return b"\x50\x03"

    def read_did(self, ecu, did):
        self.calls.append(did)
        if did == self.fail_did:
            raise NegativeResponseError(0x22, 0x31)
        payloads = {
            0x1800: "00000000FF00FFFFFFFFFFFF",
            0x1801: "03E80000FF00FFFFFFFFFFFF",
            0x1802: "01F40000FF00FFFFFFFFFFFF",
            0x1803: "00000000FF00FFFFFFFFFFFF",
        }
        raw = bytes.fromhex(payloads[did])
        self.tx_logger(direction="TX", ecu=ecu.key, payload=f"22{did:04X}",
                       purpose="test")
        self.tx_logger(direction="RX", ecu=ecu.key, payload=raw.hex(),
                       purpose="response")
        return raw

    def default_session(self, ecu, purpose="return to default session"):
        self.calls.append("default")
        self.tx_logger(direction="TX", ecu=ecu.key, payload="1001",
                       purpose=purpose)
        return b"\x50\x01"


def test_read_wheels_decodes_and_always_returns_to_default():
    conn = _FakeConn()
    result = read_wheels(conn, ABS_APPROVAL_TOKEN)

    assert result["approval_used"] is True
    assert conn.calls == ["session", 0x1800, 0x1801, 0x1802, 0x1803,
                           "default"]
    assert result["returned_to_default"] is True
    assert [entry["value"] for entry in result["reads"]] == [0.0, 10.0, 5.0, 0.0]


def test_read_wheels_returns_to_default_after_a_negative_read():
    conn = _FakeConn(fail_did=0x1801)
    result = read_wheels(conn, ABS_APPROVAL_TOKEN)

    assert conn.calls[-1] == "default"
    assert result["returned_to_default"] is True
    assert result["reads"][1]["error"] == "NRC 0x31"


def test_read_wheels_tries_default_session_even_if_the_session_change_fails():
    conn = _FakeConn(session_error=NegativeResponseError(0x10, 0x22))
    result = read_wheels(conn, ABS_APPROVAL_TOKEN)

    assert conn.calls == ["session", "default"]
    assert "NRC 0x22" in result["session_error"]
    assert result["returned_to_default"] is True


def test_read_wheels_refuses_a_wrong_token_before_any_call():
    conn = _FakeConn()
    with pytest.raises(ReadOnlyViolationError):
        read_wheels(conn, "wrong")
    assert conn.calls == []


def test_dry_run_does_not_touch_an_adapter(capsys):
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "no adapter access" in out
    assert "1003" in out and "0x1800" in out and "1001" in out


def test_live_requires_the_explicit_session_change_opt_in(capsys):
    assert main(["--execute"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "No adapter access attempted" in err


# -- the approved session must stay physical ---------------------------------
# Falling back to functional addressing sent 1003 on the broadcast header,
# asking every ECU to change session, and the first 0x50 from ANY ECU was
# reported as the ABS module's answer.

class _NoMebTransport(_RecordingTransport):
    meb_addressing = False


class _RefusingTransport(_RecordingTransport):
    def set_module(self, tx, rx=None):
        self.module = (tx, rx)
        return tx is None          # the module setup is refused


def test_approved_session_refused_without_meb_addressing():
    t = _NoMebTransport(["7E8065003001E01E5"])
    with pytest.raises(ReadOnlyViolationError):
        DiagnosticConnection(t).approved_extended_session(
            ABS, approval=ABS_APPROVAL_TOKEN, purpose="test")
    assert "1003" not in t.written


def test_approved_session_refused_when_module_setup_is_refused():
    t = _RefusingTransport(["7E8065003001E01E5"])
    logged = []
    conn = DiagnosticConnection(t, tx_logger=lambda **kw: logged.append(kw))
    with pytest.raises(ReadOnlyViolationError):
        conn.approved_extended_session(ABS, approval=ABS_APPROVAL_TOKEN,
                                       purpose="test")
    assert "1003" not in t.written
    assert not any(e.get("payload") == "1003" for e in logged)


def test_approved_session_rejects_a_foreign_responder():
    t = _RecordingTransport(["7E8065003001E01E5"])      # 0x7E8, not ABS 0x77D
    with pytest.raises(ProtocolError):
        DiagnosticConnection(t).approved_extended_session(
            ABS, approval=ABS_APPROVAL_TOKEN, purpose="test")
