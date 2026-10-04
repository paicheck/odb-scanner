"""Figure out why the adapter is not connecting.

`main.py discover` tells you whether the car answered. This tells you *which
layer* stopped answering, because the failure looks identical from the outside
("NO DATA" everywhere) whether the cause is a wrong COM port, an unpaired
Bluetooth link, a locked port, a fixed protocol setting, or a sleeping car.

It walks the stack in order and stops reporting pass/fail per stage:

    1  configuration    is the adapter pointed at something plausible
    2  serial ports     does the port exist; is a Bluetooth SPP pair visible
    3  port open        can we open it, and is something else holding it
    4  adapter          does it identify as an ELM327
    5  protocol         which ATSP makes the bus answer, if any
    6  OBD-II bus       0100 / 0900 / 0902 - does anything on the car reply
    7  UDS              can we reach individual ECUs by request ID
    8  verdict          the most likely cause and what to try

Safety: every request that reaches the vehicle is passed through
uds.validate_request first, so this tool cannot transmit a write even if it is
edited. AT commands are adapter-local (reset, echo, protocol, header) and never
touch the car. Nothing here writes to the database.

    python tools/doctor.py                        # configured adapter
    python tools/doctor.py --tcp 127.0.0.1 35000  # simulator
    python tools/doctor.py --port COM7            # try another port
    python tools/doctor.py --all-ports            # probe every candidate port
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import ConfigError, load_config  # noqa: E402
from diagnostic import obd2, uds  # noqa: E402
from diagnostic.ecus import ECUS  # noqa: E402
from diagnostic.elm327 import Elm327Transport, TcpElm327  # noqa: E402
from diagnostic.interface import (  # noqa: E402
    AdapterNotFoundError,
    CommunicationError,
    detect_serial_ports,
)

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"

# Protocols worth trying. A fixed ATSP6 is a common cause of a working adapter
# showing NO DATA, so the doctor probes rather than trusting the config.
# (value, label, note)
PROTOCOLS = [
    ("6", "CAN 11-bit 500k", "standard for the ID.3 MEB CAN bus"),
    ("7", "CAN 29-bit 500k", "needed if the gateway uses extended IDs"),
    ("1", "CAN 11-bit 250k", "some body/ comfort CAN buses"),
    ("2", "CAN 29-bit 250k", "rare, but cheap adapters default to it"),
    ("0", "auto-detect", "let the adapter choose; slow but forgiving"),
    ("3", "ISO 9141-2", "K-line cars only"),
    ("4", "KWP2000", "K-line cars only"),
]

# One DID per ECU that the registry documents as readable.
_PROBE_DIDS = {
    "bat_mgmt": 0xF190,   # VIN-ish / part number territory
    "chg_mgmt": 0xF190,
    "mot_elec": 0xF190,
    "chg": 0xF190,
    "eld": 0xF190,
    "inf": 0xF190,
    "brk": 0xF190,
}


class Stage:
    """One diagnostic step and its outcome."""

    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.status = OK
        self.lines: list[str] = []
        self.notes: list[str] = []
        self.problems: list[str] = []
        self.data: dict = {}

    def line(self, text: str) -> None:
        self.lines.append(text)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def problem(self, text: str) -> None:
        """Record something wrong. FAIL only if nothing softer applies."""
        self.problems.append(text)

    def warn(self, text: str) -> None:
        self.problems.append(text)
        if self.status == OK:
            self.status = WARN

    def fail(self, text: str) -> None:
        self.problems.append(text)
        self.status = FAIL

    def render(self) -> str:
        dots = "." * max(1, 46 - len(self.title))
        head = f"[{self.number}/8] {self.title} {dots} {self.status}"
        out = [head]
        out += [f"      {ln}" for ln in self.lines]
        out += [f"      - {n}" for n in self.notes]
        out += [f"      ! {p}" for p in self.problems]
        return "\n".join(out)


class Doctor:
    def __init__(self, cfg, tcp=None, port_override=None,
                 all_ports=False, timeout=5.0):
        self.cfg = cfg
        self.tcp = tcp
        self.port_override = port_override
        self.all_ports = all_ports
        self.timeout = timeout
        self.transport: Elm327Transport | None = None
        self.identity = ""
        self.working_protocol: str | None = None

    # -- low-level helpers ---------------------------------------------------
    def _vehicle(self, cmd: str) -> list[str]:
        """Send a request to the car, refusing anything off the read-only list."""
        uds.validate_request(cmd)          # raises ReadOnlyViolationError
        assert self.transport is not None
        return self.transport.send_command(cmd)

    def _is_silent(self, lines: list[str]) -> bool:
        blob = " ".join(lines).upper()
        return (not blob) or "NO DATA" in blob or "UNABLE" in blob \
            or "BUS INIT" in blob or "ERROR" in blob

    def _is_ok(self, lines: list[str]) -> bool:
        blob = " ".join(lines).upper()
        return "OK" in blob and "NO DATA" not in blob

    # -- stage 1: configuration ---------------------------------------------
    def stage_config(self) -> Stage:
        s = Stage(1, "Configuration")
        atype = self.cfg.get("adapter.type", "elm327_serial")
        s.line(f"adapter.type      {atype}")
        if self.tcp:
            s.line(f"tcp override      {self.tcp[0]}:{self.tcp[1]}")
            return s
        port = self.port_override or self.cfg.get("adapter.port", "auto")
        s.line(f"adapter.port      {port}")
        s.line(f"adapter.baudrate  {self.cfg.get('adapter.baudrate', 38400)}")
        s.line(f"adapter.timeout   {self.cfg.get('adapter.timeout', 5.0)}")
        if atype not in ("elm327_serial", "elm327_tcp"):
            s.fail(f"unknown adapter.type {atype!r}; expected elm327_serial "
                   "or elm327_tcp")
            return s
        if atype == "elm327_tcp":
            s.line(f"tcp_host/tcp_port {self.cfg.get('adapter.tcp_host')} / "
                   f"{self.cfg.get('adapter.tcp_port')}")
        if atype == "elm327_serial" and not port:
            s.fail("adapter.port is empty - set it in config.yaml or pass "
                   "--port")
        return s

    # -- stage 2: port enumeration ------------------------------------------
    def stage_ports(self) -> Stage:
        s = Stage(2, "Serial ports")
        if self.tcp:
            s.line("skipped (TCP mode)")
            return s
        ports = detect_serial_ports()
        if not ports:
            s.warn("pyserial reports no serial ports at all")
            s.note("On Windows, check Device Manager > Ports (COM & LPT). "
                   "An unpaired Bluetooth SPP port does not appear here.")
            return s
        wanted = self.port_override or self.cfg.get("adapter.port", "auto")
        for p in ports:
            mark = "  <-- configured" if p["port"] == wanted else ""
            s.line(f"{p['port']:<8} {p['description']}{mark}")
        s.data["ports"] = [p["port"] for p in ports]

        if wanted not in ("auto", None, "") and wanted not in s.data["ports"]:
            # The single most common cause of "it doesn't connect": a COM port
            # that does not exist, or the wrong half of a Bluetooth pair.
            bt = [p for p in ports if "bluetooth" in p["description"].lower()
                  or "standard serial over bluetooth" in
                  p["description"].lower()]
            s.fail(f"{wanted} is not present")
            if bt:
                s.note("Bluetooth SPP ports visible: "
                       + ", ".join(p["port"] for p in bt))
                s.note("Windows creates TWO ports per Bluetooth serial "
                       "device: the OUTGOING one carries your bytes and the "
                       "INCOMING one does not. Connecting to the incoming "
                       "port opens cleanly and returns nothing at all.")
            s.note("Try: python tools/doctor.py --all-ports")
        return s

    # -- stage 3: open the port ---------------------------------------------
    def stage_open(self) -> Stage:
        s = Stage(3, "Port open")
        port = self.port_override or self.cfg.get("adapter.port", "auto")
        if self.tcp:
            host, tp = self.tcp
            self.transport = TcpElm327(host, tp, timeout=self.timeout)
            try:
                self.transport.open()
            except AdapterNotFoundError as exc:
                s.fail(str(exc))
                s.note("Is the simulator running? "
                       "python tools/start_scanner.py --status")
                return s
            s.line(f"connected to tcp {host}:{tp}")
            return s

        candidates = [port] if not self.all_ports else \
            (s.data.get("ports") or [port])
        last = ""
        for cand in candidates:
            t = Elm327Transport(port=cand,
                                baudrate=int(self.cfg.get("adapter.baudrate",
                                                           38400)),
                                timeout=self.timeout)
            try:
                t.open()
            except AdapterNotFoundError as exc:
                last = str(exc)
                s.line(f"{cand:<8} FAILED  {exc}")
                continue
            self.transport = t
            s.line(f"{cand:<8} opened")
            return s
        s.fail(last or "nothing could be opened")
        msg = last.lower()
        if self.tcp:
            s.note(f"Nothing is listening on {self.tcp[0]}:{self.tcp[1]}.")
            s.note("For the simulator: python tools/run_sim.py")
            s.note("For a TCP-serial bridge, check the host and port in "
                   "adapter.tcp_host / adapter.tcp_port.")
            return s
        if "access is denied" in msg or "permission" in msg \
                or "resource busy" in msg or "already open" in msg \
                or "device is busy" in msg:
            s.problems.pop()
            s.fail(f"the port is locked by another process: {last}")
            s.note("Close the dashboard (`main.py serve`), any running "
                   "`main.py collect`, tools/elm_console.py, and the "
                   "Veepeak app -- only one process may hold the port.")
            s.note("On Bluetooth SPP a second opener gets this reliably; "
                   "there is no sharing without a driver that supports it.")
        return s

    # -- stage 4: adapter identity ------------------------------------------
    def stage_adapter(self) -> Stage:
        s = Stage(4, "Adapter identity")
        if self.transport is None:
            s.fail("no open transport")
            return s
        t = self.transport
        try:
            time.sleep(0.3)
            t.send_command("ATZ")
            time.sleep(0.3)
            resp = t.send_command("ATI")
        except CommunicationError as exc:
            s.fail(f"no response to ATI: {exc}")
            s.note("The port opened but nothing speaks ELM327 on it. Almost "
                   "always one of: the Bluetooth SPP outgoing port is not the "
                   "one you picked, the device is not actually paired, or the "
                   "adapter needs vehicle ignition power before it answers.")
            s.note("Check the raw bytes with: "
                   "python tools/elm_console.py --no-init")
            return s
        self.identity = " ".join(resp).strip()
        s.line(f"ATI  {self.identity}")
        blob = self.identity.upper()
        if not blob or blob == "UNKNOWN":
            s.fail("adapter did not identify itself")
            s.note("Some very cheap clones answer '?' or an empty line. Those "
                   "are usually unusable for UDS even when they answer.")
            return s
        known = ("ELM327" in blob or "OBDLINK" in blob or "STN" in blob
                 or "SIM" in blob)
        if not known:
            s.warn(f"unexpected identity {self.identity!r}")
            s.note("Not a recognised ELM327 family string. If the bus stages "
                   "below answer, ignore this; if they do not, the clone may "
                   "not implement the protocol it advertises.")
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATCAF1"):
            try:
                t.send_command(cmd)
            except CommunicationError:
                s.warn(f"adapter rejected {cmd}")
        return s

    # -- stage 5: protocol negotiation --------------------------------------
    def stage_protocol(self) -> Stage:
        s = Stage(5, "Protocol negotiation")
        if self.transport is None:
            s.fail("no open transport")
            return s
        t = self.transport
        configured = "6"
        responders = []
        refused = []
        for value, label, _note in PROTOCOLS:
            try:
                set_resp = t.send_command(f"ATSP{value}")
                time.sleep(0.15)
                t.set_header(None)
                lines = self._vehicle("0100")
            except CommunicationError as exc:
                s.line(f"ATSP{value} {label:<18} error: {exc}")
                continue
            blob = " ".join(set_resp).upper()
            if "?" in blob or "ERROR" in blob or "STOPPED" in blob:
                # The adapter declined to change protocol. It is therefore
                # pinned to whatever it was already on, which is a completely
                # different problem from the car being asleep, and it is
                # visible here and nowhere else.
                refused.append(value)
                s.line(f"ATSP{value} {label:<18} REFUSED "
                       f"({blob.strip() or 'no acknowledgement'})")
                continue
            if self._is_silent(lines):
                s.line(f"ATSP{value} {label:<18} no answer")
            else:
                s.line(f"ATSP{value} {label:<18} BUS ANSWERED  "
                       f"{' '.join(lines)[:40]}")
                responders.append(value)
                if self.working_protocol is None:
                    self.working_protocol = value
        s.data["refused"] = refused
        if refused and not responders:
            s.fail("the adapter refused every protocol change and the bus "
                   "never answered")
            s.note("This is not a sleeping car: the adapter would not even "
                   "change protocol. It is stuck on the protocol it powered up "
                   "with.")
            s.note("Unplug the adapter completely (not just the OBD side), "
                   "wait a few seconds, plug it back in, and re-run. That "
                   "resets the pinned protocol on most units.")
            s.note("If it persists, the adapter's firmware is ignoring ATSP - "
                   "common on cheap clones and on some Bluetooth OBDII "
                   "firmwares.")
        elif responders:
            if configured not in responders:
                s.warn(f"only ATSP{','.join(responders)} works, but the "
                       f"collector uses ATSP{configured}")
                s.note("This is very likely your bug: a fixed protocol the "
                       "car is not on produces NO DATA everywhere. "
                       f"Set adapter.baudrate aside and change the ATSP in "
                       f"diagnostic/elm327.py initialize() to ATSP"
                       f"{responders[0]}.")
            else:
                s.line(f"configured ATSP{configured} works")
            if len(responders) > 1:
                s.note(f"several protocols answered ({','.join(responders)}); "
                       "the bus may be alive but the adapter auto-detects "
                       "poorly - keep ATSP explicit.")
        else:
            s.fail("no protocol produced a bus response to 0100")
            s.note("The adapter is alive (stage 4 passed) but the car is not "
                   "answering. In order of likelihood:")
            s.note("  a) the vehicle is asleep - turn the ignition on. An "
                   "ELM327 draws almost no current and many gateways only "
                   "wake on ignition.")
            s.note("  b) the OBD connector is not fully seated, or the "
                   "adapter is plugged into a socket that is not powered.")
            s.note("  c) a cheap clone cannot do the CAN filtering this car "
                   "needs; an OBDLink MX or a Veepeak unit can.")
            s.note("  d) 29-bit vs 11-bit mismatch on the gateway ID - all "
                   "variants were tried above, so this is unlikely.")
        return s

    # -- stage 6: OBD-II -----------------------------------------------------
    def stage_obd(self) -> Stage:
        s = Stage(6, "OBD-II bus")
        if self.transport is None:
            s.fail("no open transport")
            return s
        if self.working_protocol is None:
            # Every protocol already timed out on 0100; repeating the same
            # request with a different header only costs another timeout each.
            s.fail("not attempted - no protocol produced a bus response")
            return s
        t = self.transport
        if self.working_protocol:
            t.send_command(f"ATSP{self.working_protocol}")
        t.set_header(None)
        t.set_receive_address(None)

        try:
            lines = self._vehicle("0100")
        except (CommunicationError, uds.ReadOnlyViolationError) as exc:
            s.fail(str(exc))
            return s
        if self._is_silent(lines):
            s.fail(f"0100 (supported PIDs) -> {' '.join(lines) or 'empty'}")
            return s
        blob = " ".join(lines).upper()
        s.line(f"0100  {' '.join(lines)[:48]}")
        if "SEARCHING" in blob or "BUS INIT" in blob:
            s.warn("adapter is still searching for the bus")
            s.note("A gateway that answers slowly needs more than "
                   "adapter.timeout seconds. Raise adapter.timeout to 10-15.")
        bits = _supported_pid_bits(lines)
        if bits:
            s.line(f"       supported PID bitmask {bits}")

        for cmd, name in (("0900", "PID 00 (protocol)"),
                          ("0902", "VIN"),
                          ("03", "DTCs")):
            try:
                got = self._vehicle(cmd)
            except (CommunicationError, uds.ReadOnlyViolationError) as exc:
                s.line(f"{cmd}  error: {exc}")
                continue
            if self._is_silent(got):
                s.line(f"{cmd}  {name:<18} no answer")
                continue
            if cmd == "0902":
                try:
                    vin = obd2.parse_vin_response(got)
                except Exception as exc:                    # noqa: BLE001
                    s.line(f"{cmd}  VIN undecodable ({exc})")
                    continue
                if vin:
                    s.line(f"{cmd}  VIN               {vin}")
                    s.data["vin"] = vin
                else:
                    s.line(f"{cmd}  VIN               not returned")
                    s.warn("mode 09 works but the VIN did not decode")
                    s.note("A gateway that supports 09 but not 02 is common "
                           "on some models; UDS is the more reliable path.")
            else:
                s.line(f"{cmd}  {name:<18} {' '.join(got)[:36]}")

        if not s.data.get("vin") and not s.problems:
            s.warn("OBD-II answered but nothing decoded cleanly")
        return s

    # -- stage 7: UDS --------------------------------------------------------
    def stage_uds(self) -> Stage:
        s = Stage(7, "UDS by request ID")
        if self.transport is None:
            s.fail("no open transport")
            return s
        if self.working_protocol is None:
            s.fail("not attempted - the bus never answered a single request")
            return s
        t = self.transport
        hit, tried = [], 0
        for key, spec in ECUS.items():
            did = _PROBE_DIDS.get(key, 0xF190)
            tried += 1
            try:
                t.set_header(spec.tx)
                t.set_receive_address(spec.rx)
                lines = self._vehicle(f"22{did:04X}")
            except (CommunicationError, uds.ReadOnlyViolationError) as exc:
                s.line(f"{key:<10} tx={spec.tx:03X} error: {exc}")
                continue
            if self._is_silent(lines):
                s.line(f"{key:<10} tx={spec.tx:03X} no answer")
                continue
            payload = uds.parse_elm_lines(lines)
            if not payload:
                s.line(f"{key:<10} tx={spec.tx:03X} answered "
                       f"{' '.join(lines)[:26]} (undecodable)")
                hit.append(key)
                continue
            if payload[0] == 0x7F:
                # An NRC still proves the ECU is alive and speaking UDS; only
                # this DID is not supported. That distinction is the whole
                # point of the stage, so it must not be reported as success.
                nrc = payload[2] if len(payload) > 2 else 0
                s.line(f"{key:<10} tx={spec.tx:03X} ALIVE, DID not "
                       f"supported (NRC 0x{nrc:02X})")
                hit.append(key)
                continue
            s.line(f"{key:<10} tx={spec.tx:03X} OK  "
                   f"{payload.hex().upper()[:32]}")
            hit.append(key)
        s.data["uds_hits"] = hit
        if hit:
            s.line(f"{len(hit)}/{tried} ECUs answered a UDS read")
        else:
            s.fail("no ECU answered a UDS read")
            s.note("OBD-II and UDS are different addressing. If 0100 worked "
                   "but no 22 read did, the adapter is likely resetting the "
                   "header between requests, or the ECU IDs differ from the "
                   "registry (run tools/elm_console.py and try "
                   "ATSH7E5 then 22F190 by hand).")
        return s

    # -- stage 8: verdict ----------------------------------------------------
    def verdict(self, stages: list[Stage]) -> tuple[list[str], list[str]]:
        causes: list[str] = []
        fixes: list[str] = []
        by_title = {st.title: st for st in stages}
        cfg = by_title.get("Configuration")
        ports = by_title.get("Serial ports")
        opened = by_title.get("Port open")
        ident = by_title.get("Adapter identity")
        proto = by_title.get("Protocol negotiation")
        obd = by_title.get("OBD-II bus")
        uds = by_title.get("UDS by request ID")

        if cfg and cfg.status == FAIL:
            causes.append("The adapter is not configured to anything usable.")
            fixes.append("Fix adapter.type/adapter.port in config.yaml.")
            return causes, fixes

        if ports and ports.status == FAIL:
            causes.append(f"The configured COM port does not exist.")
            fixes.append("Run with --all-ports to find the real one.")
            fixes.append("On Windows Bluetooth SPP, choose the OUTGOING port "
                         "(the one whose description reads 'outgoing'); the "
                         "incoming port opens but carries no data.")
            fixes.append("Re-pair the device in Windows Settings > Bluetooth "
                         "& devices, then re-run.")
            return causes, fixes

        if opened and opened.status == FAIL:
            causes.append("The port exists but could not be opened.")
            fixes.append("Close anything else holding it: the dashboard, "
                         "`main.py collect`, tools/elm_console.py, and the "
                         "Veepeak phone/desktop app.")
            fixes.append("Unplug and replug the adapter, then re-pair.")
            return causes, fixes

        if ident and ident.status == FAIL:
            causes.append("The port opened but no ELM327 answered on it.")
            fixes.append("Confirm you picked the outgoing Bluetooth SPP port.")
            fixes.append("Try the other baud rate / a USB adapter if you have "
                         "one (`--port`, adapter.baudrate).")
            fixes.append("Use tools/elm_console.py --no-init to see the raw "
                         "bytes actually arriving.")
            return causes, fixes

        if ident and ident.status == WARN and any(
                "unexpected identity" in p for p in ident.problems):
            causes.append("The adapter answered but does not identify as a "
                          "known ELM327 family.")
            fixes.append("Cheap clones often lie about their capabilities. If "
                         "the bus stages below pass, ignore this.")

        if proto and proto.status == FAIL:
            if proto.data.get("refused"):
                causes.append("The adapter refused to change protocol, so it "
                              "is pinned to whatever protocol it powered up "
                              "with -- and the car is not on that one.")
                fixes.append("Unplug the adapter completely, wait a few "
                             "seconds, plug it back in, then re-run. That "
                             "resets the pinned protocol on most units.")
                fixes.append("If it still refuses, the firmware is ignoring "
                             "ATSP -- common on cheap clones and some "
                             "Bluetooth OBDII firmwares. A USB OBDLink or "
                             "Veepeak dongle avoids it.")
            else:
                causes.append("The adapter works, but the car never answered. "
                              "This is a vehicle-side or power problem, not a "
                              "tool problem.")
                fixes.append("Turn the ignition ON and leave it on. The ID.3 "
                             "gateway will not answer over OBD when asleep.")
                fixes.append("Reseat the OBD plug; on many cars it is under "
                             "the centre console and easy to leave "
                             "half-seated.")
                fixes.append("If the adapter is Bluetooth, confirm it is "
                             "powered from the OBD socket - it cannot run on "
                             "USB alone.")
            return causes, fixes

        if proto and proto.status == WARN:
            causes.append("The car answered on a different protocol than the "
                          "collector forces.")
            fixes.append("Change the ATSP in "
                         "diagnostic/elm327.py initialize() as suggested "
                         "above - this alone may fix `main.py discover`.")
            fixes.append("Then re-run `python main.py discover`.")

        if obd and obd.status == FAIL:
            causes.append("OBD-II functional requests got no answer.")
            fixes.append("Raise adapter.timeout to 10-15 s if the gateway is "
                         "slow to wake.")
            fixes.append("Confirm the ignition is on and the adapter is seated.")
            return causes, fixes

        if uds and uds.status == FAIL and obd and obd.status != FAIL:
            causes.append("OBD-II works but UDS addressing does not.")
            fixes.append("A functional 0100 answering while every 22xx read "
                         "goes silent points at header handling: use "
                         "tools/elm_console.py and try `ATSH7E5` then `22F190` "
                         "by hand to see whether an ECU answers.")
            fixes.append("Check whether another program is sharing the port "
                         "and interleaving commands.")

        if obd and obd.status != FAIL or uds and uds.status != FAIL:
            causes.append("The adapter is connecting. Any remaining problem is "
                          "above this tool, in the collector or the config.")
        return causes, fixes


def _supported_pid_bits(lines: list[str]) -> str:
    """Best-effort decode of the 0100 bitmask, for reporting only."""
    try:
        payload = uds.parse_elm_lines(lines)
    except Exception:                                        # noqa: BLE001
        return ""
    if not payload:
        return ""
    blob = payload.hex().upper()
    return f"{blob[:8]}" if len(blob) >= 8 else ""


def run(cfg, tcp=None, port_override=None, all_ports=False, timeout=5.0,
        verbose=True) -> tuple[list[Stage], list[str], list[str]]:
    d = Doctor(cfg, tcp=tcp, port_override=port_override,
               all_ports=all_ports, timeout=timeout)
    plan = (("Configuration", d.stage_config),
            ("Serial ports", d.stage_ports),
            ("Port open", d.stage_open),
            ("Adapter identity", d.stage_adapter),
            ("Protocol negotiation", d.stage_protocol),
            ("OBD-II bus", d.stage_obd),
            ("UDS by request ID", d.stage_uds))
    stages: list[Stage] = []
    opened = False
    adapter_broken = False
    # Each stage is isolated: a diagnostic tool that dies on the first
    # exception is useless precisely when something is already broken.
    for number, (title, build) in enumerate(plan, 1):
        needs_adapter = title in ("Adapter identity", "Protocol negotiation",
                                  "OBD-II bus", "UDS by request ID")
        if needs_adapter and (not opened or adapter_broken):
            reason = ("no adapter was opened" if not opened else
                      "the adapter never identified itself, so probing the "
                      "bus through it would only repeat the timeout")
            # Without a port there is nothing to say beyond "not reached", and
            # repeating the same error four times buries the real cause.
            stage = Stage(number, title)
            stage.status = SKIP
            stage.line(f"not reached - {reason}")
        else:
            try:
                stage = build()
            except Exception as exc:                         # noqa: BLE001
                stage = Stage(number, title)
                stage.fail(f"stage raised {type(exc).__name__}: {exc}")
        opened = opened or d.transport is not None
        if stage.status == "FAIL" and title == "Adapter identity":
            adapter_broken = True
        stages.append(stage)
        if verbose:
            print(stage.render())
            print()
    if d.transport is not None:
        try:
            d.transport.close()
        except Exception:                                    # noqa: BLE001
            pass
    causes, fixes = d.verdict(stages)
    return stages, causes, fixes


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diagnose why the adapter is not connecting.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Every stage is read-only: no write can reach the vehicle.")
    ap.add_argument("--config", default=None, help="alternative config file")
    ap.add_argument("--port", default=None,
                    help="override adapter.port for this run")
    ap.add_argument("--tcp", nargs=2, metavar=("HOST", "PORT"),
                    help="talk to a TCP adapter/simulator instead of serial")
    ap.add_argument("--all-ports", action="store_true",
                    help="try every serial port until one opens")
    ap.add_argument("--timeout", type=float, default=None,
                    help="per-command timeout in seconds")
    ap.add_argument("--only-config", action="store_true",
                    help="print configuration and exit without touching "
                         "hardware")
    args = ap.parse_args()

    print("ID.3 connection doctor " + "=" * 46)
    print("read-only: this tool cannot transmit a write to the vehicle\n")

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration is unusable:\n  {exc}")
        return 1
    if args.only_config:
        for key in ("adapter.type", "adapter.port", "adapter.baudrate",
                    "adapter.timeout", "adapter.tcp_host", "adapter.tcp_port"):
            print(f"  {key:<20} {cfg.get(key)}")
        return 0

    timeout = args.timeout or float(cfg.get("adapter.timeout", 5.0))
    tcp = tuple(args.tcp) if args.tcp else None
    if tcp:
        tcp = (tcp[0], int(tcp[1]))

    code, causes, fixes = run(cfg, tcp=tcp, port_override=args.port,
                              all_ports=args.all_ports, timeout=timeout)

    print("Verdict " + "=" * 52)
    if causes:
        for i, c in enumerate(causes, 1):
            print(f"  {i}. {c}")
    else:
        print("  Connected, and nothing above flagged a problem.")
    if fixes:
        print("\n  Try, in order:")
        for i, f in enumerate(fixes, 1):
            print(f"  {i}. {f}")
    return 0 if all(st.status != FAIL for st in code) else 1


if __name__ == "__main__":
    raise SystemExit(main())