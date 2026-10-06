# MEB Diagnostic Reference — consolidated findings

Objective: make `odb_scanner` obtain the same live MEB battery/vehicle data Car
Scanner obtains from this 2021 ID.3 (VIN `WVWZZZE1ZMP087053`, 58 kWh, RHD).

Premise, restated because it governs everything below: Car Scanner reads this
vehicle's BMS through the OBD connector. Our `NO DATA` therefore describes *our
implementation*, not the vehicle.

Status: Phases 1–3 and 7 are complete. Phases 4–6, 9–15 need COM3 free.

---

## 1. Evidence base

### 1.1 Our live run (5 cycles, ignition on, `did_profile: meb`)

```
Opened serial adapter COM3 @ 38400 baud
MEB physical addressing available (ATCP + 6-digit ATSH)
Adapter initialized: ATI ELM327 v1.5
CAN bus alive after warm-up: 18DAF10506410098180001 18DAF10A037F0111 18DAF10106410080080011
Identified vehicle VIN=WVWZZZE1ZMP087053 year=2021
functional UDS responders (session probe): 0x0A
Cell sweep disabled: battery DIDs answer 'no-response'
ECU discovery: bat_mgmt=no-response, dcdc=no-response, energy=no-response,
               climate=no-response, veh_info=no-response, chg_mgmt=chg=mot_elec=
               eld=inf=brk=no-dids-registered
cycle 1..5: {'speed_kmh': 0.0, 'lv_voltage_v': 13.95..13.97}
```

So the bus is awake and 29-bit framing works (`18DAF1xx` warm-up frames prove
protocol 7 is correct), the adapter accepts `ATCP 17` and 6-digit `ATSH`, VIN
reads via mode 09 — and no BMS DID is answered. That is a request-path
problem, not a bus problem.

`tx_log` from the same run, showing exactly what went out:

```
TX bat_mgmt 0322F40D55555555   RX bat_mgmt NO DATA
TX bat_mgmt 0322162055555555   RX bat_mgmt NO DATA
TX bat_mgmt 03221E3B55555555   RX bat_mgmt NO DATA
```

The wire format matches ABRP's documented form byte-for-byte
(`03` length + `22` + DID + `0x55` padding). So the *framing* is not obviously
wrong either — which points at addressing/session rather than encoding.

### 1.2 Car Scanner export (955 rows, 2026-10-05 20:01, ~16.8 min, parked)

Imported via `tools/import_carscanner.py` → 266 rows, 91 keys, provenance
`imported: Car Scanner CSV`, ecu preserved (`8C.BMS` 145, `19.Gate` 119,
`8105.DC/DC Converter` 2).

Three ECU classes, all present and all working for Car Scanner:

| Car Scanner tag | readings | our mapping |
|---|---|---|
| `[8C.BMS]` | 405 | `bat_mgmt` — SoC, pack V/A, 108 cells, 17 temps, counters |
| `[19.Gate]` | 137 | `energy` (0x710) + `dcdc` (0xB9) + `veh_info` (0x76) |
| `[8105.DC/DC Converter]` | 2 | separate DC/DC ECU |
| `[01.ENG] [03.ABS] [08.HVAC] [51.ElDrive] [75.GPS]` | 12 | mot_elec, brk, climate, eld |
| untagged OBD-II | 396 | mostly wrong on an EV (see 1.3) |

### 1.3 Car Scanner's generic PIDs are not trustworthy

Importing these would poison the database:

```
Hybrid/EV Battery System Voltage   1023.984 V   (real pack is 431.25 V)
Hybrid/EV battery pack remaining charge  80.784 %  (BMS says 81.2 %)
Engine coolant temperature         215 °C
Engine RPM / Calculated engine load  0 / 100 %
```

The importer excludes untagged PIDs unless `--allow-generic`, and never maps
them onto a MEB DID.

---

## 2. Three open-source reference implementations

All three reach the same DIDs with the same framing, so the target path is not
in doubt — only our reproduction of it is.

### 2.1 evDash — `src/CarVWID3.cpp` (most complete)

Init queue, in order:

```
AT Z          reset
AT I          identity
AT SP7        ISO 15765-4 CAN 29-bit 500 kbaud
AT BI         bypass init so the protocol stays at 7
AT CAF0       CAN auto-formatting OFF -> PCI bytes are ours to write
AT L0         linefeeds off
AT DP
AT ST16       short timeout
```

Then a flat command queue, **no session change and no tester-present between
DIDs** — `ATSH` + bare `22xxxx`:

```
ATSH17FC00B9 / 22465B / 22465D          DC/DC
ATSH17FC007B / 22028C 22F40D 227448 ... BMS
ATSH17FC0076 / 220364 22295A 22210E 22F802
ATSH767 / 2222B3 222430 222431
ATSH746 / 222613 222609 22263B 2242DB 22F449 220801 220800
ATSH710 / 222AB2 222AB8 222AF7
```

Timing: `rxTimeoutMs = 500`, `delayBetweenCommandsMs = 0` for ID.3 58 kWh.

`AT BI` is the part I suspect we are missing: it tells the adapter to *skip its
own bus-detection sequence*. Without it the clone may auto-negotiate and land on
a different protocol than the `ATSP7` we set.

Note evDash's response parsing uses fixed column offsets into a merged row
(`hexToDecFromResponse(6, 10, 2, false)`) — i.e. it relies on `ATCAF0` plus
`ATH1` so the frame is `header + len + 62 + DID + data`, and indexes data from
column 6. We parse by structure, which is strictly better, but it means our
column assumptions are untested against real frames.

### 2.2 ABRP — `ev-obd-pids/volkswagen/MEB.json`

```json
"init_commands": ["ATZ","ATE0","ATL0","ATSP7","ATBI",
                  "ATSH FC007B","ATCP 17","ATCAF0",
                  "ATCF 17FE7","ATCRA17FE007B"],
"obd_protocol": "7",
"data_commands": ["03221E3D55555555","03221E3B55555555",
                  "0322028C55555555","0322744855555555"]
```

Equations:

```json
current : ((A<<32+B<<16+C<<8+D-150000)/100)*-1
voltage : ((A*256)+B)/4
soc     : 1.12*A/2.5-7.16
```

Three things we do not do at all:

- `ATCF 17FE7` — flow control for the 29-bit bus.
- `ATCRA17FE007B` — receive filter pinned to the BMS response id. We send
  `ATCRA0` (accept-all), which works but is not what the reference does.
- `ATBI`.

Also note ABRP negates current (`*-1`) and uses a different SoC fit
(`1.12*A/2.5-7.16`) than evDash (`byte/2.5` and `raw*0.4425-6.1947`). Our
`decoders/meb.py` follows evDash. This is a real, documented divergence between
sources and is exactly the kind of thing that must be settled against the car,
not on paper. Our own note records that the car's own counters settle the
*sign*: parked, current read positive, charge counter frozen, discharge counter
growing, energy content falling ⇒ **positive = discharge**. ABRP's `*-1` agrees
with that.

### 2.3 spot2000 — `Volkswagen-MEB-EV-CAN-parameters` (the one ecus.py calls authoritative)

193 rows, 13 columns, `Status | group | Popular name | Unit | type | Request
package | ATCP | ATSH | data send | Response package | ATCRA | datareceived |
Calculation | info`. It is the only source that states **`ATCP`, `ATSH` and
`ATCRA` per DID** — exactly what `ecus.py:17` claims when it cites it. It was
read after the first draft of this document, which cited it second-hand.

It independently confirms our wire form, character for character:

```
0x17fc007b 03 22 1e 40 55 55 55 55     <- request
0x17fe007b 05 62 1e 40 XX YY aa aa     <- response
```

`03` = ISO-TP single-frame length, `22` + DID, then `0x55` padding to 7 data
bytes. That is precisely what `_transmit` builds, so framing is now confirmed by
two independent sources (this and ABRP) and is not the fault.

**Addresses, all matching `ecus.py`:**

| module | request id | ATCP | ATSH | ATCRA | response |
|---|---|---|---|---|---|
| BMS | `0x17fc007b` | `17` | `17fc007b` | `17fe007b` | 145 SIDs |
| DC/DC | `0x17fc00b9` | `17` | `17fc00b9` | `17fe00b9` | 2 SIDs |
| Vehicle info | `0x17fc0076` | `17` | `17fc0076` | `17fe0076` | 4 SIDs |
| Energy | `0x00000710` | `00` | `00000710` | (not stated) | 4 SIDs |
| Climate | `0x00000746` | `00` | `00000746` | `000007b0` | 11 SIDs |
| GPS | `0x00000767` | `00` | `00000767` | `000007d1` | 8 SIDs |

GPS is absent from `ecus.py` entirely, and its response id is `0x7D1`, not the
`0x767` evDash addresses. Not needed for the objective; recorded because it
means `ecus.py` is not exhaustive.

**`ATCRA` is stated per module.** Our `ATCRA0` (see 5.1) is not what any
reference sends. The correct value per module is the response id in the table
above. Still to be decided whether to change it — see 5.1.

### 2.4 Every decoder scale is confirmed by a second independent source

spot2000's `Calculation` column agrees with `decoders/meb.py` on all sixteen
DIDs it documents:

| DID | spot2000 | ours | |
|---|---|---|---|
| `028C` | `XX/2,5` | `u8/2.5` | ✓ |
| `1E3B` | `(XX*2^8+YY)/4` | `u16/4` | ✓ |
| `1E3D` | `(WW*2^32+…+ZZ-150000)/100` | `(u32-150000)/100` | ✓ |
| `2A0B` | `XX/2-40` | `u8/2-40` | ✓ |
| `1E32` | `/8583,07123641215`, `signed()` on discharge | same, s32 | ✓ |
| `1E0E` / `1E0F` | `(WW*2^8+XX)/64` | `u16/64` | ✓ |
| `189D` | outlet `[0:2]/64`, inlet `[2:4]/64` | same | ✓ |
| `1E1B` | `(XX*2^8+YY)/5` | `u16/5` | ✓ |
| `F40D` | `XX` | `u8` | ✓ |
| `295A` | `(XX*2^16+YY*2^8+ZZ)` | 3-byte BE | ✓ |
| `1E40…` | `(XX*2^8+YY)/1000+1` | `u16/1000+1` | ✓ |
| `465B` | `(XX*2^8+YY)/16` | `u16/16` | ✓ |
| `465D` | `(XX*2^8+YY)/512` | `u16/512` | ✓ |
| `0364` | `(XX*2^8+YY)/10` | `u16/10` | ✓ |
| `2609` | `XX/2-50` | `u8/2-50` | ✓ |
| `2613` | `(XX*2^8+YY)/5-40` | `u16/5-40` | ✓ |

Phase 11 is therefore answered for these: the ~10 uncertain scale factors are
not uncertain because they were guessed, they are `DocStatus.EXPERIMENTAL`
because they came from reverse engineering rather than a VW document. No change
needed; the notes' provenance claims should cite spot2000 as a second source.

Response shapes worth knowing, because they tell us what arrives to parse:
`1E33`/`1E34`/`1E0E`/`1E1C`/`189D` return 4 data bytes (a `u16` plus a `u16`
cell-or-sensor index — `ZZ is the cell #`), which `read_did()` strips the DID
echo from correctly.

### 2.5 `0x2AB8` cannot be derived from any public source — now confirmed twice

spot2000 lists both energy DIDs with **`[equation missing]`**:

```
[equation missing] HV Battery energy content     0x00000710 03 22 2a b8 55 55 55 55
[equation missing] HV Battery max energy content 0x00000710 03 22 2a b2 55 55 55 55
```

The author has the addressing and the request and lacks the equation. That is
the same position evDash is in (queued, commented out). So
`decoders/meb.py`'s "DIVISOR ASSUMED, UNVERIFIED — no open-source
implementation decodes this DID" is now corroborated by a second independent
source, and `1310.77` must stay labelled a hypothesis until a raw capture pins
it. The Car Scanner export gives the target values (41200–41325 Wh,
max 53200 Wh) but contains no raw bytes, so it cannot supply the divisor.

### 2.6 `0x2AF7` is the whole 12 V block, not just a voltage — the Phase 12 lead

This is the single most actionable new fact. spot2000 attributes **12V Battery
SoC to DID `0x2AF7`** — the same DID we decode for 12 V voltage — and lists
`0x2AF7` with `Response package: multiframe`. It also lists `12V Battery current`
with **no request package at all**, i.e. not yet located.

So `0x2AF7` returns a multi-frame payload holding the 12 V battery block:
voltage (our `u16/1024 + 4.26`, the first two bytes), and inside the rest,
SoC, current, temperature, capacity and aging — which is exactly the
`[19.Gate]` 12 V field set Car Scanner reports and which we currently import
only as `gate_*` keys with no DID.

This makes `[19.Gate]` completion tractable without new DID discovery: read
`0x2AF7` as multi-frame, keep the raw bytes, and locate the sub-fields by
fitting against the seven simultaneous Car Scanner readings already in the
import (SoC 93–97 %, current 0.612–0.681 A, temp 22/25 °C, capacity 49 Ah,
aging 89 %, total charge/discharge 1275/1209 Ah). One raw capture plus eight
known target values is enough to solve the offsets.

It also means our current `aux_12v_voltage` decoder is correct but silently
discards the rest of the frame — `reassemble()` already returns the whole
multi-frame payload, so nothing needs changing to *get* the bytes; only the
sub-field decoders are missing.

### 2.7 Current sign: a genuine 2-against-1 conflict, unresolved

spot2000's `info` column for `0x1E3D` states: *"Negative value is out from
battery (consumption) and positive value is into battery (charging or regen)."*
ABRP agrees, via the `)*-1` on its equation. evDash does not negate, and we
follow evDash.

| source | discharge reads as |
|---|---|
| evDash, and this project | positive |
| ABRP | negative (`*-1`) |
| spot2000 | negative (stated) |

Our `decoders/meb.py` argues the sign from the car's own counters: parked,
Car Scanner showed +0.93…+1.98 A, the charge counter froze, the discharge
counter grew, and energy content fell 41325 → 41200 Wh. Energy left the pack
while current read positive, hence positive = discharge.

That reasoning is sound *about Car Scanner's sign*, which is what matters for
matching Car Scanner — but it does not establish the DID's own convention, and
two of three sources say the opposite. Car Scanner also reports two currents,
`DC Battery Current` (+0.56…+1.98 A) and `DC Battery Current #2`
(−0.95…−1.99 A), opposite in sign and similar in magnitude, which suggests a
bipolar pair rather than one sensor read two ways.

If this is wrong, `pack_current` is inverted, and that inverts charging
detection, pack-power sign, and the regen-vs-discharge history. **Must be settled
with a raw capture during charge and discharge, not on paper.** Flagged rather
than changed.

### 2.8 OVMS — read, but it is the wrong car

`vehicle_vweup` documents the **e-Up**, not the ID.3, and is largely a metrics
and configuration reference ("All communication with the car is read-only", plus
SOH methodology). It contributes nothing to MEB addressing or transport and was
overweighted in the first draft of this document.

### 2.9 Car Scanner's own custom-PID model

From carscanner.info: a PID is a `Command` (`Mode+PID`), an optional `Header`,
and a `Formula` over the *data* bytes. Car Scanner skips header bytes, the ISO-TP
length byte, the response-mode marker (`0x62`) and the DID echo before exposing
bytes as `A, B, C…`. So for `22 1E 3B` the response `62 1E 3B XX XX` exposes
`A=XX, B=XX`.

For `22 <DID>` this means Car Scanner's formula operates on **exactly the bytes
after the DID echo** — which is what `read_did()` returns. Our decode layer is
already aligned with it.

The custom-PID page also documents "start/stop diagnostic commands" fully
qualified ELM commands run before/after a PID (e.g. `ATCRA7E8,ATFCSH7E0`), which
is how Car Scanner switches addressing per PID without a session change.

---

## 3. Module addressing — confirmed

| Module | 29-bit request → response | 11-bit request → response | Protocol |
|---|---|---|---|
| BMS | `0x17FC007B` → `0x17FE007B` | `0x7E5` → `0x7ED` | 7 |
| DC/DC | `0x17FC00B9` → `0x17FE00B9` | — | 7 |
| Vehicle info | `0x17FC0076` → `0x17FE0076` | — | 7 |
| Energy | — | `0x710` → `0x77A` | **6** |
| Climate | — | `0x746` → `0x7B0` | **6** |
| GPS | — | `0x767` → `0x767` | 6 |

Our `ecus.py` matches this. `0x7D0` does **not** appear anywhere in the MEB map —
it is an OBD-II-era guess and should not be the target. Car Scanner's
`[19.Gate]` energy content (41,200–41,325 Wh) is the `0x710` module, i.e.
**protocol 6**, which we do have in the registry but which the run reported
`no-response` as well.

---

## 4. Decoder cross-check against the export

Every real-car figure cited in `decoders/meb.py` notes is reproduced by the
export — so the scales are right; what is missing is the path that returns the
bytes.

| decoder note | export observed | match |
|---|---|---|
| `pack_voltage`: raw 1725 → 431.25 V | 431.25 .. 431.75 | ✓ |
| `soc_abs`: raw 203 → 81.2 % | 81.2 | ✓ |
| `soc_normal`: → 83.63 % | 83.6261 | ✓ |
| `pack_current`: 150198 → 1.98 A | 0.56 .. 1.98 | ✓ |
| `bat_temp_max` → 20.875 °C | 20.75 .. 20.875 | ✓ |
| `bat_temp_min` → 19.625 °C | 19.625 | ✓ |
| `cell_voltage_max`: 16377 → 3.9984 V | 3.99738 .. 3.99836 | ✓ |
| `hv_energy_max` → 53200 Wh | 53200 | ✓ |
| `hv_energy_content` → 41200–41325 Wh | 41200 .. 41325 | ✓ |
| `dyn_charge_limit` → 213 A | 211.2 .. 213 | ✓ |

Honesty note carried in the tool's own output: many notes cite a "CS log" as
their source, so this is **consistency, not independent proof**. What it does
establish is the real-car figure and its date. The export contains no raw bytes,
so it cannot re-derive anything.

`cell_v_*`: `u16/1000 + 1` with `0x0FFE` = unpopulated. Export shows cells
#003–#016, #061–#081, #093–#101 populated at 3.980–4.000 V, and
`HV Battery cell # with lowest voltage = 61` matches the 3.982 V reading at
#061. Consistent.

---

## 5. Concrete differences between our path and the references

Ordered by how likely they are to explain `NO DATA`.

| # | Difference | Reference behaviour | Ours | Confidence |
|---|---|---|---|---|
| 1 | **`AT BI` missing** | `ATBI` before any bus use, to defeat the clone's auto-protocol detection | absent | **high** |
| 2 | **Protocol-6 modules never exercised** | energy/climate/GPS on `ATSP6` | run reported `energy`/`climate` `no-response`; not separately proven | **high** |
| 3 | **`ATCF 17FE7` flow control absent** | ABRP sets it for 29-bit | absent | medium |
| 4 | **Receive filter** | `ATCRA17FE007B` | `ATCRA0` (accept-all) | low — accept-all should be a superset |
| 5 | **Tester Present** | evDash sends `3E` before commanded control ops, *not* between DIDs | never sent | low — evDash's DID loop needs none |
| 6 | **Diagnostic session per module** | none in evDash/ABRP; `uds.py` allows `10 01` | never sent to a module | low |
| 7 | `ATCAF1` vs `ATCAF0` | `ATCAF0`, manual PCI | module reads do use `ATCAF0` via `set_module`; functional reads use `ATCAF1` | medium — worth isolating |
| 8 | Response parse offsets | evDash indexes columns 6+ | we parse ISO-TP structurally | low, but untested on real 29-bit frames |

Difference 1 is the strongest candidate and it is *not* something our code is
wrong about by design — it is simply absent, and it is exactly the sort of
clone-specific workaround the field comment in `elm327.py` already documents for
`ATSH`.

Note on `0x7D0`: not present in any reference. Not a target.

### 5.1 `ATCRA0` is mislabelled, and provably harmless on this adapter

`elm327.py:392` comments `ATCRA0` as a "best-effort accept-all filter". Per the
ELM327 command set that is wrong: `ATCRA` *sets* a CAN receive filter to a given
address, so `ATCRA0` means "accept only CAN id 0x000". ABRP sends
`ATCRA17FE007B` — a real filter, the BMS response id.

It is demonstrably not what is breaking us. `_negotiate_meb()` issues `ATCRA0`
during `initialize()`, and the warm-up that follows returns live frames:

```
CAN bus alive after warm-up: 18DAF10506410098180001 18DAF10A037F0111 ...
```

So this clone ignores `ATCRA0` rather than deafening itself. Recorded because the
comment is actively misleading and a future adapter may honour it — at which
point it would silently kill every response. Replace with plain `ATCRA` (no
argument) or drop it.

spot2000 (2.3) states the value each module wants, and none of them is `0`:

```
ATCP 17  ATSH 17fc007b  ATCRA 17fe007b     BMS
ATCP 17  ATSH 17fc00b9  ATCRA 17fe00b9     DC/DC
ATCP 17  ATSH 17fc0076  ATCRA 17fe0076     vehicle info
ATCP 00  ATSH 00000746  ATCRA 000007b0     climate
ATCP 00  ATSH 00000767  ATCRA 000007d1     GPS
```

i.e. the module's own response id. Setting it would be a behaviour change to
addressing we currently cannot observe, so it is deferred until the experiments
run — but the correct value is now known rather than guessed, and
`set_receive_address()` is already a deliberate no-op that exists for exactly
this reason.

### 5.2 `ATSTFH` — absent here, present in neither reference

Neither evDash nor ABRP sends `ATSTFH` ("stay in header mode after send"), so
it is not an established requirement for MEB. Listed only to record that it was
considered and ruled out, rather than silently skipped. Car Scanner's documented
per-PID start commands (`ATCRA7E8,ATFCSH7E0,ATFCSD300000`) do include flow
control — `ATFCSH`, not `ATSTFH` — which lines up with ABRP's `ATCF 17FE7` and
keeps flow control as a live hypothesis (difference 3).

---

## 6. Adapter: Veepeak OBDCheck BLE+ over Bluetooth SPP

**Car Scanner read this car's BMS through this same adapter** (confirmed by the
owner). That is the single most useful fact in this section, because it collapses
the hardware question:

- The MEB diagnostic bus is reachable through the OBD connector with this
  adapter. It is therefore classical CAN, not CAN-FD: an ELM327 v1.5 clone
  cannot speak CAN-FD, and Car Scanner obtained `62 xx xx` positive responses
  through it. **CAN-FD is ruled out as the cause of our `NO DATA`.**
- **The current adapter can be retained.** No OBDLink MX+, no SocketCAN, no
  Linux, no CAN-FD dongle. Whatever is wrong is in our adapter *state* and
  request path, not in the hardware or in the car's firewall.
- It also means the earlier "BMS not exposed via the OBD surface" reading in
  `collector.discover_ecus()` was wrong about the vehicle and wrong about the
  limit of this adapter. That code concludes from one `22` DID returning no
  answer that the BMS is unreachable; the evidence now says the same adapter,
  configured differently, does reach it.

Adapter facts as observed:

- ELM327 v1.5 clone firmware; COM3 outgoing / COM4 incoming (Windows SPP pair).
- Confirmed by our run: accepts `ATCP 17`, accepts 6-digit `ATSH FC007B`.
- Documented clone behaviour already in `elm327.py`: refuses 8-digit `ATSH`;
  silently mis-applies 3-digit `ATSH` under protocol 7; refuses the plain `ATSH`
  that would clear a header (only `ATZ` recovers).
- CAN-FD: this adapter cannot do it (v1.5 clone, classical CAN controller).
  Experiment 13 is kept as a cheap confirmation rather than a hypothesis.

**What this promotes.** With hardware and framing both ruled out, the gap is
narrower than "find the right command": it is *adapter state at the moment the
BMS is addressed*. Three references configure the adapter before any DID read in
ways we do not — `AT BI` (evDash, ABRP), `ATCF 17FE7` flow control (ABRP), and a
per-module `ATCRA` (ABRP, spot2000, and Car Scanner's documented per-PID
`ATCRA…,ATFCSH…,ATFCSD…`). Flow control is now a stronger candidate than it was,
because it is the one thing Car Scanner demonstrably does that neither evDash nor
our code does.

---

## 7. Experiment matrix (`tools/diagnose_meb_path.py`)

Ready to run; compiles and passes ruff. Every request it sends is read-only
(0x10 0x01, 0x22, 0x19, 0x3E, 0x01/0x03/0x09); `AT*` commands are adapter-local
and never reach the vehicle.

**The matrix has been executed end to end**, against the built-in simulator,
which implements the MEB module map with the real addresses and MEB-scaled
values (`simulator/vehicle.py`, `MEB_MODULES`). `tests/test_meb_path.py` runs it
there and asserts the arms that must answer do:

```
17FE007B0462028CA1        BMS 0x028C  ->  A1 = 161 -> 64.4 % SoC
17FE007B05621E3B058C      BMS 0x1E3B  ->  058C = 1420 -> 355 V
17FE007B07621E3D0002469F  BMS 0x1E3D  ->  0002469F = 150175 -> 1.75 A
77A07622AB204280A64       energy 0x2AB2 (11-bit header, 4 data bytes)
77A05622AF7290A           energy 0x2AF7 -> 290A = 10506 -> 14.52 V
17FE00B90562465B00E2      DC/DC 0x465B -> 00E2 = 226 -> 14.1 A
```

`--tcp HOST PORT` runs the tool against the simulator instead of the car, which
is how the harness was verified. Without that, a matrix that had never executed
would be a guess with print statements, and a wrong header or a mis-padded frame
would have made every experiment report NO DATA on the real car — a conclusion
about the harness mistaken for a conclusion about the vehicle.

Two negative controls are asserted, because they are what make a NO DATA
meaningful:

- an unaddressed module must **not** answer;
- functional addressing must **not** reach the BMS.

Writing that first control found a real simulator defect: an unknown 6-digit
`ATSH` left `meb_module` unset but left `current_ecu` pointing at the 11-bit
motor, so the simulator answered `0x7E8` for a module that does not exist. On a
real bus that gets NO DATA. Fixed by distinguishing module headers (`ATCP 17`)
from the functional header (`ATCP 18`), which is also 6 hex digits.

| Exp | Hypothesis | Key action | Success criterion |
|---|---|---|---|
| 1 | bus alive | `0100` | pid response |
| 2 | functional UDS alive | `1001` | `50 01` from any ECU |
| 3 | functional DID works | `22F190` | `62 F190` + VIN |
| 4 | MEB addressing accepted | `ATCP 17` + `ATSH FC007B` | no `?` |
| 5 | **BMS answers physically** | `ATSH FC007B`, `ATCAF0`, `03 22 xxxx 55…` | `62 028C` from `0x17FE007B` |
| **6** | **`AT BI` is the blocker** | repeat 5 after `ATBI` | answers where 5 was silent |
| 7 | session required | `02 1001 55…` then DID | answers where 5/6 were silent |
| 7b | tester present required | `02 3E00 55…` then DID | answers where 5/6 were silent |
| **8** | **everything at once** | `ATBI` + `10 01` + `3E 00` + DID | answers where all single arms were silent |
| 9 | **energy module, 11-bit** | `ATSP6` + `ATSH 000710` + `ATCAF0` | `62 2AB8` / `2AB2` / `2AF7` from `0x77A` |
| 10 | **DC/DC, 29-bit** | `ATSH FC00B9` | `62 465B` / `465D` from `0x17FE00B9` |
| 11 | gateway routing | functional `22 1E3B`, no module header | any `62 1E3B` |
| 12 | BMS on 11-bit `0x7E5` | `ATSP6` + `ATSH 0007E5` | `62 xxxx` from `0x7ED` |
| 13 | **CAN-FD capability** | `ATFD`, `AT@`, `ATRV`, `ATDP` | adapter reports FD support / `ATFD` accepted |

Experiment 5 sweeps the six highest-value BMS DIDs (`0x028C` SoC, `0x1E3B` pack
voltage, `0x1E3D` pack current, `0x2A0B` temp, `0x1E0E`/`0x1E0F` max/min temp) so
one run answers "does the BMS answer *anything*" rather than "does this one DID".

Experiments 6 and 8 print their own interpretation line, because they are the two
that would end the investigation:

- 6 answering ⇒ `AT BI` was the whole difference.
- 8 answering ⇒ the path needs session and/or keep-alive.
- 7b is deliberately *not* treated as independently meaningful: evDash sends `3E`
  only before its commanded control operations, never between plain `22xxxx`
  reads, so a DID that needs a keep-alive would be a genuine surprise worth
  recording rather than assuming.

Experiment numbering in the script matches this table exactly (EXP-7b for the
tester-present arm), so a result can be cited by number without ambiguity.

## 8. Reading the results

| Outcome | Conclusion | Next |
|---|---|---|
| 5 or 6 answers | it was `ATBI`/timing | implement in `_base_init`, re-run collector |
| 7 or 8 answers | session / tester-present required | add `ensure_session()` + keepalive per module |
| 9 and 10 answer, 5 does not | BMS specifically unreachable; gateway modules are not | implement energy/dcdc first, report the BMS honestly |
| 9 answers only | protocol 6 path is fine, 29-bit module path is broken | focus 29-bit: `ATCF`, `ATCRA`, id format |
| 13 reports FD and 5 still fails | CAN-FD bus is required | specify FD hardware precisely |
| nothing answers | request frame never reaches the BMS | capture raw bytes via `elm_console.py`, compare wire form |

---

## 9. First target measurement (Phase 9)

`pack_voltage` (DID `0x1E3B`, `u16/4`) — chosen over SoC because its scale
(`/4`) is unambiguous across all three references, whereas SoC has two competing
fits (evDash `/2.5` and `/0.4425-6.1947`; ABRP `1.12*A/2.5-7.16`) and is the
one quantity where the sources genuinely disagree.

Acceptance: `odb_scanner` `pack_voltage` within one LSB (0.25 V) of the
Car Scanner 431.25–431.75 V band, across ≥3 readings. Then expand.

---

## 10. Safety

- `0x27`, `0x2E`, `0x31`, `0x14`, `0x30`, `0x37`, flashing: never sent.
- `diagnostic/uds.py:202` `validate_request()` remains the single choke point;
  `tools/diagnose_meb_path.py` sends only allow-listed services.
- `tools/elm_console.py` is the one documented exception (not read-only) and is
  not part of any planned path.
- `guard-test` must keep passing; baseline is 210 tests, ruff clean, smoke 24/0/1.

---

## 11. Immediate blocker

**The adapter is not paired to Windows.** It was removed from Bluetooth to
release COM3, which also removed the virtual serial port — `pyserial` now reports
*no serial ports at all*, there are no `HARDWARE\DEVICEMAP\SERIALCOMM` entries,
and no Veepeak device appears in PnP.

To resume:

1. Plug the Veepeak into the OBD socket (it needs the car's 12 V for its radio).
   Ignition on.
2. Pairing mode — hold the dongle's button ~5 s until the LED flashes rapidly.
3. Windows → Settings → Bluetooth & devices → Add device.
4. Verify the port: `python -c "import serial.tools.list_ports as l; [print(p.device, p.description) for p in l.comports()]"`.
   **Do not assume COM3.** Windows assigns the lowest free port, so it may be a
   different number; `config.yaml` says `COM3` and will need updating if so.
5. Then, from `C:\odb_scanner`:

```
python tools\diagnose_meb_path.py            # the matrix
python tools\doctor.py                       # independent sanity check
python main.py collect --cycles 5            # real collection
```

The matrix is verified end to end (7), so whatever it prints is about the car,
not about the harness. Read the first arm that answers, or the first one whose
baseline was silent.

---

## 12. Open questions this document does not answer

**Answered since the first draft:**

- **Which adapter did Car Scanner use? This same Veepeak.** So the vehicle is
  reachable through the OBD connector with this adapter, CAN-FD is ruled out,
  and the current hardware can be retained (6).
- Which DIDs sit behind Car Scanner's `[19.Gate]` 12 V block? **`0x2AF7`**,
  multi-frame, holding the whole block (2.6). Not new DID discovery at all.
- Is the `0x2AB8` divisor derivable from public sources? **No** — evDash and
  spot2000 independently have the addressing and no equation (2.5).
- Are the ~10 "unverified" decoder scales sound? **Yes**, all sixteen that
  spot2000 documents agree with `decoders/meb.py` (2.4).
- Is our ISO-TP framing right? **Yes** — byte-identical to ABRP and spot2000
  (1.1, 2.3).
- Which SoC fit? evDash `byte/2.5` and display `raw*0.4425-6.1947`, now backed
  by spot2000's `XX/2,5` (2-against-1 over ABRP's `1.12*A/2.5-7.16`), and the
  Car Scanner pair 81.2 / 83.63 % reproduces evDash exactly from `raw = 203`.
- Is CAN-FD required? **No.** Same adapter, Car Scanner read the BMS (6).

**Still open:**

1. Which of the three adapter-state differences is the blocker — `AT BI`, the
   `ATCF 17FE7` flow-control setting, or the per-module `ATCRA`? All three are
   things Car Scanner and/or two references do and we do not, and none of them
   is a change of request bytes. Experiments 6 and 8 discriminate; if neither
   answers, flow control and `ATCRA` need their own arms, which the matrix does
   not yet have.
2. **Current sign, positive or negative on discharge** (2.7). evDash and this
   project say positive; ABRP and spot2000 say negative. Settle with a raw
   capture during charge and discharge. Until then `pack_current`'s sign is
   carried from evDash on the strength of Car Scanner's own counters, and is the
   project's single most consequential unverified assumption.
3. Why does the car report two opposite-signed currents (`DC Battery Current`
   and `DC Battery Current #2`)? A bipolar pair, or one sensor read two ways?
   Affects whether we need a second current DID.
4. What are the sub-field offsets inside `0x2AF7` (2.6)? Solvable from one raw
   capture plus the eight simultaneous Car Scanner readings already imported.
