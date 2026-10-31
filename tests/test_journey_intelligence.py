"""Tests for analysis/journey_intelligence.py."""
from database.repository import Repository

from analysis.journey_intelligence import journey_summary


def test_journey_summary_empty(tmp_path):
    db = tmp_path / "c.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle("SYNTHETIC00000000")
        got = journey_summary(repo, vid)
        assert got["total_drives"] == 0
        assert got["total_km"] == 0
