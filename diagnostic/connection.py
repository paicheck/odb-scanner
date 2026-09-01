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
from .elm327 import Elm327Transport
from .interface import CommunicationError, OBDInterface
from .uds import NegativeResponseError, ProtocolError

log = logging.getLogger(__name__)


class DiagnosticConnection:
    def __init__(self, transport: OBDInterface, tx_logger=None):
        self.t = transport
        self.tx_logger = tx_logger or (lambda **kw: None)
        self._current_ecu: tuple[int, int | None] | None = None

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

    # -- addressing ----------------------------------------------------------
    def _set_ecu(self, tx: int, rx: int | None) -> None:
        if self._current_ecu != (tx, rx):
            self.t.set_header(tx)
            self.t.set_receive_address(rx)
            self._current_ecu = (tx, rx)

    # -- core transmit -------------------------------------------------------
    def _transmit(self, hexcmd: str, ecu: ECUSpec | None, purpose: str) -> list[str]:
        if ecu is not None:
            self._set_ecu(ecu.tx, ecu.rx)
        self.tx_logger(direction="TX", ecu=ecu.key if ecu else None,
                       payload=hexcmd, purpose=purpose)
        lines = self.t.send_command(hexcmd)
        payload = uds.parse_elm_lines(lines)
        self.tx_logger(direction="RX", ecu=ecu.key if ecu else None,
                       payload=payload.hex().upper() if payload else
                       " | ".join(lines[:4]),
                       purpose="response")
        return lines

    # -- UDS read ------------------------------------------------------------
    def read_did(self, ecu: ECUSpec, did: int) -> bytes:
        """UDS 0x22 ReadDataByIdentifier. Returns data bytes after the DID.

        Raises NegativeResponseError on NRC (e.g. 0x31 out of range) and
        ProtocolError on malformed responses.
        """
        payload = uds.parse_elm_lines(
            self._transmit(
                f"22{did:04X}", ecu,
                f"UDS 0x22 ReadDataByIdentifier DID 0x{did:04X} from "
                f"{ecu.name} (read-only diagnostic value)",
            )
        )
        if payload is None:
            raise CommunicationError(f"No/undecodable response to DID 0x{did:04X}")
        positive = uds.expect_positive(payload, 0x22)
        if len(positive) < 3 or ((positive[1] << 8) | positive[2]) != did:
            raise ProtocolError(f"DID echo mismatch for 0x{did:04X}: {positive.hex()}")
        return positive[3:]

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

        Read-only: 0x19 with sub-functions 0x01/0x02/0x04 are pure reads.
        Clearing (0x14) is blocked at the UDS layer.
        """
        payload = uds.parse_elm_lines(
            self._transmit(
                "190208", ecu,
                f"UDS 0x19 0x02 ReadDTCInformation (status mask 0x08, confirmed "
                f"DTCs) from {ecu.name} - read-only",
            )
        )
        if payload is None:
            raise CommunicationError(f"No response to DTC read from {ecu.key}")
        positive = uds.expect_positive(payload, 0x19)
        return obd2.parse_uds_dtc_response(positive)
