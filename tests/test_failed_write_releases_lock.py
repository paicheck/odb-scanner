"""A failed write must not leave the database locked.

POST /investigations/<missing id>/observe failed a foreign-key check, returned
500, and left that worker thread's implicit transaction open -- holding the
write lock, so the collector (another process) got "database is locked" and
silently stopped recording.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


def _other_writer_can_write(db: str) -> bool:
    other = sqlite3.connect(db, timeout=0.5)
    try:
        other.execute("INSERT INTO meta(key, value) VALUES ('probe', 'x') "
                      "ON CONFLICT(key) DO UPDATE SET value='y'")
        other.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def test_a_failed_write_rolls_back_its_transaction(tmp_path):
    db = str(tmp_path / "t.db")
    repo = Repository(db)
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_investigation_evidence(9999, "observe", "measurement", "x")
    assert not repo.conn.in_transaction
    assert _other_writer_can_write(db)
    repo.close()


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path / "web.db")
    r = Repository(db)
    r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"}})
    c = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                   raise_server_exceptions=False)
    c.db = db
    return c


def test_observe_on_a_missing_investigation_is_404_and_leaves_no_lock(client):
    r = client.post("/investigations/9999/observe", data={"key": "pack_voltage"})
    assert r.status_code == 404
    assert _other_writer_can_write(client.db)


def test_finalize_on_a_missing_investigation_is_404(client):
    r = client.post("/investigations/9999/finalize", data={"summary": "x"})
    assert r.status_code == 404
    assert _other_writer_can_write(client.db)
