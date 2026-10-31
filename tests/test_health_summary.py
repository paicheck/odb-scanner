"""Tests for analysis/health_summary.py."""
from config import Config
from database.repository import Repository
from routing.car import car_energy

from analysis.health_summary import health_summary


def test_health_summary_returns_status(tmp_path):
    db = tmp_path / "c.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle("SYNTHETIC00000000")
        got = health_summary(repo, vid)
        assert "status" in got
        assert "dtc_count" in got
        assert "capacity_trend" in got
