# MEB gateway HV energy content — DID 0x2AB8 / 0x2AB2

Status: **implemented offline, NOT validated on a vehicle.**
Last updated: 2026-10-15.

This is the record for the two read-only gateway measurements added to this
project. It supersedes the "unresolved" entries in
`measurement_definitions.md`, `ecu_addressing.md` and `live_measurement_pipeline.md`.

**No diagnostic request was sent to a vehicle while producing this.**
Every number below is either taken from the recovered Car Scanner profile or
computed by the offline unit tests. There are no captured ID.3 response frames.

---

## 1. Source of the definitions

| Item | Value |
|---|---|
| Origin | Car Scanner (ELM327 OBD2) `com.ovz.carscanner` 2.2.21, static reverse engineering |
| Where the definitions live | embedded resource `CarScannerMaui.EmbeddedFiles.profiles7.db` |
| Decode chain | LZMA-Alone → `ShiftStream2` (even `-0x58-(p&0xFF)`, odd `+0x45+(p&0xFF)`) → BSON → `List<OBDReaderProfileV2>` |
| Target profile | index **342** — *"EV MEB: ID.3, ID.4, ID.5, ID.6, ID.7, ID.Buzz, Enyaq, Elroq, Q4 E-tron, Q6 e-tron, Cupra Born, Cupra Tavascan, Porsche Macan EV, …"* |
| Measurements in that profile | 308 `CustomPID` records |
| Record type | `CarScannerMaui.OBD2.PIDS.CustomPID` (JSON keys `CMD`, `HDR`, `SBI`, `DL`, `MUL`, `DIV`, `OFS`, `SIG`, `RBS`, `TP`, `UN`, `BCM`, `ACM`) |
| Reference decoder | `CustomPID.DecodeByteSet` — big-endian when `RBS=false`, then `value = raw * MUL / DIV + OFS` |
| Reference transport | plain UDS over ISO-TP; **not** the legacy `VWTP20` path (that one targets PQ/MQB platforms) |

The two records, verbatim from the recovered profile:

```json
{"NM":"[19.Gate] Maximum energy content of the traction battery",
 "CMD":"222AB2","SBI":0,"DL":2,"MUL":50.0,"DIV":1.0,"OFS":0.0,
 "SIG":false,"RBS":false,"TP":1,"UN":61,"HDR":"710",
 "BCM":"ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A",
 "ACM":"ATFCSM0;ATAR;ATSP7"}

{"NM":"[19.Gate] HV Battery energy content",
 "CMD":"222AB8","SBI":2,"DL":2,"MUL":25.0,"DIV":1.0,"OFS":0.0,
 "SIG":false,"RBS":false,"TP":1,"UN":61,"HDR":"710",
 "BCM":"ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A",
 "ACM":"ATFCSM0;ATAR;ATSP7"}
```

`TP=1` is `CustomPIDType.ByteSet`; `UN=61` is Wh; `SIG=false` → unsigned;
`RBS=false` → big-endian. The project's `hv_energy_content` /
`hv_energy_max` keys are named after the two measurements.

**This is a third-party static definition, not a Volkswagen specification.**
It is treated as `doc_status = experimentally determined` in the database
because it has *not* been confirmed against this vehicle — the label is the
conservative choice, and raw bytes are stored either way.

---

## 2. The two measurements and their conversions

| key | DID | reference name | offset (`SBI`) | length (`DL`) | scale (`MUL`) | unit |
|---|---|---|---|---|---|---|
| `hv_energy_content` | `0x2AB8` | `[19.Gate] HV Battery energy content` | 2 | 2 | 25 | Wh |
| `hv_energy_max` | `0x2AB2` | `[19.Gate] Maximum energy content of the traction battery` | 0 | 2 | 50 | Wh |

With `d` = the DID **data record**, i.e. the bytes after the positive
response marker and the echoed DID:

```text
22 2A B8  ->  62 2A B8  d[0] d[1] d[2] d[3] ...
                       \_____________/  \___________/
                        DID echo (2 B)   d = data record

hv_energy_content = ((d[2] << 8) | d[3]) * 25      # Wh
hv_energy_max     = ((d[0] << 8) | d[1]) * 50      # Wh
```

Decoder: `decoders/gateway.py` (`_wh_at`). A record shorter than
`offset + 2` raises, `DIDSpec.decode_value()` turns that into `None`, and the
collector stores the row as unsuccessful **with the raw bytes preserved** —
no value is ever invented from the wrong bytes.

### 2.1 Why 25 Wh and 50 Wh are consistent — and what that is *not*

Arithmetic plausibility only (**not evidence, no frame captured**): an
ID.3 58 kWh pack needs raw `1160` at 50 Wh/LSB (`2AB2` → 58 000 Wh gross).
The same raw value read through `2AB8` yields 29 000 Wh, i.e. the two DIDs
describe the same quantity at different resolution — current vs maximum. This
does **not** prove the scales; it only shows they are not mutually absurd.

---

## 3. Request path, layer by layer

The layers are easy to conflate; this is where each one lives in this codebase.

| Layer | What it means | `[19.Gate]` value | Code in this repo |
|---|---|---|---|
| 1. ECU addressing | 11-bit CAN id the request is sent **to** | not stated by the profile (no `ATSH` in `BCM`); `0x792` used here | `Elm327Transport.set_header()` → `ATSH792`; `diagnostic/ecus.py` `ECUS["gate"].tx` |
| 2. Response filtering | which CAN ids the adapter is allowed to report | `ATCRA77A` (semantics unresolved — §4) / declared response header `0x710` | `Elm327Transport.set_receive_address()` → `ATCRA710`; `ECUS["gate"].rx` |
| 3. ISO-TP | PCI byte, segmentation, flow control | handled by the ELM327 itself for requests it sends; no `ATCP17/18` in this record | `uds.parse_elm_lines()` / `uds.reassemble()` only reassemble |
| 4. Response header + marker | CAN id + positive-response byte | `0x710`, marker `0x62` (`0x22 + 0x40`) | `uds.line_to_frame()`, `uds.expect_positive()` |
| 5. DID echo | 2 bytes echoed by the ECU | `2A B8` / `2A B2` | `DiagnosticConnection.read_did()` validates the echo and returns the data record |
| 6. Payload offset + scaling | index into the data record, then scaling | `SBI=2`/`0`, `DL=2`, `×25`/`×50` | `decoders/gateway.py` |

Notes:

* The reference app passes the *same* bytes to its decoder: it strips the
  `0x62` marker and the 2-byte DID echo before `Decode()` (visible in
  `OBDDataReader` 11-bit path: for `22…` requests it skips 6 hex chars =
  3 bytes). `read_did()` returns exactly that, so offsets are directly
  comparable.
* `ATFCSH710` / `ATFCSM1` / `ATFCSD300000` in the reference `BCM` are ELM
  *flow-control* settings. They are deliberately **not** implemented here:
  this transport issues only `ATSH`/`ATCRA` and the ELM handles flow control
  for single-frame requests. Adding ATFCSH/ATFCSM would change live
  behaviour on unvalidated assumptions.
* The reference `ACM` (`ATFCSM0;ATAR;ATSP7`) restores 29-bit mode after the
  measurement. This project is 11-bit throughout, so there is nothing to
  restore.

---

## 4. Addressing: what the evidence supports, and what we chose

| Claim | Evidence | Level |
|---|---|---|
| `0x792` is the gateway request id | only address explicitly named "Gateway" in Car Scanner code (`BuildList`, `GetECUName`) — from the earlier audit in `ecu_addressing.md` | code-verified name, not on-car verified |
| `0x710` is the expected response header | `HDR` field **and** `ATFCSH710` in the same records | consistent, twice |
| `ATCRA77A` is a response CAN id | **contradicted** — see below | rejected |
| The request id is `0x792` when the response filter is `0x710` | no `ATSP6`-level `ATSH` exists in the record; `0x772` (= 0x710 − 8) is equally consistent | unresolved |

**The `ATCRA` anomaly (new finding, verified across the profile).** In the
recovered MEB profile, `ATCRA = declared response header + 0x6A` for *every*
11-bit ECU class:

| ECU label | response header | ATCRA | difference |
|---|---|---|---|
| `19.Gate` | `0x710` | `0x77A` | `+0x6A` |
| `08.HVAC` | `0x746` | `0x7B0` | `+0x6A` |
| `C6.Charger` | `0x744` | `0x7AE` | `+0x6A` |
| `03.ABS` | `0x713` | `0x77D` | `+0x6A` |
| `75.GPS` | `0x767` | `0x7D1` | `+0x6A` |

A constant offset across five independent ECU classes means the 3-digit
`ATCRA` argument is some Car Scanner encoding — **not** a raw response CAN
id. (An earlier draft of this project assumed `0x77A = 0x792 + 8`; that is
arithmetically false: `0x792 + 8 = 0x79A`. The assumption was dropped.)

**Choice made:** filter on `0x710` (`ECUS["gate"].rx`), the id the profile
declares twice, rather than copying an argument whose meaning is unknown.
If the adapter reports nothing, the fallback order is `0x77A` → no filter →
`ATSH772`, each a one-line change in `diagnostic/ecus.py`.

---

## 5. Confidence summary

| Claim | Confidence |
|---|---|
| DIDs are `0x2AB8` (current) and `0x2AB2` (max), service `0x22` | **verified from recovered source** |
| Byte order unsigned big-endian, 2 bytes | **verified from recovered source** |
| Scales 25 Wh / 50 Wh, offsets 2 / 0, unit Wh | **verified from recovered source** |
| MEB uses UDS-over-ISO-TP, not VWTP20 | **verified from recovered source** |
| Our decoder reproduces `DecodeByteSet` semantics | **verified by offline unit tests** |
| Gateway request id `0x792` | **inferred** (code name only) |
| Gateway response header `0x710` | **inferred** (profile metadata, never seen on the wire) |
| `ATCRA` semantics | **unresolved** (§4) |
| That `0x2AB8` returns ≥ 4 bytes, i.e. that `SBI=2` is real | **unresolved** — no captured frame |
| That the scales are right on an ID.3 | **unresolved** — no captured frame |

---

## 6. Unresolved transport assumptions (each with a falsification test)

1. **`SBI=2` for `0x2AB8`.** If the real record is shorter than 4 bytes the
   value comes back `None` and the row is stored unsuccessful. *Falsified by*
   a captured `62 2A B8 …` shorter than 4 data bytes, or by a value that is
   inconsistent with the displayed range.
2. **`ATCRA` semantics.** *Falsified/confirmed* by capturing which CAN id the
   gateway actually answers on.
3. **Request id.** *Falsified* by a `NO DATA` at `ATSH792` while `ATSH772`
   answers, or the reverse.
4. **Response filtering on `0x710`.** *Falsified* by a `NO DATA` at
   `ATCRA710` when the vehicle does answer.
5. **Wh scales on this pack.** *Falsified* by comparing `hv_energy_max`
   against the published pack capacity and `hv_energy_content` against the
   dash SoC: `content/max ≈ SoC`.

---

## 7. Proposed in-car validation — **NOT PERFORMED, requires separate authorisation**

Read-only, `0x22` only, no session change, no clearing, no coding.

```bat
:: 1. adapter plugged in, vehicle awake, ignition on
:: 2. discovery probe of the gateway (sends 0x10 0x01 - allow-listed, non-modifying)
python main.py discover
::    expect a line "gate  ..." - record responder / no-response / nrc-XX

:: 3. if 'no-response', try the fallbacks ONE AT A TIME, editing
::    diagnostic/ecus.py ECUS["gate"]:
::      rx = 0x77A   (Car Scanner's ATCRA literal)
::      rx = None   (clear the filter, let everything through)
::      tx = 0x772   (alternative request id)
::    re-running `python main.py discover` after each single change

:: 4. one collection cycle; both DIDs should now be recorded
python main.py collect --cycles 1

:: 5. inspect what the adapter actually returned
python -c "import sqlite3;c=sqlite3.connect('data/odb_scanner.db');\
print(c.execute(\"select ts,key,unit,value,raw_response,success,error from measurements where ecu='gate'\").fetchall())"
python -c "import sqlite3;c=sqlite3.connect('data/odb_scanner.db');\
print(c.execute(\"select payload,purpose from tx_log where ecu='gate' order by id\").fetchall())"

:: 6. sanity checks before trusting the scale
::    - hv_energy_max should be near the published gross capacity
::      (58 kWh pack -> raw ~1160 at 50 Wh/LSB)
::    - hv_energy_content / hv_energy_max should track the dash SoC
::    - hv_energy_content must stay below hv_energy_max
```

Every transmission is already recorded in `tx_log` with its purpose, so step 5
is a read of the local database, not a new request. Stop at the first
`NO DATA`; do **not** start probing other addresses.

---

## 8. Where this is tested

`tests/test_gateway_energy.py` (offline, no vehicle) covers request
construction, ECU addressing, the offset semantics, big-endian decoding, both
scale factors, truncated/malformed/negative responses, multi-frame
reassembly, no-response and transport timeout, the read-only allow-list and
the rejection of unsupported services.
