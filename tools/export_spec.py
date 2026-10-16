"""Export the shared diagnostic spec for cross-platform clients.

The Python collector and (future) native clients must never disagree about a
DID address, a scale factor, or what counts as a read-only request. This tool
dumps every piece of that knowledge into one versioned JSON file, so a client
(Kotlin/Android today, others later) can load it as data instead of
re-implementing it as code and drifting.

What the spec contains, and where it comes from:

  * read_only_guard      -- diagnostic/uds.py (the safety choke point)
  * ecus                 -- diagnostic/ecus.py, plus the addressing mode and
                            transport quirks this clone needs (derived from the
                            tx29 value; see diagnostic/elm327.py)
  * profiles.<p>.measurements
                         -- decoders/catalogue.py (the DID map + the structured
                            decode recipes that are known)
  * profiles.<p>.cell_voltages
                         -- decoders/meb.py (the per-cell sweep: base DID, slot
                            count, unused marker, decode)

The decode recipe is the simple-arithmetic form
    value = raw_integer[offset:offset+length] * scale / divisor + addend
carried by the catalogue's ByteLayout. Decoders that do not fit that form
(dict results, guarded lookups beyond the cell-unused marker) are exported
with "decode": null and must be handled by the client explicitly. Every raw
response is preserved by the collector regardless, so no data is lost by
deferring a decode.

Usage:
    python tools/export_spec.py [--out spec/odb_scanner.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagnostic import uds
from diagnostic.ecus import ECUS

SPEC_VERSION = "1"


def _addr_mode(tx29: int | None) -> str:
    """Classify a module's physical addressing for the transport layer."""
    if tx29 is None:
        return "functional"
    return "11-bit" if tx29 <= 0x7FF else "29-bit"


def _transport_quirks(tx29: int | None) -> dict:
    """The per-module ELM327 setup this clone needs (see elm327.py).

    The rules are empirical, established against the v1.5 clone:
      * 11-bit modules (energy 0x710, climate 0x746, gps 0x767) are read with
        auto-formatting ON and a bare request plus the ISO-TP flow-control
        preamble (ATFCSH/ATFCSD/ATFCSM 1) -- the 8-byte hand-framed CAF0 line
        is refused with '?' under ATSP6 (Track B).
      * 29-bit modules are hand-framed (ATCAF0) with the receive filter pinned
        (ATCRA) and flow control reset to default (ATFCSM 0).
      * functional reads use auto-formatting and no physical header.
    """
    mode = _addr_mode(tx29)
    if mode == "11-bit":
        return {"auto_formatting": True, "flow_control": True,
                "receive_filter": True, "hand_frame": False}
    if mode == "29-bit":
        return {"auto_formatting": False, "flow_control": False,
                "receive_filter": True, "hand_frame": True}
    return {"auto_formatting": True, "flow_control": False,
            "receive_filter": False, "hand_frame": False}


def _hex(v: int | None) -> str | None:
    return f"0x{v:X}" if v is not None else None


def build_read_only_guard() -> dict:
    """The allow-list the Android read-only layer must enforce verbatim."""
    return {
        "allowed_services": {
            f"0x{sid:02X}": name for sid, name in uds.SERVICES.items()
        },
        "blocked_services": {
            f"0x{sid:02X}": name for sid, name in uds.BLOCKED_SERVICES.items()
        },
        "read_subfunctions": {
            f"0x{sid:02X}": sorted(subs)
            for sid, subs in uds.READ_SUBFUNCTIONS.items()
        },
        "min_request_bytes": {
            f"0x{sid:02X}": n for sid, n in uds.MIN_REQUEST_BYTES.items()
        },
        "max_request_bytes": uds.MAX_REQUEST_BYTES,
        "nrc_names": {f"0x{nrc:02X}": name for nrc, name in uds.NRC.items()},
    }


def build_ecus() -> list[dict]:
    """ECU addressing + transport quirks for every registered ECU."""
    out = []
    for _key, spec in ECUS.items():
        mode = _addr_mode(spec.tx29)
        out.append({
            "key": spec.key,
            "name": spec.name,
            "tx": _hex(spec.tx),
            "rx": _hex(spec.rx),
            "tx29": _hex(spec.tx29),
            "rx29": _hex(spec.rx29),
            "addressing": mode,
            "doc_status": spec.doc_status,
            "notes": spec.notes,
            "transport": _transport_quirks(spec.tx29),
        })
    return out


def _scalar(offset, length, signed, scale, divisor, addend, formula=None) -> dict:
    """A simple-arithmetic decode recipe: value = int * scale / divisor + addend."""
    if formula is None:
        formula = (f"u{length * 8}be[{offset}:{offset + length}] "
                   f"{'signed' if signed else 'unsigned'} x {scale:g}"
                   + (f" / {divisor:g}" if divisor != 1.0 else "")
                   + (f" + {addend:g}" if addend else ""))
    return {
        "kind": "scalar",
        "offset": offset,
        "length": length,
        "byte_order": "big-endian",
        "signed": signed,
        "scale": scale,
        "divisor": divisor,
        "addend": addend,
        "formula": formula,
    }


def _special(name, formula) -> dict:
    """A decoder that is not simple arithmetic; the client implements it by name."""
    return {"kind": "special", "name": name, "formula": formula}


# Module-level decoders that are simple arithmetic, keyed by function __name__.
# (offset, length, signed, scale, divisor, addend). Mirrors decoders/meb.py.
_FUNCTION_RECIPES = {
    "_soc_bms": (0, 1, False, 0.4, 1.0, 0.0),          # raw[0] / 2.5
    "_soc_display": (0, 1, False, 0.4425, 1.0, -6.1947),
    "_pack_voltage": (0, 2, False, 0.25, 1.0, 0.0),    # u16be / 4
    "_pack_current": (0, 4, False, 0.01, 1.0, -1500.0),  # (u32 - 150000) / 100
    "_cell_voltage_scaled": (0, 2, False, 1.0 / 4096, 1.0, 0.0),
    "_battery_temp": (0, 1, False, 0.5, 1.0, -40.0),
    "_temp_scaled": (0, 2, False, 1.0 / 64, 1.0, 0.0),
    "_op_mode": (0, 1, False, 1.0, 1.0, 0.0),
    "_byte": (0, 1, False, 1.0, 1.0, 0.0),
    "_amb_12v": (0, 2, False, 0.001, 1.0, 4.0),
    "_aux_12v_current": (2, 4, True, 0.001, 1.0, 0.0),
    "_outdoor_temp": (0, 1, False, 0.5, 1.0, -50.0),
    "_cabin_temp": (0, 2, False, 0.2, 1.0, -40.0),
    "_energy_max_wh": (0, 2, False, 50.0, 1.0, 0.0),    # u16be[0:2] x 50 Wh
    "_energy_content_wh": (2, 2, False, 25.0, 1.0, 0.0),  # u16be[2:4] x 25 Wh
    "_odometer_km": (0, 3, False, 1.0, 1.0, 0.0),       # u24be
}

# Module-level decoders that need client logic, keyed by __name__ -> (name, formula).
_SPECIAL_DECODERS = {
    "_cell_voltage": ("cell_voltage", "u16be/1000+1; 0x0FFE = sensor absent"),
    "_cell_temp": ("cell_temp", "u16be/8-40; >1020 = sensor absent"),
    "_energy_counters": ("energy_counters",
                         "u32 charge @0, s32 discharge @4, /8583.07 Wh"),
    "_charge_mode": ("charge_mode", "byte0: {4:1, 6:2}.get(raw[0], 0)"),
    "_coolant_temps": ("coolant_temps", "outlet u16[0:2]/64, inlet u16[2:4]/64"),
    "_gear": ("gear", "gear code from data byte 1"),
    "_vin": ("vin", "ASCII"),
}

# Lambdas (u16be/div and u8/div) keyed by DID key -> (offset, length, signed,
# scale, divisor, addend). Mirrors decoders/meb.py.
_LAMBDA_RECIPES = {
    "dyn_charge_limit": (0, 2, False, 0.2, 1.0, 0.0),       # u16 / 5
    "dyn_discharge_limit": (0, 2, False, 0.2, 1.0, 0.0),    # u16 / 5
    "ptc_current": (0, 1, False, 0.25, 1.0, 0.0),           # u8 / 4
    "dcdc_current": (0, 2, False, 1.0 / 16, 1.0, 0.0),      # u16 / 16
    "dcdc_voltage": (0, 2, False, 1.0 / 512, 1.0, 0.0),     # u16 / 512
    "hv_aux_power": (0, 2, False, 0.1, 1.0, 0.0),           # u16 / 10
}


def _closure_args(decode):
    """Captured variables of a factory-produced decode closure, or None."""
    try:
        names = decode.__code__.co_freevars
        cells = decode.__closure__ or []
    except (AttributeError, ValueError):
        return None
    return dict(zip(names, [c.cell_contents for c in cells]))


def _decode_recipe(key: str, decode) -> dict | None:
    """Recipe for one DID's live decoder, or None for raw-only.

    Handles, in order: the Car Scanner field closures (_cs_field_decoder /
    _cs_pair_decoder, which cover most of the MEB profile), the module-level
    arithmetic decoders, and the lambdas. Decoders that need real client logic
    (dict results, string decodes, guarded lookups) come out as "special" so the
    client implements them by name instead of guessing a formula.
    """
    if decode is None:
        return None
    args = _closure_args(decode)
    if args is not None:
        if "sbi" in args:  # _cs_field_decoder: sbi, dl, mul, div, ofs, sig
            return _scalar(args.get("sbi", 0), args["dl"], args.get("sig", False),
                           args.get("mul", 1.0), args.get("div", 1.0),
                           args.get("ofs", 0.0))
        if "first_mul" in args:  # _cs_pair_decoder: two values in one DID
            return _special("pair", "scaled value @0 + raw index @dl")
    name = getattr(decode, "__name__", "")
    if name in _FUNCTION_RECIPES:
        off, ln, signed, sc, dv, ad = _FUNCTION_RECIPES[name]
        return _scalar(off, ln, signed, sc, dv, ad)
    if name in _SPECIAL_DECODERS:
        sn, formula = _SPECIAL_DECODERS[name]
        return _special(sn, formula)
    if key in _LAMBDA_RECIPES:
        off, ln, signed, sc, dv, ad = _LAMBDA_RECIPES[key]
        return _scalar(off, ln, signed, sc, dv, ad)
    return None  # raw-only: an unmapped decoder preserves bytes, decodes later


def _measurements(profile: str) -> list[dict]:
    """DID map + decode recipes for one profile, flattened for a client."""
    from decoders.catalogue import catalogue_rows
    from decoders.registry import build_default_registry

    registry = build_default_registry(profile)
    slow = {s.key: s.slow for s in registry.all()}
    decode_fn = {s.key: s.decode for s in registry.all()}
    rows = catalogue_rows(profile)
    out = []
    for row in rows:
        key = row["key"]
        out.append({
            "key": key,
            "name": row["name"],
            "unit": row["unit"],
            "ecu_key": row["ecu_key"],
            "did": row["did"],
            "service": row["service"],
            "request_bytes": row["request_bytes"],
            "slow": slow.get(key, False),
            "provenance": row["provenance"],
            "doc_status": row["doc_status"],
            "confidence": row["confidence"],
            "decode": _decode_recipe(key, decode_fn.get(key)),
        })
    return out


def _cell_voltages(profile: str) -> dict | None:
    """The per-cell sweep parameters, or None when the profile has none."""
    from decoders.meb import CELL_UNUSED, CELL_V_BASE, CELL_V_SLOTS

    # The cell sweep is a bat_mgmt read of CELL_V_BASE .. + CELL_V_SLOTS-1.
    # Each slot decodes as u16be / 1000 + 1, and the unused marker is the
    # decoder's "sensor absent" signal, not a reading.
    if profile != "meb":
        return None
    return {
        "ecu_key": "bat_mgmt",
        "did_base": _hex(CELL_V_BASE),
        "slots": CELL_V_SLOTS,
        "unused_marker": _hex(CELL_UNUSED),
        "decode": {
            "offset": 0, "length": 2, "byte_order": "big-endian",
            "signed": False, "scale": 1.0, "divisor": 1000.0, "addend": 1.0,
            "formula": "u16be / 1000 + 1 (0x0FFE = sensor absent)",
        },
    }


def build_spec() -> dict:
    from decoders.catalogue import CATALOGUE_VERSION

    return {
        "spec_version": SPEC_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "read_only_guard": build_read_only_guard(),
        "ecus": build_ecus(),
        "profiles": {
            "meb": {
                "measurements": _measurements("meb"),
                "cell_voltages": _cell_voltages("meb"),
            },
            "eup": {
                "measurements": _measurements("eup"),
                "cell_voltages": None,
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the shared diagnostic spec as JSON")
    parser.add_argument("--out", default="spec/odb_scanner.json",
                        help="destination file (default spec/odb_scanner.json)")
    parser.add_argument("--profile", default=None,
                        help="restrict to one profile (meb|eup) for a quick look")
    args = parser.parse_args(argv)

    spec = build_spec()
    if args.profile:
        spec["profiles"] = {args.profile: spec["profiles"][args.profile]}

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, sort_keys=True)
        fh.write("\n")

    nm = len(spec["profiles"]["meb"]["measurements"])
    ne = len(spec["profiles"]["eup"]["measurements"])
    print(f"Wrote {out}")
    print(f"  ecus: {len(spec['ecus'])}")
    print(f"  meb measurements: {nm}, eup measurements: {ne}")
    print(f"  read-only services: {len(spec['read_only_guard']['allowed_services'])} "
          f"allowed, {len(spec['read_only_guard']['blocked_services'])} blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
