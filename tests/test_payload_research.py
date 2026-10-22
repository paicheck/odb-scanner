"""Regression vectors for the 2026-10-21 unknown-payload research.

Each case pins a finding that changed, or deliberately did not change, how a
stored payload is interpreted. The point is not that a number looks plausible;
it is that the arithmetic agrees with the evidence and that a sentinel cannot
become a plausible-looking reading.

No vehicle and no database are used here. Raw vectors are copied from
`research/measurement_discovery/payload_research_20261021.md`.
"""
from __future__ import annotations

import pytest

from decoders.aliases import alias_map, aliases_for, resolve
from decoders.registry import build_default_registry


@pytest.fixture(scope="module")
def reg():
    return build_default_registry("meb")


# --------------------------------------------------------------- 0x1E39
def test_charging_voltage_is_signed_and_the_sentinel_is_none(reg):
    """The profile declares 0x1E39 signed; unsigned FFFF was 2,047.97 V.

    A negative charging voltage is also physically odd on this car, so the
    honest result for the sentinel and for a negative field is "no value",
    not a plausible-looking voltage.
    """
    spec = reg.get("bat_charging_voltage")

    assert spec.decode_value(bytes.fromhex("FFFF")) is None
    assert spec.decode_value(bytes.fromhex("FFFE")) is None
    assert spec.decode_value(bytes.fromhex("8000")) is None

    # The live parked-car samples still decode exactly as the profile says.
    assert spec.decode_value(bytes.fromhex("0000")) == pytest.approx(0.0)
    assert spec.decode_value(bytes.fromhex("0001")) == pytest.approx(
        0.0312499784501336)
    assert spec.decode_value(bytes.fromhex("0006")) == pytest.approx(
        0.0312499784501336 * 6)


# --------------------------------------------------------------- 0x2A53
def test_reserved_dcdc_field_is_complementary_and_raw_only(reg):
    """0x2A53[3:5] is the complement of [1:3], not a second current.

    The old decoder subtracted the real-current offset from the complement
    and produced ~250 A. The real current lives in bytes 1-2.
    """
    reserved = reg.get("dc_dc_converter_reserved_current")
    real = reg.get("dcdc_gw_current")

    # Raw-only: the registry refuses to invent a current for this field.
    assert reserved.decode is None
    assert reserved.unit == ""
    assert reserved.decode_value(bytes.fromhex("95020F02F9")) is None

    cases = [
        ("95020F02F9", 16.0),
        ("96021202F9", 19.0),
        ("94021502F3", 22.0),
        ("94021F02EA", 32.0),
    ]
    for raw, expected_current in cases:
        payload = bytes.fromhex(raw)
        assert real.decode_value(payload) == pytest.approx(expected_current)

        first = int.from_bytes(payload[1:3], "big")
        second = int.from_bytes(payload[3:5], "big")
        assert first + second == pytest.approx(1288, abs=4), (
            f"{raw}: complement invariant is not ~0x0508")


# --------------------------------------------------------------- 0x5867
def test_odometer_2_is_a_ten_km_counter_not_the_odometer(reg):
    """The field tracks 10 km per LSB; it is not a copy of the odometer.

    x100 was floated because 1115 x 100 is close to the 111,020 km odometers,
    but the car moved ~10.5 km while the raw LSB changed by one. x10 matches
    that delta; x100 is arithmetically refuted.
    """
    spec = reg.get("odometer_2")

    assert "secondary distance counter" in spec.name.lower()
    before = spec.decode_value(bytes.fromhex("0005045A00000000"))
    after = spec.decode_value(bytes.fromhex("0005045B00000000"))

    assert before == pytest.approx(11140.0)
    assert after == pytest.approx(11150.0)
    assert after - before == pytest.approx(10.0)
    assert after - before != pytest.approx(100.0)


# --------------------------------------------------------------- 0x27C4
def test_windshield_temperature_current_u16_decode_is_refuted(reg):
    """The profile's u16 scale is wrong; byte 0 x 0.1 is now implemented.

    The old arithmetic is kept in the test so a future change cannot silently
    revive it, while the decoder must return the byte-0 candidate. The key is
    still quarantined until the cold-soak/defroster test.
    """
    spec = reg.get("windshield_temperature")

    cases = [
        ("719F619091", 454444.875, 11.3),
        ("729FD1988D", 458444.875, 11.4),
        ("7A2321C07D", 488507.375, 12.2),
        ("7A62C1BC81", 489491.75, 12.2),
    ]
    for raw, old_value, candidate in cases:
        payload = bytes.fromhex(raw)
        old_formula = int.from_bytes(payload[:2], "big") * 15.625 - 39.5
        assert old_formula == pytest.approx(old_value)
        assert spec.decode_value(payload) == pytest.approx(candidate)
        assert spec.decode_value(payload) != pytest.approx(old_value)


# --------------------------------------------------------------- 0x42DB
def test_co2_sentinel_is_not_a_ppm_reading(reg):
    """03FE is the not-fitted sentinel, not 25,400 or 1,022 ppm.

    03FE is also the BMS not-fitted sentinel, and all 40 live rows carry it.
    A non-sentinel frame decodes as a whole u16; the byte-1 x100 layout is
    rejected because a normal 500 ppm frame (01F4) would become 24,400 ppm.
    """
    spec = reg.get("co2_content_interior")
    sentinel = bytes.fromhex("03FE")

    assert spec.decode_value(sentinel) is None
    assert int.from_bytes(sentinel, "big") == 1022
    assert spec.decode_value(bytes.fromhex("01F4")) == pytest.approx(500.0)
    # The same two bytes are the unpopulated cell-temperature sentinel.
    assert reg.get("cell_t_18").decode_value(sentinel) is None


# --------------------------------------------------------------- 0x1800-03
def test_abs_wheel_speeds_are_registered_only_for_the_dedicated_tool(reg):
    """The default collector must not send 0x1800..0x1803 without 0x10 03."""
    from decoders.meb import register_abs
    from decoders.registry import DIDRegistry

    with pytest.raises(KeyError):
        reg.get("wheel_speed_front_left")

    abs_reg = DIDRegistry()
    register_abs(abs_reg)
    keys = [f"wheel_speed_{corner}" for corner in
            ("front_left", "front_right", "rear_left", "rear_right")]
    for key in keys:
        spec = abs_reg.get(key)
        assert spec.ecu_key == "brk"
        assert spec.unit == "km/h"
        # Parked capture block: first u16 is zero.
        assert spec.decode_value(
            bytes.fromhex("00000000FF00FFFFFFFFFFFF")
        ) == pytest.approx(0.0)
        # Synthetic 10.00 km/h.
        assert spec.decode_value(
            bytes.fromhex("03E80000FF00FFFFFFFFFFFF")
        ) == pytest.approx(10.0)


# --------------------------------------------------------------- 0xF449
def test_accelerator_pedal_decode_and_alias(reg):
    """The decode is already correct; only the stored key name is opaque."""
    spec = reg.get("res_pid_0149")

    assert spec.name == "Accelerator pedal position"
    assert spec.decode_value(bytes.fromhex("00")) == pytest.approx(0.0)
    assert spec.decode_value(bytes.fromhex("FF")) == pytest.approx(100.0)

    assert resolve("accelerator_pedal_pct") == "res_pid_0149"
    assert "accelerator_pedal_pct" in aliases_for("res_pid_0149")
    assert alias_map()["accelerator_pedal_pct"] == "res_pid_0149"


# --------------------------------------------------------------- quarantine
def test_the_quarantine_survives_the_payload_fixes():
    """Fixing one artifact must not make an unresolved key scoreable."""
    from decoders.quality import Verification, quality_for

    for key in (
        "windshield_temperature",
        "co2_content_interior",
        "bat_charging_voltage",
        "odometer_2",
        "dc_dc_converter_reserved_current",
    ):
        q = quality_for("meb", key)
        assert q.verification is Verification.INVALID, key
        assert q.use_in_scoring is False, key

    # The resolved pedal is usable, and its alias does not create a second key.
    q = quality_for("meb", "res_pid_0149")
    assert q.verification is not Verification.INVALID
    assert q.use_in_scoring is True
