"""Offline tests for the gateway battery-resistance DIDs (0x2A25 / 0x2A0E).

These two DIDs sit on the same gateway module (0x710 -> 0x77A) as the two
VERIFIED energy DIDs (0x2AB8 / 0x2AB2). They are PROVISIONAL: the layout comes
from the OBDb/Volkswagen-ID3 signalset (CC-BY-SA-4.0, "confirmed for MY2021"),
and the module addressing is already verified by this project's passive
capture, but no project-owned capture of either DID exists yet.

NOTHING here talks to a vehicle. Every payload is synthetic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decoders.catalogue import (  # noqa: E402
    ByteOrder,
    Confidence,
    EvidenceKind,
    build_catalogue,
    validate_catalogue,
)
from decoders.quality import quality_for  # noqa: E402
from decoders.registry import build_default_registry  # noqa: E402

INTERNAL_RES = "bat_internal_resistance_raw"
SSB_RES = "aux_battery_resistance_mohm"


def _specs():
    reg = build_default_registry("meb")
    return reg, {s.key: s for s in reg.all()}


# ------------------------------------------------------------------ registry
def test_both_resistance_dids_are_registered_on_the_gateway():
    _, specs = _specs()
    for key, did, unit in (
        (INTERNAL_RES, 0x2A25, ""),
        (SSB_RES, 0x2A0E, "mOhm"),
    ):
        spec = specs[key]
        assert spec.did == did
        assert spec.ecu_key == "energy"
        assert spec.unit == unit
        assert spec.slow, "a static resistance must not poll every fast cycle"


def test_resistance_dids_are_read_only_service_22():
    reg, specs = _specs()
    from diagnostic.ecus import ECUS

    ecu = ECUS["energy"]
    assert ecu.tx == 0x710 and ecu.rx == 0x77A
    for key in (INTERNAL_RES, SSB_RES):
        assert specs[key].did is not None
        from diagnostic.uds import validate_request

        validate_request(f"22{specs[key].did:04X}")
    assert reg.get(INTERNAL_RES).ecu_key == reg.get(SSB_RES).ecu_key == "energy"


# ------------------------------------------------------------- decoder maths
def test_internal_resistance_is_u8_over_5():
    _, specs = _specs()
    decode = specs[INTERNAL_RES].decode
    assert decode(bytes([0x00])) == 0.0
    assert decode(bytes([0x64])) == 20.0        # 100 / 5
    assert decode(bytes([0xFF])) == 51.0        # 255 / 5, the max index
    # a second byte is ignored (the field is a single byte at offset 0)
    assert decode(bytes([0x64, 0xFF])) == 20.0


def test_ssb_resistance_is_u8_times_2_over_10_milliohm():
    _, specs = _specs()
    decode = specs[SSB_RES].decode
    assert decode(bytes([0x00])) == 0.0
    assert decode(bytes([0x0A])) == 2.0         # 10 * 2 / 10
    assert decode(bytes([0x32])) == 10.0        # 50 * 2 / 10 = top of the range
    # 255 * 2 / 10 = 51 mOhm: above the 0-10.2 mOhm plausible bound, so the
    # quality layer flags it. The decoder still reports the arithmetic.
    assert decode(bytes([0xFF])) == 51.0


def test_the_two_resistance_dids_share_a_scale_but_not_a_meaning():
    """Both decode u8/5 arithmetically; only the units/meaning differ."""
    _, specs = _specs()
    raw = bytes([0x28])  # 40
    assert specs[INTERNAL_RES].decode(raw) == 8.0      # raw index
    assert specs[SSB_RES].decode(raw) == 8.0           # mOhm


def test_empty_payload_yields_no_value():
    _, specs = _specs()
    for key in (INTERNAL_RES, SSB_RES):
        assert specs[key].decode_value(b"") is None
        with pytest.raises(IndexError):
            specs[key].decode(b"")


# ----------------------------------------------------------------- catalogue
def test_resistance_dids_are_provisional_not_verified():
    cat = build_catalogue("meb")
    for key in (INTERNAL_RES, SSB_RES):
        d = cat[key]
        assert d.confidence is Confidence.PROVISIONAL, (
            "a third-party definition is not a project-owned capture")
        assert d.evidence_kind is EvidenceKind.THIRD_PARTY_DEFINITION
        assert d.address_confidence is Confidence.VERIFIED, (
            "the 0x710 -> 0x77A addressing is confirmed by the passive capture")
        assert d.layout is not None
        assert d.layout.byte_order is ByteOrder.BIG_ENDIAN
        assert d.layout.length == 1
        assert d.plausible_min is not None and d.plausible_max is not None


def test_catalogue_layout_reproduces_the_live_decoder():
    """The declared ByteLayout must agree with the shipped decoder."""
    assert validate_catalogue("meb") == []


def test_internal_resistance_range_is_the_raw_index_range():
    d = build_catalogue("meb")[INTERNAL_RES]
    assert d.plausible_min == 0.0
    assert d.plausible_max == 51.0


def test_ssb_resistance_range_is_physically_plausible():
    d = build_catalogue("meb")[SSB_RES]
    assert d.plausible_min == 0.0
    assert d.plausible_max == pytest.approx(10.2)


def test_only_the_two_captured_energy_dids_are_verified():
    verified = {k for k, d in build_catalogue("meb").items()
                if d.confidence is Confidence.VERIFIED}
    assert verified == {"hv_energy_content", "hv_energy_max"}


# ------------------------------------------------------------------- quality
def test_resistance_dids_are_provisional_and_scorable():
    for key in (INTERNAL_RES, SSB_RES):
        q = quality_for("meb", key)
        assert q.verification.value == "provisional"
        assert q.use_in_scoring is True
        assert q.plausible_min is not None and q.plausible_max is not None


# ------------------------------------------------------------- spec export
def test_resistance_dids_export_as_scalar_recipes():
    import json

    spec = json.loads((Path(__file__).resolve().parents[1]
                       / "spec" / "odb_scanner.json").read_text(encoding="utf-8"))
    by_key = {m["key"]: m for m in spec["profiles"]["meb"]["measurements"]}
    internal = by_key[INTERNAL_RES]["decode"]
    ssb = by_key[SSB_RES]["decode"]
    assert internal == {
        "kind": "scalar", "offset": 0, "length": 1,
        "byte_order": "big-endian", "signed": False,
        "scale": 1.0, "divisor": 5.0, "addend": 0.0,
        "formula": "u8be[0:1] unsigned x 1 / 5",
    }
    assert ssb == {
        "kind": "scalar", "offset": 0, "length": 1,
        "byte_order": "big-endian", "signed": False,
        "scale": 2.0, "divisor": 10.0, "addend": 0.0,
        "formula": "u8be[0:1] unsigned x 2 / 10",
    }


def test_resistance_dids_do_not_widen_the_uds_api():
    from diagnostic import uds

    public = [n for n in dir(uds) if n.startswith("build_")]
    assert public == ["build_read_did_request", "build_session_request",
                      "build_tester_present"]
