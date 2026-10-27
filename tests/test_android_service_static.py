"""Static checks on the Android collector service (no Android runtime here).

A START_STICKY restart after the system kills the process delivers a NULL
intent; onStartCommand matched nothing for it, so the car phone silently
stopped logging until someone opened the app. ACTION_BOOT was never sent.
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "android" / "app" / "src" / "main"
       / "kotlin" / "com" / "odbscanner" / "service")


def test_a_sticky_restart_resumes_collection():
    service = (SRC / "CollectorService.kt").read_text(encoding="utf-8")
    assert "null -> if (CollectorPrefs.collecting(this)) startCollecting()" in service
    assert "CollectorPrefs.setCollecting(this, true)" in service
    assert "CollectorPrefs.setCollecting(this, false)" in service


def test_dead_boot_action_is_gone():
    service = (SRC / "CollectorService.kt").read_text(encoding="utf-8")
    assert "ACTION_BOOT" not in service


def test_cloud_sync_attributes_each_row_to_its_own_vehicle():
    """One VIN (vinFor(1), falling back to the first vehicle) used to be
    stamped on every pending row, so a second car's readings were archived
    under the first car's VIN; an unknown VIN was sent as "" not null."""
    net = SRC.parent / "net" / "CloudSync.kt"
    sync = net.read_text(encoding="utf-8")
    assert 'vins[m.vehicleId] ?: JSONObject.NULL' in sync
    assert "repo.vinFor(" not in sync
    db = (SRC.parent / "db" / "Repository.kt").read_text(encoding="utf-8")
    assert "vehicles.getById(vehicleId)?.vin" in db
    assert "?: vehicles.getFirst())?.vin" not in db


def test_screens_do_not_load_the_whole_measurements_table():
    """latestMeasurements and everSucceededKeys loaded every row (getAll) and
    filtered in Kotlin -- every 3 s for the screen, on every connect for the
    collector -- on a table an always-on phone grows without bound."""
    db = (SRC.parent / "db" / "Repository.kt").read_text(encoding="utf-8")
    assert "measurements.latestPerKey(vehicleId)" in db
    assert "measurements.succeededEcuPids(vehicleId)" in db
    assert "measurements.getAll(" not in db
