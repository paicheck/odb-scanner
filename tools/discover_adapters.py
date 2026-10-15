"""Discover and classify OBD adapters attached to this machine.

Read-only and vehicle-inert by default: it enumerates the serial interfaces the
OS reports, classifies each from its real metadata (VID/PID, reported product
strings), and says how far the evidence goes -- detected, identified, transport
supported. It opens nothing and sends nothing unless --verify is passed.

    python tools/discover_adapters.py              # enumerate + classify
    python tools/discover_adapters.py --json       # machine-readable
    python tools/discover_adapters.py --verify     # also open + ATI probe

WHAT EACH COLUMN MEANS
    class       what the metadata says the device is
    evidence    how far discovery got: detected < identified <
                transport_supported < connected < verified
    transport   the transport that could drive it, or "-" for vendor-specific

A device is never called an OBD adapter just because a COM port exists. A
generic USB-serial chip (CH340/CP210x/FTDI/PL2303) is reported as
usb_serial_other: it can carry bytes, but OBD capability is UNVERIFIED until an
ATI probe answers. VID 0403 is FTDI, a chip vendor shared by thousands of
products -- it is never reported as "Ross-Tech" or "ELM327" on its own.

--verify opens each candidate port and sends ATI (and nothing else). ATI is an
adapter-local identification command; it reaches no ECU and puts no diagnostic
frame on the bus, so verification cannot alter the vehicle. It is the step that
turns "identified" into "verified".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from diagnostic.discovery import (  # noqa: E402
    AmbiguousAdapterError,
    DeviceClass,
    Evidence,
    UnsupportedAdapterError,
    classify_device,
    discover_serial_devices,
    select_device,
    verify_adapter,
)
from diagnostic.interface import (  # noqa: E402
    AdapterNotFoundError,
    CommunicationError,
)


def _verify_one(device, baudrate: int, timeout: float) -> tuple[str, str]:
    """Open one device and ATI-probe it. Returns (evidence, detail).

    Never raises: a device that will not open or will not answer is a result to
    report, not a crash. The port is always closed again so a probe cannot leak
    a handle and make the next attempt fail with "access denied".
    """
    from diagnostic.elm327 import Elm327Transport

    transport = Elm327Transport(port=device.port, baudrate=baudrate,
                                timeout=timeout)
    try:
        identity = verify_adapter(transport, timeout=timeout)
        return Evidence.VERIFIED.value, f"ATI -> {identity!r}"
    except AdapterNotFoundError as exc:
        return Evidence.IDENTIFIED.value, f"would not open: {exc}"
    except CommunicationError as exc:
        return Evidence.CONNECTED.value, f"opened, no ELM327 answer: {exc}"
    except Exception as exc:  # noqa: BLE001 - report, do not crash the scan
        return Evidence.DETECTED.value, f"probe error: {type(exc).__name__}: {exc}"
    finally:
        try:
            transport.close()
        except Exception:  # pragma: no cover - best effort
            pass


def build_report(*, verify: bool = False, baudrate: int = 38400,
                 timeout: float = 2.0) -> dict:
    """Enumerate, classify and (optionally) verify every serial device."""
    devices = discover_serial_devices()
    entries = []
    for device in devices:
        cls = classify_device(device)
        evidence = cls.evidence.value
        detail = cls.reason
        if verify and cls.device_class != DeviceClass.VENDOR_SPECIFIC:
            evidence, detail = _verify_one(device, baudrate, timeout)
        elif verify:
            detail = ("not probed: vendor-specific interface, no ELM327 "
                      "transport")
        entries.append({
            "port": device.port,
            "class": cls.device_class.value,
            "evidence": evidence,
            "transport": cls.transport or "-",
            "vid": device.vid,
            "pid": device.pid,
            "serial_number": device.serial_number,
            "description": device.description,
            "chip": cls.chip,
            "reason": cls.reason,
            "detail": detail,
            "auto_select_candidate": cls.usable_as_elm327,
            "notes": cls.notes,
        })

    # What selection would do, without actually connecting.
    selection: dict = {"status": "ok"}
    try:
        chosen = select_device(devices)
        selection = {"status": "ok", "port": chosen.device.port,
                     "class": chosen.device_class.value,
                     "reason": chosen.reason}
    except AmbiguousAdapterError as exc:
        selection = {"status": "ambiguous",
                     "candidates": [c.device.port for c in exc.candidates],
                     "message": str(exc)}
    except UnsupportedAdapterError as exc:
        selection = {"status": "unsupported", "message": str(exc)}
    except AdapterNotFoundError as exc:
        selection = {"status": "none", "message": str(exc)}

    return {"devices": entries, "selection": selection, "verified": verify}


def render_text(report: dict) -> str:
    lines = []
    devices = report["devices"]
    if not devices:
        lines.append("No serial interfaces found.")
        lines.append("On Windows: Device Manager > Ports (COM & LPT). An "
                     "unpaired Bluetooth adapter does not appear here, and a "
                     "USB adapter with no driver shows under 'Other devices' "
                     "with a warning icon.")
        return "\n".join(lines)

    lines.append(f"Found {len(devices)} serial interface(s):")
    lines.append("")
    for d in devices:
        vidpid = (f"  VID:PID={d['vid']:04X}:{d['pid']:04X}"
                  if d["vid"] is not None and d["pid"] is not None else "")
        cand = "  [auto-select candidate]" if d["auto_select_candidate"] else ""
        lines.append(f"  {d['port']:<8} [{d['class']}]{vidpid}{cand}")
        lines.append(f"           {d['description']}")
        lines.append(f"           evidence={d['evidence']}  transport="
                     f"{d['transport']}")
        lines.append(f"           {d['detail']}")
        if d.get("chip"):
            lines.append(f"           chip: {d['chip']}")
        for note in d["notes"]:
            lines.append(f"           - {note}")
        lines.append("")

    sel = report["selection"]
    lines.append("Selection (what a connection attempt would use):")
    if sel["status"] == "ok":
        lines.append(f"  -> {sel['port']} [{sel['class']}]: {sel['reason']}")
    elif sel["status"] == "ambiguous":
        lines.append("  AMBIGUOUS: multiple plausible adapters, none "
                     "configured. Refusing to guess.")
        lines.append("  Candidates: " + ", ".join(sel["candidates"]))
        lines.append("  Set adapter.port in config.yaml (or config.local.yaml) "
                     "to the one you want.")
    elif sel["status"] == "unsupported":
        lines.append("  UNSUPPORTED: " + sel["message"])
    else:
        lines.append("  NONE: " + sel["message"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Discover and classify OBD adapters. Read-only; opens "
                    "nothing unless --verify is given.")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    ap.add_argument("--verify", action="store_true",
                    help="open each candidate and send ATI to confirm it is an "
                         "ELM327-class adapter (adapter-local; reaches no ECU)")
    ap.add_argument("--baudrate", type=int, default=38400)
    ap.add_argument("--timeout", type=float, default=2.0,
                    help="per-port probe timeout in seconds (default 2)")
    args = ap.parse_args(argv)

    report = build_report(verify=args.verify, baudrate=args.baudrate,
                          timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
