"""Statistics can exclude imported and simulated rows.

measurement_series returned every successful row regardless of provenance, so
anomaly baselines mixed Car Scanner imports (another tool's sign convention)
and simulator output with what the car actually reported.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repository import Repository  # noqa: E402


def test_live_only_drops_imported_and_simulated(tmp_path):
    repo = Repository(str(tmp_path / "t.db"))
    vid = repo.ensure_vehicle("WVWZZZE1ZMP087053")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for prov, value in (("reported", -5.0), ("imported: Car Scanner CSV", 1.98),
                        ("simulated", 42.0), ("calculated", -4.0)):
        repo.record_measurement(vid, ts, "pack_current", "bat_mgmt", "UDS-0x22",
                                "0x1E3D", "A", prov, "builtin", "experimental",
                                "", value)
    repo.conn.commit()
    everything = repo.measurement_series(vid, "pack_current", 30)
    live = repo.measurement_series(vid, "pack_current", 30, live_only=True)
    assert len(everything) == 4
    assert sorted(r["value"] for r in live) == [-5.0, -4.0]
    repo.close()
