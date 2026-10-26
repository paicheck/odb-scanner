"""Interactive ELM327 console for live diagnostics at the vehicle.

READ-ONLY BY DEFAULT. Adapter commands (AT..., and ST... on OBDLink) configure
the adapter itself and are sent as typed; every other line is checked by
diagnostic/uds.validate_request -- the same allow-list the collector uses -- so
a stray 2E (write data), 31 (routine control), 1002 (programming session) or
14 (clear DTCs) is refused instead of reaching the vehicle.

*** --raw-unsafe TURNS THAT OFF. ***

With --raw-unsafe whatever you type is sent as typed, with no allow-list. That
mode exists for discovery work where a read is not enough, it says so on every
prompt, and it is the one place the read-only guarantee in README.md does not
extend to. Prefer `main.py discover`, `main.py doctor` and `main.py guard-test`.

Uses the same transport and init sequence as main.py, but every command is
typed by hand and responses are printed raw. Built for the "discovery
answers NO DATA" situation:

    python tools/elm_console.py                          # configured adapter
    python tools/elm_console.py --tcp 127.0.0.1 35000    # simulator
    python tools/elm_console.py --no-init                # skip AT init
    python tools/elm_console.py --raw-unsafe             # NO allow-list

Useful commands:
    ATI      adapter identity          0100   CAN bus warm-up: does anything answer?
    ATSP0    auto protocol (retry if fixed ATSP6 seems wrong)
    0902     read VIN                  03     read DTCs
    ATSH7E5  address a specific ECU, then send UDS reads (e.g. 22xxxx)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from diagnostic.elm327 import Elm327Transport  # noqa: E402
from diagnostic.interface import CommunicationError  # noqa: E402
from diagnostic.uds import ReadOnlyViolationError, validate_request  # noqa: E402

HELP_TEXT = """Read-only: AT/ST adapter commands are sent as typed; vehicle requests must
pass the project's read-only allow-list (0100, 0902, 03, 22xxxx, 1001, 19xx,
3E00, ...). Start with --raw-unsafe to send anything, writes included.

commands:
  <ELM327 or UDS command>    e.g. 0100, 0902, 03, ATSP0, ATSH7E5, 22F190
  info                       show adapter identity
  help                       show this help
  q / quit / exit            leave the console
"""

RAW_HELP_TEXT = """RAW-UNSAFE MODE. Whatever you type is sent as typed, with no
allow-list -- so 2E, 31, 1002 and 14 will reach the vehicle if you enter them.

commands:
  <any ELM327/UDS command>   sent as typed (0100, 0902, 03, ATSP0, ATSH7E5, ...)
  info                       show adapter identity
  help                       show this help
  q / quit / exit            leave the console
"""


def is_adapter_command(cmd: str) -> bool:
    """AT (ELM327) and ST (OBDLink) lines configure the adapter, not the car."""
    return cmd.strip()[:2].upper() in ("AT", "ST")


def refusal(cmd: str, raw_unsafe: bool) -> str | None:
    """Why `cmd` must not be sent, or None when it may be.

    Vehicle requests go through the same validate_request the collector's
    _transmit uses, so the console refuses exactly what the rest of the
    project refuses.
    """
    if raw_unsafe or is_adapter_command(cmd):
        return None
    try:
        validate_request(cmd)
    except ReadOnlyViolationError as exc:
        return str(exc)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None, help="alternative config file")
    ap.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"),
                    help="talk to a TCP adapter instead (e.g. the simulator)")
    ap.add_argument("--port", help="serial port override (e.g. COM3)")
    ap.add_argument("--no-init", action="store_true",
                    help="skip the standard AT init sequence")
    ap.add_argument("--raw-unsafe", action="store_true",
                    help="send vehicle requests with NO read-only allow-list "
                         "(writes, session changes and DTC clears included)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.tcp:
        transport = Elm327Transport(
            port="auto", host=args.tcp[0], tcp_port=int(args.tcp[1]),
            timeout=float(cfg.get("adapter.timeout", 5.0)))
    else:
        transport = Elm327Transport(
            port=args.port or cfg.get("adapter.port", "auto"),
            baudrate=int(cfg.get("adapter.baudrate", 38400)),
            timeout=float(cfg.get("adapter.timeout", 5.0)))

    try:
        transport.open()
        if not args.no_init:
            transport.initialize()
    except CommunicationError as exc:
        print(f"Cannot open adapter: {exc}")
        return 1

    print(f"Connected: {transport.description()} — 'help' for help, 'q' to quit")
    # Printed on connect, not only in --help: the person most likely to type a
    # write service by accident is the one who did not read the docstring.
    if args.raw_unsafe:
        print("RAW-UNSAFE: NOT READ-ONLY. What you type is sent to the vehicle "
              "as typed.")
    else:
        print("Read-only: vehicle requests must pass the allow-list "
              "(--raw-unsafe disables it).")
    prompt = "elm(RAW-UNSAFE)> " if args.raw_unsafe else "elm> "
    try:
        while True:
            try:
                cmd = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not cmd:
                continue
            low = cmd.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "help":
                print(RAW_HELP_TEXT if args.raw_unsafe else HELP_TEXT, end="")
                continue
            if low == "info":
                print(f"  identity: {transport.identity}")
                continue
            reason = refusal(cmd, args.raw_unsafe)
            if reason:
                print(f"  refused: {reason}")
                continue
            try:
                for line in transport.send_command(cmd):
                    print(f"  {line}")
            except CommunicationError as exc:
                print(f"  error: {exc}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
