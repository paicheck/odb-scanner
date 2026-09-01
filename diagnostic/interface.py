"""Transport-agnostic OBD interface abstractions.

Every concrete transport (USB ELM327 serial, TCP simulator, later python-can)
implements this interface. Higher layers never touch hardware directly.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class CommunicationError(RuntimeError):
    """Generic communication failure with adapter or vehicle."""


class AdapterNotFoundError(CommunicationError):
    """No OBD adapter could be detected."""


class OBDInterface(ABC):
    """A line-oriented ELM327-style interface.

    `send_command` sends one ELM327 command line and returns the cleaned
    response lines (without the '>' prompt). All protocol work (AT init,
    UDS, modes) happens above this layer.
    """

    name = "abstract"

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def send_command(self, command: str) -> list[str]: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    def __enter__(self) -> "OBDInterface":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - best effort
            log.exception("Error while closing adapter")


def detect_serial_ports() -> list[dict]:
    """List available serial ports (USB ELM327/OBDLink adapters)."""
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover
        return []
    return [
        {"port": p.device, "description": p.description, "hwid": p.hwid}
        for p in list_ports.comports()
    ]
