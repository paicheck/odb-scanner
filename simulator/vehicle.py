"""Vehicle simulator: emulates an ELM327 + VW ID.3 over TCP.

Purpose: develop and test the ENTIRE pipeline without a vehicle. The
simulator answers ELM327 AT commands, standard OBD-II modes 01/03/09, and
UDS 0x22 DIDs for the battery/charging ECUs registered in decoders/.

Responses use realistic synthetic values with slow drift. It is obviously
NOT a real ID.3; every report generated against simulator data must be
treated as test data only.
"""
from __future__ import annotations

import math
import random
import socketserver
import struct
import threading
import time

# The VIN the simulator answers with. Deliberately NOT the car's: a simulator
# that reports the real VIN is indistinguishable from the car as soon as its
# rows are written next to measured ones, which is how a month of simulated
# readings (and a seeded example history) became part of this vehicle's own
# record. Override at run time with `main.py simulate` (config: simulation.vin).
DEMO_VIN = b"WVWZZZE1ZMS000001"
VIN = DEMO_VIN


def set_vin(vin) -> None:
    """Point the simulator at another VIN. None/empty leaves it alone."""
    global VIN
    if not vin:
        return
    VIN = vin.encode() if isinstance(vin, str) else bytes(vin)

# --- synthetic vehicle state -------------------------------------------------
_state_lock = threading.Lock()
_start = time.time()


def _drift(base: float, amplitude: float, period_s: float) -> float:
    t = time.time() - _start
    return base + amplitude * math.sin(2 * math.pi * t / period_s) \
        + random.uniform(-amplitude * 0.05, amplitude * 0.05)


def pack_voltage() -> float:
    return _drift(355.0, 8.0, 900)


def pack_current() -> float:
    return _drift(-12.0, 30.0, 300)  # negative = charging


def soc() -> float:
    return _drift(64.0, 12.0, 3600)


def cell_v(i: int, n: int = 102) -> float:
    base = 3.82 + 0.35 * soc() / 100.0
    offset = _drift(0.0, 0.004 + 0.00002 * i, 1800 + i)
    return base + offset + random.uniform(-0.0005, 0.0005)


def battery_temp() -> float:
    return _drift(24.0, 4.0, 1200)


def ecu_temp() -> float:
    return _drift(38.0, 6.0, 600)


def charge_mode() -> int:
    # cycles idle -> AC charging for demo purposes
    return 1 if int((time.time() - _start) / 120) % 2 == 0 else 0


def twelve_v() -> float:
    return _drift(14.1, 0.35, 240)


# --- DID table ---------------------------------------------------------------
def _enc_u16(v: float) -> bytes:
    return int(max(0, min(65535, round(v)))).to_bytes(2, "big")


def _did_value(did: int) -> bytes | None:
    with _state_lock:
        if did == 0x1E3B:
            return _enc_u16(pack_voltage() * 64)
        if did == 0x1E3D:
            return _enc_u16((pack_current() + 2048) * 5)
        if did == 0x028C:
            return bytes([round(soc() * 2.5) & 0xFF])
        if did == 0x1E33:
            return _enc_u16(max(cell_v(i) for i in range(4)) * 256)
        if did == 0x1E34:
            return _enc_u16(min(cell_v(i) for i in range(4)) * 256)
        if did == 0x2A0B:
            return _enc_u16((battery_temp() + 40) * 64)
        if 0x1E40 <= did < 0x1EA6:
            return _enc_u16(cell_v(did - 0x1E40) * 256)
        if did == 0x1E32:
            return (123456).to_bytes(4, "big") + (98765).to_bytes(4, "big")
        if did == 0x74CB:
            return _enc_u16(1623) + b"\x00" * 6
        if did == 0x1DD6:
            return bytes([charge_mode()])
        if did == 0x1DD0:
            return bytes([round(soc() * 2) & 0xFF])
        if did == 0x41FC:
            return _enc_u16(232)
        if did == 0x41FB:
            return bytes([round(_drift(16.0, 2.0, 120) * 10) & 0xFF])
        if did == 0x1DE4:
            return _enc_u16(42)
        if did == 0x1DEC:
            return bytes([1])
        if did == 0xF190:
            return VIN    # VIN DID: real MEB ECUs answer this functionally
        return None


# --- MEB module persona ------------------------------------------------------
# Real MEB modules answer only at their physical address, reached with
# ATCP <priority> + 6-digit ATSH. The simulator implements that so the MEB
# addressing path (Elm327Transport.set_module / DiagnosticConnection._transmit)
# can be exercised end to end without a car. Values are MEB-scaled -- the
# point of the exercise is that the MEB decoder formulas reproduce sane
# engineering values, not that the sim matches the e-Up persona above.
#
# Not all MEB modules are on the 29-bit bus: the energy and climate modules
# sit at plain 11-bit ids (evDash/spot2000), so their response headers are
# 3 hex digits, not 8.
_MEB_CELL_SLOTS = 108          # 0x1E40..0x1EAB
_MEB_CELL_ACTIVE = 102         # slots 102..107 answer 0x0FFE (unpopulated)
_MEB_MAX_ENERGY_WH = 53200.0   # rated HV energy content (SoH reference)
_MEB_CONTENT_WH = 41200.0      # energy content parked at ~81 % SoC
_MEB_ODO_KM = 48123.0
_MEB_12V_V = 14.52

# request id -> response id header as the adapter prints it
MEB_MODULES = {
    0x17FC007B: "17FE007B",   # HV battery management
    0x17FC00B9: "17FE00B9",   # DC/DC converter
    0x00000710: "77A",        # gateway energy information (11-bit)
    0x17FC0076: "17FE0076",   # vehicle info (odo/gear/VIN)
    0x00000746: "7B0",        # climate control (11-bit)
    0x17FC007C: "17FE007C",   # electric drive (motor/inverter)
    0x00000767: "7D1",        # GPS / telematics (11-bit)
}


def _meb_did_value(req29: int, did: int) -> bytes | None:
    """MEB-scaled DID payload for one module, or None -> NRC 0x31."""
    with _state_lock:
        if req29 == 0x17FC007B:
            if did == 0x028C:
                return bytes([round(soc() * 2.5) & 0xFF])
            if did == 0x1E3B:
                return _enc_u16(pack_voltage() * 4)
            if did == 0x1E3D:
                return int(pack_current() * 100 + 150000).to_bytes(4, "big")
            if did == 0x1E33:
                hi = max(range(4), key=cell_v)
                return _enc_u16(cell_v(hi) * 4096) + _enc_u16(hi + 1)
            if did == 0x1E34:
                lo = min(range(4), key=cell_v)
                return _enc_u16(cell_v(lo) * 4096) + _enc_u16(lo + 1)
            if did == 0x2A0B:
                return bytes([round((battery_temp() + 40) * 2) & 0xFF])
            if did == 0x1E0E:
                return _enc_u16((battery_temp() + 1.25) * 64) + _enc_u16(1)
            if did == 0x1E0F:
                return _enc_u16((battery_temp() - 1.25) * 64) + _enc_u16(2)
            if did == 0x1E1B:
                return _enc_u16(213 * 5)
            if did == 0x1E1C:
                return _enc_u16(400 * 5)
            if did == 0x1E32:
                # Four s32 fields: charge Ah, discharge Ah, charge kWh,
                # discharge kWh (CS MEB profile #342).
                def s32(v: float) -> bytes:
                    return int(v).to_bytes(4, "big", signed=True)
                return (s32(58196.5) + s32(-57808.3) +
                        s32(24436.98) + s32(-23459.86))
            if did == 0x7448:
                # 0 standby / 1 driving / 4 AC charging / 6 DC charging
                return bytes([4 if charge_mode() else 0])
            if did == 0x743B:
                return bytes([35])
            if did == 0x1620:
                return bytes([0])                 # no PTC draw when parked
            if did == 0x189D:
                # outlet = u16[0:2]/64, inlet = u16[2:4]/64
                return _enc_u16((battery_temp() + 2.0) * 64) + \
                       _enc_u16((battery_temp() + 0.0) * 64)
            if did == 0xF40D:
                return bytes([0])                 # parked
            if did == 0x0500:
                return b"SIMHV0000000001"
            if 0x1E40 <= did < 0x1E40 + _MEB_CELL_SLOTS:
                i = did - 0x1E40
                if i >= _MEB_CELL_ACTIVE:
                    return _enc_u16(0x0FFE)      # unpopulated slot
                return _enc_u16((cell_v(i) - 1.0) * 1000)
            if 0x1EAE <= did <= 0x1EBD or did in (0x7425, 0x7426):
                # u16 = (degC + 40) * 8
                return _enc_u16((battery_temp() + 40) * 8)
            return None
        if req29 == 0x00000710:
            if did == 0x2AB2:
                # SYNTHETIC FIXTURE, not a capture. Layout per the Car Scanner
                # MEB profile (idx 342): SBI=0, DL=2, MUL=50 Wh.
                return _enc_u16(round(_MEB_MAX_ENERGY_WH / 50.0))
            if did == 0x2AB8:
                # SYNTHETIC FIXTURE, not a capture. SBI=2, DL=2, MUL=25 Wh, so
                # the value sits at offset 2 behind two filler bytes.
                return b"\x00\x00" + _enc_u16(round(_MEB_CONTENT_WH / 25.0))
            if did == 0x2AF7:
                return _enc_u16((_MEB_12V_V - 4.0) * 1000)
            return None
        if req29 == 0x17FC00B9:
            if did == 0x465B:
                return _enc_u16(14.1 * 16)
            if did == 0x465D:
                return _enc_u16(14.1 * 512)
            return None
        if req29 == 0x17FC0076:
            if did == 0x295A:
                return int(_MEB_ODO_KM).to_bytes(3, "big")
            if did == 0x210E:
                return bytes([0x00, 0x08])       # 08 = P
            if did == 0x0364:
                return _enc_u16(0.45 * 10)       # ~0.45 kW of auxiliaries
            if did == 0xF802:
                return VIN
            return None
        if req29 == 0x00000746:
            if did == 0x2609:
                return bytes([round((14.0 + 50) * 2) & 0xFF])
            if did == 0x2613:
                return _enc_u16((18.5 + 40) * 5)
            return None
        if req29 == 0x17FC007C:
            if did == 0x3E93:
                # DC link voltage, 400 V at rest, IEEE-754 big-endian float32
                # (FLOAT32(A,B,C,D) in the Car Scanner profile). The point is
                # that the module answers at all -- a dead 29-bit path must show
                # up as a NULL for the whole module, not as a plausible value.
                return struct.pack(">f", 400.0)
            if did == 0x4FAE:
                return struct.pack(">f", 16.18)
            return None
        if req29 == 0x00000767:
            if did == 0x2431:
                return bytes([12, 9])        # satellites visible, tracked
            return None
    return None


# --- protocol handling --------------------------------------------------------
ECU_TABLE = {
    0x7E5: 0x7ED,   # BMS
    0x765: 0x7CF,   # charge management
    0x744: 0x7AE,   # OBC
    0x7E0: 0x7E8,   # motor electronics (standard OBD-II responder)
}


def _isotp_encode(payload: bytes) -> list[bytes]:
    n = len(payload)
    if n <= 7:
        return [bytes([n]) + payload]
    frames = [bytes([0x10 | ((n >> 8) & 0x0F), n & 0xFF]) + payload[:6]]
    rest, seq = payload[6:], 1
    while rest:
        frames.append(bytes([0x20 | (seq % 16)]) + rest[:7])
        rest, seq = rest[7:], seq + 1
    return frames


def _build_response(req: bytes, tx: int) -> list[bytes] | None:
    """Build UDS positive response frames for a request, or None."""
    if not req:
        return None
    sid = req[0]
    if sid == 0x22 and len(req) >= 3:
        did = (req[1] << 8) | req[2]
        val = _did_value(did)
        if val is None:
            # ISO-TP single frame: PCI length byte first, then 7F <sid> NRC.
            # A real ELM327 with headers on shows "7E8 03 7F 22 31"; emitting
            # the 7F bare made reassemble() treat it as an unknown frame type
            # and return None, so every NRC looked like "no response" instead
            # of "this ECU is alive but does not have that DID".
            return [bytes([0x03, 0x7F, 0x22, 0x31])]  # NRC requestOutOfRange
        return _isotp_encode(bytes([0x62, req[1], req[2]]) + val)
    if sid == 0x10:
        return _isotp_encode(
            bytes([0x50, req[1] if len(req) > 1 else 0x01, 0x00, 0x19, 0x01, 0xF4])
        )
    if sid == 0x3E:
        # TesterPresent positive response, single frame with PCI length.
        return [bytes([0x02, 0x7E, 0x00])]
    if sid == 0x19 and len(req) >= 3 and req[1] == 0x02:
        # one confirmed DTC: U1123 00 (D1 23 00), status 0x2F. Returned to
        # any requester: the collector addresses functionally (no ATSH is
        # ever sent), so the "current ECU" no longer distinguishes who is
        # being asked -- the simulator answers as its single persona.
        return _isotp_encode(
            bytes([0x59, 0x02, 0xFF, 0xD1, 0x23, 0x00, 0x2F])
        )
    if sid == 0x19 and len(req) >= 3 and req[1] == 0x04:
        # reportDTCSnapshotRecordNumber: the demo DTC (D1 23 00) carries a
        # snapshot -- record 01 with one identifier (pack voltage DID
        # 0x1E3B at the live DID's scale) -- so freeze-frame data flows
        # through the whole pipeline. Codes the simulator does not store
        # get NRC 0x31, like a real ECU.
        if len(req) < 6:
            return [bytes([0x03, 0x7F, 0x19, 0x13])]
        # request layout: 19 04 <DTC(3)> <recordNumber> -- the DTC starts at
        # index 2 (unlike the RESPONSE, where a statusAvailabilityMask byte
        # shifts it to index 3).
        if req[2:5] == bytes([0xD1, 0x23, 0x00]):
            snap = (bytes([0xD1, 0x23, 0x00, 0x2F,   # DTC + status
                           0x01, 0x01,               # record 01, 1 identifier
                           0x1E, 0x3B])              # DID: pack voltage
                    + _enc_u16(pack_voltage() * 64))
            return _isotp_encode(bytes([0x59, 0x04, 0xFF]) + snap)
        return [bytes([0x03, 0x7F, 0x19, 0x31])]     # requestOutOfRange
    if sid == 0x01 and len(req) >= 2:  # standard OBD-II via motor ECU
        pid = req[1]
        if pid == 0x00:
            return [bytes([0x06, 0x41, 0x00, 0xBE, 0x3F, 0xA8, 0x13])]
        if pid == 0x42:
            v = round(twelve_v() * 1000)
            return [bytes([0x06, 0x41, 0x42, (v >> 8) & 0xFF, v & 0xFF, 0x00, 0x00])]
        if pid == 0x0D:
            return [bytes([0x03, 0x41, 0x0D, 38])]
        # PCI byte first, as for 0x22 above: a bare 7F 01 12 parsed as silence.
        return [bytes([0x03, 0x7F, 0x01, 0x12])]  # subFunctionNotSupported
    if sid == 0x09 and len(req) >= 2 and req[1] == 0x02:
        data = bytes([0x49, 0x02, 0x01]) + VIN
        return _isotp_encode(data)
    if sid == 0x03:
        return [bytes([0x02, 0x43, 0x00, 0x00, 0x00])]  # no DTCs
    return [bytes([0x03, 0x7F, sid, 0x11])]  # serviceNotSupported, with PCI


class SimHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(0.5)
        buf = b""
        self.cp = 0x18           # CAN id priority/priority byte (ATCP)
        self.caf = 1            # CAN auto-formatting (ATCAF)
        self.meb_module = None  # 29-bit MEB module id, or None = functional
        self.current_ecu = (0x7E0, 0x7E8)
        # Clone-strict mode (SimServer strict=True): reproduce the documented
        # refusals of the field VEEPEAK clone, so a transport regression fails
        # here instead of only on the car. Off by default, because the harness
        # contract tests deliberately send the forms the real clone refuses.
        self.strict = bool(getattr(self.server, "strict", False))
        while True:
            try:
                chunk = self.request.recv(1024)
            except TimeoutError:
                # Idle keep-alive: a real ELM327 link stays open between polls
                # (the collector only talks every collector.poll_interval), so a
                # recv timeout must NOT be treated as a disconnect. Closing here
                # would reset the link and leave the client stuck.
                continue
            except OSError:
                return  # client went away
            if not chunk:
                return
            buf += chunk
            while b"\r" in buf:
                line, buf = buf.split(b"\r", 1)
                cmd = line.decode("ascii", "replace").strip().upper()
                if not cmd:
                    continue
                if cmd.startswith("ATSH"):
                    out = self._set_header(cmd)
                else:
                    out = self._handle_cmd(cmd)
                for text in out:
                    self.request.sendall((text + "\r").encode("ascii"))
                self.request.sendall(b">")

    def _set_header(self, cmd: str) -> list[str]:
        """ATSH: 3-digit legacy (11-bit) or 6-digit MEB (with the CP byte).

        The 6-digit form is what ELM327-class adapters accept for MEB module
        addressing (ATCP supplies the priority byte); the completed 29-bit id
        is (cp << 24) | value. Returns the adapter's reply lines (normally
        none, or ['?'] when clone-strict mode refuses the header).
        """
        arg = cmd[4:].replace(" ", "")
        if self.strict and len(arg) == 8:
            # The field clone refuses the 8-digit ATSH a raw 29-bit id would
            # need (see Elm327Transport.set_header), which is why MEB modules
            # are addressed with ATCP + 6 digits. Modelling the refusal means a
            # regression to an 8-digit header fails the test suite.
            return ["?"]
        if len(arg) == 6:
            try:
                hdr = (self.cp << 24) | int(arg, 16)
            except ValueError:
                return []
            self.meb_module = hdr if hdr in MEB_MODULES else None
            # An unknown MODULE header must fall silent. Leaving current_ecu
            # on the 11-bit motor made the simulator answer an unaddressed
            # request as 0x7E8, which is unfaithful: on a real bus an id
            # nothing listens on gets NO DATA. That mattered because the MEB
            # experiment matrix relies on this being the negative control --
            # if every arm answered, a NO DATA on the car would say nothing
            # about the car.
            #
            # "Unknown" excludes the functional header, which is also 6 hex
            # digits (DB33F1 under ATCP 18) and legitimately reaches an ECU.
            # The priority byte is what separates the two cases: modules are
            # addressed with ATCP 17, functional with ATCP 18.
            if self.meb_module is not None or self.cp == 0x18:
                self.current_ecu = (0x7E0, 0x7E8)
            else:
                self.current_ecu = None
            return []
        try:
            tx = int(arg, 16) & 0x7FF
        except ValueError:
            return []
        if tx == 0x7DF:                            # functional addressing
            self.current_ecu = (0x7DF, 0x7E8)
        elif tx in ECU_TABLE:
            self.current_ecu = (tx, ECU_TABLE[tx])
        else:
            # A 3-digit header under ATSP6 addresses an 11-bit id directly,
            # independent of ATCP. The energy (0x710) and climate (0x746)
            # modules are 11-bit, so Car Scanner's gateway sequence sends
            # 'ATSH 710' with no ATCP 00 first -- cp may still hold the
            # previous module's priority byte. Match the bare id before the
            # (cp << 24) | tx form, which only matches under ATCP 00.
            if tx in MEB_MODULES:
                self.meb_module = tx
                self.current_ecu = (0x7E0, 0x7E8)
            elif (self.cp << 24) | tx in MEB_MODULES:
                self.meb_module = (self.cp << 24) | tx
                self.current_ecu = (0x7E0, 0x7E8)
            else:                                  # no ECU lives there
                self.meb_module = None
                self.current_ecu = None
        return []

    def _handle_cmd(self, cmd: str) -> list[str]:  # noqa: C901
        import diagnostic.uds as uds

        if cmd.startswith("AT"):
            if cmd == "ATZ":
                time.sleep(0.2)
                self.cp, self.caf, self.meb_module = 0x18, 1, None
                self.current_ecu = (0x7E0, 0x7E8)
                return ["ELM327 v1.5 SIM"]
            if cmd == "ATI":
                return ["SIM327 v1.5 (odb_scanner simulator)"]
            c = cmd.replace(" ", "")
            if c == "ATRV":
                # An awake car: the DC-DC converter holds the 12 V rail up.
                return ["14.2V"]
            if c.startswith("ATCP") and len(c) == 6:
                try:
                    self.cp = int(c[4:6], 16)
                except ValueError:
                    return ["?"]
                return ["OK"]
            if c.startswith("ATCAF") and len(c) == 6:
                if self.strict and c[5:6] == "0":
                    # Car Scanner's captured MEB sequence is ATCAF1 throughout;
                    # the field clone answers the hand-framed ATCAF0 line with
                    # '?'. Refuse the command in clone-strict mode so a
                    # regression back to hand-framing is caught here.
                    return ["?"]
                self.caf = int(c[5:6])
                return ["OK"]
            return ["OK"]
        hexstr = uds.clean_hex(cmd)
        if len(hexstr) % 2:
            return ["?"]
        try:
            req = bytes.fromhex(hexstr)
        except ValueError:
            return ["?"]
        if self.strict and self.caf == 1 and len(req) > 7:
            # Auto-formatting (ATCAF1) accepts at most 7 request bytes -- the
            # adapter adds the ISO-TP PCI byte itself. An 8-byte hand-built
            # line is answered '?' by the clone, which is exactly how the old
            # hand-framed 29-bit path died on the car.
            return ["?"]
        if self.meb_module is not None:
            return self._meb_response(req)
        ecu = self.current_ecu
        if ecu is None:
            return ["NO DATA"]
        # choose ECU: default functional/motor; ATSH already applied by adapter
        resp_frames = _build_response(req, ecu[0])
        if resp_frames is None:
            return ["NO DATA"]
        return [f"{ecu[1]:03X}" + f.hex().upper() for f in resp_frames]

    def _meb_response(self, req: bytes) -> list[str]:
        """Answer as a physically addressed MEB module (8-digit header).

        Under ATCAF0 the client builds the ISO-TP single frame itself, so the
        PCI byte arrives here and is stripped. Frames go out with PCI bytes
        included (that is what a real adapter does under ATH1 either way).
        """
        if not req:
            return ["NO DATA"]
        if not self.caf and (req[0] >> 4) == 0:
            n = req[0] & 0x0F
            req = req[1:1 + n]
        if not req:
            return ["NO DATA"]
        req29 = self.meb_module
        sid = req[0]
        if sid == 0x22 and len(req) >= 3:
            did = (req[1] << 8) | req[2]
            val = _meb_did_value(req29, did)
            if val is None:
                return [MEB_MODULES[req29] + "037F2231"]
            frames = _isotp_encode(bytes([0x62, req[1], req[2]]) + val)
            return [MEB_MODULES[req29] + f.hex().upper() for f in frames]
        if sid == 0x10:
            frames = _isotp_encode(bytes([0x50, req[1] if len(req) > 1 else 0x01,
                                           0x00, 0x19, 0x01, 0xF4]))
            return [MEB_MODULES[req29] + f.hex().upper() for f in frames]
        if sid == 0x3E:
            return [MEB_MODULES[req29] + "027E00"]
        return [MEB_MODULES[req29] + bytes([0x03, 0x7F, sid, 0x11]).hex().upper()]


class _SimTCPServer(socketserver.ThreadingTCPServer):
    """Threading server whose handler threads cannot hold the process open.

    socketserver.ThreadingTCPServer defaults to non-daemon handler threads, so
    any client that connects and then goes away without closing (a failed
    assertion, a crashed caller, Ctrl-C) leaves a thread parked in recv() and
    the interpreter never exits. allow_reuse_address is set here rather than
    by mutating socketserver.ThreadingTCPServer, which would leak into every
    other TCP server in the process.
    """

    allow_reuse_address = True
    daemon_threads = True
    # Clone-strict mode for every connection this server handles. Set by
    # SimServer before serve_forever; read by SimHandler from self.server.
    strict = False


class SimServer:
    def __init__(self, host: str, port: int, strict: bool = False,
                 vin: str | bytes | None = None):
        self.host, self.port = host, port
        # strict=True makes the simulator reproduce the field clone's
        # documented refusals (8-digit ATSH, ATCAF0, an >7-byte request under
        # ATCAF1) instead of accepting everything. The default remains
        # permissive so the experiment-harness contract tests can still send
        # the forms the real clone refuses.
        self.strict = strict
        # VIN the simulated vehicle reports; None keeps whatever is current. The
        # VIN lives in a module global because the DID tables that answer 0xF190
        # and 0x17FE0076 are plain functions, so this sets it for the process --
        # one simulator per process is the only arrangement that was ever used.
        # stop() puts the previous value back, so a caller that pins a VIN for
        # one server does not silently change the next one's identity.
        self._prev_vin = VIN
        set_vin(vin)
        self._srv = None
        self._thread = None

    def start(self) -> None:
        self._srv = _SimTCPServer((self.host, self.port), SimHandler)
        self._srv.strict = self.strict
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        # See __init__: the VIN is process-global, so hand back whatever was
        # current before this server claimed it.
        set_vin(self._prev_vin)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

