"""The dashboard refuses Host names it was not configured for (DNS rebinding).

A page on attacker.example can re-point that name at 127.0.0.1. The browser
then sends Host: attacker.example with Origin http://attacker.example, which
SameOriginMiddleware accepted as same-origin; on a loopback bind, which needs
no token, every page was readable and every POST callable.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    from web.dashboard import create_app
    cfg = Config({"database": {"path": str(tmp_path / "t.db")},
                  "web": {"host": "127.0.0.1",
                          "allowed_hosts": ["car-pc.home.arpa"]}})
    return create_app(cfg, bind_host="127.0.0.1")


def _client(app, host):
    from fastapi.testclient import TestClient
    return TestClient(app, base_url=f"http://{host}")


def test_a_rebound_name_is_refused_on_get_and_post(app):
    c = _client(app, "attacker.example:8000")
    assert c.get("/").status_code == 400
    assert c.get("/api/progress").status_code == 400
    r = c.post("/investigations/new", data={"title": "x", "symptom": "y"},
               headers={"Origin": "http://attacker.example:8000"})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000",
                                  "[::1]:8000", "192.168.1.20:8000",
                                  "car-pc.home.arpa:8000"])
def test_legitimate_hosts_are_served(app, host):
    assert _client(app, host).get("/").status_code == 200


def test_this_machines_hostname_is_allowed(app):
    import socket
    assert _client(app, socket.gethostname().lower()).get("/").status_code == 200
