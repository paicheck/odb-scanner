"""The dashboard's data-quality layer.

* analysis.data_quality: sessions split at the chart gap threshold; live,
  imported and simulated rows counted apart; reads answered; signals by
  decoder evidence; readings outside their documented range.
* The overview tiles show the newest reading of each named signal with its
  age. "State of charge" used to show the DISPLAY SoC from the last battery
  snapshot above a chart of the BMS SoC -- 92.9 % beside 88.8 %, no label,
  no time.
* Panels carry a scannable quality row per series (evidence badge, latest
  value and age, flags) instead of a paragraph.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import data_quality  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _rec(repo, vid, ts, key, value, prov="reported", success=True, unit="V"):
    repo.record_measurement(vid, _iso(ts), key, "bat_mgmt", "UDS-0x22", "0x1E3B",
                            unit, prov, "builtin", "experimental", "", value,
                            None, success, None if success else "NO DATA")


def test_sessions_split_at_the_gap_threshold():
    t0 = NOW - timedelta(days=2)
    ts = [_iso(t0 + timedelta(minutes=m)) for m in (0, 5, 10, 200, 205)]
    s = data_quality.sessions(ts, gap_s=900)
    assert [x["readings"] for x in s] == [3, 2]
    assert s[0]["duration_s"] == 600


@pytest.fixture()
def repo(tmp_path):
    r = Repository(str(tmp_path / "q.db"))
    vid = r.ensure_vehicle("SYNTHETIC00000000", make="VW", model="ID.3")
    t0 = NOW - timedelta(days=3)
    for m in range(3):                                    # session 1
        _rec(r, vid, t0 + timedelta(minutes=m), "pack_voltage", 400.0)
    _rec(r, vid, t0 + timedelta(minutes=3), "pack_voltage", 9999.0)   # implausible
    _rec(r, vid, t0 + timedelta(minutes=4), "pack_voltage", None, success=False)
    t1 = NOW - timedelta(hours=5)                         # session 2
    _rec(r, vid, t1, "soc_abs", 88.8, unit="%")
    _rec(r, vid, t1, "soc_normal", 92.9, unit="%")
    _rec(r, vid, t1, "pack_voltage", 1.98, prov="imported: Car Scanner CSV")
    _rec(r, vid, t1, "pack_voltage", 401.0, prov="simulated")
    # The battery snapshot carries the DISPLAY SoC; the tile must not use it.
    r.record_battery_snapshot(vid, _iso(t1 - timedelta(minutes=20)),
                              soc_abs_pct=90.0, soc_normal_pct=93.0)
    r.conn.commit()
    r.vid = vid
    yield r
    r.close()


def test_snapshot_counts_live_imported_simulated_apart(repo):
    q = data_quality.snapshot(repo, repo.vid, 30, "meb", 900.0)
    assert q["sessions"] == 2
    assert q["live_rows"] == 6 and q["imported_rows"] == 1 and q["simulated_rows"] == 1
    assert q["reads"] == 7 and q["reads_ok"] == 6
    assert q["last_ts"] == _iso(NOW - timedelta(hours=5))
    assert q["implausible"] == [{"key": "pack_voltage", "count": 1,
                                 "range": [200.0, 500.0]}]
    assert sum(q["evidence"].values()) + q["uncatalogued"] == q["signals"] == 3


@pytest.mark.parametrize("delta,want", [
    (timedelta(seconds=30), "just now"), (timedelta(minutes=12), "12 min ago"),
    (timedelta(hours=5), "5 h ago"), (timedelta(days=3), "3 d ago")])
def test_age_filter(delta, want):
    from web.dashboard import _format_age
    # Measured from the real clock, not the module-level NOW: the suite can take
    # minutes to reach this test, and "12 min ago" then reads "13 min ago".
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert _format_age(_iso(now - delta)) == want
    assert _format_age(None) == "age unknown"


@pytest.fixture()
def client(repo):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    cfg = Config({"database": {"path": repo.path}, "web": {"host": "127.0.0.1"},
                  "vehicle": {"did_profile": "meb"}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"))


def test_overview_shows_data_quality_and_honest_tiles(client):
    html = client.get("/").text
    assert "Data quality" in html and "Reads answered" in html
    assert "86 %" in html                          # 6 of 7 reads answered
    # The SoC tile is the BMS figure from the newest reading, with its age,
    # and names the display SoC as a different definition.
    assert "State of charge (BMS)" in html and "88.8 %" in html
    assert "5 h ago" in html
    assert "92.9 % (a different definition)" in html
    assert "93%" not in html


def test_panels_carry_quality_rows(client):
    html = client.get("/battery").text
    assert 'class="series-row"' in html
    assert 'class="ev ev-' in html                 # evidence badge
    assert "outside plausible range" in html       # the 9999 V reading
    assert "imported" in html
