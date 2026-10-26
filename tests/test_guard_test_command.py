"""`main.py guard-test` checks the transmit paths, not only the validator.

It used to call validate_request directly, so a request validated as one
string and sent as another, an approved session falling back to broadcast, and
the console's adapter-command bypass all passed it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_guard_test_passes_on_the_real_code(capsys):
    assert main.cmd_guard_test() == 0
    out = capsys.readouterr().out
    assert "transmit path refused a write smuggled after a CR" in out
    assert "console refuses 'ATCAF0'" in out
    assert "GUARANTINE" not in out


def test_guard_test_fails_when_the_console_gate_is_broken(monkeypatch, capsys):
    import tools.elm_console as console
    monkeypatch.setattr(console, "refusal", lambda cmd, raw_unsafe: None)
    assert main.cmd_guard_test() == 1
    assert "FAIL: console would send 'ATCAF0'" in capsys.readouterr().out


def test_guard_test_fails_when_transmit_sends_unvalidated(monkeypatch, capsys):
    from diagnostic import uds
    monkeypatch.setattr(uds, "canonical_request", lambda p: p)
    monkeypatch.setattr(uds, "validate_request", lambda p: 0x22)
    assert main.cmd_guard_test() == 1
    assert "FAIL: transmit path sent" in capsys.readouterr().out
