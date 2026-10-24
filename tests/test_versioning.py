"""The app version must be one edit, shared by Python and Android.

``version.json`` at the repo root is the single source of truth; the Python CLI
(``main.py --version``, ``version.__version__``) and the Android app
(``build.gradle.kts``) both read it. If either ever hard-codes its own number
again, the two will drift exactly the way the spec drifted before
``test_spec_export_in_sync.py`` pinned it. These tests pin the version instead.

Three versions exist in the tree and are intentionally distinct; do not
conflate them: the app version (here), ``spec.json``'s ``spec_version``, and
``decoders/catalogue.py``'s ``CATALOGUE_VERSION``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import version as app_version
from version import android_version_code, version_parts

ROOT = Path(__file__).resolve().parents[1]
ANDROID_GRADLE = ROOT / "android" / "app" / "build.gradle.kts"


def _committed() -> str:
    return json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]


def test_version_file_is_semver() -> None:
    version_parts(_committed())  # raises if not MAJOR.MINOR.PATCH


def test_python_version_matches_the_json() -> None:
    assert app_version.__version__ == _committed()


def test_version_parts_rejects_malformed() -> None:
    for bad in ("1.2", "1.2.3.4", "a.b.c", "01.2.3", "1.2.", "", "1.2.3-rc1"):
        with pytest.raises(ValueError):
            version_parts(bad)


def test_android_version_code_is_monotonic_and_positive() -> None:
    code = android_version_code()
    assert code > 0
    # The mapping is MAJOR*10000 + MINOR*100 + PATCH; assert it round-trips
    # through the parts so a future scheme change is visible here.
    major, minor, patch = version_parts()
    assert code == major * 10000 + minor * 100 + patch


def test_android_reads_the_shared_version_file() -> None:
    """The Gradle file must consume version.json, not carry its own literal."""
    text = ANDROID_GRADLE.read_text(encoding="utf-8")
    assert "version.json" in text
    # versionName comes from the parsed string, versionCode is derived from it
    # rather than typed by hand.
    assert "versionName = appVersion" in text
    assert "versionCode = appVersionCode" in text
    # No stale hard-coded version survives.
    assert 'versionName = "0.1.0"' not in text
