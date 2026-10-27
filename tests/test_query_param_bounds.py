"""Bad query parameters get a 4xx or a clamped default, never a 500.

These routes parsed parameters with a bare int(), so ?days=x was a 500, and
?days=999999999 overflowed a timedelta; /event?ts=9999-12-31T23:00:00 parsed
but overflowed when the window was added.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path_factory.mktemp("q") / "q.db")
    r = Repository(db)
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    r.record_measurement(vid, ts, "pack_voltage", "bat_mgmt", "UDS-0x22",
                         "0x1E3B", "V", "reported", "builtin", "experimental",
                         "0690", 420.0)
    r.conn.commit()
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      raise_server_exceptions=False)


BAD = ["days=x", "days=999999999", "days=-5", "max_points=x",
       "max_points=999999999", "limit=x", "limit=-1"]
ROUTES = ["/quality", "/explore", "/api/discover", "/api/series?key=pack_voltage",
          "/api/timeline", "/api/export/series?key=pack_voltage"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("query", BAD)
def test_bad_numbers_never_500(client, route, query):
    sep = "&" if "?" in route else "?"
    r = client.get(f"{route}{sep}{query}")
    assert r.status_code < 500, (route, query, r.status_code)


@pytest.mark.parametrize("ts", ["9999-12-31T23:00:00", "0001-01-01T00:00:00",
                                "not-a-date"])
def test_event_page_survives_extreme_timestamps(client, ts):
    assert client.get("/event", params={"ts": ts}).status_code < 500
