"""0x7448 byte 0 is one bit field, read one way.

charge_mode matched 4/6 exactly and mapped everything else to 0 ("idle"),
while the port flags read the same byte as bits -- so 0x0E (rapid-port bit
set) decoded as idle, and the collector closed a live charging session on it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decoders.registry import build_default_registry  # noqa: E402

CHARGE_MODE = build_default_registry("meb").get("charge_mode")


@pytest.mark.parametrize("raw,want", [
    (0x00, 0), (0x01, 0),          # standby, driving
    (0x04, 1), (0x05, 1),          # AC charging (bit 2), awake or not
    (0x06, 2), (0x07, 2),          # DC charging (bits 1+2)
    (0x02, None),                  # bit 1 alone: undocumented
    (0x08, None), (0x0E, None), (0x0C, None), (0xFF, None),   # unknown bits
])
def test_charge_mode_bit_field(raw, want):
    assert CHARGE_MODE.decode_value(bytes([raw])) == want


def test_an_unknown_value_does_not_close_a_live_session(tmp_path):
    from collector import Collector
    from config import Config
    from database.repository import Repository
    repo = Repository(str(tmp_path / "c.db"))
    cfg = Config({"collector": {"poll_interval": 5.0, "slow_poll_interval": 60.0,
                                "max_value_age_s": 900.0},
                  "database": {"path": str(tmp_path / "c.db")}})
    col = Collector(cfg, repo)
    col.vehicle_id = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    col._charging_tracking("2026-10-27T10:00:00+00:00", {"charge_mode": 2})
    assert col._active_session_id is not None
    mode = CHARGE_MODE.decode_value(bytes([0x0E]))
    col._charging_tracking("2026-10-27T10:01:00+00:00", {"charge_mode": mode})
    assert col._active_session_id is not None, "session closed on an unknown byte"
    repo.close()
