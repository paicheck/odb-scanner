"""VW MEB (ID.3 / ID.4 / Enyaq / Born) UDS DID map -- diagnostic-bus profile.

Why a separate map: on MEB the HV battery is not exposed through the OBD
functional addressing that works on the e-Up. The BMS answers only at its
29-bit module address 0x17FC007B (ATCP 17 + ATSH FC007B -- see
Elm327Transport.set_module), and the scale factors differ from the e-Up ones
(pack voltage u16/4, not u16/64). Functionally-read MEB DIDs answer
NRC 0x31 (requestOutOfRange), which is the wall the dashboard reports.

Provenance of the scale factors:

  [evDash]  nickn17/evDash CarVWID3.cpp (community RE of the MEB diagnostics,
            Apache-2.0 sources) -- the per-module command queues
  [ABRP]    iternio/ev-obd-pids MEB.json (independent implementation)
  [spot]    spot2000/Volkswagen-MEB-EV-CAN-parameters CSV: the only source
            that states ATCP/ATSH/ATCRA per DID, and the authority for the
            11-bit modules (energy 0x710 -> 0x77A, climate 0x746 -> 0x7B0)
  [CS]      cross-checked against a Car Scanner export of the actual vehicle
            (2026-10-05, ID.3 MY2021, 58 kWh). NOTE: for most DIDs this is a
            byte-exact cross-check against an ELM OBD2 log; for the two HV
            energy DIDs only DECODED VALUES survive (see ENERGY DECODER
            RESOLUTION below), so those two rest on arithmetic consistency,
            not on bytes.
              - SoC raw 0xCB (203) -> 81.2 % BMS / 83.63 % display
              - pack raw 0x06BD (1725) -> 431.25 V, matching 108 cells @ ~3.99 V
                (0x06BD, not 0x06CD: the first transcription of this capture
                gave 0x06CD, which is 1741 and decodes to 435.25 V. The decimal
                was right and the hex was wrong. A prose note is the only form
                this evidence takes, so nothing caught it until
                tools/validate_decoders.py re-derived the value from the bytes.)
              - cell raw 2997 -> 3.997 V (u16/1000 + 1)
              - max cell raw 16377 -> 3.9984 V (u16/4096)
              - current raw 150198 -> 1.98 A ((u32-150000)/100)
              - battery temp raw 120 -> 20.0 C (u8/2 - 40)
            Disagreements would have shown up as visibly wrong values; none did.

            CURRENT SIGN is settled on raw bytes, not on Car Scanner's display:
            parked, 0x1E3D sample 000248FD decodes to -2.43 A, and the
            independent 0x035E sample FFED decodes to -1.9 A, while the
            accumulated-discharge counter rose. Energy was leaving the pack, so
            NEGATIVE = discharging. Car Scanner and ABRP negate this for
            display; evDash does not. The decoder arithmetic was already
            correct -- only the old "positive = discharging" docstring and the
            Car Scanner comparison were wrong. Do NOT invert it. See
            research/measurement_discovery/decoder_audit.md (F3).

            Independently confirmed: spot2000's per-DID `Calculation` column
            agrees with this module on the equations it documents
            (028C, 1E3B, 1E3D, 2A0B, 1E0E/1E0F, 189D, 1E1B, F40D, 295A,
            1E40+, 465B, 465D, 0364, 2609, 2613). The deliberate exception is
            1E32: Car Scanner's MEB profile exposes four fields there (Ah at
            0/4, kWh at 8/12), while evDash/spot2000 describe only the first
            two as energy. The four-field layout reproduces the observed
            ~24,437/23,460 kWh values; the old two-field kWh interpretation
            produced ~3,724/3,700 kWh. So the EXPERIMENTAL flags below record
            where the knowledge came from -- reverse engineering, not a VW
            document -- not a doubt about the arithmetic.

Raw bytes are always preserved by the collector, so a wrong guess here costs
nothing but a re-decode later.
"""

from __future__ import annotations

import math
import re
import struct

from .registry import (
    DIDRegistry,
    DIDSpec,
    DocStatus,
    u16be,
    u32be,
)

ECU_BAT = "bat_mgmt"  # MEB module 0x7B
ECU_ENERGY = "energy"  # MEB module 0x710 (11-bit)
ECU_DCDC = "dcdc"  # MEB module 0xB9
ECU_VEH = "veh_info"  # MEB module 0x76
ECU_CLIMATE = "climate"  # MEB module 0x746 (11-bit)
ECU_CHG = "chg"  # MEB module 0x744 (11-bit): HV charger / OBC
ECU_ELDRIVE = "eldrive"  # MEB module 0x7C (29-bit): electric drive
ECU_GPS = "gps"  # MEB module 0x767 (11-bit): GPS / telematics
ECU_BRK = "brk"  # MEB module 0x713 (11-bit): ABS/ESC; extended session required

_EXP = DocStatus.EXPERIMENTAL

# Number of cell-voltage slots on the MEB pack. 0x1E40..0x1EAB inclusive.
CELL_V_BASE = 0x1E40
CELL_V_SLOTS = 108
# evDash marks unpopulated slots with 0x0FFE; a decoder returning None for
# those keeps them out of the min/max/curve without discarding the DID.
CELL_UNUSED = 0x0FFE

# Cell/module temperature points: 0x1EAE..0x1EBD plus 0x7425..0x742C.
# Car Scanner's MEB profile names these "[8C.BMS] HV Battery temp point 1..24"
# in this order, and declares the same u16/8 - 40 scaling for every one of them.
CELL_T_DIDS = list(range(0x1EAE, 0x1EBE)) + list(range(0x7425, 0x742D))
# Car Scanner guards the points from 0x7426 on with
#   if((A*256+B)>1020;[NaN];(A*256+B)/8-40)
# i.e. a raw count above 1020 means "sensor absent", not a reading. Without the
# guard an absent sensor decodes to ~500 degC and poisons the pack temperature
# range, so it is applied to every point.
CELL_T_MAX = 1020

# evDash's old 0x1E32 divisor (8583.07123641215 Wh/count) is NOT used. That
# layout read the Ah fields at 0/4 as if they were kWh and is superseded by the
# four-field Car Scanner layout below.
#
# Car Scanner MEB profile #342, DID 0x1E32 (all four fields signed s32):
#   Total accumulated charge    (Ah)  SBI=0  DL=4  MUL=0.001820444117116
#   Total accumulated discharge (Ah)  SBI=4  DL=4  MUL=0.001820444117116
#   Total accumulated charge    (kWh) SBI=8  DL=4  MUL=116508.398 / 1e9
#   Total accumulated discharge (kWh) SBI=12 DL=4  MUL=116508.398 / 1e9
ENERGY_AH_PER_COUNT = 0.001820444117116
ENERGY_KWH_PER_COUNT = 116508.398 / 1_000_000_000.0
# evDash CarMEB: "max energy content" u32 at 1310.77 Wh per count.
#
# REFUTED as a decoder for the values it was cited as validating. It is kept
# only so `_max_energy_wh` can still demonstrate why it fails, and so the
# refutation in docs/investigation/phase_c_energy_validation.md stays
# reproducible. See ENERGY DECODER RESOLUTION below.
MAX_ENERGY_COUNT_WH = 1310.77

# Live HV energy layout, from the Car Scanner MEB profile (idx 342) MUL/DL/SBI.
MAX_ENERGY_LSB_WH = 50.0  # 0x2AB2, offset 0
ENERGY_CONTENT_LSB_WH = 25.0  # 0x2AB8, offset 2
ENERGY_CONTENT_OFFSET = 2
# ---------------------------------------------------------------------------
# ENERGY DECODER RESOLUTION (0x2AB2 / 0x2AB8)
#
# This file used to decode both DIDs as u32be / 1310.77, while
# decoders/gateway.py decoded them as u16be x 50 / x 25 at offsets 0 / 2.
# Both shipped; the u32 version was the live one because config.yaml selects
# did_profile: meb. The only real-world values for either DID are three
# numbers from a Car Scanner CSV export of THIS vehicle (data/real_car.db):
#
#     hv_energy_content  41,325 Wh   2 rows  2026-10-05T19:02:08
#     hv_energy_content  41,200 Wh   2 rows  2026-10-05T19:17:25
#     hv_energy_max      53,200 Wh   5 rows  2026-10-05T19:02:07 .. 19:17:28
#
# No raw vehicle response exists for either DID. Every other row for these
# keys is success=0 with error 'No/undecodable response to DID 0x2ABx' -- 35
# attempts at each DID on 2026-10-06 and 2026-10-12, all addressed 0x710 ->
# 0x77A, all unanswered.
#
# REACHABILITY, done exactly. A layout reaches a value v only if the integer
# raw it would need actually exists. The two layouts invert differently, and
# using one inversion for both is how an earlier version of this note came to
# claim more than is true:
#
#   u16be x s :  value = raw * s     ->  raw = v / s
#   u32be / d :  value = raw / d     ->  raw = v * d
#
# Checked with Fraction (1310.77 is exactly 131077/100 in lowest terms):
#
#   v=41,325  x25  raw = 1653 (0x0675)      REACHABLE, fits u16
#   v=41,200  x25  raw = 1648 (0x0670)      REACHABLE, fits u16
#   v=53,200  x50  raw = 1064 (0x0428)      REACHABLE, fits u16
#
#   v=41,325  /1310.77  raw = 216670281/4   NOT AN INTEGER   <-- excluded
#   v=41,200  /1310.77  raw = 54003724      reachable
#   v=53,200  /1310.77  raw = 69732964      reachable
#
# So the u32/1310.77 layout is refuted, but precisely: it is the 41,325 Wh
# reading alone that excludes it. 41,200 Wh and 53,200 Wh are both multiples
# of 100, and 131077/100 can only ever produce multiples of 100, so neither of
# them could have distinguished this layout. Any stronger claim than that is
# not supported by the evidence.
#
# Corroborating constraint, and it is independent: for a multiplicative layout
# two readings of the same DID force the scale to divide their gcd --
# gcd(41325, 41200) = 25, so the admissible scale factors are {1, 5, 25} Wh.
# 25 is in that set; 1310.77 is not a member of it.
#
# The 4-byte payload 04280A64 that the tests and the simulator use decodes to
# 53,200 under BOTH layouts, so it cannot discriminate -- it is a synthetic
# fixture built as int(53200 * 1310.77) and must not be read as a capture.
#
# STATUS: CONFIRMED on captured bytes (2026-10-15 passive HCI capture).
#
# A Bluetooth HCI snoop capture of Car Scanner reading both DIDs on an ID.3
# settled the remaining unknowns without a live request of our own:
#
#   - Addressing is 11-bit 0x710 -> 0x77A: ATSH710 / ATFCSH710 / ATCRA77A on
#     ATSP6, with flow control ATFCSD300000 + ATFCSM1. This is NOT the
#     0x792 -> 0x710 pair that decoders/gateway.py's docstring still
#     contemplates; the +0x6A "encoding" really is the response id 0x77A.
#   - 0x2AB8 is MULTI-FRAME: 62 2A B8 00 00 05 7F 00 00 00 00 (11 bytes, one
#     First Frame + one Consecutive Frame). u16be[2:4] = 0x057F = 1407, x25 =
#     35,175 Wh -- matching Car Scanner's displayed 35,175 Wh exactly.
#   - 0x2AB2 is SINGLE-FRAME: 62 2A B2 04 28 04 00 (7 bytes). u16be[0:2] =
#     0x0428 = 1064, x50 = 53,200 Wh -- matching the displayed 53,200 Wh.
#
# So the SBI=2 offset, the SBI=0 offset, the multipliers AND the addressing
# pair are all confirmed on real bytes. The gross-vs-usable MEANING (0x2AB2 =
# rated maximum, 0x2AB8 = usable right now) still rests on the profile names
# rather than an independent VW document, but 53,200 > 35,175 is consistent.
# See docs/investigation/meb_gateway_energy_passive_capture_20261015.md.
# ---------------------------------------------------------------------------

# 0x7448 byte 0 is a bit field; see _charge_mode for how it maps onto the
# app-wide convention (decoders.charging.CHARGE_MODE_CODES).

_GEAR_CODES = {6: "N", 5: "D", 12: "B", 8: "P", 7: "R"}


# -- BMS decoders ----------------------------------------------------------
def _soc_bms(raw: bytes) -> float:
    return raw[0] / 2.5


def _soc_display(raw: bytes) -> float:
    """Displayed SoC: linear fit of raw*2.5 onto the dash scale.

    evDash fits the dash SoC against the BMS SoC; the published offset
    reproduces the car's own display exactly (raw 203 -> 83.63 %).
    """
    return raw[0] * 0.4425 - 6.1947


def _pack_voltage(raw: bytes) -> float:
    return u16be(raw) / 4.0


def _pack_current(raw: bytes) -> float:
    """Pack current in A; NEGATIVE = discharging (power leaves the pack).

    This is the DID's own sign, not Car Scanner's display sign. Parked, the
    raw 0x1E3D sample 000248FD decodes to -2.43 A while the accumulated
    discharge counter rose; the independent 0x035E sample FFED decodes to
    -1.9 A at the same time. Car Scanner and ABRP negate this for display, so
    a positive Car Scanner reading is not evidence that the DID is positive.

    Do NOT invert the arithmetic: previous releases described this as
    "positive = discharging" while returning negative on discharge. The
    arithmetic was already consistent with the raw data; only that wording and
    the Car Scanner comparison were wrong. See the module docstring and
    research/measurement_discovery/decoder_audit.md.
    """
    return (u32be(raw) - 150000) / 100.0


def _cell_voltage(raw: bytes):
    v = u16be(raw)
    if v == CELL_UNUSED:
        return None
    return v / 1000.0 + 1.0


def _cell_voltage_scaled(raw: bytes) -> float:
    """Cell extreme DIDs 0x1E33/0x1E34: u16/4096 (no +1 offset)."""
    return u16be(raw) / 4096.0


def _battery_temp(raw: bytes) -> float:
    return raw[0] / 2.0 - 40.0


def _s16be(raw: bytes, off: int = 0) -> int:
    """Big-endian SIGNED 16-bit. u16be does the indexing, so a short payload
    fails the same way (IndexError -> logged None), never as a wrong number."""
    v = u16be(raw, off)
    return v - 0x10000 if v & 0x8000 else v


def _temp_scaled(raw: bytes) -> float:
    # Signed: the Car Scanner profile declares 0x1E0E/0x1E0F temperature
    # fields signed=True (evDash reads them unsigned). Unsigned, the first
    # sub-zero reading of the winter decoded as ~1000 C -- raw FEC0 is
    # -320/64 = -5.0 C, not 65216/64 = 1019 C -- and fed straight into the
    # battery-temperature spread and the anomaly baselines.
    return _s16be(raw) / 64.0


def _cell_temp(raw: bytes):
    """Battery temperature sensor in degC. None when the sensor is absent.

    The >CELL_T_MAX guard is Car Scanner's own, from the formula it declares
    for points 0x7426..0x742C: `if((A*256+B)>1020;[NaN];(A*256+B)/8-40)`.
    """
    v = u16be(raw)
    if v > CELL_T_MAX:
        return None
    return v / 8.0 - 40.0


def _energy_counters(raw: bytes) -> dict:
    """Four lifetime counters from 0x1E32, in the profile's own layout.

    The old decoder read offsets 0/4 as *kWh* with evDash's 8583.071 Wh per
    count divisor. Those offsets are actually Ah counters; the kWh counters
    live at offsets 8/12, and the profile supplies a different scale. The old
    output was therefore not merely scaled differently -- it described the
    wrong physical quantity and reported ~3,724/3,700 kWh instead of the
    ~24,437/23,460 kWh in the same bytes.

    All four fields are signed in the profile. Charge is positive; discharge
    is the magnitude of the signed field, matching the historical
    ``used_kwh`` key. ``charged_kwh``/``used_kwh`` are retained because the
    collector and database columns already use them.
    """
    # A truncated answer must be a failed read, not a plausible number. This is
    # the same failure mode that u32be was hardened against above.
    if len(raw) < 16:
        raise IndexError(f"energy counters need 16 bytes, got {len(raw)}")

    def s32(off: int) -> int:
        return int.from_bytes(raw[off : off + 4], "big", signed=True)

    charged_ah = s32(0) * ENERGY_AH_PER_COUNT
    discharged_ah = abs(s32(4)) * ENERGY_AH_PER_COUNT
    charged_kwh = s32(8) * ENERGY_KWH_PER_COUNT
    discharged_kwh = abs(s32(12)) * ENERGY_KWH_PER_COUNT
    return {
        "charged_ah": charged_ah,
        "discharged_ah": discharged_ah,
        "charged_kwh": charged_kwh,
        "discharged_kwh": discharged_kwh,
        # Backwards-compatible aliases for the existing collector/snapshot.
        "used_kwh": discharged_kwh,
    }


def _u16_over(raw: bytes, div: float) -> float:
    return u16be(raw) / div


def _charge_mode(raw: bytes) -> int | None:
    """MEB 0x7448 byte 0 -> app-wide charge-mode convention (0/1/2), or None.

    Byte 0 is a bit field, which is how the Car Scanner profile reads it
    (standby = NOT bit 0; DC charging = bit 1 AND bit 2) and which evDash's
    enum values fit exactly: 0 standby, 1 driving (bit 0), 4 AC (bit 2),
    6 DC (bits 1+2). This used to be an exact match on 4/6 with everything
    else mapped to 0, so a charging byte with any further bit set -- 0x05,
    0x0E -- read as "idle" and _charging_tracking closed a live session on it.
    A value with bits the sources do not describe is unknown (None), which the
    collector treats as "no evidence" rather than as not charging.
    """
    b = raw[0]
    if b & ~0x07:
        return None              # bits beyond 0-2: undocumented
    if b & 0x04:
        return 2 if b & 0x02 else 1
    return 0 if b in (0x00, 0x01) else None   # bit 1 alone: undocumented


def _op_mode(raw: bytes) -> int:
    return raw[0]


def _byte(raw: bytes) -> int:
    return raw[0]


def _byte_over(raw: bytes, div: float) -> float:
    return raw[0] / div


def _coolant_temps(raw: bytes) -> dict:
    """Cooling-circuit inlet/outlet in degC from one 4-byte DID.

    Byte order was ambiguous between sources until they were compared: the
    spot2000 row labelled "outlet" reads data bytes 0-1 and evDash's live
    inlet line reads data bytes 2-3, so outlet = [0:2], inlet = [2:4].
    """
    # Both fields signed per the Car Scanner profile (0x189D, signed=True):
    # unsigned, a -1 C coolant read as 1023 C.
    return {"outlet_c": _s16be(raw, 0) / 64.0, "inlet_c": _s16be(raw, 2) / 64.0}


def _amb_12v(raw: bytes) -> float:
    """12 V battery voltage, first field of the multi-frame DID 0x2AF7.

    Car Scanner MEB profile: u16 x 0.001 + 4.0 (SBI=0 DL=2 MUL=0.001 OFS=4.0).
    evDash used u16/1024 + 4.26; the profile's /1000 + 4.0 is what Car Scanner
    displays, so it is the scaling we match. (They differ by < 0.02 V.)
    """
    return u16be(raw) * 0.001 + 4.0


def _aux_12v_current(raw: bytes) -> float:
    """12 V battery current, DID 0x2AF7 offset 2, length 4, signed, x0.001 A."""
    if len(raw) < 6:
        raise IndexError(
            f"12V current needs 4 bytes at offset 2, got {max(0, len(raw) - 2)}"
        )
    return int.from_bytes(raw[2:6], "big", signed=True) * 0.001


def _outdoor_temp(raw: bytes) -> float:
    return raw[0] / 2.0 - 50.0


def _cabin_temp(raw: bytes) -> float:
    return u16be(raw) / 5.0 - 40.0


# -- Car Scanner profile-derived fields -------------------------------------
#
# The MEB profile in profiles7.db (index 342) states every measurement's layout
# explicitly: SBI (start byte), DL (length), MUL, DIV, OFS and byte order, with
# RBS=false -- big-endian -- on all 308 records. These helpers apply exactly that
# rather than inferring a divisor from a displayed number.
#
# SBI matters: several DIDs pack two values, the second starting at byte 2. 0x1E2C
# and 0x1E2D are "Battery max/min SoC cell %" at byte 0 AND "Battery max/min
# SoC cell #" at byte 2, and 0x1E33/0x1E34 are the max/min cell INDEX rather
# than a voltage. Reading either as a single value at byte 0 gives a plausible
# number that means something else.
CS_SRC = "CS profiles7.db MEB#342"


def _cs_field(raw: bytes, sbi: int, dl: int) -> int:
    """One big-endian unsigned field at the declared offset and width.

    Refuses a payload short of the field, for the same reason u32be does: a
    truncated answer must surface as a failed read, not as a plausible number.
    int.from_bytes would happily accept 1-3 bytes and invent a value.
    """
    chunk = raw[sbi : sbi + dl]
    if len(chunk) != dl:
        raise IndexError(
            f"CS field needs {dl} bytes at offset {sbi}, got {max(0, len(raw) - sbi)}"
        )
    return int.from_bytes(chunk, "big")


def _cs_scaled(
    raw: bytes, *, sbi: int, dl: int, mul: float, div: float, ofs: float
) -> float:
    return _cs_field(raw, sbi, dl) * mul / div + ofs


def _cs_field_decoder(
    *, sbi: int, dl: int, mul: float, div: float, ofs: float, sig: bool = False
):
    def decode(raw: bytes) -> float:
        chunk = raw[sbi : sbi + dl]
        if len(chunk) != dl:
            raise IndexError(
                f"CS field needs {dl} bytes at offset {sbi}, got {max(0, len(raw) - sbi)}"
            )
        val = int.from_bytes(chunk, "big", signed=sig)
        return val * mul / div + ofs

    return decode


_BAT_CHARGING_VOLTAGE_SCALE = 0.0312499784501336


def _bat_charging_voltage(raw: bytes) -> float | None:
    """0x1E39 is a SIGNED 16-bit charging voltage, with a sentinel.

    The Car Scanner profile declares this field signed, but the generic
    `_cs_field_decoder` defaulted to unsigned.  That turned the stored
    ``FFFF`` payload into 2,047.97 V -- a plausible-looking number that is
    really either a sentinel or -0.03125 V.  The car was parked in every
    capture, so the honest decode is "no value" for the sentinel and for any
    negative result; a charging session is still needed to confirm the top of
    the range.  The raw bytes are preserved by the collector either way.
    """
    chunk = raw[0:2]
    if len(chunk) != 2:
        raise IndexError(
            f"battery charging voltage needs 2 bytes at offset 0, "
            f"got {len(chunk)}"
        )
    if chunk in (b"\xff\xff", b"\xff\xfe"):
        return None
    value = int.from_bytes(chunk, "big", signed=True)
    if value < 0:
        return None
    return value * _BAT_CHARGING_VOLTAGE_SCALE


def _windshield_temperature(raw: bytes) -> float | None:
    """0x27C4 byte 0 is a 0.1 degC climate sensor; the profile u16 is wrong.

    The Car Scanner profile declares SBI=0 DL=2 x15.625 -39.5. Applying that
    to the observed ``7A2321C07D`` gives 488,507 degC, while byte 0 x0.1 gives
    12.2 degC -- within a few degrees of the same-session outside temperature
    across 41 rows. The remaining bytes are not decoded here. Whether this
    sensor is the windshield itself or an ambient sensor near it still needs
    the cold-soak/defroster test, so the key remains quarantined.
    """
    if not raw:
        raise IndexError("windshield temperature needs byte 0")
    return raw[0] * 0.1


def _co2_content_interior(raw: bytes) -> float | None:
    """0x42DB is a 16-bit ppm field; 0x03FE is the not-fitted sentinel.

    The profile's SBI=1 DL=1 x100 layout turns the observed ``03FE`` into
    25,400 ppm and a normal 500 ppm frame (``01F4``) into 24,400 ppm. The
    whole-u16 reading is 1,022 ppm for the sentinel and 500 ppm for ``01F4``.
    0x03FE is also the repeated BMS cell-temperature not-fitted sentinel, and
    all 40 live rows carry it, so the sensor is treated as not fitted rather
    than as a 1,022 ppm reading. A non-sentinel scale is still provisional.
    """
    if len(raw) < 2:
        raise IndexError("CO2 content needs 2 bytes, got 1")
    value = int.from_bytes(raw[:2], "big")
    if value == 0x03FE:
        return None
    return float(value)


def _f32be(raw: bytes, off: int = 0) -> float | None:
    """IEEE-754 big-endian float32 at ``off`` (electric-drive family).

    The Car Scanner profile states FLOAT32(A,B,C,D) for 25 of the 26
    electric-drive rows. The old registry treated them as u16/10+1, which
    turned 433.19 V into 1,737.8 V and 16.18 degC into ~1,682 degC. A short
    payload raises, and a non-finite value returns None, so neither can become
    a plausible-looking reading.
    """
    chunk = raw[off : off + 4]
    if len(chunk) != 4:
        raise IndexError(
            f"float32 needs 4 bytes at offset {off}, got {max(0, len(raw) - off)}"
        )
    value = struct.unpack(">f", chunk)[0]
    return value if math.isfinite(value) else None


def _index_u16(raw: bytes) -> int:
    """1-based cell/sensor number carried at bytes 2-3 of an extreme DID."""
    if len(raw) < 4:
        raise IndexError(f"index needs 4 bytes, got {len(raw)}")
    return u16be(raw, 2)


def _ascii(raw: bytes) -> str:
    """Trimmed ASCII payload used by the battery serial DID."""
    return raw.decode("ascii", errors="replace").strip("\x00 ")


def _bit2(raw: bytes) -> int:
    return (raw[0] >> 2) & 1


def _bit3(raw: bytes) -> int:
    return (raw[0] >> 3) & 1


def _cs_pair_decoder(*, dl: int, first_mul: float, first_div: float, first_ofs: float):
    """A DID carrying two values: a scaled one at byte 0 and a raw index at 2.

    Reads bytes 0..2*dl in one step. Reading the halves independently would let
    the first succeed while the second is short, which turns a truncated
    response into a partial result recorded as complete.
    """

    def decode(raw: bytes) -> dict:
        need = 2 * dl
        if len(raw) < need:
            raise IndexError(f"pair field needs {need} bytes, got {len(raw)}")
        return {
            "value": _cs_scaled(
                raw, sbi=0, dl=dl, mul=first_mul, div=first_div, ofs=first_ofs
            ),
            "index": _cs_field(raw, dl, dl),
        }

    return decode


def _cs_note(sbi: int, dl: int, mul: float, div: float, ofs: float) -> str:
    mul_s = f"{mul:g}"
    div_s = f"{div:g}"
    mul_s = mul_s if mul_s not in ("1", "1.0") else "1"
    div_s = div_s if div_s not in ("1", "1.0") else "1"
    return (
        f"u16be[{sbi}:{sbi + dl}] * {mul_s}"
        + (f" / {div_s}" if div_s != "1" else "")
        + (f" + {ofs:g}" if ofs else "")
        + f" [{CS_SRC}]"
    )


# -- module decoders -------------------------------------------------------
def _max_energy_wh(raw: bytes) -> float:
    """DEPRECATED -- superseded, kept so the refutation stays checkable.

    This was `u32be / 1310.77`. It CAN produce 53,200 Wh (from raw 69732964),
    so this decoder is not refuted for hv_energy_max on its own; it is
    refuted for hv_energy_content, which needs the same layout to yield
    41,325 Wh and cannot. Do not use it. `_energy_max_wh` is the live decoder.
    """
    return u32be(raw) / MAX_ENERGY_COUNT_WH


def _energy_content_wh_refuted(raw: bytes) -> float:
    """DEPRECATED -- this is the one the arithmetic actually excludes.

    41,325 Wh would require raw = 41325 * 1310.77 = 216670281/4, which is not
    an integer, so no u32 payload can decode to it under this layout. See the
    ENERGY DECODER RESOLUTION comment above and
    docs/investigation/phase_c_energy_validation.md.
    """
    return u32be(raw) / MAX_ENERGY_COUNT_WH


def _energy_max_wh(raw: bytes) -> float:
    """Rated (maximum) HV energy content in Wh -- the SoH reference value.

    u16be at offset 0, x 50 Wh, unsigned (Car Scanner MEB profile idx 342:
    SBI=0 DL=2 MUL=50.0 DIV=1.0 OFS=0.0 SIG=false RBS=false UN=61).
    """
    if len(raw) < 2:
        raise IndexError(f"hv_energy_max needs 2 bytes, got {len(raw)}")
    return u16be(raw) * MAX_ENERGY_LSB_WH


def _energy_content_wh(raw: bytes) -> float:
    """HV energy content right now, in Wh.

    u16be at offset 2, x 25 Wh, unsigned (Car Scanner MEB profile idx 342:
    SBI=2 DL=2 MUL=25.0 DIV=1.0 OFS=0.0 SIG=false RBS=false UN=61).

    The SBI=2 offset has never been seen on a captured frame. A record too
    short to reach offset 2+2 raises rather than returning a number read from
    the wrong bytes, so a short reply stores NULL with the raw bytes kept.
    """
    if len(raw) < ENERGY_CONTENT_OFFSET + 2:
        raise IndexError(
            f"hv_energy_content needs 4 bytes at offset {ENERGY_CONTENT_OFFSET}, "
            f"got {len(raw)}"
        )
    return u16be(raw, ENERGY_CONTENT_OFFSET) * ENERGY_CONTENT_LSB_WH


def _odometer_km(raw: bytes) -> float:
    # Slicing silently accepts 1-2 bytes and returns a smaller, entirely
    # plausible mileage (0BBF -> 3007 km), which is indistinguishable from a
    # real reading once stored.
    if len(raw) < 3:
        raise IndexError(f"odometer needs 3 bytes, got {len(raw)}")
    return float(int.from_bytes(raw[:3], "big"))


def _gear(raw: bytes) -> str:
    return _GEAR_CODES.get(raw[1] if len(raw) > 1 else -1, f"raw {raw.hex().upper()}")


def _vin(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip("\x00 ")


# 0x2430 on the GPS module (0x767): the receiver's fix as two ASCII
# degrees-minutes-seconds strings plus an altitude word. Layout from evDash's
# CarVWID3 parser and its sample record (another MEB car):
#   31 38 B0 33 36 27 36 2E 30 22 45 | 00 00 00 | 34 38 B0 31 32 27 33 34 2E 33
#   22 4E | 00 00 02 A1 AA
#   = 18°36'6.0"E, 48°12'34.3"N, altitude 0x02A1 - 501 = 172 m.
# Fields: record[0:14] and record[14:28], each NUL-terminated; altitude u16 at
# record[28:30] minus 501. evDash's comments call the first field latitude and
# assign it to longitude; the code (and the sample) are right that it is the
# E/W one, so each field is placed by its OWN hemisphere letter, not its slot.
# Not yet read on this car: the catalogue keeps it UNKNOWN until it is.
_DMS = re.compile(r"(\d{1,3})\D+(\d{1,2})'(\d{1,2}(?:\.\d+)?)\"?\s*([NSEW])")
GPS_ALTITUDE_OFFSET = 501


def _dms_to_degrees(text: str) -> tuple[float, str] | None:
    m = _DMS.search(text)
    if not m:
        return None
    deg, minutes, seconds, hemi = int(m[1]), int(m[2]), float(m[3]), m[4]
    if minutes >= 60 or seconds >= 60:
        return None
    value = deg + minutes / 60.0 + seconds / 3600.0
    return (-value if hemi in "SW" else value), hemi


def _gps_position(raw: bytes) -> dict | None:
    """Latitude/longitude (decimal degrees, WGS84 as reported) and altitude.

    None when there is no usable fix -- an empty or zero field, a value out of
    range, or both fields on the same axis -- never a 0,0 point in the Gulf of
    Guinea.
    """
    if len(raw) < 30:
        return None
    lat = lon = None
    for field in (raw[0:14], raw[14:28]):
        text = field.split(b"\x00", 1)[0].decode("latin-1")
        parsed = _dms_to_degrees(text)
        if parsed is None:
            continue
        value, hemi = parsed
        if hemi in "NS":
            lat = value
        else:
            lon = value
    if lat is None or lon is None or not (-90 <= lat <= 90) \
            or not (-180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    altitude = int.from_bytes(raw[28:30], "big") - GPS_ALTITUDE_OFFSET
    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        # evDash's own sanity bound; below it the word is not an altitude.
        "altitude_m": altitude if altitude > -500 else None,
    }


_ABS_FIELDS: tuple[tuple[str, int, str], ...] = (
    ("wheel_speed_front_left", 0x1800, "Front left wheel speed"),
    ("wheel_speed_front_right", 0x1801, "Front right wheel speed"),
    ("wheel_speed_rear_left", 0x1802, "Rear left wheel speed"),
    ("wheel_speed_rear_right", 0x1803, "Rear right wheel speed"),
)


def register_abs(reg: DIDRegistry) -> None:
    """Register the four Car Scanner [03.ABS] wheel-speed DIDs.

    Deliberately NOT called by ``register_meb``: the default collector would
    send 0x22 0x1800..0x1803 without the extended diagnostic session and only
    collect negative responses. The dedicated ABS tool calls this after an
    explicitly approved ``0x10 03`` on the 0x713 -> 0x77D pair.
    """
    for key, did, name in _ABS_FIELDS:
        reg.register(
            DIDSpec(
                key,
                ECU_BRK,
                did,
                name,
                "km/h",
                decode=_cs_field_decoder(
                    sbi=0, dl=2, mul=0.01, div=1, ofs=0
                ),
                doc_status=_EXP,
                slow=False,
                notes=(
                    "u16be[0:2] x 0.01 km/h [CS profile #342, [03.ABS]]; "
                    "the response is a 12-byte block but the profile field is "
                    "the first two bytes. Requires extended session 0x10 03 "
                    "on 0x713 -> 0x77D; raw bytes are preserved."
                ),
            )
        )


def register_meb(reg: DIDRegistry) -> None:
    """Register the full MEB DID map (BMS + gateway/energy/DC-DC modules)."""
    _register_bms(reg)
    _register_cells(reg)
    _register_modules(reg)
    _register_profile_fields(reg)
    _register_profile_extras(reg)


# Fields taken verbatim from the Car Scanner MEB profile. Only modules this
# transport demonstrably reaches are listed: the BMS (29-bit 0x17FC007B, which
# answered 3,506/3,506 reads in the logged history) and the charger
# (11-bit 0x744, 211/211). The gateway and HVAC modules are excluded on
# purpose -- they have never answered, and adding DIDs there would inflate the
# registry with rows nothing can collect. See docs/investigation/
# knowledge_gaps.md.
#
# (key, did, name, unit, ecu, sbi, dl, mul, div, ofs)
_CS_FIELDS: tuple[tuple, ...] = (
    ("odometer_bms", 0x02BD, "Odometer (BMS copy)", "km", ECU_BAT, 1, 3, 1.0, 1.0, 0.0),
    ("ptc_voltage", 0x1621, "PTC heater voltage", "V", ECU_BAT, 0, 1, 0.1, 1.0, 0.0),
    (
        "bms_pcb_temp",
        0x1622,
        "BMS PCB temperature",
        "degC",
        ECU_BAT,
        0,
        1,
        1.0,
        1.0,
        -50.0,
    ),
    (
        "ptc_medium_temp_1",
        0x1623,
        "PTC medium temperature 1",
        "degC",
        ECU_BAT,
        0,
        1,
        1.0,
        1.0,
        -50.0,
    ),
    (
        "ptc_medium_temp_2",
        0x1624,
        "PTC medium temperature 2",
        "degC",
        ECU_BAT,
        0,
        1,
        1.0,
        1.0,
        -50.0,
    ),
    (
        "bat_v_after_contacts",
        0x189B,
        "Battery voltage after contacts",
        "V",
        ECU_BAT,
        0,
        2,
        0.5,
        1.0,
        -1000.0,
    ),
    (
        "bat_v_before_contacts",
        0x1E36,
        "Battery voltage before contacts",
        "V",
        ECU_BAT,
        0,
        2,
        0.0312499784501336,
        1.0,
        0.0,
    ),
    (
        "bat_v_after_contacts_2",
        0x1E38,
        "Battery voltage (2nd, after contacts)",
        "V",
        ECU_BAT,
        0,
        2,
        0.0312499784501336,
        1.0,
        0.0,
    ),
    (
        "bat_charging_voltage",
        0x1E39,
        "Battery charging voltage",
        "V",
        ECU_BAT,
        0,
        2,
        0.0312499784501336,
        1.0,
        0.0,
    ),
    # NOT named cell_v_*: that prefix is the per-cell rollup group in
    # analysis/provenance.py, and this is a single scalar, not one of the 108
    # slots. A "cell_v_sum" key would be folded into the rollup and render as
    # "1/109 live".
    (
        "cell_voltage_sum",
        0x1E3C,
        "Sum of all cell voltages",
        "V",
        ECU_BAT,
        0,
        4,
        244140.625,
        1000000000.0,
        0.0,
    ),
    (
        "pack_current_2",
        0x1E3E,
        "HV pack current (2nd)",
        "A",
        ECU_BAT,
        0,
        4,
        0.01,
        1.0,
        -1500.0,
        # Same sign convention as 0x1E3D: negative = discharging.
    ),
    (
        "min_discharge_voltage",
        0x5170,
        "Minimum discharge voltage",
        "V",
        ECU_BAT,
        0,
        2,
        1.0,
        1.0,
        0.0,
    ),
    (
        "max_charge_voltage",
        0x5171,
        "Maximum charge voltage",
        "V",
        ECU_BAT,
        0,
        2,
        1.0,
        1.0,
        0.0,
    ),
    (
        "odometer_2",
        0x5867,
        "Secondary distance counter (10 km/LSB; not the odometer)",
        "km",
        ECU_BAT,
        2,
        2,
        10.0,
        1.0,
        0.0,
    ),
    ("motor_rpm", 0xF40C, "Motor speed", "rpm", ECU_BAT, 0, 2, 1.0, 4.0, 0.0),
    ("obc_dc_current", 0x103D, "HV DC current (OBC)", "A", ECU_CHG, 0, 2, 0.1, 1.0, 0.0),
    (
        "obc_cycle_energy",
        0x103E,
        "Cycle energy transformed",
        "kWh",
        ECU_CHG,
        0,
        2,
        0.01,
        1.0,
        0.0,
    ),
    (
        "socket_a_voltage",
        0x1525,
        "Charge socket A voltage",
        "V",
        ECU_CHG,
        0,
        2,
        1.0,
        1.0,
        0.0,
    ),
    (
        "hv_output_voltage",
        0x1DA7,
        "Charger HV output voltage",
        "V",
        ECU_CHG,
        0,
        2,
        0.1,
        1.0,
        0.0,
    ),
    (
        "socket_a_temp",
        0x1DDA,
        "Charge socket A temperature",
        "degC",
        ECU_CHG,
        1,
        2,
        0.1,
        1.0,
        -55.0,
    ),
)

# DIDs carrying two values in one response.
_CS_PAIRS: tuple[tuple, ...] = (
    ("cell_soc_max", 0x1E2C, "Cell with the highest SoC", "%", ECU_BAT),
    ("cell_soc_min", 0x1E2D, "Cell with the lowest SoC", "%", ECU_BAT),
)

# Profile rows whose generic arithmetic is not the whole story.  These are
# decoder changes backed by the 2026-10-21 payload research, not guesses:
#   * 0x1E39 is signed and carries a sentinel (2,047.97 V unsigned);
#   * 0x2A53[3:5] is the complement of the real current at [1:3], so decoding
#     it as a second current fabricated ~250 A;
#   * 0x5867 is a 10 km/LSB counter, not the total odometer.
_CS_SPECIAL_DECODERS = {
    "bat_charging_voltage": _bat_charging_voltage,
    "windshield_temperature": _windshield_temperature,
    "co2_content_interior": _co2_content_interior,
}

_CS_RAW_ONLY = {"dc_dc_converter_reserved_current"}

_CS_SPECIAL_NOTES = {
    "bat_charging_voltage": (
        "s16be[0:2] x 0.0312499784501336 [CS profile #342]; the profile "
        "declares SIGNED. 0xFFFF/0xFFFE and negative values are treated as "
        "sentinel/invalid -> None. Historical unsigned rows may still contain "
        "2,047.97 V; raw bytes are preserved."
    ),
    "odometer_2": (
        "u16be[2:4] x 10 km/LSB [CS profile #342]; a secondary distance "
        "counter, not the odometer. The x10 delta matched a ~10.5 km drive, "
        "which refutes x100. On this car the counter equals the main odometer "
        "minus 99,869-99,871 km, consistent with a BMS replacement/reset at "
        "~99,870 km; service history is not available, so its absolute zero "
        "still cannot be asserted. Do not use it for distance or SoH."
    ),
    "windshield_temperature": (
        "byte 0 x 0.1 degC; the profile's u16be[0:2] x15.625 -39.5 gives "
        "488,507 degC for raw 7A2321C07D and is arithmetically refuted. Byte 0 "
        "tracks the same-session outside temperature within a few degrees "
        "across 41 rows. Whether it is the windshield itself or an ambient "
        "sensor still needs the cold-soak/defroster test; raw bytes preserved."
    ),
    "co2_content_interior": (
        "u16be[0:2] ppm; 0x03FE is the not-fitted sentinel (40/40 rows and "
        "cell_t_18..cell_t_23) and decodes to None. The profile's "
        "SBI=1 DL=1 x100 layout turns 03FE into 25,400 ppm and would turn a "
        "normal 500 ppm frame 01F4 into 24,400 ppm, so that layout is "
        "rejected. A non-sentinel scale remains provisional."
    ),
}


def _register_profile_fields(reg: DIDRegistry) -> None:
    for key, did, name, unit, ecu, sbi, dl, mul, div, ofs in _CS_FIELDS:
        decode = _CS_SPECIAL_DECODERS.get(key)
        if key not in _CS_RAW_ONLY and decode is None:
            decode = _cs_field_decoder(
                sbi=sbi, dl=dl, mul=mul, div=div, ofs=ofs)
        reg.register(
            DIDSpec(
                key,
                ecu,
                did,
                name,
                unit,
                decode=decode,
                doc_status=_EXP,
                slow=True,
                notes=_CS_SPECIAL_NOTES.get(
                    key, _cs_note(sbi, dl, mul, div, ofs)),
            )
        )
    for key, did, name, unit, ecu in _CS_PAIRS:
        reg.register(
            DIDSpec(
                key,
                ecu,
                did,
                name,
                unit,
                decode=_cs_pair_decoder(
                    dl=2, first_mul=0.01, first_div=1.0, first_ofs=0.0
                ),
                doc_status=_EXP,
                slow=True,
                notes=f"u16be[0:2] * 0.01 and u16be[2:4] as a cell index [{CS_SRC}]",
            )
        )


def _register_bms(reg: DIDRegistry) -> None:
    reg.register(
        DIDSpec(
            "soc_abs",
            ECU_BAT,
            0x028C,
            "State of charge (BMS)",
            "%",
            decode=_soc_bms,
            doc_status=_EXP,
            notes="u8 / 2.5 [evDash; CS log: raw 203 -> 81.2 %]",
        )
    )
    reg.register(
        DIDSpec(
            "soc_normal",
            ECU_BAT,
            0x028C,
            "State of charge (display)",
            "%",
            decode=_soc_display,
            doc_status=_EXP,
            notes="raw*0.4425-6.1947 [evDash dash fit; CS log: -> 83.63 %]",
        )
    )
    reg.register(
        DIDSpec(
            "pack_voltage",
            ECU_BAT,
            0x1E3B,
            "HV pack voltage",
            "V",
            decode=_pack_voltage,
            doc_status=_EXP,
            notes="u16 / 4 [evDash; CS log: raw 1725 (0x06BD) -> 431.25 V]",
        )
    )
    reg.register(
        DIDSpec(
            "pack_current",
            ECU_BAT,
            0x1E3D,
            "HV pack current",
            "A",
            decode=_pack_current,
            doc_status=_EXP,
            notes="(u32-150000)/100; NEGATIVE = discharge. Raw 000248FD -> "
            "-2.43 A while the discharge counter rose; Car Scanner/ABRP "
            "negate for display. Do not invert the arithmetic.",
        )
    )
    reg.register(
        DIDSpec(
            "cell_voltage_max",
            ECU_BAT,
            0x1E33,
            "Cell voltage max",
            "V",
            decode=_cell_voltage_scaled,
            doc_status=_EXP,
            notes="u16 / 4096 [evDash; CS log: 16377 -> 3.9984 V]; "
            "the 1-based cell number is bytes 2-3 (cell_voltage_max_cell)",
        )
    )
    reg.register(
        DIDSpec(
            "cell_voltage_min",
            ECU_BAT,
            0x1E34,
            "Cell voltage min",
            "V",
            decode=_cell_voltage_scaled,
            doc_status=_EXP,
            notes="u16 / 4096 [evDash]; the 1-based cell number is bytes 2-3 "
            "(cell_voltage_min_cell)",
        )
    )
    # Car Scanner exposes the same 4-byte DID as a second row: bytes 2-3 are the
    # 1-based cell number. The old decoder discarded them, which is why the
    # catalogue could show the extreme value but not which cell produced it.
    reg.register(
        DIDSpec(
            "cell_voltage_max_cell",
            ECU_BAT,
            0x1E33,
            "Cell number with max voltage",
            "",
            decode=_index_u16,
            doc_status=_EXP,
            slow=True,
            notes="u16be[2:4], 1-based cell number [CS profile #342; "
            "offline cross-check 28/31 sweeps]",
        )
    )
    reg.register(
        DIDSpec(
            "cell_voltage_min_cell",
            ECU_BAT,
            0x1E34,
            "Cell number with min voltage",
            "",
            decode=_index_u16,
            doc_status=_EXP,
            slow=True,
            notes="u16be[2:4], 1-based cell number [CS profile #342; "
            "offline cross-check 29/31 sweeps]",
        )
    )
    reg.register(
        DIDSpec(
            "battery_temp",
            ECU_BAT,
            0x2A0B,
            "HV battery temperature",
            "degC",
            decode=_battery_temp,
            doc_status=_EXP,
            notes="u8 / 2 - 40 [evDash; CS log: 120 -> 20.0 C]",
        )
    )
    reg.register(
        DIDSpec(
            "bat_temp_max",
            ECU_BAT,
            0x1E0E,
            "HV battery temperature max",
            "degC",
            decode=_temp_scaled,
            doc_status=_EXP,
            notes="s16 / 64 [evDash; CS log: 20.875 C; CS profile: signed]; "
            "the 1-based sensor number is bytes 2-3 (bat_temp_max_sensor)",
        )
    )
    reg.register(
        DIDSpec(
            "bat_temp_min",
            ECU_BAT,
            0x1E0F,
            "HV battery temperature min",
            "degC",
            decode=_temp_scaled,
            doc_status=_EXP,
            notes="s16 / 64 [evDash; CS log: 19.625 C; CS profile: signed]; "
            "the 1-based sensor number is bytes 2-3 (bat_temp_min_sensor)",
        )
    )
    reg.register(
        DIDSpec(
            "bat_temp_max_sensor",
            ECU_BAT,
            0x1E0E,
            "Temperature sensor with max reading",
            "",
            decode=_index_u16,
            doc_status=_EXP,
            slow=True,
            notes="u16be[2:4], 1-based sensor number [CS profile #342; "
            "offline cross-check 31/31 sweeps]",
        )
    )
    reg.register(
        DIDSpec(
            "bat_temp_min_sensor",
            ECU_BAT,
            0x1E0F,
            "Temperature sensor with min reading",
            "",
            decode=_index_u16,
            doc_status=_EXP,
            slow=True,
            notes="u16be[2:4], 1-based sensor number [CS profile #342; "
            "offline cross-check 30/31 sweeps]",
        )
    )
    reg.register(
        DIDSpec(
            "energy_counters",
            ECU_BAT,
            0x1E32,
            "Energy counters",
            "kWh",
            decode=_energy_counters,
            doc_status=_EXP,
            slow=True,
            notes="FOUR signed s32 fields from CS MEB#342: charge Ah @0, "
            "discharge Ah @4, charge kWh @8, discharge kWh @12; "
            "Ah x 0.001820444117116, kWh x 116508.398/1e9. The old "
            "@0/@4 + /8583.071 layout read Ah counts as kWh.",
        )
    )
    reg.register(
        DIDSpec(
            "dyn_charge_limit",
            ECU_BAT,
            0x1E1B,
            "Dynamic charge current limit",
            "A",
            decode=lambda raw: _u16_over(raw, 5.0),
            doc_status=_EXP,
            notes="u16 / 5 [evDash; CS log: 213 A]",
        )
    )
    reg.register(
        DIDSpec(
            "dyn_discharge_limit",
            ECU_BAT,
            0x1E1C,
            "Dynamic discharge current limit",
            "A",
            decode=lambda raw: _u16_over(raw, 5.0),
            doc_status=_EXP,
            slow=True,
            notes="u16 / 5 [evDash]",
        )
    )
    reg.register(
        DIDSpec(
            "charge_mode",
            ECU_BAT,
            0x7448,
            "HV charge mode code",
            "code",
            decode=_charge_mode,
            doc_status=_EXP,
            notes="byte0 bit field: bit2 = charging, +bit1 = DC -> 1 AC / "
            "2 DC, 0/1 -> 0, other bits -> unknown (see "
            "decoders.charging.CHARGE_MODE_CODES) [evDash; CS profile]",
        )
    )
    reg.register(
        DIDSpec(
            "op_mode",
            ECU_BAT,
            0x7448,
            "BMS operation mode",
            "code",
            decode=_op_mode,
            doc_status=_EXP,
            notes="byte0; 0=standby, 1=driving, 4=AC, 6=DC [evDash]",
        )
    )
    reg.register(
        DIDSpec(
            "charge_port_normal",
            ECU_BAT,
            0x7448,
            "Normal (AC) charge port connected",
            "",
            decode=_bit2,
            doc_status=_EXP,
            slow=True,
            notes="byte0 bit 2, 0/1 [CS profile #342 BitValue]",
        )
    )
    reg.register(
        DIDSpec(
            "charge_port_rapid",
            ECU_BAT,
            0x7448,
            "Rapid (DC) charge port connected",
            "",
            decode=_bit3,
            doc_status=_EXP,
            slow=True,
            notes="byte0 bit 3, 0/1 [CS profile #342 BitValue]",
        )
    )
    reg.register(
        DIDSpec(
            "coolant_pump",
            ECU_BAT,
            0x743B,
            "Battery coolant pump",
            "%",
            decode=_byte,
            doc_status=_EXP,
            slow=True,
            notes="byte0 [evDash]",
        )
    )
    reg.register(
        DIDSpec(
            "coolant_temps",
            ECU_BAT,
            0x189D,
            "Battery cooling liquid",
            "degC",
            decode=_coolant_temps,
            doc_status=_EXP,
            notes="outlet = s16[0:2]/64, inlet = s16[2:4]/64 [spot; evDash; "
            "CS profile: signed]",
        )
    )
    reg.register(
        DIDSpec(
            "ptc_current",
            ECU_BAT,
            0x1620,
            "PTC heater battery current",
            "A",
            decode=lambda raw: _byte_over(raw, 4.0),
            doc_status=_EXP,
            notes="u8 / 4 [spot]",
        )
    )
    reg.register(
        DIDSpec(
            "speed_kmh",
            ECU_BAT,
            0xF40D,
            "Vehicle speed (BMS)",
            "km/h",
            decode=_byte,
            doc_status=_EXP,
            notes="byte0, km/h [evDash; CS log reports 0 while parked]",
        )
    )
    reg.register(
        DIDSpec(
            "battery_serial",
            ECU_BAT,
            0x0500,
            "HV battery serial",
            "",
            decode=_ascii,
            doc_status=_EXP,
            slow=True,
            notes="raw ASCII, trimmed; observed '288 WARMF F265' [CS log]",
        )
    )
    # 0xF802 on the BMS returns NRC 0x31 on this car. The usable VIN is the
    # veh_info 0xF802 entry registered as "vin"; keeping a second, always-failing
    # request here only added one refused read per slow phase.


def _register_cells(reg: DIDRegistry) -> None:
    for i in range(CELL_V_SLOTS):
        reg.register(
            DIDSpec(
                f"cell_v_{i:03d}",
                ECU_BAT,
                CELL_V_BASE + i,
                f"Cell voltage #{i + 1:03d}",
                "V",
                decode=_cell_voltage,
                doc_status=_EXP,
                slow=True,
                notes="u16/1000 + 1; 0x0FFE = unpopulated slot [evDash]",
            )
        )
    for i, did in enumerate(CELL_T_DIDS):
        reg.register(
            DIDSpec(
                f"cell_t_{i:02d}",
                ECU_BAT,
                did,
                f"Battery temperature sensor #{i + 1:02d}",
                "degC",
                decode=_cell_temp,
                doc_status=_EXP,
                slow=True,
                notes="u16/8 - 40 [evDash]",
            )
        )


def _register_modules(reg: DIDRegistry) -> None:
    # 0x2AB2 / 0x2AB8 -- see the ENERGY DECODER RESOLUTION note in the module
    # docstring for why these decode with the Car Scanner profile layout rather
    # than the evDash u32/1310.77 one. The ecu_key stays "energy" (0x710 ->
    # 0x77A) because that is the addressing this profile has actually tried;
    # changing it here would be an address change with no evidence behind it.
    reg.register(
        DIDSpec(
            "hv_energy_max",
            ECU_ENERGY,
            0x2AB2,
            "Max HV energy content",
            "Wh",
            decode=_energy_max_wh,
            doc_status=_EXP,
            slow=True,
            notes="u16be[0:2] x 50 Wh unsigned [Car Scanner MEB profile idx 342: "
            "SBI=0 DL=2 MUL=50 SIG=false RBS=false UN=61(Wh)]. CONFIRMED on "
            "captured bytes 2026-10-15: single-frame 62 2A B2 04 28 04 00 -> "
            "0x0428 = 1064 x 50 = 53,200 Wh, matching Car Scanner's displayed "
            "value (passive HCI capture).",
        )
    )
    reg.register(
        DIDSpec(
            "hv_energy_content",
            ECU_ENERGY,
            0x2AB8,
            "HV energy content",
            "Wh",
            decode=_energy_content_wh,
            doc_status=_EXP,
            slow=True,
            notes="u16be[2:4] x 25 Wh unsigned [Car Scanner MEB profile idx 342: "
            "SBI=2 DL=2 MUL=25 SIG=false RBS=false UN=61(Wh)]. CONFIRMED on "
            "captured bytes 2026-10-15: multi-frame 62 2A B8 00 00 05 7F 00 "
            "00 00 00 -> u16be[2:4] 0x057F = 1407 x 25 = 35,175 Wh, matching "
            "Car Scanner's displayed value (passive HCI capture).",
        )
    )
    reg.register(
        DIDSpec(
            "aux_12v_voltage",
            ECU_ENERGY,
            0x2AF7,
            "12V battery voltage (BMS)",
            "V",
            decode=_amb_12v,
            doc_status=_EXP,
            slow=True,
            notes="u16 x 0.001 + 4.0, first field of multi-frame 0x2AF7 "
            "[Car Scanner MEB profile SBI=0 DL=2 MUL=0.001 OFS=4.0]",
        )
    )
    reg.register(
        DIDSpec(
            "aux_12v_current",
            ECU_ENERGY,
            0x2AF7,
            "12V battery current",
            "A",
            decode=_aux_12v_current,
            doc_status=_EXP,
            slow=True,
            notes="s32be x 0.001 at offset 2 of multi-frame 0x2AF7 "
            "[Car Scanner MEB profile SBI=2 DL=4 MUL=0.001 SIG=true]",
        )
    )
    for key, name, unit, sbi, dl, mul, ofs, note in (
        ("aux_12v_soc", "12V battery state of charge", "%", 7, 1, 1.0, 0.0, "u8[7]"),
        (
            "aux_12v_temp",
            "12V battery temperature",
            "degC",
            8,
            1,
            1.0,
            -40.0,
            "u8[8] - 40",
        ),
        (
            "aux_12v_sensor_temp",
            "12V battery sensor temperature",
            "degC",
            9,
            1,
            1.0,
            -40.0,
            "u8[9] - 40",
        ),
        (
            "aux_12v_total_charge",
            "12V battery total charge",
            "Ah",
            12,
            4,
            0.1,
            0.0,
            "u32be[12:16] x 0.1",
        ),
        (
            "aux_12v_total_discharge",
            "12V battery total discharge",
            "Ah",
            16,
            4,
            0.1,
            0.0,
            "u32be[16:20] x 0.1",
        ),
        (
            "aux_12v_aging_capacity",
            "12V battery aging (capacity)",
            "%",
            20,
            1,
            1.0,
            0.0,
            "u8[20]",
        ),
        (
            "aux_12v_aging_power",
            "12V battery aging (power)",
            "%",
            21,
            1,
            1.0,
            0.0,
            "u8[21]",
        ),
        (
            "aux_12v_adapted_capacity",
            "12V battery adapted capacity",
            "Ah",
            22,
            1,
            1.0,
            0.0,
            "u8[22]",
        ),
    ):
        reg.register(
            DIDSpec(
                key,
                ECU_ENERGY,
                0x2AF7,
                name,
                unit,
                decode=_cs_field_decoder(sbi=sbi, dl=dl, mul=mul, div=1.0, ofs=ofs),
                doc_status=_EXP,
                slow=True,
                notes=f"{note}, multi-frame 0x2AF7 [{CS_SRC}]",
            )
        )
    reg.register(
        DIDSpec(
            "dcdc_current",
            ECU_DCDC,
            0x465B,
            "DC/DC charging current",
            "A",
            decode=lambda raw: _u16_over(raw, 16.0),
            doc_status=_EXP,
            slow=True,
            notes="u16 / 16 [evDash CarMEB]",
        )
    )
    reg.register(
        DIDSpec(
            "dcdc_voltage",
            ECU_DCDC,
            0x465D,
            "DC/DC output voltage",
            "V",
            decode=lambda raw: _u16_over(raw, 512.0),
            doc_status=_EXP,
            slow=True,
            notes="u16 / 512 [evDash CarMEB]",
        )
    )
    reg.register(
        DIDSpec(
            "gps_position",
            ECU_GPS,
            0x2430,
            "GPS position (latitude, longitude, altitude)",
            "",
            decode=_gps_position,
            doc_status=_EXP,
            slow=True,
            notes="ASCII DMS lat/lon + u16 altitude - 501 at record[28:30] "
            "[evDash CarVWID3 parser + sample record]; multi-frame; not yet "
            "read on this car",
        )
    )
    reg.register(
        DIDSpec(
            "odometer_km",
            ECU_VEH,
            0x295A,
            "Odometer",
            "km",
            decode=_odometer_km,
            doc_status=_EXP,
            slow=True,
            notes="3-byte big-endian [evDash CarMEB]",
        )
    )
    reg.register(
        DIDSpec(
            "gear",
            ECU_VEH,
            0x210E,
            "Gear position",
            "",
            decode=_gear,
            doc_status=_EXP,
            slow=True,
            notes="data byte 1 [evDash CarMEB]",
        )
    )
    reg.register(
        DIDSpec(
            "vin",
            ECU_VEH,
            0xF802,
            "VIN (gateway)",
            "",
            decode=_vin,
            doc_status=_EXP,
            slow=True,
            notes="raw ASCII [evDash CarVWID3]",
        )
    )
    reg.register(
        DIDSpec(
            "hv_aux_power",
            ECU_VEH,
            0x0364,
            "HV auxiliary consumer power",
            "kW",
            # Signed (profile 0x0364, signed=True): auxiliary consumers can
            # feed power back, and unsigned a -0.5 kW read as 6553.1 kW.
            decode=lambda raw: _s16be(raw) / 10.0,
            doc_status=_EXP,
            notes="s16 / 10 [spot; CS profile: signed]",
        )
    )
    reg.register(
        DIDSpec(
            "outside_temp",
            ECU_CLIMATE,
            0x2609,
            "Outside temperature",
            "degC",
            decode=_outdoor_temp,
            doc_status=_EXP,
            slow=True,
            notes="u8 / 2 - 50 [evDash CarVWID3]",
        )
    )
    reg.register(
        DIDSpec(
            "inside_temp",
            ECU_CLIMATE,
            0x2613,
            "Inside temperature",
            "degC",
            decode=_cabin_temp,
            doc_status=_EXP,
            slow=True,
            notes="u16 / 5 - 40 [evDash CarVWID3]",
        )
    )

    # -- [19.Gate] additional gateway measurements (Car Scanner MEB profile) --
    # Same 0x710 -> 0x77A 11-bit path as the energy DIDs (ATSP6 / ATFCSH710 /
    # ATCRA77A), which the 2026-10-15 passive capture confirmed. Scalings are
    # the profile's SBI/DL/MUL/DIV/OFS fields, verbatim, recovered from the
    # extracted MEB profile (profile_EV_MEB_*.json). EXPERIMENTAL: not yet read
    # via this project's own collector, but the addressing and the field layout
    # come straight from Car Scanner's profile, not from a guess.
    # The three range DIDs are in MILES on this (UK) car: Car Scanner's own
    # export labels them miles (tools/import_carscanner.py), and the owner's
    # reading of 194 near 87 % charge only fits miles (194 km would mean about
    # 24 kWh/100 km). They follow the cluster's distance unit; a car set to km
    # would report km.
    for key, did, name, unit, sbi, dl, mul, ofs, slow, note in (
        (
            "range_displayed",
            0x2AB6,
            "Estimated range (displayed)",
            "miles",
            0,
            2,
            1.0,
            0.0,
            False,
            "u16be[0:2] x 1",
        ),
        (
            "range_can",
            0x2AB5,
            "Estimated range (CAN bus)",
            "miles",
            4,
            2,
            1.0,
            0.0,
            False,
            "u16be[4:6] x 1",
        ),
        (
            "range_internal",
            0x2AB4,
            "Estimated range (internal)",
            "miles",
            2,
            2,
            1.0,
            0.0,
            False,
            "u16be[2:4] x 1",
        ),
        (
            "dcdc_gw_voltage",
            0x2A53,
            "DC-DC converter low voltage",
            "V",
            0,
            1,
            0.1,
            0.0,
            False,
            "u8[0] x 0.1",
        ),
        (
            "dcdc_gw_current",
            0x2A53,
            "DC-DC converter current",
            "A",
            1,
            2,
            1.0,
            -511.0,
            False,
            "u16be[1:3] - 511",
        ),
        (
            "hv_em_power_limit",
            0x15D1,
            "HV-EM power limitation",
            "W",
            1,
            2,
            50.0,
            0.0,
            False,
            "u16be[1:3] x 50",
        ),
    ):
        reg.register(
            DIDSpec(
                key,
                ECU_ENERGY,
                did,
                name,
                unit,
                decode=_cs_field_decoder(sbi=sbi, dl=dl, mul=mul, div=1.0, ofs=ofs),
                doc_status=_EXP,
                slow=slow,
                notes=f"{note} [{CS_SRC}]",
            )
        )
    # Average energy consumption per road category (DID 0x2AB7, 7 x u16be x0.1
    # kWh/100km packed at offsets 0,2,4,6,8,10,12). Slow-changing, polled rarely.
    for cat in range(7):
        reg.register(
            DIDSpec(
                f"road_avg_consumption_{cat}",
                ECU_ENERGY,
                0x2AB7,
                f"Avg consumption (roads cat {cat})",
                "kWh/100km",
                decode=_cs_field_decoder(sbi=cat * 2, dl=2, mul=0.1, div=1.0, ofs=0.0),
                doc_status=_EXP,
                slow=True,
                notes=f"u16be[{cat * 2}:{cat * 2 + 2}] x 0.1 [{CS_SRC}]",
            )
        )

    # Battery resistance DIDs (gateway 0x710 -> 0x77A, same module as the two
    # VERIFIED energy DIDs above). Definitions are from the OBDb/Volkswagen-ID3
    # signalset (CC-BY-SA-4.0), which lists both as confirmed for the 2021
    # model year; the addressing is already confirmed by this project's passive
    # capture. PROVISIONAL: no project-owned capture of either DID exists yet,
    # so the scale rests on the third-party definition, not on raw bytes.
    reg.register(
        DIDSpec(
            "bat_internal_resistance_raw",
            ECU_ENERGY,
            0x2A25,
            "HV battery internal resistance (raw)",
            "",
            decode=_cs_field_decoder(sbi=0, dl=1, mul=1.0, div=5.0, ofs=0.0),
            doc_status=_EXP,
            slow=True,
            notes="u8[0] / 5 raw index (0-51) [OBDb Volkswagen-ID3 signalset "
            "v3, ID3_BAT_INTERNAL_RES_RAW, confirmed MY2021]. The physical "
            "unit is not documented by the source ('unknown'); treat as a "
            "relative battery-resistance index, not mOhm. PROVISIONAL.",
        )
    )
    reg.register(
        DIDSpec(
            "aux_battery_resistance_mohm",
            ECU_ENERGY,
            0x2A0E,
            "12 V (start/stop) battery resistance",
            "mOhm",
            decode=_cs_field_decoder(sbi=0, dl=1, mul=2.0, div=10.0, ofs=0.0),
            doc_status=_EXP,
            slow=True,
            notes="u8[0] x 2 / 10 mOhm [OBDb Volkswagen-ID3 signalset v3, "
            "ID3_SSB_R, confirmed MY2020-2021]. Start/stop (12 V auxiliary) "
            "battery internal resistance; higher = ageing. PROVISIONAL.",
        )
    )


# 25 of the 26 electric-drive rows declare FLOAT32(A,B,C,D) in Car Scanner MEB
# profile #342. The exception is `eldrive_odometer` (a 4-byte integer ByteSet).
# The profile's DL field is 2 for most of these rows even though the formula
# consumes four bytes, so the float decoder always reads four bytes at offset 0.
_FLOAT32_KEYS = frozenset({
    "actual_mechanical_torque_calculated",
    "actual_mechanical_torque_commanded_value",
    "actual_possible_maximum_torque",
    "actual_possible_minimum_torque",
    "dc_link_current",
    "dc_link_voltage",
    "dc_link_voltage_reserve",
    "dmcm_stator_voltage_d_direction_actual_value",
    "dmcm_stator_voltage_d_direction_commanded_value",
    "dmcm_stator_voltage_q_direction_actual_value",
    "dmcm_stator_voltage_q_direction_commanded_value",
    "desired_link_voltage",
    "ecu_pcb_temperature_1",
    "ecu_pcb_temperature_2",
    "electric_drive_inflow_coolant_temperature",
    "electric_drive_motor_speed_rad_sec",
    "inverter_power_loss_phase_u",
    "inverter_power_loss_phase_v",
    "inverter_power_loss_phase_w",
    "motor_maximum_current",
    "motor_minimum_current",
    "rotor_temperature_model",
    "stator_electrical_frequency_rad_sec",
    "stator_temperature",
    "torque_calculated_from_measured_currents",
})


def _register_profile_extras(reg: DIDRegistry) -> None:
    """Register remaining Car Scanner MEB profile DIDs (2026-10-16).

    Recovered from the extracted MEB profile JSON (profile_EV_MEB_*.json).
    All read-only (UDS 0x22); scalings verbatim from SBI/DL/MUL/DIV/OFS/SIG.
    Covers vehicle-info (0x76), electric drive (0x7C), HVAC (0x746), GPS
    (0x767) and the remaining gateway (0x710) fields. EXPERIMENTAL: not yet
    read via this project's own collector.
    """
    # (key, ecu, did, name, unit, sbi, dl, mul, div, ofs, sig, slow)
    table = (
        (
            "front_drive_actual_torque",
            ECU_VEH,
            0x0335,
            "Front drive actual torque",
            "Nm",
            0,
            2,
            1,
            1,
            0,
            True,
            False,
        ),
        (
            "rear_drive_actual_torque",
            ECU_VEH,
            0x033B,
            "Rear drive actual torque",
            "Nm",
            0,
            2,
            1,
            1,
            0,
            True,
            False,
        ),
        (
            "front_rear_torque_difference",
            ECU_VEH,
            0x0343,
            "Front:Rear torque difference",
            "Nm",
            0,
            2,
            0.5,
            1,
            0,
            True,
            False,
        ),
        (
            "rated_torque_on_the_front_axle",
            ECU_VEH,
            0x0344,
            "Rated torque on the front axle",
            "Nm",
            0,
            2,
            0.5,
            1,
            0,
            True,
            False,
        ),
        (
            "steering_wheel_angle",
            ECU_VEH,
            0x0346,
            "Steering wheel angle",
            "deg",
            0,
            2,
            0.1,
            1,
            0,
            False,
            False,
        ),
        (
            "hv_battery_current",
            ECU_VEH,
            0x035E,
            "HV Battery current",
            "A",
            0,
            2,
            0.1,
            1,
            0,
            True,
            True,
        ),
        (
            "additional_high_voltage_consumers_total_current",
            ECU_VEH,
            0x0360,
            "Additional high-voltage consumers, total current",
            "A",
            0,
            2,
            0.01,
            1,
            0,
            True,
            False,
        ),
        (
            "brake_booster_pressure",
            ECU_VEH,
            0x2005,
            "Brake booster pressure",
            "bar",
            0,
            2,
            0.001,
            1,
            0,
            False,
            False,
        ),
        (
            "radiator_fan_duty",
            ECU_VEH,
            0x2055,
            "Radiator fan duty",
            "%",
            0,
            2,
            0.01,
            1,
            0,
            False,
            False,
        ),
        (
            "coolant_circulation_pump_duty",
            ECU_VEH,
            0x276D,
            "Coolant circulation pump duty",
            "%",
            0,
            2,
            0.01,
            1,
            0,
            False,
            False,
        ),
        (
            "driver_requested_torque",
            ECU_VEH,
            0x29D4,
            "Driver requested torque",
            "Nm",
            0,
            2,
            0.1,
            1,
            0,
            False,
            False,
        ),
        (
            "battery_soc_normalized",
            ECU_VEH,
            0x3DEF,
            "Battery SoC (normalized)",
            "%",
            0,
            2,
            0.01,
            1,
            0,
            True,
            True,
        ),
        (
            "motor_rpm_2",
            ECU_VEH,
            0x3DF8,
            "Motor RPM",
            "rpm",
            0,
            2,
            0.5,
            1,
            0,
            True,
            False,
        ),
        (
            "dc_dc_converter_coolant_temperature_calculated",
            ECU_VEH,
            0x43B8,
            "DC-DC converter coolant temperature Calculated",
            "degC",
            0,
            2,
            0.1,
            1,
            0,
            True,
            False,
        ),
        (
            "acceleration",
            ECU_VEH,
            0x4539,
            "Acceleration",
            "m/s2",
            0,
            2,
            0.001,
            1,
            0,
            True,
            False,
        ),
        (
            "power_electronics_voltage_in_traction_mode",
            ECU_VEH,
            0x4675,
            "Power electronics: voltage in traction mode",
            "V",
            0,
            2,
            0.1,
            1,
            0,
            True,
            False,
        ),
        (
            "total_distance_travelled_lifetime",
            ECU_VEH,
            0xF817,
            "Total distance travelled (Lifetime)",
            "km",
            4,
            4,
            0.1,
            1,
            0,
            True,
            False,
        ),
        (
            "torque_calculated_from_measured_currents",
            ECU_ELDRIVE,
            0x0308,
            "Torque, calculated from measured currents",
            "Nm",
            0,
            2,
            1,
            10,
            0,
            False,
            False,
        ),
        (
            "ecu_pcb_temperature_1",
            ECU_ELDRIVE,
            0x0805,
            "ECU PCB Temperature #1",
            "degC",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "ecu_pcb_temperature_2",
            ECU_ELDRIVE,
            0x0806,
            "ECU PCB Temperature #2",
            "degC",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "eldrive_odometer",
            ECU_ELDRIVE,
            0x086D,
            "Odometer",
            "km",
            0,
            4,
            1,
            1,
            0,
            False,
            True,
        ),
        (
            "dmcm_stator_voltage_d_direction_actual_value",
            ECU_ELDRIVE,
            0x1007,
            "DMCM stator voltage D direction actual value",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dmcm_stator_voltage_q_direction_actual_value",
            ECU_ELDRIVE,
            0x1008,
            "DMCM stator voltage Q direction actual value",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dmcm_stator_voltage_d_direction_commanded_value",
            ECU_ELDRIVE,
            0x1009,
            "DMCM stator voltage D direction commanded value",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dmcm_stator_voltage_q_direction_commanded_value",
            ECU_ELDRIVE,
            0x100A,
            "DMCM stator voltage Q direction commanded value",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "inverter_power_loss_phase_u",
            ECU_ELDRIVE,
            0x1040,
            "Inverter power loss phase U",
            "W",
            0,
            4,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "inverter_power_loss_phase_v",
            ECU_ELDRIVE,
            0x1041,
            "Inverter power loss phase V",
            "W",
            0,
            4,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "inverter_power_loss_phase_w",
            ECU_ELDRIVE,
            0x1042,
            "Inverter power loss phase W",
            "W",
            0,
            4,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "rotor_temperature_model",
            ECU_ELDRIVE,
            0x2CCC,
            "Rotor temperature model",
            "degC",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dc_link_voltage",
            ECU_ELDRIVE,
            0x3E93,
            "DC Link voltage",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dc_link_current",
            ECU_ELDRIVE,
            0x3E98,
            "DC Link current",
            "A",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "motor_maximum_current",
            ECU_ELDRIVE,
            0x3EE7,
            "Motor maximum current",
            "A",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "motor_minimum_current",
            ECU_ELDRIVE,
            0x3EE8,
            "Motor minimum current",
            "A",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "actual_mechanical_torque_calculated",
            ECU_ELDRIVE,
            0x3EE9,
            "Actual mechanical torque calculated",
            "Nm",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "actual_possible_maximum_torque",
            ECU_ELDRIVE,
            0x3EEA,
            "Actual possible maximum torque",
            "Nm",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "actual_possible_minimum_torque",
            ECU_ELDRIVE,
            0x3EEB,
            "Actual possible minimum torque",
            "Nm",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "desired_link_voltage",
            ECU_ELDRIVE,
            0x3EED,
            "Desired link voltage",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "electric_drive_motor_speed_rad_sec",
            ECU_ELDRIVE,
            0x40C8,
            "Electric drive motor speed (rad/sec)",
            "",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "dc_link_voltage_reserve",
            ECU_ELDRIVE,
            0x43B9,
            "DC Link voltage reserve",
            "V",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "stator_electrical_frequency_rad_sec",
            ECU_ELDRIVE,
            0x4657,
            "Stator electrical frequency (rad/sec)",
            "",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "stator_temperature",
            ECU_ELDRIVE,
            0x4672,
            "Stator temperature",
            "degC",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "electric_drive_inflow_coolant_temperature",
            ECU_ELDRIVE,
            0x4FAE,
            "Electric drive inflow coolant temperature",
            "degC",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "actual_mechanical_torque_commanded_value",
            ECU_ELDRIVE,
            0x58E1,
            "Actual mechanical torque commanded value",
            "Nm",
            0,
            2,
            1,
            10,
            1,
            False,
            False,
        ),
        (
            "a_c_compressor_power_consumption_actual_value",
            ECU_CLIMATE,
            0x0800,
            "A/C compressor power consumption: actual value",
            "W",
            5,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "a_c_compressor_speed",
            ECU_CLIMATE,
            0x2603,
            "A/C compressor speed",
            "rpm",
            0,
            1,
            100,
            1,
            0,
            False,
            False,
        ),
        (
            "a_c_compressor_load",
            ECU_CLIMATE,
            0x2604,
            "A/C compressor load",
            "Nm",
            0,
            1,
            0.1,
            1,
            0,
            False,
            False,
        ),
        (
            "refrigerant_pressure",
            ECU_CLIMATE,
            0x2605,
            "Refrigerant pressure",
            "bar",
            0,
            1,
            0.2,
            1,
            0,
            False,
            False,
        ),
        (
            "radiator_fan_commanded_duty",
            ECU_CLIMATE,
            0x2607,
            "Radiator fan commanded duty",
            "%",
            0,
            1,
            0.4,
            1,
            0,
            False,
            False,
        ),
        (
            "outside_temperature_calculated_from_cabin",
            ECU_CLIMATE,
            0x260A,
            "Outside temperature (calculated from cabin)",
            "degC",
            0,
            1,
            0.5,
            1,
            -50,
            False,
            True,
        ),
        (
            "front_left_deflector_temperature",
            ECU_CLIMATE,
            0x260C,
            "Front left deflector temperature",
            "degC",
            0,
            1,
            0.333,
            1,
            0,
            False,
            False,
        ),
        (
            "front_right_deflector_temperature",
            ECU_CLIMATE,
            0x260D,
            "Front right deflector temperature",
            "degC",
            0,
            1,
            0.333,
            1,
            0,
            False,
            False,
        ),
        (
            "temperature_after_evaporator",
            ECU_CLIMATE,
            0x2612,
            "Temperature after evaporator",
            "degC",
            0,
            1,
            0.333,
            1,
            0,
            True,
            False,
        ),
        (
            "fresh_air_recirculation_flap_actual_value_steps",
            ECU_CLIMATE,
            0x261E,
            "Fresh air/Recirculation flap actual value steps",
            "",
            0,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "fresh_air_recirculation_flap_actual_value",
            ECU_CLIMATE,
            0x261E,
            "Fresh air/Recirculation flap actual value %",
            "%",
            2,
            1,
            0.5,
            1,
            0,
            False,
            False,
        ),
        (
            "fresh_air_recirculation_flap_specified_value_steps",
            ECU_CLIMATE,
            0x261F,
            "Fresh air/Recirculation flap specified value steps",
            "",
            0,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "fresh_air_recirculation_flap_stop_for_fresh_air",
            ECU_CLIMATE,
            0x2620,
            "Fresh air/Recirculation flap stop for fresh air",
            "",
            0,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "fresh_air_recirculation_flap_stop_for_recirculated_air",
            ECU_CLIMATE,
            0x2621,
            "Fresh air/Recirculation flap stop for recirculated air",
            "",
            0,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "front_left_temperature_flap_actual_value",
            ECU_CLIMATE,
            0x2622,
            "Front left temperature flap actual value",
            "%",
            2,
            1,
            0.5,
            1,
            0,
            False,
            False,
        ),
        (
            "front_right_temperature_flap_actual_value",
            ECU_CLIMATE,
            0x2626,
            "Front right temperature flap actual value",
            "%",
            2,
            1,
            0.5,
            1,
            0,
            False,
            False,
        ),
        (
            "rear_deflector_temperature",
            ECU_CLIMATE,
            0x263D,
            "Rear deflector temperature",
            "degC",
            0,
            1,
            0.333,
            1,
            0,
            False,
            False,
        ),
        (
            "air_polution_level_0_clean_air",
            ECU_CLIMATE,
            0x26ED,
            "Air polution level (0 = clean air)",
            "",
            0,
            1,
            0.1,
            1,
            0,
            True,
            False,
        ),
        (
            "windshield_temperature",
            ECU_CLIMATE,
            0x27C4,
            "Windshield temperature",
            "degC",
            0,
            1,
            0.1,
            1,
            0,
            False,
            False,
        ),
        (
            "cabin_humidity",
            ECU_CLIMATE,
            0x27C4,
            "Cabin humidity",
            "%",
            4,
            1,
            0.5,
            1,
            0,
            False,
            False,
        ),
        (
            "co2_content_interior",
            ECU_CLIMATE,
            0x42DB,
            "CO2 content interior (16-bit ppm; 0x03FE = not fitted)",
            "ppm",
            0,
            2,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "a_c_refrigerant_pressure_and_temperature_sensor_1_pressure",
            ECU_CLIMATE,
            0x4EEF,
            "A/C refrigerant pressure and temperature sensor 1: Pressure",
            "bar",
            0,
            2,
            88,
            10000,
            0,
            False,
            False,
        ),
        (
            "a_c_refrigerant_pressure_and_temperature_sensor_1_temperature",
            ECU_CLIMATE,
            0x4EEF,
            "A/C refrigerant pressure and temperature sensor 1: Temperature",
            "degC",
            2,
            2,
            10,
            100,
            -40,
            False,
            False,
        ),
        (
            "a_c_refrigerant_pressure_and_temperature_sensor_1_interior_temperature",
            ECU_CLIMATE,
            0x4EEF,
            "A/C refrigerant pressure and temperature sensor 1: Interior Temperature",
            "degC",
            4,
            2,
            10,
            100,
            -40,
            False,
            False,
        ),
        (
            "res_pid_0149",
            ECU_CLIMATE,
            0xF449,
            "Accelerator pedal position",
            "%",
            0,
            1,
            1,
            2.55,
            0,
            False,
            False,
        ),
        (
            "number_of_visible_satellites",
            ECU_GPS,
            0x2431,
            "Number of visible satellites",
            "",
            0,
            1,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "number_of_tracked_satellites",
            ECU_GPS,
            0x2431,
            "Number of tracked satellites",
            "",
            1,
            1,
            1,
            1,
            0,
            False,
            False,
        ),
        (
            "hv_em_nv_power_limitation",
            ECU_ENERGY,
            0x15D1,
            "HV-EM NV Power limitation",
            "W",
            3,
            1,
            50,
            1,
            0,
            False,
            False,
        ),
        (
            "nv_energy_requirement",
            ECU_ENERGY,
            0x15D1,
            "NV Energy requirement",
            "Wh",
            4,
            1,
            50,
            1,
            0,
            False,
            False,
        ),
        (
            "hv_em_hv_battery_energy_information",
            ECU_ENERGY,
            0x15D1,
            "HV-EM HV Battery energy information",
            "Wh",
            5,
            2,
            50,
            1,
            0,
            False,
            False,
        ),
        (
            "dc_dc_converter_reserved_current",
            ECU_ENERGY,
            0x2A53,
            "DC-DC current complement (not an independent current)",
            "",
            3,
            2,
            1,
            1,
            -511,
            False,
            False,
        ),
        (
            "average_battery_consumption_internal_value",
            ECU_ENERGY,
            0x2AB4,
            "Average battery consumption (internal value)",
            "A",
            9,
            1,
            1,
            1,
            0,
            False,
            False,
        ),
    )
    for key, ecu, did, name, unit, sbi, dl, mul, div, ofs, sig, slow in table:
        if key in _CS_RAW_ONLY:
            # 0x2A53[3:5] is the complement of the real current at [1:3]
            # (u16[1:3] + u16[3:5] ~= 0x0508 in every capture).  Subtracting
            # the current offset from the complement fabricated ~250 A, so
            # this key is now raw-only until an independent meaning is found.
            reg.register(
                DIDSpec(
                    key,
                    ecu,
                    did,
                    name,
                    unit,
                    decode=None,
                    doc_status=_EXP,
                    slow=slow,
                    notes="u16be[3:5] is the complement of the real current "
                    "at u16be[1:3] (sum ~0x0508); no independent current. "
                    "Raw-only; use dcdc_gw_current for the actual current "
                    f"[{CS_SRC}; 2026-10-21 payload research]",
                )
            )
            continue
        if key in _CS_SPECIAL_DECODERS:
            # The generic SBI/DL arithmetic is not the whole story: 0x27C4's
            # two-byte temperature formula and 0x42DB's byte-1 ppm formula are
            # both refuted by the captured payloads. Use the documented special
            # decoder and keep the raw bytes.
            reg.register(
                DIDSpec(
                    key,
                    ecu,
                    did,
                    name,
                    unit,
                    decode=_CS_SPECIAL_DECODERS[key],
                    doc_status=_EXP,
                    slow=slow,
                    notes=_CS_SPECIAL_NOTES[key],
                )
            )
            continue
        if key in _FLOAT32_KEYS:
            # The profile's DL=2 here describes the displayed field width, not
            # the bytes consumed by FLOAT32(A,B,C,D). Always read four.
            reg.register(
                DIDSpec(
                    key,
                    ecu,
                    did,
                    name,
                    unit,
                    decode=_f32be,
                    doc_status=_EXP,
                    slow=slow,
                    notes="FLOAT32(A,B,C,D), IEEE-754 big-endian at offset 0 "
                    "[CS profile #342]; replaces the former u16/10 decode",
                )
            )
            continue
        reg.register(
            DIDSpec(
                key,
                ecu,
                did,
                name,
                unit,
                decode=_cs_field_decoder(
                    sbi=sbi, dl=dl, mul=mul, div=div, ofs=ofs, sig=sig
                ),
                doc_status=_EXP,
                slow=slow,
                notes=f"{'s' if sig else 'u'}{8 * dl}be[{sbi}:{sbi + dl}] "
                f"x {mul:g}/{div:g} + {ofs:g} [{CS_SRC}]",
            )
        )
