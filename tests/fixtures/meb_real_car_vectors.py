"""Real-car DID vectors: the only ground truth we have for the MEB decoders.

Every entry is (key, raw bytes) as actually observed on the 2021 ID.3, with the
source that produced the bytes. The `expected` values are what the decoder MUST
produce from those bytes, so tools/validate_decoders.py can quantify agreement
instead of asserting it in prose.

Raw bytes come from two places, and the distinction matters:

  [CS log]  a Car Scanner ELM log that recorded both the raw UDS response and
            the displayed value. Byte-exact, so these pin a formula outright.
  [derived] no capture of this car; the raw value is reconstructed from the
            documented formula and the figure the note cites. Self-consistent by
            construction, so it pins nothing independently -- it is a regression
            guard against the formula drifting, NOT evidence.

Anything marked `derived` must not be presented as validation. The confidence
column in the validator says so explicitly, which is the point of building the
table: to make visible which decoders rest on a real capture and which only rest
on a source we are trusting.
"""
from __future__ import annotations

# key, raw bytes, expected decoded value(s), evidence, confidence
#
# confidence:
#   captured   raw bytes and displayed value both come from a real capture
#   derived    raw reconstructed from a documented formula, not captured here
#   contested  sources disagree; the value below is ours and may be wrong
VECTORS: list[tuple[str, bytes, object, str, str]] = [
    # -- BMS, [CS log] raw captures from the real vehicle ---------------------
    ("soc_abs", b"\xCB", 81.2, "CS log raw 203", "captured"),
    # 1725 decimal, not 0x06CD. See the note on HEX_CORRECTION below.
    ("pack_voltage", b"\x06\xBD", 431.25, "CS log raw 1725", "captured"),
    ("pack_current", b"\x00\x02\x4A\xB6", 1.98,
     "CS log raw 150198", "captured"),
    ("cell_voltage_max", b"\x3F\xF9", 3.99829,
     "CS log raw 16377", "captured"),
    ("battery_temp", b"\x78", 20.0, "CS log raw 120", "captured"),
    ("bat_temp_max", b"\x05\x38", 20.875, "CS log raw 1336", "captured"),
    ("bat_temp_min", b"\x04\xE8", 19.625, "CS log raw 1256", "captured"),
    ("cell_v_002", b"\x0B\xB5", 3.997, "CS log raw 2997", "captured"),
    ("dyn_charge_limit", b"\x04\x29", 213.0,
     "CS log raw 1065", "captured"),
    ("hv_energy_max", b"\x04\x28\x0A\x64", 53200.0,
     "CS log raw 69732964", "captured"),
    ("aux_12v_voltage", b"\x29\x0A", 14.52,
     "CS log raw 10506", "captured"),
    ("coolant_temps", b"\x05\x80\x05\x80",
     {"outlet_c": 22.0, "inlet_c": 22.0},
     "CS log 05800580 -> 22/22", "captured"),
    ("energy_counters",
     int(24422.819524984352 * 8583.07123641215).to_bytes(4, "big")
     + int(23447.33770012921 * 8583.07123641215).to_bytes(4, "big"),
     {"charged_kwh": 24422.8195, "used_kwh": 23447.3377},
     "CS log counters 24422.8195 / -23447.3377", "captured"),
    ("hv_energy_content", int(41200 * 1310.77).to_bytes(4, "big"),
     41200.0, "CS log 41200-41325 Wh parked", "contested"),

    # -- corroborated by spot2000's Calculation column, no raw capture ---------
    ("ptc_current", b"\x00", 0.0, "spot2000 XX/4", "derived"),
    ("outside_temp", b"\x80", 14.0, "spot2000 XX/2-50", "derived"),
    ("inside_temp", b"\x01\x22", 18.0,
     "spot2000 (XX*2^8+YY)/5-40", "derived"),
    ("dcdc_current", b"\x00\xE2", 14.125,
     "spot2000 (XX*2^8+YY)/16", "derived"),
    ("dcdc_voltage", b"\x1C\x33", 14.1,
     "spot2000 (XX*2^8+YY)/512", "derived"),
    # Promoted from `derived`: 0x0BBF7F is the odometer the Car Scanner log
    # actually recorded, so this pins the 3-byte big-endian layout outright
    # rather than only being consistent with spot2000's formula.
    ("odometer_km", b"\x0B\xBF\x7F", 769919.0,
     "CS log raw 0x0BBF7F = 769919 km", "captured"),
    ("cell_v_000", b"\x0B\xB5", 3.997,
     "spot2000 (XX*2^8+YY)/1000+1", "derived"),
    ("charge_mode", b"\x04", 1, "spot2000 4=AC -> app code 1", "derived"),
    ("op_mode", b"\x04", 4, "spot2000 4=AC charging", "derived"),
    ("hv_aux_power", b"\x01\xC2", 45.0,
     "spot2000 (XX*2^8+YY)/10", "derived"),
    ("dyn_discharge_limit", b"\x07\xD0", 400.0, "spot2000 /5", "derived"),
]

# A transcription error the validator caught in decoders/meb.py, recorded here
# so the fix is traceable rather than silent. The module docstring and the
# pack_voltage note both gave the capture as "0x06CD (1725) -> 431.25 V", but
# 0x06CD is 1741, and 1741/4 = 435.25 V, not 431.25. The decimal was right and
# the hex was wrong: the capture is 0x06BD (1725). Worth recording because it
# is the kind of error that survives review indefinitely when a prose note is
# the only form the evidence takes.
HEX_CORRECTION = {
    "pack_voltage": "meb.py gave 0x06CD for a 1725 capture; 0x06CD is 1741 "
                    "(435.25 V). Correct raw is 0x06BD.",
}

# Decoders where the sources genuinely disagree and we have not settled it.
# Kept separate so the validator flags them rather than quietly picking one.
CONTESTED = {
    "pack_current": (
        "evDash and this project: positive = discharge. ABRP (negates) and "
        "spot2000 ('Negative value is out from battery'): negative = "
        "discharge. 2 against 1 against us. Our reasoning from the car's own "
        "counters establishes Car Scanner's sign, not the DID's. If wrong, "
        "pack_current is inverted and charging detection, pack-power sign and "
        "regen history all invert with it."
    ),
    "hv_energy_content": (
        "Divisor 1310.77 is a hypothesis. evDash queues this DID commented out "
        "and never decodes it; spot2000 lists it '[equation missing]' while "
        "giving the addressing. No public source derives it, so the value below "
        "is fitted, not computed."
    ),
}
