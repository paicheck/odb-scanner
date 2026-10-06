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

### 2.3 OVMS — `vehicle_vweup`

e-Up rather than ID.3, but the same DID family (`0x1E3B`, `0x1E3D`, `0x028C`,
`0x2A0B`, `0x1E32`) with its own scales, and it states plainly: "All
communication with the car is read-only."

### 2.4 Car Scanner's own custom-PID model

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

### 5.2 `ATSTFH` — absent here, present in neither reference

Neither evDash nor ABRP sends `ATSTFH` ("stay in header mode after send"), so
it is not an established requirement for MEB. Listed only to record that it was
considered and ruled out, rather than silently skipped. Car Scanner's documented
per-PID start commands (`ATCRA7E8,ATFCSH7E0,ATFCSD300000`) do include flow
control — `ATFCSH`, not `ATSTFH` — which lines up with ABRP's `ATCF 17FE7` and
keeps flow control as a live hypothesis (difference 3).

---

## 6. Adapter: Veepeak OBDCheck BLE+ over Bluetooth SPP

- ELM327 v1.5 clone firmware; COM3 outgoing / COM4 incoming (Windows SPP pair).
- Confirmed by our run: accepts `ATCP 17`, accepts 6-digit `ATSH FC007B`.
- Documented clone behaviour already in `elm327.py`: refuses 8-digit `ATSH`;
  silently mis-applies 3-digit `ATSH` under protocol 7; refuses the plain `ATSH`
  that would clear a header (only `ATZ` recovers).
- CAN-FD: **unverified**. No `ATFD` capability test has run — `tools/diagnose_meb_path.py`
  includes one (EXP-12). This is the single most important unknown, and it is
  cheap to answer.
- COM3 is currently held by the phone's Bluetooth pairing; the adapter cannot be
  opened until the device is removed from Windows Bluetooth, not merely
  disconnected in the app.

CAN-FD expectations if it turns out to matter: arbitration 500 kbit/s,
data 2 Mbit/s typical for VW, `ATSH 8000` style extended flags, ISO-TP over
64-byte frames. Nothing in the three references requires CAN-FD — evDash and
ABRP both use classical CAN protocol 7 — so a working classical CAN path is the
target and CAN-FD is a contingency.

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

**COM3 is held.** `OSError(22) … WinError 121 (semaphore timeout)` after ~5.2 s.
Windows Bluetooth SPP keeps the port bound even after disconnecting in the
phone app; the Veepeak device must be *removed* from Windows Settings → Bluetooth
& devices, then re-paired. Until then `doctor.py` cannot get past stage 3 and no
experiment can run.

---

## 12. Open questions this document does not answer

1. Does the Veepeak clone support CAN-FD at all, and does the MEB diagnostic bus
   need it? (exp 13)
2. Is `ATBI` the actual blocker? (exp 6)
3. Does this MY2021 58 kWh ID.3 expose the BMS on the OBD connector at all, or
   only via gateway routing? Both evDash and Car Scanner read it through the OBD
   port, so the answer is yes — but the *path* differs.
4. Which SoC fit is correct for this car — evDash's or ABRP's? Only the car can
   settle it; the export's 81.2 % / 83.63 % pair matches evDash's
   `raw/2.5 = 81.2` and `raw*0.4425-6.1947 = 83.63` with `raw = 203`, which
   favours evDash, but ABRP's fit was never cross-checked against this car.
5. What are the DIDs behind Car Scanner's `[19.Gate]` 12 V SoC / current /
   reserve / consumption fields? Unknown; imported as `gate_*` keys only.
