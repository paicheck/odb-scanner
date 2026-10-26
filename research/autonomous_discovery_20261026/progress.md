# Autonomous discovery pass — progress ledger

**Session start:** 2026-10-26
**Scope:** offline discovery of additional diagnostic measurements for the
2021 VW ID.3 58 kWh (MEB). No vehicle connection, no diagnostic request, no
write/coding/adaptation/security-access/routine/actuator test, no fault
clearing. No destructive repository action.

## Safety commitments (this pass)

- No vehicle communication of any kind.
- The read-only guard (`diagnostic/uds.py`) is unchanged.
- No `elm_console.py`, no `read_abs_wheel_speeds.py --execute`, no session change.
- Live database (`data/odb_scanner.db`) only referenced, never written.
- No `git reset --hard` / `git clean` / destructive checkout.
- No edits to another concurrent session's files (see "Concurrent session" below).

## Repository state

- Branch `main`; HEAD `bd86d55 Add production cloud deploy stack` (2026-10-26).
- Pre-existing uncommitted work (NOT ours, preserved): `M .gitignore`,
  `?? cloud/CPANEL.md`, `?? cloud/cpanel.env.example`, `?? cloud/passenger_wsgi.py`,
  `?? cloud/requirements-cpanel.txt`.
- **Concurrent session detected mid-pass** (see below): its files are
  `analysis/replay.py`, `android/.../Decode.kt`, `android/.../DecodeTest.kt`,
  `tests/test_catalogue.py`, and `docs/investigation/decoder_accuracy_audit_20261026/`.
  They are NOT this pass's work and were left untouched.

## Verified baseline (2026-10-26)

| Item | Reported | Verified | Final |
|---|---|---|---|
| MEB DID specs | ~287 | 287 | **289** (+2) |
| distinct DIDs | ~253 | 253 | **255** (+2) |
| catalogue records | ~296 | 287 | **289** |
| quality records | ~296 | 296 | **298** |
| Python tests | ~765 | 777 | +14 |
| lint | clean | clean | clean (this pass's files) |

- Quality: 49 provisional / 240 unknown / 2 verified / 5 invalid; 5 excluded.
- "318 distinct measurement keys" (brief) was traced to the Pelican/OBDb
  "318 parameters" page for the ID.3, not to this project's key count (287).

## What this pass did

- Coverage gap analysis (240 UNKNOWN catalogue keys; 250/287 no plausible range).
- External research: cross-referenced the registry against the OBDb/Volkswagen-ID3
  signalset v3 (CC-BY-SA-4.0) and dalathegreat/Battery-Emulator.
- **Implemented** two new gateway DIDs (0x2A25, 0x2A0E) with decoders, catalogue
  overlays, 14 regression tests, and spec regeneration.
- **Documented** 16 candidates, 6 discrepancies, 1 blocked CAN capability, and
  this final report, under `research/autonomous_discovery_20261026/`.

## Concurrent session (important)

A separate autonomous pass was already running in this repository. It fixed
`analysis/replay.py` (text-valued rows no longer crash replay), aligned the
Android gear decoder with the Python `_gear` map (8=P/7=R/6=N/5=D/12=B), and
produced a read-only decoder-accuracy impact report
(`docs/investigation/decoder_accuracy_audit_20261026/impact_odb.md`). Its files
do not overlap this pass's files; the shared spec was only touched by this pass
(adding 0x2A25/0x2A0E), which is consistent with the other pass's non-spec
changes. Do not commit without coordinating both passes.

## Commands executed

- `python -m pytest tests -q` → **777 passed** (144 s) [baseline].
- `python -m ruff check .` → clean [baseline].
- `python tools/export_spec.py --sync-android` → regenerated spec + Android asset.
- `python -m pytest tests/test_gateway_resistance.py -q` → **14 passed**.
- `python -m pytest <8 targeted files> -q` → **196 passed**.
- `python -m ruff check decoders/meb.py decoders/catalogue.py tests/test_gateway_resistance.py` → clean.
- `python -m pytest tests -q` (full suite, post-change) → **792 passed** (187 s).
  Growth 777→792 = 14 this pass + 1 concurrent pass; the two passes coexist.

## Files changed (this pass)

- `decoders/meb.py` — +2 DIDs (0x2A25, 0x2A0E).
- `decoders/catalogue.py` — +2 OVERLAY entries.
- `tests/test_gateway_resistance.py` — new (14 tests).
- `spec/odb_scanner.json` + `android/.../assets/spec.json` — regenerated.
- `research/autonomous_discovery_20261026/` — progress, candidate_inventory,
  evidence_register, final_report.

## Blocked / needs human approval

- Known-state capture of 0x2A25/0x2A0E.
- Discrepancy tests (0x2613, 0x189D, 0x1E0E/0x1E0F, 0x27C4, 0x42DB, 0x028C).
- OBC reachability (blocks charger DIDs).
- BMS CAN-broadcast isolation/contactor/balancing (different transport).
- ABS 0x10 03 (unchanged, separate approval).

## Next action

- ✅ Done. This pass is complete: baseline recorded, research performed, two
  DIDs implemented and tested, 16 candidates and 6 discrepancies documented,
  full suite **792 passed**. Hand off to a known-state capture session (see
  `final_report.md` §F/G) — no further offline implementation is queued.
