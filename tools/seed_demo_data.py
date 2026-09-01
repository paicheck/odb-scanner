"""Seed the database with a realistic 30-day example diagnostic dataset.

Synthetic data clearly marked as test data. Values mimic an ID.3 58 kWh with
a slowly growing cell-voltage imbalance and occasional communication DTCs
after AC charging — enough to exercise every analysis and report path.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from database.repository import Repository
from decoders.bms import NOMINAL_CAC_AH_58KWH

VIN = "WVWZZZE1ZMP087053"


def seed(db_path: str, days: int = 30) -> None:
    repo = Repository(db_path)
    vid = repo.ensure_vehicle(VIN, make="Volkswagen", model="ID.3",
                              variant="58 kWh (Pro)", year=2021)
    now = datetime.now(timezone.utc)
    rng = random.Random(42)

    # ECU statuses
    repo.upsert_ecu(vid, "bat_mgmt", "HV battery management", 0x7E5, 0x7ED,
                    "responder", "experimentally determined")
    repo.upsert_ecu(vid, "chg_mgmt", "HV charge management", 0x765, 0x7CF,
                    "responder", "experimentally determined")
    repo.upsert_ecu(vid, "chg", "HV charger (OBC)", 0x744, 0x7AE,
                    "responder", "experimentally determined")
    repo.upsert_ecu(vid, "mot_elec", "Motor electronics", 0x7E0, 0x7E8,
                    "responder", "experimentally determined")

    cac = 161.0
    sessions = []
    # ---- daily battery snapshots + cell voltages -------------------------
    for day in range(days, -1, -1):
        ts = (now - timedelta(days=day)).isoformat(timespec="seconds")
        soc = 72 - 0.4 * day + rng.uniform(-6, 6)
        base = 3.80 + soc / 400.0
        delta_mv = 22 + 0.5 * (days - day) + rng.uniform(-4, 4)  # growing drift
        vmin = base
        vmax = base + delta_mv / 1000.0
        pack_v = base * 96 + 3.0
        temp = 22 + 3 * rng.random()
        repo.record_battery_snapshot(
            vid, ts, soc_normal_pct=soc, pack_voltage_v=pack_v,
            pack_current_a=rng.uniform(-15, 5),
            pack_power_kw=rng.uniform(-6, 1.5),
            cell_min_v=vmin, cell_max_v=vmax, cell_delta_mv=delta_mv,
            battery_temp_c=temp, energy_charged_kwh=2400 + (days - day) * 9,
            energy_used_kwh=2100 + (days - day) * 11,
            soh_pct=round(100 * cac / NOMINAL_CAC_AH_58KWH, 1), cac_ah=cac,
            charge_mode="idle/ready",
        )
        # 8 cell groups for the demo pack
        repo.record_cell_voltages(
            vid, ts, [vmin + rng.uniform(0, delta_mv / 1000.0) for _ in range(8)])

        # ---- AC charging every ~3 days, some followed by a U112300 -------
        if day % 3 == 0:
            start = now - timedelta(days=day, hours=6)
            dur_min = rng.uniform(120, 210)
            sid = repo.open_session(vid, start.isoformat(timespec="seconds"),
                                    "AC", start_soc=round(soc - 25, 1))
            for step in range(12):
                frac = step / 11
                sts = (start + timedelta(minutes=dur_min * frac)).isoformat(
                    timespec="seconds")
                sample_soc = round(soc - 25 + 25 * frac, 1)
                repo.add_charging_sample(
                    sid, sts, voltage_v=232, current_a=16,
                    power_kw=3.6 + rng.uniform(-0.2, 0.3),
                    soc=sample_soc,
                    battery_temp_c=temp + rng.uniform(0, 4),
                )
            end_ts = start + timedelta(minutes=dur_min)
            repo.close_session(sid, end_ts.isoformat(timespec="seconds"),
                               end_soc=round(soc, 1))
            # DTC appears within 2h after ~half of the AC sessions
            if rng.random() < 0.5:
                occ = end_ts + timedelta(minutes=rng.uniform(20, 110))
                repo.upsert_dtc(
                    vid, occ.isoformat(timespec="seconds"), "chg_mgmt",
                    "U112300", "Data bus: received invalid data / missing message",
                    ["communication", "informational"], status="confirmed",
                    freeze_frame={"status_byte": 0x2F},
                )
                repo.add_event(vid, occ.isoformat(timespec="seconds"), "wake",
                               "Vehicle wake after AC charging")

    # one unrelated historical DTC on the BMS
    repo.upsert_dtc(vid, (now - timedelta(days=40)).isoformat(timespec="seconds"),
                    "bat_mgmt", "P0A7F00", "Hybrid/EV battery pack deterioration",
                    ["potentially critical", "powertrain"], status="historical",
                    freeze_frame={"status_byte": 0x08})
    repo.close()
    print(f"Seeded {db_path} with {days + 1} days of example data.")
