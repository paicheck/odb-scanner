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
    # 0x2AB2/0x2AB8 carry NO captured bytes: data/real_car.db holds only the
    # decoded values from a Car Scanner export, whose raw_response column holds
    # an export LINE, not a payload. The vectors below are therefore SYNTHETIC
    # -- built from the layout the catalogue declares -- and are labelled as
    # such. They exercise the decode path; they do not validate it.
    #
    # 0x04280A64 is kept at 4 bytes because it is the payload that 53,200 Wh
    # maps to under BOTH competing layouts, and the extra bytes must not change
    # the answer. If the decoder ever reads all four, this vector breaks.
    ("hv_energy_max", b"\x04\x28\x0A\x64", 53200.0,
     "SYNTHETIC from u16be[0:2]x50; no captured bytes exist -- the only real "
     "evidence is the decoded value 53200 Wh from a Car Scanner CSV export",
     "derived"),
    ("aux_12v_voltage", b"\x29\x0A", 14.506,
     "CS log raw 10506 (10506 x 0.001 + 4.0)", "captured"),
    ("coolant_temps", b"\x05\x80\x05\x80",
     {"outlet_c": 22.0, "inlet_c": 22.0},
     "CS log 05800580 -> 22/22", "captured"),
    ("energy_counters",
     int(24422.819524984352 * 8583.07123641215).to_bytes(4, "big")
     + int(23447.33770012921 * 8583.07123641215).to_bytes(4, "big"),
     {"charged_kwh": 24422.8195, "used_kwh": 23447.3377},
     "CS log counters 24422.8195 / -23447.3377", "captured"),
    # SYNTHETIC: u16be[2:4] x 25 -> 41200 Wh from raw 1648 (0x0670). The two
    # leading bytes are there because SBI=2 means they must be skipped.
    ("hv_energy_content", b"\x00\x00\x06\x70",
     41200.0, "SYNTHETIC from u16be[2:4]x25; no captured bytes exist -- the only "
     "real evidence is the decoded values 41200/41325 Wh from a Car Scanner "
     "CSV export", "derived"),

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
        "counters establishes Car Scanner's sign, not the DID's. STILL UNPROVEN: "
        "no raw capture during charge and discharge has been made, so the "
        "DID's own convention is untested. If wrong, pack_current is inverted "
        "and charging detection, pack-power sign and regen history all invert "
        "with it."
    ),
    "hv_energy_content": (
        "The OLD u32/1310.77 layout is refuted, not merely doubted. The only "
        "real-world values for this DID are 41,325 and 41,200 Wh (Car Scanner "
        "export, data/real_car.db). 41325/1310.77 = 31.5273 and "
        "41200/1310.77 = 31.4319 are not integers, so no integral u32 can "
        "produce either; the layout cannot have generated the values it was "
        "cited as validating. Its 125 Wh LSB also cannot explain the 125 Wh "
        "step between the two readings (0.095 counts). Independently, two "
        "readings force the scale to divide gcd(41325,41200)=25, so the "
        "admissible scales are {1,5,25} and 1310.77 is not among them. The "
        "LIVE layout is u16be[2:4] x 25 from the Car Scanner profile "
        "(SBI=2 DL=2 MUL=25), which reproduces both values from integral raw "
        "counts 1653/1648 and reads the 125 Wh step as exactly 5 counts. "
        "RESOLVED 2026-10-15: the SBI=2 offset and the 0x710->0x77A address "
        "pair are confirmed by a passive HCI capture (62 2A B8 00 00 05 7F 00 "
        "00 00 00 -> 35,175 Wh). Usable-vs-gross remains unresolved. See "
        "docs/investigation/meb_gateway_energy_passive_capture_20261015.md."
    ),
}
