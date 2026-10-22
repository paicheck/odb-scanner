#!/usr/bin/env python3
"""Approval-gated, read-only ABS wheel-speed reader for the VW ID.3.

The four Car Scanner [03.ABS] DIDs (0x1800-0x1803) answer on 0x713 -> 0x77D
only in the extended diagnostic session. That session changes ECU behaviour,
so the normal UDS allow-list deliberately refuses 0x10 0x03. This tool is the
one narrow exception:

  * without ``--execute`` it only prints the plan;
  * with ``--execute`` it additionally requires ``--approve-session-change``;
  * the session change is exactly 0x10 0x03, the reads are exactly 0x22, and
    the tool always attempts 0x10 0x01 again in a ``finally`` block;
  * every TX/RX pair is kept in the report the tool prints/writes, and is also
    passed to any ``tx_logger`` the connection already has. The tool itself
    does not open or rewrite ``data/odb_scanner.db``.

It never writes a DID, runs a routine, clears a fault, or performs an actuator
test. The raw 12-byte block is preserved; the profile's first two bytes are
decoded as 0.01 km/h/LSB.

Usage::

    python tools/read_abs_wheel_speeds.py --dry-run
    python tools/read_abs_wheel_speeds.py --execute --approve-session-change \
        --port COM3

Do not run the live form until the session change has been explicitly
approved for the car and the operator is prepared to return the ABS module to
the default session.
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
from decoders.meb import register_abs
from decoders.registry import DIDRegistry, build_default_registry
from diagnostic.connection import DiagnosticConnection
from diagnostic.ecus import ECUS
from diagnostic.elm327 import Elm327Transport
from diagnostic.uds import (
    ABS_APPROVAL_TOKEN,
    CommunicationError,
    NegativeResponseError,
    ProtocolError,
    ReadOnlyViolationError,
)

ABS = dataclasses.replace(
    ECUS["brk"],
    tx29=0x00000713,
    rx29=0x0000077D,
    doc_status="experimentally determined (Car Scanner [03.ABS] + passive HCI)",
    notes="0x713 -> 0x77D. 0x1800-0x1803 need the extended diagnostic session.",
)

ABS_DIDS: tuple[tuple[int, str, str], ...] = (
    (0x1800, "wheel_speed_front_left", "Front left wheel speed"),
    (0x1801, "wheel_speed_front_right", "Front right wheel speed"),
    (0x1802, "wheel_speed_rear_left", "Rear left wheel speed"),
    (0x1803, "wheel_speed_rear_right", "Rear right wheel speed"),
)


def build_plan(only: set[int] | None = None) -> list[dict]:
    """The exact live sequence. Used by --dry-run and tests."""
    plan = [
        {
            "step": "session",
            "request": "1003",
            "label": "extended diagnostic session (explicit approval required)",
        }
    ]
    for did, key, label in ABS_DIDS:
        if only and did not in only:
            continue
        plan.append(
            {
                "step": "read",
                "did": f"0x{did:04X}",
                "request": f"22{did:04X}",
                "key": key,
                "label": label,
            }
        )
    plan.append(
        {
            "step": "session",
            "request": "1001",
            "label": "return to default session (always attempted)",
        }
    )
    return plan


def _registry() -> DIDRegistry:
    registry = build_default_registry("meb")
    register_abs(registry)
    return registry


def read_wheels(conn: DiagnosticConnection, approval: str,
                only: set[int] | None = None) -> dict:
    """Perform the approved session + four reads + default-session return."""
    if approval != ABS_APPROVAL_TOKEN:
        raise ReadOnlyViolationError(
            "Refusing the ABS session change: the approval token is missing "
            "or wrong. No adapter access was attempted."
        )

    registry = _registry()
    events: list[dict] = []
    prior_logger = conn.tx_logger

    def capture_event(**event) -> None:
        events.append(event)
        prior_logger(**event)

    conn.tx_logger = capture_event
    result: dict = {
        "approval_used": True,
        "session": None,
        "reads": [],
        "returned_to_default": False,
        "session_error": None,
        "default_session_error": None,
        "tx": [],
        "rx": [],
    }

    def snapshot() -> None:
        result["tx"] = [
            e["payload"] for e in events if e.get("direction") == "TX"
        ]
        result["rx"] = [
            e["payload"] for e in events if e.get("direction") == "RX"
        ]

    try:
        try:
            session = conn.approved_extended_session(
                ABS, approval=approval,
                purpose="ABS wheel-speed read: approved extended session",
            )
            result["session"] = session.hex().upper()
        except (NegativeResponseError, ProtocolError, CommunicationError) as exc:
            result["session_error"] = str(exc)
            return result

        for did, key, label in ABS_DIDS:
            if only and did not in only:
                continue
            read_start = len(events)
            entry: dict = {
                "did": f"0x{did:04X}", "key": key, "label": label,
                "request": f"22{did:04X}", "record_hex": None,
                "payload_len": None, "value": None, "unit": "km/h",
                "error": None, "tx": [], "rx": [],
            }
            try:
                record = conn.read_did(ABS, did)
            except NegativeResponseError as exc:
                entry["error"] = f"NRC 0x{exc.nrc:02X}"
            except ProtocolError as exc:
                entry["error"] = f"protocol: {exc}"
            except CommunicationError as exc:
                entry["error"] = f"no response: {exc}"
            else:
                entry["record_hex"] = record.hex().upper()
                entry["payload_len"] = len(record)
                entry["value"] = registry.get(key).decode_value(record)
            entry["tx"] = [
                e["payload"] for e in events[read_start:]
                if e.get("direction") == "TX"
            ]
            entry["rx"] = [
                e["payload"] for e in events[read_start:]
                if e.get("direction") == "RX"
            ]
            result["reads"].append(entry)
    finally:
        # The session change was attempted; even if its positive response was
        # lost, try to put the ABS module back into the default session.
        try:
            conn.default_session(
                ABS, purpose="ABS wheel-speed read: return to default session"
            )
            result["returned_to_default"] = True
        except (NegativeResponseError, ProtocolError, CommunicationError) as exc:
            result["default_session_error"] = str(exc)
        snapshot()
        conn.tx_logger = prior_logger

    return result


def print_report(result: dict, header: str) -> None:
    print("=" * 72)
    print("ABS wheel speeds - approval-gated UDS 0x22 - 0x713 -> 0x77D")
    print(header)
    print("=" * 72)
    if result["session"]:
        print(f"SESSION  positive {result['session']}")
    elif result["session_error"]:
        print(f"SESSION  ERROR {result['session_error']}")
    for entry in result["reads"]:
        print(f"\n{entry['did']}  {entry['label']}")
        for line in entry["tx"]:
            print(f"  TX {line}")
        for line in entry["rx"]:
            print(f"  RX {line}")
        if entry["error"]:
            print(f"  ERROR {entry['error']}")
        else:
            print(f"  PAYLOAD {entry['record_hex']} ({entry['payload_len']} bytes)")
            unit = f" {entry['unit']}" if entry["unit"] else ""
            if entry["value"] is None:
                print(f"    {entry['key']}: <no value>{unit}")
            else:
                print(f"    {entry['key']}: {entry['value']}{unit}")
    print(
        f"\nReturned to default session: "
        f"{'YES' if result['returned_to_default'] else 'NO'}"
    )
    if result["default_session_error"]:
        print(f"  WARNING {result['default_session_error']}")


def _parse_only(text: str | None) -> set[int] | None:
    if not text:
        return None
    allowed = {did for did, _, _ in ABS_DIDS}
    wanted: set[int] = set()
    for chunk in text.split(","):
        token = chunk.strip().lower().removeprefix("0x")
        if not token:
            continue
        try:
            did = int(token, 16)
        except ValueError:
            raise SystemExit(f"--only: {chunk!r} is not a hex DID") from None
        if did not in allowed:
            raise SystemExit(
                f"--only: 0x{did:04X} is not an ABS wheel-speed DID "
                f"({', '.join(f'{d:04X}' for d in sorted(allowed))})"
            )
        wanted.add(did)
    return wanted or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Approval-gated read-only ABS wheel-speed reader."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and do not open an adapter")
    parser.add_argument("--execute", action="store_true",
                        help="actually open the adapter (requires the opt-in)")
    parser.add_argument(
        "--approve-session-change",
        action="store_true",
        help="explicitly approve the one 0x10 0x03 extended-session request",
    )
    parser.add_argument("--only", help="comma-separated ABS DIDs, e.g. 1800,1801")
    parser.add_argument("--port", help="serial port (default: config adapter.port)")
    parser.add_argument("--baudrate", type=int, help="serial baud rate")
    parser.add_argument("--timeout", type=float, help="adapter timeout in seconds")
    parser.add_argument(
        "--tcp", nargs=2, metavar=("HOST", "PORT"),
        help="talk to an ELM327-over-TCP endpoint (the simulator)",
    )
    parser.add_argument("--out", help="JSON evidence file")
    parser.add_argument("--no-write", action="store_true",
                        help="do not write a JSON evidence file")
    args = parser.parse_args(argv)

    only = _parse_only(args.only)
    header = (
        f"Profile: meb | {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )

    if not args.execute:
        print("=" * 72)
        print("ABS wheel-speed plan (dry run - no adapter access)")
        print(header)
        print("=" * 72)
        for item in build_plan(only):
            if item["step"] == "session":
                print(f"{item['request']}  {item['label']}")
            else:
                print(f"{item['did']}  {item['request']}  {item['label']}")
        print("\nLive use requires --execute --approve-session-change.")
        print("The session change is state-changing; do not run it without approval.")
        return 0

    if not args.approve_session_change:
        print(
            "REFUSED: --execute requires --approve-session-change. "
            "No adapter access attempted.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config()
    timeout = (
        args.timeout if args.timeout is not None
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
    except Exception as exc:
        print(f"ERROR: could not open the adapter: {exc}")
        return 1

    try:
        result = read_wheels(conn, ABS_APPROVAL_TOKEN, only)
        adapter = transport.identity
    finally:
        conn.close()

    print_report(result, header)

    if not args.no_write:
        if args.out:
            out = Path(args.out)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = ROOT / "data" / "logs" / f"abs_wheel_speeds_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "tool": "tools/read_abs_wheel_speeds.py",
            "profile": "meb",
            "abs": {
                "request_id": "0x713",
                "response_id": "0x77D",
                "session_change": "1003",
                "source": "Car Scanner [03.ABS] + 2026-10-15 passive HCI",
            },
            "started_utc": header,
            "adapter": adapter,
            "result": result,
        }
        out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence written to {out}")

    if result["session_error"] or result["default_session_error"]:
        return 2
    return 0 if all(entry["error"] is None for entry in result["reads"]) else 2


if __name__ == "__main__":
    sys.exit(main())
