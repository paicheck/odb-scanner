# OBD adapters: discovery, transports and limits

This document covers which OBD interfaces this application can use, how it
finds them, how to select one, and — just as important — what it deliberately
refuses to assume about hardware it has not spoken to.

All commands below are Windows PowerShell using the project virtual
environment.

```powershell
cd C:\odb_scanner
.\.venv\Scripts\python.exe tools\discover_adapters.py
```

---

## 1. The five levels of knowing an adapter

Finding a COM port is not the same as knowing what is on the other end of it,
and neither is the same as having spoken to it. The discovery layer
(`diagnostic/discovery.py`) keeps these stages separate and labels every device
with the highest one it has actually reached:

| Evidence | Meaning | How it is reached |
|---|---|---|
| `detected` | a serial interface exists | enumerated via pyserial |
| `identified` | metadata gives reasonable confidence what it is | VID/PID + reported product strings |
| `transport_supported` | this project has a transport that can drive it | classification maps to a transport |
| `connected` | the port actually opened | `open()` succeeded |
| `verified` | a diagnostic exchange confirmed the protocol | an `ATI` probe answered |

**A device that is merely `detected` is never treated as usable.** Finding a
COM port is not proof the adapter supports OBD communication. Only an actual
exchange (`verified`) proves that, and until then the honest label is a lower
one.

---

## 2. Supported adapter types

| Class | What it is | Transport | Status |
|---|---|---|---|
| `bluetooth_spp` | Bluetooth Serial Port Profile (the Veepeak OBDCheck BLE+) | ELM327 over the outgoing COM port | **Supported, in use** |
| `elm327_usb_serial` | USB adapter that names itself ELM327/OBDLink/vLinker | ELM327 serial | Supported |
| `usb_serial_other` | a generic USB-serial chip (CH340, CP210x, FTDI, PL2303) | ELM327 serial, **unverified** | Candidate — must answer `ATI` |
| `vendor_specific` | a proprietary interface (e.g. Ross-Tech HEX) | none | **Refused**, see §6 |
| `unknown` | no identifying metadata | ELM327 serial, **unverified** | Candidate only if explicitly configured |

The application has exactly **one** wire protocol: ELM327-style `AT` commands
plus ISO-TP / UDS framing on top (`diagnostic/elm327.py`,
`diagnostic/connection.py`, `diagnostic/uds.py`). Bluetooth SPP and USB serial
are both just COM ports to pyserial, so they share that transport. There is no
second transport, and nothing here pretends a non-ELM327 device can be driven
as one.

---

## 3. Running discovery

```powershell
# Enumerate and classify every serial interface (opens nothing, sends nothing)
.\.venv\Scripts\python.exe tools\discover_adapters.py

# Machine-readable
.\.venv\Scripts\python.exe tools\discover_adapters.py --json

# Also open each candidate and send ATI to confirm it is ELM327-class
.\.venv\Scripts\python.exe tools\discover_adapters.py --verify
```

`discover_adapters.py` without `--verify` is completely inert: it reads OS
device metadata and opens no port. With `--verify` it opens each candidate and
sends **`ATI` and nothing else**. `ATI` is an adapter-local identification
command — it reaches no ECU and puts no diagnostic frame on the bus — so
verifying a connection cannot alter the vehicle.

The full diagnostic check (which does talk to the car, read-only) is:

```powershell
.\.venv\Scripts\python.exe tools\doctor.py
```

Stage 2 of the doctor now lists each port with its class, evidence and
VID:PID, and warns about any vendor-specific interface it will not drive.

---

## 4. Selecting a COM port

### Automatic

`config.yaml` ships with `adapter.port: COM3`. Set it to `auto` to let the
application scan:

```yaml
adapter:
  type: elm327_serial
  port: auto
```

Auto-detection enumerates real interfaces (it never probes a fixed COM1–COM20
range), classifies them, **skips vendor-specific devices**, and then probes the
rest in confidence order with `ATI`. The first port that answers with an
ELM327-family identity wins.

The `ATI` probe is what makes this safe for Bluetooth: Windows enumerates
**two** ports per Bluetooth serial device and only the *outgoing* one carries
bytes. The two are indistinguishable by metadata, so an actual exchange is the
only way to pick the working one. Classification narrows the field;
verification chooses within it.

### Manual (explicit COM port)

```yaml
adapter:
  port: COM4
```

An explicit port is honoured exactly as given — discovery does not
second-guess it. This is the recommended setting once you know which port
works, because it is deterministic.

### When several adapters are present

If more than one plausible adapter is enumerated and none is configured,
selection **raises and lists the candidates** rather than silently picking the
first. The first enumerated port is frequently the dead half of a Bluetooth
pair, which opens cleanly and then returns nothing at all — exactly the failure
that is hardest to diagnose.

```
AmbiguousAdapterError: Multiple plausible OBD adapters are present and none is
configured. Candidates:
  - COM4  Standard Serial over Bluetooth link  [bluetooth_spp]
  - COM5  USB-SERIAL CH340  VID:PID=1A86:7523  [usb_serial_other]
Set adapter.port to the COM port you want, or pass --port.
```

---

## 5. Inspecting VID/PID and hardware IDs

`tools\discover_adapters.py` prints, for each port: the COM port, class,
evidence level, description, VID:PID, serial number, identified chip, and the
reason for the classification.

To see the raw OS view:

```powershell
# Device Manager
devmgmt.msc          # then: Ports (COM & LPT) -> device -> Properties -> Details -> Hardware Ids

# Or from PowerShell
Get-PnpDevice -Class Ports | Format-List FriendlyName, InstanceId, Status
```

A hardware ID looks like `USB\VID_1A86&PID_7523\...`. The `VID_`/`PID_` pair is
what discovery reads.

### Configured VID/PID hints

```yaml
adapter:
  port: auto
  vid: 0x1A86      # accepts an int or a hex string
  pid: 0x7523
```

Hints **prioritise** a matching device during the scan. They never bypass the
`ATI` probe, so a wrong or stale hint only changes the order ports are tried —
it cannot make a dead port win. A hint is a chip/product identifier, **not**
proof of OBD support.

---

## 6. Why a Ross-Tech HEX cannot be assumed compatible

Ross-Tech HEX-V2 / HEX-USB / HEX-NET interfaces speak a **proprietary
protocol**, not ELM327 `AT` commands. Driving one as a generic serial adapter
produces garbage that looks like a vehicle fault. Discovery therefore classifies
a device that *reports itself* as a Ross-Tech HEX as `vendor_specific` and
**refuses to build a transport for it**, raising `UnsupportedAdapterError`
instead of silently failing later.

Two conservatism rules are enforced here, and both are tested:

1. **VID `0403` is never treated as "Ross-Tech".** `0403` is Future Technology
   Devices International (FTDI), a chip vendor shared by thousands of unrelated
   products — and some Ross-Tech interfaces do use FTDI chips. The vendor ID
   identifies the *chip*, never the *product*. A bare FTDI device with no
   identifying product string is classified `usb_serial_other`, not
   `vendor_specific` and not `elm327_usb_serial`.
2. **A specific VID:PID is not assumed to identify a product either.** Even the
   PID Ross-Tech has been seen with does not, on its own, make a device a
   Ross-Tech — a clone or an unrelated FTDI product can share it. Identification
   is by the device's **own reported product/manufacturer string**, which is
   real device metadata, not a guessed ID.

Supporting a vendor-specific interface properly requires implementing its
documented protocol as its own transport. That has not been done, so the honest
answer is "unsupported", not "probably works".

---

## 7. Known limits of generic USB serial adapters

A device built on a CH340, CP210x, FTDI or PL2303 chip is classified
`usb_serial_other`. What that means in practice:

- The chip proves the device can **carry bytes**. It says nothing about whether
  the thing plugged into the OBD socket speaks ELM327, ISO-TP, or the CAN
  frame format this car needs.
- It stays at `identified`, never `verified`, until an `ATI` probe answers.
- Cheap ELM327 clones on these chips frequently do **not** implement the full
  command set. Documented, observed gaps on the clone this project was
  developed against:
  - multi-frame ISO-TP reassembly (the application reassembles in software
    instead — see `diagnostic/uds.py:reassemble`),
  - `ATCRA` receive filtering (refused or ignored),
  - 8-digit `ATSH` for 29-bit identifiers (refused; the 6-digit `ATCP`+`ATSH`
    form is used instead).
- Do not assume a generic ELM327 interface supports every CAN or ISO-TP
  operation the ID.3 requires. Extended 29-bit addressing in particular is
  negotiated and **probed** at init (`_negotiate_meb`); if the adapter refuses
  it, the application falls back to functional addressing rather than failing.

---

## 8. Connection failures vs. protocol failures

These are different problems and the code reports them differently:

| Exception | Meaning | Typical cause |
|---|---|---|
| `AdapterNotFoundError` | nothing there, or the port would not open | unpaired Bluetooth, wrong COM port, adapter unplugged |
| `UnsupportedAdapterError` | found, but deliberately not driven | vendor-specific interface |
| `AmbiguousAdapterError` | several plausible adapters, none configured | two adapters plugged in |
| `CommunicationError` | opened, but no coherent answer | not an ELM327, wrong baudrate, dead Bluetooth half |
| `NegativeResponseError` | the **vehicle** answered with a UDS NRC | ECU refused the request (e.g. NRC 0x31) |

`NO DATA`, a timeout, a negative UDS response and an unsupported service are
therefore four distinguishable outcomes, not one generic "it failed". The
first four are about the link; the last is about the car.

---

## 9. Adding support for another interface safely

1. **Get the device's real metadata first.** Run
   `tools\discover_adapters.py` and note the reported description, product
   string and VID:PID. Do not guess.
2. **Find documented protocol evidence.** A vendor datasheet, a published
   protocol description, or a reproducible capture. "It showed up as a COM
   port" is not evidence it speaks ELM327.
3. **Decide whether the existing transport applies.** If the device answers
   `ATI` and accepts ELM327 `AT` commands, it is `elm327_usb_serial` /
   `usb_serial_other` and needs no new code — add its identifying string to
   `ELM_NAME_HINTS` in `diagnostic/discovery.py` only if that string is
   reliable.
4. **If it speaks something else, write a new transport.** Subclass
   `OBDInterface` (`diagnostic/interface.py`) and implement `open`, `close`,
   `send_command` and `is_open`. Do **not** force it through the ELM327
   transport.
5. **Add a classification rule keyed on the device's own reported identity,**
   never on a bare vendor ID. Add it to `classify_device` with a reason string
   that says what evidence it rests on.
6. **Add tests with mocked ports.** Follow
   `tests/test_adapter_discovery.py`: no test may require a connected car or
   real hardware.
7. **Keep it read-only.** Any new transport must still route vehicle requests
   through `diagnostic/uds.py:validate_request`, which is the single choke
   point enforcing the read-only guarantee. A transport that can send arbitrary
   bytes is a hole in that guarantee.

---

## 10. Verifying the connection against the car (read-only)

A repeatable procedure that issues no write, no coding, no adaptation and no
security access:

```powershell
cd C:\odb_scanner

# 1. What adapters does the machine see? (opens nothing)
.\.venv\Scripts\python.exe tools\discover_adapters.py

# 2. Confirm the adapter answers ATI (adapter-local; reaches no ECU)
.\.venv\Scripts\python.exe tools\discover_adapters.py --verify

# 3. Full read-only diagnostic check: config, ports, link, protocol, bus,
#    VIN, ECU discovery, DTCs
.\.venv\Scripts\python.exe tools\doctor.py

# 4. If the car is asleep, wake it: ignition on, engine off. Then re-run.
#    doctor.py distinguishes "adapter fine, vehicle asleep" from "no adapter".

# 5. One short read-only collection run
.\.venv\Scripts\python.exe main.py collect --cycles 5

# 6. Confirm the read-only guarantee is intact
.\.venv\Scripts\python.exe main.py guard-test
```

The vehicle must be awake (ignition on) for anything past step 2 to return
data. A parked, asleep ID.3 will answer `NO DATA` on the diagnostic bus, which
the doctor reports as "vehicle likely asleep" rather than as an adapter fault.

### Hardware-in-the-loop tests

The normal test suite never touches hardware. To exercise the real adapter:

```powershell
.\.venv\Scripts\python.exe tools\doctor.py --all-ports
```

`--all-ports` tries every enumerated port until one opens, which is the manual
equivalent of the mocked auto-detection tests and the quickest way to find the
working half of a Bluetooth pair.
