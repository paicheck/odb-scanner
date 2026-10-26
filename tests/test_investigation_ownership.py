"""Investigation routes check which vehicle owns the investigation.

They used to load by id alone: once a newer vehicle took over first_vehicle(),
the old vehicle's investigation disappeared from the list but stayed readable
and exportable by id, and observe/finalize attached the new vehicle's
measurements to it and overwrote its conclusion. A resolved investigation
could also be changed after the fact.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import investigation as inv_mod  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402


@pytest.fixture()
def setup(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    db = str(tmp_path / "web.db")
    r = Repository(db)
    old = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    old_inv = inv_mod.open_investigation(r, old, "old car", "symptom").id
    # A later first_seen makes this the dashboard's current vehicle.
    r.conn.execute("UPDATE vehicles SET first_seen='2000-01-01T00:00:00+00:00' "
                   "WHERE id=?", (old,))
    r.conn.commit()
    new = r.ensure_vehicle("SYNTHETIC00000001", make="VW", model="ID.4")
    new_inv = inv_mod.open_investigation(r, new, "new car", "symptom").id
    r.close()
    cfg = Config({"database": {"path": db}, "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"))
    return client, db, old_inv, new_inv


def test_another_vehicles_investigation_is_not_found(setup):
    client, db, old_inv, new_inv = setup
    assert client.get(f"/investigations/{new_inv}").status_code == 200
    assert client.get(f"/investigations/{old_inv}").status_code == 404
    assert client.get(f"/api/export/investigation/{old_inv}").status_code == 404
    assert client.post(f"/investigations/{old_inv}/observe",
                       data={"key": "pack_voltage"}).status_code == 404
    assert client.post(f"/investigations/{old_inv}/finalize",
                       data={"summary": "hijacked"}).status_code == 404
    r = Repository(db)
    row = r.investigation(old_inv)
    assert row["summary"] != "hijacked"
    assert r.investigation_evidence(old_inv) == []
    r.close()


def test_a_resolved_investigation_cannot_be_changed(setup):
    client, db, _, new_inv = setup
    r = Repository(db)
    r.update_investigation(new_inv, status="resolved", conclusion="CONFIRMED.")
    with pytest.raises(ValueError):
        inv_mod.finalize(r, new_inv, summary="again", criteria_confirmed=True)
    r.close()
    assert client.post(f"/investigations/{new_inv}/observe",
                       data={"key": "pack_voltage"}).status_code == 409
    assert client.post(f"/investigations/{new_inv}/finalize",
                       data={"summary": "rewrite"}).status_code == 409
    assert client.get(f"/investigations/{new_inv}").status_code == 200
