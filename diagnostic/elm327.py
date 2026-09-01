"""ELM327-compatible transport over serial (USB ELM327/OBDLink) or TCP.

TCP mode is used by the built-in vehicle simulator and by TCP-serial bridges.
Initialization uses conservative, documented ELM327 AT commands:

    ATZ    reset            ATE0   echo off
    ATL0   linefeeds off    ATS0   spaces off
    ATH1   headers ON (required to address specific ECUs via UDS)
    ATAT1  adaptive timing  ATSP6  ISO 15765-4 CAN 500 kbit/s 11-bit
    ATCAF1 CAN auto-formatting on

Note: multi-frame (ISO-TP) responses are reassembled by the ELM327 for
*requests it sends itself*; it automatically emits flow-control frames.
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
            except (OSError, socket.timeout):
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
    def initialize(self) -> str:
        """Bring the adapter into a known state. Returns adapter identity."""
        time.sleep(0.3)
        self.send_command("ATZ")
        time.sleep(0.3)
        resp = self.send_command("ATI")
        self.identity = " ".join(resp) if resp else "unknown"
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATSP6", "ATCAF1"):
            self.send_command(cmd)
        log.info("Adapter initialized: %s", self.identity)
        return self.identity

    def set_header(self, tx_id: int) -> None:
        self.send_command(f"ATSH{tx_id & 0x7FF:03X}")

    def set_receive_address(self, rx_id: int | None) -> None:
        if rx_id is None:
            resp = self.send_command("ATCRA")
            if any("?" in r for r in resp):
                log.warning("Adapter refused 'ATCRA' (clear filter)")
        else:
            self.send_command(f"ATCRA{rx_id & 0x7FF:03X}")

    def description(self) -> str:
        return f"{self.name} ({self.identity})"


class TcpElm327(Elm327Transport):
    """ELM327 over TCP — the simulator presents itself as an ELM327."""

    name = "elm327-tcp"

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        super().__init__(timeout=timeout, host=host, tcp_port=port)

