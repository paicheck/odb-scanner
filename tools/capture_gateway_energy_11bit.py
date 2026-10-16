#!/usr/bin/env python
"""One-shot gateway energy capture using Car Scanner's exact 11-bit sequence.

Car Scanner's profiles7.db BCM field for [19.Gate] energy DIDs:
  ATSP6; ATFCSH710; ATFCSD300000; ATFCSM1; ATCRA77A
  then 22 2AB8 / 22 2AB2

This reproduces that sequence exactly, unlike capture_gateway_energy.py
which used ATSP7 functional addressing.

Usage: python tools/capture_gateway_energy_11bit.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import Collector
from config import load_config
from database.repository import Repository


async def main() -> int:
    print("=" * 60)
    print("Gateway HV Energy Capture — Car Scanner 11-bit Sequence")
    print("ATSP6; ATFCSH710; ATFCSD300000; ATFCSM1; ATCRA77A")
    print("DIDs: 0x2AB2 (max), 0x2AB8 (content)")
    print("=" * 60)

    cfg = load_config()
    repo = Repository(cfg.get("database.path"))
    c = Collector(cfg, repo)

    try:
        # Open and identify (sets up transport, negotiates protocol)
        print("Opening connection and identifying vehicle...")
        ident = c.open_and_identify()
        print(f"Connected: {ident}")

        # Use Car Scanner's exact 11-bit gateway sequence
        print("\nConfiguring Car Scanner gateway sequence...")
        ok = c.conn.t.set_module_car_scanner_11bit(0x710, 0x77A)
        if not ok:
            print("ERROR: Adapter refused one or more gateway config commands")
            return 1
        print("Gateway sequence applied successfully")

        for did, name in ((0x2AB2, "hv_energy_max"), (0x2AB8, "hv_energy_content")):
            print(f"\nReading DID 0x{did:04X} ({name})...")
            try:
                # Use the transport directly with the gateway sequence already set
                # We need to send the raw UDS request through the transport
                lines = c.conn.t.send_command(f"22{did:04X}")
                print(f"  RAW LINES: {lines}")

                from diagnostic import uds
                payload = uds.parse_elm_lines(lines)
                print(f"  PAYLOAD: {payload.hex().upper() if payload else None}")

                if not payload:
                    print("  OUTCOME 3: no decodable response -> STOP")
                    continue

                try:
                    pos = uds.expect_positive(payload, 0x22)
                except uds.NegativeResponseError as exc:
                    print(f"  OUTCOME 2: {exc} -> STOP, record the NRC only")
                    continue
                except uds.ProtocolError as exc:
                    print(f"  OUTCOME 5: malformed -> {exc} -> STOP")
                    continue

                rec = pos[3:]
                print(f"  POSITIVE: {pos.hex().upper()}")
                print(f"  DID RECORD: {rec.hex().upper()} len: {len(rec)}")

                # Check answering CAN ID from first line
                seen = lines[0][:3].upper() if lines else ""
                print(f"  ANSWERING CAN ID: {seen} (expected 77A)")

                # Decode using current catalogue
                from decoders.registry import build_default_registry
                reg = build_default_registry("meb")
                key = "hv_energy_max" if did == 0x2AB2 else "hv_energy_content"
                try:
                    spec = reg.get(key)
                    val = spec.decode(rec)
                    print(f"  DECODED: {val} {spec.unit}")
                except Exception as e:
                    print(f"  DECODE ERROR: {e}")

            except Exception as e:
                print(f"  ERROR: {e}")
                import traceback
                traceback.print_exc()
                continue

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
