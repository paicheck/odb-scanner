"""Tests for the timeline / event-context / DTC drill-down investigation pages.

All data is synthetic, held in throwaway databases. None of it describes a real
car; the point is the *flow*: browse the merged event timeline, open one
moment's context, drill into a fault code, and seed an investigation -- without
the dashboard ever writing a byte to the vehicle. Every route under test is a
read-only consumer of SQLite, so the strongest safety assertion here is that
browsing them leaves `tx_log` (the byte-level transmission manifest) empty.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _record(repo, key, ts, value, *, ecu="bat_mgmt", unit="V",
            provenance="reported", raw="AB", success=True, error=None):
    return repo.record_measurement(
        repo.vehicle_id, ts, key, ecu, "22", "F1A1", unit, provenance,
        "identity", "experimentally determined", raw, value=value,
        success=success, error=error)


@pytest.fixture()
def repo(tmp_path):
    from database.repository import Repository
    r = Repository(tmp_path / "timeline.db")
    r.vehicle_id = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    yield r
    r.close()


# ------------------------------------------------------------------ context
def test_measurement_context_returns_closest_value_per_key(repo):
    from analysis.context import measurement_context

    center = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    _record(repo, "soc_abs", _iso(center), 50.0, unit="%")
    _record(repo, "soc_abs", _iso(center + timedelta(minutes=5)), 51.0, unit="%")
    _record(repo, "pack_voltage", _iso(center + timedelta(minutes=1)), 400.0)

    rows = measurement_context(repo, repo.vehicle_id, _iso(center), window_s=3600)
    by_key = {r["key"]: r for r in rows}
    # soc_abs has a reading exactly at the anchor: that one wins over the one
    # five minutes later.
    assert by_key["soc_abs"]["value"] == 50.0
    assert by_key["pack_voltage"]["delta_s"] == 60.0
    # Sorted by absolute distance: the exact anchor first.
    assert rows[0]["key"] == "soc_abs"


def test_measurement_context_respects_window_and_unparseable_anchor(repo):
    from analysis.context import measurement_context

    center = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    _record(repo, "soc_abs", _iso(center + timedelta(hours=5)), 50.0, unit="%")
    assert measurement_context(repo, repo.vehicle_id, _iso(center),
                               window_s=3600) == []
    assert measurement_context(repo, repo.vehicle_id, "not-a-date") == []


def test_measurement_context_keeps_provenance_and_text_values(repo):
    from analysis.context import measurement_context

    center = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
    # A text-valued reading (gear = "P") must be returned with its text_value,
    # and an imported reading must keep its provenance rather than being
    # re-labelled live.
    repo.record_measurement(repo.vehicle_id, _iso(center), "gear", "veh_info",
                            "22", "F1A1", "", "imported: Car Scanner CSV",
                            "identity", "experimentally determined", "",
                            value=None, text_value="P", success=True)
    rows = measurement_context(repo, repo.vehicle_id, _iso(center))
    assert rows[0]["key"] == "gear"
    assert rows[0]["text_value"] == "P"
    assert rows[0]["provenance"] == "imported: Car Scanner CSV"


# ------------------------------------------------------------------- pages
@pytest.fixture()
def client(tmp_path):
    from config import Config
    from database.repository import Repository
    from web.dashboard import create_app

    db = tmp_path / "web.db"
    cfg = Config({
        "database": {"path": str(db)},
        "vehicle": {"did_profile": "meb"},
        "web": {"host": "127.0.0.1"},
        "collector": {"max_value_age_s": 900, "slow_poll_interval": 60,
                      "poll_interval": 5},
        "analysis": {"trend_window_days": 30, "charging_window_days": 90},
    })
    r = Repository(str(db))
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    r.vehicle_id = vid
    now = datetime.now(timezone.utc)
    for i in range(10):
        _record(r, "soc_abs", _iso(now - timedelta(minutes=i)), 50 + i, unit="%")
    dtc_id = r.upsert_dtc(
        vid, _iso(now - timedelta(minutes=5)), "eldrive", "U112300",
        "Data bus: received invalid data / missing message",
        ["communication", "informational"], status="warning",
        freeze_frame={"status_byte": 0x28,
                      "snapshot": [{"records": [{"record": 1,
                                                 "data": {"0xF1A1": "ABCD"}}]}]})
    r.add_event(vid, _iso(now - timedelta(minutes=5)), "note",
                "something happened")
    r.close()

    app = create_app(cfg, bind_host="127.0.0.1")
    from fastapi.testclient import TestClient
    with TestClient(app, follow_redirects=True) as c:
        c.db_path = str(db)
        c.dtc_id = dtc_id
        c.anchor_ts = _iso(now - timedelta(minutes=5))
        yield c


def test_timeline_page_renders_and_links_events(client):
    r = client.get("/timeline")
    assert r.status_code == 200, r.text[:500]
    assert "something happened" in r.text
    assert "U112300" in r.text          # the DTC occurrence is on the timeline
    assert "/event?ts=" in r.text       # events are clickable


def test_timeline_kind_filter_hides_other_kinds(client):
    r = client.get("/timeline", params={"kind": "note"})
    assert r.status_code == 200
    assert "something happened" in r.text
    assert "U112300" not in r.text      # the dtc kind is filtered out


def test_event_page_shows_anchor_events_and_nearby_measurements(client):
    r = client.get("/event", params={"ts": client.anchor_ts,
                                     "title": "Test event"})
    assert r.status_code == 200, r.text[:500]
    assert "Test event" in r.text
    assert "soc_abs" in r.text          # a measurement recorded nearby
    assert "U112300" in r.text          # the dtc event in the ±window


def test_event_page_without_vehicle_or_ts_is_safe(client):
    # A fresh app has no vehicle; /event with no ts must still render.
    assert client.get("/event").status_code == 200
    assert client.get("/event", params={"ts": "not-a-date"}).status_code == 200


def test_dtc_detail_page_shows_occurrence_and_raw_snapshot(client):
    r = client.get(f"/dtcs/{client.dtc_id}")
    assert r.status_code == 200, r.text[:500]
    assert "U112300" in r.text
    assert "ABCD" in r.text             # raw freeze-frame record, kept raw
    assert "investigate this fault" in r.text


def test_dtc_detail_unknown_id_is_404(client):
    assert client.get("/dtcs/9999").status_code == 404


def test_dtc_list_links_each_code_to_its_detail(client):
    r = client.get("/dtcs")
    assert r.status_code == 200
    assert f'/dtcs/{client.dtc_id}' in r.text


def test_investigation_from_event_seeds_observation_and_association(client):
    r = client.post("/investigations/new", data={
        "title": "T", "symptom": "s", "anchor_key": "soc_abs",
        "anchor_ts": client.anchor_ts, "criteria": "c"})
    assert r.status_code == 200
    body = client.get("/api/export/investigation/1?format=json").json()
    stages = {e["stage"] for e in body["evidence"]}
    assert "observation" in stages     # anchor key was observed
    assert "association" in stages     # nearby events were associated


def test_browsing_timeline_and_event_never_writes_to_the_vehicle(client):
    """The safety manifest (tx_log) records every byte transmitted. Browsing the
    new pages must leave it empty: reading the dashboard is never a diagnostic
    request."""
    import sqlite3

    client.get("/timeline")
    client.get("/event", params={"ts": client.anchor_ts})
    client.get(f"/dtcs/{client.dtc_id}")

    con = sqlite3.connect(client.db_path)
    try:
        tx = con.execute("SELECT COUNT(*) FROM tx_log").fetchone()[0]
    finally:
        con.close()
    assert tx == 0
