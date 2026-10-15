# MEB gateway HV energy content — DID 0x2AB8 / 0x2AB2

Status: **implemented, offline-tested, NOT empirically validated.**
Audited 2026-10-15 (offline code audit; no vehicle contact).
Verdict: **READY FOR USER REVIEW** — see §10.

> This document records (a) the two measurement definitions, (b) the code
> paths that were traced, (c) the addressing that is still unproven, and
> (d) a conservative single-request validation runbook with a stop rule for
> every possible first-request outcome.
>
> **No diagnostic request has ever been sent to a vehicle by this work.**
> Every byte string in the tests is synthetic. No ID.3 response has been
> captured. Nothing here is empirically validated.

---

## 0. Post-merge status (added 2026-10-15, after the C:\odb_scanner merge)

This audit was written against the pre-merge tree. The 67-commit
`C:\odb_scanner` line was then merged in (`f233d6f`). **Four claims below
are now stale, and one of them is actively unsafe to act on.** They are
listed here rather than silently rewritten throughout, so the original
audit text stays readable as a record of what was true when it was made.

| Ref | Claim in this doc | Status after the merge |
|---|---|---|
| §2 row 5 | the read path issues `ATSH792` + `ATCRA710` per read (`connection.py:41 _set_ecu()`) | **NO LONGER TRUE.** `_set_ecu()` is gone. `connection._transmit()` now addresses functionally by default and only calls `set_module(tx29, rx29)` when the transport negotiated MEB addressing **and** the `ECUSpec` carries a 29-bit address. `ECUS['gate']` has no `tx29`/`rx29`, so **no `ATSH` and no `ATCRA` are emitted at all** for these DIDs. |
| §3.1 | the response CAN header is never verified; `ATCRA` is the only barrier | **STILL TRUE — but the second half of the claim is now known to be FALSE on this adapter, and the first half is the whole defence.** See the note immediately below the table. |
| §3.2 | `uds.reassemble()` returns a short buffer on truncation — "reported, deliberately left unfixed" | **FIXED UPSTREAM.** `reassemble()` now compares against the ISO-TP announced length and returns `None` on a short read, with a comment naming this exact hazard. Guarded by `test_diagnostic.py::test_truncated_multiframe_response_is_rejected` and by `test_gateway_energy.py::test_reassemble_rejects_truncation_instead_of_returning_a_short_buffer`. |
| §4 | suggested fallbacks: `rx = 0x77A`, then clear the filter, then `tx = 0x772` | **DO NOT ATTEMPT ON THIS ADAPTER.** `Elm327Transport.set_header` and `set_receive_address` are now deliberate **no-ops**, because the clone was found in the field to misapply an 11-bit `ATSH` as a 29-bit id (every later request then dies with `NO DATA`) and to refuse the plain `ATCRA` that clears a filter — a set filter could deafen the adapter until `ATZ`. The audit's §5 already withdrew these; this entry records that they are not merely unwise but actively unsafe on this hardware. |

| §7 | `python -m pytest tests -q` → 118 passed | **STALE.** The suite is now 439 passed / 0 failed after the merge (427 passed + 12 deselected under `-m "not slow"`). |

### 0.1 §3.1 needs re-reading against the merged transport

The audit's §3.1 says the adapter's `ATCRA` filter is "the only barrier"
stopping a well-formed reply from another ECU being accepted as gateway
data. After the merge that statement is **wrong in the useful direction**.

`Elm327Transport.set_header()` and `set_receive_address()` are now no-ops.
Their docstrings record why, from the field: on the v1.5 clone a 3-digit
`ATSH` is *accepted* but applied as a 29-bit id (so `ATSH7E5` becomes
`0x000007E5`, which nothing on the bus listens to, and every later request
dies with `NO DATA`); the 8-digit form is refused with `?`; and the plain
`ATCRA` that would clear a filter is refused too, so once set it cannot be
unset short of `ATZ`.

So on this adapter there is **no adapter-side barrier at all**, and it is
not one that can be added. Response attribution moved entirely into
software: `uds.payloads_by_source()` keys each reassembled payload by the
sender's CAN header (with `ATH1` every frame carries its sender's id), and
`functional_probe()` returns that map. `read_did()` iterates those
payloads but still does not compare the sender against `ECUS['gate'].rx`.

That leaves §3.1's underlying provenance gap **exactly as open as the
audit found it** — an unrelated ECU's well-formed `62 2A B8 …` is still
accepted as gateway data — with one fewer mitigation. It is also why §5.3's
instruction to read `RAW LINES` and check the answering CAN id is now the
*only* thing standing between a first contact and a confidently wrong
conclusion.

Two further changes the audit should know about:

1. **A competing implementation of the same two DIDs now exists.**
   `decoders/meb.py` registers `hv_energy_content` / `hv_energy_max` for
   the `meb` profile, addressed to `ECU_ENERGY` (`"energy"`, 0x710) rather
   than `"gate"` (0x792), and decoded as **u32be / 1310.77** — a divisor
   its own docstring marks *assumed and unverified* — rather than the
   u16be ×25 / ×50 recovered from the Car Scanner profile. `config.yaml`
   ships `vehicle.did_profile: meb`, so **the `meb.py` version is the one
   that actually runs by default**; `decoders/gateway.py` (this audit's
   subject) is reached only on the `eup` profile. Both registrations are
   retained and neither was silently preferred over the other — see the
   docstring of `decoders.registry.build_default_registry`. Which
   addressing is right (`gate` 0x792→0x710 vs `energy` 0x710→0x77A)
   remains **unresolved**, and this audit's §4 is part of that open
   question rather than a settled answer to it.
2. **`read_did()` now distinguishes a wrong service marker from silence.**
   Before the merge, a reply whose first byte was neither `0x62` nor `0x7F`
   was discarded and reported as `CommunicationError` — indistinguishable
   from `NO DATA`. It now raises `ProtocolError` (a `CommunicationError`
   subclass, so no caller changed behaviour). That is what §5.4 outcome 5
   needs to be actionable.

The runbook in §5 is **still valid and still not authorised**. Re-read
§5.3 before using it: the `Elm327Transport` constructor and
`set_header` / `set_receive_address` calls in that script no longer match
what the collector does, so the script must be adapted to the merged
transport rather than run as written.

---

## 1. The two measurements

Recovered from the Car Scanner MEB vehicle profile (embedded resource
`CarScannerMaui.EmbeddedFiles.profiles7.db`, profile index **342**, 308
`CustomPID` records). Verbatim records:

```json
{"NM":"[19.Gate] HV Battery energy content",
 "CMD":"222AB8","SBI":2,"DL":2,"MUL":25.0,"DIV":1.0,"OFS":0.0,
 "SIG":false,"RBS":false,"TP":1,"UN":61,"HDR":"710",
 "BCM":"ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A",
 "ACM":"ATFCSM0;ATAR;ATSP7"}

{"NM":"[19.Gate] Maximum energy content of the traction battery",
 "CMD":"222AB2","SBI":0,"DL":2,"MUL":50.0,"DIV":1.0,"OFS":0.0,
 "SIG":false,"RBS":false,"TP":1,"UN":61,"HDR":"710",
 "BCM":"ATSP6;ATFCSH710;ATFCSD300000;ATFCSM1;ATCRA77A",
 "ACM":"ATFCSM0;ATAR;ATSP7"}
```

`TP=1` is `CustomPIDType.ByteSet`, `UN=61` is Wh, `SIG=false` unsigned,
`RBS=false` big-endian. The reference decoder is `CustomPID.DecodeByteSet`
(big-endian when `RBS=false`, then `value = raw * MUL / DIV + OFS`).

With `d` = the DID **data record**, i.e. the bytes after the `0x62` marker
and the echoed DID:

```text
22 2A B8  ->  62 2A B8  d0 d1 d2 d3 …
                    └DID echo┘   └ data record ┘

hv_energy_content = ((d[2] << 8) | d[3]) * 25      # Wh
hv_energy_max     = ((d[0] << 8) | d[1]) * 50      # Wh
```

Implementation matches the source exactly: `decoders/gateway.py:96-104`
holds the constants, `:107` `_wh_at` enforces the length, `:124`/`:130`
apply offset and scale.

This is a **third-party static definition**, not a Volkswagen specification.

---

## 2. Code paths reviewed

Traced by reading the source and by executing the paths offline with a fake
transport. No step below involved a vehicle.

| # | Path | Location |
|---|---|---|
| 1 | ECU record, `tx`/`rx` | `diagnostic/ecus.py:41` (`ECUSpec("gate", …, 0x792, 0x710)`) |
| 2 | Specs registered | `decoders/gateway.py:135` `register_gateway()`, called from `decoders/registry.py:78` `build_default_registry()` |
| 3 | Spec → ECU + DID | `decoders/gateway.py:88-104` (constants), `DIDSpec.did` / `.ecu_key` |
| 4 | Poll entry point | `collector.py:125` `poll_did()` → `ECUS[spec.ecu_key]` |
| 5 | Addressing | `diagnostic/connection.py:41` `_set_ecu()` → `elm327.py:185` `set_header()` → `ATSH792`, `elm327.py:188` `set_receive_address()` → `ATCRA710` |
| 6 | Request construction | `diagnostic/connection.py:62` `read_did()` → `_transmit(f"22{did:04X}")` → `elm327.py:133` `send_command()` |
| 7 | Allow-list enforcement | `diagnostic/uds.py:108` `build_read_did_request()` → `:95` `validate_service(0x22)` (only invoked by the simulator/tests; `read_did()` formats the same string inline and cannot reach a non-`0x22` service) |
| 8 | ISO-TP parse | `diagnostic/uds.py:143` `line_to_frame()` → `:164` `reassemble()` → `:186` `parse_elm_lines()` |
| 9 | Marker + echo handling | `diagnostic/uds.py:192` `expect_positive()`, `diagnostic/connection.py:78-80` |
| 10 | Decode | `decoders/gateway.py:107-133`, guarded by `decoders/registry.py:47` `decode_value()` |
| 11 | Storage | `collector.py:107` `_record()` → `repository.record_measurement()`; raw bytes preserved on both success and failure |
| 12 | Audit trail | `diagnostic/connection.py:51-58` `tx_logger` → `tx_log` table |
| 13 | Broad paths (NOT to be used for first contact) | `collector.py:80` `discover_ecus()`, `collector.py:136` `collect_once()`, `main.py:23` `cmd_discover()` |

### 2.1 Answers to the audit questions

| Question | Answer | Verified how |
|---|---|---|
| How is `gate` represented? | `ECUSpec(key="gate", name="Central gateway (J533)", tx=0x792, rx=0x710, doc_status="inferred (not yet validated on this vehicle)")` — a frozen dataclass in a module-level dict, no base class, no probing logic | read `ecus.py:22-66` |
| Transmit / expected response header | `ATSH792` transmitted, `ATCRA710` filter | read `elm327.py:185-194`, `connection.py:41-45` |
| How is the `0x22` request built? | `f"22{did:04X}"` in `connection.py:70`; `DIDSpec.did` is a module constant, never user input | traced + `test_gateway_reads_only_emit_uds_22_requests` |
| ISO-TP / multi-frame handled? | Yes: `line_to_frame` validates the PCI byte, `reassemble` handles single-frame (PCI 0) and first/consecutive (PCI 1/2) with the 12-bit length | read + `test_multi_frame_response_is_reassembled_before_decoding`; **but see the truncation defect in §3.2** |
| Where are header / marker / echo removed? | **header** `uds.py:153` `candidates = (h[3:], h)` — first three hex chars dropped, *never compared to the expected rx*; **marker** verified at `uds.py:198` but **kept** in the returned buffer; **echo** `connection.py:78-80` validates then `return positive[3:]` strips `0x62` + 2 DID bytes | traced + `test_limitation_response_can_header_is_never_verified` |
| Is `SBI=2` on the right byte sequence? | Yes. The decoder receives the DID data record only, identical to the reference decoder (which strips marker + echo before `Decode`; confirmed in the recovered `OBDDataReader` 11-bit path, which skips 6 hex chars = 3 bytes for `22…` requests) | source cross-check, §1 |
| Truncated / malformed / negative / absent? | Truncated record → `ValueError` → `decode_value()` returns `None`; malformed → `ProtocolError`; negative → `NegativeResponseError`; absent → `CommunicationError`; `try_read_did()` absorbs all three | traced + 12 tests |
| Does a failure stay invalid? | Yes. No fallback path converts a bad response into a number. A silent gateway yields `success=0`; a decodable-length-but-undecodable record yields `value=NULL` with the raw bytes stored | `test_collector_records_unsuccessful_gateway_read` |
| Can this feature transmit anything else? | No. The only strings reachable are `ATSH…`, `ATCRA…` and `22xx xx` for the two constant DIDs. `0x10/0x19/0x03/0x09` are reachable only via `discover`/`collect`, which this feature does not call | `test_gateway_reads_only_emit_uds_22_requests` |
| Changes needed to the allow-list / UDS layer? | **No.** `diagnostic/uds.py` is untouched and needs no change for safety | reviewed |

---

## 3. Findings

Three issues were found. **None is a vehicle-safety defect**: no write path
exists, and no guard was weakened. Two are data-integrity/provenance
limitations; one is a latent defect in the shared ISO-TP parser.

### 3.1 The response CAN header is never verified (provenance gap)

`uds.line_to_frame()` strips the leading 11-bit id and never compares it to
`ECUSpec.rx`. Demonstrated: a well-formed `62 2A B8 …` reply on `0x7E8`,
`0x7ED` or `0x7EA` is accepted exactly like one on the expected `0x710`, and
produces the identical DID record. The only barrier is the ELM327's own
`ATCRA` filter.

Compounding it: `connection._transmit()` stores the **reassembled UDS
payload** in `tx_log` RX rows, so the answering CAN id is *not persisted*.
A mis-addressed read therefore cannot be diagnosed from the database after
the fact.

*Consequence for validation:* the first request must capture and inspect the
raw ELM lines (§6). The persistent fix — recording the raw response lines in
`tx_log` — is recommended but was **not** applied here, because the runbook
below does not need it and changing shared read-path code is out of scope for
an audit.

### 3.2 `uds.reassemble()` cannot distinguish truncated from complete (latent defect — reported, not fixed)

When the continuation frames of a multi-frame response never arrive,
`reassemble()` returns a **short buffer** instead of `None`. Demonstrated:

```
frames        : ['100C622AB8112204', '21D8AABBCCDDEE']
reassemble(all) = 622AB8112204D8AABBCCDDEE   -> 31000.0 Wh
reassemble([FF]) = 622AB8112204             -> record 112204 -> decoder refuses
```

**Impact on these two DIDs is bounded.** ISO-TP delivers bytes in order, and
both DIDs read near the *start* of the record (offsets 2 and 0):

* `0x2AB8` needs 4 record bytes; a single first ISO-TP frame carries at most
  3, so truncation can only ever make the value **unavailable**, never wrong.
* `0x2AB2` reads the first 2 record bytes, which truncation cannot corrupt.

A DID read near the *end* of a long record would be silently mis-valued.
That is a genuine defect in `diagnostic/uds.py`. Per the audit's safety
restriction, it is **reported here and deliberately left unfixed** — changing
the safety-critical UDS layer requires a separate, explicit decision.

### 3.3 `main.py discover` is far too broad for a first contact

Established exactly what it transmits (`main.py:23` → `collector.py:80`):

| Step | Transmitted |
|---|---|
| `open_and_identify()` | adapter AT init, then `0902` (VIN, functional) |
| `discover_ecus()` | **`1001` to all 8 registered ECUs** — `bat_mgmt`, `gate`, `chg_mgmt`, `chg`, `mot_elec`, `eld`, `inf`, `brk` — each preceded by its own `ATSH`/`ATCRA` |
| `read_and_store_dtcs()` | `03` (mode 03) plus `190208` to every ECU that answered |

So `discover` issues **8 separate requests to 8 different addresses**. All
are allow-listed and non-modifying, but it is a multi-address operation and
is unsuitable as the first contact with an unverified address pair. The runbook
in §6 replaces it with a single request.

---

## 4. Addressing: what is known, and what is not

Six distinct things must not be conflated:

| # | Concept | What it is | Status here |
|---|---|---|---|
| 1 | **Request / transmit CAN id** | the 11-bit id the request is sent *to*; `ATSH` | `0x792` — from Car Scanner's `BuildList`/`GetECUName`, the only address named "Gateway" in code. **Not observed on this vehicle.** |
| 2 | **Response CAN id** | the id the ECU answers *on* | `0x710` — the `[19.Gate]` records declare it twice (`HDR` field and `ATFCSH710`). **Never seen on a bus capture.** |
| 3 | **ELM response filtering** | which ids the adapter is willing to surface | `ATCRA710` via `set_receive_address()`. Adapter-dependent; may be ignored by some firmware. |
| 4 | **`ATCRA` argument in the Car Scanner profile** | the literal string Car Scanner passes | `ATCRA77A`. **Semantics unknown — not a response CAN id** (see below). |
| 5 | **Diagnostic request payload** | `22 2A B8` | Certain. |
| 6 | **ISO-TP response payload** | what `parse_elm_lines()` returns after reassembly, still beginning `62 2A B8` | Certain. |

### 4.1 The `ATCRA` anomaly

In the recovered profile, `ATCRA = declared response header + 0x6A` for
**every** 11-bit ECU class:

| ECU label | response header | ATCRA | difference |
|---|---|---|---|
| `19.Gate` | `0x710` | `0x77A` | `+0x6A` |
| `08.HVAC` | `0x746` | `0x7B0` | `+0x6A` |
| `C6.Charger` | `0x744` | `0x7AE` | `+0x6A` |
| `03.ABS` | `0x713` | `0x77D` | `+0x6A` |
| `75.GPS` | `0x767` | `0x7D1` | `+0x6A` |

Five independent ECU classes agreeing on a constant offset means `ATCRA`
there is some Car Scanner encoding, **not** a raw response CAN id. (An early
draft of this project assumed `0x77A = 0x792 + 8`; that is arithmetically
false — `0x792 + 8 = 0x79A`. The assumption was dropped.) The offset is
**not** evidence about the response id in either direction: it is a
correlation within a third party's configuration strings, nothing more.

### 4.2 Claims by support level

| Claim | Supported by |
|---|---|
| DIDs, offsets, scales, units, byte order, UDS service | recovered application source (profile records + `DecodeByteSet`) |
| MEB uses UDS over ISO-TP, not VWTP20 | recovered application source |
| Marker + echo are stripped before the offset applies | recovered application source (`OBDDataReader`), matches this implementation |
| `0x710` is the expected response header | third-party profile metadata (twice), **not** observed |
| `0x792` is the gateway request id | a name in third-party code, **not** observed |
| `ATCRA77A` is a response CAN id | **contradicted** — do not use |
| The address pair is correct for this car | **unverified** |

**Deliberately unchanged:** the addresses were not altered to make anything
pass, no scanning/guessing/retry logic was added, and none is proposed.

---

## 5. Validation runbook (revised — requires separate authorisation)

### 5.1 What changed and why

The previous version recommended `python main.py discover` and a sequence of
address substitutions. Both are withdrawn:

* `discover` transmits to **8 addresses** (§3.3) — far broader than needed;
* trying `rx = 0x77A`, then clearing the filter, then `tx = 0x772` is
  **address guessing**, which contradicts the rule "do not probe alternative
  addresses". It also contradicted its own stop rule.

The revised procedure sends **exactly one** vehicle request, to the one
configured address, for one of the two documented DIDs.

### 5.2 Preconditions

* Written authorisation for a live read of DID `0x2AB8` on `0x792`.
* Vehicle awake, ignition on, adapter plugged in, **engine not running**,
  no charging session in progress, doors closed.
* `config.yaml`: `adapter.type: elm327_serial`, `port: COMx`, `baudrate`
  as for the other ECUs. Leave the rest untouched.
* Note the vehicle's displayed SoC **before** starting (needed for §7).
* Do not run `discover`, `collect`, `serve` or any dashboard action in the
  same session.

### 5.3 The single request

Read-only; sends only adapter configuration plus one `0x22`. `ATCRA` and
`ATSH` are set to the configured values, not varied.

```powershell
@'
import sys; sys.path.insert(0, r"C:\Users\kings\OneDrive\Documents\odb_scanner")
from diagnostic import uds
from diagnostic.ecus import get_ecu
from diagnostic.elm327 import Elm327Transport
from diagnostic.interface import CommunicationError

gate = get_ecu("gate")
t = Elm327Transport(port="COMx", baudrate=38400, timeout=5.0)
try:
    t.open()
    print("IDENT:", t.initialize())
    t.set_header(gate.tx)
    t.set_receive_address(gate.rx)
    print("TX 222AB8  (ATSH%03X ATCRA%03X)" % (gate.tx, gate.rx))
    lines = t.send_command("222AB8")
    print("RAW LINES (note the CAN id):", lines)
    payload = uds.parse_elm_lines(lines)
    print("PAYLOAD:", payload.hex().upper() if payload else None)
    if not payload:
        print("OUTCOME 3: no decodable response -> STOP, conclude nothing")
    else:
        try:
            pos = uds.expect_positive(payload, 0x22)
        except uds.NegativeResponseError as exc:
            print("OUTCOME 2:", exc, "-> STOP, record the NRC only")
        except uds.ProtocolError as exc:
            print("OUTCOME 5: malformed ->", exc, "-> STOP")
        else:
            rec = pos[3:]
            print("POSITIVE:", pos.hex().upper())
            print("DID RECORD:", rec.hex().upper(), "len:", len(rec))
            seen = lines[0][:3].upper() if lines else ""
            want = "%03X" % gate.rx
            print("ANSWERING CAN ID:", seen, "(expected " + want + ")")
            if seen != want:
                print("OUTCOME 6: UNEXPECTED header -> STOP, discard the value")
            elif len(rec) >= 4:
                print("hv_energy_content =", ((rec[2] << 8) | rec[3]) * 25, "Wh")
            else:
                print("hv_energy_content = n/a: record shorter than offset 2 + 2; do NOT compute by hand")
except CommunicationError as exc:
    print("OUTCOME 4: transport error ->", exc, "-> STOP, says nothing about the address")
finally:
    t.close()
'@ | .venv\Scripts\python -
```

Record verbatim: the `RAW LINES` output, the `PAYLOAD`, the record length,
the displayed SoC, and whether the car was awake/charging.

Repeat **once** only if the first attempt produced a clean, classified
outcome. Do not vary `ATSH`/`ATCRA`, and do not substitute another address.

### 5.4 Stop conditions — every possible first-request outcome

**Stop after the first classified outcome. Do not re-run with different
settings on the basis of a single observation.**

| Outcome | What you will see | What you **can** conclude | What you **must not** infer |
|---|---|---|---|
| **1. Valid positive response** | `RAW LINES` start with a CAN id and `POSITIVE` starts `62 2A B8` | The gateway (or *some* ECU) answers `0x792` with `0x2AB8` data. The addressing pair works. The DID exists | That the CAN id is `0x710` — check it in `RAW LINES`. That `SBI=2` is right — the value is only meaningful if §7 agrees. That the scale is 25 Wh/LSB |
| **2. Valid negative response (NRC)** | `PAYLOAD` = `7F 22 xx`, e.g. `7F 22 31` | The address reached a UDS responder that knows service `0x22` and refused this DID | **Not** that the DID does not exist. **Not** that the ECU is wrong. `0x31` = out of range for this session/DID; `0x22` = conditions not correct; `0x7F`/`0x7E` = wrong session. Record the NRC and stop |
| **3. No response / `NO DATA`** | `RAW LINES` = `['NO DATA']`, `PAYLOAD` = `None` | Nothing. The request went out and nothing came back within the timeout | **Not** that the DID does not exist. **Not** that the address is wrong. **Not** that the gateway is asleep or absent — `NO DATA` is identical for a wrong address, a silent ECU, an unsupported DID and a bad filter. **Stop** |
| **4. Transport timeout** | `CommunicationError: Timeout waiting for prompt` | The **adapter** did not answer the prompt — a serial/TCP/driver/power problem, upstream of the vehicle | **Not** that the CAN address is wrong. **Not** anything about the DID. Fix the link first, then re-run the identical single request |
| **5. Malformed / incomplete** | `ProtocolError`, `NegativeResponseError` on a short frame, or `DID RECORD` shorter than 4 bytes | Bytes came back but do not match a well-formed `62 2A B8` response for this DID | **Not** that the mapping is confirmed by the numbers. A short record means **no value is stored** — do not compute a Wh figure by hand |
| **6. Response from an unexpected CAN header** | `RAW LINES` show an id that is not the expected `0x710` | The adapter surfaced a frame from some other id — the `ATCRA` filter was not honoured, or another ECU answered | **Not** that this is gateway data. **Discard the value.** Do not change the filter and retry on the spot; report the id first |

### 5.5 After a clean positive result only

If and only if outcome 1 occurs, a second identical request for `0x2AB2`
(one request, `tx = "222AB2"`) may be made in the same session for §7.
Nothing else is authorised by this runbook.

### 5.6 Explicitly out of scope

Changing `tx`/`rx`; scanning or guessing addresses; retry loops;
`main.py discover`; `main.py collect`; DTC reads; session changes beyond
what the ECU does by itself. Each of these needs its own authorisation.

---

## 6. Expected values and what a convincing comparison would be

`hv_energy_content` = `(d[2]<<8 | d[3]) * 25` Wh, `hv_energy_max` =
`(d[0]<<8 | d[1]) * 50` Wh, on the DID data record. This matches the source
definitions exactly and is covered by tests.

### 6.1 Range checks justified by the source

Only the encoding range is justified:

| DID | Encodable range |
|---|---|
| `0x2AB8` | 0 … 65 535 × 25 = 0 … 1 638 375 Wh |
| `0x2AB2` | 0 … 65 535 × 50 = 0 … 3 276 750 Wh |

Any narrower bound — "must be under 62 kWh", "must track SoC" — is
**speculative** until a capture exists, because it assumes a capacity
convention the source does not state. The plausibility arithmetic below is
therefore a *hypothesis to test*, not an acceptance criterion.

### 6.2 Gross vs usable

The reference names `0x2AB2` "Maximum energy content of the traction
battery". The investigation's arithmetic (not evidence) puts a 58 kWh ID.3
at raw ≈ 1160 → 58 000 Wh, and an illustrative raw of 1240 → 62 000 Wh.
If `hv_energy_max` is **gross**, then `hv_energy_content` (current, usable)
and the **displayed SoC** — which manufacturers base on usable capacity —
are on different bases. A ratio of 0.87 against a displayed 87 % could be
perfectly correct. Do not treat a mismatch as a decode error.

### 6.3 What would constitute convincing evidence

A single reading proves nothing. The mapping becomes credible only when:

1. `hv_energy_max` is stable across several minutes and across a power cycle
   (it is a maximum, so it should not track SoC).
2. `hv_energy_content` **rises** while charging and **falls** while driving —
   direction of change is the first real evidence the DID is what it claims.
3. `hv_energy_content` stays below `hv_energy_max` at all times.
4. The same raw bytes are shown **side by side with Car Scanner** under
   matched conditions (same SoC window, same session type), and the two apps
   agree within the expected gross/usable convention difference. **This is
   the decisive test** and it is a comparison, not a plausibility argument.
5. `hv_energy_content` moves monotonically with displayed SoC at a roughly
   constant ratio — evidence of a consistent convention offset, not proof of
   the absolute scale.

Until 4 is done, the honest label stays **unvalidated**.

---

## 7. Tests: commands and actual results

Environment: Python 3.14.3, pytest 9.1.1, ruff 0.17.0. All run offline.

| Command | Result |
|---|---|
| `python -m pytest tests -q` | **118 passed** (80 in `test_gateway_energy.py`, 38 pre-existing) |
| `python -m pytest tests/test_gateway_energy.py -q` | **80 passed** |
| `python -m ruff check tests/test_gateway_energy.py` | **All checks passed** |
| `python -m ruff check .` | 62 errors — unchanged from the pre-existing baseline of 63 (the one difference is `decoders/gateway.py`'s own removed `noqa`); none in files touched here |
| `python main.py guard-test` | **exit 0**, 7/7 write services blocked |
| `python tools\smoke_test.py --cycles 1` | **21 passed, 0 failed, 1 skipped, exit 0** — includes "only allow-listed services were ever transmitted" (241 TX rows audited) |

Coverage of the safety-relevant cases: service/DID construction, ECU
addressing, offset semantics, big-endian decoding, both scale factors,
truncated records, wrong service marker, wrong DID echo, negative responses
with and without an NRC, multi-frame reassembly, no-response lines, a real
transport timeout against a silent loopback peer, allow-list enforcement,
22 blocked services and 8 unsupported services rejected, and the three
characterisation tests for §3.

### 7.1 Files changed by this audit

| File | Change |
|---|---|
| `docs/investigation/meb_gateway_energy.md` | this document: audit results, findings, revised runbook |
| `tests/test_gateway_energy.py` | +3 characterisation tests for §3.1–3.2 (no production code changed) |

No production code was modified. `diagnostic/uds.py` was not touched.
`tests/test_diagnostic.py` and all other pre-existing user changes are
untouched. **Nothing was committed.**

Pre-existing working-tree state at audit start: `tests/test_diagnostic.py`
was already modified (time-relative timestamp fix) and `docs/` plus
`odb_scanner.code-workspace` were untracked. Those are the user's; they were
left alone.

---

## 8. Remaining uncertainties

| # | Uncertainty | Resolved by |
|---|---|---|
| 1 | Is `0x792` the right request id? | one captured `62 2A B8` reply to `ATSH792` |
| 2 | Is `0x710` the right response id / does `ATCRA710` filter correctly? | the CAN id observed in `RAW LINES` |
| 3 | What does `ATCRA` mean in that profile? | not answerable from here; needs ELM327 documentation or a capture |
| 4 | Does `0x2AB8` really return ≥ 4 data bytes (`SBI=2`)? | record length from outcome 1/5 |
| 5 | Are 25 Wh and 50 Wh/LSB right on this pack? | §6.3 comparison with Car Scanner |
| 6 | Gross vs usable convention | §6.3 item 4 |

## 9. Readiness verdict

**READY FOR USER REVIEW.**

The implementation is read-only, matches the recovered source definitions,
fails safe on every malformed input tested, emits nothing but the two
documented `0x22` reads, and leaves the UDS safety layer untouched. The
remaining risk is not in the code but in the **unproven addressing pair**,
which no amount of offline testing can settle. The revised runbook is
therefore deliberately minimal: one request, one configured address, and a
stop rule for every outcome.

Not "empirically validated" — that requires outcome 1 plus the Car Scanner
comparison in §6.3.

## 10. Single safest next step

Read §5 once, then **stop and wait for explicit authorisation**. When
authorised, perform **only** the single `222AB8` request of §5.3, record
the raw lines, and classify the outcome with §5.4 — then stop. Do not vary
the address, do not run `discover`, and do not touch `0x2AB2` until the
first request has been classified.
