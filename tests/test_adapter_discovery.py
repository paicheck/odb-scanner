"""Adapter discovery and pluggable-transport tests.

Every test here runs against MOCKED serial ports and MOCKED transports. None
requires a connected car, an OBD adapter, or any real hardware -- the whole
point of the discovery layer is that it can be reasoned about and tested
without touching the vehicle.

The scenarios mirror the ones the discovery layer must get right, because each
maps to a real way this goes wrong in the field:

  * a Bluetooth pair enumerates two ports and only one carries bytes
  * an FTDI chip proves nothing about whether the device speaks OBD
  * a vendor-specific interface must be refused, not driven as ELM327
  * "the port opened" is not "the adapter answered"
  * several plausible adapters must never be resolved by picking the first
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnostic.discovery import (
    AmbiguousAdapterError,
    DeviceClass,
    Evidence,
    SerialDevice,
    UnsupportedAdapterError,
    build_transport,
    classify_device,
    connect_and_verify,
    discover_serial_devices,
    select_device,
    verify_adapter,
)
from diagnostic.interface import AdapterNotFoundError, CommunicationError


# --- mocks --------------------------------------------------------------------

class FakePortInfo:
    """Stand-in for serial.tools.list_ports_common.ListPortInfo.

    SerialDevice.from_list_port_info reads every field with getattr, so a plain
    object with the same attributes is enough -- and lets a test omit a field
    entirely to exercise the missing-metadata path.
    """

    def __init__(self, device, description="", hwid="", vid=None, pid=None,
                 serial_number=None, manufacturer=None, product=None,
                 location=None, interface=None):
        self.device = device
        self.description = description
        self.hwid = hwid
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self.manufacturer = manufacturer
        self.product = product
        self.location = location
        self.interface = interface


def fake_comports(monkeypatch, infos):
    """Patch pyserial's comports() to return the given fake port infos."""
    import serial.tools.list_ports as list_ports
    monkeypatch.setattr(list_ports, "comports", lambda: list(infos))


class FakeTransport:
    """A transport whose open()/send_command()/close() are scripted.

    `ati_reply` is what ATI returns; `open_error`/`send_error` make the
    corresponding call raise, to exercise the failure paths without hardware.
    """

    name = "fake"

    def __init__(self, ati_reply=("ELM327 v1.5",), open_error=None,
                 send_error=None, disconnect_after=None):
        self._ati_reply = list(ati_reply)
        self._open_error = open_error
        self._send_error = send_error
        self._disconnect_after = disconnect_after
        self._opened = False
        self._closed = False
        self.sent = []

    @property
    def is_open(self):
        return self._opened

    def open(self):
        if self._open_error:
            raise self._open_error
        self._opened = True

    def close(self):
        self._opened = False
        self._closed = True

    def send_command(self, command):
        self.sent.append(command)
        if self._send_error:
            raise self._send_error
        if self._disconnect_after is not None and \
                len(self.sent) > self._disconnect_after:
            raise CommunicationError("device disconnected mid-response")
        if command.upper().startswith("ATI"):
            return list(self._ati_reply)
        return ["OK"]


# --- 1. no adapters present ---------------------------------------------------

def test_no_adapters_present(monkeypatch):
    """An empty enumeration is a clean 'nothing found', never a crash or a
    silent fallback to a guessed port."""
    fake_comports(monkeypatch, [])
    assert discover_serial_devices() == []
    with pytest.raises(AdapterNotFoundError) as exc:
        select_device()
    assert "No serial interfaces" in str(exc.value)


# --- 2. a single Bluetooth serial adapter ------------------------------------

def test_single_bluetooth_adapter_is_identified(monkeypatch):
    """The Veepeak's outgoing port: named as Bluetooth, mapped to the serial
    ELM327 transport, and auto-selected when it is the only candidate."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link",
                     "BTHENUM\\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG"),
    ])
    devices = discover_serial_devices()
    assert len(devices) == 1
    cls = classify_device(devices[0])
    assert cls.device_class == DeviceClass.BLUETOOTH_SPP
    assert cls.evidence == Evidence.IDENTIFIED
    assert cls.transport == "elm327_serial"
    assert cls.usable_as_elm327

    chosen = select_device()
    assert chosen.device.port == "COM4"
    # The Bluetooth two-port trap must be surfaced, not hidden.
    assert any("OUTGOING" in n for n in cls.notes)


# --- 3. a generic USB serial adapter -----------------------------------------

def test_generic_usb_serial_adapter_is_other_not_elm(monkeypatch):
    """A CH340 device is a serial chip, not a proven OBD adapter. It must be
    USB_SERIAL_OTHER (a candidate to probe), never ELM327_USB_SERIAL."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM5", "USB-SERIAL CH340", "USB VID:PID=1A86:7523",
                     vid=0x1A86, pid=0x7523),
    ])
    cls = classify_device(discover_serial_devices()[0])
    assert cls.device_class == DeviceClass.USB_SERIAL_OTHER
    assert cls.chip and "CH340" in cls.chip
    assert cls.usable_as_elm327          # can be probed...
    assert cls.evidence == Evidence.IDENTIFIED   # ...but only IDENTIFIED
    assert any("not that it speaks OBD" in n for n in cls.notes)


# --- 4. multiple candidate adapters ------------------------------------------

def test_multiple_candidates_are_reported_not_guessed(monkeypatch):
    """Two plausible adapters and no configured port must raise and list both.

    Silently picking the first is the exact failure that connects to the wrong
    half of a Bluetooth pair and then reports 'the car did not answer'.
    """
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
        FakePortInfo("COM5", "USB-SERIAL CH340", "USB VID:PID=1A86:7523",
                     vid=0x1A86, pid=0x7523),
    ])
    with pytest.raises(AmbiguousAdapterError) as exc:
        select_device()
    listing = str(exc.value)
    assert "COM4" in listing and "COM5" in listing
    assert len(exc.value.candidates) == 2


def test_multiple_candidates_resolved_by_explicit_port(monkeypatch):
    """An explicit configured port disambiguates without guessing."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
        FakePortInfo("COM5", "USB-SERIAL CH340", "USB VID:PID=1A86:7523",
                     vid=0x1A86, pid=0x7523),
    ])
    chosen = select_device(configured_port="COM5")
    assert chosen.device.port == "COM5"


def test_configured_port_that_is_absent_lists_what_is_present(monkeypatch):
    """A configured COM port that is not there must say so and show the real
    ports, rather than falling back to a different device behind the user's
    back."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
    ])
    with pytest.raises(AdapterNotFoundError) as exc:
        select_device(configured_port="COM9")
    assert "COM9" in str(exc.value)
    assert "COM4" in str(exc.value)


# --- 5. missing VID/PID metadata ---------------------------------------------

def test_missing_vid_pid_metadata_degrades_to_unknown(monkeypatch):
    """A port with no VID/PID and no useful description is DETECTED, not
    identified -- and classification must not invent an identity for it.

    It is also NOT an auto-select candidate: a mystery COM port could be a
    modem, a mouse, or a virtual port, and 'a port exists' is not evidence it
    speaks OBD. It remains explicitly selectable, so a user who knows what they
    plugged in can still configure and probe it.
    """
    fake_comports(monkeypatch, [FakePortInfo("COM6")])
    device = discover_serial_devices()[0]
    assert device.vid is None and device.pid is None
    cls = classify_device(device)
    assert cls.device_class == DeviceClass.UNKNOWN
    assert cls.evidence == Evidence.DETECTED      # not IDENTIFIED
    assert not cls.usable_as_elm327               # not auto-selected
    # Auto-selection refuses to guess at it...
    with pytest.raises(AdapterNotFoundError):
        select_device()
    # ...but an explicit port selection still returns it, to be probed.
    chosen = select_device(configured_port="COM6")
    assert chosen.device.port == "COM6"
    assert chosen.device_class == DeviceClass.UNKNOWN


def test_serial_device_handles_partially_missing_fields():
    """from_list_port_info must tolerate any field being absent or empty."""
    class Bare:
        device = "COM7"
        # every other attribute missing entirely
    device = SerialDevice.from_list_port_info(Bare())
    assert device.port == "COM7"
    assert device.vid is None and device.description == ""
    # Empty strings normalise to None so they do not read as real metadata.
    class EmptyStrings:
        device = "COM8"
        description = ""
        hwid = ""
        vid = None
        pid = None
        serial_number = ""
        manufacturer = ""
        product = ""
        location = ""
        interface = ""
    d2 = SerialDevice.from_list_port_info(EmptyStrings())
    assert d2.serial_number is None and d2.product is None


# --- 6. unknown hardware -----------------------------------------------------

def test_unknown_usb_hardware_is_detected_but_unidentified(monkeypatch):
    """A USB device with a VID/PID we do not recognise is UNKNOWN at DETECTED
    level: it can be opened and probed, but nothing claims to know what it is."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM9", "Some Mystery Device", "USB VID:PID=DEAD:BEEF",
                     vid=0xDEAD, pid=0xBEEF),
    ])
    cls = classify_device(discover_serial_devices()[0])
    assert cls.device_class == DeviceClass.UNKNOWN
    assert cls.evidence == Evidence.DETECTED
    assert "no recognised" in cls.reason


# --- 7. detected but cannot communicate --------------------------------------

def test_adapter_that_opens_but_never_answers():
    """The port opens (CONNECTED) but ATI gets nothing: that is a protocol
    mismatch, reported as CommunicationError -- NOT as 'no adapter'."""
    transport = FakeTransport(ati_reply=[])
    transport.open()
    with pytest.raises(CommunicationError) as exc:
        verify_adapter(transport)
    assert "did not answer ATI" in str(exc.value)


def test_adapter_that_answers_with_a_refusal():
    """ATI answered with '?' -- the device is there but does not speak ELM327."""
    transport = FakeTransport(ati_reply=["?"])
    with pytest.raises(CommunicationError):
        verify_adapter(transport)
    # verify_adapter opened it, so it must also have closed it on failure.
    assert transport._closed


# --- 8. connection timeout ---------------------------------------------------

def test_connection_timeout_is_an_adapter_not_found_error():
    """A port that will not open within the timeout is AdapterNotFoundError,
    distinguishable from a device that opened and then stayed silent."""
    transport = FakeTransport(
        open_error=AdapterNotFoundError("Cannot open COM10: timed out"))
    with pytest.raises(AdapterNotFoundError):
        verify_adapter(transport)
    assert not transport._opened


# --- 9. unexpected disconnect ------------------------------------------------

def test_unexpected_disconnect_during_verify_closes_cleanly():
    """The device vanishes mid-exchange. The error must propagate AND the port
    must be released, or the next attempt fails with 'access denied'."""
    transport = FakeTransport(ati_reply=["ELM327 v1.5"], disconnect_after=0)
    # disconnect_after=0 makes the FIRST send_command raise.
    with pytest.raises(CommunicationError) as exc:
        verify_adapter(transport)
    assert "disconnected" in str(exc.value)
    assert transport._closed, "a failed verify must not leak the handle"


# --- 10. malformed or incomplete responses -----------------------------------

@pytest.mark.parametrize("reply", [
    [],                       # nothing at all
    ["?"],                    # ELM refusal
    [""],                     # empty line
    ["   "],                  # whitespace only
])
def test_malformed_ati_responses_are_rejected(reply):
    """An identity that is empty or a bare refusal is not a verified adapter."""
    transport = FakeTransport(ati_reply=reply)
    with pytest.raises(CommunicationError):
        verify_adapter(transport)


def test_partial_identity_that_is_coherent_is_accepted():
    """A clone that answers 'ELM327 v1.5' is verified even though it is not a
    genuine ELM327 -- answering ATI coherently IS the verification."""
    transport = FakeTransport(ati_reply=["ELM327 v1.5"])
    assert verify_adapter(transport) == "ELM327 v1.5"


# --- 11. vendor id matches a known chipset, product unverified ---------------

def test_ftdi_vid_alone_is_not_ross_tech(monkeypatch):
    """THE conservatism rule. VID 0403 is FTDI, a chip vendor shared by
    thousands of products including some Ross-Tech HEX interfaces. A bare FTDI
    device with no identifying product string must be USB_SERIAL_OTHER -- never
    VENDOR_SPECIFIC, never ELM327, never 'Ross-Tech'."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM11", "USB Serial Port", "USB VID:PID=0403:6001",
                     vid=0x0403, pid=0x6001),
    ])
    cls = classify_device(discover_serial_devices()[0])
    assert cls.device_class == DeviceClass.USB_SERIAL_OTHER
    assert cls.device_class != DeviceClass.VENDOR_SPECIFIC
    assert cls.chip == "FTDI"
    assert any("0403 is FTDI" in n for n in cls.notes)
    assert any("never the product" in n for n in cls.notes)


def test_ftdi_vid_with_unverified_pid_is_still_just_a_chip(monkeypatch):
    """Even the PID Ross-Tech has been seen with (FA24) does not, on its own,
    make a device Ross-Tech: a clone or an unrelated FTDI product can share it.
    Without the device's own name saying so, it stays USB_SERIAL_OTHER."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM12", "USB Serial Port", "USB VID:PID=0403:FA24",
                     vid=0x0403, pid=0xFA24),
    ])
    cls = classify_device(discover_serial_devices()[0])
    assert cls.device_class == DeviceClass.USB_SERIAL_OTHER
    assert cls.device_class != DeviceClass.VENDOR_SPECIFIC


def test_vendor_specific_identified_by_its_own_name(monkeypatch):
    """A device that REPORTS itself as a Ross-Tech HEX is vendor-specific --
    identified by its own product string, which is real device metadata, not a
    guessed VID:PID. It must be refused, not driven as ELM327."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM13", "Ross-Tech HEX-USB", "USB VID:PID=0403:FA24",
                     vid=0x0403, pid=0xFA24, manufacturer="Ross-Tech",
                     product="HEX-USB"),
    ])
    cls = classify_device(discover_serial_devices()[0])
    assert cls.device_class == DeviceClass.VENDOR_SPECIFIC
    assert cls.evidence == Evidence.IDENTIFIED
    assert cls.transport is None
    assert not cls.usable_as_elm327
    with pytest.raises(UnsupportedAdapterError):
        build_transport(cls)


def test_vendor_specific_is_the_only_device_says_so_explicitly(monkeypatch):
    """When the sole serial device is vendor-specific, selection must name it
    rather than report a generic 'nothing found'."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM13", "Ross-Tech HEX-V2", "USB VID:PID=0403:FA24",
                     vid=0x0403, pid=0xFA24, product="HEX-V2"),
    ])
    with pytest.raises(UnsupportedAdapterError) as exc:
        select_device()
    assert "vendor-specific" in str(exc.value)


def test_vendor_specific_never_auto_selected_among_others(monkeypatch):
    """A vendor-specific device present alongside a real candidate must not be
    auto-chosen, and must not make the selection ambiguous."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM13", "Ross-Tech HEX-USB", "USB VID:PID=0403:FA24",
                     vid=0x0403, pid=0xFA24, product="HEX-USB"),
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
    ])
    chosen = select_device()
    assert chosen.device.port == "COM4"      # the Bluetooth one, not the HEX


# --- 12. preservation of existing Veepeak behaviour --------------------------

def test_veepeak_path_builds_the_same_elm327_transport(monkeypatch):
    """The discovery layer must hand back the SAME Elm327Transport the existing
    code uses, pointed at the same port -- not a new abstraction that changes
    how the Veepeak is driven."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM3", "Standard Serial over Bluetooth link", "BTHENUM"),
    ])
    cls = select_device(configured_port="COM3")
    transport = build_transport(cls, baudrate=38400, timeout=5.0)
    from diagnostic.elm327 import Elm327Transport
    assert isinstance(transport, Elm327Transport)
    assert transport.port == "COM3"
    assert transport.baudrate == 38400
    assert transport.timeout == 5.0
    assert not transport.is_open       # returned unopened: connect is a step


def test_connect_and_verify_opens_probes_and_returns_identity(monkeypatch):
    """The full happy path for a Veepeak-like adapter: build, open, ATI,
    identity -- and the transport is left OPEN for the caller to use."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM3", "Standard Serial over Bluetooth link", "BTHENUM"),
    ])
    cls = select_device(configured_port="COM3")

    created = {}

    class VerifyTransport(FakeTransport):
        def __init__(self, **kwargs):
            super().__init__(ati_reply=["ELM327 v1.5"], **kwargs)
            created["t"] = self

    monkeypatch.setattr("diagnostic.elm327.Elm327Transport",
                        lambda **k: VerifyTransport())
    transport, identity = connect_and_verify(cls)
    assert identity == "ELM327 v1.5"
    assert transport.is_open
    assert transport.sent == ["ATI"]      # verification sends ATI and nothing else


def test_verify_never_sends_a_vehicle_request():
    """Verification is ATI only. It must not put a diagnostic frame on the bus
    or wake an ECU -- 'test my connection' has to be inert toward the car."""
    transport = FakeTransport(ati_reply=["ELM327 v1.5"])
    verify_adapter(transport)
    assert transport.sent == ["ATI"]
    assert not any(s.upper().startswith(("22", "10", "3E", "19", "01", "09"))
                   for s in transport.sent)


# --- evidence ordering -------------------------------------------------------

def test_evidence_levels_are_ordered():
    """The whole point of the evidence enum: DETECTED < IDENTIFIED <
    TRANSPORT_SUPPORTED < CONNECTED < VERIFIED, so a caller can ask 'did we at
    least identify it?' without conflating stages."""
    assert Evidence.DETECTED < Evidence.IDENTIFIED
    assert Evidence.IDENTIFIED < Evidence.TRANSPORT_SUPPORTED
    assert Evidence.TRANSPORT_SUPPORTED < Evidence.CONNECTED
    assert Evidence.CONNECTED < Evidence.VERIFIED
    assert Evidence.VERIFIED <= Evidence.VERIFIED
    assert not (Evidence.DETECTED >= Evidence.IDENTIFIED)


# --- VID/PID hints ------------------------------------------------------------

def test_vid_pid_hint_narrows_candidates_but_does_not_verify(monkeypatch):
    """A configured VID/PID hint selects among devices, but a match is still
    only as classified as its metadata -- the hint never upgrades it to
    VERIFIED."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
        FakePortInfo("COM5", "USB-SERIAL CH340", "USB VID:PID=1A86:7523",
                     vid=0x1A86, pid=0x7523),
    ])
    chosen = select_device(vid_hint=0x1A86, pid_hint=0x7523)
    assert chosen.device.port == "COM5"
    assert chosen.evidence == Evidence.IDENTIFIED   # hint did not VERIFY it


def test_vid_pid_hint_matching_nothing_falls_back(monkeypatch):
    """A hint that matches no device must not leave selection empty-handed; it
    falls back to the normal candidate set."""
    fake_comports(monkeypatch, [
        FakePortInfo("COM4", "Standard Serial over Bluetooth link", "BTHENUM"),
    ])
    chosen = select_device(vid_hint=0xDEAD, pid_hint=0xBEEF)
    assert chosen.device.port == "COM4"


# --- device summary ----------------------------------------------------------

def test_device_summary_is_redaction_free_but_useful():
    """The summary used in candidate listings shows port, description, VID:PID
    and serial -- enough to pick the right device, with no vehicle data."""
    device = SerialDevice(port="COM3", description="Standard Serial over "
                          "Bluetooth link", vid=0x0403, pid=0xFA24,
                          serial_number="ABC123")
    summary = device.summary()
    assert "COM3" in summary
    assert "0403:FA24" in summary
    assert "ABC123" in summary


# --- _autodetect_serial integration (discovery-aware, probe-verified) --------

def _fake_serial_class(opened):
    """A serial.Serial stand-in that records which ports were opened."""

    class FakeSerial:
        def __init__(self, port, *args, **kwargs):
            self.port = port
            opened.append(port)

        def close(self):
            pass

        in_waiting = 0

        def reset_input_buffer(self):
            pass

    return FakeSerial


def test_autodetect_skips_vendor_specific_and_picks_the_port_that_answers(
        monkeypatch):
    """The Veepeak preservation case. Windows enumerates two identical-looking
    Bluetooth ports; only the outgoing one answers ATI. Metadata cannot tell
    them apart, so the probe must -- and a vendor-specific device present
    alongside must never be opened at all.
    """
    import serial

    from diagnostic import discovery
    from diagnostic.elm327 import Elm327Transport

    def fake_classify():
        hex_dev = SerialDevice(port="COM13", description="Ross-Tech HEX-USB",
                               vid=0x0403, pid=0xFA24, product="HEX-USB")
        bt_dead = SerialDevice(port="COM3",
                               description="Standard Serial over Bluetooth link")
        bt_live = SerialDevice(port="COM4",
                               description="Standard Serial over Bluetooth link")
        return [discovery.classify_device(d) for d in (hex_dev, bt_dead, bt_live)]

    monkeypatch.setattr(discovery, "discover_and_classify", fake_classify)
    opened: list = []
    monkeypatch.setattr(serial, "Serial", _fake_serial_class(opened))

    t = Elm327Transport(port="auto")

    def fake_send(command):
        port = getattr(t._dev, "port", None)
        if command == "ATI":
            # Only the outgoing port (COM4) reports an ELM327 identity.
            return ["ELM327 v1.5"] if port == "COM4" else ["NO DATA"]
        return ["OK"]

    monkeypatch.setattr(t, "send_command", fake_send)

    assert t._autodetect_serial() == "COM4"
    assert "COM13" not in opened, "a vendor-specific interface must not be probed"
    assert "COM3" in opened and "COM4" in opened, (
        "both Bluetooth ports look identical; only the probe can choose")


def test_autodetect_prefers_an_identified_elm_over_an_unknown_port(monkeypatch):
    """Candidates are probed in confidence order, so a device that names itself
    ELM327 is tried before a mystery port."""
    import serial

    from diagnostic import discovery
    from diagnostic.elm327 import Elm327Transport

    def fake_classify():
        mystery = SerialDevice(port="COM20")                      # UNKNOWN
        elm = SerialDevice(port="COM21", description="OBDLink LX")  # ELM327
        return [discovery.classify_device(d) for d in (mystery, elm)]

    monkeypatch.setattr(discovery, "discover_and_classify", fake_classify)
    opened: list = []
    monkeypatch.setattr(serial, "Serial", _fake_serial_class(opened))

    t = Elm327Transport(port="auto")
    monkeypatch.setattr(t, "send_command",
                        lambda cmd: ["ELM327 v1.5"] if cmd == "ATI" else ["OK"])

    assert t._autodetect_serial() == "COM21"
    # The identified ELM327 is probed first and answers, so the mystery port is
    # never touched.
    assert opened == ["COM21"]


def test_autodetect_reports_classified_candidates_when_nothing_answers(
        monkeypatch):
    """When no adapter answers ATI, the error must list what WAS found and how
    it classified, and point at the discovery tool -- not just say 'none'."""
    import serial

    from diagnostic import discovery
    from diagnostic.elm327 import Elm327Transport

    def fake_classify():
        chip = SerialDevice(port="COM5", description="USB-SERIAL CH340",
                            vid=0x1A86, pid=0x7523)
        return [discovery.classify_device(chip)]

    monkeypatch.setattr(discovery, "discover_and_classify", fake_classify)
    monkeypatch.setattr(serial, "Serial", _fake_serial_class([]))

    t = Elm327Transport(port="auto")
    monkeypatch.setattr(t, "send_command", lambda cmd: ["NO DATA"])

    with pytest.raises(AdapterNotFoundError) as exc:
        t._autodetect_serial()
    msg = str(exc.value)
    assert "COM5" in msg
    assert "usb_serial_other" in msg
    assert "discover_adapters" in msg


# --- config integration (VID/PID hints, explicit port preserved) -------------

def test_int_hint_parses_int_hex_and_garbage():
    """A config hint may be an int, a hex string, or absent. Garbage must
    degrade to None, never crash transport construction at startup."""
    from collector import _int_hint
    assert _int_hint(None) is None
    assert _int_hint("") is None
    assert _int_hint(267) == 267
    assert _int_hint("0x0403") == 0x0403
    # A bare hex string with no 0x prefix is not a valid base-0 literal, so it
    # degrades to None (hint ignored) rather than being misread as decimal.
    assert _int_hint("1A86") is None
    assert _int_hint("not-a-number") is None


def test_build_transport_passes_vid_pid_hints():
    """Configured hints reach the transport so auto-detection can prioritise
    them -- but the transport is still built unopened and still probes."""
    from collector import build_transport
    from config import Config
    from diagnostic.elm327 import Elm327Transport

    cfg = Config({"adapter": {"type": "elm327_serial", "port": "auto",
                              "vid": "0x1A86", "pid": 0x7523,
                              "baudrate": 38400, "timeout": 5.0}})
    t = build_transport(cfg)
    assert isinstance(t, Elm327Transport)
    assert t.port == "auto"
    assert t.vid_hint == 0x1A86
    assert t.pid_hint == 0x7523
    assert not t.is_open


def test_build_transport_explicit_port_path_is_unchanged():
    """The Veepeak path: an explicit COM port builds exactly the transport it
    always did, with no hints and no discovery second-guessing the user."""
    from collector import build_transport
    from config import Config
    from diagnostic.elm327 import Elm327Transport

    cfg = Config({"adapter": {"type": "elm327_serial", "port": "COM3",
                              "baudrate": 38400, "timeout": 5.0}})
    t = build_transport(cfg)
    assert isinstance(t, Elm327Transport)
    assert t.port == "COM3"
    assert t.baudrate == 38400
    assert t.vid_hint is None and t.pid_hint is None


def test_build_transport_tcp_path_is_unchanged():
    """The simulator/TCP transport must not be affected by serial discovery."""
    from collector import build_transport
    from config import Config
    from diagnostic.elm327 import Elm327Transport

    cfg = Config({"adapter": {"type": "elm327_tcp", "tcp_host": "127.0.0.1",
                              "tcp_port": 35000, "timeout": 5.0}})
    t = build_transport(cfg)
    assert isinstance(t, Elm327Transport)
    assert t.host == "127.0.0.1" and t.tcp_port == 35000


def test_vid_pid_hint_prioritises_but_does_not_select(monkeypatch):
    """A hint reorders the probe; the device still has to answer ATI. A hinted
    but dead port must lose to an unhinted port that actually responds."""
    import serial

    from diagnostic import discovery
    from diagnostic.elm327 import Elm327Transport

    def fake_classify():
        hinted_dead = SerialDevice(port="COM30", description="USB-SERIAL CH340",
                                   vid=0x1A86, pid=0x7523)
        plain_live = SerialDevice(port="COM31", description="OBDLink LX")
        return [discovery.classify_device(d)
                for d in (hinted_dead, plain_live)]

    monkeypatch.setattr(discovery, "discover_and_classify", fake_classify)
    opened: list = []
    monkeypatch.setattr(serial, "Serial", _fake_serial_class(opened))

    t = Elm327Transport(port="auto", vid_hint=0x1A86, pid_hint=0x7523)

    def fake_send(command):
        if command != "ATI":
            return ["OK"]
        # The hinted CH340 is dead; the OBDLink answers.
        return ["NO DATA"] if getattr(t._dev, "port", None) == "COM30" \
            else ["ELM327 v1.5"]

    monkeypatch.setattr(t, "send_command", fake_send)

    assert t._autodetect_serial() == "COM31"
    # The hint made COM30 be tried first, but it did not answer, so COM31 won.
    assert opened[0] == "COM30"


# --- doctor: the serial candidate list ----------------------------------------

class _StubCfg:
    """Minimal stand-in for Config: the doctor only ever calls .get()."""

    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_doctor_all_ports_tries_every_usable_port(monkeypatch):
    """--all-ports is the doctor's own remedy for a dead configured port.

    Stage 3 read its candidate list from its own Stage object, but stage 2 had
    written the list onto stage 2's -- a different instance -- so the lookup
    always found {} and the `or [port]` fallback re-opened exactly the port
    that had just failed. On a Bluetooth pair that means COM4, the half that
    actually carries bytes, is never opened, while the verdict prints "Run with
    --all-ports to find the real one". Each retry also costs a ~60 s blocking
    open on a stale port, so the user waits twice for the same answer.
    """
    from diagnostic import discovery
    from tools import doctor as doc

    def fake_classify():
        hex_dev = SerialDevice(port="COM13", description="Ross-Tech HEX-USB",
                               vid=0x0403, pid=0xFA24, product="HEX-USB")
        bt_a = SerialDevice(port="COM3",
                            description="Standard Serial over Bluetooth link")
        bt_b = SerialDevice(port="COM4",
                            description="Standard Serial over Bluetooth link")
        return [discovery.classify_device(d) for d in (hex_dev, bt_a, bt_b)]

    monkeypatch.setattr(doc, "discover_and_classify", fake_classify)

    attempted: list[str] = []

    class _DeadTransport:
        def __init__(self, port=None, baudrate=38400, timeout=5.0, **kw):
            self.port = port

        def open(self):
            attempted.append(self.port)
            raise AdapterNotFoundError(f"Cannot open {self.port}")

    monkeypatch.setattr(doc, "Elm327Transport", _DeadTransport)

    d = doc.Doctor(_StubCfg({"adapter.port": "COM3"}), all_ports=True)
    d.stage_ports()
    d.stage_open()

    assert attempted, "no port was opened at all"
    assert attempted[0] == "COM3", "the configured port should still be tried first"
    assert "COM4" in attempted, (
        "--all-ports never tried the other half of the Bluetooth pair")
    assert "COM13" not in attempted, (
        "stage 2 already established a vendor-specific interface cannot work "
        "as an ELM327; opening it only costs another blocking open")


def test_doctor_without_all_ports_tries_only_the_configured_port(monkeypatch):
    """The default must stay narrow: one port, the one that was configured."""
    from diagnostic import discovery
    from tools import doctor as doc

    def fake_classify():
        return [discovery.classify_device(SerialDevice(port=p))
                for p in ("COM3", "COM4")]

    monkeypatch.setattr(doc, "discover_and_classify", fake_classify)
    attempted: list[str] = []

    class _DeadTransport:
        def __init__(self, port=None, baudrate=38400, timeout=5.0, **kw):
            self.port = port

        def open(self):
            attempted.append(self.port)
            raise AdapterNotFoundError("nope")

    monkeypatch.setattr(doc, "Elm327Transport", _DeadTransport)

    d = doc.Doctor(_StubCfg({"adapter.port": "COM3"}))
    d.stage_ports()
    d.stage_open()

    assert attempted == ["COM3"]
