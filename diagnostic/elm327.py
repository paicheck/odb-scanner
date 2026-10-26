"""ELM327-compatible transport over serial (USB ELM327/OBDLink) or TCP.

TCP mode is used by the built-in vehicle simulator and by TCP-serial bridges.
Initialization uses conservative, documented ELM327 AT commands:

    ATZ    reset            ATE0   echo off
    ATL0   linefeeds off    ATS0   spaces off
    ATH1   headers ON (every response frame carries its sender's CAN id)
    ATAT1  adaptive timing  ATSP7  ISO 15765-4 CAN 500 kbit/s 29-bit
    ATCAF1 CAN auto-formatting on

Addressing is functional by default: requests go out on the protocol's
default functional id and each ECU's answer is attributed by its response
header (18DAF1xx) or, on the 11-bit bus, its rx id. When the adapter accepts
it, VAG MEB module addressing (ATCP + 6-digit ATSH) is negotiated at init so
reads can also be aimed at one ECU physically -- see set_module. No receive
filter (ATCRA) is ever set to a narrow value; see set_receive_address.

Note: multi-frame (ISO-TP) responses are reassembled by the ELM327 for
*requests it sends itself* on good adapters; cheap clones emit the raw
first/consecutive frames instead, so diagnostic/uds.py reassembles per
sender either way.
"""
from __future__ import annotations

import logging
import socket
import time

from .interface import (
    AdapterNotFoundError,
    CommunicationError,
    OBDInterface,
)

log = logging.getLogger(__name__)

_ADAPTER_HINTS = ("ELM327", "OBDLINK", "STN", "OBDII", "OBD-II")

# Protocol the transport pins during initialize(). "7" = ISO 15765-4 CAN
# 29-bit 500 kbit/s -- the ID.3/MEB diagnostic bus answers on 29-bit ids
# (18DAF1xx / 18DB33F1) and is silent under ATSP6 (11-bit). Verified with
# tools/doctor.py stage 5; change it there first if a vehicle disagrees.
PINNED_PROTOCOL = "7"


class Elm327Transport(OBDInterface):
    name = "elm327"

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 38400,
        timeout: float = 5.0,
        host: str | None = None,
        tcp_port: int | None = None,
        vid_hint: int | None = None,
        pid_hint: int | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.host = host
        self.tcp_port = tcp_port
        # Optional VID/PID hints from config. These PRIORITISE matching devices
        # during auto-detection; they never verify protocol on their own (a
        # VID/PID match still has to answer ATI). A hint identifying a chip
        # vendor such as FTDI (0403) says nothing about whether the device
        # speaks OBD -- see diagnostic/discovery.py.
        self.vid_hint = vid_hint
        self.pid_hint = pid_hint
        self._dev = None
        self._opened = False
        self.identity = "unknown"
        self._addressing_warned = False
        # VAG MEB physical addressing state (see _negotiate_meb / set_module)
        self.meb_addressing = False
        self._meb_module: int | None = None   # None = functional addressing
        self._caf = 1                          # CAN auto-formatting on/off
        self._proto = PINNED_PROTOCOL          # active ATSP protocol
        # CAN receive filter currently programmed (None = default/unknown).
        # The clone's default filter silently drops 17FExxxx module responses,
        # so a module read must pin this to the module's response id.
        self._atcra: int | None = None
        # Set when the link may be holding an unanswered request, so the next
        # send_command drains before writing. See _drain_input.
        self._needs_drain = False

    # -- lifecycle ----------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._opened

    @property
    def auto_formatting(self) -> bool:
        """True when the adapter is in ATCAF1 (auto-formatting ON).

        Every MEB module -- the 11-bit gateway and the 29-bit BMS/DC-DC alike --
        is read in ATCAF1, so requests go out bare and the adapter adds the
        ISO-TP PCI byte, header and flow control. This reports that state; the
        request path no longer branches on it.
        """
        return self._caf == 1

    def open(self) -> None:
        if self._opened:
            return
        if self.host:
            self._open_tcp()
        else:
            self._open_serial()
        self._opened = True

    def _open_tcp(self) -> None:
        assert self.host and self.tcp_port
        try:
            sock = socket.create_connection(
                (self.host, self.tcp_port), timeout=self.timeout
            )
        except OSError as exc:
            raise AdapterNotFoundError(
                f"Cannot connect to {self.host}:{self.tcp_port}: {exc}"
            ) from exc
        sock.settimeout(0.5)
        self._dev = sock
        log.info("Connected to TCP adapter %s:%s", self.host, self.tcp_port)

    def _open_serial(self) -> None:
        import serial  # local import so TCP-only setups do not need pyserial

        port = self.port
        if not port or port == "auto":
            port = self._autodetect_serial()
        try:
            self._dev = serial.Serial(port, self.baudrate, timeout=0.5)
        except (serial.SerialException, OSError) as exc:
            raise AdapterNotFoundError(f"Cannot open {port}: {exc}") from exc
        log.info("Opened serial adapter %s @ %d baud", port, self.baudrate)

    def _autodetect_serial(self) -> str:
        """Find an ELM327-class adapter by enumerating, classifying, probing.

        Discovery (diagnostic/discovery.py) enumerates the real serial
        interfaces and classifies each from its metadata. Vendor-specific
        interfaces are skipped: they will not answer ATI, and probing one only
        wastes time and mislabels the failure. Candidates are probed in
        confidence order and the first that answers ATI with an ELM327-family
        identity wins.

        The ATI probe is what makes this safe for the Bluetooth Veepeak:
        Windows enumerates TWO ports per Bluetooth serial device and only the
        OUTGOING one carries bytes. Metadata alone cannot tell them apart --
        both look identical -- so an actual exchange is the only way to pick
        the working one. Classification narrows the field; verification chooses
        within it.
        """
        try:
            import serial
        except ImportError as exc:
            raise AdapterNotFoundError("pyserial not installed") from exc
        from .discovery import DeviceClass, discover_and_classify

        classified = discover_and_classify()
        order = {DeviceClass.ELM327_USB_SERIAL: 0, DeviceClass.BLUETOOTH_SPP: 1,
                 DeviceClass.USB_SERIAL_OTHER: 2, DeviceClass.UNKNOWN: 3}

        def hint_rank(c) -> int:
            """0 for a device matching the configured VID/PID hint, else 1.

            Hints only reorder the probe; they do not select. A hinted device
            still has to answer ATI, so a wrong hint cannot make a dead port
            win -- it just gets tried first.
            """
            if self.vid_hint is None and self.pid_hint is None:
                return 1
            d = c.device
            matches = ((self.vid_hint is None or d.vid == self.vid_hint)
                       and (self.pid_hint is None or d.pid == self.pid_hint))
            return 0 if matches else 1

        candidates = sorted(
            (c for c in classified
             if c.device_class != DeviceClass.VENDOR_SPECIFIC),
            key=lambda c: (hint_rank(c), order.get(c.device_class, 4)))
        for c in candidates:
            port = c.device.port
            try:
                dev = serial.Serial(port, self.baudrate, timeout=0.5)
            except (serial.SerialException, OSError):
                continue
            try:
                self._dev = dev
                resp = self.send_command("ATI")
                ident = " ".join(resp).upper()
                if any(h in ident for h in _ADAPTER_HINTS):
                    return port
            except CommunicationError:
                pass
            finally:
                try:
                    if self._dev is dev:
                        self._dev = None
                    dev.close()
                except Exception:
                    pass
        found = ", ".join(f"{c.device.port}[{c.device_class.value}]"
                          for c in classified) or "none"
        raise AdapterNotFoundError(
            "No ELM327-compatible adapter answered ATI. Serial interfaces "
            f"found: {found}. Set adapter.port in config.yaml to the COM port "
            "of your device (see Windows Device Manager), or run "
            "python tools/discover_adapters.py to inspect them."
        )

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # pragma: no cover
                pass
        self._dev = None
        self._opened = False
        self._needs_drain = True
        # Addressing state describes an adapter that no longer exists. Leaving
        # it set means a request issued between close() and initialize() takes
        # the MEB path against a closed transport, and if a later _negotiate_meb
        # refuses, meb_addressing stays True with a stale _meb_module -- which
        # makes set_module() re-issue ATCP/ATSH, get refused, and _recover() the
        # adapter, once per request. _base_init resets these too; close() is
        # just the other place that has to.
        self.meb_addressing = False
        self._caf = None        # unknown, not "off": this adapter is gone
        self._proto = None
        self._meb_module = None
        self._atcra = None

    # -- raw line I/O -------------------------------------------------------
    def send_command(self, command: str) -> list[str]:
        if self._dev is None:
            raise CommunicationError("Adapter not open")
        # The adapter ends a command at CR, so an embedded CR/LF would turn one
        # (validated) command into several, the later ones never checked.
        # ReadOnlyViolationError, not CommunicationError: callers swallow the
        # latter per read, and this must be loud.
        if "\r" in command or "\n" in command:
            from .uds import ReadOnlyViolationError
            raise ReadOnlyViolationError(
                f"Refusing to send {command!r}: a command must be a single "
                "line. This system is strictly READ-ONLY.")
        # Discard anything already in the buffer before asking a new question.
        # Only when something might be there: a desync can only begin with a
        # read that did not complete, and _read_until_prompt already drains on
        # that path. Doing this unconditionally would cost a socket timeout on
        # every one of the ~110 commands a cycle issues, which is seconds of
        # dead time per cycle to protect against something that cannot happen.
        if self._needs_drain:
            self._drain_input()
            self._needs_drain = False
        self._write(command + "\r")
        raw = self._read_until_prompt(self.timeout)
        lines = raw.replace(">", "\n").splitlines()
        return [ln.strip() for ln in lines if ln.strip()]

    def _drain_input(self, max_bytes: int = 4096) -> int:
        """Throw away unread input. Returns how many bytes were discarded.

        A timed-out read leaves the adapter mid-answer. The bytes already
        consumed are gone, but the rest of that answer -- including the '>'
        prompt that terminates it -- is still sitting in the driver buffer. The
        next send_command() then writes its command and _read_until_prompt()
        finds the STALE '>' first, breaks immediately, and returns the previous
        request's lines as this request's answer. The link is then permanently
        one request out of step until an ATZ happens to resynchronise it.

        That is a data-integrity failure, not a cosmetic one. Most answers are
        caught by the echo check in read_did(), but the DIDs that are re-polled
        every cycle answer with byte-identical bytes, so a stale reply is
        indistinguishable from a live one -- and it is then stored with the
        current timestamp, which defeats the max_value_age_s freshness bound
        that exists precisely to catch stale readings. Anomaly detection then
        compares week-old data against a live baseline without noticing.

        Draining before the write makes a late answer from a previous request
        impossible to mistake for the current one; draining on failure makes
        the next request start clean rather than one request further behind.
        """
        if self._dev is None:
            return 0
        discarded = 0
        if hasattr(self._dev, "reset_input_buffer"):
            # serial: pyserial knows exactly what is pending
            try:
                pending = self._dev.in_waiting
                self._dev.reset_input_buffer()
                discarded += int(pending or 0)
            except Exception:  # pragma: no cover - platform dependent
                pass
            return discarded
        # socket: recv until the deadline. Note that on a timeout-configured
        # socket, "no data yet" arrives as a raised timeout, NOT as an empty
        # recv -- an empty recv means the peer closed. So this must not stop on
        # b""; only the deadline or an error ends it. Stopping early is exactly
        # the bug being fixed here, because the stale answer is still sitting
        # there waiting to be read.
        deadline = time.time() + 0.2
        while time.time() < deadline and discarded < max_bytes:
            try:
                self._dev.settimeout(0.05)
                chunk = self._dev.recv(1024)
            except (TimeoutError, OSError):
                break
            except Exception:  # pragma: no cover - platform dependent
                break
            discarded += len(chunk or b"")
        try:
            self._dev.settimeout(0.5)   # restore what open() established
        except Exception:  # pragma: no cover
            pass
        return discarded

    def _write(self, text: str) -> None:
        try:
            if hasattr(self._dev, "write"):  # serial
                self._dev.write(text.encode("ascii"))
            else:  # socket
                self._dev.sendall(text.encode("ascii"))
        except (OSError, AttributeError) as exc:
            raise CommunicationError(f"Write failed: {exc}") from exc

    def _read_until_prompt(self, timeout: float) -> str:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if hasattr(self._dev, "read"):  # serial
                    chunk = self._dev.read(256)
                else:  # socket
                    chunk = self._dev.recv(4096)
            except (TimeoutError, OSError):
                chunk = b""
            if chunk:
                buf += chunk
                if b">" in buf:
                    break
        text = buf.decode("ascii", errors="replace")
        if ">" not in text:
            # Clear the partial answer before giving up, or the next request
            # inherits it and reads this one's reply as its own. The flag also
            # covers the case where the rest of the answer arrives after this
            # drain has already finished looking.
            self._drain_input()
            self._needs_drain = True
            raise CommunicationError(
                f"Timeout waiting for prompt after: {buf[:80]!r}"
            )
        return text

    # -- ELM327 protocol ----------------------------------------------------
    def _base_init(self) -> None:
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH1", "ATAT1",
                    f"ATSP{PINNED_PROTOCOL}", "ATCAF1"):
            self.send_command(cmd)
        self._caf = 1
        self._proto = PINNED_PROTOCOL
        self._meb_module = None

    def initialize(self) -> str:
        """Bring the adapter into a known state. Returns adapter identity."""
        time.sleep(0.3)
        self.send_command("ATZ")
        time.sleep(0.3)
        resp = self.send_command("ATI")
        self.identity = " ".join(resp) if resp else "unknown"
        self._base_init()
        self._negotiate_meb()
        # OBD-II warm-up: '0100' (supported-PID request) makes the ELM finish
        # CAN bus init and shows whether anything is awake. On a silent bus
        # (vehicle asleep, gateway off) it returns NO DATA — logged, not
        # fatal: the caller surfaces a clear "vehicle did not answer" error.
        # This is a vehicle request sent straight through the transport, so it
        # bypasses DiagnosticConnection._transmit and must be validated here --
        # otherwise it would be a hole in the read-only guarantee.
        from . import uds
        uds.validate_request("0100")
        warm = " ".join(self.send_command("0100")).upper()
        if "NO DATA" in warm or "UNABLE" in warm or not warm:
            log.warning(
                "CAN bus silent after 0100 warm-up (%r) — vehicle likely "
                "asleep (ignition off). Requests will return NO DATA until "
                "the gateway wakes up.", warm or "no output")
        else:
            log.info("CAN bus alive after warm-up: %s", warm)
        log.info("Adapter initialized: %s", self.identity)
        return self.identity

    def set_header(self, tx_id: int | None) -> None:
        """No-op: this transport stays on functional addressing.

        Empirically established on the v1.5 clone in the field, running the
        29-bit protocol (ATSP7):

        1. ATSH with a 3-digit (11-bit) value is *accepted* but applied as
           a 29-bit id (ATSH7E5 -> 0x000007E5), which nothing on the bus
           listens to -- every later request dies with NO DATA.
        2. The 8-digit ATSH a 29-bit id requires is refused ('?'), and so
           is the plain ATSH that would clear a header -- once a bad
           header is set, only ATZ recovers.

        Functional requests (default header, 18DB33F1 under ATSP7) reach
        every ECU, and with ATH1 each response carries its sender's id
        (18DAF1xx), so higher layers attribute responses per ECU at parse
        time (uds.payloads_by_source) instead of addressing them here.
        """
        if tx_id is not None and not self._addressing_warned:
            self._addressing_warned = True
            log.info("ATSH suppressed: functional addressing only; responses "
                     "are attributed by their response header instead")

    def set_receive_address(self, rx_id: int | None) -> None:
        """No-op: never program a CAN receive filter.

        The clone refuses the plain ATCRA that would clear a filter, so a
        once-set filter could deafen the adapter to every other response
        until ATZ. Receive everything and select in software
        (uds.payloads_by_source) instead.
        """

    # -- VAG MEB physical addressing ---------------------------------------
    def _negotiate_meb(self) -> None:
        """Probe whether the adapter accepts VAG MEB module addressing.

        MEB modules (BMS 0x17FC007B, DC/DC 0x17FC00B9, ...) answer at 29-bit
        ids built from a priority byte plus a 6-digit address. ABRP's MEB
        profile (ev-obd-pids) drives ELM327-class adapters with:

            ATCP 17          # priority byte
            ATSH FC007B      # completed to 0x17FC007B

        Both are needed because the field clone refuses the 8-digit ATSH a
        29-bit id would otherwise require. On success the functional header
        is restored (CP 18 + DB33F1) so OBD-II modes keep working; if that
        restore is refused the adapter is reset with ATZ. Failure of either
        probe just leaves the transport in functional-only mode.

        Only the 29-bit half of MEB addressing is probed here. The 11-bit
        modules (energy 0x710, climate 0x746) need protocol 6 and are reached
        by set_module, which negotiates per module.

        Every refusal path clears meb_addressing. It used to only ever be set,
        never cleared, so a re-negotiation that had previously succeeded and
        then failed (an ATZ from _recover() against an adapter now refusing,
        say) left the flag True with a stale _meb_module. _transmit() would keep
        taking the MEB branch, set_module() would re-issue ATCP/ATSH, get
        refused, and call _recover() -- one full adapter reset per request, on
        every poll, for the rest of the session.
        """
        self.meb_addressing = False
        if any("?" in r for r in self.send_command("ATCP 17")):
            log.info("Adapter refused 'ATCP 17' - MEB physical addressing "
                     "unavailable, staying functional-only")
            self.send_command("ATCP 18")
            return
        if any("?" in r for r in self.send_command("ATSH FC007B")):
            log.info("Adapter refused 6-digit 'ATSH FC007B' - staying "
                     "functional-only")
            self.send_command("ATCP 18")
            return
        self.meb_addressing = True
        self._meb_module = 0x17FC007B
        # ATCRA0 IS NOT AN ACCEPT-ALL FILTER. Per the ELM327 command set, plain
        # `ATCRA` (no argument) restores the receive filters to their default,
        # and `ATCRA <id>` pins the filter to that one id -- so `ATCRA0` pins it
        # to CAN id 0x000. An earlier comment here called it a "best-effort
        # accept-all filter", which was simply wrong.
        #
        # It used to be harmless here, and that was measured rather than
        # assumed: the warm-up after this still returned live frames, which a
        # filter on 0x000 would suppress, so this clone was taken to ignore
        # CRA outright. The car has since disproved that. In the 2026-10-12
        # matrix, 'ATCRA 17FE007B' produced this project's first BMS answer
        # (62 02 8C B3), which means CRA is fully implemented and was
        # silently discarding every 17FExxxx response up to that point.
        # Warm-up frames arrive on 18DAF1xx, a different id range, so they
        # were never evidence about the module filter at all.
        #
        # This command is left in place because the plain `ATCRA` that clears
        # the filter is refused by this clone (it accepts only the form with
        # an argument), and a filter pinned to 0x000 would deafen the link.
        # The per-module filter is set by set_module() instead, where the
        # module's own response id is known.
        self.send_command("ATCRA0")
        if not self._restore_functional():
            log.warning("Functional header restore refused - resetting adapter")
            self.send_command("ATZ")
            time.sleep(0.3)
            self._base_init()
            # ATZ wiped every addressing setting the adapter held, so the
            # physical header this method just negotiated no longer exists.
            # Leaving meb_addressing=True would send every subsequent request
            # down the MEB path against a functional-only adapter.
            self.meb_addressing = False
            self._meb_module = None
            # Without this return the line below announced that MEB addressing
            # was available immediately after giving up on it -- the log said
            # the opposite of what the transport had just decided.
            return
        log.info("MEB physical addressing available (ATCP + 6-digit ATSH)")

    def _restore_functional(self) -> bool:
        """Point the adapter back at the functional OBD header (0x18DB33F1)."""
        resp = self._set_protocol(PINNED_PROTOCOL)
        resp += self.send_command("ATCP 18")
        resp += self.send_command("ATSH DB33F1")
        # Reset flow control an 11-bit module left behind (see set_module).
        self.send_command("ATFCSM 0")
        if any("?" in r for r in resp):
            return False
        self._meb_module = None
        return True

    def _set_protocol(self, proto: str) -> list[str]:
        """Switch ISO 15765-4 frame format (ATSP). Silent no-op when already set.

        Protocol 7 is 29-bit and protocol 6 is 11-bit, both at 500 kbit/s.
        MEB modules in the 11-bit range (energy 0x710, climate 0x746) are only
        reachable on protocol 6; going back to a 29-bit module needs 7 again.
        """
        if self._proto == proto:
            return []
        resp = self.send_command(f"ATSP{proto}")
        if not any("?" in r for r in resp):
            self._proto = proto
            # ATSP re-initialises the CAN layer, so the header programmed by a
            # previous ATCP/ATSH and the auto-formatting mode set by ATCAF are
            # no longer known to be what we last set them to. Whether this
            # clone preserves them across a protocol switch is exactly the kind
            # of behaviour this module's other comments insist must be measured
            # rather than assumed -- and assuming it does has a specific, silent
            # failure mode: a module read goes out as a hand-built 8-byte line
            # while the adapter is back on ATCAF1, where the payload limit is 7,
            # and the ELM327 answers '?' instead of transmitting. That is not a
            # bus timeout ('NO DATA'), it is the adapter refusing the command,
            # and the two are indistinguishable once discover_ecus() has mapped
            # both onto 'no-response'.
            #
            # Forgetting both is cheap -- it costs one ATCAF per protocol
            # change -- and it makes the next set_module() re-assert the state
            # instead of trusting a cache it cannot verify.
            self._meb_module = None
            self._caf = None
        return resp

    def _recover(self) -> bool:
        """Clear a latched bad header with ATZ and re-negotiate addressing.

        The clone latches a header it does not like (see set_header): only ATZ
        brings it back to a usable state. A refused module switch therefore
        costs one adapter reset, after which addressing capability is probed
        again rather than assumed.

        Always returns False: the caller must re-issue the request with
        functional addressing, because the adapter is no longer aimed at the
        module it asked for.
        """
        # Whatever was in flight is now unanswerable, so make sure the next
        # write starts from a clean buffer.
        self._needs_drain = True
        log.info("Adapter refused a module header -- resetting and re-negotiating")
        self.send_command("ATZ")
        time.sleep(0.3)
        self._base_init()
        self._negotiate_meb()
        return False

    def set_module(self, tx29: int | None, rx29: int | None = None) -> bool:
        """Switch addressing between functional (None) and one MEB module.

        Module reads keep CAN auto-formatting ON: the request goes out bare
        ('22 XXXX') and the adapter adds the ISO-TP PCI byte, header and flow
        control. This is Car Scanner's wire form for both the 11-bit gateway and
        the 29-bit BMS/DC-DC modules (2026-10-15 passive HCI capture). The
        hand-built ATCAF0 8-byte line this transport once used for 29-bit
        modules is refused by this clone with '?', which is why no MEB read now
        hand-frames. Functional OBD-II also stays on ATCAF1 with bare '0100'.

        `tx29` may be either MEB form: a 29-bit id (0x17FC007B) or an 11-bit
        one (0x710). Both are programmed as ATCP <priority> + ATSH <6 hex
        digits>, which is what the community tooling sends; the protocol is
        chosen to match the id.

        `rx29` is the module's response id, programmed as the CAN receive
        filter. It is not optional in practice: this clone's default filter
        accepts functional responses (18DAF1xx) but silently drops MEB
        module responses (17FExxxx), so a module read that sets no filter
        times out with NO DATA even though the request was transmitted
        correctly. Car Scanner pins the same per-module filter; the 2026-10-15
        capture shows it reading the BMS with 'ATCRA17FE007B' plus a bare
        '221E3B', answered '17FE007B05621E3B0690'.

        Returns False when the switch was refused and the caller should
        fall back to functional addressing.
        """
        if not self.meb_addressing:
            return tx29 is None
        # 11-bit MEB modules (energy 0x710, climate 0x746, GPS 0x767) need Car
        # Scanner's sequence -- 3-digit ATSH/ATCRA plus the flow-control
        # preamble and auto-formatting -- which the 6/8-digit path below does
        # not produce. The 2026-10-15 passive capture confirmed Car Scanner
        # reaches the gateway (0x710) with ATSH710 / ATSP6 / ATFCSH710 /
        # ATFCSD300000 / ATFCSM1 / ATCRA77A plus a bare request; the same
        # recipe applies to the other 11-bit modules.
        if tx29 is not None and tx29 <= 0x7FF and rx29 is not None:
            return self.set_module_car_scanner_11bit(tx29, rx29)
        want_proto = "6" if tx29 is not None and tx29 <= 0x7FF else PINNED_PROTOCOL
        # The CAF check belongs in this guard: being aimed at the right module
        # on the right protocol is not enough if auto-formatting is not in the
        # mode the bare request needs. _set_protocol() clears _caf to None
        # whenever it switches, so this correctly misses the cache then.
        # The filter belongs here for the same reason: a module switch that
        # leaves the previous module's response id pinned misses the new one.
        if (tx29 == self._meb_module and self._proto == want_proto
                and self._caf == 1 and self._atcra == rx29):
            return True
        if tx29 is None:
            if not self._restore_functional():
                return self._recover()
            # Restore the default filter. Leaving a module's response id pinned
            # here would deafen every later functional read (mode 01 etc.
            # answer from 18DAF1xx), and the failure would look like a silent
            # bus rather than like what it is.
            self.send_command("ATCRA")
            self._atcra = None
            if self._caf != 1:
                self.send_command("ATCAF1")
                self._caf = 1
            return True
        resp = self._set_protocol(want_proto)
        resp += self.send_command(f"ATCP {(tx29 >> 24) & 0xFF:02X}")
        resp += self.send_command(f"ATSH {tx29 & 0xFFFFFF:06X}")
        if any("?" in r for r in resp):
            log.warning("Adapter refused module header %08X", tx29)
            return self._recover()
        # Car Scanner's 29-bit preamble, captured 2026-10-15. It configures the
        # BMS/DC-DC modules exactly like the 11-bit gateway -- auto-formatting
        # ON with a bare request -- not as a hand-built ATCAF0 frame:
        #   ATSHFC007B / ATSP7 / ATCP17 / ATCRA17FE007B /
        #   ATFCSH17FC007B / ATFCSD300000 / ATFCSM1  ->  bare '221E3B'
        # answered '17FE007B05621E3B0690'. Two details contradict the old
        # hand-framed path: ATFCSM stays 1 (this clone reads 29-bit modules with
        # user-defined flow control ON) and no ATCAF0 is ever sent, so the
        # adapter frames the request. The flow-control header is the full
        # 8-digit request id; the clone accepts ATFCSH at that width even though
        # it refuses an 8-digit ATSH.
        if rx29 is not None:
            resp = self.send_command(f"ATCRA {rx29:08X}")
            if any("?" in r for r in resp):
                log.warning("Adapter refused ATCRA %08X", rx29)
                return self._recover()
        self._atcra = rx29
        for cmd in (f"ATFCSH {tx29:08X}", "ATFCSD 300000", "ATFCSM 1",
                    "ATCAF1"):
            resp = self.send_command(cmd)
            if any("?" in r for r in resp):
                log.warning("Adapter refused %s for module %08X", cmd, tx29)
                return self._recover()
        self._caf = 1
        self._meb_module = tx29
        return True

    def set_module_car_scanner_11bit(self, tx29: int, rx29: int) -> bool:
        """Configure an 11-bit MEB module per Car Scanner's BCM field.

        Car Scanner reaches the 11-bit modules (gateway/energy 0x710, climate
        0x746, GPS 0x767) with auto-formatting ON and a bare request, plus the
        ISO-TP flow-control preamble. The gateway sequence was observed in the
        2026-10-15 passive HCI capture (the profiles7.db BCM field omits the
        ATSH request header, but the capture shows Car Scanner sends it):
          ATSP6                    # ISO 15765-4 CAN 11-bit 500 kbit/s
          ATSH 710                 # request header = module id
          ATFCSH 710               # flow-control header = request id
          ATFCSD 300000            # flow control: BS=0, STmin=0
          ATFCSM 1                 # user-defined flow control
          ATCRA 77A                # receive filter = response id
          ATCAF1                   # auto-formatting ON: send bare '22 XXXX'

        After this, the adapter is ready for a bare request: '22 XXXX'. The
        adapter adds the ISO-TP PCI byte, header and flow control itself. The
        8-byte hand-framed CAF0 line is refused ('?') on this clone, under
        ATSP6 and ATSP7 alike, so every MEB module uses this form
        (docs/MEB_NO_RESPONSE_HANDOFF.md Track B).

        Returns False if any command is refused by the adapter.
        """
        # Cache: if already aimed at this module on the right protocol, filter
        # and auto-formatting mode, don't re-send the AT commands before every
        # read. The other set_module() paths reset these fields when switching
        # away, so a stale cache cannot survive a module change.
        if (self._proto == "6" and self._caf == 1
                and self._atcra == rx29 and self._meb_module == tx29):
            return True
        # Must be on 11-bit protocol
        resp = self._set_protocol("6")
        if any("?" in r for r in resp):
            log.warning("Adapter refused ATSP6 for 11-bit module %03X", tx29)
            return False

        # Car Scanner's 11-bit sequence as captured, plus ATCAF1: auto-format
        # the bare request instead of hand-framing it (the CAF0 8-byte line is
        # refused under ATSP6). The flow-control preamble lets the adapter
        # reassemble multi-frame replies.
        for cmd in (f"ATSH {tx29:03X}", f"ATFCSH {tx29:03X}",
                    "ATFCSD 300000", "ATFCSM 1", f"ATCRA {rx29:03X}",
                    "ATCAF1"):
            resp = self.send_command(cmd)
            if any("?" in r for r in resp):
                log.warning("Adapter refused %s for 11-bit module %03X", cmd,
                            tx29)
                return False

        # Track state so set_module(None) can restore correctly
        self._proto = "6"
        self._caf = 1  # ATCAF1: auto-formatting, bare request, adapter frames it
        self._atcra = rx29
        self._meb_module = tx29

        log.info(
            "MEB 11-bit module %03X -> %03X addressing configured "
            "per Car Scanner profile", tx29, rx29
        )
        return True

    def description(self) -> str:
        return f"{self.name} ({self.identity})"


class TcpElm327(Elm327Transport):
    """ELM327 over TCP — the simulator presents itself as an ELM327."""

    name = "elm327-tcp"

    def __init__(self, host: str, port: int, timeout: float = 5.0) -> None:
        super().__init__(timeout=timeout, host=host, tcp_port=port)

