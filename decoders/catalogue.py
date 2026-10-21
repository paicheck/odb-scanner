"""Versioned, provenance-aware measurement definition catalogue.

WHY THIS EXISTS
---------------
`decoders/registry.py` already holds every decodable DID as a `DIDSpec`, and
`database/measurements` already holds every observation with its raw bytes. What
neither holds is the thing a reader actually needs to judge a number: *where
this definition came from, how sure are we, and what would falsify it*.

That gap had real consequences in this project. Two byte layouts were
implemented for the same two DIDs -- `decoders/gateway.py` (u16be, x25 / x50,
from the Car Scanner MEB profile) and `decoders/meb.py` (u32be / 1310.77,
"assumed") -- and the assumed one shipped as the default, because nothing
recorded that one of them had been *arithmetically refuted* against the only
real-world values that exist. See docs/investigation/phase_c_energy_validation.md.

So this module adds the missing half without inventing a parallel data model:

  * `MeasurementDefinition` carries the full definition record.
  * `build_catalogue(profile)` derives it FROM the `DIDRegistry`, so the
    catalogue cannot drift away from the decoders that actually run. Fields the
    registry cannot supply (byte offset, width, signedness, evidence, limits)
    come from `OVERLAY`, and anything not in the overlay stays `UNKNOWN` rather
    than being guessed.
  * `validate_catalogue()` re-checks the overlay against the live decoder with
    a synthetic probe, so a layout that does not reproduce the decoder is a
    hard error rather than a plausible-looking spreadsheet row.

CONFIDENCE (deliberately the brief's three-state vocabulary, not DocStatus)
-----------------------------------------------------------------------
    VERIFIED     independently supported by sufficient evidence AND repeatable
                 validation. The two gateway energy DIDs (0x2AB8 / 0x2AB2)
                 reached VERIFIED on 2026-10-15, when a passive HCI capture of
                 Car Scanner returned their actual response bytes. Every other
                 MEB DID remains below VERIFIED, because no other raw vehicle
                 response has been captured yet.
    PROVISIONAL  plausible, backed by identifiable evidence, not fully
                 validated.
    UNKNOWN      meaning, decoding or applicability cannot be established.

A source's own label is never treated as verification. `evidence_kind` records
what kind of artefact the claim rests on, and `falsified_by` records what
observation would overturn it.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from enum import Enum

from .registry import DIDRegistry, DIDSpec, build_default_registry

CATALOGUE_VERSION = "2026-10-21.1"


class Confidence(str, Enum):
    VERIFIED = "VERIFIED"
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"


class ByteOrder(str, Enum):
    BIG_ENDIAN = "big-endian"
    LITTLE_ENDIAN = "little-endian"
    UNKNOWN = "unknown"


class Encoding(str, Enum):
    """How the bytes in a ByteLayout become the raw number."""

    INTEGER = "integer"
    FLOAT32 = "float32"


class EvidenceKind(str, Enum):
    """What sort of artefact a definition rests on. Not a confidence level."""

    NONE = "none"
    #: a name or label lifted out of a third-party application's data
    THIRD_PARTY_LABEL = "third-party application label"
    #: an explicit encoding field recovered from that application's definition
    #: records (e.g. Car Scanner's SBI/DL/MUL/DIV/OFS)
    THIRD_PARTY_DEFINITION = "third-party application definition record"
    #: an open-source reverse-engineering implementation
    OPEN_SOURCE_RE = "open-source reverse engineering"
    #: a value we computed ourselves
    DERIVED = "derived in this project"
    #: bytes actually returned by a vehicle
    VEHICLE_RESPONSE = "vehicle response"


@dataclass(frozen=True)
class ByteLayout:
    """How to turn the DID data record into a number.

    value = raw * scale / divisor + addend, where ``raw`` is an integer by
    default or an IEEE-754 float32 when ``encoding`` says so.
    """

    offset: int
    length: int
    byte_order: ByteOrder
    signed: bool
    scale: float = 1.0
    divisor: float = 1.0
    addend: float = 0.0
    encoding: Encoding = Encoding.INTEGER

    def __post_init__(self) -> None:
        if self.encoding is Encoding.FLOAT32 and self.length != 4:
            raise ValueError("float32 layouts must be 4 bytes wide")

    def describe(self) -> str:
        endian = "be" if self.byte_order is ByteOrder.BIG_ENDIAN else "le"
        if self.encoding is Encoding.FLOAT32:
            parts = [f"float32{endian}",
                     f"[{self.offset}:{self.offset + self.length}]"]
        else:
            parts = [f"u{self.length * 8}{endian}",
                     f"[{self.offset}:{self.offset + self.length}]",
                     f"{'signed' if self.signed else 'unsigned'}"]
        m = f" x {self.scale:g}" if self.scale != 1.0 else ""
        dv = f" / {self.divisor:g}" if self.divisor != 1.0 else ""
        a = f" + {self.addend:g}" if self.addend else ""
        return "".join(parts) + m + dv + a

    def signature(self) -> str:
        """Stable text form; part of the definition version."""
        return self.describe()


@dataclass(frozen=True)
class MeasurementDefinition:
    key: str
    name: str
    unit: str
    confidence: Confidence
    # addressing / protocol
    ecu_key: str | None = None
    ecu_name: str | None = None
    request_can_id: int | None = None
    response_can_id: int | None = None
    address_confidence: Confidence = Confidence.UNKNOWN
    transport: str = "UDS over ISO-TP"
    session_required: str = "unknown"
    service: int | None = None
    did: int | None = None
    request_bytes: str | None = None
    # decoding
    layout: ByteLayout | None = None
    response_length_hint: int | None = None
    # expectations
    plausible_min: float | None = None
    plausible_max: float | None = None
    plausible_basis: str = "not established"
    # provenance
    provenance: str = "reported"
    doc_status: str = "unknown"
    evidence_kind: EvidenceKind = EvidenceKind.NONE
    evidence_source: str = ""
    evidence_artifact: str = ""
    recorded_at: str = "2026-10-15"
    validation_method: str = "none"
    limitations: str = ""
    falsified_by: str = ""
    competing_interpretations: tuple[str, ...] = ()
    version: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    def version_signature(self) -> str:
        """Digest of everything that would change a decoded value or its trust."""
        h = hashlib.sha256()
        for part in (self.key, self.unit, str(self.did), str(self.service),
                     self.layout.signature() if self.layout else "no-layout",
                     self.confidence.value, self.ecu_key or ""):
            h.update(part.encode())
        return h.hexdigest()[:12]

    def ensure_version(self) -> MeasurementDefinition:
        if not self.version:
            object.__setattr__(self, "version",
                               f"{CATALOGUE_VERSION}+{self.version_signature()}")
        return self

    def as_dict(self) -> dict:
        d = {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "confidence": self.confidence.value,
            "ecu_key": self.ecu_key,
            "ecu_name": self.ecu_name,
            "request_can_id": (f"0x{self.request_can_id:03X}"
                               if self.request_can_id is not None else None),
            "response_can_id": (f"0x{self.response_can_id:03X}"
                                if self.response_can_id is not None else None),
            "address_confidence": self.address_confidence.value,
            "transport": self.transport,
            "session_required": self.session_required,
            "service": (f"0x{self.service:02X}") if self.service is not None else None,
            "did": (f"0x{self.did:04X}") if self.did is not None else None,
            "request_bytes": self.request_bytes,
            "byte_offset": self.layout.offset if self.layout else None,
            "byte_length": self.layout.length if self.layout else None,
            "byte_order": self.layout.byte_order.value if self.layout else None,
            "encoding": self.layout.encoding.value if self.layout else None,
            "signed": self.layout.signed if self.layout else None,
            "scale": self.layout.scale if self.layout else None,
            "divisor": self.layout.divisor if self.layout else None,
            "addend": self.layout.addend if self.layout else None,
            "formula": self.layout.describe() if self.layout else None,
            "response_length_hint": self.response_length_hint,
            "plausible_min": self.plausible_min,
            "plausible_max": self.plausible_max,
            "plausible_basis": self.plausible_basis,
            "provenance": self.provenance,
            "doc_status": self.doc_status,
            "evidence_kind": self.evidence_kind.value,
            "evidence_source": self.evidence_source,
            "evidence_artifact": self.evidence_artifact,
            "recorded_at": self.recorded_at,
            "validation_method": self.validation_method,
            "limitations": self.limitations,
            "falsified_by": self.falsified_by,
            "competing_interpretations": "; ".join(self.competing_interpretations),
            "version": self.version,
        }
        d.update(self.extras)
        return d


# ---------------------------------------------------------------------------
# Overlay: what the registry cannot tell us, keyed by (profile, key).
# Anything absent stays UNKNOWN. Nothing here is inferred from plausibility
# alone; each entry names the artefact it came from.
# ---------------------------------------------------------------------------

def _eldrive_float_overlays() -> dict[str, dict]:
    """Catalogue entries for the 25 electric-drive float32 DIDs.

    The registry already decodes them; this records *why* that encoding is
    believed and what a sane value looks like. The bounds are deliberately
    broad screening limits: they catch a u16/10+1 re-decode (which produced
    ~1680 degC and ~1738 V), not a control-limit violation.
    """
    from .meb import _FLOAT32_KEYS

    groupings = (
        (
            {
                "actual_mechanical_torque_calculated",
                "actual_mechanical_torque_commanded_value",
                "actual_possible_maximum_torque",
                "actual_possible_minimum_torque",
                "torque_calculated_from_measured_currents",
            },
            (-600.0, 600.0),
            "MEB drive-unit torque; broad screening bound, not a control limit",
        ),
        (
            {
                "dc_link_current",
                "motor_maximum_current",
                "motor_minimum_current",
            },
            (-1000.0, 1000.0),
            "MEB drive-unit current; broad screening bound, not a control limit",
        ),
        (
            {
                "dc_link_voltage",
                "dc_link_voltage_reserve",
                "desired_link_voltage",
                "dmcm_stator_voltage_d_direction_actual_value",
                "dmcm_stator_voltage_d_direction_commanded_value",
                "dmcm_stator_voltage_q_direction_actual_value",
                "dmcm_stator_voltage_q_direction_commanded_value",
            },
            (-600.0, 600.0),
            "MEB 400 V-class drive-unit voltage; broad screening bound, not a "
            "control limit",
        ),
        (
            {
                "ecu_pcb_temperature_1",
                "ecu_pcb_temperature_2",
                "electric_drive_inflow_coolant_temperature",
                "rotor_temperature_model",
                "stator_temperature",
            },
            (-40.0, 200.0),
            "passenger-EV drive-unit temperature; broad screening bound, not a "
            "control limit",
        ),
        (
            {
                "inverter_power_loss_phase_u",
                "inverter_power_loss_phase_v",
                "inverter_power_loss_phase_w",
            },
            (0.0, 10_000.0),
            "MEB inverter loss; broad screening bound, not a control limit",
        ),
        (
            {
                "electric_drive_motor_speed_rad_sec",
                "stator_electrical_frequency_rad_sec",
            },
            (-20_000.0, 20_000.0),
            "electric-machine speed/frequency; broad screening bound, not a "
            "control limit",
        ),
    )
    ranges: dict[str, tuple[float, float, str]] = {}
    for keys, (lo, hi), basis in groupings:
        for key in keys:
            ranges[key] = (lo, hi, basis)

    source = (
        "Car Scanner MEB profile #342 declares FLOAT32(A,B,C,D) for these "
        "rows; 17 stored real-vehicle payloads per DID re-decode to finite, "
        "physically plausible values under that encoding"
    )
    common = {
        "confidence": Confidence.PROVISIONAL,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": source,
        "evidence_artifact": (
            "data/odb_scanner.db; "
            "research/measurement_discovery/implementation_impact.csv"),
        "validation_method": (
            "offline replay of stored raw bytes plus a catalogue probe: the "
            "declared float32 layout reproduces the live decoder exactly"),
        "limitations": (
            "the encoding is an explicit third-party definition plus physical "
            "plausibility; no VW document or project-owned capture confirms "
            "it yet. The bounds are screening limits, not control limits."),
        "falsified_by": (
            "a captured payload that is non-finite or outside the broad range, "
            "or a second source that states a different encoding"),
        "response_length_hint": 4,
    }
    out: dict[str, dict] = {}
    for key in _FLOAT32_KEYS:
        lo, hi, basis = ranges[key]
        out[key] = {
            **common,
            "layout": ByteLayout(
                offset=0, length=4, byte_order=ByteOrder.BIG_ENDIAN,
                signed=False, encoding=Encoding.FLOAT32),
            "plausible_min": lo,
            "plausible_max": hi,
            "plausible_basis": basis,
        }
    return out


def _cs_layouts() -> dict[str, dict]:
    """Layouts the MEB module docstring already documents as observed bytes.

    Sourced from the `[CS]` entries in decoders/meb.py's module docstring: raw
    bytes from a Car Scanner export of THIS vehicle, with each scale
    independently corroborated by the spot2000 per-DID `Calculation` column
    (an open-source reverse-engineering source). That pairing is stronger than
    the HV energy DIDs have -- a byte-level agreement on a logged reading, plus
    a second source for the arithmetic -- but it is still PROVISIONAL, because
    this project has never captured a MEB response itself.

    Keys are the field names build_catalogue() reads: `length1..length4` carry
    the width, so the layout is built without repeating it three times.
    """
    src = ("decoders/meb.py module docstring [CS]: raw bytes from a Car "
           "Scanner export of this vehicle; scale corroborated by the "
           "spot2000 per-DID Calculation column")
    common = {"confidence": Confidence.PROVISIONAL,
              "evidence_kind": EvidenceKind.OPEN_SOURCE_RE,
              "evidence_source": src,
              "evidence_artifact": "data/real_car.db measurements rows",
              "validation_method": "offline replay of the stored DID record",
              "limitations": "bytes came from a Car Scanner export of this "
                             "vehicle, not from a capture made by this project"}
    return {
        "soc_abs": dict(length1={"scale": 0.4}, **common),
        "pack_voltage": dict(length2={"scale": 0.25}, **common),
        "cell_voltage_max": dict(length2={"scale": 1 / 4096}, **common),
        "cell_voltage_min": dict(length2={"scale": 1 / 4096}, **common),
        "battery_temp": dict(length1={"scale": 0.5, "addend": -40.0}, **common),
        "odometer_km": dict(length3={"scale": 1.0}, **common),
    }


OVERLAY: dict[tuple[str, str], dict] = {
    **(  # MEB BMS DIDs whose layout the module docstring records
        {("meb", k): v for k, v in _cs_layouts().items()}
    ),
    **(  # electric-drive float32 family
        {("meb", k): v for k, v in _eldrive_float_overlays().items()}
    ),
    ("meb", "energy_counters"): {
        "confidence": Confidence.PROVISIONAL,
        "response_length_hint": 16,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "Car Scanner MEB profile #342 exposes four fields at 0x1E32: "
            "signed charge/discharge Ah at bytes 0/4 and signed charge/"
            "discharge kWh at bytes 8/12. 28 stored real-vehicle records "
            "re-decoded with this layout and the counters move in the "
            "expected direction across the captured sessions."),
        "evidence_artifact": (
            "data/odb_scanner.db; tests/fixtures/meb_real_car_vectors.py; "
            "research/measurement_discovery/implementation_impact.csv"),
        "validation_method": (
            "offline replay of all stored 16-byte records; the old two-field "
            "energy interpretation is arithmetically refuted by the observed "
            "~24,437/~23,460 kWh totals."),
        "limitations": (
            "the four-field layout is a third-party definition that reproduces "
            "the observed totals; no VW document states whether the counters "
            "are lifetime, trip or service-relative."),
        "falsified_by": (
            "a captured record shorter than 16 bytes, or counters that decrease "
            "while the car charges/discharges"),
        "extras": {
            "layout_fields": (
                "0:4 charge Ah, 4:8 discharge Ah, 8:12 charge kWh, "
                "12:16 discharge kWh (all signed s32be)"),
        },
    },
    ("meb", "pack_current"): {
        "confidence": Confidence.PROVISIONAL,
        "layout": ByteLayout(offset=0, length=4, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False, scale=0.01, addend=-1500.0),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "stored 0x1E3D payload 000248FD -> -2.43 A while the independent "
            "0x035E payload FFED -> -1.9 A and the discharge counter rose; "
            "negative therefore means discharge"),
        "evidence_artifact": (
            "data/odb_scanner.db; "
            "research/measurement_discovery/decoder_audit.md"),
        "validation_method": (
            "two independent DIDs agree in sign during a discharge event; raw "
            "bytes reproduce the stored arithmetic exactly"),
        "limitations": (
            "the absolute scale is reverse-engineered; the sign convention is "
            "the part confirmed on independent bytes"),
        "falsified_by": (
            "a parked capture where current is positive while energy leaves "
            "the pack, or a scale that disagrees with a known load"),
        "plausible_min": -600.0,
        "plausible_max": 600.0,
        "plausible_basis": "MEB pack current, charge/discharge",
    },
    ("meb", "cell_voltage_max_cell"): {
        "confidence": Confidence.PROVISIONAL,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "Car Scanner MEB profile #342: u16be index at bytes 2-3; the "
            "index matched the tied maximum cell in 28 of 31 offline sweeps"),
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with the per-cell sweep",
        "limitations": "1-based index; tie handling is still under review",
        "falsified_by": "an index outside the populated cell count, or a sweep "
                        "where it does not identify the maximum",
        "plausible_min": 1.0,
        "plausible_max": 108.0,
        "plausible_basis": "MEB 58 kWh pack has 108 cell slots",
    },
    ("meb", "cell_voltage_min_cell"): {
        "confidence": Confidence.PROVISIONAL,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "Car Scanner MEB profile #342: u16be index at bytes 2-3; the "
            "index matched the tied minimum cell in 29 of 31 offline sweeps"),
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with the per-cell sweep",
        "limitations": "1-based index; tie handling is still under review",
        "falsified_by": "an index outside the populated cell count, or a sweep "
                        "where it does not identify the minimum",
        "plausible_min": 1.0,
        "plausible_max": 108.0,
        "plausible_basis": "MEB 58 kWh pack has 108 cell slots",
    },
    ("meb", "bat_temp_max_sensor"): {
        "confidence": Confidence.PROVISIONAL,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "Car Scanner MEB profile #342: u16be index at bytes 2-3; matched "
            "the tied hottest sensor in 31 of 31 offline sweeps"),
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with the per-sensor sweep",
        "limitations": "1-based sensor number; tie handling is still under review",
        "falsified_by": "an index outside the populated sensor count, or a sweep "
                        "where it does not identify the maximum",
        "plausible_min": 1.0,
        "plausible_max": 255.0,
        "plausible_basis": "u16 index narrowed to the observed sensor range",
    },
    ("meb", "bat_temp_min_sensor"): {
        "confidence": Confidence.PROVISIONAL,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                             signed=False),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "Car Scanner MEB profile #342: u16be index at bytes 2-3; matched "
            "the tied coldest sensor in 30 of 31 offline sweeps"),
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with the per-sensor sweep",
        "limitations": "1-based sensor number; tie handling is still under review",
        "falsified_by": "an index outside the populated sensor count, or a sweep "
                        "where it does not identify the minimum",
        "plausible_min": 1.0,
        "plausible_max": 255.0,
        "plausible_basis": "u16 index narrowed to the observed sensor range",
    },
    ("meb", "charge_port_normal"): {
        "confidence": Confidence.PROVISIONAL,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": "Car Scanner MEB profile #342 BitValue: byte 0 bit 2",
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with charge-port state labels",
        "limitations": "bit meaning is from the profile, not a VW document",
        "falsified_by": "a capture where the bit does not track AC plug state",
        "plausible_min": 0.0,
        "plausible_max": 1.0,
        "plausible_basis": "single bit",
        "extras": {"bit": "byte 0 bit 2"},
    },
    ("meb", "charge_port_rapid"): {
        "confidence": Confidence.PROVISIONAL,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": "Car Scanner MEB profile #342 BitValue: byte 0 bit 3",
        "evidence_artifact": "research/measurement_discovery/decoder_audit.md",
        "validation_method": "offline comparison with charge-port state labels",
        "limitations": "bit meaning is from the profile, not a VW document",
        "falsified_by": "a capture where the bit does not track DC plug state",
        "plausible_min": 0.0,
        "plausible_max": 1.0,
        "plausible_basis": "single bit",
        "extras": {"bit": "byte 0 bit 3"},
    },
    ("meb", "battery_serial"): {
        "confidence": Confidence.PROVISIONAL,
        "response_length_hint": 16,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "stored 0x0500 payload decodes as trimmed ASCII "
            "'288 VWARMFF265' on every captured row"),
        "evidence_artifact": "data/odb_scanner.db",
        "validation_method": "offline replay of stored ASCII payloads",
        "limitations": "the string is readable; its exact field boundaries are "
                      "not documented",
        "falsified_by": "a payload that is not valid printable ASCII",
        "extras": {"encoding": "trimmed ASCII"},
    },
    ("meb", "hv_energy_content"): {
        "confidence": Confidence.VERIFIED,
        "address_confidence": Confidence.VERIFIED,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                              signed=False, scale=25.0),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "Passive Bluetooth HCI capture of Car Scanner, 2026-10-15 "
            "(docs/investigation/meb_gateway_energy_passive_capture_20261015.md): "
            "request 222AB8 -> multi-frame response 62 2A B8 00 00 05 7F 00 00 "
            "00 00 (11 bytes). u16be[2:4] = 0x057F = 1407 x 25 = 35,175 Wh, "
            "matching Car Scanner's displayed 35,175 Wh exactly."),
        "evidence_artifact": "docs/investigation/meb_gateway_energy_passive_"
                            "capture_20261015.md",
        "validation_method": (
            "captured bytes: the response's own bytes decode to the "
            "contemporaneous displayed value (35,175 Wh) under the profile's "
            "SBI=2 DL=2 MUL=25 layout. The u32/1310.77 alternative is excluded "
            "by the earlier gcd constraint, so this layout is the survivor."),
        "limitations": (
            "Address (0x710 -> 0x77A), the SBI=2 offset and the x25 multiplier "
            "are CONFIRMED on captured bytes. The GROSS-vs-USABLE meaning still "
            "rests on the profile name, not a VW document."),
        "falsified_by": (
            "a captured DID record whose length is under 4 bytes, or a decoded "
            "value that is not a multiple of 25 Wh while the pack state changes."),
        "competing_interpretations": (
            "usable vs gross energy content (unresolved)"),
        "plausible_min": 0.0,
        "plausible_max": 65535 * 25.0,
        "plausible_basis": "encoding range only (u16 x 25 Wh).",
    },
    ("meb", "hv_energy_max"): {
        "confidence": Confidence.VERIFIED,
        "address_confidence": Confidence.VERIFIED,
        "layout": ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                              signed=False, scale=50.0),
        "response_length_hint": 2,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "Passive Bluetooth HCI capture of Car Scanner, 2026-10-15 "
            "(docs/investigation/meb_gateway_energy_passive_capture_20261015.md): "
            "request 222AB2 -> single-frame response 62 2A B2 04 28 04 00 "
            "(7 bytes). u16be[0:2] = 0x0428 = 1064 x 50 = 53,200 Wh, matching "
            "Car Scanner's displayed 53,200 Wh exactly."),
        "evidence_artifact": "docs/investigation/meb_gateway_energy_passive_"
                            "capture_20261015.md",
        "validation_method": (
            "captured bytes: the response's own bytes decode to the "
            "contemporaneous displayed value (53,200 Wh) under the profile's "
            "SBI=0 DL=2 MUL=50 layout. This DID no longer rests on the content "
            "DID's refutation alone."),
        "limitations": (
            "Address (0x710 -> 0x77A), the SBI=0 offset and the x50 multiplier "
            "are CONFIRMED on captured bytes. Whether the value is GROSS rated "
            "or MAXIMUM USABLE capacity is unresolved; 53,200 Wh is 0.917 of a "
            "marketed 58 kWh, consistent with either reading."),
        "falsified_by": (
            "a value that changes while the pack state is constant, or a captured "
            "record shorter than 2 bytes."),
        "competing_interpretations": (
            "gross rated capacity vs maximum usable capacity (unresolved); "
            "state of health proxy vs absolute value (unresolved)"),
        "plausible_min": 0.0,
        "plausible_max": 65535 * 50.0,
        "plausible_basis": "encoding range only (u16 x 50 Wh).",
    },
    ("eup", "hv_energy_content"): {
        "confidence": Confidence.VERIFIED,
        "layout": ByteLayout(offset=2, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                              signed=False, scale=25.0),
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": "Same captured bytes as the meb entry "
                           "(2026-10-15 passive HCI capture).",
        "evidence_artifact": "docs/investigation/meb_gateway_energy_passive_"
                            "capture_20261015.md",
        "validation_method": "verified via the meb entry's captured response.",
        "limitations": "Decode confirmed on an ID.3 (MEB). The e-Up's own "
                       "gateway addressing is a different car and remains "
                       "unverified (address_confidence stays UNKNOWN).",
        "plausible_min": 0.0,
        "plausible_max": 65535 * 25.0,
        "plausible_basis": "encoding range only.",
    },
    ("eup", "hv_energy_max"): {
        "confidence": Confidence.VERIFIED,
        "layout": ByteLayout(offset=0, length=2, byte_order=ByteOrder.BIG_ENDIAN,
                              signed=False, scale=50.0),
        "response_length_hint": 2,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": "Same captured bytes as the meb entry "
                           "(2026-10-15 passive HCI capture).",
        "evidence_artifact": "docs/investigation/meb_gateway_energy_passive_"
                            "capture_20261015.md",
        "validation_method": "verified via the meb entry's captured response.",
        "limitations": "Decode confirmed on an ID.3 (MEB); e-Up gateway "
                       "addressing unverified. GROSS vs MAXIMUM USABLE capacity "
                       "unresolved.",
        "plausible_min": 0.0,
        "plausible_max": 65535 * 50.0,
        "plausible_basis": "encoding range only.",
    },
    # -- 2026-10-21 payload research -------------------------------------
    ("meb", "bat_charging_voltage"): {
        "confidence": Confidence.PROVISIONAL,
        "length2": {"scale": 0.0312499784501336, "signed": True},
        "response_length_hint": 2,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "stored 0x1E39 payloads 0000/0001/0006 and FFFF; the Car Scanner "
            "profile declares the field signed. Signed, FFFF is -0.03125 V; "
            "the parked-car samples are 0-0.1875 V."),
        "evidence_artifact": (
            "data/odb_scanner.db; docs/investigation/live_measurement_evidence.csv"),
        "validation_method": (
            "offline replay of the stored records; the sentinel is also the "
            "pattern used by unpopulated BMS slots"),
        "limitations": (
            "every capture is from a parked car, so the top of the charging "
            "range is unconfirmed; 0xFFFF/0xFFFE and negatives now return None"),
        "falsified_by": (
            "a capture where 0xFFFF is followed by a plausible charging "
            "voltage and the field is known to be unsigned"),
        "plausible_min": 0.0,
        "plausible_max": 500.0,
        "plausible_basis": "MEB pack charging voltage",
    },
    ("meb", "odometer_2"): {
        "confidence": Confidence.PROVISIONAL,
        "length2": {"offset": 2, "scale": 10.0},
        "response_length_hint": 4,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "0x5867 0005045A00000000 -> 11,140 and 0005045B00000000 -> 11,150 "
            "while the four agreeing odometers moved ~10.5 km. The x10 delta "
            "matches; x100 would require 100 km per LSB and is refuted."),
        "evidence_artifact": "data/odb_scanner.db",
        "validation_method": "offline delta check across the 18/19 Oct captures",
        "limitations": (
            "a 10 km/LSB secondary distance counter, not the total odometer; "
            "its absolute zero and physical meaning are unknown"),
        "falsified_by": (
            "a known-distance drive where the LSB does not track 10 km, or a "
            "capture where the absolute value matches the total odometer"),
        "plausible_min": 0.0,
        "plausible_max": 2_000_000.0,
        "plausible_basis": "passenger-car lifetime distance counter",
    },
    ("meb", "dc_dc_converter_reserved_current"): {
        "confidence": Confidence.UNKNOWN,
        "response_length_hint": 5,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "124 stored 0x2A53 five-byte payloads: u16[1:3] + u16[3:5] is "
            "1287-1291 (~0x0508). The second field is the complement of the "
            "real current, not a second current."),
        "evidence_artifact": "data/odb_scanner.db",
        "validation_method": "offline complement invariant",
        "limitations": (
            "the field carries no independent current; the registry is raw-only "
            "for this key and dcdc_gw_current is the real current"),
        "falsified_by": (
            "a known-load capture where the second field moves independently "
            "of the first"),
    },
    ("meb", "res_pid_0149"): {
        "confidence": Confidence.PROVISIONAL,
        "length1": {"offset": 0, "divisor": 2.55},
        "response_length_hint": 1,
        "evidence_kind": EvidenceKind.THIRD_PARTY_DEFINITION,
        "evidence_source": (
            "spot2000 0xF449: response byte XX / 2.55 = pedal position in %; "
            "evDash lists 22F449 as accelerator pedal position; live raw 00 "
            "= 0% in a parked car."),
        "evidence_artifact": (
            "research/measurement_discovery/sources/"
            "spot2000_VW_MEB_UDS_PIDs_list.csv; "
            "research/measurement_discovery/sources/evDash_CarVWID3.cpp; "
            "data/odb_scanner.db"),
        "validation_method": (
            "two independent third-party definitions plus a live zero"),
        "limitations": (
            "a pressed-pedal capture is still needed to confirm the top of "
            "the range; the stored key keeps its historical name"),
        "falsified_by": (
            "a pressed-pedal capture that does not scale as XX / 2.55"),
        "plausible_min": 0.0,
        "plausible_max": 100.0,
        "plausible_basis": "accelerator pedal position (%)",
        "extras": {"aliases": "accelerator_pedal_pct"},
    },
    ("meb", "windshield_temperature"): {
        "confidence": Confidence.UNKNOWN,
        "response_length_hint": 5,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "37 paired samples: byte 0 x 0.1 vs outside_temp has 1.17 degC "
            "mean absolute error (max 2.5 degC). The profile's u16[0:2] x "
            "15.625 - 39.5 decode produces ~483,476 degC and is refuted."),
        "evidence_artifact": "data/odb_scanner.db",
        "validation_method": (
            "offline correlation; the byte-0 candidate is not implemented"),
        "limitations": (
            "current decode is invalid; byte 0 x 0.1 is a candidate that still "
            "needs a cold-soak/defroster test to separate windshield from "
            "ambient"),
        "falsified_by": (
            "a cold-soak/defroster test where byte 0 does not follow the "
            "windshield/ambient temperature"),
    },
    ("meb", "co2_content_interior"): {
        "confidence": Confidence.UNKNOWN,
        "response_length_hint": 2,
        "evidence_kind": EvidenceKind.VEHICLE_RESPONSE,
        "evidence_source": (
            "40/40 stored 0x42DB responses are 03FE. Byte 1 x 100 = 25,400 "
            "ppm; the whole u16 = 1,022 ppm; 03FE is also the BMS not-fitted "
            "sentinel for unpopulated cell-temperature slots."),
        "evidence_artifact": "data/odb_scanner.db",
        "validation_method": (
            "offline payload enumeration; both hypotheses are retained"),
        "limitations": (
            "unresolved between a 1,022 ppm whole-u16 reading and a not-fitted "
            "sentinel; the current 25,400 ppm decode is not credible"),
        "falsified_by": (
            "a fresh-air/occupied test where the value moves in 400-2,000 ppm "
            "(whole-u16) or stays fixed (sentinel)"),
    },
}


def _extras(ov: dict) -> dict[str, str]:
    """Collapse the overlay's ``extras`` sub-map plus any leftover keys."""
    extra = dict(ov.pop("extras", {}) or {})
    extra.update(ov)
    return extra


def _ecu_addressing(profile: str, ecu_key: str | None):
    """(name, tx, rx) for an ECU key, or Nones. Never guesses."""
    if not ecu_key:
        return None, None, None
    try:
        from diagnostic.ecus import ECUS
    except Exception:  # pragma: no cover - import cycle guard
        return None, None, None
    spec = ECUS.get(ecu_key)
    if spec is None:
        return None, None, None
    return spec.name, spec.tx, spec.rx


def build_catalogue(
        profile: str = "meb",
        registry: DIDRegistry | None = None) -> dict[str, MeasurementDefinition]:
    """Derive the catalogue for one profile from its live registry."""
    reg = registry or build_default_registry(profile)
    out: dict[str, MeasurementDefinition] = {}
    for spec in reg.all():
        name, tx, rx = _ecu_addressing(profile, spec.ecu_key)
        ov = dict(OVERLAY.get((profile, spec.key), {}))
        addr_conf = ov.pop("address_confidence", Confidence.UNKNOWN)
        layout = None
        did = spec.did
        # An overlay entry may give the layout either as a ready ByteLayout or as
        # length1..lengthN + fields, so the compact form can be shared between
        # profiles without repeating the width on every key.
        lay = ov.pop("layout", None)
        if lay is None:
            for length in (1, 2, 3, 4):
                if (fields := ov.pop(f"length{length}", None)) is not None:
                    lay = ByteLayout(
                        offset=fields.get("offset", 0), length=length,
                        byte_order=ByteOrder(fields.get("byte_order", "big-endian")),
                        signed=fields.get("signed", False),
                        scale=fields.get("scale", 1.0),
                        divisor=fields.get("divisor", 1.0),
                        addend=fields.get("addend", 0.0),
                        encoding=Encoding(fields.get("encoding", "integer")))
                    break
        elif not isinstance(lay, ByteLayout):
            lay = ByteLayout(
                offset=lay.get("offset", 0), length=lay["length"],
                byte_order=ByteOrder(lay.get("byte_order", "big-endian")),
                signed=lay.get("signed", False),
                scale=lay.get("scale", 1.0),
                divisor=lay.get("divisor", 1.0),
                addend=lay.get("addend", 0.0),
                encoding=Encoding(lay.get("encoding", "integer")))
        layout = lay
        d = MeasurementDefinition(
            key=spec.key,
            name=spec.name,
            unit=spec.unit,
            confidence=ov.pop("confidence", Confidence.UNKNOWN),
            ecu_key=spec.ecu_key,
            ecu_name=name,
            request_can_id=tx,
            response_can_id=rx,
            address_confidence=addr_conf,
            service=0x22 if did is not None else None,
            did=did,
            request_bytes=(f"{0x22:02X}{did:04X}") if did is not None else None,
            layout=layout,
            response_length_hint=ov.pop("response_length_hint", None),
            provenance=spec.provenance.value,
            doc_status=spec.doc_status.value,
            evidence_kind=ov.pop("evidence_kind", EvidenceKind.NONE),
            evidence_source=ov.pop("evidence_source", ""),
            evidence_artifact=ov.pop("evidence_artifact", ""),
            validation_method=ov.pop("validation_method", "none"),
            limitations=ov.pop("limitations", spec.notes),
            falsified_by=ov.pop("falsified_by", ""),
            competing_interpretations=tuple(ov.pop("competing_interpretations", ())),
            plausible_min=ov.pop("plausible_min", None),
            plausible_max=ov.pop("plausible_max", None),
            plausible_basis=ov.pop("plausible_basis", "not established"),
            session_required=ov.pop("session_required", "unknown"),
            extras=_extras(ov),
        ).ensure_version()
        out[spec.key] = d
    return out


class CatalogueError(ValueError):
    """The catalogue disagrees with itself or with the live decoders."""


def _apply_layout(layout: ByteLayout, record: bytes) -> float:
    chunk = record[layout.offset:layout.offset + layout.length]
    if len(chunk) != layout.length:
        raise IndexError("short record")
    order = "big" if layout.byte_order is ByteOrder.BIG_ENDIAN else "little"
    if layout.encoding is Encoding.FLOAT32:
        n = struct.unpack((">" if order == "big" else "<") + "f", chunk)[0]
    else:
        n = int.from_bytes(chunk, order, signed=layout.signed)
    return (n * layout.scale / layout.divisor) + layout.addend


def validate_definition(defn: MeasurementDefinition, spec: DIDSpec) -> None:
    """Check a definition against the decoder that actually ships.

    Three things must hold, and each has bitten this project before:
      * the layout, if declared, must reproduce the live decode function on a
        probe record -- otherwise the spreadsheet and the code disagree;
      * PROVISIONAL/VERIFIED needs evidence of some kind;
      * VERIFIED needs evidence that is not merely a third party's own label.
    """
    if defn.did != spec.did:
        raise CatalogueError(
            f"{defn.key}: catalogue did 0x{defn.did:04X} != registry "
            f"0x{spec.did:04X}" if defn.did is not None and spec.did is not None
            else f"{defn.key}: did mismatch")
    if defn.unit != spec.unit:
        raise CatalogueError(
            f"{defn.key}: catalogue unit {defn.unit!r} != registry {spec.unit!r}")

    if defn.layout is not None and spec.decode is not None:
        layout = defn.layout
        # Build a record long enough for the declared offset and put a known
        # marker count at that offset. The marker must fit the declared WIDTH,
        # so it is masked rather than fixed at 0x1234 -- a 1-byte layout would
        # otherwise raise OverflowError instead of testing anything.
        rec = bytearray(layout.offset + layout.length)
        if layout.encoding is Encoding.FLOAT32:
            marker_bytes = struct.pack(
                (">" if layout.byte_order is ByteOrder.BIG_ENDIAN else "<") + "f",
                1.5)
        else:
            marker = 0x1234 & ((1 << (8 * layout.length)) - 1)
            marker_bytes = marker.to_bytes(
                layout.length,
                "big" if layout.byte_order is ByteOrder.BIG_ENDIAN else "little")
        rec[layout.offset:layout.offset + layout.length] = marker_bytes
        expect = _apply_layout(layout, bytes(rec))
        try:
            got = spec.decode(bytes(rec))
        except Exception as exc:
            raise CatalogueError(
                f"{defn.key}: live decoder raised on a probe record built from "
                f"its own declared layout ({layout.describe()}): {exc}") from exc
        if got is None or abs(float(got) - expect) > 1e-9:
            raise CatalogueError(
                f"{defn.key}: live decoder gives {got!r} but the declared layout "
                f"{layout.describe()} gives {expect!r}. The catalogue and the "
                f"decoder disagree; one of them is wrong.")

    if defn.confidence in (Confidence.VERIFIED, Confidence.PROVISIONAL) \
            and defn.evidence_kind is EvidenceKind.NONE:
        raise CatalogueError(
            f"{defn.key}: marked {defn.confidence.value} with no evidence kind. "
            f"A confidence level is a claim about evidence; without evidence it "
            f"is a guess.")
    if defn.confidence is Confidence.VERIFIED \
            and defn.evidence_kind in (EvidenceKind.THIRD_PARTY_LABEL,
                                       EvidenceKind.THIRD_PARTY_DEFINITION,
                                       EvidenceKind.DERIVED):
        raise CatalogueError(
            f"{defn.key}: marked VERIFIED on {defn.evidence_kind.value}. A "
            f"source's own label is not independent verification. This definition "
            f"cannot reach VERIFIED without a vehicle response or a cross-check "
            f"against a physical quantity.")
    if defn.plausible_min is not None and defn.plausible_max is not None \
            and defn.plausible_min > defn.plausible_max:
        raise CatalogueError(f"{defn.key}: plausible_min > plausible_max")


def validate_catalogue(profile: str = "meb",
                       registry: DIDRegistry | None = None) -> list[str]:
    """Return a list of problems; empty means the catalogue is self-consistent."""
    reg = registry or build_default_registry(profile)
    cat = build_catalogue(profile, reg)
    problems: list[str] = []
    for key, defn in cat.items():
        try:
            validate_definition(defn, reg.get(key))
        except CatalogueError as exc:
            problems.append(str(exc))
    return problems


def catalogue_rows(profile: str = "meb") -> list[dict]:
    """Flatten to dicts, sorted by key, ready for CSV/JSON export."""
    return [build_catalogue(profile)[k].as_dict()
            for k in sorted(build_catalogue(profile))]
