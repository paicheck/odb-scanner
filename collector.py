"""Diagnostic collector: connects, discovers, reads, stores. READ-ONLY.

Every vehicle request it transmits is logged via repo.log_tx with a purpose
string (the adapter's AT configuration commands are not vehicle requests and
are not logged). It only uses DiagnosticConnection methods, which can only
produce:
  * ELM327 AT commands (configuration, not vehicle commands)
  * OBD-II modes 01/03/09 (read)
  * UDS 0x10 0x01, 0x22, 0x19 0x02/0x04 (read) — enforced by
    diagnostic/uds.py:validate_request at DiagnosticConnection._transmit
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
from diagnostic.uds import NegativeResponseError, ProtocolError

log = logging.getLogger(__name__)

# Adapter identification strings that mean "this is our own simulator, not a
# car". The simulator announces itself as an ELM327 with this ATI response; a
# real adapter answers "ATI ELM327 v1.5" (Veepeak) or the OBDLink equivalent.
SIMULATOR_MARKERS = ("odb_scanner simulator", "sim327")


def is_simulator(description: str) -> bool:
    """True when an adapter identification string belongs to the simulator."""
    low = (description or "").lower()
    return any(marker in low for marker in SIMULATOR_MARKERS)


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

# Pacing when a cycle raises rather than merely finds no data. Short, because
# the commonest cause is transient -- SQLite's busy timeout expiring while the
# dashboard reads, or a transient I/O error -- and stopping collection for a
# minute to retry a write is worse than the error. Backs off to a minute so a
# genuinely full or corrupt disk does not spin a core all day.
COLLECT_ERROR_BACKOFF_S = 2.0
COLLECT_ERROR_MAX_BACKOFF_S = 60.0

# How many consecutive failed cycles to report before going quiet. The log gets
# one line per failure with a traceback; past this it repeats once and then only
# logs when the count changes, so a long outage stays readable.
CYCLE_FAIL_REPORT_EVERY = 10

# Which polled PIDs get persisted, as (measurement key, snapshot key, unit,
# doc). PID 0x00 is the supported-bitmask handshake, not a value, so it is
# deliberately absent. A decoded PID missing from this table would be
# silently dropped -- which is how vehicle speed went unrecorded.
OBD_PID_KEYS = {
    0x0D: ("vehicle_speed", "speed_kmh", "km/h", "SAE J1979 PID 0x0D"),
    0x42: ("lv_voltage_obd", "lv_voltage_v", "V",
           "SAE J1979 PID 0x42 (control module voltage ~ 12 V system)"),
}


def _int_hint(value) -> int | None:
    """Parse an optional VID/PID config hint. Accepts an int or a hex string
    ("0x0403" / "0403"); anything unusable is None, never a crash."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


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
        # Optional discovery hints. They prioritise matching devices during
        # auto-detection but never bypass the ATI probe, so a stale or wrong
        # hint degrades to the normal behaviour rather than mis-selecting.
        vid_hint=_int_hint(a.get("adapter.vid")),
        pid_hint=_int_hint(a.get("adapter.pid")),
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
        # Set by open_and_identify() when the adapter turns out to be the
        # simulator. Every row this run writes keeps an honest source label
        # (see _prov) and the cloud uploader skips it outright.
        self.simulated = False
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
        # Previous cycle's UDS DTC set as {(ecu_label, code): status_byte}. None
        # means "no baseline yet" -- the first successful read only records the
        # baseline, it does not emit warning-on/off, so a collector restart does
        # not replay a lamp that was already on when it booted. The next read
        # diffs against this to emit `dtc-cleared` when a code stops appearing.
        self._prev_dtc_status: dict[tuple[str, str], int | None] | None = None
        # (ecu_key, did) pairs this vehicle has rejected with NRC 0x31, loaded
        # once the vehicle is known. poll_did() skips these and adds new ones,
        # so a DID the car does not implement is not re-polled every cycle.
        self._unsupported: set[tuple[str, int]] = set()
        # Silent-DID retirement: a DID that has NEVER succeeded on this vehicle
        # and returns NO DATA (not NRC) for `silent_retire_threshold` consecutive
        # reads is also retired. Two guards keep that honest. `_ever_succeeded`:
        # silence on a DID that has answered before means "car asleep", not "DID
        # dead", so it is never retired. `_phase_ok_ecus`: silence only counts
        # when the DID's module answered something in the same phase, because a
        # module that said nothing at all was unreachable and its silence says
        # nothing about the DID (see _end_silence_phase).
        self._ever_succeeded: set[tuple[str, int]] = set()
        self._silent_failures: dict[tuple[str, int], int] = {}
        # Bookkeeping for the reachability guard: which ECUs answered in the
        # phase now running, and the silent reads buffered during it.
        self._phase_ok_ecus: set[str] = set()
        self._pending_silent: dict[tuple[str, int], int] = {}
        self.silent_retire_threshold = int(
            self.cfg.get("collector.silent_retire_threshold", 3))
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
        self._failed_cycles = 0
        self._reported_cycle_failures = 0

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
        description = self.conn.t.description()
        self.simulated = is_simulator(description)
        self.repo.add_event(self.vehicle_id, utcnow(), "adapter",
                            f"Connected via {description}; "
                            f"VIN read = {vin}")
        self._adopt_open_session()
        self._unsupported = self.repo.unsupported_did_keys(self.vehicle_id)
        self._ever_succeeded = self.repo.ever_succeeded_keys(self.vehicle_id)
        if self.simulated:
            log.warning(
                "Adapter identifies as the simulator (%s): this run writes "
                "provenance='simulated' and is never uploaded to the cloud. "
                "Simulator output belongs in its own database "
                "(simulation.db_path), not the car's.", description)
        log.info("Identified vehicle VIN=%s year=%s unsupported_dids=%d "
                 "ever_succeeded=%d", vin, year, len(self._unsupported),
                 len(self._ever_succeeded))
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

        Statuses: responder | refused | nrc-<code> | no-response |
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
                payloads, refused = self.conn.functional_probe_with_reason(
                    f"22{did:04X}",
                    f"UDS 0x22 DID 0x{did:04X} discovery probe ({key}"
                    + (f", module {spec.tx29:08X}" if spec.tx29 else
                       ", functional") + ")",
                    ecu=spec)
            except CommunicationError as exc:
                log.debug("ECU %s probe failed: %s", key, exc)
                payloads = {}
                refused = False
            for src, p in payloads.items():
                if (p and p[0] == 0x62 and len(p) >= 3
                        and ((p[1] << 8) | p[2]) == did):
                    status = "responder"
                    self.ecu_addresses[key] = src
                    break
                if p and p[0] == 0x7F and len(p) > 2 and status == "no-response":
                    status = f"nrc-{p[2]:02X}"
            if refused and status == "no-response":
                status = "refused"
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
                                     or bat == "no-response"
                                     or bat == "refused"):
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
    def _prov(self, default: Provenance) -> str:
        """Source label for a row this run writes.

        A simulated run keeps 'simulated' whatever the decoder claims: the
        value looks like a reading and was computed exactly the way a real one
        is, so writing 'reported' would misstate where the number came from.
        """
        return (Provenance.SIMULATED if self.simulated else default).value

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
            spec.unit, self._prov(spec.provenance),
            "builtin" if spec.decode else "raw-only",
            spec.doc_status.value,
            raw.hex().upper() if raw else "", value, text_value,
            success, error,
        )

    def poll_did(self, spec, ts: str) -> bytes | None:
        key = (spec.ecu_key, spec.did)
        if key in self._unsupported:
            return None
        ecu = ECUS[spec.ecu_key]
        try:
            raw = self.conn.read_did(ecu, spec.did)
            self._record(spec, raw, ts)
            self._attempt(True)
            self._ever_succeeded.add(key)
            self._phase_ok_ecus.add(spec.ecu_key)
            self._silent_failures.pop(key, None)
            return raw
        except NegativeResponseError as exc:
            self._record(spec, None, ts, error=str(exc), success=False)
            self._attempt(False)
            # NRC 0x31 = this car does not implement that DID. Remember it so
            # the collector stops re-polling a known-dead DID every cycle.
            if exc.nrc == 0x31:
                self._retire_did(key, ts)
            return None
        except ProtocolError as exc:
            # The ECU answered but not with a decodable positive or negative
            # response. That is an addressing/decoder problem, not silence, so
            # it does not count toward silent-DID retirement.
            self._record(spec, None, ts, error=str(exc), success=False)
            self._attempt(False)
            return None
        except CommunicationError as exc:
            # NO DATA: the ECU said nothing. Buffered, not retired: whether the
            # silence is meaningful depends on whether the module answered at
            # all, which is only known once the phase ends.
            self._record(spec, None, ts, error=str(exc), success=False)
            self._attempt(False)
            self._note_silent(key)
            return None

    def _retire_did(self, key: tuple[str, int], ts: str) -> None:
        """Mark a DID retired so it is skipped from the next cycle on."""
        self._unsupported.add(key)
        self.repo.mark_did_unsupported(
            self.vehicle_id, key[0], key[1], ts)

    def _begin_silence_phase(self) -> None:
        """Start a stretch of reads over which module reachability is judged.

        One entry per rotation of the registry (the fast DIDs, then the slow
        DIDs with the cell sweep), so a module that answered earlier in the same
        rotation is not credited to a read it never saw.
        """
        self._phase_ok_ecus = set()
        self._pending_silent = {}

    def _note_silent(self, key: tuple[str, int]) -> None:
        """Buffer one NO DATA read for _end_silence_phase to judge."""
        if key in self._ever_succeeded:
            # It answered before, so silence here means "car asleep", not "DID
            # dead". Never retire a DID that has ever produced data.
            return
        self._pending_silent[key] = self._pending_silent.get(key, 0) + 1

    def _end_silence_phase(self, ts: str) -> None:
        """Count the phase's silent reads -- but only where that proves anything.

        Retirement says "this car does not implement that DID", so it is only
        sound when the module was actually answering. On 2026-10-17 every DID on
        the 29-bit bus (BMS, DC-DC, vehicle-info, electric drive) returned NO
        DATA for a whole cycle while the 11-bit modules answered normally: the
        adapter had stopped reaching the 29-bit bus. The unconditional rule then
        retired 72 DIDs in a single pass -- including the odometer, the VIN and
        the gear selector -- and re-reading them the next day returned every
        value. So a silent read counts only when the same ECU produced at least
        one successful read in this same phase. A module that said nothing at
        all was unreachable, and "we never reached it" is not evidence about
        what it implements.
        """
        pending, self._pending_silent = self._pending_silent, {}
        answered, self._phase_ok_ecus = self._phase_ok_ecus, set()
        for key, silent in pending.items():
            if key[0] not in answered:
                log.debug(
                    "Not retiring %s DID 0x%04X: module answered nothing this "
                    "phase (unreachable, so silence is uninformative)",
                    key[0], key[1])
                continue
            count = self._silent_failures.get(key, 0) + silent
            if count >= self.silent_retire_threshold:
                self._retire_did(key, ts)
                self._silent_failures.pop(key, None)
            else:
                self._silent_failures[key] = count

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
                f"0x{pid:02X}", unit, self._prov(Provenance.REPORTED), doc,
                "documented", data.hex().upper(), value,
            )
            snapshot[snap_key] = value

        # fast battery DIDs
        self._begin_silence_phase()
        for spec in self.registry.fast():
            raw = self.poll_did(spec, ts)
            if raw is not None and spec.decode:
                v = spec.decode_value(raw)
                if not isinstance(v, dict):
                    snapshot[spec.key] = v
        self._end_silence_phase(ts)

        # computed values (labelled calculated)
        voltage, current = snapshot.get("pack_voltage"), snapshot.get("pack_current")
        if voltage is not None and current is not None:
            snapshot["pack_power_kw"] = round(voltage * current / 1000.0, 3)
            self.repo.record_measurement(
                self.vehicle_id, ts, "pack_power_kw", "bat_mgmt", "calc", "-",
                "kW", self._prov(Provenance.CALCULATED),
                "voltage * current / 1000", "calculated", "",
                snapshot["pack_power_kw"],
            )
        vmin, vmax = snapshot.get("cell_voltage_min"), snapshot.get("cell_voltage_max")
        if vmin is not None and vmax is not None:
            delta_mv = round((vmax - vmin) * 1000.0, 1)
            snapshot["cell_delta_mv"] = delta_mv
            self.repo.record_measurement(
                self.vehicle_id, ts, "cell_delta_mv", "bat_mgmt", "calc", "-",
                "mV", self._prov(Provenance.CALCULATED),
                "(max - min) * 1000", "calculated", "", delta_mv,
            )

        # slow phase: per-cell sweep + non-cell slow DIDs. Reads 107 DIDs, so
        # it runs on its own cadence (collector.slow_poll_interval) rather than
        # on every fast poll.
        if self._slow_phase_due():
            # The cell sweep and the slow DIDs are one rotation: a module that
            # answered a cell DID has been reached, so its slow DIDs are judged
            # against the same evidence.
            self._begin_silence_phase()
            if self.cfg.get("collector.read_cell_voltages", True) \
                    and self._cells_readable:
                self._sweep_cells(ts)
            self._slow_pass(ts, snapshot)
            self._end_silence_phase(ts)
        self._record_lv_derived(ts, snapshot)
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
        """Poll the non-cell slow DIDs; put their scalar values in the snapshot.

        Per-cell DIDs are excluded here because _sweep_cells() already reads
        them in the same slow phase. Decoding the returned raw bytes again is
        cheap and lets later derived values (12 V power, DC-DC power) use the
        same rotation instead of waiting for the next fast pass.
        """
        for spec in self.registry.slow():
            if spec.key.startswith("cell_v_"):
                continue
            raw = self.poll_did(spec, ts)
            if raw is not None and spec.decode:
                v = spec.decode_value(raw)
                if not isinstance(v, dict):
                    snapshot[spec.key] = v

    def _record_lv_derived(self, ts: str, snapshot: dict) -> None:
        """Record low-voltage power from voltage x current, when both exist.

        These are the measurements the low-voltage investigation kept asking
        for but that no DID reports: 12 V terminal power, the DC/DC module's
        output power, and the gateway's DC-DC estimate. They are labelled
        CALCULATED and kept out of the battery snapshot; the raw current and
        voltage rows remain the source of truth.
        """
        for key, vkey, ikey, unit, ecu, scale, formula in (
            ("aux_12v_power_w", "aux_12v_voltage", "aux_12v_current",
             "W", "bat_mgmt", 1.0, "12V voltage * current"),
            ("dcdc_power_kw", "dcdc_voltage", "dcdc_current",
             "kW", "dcdc", 0.001, "DC/DC voltage * current / 1000"),
            ("dcdc_gw_power_kw", "dcdc_gw_voltage", "dcdc_gw_current",
             "kW", "energy", 0.001, "gateway DC-DC voltage * current / 1000"),
        ):
            voltage, current = snapshot.get(vkey), snapshot.get(ikey)
            if voltage is None or current is None:
                continue
            value = round(voltage * current * scale, 3)
            snapshot[key] = value
            self.repo.record_measurement(
                self.vehicle_id, ts, key, ecu, "calc", "-", unit,
                self._prov(Provenance.CALCULATED), formula, "calculated",
                "", value,
            )

    def _record_spreads(self, ts: str, val, val_json) -> None:
        """Record cell-SoC and battery-temperature spreads from extreme DIDs.

        The cheapest health signals we can derive, and they need no second
        read: the extreme values arrive with the same payload as their 1-based
        cell/sensor numbers. ``val``/``val_json`` are the snapshot closures from
        _battery_snapshot so this uses the same freshness ceiling.
        """
        soc_max = val_json("cell_soc_max").get("value")
        soc_min = val_json("cell_soc_min").get("value")
        if soc_max is not None and soc_min is not None:
            self.repo.record_measurement(
                self.vehicle_id, ts, "cell_soc_spread_pct", "bat_mgmt",
                "calc", "-", "%", self._prov(Provenance.CALCULATED),
                "max cell SoC - min cell SoC", "calculated", "",
                round(soc_max - soc_min, 3),
            )
        temp_max, temp_min = val("bat_temp_max"), val("bat_temp_min")
        if temp_max is not None and temp_min is not None:
            self.repo.record_measurement(
                self.vehicle_id, ts, "battery_temp_spread_c", "bat_mgmt",
                "calc", "-", "degC", self._prov(Provenance.CALCULATED),
                "max sensor temperature - min sensor temperature",
                "calculated", "", round(temp_max - temp_min, 3),
            )

    def _battery_snapshot(self, ts: str, snap: dict) -> None:
        # Bound how old a carried-forward value may be. Without this the
        # fallback below is "the last value that ever succeeded", so a DID that
        # failed for days would be re-stamped with today's ts and the row would
        # mix fresh and week-old readings -- which then becomes the baseline
        # that anomaly.py compares against.
        latest = self.repo.latest_measurements(self.vehicle_id,
                                               max_age_s=self.max_value_age_s,
                                               live_only=True)

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
                self._prov(Provenance.ESTIMATED), soh_basis,
                "estimated", "", soh,
            )
        self._record_spreads(ts, val, val_json)
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
                self.vehicle_id, max_age_s=self.max_value_age_s,
                live_only=True)
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
        known (learned functionally, or a documented request/response id), else
        a plain address label."""
        for key, addr in self.ecu_addresses.items():
            if addr == src:
                return key
        if src is not None:
            for key, spec in ECUS.items():
                if spec.rx == src or spec.rx29 == src:
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
        #
        # The status mask decides which DTCs the ECU reports. It used to be
        # 0x08 (confirmed only), which cannot see a fault that is lighting the
        # dash before it is confirmed. The default is now 0x8F -- warning
        # indicator (0x80), confirmed (0x08), pending (0x04), failed-this-cycle
        # (0x02) and testFailed (0x01) -- still a single request, so this is a
        # wider read rather than extra polling. It is configurable because the
        # right mask is a judgement call, not a fact.
        mask = str(self.cfg.get("collector.dtc_status_mask", "8F")).upper()
        if len(mask) != 2 or any(ch not in "0123456789ABCDEF" for ch in mask):
            log.warning("collector.dtc_status_mask=%r is not a hex byte; "
                        "using the default 8F", mask)
            mask = "8F"
        read_ok = False
        try:
            payloads = self.conn.functional_probe(
                f"1902{mask}", "UDS 0x19 0x02 ReadDTCInformation (status mask "
                               f"0x{mask}, warning+active+pending+confirmed "
                               "DTCs) - functional, read-only")
            # A response from any ECU (even an empty 0x59 list) is a real read.
            # An empty mapping means no ECU answered, which is a silent failure,
            # not "no DTCs" -- read_ok stays False so it can never read as a
            # clear.
            read_ok = bool(payloads)
        except CommunicationError as exc:
            log.debug("UDS DTC read failed: %s", exc)
            payloads = {}
        # A functional request on 18DB33F1 cannot reach a module that only
        # answers physical requests. eldrive is read physically for its DIDs,
        # so its DTCs are asked for the same way; otherwise an electric-drive
        # fault can light the dash for weeks while this tool sees no code at
        # all. Each configured ECU costs one request per cycle, so the list is
        # deliberately short and configurable.
        extra_ecus = self.cfg.get("collector.dtc_ecu_keys", ["eldrive"]) or []
        for key in extra_ecus:
            spec = ECUS.get(key)
            if spec is None or getattr(spec, "tx29", None) is None:
                continue
            try:
                extra = self.conn.functional_probe(
                    f"1902{mask}", f"UDS 0x19 0x02 ReadDTCInformation from "
                                   f"{spec.name} (status mask 0x{mask}, "
                                   f"physical, read-only)",
                    ecu=spec)
            except CommunicationError as exc:
                log.debug("Physical DTC read failed for %s: %s", key, exc)
                continue
            for src, payload in extra.items():
                if payload and payload[0] == 0x59:
                    payloads.setdefault(src, payload)
        uds_items: list[tuple[int | None, str, dict]] = []
        seen_items: set[tuple[str, str, int | None]] = set()
        # ECUs that gave a positive 0x59 this cycle, even an empty list. Only
        # their codes can be judged "no longer reported" below.
        answered: set[str] = set()
        for src, payload in payloads.items():
            if not payload or payload[0] != 0x59:
                continue
            label = self._ecu_label_for_source(src)
            answered.add(label)
            for item in obd2.parse_uds_dtc_response(payload):
                sig = (label, item["code"], item.get("status_byte"))
                if sig in seen_items:
                    continue
                seen_items.add(sig)
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
                                 cats, status=dtc_analysis.status_label(
                                     item.get("status_byte")),
                                 freeze_frame=freeze)
            entry = {"ecu": label, "code": code,
                     "status_byte": item.get("status_byte")}
            if records:
                entry["snapshot"] = records
            all_dtcs.append(entry)

        # ---- warning-lamp / DTC off-transition detection -------------------
        # A code that fully clears drops out of the 0x19 0x02 response, and that
        # absence is the only off-signal for it (bit 7 flipping while the code is
        # still stored is recovered separately in analysis.timeline from the
        # status-byte history). Guarded by `read_ok` so a failed read can never
        # masquerade as "the lamp went out"; the first successful read only sets
        # the baseline so a collector restart does not replay a lamp that was
        # already on when it booted.
        #
        # Absence only counts for an ECU that answered THIS cycle. read_ok is
        # true when any ECU answered, and the electric drive is read with a
        # separate physical request whose failure is skipped silently -- so an
        # ECU that missed one cycle used to have every code logged as cleared
        # (or "warning lamp off") and then reappear. Codes of an ECU that did
        # not answer are carried forward unjudged.
        if read_ok:
            curr = {(label, item["code"]): item.get("status_byte")
                    for _, label, item in uds_items}
            prev = self._prev_dtc_status
            if prev is None:
                self._prev_dtc_status = curr
            else:
                for key, was in prev.items():
                    if key in curr:
                        continue
                    if key[0] not in answered:
                        curr[key] = was
                        continue
                    was_warning = bool(was) and bool(was & 0x80)
                    desc = (
                        f"Warning lamp off: {key[1]} on {key[0]} "
                        "(code no longer reported)" if was_warning
                        else f"DTC cleared: {key[1]} on {key[0]}")
                    self.repo.add_event(
                        self.vehicle_id, ts, "dtc-cleared", desc, ecu=key[0],
                        data={"code": key[1], "was_warning": was_warning})
                self._prev_dtc_status = curr
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
            try:
                snap = self.collect_once()
            except Exception as exc:
                # A cycle must not be able to end collection. collect_once()
                # swallows CommunicationError per read, but nothing above it
                # caught a storage failure: "database is locked" after the
                # busy timeout, SQLITE_FULL, an I/O error, or a TypeError from
                # the timestamp arithmetic in close_session() would all
                # propagate out of cmd_collect and kill the process. Nothing
                # restarts it, so the collector dies silently and the dashboard
                # just goes stale -- you lose the rest of the drive with no
                # error anywhere a user would look.
                #
                # Back off so a permanently broken store does not spin a core,
                # and let a normal cycle clear it. Reconnecting is NOT
                # attempted: the adapter is usually fine, and an ATZ storm
                # would only make a full disk worse.
                self._failed_cycles += 1
                pause = min(COLLECT_ERROR_MAX_BACKOFF_S,
                            COLLECT_ERROR_BACKOFF_S * self._failed_cycles)
                if (self._failed_cycles <= 3
                        or self._failed_cycles % CYCLE_FAIL_REPORT_EVERY == 0):
                    log.error("Collection cycle %d failed (%s: %s); retrying in "
                              "%.0fs. Collection continues -- this is not fatal.",
                              cycles + 1, type(exc).__name__, exc, pause,
                              exc_info=True)
                    self._reported_cycle_failures = self._failed_cycles
                elif self._failed_cycles == self._reported_cycle_failures + 1:
                    log.error("...still failing (%d consecutive cycle failures, "
                              "last: %s: %s)", self._failed_cycles,
                              type(exc).__name__, exc)
                    self._reported_cycle_failures = self._failed_cycles
                self._note_cycle_failed(exc)
                time.sleep(pause)
                next_due = time.monotonic()
                continue
            self._failed_cycles = 0
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

    def _note_cycle_failed(self, exc: BaseException) -> None:
        """Record the first failed cycle as an event, once per outage.

        A cycle that raises is different from a cycle that finds nothing: the
        adapter is probably fine and the failure is in storage or in code. The
        distinction matters when someone reads the dashboard's "what happened"
        view months later, so it is written down and not just logged -- a
        failing database means nothing at all is being recorded, including this.
        """
        if self.vehicle_id is None or self._failed_cycles != 1:
            return
        log.error("Collection is failing and is being retried, not abandoned. "
                  "If this persists, nothing is being recorded: check free "
                  "disk space and that nothing else has the database open.")
        try:
            self.repo.add_event(
                self.vehicle_id, utcnow(), "adapter",
                f"Collection cycle raised {type(exc).__name__}: {exc}. "
                "Retried with backoff; the adapter was not reset."
            )
        except Exception:
            # The store is probably the thing that is broken.
            log.debug("could not record cycle-failure event", exc_info=True)

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



