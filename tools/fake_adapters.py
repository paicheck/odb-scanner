"""Fake adapters that reproduce each way a real connection fails.

Used by tests/test_diagnostic.py to prove the doctor reaches the right verdict
for each symptom, rather than only for a working link. Each mode models one
real-world situation:

    dead       adapter is fine, the car is asleep (ignition off)
    mute       the port opens and swallows bytes, never answering at all
               (Bluetooth SPP outgoing port not chosen, or not paired)
    garbage    bytes arrive but never as a clean ELM327 prompt
               (wrong baud rate, or a marginal Bluetooth link)
    clone      works and answers, but does not identify as a known family
               (cheap clone -- should warn, not fail)
    wrongproto adapter is stuck on CAN 29-bit and refuses to switch, while the
               car is on 11-bit -- the classic fixed-ATSP bug

Standalone use:

    python tools/fake_adapters.py dead 35400
"""
from __future__ import annotations

import socket
import socketserver
import sys
import threading

CAR_PROTOCOL = "6"          # the protocol the simulated car is actually on


def make_handler(mode: str):
    """Build a request handler class for one failure mode."""

    class Handler(socketserver.BaseRequestHandler):
        MODE = mode
        # wrongproto powers up pinned to CAN 29-bit; everything else on 11-bit.
        PROTOCOL = "7" if mode == "wrongproto" else "6"

        def handle(self):
            self.request.settimeout(0.5)
            buf = b""
            while True:
                try:
                    chunk = self.request.recv(1024)
                except (socket.timeout, OSError):
                    continue
                if not chunk:
                    return
                buf += chunk
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    cmd = line.decode("ascii", "replace").strip().upper()
                    if not cmd:
                        continue
                    replies, prompt = self._reply(cmd)
                    for out in replies:
                        self.request.sendall(
                            (out + "\r").encode("ascii", "replace"))
                    if prompt:
                        self.request.sendall(b">")

        def _reply(self, cmd: str) -> tuple[list[str], bool]:
            if self.MODE == "mute":
                # Swallow the bytes and never even send a prompt.
                return [], False
            if self.MODE == "garbage":
                # Framing noise: bytes in, no recognisable prompt out.
                return ["@@@ ??? zzz"], False

            if cmd == "ATZ":
                return ["ELM327 v1.5"], True
            if cmd == "ATI":
                if self.MODE == "clone":
                    return ["OBDII-CAN v1.0"], True
                return ["ELM327 v1.5"], True
            if cmd.startswith("ATSP"):
                want = cmd[4:].strip()
                if self.MODE == "wrongproto" and want != "7":
                    return ["?"], True      # refuses to leave 29-bit
                type(self).PROTOCOL = want or "0"
                return ["OK"], True
            if cmd.startswith("AT"):
                return ["OK"], True

            # Vehicle request: the car only answers on its own protocol.
            if self.MODE == "wrongproto" and self.PROTOCOL != CAR_PROTOCOL:
                return ["NO DATA"], True
            if self.MODE == "clone":
                return ["7E8064100BE3FA813"], True
            return ["NO DATA"], True

    return Handler


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(mode: str, port: int = 0):
    """Start a fake adapter on a background thread.

    Returns (server, port). Pass port=0 to let the OS choose, which is what
    the tests do so they never collide with a real adapter or each other.
    """
    srv = _Server(("127.0.0.1", port), make_handler(mode))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "dead"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 35400
    # TCPServer.__enter__ only binds; nothing is served until serve_forever.
    with _Server(("127.0.0.1", port), make_handler(mode)) as srv:
        print(f"fake adapter mode={mode} on tcp://127.0.0.1:{port}",
              flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass