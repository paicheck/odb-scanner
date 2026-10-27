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
