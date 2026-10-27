"""Investigation form fields are length-capped.

They are stored permanently and rendered on /investigations; the only limit
was Starlette's ~1 MB per field.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path / "f.db")
    r = Repository(db)
    r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"))


@pytest.mark.parametrize("field,limit", [("title", 200), ("symptom", 2000),
                                         ("criteria", 2000), ("anchor_key", 80)])
def test_overlong_fields_are_refused(client, field, limit):
    data = {"title": "t", "symptom": "s", field: "x" * (limit + 1)}
    assert client.post("/investigations/new", data=data).status_code == 422


def test_normal_fields_are_accepted(client):
    r = client.post("/investigations/new", data={"title": "t", "symptom": "s"},
                    follow_redirects=False)
    assert r.status_code == 303
