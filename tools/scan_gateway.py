#!/usr/bin/env python3
"""Read-only scan of the [19.Gate] gateway's Car Scanner DIDs.

The central gateway (J533, VAG address 19) serves the Car Scanner
"[19.Gate]" measurements on the 11-bit path 0x710 -> 0x77A. This tool reads
exactly the nine DIDs that profile lists for that module, prints the raw
response and the known decoders' values, and optionally writes a JSON
evidence file.

Safety:
  * only UDS 0x22 ReadDataByIdentifier goes on the wire; every request
    passes diagnostic.uds.validate_request() inside DiagnosticConnection
  * no session change, no writes, no routines, no DTC clearing, no security
    access
  * the DID list is an allow-list recovered from the Car Scanner MEB profile
    (idx 342); there is no sweep and no blind probing
  * the production database is not opened or modified

Usage:
    python tools/scan_gateway.py --dry-run
    python tools/scan_gateway.py
    python tools/scan_gateway.py --only 2AB8,2AF7
    python tools/scan_gateway.py --tcp 127.0.0.1 35000 --no-write

The collector must not be running: it owns the serial port.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config
from decoders.registry import DIDRegistry, build_default_registry
from diagnostic.connection import DiagnosticConnection
from diagnostic.ecus import ECUS
from diagnostic.elm327 import Elm327Transport
from diagnostic.uds import (
    CommunicationError,
    NegativeResponseError,
    ProtocolError,
)

# The gateway's own ECU record already carries the confirmed 0x710 -> 0x77A
# pair, but no MEB physical-addressing fields; without tx29/rx29 the
# DiagnosticConnection would send the request functionally. Add them for this
# scan only, leaving diagnostic/ecus.py unchanged.
GATEWAY = dataclasses.replace(
    ECUS["gate"],
    tx29=0x00000710,
    rx29=0x0000077A,
)

# [19.Gate] DIDs, in profile order, from
# docs/investigation/live_measurement_evidence.csv (Car Scanner MEB profile
# idx 342). 0x2A26/0x2A28/0x2A2F are deliberately absent: they have no
# payload/scaling evidence and must not be probed.
GATEWAY_DIDS: tuple[tuple[int, str], ...] = (
    (0x2AB2, "Maximum energy content of the traction battery"),
    (0x2AB4, "Estimated electric power reserve (internal) / consumption"),
    (0x2AB5, "Estimated electric power reserve (CAN)"),
    (0x2AB6, "Estimated electric power reserve (displayed)"),
    (0x2AB7, "Average energy consumption by road category"),
    (0x2AB8, "HV battery energy content"),
    (0x2A53, "DC-DC converter low voltage / current"),
    (0x2AF7, "12 V battery block"),
    (0x15D1, "HV-EM power limitation / NV energy"),
)

ALLOWED_DIDS = frozenset(did for did, _ in GATEWAY_DIDS)
QUARANTINED_DIDS = frozenset({0x2A26, 0x2A28, 0x2A2F})


def parse_only(text: str | None) -> set[int] | None:
    """Parse a --only list, refusing anything outside the allow-list."""
    if not text:
        return None
    wanted: set[int] = set()
    for chunk in text.split(","):
        token = chunk.strip().lower().removeprefix("0x")
        if not token:
            continue
        try:
            did = int(token, 16)
        except ValueError:
            raise SystemExit(f"--only: {chunk!r} is not a hex DID") from None
        if did not in ALLOWED_DIDS:
            raise SystemExit(
                f"--only: 0x{did:04X} is not on the [19.Gate] allow-list "
                f"({', '.join(f'{d:04X}' for d in sorted(ALLOWED_DIDS))})"
            )
        wanted.add(did)
    return wanted or None


def specs_by_did(registry: DIDRegistry) -> dict[int, list]:
    """Registry specs whose DID is on the gateway allow-list, grouped by DID."""
    grouped: dict[int, list] = {}
    for spec in registry.all():
        if spec.did in ALLOWED_DIDS:
            grouped.setdefault(spec.did, []).append(spec)
    return grouped


def build_plan(registry: DIDRegistry, only: set[int] | None = None) -> list[dict]:
    """The exact reads this tool would perform. Used by --dry-run and tests."""
    grouped = specs_by_did(registry)
    plan: list[dict] = []
    for did, label in GATEWAY_DIDS:
        if only and did not in only:
            continue
        plan.append(
            {
                "did": f"0x{did:04X}",
                "request": f"22{did:04X}",
                "label": label,
                "decoders": [spec.key for spec in grouped.get(did, [])],
            }
        )
    return plan


def scan(
    conn: DiagnosticConnection,
    registry: DIDRegistry,
    only: set[int] | None = None,
) -> list[dict]:
    """Read the allow-listed gateway DIDs. Returns one evidence dict per DID."""
    grouped = specs_by_did(registry)
    events: list[dict] = []
    conn.tx_logger = lambda **event: events.append(event)
    results: list[dict] = []
    for did, label in GATEWAY_DIDS:
        if only and did not in only:
            continue
        start = len(events)
        entry: dict = {
            "did": f"0x{did:04X}",
            "label": label,
            "request": f"22{did:04X}",
            "record_hex": None,
            "payload_len": None,
            "decoded": [],
            "tx": [],
            "rx": [],
            "error": None,
        }
        try:
            record = conn.read_did(GATEWAY, did)
        except NegativeResponseError as exc:
            entry["error"] = f"NRC 0x{exc.nrc:02X}"
        except ProtocolError as exc:
            entry["error"] = f"protocol: {exc}"
        except CommunicationError as exc:
            entry["error"] = f"no response: {exc}"
        else:
            entry["record_hex"] = record.hex().upper()
            entry["payload_len"] = len(record)
            for spec in grouped.get(did, []):
                entry["decoded"].append(
                    {
                        "key": spec.key,
                        "name": spec.name,
                        "unit": spec.unit,
                        "value": spec.decode_value(record),
                    }
                )
        entry["tx"] = [
            e["payload"] for e in events[start:] if e.get("direction") == "TX"
        ]
        entry["rx"] = [
            e["payload"] for e in events[start:] if e.get("direction") == "RX"
        ]
        results.append(entry)
    return results


def print_report(results: list[dict], header: str) -> None:
    print("=" * 72)
    print("Gateway [19.Gate] scan - UDS 0x22 only - 0x710 -> 0x77A")
    print(header)
    print("=" * 72)
    for entry in results:
        print(f"\n{entry['did']}  {entry['label']}")
        for line in entry["tx"]:
            print(f"  TX {line}")
        for line in entry["rx"]:
            print(f"  RX {line}")
        if entry["error"]:
            print(f"  ERROR {entry['error']}")
            continue
        print(f"  PAYLOAD {entry['record_hex']} ({entry['payload_len']} bytes)")
        if not entry["decoded"]:
            print("  (no registry decoder for this DID)")
        for item in entry["decoded"]:
            value = item["value"]
            if value is None:
                print(f"    {item['key']}: <no value>")
            else:
                suffix = f" {item['unit']}" if item["unit"] else ""
                print(f"    {item['key']}: {value}{suffix}")
    answered = sum(1 for entry in results if entry["error"] is None)
    print(f"\n{answered}/{len(results)} DIDs answered positively.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only scan of the [19.Gate] gateway DIDs (0x22 only)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit without touching the adapter",
    )
    parser.add_argument("--only", help="comma-separated hex DIDs, e.g. 2AB8,2AF7")
    parser.add_argument("--port", help="serial port (default: config adapter.port)")
    parser.add_argument("--baudrate", type=int, help="serial baud rate")
    parser.add_argument("--timeout", type=float, help="adapter timeout in seconds")
    parser.add_argument(
        "--tcp",
        nargs=2,
        metavar=("HOST", "PORT"),
        help="talk to an ELM327-over-TCP endpoint (the simulator)",
    )
    parser.add_argument(
        "--out",
        help="JSON evidence file (default: data/logs/gateway_scan_<UTC>.json)",
    )
    parser.add_argument(
        "--no-write", action="store_true", help="do not write a JSON evidence file"
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    only = parse_only(args.only)
    registry = build_default_registry("meb")
    header = (
        "Profile: meb | "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )

    if args.dry_run:
        print("=" * 72)
        print("Gateway [19.Gate] scan plan (dry run - no adapter access)")
        print(header)
        print("=" * 72)
        for item in build_plan(registry, only):
            print(f"{item['did']}  {item['request']}  {item['label']}")
            decoders = ", ".join(item["decoders"]) or "<raw only>"
            print(f"    decoders: {decoders}")
        return 0

    timeout = (
        args.timeout
        if args.timeout is not None
        else float(cfg.get("adapter.timeout", 5.0))
    )
    if args.tcp:
        host, port_text = args.tcp
        transport: Elm327Transport = Elm327Transport(
            host=host, tcp_port=int(port_text), timeout=timeout
        )
        target = f"tcp {host}:{port_text}"
    else:
        port = args.port or cfg.get("adapter.port", "COM3")
        baudrate = args.baudrate or int(cfg.get("adapter.baudrate", 38400))
        transport = Elm327Transport(
            port=port, baudrate=baudrate, timeout=timeout
        )
        target = f"{port} @ {baudrate}"

    conn = DiagnosticConnection(transport)
    print(f"Opening {transport.name} on {target} ...")
    try:
        conn.open()
    except Exception as exc:  # adapter absent, vehicle asleep, wrong port
        print(f"ERROR: could not open the adapter: {exc}")
        return 1

    try:
        results = scan(conn, registry, only)
        adapter = transport.identity
    finally:
        conn.close()

    print_report(results, header)

    if not args.no_write:
        if args.out:
            out = Path(args.out)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = ROOT / "data" / "logs" / f"gateway_scan_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "tool": "tools/scan_gateway.py",
            "profile": "meb",
            "gateway": {
                "request_id": "0x710",
                "response_id": "0x77A",
                "source": "Car Scanner MEB profile idx 342",
            },
            "started_utc": header,
            "adapter": adapter,
            "results": results,
        }
        out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence written to {out}")

    return 0 if all(entry["error"] is None for entry in results) else 2


if __name__ == "__main__":
    sys.exit(main())
