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
        # Every request goes out FUNCTIONALLY: no ATSH/ATCRA is ever sent.
        # The field clones refuse 29-bit headers, silently mis-apply 11-bit
        # ones under a 29-bit protocol (a poison id nothing answers), and
        # refuse the plain ATSH that would clear a bad header -- see
        # Elm327Transport.set_header. With ATH1 every response frame carries
        # its sender's CAN id, so per-ECU attribution happens at parse time
        # (uds.payloads_by_source) instead of at addressing time. The `ecu`
        # argument identifies the intended ECU for the tx log only.
        self.tx_logger(direction="TX", ecu=ecu.key if ecu else None,
                       payload=hexcmd, purpose=purpose)
        lines = self.t.send_command(hexcmd)
        payload = uds.parse_elm_lines(lines)
        self.tx_logger(direction="RX", ecu=ecu.key if ecu else None,
                       payload=payload.hex().upper() if payload else
                       " | ".join(lines[:4]),
                       purpose="response")
        return lines

    def functional_probe(self, hexcmd: str,
                         purpose: str) -> dict[int | None, bytes | None]:
        """Send one functional request and reassemble answers per sender.

        Returns reassembled payloads keyed by each responding ECU's CAN
        header (uds.source_label renders them; None values mean a sender's
        frames never formed a complete message).
        """
        return uds.payloads_by_source(self._transmit(hexcmd, None, purpose))

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
