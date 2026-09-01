"""Run the vehicle simulator in the foreground (or N seconds with --for).

Usage:
    python tools/run_sim.py                # until Ctrl+C
    python tools/run_sim.py --for 90       # auto-stop after 90 s
    python tools/run_sim.py --port 35001
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.vehicle import SimServer  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=35000)
    p.add_argument("--for", dest="duration", type=float, default=None)
    args = p.parse_args()
    with SimServer(args.host, args.port):
        print(f"Simulator on tcp://{args.host}:{args.port}"
              + (f" (stopping in {args.duration:.0f}s)" if args.duration else ""))
        try:
            if args.duration:
                time.sleep(args.duration)
            else:
                while True:
                    time.sleep(3600)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
