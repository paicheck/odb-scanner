"""Diagnostic collector: connects, discovers, reads, stores. READ-ONLY.

Everything it transmits is logged via repo.log_tx with a purpose string.
It only uses DiagnosticConnection methods, which can only produce:
  * ELM327 AT commands (configuration, not vehicle commands)
  * OBD-II modes 01/03/09 (read)
  * UDS 0x10 0x01, 0x22, 0x19 0x02 (read) — enforced by diagnostic/uds.py
"""
from __future__ import annotations

import json
import logging
import time

from analysis import dtc as dtc_analysis
from database.repository import Repository, utcnow
from decoders.bms import (
    NOMINAL_CAC_AH_58KWH,
    soh_pct_from_cac,
    soh_pct_from_energy,
)
from decoders.registry import Provenance, build_default_registry
from diagnostic import obd2, uds
from diagnostic.connection import DiagnosticConnection
from diagnostic.ecus import ECUS
from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import CommunicationError

log = logging.getLogger(__name__)

# standard OBD-II PIDs to poll on BEVs (0x42 = control module voltage ~12V)
OBD_PIDS = (0x00, 0x0D, 0x42)

# A cycle is treated as a dead adapter -- not a quiet vehicle -- once it made
# at least this many read attempts and every single one of them failed. One
# threshold for both: below it, a cycle that happened to contain only DIDs
# this car does not implement (all NRC 0x31) would be mistaken for a dead
# link and trigger a pointless reconnect storm.
DEAD_CYCLE_MIN_ATTEMPTS = 3

# Reconnect pacing. Starts at 5 s and doubles to a 5-minute ceiling: a
# Bluetooth SPP port whose phone has gone to sleep stays deaf for a while, and
# hammering open() every poll_interval just fills the log.
RECONNECT_BACKOFF_S = 5.0
RECONNECT_MAX_BACKOFF_S = 300.0

# Which polled PIDs get persisted, as (measurement key, snapshot key, unit,
# doc). PID 0x00 is the supported-bitmask handshake, not a value, so it is
# deliberately absent. A decoded PID missing from this table would be
# silently dropped -- which is how vehicle speed went unrecorded.
OBD_PID_KEYS = {
    0x0D: ("vehicle_speed", "speed_kmh", "km/h", "SAE J1979 PID 0x0D"),
    0x42: ("lv_voltage_obd", "lv_voltage_v", "V",
           "SAE J1979 PID 0x42 (control module voltage ~ 12 V system)"),
}


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
        self.registry = build_default_registry(
            cfg.get("vehicle.did_profile", "eup"))
        self.conn = DiagnosticConnection(
            build_transport(cfg), tx_logger=repo.log_tx
        )
        self.vehicle_id: int | None = None
        self.vin: str | None = None
        self.slow_interval = float(
            self.cfg.get("collector.slow_poll_interval", 60.0))
        # Nominal pack capacity, only ever used for the ESTIMATED SOH figure.
        # Overridable for a different pack; the constant stays the single
        # source of truth in decoders/bms.py.
        self.nominal_cac_ah = float(
            self.cfg.get("battery.nominal_cac_ah", NOMINAL_CAC_AH_58KWH))
        # Oldest a cached measurement may be and still be used to fill a gap in
        # the current snapshot. Must exceed slow_interval, since slow DIDs are
        # legitimately absent from most cycles.
        self.max_value_age_s = float(
            self.cfg.get("collector.max_value_age_s", 900.0))
        # negative offset so the first pass is always due, even on a host that
        # booted less than slow_interval seconds ago (monotonic starts at 0)
        self._last_slow = -self.slow_interval
        self._active_session_id: int | None = None
        # Learned during discover_ecus(): registry key -> CAN header of the
        # ECU that actually answered that key's probe (functional addressing,
        # so the answer's response header is the only identity we get).
        self.ecu_addresses: dict[str, int] = {}
        # Per-run cache of 0x19 0x04 snapshot reads, keyed by DTC code. One
        # functional read per code per run: a code nobody stores costs a
        # full adapter.timeout on every cycle otherwise, and the stored-DTC
        # set rarely changes within a run.
        self._dtc_snapshots: dict[str, dict] = {}
        # Set False by discover_ecus() when the battery DIDs are refused on
        # this vehicle's OBD surface (BMS not exposed through the gateway,
        # e.g. NRC-31): the per-cell sweep would otherwise re-collect 102
        # guaranteed failures on every slow phase.
        self._cells_readable = True
        # Per-cycle health. Every read swallows CommunicationError so one dead
        # DID cannot abort a pass, which also meant an unplugged adapter looked
        # identical to a quiet vehicle: collect_once returned {} forever, the
        # loop kept spinning at poll_interval, and each cycle wrote a failed
        # measurement row for every registered DID. Measured at 146 rows per
        # 3 cycles on a TCP adapter that was never there. These counters are
        # what let run() tell the two apart.
        self._cycle_attempts = 0
        self._cycle_failures = 0
        self._dead_cycles = 0

    def _attempt(self, ok: bool) -> None:
        """Count one read attempt and whether it produced data."""
        self._cycle_attempts += 1
        if not ok:
            self._cycle_failures += 1

    def _adopt_open_session(self) -> None:
        """Continue a session a previous run left open.

        Without this, a collector restarted mid-charge starts with no active
        session, opens a second one, and leaves the first permanently 'open' --
        so the dashboard shows two concurrent sessions and the orphan never
        gets a duration or energy figure. open_session_id() existed for this
        but nothing called it.

        Runs once the vehicle is known, so it cannot be done in __init__.
        """
        if self._active_session_id is not None:
            return
        existing = self.repo.open_session_id(self.vehicle_id)
        if existing is not None:
            self._active_session_id = existing
            log.info("Adopted charging session %s left open by a previous run",
                     existing)

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
        self._adopt_open_session()
        log.info("Identified vehicle VIN=%s year=%s", vin, year)
        return vin

    def close(self) -> None:
        self.conn.close()

    # -- phase 2: ECU discovery ----------------------------------------------
    def _probe_dids(self) -> dict[str, int]:
        """First documented non-cell DID per ECU key, for discovery probes."""
        probes: dict[str, int] = {}
        for spec in self.registry.all():
            if spec.did is None or spec.key.startswith("cell_v_"):
                continue
            probes.setdefault(spec.ecu_key, spec.did)
        return probes

    def discover_ecus(self) -> dict[str, str]:
        """Probe ECUs and attribute answers by CAN header.

        Addressing follows the transport: functionally by default (see
        Elm327Transport.set_header), or physically to the module when the
        adapter negotiated VAG MEB addressing and the ECU has a tx29 (this is
        the only way the ID.3 BMS answers). Either way the answer is
        identified by its response header (18DAF1xx on 29-bit OBD buses,
        17FExxxx for MEB modules, the rx id on 11-bit buses). Each registry
        key is probed with the first DID the decoder registry documents for
        it, so a positive answer identifies the ECU that really holds that
        data.

        Statuses: responder | no-response | nrc-<code> |
        no-dids-registered. Never writes.
        """
        results: dict[str, str] = {}
        self.ecu_addresses = {}
        # Who speaks UDS at all? One functional default-session request.
        try:
            session = self.conn.functional_probe(
                "1001", "UDS 0x10 0x01 default diagnostic session "
                        "(functional discovery probe)")
        except CommunicationError as exc:
            log.debug("functional session probe failed: %s", exc)
            session = {}
        live = sorted(uds.source_label(src) for src, p in session.items()
                      if p and p[0] == 0x50)
        if live:
            log.info("functional UDS responders (session probe): %s",
                     ", ".join(live))
        probe_dids = self._probe_dids()
        for key, spec in ECUS.items():
            did = probe_dids.get(key)
            if did is None:
                # Nothing documented for this ECU, and a generic probe
                # (e.g. 22F190) is answered by whoever knows the VIN, not
                # by "the" ECU for this role -- reporting that as a
                # responder would be misleading.
                results[key] = "no-dids-registered"
                self.repo.upsert_ecu(self.vehicle_id, key, spec.name,
                                     spec.tx, spec.rx, results[key],
                                     spec.doc_status)
                continue
            status = "no-response"
            try:
                payloads = self.conn.functional_probe(
                    f"22{did:04X}",
                    f"UDS 0x22 DID 0x{did:04X} discovery probe ({key}"
                    + (f", module {spec.tx29:08X}" if spec.tx29 else
                       ", functional") + ")",
                    ecu=spec)
            except CommunicationError as exc:
                log.debug("ECU %s probe failed: %s", key, exc)
                payloads = {}
            for src, p in payloads.items():
                if (p and p[0] == 0x62 and len(p) >= 3
                        and ((p[1] << 8) | p[2]) == did):
                    status = "responder"
                    self.ecu_addresses[key] = src
                    break
                if p and p[0] == 0x7F and len(p) > 2 and status == "no-response":
                    status = f"nrc-{p[2]:02X}"
            self.repo.upsert_ecu(self.vehicle_id, key, spec.name, spec.tx,
                                 spec.rx, status, spec.doc_status)
            results[key] = status
        if self.ecu_addresses:
            self.repo.add_event(
                self.vehicle_id, utcnow(), "note",
                "functional discovery: " + ", ".join(
                    f"{k}@{uds.source_label(v)}"
                    for k, v in sorted(self.ecu_addresses.items())))
        # Battery DIDs refused or unanswered on this vehicle's OBD surface?
        # The per-cell sweep can only produce the same verdicts 102 times
        # per slow phase -- skip it and say why.
        bat = results.get("bat_mgmt", "")
        if self._cells_readable and (bat.startswith("nrc-")
                                     or bat == "no-response"):
            self._cells_readable = False
            log.info("Cell sweep disabled: battery DIDs answer %r on this "
                     "vehicle (BMS not exposed via the OBD surface)", bat)
            self.repo.add_event(
                self.vehicle_id, utcnow(), "note",
                f"Cell sweep disabled this run: battery DID probe answered "
                f"'{bat}' -- the BMS is not reachable through the gateway's "
                f"OBD surface on this vehicle")
        log.info("ECU discovery: %s", results)
        return results

    # -- measurement recording --------------------------------------------------
    def _record(self, spec, raw: bytes | None, ts: str, error: str | None = None,
                success: bool = True) -> None:
        value = spec.decode_value(raw) if success else None
        text_value = None
        if isinstance(value, dict):
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
            self._attempt(True)
            return raw
        except CommunicationError as exc:
            self._record(spec, None, ts, error=str(exc), success=False)
            self._attempt(False)
            return None

    # -- phase 3/4: one collection pass ----------------------------------------
    def collect_once(self) -> dict:
        ts = utcnow()
        snapshot: dict = {}
        self._cycle_attempts = 0
        self._cycle_failures = 0

        # standard OBD-II (12 V voltage comes from PID 0x42, speed from 0x0D)
        for pid in OBD_PIDS:
            try:
                data = self.conn.mode01(pid)
            except CommunicationError:
                self._attempt(False)
                continue
            self._attempt(True)
            decoded = obd2.decode_pid(pid, data)
            if not decoded or pid not in OBD_PID_KEYS:
                continue
            key, snap_key, unit, doc = OBD_PID_KEYS[pid]
            value = decoded[0]
            self.repo.record_measurement(
                self.vehicle_id, ts, key, "-", "OBD-01",
                f"0x{pid:02X}", unit, "reported", doc,
                "documented", data.hex().upper(), value,
            )
            snapshot[snap_key] = value

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

        # slow phase: per-cell sweep + non-cell slow DIDs. Reads 107 DIDs, so
        # it runs on its own cadence (collector.slow_poll_interval) rather than
        # on every fast poll.
        if self._slow_phase_due():
            if self.cfg.get("collector.read_cell_voltages", True) \
                    and self._cells_readable:
                self._sweep_cells(ts)
            self._slow_pass(ts, snapshot)
        self._battery_snapshot(ts, snapshot)
        self._charging_tracking(ts, snapshot)
        return snapshot

    def _slow_phase_due(self) -> bool:
        """True when collector.slow_poll_interval has elapsed. Always true on
        the first pass, so a fresh run has cell data without waiting."""
        now = time.monotonic()
        if now - self._last_slow < self.slow_interval:
            return False
        self._last_slow = now
        return True

    def _sweep_cells(self, ts: str) -> None:
        """Read per-cell DIDs. DIDs outside the real pack return NRC 0x31
        and are stored as unsuccessful measurements (raw data preserved)."""
        cells = []          # (physical cell number, volts)
        for spec in self.registry.all():
            if not spec.key.startswith("cell_v_"):
                continue
            raw = self.poll_did(spec, ts)
            if raw and spec.decode:
                v = spec.decode_value(raw)
                if isinstance(v, float) and 1.0 < v < 5.0:
                    cells.append((int(spec.key.rsplit("_", 1)[1]), v))
        if cells:
            # The cell number comes from the DID key, not the list position:
            # cells that did not answer are absent, so positions would shift.
            self.repo.record_cell_voltages(
                self.vehicle_id, ts, [v for _, v in cells],
                [n for n, _ in cells])
            snapshot_note = f"{len(cells)} cells"
            self.repo.add_event(self.vehicle_id, ts, "note",
                                f"Cell sweep recorded {snapshot_note}")

    def _slow_pass(self, ts: str, snapshot: dict) -> None:
        """Poll the non-cell slow DIDs. Per-cell DIDs are excluded here
        because _sweep_cells() already reads them in the same slow phase."""
        for spec in self.registry.slow():
            if spec.key.startswith("cell_v_"):
                continue
            self.poll_did(spec, ts)

    def _battery_snapshot(self, ts: str, snap: dict) -> None:
        # Bound how old a carried-forward value may be. Without this the
        # fallback below is "the last value that ever succeeded", so a DID that
        # failed for days would be re-stamped with today's ts and the row would
        # mix fresh and week-old readings -- which then becomes the baseline
        # that anomaly.py compares against.
        latest = self.repo.latest_measurements(self.vehicle_id,
                                               max_age_s=self.max_value_age_s)

        def val(key, field=None):
            if key in snap:
                return snap[key]
            m = latest.get(key)
            return m["value"] if m else None

        def val_json(key) -> dict:
            """Dict-valued decoders (soh_cac, energy_counters) are stored with
            value=NULL and the payload JSON-encoded in text_value, so they must
            be read from there -- val() would always hand back None for them."""
            if isinstance(snap.get(key), dict):
                return snap[key]
            m = latest.get(key)
            if not m or not m.get("text_value"):
                return {}
            try:
                parsed = json.loads(m["text_value"])
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        energy = val_json("energy_counters")
        energy_ch = energy.get("charged_kwh")
        energy_used = energy.get("used_kwh")
        cac_ah = val_json("soh_cac").get("battery_cac_ah")
        # Prefer the capacity figure; fall back to the BMS-reported max energy
        # content (MEB packs expose no CAC DID), else no SoH at all rather
        # than a fabricated 0 %.
        soh_basis = "cac_ah / nominal_cac"
        soh = soh_pct_from_cac(cac_ah, self.nominal_cac_ah)
        if not soh:
            soh = soh_pct_from_energy(val("hv_energy_max"))
            soh_basis = "hv_energy_max / nominal_energy (rated vs marketed)"
        if soh:
            self.repo.record_measurement(
                self.vehicle_id, ts, "soh_pct", "bat_mgmt", "calc", "-", "%",
                Provenance.ESTIMATED.value, soh_basis,
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
            charge_mode=(CHARGE_MODE_CODES.get(mode, str(mode))
                         if mode is not None else None),
        )

    def _charging_tracking(self, ts: str, snap: dict) -> None:
        """Open/close charging sessions based on the charge-mode DID."""
        mode = snap.get("charge_mode")
        if mode is None:
            # Bounded, like the snapshot: charge_mode is a slow DID, so a
            # missed read must not resurrect the mode from days ago and hold a
            # charging session open (or close a live one) on stale evidence.
            latest = self.repo.latest_measurements(
                self.vehicle_id, max_age_s=self.max_value_age_s)
            m = latest.get("charge_mode")
            mode = m["value"] if m else None
        charging = mode in (1, 2, 3)
        if mode is None:
            # Unknown is not the same as "not charging". Treating a missed read
            # as mode 0 closed a live session on no evidence, leaving it
            # truncated at whatever SoC happened to be recorded.
            return
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
            # `or` here would drop a genuine 0 A reading -- which is exactly
            # what a session that has finished, or a charger that has tripped,
            # looks like -- in favour of whatever the other column said.
            def _pick(*values):
                for v in values:
                    if v is not None:
                        return v
                return None

            self.repo.add_charging_sample(
                self._active_session_id, ts,
                voltage_v=_pick(snap.get("ac_voltage"), snap.get("dc_voltage")),
                current_a=_pick(snap.get("ac_current"), snap.get("dc_current")),
                power_kw=snap.get("pack_power_kw"),
                soc=snap.get("soc_normal"),
                battery_temp_c=snap.get("battery_temp"),
            )

    # -- DTCs -------------------------------------------------------------------
    def _ecu_label_for_source(self, src: int | None) -> str:
        """Best label for a responding ECU: a registry key if the address is
        known (learned functionally, or a documented 11-bit rx id), else a
        plain address label."""
        for key, addr in self.ecu_addresses.items():
            if addr == src:
                return key
        if src is not None and src <= 0x7FF:
            for key, spec in ECUS.items():
                if spec.rx == src:
                    return key
        return f"uds-{uds.source_label(src)}"

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
        # UDS DTCs: ONE functional read, attributed per responding ECU by its
        # response header. Per-ECU reads would send the identical functional
        # request N times and could not tell whose DTCs came back anyway.
        try:
            payloads = self.conn.functional_probe(
                "190208", "UDS 0x19 0x02 ReadDTCInformation (status mask "
                          "0x08, confirmed DTCs) - functional, read-only")
        except CommunicationError as exc:
            log.debug("UDS DTC read failed: %s", exc)
            payloads = {}
        uds_items: list[tuple[int | None, str, dict]] = []
        for src, payload in payloads.items():
            if not payload or payload[0] != 0x59:
                continue
            label = self._ecu_label_for_source(src)
            for item in obd2.parse_uds_dtc_response(payload):
                uds_items.append((src, label, item))
        # Freeze-frame snapshots (0x19 0x04): one functional read per unique
        # code, attempted once per run (see _dtc_snapshots). Each ECU that
        # stores the code answers with its own snapshot; attribution by
        # response header attaches it to that ECU's DTC row.
        for code in dict.fromkeys(item["code"] for _, _, item in uds_items):
            if code in self._dtc_snapshots:
                continue
            try:
                self._dtc_snapshots[code] = self.conn.read_dtc_snapshots(code)
            except CommunicationError as exc:
                log.debug("Snapshot read failed for %s: %s", code, exc)
                self._dtc_snapshots[code] = {}
        for src, label, item in uds_items:
            code = item["code"]
            desc, _ = dtc_analysis.describe(code)
            cats = dtc_analysis.classify_dtc(code, item.get("status_byte"))
            freeze = {"status_byte": item.get("status_byte")}
            records = self._dtc_snapshots.get(code, {}).get(src)
            if records:
                freeze["snapshot"] = records
            self.repo.upsert_dtc(self.vehicle_id, ts, label, code, desc,
                                 cats, status="confirmed", freeze_frame=freeze)
            entry = {"ecu": label, "code": code,
                     "status_byte": item.get("status_byte")}
            if records:
                entry["snapshot"] = records
            all_dtcs.append(entry)
        return all_dtcs

    # -- main loop ----------------------------------------------------------------
    def _cycle_is_dead(self, snapshot: dict) -> bool:
        """True when the adapter answered nothing at all this cycle.

        A quiet vehicle is not a dead adapter: the OBD-II warm-up in
        initialize() already distinguishes "NO DATA" from a closed port, and a
        sleeping car still answers once woken. What separates the two here is
        that a sleeping/asleep vehicle yields a couple of refused DIDs while a
        dead adapter fails EVERY attempt, including the mode-01 handshake that
        needs no ECU cooperation.
        """
        return (self._cycle_attempts >= DEAD_CYCLE_MIN_ATTEMPTS
                and self._cycle_failures == self._cycle_attempts
                and not snapshot)

    def _recover_adapter(self) -> bool:
        """Re-open the adapter after a run of dead cycles. True if it is back.

        Closes the transport first: a Bluetooth SPP port whose far end has
        gone away frequently leaves the handle open but deaf, so re-running
        initialize() on it can silently do nothing.
        """
        self._dead_cycles += 1
        delay = min(RECONNECT_MAX_BACKOFF_S,
                    RECONNECT_BACKOFF_S * (2 ** min(self._dead_cycles - 1, 5)))
        log.error(
            "Adapter answered nothing for %d consecutive cycles (%d/%d reads "
            "failed, no data at all). Treating the adapter as gone rather than "
            "the vehicle being asleep. Reconnecting in %.0f s. This also "
            "happens if the phone running the Bluetooth link drops the car.",
            self._dead_cycles, self._cycle_failures, self._cycle_attempts,
            delay)
        try:
            self.conn.close()
        except Exception:
            log.debug("error closing a dead transport", exc_info=True)
        time.sleep(delay)
        try:
            self.conn.open()
            self.open_and_identify()
            # The vehicle may have driven or charged while we were blind; take
            # a full slow pass immediately rather than waiting out the interval.
            self._last_slow = -self.slow_interval
            self._cells_readable = True
            self._dtc_snapshots.clear()
            self._dead_cycles = 0
            log.info("Adapter recovered after %d dead cycle(s)", self._dead_cycles)
            return True
        except Exception as exc:
            log.error("Reconnect attempt failed (%s: %s)",
                      type(exc).__name__, exc)
            return False

    def run(self, max_cycles: int | None = None) -> None:
        interval = float(self.cfg.get("collector.poll_interval", 5.0))
        self.open_and_identify()
        self.discover_ecus()
        cycles = 0
        # Target the configured period rather than sleeping a whole interval
        # after each cycle: the work itself takes about a second, so the old
        # form ran every ~6.1 s against a 5 s setting.
        next_due = time.monotonic()
        while max_cycles is None or cycles < max_cycles:
            snap = self.collect_once()
            cycles += 1
            if self._cycle_is_dead(snap):
                # Do not record a DTC pass on a dead link, and do not log a
                # cycle summary of nothing: both add noise, not information.
                if self._dead_cycles == 0:
                    self._note_adapter_lost()
                if self._recover_adapter():
                    continue          # do not count the recovery as a cycle
                next_due = time.monotonic()
                continue
            if self._dead_cycles:
                log.info("Vehicle answering again after %d dead cycle(s)",
                         self._dead_cycles)
            self.read_and_store_dtcs()
            log.info("cycle %d: %s", cycles,
                     {k: v for k, v in snap.items() if k != "cell_v"})
            if max_cycles is None or cycles < max_cycles:
                next_due += interval
                delay = next_due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    # A cycle overran its slot. Resync rather than trying to
                    # catch up with back-to-back cycles, which would only make
                    # the adapter busier.
                    next_due = time.monotonic()

    def _note_adapter_lost(self) -> None:
        """Record the first dead cycle as an event, once per outage.

        The dashboard's 'what happened' view reads diagnostic_events, so a
        silent adapter that never appears in the logs still shows up there.
        """
        log.error(
            "Adapter stopped answering entirely (ignition off, Bluetooth link "
            "dropped, or the adapter was unplugged). No requests are reaching "
            "the vehicle and no data is being recorded.")
        if self.vehicle_id is None:
            return
        try:
            self.repo.add_event(
                self.vehicle_id, utcnow(), "adapter",
                "Adapter stopped answering; collection paused and reconnect "
                "attempts began")
        except Exception:
            log.debug("could not record adapter-loss event", exc_info=True)



