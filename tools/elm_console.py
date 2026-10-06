"""Interactive raw ELM327 console for live diagnostics at the vehicle.

*** THIS TOOL IS NOT READ-ONLY. ***

Everything else in this project routes every byte through
diagnostic/uds.validate_request, so no code path can put a write on the
vehicle. This console deliberately does not: whatever you type is sent as
typed, with no allow-list. You can transmit 2E (write data), 31 (routine
control), 1002 (programming session), 14 (clear DTCs) or any other service
simply by typing it. That is the point of the tool, and it is also why the
read-only guarantee in README.md does not extend to it.

Run it against the car only when a read is not enough, and prefer
`main.py discover`, `main.py doctor` and `main.py guard-test`, which cannot.

Uses the same transport and init sequence as main.py, but every command is
typed by hand and responses are printed raw. Built for the "discovery
answers NO DATA" situation:

    python tools/elm_console.py                          # configured adapter
    python tools/elm_console.py --tcp 127.0.0.1 35000    # simulator
    python tools/elm_console.py --no-init                # raw mode, skip AT init

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

HELP_TEXT = """NOT READ-ONLY. Whatever you type is sent as typed, with no allow-list --
so 2E, 31, 1002 and 14 will reach the vehicle if you enter them. Every other
command in this project cannot do that.

commands:
  <any ELM327/UDS command>   sent as typed (0100, 0902, 03, ATSP0, ATSH7E5, ...)
  info                       show adapter identity
  help                       show this help
  q / quit / exit            leave the console
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None, help="alternative config file")
    ap.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"),
                    help="talk to a TCP adapter instead (e.g. the simulator)")
    ap.add_argument("--port", help="serial port override (e.g. COM3)")
    ap.add_argument("--no-init", action="store_true",
                    help="skip the standard AT init sequence")
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
    print("NOT READ-ONLY: what you type is sent to the vehicle as typed.")
    try:
        while True:
            try:
                cmd = input("elm> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not cmd:
                continue
            low = cmd.lower()
            if low in ("q", "quit", "exit"):
                break
            if low == "help":
                print(HELP_TEXT, end="")
                continue
            if low == "info":
                print(f"  identity: {transport.identity}")
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
