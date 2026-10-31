"""Tests for routing/weather.py."""
from unittest.mock import MagicMock

from routing.weather import fetch, headwind_component


def test_fetch_parses_response():
    sess = MagicMock()
    sess.get.return_value.status_code = 200
    sess.get.return_value.raise_for_status = lambda: None
    sess.get.return_value.json.return_value = {
        "current": {"temperature_2m": 12.3, "wind_speed_10m": 20.0,
                    "wind_direction_10m": 90.0, "precipitation": 0.5}}
    w = fetch(51.0, 0.0, session=sess)
    assert w is not None
    assert w.temp_c == 12.3
    assert w.wind_kmh == 20.0


def test_fetch_returns_none_on_failure():
    sess = MagicMock()
    sess.get.side_effect = Exception("boom")
    assert fetch(51.0, 0.0, session=sess) is None


def test_headwind_component():
    assert headwind_component(10.0, 0.0, 0.0) == 10.0
    assert headwind_component(None, 0.0, 0.0) == 0.0
