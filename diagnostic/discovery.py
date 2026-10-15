"""Adapter discovery: enumerate, classify and select OBD interfaces.

WHY THIS IS SEPARATE FROM THE TRANSPORT
---------------------------------------
Finding a COM port is not the same as knowing what is on the other end of it,
and neither is the same as having spoken to it. Conflating those is how an
application ends up sending ELM327 `AT` commands to a device that speaks a
proprietary protocol, or "auto-selecting" the wrong half of a Bluetooth pair,
or claiming a Ross-Tech interface works because its vendor ID matched.

This module keeps the stages distinct and labels how far each device got:

    DETECTED             a serial interface exists
    IDENTIFIED           its metadata gives reasonable confidence what it is
    TRANSPORT_SUPPORTED  this project has a transport that can drive it
    CONNECTED            the port actually opened
    VERIFIED             a diagnostic exchange (ATI) confirmed the protocol

A device that is merely DETECTED is never treated as usable. Classification is
metadata only; VERIFIED only ever comes from an actual exchange.

CONSERVATISM RULES (deliberate, and the reason this module exists)
------------------------------------------------------------------
* VID 0x0403 is Future Technology Devices International (FTDI). Thousands of
  unrelated products use FTDI chips, and some Ross-Tech HEX interfaces do too --
  but the vendor ID identifies the CHIP VENDOR, never the product. Nothing here
  infers "Ross-Tech" (or "ELM327") from 0x0403 alone.
* A generic USB-serial chip (CH340, CP210x, FTDI, PL2303) proves the device can
  carry bytes, not that it speaks OBD. Such a device is classified
  USB_SERIAL_OTHER: OBD capability is UNVERIFIED until an ATI probe answers.
* Vendor-specific interfaces are identified by the device's OWN reported
  product/manufacturer string -- actual device metadata -- not by a guessed
  VID:PID. A device that names a known proprietary tool is refused rather than
  forced into the ELM327 serial abstraction, because it will not answer AT
  commands and pretending otherwise wastes the user's time and mislabels the
  failure.
* When several plausible adapters are present, selection raises and lists the
  candidates. It never silently picks the first one: the first enumerated port
  is frequently the incoming half of a Bluetooth pair, which opens cleanly and
  then returns nothing at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from .interface import AdapterNotFoundError, CommunicationError

log = logging.getLogger(__name__)

# Adapter-local commands used to verify a link. These configure/identify the
# adapter itself and reach no ECU, so they are not vehicle requests and need no
# validate_request() -- but they are the ONLY things verification sends, and it
# never proceeds to a vehicle request. Keeping verification to ATI means "test
# the connection" can never put a diagnostic frame on the bus.
_VERIFY_COMMANDS = ("ATI",)


class DeviceClass(str, Enum):
    """What kind of serial device this looks like, from metadata alone."""

    BLUETOOTH_SPP = "bluetooth_spp"
    ELM327_USB_SERIAL = "elm327_usb_serial"
    USB_SERIAL_OTHER = "usb_serial_other"
    VENDOR_SPECIFIC = "vendor_specific"
    UNKNOWN = "unknown"


class Evidence(str, Enum):
    """How far discovery actually got with a device. Strictly ordered."""

    DETECTED = "detected"
    IDENTIFIED = "identified"
    TRANSPORT_SUPPORTED = "transport_supported"
    CONNECTED = "connected"
    VERIFIED = "verified"

    def __lt__(self, other: Evidence) -> bool:  # allow ordering comparisons
        order = list(Evidence)
        return order.index(self) < order.index(other)

    def __le__(self, other: Evidence) -> bool:
        return self == other or self < other


class UnsupportedAdapterError(AdapterNotFoundError):
    """A device was found but this project has no transport for its protocol.

    Distinct from AdapterNotFoundError (nothing there) and CommunicationError
    (something there, but it did not answer): this says "we know what you
    plugged in and we deliberately do not drive it as a generic serial adapter."
    """


# --- USB-serial CHIP signatures -------------------------------------------------
# These identify the serial CHIP, which is reliable, and say nothing about
# whether the device speaks OBD. A match here yields USB_SERIAL_OTHER, never
# ELM327_USB_SERIAL. (Vendor IDs are public USB-IF assignments; the product IDs
# listed are the common defaults for each chip family.)
USB_SERIAL_CHIPS: dict[int, str] = {
    0x1A86: "CH340/CH341 (WCH)",
    0x10C4: "CP210x (Silicon Labs)",
    0x0403: "FTDI",
    0x067B: "PL2303 (Prolific)",
    0x0557: "ATEN/UC232",
    0x1B4F: "CP210x (SiOx variant)",
}

# Substrings in a device's OWN reported strings that strongly indicate an
# ELM327-class OBD adapter. Matched case-insensitively against description,
# product and manufacturer. This is identification by the device's own name,
# not by a guessed VID:PID.
ELM_NAME_HINTS = ("elm327", "elm 327", "obdlink", "scantool.net", "stn1",
                  "stn2", "vlinker", "viecar", "obdii", "obd-ii", "obd2")

# Substrings that indicate a Bluetooth serial-over-SPP port. Windows names the
# outgoing port "Standard Serial over Bluetooth link"; the device class guid on
# other platforms shows as BTHENUM/RFCOMM.
BLUETOOTH_HINTS = ("bluetooth", "standard serial over bluetooth", "bthenum",
                   "rfcomm", "spp")

# Substrings in the device's OWN reported strings that indicate a vendor
# interface speaking a PROPRIETARY protocol -- not ELM327. These are refused by
# transport_for() rather than driven as a serial adapter. Keyed on the reported
# name, never on a VID, so an unrelated FTDI device is not mislabelled.
VENDOR_SPECIFIC_HINTS = ("ross-tech", "ross tech", "hex-v2", "hex v2",
                         "hex-usb", "hex usb", "hex-net", "vcds")


@dataclass
class SerialDevice:
    """One enumerated serial interface, with whatever metadata the OS gave us.

    Every field except `port` may be None: pyserial populates VID/PID/strings
    only for USB devices that expose them, and a Bluetooth SPP port usually has
    none. Missing metadata is normal, not an error, and classification degrades
    to UNKNOWN rather than guessing.
    """

    port: str
    description: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    location: str | None = None
    interface: str | None = None

    @classmethod
    def from_list_port_info(cls, info) -> SerialDevice:
        """Build from a serial.tools.list_ports_common.ListPortInfo.

        Reads each attribute defensively: a given platform/backend may omit any
        of them, and getattr-with-default keeps enumeration working on a device
        whose descriptor is partially unreadable.
        """
        def _get(name):
            value = getattr(info, name, None)
            return value if value not in ("", None) else None

        return cls(
            port=getattr(info, "device", "") or "",
            description=getattr(info, "description", "") or "",
            hwid=getattr(info, "hwid", "") or "",
            vid=_get("vid"),
            pid=_get("pid"),
            serial_number=_get("serial_number"),
            manufacturer=_get("manufacturer"),
            product=_get("product"),
            location=_get("location"),
            interface=_get("interface"),
        )

    def _ident_strings(self) -> str:
        """All human-reported strings, lowercased, for substring matching."""
        return " ".join(
            s for s in (self.description, self.product, self.manufacturer,
                        self.interface, self.hwid) if s
        ).lower()

    def summary(self) -> str:
        """One-line human description for logs and candidate listings."""
        bits = [self.port]
        if self.description:
            bits.append(self.description)
        if self.vid is not None and self.pid is not None:
            bits.append(f"VID:PID={self.vid:04X}:{self.pid:04X}")
        if self.serial_number:
            bits.append(f"SN={self.serial_number}")
        return "  ".join(bits)


@dataclass
class Classification:
    """The result of classifying one device from metadata alone."""

    device: SerialDevice
    device_class: DeviceClass
    evidence: Evidence
    reason: str
    transport: str | None = None       # transport name that can drive it, if any
    chip: str | None = None            # identified USB-serial chip, if any
    notes: list[str] = field(default_factory=list)

    @property
    def usable_as_elm327(self) -> bool:
        """True when this device is a candidate for the ELM327 serial transport.

        USB_SERIAL_OTHER is a *candidate*: the chip can carry bytes, but OBD
        capability is unverified until an ATI probe answers. VENDOR_SPECIFIC and
        UNKNOWN are not candidates.
        """
        return self.device_class in (
            DeviceClass.BLUETOOTH_SPP,
            DeviceClass.ELM327_USB_SERIAL,
            DeviceClass.USB_SERIAL_OTHER,
        )


def classify_device(device: SerialDevice) -> Classification:
    """Classify one device from its metadata. Never opens the port.

    Order matters: vendor-specific and Bluetooth are checked before the generic
    ELM/chip rules, so a device that names a proprietary tool is not swept into
    "probably an ELM327 clone" by its chip vendor ID.
    """
    ident = device._ident_strings()

    # 1. Vendor-specific proprietary interface, identified by its own name.
    for hint in VENDOR_SPECIFIC_HINTS:
        if hint in ident:
            return Classification(
                device=device,
                device_class=DeviceClass.VENDOR_SPECIFIC,
                evidence=Evidence.IDENTIFIED,
                reason=(f"device reports {hint!r}, a vendor interface with a "
                        "proprietary protocol"),
                transport=None,
                notes=["Not driven as a generic ELM327 serial adapter: it will "
                       "not answer AT commands. Support requires a documented "
                       "protocol implementation."],
            )

    # 2. Bluetooth serial-over-SPP (e.g. the Veepeak's outgoing COM port).
    for hint in BLUETOOTH_HINTS:
        if hint in ident:
            return Classification(
                device=device,
                device_class=DeviceClass.BLUETOOTH_SPP,
                evidence=Evidence.IDENTIFIED,
                reason=f"device reports {hint!r} (Bluetooth serial)",
                transport="elm327_serial",
                notes=["Windows creates TWO ports per Bluetooth device; only "
                       "the OUTGOING one carries bytes. The incoming one opens "
                       "cleanly and returns nothing."],
            )

    # 3. Explicit ELM327/OBD name in the device's own strings.
    for hint in ELM_NAME_HINTS:
        if hint in ident:
            return Classification(
                device=device,
                device_class=DeviceClass.ELM327_USB_SERIAL,
                evidence=Evidence.IDENTIFIED,
                reason=f"device reports {hint!r} (ELM327-class OBD adapter)",
                transport="elm327_serial",
                notes=["Identification by reported name; protocol still needs "
                       "an ATI probe to be VERIFIED."],
            )

    # 4. A known generic USB-serial chip. Carries bytes; OBD capability
    #    UNVERIFIED. This is where an FTDI device lands -- NOT "Ross-Tech".
    if device.vid in USB_SERIAL_CHIPS:
        chip = USB_SERIAL_CHIPS[device.vid]
        notes = ["USB-serial chip identified; this proves the device can carry "
                 "bytes, not that it speaks OBD. Verify with an ATI probe."]
        if device.vid == 0x0403:
            notes.append(
                "VID 0403 is FTDI, a chip vendor shared by thousands of "
                "unrelated products (and by some Ross-Tech HEX interfaces). It "
                "identifies the chip, never the product -- do not infer "
                "Ross-Tech or ELM327 from it.")
        return Classification(
            device=device,
            device_class=DeviceClass.USB_SERIAL_OTHER,
            evidence=Evidence.IDENTIFIED,
            reason=f"USB-serial chip: {chip}",
            transport="elm327_serial",
            chip=chip,
            notes=notes,
        )

    # 5. A USB device we do not recognise, or no metadata at all.
    if device.vid is not None or device.pid is not None:
        return Classification(
            device=device,
            device_class=DeviceClass.UNKNOWN,
            evidence=Evidence.DETECTED,
            reason="USB device with no recognised serial-chip or OBD identity",
            transport="elm327_serial",
            notes=["Unknown hardware. It can be opened as a serial port and "
                   "probed with ATI, but nothing here identifies it."],
        )
    return Classification(
        device=device,
        device_class=DeviceClass.UNKNOWN,
        evidence=Evidence.DETECTED,
        reason="serial interface with no identifying metadata",
        transport="elm327_serial",
        notes=["No VID/PID or description. Could be a Bluetooth port, a "
               "virtual COM port, or anything else."],
    )


def discover_serial_devices() -> list[SerialDevice]:
    """Enumerate serial interfaces via pyserial. No COM-port range probing.

    Returns every port the OS reports, each wrapped in a SerialDevice with
    whatever metadata is available. An empty list means pyserial saw no serial
    interfaces at all -- on Windows that usually means an unpaired Bluetooth
    adapter or a device whose driver is not loaded.
    """
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a hard dependency
        log.warning("pyserial not installed; cannot enumerate serial devices")
        return []
    devices = []
    for info in list_ports.comports():
        device = SerialDevice.from_list_port_info(info)
        if device.port:
            devices.append(device)
    return devices


def discover_and_classify() -> list[Classification]:
    """Convenience: every enumerated device with its classification."""
    return [classify_device(d) for d in discover_serial_devices()]


class AmbiguousAdapterError(AdapterNotFoundError):
    """More than one plausible adapter and no configured preference."""

    def __init__(self, candidates: list[Classification]):
        self.candidates = candidates
        listing = "\n".join(
            f"  - {c.device.summary()}  [{c.device_class.value}: {c.reason}]"
            for c in candidates
        )
        super().__init__(
            "Multiple plausible OBD adapters are present and none is "
            "configured. Refusing to guess (the first enumerated port is often "
            "the wrong half of a Bluetooth pair). Candidates:\n" + listing +
            "\nSet adapter.port to the COM port you want, or pass --port.")


def _matches_hint(device: SerialDevice, vid: int | None,
                  pid: int | None) -> bool:
    """True when a device matches the configured VID/PID hint.

    A hint with only VID set matches any device with that VID. Hints narrow the
    candidate set; they do NOT verify protocol -- a VID/PID match still yields
    whatever classification the metadata gives, and still needs an ATI probe to
    be VERIFIED.
    """
    if vid is not None and device.vid != vid:
        return False
    if pid is not None and device.pid != pid:
        return False
    return True


def select_device(devices: list[SerialDevice] | None = None, *,
                  configured_port: str | None = None,
                  vid_hint: int | None = None,
                  pid_hint: int | None = None,
                  include_unverified: bool = True) -> Classification:
    """Choose one device to connect, or explain why none can be chosen.

    Selection order:
      1. An explicit `configured_port` (or adapter.port that is not "auto")
         wins outright -- the user told us which port, so honour it even if its
         metadata is unremarkable. A configured port that is absent raises with
         the list of ports that ARE present.
      2. Otherwise, VID/PID hints (when given) narrow the candidates.
      3. Otherwise, every device that classifies as an ELM327 candidate.

    Raises:
      AdapterNotFoundError    no serial interfaces at all, or none plausible
      AmbiguousAdapterError   several plausible ones and no preference
      UnsupportedAdapterError the chosen/only device is vendor-specific
    """
    if devices is None:
        devices = discover_serial_devices()
    classified = [classify_device(d) for d in devices]

    if not classified:
        raise AdapterNotFoundError(
            "No serial interfaces found. On Windows check Device Manager > "
            "Ports (COM & LPT); an unpaired Bluetooth adapter does not appear "
            "here.")

    # 1. Explicit port selection.
    wanted = (configured_port or "").strip()
    if wanted and wanted.lower() not in ("auto", "none"):
        match = next((c for c in classified
                      if c.device.port.lower() == wanted.lower()), None)
        if match is None:
            present = ", ".join(c.device.port for c in classified)
            raise AdapterNotFoundError(
                f"Configured port {wanted} is not present. Serial interfaces "
                f"found: {present or '(none)'}.")
        if match.device_class == DeviceClass.VENDOR_SPECIFIC:
            raise UnsupportedAdapterError(
                f"{match.device.summary()} is a vendor-specific interface "
                f"({match.reason}). It is not driven as an ELM327 serial "
                "adapter. " + " ".join(match.notes))
        return match

    # 2/3. Candidate filtering.
    candidates = classified
    if vid_hint is not None or pid_hint is not None:
        hinted = [c for c in candidates
                  if _matches_hint(c.device, vid_hint, pid_hint)]
        if hinted:
            candidates = hinted
        else:
            log.info("VID/PID hint %s:%s matched nothing; falling back to all "
                     "devices", vid_hint, pid_hint)

    # A vendor-specific device is never an auto-select candidate, but if it is
    # the ONLY device present we say so explicitly rather than "nothing found".
    plausible = [c for c in candidates if c.usable_as_elm327] if \
        include_unverified else \
        [c for c in candidates
         if c.device_class in (DeviceClass.BLUETOOTH_SPP,
                               DeviceClass.ELM327_USB_SERIAL)]
    if not plausible:
        vendor = [c for c in candidates
                  if c.device_class == DeviceClass.VENDOR_SPECIFIC]
        if vendor and len(vendor) == len(candidates):
            c = vendor[0]
            raise UnsupportedAdapterError(
                f"The only serial device present is vendor-specific: "
                f"{c.device.summary()} ({c.reason}). " + " ".join(c.notes))
        raise AdapterNotFoundError(
            "No plausible OBD adapter among the serial interfaces found: "
            + ", ".join(c.device.summary() for c in candidates))

    if len(plausible) > 1:
        raise AmbiguousAdapterError(plausible)
    return plausible[0]


def build_transport(classification: Classification, *, baudrate: int = 38400,
                    timeout: float = 5.0):
    """Return an OBDInterface for a classified device, or refuse it.

    Refuses VENDOR_SPECIFIC outright: there is no transport for a proprietary
    protocol, and opening the port anyway would just produce garbage that looks
    like a vehicle fault. Every serial class maps to the one ELM327 transport
    this project has -- Bluetooth SPP and USB serial are both just COM ports to
    pyserial, so they do not need (and must not get) separate fake transports.

    The transport is returned UNOPENED. Opening is the caller's step so that
    "identified" and "connected" stay distinguishable: build_transport reaching
    TRANSPORT_SUPPORTED does not mean the port will open.
    """
    if classification.device_class == DeviceClass.VENDOR_SPECIFIC:
        raise UnsupportedAdapterError(
            f"{classification.device.summary()} is a vendor-specific interface "
            f"({classification.reason}); no transport. "
            + " ".join(classification.notes))
    if classification.transport != "elm327_serial":
        raise UnsupportedAdapterError(
            f"No transport for {classification.device.summary()} "
            f"(class {classification.device_class.value}).")
    # Local import: discovery must not import the transport at module load, or
    # the dependency runs the wrong way for callers that only enumerate.
    from .elm327 import Elm327Transport
    return Elm327Transport(port=classification.device.port,
                           baudrate=baudrate, timeout=timeout)


def verify_adapter(transport, *, timeout: float | None = None) -> str:
    """Open the transport and confirm it answers ATI. Returns its identity.

    This is the step that turns CONNECTED into VERIFIED, and it is the only
    place discovery touches the wire. It sends ATI and nothing else -- no
    vehicle request, no protocol negotiation -- so "test my connection" can
    never put a diagnostic frame on the bus or wake an ECU.

    Bounded: the transport's own read timeout caps the wait, and a failure
    closes the port so a half-open handle is never leaked. Raises:
      AdapterNotFoundError  the port would not open
      CommunicationError    it opened but did not answer ATI like an ELM327
    """

    opened_here = False
    if not transport.is_open:
        transport.open()          # raises AdapterNotFoundError if it cannot
        opened_here = True
    try:
        resp = transport.send_command("ATI")
        identity = " ".join(resp).strip()
        upper = identity.upper()
        if not identity or "?" in identity:
            raise CommunicationError(
                f"Adapter did not answer ATI (got {identity!r}). The port "
                "opened, so this is a protocol/device mismatch, not a missing "
                "adapter -- it may not be an ELM327-class interface.")
        # A genuine ELM327-class adapter reports its identity. We do not REQUIRE
        # the literal "ELM327" substring (clones say "ELM327 v1.5", OBDLink says
        # "STN11xx", vLinker says "vLinker") -- answering ATI coherently is the
        # verification; the hint only annotates confidence.
        known = any(h in upper for h in
                    ("ELM327", "STN", "OBDLINK", "VLINKER", "VIECAR"))
        log.info("Adapter verified via ATI: %r (known family: %s)",
                 identity, known)
        return identity
    except Exception:
        if opened_here:
            try:
                transport.close()
            except Exception:  # pragma: no cover - best effort
                pass
        raise


def connect_and_verify(classification: Classification, *, baudrate: int = 38400,
                       timeout: float = 5.0):
    """Build, open and verify a transport for one classified device.

    Returns (transport, identity) on success. On any failure the transport is
    closed so the port is released cleanly -- a leaked handle is what makes the
    next connection attempt fail with "access denied" and sends the user
    chasing a phantom.
    """
    transport = build_transport(classification, baudrate=baudrate,
                                timeout=timeout)
    identity = verify_adapter(transport, timeout=timeout)
    return transport, identity
