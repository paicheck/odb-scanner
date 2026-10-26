"""WSGI-specific tests for the cPanel ingest app.

The HTTP contract itself (auth, limits, validation, duplicates) is tested
against both this app and the FastAPI one by the parametrised `client` fixture
in test_cloud_api.py. What is left here is what only the WSGI deploy has: its
startup rules and the Passenger entry point.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloud.config import CloudConfigError  # noqa: E402
from cloud.wsgi import make_application  # noqa: E402
from tests.test_cloud_api import TOKEN, FakeStore  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def test_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv("ODB_CLOUD_API_TOKEN", raising=False)
    with pytest.raises(CloudConfigError):
        make_application(store=FakeStore(), token="")
    with pytest.raises(CloudConfigError):
        make_application(store=FakeStore())


@pytest.mark.skipif((REPO / "cloud" / "cpanel.env").exists(),
                    reason="a real cpanel.env would leak into the environment")
def test_passenger_entry_point_builds_the_app(monkeypatch):
    monkeypatch.setenv("ODB_CLOUD_API_TOKEN", TOKEN)
    monkeypatch.setenv("ODB_CLOUD_DB_HOST", "localhost")
    monkeypatch.setenv("ODB_CLOUD_DB_USER", "u")
    monkeypatch.setenv("ODB_CLOUD_DB_NAME", "d")
    spec = importlib.util.spec_from_file_location(
        "passenger_wsgi_under_test", REPO / "cloud" / "passenger_wsgi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.application)
