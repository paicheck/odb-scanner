"""ELM327-compatible transport over serial (USB ELM327/OBDLink) or TCP.

TCP mode is used by the built-in vehicle simulator and by TCP-serial bridges.
Initialization uses conservative, documented ELM327 AT commands:

    ATZ    reset            ATE0   echo off
    ATL0   linefeeds off    ATS0   spaces off
    ATH1   headers ON (every response frame carries its sender's CAN id)
    ATAT1  adaptive timing  ATSP7  ISO 15765-4 CAN 500 kbit/s 29-bit
    ATCAF1 CAN auto-formatting on

Addressing is functional by default: requests go out on the protocol's
default functional id and each ECU's answer is attributed by its response
header (18DAF1xx) or, on the 11-bit bus, its rx id. When the adapter accepts
it, VAG MEB module addressing (ATCP + 6-digit ATSH) is negotiated at init so
reads can also be aimed at one ECU physically -- see set_module. No receive
filter (ATCRA) is ever set to a narrow value; see set_receive_address.

Note: multi-frame (ISO-TP) responses are reassembled by the ELM327 for
*requests it sends itself* on good adapters; cheap clones emit the raw
first/consecutive frames instead, so diagnostic/uds.py reassembles per
sender either way.
"""
from __future__ import annotations

import logging
import socket
import time

from .interface import (
    AdapterNotFoundError,
    CommunicationError,
    OBDInterface,
    detect_serial_ports,
)

log = logging.getLogger(__name__)

_ADAPTER_HINTS = ("ELM327", "OBDLINK", "STN", "OBDII", "OBD-II")

# Protocol the transport pins during initialize(). "7" = ISO 15765-4 CAN
# 29-bit 500 kbit/s -- the ID.3/MEB diagnostic bus answers on 29-bit ids
# (18DAF1xx / 18DB33F1) and is silent under ATSP6 (11-bit). Verified with
# tools/doctor.py stage 5; change it there first if a vehicle disagrees.
PINNED_PROTOCOL = "7"


class Elm327Transport(OBDInterface):
    name = "elm327"

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 38400,
        timeout: float = 5.0,
        host: str | None = None,
        tcp_port: int | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.host = host
        self.tcp_port = tcp_port
        self._dev = None
        self._opened = False
        self.identity = "unknown"
        self._addressing_warned = False
        # VAG MEB physical addressing state (see _negotiate_meb / set_module)
        self.meb_addressing = False
        self._meb_module: int | None = None   # None = functional addressing
        self._caf = 1                          # CAN auto-formatting on/off
        self._proto = PINNED_PROTOCOL          # active ATSP protocol

    # -- lifecycle ----------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._opened

    def open(self) -> None:
        if self._opened:
            return
        if self.host:
            self._open_tcp()
        else:
            self._open_serial()
        self._opened = True

    def _open_tcp(self) -> None:
        assert self.host and self.tcp_port
        try:
            sock = socket.create_connection(
                (self.host, self.tcp_port), timeout=self.timeout
            )
        except OSError as exc:
            raise AdapterNotFoundError(
                f"Cannot connect to {self.host}:{self.tcp_port}: {exc}"
            ) from exc
        sock.settimeout(0.5)
        self._dev = sock
        log.info("Connected to TCP adapter %s:%s", self.host, self.tcp_port)

    def _open_serial(self) -> None:
        import serial  # local import so TCP-only setups do not need pyserial

        port = self.port
        if not port or port == "auto":
            port = self._autodetect_serial()
        try:
            self._dev = serial.Serial(port, self.baudrate, timeout=0.5)
        except (serial.SerialException, OSError) as exc:
            raise AdapterNotFoundError(f"Cannot open {port}: {exc}") from exc
        log.info("Opened serial adapter %s @ %d baud", port, self.baudrate)

    def _autodetect_serial(self) -> str:  # pragma: no cover - needs hardware
        try:
            import serial
        except ImportError as exc:
            raise AdapterNotFoundError("pyserial not installed") from exc
        for info in detect_serial_ports():
            try:
                dev = serial.Serial(info["port"], self.baudrate, timeout=0.5)
            except (serial.SerialException, OSError):
                continue
            try:
                self._dev = dev
                resp = self.send_command("ATI")
                ident = " ".join(resp).upper()
                if any(h in ident for h in _ADAPTER_HINTS):
                    return info["port"]
            except CommunicationError:
                pass
            finally:
                try:
                    if self._dev is dev:
                        self._dev = None
                    dev.close()
                except Exception:
                    pass
        raise AdapterNotFoundError(
            "No ELM327-compatible adapter found. Set adapter.port in config.yaml "
            "to the COM port of your device (see Windows Device Manager)."
        )

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # pragma: no cover
                pass
        self._dev = None
        self._opened = False

    # -- raw line I/O -------------------------------------------------------
    def send_command(self, command: str) -> list[str]:
        if self._dev is None:
            raise CommunicationError("Adapter not open")
        self._write(command + "\r")
        raw = self._read_until_prompt(self.timeout)
        lines = raw.replace(">", "\n").splitlines()
        return [ln.strip() for ln in lines if ln.strip()]

    def _write(self, text: str) -> None:
        try:
            if hasattr(self._dev, "write"):  # serial
                self._dev.write(text.encode("ascii"))
            else:  # socket
                self._dev.sendall(text.encode("ascii"))
        except (OSError, AttributeError) as exc:
            raise CommunicationError(f"Write failed: {exc}") from exc

    def _read_until_prompt(self, timeout: float) -> str:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if hasattr(self._dev, "read"):  # serial
                    chunk = self._dev.read(256)
                else:  # socket
                    chunk = self._dev.recv(4096)
            except (TimeoutError, OSError):
                chunk = b""
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
        text = buf.decode("ascii", errors="replace")
        if ">" not in text:
            raise CommunicationError(
                f"Timeout waiting for prompt after: {buf[:80]!r}"
            )
        return text

    # -- ELM327 protocol ----------------------------------------------------
    def _base_init(self) -> None:
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1",
                    f"ATSP{PINNED_PROTOCOL}", "ATCAF1"):
            self.send_command(cmd)
        self._caf = 1
        self._proto = PINNED_PROTOCOL
        self._meb_module = None

    def initialize(self) -> str:
        """Bring the adapter into a known state. Returns adapter identity."""
        time.sleep(0.3)
        self.send_command("ATZ")
        time.sleep(0.3)
        resp = self.send_command("ATI")
        self.identity = " ".join(resp) if resp else "unknown"
        self._base_init()
        self._negotiate_meb()
        # OBD-II warm-up: '0100' (supported-PID request) makes the ELM finish
        # CAN bus init and shows whether anything is awake. On a silent bus
        # (vehicle asleep, gateway off) it returns NO DATA — logged, not
        # fatal: the caller surfaces a clear "vehicle did not answer" error.
        # This is a vehicle request sent straight through the transport, so it
        # bypasses DiagnosticConnection._transmit and must be validated here --
        # otherwise it would be a hole in the read-only guarantee.
        from . import uds
        uds.validate_request("0100")
        warm = " ".join(self.send_command("0100")).upper()
        if "NO DATA" in warm or "UNABLE" in warm or not warm:
            log.warning(
                "CAN bus silent after 0100 warm-up (%r) — vehicle likely "
                "asleep (ignition off). Requests will return NO DATA until "
                "the gateway wakes up.", warm or "no output")
        else:
            log.info("CAN bus alive after warm-up: %s", warm)
        log.info("Adapter initialized: %s", self.identity)
        return self.identity

    def set_header(self, tx_id: int | None) -> None:
        """No-op: this transport stays on functional addressing.

        Empirically established on the v1.5 clone in the field, running the
        29-bit protocol (ATSP7):

        1. ATSH with a 3-digit (11-bit) value is *accepted* but applied as
           a 29-bit id (ATSH7E5 -> 0x000007E5), which nothing on the bus
           listens to -- every later request dies with NO DATA.
        2. The 8-digit ATSH a 29-bit id requires is refused ('?'), and so
           is the plain ATSH that would clear a header -- once a bad
           header is set, only ATZ recovers.

        Functional requests (default header, 18DB33F1 under ATSP7) reach
        every ECU, and with ATH1 each response carries its sender's id
        (18DAF1xx), so higher layers attribute responses per ECU at parse
        time (uds.payloads_by_source) instead of addressing them here.
        """
        if tx_id is not None and not self._addressing_warned:
            self._addressing_warned = True
            log.info("ATSH suppressed: functional addressing only; responses "
                     "are attributed by their response header instead")

    def set_receive_address(self, rx_id: int | None) -> None:
        """No-op: never program a CAN receive filter.

        The clone refuses the plain ATCRA that would clear a filter, so a
        once-set filter could deafen the adapter to every other response
        until ATZ. Receive everything and select in software
        (uds.payloads_by_source) instead.
        """

    # -- VAG MEB physical addressing ---------------------------------------
    def _negotiate_meb(self) -> None:
        """Probe whether the adapter accepts VAG MEB module addressing.

        MEB modules (BMS 0x17FC007B, DC/DC 0x17FC00B9, ...) answer at 29-bit
        ids built from a priority byte plus a 6-digit address. ABRP's MEB
        profile (ev-obd-pids) drives ELM327-class adapters with:

            ATCP 17          # priority byte
            ATSH FC007B      # completed to 0x17FC007B

        Both are needed because the field clone refuses the 8-digit ATSH a
        29-bit id would otherwise require. On success the functional header
        is restored (CP 18 + DB33F1) so OBD-II modes keep working; if that
        restore is refused the adapter is reset with ATZ. Failure of either
        probe just leaves the transport in functional-only mode.

        Only the 29-bit half of MEB addressing is probed here. The 11-bit
        modules (energy 0x710, climate 0x746) need protocol 6 and are reached
        by set_module, which negotiates per module.
        """
        if any("?" in r for r in self.send_command("ATCP 17")):
            log.info("Adapter refused 'ATCP 17' - MEB physical addressing "
                     "unavailable, staying functional-only")
            self.send_command("ATCP 18")
            return
        if any("?" in r for r in self.send_command("ATSH FC007B")):
            log.info("Adapter refused 6-digit 'ATSH FC007B' - staying "
                     "functional-only")
            self.send_command("ATCP 18")
            return
        self.meb_addressing = True
        self._meb_module = 0x17FC007B
        self.send_command("ATCRA0")     # best-effort accept-all filter
        if not self._restore_functional():
            log.warning("Functional header restore refused - resetting adapter")
            self.send_command("ATZ")
            time.sleep(0.3)
            self._base_init()
        log.info("MEB physical addressing available (ATCP + 6-digit ATSH)")

    def _restore_functional(self) -> bool:
        """Point the adapter back at the functional OBD header (0x18DB33F1)."""
        resp = self._set_protocol(PINNED_PROTOCOL)
        resp += self.send_command("ATCP 18")
        resp += self.send_command("ATSH DB33F1")
        if any("?" in r for r in resp):
            return False
        self._meb_module = None
        return True

    def _set_protocol(self, proto: str) -> list[str]:
        """Switch ISO 15765-4 frame format (ATSP). Silent no-op when already set.

        Protocol 7 is 29-bit and protocol 6 is 11-bit, both at 500 kbit/s.
        MEB modules in the 11-bit range (energy 0x710, climate 0x746) are only
        reachable on protocol 6; going back to a 29-bit module needs 7 again.
        """
        if self._proto == proto:
            return []
        resp = self.send_command(f"ATSP{proto}")
        if not any("?" in r for r in resp):
            self._proto = proto
        return resp

    def _recover(self) -> bool:
        """Clear a latched bad header with ATZ and re-negotiate addressing.

        The clone latches a header it does not like (see set_header): only ATZ
        brings it back to a usable state. A refused module switch therefore
        costs one adapter reset, after which addressing capability is probed
        again rather than assumed.

        Always returns False: the caller must re-issue the request with
        functional addressing, because the adapter is no longer aimed at the
        module it asked for.
        """
        log.info("Adapter refused a module header -- resetting and re-negotiating")
        self.send_command("ATZ")
        time.sleep(0.3)
        self._base_init()
        self._negotiate_meb()
        return False

    def set_module(self, tx29: int | None) -> bool:
        """Switch addressing between functional (None) and one MEB module.

        Also flips CAN auto-formatting: module reads use ATCAF0 with a
        hand-built ISO-TP single frame (ABRP's proven wire form), while
        functional OBD-II stays on ATCAF1 where bare '0100' is expected.

        `tx29` may be either MEB form: a 29-bit id (0x17FC007B) or an 11-bit
        one (0x710). Both are programmed as ATCP <priority> + ATSH <6 hex
        digits>, which is what the community tooling sends; the protocol is
        chosen to match the id. Returns False when the switch was refused and
        the caller should fall back to functional addressing.
        """
        if not self.meb_addressing:
            return tx29 is None
        want_proto = "6" if tx29 is not None and tx29 <= 0x7FF else PINNED_PROTOCOL
        if tx29 == self._meb_module and self._proto == want_proto:
            return True
        if tx29 is None:
            if not self._restore_functional():
                return self._recover()
            if self._caf != 1:
                self.send_command("ATCAF1")
                self._caf = 1
            return True
        resp = self._set_protocol(want_proto)
        resp += self.send_command(f"ATCP {(tx29 >> 24) & 0xFF:02X}")
        resp += self.send_command(f"ATSH {tx29 & 0xFFFFFF:06X}")
        if any("?" in r for r in resp):
            log.warning("Adapter refused module header %08X", tx29)
            return self._recover()
        if self._caf != 0:
            self.send_command("ATCAF0")
            self._caf = 0
        self._meb_module = tx29
        return True

    def description(self) -> str:
        return f"{self.name} ({self.identity})"


class TcpElm327(Elm327Transport):
    """ELM327 over TCP — the simulator presents itself as an ELM327."""

    name = "elm327-tcp"

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        super().__init__(timeout=timeout, host=host, tcp_port=port)

