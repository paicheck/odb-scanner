# odb_scanner — Project Handover

**Last updated:** 2026-10-22 · **Git HEAD:** `main` (2026-10-22 payload/capture pass) · **Test suite:** 765 passing

This document is the "hand this to the next person" summary. It explains what the
project is, how it is put together, what state it is in, and — most importantly —
where the unresolved questions and safety boundaries are.

---

## 1. What this is

A **local, strictly read-only** diagnostic and analysis system for a
**2021 Volkswagen ID.3 58 kWh** (VIN `WVWZZZE1ZMP087053`).

```text
VW ID.3 → OBD-II port → ELM327/OBDLink (USB/Bluetooth) → Windows PC
   → Python collector → SQLite → statistics/anomaly detection
   → Ollama (local LLM) → FastAPI web dashboard / reports
```

It reads vehicle data over UDS (`0x22 ReadDataByIdentifier`) and OBD-II, stores
every raw response, decodes them into named measurements, runs statistics, and
renders a dashboard. The LLM only interprets evidence — it has **no path to the
vehicle**.

Two companion pieces live in the same repo:

- **`android/`** — an Android client (Kotlin/Compose) that loads the same
  measurement spec and decodes identically.
- **`cloud/`** — a small optional MySQL/MariaDB ingest API so phone and laptop
  readings can be merged. Local SQLite remains the source of truth.

---

## 2. Safety model — read this first

The single most important property to preserve is **read-only**. It is enforced
structurally in `diagnostic/uds.py`, the *only* module that builds UDS requests.

- **Allowed:** `0x10` (default session only, sub-function `0x01`), `0x22`
  ReadDataByIdentifier, `0x19` ReadDTCInformation (read sub-functions
  `0x01–0x09` only), `0x3E` TesterPresent, and read-only OBD-II modes `01/03/09`.
- **Blocked** (raises `ReadOnlyViolationError`): `0x14` clear DTCs, `0x27`
  security access, `0x2E` write, `0x31` routine control, `0x2F` actuator
  control, `0x34–0x37` flashing, `0x28/0x85` comms control, and everything else.
  There is **no API to send arbitrary payloads**.
- The guard checks the **whole request**, including sub-functions: `0x10 0x02`
  and `0x10 0x03` (session changes) are refused even though `0x10` is allowed.
- Every request sent by the collector is logged to the `tx_log` table with a
  human-readable `purpose` (the safety manifest). The ABS reader records its
  TX/RX pairs in its report and forwards them to any `tx_logger` the connection
  already has; it does not open the database itself.

**Two deliberate exceptions:**

1. `tools/elm_console.py` is a raw, no-allow-list console for debugging. If you
   type `2E`, `31`, `14` there, it reaches the car. Everything else — `collect`,
   `discover`, `doctor`, `guard-test`, the dashboard — goes through the
   allow-list. Do not assume `elm_console.py` is safe.
2. `tools/read_abs_wheel_speeds.py` is a narrow, approval-gated ABS reader. The
   normal allow-list still refuses `0x10 0x03`; this tool can send **exactly
   one** `0x10 0x03` on `0x713` after `--execute --approve-session-change`, then
   reads only `0x1800`–`0x1803`, and always attempts `0x10 0x01` in a `finally`
   block. Its TX/RX pairs are kept in the printed/written report and forwarded
   to an existing `tx_logger`; the tool does not open `data/odb_scanner.db`
   itself. It has **not** been run against the car. Use `--dry-run` unless the
   session change has been separately approved.

Verify the guard yourself at any time:

```bat
python main.py guard-test
```

---

## 3. Current state in one paragraph

The project is **functional end-to-end** and the recently-completed measurement
discovery phase is merged. The decoder map has been repaired and expanded: the
25 electric-drive DIDs that used to decode as nonsense (e.g. 433 V reading as
1738 V, 16 °C reading as 1682 °C) now decode as IEEE-754 float32; the battery
energy block (`0x1E32`) now reads four lifetime counters; cell/sensor "which
cell is hot/cold/weak" indices are exposed; and a set of derived power/spread
metrics (12 V power, DC-DC power, cell/temperature spread) is calculated and
surfaced. The 2026-10-21 live read-only session succeeded: 568 measurement rows,
295 distinct keys, 557 successful reads, and all 22 raw captures passed the
plausibility checker. The battery snapshot at 20:45 UTC read 79.6 % SoC
(81.8628 % normalized), 429.5 V, −4.89 A, −2.1 kW, an 18.1 mV cell delta and
9.5 °C; SOH is 91.7 %.

The 2026-10-22 payload/capture pass recovered the missing passive btsnoop:
`0x2A26`/`0x2A28`/`0x2A2F` payloads are now archived (still no profile scale,
so raw-only), `0x42DB` `03FE` is a not-fitted sentinel (decoded as `None`), and
`0x27C4` byte 0 × 0.1 °C is implemented while the windshield-vs-ambient label
stays quarantined. `0x5867` is pinned to a 10 km/LSB counter with a constant
99,869–99,871 km offset from the main odometer. The four ABS wheel-speed
decoders and an approval-gated reader are prepared but **not executed**; the
ordinary allow-list still refuses `0x10 0x03`. A sanitised evidence archive
excludes the raw phone log, and the strict expected-value archive passes 5/5
with non-null expected values. **765 tests pass.** The next live step is the
gateway scan, then known-state tests for the remaining payloads; no writes or
session changes are part of that plan, except the separately approved ABS
session if the owner opts in.

---

## 4. Repository layout

| Path | Purpose |
|---|---|
| `main.py` | CLI entry point (`collect`, `discover`, `serve`, `simulate`, `analyze`, `report`, `doctor`, `backup`, `prune`, `guard-test`, …) |
| `collector.py` | The collection loop; polls DIDs, decodes, persists, computes derived metrics |
| `diagnostic/` | Transport + UDS + ECU addressing. `uds.py` is the read-only choke point |
| `decoders/` | The measurement data model (see §5) — `registry.py`, `catalogue.py`, `quality.py`, `meb.py`, `gateway.py`, `bms.py` |
| `database/` | SQLite schema + `Repository` (`models.py`, `repository.py`) |
| `analysis/` | Statistics, anomaly detection, freshness/provenance, series/trends, discovery |
| `web/` | FastAPI dashboard + Jinja2 templates |
| `ai/` | Ollama integration (report generation) |
| `simulator/` | Built-in ID.3 simulator (TCP ELM327, no car needed) |
| `gui/` | Desktop window (`0_OPEN_GUI.bat`) |
| `android/` | Kotlin/Compose client + its own `Decode.kt` (mirrors `decoders/meb.py`) |
| `cloud/` | Optional MySQL ingest API |
| `spec/odb_scanner.json` | Generated shared spec consumed by the Android client (source of truth for DID addresses/scales) |
| `tests/` | 765 tests (no car, no Ollama required) |
| `tools/` | Scripts: `smoke_test.py`, `doctor` helpers, `replay_decoder_impact.py`, `validate_measurement_capture.py`, `export_validation_capture.py`, `export_spec.py`, `export_catalogue.py`, … |
| `research/measurement_discovery/` | The evidence package for the recent decoder work (see §7) |
| `docs/investigation/` | Historical investigation notes, replay reports, gateway-energy passive-capture writeups |
| `data/` | SQLite databases + logs (mostly gitignored; `data/odb_scanner.db` is the real car's DB) |

Top-level `.bat` files (`0_OPEN_GUI.bat` … `5_TRY_IT_NOW.bat`) are double-click
shortcuts that wrap `main.py` / `tools/start_scanner.py`.

---

## 5. The measurement data model (three layers)

This is the part worth understanding before touching any decoder.

1. **`decoders/registry.py` — `DIDRegistry` / `DIDSpec`.** Every decodable DID as
   a key, ECU, DID address, unit, and a `decode(raw_bytes) -> value` function.
   Currently **287 MEB specs / 253 distinct DIDs**.

2. **`decoders/catalogue.py` — `MeasurementDefinition`.** Adds the layer the
   registry doesn't carry: *where the definition came from, how sure we are, and
   what would falsify it*. Includes `ByteLayout` (offset/length/endianness/sign/
   scale/divisor/addend, plus a `float32` encoding), `confidence`
   (VERIFIED / PROVISIONAL / UNKNOWN), `evidence_kind`, `plausible_min/max`, and
   `falsified_by`. `validate_catalogue()` checks the catalogue reproduces the live
   decoder and that confidence levels actually have evidence.

3. **`decoders/quality.py` — `MeasurementQuality`.** Consumes the catalogue and
   decides, per key: `verification`, `data_kind` (numeric / json / text /
   derived), `use_in_scoring`, and a plausibility range. Keys with known-bad
   decodes are marked `INVALID` and **excluded from scoring** rather than
   silently poisoning health/attribution. Current totals: **296 records** —
   49 provisional, 240 unknown, 2 verified, 5 invalid (5 excluded), 10 derived.
   The catalogue itself has **287 MEB entries** — 43 provisional, 242 unknown,
   2 verified.

The rule of thumb: **a plausible value is not proof.** The catalogue will not let
you mark something PROVISIONAL/VERIFIED without an `evidence_kind`, and it will
not let you mark it VERIFIED on a third party's own label alone.

---

## 6. How to run it

### Install

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        :: pinned: requirements-lock.txt
copy config.local.yaml.example config.local.yaml     :: optional, gitignored
```

`config.yaml` is the committed default; `config.local.yaml` deep-merges over it.

### Test with no car (recommended first step)

```bat
.venv\Scripts\python tools\smoke_test.py              :: end-to-end self-test
.venv\Scripts\python -m pytest tests -q               :: 765 tests
.venv\Scripts\python -m pytest -m "not slow"          :: 748-test fast subset
.venv\Scripts\python -m ruff check .                  :: lint (clean)
```

### Run a simulated stack

```bat
.venv\Scripts\python tools\start_scanner.py            :: simulator + collector + dashboard
start http://127.0.0.1:8000
```

### With the real car

```bat
1.  Ignition ON, adapter in the OBD port.
2.  Set adapter.port in config.yaml (or "auto").
3.  .venv\Scripts\python main.py discover             :: ECU/DID discovery (read-only)
4.  .venv\Scripts\python main.py collect              :: continuous collection
5.  .venv\Scripts\python main.py serve                :: dashboard
```

If everything says `NO DATA`, the car is likely asleep — see the doctor:

```bat
.venv\Scripts\python main.py doctor                   :: diagnose the link layer by layer
```

---

## 7. What the recent decoder work actually changed

This is the substance of the recent effort and is recorded in
`research/measurement_discovery/` (esp. `decoder_audit.md`, `final_report.md`,
`validation_roadmap.md`) and `docs/investigation/`.

**Shipped (commits `8bed969`, `76af431`, `b192723`):**

- **Float32 family** — 25 electric-drive DIDs (DC-link V/I, stator/rotor/PCB
  temps, torque, inverter losses, motor current) now decode as IEEE-754
  big-endian `FLOAT32(A,B,C,D)` instead of the old `u16/10+1`. Catalogue layouts,
  provenance, and broad plausibility ranges were added.
- **`0x1E32` four-field energy block** — signed charge/discharge Ah at bytes
  0/4 and charge/discharge kWh at bytes 8/12, replacing the old two-field
  Ah-as-kWh misread (~3,724 kWh → correct ~24,437 kWh charged).
- **1-based cell/sensor indices** — `cell_voltage_max_cell`/`min_cell`,
  `bat_temp_max_sensor`/`min_sensor` expose *which* cell/sensor is the extreme.
- **Pack-current sign** — confirmed **negative = discharge** from independent
  bytes; the arithmetic was already correct, only the documentation/Car Scanner
  comparison was wrong. **Do not invert it.**
- **Derived metrics** — `pack_power_kw`, `cell_delta_mv`, `soh_pct`,
  `cell_soc_spread_pct`, `battery_temp_spread_c`, `aux_12v_power_w`,
  `dcdc_power_kw`, `dcdc_gw_power_kw` are calculated in `collector.py` and
  surfaced in the dashboard/explore/trend/export.
- **Battery serial** (`0x0500`) decodes as trimmed ASCII; charge-port bits
  (`0x7448`) as bit 2 (AC) / bit 3 (DC).
- **Removed** the always-failing BMS `0xF802` VIN request (returns NRC `0x31`);
  the usable VIN is `veh_info` `0xF802`.

**2026-10-22 payload pass (commit `b8b1c70`):**

- **`0x1E39`** — the profile declares it signed; `FFFF`/`FFFE` and negative
  values now return `None`, ending the 2,047.97 V artifact. Historical rows are
  unchanged and the raw bytes are preserved.
- **`0x2A53`** — bytes 3-4 are the complement of the real current at bytes 1-2
  (sum ~0x0508), not a second current. The key is raw-only instead of emitting a
  fabricated ~250 A; `dcdc_gw_current` is the real current.
- **`0x5867`** — relabelled a 10 km/LSB secondary distance counter, not the
  odometer. x10 matched a ~10.5 km drive; x100 is refuted.
- **`0xF449`** — already decoded correctly (`raw / 2.55` %). The readable alias
  `accelerator_pedal_pct` resolves to the stored key `res_pid_0149` without
  creating a second series.
- **Evidence tooling** — `tools/audit_payload_findings.py` re-runs the byte
  checks against a database opened `mode=ro`; `tests/test_payload_research.py`
  pins the arithmetic and the remaining hypotheses.

**2026-10-22 passive-capture pass:**

- **Recovered the missing btsnoop** (`C:\Users\kings\Documents\CarScanner_Capture\btsnoop_hci.log`,
  1,455,992 bytes, SHA-256 `eefb…96f9`). The raw log contains unrelated phone
  traffic and is **not committed**; `tools/mine_passive_capture.py` extracts only
  CAN/DID/payload records via ISO-TP SF/FF/CF reassembly.
- **`0x2A26`/`0x2A28`/`0x2A2F`** — exact payloads recovered on `0x77A`/`0x77F`.
  `0x2A26` and `0x04A3` are byte-identical (alias/shared block). No profile
  layout or scale exists, so all three remain raw-only and quarantined.
- **`0x42DB`** — every stored response is `03FE`; the same bytes are the BMS
  not-fitted sentinel in `cell_t_18`–`cell_t_23`. The decoder now returns `None`
  for `03FE` and whole-u16 ppm for a non-sentinel frame. The sensor appears not
  fitted; the non-sentinel scale is still provisional.
- **`0x27C4`** — byte 0 × 0.1 °C is implemented. The profile's
  `u16 × 15.625 − 39.5` gives ~488,507 °C and is refuted. The key remains
  quarantined until a cold-soak/defroster test separates windshield from ambient.
- **`0x5867`** — the recovered payload gives 11,140 km at ×10, and the main
  odometer pairs give a constant 99,869–99,871 km offset. That is consistent with
  a BMS replacement/reset at ~99,870 km, but service history is unavailable.
- **ABS `0x1800`–`0x1803`** — the passive capture contains the Car Scanner
  sequence `ATSH713`, `ATSP6`, `ATCRA77D`, `1003`, positive
  `77D065003001E01E5`, then the four reads. The four decoders use
  `SBI=0 DL=2 ×0.01 km/h`, are registered only by `register_abs()`, and are
  **not** in the default collector. `tools/read_abs_wheel_speeds.py` is
  approval-gated, dry-run by default, and always attempts `0x10 0x01`.
- **Evidence archive** — `passive_capture_evidence_20261015.json` (278 records
  plus DB evidence; SHA-256 `b2c490…46cb7`) and
  `strict_expected_values_20261022.json` (5/5 PASS with independent displayed
  values). The raw btsnoop is never copied into the repository.

**Still quarantined (invalid, excluded from scoring — do not "fix" by guessing):**

- `co2_content_interior` and `windshield_temperature` remain quarantined even
  though their candidate decoders are implemented. CO2 `03FE` is a sentinel;
  windshield byte 0 needs the cold-soak/defroster label test.
- `odometer_2`, `bat_charging_voltage` and `dc_dc_converter_reserved_current`
  still have no scoring use, but no longer emit the old artifact: the first is a
  relabelled counter with a known ~99,870 km offset, the second returns `None`
  for sentinels, and the third is raw-only.
- `res_pid_0149` is resolved and scoreable; the alias does not create a second
  key.

**Impact audit:** `tools/replay_decoder_impact.py` re-decoded 8,134 stored
real-vehicle rows and produced `research/measurement_discovery/implementation_impact.md/.csv`
(453 changed, 17 newly decoded, 0 now-failing). The DB was **not** rewritten —
raw bytes remain the source of truth.

**Dashboard charts (2026-10-21, not a decoder change):**

- The CSP allowed the Chart.js CDN but not inline scripts, so every template's
  `new Chart(...)` block was refused by the browser: the server looked healthy
  and no chart ever drew. All chart JS now lives in `web/static/`, and Chart.js
  is vendored there (4.5.1, MIT, SHA-256 in `web/static/vendor/README.md`). The
  policy is `script-src 'self'` with no CDN.
- Charts use a real time axis, break the line when samples are further apart
  than `collector.max_value_age_s` (a gap is drawn as a gap), label units, and
  show an explicit "no readings" state. Text/code signals render as a state
  timeline. Measurements gained a window selector; Overview, Battery, and
  Progress gained charts.

---

## 8. Test, lint, and CI status

- **Tests:** 765 passing (`python -m pytest tests -q`); 748 in the fast subset
  (`-m "not slow"`). No car or Ollama needed.
- **Lint:** `ruff check .` passes clean. Two rules are intentionally ignored in
  `pyproject.toml` (`E402`, `B905`).
- **CI:** `.github/workflows/ci.yml` runs lint, fast tests, full suite, smoke
  test, and `guard-test` on Python 3.11–3.14.
- **Android parity:** `android/.../spec/Decode.kt` mirrors the Python decoders and
  `DecodeTest.kt` pins them against the same vectors. Verified passing on this
  machine (`gradlew :app:testDebugUnitTest` → 9 tests, 0 failures; JDK is
  Android Studio's bundled JBR at `C:\Program Files\Android\Android Studio\jbr`).

---

## 9. Open / blocked items (the "what's next" list)

Every remaining item is blocked on **hardware or approval**, not on code:

| Item | Status | Unblocked by |
|---|---|---|
| Live validation capture | 2026-10-21 session archived (35/35 plausibility); strict archive 5/5 with independent values | Next capture, then `export_validation_capture.py` → `validate_measurement_capture.py`; add `expected` only from an independent display |
| Gateway `[19.Gate]` scan | Built, tested, dry-run verified; real-car run pending | Adapter + car; `python tools/scan_gateway.py` (exit 2 on any NRC/no response) |
| Known-state payload tests (`0x27C4`, `0x42DB`, `0x5867`, `0xF449`) | Prepared, blocked | Car + safe states; exact commands in `docs/NEXT_STEPS_20261022.md` |
| ABS wheel speeds `0x1800–0x1803` | Decoders + approval-gated reader ready; **not executed** | Separate approval for `0x10 03` on `0x713`; run `--dry-run` first, then `--execute --approve-session-change` |
| Gateway `0x2A26`/`0x2A28`/`0x2A2F` | Payloads recovered and archived; no profile layout/scale | A documented layout or a controlled test that proves a scale; do not blind-probe |
| Quarantined keys (`co2_content_interior`, `windshield_temperature`) | Decoders implemented; keys still invalid | Fresh-air/occupied and cold-soak/defroster tests |
| `odometer_2`, `bat_charging_voltage`, `dc_dc_converter_reserved_current` | Decoded honestly but excluded from scoring; offset known | Known-distance drive/service history, charging session, and known-load capture respectively |
| Direct cluster lamp/tell-tale DID | Closed for now: no documented DID; no capture traffic | A documented read-only cluster allow-list, not a sweep; DTC bit 7 remains the signal |
| GPS `0x2430`/`0x22B3`, HVAC `0x263B`, PTC/seat DIDs | Backlog | One live read-only state test each |

The live-capture protocol is written down in
`research/measurement_discovery/validation_capture_protocol.md`; the checker
(`tools/validate_measurement_capture.py`) and the extractor
(`tools/export_validation_capture.py`) make it a two-command job once the car is
present. The archived 35-record run passes 35/35 under the normal and `--strict`
checkers, but it has no independent `expected` fields; the separate five-record
strict archive supplies those for the two energy DIDs, SoC, pack voltage, and
odometer, and passes 5/5. `strict` mode now records the expected value in its
report, so a PASS is visibly tied to it.

---

## 10. Where the key decisions and evidence live

- `research/measurement_discovery/decoder_audit.md` — per-finding raw evidence and
  decoder rationale (including the byte-2-is-always-zero index note).
- `research/measurement_discovery/final_report.md` — findings + implementation
  context.
- `research/measurement_discovery/validation_roadmap.md` — phased validation plan,
  abort criteria, approval checklist, and the implemented/deferred status.
- `research/measurement_discovery/implementation_impact.md/.csv` — old-vs-new
  decode impact over 8,134 stored rows.
- `research/measurement_discovery/payload_research_20261021.md` — the unknown
  payload dossier, raw vectors, proposed tests, and the 2026-10-22
  implementation status.
- `tools/audit_payload_findings.py` — read-only replay of the payload findings
  against `data/odb_scanner.db` (opened `mode=ro`; never touches the car).
- `docs/NEXT_STEPS_20261022.md` — the 20 prioritized steps, with done/blocked
  status and the exact commands for the next car session.
- `docs/investigation/meb_gateway_energy_passive_capture_20261015.md` — the
  passive HCI capture that confirmed gateway energy addressing/scales, plus the
  sanitised archive hashes.
- `research/measurement_discovery/captures/passive_capture_evidence_20261015.json`
  + `.manifest.json` — 278 sanitised CAN/DID records and DB evidence; raw log
  not committed.
- `research/measurement_discovery/captures/strict_expected_values_20261022.json`
  + `.manifest.json` — five independent expected values; 5/5 PASS.
- `tools/mine_passive_capture.py` — ISO-TP reassembly from a local btsnoop.
- `tools/read_abs_wheel_speeds.py` — approval-gated ABS reader (dry-run default;
  exact `1003`; always attempts `1001`).
- `research/measurement_discovery/sources/` — spot2000 CSV and ABRP JSON
  (third-party references).

---

## 11. Key limitations and gotchas

- **Unverified scale factors** are labelled `experimentally determined` everywhere;
  they come from reverse engineering, not a VW document. Raw bytes are always kept
  so decoders can be corrected later without re-collecting.
- **29-bit modules intermittently go silent** (BMS, eldrive, veh_info, dcdc) for a
  whole rotation; values are then carried forward up to `collector.max_value_age_s`
  (900 s). Prefer each row's `ts` over assuming it is fresh.
- **Simulated/demo data is walled off by file, VIN, and provenance** — see the
  README's table of which run writes which DB, and `tools/purge_synthetic_data.py`
  if they ever mix.
- **`0x1E39` `0xFFFF` is a sentinel**, not 2,047.97 V. The decoder now returns
  `None` for `0xFFFF`/`0xFFFE` and negatives; a missing charging voltage is not
  a fault by itself. Historical rows keep the old unsigned value.
- **`0x5867` is a 10 km/LSB counter, not the odometer.** It sits a constant
  99,869–99,871 km below the main odometer on this car, consistent with a BMS
  reset/replacement at ~99,870 km; the absolute zero and the reset explanation
  still need service history. Do not use it for distance, SOH, or service
  intervals. The four agreeing odometers (`0x02BD`, `0x295A`, `0xF817`,
  `eldrive_odometer`) are the distance evidence.
- **`0x42DB` `03FE` is a sentinel, not a ppm reading.** It appears in 40/40
  stored rows and in the unpopulated `cell_t_18`–`cell_t_23` slots. The decoder
  returns `None`; do not show 1,022 or 25,400 ppm.
- **`0x27C4` uses byte 0 × 0.1 °C, not the profile's u16 formula.** The key is
  still quarantined because the cold-soak/defroster test has not separated
  windshield from ambient. The remaining bytes are not decoded.
- **The ABS tool is the only session-change exception.** The ordinary guard
  still refuses `0x10 0x03`; the tool requires both `--execute` and
  `--approve-session-change`, sends exactly one `1003`, and always attempts
  `1001`. It has not been run. Do not run it without separate approval.
- **Never commit the raw btsnoop.** It contains unrelated phone/SMS/XML data.
  Use `tools/mine_passive_capture.py` and commit only the sanitised CAN/DID
  payload archive.
- **`accelerator_pedal_pct` is an alias for `res_pid_0149`**, not a second
  measurement. API and chart lookups resolve it to the stored key.
- **The LLM is not a diagnosis** — it ranks hypotheses with confidence; a
  validator flags over-confident wording.
- **Dashboard network exposure:** the default bind is `0.0.0.0` with an access
  token; bind `127.0.0.1` for no token. No CSRF, plain HTTP — LAN/homelab only.

---

## 12. Handoff checklist

- [ ] Run `python -m pytest tests -q` — expect 765 passed.
- [ ] Run `python main.py guard-test` — confirms the read-only guard.
- [ ] Run `python tools/smoke_test.py` — confirms the whole pipeline without a car.
- [ ] Run `python tools/audit_payload_findings.py` — re-checks the payload
  findings against the stored DB, read-only (no car required).
- [ ] Run `python tools/validate_measurement_capture.py --in research\measurement_discovery\captures\strict_expected_values_20261022.json --strict`
  — expect 5/5 PASS with non-null expected values.
- [ ] Run `python tools/read_abs_wheel_speeds.py --dry-run` — prints the plan and
  does **not** open an adapter. Do not use `--execute` without separate approval.
- [ ] Read `diagnostic/uds.py` header (safety) and `research/measurement_discovery/validation_roadmap.md` (what's blocked).
- [ ] Read `research/measurement_discovery/decoder_audit.md` before touching any decoder.
- [ ] For the live capture: get explicit owner approval, then follow `validation_capture_protocol.md`.
- [ ] Never copy the raw btsnoop into the repo; use the miner and commit only the
  sanitised archive.
