"""Gateway (J533 / "[19.Gate]") read-only measurements.

The ID.3 OBD-II port is served by the central gateway ECU (J533), which
bridges the diagnostic lines to the vehicle's internal buses. Consequences:

  * Standard OBD-II modes (01/03/09) are answered by the gateway on behalf
    of the powertrain ECUs (functional requests).
  * UDS requests are also sent functionally (no ATSH -- see
    diagnostic/elm327.py set_header): whichever ECUs answer are identified
    by their response headers (18DAF1xx on this car's 29-bit bus). Which
    ECU answers which DID is determined experimentally
    (`python main.py discover`).
  * The gateway's own diagnostic address was NOT verified on this vehicle;
    see `diagnostic/ecus.py` and the note below.

REQUEST PATH — the layers, which are easy to confuse
----------------------------------------------------
For one gateway DID the full round trip is:

  1. ECU addressing (which ECU is addressed)
     `ATSH710` — the 11-bit TX CAN id (confirmed by the 2026-10-15 capture).
  2. Response filtering
     `ATCRA77A` — the 11-bit response CAN id the adapter must accept, also
     confirmed by the capture (every response line arrives tagged `77A`).
     The reference profile's ATCRA argument 0x77A is the response id, not a
     "+0x6A encoding".
  3. ISO-TP handling
     Not ours. The ELM327 adds the PCI byte and flow control for requests
     it sends itself; `uds.parse_elm_lines()` only reassembles what comes
     back. 0x2AB2 is single-frame, but 0x2AB8 is MULTI-FRAME (11 bytes:
     First Frame 10 0B + one Consecutive Frame 21) -- confirmed by the
     2026-10-15 capture -- so the flow-control setup (ATFCSH710 /
     ATFCSD300000 / ATFCSM1) is required for the adapter to solicit the
     consecutive frame.
  4. Response header + positive-response marker
     `uds.parse_elm_lines()` reads the id off the line ("77A" in the
     confirmed capture), then `uds.expect_positive()` requires 0x62
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
not a Volkswagen specification, but its addressing and offsets have now been
confirmed on this car by a passive HCI capture (2026-10-15).

| key               | DID    | reference name                    | off | len | scale |
|-------------------|--------|-----------------------------------|-----|-----|-------|
| hv_energy_content | 0x2AB8 | [19.Gate] HV Battery energy cont. |  2  |  2  | 25 Wh |
| hv_energy_max     | 0x2AB2 | [19.Gate] Max energy content (tr.)|  0  |  2  | 50 Wh |

(`hv_energy_max` is "Maximum energy content of the traction battery" in
full; shortened here only to keep this table inside the line limit.)

Both are unsigned, big-endian (`ReversedByteSet` is false in the profile),
wh (`UN` = 61). 0x2AB8 is the CURRENT usable energy content and 0x2AB2 the
MAXIMUM; they are two measurements of one quantity, not two encodings.

RESOLVED BY PASSIVE HCI CAPTURE (2026-10-15)
-------------------------------------------
  * The 0x2AB8 `SBI=2` offset is now seen on a captured frame. The response
    is multi-frame: `62 2A B8 00 00 05 7F 00 00 00 00` (11 bytes), and
    `u16be[2:4]` = `05 7F` = 1407 x 25 = 35,175 Wh, matching Car Scanner's
    displayed 35,175 Wh. The offset is confirmed, not guessed.
  * The response header is 0x77A, not an "unidentified encoding". Car
    Scanner sends `ATSH710` (request), `ATFCSH710` (flow control),
    `ATCRA77A` (receive filter) and every response line arrives tagged
    `77A`. The +0x6A relation IS the response id. The request id 0x710 is
    now observed directly, resolving the 0x792 / 0x772 alternatives.
  * Multi-frame confirmed: 0x2AB8 needs First Frame + Consecutive Frame
    reassembly and the flow-control preamble (ATFCSH710 / ATFCSD300000 /
    ATFCSM1); 0x2AB2 is single-frame.

  See docs/investigation/meb_gateway_energy_passive_capture_20261015.md.

HISTORICAL ARITHMETIC (superseded as evidence, kept for the record)
-------------------------------------------------------------------
  * 0x2AB2 x 50 Wh with raw = 1160 -> 58,000 Wh gross, matching an ID.3
    58 kWh pack; a 0x2AB8 reading sits below that while driving. This was
    plausibility only; the capture above is the actual evidence.
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
          "not a VW specification; addressing and offsets CONFIRMED on this "
          "vehicle by passive HCI capture 2026-10-15 "
          "(docs/investigation/meb_gateway_energy_passive_capture_20261015.md)")

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
