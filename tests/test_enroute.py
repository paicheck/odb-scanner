"""Tests for analysis/enroute.py."""
from analysis.enroute import assess_deviation


def test_normal_deviation():
    got = assess_deviation(
        planned_arrival_pct=20, actual_pct=50, predicted_now_pct=50,
        remaining_km=100)
    assert got["level"] == "normal"


def test_critical_low_arrival():
    got = assess_deviation(
        planned_arrival_pct=20, actual_pct=30, predicted_now_pct=50,
        remaining_km=100)
    assert got["level"] in ("adjust", "critical")
    assert got["deviation_pct"] < 0
