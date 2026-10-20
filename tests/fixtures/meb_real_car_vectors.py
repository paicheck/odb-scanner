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
    # Discharge is NEGATIVE on the DID. 000248FD = 149,757 -> -2.43 A while
    # the accumulated-discharge counter was rising. The old vector used the
    # +1.98 A sample and described it as discharge; that was the sign error.
    ("pack_current", b"\x00\x02\x48\xFD", -2.43,
     "CS log raw 149757; discharge counter rising", "captured"),
    ("cell_voltage_max", b"\x3F\xF9", 3.99829,
     "CS log raw 16377", "captured"),
    # The same 4-byte DIDs carry a 1-based cell number at bytes 2-3. The
    # leading value bytes are the captured ones above; the index is the
    # profile/offline cross-check value, not a second captured payload.
    ("cell_voltage_max_cell", b"\x3F\xF9\x00\x1C", 28,
     "CS profile #342 u16be[2:4]; offline cross-check 28/31 sweeps", "derived"),
    ("cell_voltage_min_cell", b"\x3F\xF9\x00\x1D", 29,
     "CS profile #342 u16be[2:4]; offline cross-check 29/31 sweeps", "derived"),
    ("bat_temp_max_sensor", b"\x05\x38\x00\x1F", 31,
     "CS profile #342 u16be[2:4]; offline cross-check 31/31 sweeps", "derived"),
    ("bat_temp_min_sensor", b"\x04\xE8\x00\x1E", 30,
     "CS profile #342 u16be[2:4]; offline cross-check 30/31 sweeps", "derived"),
    ("battery_serial", b"288 WARMF F265 ", "288 WARMF F265",
     "CS log ASCII payload", "captured"),
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
    # 16 bytes = four s32 fields: charge Ah, discharge Ah, charge kWh,
    # discharge kWh. The old 8-byte vector read the Ah counters with the
    # kWh divisor and produced ~3,724/3,700 kWh instead of these values.
    ("energy_counters",
     bytes.fromhex("01E7CC17FE1B74E70C8071E0F3FF868C"),
     {"charged_ah": 58196.4654, "discharged_ah": 57808.2630,
      "charged_kwh": 24436.9784, "discharged_kwh": 23459.8612,
      "used_kwh": 23459.8612},
     "CS log raw 01E7CC17 FE1B74E7 0C8071E0 F3FF868C", "captured"),
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

    # -- electric drive: FLOAT32(A,B,C,D), not u16/10+1 ----------------------
    # Raw payloads are from data/odb_scanner.db (2021 ID.3, 17 successful
    # reads). The profile states FLOAT32(A,B,C,D) for all of these; the old
    # u16 layout produced 1682 degC / 1738 V / 1744 A from the same bytes.
    ("electric_drive_inflow_coolant_temperature", bytes.fromhex("41816D8E"),
     16.1784935, "raw 0x4FAE 41816D8E; CS profile FLOAT32(A,B,C,D)",
     "captured"),
    ("dc_link_voltage", bytes.fromhex("43D897DE"), 433.1864624,
     "raw 0x3E93 43D897DE; CS profile FLOAT32(A,B,C,D)", "captured"),
    ("motor_maximum_current", bytes.fromhex("44164000"), 601.0,
     "raw 0x3EE7 44164000; CS profile FLOAT32(A,B,C,D)", "captured"),
    ("motor_minimum_current", bytes.fromhex("C3260000"), -166.0,
     "raw 0x3EE8 C3260000; CS profile FLOAT32(A,B,C,D)", "captured"),
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
#
# pack_current was removed on 2026-10-20: raw 0x1E3D 000248FD (-2.43 A) and
# independent 0x035E FFED (-1.9 A) both read negative while the accumulated
# discharge counter rose. Negative = discharge is now settled on bytes, not on
# Car Scanner's display sign. See research/measurement_discovery/decoder_audit.md.
CONTESTED = {
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
