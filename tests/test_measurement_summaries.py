"""Measurement summaries: a cache of `measurements` that must never disagree
with it.

The overview, discovery, module status and progress figures come from
`measurement_hours` / `measurement_minutes` instead of scanning every reading.
These tests hold the summaries to the raw aggregates exactly -- through
inserts folded in several steps, deletes (old rows and the newest, whose ids
are then handed out again), edits, and a raw-bytes-only prune -- and check the
fallback when the summaries cannot be refreshed.
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repository import Repository  # noqa: E402

PROVENANCES = ("reported", "imported: Car Scanner CSV", "simulated", "calculated")
COLUMNS = ("key", "ecu", "kind", "n", "ok", "vmin", "vmax", "last_ok", "last_ts")
WINDOWS = (None, "2026-10-01T00:00:00+00:00", "2026-10-02T07:31:15+00:00",
           "2026-10-03T23:59:59+00:00")


@pytest.fixture()
def repo(tmp_path):
    r = Repository(str(tmp_path / "s.db"))
    r.SUMMARY_BATCH = 37               # fold in many small steps
    r.vids = (r.ensure_vehicle("WVWZZZE1ZMS000001"),
              r.ensure_vehicle("WVWZZZE1ZMS000002"))
    r.rng = random.Random(3)
    yield r
    r.close()


def _add(repo, n):
    rng = repo.rng
    for _ in range(n):
        ts = (f"2026-10-{rng.randint(1, 3):02d}T{rng.randint(0, 23):02d}:"
              f"{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}+00:00")
        ok = rng.random() > 0.2
        value = rng.choice([rng.uniform(-5, 5), None]) if ok else None
        repo.record_measurement(
            rng.choice(repo.vids), ts, rng.choice("abc"),
            rng.choice(["x", "y", None]), "UDS-0x22", "0x0001", "V",
            rng.choice(PROVENANCES), "builtin", "x", "", value, None, ok,
            None if ok else "NO DATA")


def _raw_totals(repo, vid, since):
    sql = (f"SELECT key, COALESCE(ecu, '') AS ecu, {repo._KIND} AS kind, "
           f"{repo._TOTALS} FROM measurements WHERE vehicle_id=? "
           + ("AND ts >= ? " if since else "") + "GROUP BY key, ecu, kind")
    args = (vid, since) if since else (vid,)
    return sorted((tuple(r) for r in repo.conn.execute(sql, args)), key=str)


def _raw_minutes(repo, vid, since):
    return [tuple(r) for r in repo.conn.execute(
        "SELECT substr(ts, 1, 16), COUNT(*), MIN(ts), MAX(ts) FROM measurements "
        f"WHERE vehicle_id=? AND success=1 AND {repo._KIND} = 'live' "
        "AND ts >= ? GROUP BY 1 ORDER BY 1", (vid, since))]


def _agrees(repo):
    for vid in repo.vids:
        for since in WINDOWS:
            got = sorted((tuple(t[c] for c in COLUMNS)
                          for t in repo.measurement_totals(vid, since)), key=str)
            assert got == _raw_totals(repo, vid, since), (vid, since)
            if since:
                minutes = [tuple(m.values())
                           for m in repo.activity_minutes(vid, since)]
                assert minutes == _raw_minutes(repo, vid, since), (vid, since)


def test_summaries_equal_the_readings_through_every_kind_of_change(repo):
    _add(repo, 500)
    _agrees(repo)
    _add(repo, 300)                                      # folded incrementally
    _agrees(repo)
    repo.conn.execute("DELETE FROM measurements WHERE id % 7 = 0")
    repo.conn.commit()
    _agrees(repo)
    newest = repo.max_measurement_id()                   # ids reused next
    repo.conn.execute("DELETE FROM measurements WHERE id > ?", (newest - 50,))
    repo.conn.commit()
    _add(repo, 80)
    _agrees(repo)
    repo.conn.execute("UPDATE measurements SET value = value + 100 "
                      "WHERE id % 5 = 0 AND value IS NOT NULL")
    repo.conn.commit()
    _agrees(repo)


def test_a_delete_or_edit_marks_them_stale_and_dropping_raw_bytes_does_not(repo):
    _add(repo, 50)
    assert repo.refresh_summaries()
    repo.prune(days=0, keep_raw=True)                    # raw bytes only
    assert repo._summary_state()[1] is False
    repo.conn.execute("UPDATE measurements SET success = 0 WHERE id = 1")
    repo.conn.commit()
    assert repo._summary_state()[1] is True
    assert repo.refresh_summaries()
    assert repo._summary_state() == (repo.max_measurement_id(), False)
    repo.conn.execute("DELETE FROM measurements WHERE id = 2")
    repo.conn.commit()
    assert repo._summary_state()[1] is True
    _agrees(repo)


def test_readings_are_read_directly_when_the_summaries_cannot_be_refreshed(repo):
    _add(repo, 200)
    assert repo.refresh_summaries()
    _add(repo, 100)                                      # not yet folded in
    expected = {since: _raw_totals(repo, repo.vids[0], since) for since in WINDOWS}
    repo.conn.execute("INSERT INTO meta(key, value) VALUES ('pending', 'x')")
    assert repo.conn.in_transaction                      # a caller's open write
    assert repo.refresh_summaries() is False
    for since in WINDOWS:
        got = sorted((tuple(t[c] for c in COLUMNS)
                      for t in repo.measurement_totals(repo.vids[0], since)),
                     key=str)
        assert got == expected[since]
    repo.conn.rollback()


def test_the_window_edge_is_exact(repo):
    vid = repo.vids[0]
    for ts in ("2026-10-02T07:30:59+00:00", "2026-10-02T07:31:15+00:00",
               "2026-10-02T07:31:40+00:00", "2026-10-02T07:59:00+00:00",
               "2026-10-02T08:00:00+00:00"):
        repo.record_measurement(vid, ts, "a", "x", "UDS-0x22", "0x0001", "V",
                                "reported", "builtin", "x", "", 1.0)
    since = "2026-10-02T07:31:15+00:00"
    [t] = repo.measurement_totals(vid, since)
    assert (t["n"], t["last_ts"]) == (4, "2026-10-02T08:00:00+00:00")
    minutes = repo.activity_minutes(vid, since)
    assert [(m["minute"], m["n"]) for m in minutes] == [
        ("2026-10-02T07:31", 2), ("2026-10-02T07:59", 1), ("2026-10-02T08:00", 1)]


def test_progress_totals_come_from_the_summaries(tmp_path):
    from fastapi.testclient import TestClient

    from config import Config
    from web.dashboard import create_app

    path = str(tmp_path / "p.db")
    with Repository(path) as r:
        vid = r.ensure_vehicle("WVWZZZE1ZMS000001")
        for i in range(7):
            r.record_measurement(vid, f"2026-10-02T07:3{i}:00+00:00", "a", "x",
                                 "UDS-0x22", "0x0001", "V", "reported",
                                 "builtin", "x", "", 1.0)
    cfg = Config({"database": {"path": path}, "web": {"host": "127.0.0.1"}})
    client = TestClient(create_app(cfg, bind_host="127.0.0.1"),
                        base_url="http://127.0.0.1:8000")
    body = client.get("/api/progress").json()
    assert body["totals"]["measurements"] == 7
    with Repository(path) as r:
        assert r._summary_state() == (7, False)
