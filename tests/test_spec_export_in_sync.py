"""The exported spec must match the live registry, and Android must ship it.

``spec/odb_scanner.json`` is generated, but nothing used to check that it had
been regenerated. It silently fell three days behind on 2026-10-22: four
decoders were fixed in ``decoders/meb.py`` and the Android asset kept handing
the app the old recipes, so the phone would have re-reported exactly the wrong
numbers the desktop had just corrected. A generated file that can drift is a
generated file that *will* drift, so this pins it.

Two independent facts are checked:

  * the committed spec equals a fresh ``build_spec()`` (ignoring the
    ``generated_at`` timestamp, which is expected to differ);
  * the Android asset is byte-identical to the committed spec, so the app
    cannot ship a different map from the one the Python tests validate.

Regenerate both with::

    python tools/export_spec.py --sync-android
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.export_spec import build_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "spec" / "odb_scanner.json"
ANDROID_ASSET = (
    ROOT / "android" / "app" / "src" / "main" / "assets" / "spec.json"
)
REGENERATE = "python tools/export_spec.py --sync-android"


def _committed() -> dict:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    spec.pop("generated_at", None)
    return spec


def _live() -> dict:
    spec = build_spec()
    # A timestamp, by design different on every export.
    spec.pop("generated_at", None)
    return spec


def _measurements(spec: dict) -> dict[tuple[str, str], dict]:
    return {
        (profile, m["key"]): m
        for profile, data in spec["profiles"].items()
        for m in data["measurements"]
    }


def _drift(committed: dict, live: dict) -> str:
    lines = [f"spec/odb_scanner.json is stale -- run `{REGENERATE}`."]
    if committed.get("read_only_guard") != live.get("read_only_guard"):
        lines.append("  read_only_guard differs (the read-only allow-list!)")
    if committed.get("ecus") != live.get("ecus"):
        lines.append("  ecus differ")
    old, new = _measurements(committed), _measurements(live)
    for key in sorted(old.keys() - new.keys()):
        lines.append(f"  removed: {key[0]}/{key[1]}")
    for key in sorted(new.keys() - old.keys()):
        lines.append(f"  added: {key[0]}/{key[1]}")
    for key in sorted(old.keys() & new.keys()):
        if old[key] != new[key]:
            lines.append(f"  changed: {key[0]}/{key[1]}")
            if old[key].get("decode") != new[key].get("decode"):
                lines.append(f"    decode {old[key].get('decode')} -> "
                             f"{new[key].get('decode')}")
    return "\n".join(lines)


def test_committed_spec_matches_the_live_registry() -> None:
    committed, live = _committed(), _live()
    assert committed == live, _drift(committed, live)


def test_android_asset_is_the_committed_spec() -> None:
    assert ANDROID_ASSET.read_bytes() == SPEC_PATH.read_bytes(), (
        "android/app/src/main/assets/spec.json is not the committed spec -- "
        f"run `{REGENERATE}`"
    )


def test_pair_distinguishes_its_field_widths() -> None:
    """A client cannot implement `pair` from the recipe name alone.

    The two SoC DIDs carry a scaled value at bytes 0-1 and a raw cell index at
    bytes 2-3. If the exporter ever stops carrying ``width``/``scale``, the
    Kotlin decoder can only guess -- and a wrong guess here reports a plausible
    percentage for the wrong cell.
    """
    pairs = [m for m in _live()["profiles"]["meb"]["measurements"]
             if (m.get("decode") or {}).get("name") == "pair"]
    assert pairs, "the meb profile should still contain pair decoders"
    for measurement in pairs:
        decode = measurement["decode"]
        assert decode["kind"] == "special"
        assert decode["width"] == 2, measurement["key"]
        assert decode["scale"] == pytest.approx(0.01), measurement["key"]
        assert decode["divisor"] == pytest.approx(1.0), measurement["key"]
        assert decode["addend"] == pytest.approx(0.0), measurement["key"]
