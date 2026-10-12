# MEB no-response — handoff for planning

**Vehicle:** 2021 VW ID.3 58 kWh, VIN `WVWZZZE1ZMP087053`.
**Goal:** make `odb_scanner` live-read the same BMS/DC-DC/energy/climate data that Car
Scanner reads on the same car through the same adapter.

The deeper source of truth (raw evidence, source logs, provenance) is
`docs/MEB_DIAGNOSTIC_REFERENCE.md` — read §1, §2, §3, §5, §7, §8, §12 next.

---

## 1. What we need from you

**Status: the BMS path is solved.** On 2026-10-12 the matrix produced this
project's first BMS answer — see §1.1. The remaining work is the 11-bit
modules (energy, climate), which are a separate and narrower problem.

The symptom is **not** one problem. The wire log splits it cleanly into two
independent tracks (§4), and the plan should treat them separately:

- **Track A — BMS/DC-DC/vehicle-info (29-bit).** **Solved.** The clone's
  default CAN receive filter silently drops `17FExxxx` responses while
  accepting `18DAF1xx` functional ones. `ATCRA <response-id>` per module
  fixes it. Implemented in `set_module()`.
- **Track B — energy/climate (11-bit).** Still refused with `?`. The
  header-width theory (EXP-17) was tested and **refuted** on the car.
  Needs further experiment.

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

## 6. Track B — energy/climate (11-bit modules)

**The `?` refusals on energy and climate are a separate, narrower problem from
Track A.** The BMS fix (`ATCRA <rx29>`) does not apply here because the car
never transmitted the requests — the adapter refused them with `?` before they
reached the bus. The simulator confirms energy (0x77A) and climate (0x7B0)
answer when `ATCRA` is pinned to their response IDs.

**Hypothesis:** the adapter also requires `ATCRA <response-id>` for the 11-bit
modules, just like the BMS. The collector's `set_module()` already sends
`ATCRA <rx29>` for energy (0x77A) and climate (0x7B0) because `ecus.py`
defines those `rx29` values and `connection.py` passes them to `set_module()`.
So the fix is **already in the data path** — the next live run will test it.

**New matrix arms (simulator-verified):**

| Exp | Hypothesis | Simulator |
|---|---|---|
| EXP-18 | Energy with `ATCRA 0000077A` | ✅ answers 0x2AB8/0x2AB2/0x2AF7 |
| EXP-19 | Climate with `ATCRA 000007B0` | ✅ answers 0x2609/0x2613 |

**EXP-17 (3-digit header) was refuted** — both 3-digit and 6-digit headers
were refused on the car, so header width is not the Track B defect.

**Next step:** run the matrix against the car with the Veepeak awake. EXP-18
and EXP-19 will determine whether the energy/climate `?` refusals are also
just the receive filter.

---

## 7. Hard constraints (non-negotiable)

1. **Strictly read-only.** Only `0x10 0x01`, `0x22`, `0x19`, `0x3E`,
   `0x01/0x03/0x09`, plus adapter-local `AT*`. **Never** `0x27`, `0x2E`, `0x31`,
   `0x14`, `0x30`, writes, coding, adaptations, actuator tests, ECU reset, flashing.
2. **Never weaken `diagnostic/uds.py` `validate_request()`** — the single choke
   point. Suite must stay green (325 tests, ruff clean, `smoke_test.py` 24/0/1,
   `guard-test`); new tests must fail on pre-fix code.
3. **No blind CAN-ID scans, no arbitrary UDS.** Experiments go through
   `tools/diagnose_meb_path.py`, whose arms are allow-listed and verified end-to-end
   against the simulator (`tests/test_meb_path.py`).
4. **No `0x10 0x03`.**
5. Don't rewrite the working ELM327 transport; deltas land behind a positive
   experiment, individually reversible, one change per arm so a hit is attributable.

---

## 8. Tooling and partial results already in hand

`tools/diagnose_meb_path.py` — 16-arm read-only matrix (EXP-1…EXP-16, numbered as in
reference doc §7), verified against the simulator. Also `tools/elm_console.py` (raw
console, explicitly *not* read-only — the one sanctioned exception),
`tools/doctor.py`, `tools/discover_adapters.py`, `main.py collect`.

## 8. Tooling and partial results already in hand

`tools/diagnose_meb_path.py` — 19-arm read-only matrix (EXP-1…EXP-19, numbered as in
reference doc §7), verified against the simulator. Also `tools/elm_console.py` (raw
console, explicitly *not* read-only — the one sanctioned exception),
`tools/doctor.py`, `tools/discover_adapters.py`, `main.py collect`.

| Exp | Question | Status from normal runs |
|---|---|---|
| 4 | MEB addressing accepted | **pass** (`ATCP 17` + `ATSH FC007B` accepted) |
| 5 | BMS answers physically | **silent** — 472 reads, `NO DATA` (Track A) |
| 9 | energy module, 11-bit `ATSP6` | **invalid** — adapter refused (`?`), never transmitted (Track B) |
| 10 | DC/DC, 29-bit | **inconclusive** — 1 `NO DATA`, 14 `?` (Track B) |
| 11 | functional `0x22` routing | **`7F 22 31`** (5 Oct) — functional does not route |
| 15 | BMS receive filter | **ANSWERED** — `ATCRA 17FE007B` → `62 02 8C B3` (Track A solved) |
| 17 | energy 3-digit header | `?` — **refuted** on car |
| 18 | energy with `ATCRA 0000077A` | **simulator ✅** — next car run will test |
| 19 | climate with `ATCRA 000007B0` | **simulator ✅** — next car run will test |

EXP-6 (`ATBI`), EXP-7/7b/8 (session / tester-present / combined), EXP-12
(BMS on 11-bit `0x7E5`), EXP-13 (CAN-FD probe, cheap), EXP-14 (flow control),
EXP-16 (full reference config). EXP-15 answered; EXP-17 refuted. **EXP-18/19 are
the Track B discriminators to order first.** Track B should be fixed before
EXP-9/10 are re-run, otherwise they cannot produce a meaningful result.

---

## 9. Open questions the plan must answer

1. **Track B:** why does the adapter answer `?` to a well-formed data frame after a
   module switch, while `set_module()` reports success? Does the `ATSP6` switch clear
   or invalidate the header? Reproducible offline against the simulator.
2. **Track A:** which difference blocks physical `0x22` to the BMS — `ATBI`, flow
   control, per-module `ATCRA`, or a combination?
3. Who actually answers functionally? Resolve the identity behind source `0x0A` and
   behind the `7F 22 31`, since the NRC's meaning depends on it.
4. Is a per-module session required despite evDash/ABRP not sending one? (EXP-7
   falsifies the `10 03` theory if a `10 01` arm answers.)
5. **Do energy and climate (11-bit) also need their receive filter pinned?**
   EXP-18 and EXP-19 will test this on the next car run. The simulator confirms
   both answer when `ATCRA` is pinned to 0x77A / 0x7B0. The collector's
   `set_module()` already sends these filters automatically.
6. Operational: after the 12 Oct re-pair Windows exposes **two** outgoing SPP ports
   (`COM3`, `COM4`, both "Standard Serial over Bluetooth link"). Prove the live port
   (`discover_adapters.py`, then per-port `ATI`) before any car work.

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
| `tools/diagnose_meb_path.py` | 19-arm read-only matrix |
| `decoders/meb.py`, `decoders/registry.py` | 157 DID specs (16 fast / 141 slow) |
| `collector.py` | `discover_ecus()` — status mapping defect (§4), cell-sweep bug (§6) |
| `data/real_car.db` | 318 measurements (266 Car Scanner imports + 52 real OBD-II); `tx_log` holds the wire evidence above |

## 11. Current live state (12 Oct)

Comms with the car are **down**: the Veepeak's Bluetooth is asleep (car slept after
the last run; the stack was stopped cleanly). To resume: reconnect the dongle
(unplug/replug, ignition on, Windows → Bluetooth → Connect), then confirm the live
SPP port before experiments. Web dashboard (port 8000) is up; collector is not
running. No data lost. **Track B needs none of this** — it can start immediately
against the simulator.