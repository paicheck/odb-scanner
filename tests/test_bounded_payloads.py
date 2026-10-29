"""Pages and APIs stay a bounded size however long the car has been read.

A month of 10 s readings made /range 5.6 MB (31k scatter points) and
/api/battery 13 MB of JSON, and the cell-sweep chart carried every sweep with
a note -- all growing for as long as the collector runs.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.chart_semantics import thin_pairs  # noqa: E402
from config import Config  # noqa: E402
from database.repository import Repository  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def test_thin_pairs_keeps_the_spread_and_the_extremes():
    pairs = [{"x": i % 97, "y": (i * 37) % 1000, "ts_a": str(i)} for i in range(10_000)]
    pairs[4321]["y"] = 5000.0                            # one outlier
    shown = thin_pairs(pairs, 500)
    assert len(shown) <= 505
    assert shown[0] is pairs[0] and shown[-1] is pairs[-1]
    assert any(p["y"] == 5000.0 for p in shown)
    for axis in ("x", "y"):
        assert min(p[axis] for p in shown) == min(p[axis] for p in pairs)
        assert max(p[axis] for p in shown) == max(p[axis] for p in pairs)
    assert [int(p["ts_a"]) for p in shown] == sorted(int(p["ts_a"]) for p in shown)
    assert thin_pairs(pairs[:10], 500) == pairs[:10]


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from web.dashboard import create_app
    path = str(tmp_path / "b.db")
    with Repository(path) as r:
        vid = r.ensure_vehicle("WVWZZZE1ZMS000001")
        rows, snaps = [], []
        for i in range(3000):
            ts = (NOW - timedelta(seconds=10 * (3000 - i))).isoformat()
            rows.append((vid, ts, "soc_abs", 50 + i / 100))
            rows.append((vid, ts, "range_displayed", 100 + i / 50))
            snaps.append((vid, ts, 50 + i / 100))
        r.conn.executemany(
            "INSERT INTO battery_measurements(vehicle_id, ts, soc_abs_pct) "
            "VALUES (?, ?, ?)", snaps)
        r.conn.executemany(
            "INSERT INTO measurements(vehicle_id, ts, key, ecu, value, unit, "
            "provenance, decoding_method, success) "
            "VALUES (?, ?, ?, 'bat_mgmt', ?, '', 'reported', 'builtin', 1)", rows)
        r.conn.commit()
    cfg = Config({"database": {"path": path}, "web": {"host": "127.0.0.1"},
                  "analysis": {"trend_window_days": 30}})
    return TestClient(create_app(cfg, bind_host="127.0.0.1"),
                      base_url="http://127.0.0.1:8000")


def test_range_scatter_is_capped_and_says_so(client):
    page = client.get("/range").text
    m = re.search(r'<script type="application/json" id="panel-data">(.*?)</script>',
                  page, re.S)
    scatter = json.loads(m.group(1))["scatter"]
    assert len(scatter["pairs"]) <= 2005
    assert "3000 pairs" in page and "The chart" in page and "shows" in page


def test_battery_api_is_capped_and_spread_evenly(client):
    rows = client.get("/api/battery").json()
    assert len(rows) == 2000
    assert rows == sorted(rows, key=lambda r: r["ts"])
    assert rows[-1]["soc_abs_pct"] == pytest.approx(50 + 2999 / 100)
    assert len(client.get("/api/battery?max_points=50").json()) == 50
    assert len(client.get("/api/battery?max_points=100000").json()) == 3000
