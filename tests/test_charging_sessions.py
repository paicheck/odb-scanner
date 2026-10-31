"""Tests for analysis/charging_sessions.py."""
from database.repository import Repository

from analysis.charging_sessions import charging_summary


def test_charging_summary_empty(tmp_path):
    db = tmp_path / "c.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle("SYNTHETIC00000000")
        got = charging_summary(repo, vehicle_id=vid)
        assert got["sessions"] == 0
        assert got["by_type"] == {}
