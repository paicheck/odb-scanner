"""Measurement quality: is this key trustworthy, and may analysis use it?

WHY THIS EXISTS
---------------
`docs/DATA_CATALOGUE.md` ends with a "Known BAD scalings — do not trust these
values" caveat listing about a dozen keys that decode to physically impossible
numbers on this car (a 1681 °C PCB, a 1738 V DC link on a ~435 V pack, a
483,476 °C windscreen). Until now that list was *prose*. Nothing in the code
stopped an anomaly scan, a trend fit or an investigation from taking those
numbers at face value and ranking them as the most alarming signals on the
vehicle — which they are, and always will be, precisely because the scale
factor is wrong.

This module turns the prose into a machine-readable registry so every consumer
can ask two questions:

  * how much do we trust this measurement's decoder? (`verification`)
  * may this measurement be used for health scoring / fault attribution /
    rankings? (`use_in_scoring`)

and get a *consistent* answer. A measurement marked `INVALID` is never deleted:
the brief is explicit that the raw data stays. It is only excluded from
scoring, and it remains visible in the decoder-lab workspace
(``analysis.decoder_lab``) where it belongs.

WHAT IS NOT HERE
----------------
This module does not change a decoder. Correcting a scale factor is a
definition change, and the brief forbids doing that silently. A key marked
INVALID stays INVALID until a captured vehicle response (see
``analysis.replay``) justifies a new definition — at which point the catalogue
is updated with evidence, not this file by assertion.

The three verification levels deliberately reuse
``decoders.catalogue.Confidence``'s vocabulary (VERIFIED / PROVISIONAL /
UNKNOWN) and add one value it does not have: INVALID, meaning *we have positive
evidence the current decode is wrong*. PROVISIONAL means "plausible, not
proven"; INVALID means "known bad, do not use".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .catalogue import Confidence, build_catalogue


class Verification(str, Enum):
    """How much the decoder behind a key can be trusted."""

    VERIFIED = "verified"        # independently supported AND repeatable
    PROVISIONAL = "provisional"  # plausible, identifiable evidence, not proven
    UNKNOWN = "unknown"          # meaning / decoding / applicability unknown
    INVALID = "invalid"          # positive evidence the decode is wrong


class DataKind(str, Enum):
    """What sort of value the key carries, which decides what maths is legal."""

    NUMERIC = "numeric"      # a single real number
    TEXT = "text"            # a string (gear, VIN) sharing the value column
    JSON = "json"            # structured; numeric column NULL, payload in text_value
    DERIVED = "derived"      # computed here (calculated/estimated), not read
    UNKNOWN = "unknown"


class Plausibility(str, Enum):
    OK = "ok"
    IMPLAUSIBLE = "implausible"
    NOT_NUMERIC = "not_numeric"
    NO_VALUE = "no_value"
    NO_RANGE = "no_range"


# ---------------------------------------------------------------------------
# Known-bad scalings on THIS vehicle (the ID.3, profile "meb").
#
# Each entry names the physical absurdity, because a future reader must be able
# to re-check it rather than trust the label. Values in the comment are from
# docs/DATA_CATALOGUE.md and the real database.
# ---------------------------------------------------------------------------

_KNOWN_BAD: dict[str, str] = {
    # The 25-row electric-drive float32 family is no longer listed here: the
    # registry now decodes FLOAT32(A,B,C,D) instead of u16/10+1. The old ~1682
    # degC / ~1738 V / ~1e8 W values were the u16 mis-decode, not the sensor.
    # climate — temperatures / concentrations far out of range
    "windshield_temperature": (
        "the profile u16[0:2] x 15.625 - 39.5 read ~483476 degC; the decoder "
        "now uses byte 0 x 0.1 (11-13 degC, ~1.17 degC MAE versus outside) but "
        "windshield-vs-ambient still needs a cold-soak/defroster test"),
    "co2_content_interior": (
        "40/40 responses are 03FE; byte 1 x 100 gives 25,400 ppm, whole u16 "
        "gives 1,022 ppm, and 03FE is also the BMS not-fitted sentinel. 03FE "
        "now decodes to None; a non-sentinel 16-bit scale is still unconfirmed"),
    # bat_mgmt — the profile declares this field SIGNED. The old unsigned
    # decode turned the 0xFFFF sentinel into 2,047.97 V; the live decoder now
    # returns None for 0xFFFF/0xFFFE and negative values.
    "bat_charging_voltage": (
        "profile declares signed; unsigned 0xFFFF became 2,047.97 V. "
        "Sentinel/negative values now decode to None, not a voltage"),
    # BMS 0x5867 — a 10 km/LSB secondary counter, not the total odometer.
    # x10 matched a ~10.5 km drive; x100 is refuted by that same delta.
    "odometer_2": (
        "10 km/LSB secondary distance counter, not the odometer: x10 matched "
        "a ~10.5 km drive and x100 is refuted. It equals the main odometer "
        "minus 99,869-99,871 km on this car, consistent with a BMS reset at "
        "~99,870 km; service history is unavailable, so the absolute zero is "
        "still not usable for distance/SoH"),
    # Gateway 0x2A53 offsets 1 and 3 are complementary, not two currents:
    # u16[1:3] + u16[3:5] ~= 0x0508 in every capture. The key is raw-only.
    "dc_dc_converter_reserved_current": (
        "bytes 3-4 are the complement of the real current in bytes 1-2 "
        "(sum ~0x0508), not an independent current; raw-only, use "
        "dcdc_gw_current"),
}


# ---------------------------------------------------------------------------
# Data-kind fixes: keys whose value does not live where a naive reader looks.
# ---------------------------------------------------------------------------

_TEXT_KEYS = {"gear", "vin", "battery_serial"}

_JSON_KEYS = {
    "cell_soc_max", "cell_soc_min", "energy_counters", "coolant_temps",
}

_DERIVED_KEYS = {"cell_delta_mv", "cell_soc_spread_pct",
                 "battery_temp_spread_c", "pack_power_kw", "soh_pct",
                 "cell_voltage_sum", "cells_voltage_sum",
                 "aux_12v_power_w", "dcdc_power_kw", "dcdc_gw_power_kw"}


@dataclass(frozen=True)
class MeasurementQuality:
    """The judgement a consumer needs before using one measurement."""

    key: str
    verification: Verification
    data_kind: DataKind
    unit: str = ""
    use_in_scoring: bool = True
    known_issue: str = ""
    plausible_min: float | None = None
    plausible_max: float | None = None
    plausible_basis: str = "not established"
    confidence: Confidence = Confidence.UNKNOWN
    notes: str = ""
    falsified_by: str = ""
    extras: dict = field(default_factory=dict)

    # -- convenience -------------------------------------------------------
    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "verification": self.verification.value,
            "data_kind": self.data_kind.value,
            "unit": self.unit,
            "use_in_scoring": self.use_in_scoring,
            "known_issue": self.known_issue,
            "plausible_min": self.plausible_min,
            "plausible_max": self.plausible_max,
            "plausible_basis": self.plausible_basis,
            "confidence": self.confidence.value,
            "notes": self.notes,
            "falsified_by": self.falsified_by,
            **self.extras,
        }

    def check(self, value) -> tuple[Plausibility, str]:
        """Judge one observed value against this key's expectations.

        This never *changes* a value and never fills a gap: it labels. A key
        with no established range returns NO_RANGE rather than guessing one,
        because a made-up range would manufacture the very flag it claims to
        detect.
        """
        if value is None or value == "":
            return Plausibility.NO_VALUE, ""
        if self.data_kind in (DataKind.TEXT, DataKind.JSON):
            return Plausibility.NOT_NUMERIC, "value is not a single number"
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return Plausibility.NOT_NUMERIC, "stored as text, not numeric"
        if self.plausible_min is None or self.plausible_max is None:
            return Plausibility.NO_RANGE, self.plausible_basis
        if self.known_issue:
            # A known-bad decoder is implausible by policy whatever the number:
            # the range test would pass by luck for some rows of a mis-scaled
            # key, and calling those rows "ok" would be a lie of omission.
            return Plausibility.IMPLAUSIBLE, self.known_issue
        if value < self.plausible_min or value > self.plausible_max:
            return (Plausibility.IMPLAUSIBLE,
                    f"{value:g}{(' ' + self.unit) if self.unit else ''} outside "
                    f"[{self.plausible_min:g}, {self.plausible_max:g}] "
                    f"({self.plausible_basis})")
        return Plausibility.OK, ""


# A small, explicitly-sourced set of plausibility ranges. Anything absent keeps
# `NO_RANGE`, so nothing is flagged on the strength of an invented bound.
_RANGES: dict[str, tuple[float, float, str]] = {
    "soc_abs": (0.0, 100.0, "% of usable capacity"),
    "soc_normal": (0.0, 100.0, "% of usable capacity"),
    "pack_voltage": (200.0, 500.0, "MEB 58 kWh pack terminal voltage"),
    "pack_current": (-600.0, 600.0, "MEB pack current, charge/discharge"),
    "pack_power_kw": (-200.0, 200.0, "MEB pack DC power, charge/discharge"),
    "cell_voltage_max": (2.0, 4.4, "Li-ion cell terminal voltage"),
    "cell_voltage_min": (2.0, 4.4, "Li-ion cell terminal voltage"),
    "battery_temp": (-30.0, 70.0, "HV pack operating temperature"),
    "bat_temp_max": (-30.0, 70.0, "HV pack operating temperature"),
    "bat_temp_min": (-30.0, 70.0, "HV pack operating temperature"),
    "outside_temp": (-40.0, 60.0, "ambient air temperature"),
    "inside_temp": (-40.0, 70.0, "cabin air temperature"),
    "lv_voltage_obd": (0.0, 16.0, "12 V lead-acid system voltage"),
    "aux_12v_voltage": (0.0, 16.0, "12 V lead-acid system voltage"),
    "vehicle_speed": (0.0, 260.0, "road-legal passenger-car speed"),
    "speed_kmh": (0.0, 260.0, "road-legal passenger-car speed"),
    "odometer_km": (0.0, 2_000_000.0, "passenger-car lifetime distance"),
    "hv_energy_content": (0.0, 70_000.0, "MEB pack energy content (Wh)"),
    "hv_energy_max": (0.0, 70_000.0, "MEB pack energy content (Wh)"),
    "cell_delta_mv": (0.0, 500.0, "healthy pack cell spread (mV)"),
    "cell_soc_spread_pct": (0.0, 100.0, "cell SoC spread (%)"),
    "battery_temp_spread_c": (0.0, 30.0, "battery sensor spread (degC)"),
    "soh_pct": (0.0, 150.0, "state-of-health percentage"),
    "aux_12v_power_w": (-1000.0, 1000.0, "12 V battery terminal power"),
    "dcdc_power_kw": (0.0, 3.0, "MEB DC-DC converter output"),
    "dcdc_gw_power_kw": (0.0, 3.0, "MEB DC-DC converter output"),
    "res_pid_0149": (0.0, 100.0, "accelerator pedal position"),
}

# `cell_v_NNN` all share the per-cell range.
_CELL_KEYS = ("cell_v_", "cell_t_")


def _profile_bad_keys() -> dict[str, str]:
    """The known-bad set is about this specific car's decode, so it applies to
    any profile that decodes these DIDs. Kept as a function so the set cannot be
    mutated by a caller."""
    return dict(_KNOWN_BAD)


def _data_kind(key: str) -> DataKind:
    if key in _JSON_KEYS:
        return DataKind.JSON
    if key in _TEXT_KEYS:
        return DataKind.TEXT
    if key in _DERIVED_KEYS:
        return DataKind.DERIVED
    return DataKind.NUMERIC


def _verification(key: str, confidence: Confidence) -> Verification:
    if key in _KNOWN_BAD:
        return Verification.INVALID
    return {
        Confidence.VERIFIED: Verification.VERIFIED,
        Confidence.PROVISIONAL: Verification.PROVISIONAL,
        Confidence.UNKNOWN: Verification.UNKNOWN,
    }[confidence]


def _range_for(key: str, confidence: Confidence, plausible_min, plausible_max,
               plausible_basis: str):
    """Decide the range for a key: overlay > per-key table > per-cell > none."""
    if plausible_min is not None and plausible_max is not None:
        return plausible_min, plausible_max, plausible_basis
    if key in _RANGES:
        return _RANGES[key]
    for prefix in _CELL_KEYS:
        if key.startswith(prefix):
            if prefix == "cell_v_":
                return 2.0, 4.4, "Li-ion cell terminal voltage"
            return -40.0, 90.0, "HV pack cell-sensor temperature"
    return None, None, "not established"


def build_quality(profile: str = "meb",
                  registry=None) -> dict[str, MeasurementQuality]:
    """One quality record per catalogue key, with the known-bad set applied."""
    cat = build_catalogue(profile, registry)
    out: dict[str, MeasurementQuality] = {}
    for key, defn in cat.items():
        kind = _data_kind(key)
        # A derived value is not a vehicle reading; it may be scored, but the
        # kind tells consumers not to treat it as an independent observation.
        conf = defn.confidence
        verification = _verification(key, conf)
        pmin, pmax, basis = _range_for(
            key, conf, defn.plausible_min, defn.plausible_max,
            defn.plausible_basis)
        issue = _KNOWN_BAD.get(key, "")
        use = verification is not Verification.INVALID
        out[key] = MeasurementQuality(
            key=key,
            verification=verification,
            data_kind=kind,
            unit=defn.unit or "",
            use_in_scoring=use,
            known_issue=issue,
            plausible_min=pmin,
            plausible_max=pmax,
            plausible_basis=basis,
            confidence=conf,
            notes=defn.limitations,
            falsified_by=defn.falsified_by,
        )

    # Derived/calculated metrics are not DIDs, so the catalogue does not carry
    # them; but trend, export and explore consumers still need their ranges and
    # kind. They are labelled DERIVED so nobody mistakes them for a reading.
    from decoders.bms import COMPUTED_KEYS

    units = {k: unit for k, (_, unit) in COMPUTED_KEYS.items()}
    names = {k: name for k, (name, _) in COMPUTED_KEYS.items()}
    for key in sorted(_DERIVED_KEYS):
        if key in out:
            continue
        pmin, pmax, basis = _range_for(
            key, Confidence.PROVISIONAL, None, None, "")
        out[key] = MeasurementQuality(
            key=key,
            verification=(Verification.PROVISIONAL
                          if pmin is not None else Verification.UNKNOWN),
            data_kind=DataKind.DERIVED,
            unit=units.get(key, ""),
            use_in_scoring=True,
            known_issue="",
            plausible_min=pmin,
            plausible_max=pmax,
            plausible_basis=basis,
            confidence=Confidence.PROVISIONAL,
            notes=names.get(key, "calculated in collector.py"),
            falsified_by=(
                "recomputation from the stored input rows disagrees with the "
                "stored derived value"),
            extras={"name": names.get(key, key), "calculated": "true"},
        )
    return out


_CACHE: dict[str, dict[str, MeasurementQuality]] = {}


def quality_registry(profile: str = "meb") -> dict[str, MeasurementQuality]:
    """Cached registry for a profile. Building it walks the whole catalogue, so
    the dashboard should not pay for it on every request."""
    if profile not in _CACHE:
        _CACHE[profile] = build_quality(profile)
    return _CACHE[profile]


def quality_for(profile: str, key: str) -> MeasurementQuality:
    """Quality for one key, or a conservative UNKNOWN record when the key is
    not in the catalogue.

    A key the catalogue does not describe is *not* assumed safe: it is marked
    UNKNOWN and excluded from scoring, because the whole point of this module is
    that an undescribed number must not silently drive a health verdict."""
    reg = quality_registry(profile)
    if key in reg:
        return reg[key]
    # A key the catalogue does not describe can still be classified by shape
    # (text / JSON / derived), so consumers are not told a derived value is a
    # raw reading just because it has no DID.
    pmin, pmax, basis = _range_for(key, Confidence.UNKNOWN, None, None, "")
    kind = _data_kind(key)
    return MeasurementQuality(
        key=key,
        verification=Verification.UNKNOWN,
        data_kind=kind,
        use_in_scoring=False,
        known_issue="key not present in the decoder catalogue",
        plausible_min=pmin,
        plausible_max=pmax,
        plausible_basis=basis,
        notes="no definition; cannot be trusted for scoring",
    )


def use_in_scoring(profile: str, key: str) -> bool:
    return quality_for(profile, key).use_in_scoring


def excluded_keys(profile: str = "meb") -> set[str]:
    """Keys that must not feed health scoring, attribution or rankings."""
    return {k for k, q in quality_registry(profile).items()
            if not q.use_in_scoring}


def quality_rows(profile: str = "meb") -> list[dict]:
    """Flatten to dicts sorted by key, ready for CSV/JSON export."""
    reg = quality_registry(profile)
    return [reg[k].as_dict() for k in sorted(reg)]


def summary(profile: str = "meb") -> dict:
    reg = quality_registry(profile)
    out = {"total": len(reg), "by_verification": {}, "by_data_kind": {},
           "excluded": 0}
    for q in reg.values():
        out["by_verification"][q.verification.value] = \
            out["by_verification"].get(q.verification.value, 0) + 1
        out["by_data_kind"][q.data_kind.value] = \
            out["by_data_kind"].get(q.data_kind.value, 0) + 1
        if not q.use_in_scoring:
            out["excluded"] += 1
    return out


def known_bad_keys() -> set[str]:
    """Keys with positive evidence their decoder is wrong."""
    return set(_KNOWN_BAD)
