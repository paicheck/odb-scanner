"""Diagnostic collector: connects, discovers, reads, stores. READ-ONLY.

Everything it transmits is logged via repo.log_tx with a purpose string.
It only uses DiagnosticConnection methods, which can only produce:
  * ELM327 AT commands (configuration, not vehicle commands)
  * OBD-II modes 01/03/09 (read)
  * UDS 0x10 0x01, 0x22, 0x19 0x02 (read) — enforced by diagnostic/uds.py
"""
from __future__ import annotations

import logging
import time

from analysis import dtc as dtc_analysis
from decoders.registry import Provenance, build_default_registry
from diagnostic import obd2, uds
from diagnostic.connection import DiagnosticConnection
from diagnostic.ecus import ECUS, ECUSpec
from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import CommunicationError
from database.repository import Repository, utcnow

log = logging.getLogger(__name__)

# standard OBD-II PIDs to poll on BEVs (0x42 = control module voltage ~12V)
OBD_PIDS = (0x00, 0x0D, 0x42)


def build_transport(cfg):
    a = cfg
    if a.get("adapter.type") == "elm327_tcp":
        return Elm327Transport(
            host=a.get("adapter.tcp_host", "127.0.0.1"),
            tcp_port=int(a.get("adapter.tcp_port", 35000)),
            timeout=float(a.get("adapter.timeout", 5.0)),
        )
    return Elm327Transport(
        port=a.get("adapter.port", "auto"),
        baudrate=int(a.get("adapter.baudrate", 38400)),
        timeout=float(a.get("adapter.timeout", 5.0)),
    )


class Collector:
    def __init__(self, cfg, repo: Repository):
        self.cfg = cfg
        self.repo = repo
        self.registry = build_default_registry()
        self.conn = DiagnosticConnection(
            build_transport(cfg), tx_logger=repo.log_tx
        )
        self.vehicle_id: int | None = None
        self.vin: str | None = None
        self._last_slow = 0.0
        self._active_session_id: int | None = None

    # -- phase 1: connect & identify -----------------------------------------
    def open_and_identify(self) -> str:
        self.conn.open()
        vin = self.conn.read_vin()
        self.vin = vin
        year = obd2.decode_vin_year(vin)
        self.vehicle_id = self.repo.ensure_vehicle(
            vin,
            make=self.cfg.get("vehicle.make", "Volkswagen"),
            model=self.cfg.get("vehicle.model", "ID.3"),
            variant=self.cfg.get("vehicle.variant", ""),
            year=year,
        )
        self.repo.add_event(self.vehicle_id, utcnow(), "adapter",
                            f"Connected via {self.conn.t.description()}; "
                            f"VIN read = {vin}")
        log.info("Identified vehicle VIN=%s year=%s", vin, year)
        return vin

    def close(self) -> None:
        self.conn.close()

    # -- phase 2: ECU discovery ----------------------------------------------
    def discover_ecus(self) -> dict[str, str]:
        """Probe each known ECU with a default-session request.

        Statuses: responder | no-response | nrc-<code>. Never writes.
        """
        results = {}
        for key, spec in ECUS.items():
            status = "no-response"
            try:
                payload = uds.parse_elm_lines(
                    self.conn._transmit("1001", spec,
                                        "UDS 0x10 0x01 default diagnostic "
                                        f"session (ECU discovery probe, {key})")
                )
                if payload and payload[0] == 0x50:
                    status = "responder"
                elif payload and payload[0] == 0x7F and len(payload) > 2:
                    status = f"nrc-{payload[2]:02X}"
            except CommunicationError as exc:
                log.debug("ECU %s probe failed: %s", key, exc)
            self.repo.upsert_ecu(self.vehicle_id, key, spec.name, spec.tx,
                                 spec.rx, status, spec.doc_status)
            results[key] = status
        log.info("ECU discovery: %s", results)
        return results

    # -- measurement recording --------------------------------------------------
    def _record(self, spec, raw: bytes | None, ts: str, error: str | None = None,
                success: bool = True) -> None:
        value = spec.decode_value(raw) if success else None
        text_value = None
        if isinstance(value, dict):
            import json
            text_value, value = json.dumps(value), None
        self.repo.record_measurement(
            self.vehicle_id, ts, spec.key, spec.ecu_key,
            "UDS-0x22" if spec.did else "-",
            f"0x{spec.did:04X}" if spec.did else "-",
            spec.unit, spec.provenance.value,
            "builtin" if spec.decode else "raw-only",
            spec.doc_status.value,
            raw.hex().upper() if raw else "", value, text_value,
            success, error,
        )

    def poll_did(self, spec, ts: str) -> bytes | None:
        ecu = ECUS[spec.ecu_key]
        try:
            raw = self.conn.read_did(ecu, spec.did)
            self._record(spec, raw, ts)
            return raw
        except (CommunicationError,) as exc:
            self._record(spec, None, ts, error=str(exc), success=False)
            return None

    # -- phase 3/4: one collection pass ----------------------------------------
    def collect_once(self) -> dict:
        ts = utcnow()
        snapshot: dict = {}

        # standard OBD-II (12 V voltage comes from PID 0x42)
        for pid in OBD_PIDS:
            try:
                data = self.conn.mode01(pid)
            except CommunicationError:
                continue
            decoded = obd2.decode_pid(pid, data)
            if decoded and pid == 0x42:
                self.repo.record_measurement(
                    self.vehicle_id, ts, "lv_voltage_obd", "-", "OBD-01",
                    "0x42", "V", "reported", "SAE J1979 PID 0x42",
                    "documented", data.hex().upper(), decoded[0],
                )
                snapshot["lv_voltage_v"] = decoded[0]

        # fast battery DIDs
        for spec in self.registry.fast():
            raw = self.poll_did(spec, ts)
            if raw is not None and spec.decode:
                v = spec.decode_value(raw)
                if not isinstance(v, dict):
                    snapshot[spec.key] = v

        # computed values (labelled calculated)
        voltage, current = snapshot.get("pack_voltage"), snapshot.get("pack_current")
        if voltage is not None and current is not None:
            snapshot["pack_power_kw"] = round(voltage * current / 1000.0, 3)
            self.repo.record_measurement(
                self.vehicle_id, ts, "pack_power_kw", "bat_mgmt", "calc", "-",
                "kW", Provenance.CALCULATED.value,
                "voltage * current / 1000", "calculated", "", 
                snapshot["pack_power_kw"],
            )
        vmin, vmax = snapshot.get("cell_voltage_min"), snapshot.get("cell_voltage_max")
        if vmin is not None and vmax is not None:
            delta_mv = round((vmax - vmin) * 1000.0, 1)
            snapshot["cell_delta_mv"] = delta_mv
            self.repo.record_measurement(
                self.vehicle_id, ts, "cell_delta_mv", "bat_mgmt", "calc", "-",
                "mV", Provenance.CALCULATED.value,
                "(max - min) * 1000", "calculated", "", delta_mv,
            )

        # per-cell sweep (slow pass piggybacks on fast pass cadence flag)
        if self.cfg.get("collector.read_cell_voltages", True):
            self._sweep_cells(ts)

        self._slow_pass(ts, snapshot)
        self._battery_snapshot(ts, snapshot)
        self._charging_tracking(ts, snapshot)
        return snapshot

    def _sweep_cells(self, ts: str) -> None:
        """Read per-cell DIDs. DIDs outside the real pack return NRC 0x31
        and are stored as unsuccessful measurements (raw data preserved)."""
        voltages = []
        for spec in self.registry.all():
            if not spec.key.startswith("cell_v_"):
                continue
            raw = self.poll_did(spec, ts)
            if raw and spec.decode:
                v = spec.decode_value(raw)
                if isinstance(v, float) and 1.0 < v < 5.0:
                    voltages.append(v)
        if voltages:
            self.repo.record_cell_voltages(self.vehicle_id, ts, voltages)
            snapshot_note = f"{len(voltages)} cells"
            self.repo.add_event(self.vehicle_id, ts, "note",
                                f"Cell sweep recorded {snapshot_note}")

    def _slow_pass(self, ts: str, snapshot: dict) -> None:
        for spec in self.registry.slow():
            self.poll_did(spec, ts)

    def _battery_snapshot(self, ts: str, snap: dict) -> None:
        latest = self.repo.latest_measurements(self.vehicle_id)
        def val(key, field=None):
            if key in snap:
                return snap[key]
            m = latest.get(key)
            return m["value"] if m else None

        energy = latest.get("energy_counters")
        energy_ch = energy_used = None
        if energy and energy.get("text_value"):
            import json
            d = json.loads(energy["text_value"])
            energy_ch, energy_used = d.get("charged_kwh"), d.get("used_kwh")
        cac = val("soh_cac") or {}
        cac_ah = cac.get("battery_cac_ah") if isinstance(cac, dict) else None
        nominal = 164.0  # ID.3 58 kWh nominal (published spec, see bms.py)
        soh = round(100.0 * cac_ah / nominal, 1) if cac_ah else None
        if soh:
            self.repo.record_measurement(
                self.vehicle_id, ts, "soh_pct", "bat_mgmt", "calc", "-", "%",
                Provenance.ESTIMATED.value, "cac_ah / nominal_cac",
                "estimated", "", soh,
            )
        mode = val("charge_mode")
        from decoders.charging import CHARGE_MODE_CODES
        self.repo.record_battery_snapshot(
            self.vehicle_id, ts,
            soc_abs_pct=val("soc_abs"), soc_normal_pct=val("soc_normal"),
            pack_voltage_v=val("pack_voltage"), pack_current_a=val("pack_current"),
            pack_power_kw=val("pack_power_kw"), cell_min_v=val("cell_voltage_min"),
            cell_max_v=val("cell_voltage_max"), cell_delta_mv=val("cell_delta_mv"),
            battery_temp_c=val("battery_temp"),
            energy_charged_kwh=energy_ch, energy_used_kwh=energy_used,
            soh_pct=soh, cac_ah=cac_ah,
            charge_mode=CHARGE_MODE_CODES.get(mode, str(mode)) if mode is not None else None,
        )

    def _charging_tracking(self, ts: str, snap: dict) -> None:
        """Open/close charging sessions based on the charge-mode DID."""
        mode = snap.get("charge_mode")
        if mode is None:
            latest = self.repo.latest_measurements(self.vehicle_id)
            m = latest.get("charge_mode")
            mode = m["value"] if m else None
        charging = mode in (1, 2, 3)
        if charging and self._active_session_id is None:
            ctype = "DC" if mode == 2 else "AC"
            self._active_session_id = self.repo.open_session(
                self.vehicle_id, ts, ctype, snap.get("soc_normal"))
            self.repo.add_event(self.vehicle_id, ts, "charge-start",
                                f"{ctype} charging detected (mode={mode})")
        elif not charging and self._active_session_id is not None:
            self.repo.close_session(self._active_session_id, ts,
                                    snap.get("soc_normal"))
            self.repo.add_event(self.vehicle_id, ts, "charge-end",
                                "Charging ended")
            self._active_session_id = None
        elif charging and self._active_session_id:
            self.repo.add_charging_sample(
                self._active_session_id, ts,
                voltage_v=snap.get("ac_voltage") or snap.get("dc_voltage"),
                current_a=snap.get("ac_current") or snap.get("dc_current"),
                power_kw=snap.get("pack_power_kw"),
                soc=snap.get("soc_normal"),
                battery_temp_c=snap.get("battery_temp"),
            )

    # -- DTCs -------------------------------------------------------------------
    def read_and_store_dtcs(self) -> list[dict]:
        ts = utcnow()
        all_dtcs: list[dict] = []
        # legislated OBD-II DTCs
        try:
            for code in self.conn.read_dtcs_obd2():
                desc, _ = dtc_analysis.describe(code)
                self.repo.upsert_dtc(self.vehicle_id, ts, "OBD-II", code, desc,
                                     dtc_analysis.classify_dtc(code))
                all_dtcs.append({"ecu": "OBD-II", "code": code})
        except CommunicationError as exc:
            log.debug("OBD-II DTC read failed: %s", exc)
        # UDS DTCs from discovered responder ECUs
        for row in self.repo.list_ecus(self.vehicle_id):
            if row["status"] != "responder":
                continue
            spec = ECUS.get(row["key"])
            if spec is None:
                continue
            try:
                results = self.conn.read_dtcs_uds(spec)
            except CommunicationError as exc:
                log.debug("UDS DTC read failed for %s: %s", spec.key, exc)
                continue
            for item in results:
                code = item["code"]
                desc, _ = dtc_analysis.describe(code)
                cats = dtc_analysis.classify_dtc(code, item.get("status_byte"))
                self.repo.upsert_dtc(self.vehicle_id, ts, spec.key, code, desc,
                                     cats, status="confirmed",
                                     freeze_frame={"status_byte": item.get("status_byte")})
                all_dtcs.append({"ecu": spec.key, "code": code,
                                 "status_byte": item.get("status_byte")})
        return all_dtcs

    # -- main loop ----------------------------------------------------------------
    def run(self, max_cycles: int | None = None) -> None:
        interval = float(self.cfg.get("collector.poll_interval", 5.0))
        slow_interval = float(self.cfg.get("collector.slow_poll_interval", 60.0))
        self.open_and_identify()
        self.discover_ecus()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            snap = self.collect_once()
            self.read_and_store_dtcs()
            log.info("cycle %d: %s", cycles,
                     {k: v for k, v in snap.items() if k != "cell_v"})
            cycles += 1
            if max_cycles is None or cycles < max_cycles:
                time.sleep(interval)



