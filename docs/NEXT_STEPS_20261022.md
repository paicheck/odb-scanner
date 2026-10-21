# 20 next steps — 2026-10-22

**State:** offline implementation pass complete. Commit `b8b1c70` on `main`.
`python -m pytest tests -q` → **746 passed**; `-m "not slow"` → **729 passed**;
`python -m ruff check .` → clean.

**Safety:** this document does **not** authorize a vehicle session. Every live
item below is read-only and still needs the owner's explicit approval. The only
allowed requests are UDS `0x22` reads on modules already in the collector's
allow-list. No session change, coding, adaptation, security access, routine,
actuator test, or fault clearing.

---

## Done in this pass

| # | Step | Status | Evidence |
|---:|---|---|---|
| 1 | Decode `0x1E39` as **signed**, with `FFFF`/`FFFE` and negatives returning `None` | **Done** | `decoders/meb.py`; `tests/test_payload_research.py`; 2,047.97 V artifact gone |
| 2 | Make `0x2A53` reserved current **raw-only** (bytes 3-4 are the complement of the real current, not a current) | **Done** | `decoders/meb.py`; 124-row invariant `u16[1:3] + u16[3:5] ≈ 0x0508` |
| 3 | Relabel `0x5867` as a **10 km/LSB secondary counter**, not the odometer; keep x10 and record x100 as refuted | **Done** | `decoders/meb.py`; x10 delta 10 km vs ~10.5 km odometer delta |
| 4 | Add the `accelerator_pedal_pct` alias for `0xF449` without renaming `res_pid_0149` | **Done** | `decoders/aliases.py`; `web/dashboard.py`; alias route tests |
| 5 | Pin the unresolved `0x27C4` and `0x42DB` arithmetic with regression vectors | **Done** | `tests/test_payload_research.py`; both keys stay quarantined |
| 6 | Add catalogue evidence, limitations, falsifiers, and bump the catalogue version | **Done** | `decoders/catalogue.py`; `CATALOGUE_VERSION = 2026-10-21.1` |
| 7 | Update the quality registry notes without un-quarantining anything | **Done** | `decoders/quality.py`; 5 invalid / 5 excluded |
| 8 | Build a **read-only** payload audit tool | **Done** | `tools/audit_payload_findings.py`; DB opened `mode=ro`, `query_only=ON` |
| 9 | Test the audit tool against a scratch database | **Done** | `tests/test_payload_audit_tool.py`; 3 tests |
| 10 | Run the full suite, fast subset, lint, and gateway dry-run | **Done** | 746 / 729 / clean; `tools/scan_gateway.py --dry-run` lists 9 DIDs |
| 11 | Archive the 2026-10-21 capture and validate it offline | **Done** | 35 captures, **35/35 pass**, 0 fail, 0 indeterminate, normal and `--strict` |

### Artifacts created

- `research/measurement_discovery/captures/live_20261021.json` — 35 raw
  `{key, raw, ts}` records exported read-only from the live DB.
- `research/measurement_discovery/captures/live_20261021_validation.json` —
  35/35 pass under the normal checker.
- `research/measurement_discovery/captures/live_20261021_validation_strict.json` —
  35/35 under `--strict`. The capture has no `expected` fields, so this is still
  a decoder/plausibility validation, not an independent expected-value
  confirmation.

Useful offline commands:

```bat
python tools/audit_payload_findings.py
python tools/scan_gateway.py --dry-run
python -m pytest tests -q
python -m ruff check .
```

---

## Blocked on the car (read-only, one test per session)

| # | Step | Status | Exact test |
|---:|---|---|---|
| 12 | Run the `[19.Gate]` scanner on the real car | **Blocked: adapter + car** | `python tools/scan_gateway.py`; expect 9/9 DIDs and exit 2 on any NRC/no response |
| 13 | Distinguish windshield from ambient for `0x27C4` | **Blocked: safe cold state** | Cold-soak, record `0x27C4`, `outside_temp`, HVAC state; then defroster/vent. Candidate is byte 0 × 0.1 |
| 14 | Resolve `0x42DB` (1,022 ppm whole-u16 vs `03FE` not-fitted) | **Blocked: safe cabin state** | Fresh air, then occupied cabin; look for movement in 400–2,000 ppm or a fixed `03FE` |
| 15 | Confirm `0x5867` on a known-distance drive | **Blocked: drive** | Log `0x5867`, `0x02BD`, `0x295A`, `0xF817`, `eldrive_odometer`; expect ~10 km per LSB, not 100 |
| 16 | Confirm `0xF449` at part/full pedal | **Blocked: safe stationary or closed-road state** | Read `0xF449` at 0 %, ~50 %, 100 %; expect raw / 2.55 = % |
| 17 | Confirm `0x1E39` during a real charge | **Blocked: charging session** | Expect a positive voltage that rises; `FFFF` must stay `None`, never 2,047.97 V |
| 18 | Confirm the `0x2A53` complement under a known 12 V load | **Blocked: known load** | Compare bytes 1-2 (real current) with bytes 3-4; their sum should stay ~0x0508 |
| 19 | Optional cell 36/61 check at lower SoC or steady load | **Blocked: safe low-SoC state** | Read-only cell sweep at 30–50 % SoC; compare cell 61 (−13/−14 mV persistent) and cell 36 (drifting downward) |
| 20 | Restart collection and refresh the dashboard | **Partly done: docs updated; collector stopped by user** | `python main.py collect`; dashboard picks up the next rows. No DB rewrite |

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

## Recommendations

- **Do not blind-probe** `0x2A26`, `0x2A28`, or `0x2A2F`. They have no retained
  payload and no scaling evidence; a positive response alone is not a decoder.
- **Do not enable `0x10 03`** for ABS wheel speeds without separate approval.
  It is a session change, not a read.
- **Do not put CO2 on the dashboard** yet. `03FE` is both a plausible whole-u16
  reading and the BMS not-fitted sentinel; the honest state is unresolved.
- **Do not use `0x5867` for service intervals or SOH.** It is a 10 km/LSB
  counter whose absolute zero/meaning is unknown.
- **Do not treat a missing `0x1E39` as a fault.** `None` means sentinel/invalid,
  not 0 V or 2,047 V.
- **Run the audit tool after every decoder change.** It catches the three
  artifacts this pass removed and documents the two unresolved ones.
- **Keep `data/odb_scanner.db` read-only in analysis.** The audit tool, export
  tool, and validator all read it without writing; preserve that property.
- **Prefer the four agreeing odometers** (`0x02BD`, `0x295A`, `0xF817`,
  `eldrive_odometer`) for distance evidence, not `odometer_2`.

## Exit criteria for the next live session

- Gateway scan: 9/9 DIDs read, no NRC, no unexpected module drop.
- `0x27C4`: a candidate that follows windshield/ambient in the expected direction
  through both cold-soak and defroster, or a clear refutation.
- `0x42DB`: a value that moves with cabin occupancy (whole-u16 hypothesis) or a
  fixed `03FE` across known states (sentinel hypothesis).
- `0x5867`: a known-distance delta consistent with 10 km/LSB across at least two
  odometer points; x100 rejected again.
- `0xF449`: 0 % at rest and a monotonic rise with pedal; no value outside 0–100.
- `0x1E39`: a positive charging voltage, with no sentinel-as-voltage output.
- `0x2A53`: the complement invariant holds under load and the real current is
  read from bytes 1-2.
- No writes, no session changes, no DTC clearing, and no new warning lights.

If any test produces a surprise, stop, preserve the raw bytes, and add the
observation to `research/measurement_discovery/payload_research_20261021.md`
before changing a decoder.
