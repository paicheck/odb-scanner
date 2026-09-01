"""DID decoder registry.

Every decodable parameter is registered as a DIDSpec carrying:

  * the ECU and DID it comes from
  * a decode function (raw bytes -> value) or None (raw-only, not yet decoded)
  * provenance: reported | calculated | estimated
  * doc_status: documented | experimentally determined | inferred | unknown

Raw bytes are ALWAYS preserved by the collector regardless of whether the
DID decodes, so new decoders can be added later without data loss
(requirement #14). Scale factors cite their source in `notes`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Provenance(str, Enum):
    REPORTED = "reported"        # directly reported by the vehicle
    CALCULATED = "calculated"    # derived in software from reported values
    ESTIMATED = "estimated"      # inferred; NOT an official vehicle measurement


class DocStatus(str, Enum):
    DOCUMENTED = "documented"
    EXPERIMENTAL = "experimentally determined"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DIDSpec:
    key: str
    ecu_key: str
    did: int | None
    name: str
    unit: str
    decode: Callable[[bytes], object] | None = None
    provenance: Provenance = Provenance.REPORTED
    doc_status: DocStatus = DocStatus.EXPERIMENTAL
    notes: str = ""
    slow: bool = False  # poll on slow interval (counter/static-ish values)

    def decode_value(self, raw: bytes):
        if self.decode is None or not raw:
            return None
        try:
            return self.decode(raw)
        except Exception:
            return None


class DIDRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, DIDSpec] = {}

    def register(self, spec: DIDSpec) -> None:
        if spec.key in self._specs:
            raise ValueError(f"Duplicate DID spec key: {spec.key}")
        self._specs[spec.key] = spec

    def get(self, key: str) -> DIDSpec:
        return self._specs[key]

    def all(self) -> list[DIDSpec]:
        return list(self._specs.values())

    def fast(self) -> list[DIDSpec]:
        return [s for s in self._specs.values() if not s.slow and s.did is not None]

    def slow(self) -> list[DIDSpec]:
        return [s for s in self._specs.values() if s.slow and s.did is not None]


def build_default_registry() -> DIDRegistry:
    from decoders import battery, charging

    reg = DIDRegistry()
    battery.register_battery(reg)
    battery.register_cell_specs(reg)
    charging.register_charging(reg)
    return reg


# -- raw byte helpers (big-endian, VAG convention) ---------------------------
def u16be(raw: bytes, off: int = 0) -> int:
    return (raw[off] << 8) | raw[off + 1]


def u32be(raw: bytes, off: int = 0) -> int:
    return int.from_bytes(raw[off : off + 4], "big")
