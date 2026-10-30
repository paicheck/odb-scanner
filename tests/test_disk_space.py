"""The drive holding the database running low is said, before it fills.

When it fills, SQLite refuses every write: the collector survives that, but
the readings of the drive are lost with nothing on screen to say why.
"""
import logging
import os
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.repository as repository  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

Usage = namedtuple("Usage", "total used free")


def _client(path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": path}, "web": {"host": "127.0.0.1"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      base_url="http://127.0.0.1:8000")


def test_disk_space_reports_free_space_and_database_size(tmp_path):
    with Repository(str(tmp_path / "d.db")) as r:
        space = r.disk_space()
        assert space["free"] > 0 and space["db"] > 0
        assert space["dir"] == str(tmp_path)
    with Repository(":memory:") as r:
        assert r.disk_space() is None


def test_every_page_warns_when_the_drive_is_low(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    Repository(path).close()
    monkeypatch.setattr(repository.shutil, "disk_usage",
                        lambda _: Usage(100, 99, 300 * 1024 ** 2))
    client = _client(path)
    for page in ("/", "/trips", "/battery"):
        text = client.get(page).text
        assert "almost full" in text and "0.3 GB free" in text, page
    monkeypatch.setattr(repository.shutil, "disk_usage",
                        lambda _: Usage(100, 99, 50 * 1024 ** 3))
    assert "GB free" not in _client(path).get("/").text


def test_the_collector_logs_a_low_drive_every_ten_minutes(tmp_path, monkeypatch,
                                                         caplog):
    import collector as collector_mod

    monkeypatch.setattr(repository.shutil, "disk_usage",
                        lambda _: Usage(100, 99, 1024 ** 3))
    clock = [1000.0]
    monkeypatch.setattr(collector_mod.time, "monotonic", lambda: clock[0])
    with Repository(str(tmp_path / "c.db")) as r:
        c = collector_mod.Collector.__new__(collector_mod.Collector)
        c.repo = r
        c._disk_checked = -collector_mod.DISK_CHECK_S
        # The alert itself is tested in tests/test_alerts.py.
        c.notifier = type("Quiet", (), {"send": lambda *a, **k: None})()
        with caplog.at_level(logging.WARNING, logger="collector"):
            c._check_disk()
            clock[0] += 60
            c._check_disk()                              # too soon: quiet
            clock[0] += collector_mod.DISK_CHECK_S
            c._check_disk()
    warnings = [m for m in caplog.messages if "free on the drive" in m]
    assert len(warnings) == 2 and "1.0 GB free" in warnings[0]
