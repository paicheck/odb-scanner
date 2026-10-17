# MEB no-response — handoff for planning

**Vehicle:** 2021 VW ID.3 58 kWh, VIN `WVWZZZE1ZMP087053`.
**Goal:** make `odb_scanner` live-read the same BMS/DC-DC/energy/climate data that Car
Scanner reads on the same car through the same adapter.

The deeper source of truth (raw evidence, source logs, provenance) is
`docs/MEB_DIAGNOSTIC_REFERENCE.md` — read §1, §2, §3, §5, §7, §8, §12 next.

---

## 1. What we need from you

**Status: both tracks are solved.** The BMS path (Track A) answered on
2026-10-12 — see §1.1. The 11-bit modules (Track B) answered on 2026-10-16 —
see §1.2. Both fixes are implemented in `diagnostic/elm327.py`.

The symptom is **not** one problem. The wire log splits it cleanly into two
independent tracks (§4):

- **Track A — BMS/DC-DC/vehicle-info (29-bit).** **Solved.** The clone's
  default CAN receive filter silently drops `17FExxxx` responses while
  accepting `18DAF1xx` functional ones. `ATCRA <response-id>` per module
  fixes it. Implemented in `set_module()`.
- **Track B — energy/climate/GPS (11-bit).** **Solved.** The clone refuses an
  8-byte hand-framed `ATCAF0` line under `ATSP6` with `?`. Car Scanner instead
  reads these modules with `ATCAF1` (auto-formatting) and a *bare* request
  (`22 2AB8`) plus the ISO-TP flow-control preamble (`ATFCSH`/`ATFCSD`/
  `ATFCSM 1`). See §1.2.

Deliver: an ordered experiment plan for Track B, and a decision tree mapping
each outcome → the minimal code change → how to re-verify.
No code changes before an experiment discriminates; nothing added to the
collector's init blindly.

Acceptance target (reference doc §9): `pack_voltage` (DID `0x1E3B`, `u16/4`) within
one LSB (0.25 V) of the Car Scanner band 431.25–431.75 V, across ≥3 readings.

---

## 1.1 The BMS answer (2026-10-12, first ever)

The matrix run against the car produced the first BMS answer this project
has had:

```
=== EXP-15: BMS DID 0x028C (SoC) with ATCRA 17FE007B ===
TX: ATCRA 17FE007B  (receive filter = BMS response id)
  RX: OK
TX: 0322028C55555555  (BMS DID 0x028C after ATCRA)
  RX: 17FE007B0462028CB3AAAAAA
  ANSWERED: 62028CB3
```

`62028CB3` — positive response to DID `0x028C` (SoC), data byte `0xB3` = 179
→ **71.6 %** (evDash `byte/2.5` fit). The car was parked and collecting, so
SoC has dropped from the morning's 81.2 % — consistent.

**Root cause.** The clone's default CAN receive filter accepts functional OBD
responses (`18DAF1xx`) but silently drops MEB module responses (`17FExxxx`).
Every MEB read was transmitted correctly and the answer was discarded by the
adapter. `ATCRA0` (sent during init) was ignored, which is why warm-up frames
arrived — but `ATCRA <id>` is fully implemented. Car Scanner does the same
per-PID start commands (`ATCRA7E8,ATFCSH7E0,...`), which is the one thing it
does that this transport did not.

**Fix.** `set_module()` now takes the module's `rx29` and programs
`ATCRA <rx29>` after the header. Restoring functional addressing sends plain
`ATCRA` to clear the filter, or every later mode-01 read would time out.

**What the same matrix ruled out** (all on the car, same run):

| Arm | Result | Conclusion |
|---|---|---|
| EXP-6 `ATBI` | NO DATA | not the blocker |
| EXP-7 session `10 01` | `?` on the session frame | clone refuses it; not the blocker |
| EXP-7b tester-present | `?` | not the blocker |
| EXP-8 everything | NO DATA (pre-ATCRA) | combination is not it |
| EXP-14 `ATCF 17FE7` | `?` on ATCF | clone refuses it; not the blocker |
| **EXP-15 `ATCRA`** | **answered** | **the filter was the whole BMS problem** |
| EXP-16 full config | answered | confirms; no other variable needed |
| EXP-17 3-digit `ATSH` | `?` | **header width refuted** for 11-bit modules |
| EXP-9 energy 6-digit | `?` | energy/climate still refused — separate issue |
| EXP-12 BMS on 11-bit `0x7E5` | `?` | BMS is 29-bit only, as mapped |
| EXP-11 functional `0x22` | NO DATA | gateway does not route, as expected |

The simulator answers every DID the car refuses, which is what made this
findable: the harness is sound, and the `?`/`NO DATA` split was always about
the adapter, not the wire form.

---

## 1.2 Track B resolution (2026-10-16)

The 11-bit modules now answer. Root cause and fix:

**Root cause.** Under `ATSP6` (11-bit) the clone refuses an 8-byte hand-framed
`ATCAF0` single frame (`03 22 2A B8 55…`) with `?` — it accepts every AT
configuration command, then refuses the *data frame*. This is not a header-width
problem (EXP-17's true 3-digit `ATSH 710` also returned `?`). The decisive
evidence is the 2026-10-15 passive HCI capture of Car Scanner, which reads the
gateway with `ATCAF1` and a **bare** request:

```
ATSH710 → ATSP6 → ATFCSH710 → ATFCSD300000 → ATFCSM1 → ATCRA77A → 22 2AB8
```

The adapter adds the ISO-TP PCI byte, header and flow control itself; the
transport must *not* hand-frame.

**Fix.** `set_module_car_scanner_11bit(tx29, rx29)` sends that sequence (plus
`ATCAF1`) for every 11-bit MEB module (`energy` 0x710→0x77A, `climate`
0x746→0x7B0, `gps` 0x767→0x7D1). `_transmit` sends the request **bare** when the
transport reports auto-formatting (`Elm327Transport.auto_formatting`), instead of
hand-framing.

**Verified on the car (2026-10-16):** `energy` and `climate` discovery flipped
from `refused` to `responder`, and `0x2AB2` answered `62 2A B2 04 28 04 00`
(53,200 Wh). `gps` answered NRC `0x78` (response-pending), i.e. reachable.

**Knock-on quirk — superseded (17 Oct, see §1.3).** This first blamed the
leftover `ATFCSM 1` for the 29-bit `?` refusals, and "fixed" it by sending
`ATFCSM 0` on the way back to 29-bit. That was the wrong cause: the 29-bit
refusals were the hand-framed `ATCAF0` *data line*, which Car Scanner never
sends. The `ATFCSM 0` reset has been removed; every module now uses Car
Scanner's `ATFCSM 1` + bare-request form.

---

## 1.3 The 29-bit modules — same form, not a separate one (2026-10-17)

The 11-bit fix generalises: **every** MEB module is read with Car Scanner's
`ATCAF1` + bare-request form. The 29-bit modules (BMS, DC/DC, charger, drive,
vehicle-info) had been sent as a hand-built `ATCAF0` 8-byte frame
(`03 22 02 8C 55 55 55 55`), which this clone refuses with `?` — that refusal
*is* the "29-bit modules return nothing" symptom (bat_mgmt 0/822 on 17 Oct,
`eldrive`/`veh_info`/`dcdc`/`chg` likewise).

**Evidence.** The same 2026-10-15 passive HCI capture that resolved Track B also
contains Car Scanner's 29-bit phase, configured identically to the 11-bit one:

```
ATSHFC007B → ATSP7 → ATCP17 → ATCRA17FE007B →
ATFCSH17FC007B → ATFCSD300000 → ATFCSM1      →   bare '221E3B'
```

answered `17FE007B05621E3B0690` (pack voltage `06 90`). The app never sends
`ATCAF0` for a working read, and it *keeps* `ATFCSM 1`. Two details contradicted
the old path: the flow-control header is the **full 8-digit request id**
(`ATFCSH17FC007B` — accepted, even though an 8-digit `ATSH` is refused), and
auto-formatting stays **on**, so the request goes out bare and the adapter adds
the ISO-TP PCI byte, header and flow control.

**Fix.** `set_module()`'s 29-bit branch now sends
`ATCP` / `ATSH` / `ATCRA` / `ATFCSH <tx29>` / `ATFCSD 300000` / `ATFCSM 1` /
`ATCAF1` and leaves the request bare; `_transmit()` no longer hand-frames
anything. The hand-framed form and the `ATFCSM 0` reset are both gone.

**Not yet re-verified on the car** — the adapter was out of range when the fix
landed. Verification is one collector cycle: `bat_mgmt`, `eldrive`, `veh_info`,
`dcdc` and `chg` should return data instead of `?`.

---

## 2. Facts that are settled (do not re-investigate)

- **Hardware is not the problem.** Car Scanner read this exact car's BMS through
  this exact Veepeak (ELM327 v1.5 clone, classical CAN, Bluetooth SPP). CAN-FD is
  therefore ruled out (a v1.5 clone cannot do FD) and the car does expose the MEB
  bus through the OBD connector.
- **The bus is alive and 29-bit framing works.** Warm-up returns live frames
  (`18DAF10506410098180001 18DAF10A037F0111 ...`), proving protocol 7 is right.
- **The wire form is byte-identical to the references:** `03 22 <DID> 55 55 55 55`
  matches ABRP and spot2000 exactly.
- **Addressing values are right** and match evDash/ABRP/spot2000: `ATCP 17` +
  6-digit `ATSH FC007B` (BMS), `FC00B9` (DC/DC), `FC0076` (veh-info); `ATCP 00` +
  `ATSH 000710` (energy), `000746` (climate).
- **Functional OBD through the gateway works**: mode-01 PIDs 0x00/0x0D/0x42
  (12 V ≈ 14.2 V, speed 0), mode-09 VIN, and functional `0x10 0x01`.

---

## 3. What works (live evidence, 12 Oct run, 20 cycles)

Adapter freshly re-paired on COM3; the 12 V rail reading 14.2 V means the DC-DC was
running, i.e. the car was awake.

```
Opened serial adapter COM3 @ 38400 baud
MEB physical addressing available (ATCP + 6-digit ATSH)
Adapter initialized: ATI ELM327 v1.5
CAN bus alive after warm-up: 18DAF10A037F0111 18DAF10506410098180001 18DAF10106410080080011
Identified vehicle VIN=WVWZZZE1ZMP087053 year=2021
functional UDS responders (session probe): 0x0A    <- source address only; identity NOT established (§4)
Cell sweep disabled: battery DIDs answer 'no-response'
ECU discovery: bat_mgmt=no-response, dcdc=no-response, energy=no-response,
               climate=no-response, veh_info=no-response      <- this line is misleading, see §4
cycle 1..20: {'speed_kmh': 0.0, 'lv_voltage_v': 14.2}
```

Functional `0x10 0x01` replies `50 01 00 32 01 F4` (P2 = 50 ms, P2* = 500 ms) — the
same response shape ELMduino issue #207 recorded on an Enyaq for `0x10 0x03`
(`50 03 00 32 01 F4`); same length, different sub-function byte.

Session totals: 357 measurement rows, 804 `tx_log` rows. Only `lv_voltage_obd` and
`vehicle_speed` carry values; every other key was polled and stored null.

---

## 4. What fails — three distinct failure modes, not one

This is the core of the brief. **The collector reports all five MEB modules as
`no-response`, and that report is wrong**: its status mapping collapses "the adapter
refused the command" into the same bucket as "the bus stayed silent". The `tx_log`
RX payloads separate them.

### Per-module reality (12 Oct run; the 6 Oct run is identical in pattern)

| Module | Addressing | RX payload | Reads | What it means |
|---|---|---|---|---|
| `bat_mgmt` | 29-bit `17FC007B`, ATSP7 | `NO DATA` ×472 | 473 | **Request reached the bus; the BMS did not answer.** Track A. |
| `energy` | 11-bit `0x710`, **ATSP6** | `?` ×22 | 22 | **Adapter refused — never transmitted.** Track B. |
| `climate` | 11-bit `0x746`, **ATSP6** | `?` ×15 | 15 | Adapter refused. Track B. |
| `veh_info` | 29-bit `17FC0076` | `?` ×22, `NO DATA` ×20 | 42 | Mixed — intermittent. Track B. |
| `dcdc` | 29-bit `17FC00B9` | `?` ×14, `NO DATA` ×1 | 15 | Mixed, mostly refused. Track B. |

`NO DATA` = the ELM327 transmitted and timed out waiting for the car.
`?` = the ELM327 rejected the command line itself; nothing went on the wire.

**Consequence: the 11-bit protocol-6 path has never actually been exercised on this
car.** Every energy and climate request was refused locally. The reference doc's
gap #2 ("protocol-6 modules never exercised") therefore still stands in full — do
not treat those modules as having been tested and found silent.

### The state-contamination lead (Track B)

Ordering in cycle 1's discovery probes:

```
14:17:02  bat_mgmt  NO DATA   <- reached the bus
14:17:04  dcdc      NO DATA   <- reached the bus (before any ATSP6)
14:17:06  energy    ?         <- first protocol-6 switch; refused
14:17:08  climate   ?
14:17:10  veh_info  ?         <- 29-bit module, now also refused
```

`dcdc` reaches the bus *before* the first `ATSP6` and is refused *after* it;
`veh_info` shows both outcomes across the run, so the state recovers intermittently.
**Hypothesis: the protocol switch in `set_module()` leaves the adapter in a state
where it rejects the subsequent data frame.** Note `set_module()` logged no
"Adapter refused module header" warning, so it believed `ATSP`/`ATCP`/`ATSH`
succeeded — the refusal appears only on the data line. Mechanism is open; the
preceding TX frames are well-formed (`03222AB255555555`, `0322260955555555`,
`0322295A55555555`).

### Functional vs physical `0x22` (Track A)

| When | Result | Evidence |
|---|---|---|
| **Functional** `0x22`, no module header (5 Oct run) | **`7F 22 31`** — a real negative response | `tx_log` `RX 7F2231`, repeated per DID |
| **Physical** `0x22` to `bat_mgmt` (6 + 12 Oct) | `NO DATA` — silence | `tx_log`, 472 unanswered reads today |

`NRC 0x31` is **requestOutOfRange**. So something on the functional surface hears
`0x22` and rejects it, which is consistent with a gateway that will not serve these
DIDs functionally — physical routing is required. Our physical reads to the BMS do
go out and get nothing back.

**Responder identity is not established.** `0x0A` is only the source address parsed
out of `18DAF1`**`0A`** by `uds.source_label()`. Car Scanner labels the gateway
`19.Gate`, so `0x0A` is probably *not* the gateway. The `ecu` column on those
Oct-5 NRC rows is our own probe label, not the responder's identity. Worth
resolving early — it changes how the NRC is interpreted.

---

## 5. Exactly what our code sends

`Elm327Transport` (`diagnostic/elm327.py`), `set_module()` per module:

```
29-bit modules (BMS/DC-DC/veh-info):   ATSP7, ATCP 17, ATSH FC007B (6 digits), ATCAF0
11-bit modules (energy/climate):       ATSP6, ATCP 00, ATSH 000710/000746,    ATCAF0
functional restore:                    ATCP 18, ATSH DB33F1, ATCAF1
then the UDS read:                     03 22 <DID> 55 55 55 55
```

Init also sends `ATCRA0`. The code comments call that an "accept-all filter", which
is **wrong** — per the ELM327 datasheet `ATCRA0` filters to CAN id `0x000`, and
plain `ATCRA` is the accept-all form. It happens to be harmless here because this
clone ignores it (warm-up frames still arrive), but the comment is misleading and a
stricter adapter would silently go deaf.

---

## 6. Track A candidates — ranked gap vs. the references

All three references (evDash, ABRP, spot2000) configure the adapter before any DID
read in ways we do not. Ranking from reference doc §5:

| # | What we omit | References do | Confidence |
|---|---|---|---|
| 1 | `ATBI` (bypass initiation) | evDash + ABRP send it before any bus use | **HIGH** — clone-specific workaround; the datasheet pairs it with non-OBD CAN framing |
| 2 | Flow control `ATCF 17FE7` (ABRP) or `ATFCSH`+`ATFCSD`+`ATFCSM` (datasheet recipe) | Car Scanner sends `ATCRA7E8,ATFCSH7E0,ATFCSD300000` per PID | **MEDIUM→strong** — the one thing Car Scanner demonstrably does that neither we nor evDash do |
| 3 | Per-module `ATCRA <response id>` e.g. `ATCRA 17FE007B` | ABRP, spot2000, Car Scanner pin it | **LOW** as a cause (see §5), but should be corrected anyway |
| 4 | `ATCAF` split (module `ATCAF0`, functional `ATCAF1`) | references stay `ATCAF0` | **MEDIUM** — worth isolating |
| 5 | Tester present `3E 00` between DIDs | evDash sends it only before *commanded* ops | **LOW** |
| 6 | Per-module session (`0x10 0x01` first) | none in evDash/ABRP | **LOW** |
| 7 | Response parsing | evDash indexes fixed columns; we parse ISO-TP structurally | **LOW**, untested on real 29-bit frames |

**Policy-locked hypothesis:** `0x10 0x03` (extended session). The MEB gateway does
answer it (ELMduino #207) and a DID refusing `0x22` under `0x10 0x01` could
conceivably serve it under `0x10 0x03`. We are forbidden from sending it (§7).
evDash and ABRP reach these DIDs with **no session change at all**, so this is
live-but-improbable, and it is a decision for the owner — never a side-effect of an
experiment.

**Do not design around the collector's verdict.** Its "BMS not exposed via the OBD
surface" message is known-wrong (same adapter, Car Scanner reads it), and its
discovery status mapping hides the `?`/`NO DATA` distinction (§4). Separately,
`collector.discover_ecus()` disables the cell sweep after a single unanswered DID,
which keeps suppressing cell collection even if reads start working mid-run.

---

## 6. Track B — the `?` refusals (11-bit modules)

> **RESOLVED 2026-10-16.** The cause is the `ATCAF0` hand-framed 8-byte line,
> which the clone refuses under `ATSP6` — and, as found on 17 Oct, under `ATSP7`
> too. Car Scanner uses `ATCAF1` + a bare request + the flow-control preamble for
> every module. Fix and evidence in §1.2 and §1.3. The analysis below is retained
> as the record that led there.

**The 2026-10-13 car run refuted the receive-filter theory for Track B.** With
the Veepeak awake and ignition on, EXP-18 (energy + `ATCRA 0000077A`) and EXP-19
(climate + `ATCRA 000007B0`) both still returned `?`. So pinning the filter —
the fix that solved the BMS — is **not** what the 11-bit modules need.

What that run did establish:

- **`?` means the adapter refused the request, not that the module was silent.**
  A `?` arrives instantly (a parse/parameter error); silence is `NO DATA`.
  Every `ATSP6` (11-bit) data frame returned `?`; every `ATSP7` (29-bit) frame
  transmitted (then `NO DATA` or an answer). The car never saw the 11-bit
  request.
- **The clone did accept all the AT configuration** (`ATSP6`, `ATCP 00`,
  `ATSH 000710`, `ATCAF0`, `ATCRA …` each returned `OK`) and then refused the
  *data frame* `03222AB855555555`.

Two candidates remain, and the matrix now has one arm for each:

| Exp | Isolates | vs |
|---|---|---|
| **EXP-17** (new form) | `ATSH 710` — the 3-digit header the protocol specifies | EXP-18 (6-digit `ATSH 000710`), same everything else |
| **EXP-20** | a 7-byte frame (`03 22 2A B8 55 x7`) | EXP-18 (8-byte frame) |

**Note on EXP-17.** The first version of the arm sent `ATSH 0710` — *four* hex
digits, not three. The clone refused the ATSH itself with `?`, so the true
3-digit form (`710`) was never exercised. Its hypothesis was therefore never
tested, and the record now sends `710`.

**Also fixed:** EXP-21 re-runs the DC/DC module (29-bit, like the BMS) **with**
`ATCRA 17FE00B9`. EXP-10 read it with no filter and was refused `?`, even though
the identical framing for the BMS (EXP-5) transmitted fine — so the DC/DC
module, like the BMS, may have needed only its receive filter. That is a likely
free win and is now its own arm.

**Order at the car:** run the matrix as-is. EXP-17 answering → the 6-digit
header is the Track B defect. EXP-20 answering → the 11-bit send limit is 7
bytes. Both `?` → the remainder is the CAF mode or the `ATSP6`→`ATSP7`
transition (see §9).

---

## 7. Hard constraints (non-negotiable)

1. **Strictly read-only.** Only `0x10 0x01`, `0x22`, `0x19`, `0x3E`,
   `0x01/0x03/0x09`, plus adapter-local `AT*`. **Never** `0x27`, `0x2E`, `0x31`,
   `0x14`, `0x30`, writes, coding, adaptations, actuator tests, ECU reset, flashing.
2. **Never weaken `diagnostic/uds.py` `validate_request()`** — the single choke
   point. Suite must stay green (355 tests, ruff clean, `smoke_test.py` 24/0/1,
   `guard-test`); new tests must fail on pre-fix code.
3. **No blind CAN-ID scans, no arbitrary UDS.** Experiments go through
   `tools/diagnose_meb_path.py`, whose arms are allow-listed and verified end-to-end
   against the simulator (`tests/test_meb_path.py`).
4. **No `0x10 0x03`.**
5. Don't rewrite the working ELM327 transport; deltas land behind a positive
   experiment, individually reversible, one change per arm so a hit is attributable.

---

## 8. Tooling and partial results already in hand

`tools/diagnose_meb_path.py` — 21-arm read-only matrix (EXP-1…EXP-21, numbered as in
reference doc §7), verified against the simulator. Also `tools/collect_acceptance.py`
(one bounded collector run, then reads the database back — the product-level check
that Track A reaches the dashboard), `tools/elm_console.py` (raw
console, explicitly *not* read-only — the one sanctioned exception),
`tools/doctor.py`, `tools/discover_adapters.py`, `main.py collect`.

| Exp | Question | Status from normal runs |
|---|---|---|
| 4 | MEB addressing accepted | **pass** (`ATCP 17` + `ATSH FC007B` accepted) |
| 5 | BMS answers physically | **silent** — 472 reads, `NO DATA` (Track A) |
| 9 | energy module, 11-bit `ATSP6` | **invalid** — adapter refused (`?`), never transmitted (Track B) |
| 10 | DC/DC, 29-bit, no filter | **`?`** — refused even though it is 29-bit |
| 11 | functional `0x22` routing | **`7F 22 31`** (5 Oct) — functional does not route |
| 15 | BMS receive filter | **ANSWERED** (13 Oct) — `ATCRA 17FE007B` → `62 02 8C B2` (Track A solved) |
| 16 | BMS full reference config | **ANSWERED** (13 Oct) — `62 02 8C B2` |
| 17 | energy 3-digit header | `ATSH 0710` (4-digit) refused at the ATSH step; the true 3-digit `710` also `?` — header width **not** the cause |
| 18 | energy with `ATCRA 0000077A` | **`?`** (13 Oct) — filter is *not* the Track B fix |
| 19 | climate with `ATCRA 000007B0` | **`?`** (13 Oct) |
| 20 | energy, 7-byte frame | not the discriminator — see §1.2 |
| 21 | DC/DC with `ATCRA 17FE00B9` | answered (16 Oct, discovery `responder`) |

EXP-6 (`ATBI`), EXP-7/7b/8 (session / tester-present / combined), EXP-12
(BMS on 11-bit `0x7E5`), EXP-13 (CAN-FD probe), EXP-14 (flow control) were all
silent or refused. **Track B is resolved (16 Oct): `ATCAF1` + bare request +
flow-control preamble — see §1.2.**

Expanded per-arm evidence from the 13 Oct run lives in the local (gitignored)
`docs/experiment_record_results.md`.

---

## 9. Open questions the plan must answer

1. **Track B — RESOLVED (16 Oct).** The clone refuses a well-formed 11-bit data
   frame only when it is an `ATCAF0` hand-framed 8-byte line; Car Scanner's
   `ATCAF1` + bare request + flow-control preamble transmits. See §1.2.
2. **Track A is solved:** `ATCRA 17FE007B` answers the BMS (EXP-15/16, 13 Oct).
   The remaining Track A question is only whether the collector drives it in a
   real cycle — answer it with `tools/collect_acceptance.py` (one bounded run,
   then it asserts `pack_voltage` / `soc_abs` / a per-cell sweep landed in the
   database and stayed read-only).
3. Who actually answers functionally? Resolve the identity behind source `0x0A` and
   behind the `7F 22 31`, since the NRC's meaning depends on it.
4. Is a per-module session required despite evDash/ABRP not sending one? (EXP-7
   falsifies the `10 03` theory if a `10 01` arm answers.)
5. **Does the collector recover 29-bit framing after reading an 11-bit module?**
   — **Resolved (17 Oct).** There was no framing to "recover": the 29-bit modules
   were being sent a hand-framed `ATCAF0` line, which this clone refuses with `?`
   (the `veh_info`/`eldrive`/`bat_mgmt` symptom). Every module now uses Car
   Scanner's uniform `ATCAF1` + bare-request form, so the 11-bit→29-bit
   transition no longer changes the framing at all. See §1.3. (Not yet
   re-verified on the car.)
6. Operational: after the 12 Oct re-pair Windows exposes **two** outgoing SPP ports
   (`COM3`, `COM4`, both "Standard Serial over Bluetooth link"). Prove the live port
   (`discover_adapters.py`, then per-port `ATI`) before any car work. (On 13 Oct
   COM3 opened once, then later gave `OSError(22, 'semaphore timeout')` — the
   paired-but-not-connected state.)

**Out of scope** (tracked separately, unblocked by the above): pack-current sign,
`0x2AF7` sub-field offsets, `0x2AB8` divisor.

---

## 10. Where everything lives

| Path | Contents |
|---|---|
| `docs/MEB_DIAGNOSTIC_REFERENCE.md` | full evidence base (§1,§2,§3,§5,§7,§8,§12) |
| `diagnostic/elm327.py` | transport; `_negotiate_meb`, `set_module`, init |
| `diagnostic/ecus.py` | module address registry (`tx29`/`rx29`) |
| `diagnostic/uds.py` | `validate_request()` — policy choke point |
| `tools/diagnose_meb_path.py` | 21-arm read-only matrix |
| `decoders/meb.py`, `decoders/registry.py` | 157 DID specs (16 fast / 141 slow) |
| `collector.py` | `discover_ecus()` — status mapping defect (§4), cell-sweep bug (§6) |
| `data/real_car.db` | 318 measurements (266 Car Scanner imports + 52 real OBD-II); `tx_log` holds the wire evidence above |

## 11. Current live state (13 Oct)

Comms with the car are **down again**: the 13 Oct matrix run completed (the BMS
answered, Track A reconfirmed), then COM3 later failed to open with
`OSError(22, 'semaphore timeout period has expired')` — Windows showing the
pairing as present while the RFCOMM link is not actually up. That is the same
sleep state as before: reconnect the dongle (unplug/replug, ignition on,
Windows → Bluetooth → Connect), then confirm the live SPP port before
experiments.

The last live result is the 13 Oct run: **EXP-15/16 answered `62 02 8C B2`
(SoC 71.2 %)**; EXP-9/17/18/19/10/12 were `?`. That run is captured in the
local `docs/experiment_record_results.md`. The work that follows needs no car:
EXP-17/20/21 are implemented and simulator-verified, and the discriminating
result is one matrix run away.

When comms are back, run **`tools/collect_acceptance.py` first** (highest value:
proves Track A reaches the database in a real cycle, including after the 11-bit
energy/climate excursion — open question 5), then the matrix to settle Track B.
The acceptance tool writes named PASS/FAIL rows per check and exits 2 (not 1)
when the adapter/vehicle cannot be reached, so a Bluetooth failure cannot be
mistaken for bad data.