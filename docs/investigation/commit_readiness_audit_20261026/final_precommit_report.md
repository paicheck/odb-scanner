# Final tree-freeze and pre-commit verification report (2026-10-26)

**Final verification timestamp:** 2026-10-26 20:50 UTC.
**Scope:** bring the repository to a verified, stable pre-commit state, close
the decoder-accuracy workstream, investigate the production database
modification, and record a readiness verdict. **Nothing was staged or
committed.**

**Safety:** no vehicle communication, no ELM/OBD tool, no diagnostic request,
no session change, no fault clearing, no security access / routine / actuator /
programming. Only offline tests, fakes, loopback sockets and temp databases.
No process was stopped or restarted.

---

## 1. Workstream completion status — all three complete

| Workstream | State | Evidence |
|---|---|---|
| **Discovery** (DIDs 0x2A25/0x2A0E) | **Complete** | `decoders/meb.py`, `decoders/catalogue.py`, `tests/test_gateway_resistance.py` (14 tests), `spec/odb_scanner.json`, `research/autonomous_discovery_20261026/` (progress/candidate/evidence/final) |
| **Decoder-accuracy / replay** | **Complete** | fixes F1/F2/F3 + tests; `findings.md`, `evidence_register.md`, `final_report.md` all present; `progress.md` has a "Close-out" section |
| **Dashboard-investigation** | **Complete** | `final_report.md` written 20:38:14; `analysis/context.py`, `web/dashboard.py`, templates, `tests/test_timeline_investigation.py` (12 tests) present |

The tree is now **quiescent**: the last source-file write was the
decoder-accuracy `progress.md` at 20:46:28, and the only post-20:46 activity is
the gitignored generated file `docs/experiment_record_sim.md` (simulator
self-test output). Two long-lived Python processes remain running
(PID 15056 — the dashboard server holding the DB open; PID 19076/7888), but
they are no longer modifying committed source.

---

## 2. Exact test and lint results

| Command | Result |
|---|---|
| `python -m pytest tests -q` | **805 passed** (150 s) |
| `python -m ruff check .` | **All checks passed** |
| `build_spec() == spec/odb_scanner.json` (ignoring `generated_at`) | reproducible |
| `spec/odb_scanner.json` vs Android asset | byte-identical (SHA-256 `483054ed324a36c3…`) |
| `validate_catalogue("meb")` / `validate_catalogue("eup")` | `[]` / `[]` |
| read-only allow-list | unchanged — allowed `{0x01,0x10,0x19,0x22,0x03,0x3E,0x09}`, 18 blocked, `0x10`→`{0x01}`, `0x19`→`{0x01..0x09}` |
| `diagnostic/` diff | absent (guard byte-for-byte unchanged) |

Evidence classifications preserved:
- `0x2A25` and `0x2A0E` remain **PROVISIONAL** (`THIRD_PARTY_DEFINITION`,
  `address_confidence=VERIFIED`); only `hv_energy_content`/`hv_energy_max` are
  VERIFIED. Neither new DID is asserted to prove SoH or a 12 V battery fault.
- The six decoder discrepancies (0x2613, 0x189D, 0x1E0E/0x1E0F, 0x27C4, 0x42DB,
  0x028C) remain **UNRESOLVED**; no code change was made.
- The five INVALID keys remain INVALID (pending car evidence).

Test growth: 777 baseline → **805** = 14 (discovery) + 1 (replay) + 1
(quality-fix) + 12 (dashboard timeline/investigation). The decoder-accuracy
workstream also reports the Android JVM suite offline: **52 tests, 0 failures**
(`DecodeTest` 11).

---

## 3. Database integrity findings

Investigated `data/odb_scanner.db` (the pre-commit report had flagged an mtime
change at 20:33:49). Findings, verified read-only:

- `data/real_car.db` — **unchanged** (integrity `ok`; 4,057 measurements).
- `data/odb_scanner.db` — integrity `ok`; 20,798 measurements / 1 vehicle /
  46,530 tx_log rows. It is currently held open by a long-running dashboard
  server (PID 15056), which maintains a `-wal`/`-shm` pair and makes the
  main-file byte hash unstable.
- The mtime changes are **free-space/header page bookkeeping**, not content
  edits. The decoder-accuracy audit's table-by-table comparison against its
  20:24 working copy found **zero schema and zero row differences**, and a
  pre-WAL page comparison found only pages 1–2 differ. (`evidence_register.md`
  E17.)
- **Conclusion:** no logical database content was changed by any audit; the
  earlier mtime observation is explained by SQLite WAL/free-page bookkeeping
  from the concurrent dashboard server. Residual uncertainty: the exact writer
  is attributed to that server as a caveat, not as a proven fact. No
  production-database write, restore, or delete was performed by this audit.

---

## 4. Final changed-file summary by workstream

**Discovery (complete):**
- `decoders/meb.py` (+38), `decoders/catalogue.py` (+64) — two PROVISIONAL DIDs.
- `tests/test_gateway_resistance.py` (new, 14 tests).
- `spec/odb_scanner.json` + `android/.../assets/spec.json` (regenerated).
- `research/autonomous_discovery_20261026/`.

**Decoder-accuracy / replay (complete):**
- `analysis/replay.py` (+10/-1) — text-row skip fix (F2).
- `tests/test_catalogue.py` (+25) — regression test.
- `android/.../Decode.kt` (+19/-6), `DecodeTest.kt` (+16) — gear fix (F1) + test.
- `docs/investigation/decoder_accuracy_audit_20261026/` — scripts
  (`inventory.py`, `lab_sweep.py`, `inventory_md.py`, `numeric_audit.py`),
  generated reports, `findings.md`, `evidence_register.md`, `final_report.md`,
  `progress.md` (Close-out).

**Dashboard-investigation (complete):**
- `analysis/context.py` (new), `database/repository.py` (+11),
  `decoders/quality.py` (+1/-1) — `cell_voltage_sum` fix (F3),
  `web/dashboard.py` (+78), `tests/test_platform.py` (+13),
  `tests/test_timeline_investigation.py` (new, 12 tests),
  `web/templates/{base,dtcs}.html` (mod), `timeline,event,dtc_detail}.html` (new),
  `docs/investigation/dashboard_improvement/`.

**Pre-existing (preserved):** `.gitignore`, `cloud/*`.

**This audit:** `docs/investigation/commit_readiness_audit_20261026/`.

No file is touched by more than one workstream. No new vehicle-communication
path exists: the dashboard additions are read-only SQLite consumers; the new
DIDs join the existing `energy` module's slow rotation via the existing `0x22`
allow-list with the existing auto-retire-on-NRC-0x31 mechanism.

---

## 5. Unresolved issues and limitations

1. **Stale `dbpeek.py` reference.** This audit removed the broken throwaway
   `docs/investigation/decoder_accuracy_audit_20261026/dbpeek.py` (it opened the
   real DB without `mode=ro` and queried non-existent columns). The
   decoder-accuracy `progress.md` "Artefacts produced" table still lists it;
   that single line is now stale and can be dropped by the author at commit.
2. **Six decoder discrepancies** and the **five INVALID keys** remain unresolved,
   pending known-state captures (explicitly not performed).
3. **OBC (0x744) unreachable** and **BMS CAN-broadcast measurements out of
   scope** — pre-existing, unchanged.
4. **Two long-lived processes** (PID 15056 dashboard server, PID 19076/7888)
   remain running; left untouched per instruction. They no longer modify
   committed source.
5. **Generated investigation artefacts** (`lab_sweep.json` ~70 KB,
   `numeric_audit.json`, `decoder_inventory.md`, impact CSVs) are regenerable;
   committing them is a human decision (the producing scripts are committed
   alongside).

---

## 6. Readiness verdict

**Ready for human review and commit.**

- All three workstreams are complete and disjoint by file.
- The full suite passes (**805**), lint is clean, the spec is reproducible and
  byte-identical to the Android asset, catalogues validate, and the read-only
  guard is byte-for-byte unchanged.
- The production database content is intact (integrity `ok`; the observed mtime
  change was benign WAL/free-page bookkeeping, not a content edit).
- No vehicle interaction or prohibited diagnostic action occurred; no process
  was stopped or restarted.

Suggested commit hygiene (for the human, not performed here):
1. Drop the stale `dbpeek.py` line from the decoder-accuracy ledger.
2. Decide whether to commit the regenerable `.json`/`.csv` audit output.
3. Commit the three workstreams as separate commits (they are disjoint by
   file), then re-run `pytest tests -q` and `ruff check .` on the committed
   tree as a final gate.

Nothing was staged or committed.

---

## 7. Commit closeout (2026-10-26)

### 7.1 Stale ledger entry corrected

The decoder-accuracy ledger (`docs/investigation/decoder_accuracy_audit_20261026/`)
still listed the removed throwaway `dbpeek.py` in its artefact table and in the
final report's artefact list. Both stale references were removed (the file
itself was already absent). No other artefact was renamed or deleted.

### 7.2 Audit artefacts — included vs excluded

The entire `docs/investigation/decoder_accuracy_audit_20261026/` directory was
committed: the four reusable read-only scripts, the human-readable reports, and
the generated JSON/CSV evidence. The JSON/CSV were included because they capture
point-in-time evidence against a database state that has since changed (so they
are not cheaply reproducible), and because they are referenced by the reports.
A secret/VIN scan found no credentials, tokens, personal data, or real VIN in
any staged file (`__pycache__` is gitignored).

### 7.3 Commits created

| Hash | Message | Files |
|---|---|---|
| `a05a781` | `Fix decoder accuracy and replay handling` | replay text-row fix (F2), Android gear parity (F1), `cell_voltage_sum` classification fix (F3), their regression tests, and the full decoder-accuracy audit package (25 files) |
| `011d2eb` | `Add provisional MEB gateway resistance measurements` | DIDs 0x2A25/0x2A0E decoders + catalogue overlays, `tests/test_gateway_resistance.py`, regenerated `spec/odb_scanner.json` + Android asset, discovery research package (9 files) |

No commit history was amended; no push occurred.

### 7.4 Verification applicable to each commit

- Commit `a05a781` covers `analysis/replay.py`, `decoders/quality.py`,
  `tests/test_catalogue.py`, `tests/test_platform.py`, and the Android gear
  files. Its Python tests are in `tests/test_catalogue.py` and
  `tests/test_platform.py`; these passed in the current tree
  (`python -m pytest tests/test_catalogue.py tests/test_platform.py
  tests/test_gateway_resistance.py tests/test_spec_export_in_sync.py -q`
  → **116 passed**). The Android gear parity is pinned by `DecodeTest.kt`
  (52-test JVM suite, reported by the decoder-accuracy ledger).
- Commit `011d2eb` covers `decoders/meb.py`, `decoders/catalogue.py`,
  `spec/odb_scanner.json` (+ Android asset), and `tests/test_gateway_resistance.py`.
  `validate_catalogue("meb"/"eup")` → clean; the spec is reproducible and
  byte-identical to the Android asset; `tests/test_gateway_resistance.py`
  and `tests/test_spec_export_in_sync.py` passed in the same targeted run.
- The earlier full-suite result (**805 passed**, `ruff check .` clean) was
  recorded against the tree before the chart-semantics workstream began
  modifying `analysis/series.py`, `analysis/battery.py`,
  `analysis/chart_semantics.py`, `database/repository.py`, and
  `web/dashboard.py`. Neither commit A nor B includes any of those files.

### 7.5 Dashboard commit deferred

The dashboard-investigation workstream could **not** be committed cleanly: its
`database/repository.py` (`dtc()`) and `web/dashboard.py` (timeline/event/DTC
routes) changes are interleaved with an **active fourth workstream ("chart
semantics")** that is still modifying those same files (and `analysis/series.py`,
`analysis/battery.py`, `analysis/chart_semantics.py`). The dashboard routes
depend on the `dtc()` method, so a partial commit would be broken. All
dashboard files are left unstaged, together with the chart-semantics files, for
a later commit once the chart-semantics workstream finishes.

### 7.6 Left unstaged (with reason)

- `database/repository.py`, `web/dashboard.py` — interleaved dashboard +
  chart-semantics changes (active).
- `analysis/context.py`, `analysis/dtc.py`, `web/templates/*`,
  `tests/test_timeline_investigation.py`, `docs/investigation/dashboard_improvement/`
  — dashboard workstream, deferred (coupled to the interleaved files).
- `analysis/series.py`, `analysis/battery.py`, `analysis/chart_semantics.py`,
  `docs/investigation/chart_semantics/` — active fourth workstream, not part of
  this task.
- `.gitignore`, `cloud/*` — pre-existing, unrelated to the three workstreams.

### 7.7 Confirmation

- No remote push, publish, or upload occurred.
- No vehicle communication or prohibited diagnostic action occurred; no
  process was stopped or restarted.
- No production database was opened read-write, modified, restored, or deleted
  by this task (the earlier `data/odb_scanner.db` mtime change remains
  unattributed to a specific writer, per `evidence_register.md` E17).
