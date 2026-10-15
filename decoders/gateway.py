"""Gateway (J533 / "[19.Gate]") read-only measurements.

The ID.3 OBD-II port is served by the central gateway ECU (J533), which
bridges the diagnostic lines to the vehicle's internal buses. Consequences:

  * Standard OBD-II modes (01/03/09) are answered by the gateway on behalf
    of the powertrain ECUs (functional 0x7DF requests).
  * VAG UDS requests are sent with an ECU-specific header (ATSH 7xx). Some
    ECUs answer directly; others may only respond when the gateway routes
    the request. Which is true per ECU must be determined experimentally
    (`python main.py discover`).
  * The gateway's own diagnostic address was NOT verified on this vehicle;
    see `diagnostic/ecus.py` and the note below.

REQUEST PATH — the layers, which are easy to confuse
----------------------------------------------------
For one gateway DID the full round trip is:

  1. ECU addressing (which ECU is addressed)
     `ATSH792` — `Elm327Transport.set_header()`; the 11-bit TX CAN id.
  2. Response filtering
     `ATCRA710` — `Elm327Transport.set_receive_address()`; which CAN id the
     adapter is willing to report back. `DiagnosticConnection._set_ecu()`
     pairs it with ATSH whenever the target ECU changes. The reference
     profile writes `ATCRA77A` here instead; that argument is NOT copied,
     see UNRESOLVED below.
  3. ISO-TP handling
     Not ours. The ELM327 adds the PCI byte and flow control for requests
     it sends itself; `uds.parse_elm_lines()` only reassembles what comes
     back. These gateway records are expected to be single-frame.
  4. Response header + positive-response marker
     `uds.parse_elm_lines()` reads the id off the line ("710" in the
     reference profile), then `uds.expect_positive()` requires 0x62
     (0x22 + 0x40) and rejects 0x7F negative responses.
  5. DID echo
     `DiagnosticConnection.read_did()` checks the echoed DID and returns
     the DID *data record* only. This matches the reference decoder, which
     receives the same thing (response marker and DID echo stripped).
  6. Payload offsets and scaling
     This module. `PAYLOAD_OFFSET`/`PAYLOAD_LENGTH` reproduce the reference
     `StartByteId`/`DataLength` fields; `LSB_WH` reproduces `Multiplier`
     (Divider is 1.0 and Offset is 0.0 for both DIDs, so `* MUL + OFS`).

MEASUREMENT DEFINITIONS
-----------------------
Both definitions come from the Car Scanner MEB vehicle profile (embedded
resource `profiles7.db`, profile index 342, 308 measurement records). This
is a THIRD-PARTY static definition recovered by reverse engineering — it is
not a Volkswagen specification and it has not been confirmed on this car.

| key               | DID    | reference name                                  | off | len | scale |
|-------------------|--------|-------------------------------------------------|-----|-----|-------|
| hv_energy_content | 0x2AB8 | [19.Gate] HV Battery energy content             |  2  |  2  | 25 Wh |
| hv_energy_max     | 0x2AB2 | [19.Gate] Maximum energy content (traction bat.) |  0  |  2  | 50 Wh |

Both are unsigned, big-endian (`ReversedByteSet` is false in the profile),
wh (`UN` = 61). 0x2AB8 is the CURRENT usable energy content and 0x2AB2 the
MAXIMUM; they are two measurements of one quantity, not two encodings.

UNRESOLVED (do not treat as validated)
--------------------------------------
  * The 0x2AB8 `SBI=2` offset has never been seen on a captured frame. It
    means "skip the first two bytes of the 0x2AB8 data record, then read a
    big-endian u16". If the ECU returns a shorter record, the value is
    recorded as unavailable rather than guessed.
  * The response header. The reference records declare it twice, as the HDR
    field and as `ATFCSH710`, so 0x710 is what we filter on. Their ATCRA
    argument is 0x77A, which is not 0x710 + 8 but exactly 0x710 + 0x6A - and
    every 11-bit ECU class in that profile shows the same +0x6A relation, so
    ATCRA there is an unidentified Car Scanner encoding rather than a
    response CAN id. These records also carry no ATSH, so the request id is
    not stated by them either (0x792 is the only gateway address named in
    Car Scanner code; 0x772 = 0x710 - 8 is an untested alternative).
  * Arithmetic plausibility (NOT evidence): 0x2AB2 x 50 Wh with
    raw = 1160 -> 58,000 Wh gross, matching an ID.3 58 kWh pack. A 0x2AB8
    reading is therefore expected to sit below that while driving.
    No live frame has been captured; raw bytes are always preserved.
"""
from __future__ import annotations

from .registry import (
    DIDRegistry,
    DIDSpec,
    DocStatus,
    u16be,
)

ECU = "gate"

SOURCE = ("Car Scanner MEB profile (profiles7.db idx 342), record "
          "'[19.Gate] HV Battery energy content'/'[19.Gate] Maximum energy "
          "content of the traction battery'; static third-party definition, "
          "not a VW specification, not yet validated on this vehicle")

# DID 0x2AB8 - current usable HV battery energy content.
DID_HV_ENERGY_CONTENT = 0x2AB8
PAYLOAD_OFFSET_CONTENT = 2   # SBI
PAYLOAD_LENGTH = 2           # DL
LSB_WH_CONTENT = 25.0        # MUL (DIV 1.0, OFS 0.0)

# DID 0x2AB2 - maximum HV battery energy content.
DID_HV_ENERGY_MAX = 0x2AB2
PAYLOAD_OFFSET_MAX = 0       # SBI
LSB_WH_MAX = 50.0            # MUL (DIV 1.0, OFS 0.0)


def _wh_at(raw: bytes, offset: int, lsb_wh: float, did: int) -> float:
    """Unsigned big-endian u16 at `offset` of a DID data record, in Wh.

    Mirrors the reference decoder: a record shorter than `offset + 2`
    cannot produce a value, so it raises instead of yielding a number
    read from the wrong bytes. `DIDSpec.decode_value()` turns that into
    `None`, and the collector keeps the raw bytes either way.
    """
    if len(raw) < offset + PAYLOAD_LENGTH:
        raise ValueError(
            f"DID 0x{did:04X}: response record is {len(raw)} byte(s); "
            f"{offset + PAYLOAD_LENGTH} required to read the value at "
            f"offset {offset}"
        )
    return u16be(raw, offset) * lsb_wh


def _hv_energy_content(raw: bytes) -> float:
    """DID 0x2AB8 -> Wh.  value = (p[2] << 8 | p[3]) * 25."""
    return _wh_at(raw, PAYLOAD_OFFSET_CONTENT, LSB_WH_CONTENT,
                  DID_HV_ENERGY_CONTENT)


def _hv_energy_max(raw: bytes) -> float:
    """DID 0x2AB2 -> Wh.  value = (p[0] << 8 | p[1]) * 50."""
    return _wh_at(raw, PAYLOAD_OFFSET_MAX, LSB_WH_MAX, DID_HV_ENERGY_MAX)


def register_gateway(reg: DIDRegistry) -> None:
    reg.register(DIDSpec(
        "hv_energy_content", ECU, DID_HV_ENERGY_CONTENT,
        "HV battery energy content (gateway)", "Wh",
        decode=_hv_energy_content,
        doc_status=DocStatus.EXPERIMENTAL,
        notes=f"UDS 0x22 DID 0x2AB8, u16be at payload offset "
              f"{PAYLOAD_OFFSET_CONTENT} x {LSB_WH_CONTENT:g} Wh, unsigned. "
              f"[{SOURCE}]",
    ))
    reg.register(DIDSpec(
        "hv_energy_max", ECU, DID_HV_ENERGY_MAX,
        "HV battery maximum energy content (gateway)", "Wh",
        decode=_hv_energy_max,
        doc_status=DocStatus.EXPERIMENTAL,
        notes=f"UDS 0x22 DID 0x2AB2, u16be at payload offset "
              f"{PAYLOAD_OFFSET_MAX} x {LSB_WH_MAX:g} Wh, unsigned. "
              f"[{SOURCE}]",
        slow=True,
    ))
