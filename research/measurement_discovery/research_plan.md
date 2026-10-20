# Research plan — additional diagnostic measurements for the 2021 VW ID.3

Owner vehicle: 2021 VW ID.3 Pro Performance, 58 kWh, MEB platform,
VIN `WVWZZZE1ZMP087053`. Tools available: Car Scanner (Android), OBDeleven
NextGen, and this read-only Python project.

Status: **research / inventory only.** No connection to the vehicle, no
diagnostic requests, no writes, no config changes. This document and every
artifact beside it are offline analysis.

## Objective

Discover what additional useful measurements this specific vehicle may expose,
how they could be read safely, and rank them into an evidence-based expansion
plan. Deliver a prioritised, machine-readable catalogue and a staged validation
roadmap, with the current 152 selected measurements as the comparison baseline.

## Ground rules (from the task brief)

* No live diagnostics. All findings come from existing local files, saved logs,
  source code, the extracted Car Scanner profile, and permitted external
  research sources.
* A signal defined in a profile is **not** evidence it exists, responds, or is
  correctly decoded on this car.
* A plausible value is **not** a verified decoder.
* A stale reading is **not** current vehicle state.
* Preserve all originals. All output is additive under this directory.
* Never change more than one protocol variable per experiment (for the future
  live phase).

## Evidence levels used throughout

| Level | Meaning |
|---|---|
| `verified_on_vehicle` | A raw response has been observed from this car and the decoded value is physically plausible across states. |
| `supported_by_source` | A primary source (Car Scanner profile, or a named open-source project) defines the DID + layout. Not yet read on this car. |
| `observed_positive_undecoded` | This car returned a positive `0x62` response, but the payload layout/scaling is unknown. |
| `inferred` | Deduced from ECU functionality or a related signal; no direct definition located. |
| `speculative` | Guess or second-hand list entry; must not be presented as supported. |
| `rejected` | Repeated negative response or a demonstrably wrong ECU/DID combination; record it, do not pursue it. |

## Task groups

1. **Inventory existing measurements and profile definitions** — parse the
   current registry (152 selected) and the extracted 308-record MEB profile.
2. **Search for additional signals in the installed Car Scanner profile** —
   every `CustomPID` record not currently selected, deduplicated.
3. **Inspect profile history and release changes** — variant/duplicate records,
   alternate scalings for the same DID.
4. **Search public MEB repositories** — `raimuucka/OBD2-Volkswagen-MEB-EV-CAN-parameters`,
   evDash, ABRP, ELMduino issue #207, OVMS, and related projects.
5. **Investigate other relevant ECU measurement families** — 12 V/DC-DC, BMS,
   electric drive, charging/OBC, vehicle context, other ECUs.
6. **Cross-reference candidates across independent sources.**
7. **Audit existing decoders and suspicious values** — the implausible-value
   list, alternate scaling/byte-order/signedness hypotheses.
8. **Compare candidates against the existing 152** — alias/duplicate control.
9. **Evaluate relevance** to the intermittent electric-drive warning and the
   low-voltage/communication investigation.
10. **Rank and produce the final catalogue and report.**

## Work loop

Plan -> investigate -> record -> cross-check -> integrate -> validate ->
reassess. Update `research_backlog.md` and `progress_log.md` after each unit.
A negative result is recorded as what was searched and what it establishes.

## Priority order

1. Extract and deduplicate the full installed-profile signal set (highest value,
   directly answers "supported but not selected").
2. Decoder audit of the implausible values (12 V + drive warnings are the
   owner's active problems).
3. External cross-check for alternate scalings and missing signals.
4. Rank, write report, QC pass.

## Safety boundaries (unchanged)

* No vehicle connection during this phase.
* No `0x22` / `0x10 0x03` / `0x27` / `0x2E` / `0x31` / `0x14` transmitted.
* No changes to `diagnostic/uds.py`, the allow-list, or any decoder.
* Live validation, if ever proposed, is a separate stage requiring explicit
  owner approval and an independently reviewed read-only allow-list.
