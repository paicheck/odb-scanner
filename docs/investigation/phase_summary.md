# Investigation Summary (Phases A-B complete; C-F blocked on artifacts)

> **Status 2026-10-15: the "Blocked/Unresolved" list below is OUT OF DATE.**
> Phases C–F are complete. The artefacts were obtained
> (`C:\Users\kings\Documents\CarScanner_Investigation\`), the measurement
> definitions were recovered from `profiles7.db` (MEB profile 342), and
> `hv_energy_content` (DID `0x2AB8`) / `hv_energy_max` (DID `0x2AB2`) are
> implemented in `decoders/gateway.py` with 77 offline tests. Still open:
> on-car confirmation of the gateway addressing and the `0x2AB8` payload
> offset — see **`meb_gateway_energy.md`**. The original text is kept below.

## Completed
- Phase A (baseline): Documented transport, UDS read-only model, ECUs, registry/decoders, collector, tests. Fixed time-sensitive test. All 38 tests pass, guard-test passes. Baseline captured in baseline.md.
- Phase B (transport): Documented initialization (ATZ,ATI, ATH1, ATAT1, ATSP6, ATCAF1), addressing (ATSH/ATCRA 11-bit), parsing/reassembly, error classifications. Transport model documented. Noted "energy/climate" references don't exist in this codebase.

## Blocked/Unresolved
- Phase C (Car Scanner live-data call graph): Missing extracted assemblies/resources (CarScannerMaui, methods LoadVAG_MEB/GetVAGCanRequests/GetVAGCode/GetVAGKWPRequests, ShiftStream2, BinaryDeserilze...). Searched extensively; not found in Documents tree. phase_c_d_blocker.md created.
- Phase D (decode measurement resources): Missing vagmeb.db, vag.micro.vag, vag.strings.vag and decoder code. Cannot establish format without artifacts.
- Phase E (ECU addressing): Current is 11-bit (ATSP6). Reported 17FC/17FE (29-bit) and 18DA family for [19.Gate] unverified. No evidence in repo; ecu_addressing.md documents gap.
- Phase F (measurement definitions): hv_energy_content/max (19.Gate), DC/DC [8105] unknown. Existing BMS pack voltage/current exist; cell temps registered function exists but not auto-registered in default registry.

## Next highest-value steps (if artifacts available)
1. Locate Car Scanner extracted managed assemblies (.dll) and resource files (vagmeb.db, vag.*.vag). Search entire filesystem more broadly or obtain from previous extraction.
2. Use disassembly to find LoadVAG_MEB/GetVAGCanRequests RVAs and trace call graph; extract actual request sequences and addressing.
3. Decode resources per code that consumes them (ShiftStream2) to get measurement definitions.
4. Validate recovered definitions against captures before implementing.

## Safety
All work so far preserves read-only behavior, no writes attempted, existing functionality intact (BMS path unchanged). No speculative DIDs added.
