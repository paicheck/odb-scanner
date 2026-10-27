"""/api/export/series resolves aliases and builds a safe filename.

It exported zero rows for an alias the chart resolved and showed, and built
Content-Disposition from the raw key: `a"b` broke the quoting, and a key such
as `€` raised while encoding the header (500).
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path / "e.db")
    r = Repository(db)
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.record_measurement(vid, datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "res_pid_0149", "-", "OBD-01", "0x49", "%",
        "reported", "builtin", "documented", "41", 12.0)
    r.conn.commit()
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      raise_server_exceptions=False)


def test_an_alias_exports_the_canonical_rows(client):
    r = client.get("/api/export/series", params={"key": "accelerator_pedal_pct",
                                                 "format": "json"})
    assert r.status_code == 200
    assert r.json()["row_count"] == 1


@pytest.mark.parametrize("key", ['a"b', "€", r"x\y"])
def test_odd_keys_give_a_well_formed_header(client, key):
    r = client.get("/api/export/series", params={"key": key})
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert disposition.count('"') == 2
    assert disposition.isascii()
