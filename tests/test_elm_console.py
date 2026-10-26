"""The ELM console is read-only unless started with --raw-unsafe."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.elm_console import is_adapter_command, refusal  # noqa: E402


@pytest.mark.parametrize("cmd", ["ATZ", "atsp0", "ATSH7E5", "AT CRA 7ED",
                                 "STDI", "ATCP 17"])
def test_adapter_commands_pass(cmd):
    assert is_adapter_command(cmd)
    assert refusal(cmd, raw_unsafe=False) is None


@pytest.mark.parametrize("cmd", ["0100", "0902", "03", "22F190", "22 F1 90",
                                 "1001", "1902FF", "1905", "3E00"])
def test_reads_pass(cmd):
    assert refusal(cmd, raw_unsafe=False) is None


@pytest.mark.parametrize("cmd", ["14FFFFFF", "2EF19001", "3101FF00", "1002",
                                 "1003", "2701", "nonsense"])
def test_writes_and_session_changes_are_refused(cmd):
    assert refusal(cmd, raw_unsafe=False)


@pytest.mark.parametrize("cmd", ["14FFFFFF", "2EF19001", "1003"])
def test_raw_unsafe_sends_anything(cmd):
    assert refusal(cmd, raw_unsafe=True) is None
