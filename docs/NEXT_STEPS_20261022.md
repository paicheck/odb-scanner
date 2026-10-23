# 20 next steps — 2026-10-22

**State:** offline implementation pass complete (2026-10-22 payload/capture
pass). `python -m pytest tests -q` → **765 passed**; `-m "not slow"` →
**748 passed**; `python -m ruff check .` → clean; the real-DB payload audit
passes and the strict expected-value archive is **5/5 PASS**.

**Safety:** this document does **not** authorize a vehicle session. Every normal
live item below is read-only and still needs the owner's explicit approval. The
only normally allowed requests are UDS `0x22` reads on modules already in the
collector's allow-list. No coding, adaptation, security access, routine,
actuator test, or fault clearing.

**One narrow exception is prepared, not executed:** the ABS wheel-speed reader
(`tools/read_abs_wheel_speeds.py`) can send exactly one `0x10 0x03` extended
session on `0x713` after `--execute --approve-session-change`, then reads only
`0x1800`–`0x1803`, and always attempts `0x10 0x01`. It has **not** been run
against the car. The ordinary UDS allow-list still refuses `0x10 0x03`; do not
treat the tool's existence as approval.

---

## Done in this pass

| # | Step | Status | Evidence |
|---:|---|---|---|
| 1 | Decode `0x1E39` as **signed**, with `FFFF`/`FFFE` and negatives returning `None` | **Done** | `decoders/meb.py`; `tests/test_payload_research.py`; 2,047.97 V artifact gone |
| 2 | Make `0x2A53` reserved current **raw-only** (bytes 3-4 are the complement of the real current, not a current) | **Done** | `decoders/meb.py`; 124-row invariant `u16[1:3] + u16[3:5] ≈ 0x0508` |
| 3 | Relabel `0x5867` as a **10 km/LSB secondary counter**, not the odometer; keep x10 and record x100 as refuted | **Done** | `decoders/meb.py`; x10 delta 10 km vs ~10.5 km odometer delta |
| 4 | Add the `accelerator_pedal_pct` alias for `0xF449` without renaming `res_pid_0149` | **Done** | `decoders/aliases.py`; `web/dashboard.py`; alias route tests |
| 5 | Pin the unresolved `0x27C4` and `0x42DB` arithmetic with regression vectors | **Done** | `tests/test_payload_research.py`; both keys stay quarantined |
| 6 | Add catalogue evidence, limitations, falsifiers, and bump the catalogue version | **Done** | `decoders/catalogue.py`; `CATALOGUE_VERSION = 2026-10-22.1` |
| 7 | Update the quality registry notes without un-quarantining anything | **Done** | `decoders/quality.py`; 5 invalid / 5 excluded |
| 8 | Build a **read-only** payload audit tool | **Done** | `tools/audit_payload_findings.py`; DB opened `mode=ro`, `query_only=ON` |
| 9 | Test the audit tool against a scratch database | **Done** | `tests/test_payload_audit_tool.py`; 3 tests |
| 10 | Run the full suite, fast subset, lint, and gateway dry-run | **Done** | 765 / 748 / clean; `tools/scan_gateway.py --dry-run` lists 9 DIDs |
| 11 | Archive the 2026-10-21 capture and validate it offline | **Done** | 35 captures, **35/35 pass**, 0 fail, 0 indeterminate, normal and `--strict` |

### 2026-10-22 follow-up pass (done)

| # | Step | Status | Evidence |
|---:|---|---|---|
| F1 | Recover the passive btsnoop and extract only CAN/DID/payload records | **Done** | `tools/mine_passive_capture.py`; 18,468 btsnoop / 10,417 ACL records parsed; raw log not committed |
| F2 | Archive the recovered evidence with SHA-256 provenance | **Done** | `passive_capture_evidence_20261015.json` + `.manifest.json`; raw phone data excluded |
| F3 | Recover `0x2A26`/`0x2A28`/`0x2A2F` payloads | **Done** | Exact 32/32/1-byte payloads in the archive; `0x2A26` == `0x04A3`; no profile scale, so raw-only |
| F4 | Implement the `0x42DB` sentinel-aware decoder | **Done** | `03FE` → `None`; non-sentinel → whole-u16 ppm; 40/40 rows and cell sentinel pinned |
| F5 | Implement the `0x27C4` byte-0 decoder | **Done** | byte 0 × 0.1 °C; old u16 formula refuted; key still quarantined pending cold-soak/defroster |
| F6 | Pin the `0x5867` offset | **Done** | 11,140/11,150 km vs 111,009/111,020 km → 99,869–99,871 km offset; BMS-reset hypothesis only |
| F7 | Register ABS wheel-speed decoders for the dedicated tool only | **Done** | `register_abs()`; default collector does not send `0x1800`–`0x1803`; 4 decoders, 0.01 km/h |
| F8 | Prepare the approval-gated ABS reader | **Done, not executed** | `tools/read_abs_wheel_speeds.py`; `--execute --approve-session-change`; exact `1003` only; `1001` in `finally` |
| F9 | Add passive-miner and strict-archive tests | **Done** | `tests/test_passive_capture.py`; `tests/test_abs_tool.py`; 765 total |
| F10 | Build and run the strict expected-value archive | **Done** | `strict_expected_values_20261022.json`; **5/5 PASS** with non-null expected values |
| F11 | Close the cluster lamp question with evidence | **Done** | No `0x773`/`0x7DD` in the capture; no profile lamp DID; no sweep; DTC bit 7 remains the signal |

### Artifacts created

- `research/measurement_discovery/captures/live_20261021.json` — 35 raw
  `{key, raw, ts}` records exported read-only from the live DB.
- `research/measurement_discovery/captures/live_20261021_validation.json` —
  35/35 pass under the normal checker.
- `research/measurement_discovery/captures/live_20261021_validation_strict.json` —
  35/35 under `--strict`. The capture has no `expected` fields, so this is still
  a decoder/plausibility validation, not an independent expected-value
  confirmation.
- `research/measurement_discovery/captures/passive_capture_evidence_20261015.json`
  + `.manifest.json` — 278 sanitised CAN/DID records plus DB evidence; archive
  SHA-256 `b2c490d523bbeecc61b9ee30022e2929e3c4d31752d69754fb7f950e03646cb7`;
  source btsnoop SHA-256 `eefb…96f9`; the raw log is not committed.
- `research/measurement_discovery/captures/strict_expected_values_20261022.json`
  + `.manifest.json` — five independent expected values; **5/5 PASS** under
  `--strict` with non-null expected values.
- `tools/mine_passive_capture.py` — ISO-TP SF/FF/CF reassembly from a local
  btsnoop; never opens a socket or adapter.
- `tools/read_abs_wheel_speeds.py` — approval-gated ABS reader; default dry-run,
  `--execute` plus `--approve-session-change`, exact `1003`, always attempts
  `1001`. Not executed.
- `tests/test_passive_capture.py`, `tests/test_abs_tool.py` — synthetic btsnoop,
  archive hash/fact checks, strict archive, and the token/session guard.

Useful offline commands:

```bat
python tools/audit_payload_findings.py
python tools/scan_gateway.py --dry-run
python tools/read_abs_wheel_speeds.py --dry-run
python tools/validate_measurement_capture.py --in research\measurement_discovery\captures\strict_expected_values_20261022.json --strict
python -m pytest tests -q
python -m ruff check .
```

---

## Blocked on the car (read-only, one test per session)

| # | Step | Status | Exact test |
|---:|---|---|---|
| 12 | Run the `[19.Gate]` scanner on the real car | **Done 2026-10-22: 9/9 positive, 0 NRC** | `python tools/scan_gateway.py`; archived as `research/measurement_discovery/captures/live_gateway_scan_20261022.json` + manifest |
| 13 | Distinguish windshield from ambient for `0x27C4` | **Attempted 2026-10-22; incomplete: baseline only, no defroster window** | Cold-soak, record `0x27C4`, `outside_temp`, HVAC state; then defroster/vent. Candidate is byte 0 × 0.1; the key stays quarantined until this test |
| 14 | Resolve `0x42DB` (whole-u16 ppm vs `03FE` not-fitted) | **Blocked: safe cabin state; sentinel decoder done** | Fresh air, then occupied cabin; look for movement in 400–2,000 ppm or a fixed `03FE`. All 40 rows so far are `03FE`, so “sensor not fitted” is the leading hypothesis |
| 15 | Confirm `0x5867` on a known-distance drive | **Blocked: drive; offset now known** | Log `0x5867`, `0x02BD`, `0x295A`, `0xF817`, `eldrive_odometer`; expect ~10 km per LSB, not 100. On this car the counter is 99,869–99,871 km below the main odometer |
| 16 | Confirm `0xF449` at part/full pedal | **Blocked: safe stationary or closed-road state** | Read `0xF449` at 0 %, ~50 %, 100 %; expect raw / 2.55 = % |
| 17 | Confirm `0x1E39` during a real charge | **Blocked: charging session** | Expect a positive voltage that rises; `FFFF` must stay `None`, never 2,047.97 V |
| 18 | Confirm the `0x2A53` complement under a known 12 V load | **Blocked: known load** | Compare bytes 1-2 (real current) with bytes 3-4; their sum should stay ~0x0508 |
| 19 | Optional cell 36/61 check at lower SoC or steady load | **Blocked: safe low-SoC state** | Read-only cell sweep at 30–50 % SoC; compare cell 61 (−13/−14 mV persistent) and cell 36 (drifting downward) |
| 20 | Restart collection and refresh the dashboard | **Stopped after 2026-10-22 live session; no adapter process running** | `python tools/start_scanner.py` when back at the car; dashboard picks up the next rows. No DB rewrite |
| 21 | Read ABS wheel speeds `0x1800`–`0x1803` | **Blocked: separate approval for `0x10 03`** | `python tools/read_abs_wheel_speeds.py --dry-run`, then only after approval `--execute --approve-session-change`; exact `1003`, four `22` reads, `1001` return |
| 22 | Direct cluster lamp/tell-tale DID | **Closed for now: no documented DID** | No `0x773`/`0x7DD` traffic in the passive capture and no profile lamp DID; do not sweep. Use DTC status bit 7 |

---

## Live session 2026-10-22 (results)

- **Gateway scan: DONE.** `tools/scan_gateway.py` read all nine `[19.Gate]`
  DIDs on `0x710 -> 0x77A`: **9/9 positive, 0 NRC, 0 timeouts**, only UDS
  `0x22`, no session change. Cross-check: `0x2AB8` and the final field of
  `0x15D1` both read **39,950 Wh**; `0x2AB2` read **53,200 Wh**. Evidence:
  `research/measurement_discovery/captures/live_gateway_scan_20261022.json`
  plus `.manifest.json` (SHA-256
  `05bf1e4a690c1ca88b38bc1e32ae59aa1d15ff3c39ec7e05b35ee5a9975f9595`).
- **`0x27C4` cold-soak/defroster: INCOMPLETE.** A targeted read-only sampler
  captured four baseline points (windshield **12.7 °C**, outside **12.0 °C**,
  front-left deflector **39.96 °C**, parked, A/C and PTC off) before the session
  was stopped at 26 s. No defroster-on window was captured. This is **not**
  evidence for the windshield label; the key remains quarantined. The raw
  sampler log is `data/logs/climate_27c4_defroster_20261022T204247Z.json`
  (gitignored, not archived as evidence).
- **ABS wheel speeds: not attempted.** No `0x10 03` was sent and no separate
  approval was given.
- **Collector/dashboard: stopped.** No live adapter process remains. Restart
  with `python tools/start_scanner.py` when back at the car.

---

## Recommended live-session order

1. **Gateway scan first.** It is a bounded 9-DID read on `0x710 -> 0x77A`.
   If it exits `2`, stop and inspect the JSON evidence; do not retry a different
   path or a session change.
2. **One known-state test per session.** Record ambient temperature, HVAC mode,
   SoC, ignition state, and whether the car is plugged in. A test without that
   context is not evidence.
3. **`0x27C4` cold-soak/defroster** before any drive.
4. **`0x42DB` fresh-air/occupied** while parked, not while driving.
5. **`0xF449` pedal** only in a safe stationary/closed-road condition.
6. **`0x5867` known-distance drive** last, because it needs a real distance and
   the four odometers in the same capture.
7. **`0x1E39` charging** only during a normal charge the owner already planned.
8. **`0x2A53` load** opportunistically, with a known 12 V load, not by forcing
   an actuator.

**ABS wheel speeds are not in the normal order.** They are the only step that
needs a session change, and they require a separate explicit approval. Run the
dry-run first; do not combine them with any other test.

## Recommendations

- **Do not blind-probe or invent a scale** for `0x2A26`, `0x2A28`, or `0x2A2F`.
  Their payloads are now recovered and archived, but no profile layout exists;
  `0x2A26` is byte-identical to `0x04A3`. They stay raw-only/quarantined.
- **Do not enable `0x10 03`** for ABS wheel speeds without separate approval.
  It is a session change, not a read. The prepared tool still requires
  `--execute --approve-session-change`, sends only that one request, and always
  attempts `0x10 0x01`.
- **Do not put CO2 on the dashboard** yet. `03FE` is the BMS not-fitted sentinel
  and now decodes to `None`; the non-sentinel whole-u16 scale is provisional.
- **Do not sweep the instrument cluster.** No documented lamp/tell-tale DID
  exists; the passive capture has no `0x773`/`0x7DD` traffic. The supported lamp
  signal is DTC status bit 7.
- **Do not use `0x5867` for service intervals or SOH.** It is a 10 km/LSB
  counter with a constant ~99,870 km offset on this car; the absolute zero and
  the BMS-reset explanation still need service history.
- **Do not treat a missing `0x1E39` as a fault.** `None` means sentinel/invalid,
  not 0 V or 2,047 V.
- **Run the audit tool and strict validator after every decoder change.** They
  catch the three artifacts this pass removed and keep the independent expected
  values honest.
- **Keep `data/odb_scanner.db` read-only in analysis.** The audit tool, export
  tool, validator and passive miner all read without writing; preserve that
  property. Never commit the raw btsnoop: it contains unrelated phone data.
- **Prefer the four agreeing odometers** (`0x02BD`, `0x295A`, `0xF817`,
  `eldrive_odometer`) for distance evidence, not `odometer_2`.

## Exit criteria for the next live session

- Gateway scan: 9/9 DIDs read, no NRC, no unexpected module drop.
- `0x27C4`: a candidate that follows windshield/ambient in the expected direction
  through both cold-soak and defroster, or a clear refutation.
- `0x42DB`: a value that moves with cabin occupancy (whole-u16 hypothesis) or a
  fixed `03FE` across known states (sentinel hypothesis). The sentinel decode
  must remain `None`, never 1,022 or 25,400 ppm.
- `0x5867`: a known-distance delta consistent with 10 km/LSB across at least two
  odometer points; x100 rejected again. The ~99,870 km offset should stay
  constant unless the BMS was reset.
- `0xF449`: 0 % at rest and a monotonic rise with pedal; no value outside 0–100.
- `0x1E39`: a positive charging voltage, with no sentinel-as-voltage output.
- `0x2A53`: the complement invariant holds under load and the real current is
  read from bytes 1-2.
- ABS (only if separately approved): positive `0x50 0x03`, four 12-byte
  records, plausible wheel speeds, and a positive `0x50 0x01` return. If the
  return fails, stop and record it; do not repeat the session change.
- No writes, no coding/adaptation/security access, no routines/actuator tests,
  no DTC clearing, no cluster sweep, and no new warning lights. The only
  possible session change is the separately approved ABS `0x10 0x03`.

If any test produces a surprise, stop, preserve the raw bytes, and add the
observation to `research/measurement_discovery/payload_research_20261021.md`
before changing a decoder.
