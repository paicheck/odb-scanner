"""Electric drive decoders.

STATUS: no verified UDS DIDs for motor speed/torque/inverter temperature on
the ID.3 exist in public open-source documentation at this time.

Per project requirement #18 ("don't fake support") this module deliberately
registers NOTHING. If a DID is experimentally identified (e.g. by comparing
raw CAN traces against known driving states), register it here as:

    reg.register(DIDSpec(
        "motor_rpm", "eld", 0xXXXX, "Motor speed", "rpm",
        decode=..., doc_status=DocStatus.EXPERIMENTAL,
        notes="how it was identified",
    ))

and the collector, database, analysis and dashboard will pick it up without
further changes. See decoders/bms.DRIVE_SYSTEM_STATUS.
"""
from __future__ import annotations

from .registry import DIDRegistry  # noqa: F401  (import surface for future use)
