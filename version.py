"""The odb_scanner product version.

``version.json`` at the repo root is the single source of truth, shared by the
Python CLI and the Android app. Both read it instead of each carrying its own
string, so the two cannot drift and a release bump is one edit.

This is the *application* version. Two other version strings exist in the repo
and mean different things, so they are deliberately not conflated with it:

  * ``spec.json``'s ``spec_version`` — the data-schema / read-only allow-list
    version the generated spec carries (tools/export_spec.py).
  * ``CATALOGUE_VERSION`` — the decoder catalogue version
    (decoders/catalogue.py), which the spec exports as ``catalogue_version``.

Nothing else in the tree should define an application version.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent / "version.json"

__version__ = json.loads(VERSION_FILE.read_text(encoding="utf-8"))["version"]

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def version_parts(version: str = __version__) -> tuple[int, int, int]:
    """Return (major, minor, patch) of a SemVer, or raise for anything else."""
    match = _SEMVER.match(version)
    if not match:
        raise ValueError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def android_version_code(version: str = __version__) -> int:
    """The monotonic integer the Android app derives from the same string.

    ``MAJOR * 10000 + MINOR * 100 + PATCH`` maps ``0.2.0`` to ``200``. It only
    has to be unique and increasing per release; the human-readable form is
    ``versionName``, never this number.
    """
    major, minor, patch = version_parts(version)
    return major * 10000 + minor * 100 + patch
