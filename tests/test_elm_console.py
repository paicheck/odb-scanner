"""The ELM console is read-only unless started with --raw-unsafe.

Read-only covers adapter commands too: several of them change what reaches the
car without the vehicle request looking any different (ATCAF0 makes a
validated 0104 arrive as mode 04, clear DTCs), so they are allow-listed rather
than waved through for starting with "AT".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.elm_console import is_adapter_command, refusal  # noqa: E402


@pytest.mark.parametrize("cmd", [
    "ATZ", "atsp0", "ATSP6", "ATSPA7", "ATSH7E5", "AT SH 7E0", "ATSH FC007B",
    "ATSH18DB33F1", "AT CRA 7ED", "ATCRA", "ATCRA17FE007B", "ATCP 17",
    "ATH1", "ATE0", "ATL0", "ATS0", "ATI", "AT@1", "ATDP", "ATDPN", "ATRV",
    "ATST64", "ATAT1", "ATCAF1", "ATCF 7E8", "ATCM 7F0", "ATD", "ATWS", "ATPC",
])
def test_safe_adapter_commands_pass(cmd):
    assert is_adapter_command(cmd)
    assert refusal(cmd, raw_unsafe=False) is None


@pytest.mark.parametrize("cmd", [
    # change how a validated request is framed
    "ATCAF0", "at caf0", "AT CAF 0", "ATCEA", "ATCEA12", "ATV1", "ATAL",
    # flow-control content: the next multi-frame read sends these bytes
    "ATFCSH 7E0", "ATFCSD 0414FFFFFF", "ATFCSM 1", "ATCFC0",
    # transmit on their own
    "ATRTR", "ATWM 8213F13E", "ATFI", "ATSI", "ATBI",
    # monitor / acknowledge modes, adapter EEPROM, responses off
    "ATMA", "ATCSM0", "ATPP 2C SV 40", "ATSD 12", "AT@3 ABCDEFGHIJKL", "ATR0",
    # OBDLink: STPX sends arbitrary data, and no ST command is allow-listed
    "STPX H:7E0, D:14FFFFFF", "STPX h:7e0, d:14ffffff", "STDI", "STI",
    # malformed or smuggled
    "ATSH7E", "ATSH 7E0;14", "ATZ 0104",
])
def test_unsafe_adapter_commands_are_refused(cmd):
    assert refusal(cmd, raw_unsafe=False)


@pytest.mark.parametrize("cmd", ["0100", "0902", "03", "22F190", "22 F1 90",
                                 "1001", "1902FF", "1905", "3E00"])
def test_reads_pass(cmd):
    assert refusal(cmd, raw_unsafe=False) is None


@pytest.mark.parametrize("cmd", ["14FFFFFF", "2EF19001", "3101FF00", "1002",
                                 "1003", "2701", "04", "nonsense"])
def test_writes_and_session_changes_are_refused(cmd):
    assert refusal(cmd, raw_unsafe=False)


@pytest.mark.parametrize("cmd", ["14FFFFFF", "2EF19001", "1003", "ATCAF0",
                                 "STPX H:7E0, D:14FFFFFF"])
def test_raw_unsafe_sends_anything(cmd):
    assert refusal(cmd, raw_unsafe=True) is None


def test_read_only_mode_sends_the_canonical_form_it_checked():
    from tools.elm_console import wire_form
    assert wire_form("22 f1 90", raw_unsafe=False) == "22F190"
    assert wire_form("at sh 7e0", raw_unsafe=False) == "ATSH7E0"
    assert wire_form(" 1003 ", raw_unsafe=True) == "1003"
