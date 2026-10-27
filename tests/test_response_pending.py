"""NRC 0x78 (responsePending) does not hide the answer that follows it.

An ECU that needs time replies 7F <sid> 78 and then the real response. Only
the first message per sender was reassembled, so read_did raised NRC 0x78 and
the positive answer was dropped.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagnostic import uds  # noqa: E402


def test_single_frame_answer_after_pending():
    assert uds.parse_elm_lines(["77D037F2278", "77D0662180012340000"]) \
        == bytes.fromhex("621800123400")


def test_multi_frame_answer_after_pending_29bit():
    lines = ["18DAF105037F2278", "18DAF105100D621E3D010203",
             "18DAF1052104050607080910"]
    assert uds.parse_elm_lines(lines) == bytes.fromhex(
        "621E3D01020304050607080910")


def test_pending_alone_still_surfaces_as_the_nrc():
    assert uds.parse_elm_lines(["77D037F2278"]) == bytes.fromhex("7F2278")
    assert "ResponsePending" in uds.NRC[0x78]


def test_two_senders_are_still_kept_apart():
    got = uds.payloads_by_source(["7E8037F2278", "7E906621E3B0690",
                                  "7E80562F1905A5A"])
    assert got[0x7E8] == bytes.fromhex("62F1905A5A")
    assert got[0x7E9] == bytes.fromhex("621E3B0690")
