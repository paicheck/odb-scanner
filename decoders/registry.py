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

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger(__name__)


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
        """Decode raw bytes, or None if there is nothing to decode.

        A decoder that raises is a bug in *our* code, not a vehicle condition,
        so it must not silently look like "this DID has no value". It is logged
        at WARNING with the raw bytes and still returns None, because one bad
        decoder must never abort a collection pass.
        """
        if self.decode is None or not raw:
            return None
        try:
            return self.decode(raw)
        except Exception:
            log.warning("decoder for %s (DID 0x%s) raised on raw=%s -- "
                        "returning None; raw bytes are preserved",
                        self.key,
                        f"{self.did:04X}" if self.did is not None else "----",
                        raw.hex().upper())
            log.debug("decoder traceback", exc_info=True)
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


def build_default_registry(profile: str = "eup") -> DIDRegistry:
    """Build the DID map for a vehicle profile.

    eup  -- e-Up / OVMS-sourced map (the historical default; also what the
            built-in simulator speaks for its e-Up persona).
    meb  -- VW MEB map (ID.3/ID.4/Enyaq/Born): different addressing AND
            different scale factors, e.g. pack voltage u16/4 (not /64) and
            cell voltage u16/1000+1 (not /256). Selected per car via
            vehicle.did_profile in config.yaml.

    NOTE -- the two gateway energy DIDs (0x2AB8 / 0x2AB2) exist on BOTH
    sides of this map, under two independent implementations that disagree:

      eup  -> decoders.gateway : key ecu_key "gate", u16be at offset 2 x25
               Wh / offset 0 x50 Wh, from the recovered Car Scanner profile
               (MUL/DL/SBI fields recovered verbatim).
      meb  -> decoders.meb     : key ecu_key "energy", u32be / 1310.77,
               whose own docstring marks the divisor ASSUMED and unverified.

    They must be registered exactly once per profile, so each profile keeps
    its own. Which addressing is right -- "gate" 0x792->0x710 versus
    "energy" 0x710->0x77A -- is UNRESOLVED and is not decided here; see
    docs/investigation/meb_gateway_energy.md and docs/MEB_DIAGNOSTIC_REFERENCE.md.
    """
    reg = DIDRegistry()
    if profile == "meb":
        from decoders import meb

        meb.register_meb(reg)
        return reg

    from decoders import battery, charging, gateway

    battery.register_battery(reg)
    battery.register_cell_specs(reg)
    charging.register_charging(reg)
    gateway.register_gateway(reg)
    return reg


# -- raw byte helpers (big-endian, VAG convention) ---------------------------
def u16be(raw: bytes, off: int = 0) -> int:
    return (raw[off] << 8) | raw[off + 1]


def u32be(raw: bytes, off: int = 0) -> int:
    """Big-endian u32, refusing a payload too short to hold one.

    This must fail the same way u16be does. u16be indexes, so a short payload
    raises IndexError, which DIDSpec.decode_value turns into a logged None --
    the safe outcome. int.from_bytes accepts 0-3 bytes happily, so slicing
    here used to fail OPEN and return a plausible-looking number instead:

        pack_current  raw=06BD  ->  -1482.75 A     (4-byte value is 1.98 A)
        hv_energy_max raw=0428  ->      0.81 Wh

    Nothing downstream can tell that apart from a real reading. collector.py
    turns -1482.75 A into pack_power_kw = -639 kW, charging detection
    inverts, and the anomaly baseline adopts it as normal. uds.reassemble
    already documents this exact hazard and guards the multi-frame path; the
    single-frame path reached the decoder unguarded.
    """
    chunk = raw[off : off + 4]
    if len(chunk) != 4:
        raise IndexError(
            f"u32be needs 4 bytes at offset {off}, got {len(chunk)}")
    return int.from_bytes(chunk, "big")
