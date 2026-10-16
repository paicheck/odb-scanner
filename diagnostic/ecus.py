"""VAG/MEB ECU diagnostic address registry.

Addressing scheme: VAG UDS-on-CAN, 11-bit identifiers. TX = tester->ECU
request ID, RX = ECU->tester response ID.

Provenance of these addresses: experimentally determined and documented by
the open-source OVMS project (vehicle_vweup module) and the obd-amigos
community PID documentation for the VW e-Up / MEB-family VAG ECUs. The
same UDS-on-CAN addressing convention applies to the ID.3, but whether a
given ECU answers directly or only via the gateway (J533) must be verified
per vehicle in the discovery phase (`main.py discover`).

MEB physical addressing is NOT uniform. The battery, DC/DC and vehicle-info
modules sit on the 29-bit diagnostic bus (0x17FC007B, 0x17FC00B9, 0x17FC0076
request -> 0x17FExxxx response), while the energy, climate and GPS modules
sit on plain 11-bit ids (0x710 -> 0x77A, 0x746 -> 0x7B0, 0x767 -> 0x7D1).
`tx29` holds either form and the transport picks the protocol accordingly
(Elm327Transport.set_module); source: spot2000/Volkswagen-MEB-EV-CAN-parameters
and nickn17/evDash CarVWID3.cpp, both of which drive exactly these ids.

The `gate` entry is the exception to all of that: it is INFERRED from a
third-party static profile, not from on-car measurement. See
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
    # VAG MEB 29-bit module addressing on the diagnostic bus (reached with
    # ATCP + 6-digit ATSH -- see Elm327Transport.set_module). When present
    # and the transport negotiated MEB addressing, UDS reads go to the
    # module physically instead of functionally.
    tx29: int | None = None
    rx29: int | None = None


ECUS: dict[str, ECUSpec] = {
    spec.key: spec
    for spec in [
        ECUSpec(
            "bat_mgmt", "HV battery management (BMS)", 0x7E5, 0x7ED,
            notes="Cell voltages, SOH, energy counters. On ID.3 the BMS may be "
                  "reachable directly or via gateway; verified during discovery.",
            tx29=0x17FC007B, rx29=0x17FE007B,
        ),
        ECUSpec(
            "gate", "Central gateway (J533)", 0x710, 0x77A,
            doc_status="experimentally determined (passive HCI capture "
                       "2026-10-15)",
            notes="Serves the Car Scanner '[19.Gate]' measurements. Addressing "
                  "0x710 (request) -> 0x77A (response) is CONFIRMED by a "
                  "passive Bluetooth HCI capture of Car Scanner on 2026-10-15: "
                  "ATSH710 / ATFCSH710 / ATCRA77A on ATSP6 with flow control "
                  "ATFCSD300000 + ATFCSM1. The earlier 0x792->0x710 pair and "
                  "the '+0x6A encoding' reading were wrong: 0x77A really is "
                  "the response id. See "
                  "docs/investigation/meb_gateway_energy_passive_capture_"
                  "20261015.md.",
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
        ECUSpec(
            "dcdc", "DC/DC converter (HV->12V)", 0x777, 0x777,
            doc_status="experimentally determined (evDash/ABRP MEB community)",
            notes="MEB module 0xB9: 12V charging current/voltage.",
            tx29=0x17FC00B9, rx29=0x17FE00B9,
        ),
        ECUSpec(
            "energy", "Gateway energy information", 0x710, 0x77A,
            doc_status="experimentally determined (evDash/ABRP MEB community)",
            notes="MEB module 0x710: HV energy content (SoH source). "
                  "NOTE: unlike the BMS/DC-DC/vehicle-info modules this one "
                  "is NOT on the 29-bit bus -- it is addressed 11-bit with "
                  "ATCP 00 + ATSH 000710 and answers at 0x77A.",
            tx29=0x00000710, rx29=0x0000077A,
        ),
        ECUSpec(
            "climate", "Climate control", 0x746, 0x7B0,
            doc_status="experimentally determined (evDash/ABRP MEB community)",
            notes="MEB module 0x746: cabin and ambient temperature. Also "
                  "11-bit addressed (ATCP 00 + ATSH 000746 -> 0x7B0).",
            tx29=0x00000746, rx29=0x000007B0,
        ),
        ECUSpec(
            "veh_info", "Vehicle info (odometer/gear/VIN)", 0x76, 0x76,
            doc_status="experimentally determined (evDash/ABRP MEB community)",
            notes="MEB module 0x76: odometer, gear position, VIN.",
            tx29=0x17FC0076, rx29=0x17FE0076,
        ),
        ECUSpec(
            "eldrive", "Electric drive (motor/inverter)", 0x7E6, 0x7EE,
            doc_status="experimentally determined (Car Scanner MEB profile "
                       "2026-10-15)",
            notes="MEB module 0x7C: torque, motor speed, inverter losses, DC "
                  "link, rotor/stator temperatures. 29-bit addressed "
                  "(0x17FC007C -> 0x17FE007C).",
            tx29=0x17FC007C, rx29=0x17FE007C,
        ),
        ECUSpec(
            "gps", "GPS / telematics", 0x767, 0x7D1,
            doc_status="experimentally determined (Car Scanner MEB profile "
                       "2026-10-15)",
            notes="MEB module 0x767: satellite counts. 11-bit addressed "
                  "(ATCP 00 + ATSH 000767 -> 0x7D1).",
            tx29=0x00000767, rx29=0x000007D1,
        ),
    ]
}


def get_ecu(key: str) -> ECUSpec:
    return ECUS[key]
