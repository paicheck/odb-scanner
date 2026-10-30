"""Repository fixes from the review.

* SchemaMismatchError was raised only after the schema script had already
  created tables, applied ALTERs and switched the journal mode.
* Connections of finished threads were never closed (the dashboard's worker
  pool retires idle threads within seconds).
* A hard prune deleted the early samples of a session still in progress.
* `main.py prune --hard` never vacuumed.
"""
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import SCHEMA_VERSION  # noqa: E402
from database.repository import Repository, SchemaMismatchError  # noqa: E402


def test_a_refused_database_is_left_untouched(tmp_path):
    db = str(tmp_path / "old.db")
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    raw.execute("INSERT INTO meta VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION - 1),))
    raw.commit()
    mode_before = raw.execute("PRAGMA journal_mode").fetchone()[0]
    raw.close()

    with pytest.raises(SchemaMismatchError):
        Repository(db)

    raw = sqlite3.connect(db)
    tables = [r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    mode_after = raw.execute("PRAGMA journal_mode").fetchone()[0]
    raw.close()
    assert tables == ["meta"]
    assert mode_after == mode_before


def test_connections_of_finished_threads_are_released(tmp_path):
    repo = Repository(str(tmp_path / "t.db"))

    def touch():
        repo.conn.execute("SELECT 1").fetchone()

    for _ in range(100):
        t = threading.Thread(target=touch)
        t.start()
        t.join()
    # The sweep runs when a connection is opened, so open one more from a
    # fresh thread; every earlier worker has finished by now.
    t = threading.Thread(target=touch)
    t.start()
    t.join()
    # Only connections of live threads (this one, plus at most the last
    # worker) may remain.
    assert len(repo._conns) <= 3, len(repo._conns)
    repo.close()


def test_hard_prune_keeps_samples_of_an_open_session(tmp_path):
    repo = Repository(str(tmp_path / "p.db"))
    vid = repo.ensure_vehicle("WVWZZZE1ZMS000001")
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(
        timespec="seconds")
    open_sid = repo.open_session(vid, old, "AC", 20.0)
    done_sid = repo.open_session(vid, old, "DC", 30.0)
    for sid in (open_sid, done_sid):
        repo.add_charging_sample(sid, old, voltage_v=400.0, current_a=10.0,
                                 power_kw=4.0, soc=21.0, battery_temp_c=20.0)
    repo.close_session(done_sid, old, 31.0)
    repo.prune(days=1, keep_raw=False)
    left = {r["session_id"] for r in repo.conn.execute(
        "SELECT session_id FROM charging_samples")}
    assert left == {open_sid}
    repo.close()


def test_hard_prune_vacuums(tmp_path, monkeypatch):
    import main
    from config import Config
    calls = []
    monkeypatch.setattr(Repository, "vacuum", lambda self: calls.append(1))
    cfg = Config({"database": {"path": str(tmp_path / "v.db")}})
    main.cmd_prune(cfg, 30.0, hard=True)
    assert calls == [1]
