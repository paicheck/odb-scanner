# Commit-readiness audit — progress ledger

**Date:** 2026-10-26
**Scope:** static, offline integration audit of the discovery pass and the
concurrent decoder-accuracy pass. No vehicle communication, no write, no
commit/push/reset/clean/discard. Read-only guard unchanged.

## State

- Branch `main`, HEAD `bd86d55`.
- **Three** non-overlapping workstreams, verified disjoint by file:
  discovery, decoder-accuracy, dashboard-investigation.
- `diagnostic/` (read-only guard) unchanged — confirmed by `git diff`.

## Checks performed (all offline)

| Check | Result |
|---|---|
| `python -m ruff check .` | **All checks passed** |
| `python -m pytest tests -q` (combined) | **793 passed** |
| `build_spec() == committed spec` (ignoring generated_at) | True |
| `spec.json` vs Android asset SHA-256 | identical |
| `validate_catalogue("meb"/"eup")` | `[]` / `[]` |
| read-only allow-list (services/sub-functions/blocked) | unchanged |
| two new DIDs | PROVISIONAL, THIRD_PARTY_DEFINITION, address VERIFIED |
| gear decoder Python↔Android | consistent (8=P,7=R,6=N,5=D,12=B) |
| replay.py text-row fix | correct (skip non-numeric, preserve numeric) |
| `cell_voltage_sum` quality fix | correct (0x1E3C is NUMERIC, not DERIVED) |

Test growth 777 → 793 = 14 discovery + 1 replay + 1 quality fix.

## Findings / recommendations

- Ready to commit, with two recommendations: remove the throwaway `dbpeek.py`
  (opens real DB read-write, queries non-existent columns); keep the two new
  DIDs PROVISIONAL and do not assert SoH/12 V failure without validation.
- Six decoder discrepancies left UNRESOLVED (no code change justified now).

## Deliverable

- `docs/investigation/commit_readiness_audit_20261026/audit_report.md`
