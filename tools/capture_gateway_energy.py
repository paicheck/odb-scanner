#!/usr/bin/env python
"""One-shot gateway energy capture (0x2AB2, 0x2AB8 at 0x792->0x710).

Read-only, single round trip per DID, no polling loop.
Usage: python tools/capture_gateway_energy.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import Collector
from config import load_config
from database.repository import Repository
from diagnostic.ecus import ECUS


async def main() -> int:
    print("=" * 60)
    print("Gateway HV Energy Capture (0x2AB2 / 0x2AB8)")
    print("Address: 0x792 -> 0x710 (J533 gateway)")
    print("Read-only UDS 0x22, one request per DID")
    print("=" * 60)

    cfg = load_config()
    repo = Repository(cfg.get("database.path"))
    c = Collector(cfg, repo)

    try:
        # Open and identify (sets up transport, negotiates protocol)
        print("Opening connection and identifying vehicle...")
        ident = c.open_and_identify()
        print(f"Connected: {ident}")

        # Force gateway addressing
        gate = ECUS["gate"]
        c.conn.t.set_header(gate.tx)
        c.conn.t.set_receive_address(gate.rx)
        print(f"Header set: TX=0x{gate.tx:03X} RX=0x{gate.rx:03X}")

        for did in (0x2AB2, 0x2AB8):
            print(f"\nReading DID 0x{did:04X}...")
            try:
                # read_did is synchronous. This line used to `await` it, so
                # the request went out and the TypeError raised on the
                # returned bytes threw the answer away.
                resp = c.conn.read_did(gate, did)
            except Exception as e:
                print(f"  ERROR: {e}")
                continue
            if resp is None or len(resp) == 0:
                print("  NO RESPONSE (timeout)")
            else:
                print(f"  RAW: {resp.hex().upper()}")
                # Try decode with current catalogue
                from decoders.registry import build_default_registry
                reg = build_default_registry("meb")
                key = "hv_energy_max" if did == 0x2AB2 else "hv_energy_content"
                try:
                    spec = reg.get(key)
                    val = spec.decode(resp)
                    print(f"  DECODED: {val} {spec.unit}")
                except Exception as e:
                    print(f"  DECODE ERROR: {e}")

        print("\n" + "=" * 60)
        print("Done. Check data/real_car.db for stored raw_response.")
        print(
            "Run: python tools/replay_validate.py "
            "--out docs/investigation/replay_report.md"
        )
        return 0

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
