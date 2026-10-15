"""VAG/MEB ECU diagnostic address registry.

Addressing scheme: VAG UDS-on-CAN, 11-bit identifiers. TX = tester->ECU
request ID, RX = ECU->tester response ID.

Provenance of these addresses: experimentally determined and documented by
the open-source OVMS project (vehicle_vweup module) and the obd-amigos
community PID documentation for the VW e-Up / MEB-family VAG ECUs. The
same UDS-on-CAN addressing convention applies to the ID.3, but whether a
given ECU answers directly or only via the gateway (J533) must be verified
per vehicle in the discovery phase (`main.py discover`).

The `gate` entry is the exception: it is INFERRED from a third-party
static profile, not from on-car measurement. See
`docs/investigation/meb_gateway_energy.md` before trusting it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ECUSpec:
    key: str
    name: str
    tx: int
    rx: int
    doc_status: str = "experimentally determined (OVMS e-Up / MEB-family)"
    notes: str = ""


ECUS: dict[str, ECUSpec] = {
    spec.key: spec
    for spec in [
        ECUSpec(
            "bat_mgmt", "HV battery management (BMS)", 0x7E5, 0x7ED,
            notes="Cell voltages, SOH, energy counters. On ID.3 the BMS may be "
                  "reachable directly or via gateway; verified during discovery.",
        ),
        ECUSpec(
            "gate", "Central gateway (J533)", 0x792, 0x710,
            doc_status="inferred (not yet validated on this vehicle)",
            notes="Serves the Car Scanner '[19.Gate]' measurements. rx 0x710 "
                  "is the response header those records declare twice: the "
                  "HDR field and ATFCSH710. Their ATCRA argument is 0x77A, "
                  "which is NOT 0x710+8 but exactly 0x710+0x6A - and every "
                  "11-bit ECU class in that profile shows the same +0x6A "
                  "relation, so ATCRA there is some Car Scanner encoding we "
                  "have not identified. We therefore filter on the declared "
                  "response header instead of copying ATCRA. tx 0x792 is the "
                  "only gateway address named in Car Scanner code; 0x772 "
                  "(= 0x710-8) is an untested alternative. Expect "
                  "'no-response' or NRC until verified during discovery - see "
                  "docs/investigation/meb_gateway_energy.md.",
        ),
        ECUSpec("chg_mgmt", "HV charge management", 0x765, 0x7CF,
                notes="SOC normal, charge mode, CCS status, remaining time."),
        ECUSpec("chg", "HV charger (OBC)", 0x744, 0x7AE,
                notes="AC/DC charge voltage/current, charger temperatures."),
        ECUSpec("mot_elec", "Motor electronics", 0x7E0, 0x7E8,
                notes="Also answers standard OBD-II modes 01/09 on MEB cars."),
        ECUSpec("eld", "Electric drive", 0x7E6, 0x7EE),
        ECUSpec("inf", "Information electronics (Infotainment)", 0x773, 0x7DD),
        ECUSpec("brk", "Brake electronics", 0x713, 0x77D),
    ]
}


def get_ecu(key: str) -> ECUSpec:
    return ECUS[key]
