"""Alerts (notify.py), the parked 12 V record, and the home charging cost.

What is pinned: an alert is always logged and never raises, even when ntfy is
down; a title cannot inject headers; one condition alerts once per cooldown;
the collector announces only fault codes the vehicle has never had stored,
keeps every parked ATRV reading as adapter_12v_voltage and alerts on a low
one once per parked spell, says what a finished charge added and cost; a
backup that fails its check alerts; and the charging pages show a home cost
only for AC sessions and only when a tariff is set.
"""
import logging

import pytest
from fastapi.testclient import TestClient

from analysis.charging import session_cost
from config import Config, load_config
from database.repository import Repository
from notify import Notifier
from tests.test_parked_car import ScriptedAdapter

SYNTHETIC_VIN = "WVWZZZE1ZMS000001"


class _Response:
    status_code = 200


@pytest.fixture()
def posts(monkeypatch):
    import requests
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        sent.append({"url": url, "data": data, "headers": headers})
        return _Response()

    monkeypatch.setattr(requests, "post", fake_post)
    return sent


def _notifier(**notify):
    return Notifier(Config({"notify": notify}))


# --- notify.py ------------------------------------------------------------------

def test_an_alert_is_always_logged(caplog, posts):
    with caplog.at_level(logging.WARNING, logger="notify"):
        assert _notifier().send("Title", "body")
    assert "ALERT Title: body" in caplog.text
    assert posts == [], "pushed with no ntfy_url configured"


def test_ntfy_gets_the_message_and_one_line_headers(posts):
    n = _notifier(ntfy_url="https://ntfy.example/topic", ntfy_token="tk")
    n.send("New fault\r\nX-Injected: 1", "P0A80 from bms", priority="high")
    (post,) = posts
    assert post["url"] == "https://ntfy.example/topic"
    assert post["data"] == b"P0A80 from bms"
    assert post["headers"]["Title"] == "New fault X-Injected: 1"
    assert post["headers"]["Priority"] == "high"
    assert post["headers"]["Authorization"] == "Bearer tk"


def test_the_same_condition_alerts_once_per_cooldown(posts):
    n = _notifier(ntfy_url="https://ntfy.example/t")
    assert n.send("Disk", "low", key="disk") is True
    assert n.send("Disk", "low", key="disk") is False
    assert n.send("Disk", "low", key="disk", cooldown_s=0) is True
    assert len(posts) == 2


def test_a_push_service_that_is_down_never_raises(monkeypatch, caplog):
    import requests

    def down(*a, **k):
        raise requests.ConnectionError("https://ntfy.example/secret-topic")

    monkeypatch.setattr(requests, "post", down)
    with caplog.at_level(logging.WARNING, logger="notify"):
        assert _notifier(ntfy_url="https://ntfy.example/secret-topic").send(
            "T", "m")
    assert "secret-topic" not in caplog.text.replace("ALERT T: m", ""), \
        "the topic URL was logged"


def test_desktop_alerts_use_notify_send(monkeypatch):
    import notify
    calls = []
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda args, **kw: calls.append(args))
    _notifier(desktop=True).send("12 V battery low", "11.8 V", priority="high")
    (args,) = calls
    assert args[0] == "/usr/bin/notify-send" and "--urgency=critical" in args
    assert args[-2:] == ["12 V battery low", "11.8 V"]


# --- the collector --------------------------------------------------------------

@pytest.fixture()
def col(tmp_path):
    from collector import Collector
    cfg = load_config()
    cfg._data["database"]["path"] = str(tmp_path / "car.db")
    cfg._data["adapter"]["type"] = "elm327_tcp"
    cfg._data["notify"] = {}
    cfg._data["charging"] = {"home_tariff_per_kwh": 0.25, "currency": "£",
                             "home_charging_efficiency": 0.9}
    c = Collector(cfg, Repository(cfg._data["database"]["path"]))
    c.conn.t = ScriptedAdapter("12.4V")
    c.vehicle_id = c.repo.ensure_vehicle(SYNTHETIC_VIN)
    sent = []
    c.notifier.send = lambda title, message, **kw: sent.append((title, message))
    c.sent = sent
    yield c
    c.repo.close()


def test_only_codes_never_stored_before_are_announced(col):
    col.repo.upsert_dtc(col.vehicle_id, "2026-10-01T00:00:00+00:00", "bms",
                        "P0AA6", "old", [])
    col._known_dtcs = col._stored_dtcs()
    col._alert_new_dtcs([{"ecu": "bms", "code": "P0AA6"},
                         {"ecu": "bms", "code": "P0A80"}])
    col._alert_new_dtcs([{"ecu": "bms", "code": "P0A80"}])      # next cycle
    assert [t for t, _ in col.sent] == ["New fault code P0A80"]


def test_parked_readings_are_kept_and_a_low_one_alerts_once_per_spell(col):
    col._enter_parked(12.4)
    col._note_parked_voltage(11.8)
    col._note_parked_voltage(11.7)
    rows = col.repo.conn.execute(
        "SELECT value FROM measurements WHERE key='adapter_12v_voltage' "
        "ORDER BY id").fetchall()
    assert [r["value"] for r in rows] == [12.4, 11.8, 11.7]
    assert [t for t, _ in col.sent] == ["12 V battery low"]
    col._enter_parked(11.6)                                     # a new spell
    assert [t for t, _ in col.sent] == ["12 V battery low"] * 2


def test_a_filling_disk_alerts_once_a_day(col, monkeypatch):
    import collector as collector_mod
    clock = [1000.0]
    monkeypatch.setattr(collector_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(col.repo, "disk_space", lambda: {
        "low": True, "free": 0.5 * 1024 ** 3, "dir": "/data", "db": 1024 ** 2})
    col._disk_checked = -collector_mod.DISK_CHECK_S
    col._check_disk()
    assert [t for t, _ in col.sent] == ["Disk nearly full"]
    assert "0.5 GB free" in col.sent[0][1]


def _session(repo, vid, ctype, energy):
    sid = repo.open_session(vid, "2026-10-29T20:00:00+00:00", ctype, 40.0)
    repo.close_session(sid, "2026-10-30T02:00:00+00:00", 80.0)
    # No power samples here, so set the estimate close_session would compute.
    repo.conn.execute("UPDATE charging_sessions SET energy_estimate_kwh=? "
                      "WHERE id=?", (energy, sid))
    repo.conn.commit()
    return sid


def test_a_finished_home_charge_says_what_it_added_and_cost(col):
    col._alert_charge_end(_session(col.repo, col.vehicle_id, "AC", 27.0))
    ((title, message),) = col.sent
    assert title == "AC charging finished"
    assert "40 % -> 80 %" in message and "27.0 kWh" in message
    assert "£7.50" in message


def test_a_dc_charge_has_no_home_cost(col):
    col._alert_charge_end(_session(col.repo, col.vehicle_id, "DC", 30.0))
    assert "£" not in col.sent[0][1]


# --- cost -----------------------------------------------------------------------

@pytest.mark.parametrize("session, cfg, expected", [
    ({"energy_estimate_kwh": 27.0, "charge_type": "AC"},
     {"home_tariff_per_kwh": 0.25}, 7.5),
    ({"energy_estimate_kwh": 27.0, "charge_type": "DC"},
     {"home_tariff_per_kwh": 0.25}, None),
    ({"energy_estimate_kwh": None, "charge_type": "AC"},
     {"home_tariff_per_kwh": 0.25}, None),
    ({"energy_estimate_kwh": 27.0, "charge_type": "AC"}, {}, None),
    ({"energy_estimate_kwh": 27.0, "charge_type": "AC"},
     {"home_tariff_per_kwh": 0.25, "home_charging_efficiency": 7}, 7.5),
])
def test_session_cost(session, cfg, expected):
    cost = session_cost(session, Config({"charging": cfg}))
    assert (cost["cost"] if cost else None) == (
        pytest.approx(expected) if expected else None)


@pytest.mark.parametrize("tariff", [0.25, None])
def test_the_charging_page_shows_a_cost_only_with_a_tariff(tmp_path, tariff):
    from web.dashboard import create_app
    db = tmp_path / "car.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle(SYNTHETIC_VIN)
        sid = _session(repo, vid, "AC", 27.0)
    cfg = Config({"database": {"path": str(db)},
                  "charging": {"home_tariff_per_kwh": tariff, "currency": "£",
                               "home_charging_efficiency": 0.9},
                  "analysis": {"charging_window_days": 3650}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"))
    listing = client.get("/charging").text
    detail = client.get(f"/charging/{sid}").text
    if tariff:
        assert "Home cost (estimated)" in listing and "£7.50" in listing
        assert "Home cost (ESTIMATED)" in detail and "30.0 kWh at the meter" in detail
    else:
        assert "Home cost" not in listing and "Home cost" not in detail


def test_a_backup_that_fails_its_check_alerts(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from main import cmd_backup
    sent = []
    monkeypatch.setattr(Notifier, "send",
                        lambda self, title, message, **kw: sent.append(title))
    with Repository(tmp_path / "car.db") as repo:
        repo.ensure_vehicle(SYNTHETIC_VIN)

    def broken(self, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
        return Path(dest)

    monkeypatch.setattr(Repository, "backup", broken)
    cfg = Config({"database": {"path": str(tmp_path / "car.db"),
                               "backup_dir": "b"}})
    assert cmd_backup(cfg) == 1
    assert sent == ["Backup failed its integrity check"]


@pytest.mark.parametrize("with_readings", [True, False])
def test_the_battery_page_shows_the_parked_12v(tmp_path, with_readings):
    from datetime import datetime, timedelta, timezone

    from web.dashboard import create_app
    db = tmp_path / "car.db"
    with Repository(db) as repo:
        vid = repo.ensure_vehicle(SYNTHETIC_VIN)
        if with_readings:
            now = datetime.now(timezone.utc)
            for hours, volts in ((30, 12.31), (2, 12.58)):
                repo.record_measurement(
                    vid, (now - timedelta(hours=hours)).isoformat(),
                    "adapter_12v_voltage", "adapter", "ATRV", None, "V",
                    "reported", "ELM327 ATRV", "documented", f"{volts}V",
                    value=volts)
    client = TestClient(create_app(Config({"database": {"path": str(db)}}),
                                   bind_host="127.0.0.1"))
    page = client.get("/battery").text
    if with_readings:
        assert "12 V battery while parked" in page
        assert "12.58 V" in page and "lowest in 7 days 12.31 V" in page
        assert "/explore?key=adapter_12v_voltage" in page
    else:
        assert "12 V battery while parked" not in page
