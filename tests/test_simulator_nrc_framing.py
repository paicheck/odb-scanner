"""Simulator NRCs carry their ISO-TP PCI byte, as a real adapter shows them.

The mode-01 and unknown-service negative responses were emitted as a bare
7F xx yy; the parser (correctly) treats that as not-a-frame, so in the
simulator an NRC looked like silence -- the same bug already fixed for 0x22.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagnostic import uds  # noqa: E402
from simulator.vehicle import _build_response  # noqa: E402


def _parsed(req_hex):
    frames = _build_response(bytes.fromhex(req_hex), 0x7E0)
    return uds.parse_elm_lines(["7E8" + f.hex().upper() for f in frames])


def test_unsupported_mode01_pid_is_a_readable_nrc():
    assert _parsed("0177") == bytes.fromhex("7F0112")


def test_unknown_service_is_a_readable_nrc():
    assert _parsed("2101") == bytes.fromhex("7F2111")
