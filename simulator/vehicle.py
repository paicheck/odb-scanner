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
import socket
import socketserver
import threading
import time

VIN = b"WVWZZZE1ZMP087053"

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
            return [bytes([0x7F, 0x22, 0x31])]  # NRC requestOutOfRange
        return _isotp_encode(bytes([0x62, req[1], req[2]]) + val)
    if sid == 0x10:
        return _isotp_encode(
            bytes([0x50, req[1] if len(req) > 1 else 0x01, 0x00, 0x19, 0x01, 0xF4])
        )
    if sid == 0x3E:
        return [bytes([0x7E, 0x00])]
    if sid == 0x19 and len(req) >= 3 and req[1] == 0x02:
        # one confirmed DTC: U1123 00 (D1 23 00), status 0x2F (charge-mgmt only)
        if tx == 0x765:
            return _isotp_encode(
                bytes([0x59, 0x02, 0xFF, 0xD1, 0x23, 0x00, 0x2F])
            )
        return _isotp_encode(bytes([0x59, 0x02, 0xFF]))
    if sid == 0x01 and len(req) >= 2:  # standard OBD-II via motor ECU
        pid = req[1]
        if pid == 0x00:
            return [bytes([0x06, 0x41, 0x00, 0xBE, 0x3F, 0xA8, 0x13])]
        if pid == 0x42:
            v = round(twelve_v() * 1000)
            return [bytes([0x06, 0x41, 0x42, (v >> 8) & 0xFF, v & 0xFF, 0x00, 0x00])]
        if pid == 0x0D:
            return [bytes([0x03, 0x41, 0x0D, 38])]
        return [bytes([0x7F, 0x01, 0x12])]
    if sid == 0x09 and len(req) >= 2 and req[1] == 0x02:
        data = bytes([0x49, 0x02, 0x01]) + VIN
        return _isotp_encode(data)
    if sid == 0x03:
        return [bytes([0x02, 0x43, 0x00, 0x00, 0x00])]  # no DTCs
    return [bytes([0x7F, sid, 0x11])]  # serviceNotSupported


class SimHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(0.5)
        buf = b""
        current_ecu = (0x7E0, 0x7E8)
        while True:
            try:
                chunk = self.request.recv(1024)
            except socket.timeout:
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
                if cmd.startswith("ATSH") and len(cmd) >= 7:
                    try:
                        tx = int(cmd[4:], 16) & 0x7FF
                        if tx == 0x7DF:            # functional addressing
                            current_ecu = (0x7DF, 0x7E8)
                        elif tx in ECU_TABLE:
                            current_ecu = (tx, ECU_TABLE[tx])
                        else:                      # no ECU lives there
                            current_ecu = None
                    except ValueError:
                        pass
                else:
                    out = self._handle_cmd(cmd, current_ecu)
                    for text in out:
                        self.request.sendall((text + "\r").encode("ascii"))
                self.request.sendall(b">")

    def _handle_cmd(self, cmd: str, ecu) -> list[str]:  # noqa: C901
        import diagnostic.uds as uds

        if cmd.startswith("AT"):
            if cmd == "ATZ":
                time.sleep(0.2)
                return ["ELM327 v1.5 SIM"]
            if cmd == "ATI":
                return ["SIM327 v1.5 (odb_scanner simulator)"]
            return ["OK"]
        if ecu is None:
            return ["NO DATA"]
        hexstr = uds.clean_hex(cmd)
        if len(hexstr) % 2:
            return ["?"]
        try:
            req = bytes.fromhex(hexstr)
        except ValueError:
            return ["?"]
        # choose ECU: default functional/motor; ATSH already applied by adapter
        resp_frames = _build_response(req, ecu[0])
        if resp_frames is None:
            return ["NO DATA"]
        lines = []
        for f in resp_frames:
            lines.append(f"{ecu[1]:03X}" + f.hex().upper())
        return lines


class SimServer:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self._srv = None
        self._thread = None

    def start(self) -> None:
        allow_reuse = socketserver.ThreadingTCPServer
        allow_reuse.allow_reuse_address = True
        self._srv = allow_reuse((self.host, self.port), SimHandler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

