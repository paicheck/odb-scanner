"""Diagnostic connection: session handling, UDS reads, tx logging.

Every transmitted request is passed to `tx_logger(direction, ecu, payload,
purpose)` so the database holds a complete manifest of what was sent to the
vehicle and why (safety requirement #16). No method here can produce a
write request — the payload space is limited to the UDS allow-list and
standard OBD-II read modes.
"""
from __future__ import annotations

import logging

from . import obd2, uds
from .ecus import ECUSpec
from .interface import CommunicationError, OBDInterface
from .uds import NegativeResponseError, ProtocolError

log = logging.getLogger(__name__)


class DiagnosticConnection:
    def __init__(self, transport: OBDInterface, tx_logger=None):
        self.t = transport
        self.tx_logger = tx_logger or (lambda **kw: None)

    def open(self) -> None:
        self.t.open()
        identity = self.t.initialize()
        self.tx_logger(direction="NOTE", ecu=None, payload="",
                       purpose=f"Adapter initialized: {identity}")

    def close(self) -> None:
        try:
            self.t.close()
        except Exception:
            log.exception("Error closing adapter")

    # -- core transmit -------------------------------------------------------
    def _transmit(self, hexcmd: str, ecu: ECUSpec | None, purpose: str) -> list[str]:
        # The single choke point for every request that reaches the vehicle.
        # validate_request raises ReadOnlyViolationError for anything off the
        # read-only allow-list, so no caller -- including the ones that build
        # hex by hand -- can put a write service on the wire.
        uds.validate_request(hexcmd)
        # Addressing: functional by default (see Elm327Transport.set_header --
        # field clones refuse 29-bit headers outright). When the transport
        # negotiated VAG MEB addressing AND the ECU carries a module address,
        # the request is sent physically to that module instead: ATCP/ATSH
        # switch plus an ATCAF0 ISO-TP frame built by hand. Functional OBD-II
        # reads switch back and stay on bare '0100'-style requests.
        set_module = getattr(self.t, "set_module", None)
        target = getattr(ecu, "tx29", None) if ecu is not None else None
        meb = bool(getattr(self.t, "meb_addressing", False)) and target is not None
        wire = hexcmd
        if callable(set_module) and getattr(self.t, "meb_addressing", False):
            if meb and set_module(target):
                body = hexcmd
                # Single-frame ISO-TP: one length byte then up to 7 data
                # bytes. The slicing below would silently drop anything past
                # that, so refuse instead. validate_request caps the request
                # at the same 7 bytes, so this is unreachable via _transmit --
                # it is here because a truncated frame produces an ECU error
                # rather than anything recognisable, and the cost of checking
                # is one comparison.
                if len(body) // 2 > 7:
                    raise CommunicationError(
                        f"Refusing to send a {len(body) // 2}-byte MEB request: "
                        f"the single-frame ISO-TP path carries at most 7 data "
                        f"bytes. Multi-frame is not implemented."
                    )
                wire = (f"{len(body) // 2:02X}{body}" + "55" * 8)[:16]
            else:
                set_module(None)
        # Record what goes on the wire, not merely what was asked for. They are
        # the same for a functional read, but an MEB-addressed request is
        # reframed into a padded ISO-TP single frame first. tx_log is
        # described in README.md as the safety manifest -- the record of every
        # request this tool ever put on the vehicle's diagnostic port -- so for
        # every MEB read it was understating what was actually sent.
        #
        # hexcmd is not lost: it is a literal substring of the frame, and the
        # frame is the validated request plus a length byte and 0x55 padding.
        self.tx_logger(direction="TX", ecu=ecu.key if ecu else None,
                       payload=wire, purpose=purpose)
        lines = self.t.send_command(wire)
        payload = uds.parse_elm_lines(lines)
        self.tx_logger(direction="RX", ecu=ecu.key if ecu else None,
                       payload=payload.hex().upper() if payload else
                       " | ".join(lines[:4]),
                       purpose="response")
        return lines

    def functional_probe(self, hexcmd: str, purpose: str,
                         ecu: ECUSpec | None = None) -> dict[int | None, bytes | None]:
        """Send one request and reassemble answers per sender.

        With `ecu` given, _transmit routes it to that module when MEB
        addressing is available; otherwise it goes out functionally. Returns
        reassembled payloads keyed by each responding ECU's CAN header
        (uds.source_label renders them; None values mean a sender's frames
        never formed a complete message).
        """
        return uds.payloads_by_source(self._transmit(hexcmd, ecu, purpose))

    # -- UDS read ------------------------------------------------------------
    def read_did(self, ecu: ECUSpec, did: int) -> bytes:
        """UDS 0x22 ReadDataByIdentifier, sent functionally.

        Returns the data bytes after the DID from the first ECU that
        answers positively with a matching DID echo. Raises
        NegativeResponseError when only NRCs came back, ProtocolError on
        malformed responses, CommunicationError when nothing decodable
        arrived at all.
        """
        lines = self._transmit(
            f"22{did:04X}", ecu,
            f"UDS 0x22 ReadDataByIdentifier DID 0x{did:04X} from "
            f"{ecu.name} (read-only diagnostic value)",
        )
        negatives: list[bytes] = []
        mismatches: list[bytes] = []
        for payload in uds.payloads_by_source(lines).values():
            if not payload:
                continue
            if payload[0] == 0x62:
                if len(payload) >= 3 and ((payload[1] << 8) | payload[2]) == did:
                    return payload[3:]
                mismatches.append(payload)
            elif payload[0] == 0x7F:
                negatives.append(payload)
        if mismatches:
            raise ProtocolError(
                f"DID echo mismatch for 0x{did:04X}: {mismatches[0].hex()}")
        if negatives:
            nrc = negatives[0][2] if len(negatives[0]) > 2 else 0
            raise NegativeResponseError(0x22, nrc)
        raise CommunicationError(f"No/undecodable response to DID 0x{did:04X}")

    def try_read_did(self, ecu: ECUSpec, did: int) -> bytes | None:
        """Like read_did but returns None on NRC / no response."""
        try:
            return self.read_did(ecu, did)
        except (NegativeResponseError, CommunicationError, ProtocolError) as exc:
            log.debug("DID 0x%04X on %s unavailable: %s", did, ecu.key, exc)
            return None

    # -- standard OBD-II -----------------------------------------------------
    def read_vin(self) -> str:
        lines = self._transmit("0902", None,
                               "OBD-II mode 09 02 - read VIN (standard read)")
        return obd2.parse_vin_response(lines)

    def mode01(self, pid: int) -> bytes:
        lines = self._transmit(f"01{pid:02X}", None,
                               f"OBD-II mode 01 PID 0x{pid:02X} (standard read)")
        return obd2.parse_mode01(lines, pid)

    def try_mode01(self, pid: int):
        try:
            data = self.mode01(pid)
            return obd2.decode_pid(pid, data)
        except (CommunicationError, ProtocolError):
            return None

    def read_dtcs_obd2(self) -> list[str]:
        lines = self._transmit("03", None,
                               "OBD-II mode 03 - read emissions DTCs (standard read)")
        return obd2.parse_mode03(lines)

    def read_dtcs_uds(self, ecu: ECUSpec) -> list[dict]:
        """UDS 0x19 0x02 reportDTCByStatusMask (mask 0x08 = confirmed DTCs).

        Sent functionally; the first positive response wins. For per-ECU
        attribution of DTCs use functional_probe("190208", ...) directly.

        Read-only: 0x19 with sub-functions 0x01/0x02/0x04 are pure reads.
        Clearing (0x14) is blocked at the UDS layer.
        """
        lines = self._transmit(
            "190208", ecu,
            f"UDS 0x19 0x02 ReadDTCInformation (status mask 0x08, confirmed "
            f"DTCs) from {ecu.name} - read-only",
        )
        negatives: list[bytes] = []
        for payload in uds.payloads_by_source(lines).values():
            if not payload:
                continue
            if payload[0] == 0x59:
                return obd2.parse_uds_dtc_response(payload)
            if payload[0] == 0x7F:
                negatives.append(payload)
        if negatives:
            nrc = negatives[0][2] if len(negatives[0]) > 2 else 0
            raise NegativeResponseError(0x19, nrc)
        raise CommunicationError(f"No response to DTC read from {ecu.key}")

    def read_dtc_snapshots(self, code: str) -> dict[int | None, list[dict]]:
        """UDS 0x19 0x04 reportDTCSnapshotRecordNumber for one DTC code.

        Sent functionally with record number 0xFF (all records), so every
        ECU that has this DTC stored answers with ITS OWN snapshot -- the
        conditions the ECU recorded at fault time. Returns reassembled
        snapshot entries keyed by responding ECU header (empty dict when
        nobody answered or everybody refused; malformed codes raise
        CommunicationError rather than guessing wire bytes).

        Read-only: 0x19 0x04 is a pure read of stored data.
        """
        try:
            dtc = obd2.encode_vag_dtc(code)
        except ValueError as exc:
            raise CommunicationError(str(exc)) from exc
        lines = self._transmit(
            f"1904{dtc.hex().upper()}FF", None,
            f"UDS 0x19 0x04 reportDTCSnapshotRecordNumber for {code}, all "
            f"records - read-only (ECU's stored conditions at fault time)",
        )
        out: dict[int | None, list[dict]] = {}
        for src, payload in uds.payloads_by_source(lines).items():
            if (payload and len(payload) >= 2
                    and payload[0] == 0x59 and payload[1] == 0x04):
                records = obd2.parse_uds_dtc_snapshot_response(payload)
                if records:
                    out[src] = records
        return out
