"""This PC as the archive: the ingest API on SQLite (`main.py ingest`).

The phones' readings have no copy off the phones unless an ingest API takes
them, and the only one the project had needed MySQL. cloud/sqlite_store.py
gives the same API an SQLite archive. Covered: it keeps the MySQL store's
contract (an id is stored once, a retried batch changes nothing, a source's
first_seen survives), both API front-ends serve it, `main.py ingest` refuses
the configurations that would expose the archive or overwrite the car's
database, and `main.py backup` snapshots the archive next to the car's.
"""
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.api import create_app
from cloud.sqlite_store import SqliteStore
from cloud.wsgi import make_application
from config import Config
from database.repository import Repository

TOKEN = "t" * 43
SYNTHETIC_VIN = "WVWZZZE1ZMS000001"


def _measurement(uid: str, **over) -> dict:
    row = {"id": uid, "ts": "2026-10-17T12:00:00.123456789Z",
           "key": "hvb_energy_content_kwh", "vin": SYNTHETIC_VIN,
           "value": 31.4, "unit": "kWh", "provenance": "reported",
           "decoding_method": "scalar", "success": True}
    row.update(over)
    return row


def _batch(*rows, source_id="android:pixel7", kind="android"):
    return {"source": {"source_id": source_id, "kind": kind, "label": "phone"},
            "measurements": list(rows)}


@pytest.fixture(params=["fastapi", "wsgi"])
def api(request, tmp_path):
    store = SqliteStore(tmp_path / "archive" / "archive.db")
    if request.param == "fastapi":
        client = TestClient(create_app(store=store, token=TOKEN))
    else:
        client = httpx.Client(
            transport=httpx.WSGITransport(app=make_application(store=store,
                                                               token=TOKEN)),
            base_url="http://testserver")
    return client, store


def _post(client, body):
    return client.post("/ingest/measurements", json=body,
                       headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_batch_is_stored_once_and_a_retry_changes_nothing(api):
    client, store = api
    ids = [f"00000000-0000-4000-8000-00000000000{i}" for i in range(3)]
    r = _post(client, _batch(*(_measurement(i) for i in ids)))
    assert r.status_code == 200, r.text
    assert (r.json()["accepted"], r.json()["duplicates"]) == (3, 0)

    again = _post(client, _batch(*(_measurement(i) for i in ids)))
    assert (again.json()["accepted"], again.json()["duplicates"]) == (0, 3)
    assert store.counts() == {"measurements": 3, "sources": 1}


def test_a_stored_row_keeps_its_values_and_utc_time(api):
    client, store = api
    uid = "00000000-0000-4000-8000-0000000000aa"
    _post(client, _batch(_measurement(uid, value=12.5)))
    _post(client, _batch(_measurement(uid, value=99.0)))    # same id, new value
    with sqlite3.connect(store.path) as con:
        value, ts, ts_raw, vin = con.execute(
            "SELECT value, ts, ts_raw, vin FROM measurements WHERE id=?",
            (uid,)).fetchone()
    assert value == 12.5, "an existing observation was overwritten"
    assert ts == "2026-10-17T12:00:00.123"
    assert ts_raw == "2026-10-17T12:00:00.123456789Z"
    assert vin == SYNTHETIC_VIN


def test_a_source_keeps_its_first_seen(tmp_path):
    from datetime import datetime
    store = SqliteStore(tmp_path / "a.db")
    store.ensure_schema()
    store.touch_source("python:pc", "python", "pc", datetime(2026, 10, 1, 9))
    store.touch_source("python:pc", "python", "pc", datetime(2026, 10, 30, 9))
    with sqlite3.connect(store.path) as con:
        first, last = con.execute(
            "SELECT first_seen, last_seen FROM sources").fetchone()
    assert first.startswith("2026-10-01") and last.startswith("2026-10-30")


def test_the_token_is_still_required(api):
    client, _ = api
    r = client.post("/ingest/measurements", json=_batch(_measurement(
        "00000000-0000-4000-8000-0000000000bb")))
    assert r.status_code == 401


def test_the_pc_collectors_own_upload_fills_the_archive(tmp_path):
    """The real uploader (cloud/client.py) against a real server on loopback:
    what cloud.api_url http://127.0.0.1:8080 does once odb-ingest runs."""
    import socket
    import threading
    import time

    import uvicorn

    from cloud.client import CloudClient, sync_pending
    from cloud.config import ClientSettings

    store = SqliteStore(tmp_path / "archive.db")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(store=store, token=TOKEN), host="127.0.0.1", port=port,
        log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    settings = ClientSettings(
        enabled=True, api_url=f"http://127.0.0.1:{port}", token=TOKEN,
        source_id="python:test-pc", batch_size=2, sync_interval_s=1.0,
        timeout_s=5.0, verify_tls=True)
    try:
        assert server.started, "ingest server did not start"
        with Repository(tmp_path / "car.db") as repo:
            vid = repo.ensure_vehicle(SYNTHETIC_VIN)
            for minute in range(5):
                repo.record_measurement(
                    vid, f"2026-10-05T18:0{minute}:00+00:00", "soc_abs",
                    "bat_mgmt", "UDS-0x22", "0x028C", "%", "reported",
                    "identity", "experimentally determined", "AB",
                    value=50.0 + minute)
            client = CloudClient(settings)
            try:
                first = sync_pending(repo, client, settings)
                again = sync_pending(repo, client, settings)
            finally:
                client.close()
    finally:
        server.should_exit = True
        thread.join(10)

    assert first == {"batches": 3, "uploaded": 5, "duplicates": 0}
    assert again == {"batches": 0, "uploaded": 0, "duplicates": 0}
    assert store.counts() == {"measurements": 5, "sources": 1}


# --- main.py ingest -------------------------------------------------------------

def _cfg(tmp_path, **ingest) -> Config:
    data = {"database": {"path": str(tmp_path / "car.db")},
            "cloud": {"token": TOKEN},
            "ingest": {"db_path": str(tmp_path / "archive.db"), **ingest}}
    return Config(data)


@pytest.fixture()
def served(monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    return calls


def test_ingest_serves_on_loopback_and_never_prints_the_token(tmp_path, served,
                                                              capsys):
    from main import cmd_ingest
    assert cmd_ingest(_cfg(tmp_path)) == 0
    assert served == [{"host": "127.0.0.1", "port": 8080, "log_level": "info"}]
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("change, why", [
    ({"ingest": {"db_path": None}}, "db_path"),
    ({"ingest": {"db_path": "SAME"}}, "own database"),
    ({"cloud": {"token": ""}}, "without a token"),
    ({"ingest": {"host": "0.0.0.0"}, "cloud": {"token": "local-dev-token"}},
     "guessable"),
])
def test_ingest_refuses_unsafe_configurations(tmp_path, served, capsys,
                                              change, why):
    from main import cmd_ingest
    cfg = _cfg(tmp_path)
    for section, values in change.items():
        for key, value in values.items():
            cfg._data.setdefault(section, {})[key] = (
                cfg._data["database"]["path"] if value == "SAME" else value)
    assert cmd_ingest(cfg) == 2
    assert served == [], "started anyway"
    assert why in capsys.readouterr().out


def test_a_network_bind_with_a_strong_token_is_allowed(tmp_path, served):
    from main import cmd_ingest
    assert cmd_ingest(_cfg(tmp_path, host="0.0.0.0")) == 0
    assert served[0]["host"] == "0.0.0.0"


# --- main.py backup ------------------------------------------------------------

def test_backup_snapshots_the_archive_too(tmp_path, capsys):
    from main import cmd_backup
    with Repository(tmp_path / "car.db") as repo:
        repo.ensure_vehicle(SYNTHETIC_VIN)
    store = SqliteStore(tmp_path / "archive.db")
    client = TestClient(create_app(store=store, token=TOKEN))
    _post(client, _batch(_measurement("00000000-0000-4000-8000-0000000000cc")))
    cfg = Config({"database": {"path": str(tmp_path / "car.db"),
                               "backup_dir": "backups"},
                  "ingest": {"db_path": str(tmp_path / "archive.db")}})
    folder = tmp_path / "backups"
    folder.mkdir()
    old_car = folder / "car-20261001T000000Z.db"
    old_car.write_bytes(b"older car snapshot")

    assert cmd_backup(cfg, keep=1) == 0

    (snap,) = folder.glob("ingest-archive-*.db")
    with sqlite3.connect(snap) as con:
        assert con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 1
    assert len(list(folder.glob("car-2026*Z.db"))) == 1, "car snapshots not pruned"
    assert "Ingest archive backup written" in capsys.readouterr().out


def test_backup_without_an_archive_yet_still_succeeds(tmp_path, capsys):
    from main import cmd_backup
    with Repository(tmp_path / "car.db") as repo:
        repo.ensure_vehicle(SYNTHETIC_VIN)
    cfg = Config({"database": {"path": str(tmp_path / "car.db"),
                               "backup_dir": "backups"},
                  "ingest": {"db_path": str(tmp_path / "not-yet.db")}})
    assert cmd_backup(cfg) == 0
    assert "does not exist yet" in capsys.readouterr().out
    assert not (tmp_path / "not-yet.db").exists()
