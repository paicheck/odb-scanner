"""Diagnostic connection: session handling, UDS reads, tx logging.

Every request transmitted through this class is passed to
`tx_logger(direction, ecu, payload, purpose)` so the database holds a manifest
of what was sent to the vehicle and why (safety requirement #16). The adapter's
AT configuration commands are not requests to the vehicle and are not logged.
No method here can produce a write request: _transmit validates, logs and sends
one canonical string, limited to the UDS allow-list and the standard OBD-II
read modes.
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

    def open(self, *, warm_up: bool = True) -> None:
        """Open and initialise the adapter. warm_up=False sends the vehicle
        nothing (see Elm327Transport.initialize)."""
        self.t.open()
        if warm_up:
            identity = self.t.initialize()
            note = f"Adapter initialized: {identity}"
        else:
            identity = self.t.initialize(warm_up=False)
            note = f"Adapter initialized, no bus warm-up: {identity}"
        self.tx_logger(direction="NOTE", ecu=None, payload="", purpose=note)

    def close(self) -> None:
        try:
            self.t.close()
        except Exception:
            log.exception("Error closing adapter")

    # -- core transmit -------------------------------------------------------
    def _transmit(self, hexcmd: str, ecu: ECUSpec | None, purpose: str,
                  *, validator=uds.validate_request,
                  require_module: bool = False,
                  approval: str | None = None) -> list[str]:
        # The single choke point for every request that reaches the vehicle.
        # validate_request raises ReadOnlyViolationError for anything off the
        # read-only allow-list, so no caller -- including the ones that build
        # hex by hand -- can put a write service on the wire. The only caller
        # that passes a different validator is approved_extended_session(),
        # which can accept exactly 0x10 0x03 and nothing else.
        #
        # The canonical form is computed first and is the ONLY string used from
        # here on: validated, logged and transmitted. Validating one string and
        # sending another let "22F190\r2EF19000" pass as a read while the
        # adapter received a read and then a write.
        hexcmd = uds.canonical_request(hexcmd)
        validator(hexcmd)
        # Addressing: functional by default (see Elm327Transport.set_header --
        # field clones refuse 29-bit headers outright). When the transport
        # negotiated VAG MEB addressing AND the ECU carries a module address,
        # the request is sent physically to that module instead: ATCP/ATSH plus
        # the module's response filter. Every MEB module -- the 11-bit gateway
        # and the 29-bit BMS/DC-DC alike -- is read with auto-formatting ON, so
        # the request goes out bare and the adapter adds the ISO-TP PCI byte,
        # header and flow control itself. That is Car Scanner's wire form
        # (2026-10-15 passive capture); the hand-built ATCAF0 8-byte line this
        # used for 29-bit modules is refused by the clone with '?'. Functional
        # OBD-II reads switch back and stay on bare '0100'-style requests.
        set_module = getattr(self.t, "set_module", None)
        target = getattr(ecu, "tx29", None) if ecu is not None else None
        meb = bool(getattr(self.t, "meb_addressing", False)) and target is not None
        wire = hexcmd
        if require_module:
            # A request that must reach ONE module (the approved 0x10 0x03)
            # may not fall back to functional addressing: on the broadcast
            # header it asks every ECU on the bus to change session. Refuse
            # before anything is logged or sent.
            if not (callable(set_module) and meb
                    and getattr(ecu, "rx29", None) is not None
                    and set_module(target, ecu.rx29)):
                raise uds.ReadOnlyViolationError(
                    f"Refusing {hexcmd}: it may only be sent physically to "
                    f"{ecu.key if ecu else 'a module'}, and physical "
                    "addressing to it could not be confirmed (no MEB "
                    "addressing, no module address, or the adapter refused "
                    "the setup). It is never sent functionally.")
        elif callable(set_module) and getattr(self.t, "meb_addressing", False):
            if not (meb and set_module(target, getattr(ecu, "rx29", None))):
                set_module(None)
        # Record what goes on the wire, not merely what was asked for. tx_log is
        # described in README.md as the safety manifest -- the record of every
        # request this tool ever put on the vehicle's diagnostic port -- so it
        # is written from the same `wire` that is transmitted. Every MEB read
        # now goes out bare (the adapter does the framing), so `wire` and
        # `hexcmd` are the same bytes; keeping the single variable means they
        # can never drift apart.
        self.tx_logger(direction="TX", ecu=ecu.key if ecu else None,
                       payload=wire, purpose=purpose)
        # The transport checks the line again itself; the one approved
        # request needs its token there too, or that check refuses it.
        lines = (self.t.send_command(wire) if approval is None
                 else self.t.send_command(wire, approval=approval))
        payload = uds.parse_elm_lines(lines)
        self.tx_logger(direction="RX", ecu=ecu.key if ecu else None,
                       payload=payload.hex().upper() if payload else
                       " | ".join(lines[:4]),
                       purpose="response")
        return lines

    def request_lines(self, hexcmd: str, ecu: ECUSpec | None,
                      purpose: str) -> list[str]:
        """Send one validated request and return the adapter's raw lines.

        For diagnostic tools that need the raw response (headers included)
        rather than a parsed payload. It is _transmit itself, so the request
        is validated, addressed and written to tx_log like every other one --
        a tool calling transport.send_command() directly gets none of that.
        """
        return self._transmit(hexcmd, ecu, purpose)

    # -- explicitly approved ABS session ------------------------------------
    def approved_extended_session(self, ecu: ECUSpec, *, approval: str,
                                  purpose: str) -> bytes:
        """Send the one approved 0x10 0x03 and require a positive 0x50 0x03.

        Only the dedicated ABS wheel-speed reader calls this. The normal
        `validate_request` path still refuses 0x10 0x03; the narrow validator
        accepted here refuses every other request. The caller must supply the
        same token the CLI required, and the TX/RX pair is written to tx_log.

        Physical only. The request is refused unless the transport confirms
        it is addressed to `ecu` alone (require_module), so it can never go
        out on the functional header and switch every ECU's session. And only
        `ecu`'s own response id counts as an answer: any other responder means
        the request was not physical after all, which is an error rather than
        a success to report under the ABS module's name.
        """
        lines = self._transmit(
            "1003", ecu,
            f"{purpose} (APPROVED extended session; token supplied)",
            validator=lambda command: uds.validate_approved_extended_session(
                command, approval
            ),
            require_module=True,
            approval=approval,
        )
        expected = getattr(ecu, "rx29", None)
        for src, payload in uds.payloads_by_source(lines).items():
            if not payload:
                continue
            if src != expected:
                raise ProtocolError(
                    f"0x10 0x03 answered by {uds.source_label(src)}, not by "
                    f"{ecu.key} ({uds.source_label(expected)}): the request "
                    "did not stay physical. Treating it as a failure.")
            if payload[0] == 0x50:
                if len(payload) < 2 or payload[1] != 0x03:
                    raise ProtocolError(
                        f"Unexpected session-control positive response: "
                        f"{payload.hex()}"
                    )
                return payload
            if payload[0] == 0x7F:
                nrc = payload[2] if len(payload) > 2 else 0
                raise NegativeResponseError(0x10, nrc)
        raise CommunicationError("No response to 0x10 0x03 session change")

    def default_session(self, ecu: ECUSpec,
                        purpose: str = "return to default session") -> bytes:
        """Return to the normal session with the already-allowed 0x10 0x01."""
        lines = self._transmit("1001", ecu, purpose)
        for payload in uds.payloads_by_source(lines).values():
            if not payload:
                continue
            if payload[0] == 0x50:
                if len(payload) < 2 or payload[1] != 0x01:
                    raise ProtocolError(
                        f"Unexpected default-session response: {payload.hex()}"
                    )
                return payload
            if payload[0] == 0x7F:
                nrc = payload[2] if len(payload) > 2 else 0
                raise NegativeResponseError(0x10, nrc)
        raise CommunicationError("No response to 0x10 0x01 default session")

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

    def functional_probe_with_reason(self, hexcmd: str, purpose: str,
                                     ecu: ECUSpec | None = None) \
            -> tuple[dict[int | None, bytes | None], bool]:
        """Like functional_probe but also returns whether the adapter refused.

        Returns (payloads_by_header, refused) where refused is True if the
        adapter explicitly refused the request (returned '?').
        """
        lines = self._transmit(hexcmd, ecu, purpose)
        return uds.payloads_by_source(lines), uds.adapter_refused(lines)

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
        strangers: list[bytes] = []
        for payload in uds.payloads_by_source(lines).values():
            if not payload:
                continue
            if payload[0] == 0x62:
                if len(payload) >= 3 and ((payload[1] << 8) | payload[2]) == did:
                    return payload[3:]
                mismatches.append(payload)
            elif payload[0] == 0x7F:
                negatives.append(payload)
            else:
                # An ECU answered, but not with a 0x22 positive (0x62) or
                # negative (0x7F) response. That is a different fact from
                # silence, and conflating the two is what makes a mis-addressed
                # read undiagnosable: "NO DATA" looks identical for a wrong
                # address, an asleep ECU and an unsupported DID, whereas a
                # well-formed reply carrying some other service marker tells us
                # the request DID reach a responder. ProtocolError subclasses
                # CommunicationError, so callers that already absorb
                # CommunicationError (try_read_did) are unaffected.
                strangers.append(payload)
        if mismatches:
            raise ProtocolError(
                f"DID echo mismatch for 0x{did:04X}: {mismatches[0].hex()}")
        if negatives:
            nrc = negatives[0][2] if len(negatives[0]) > 2 else 0
            raise NegativeResponseError(0x22, nrc)
        if strangers:
            raise ProtocolError(
                f"Unexpected service marker 0x{strangers[0][0]:02X} for DID "
                f"0x{did:04X}: {strangers[0].hex()}")
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
