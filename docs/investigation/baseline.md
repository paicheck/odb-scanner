# Baseline (Phase A)

## Repo state
- Git: 67bdd7a "Add offline end-to-end smoke test...", 6e3e750 "Read-only VW ID.3 diagnostic system..."
- Untracked: odb_scanner.code-workspace
- Existing DB: data/odb_scanner.db (large), data/diag_smoke.db

## Current transport/initialization (ELM327)
- `diagnostic/elm327.py`: ATZ -> wait -> ATI -> identity; then ATE0, ATL0, ATS0, ATH1, ATAT1, ATSP6, ATCAF1 (docstrings note headers ON required; ATSP6 sets CAN 500kbit/s 11-bit by default)
- TCP support exists; serial autodetect.
- `set_header(tx_id)` sends `ATSH{tx:03X}`; `set_receive_address(rx)` sends `ATCRA{rx:03X}` or `ATCRA` to clear.

## UDS (read-only)
- `diagnostic/uds.py` allow-list: 0x10 (sub 01), 0x22, 0x19, 0x3E. Blocks write/security/routines; exposes `build_*` only for these.
- ISO-TP parsing: `line_to_frame` accepts header-on/off, `reassemble` handles single-frame (PCI 0) and first/consec (PCI 1/2), `parse_elm_lines`, `expect_positive`.
- NRC handling, ProtocolError, NegativeResponseError.

## ECUs
- `diagnostic/ecus.py`: bat_mgmt 0x7E5/0x7ED (BMS), chg_mgmt 0x765/0x7CF, chg 0x744/0x7AE (OBC), mot_elec 0x7E0/0x7E8, eld 0x7E6/0x7EE, inf 0x773/0x7DD, brk 0x713/0x77D. Uses 11-bit UDS-on-CAN addressing. Notes say ID.3 may route via gateway.

## Registry/decoders
- `decoders/registry.py`: DIDSpec with key, ecu, did, name, unit, decode (or None), provenance (reported/calculated/estimated), doc_status (documented/experimental/inferred/unknown), slow flag. Raw bytes always preserved. Helpers `u16be`, `u32be`.
- `decoders/battery.py`: BMS DIDs (documented e-Up/MEB refs): 0x1E3B pack_voltage (/64), 0x1E3D pack_current ((raw-2048)/5), 0x028C SOC abs (/2.5), 0x1E33 max cell (/256), 0x1E34 min cell (/256), 0x2A0B batt temp (/64-40, experimental), 0x1E32 energy counters (2*u32/3600 kWh, slow), 0x74CB CAC (first u16/10 Ah, slow), cell voltages 0x1E40..+i (/256, experimental range).
- `decoders/charging.py`: chg_mgmt 0x1DD6 charge mode, 0x1DD0 SOC normal (/2, experimental), 0x1DE4 remaining time (u16, experimental), 0x1DEC LV powerstate; chg 0x41FC AC V (u16), 0x41FB AC A (/10), 0x41F8 DC V (/10), 0x41F9 DC A (/10), 0x1DDA socket status (raw), 0x1DEF CCS status (raw).

## Collector
- Uses `DiagnosticConnection`, logs every TX via `repo.log_tx` with purpose. Builds transport from config. Collects OBD PIDs (0x00,0x0D,0x42), fast DIDs, computes pack_power_kw, cell_delta_mv, cell sweep (all cell_v_* DIDs, slow), slow pass, battery snapshot, charging session tracking (mode codes 0/1/2/3). Discovery: probes 0x10 0x01 per ECU, records responder/no-response/nrc-xx.

## Tests
- 38 passed (fixed time-sensitive `test_repository_roundtrip`). Includes read-only guard, UDS parsing, OBD-II/VAG DTC, stats, DB, config, end-to-end simulator. Simulator behaves correctly.

## Known good example (tests/simulator)
- BMS 0x7E5 read DID 0x1E3B gives 2-byte response; simulator returns synthetic values. VIN read works. UDS DTC read on chg_mgmt returns U112300.

## Baseline findings vs task
- No "energy = 0x710", "climate = 0x746" references found in codebase. Current DIDs are the documented e-Up/MEB set.
- No Car Scanner APK/assemblies, resource files, or `vagmeb.db`, `GetVAGCanRequests`, `LoadVAG_MEB` found in repo. Task notes them as previous investigation artifacts to preserve, but not present here.
- Task requires investigating those Car Scanner artifacts to recover live-data definitions; the current implementation is the baseline code.

## Working BMS example (from tests)
- Read DID 0x1E3B on bat_mgmt (0x7E5/0x7ED): request `22 1E3B`, positive response `62 1E3B` + 2 bytes. Decoded by `_pack_voltage(raw)=u16be/64.0`. This matches read-only UDS path.
