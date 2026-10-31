"""The overview shows the health summary and the twin's state -- each
judged as analysis/health_summary.py and analysis/twin.py say, with the
collector's own 12 V threshold."""
import html
from datetime import datetime, timedelta, timezone

from config import Config
from database.repository import Repository

VIN = "WVWZZZE1ZMS000001"


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(
        timespec="seconds")


def _db(tmp_path, volts=None, fault=None, volts_age_min=30):
    path = tmp_path / "o.db"
    repo = Repository(path)
    vid = repo.ensure_vehicle(VIN, make="VW", model="ID.3")
    if volts is not None:
        repo.record_measurement(vid, _ago(volts_age_min), "adapter_12v_voltage",
                                "adapter", "ATRV",
                                None, "V", "reported", "adapter", "documented",
                                f"{volts}V", value=volts)
    if fault:
        repo.upsert_dtc(vid, _ago(60), "bms", fault, "test fault", [], status="active")
    repo.conn.commit()
    repo.close()
    return path


def _page(path, collector=None):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(path)}, "web": {"host": "127.0.0.1"},
                  "collector": collector or {}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 200
    return html.unescape(r.text)


def test_an_active_fault_shows_as_needing_attention(tmp_path):
    body = _page(_db(tmp_path, fault="P0AA6"))
    assert "Needs attention" in body and "P0AA6 (active)" in body


def test_the_12v_is_judged_on_the_collectors_threshold(tmp_path):
    path = _db(tmp_path, volts=12.3)
    assert "12.30 V" in _page(path) and "(fine)" in _page(path)
    strict = _page(path, collector={"low_12v_voltage": 12.5})
    assert "12 V battery 12.30 V at rest" in strict and "(low)" in strict


def test_the_twin_card_says_what_the_car_is_doing(tmp_path):
    body = _page(_db(tmp_path, volts=12.6, volts_age_min=5))
    assert "What the car is doing" in body
    assert "asleep" in body and "only the adapter" in body
    assert 'href="/api/twin"' in body


def test_no_vehicle_no_cards_and_no_error(tmp_path):
    path = tmp_path / "e.db"
    Repository(path).close()
    body = _page(path)
    assert "What the car is doing" not in body
