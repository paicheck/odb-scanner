"""Diagnostic tools that drive the adapter themselves still go through the guard.

tools/diagnose_meb_path.py and tools/capture_gateway_energy_11bit.py sent vehicle
requests straight to the transport, with no validate_request and no tx_log.
The harness also configures framing by hand (ATCAF0), under which a validated
"0104" reaches the ECU as mode 04, clear emissions DTCs.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.diagnose_meb_path as harness  # noqa: E402
from diagnostic.connection import DiagnosticConnection  # noqa: E402
from diagnostic.uds import ReadOnlyViolationError  # noqa: E402

TOOLS = Path(__file__).resolve().parent.parent / "tools"


@pytest.fixture(autouse=True)
def _formatting_on():
    harness._raw_framing = False
    yield
    harness._raw_framing = False


def test_harness_validates_the_payload_inside_a_raw_frame():
    assert harness.check_raw("0104") == "0104"            # mode 01 PID 04
    harness.check_raw("ATCAF0")
    with pytest.raises(ReadOnlyViolationError):
        harness.check_raw("0104")                         # ECU sees mode 04
    assert harness.check_raw("0210015555555555") == "0210015555555555"
    with pytest.raises(ReadOnlyViolationError):
        harness.check_raw("0314FFFF55555555")             # 0x14 clear DTCs
    harness.check_raw("ATCAF1")
    assert harness.check_raw("22F190") == "22F190"


@pytest.mark.parametrize("cmd", ["2EF19000", "1003", "22F190\r2EF19000"])
def test_harness_refuses_writes_and_smuggling(cmd):
    with pytest.raises(ReadOnlyViolationError):
        harness.check_raw(cmd)


def test_harness_flow_control_data_must_be_a_flow_control_frame():
    assert harness.check_raw("ATFCSD300000")
    with pytest.raises(ReadOnlyViolationError):
        harness.check_raw("ATFCSD 0414FFFFFF")


def test_request_lines_is_the_validated_choke_point():
    sent, logged = [], []

    class _T:
        def send_command(self, c):
            sent.append(c)
            return []

    conn = DiagnosticConnection(_T(), tx_logger=lambda **kw: logged.append(kw))
    with pytest.raises(ReadOnlyViolationError):
        conn.request_lines("2EF19000", None, "test")
    conn.request_lines("222AB2", None, "test")
    assert sent == ["222AB2"]
    assert logged[0]["payload"] == "222AB2"


def test_capture_tools_do_not_bypass_the_connection():
    eleven = (TOOLS / "capture_gateway_energy_11bit.py").read_text(encoding="utf-8")
    assert "c.conn.t.send_command(" not in eleven
    assert "request_lines(" in eleven
    legacy = (TOOLS / "capture_gateway_energy.py").read_text(encoding="utf-8")
    assert "await c.conn.read_did" not in legacy
