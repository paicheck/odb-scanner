# Commit-readiness audit — post-discovery integration (2026-10-26)

**Scope:** static, offline integration audit of **three** concurrent workstreams
— (1) the measurement-discovery pass (two new gateway DIDs), (2) a
decoder-accuracy/replay-analysis pass (replay text-row fix + Android gear fix +
audit documentation), and (3) a dashboard-investigation pass (timeline/event/
DTC drill-down + a `cell_voltage_sum` classification fix) — against the current
repository state.

**Safety:** no vehicle communication, no diagnostic request, no write/session
change/security access/routine/actuator test, no fault clearing. No commit,
push, reset, clean or discard. Read-only guard unchanged.

---

## 1. Executive summary

The repository holds three non-overlapping workstreams that integrate cleanly.
The full offline test suite passes (**793**), lint is clean, the shared
specification is reproducible and byte-identical between Python and Android,
the catalogue validates, and the read-only guard is byte-for-byte unchanged.

**Verdict: ready to commit, with the two recommendations in §11** (remove the
throwaway `dbpeek.py`; treat the two new DIDs as PROVISIONAL and do not claim
they establish SoH or 12 V battery failure without a validated interpretation).

Both new DIDs are PROVISIONAL (third-party definition + already-verified module
addressing), correctly **not** VERIFIED. Passing unit tests and a matching
generated spec are not vehicle-level validation, and this report does not treat
them as such.

The dashboard pass is additive and read-only (new web pages and a read-only
`analysis/context.py` query; the `cell_voltage_sum` fix correctly reclassifies a
real DID 0x1E3C from DERIVED to NUMERIC). It touches no file that the other two
passes modify.

---

## 2. Exact current Git status

Branch `main`, HEAD `bd86d55 Add production cloud deploy stack` (2026-10-26).

Modified (tracked):

| File | Lines | Workstream |
|---|---|---|
| `.gitignore` | +1 | pre-existing (adds `cloud/cpanel.env`) |
| `analysis/replay.py` | +10/-1 | decoder-accuracy (text-row fix) |
| `android/.../assets/spec.json` | +50 | discovery (spec regen) |
| `android/.../spec/Decode.kt` | +19/-6 | decoder-accuracy (gear fix) |
| `android/.../spec/DecodeTest.kt` | +16 | decoder-accuracy (gear test) |
| `database/repository.py` | +11 | dashboard (DTC lookup) |
| `decoders/catalogue.py` | +64 | discovery (2 overlay entries) |
| `decoders/meb.py` | +38 | discovery (2 DIDs) |
| `decoders/quality.py` | +1/-1 | dashboard (`cell_voltage_sum` fix) |
| `spec/odb_scanner.json` | +50 | discovery (spec regen) |
| `tests/test_catalogue.py` | +25 | decoder-accuracy (replay test) |
| `tests/test_platform.py` | +13 | dashboard (quality fix test) |
| `web/dashboard.py` | +78 | dashboard (timeline/event/DTC pages) |

Untracked:

| Path | Workstream |
|---|---|
| `cloud/{CPANEL.md,cpanel.env.example,passenger_wsgi.py,requirements-cpanel.txt}` | pre-existing |
| `analysis/context.py` | dashboard (new) |
| `docs/investigation/commit_readiness_audit_20261026/` | this audit |
| `docs/investigation/dashboard_improvement/` | dashboard |
| `docs/investigation/decoder_accuracy_audit_20261026/` | decoder-accuracy |
| `research/autonomous_discovery_20261026/` | discovery |
| `tests/test_gateway_resistance.py` | discovery |

No file is modified by more than one workstream; the three passes touch
disjoint paths.

---

## 3. Files changed, grouped by workstream

**Discovery pass (measurement-discovery):**
- `decoders/meb.py` — `bat_internal_resistance_raw` (0x2A25) and
  `aux_battery_resistance_mohm` (0x2A0E), both `slow=True`, `EXPERIMENTAL`.
- `decoders/catalogue.py` — two `OVERLAY` entries (PROVISIONAL,
  `THIRD_PARTY_DEFINITION`, `address_confidence=VERIFIED`).
- `tests/test_gateway_resistance.py` — new, 14 offline tests.
- `spec/odb_scanner.json` + Android asset — regenerated.
- `research/autonomous_discovery_20261026/` — progress/candidate/evidence/report.

**Decoder-accuracy / replay-analysis pass:**
- `analysis/replay.py` — skip text-valued rows in `observations_from_measurements`.
- `tests/test_catalogue.py` — regression test for that fix.
- `android/.../Decode.kt` + `DecodeTest.kt` — gear decoder aligned to Python.
- `docs/investigation/decoder_accuracy_audit_20261026/` — audit scripts
  (`inventory.py`, `lab_sweep.py`, `dbpeek.py`) and generated reports
  (`impact_odb.*`, `impact_real_car.*`, `inventory_baseline.json`,
  `lab_sweep.json`).

**Dashboard-investigation pass:**
- `analysis/context.py` (new) — read-only `measurement_context()` query.
- `database/repository.py` — `Repository.dtc(dtc_id)` lookup.
- `decoders/quality.py` — remove `cell_voltage_sum` (real DID 0x1E3C) from
  `_DERIVED_KEYS`; keep the plural `cells_voltage_sum` (imported alias) derived.
- `web/dashboard.py` — new `/timeline`, `/event`, `/dtcs/{id}` pages and
  event-anchored investigation creation.
- `tests/test_platform.py` — regression test for the quality fix.
- `docs/investigation/dashboard_improvement/` — progress log.

**Pre-existing (unrelated, preserved):** `.gitignore`, `cloud/*`.

---

## 4. DID-by-DID evidence and confidence

### 4.1 `bat_internal_resistance_raw` — DID 0x2A25

| Aspect | Finding | Confidence |
|---|---|---|
| Identifier/ECU | gateway 0x710→0x77A (11-bit) | STRONGLY INFERRED |
| Source definition | OBDb signalset v3 `ID3_BAT_INTERNAL_RES_RAW`: `{len:8, max:51, div:5, unit:"unknown"}` | third-party |
| Model-year applicability | OBDb lists "confirmed for MY2021"; this VIN (…`M`P08…) is MY2021 | STRONGLY INFERRED (not this project's capture) |
| Addressing evidence | 0x710→0x77A VERIFIED by 2026-10-15 passive capture | VERIFIED |
| Payload/offset | u8 at offset 0, unsigned, big-endian (single byte) | STRONGLY INFERRED |
| Scaling/unit | raw / 5, unit "unknown" (raw index 0–51, **not** a physical resistance) | PROVISIONAL |
| Sentinel handling | short payload → `decode_value` returns `None` (IndexError); no sentinel defined by source | n/a |
| Decoder registration | `_cs_field_decoder(sbi=0, dl=1, mul=1.0, div=5.0)` | — |
| Catalogue | PROVISIONAL, `THIRD_PARTY_DEFINITION`, `address_confidence=VERIFIED`, range 0–51 | correct |
| Python/spec/Android | scalar recipe `u8be[0:1] unsigned x 1 / 5`, generic Android scalar path | consistent |

**Key distinction:** the external definition is *not* proof the signal has been
read and validated on this vehicle. It remains PROVISIONAL. It must not be
used to assert battery state of health or degradation without a validated
interpretation (the source itself labels the unit "unknown").

### 4.2 `aux_battery_resistance_mohm` — DID 0x2A0E

| Aspect | Finding | Confidence |
|---|---|---|
| Identifier/ECU | gateway 0x710→0x77A (11-bit) | STRONGLY INFERRED |
| Source definition | OBDb `ID3_SSB_R`: `{len:8, max:51, mul:2, div:10, unit:"milliohms"}` | third-party |
| Model-year applicability | OBDb "confirmed for MY2020-2021…" | STRONGLY INFERRED (not this project's capture) |
| Addressing evidence | 0x710→0x77A VERIFIED (passive capture) | VERIFIED |
| Payload/offset | u8 at offset 0, unsigned | STRONGLY INFERRED |
| Scaling/unit | raw × 2 / 10 mΩ, range 0–10.2 mΩ (physically plausible for an AGM 12 V battery) | PROVISIONAL |
| Sentinel handling | short payload → `None`; no sentinel defined | n/a |
| Decoder registration | `_cs_field_decoder(sbi=0, dl=1, mul=2.0, div=10.0)` | — |
| Catalogue | PROVISIONAL, `THIRD_PARTY_DEFINITION`, `address_confidence=VERIFIED`, range 0–10.2 | correct |
| Python/spec/Android | scalar recipe `u8be[0:1] unsigned x 2 / 10` | consistent |

Same distinction applies: **not** VERIFIED; must not be used to claim a 12 V
battery failure without a validated interpretation and a known-load capture.

Neither DID is upgraded to VERIFIED. The PROVISIONAL classification is correct.

---

## 5. Decoder discrepancy assessment

None of these is changed in this audit; each needs a known-state capture.

| # | DID/Key | Current impl (registry) | Conflicting evidence (OBDb) | Confidence | Offline test possible | Falsifies current impl | Change now? |
|---|---|---|---|---|---|---|---|
| D1 | 0x2613 `inside_temp` | `u16be/5 − 40` (evDash) | `s16/10` | UNRESOLVED | no (needs raw + known temp) | a capture whose value is only plausible under `s16/10` | No |
| D2 | 0x189D `coolant_temps` | unsigned `u16/64` | signed `u16/64` | UNRESOLVED (low impact: coolant is always positive) | no | a signed-negative coolant reading | No |
| D3 | 0x1E0E/0x1E0F `bat_temp_max/min` | unsigned `u16/64` | signed `u16/64` | UNRESOLVED (low impact) | no | a signed-negative battery temperature | No |
| D4 | 0x27C4 `windshield_temperature` | byte0 ×0.1 (matches observed ~12 °C) | byte0 ×0.576−38.8 (≈31 °C for 0x7A); Car Scanner u16×15.625−39.5 already refuted | UNRESOLVED (registry best-supported by observed data; key quarantined) | partial (correlate byte0 vs outside_temp) | a cold-soak/defroster test where byte0 does not track windshield/ambient | No |
| D5 | 0x42DB `co2_content_interior` | whole-u16 ppm, `03FE`→None sentinel | byte1 ×100 (OBDb and Car Scanner) | sentinel handling STRONGLY INFERRED; non-sentinel scale UNRESOLVED | partial (enumerate stored `03FE`) | a fresh-air/occupied test where a non-sentinel value does not move in 400–2000 ppm | No |
| D6 | 0x028C `soc_normal` | `raw×0.4425−6.1947` (evDash) | `raw×20.4/46−6.4` (OBDb) | UNRESOLVED (both reproduce ~83.6 % at raw 203; functionally equivalent) | no | either fit failing to match the dash at a second point | No |

**Conclusion:** no code change is justified now. The discrepancies are
documentation/interpretation gaps that only a known-state capture can close.

---

## 6. Concurrent-change integration results

### 6.1 `analysis/replay.py` — text-row crash fix

`observations_from_measurements` now wraps `float(value)` in
`try/except (TypeError, ValueError)` and `continue`s on non-numeric rows. This
correctly skips `gear` ("P") and `vin` rows without aborting the replay, while
preserving numeric rows (`float` of a numeric string or REAL both succeed).
**Correct and complete.** The companion test
`tests/test_catalogue.py::test_text_valued_rows_are_skipped_not_crashed_on`
proves the skip and the survival of a numeric row. It handles realistic
text-valued and malformed rows without breaking numeric replay.

### 6.2 Android gear decoder

`Decode.kt::gearCode` now reads data byte 1 unsigned (`record[1].toInt() and 0xFF`)
and maps 8=P, 7=R, 6=N, 5=D, 12=B, with fallback `"raw " + <whole-record hex>`.
This **matches** `decoders/meb.py::_gear` (`_GEAR_CODES = {6:"N", 5:"D", 12:"B",
8:"P", 7:"R"}`, reads `raw[1]`, fallback `raw.hex().upper()`). The prior Kotlin
map (0x04/0x10/0x20) was genuinely incompatible. **Verified consistent**, not
merely matching a possibly-incorrect implementation. `DecodeTest.kt` pins the
agreement.

### 6.3 Decoder-audit documentation and scripts

- `impact_odb.md` / `impact_real_car.md` (+ CSV): read-only re-decode of stored
  bytes; correct "the DB is not rewritten" discipline. Generated before the two
  new DIDs, so their exact "keys with no stored rows" counts are slightly stale
  (point-in-time snapshots). Not a functional issue.
- `inventory.py`, `lab_sweep.py`: read-only audit scripts (`mode=ro` and
  copy-to-temp respectively). Safe. `inventory.py` has a trivial
  `uncertain_layout`/`Counter` lint history that is now resolved.
- `dbpeek.py`: 9-line throwaway that (a) opens `data/odb_scanner.db` **without**
  `mode=ro` (only SELECTs, but inconsistent with the read-only discipline) and
  (b) queries non-existent columns `value_numeric`/`value_text` (the real
  columns are `value`/`text_value`). It is not meant to be committed — see §11.

### 6.4 Dashboard-investigation pass

Reviewed as a third, additive workstream.

- `analysis/context.py` — `measurement_context()` is a bounded, read-only
  `SELECT` that returns each value with its real observation timestamp, unit and
  provenance and a signed `delta_s` from the anchor; it explicitly does **not**
  interpolate or re-stamp. Safe.
- `database/repository.py` — `Repository.dtc(dtc_id)` is an additive, read-only
  lookup, consistent with the existing `investigation()` method.
- `decoders/quality.py` — removing `cell_voltage_sum` (the real DID 0x1E3C) from
  `_DERIVED_KEYS` while keeping the plural `cells_voltage_sum` (imported alias)
  is a **correct** fix: 0x1E3C is read from the car, not calculated. Verified:
  `quality_for("meb","cell_voltage_sum").data_kind` is now NUMERIC while the
  plural stays DERIVED. Net quality-registry effect: derived 10→9, numeric
  279→282 (includes the two new discovery DIDs).
- `web/dashboard.py` — new `/timeline`, `/event`, `/dtcs/{id}` routes and
  event-anchored investigation creation are read-only HTML pages; integer query
  params are bounded (`_int_param` clamps and falls back) so a malformed query
  cannot 500 or open an unbounded query. No new vehicle path.
- `tests/test_platform.py` — the added test pins the `cell_voltage_sum`
  classification and passes.

No safety impact: all additions read SQLite only.

**Note — this workstream is still active.** During this audit it continued to
add files consistent with its declared scope: the templates
`web/templates/{timeline,event,dtc_detail}.html` (new) and `base.html`/`dtcs.html`
(modified), and a small `analysis/dtc.py` change that adds the DTC row `id` to
the summary so the drill-down can link to it. All are benign read-only
additions (Jinja2 rendering + an additive field). The audit below is therefore
a point-in-time snapshot; `ruff check .` still passes after these additions.

---

## 7. Shared-spec and Android consistency

- **Reproducible:** `tools.export_spec.build_spec()` equals the committed
  `spec/odb_scanner.json` (ignoring `generated_at`). VERIFIED by direct check.
- **Byte-identical:** `spec/odb_scanner.json` and
  `android/app/src/main/assets/spec.json` have identical SHA-256. VERIFIED.
- **Catalogue/decoder agreement:** `validate_catalogue("meb") == []` and
  `validate_catalogue("eup") == []`. VERIFIED.
- **PROVISIONAL preserved:** the two new DIDs are PROVISIONAL with
  `THIRD_PARTY_DEFINITION`; only `hv_energy_content`/`hv_energy_max` remain
  VERIFIED (guarded by `test_only_the_two_captured_energy_dids_are_verified`).
- **eup unaffected:** eup measurement count unchanged (122).

---

## 8. Read-only safety findings

- `diagnostic/` is byte-for-byte unchanged (no diff in `uds.py`, `ecus.py`,
  `elm327.py`, `obd2.py`, `connection.py`, `discovery.py`).
- Allowed services remain `{0x01, 0x10, 0x19, 0x22, 0x03, 0x3E, 0x09}`; blocked
  services 18; `0x10` sub-function still `{0x01}` only; `0x19` sub-functions
  `{0x01..0x09}`. The two new DIDs use only service `0x22`.
- No new automatic vehicle-communication path: the new DIDs join the existing
  slow rotation of the already-addressed `energy` module via the existing
  allow-list, with the existing auto-retire-on-NRC-0x31 mechanism.
- No unsafe test was executed; the full suite uses fakes, loopback TCP sockets
  and temp databases, never a real adapter. The `hardware` marker is defined but
  currently unused (no test is marked `hardware`).
- Authentication/privacy/data-integrity boundaries are untouched: `web/auth.py`
  (the token/cookie gate), `config.py`, the CSP (`script-src 'self'`), `cloud/`
  committed sources, and the Android manifest are all unchanged. The dashboard
  pass adds only read-only HTML pages (`web/dashboard.py` + templates) that read
  SQLite; no auth or CSP change.

---

## 9. Exact test and lint commands with results

| Command | Result |
|---|---|
| `python -m pytest tests -q` (combined, current state) | **793 passed** (212 s) |
| `python -m ruff check .` | **All checks passed** |

Test growth: 777 baseline → **793** = 14 (discovery) + 1 (decoder-accuracy
replay test) + 1 (dashboard `cell_voltage_sum` test).

The earlier reported Ruff failures (`dbpeek.py` I001; `inventory.py` F401
`Counter`, F841 `uncertain_layout`; plus one B017 in the discovery test) are all
resolved in the current tree — the concurrent pass fixed its scripts and the
discovery pass fixed its test. Nothing was hidden via `noqa` or a widened
exclude; `ruff check .` passes on the current files with the existing
configuration.

Test breakdown: no failures, no skips, no hardware tests run (none exist under
the `hardware` marker). The `slow` tests run against loopback sockets only.

---

## 10. Unresolved issues and their risk

0. **Active concurrent development.** The dashboard-investigation pass was still
   adding files during this audit (templates, an `analysis/dtc.py` id field).
   This report is a point-in-time snapshot; the exact file list and test count
   may move as that pass continues. The core findings (DIDs, discrepancies,
   replay/gear fixes, spec/safety) are unaffected by those additions.
1. **The two new DIDs are PROVISIONAL, not VERIFIED** (risk: low; a mis-scaled
   value is bounded by the plausible-range check and the raw bytes are always
   preserved for re-decode).
2. **`dbpeek.py` opens the real DB read-write and queries non-existent columns**
   (risk: low; it only SELECTs, but it is a broken throwaway that should not
   ship).
3. **Six decoder discrepancies remain UNRESOLVED** (risk: low; the affected keys
   are either quarantined/invalid, or the sign discrepancy is immaterial for
   the physically positive quantities involved).
4. **Concurrent audit reports are point-in-time and predate the two new DIDs**
   (risk: documentation staleness only).
5. **OBC (0x744) unreachable** and **BMS CAN-broadcast measurements out of
   scope** — pre-existing, unchanged, not introduced by any pass.

---

## 11. Commit-readiness recommendation

**Ready to commit**, subject to two recommendations:

1. **Remove (or move out of the tree) `docs/investigation/decoder_accuracy_audit_20261026/dbpeek.py`**
   before commit. It is a broken, throwaway schema peek that opens the real
   database without `mode=ro`. `inventory.py` and `lab_sweep.py` are safe and
   may be committed as read-only audit scripts, but consider relocating them to
   `tools/` (where `replay_decoder_impact.py` and `audit_payload_findings.py`
   already live) to avoid future lint surprises in `docs/`.
2. **Keep both new DIDs PROVISIONAL** and record (in the discovery report, which
   already does) that they must not be used to assert SoH, battery degradation
   or 12 V battery failure without a validated interpretation.

The three workstreams are independent and can be committed together or
separately; none depends on the others.

---

## 12. Prohibited / human-gated future tests (not performed)

- **Known-state capture of 0x2A25 / 0x2A0E** — real car, read-only `0x22` on
  0x710→0x77A. Requires owner approval.
- **Six discrepancy tests** — one known-state read each (0x2613 at known
  ambient, 0x189D/0x1E0E/0x1E0F cold-soak, 0x42DB fresh-air vs occupied, 0x27C4
  cold-soak/defroster, 0x028C cross-check).
- **OBC reachability** — blocked until 0x744 answers (root cause separate).
- **BMS CAN-broadcast isolation/contactor/balancing** — raw CAN capture, a
  different transport and approval.
- **ABS wheel speeds 0x1800–0x1803** — separate `0x10 03` approval (unchanged).

None of these was executed; no vehicle communication occurred.
