# Shared diagnostic spec

`odb_scanner.json` is the single source of truth for the DID map, scale factors,
the read-only allow-list, and the per-ECU transport quirks. The Python collector
reads this knowledge from its live registry; the Android app loads it as data.
Regenerate with:

```powershell
python tools/export_spec.py            # writes spec/odb_scanner.json
```

## Contents

* `read_only_guard` — the allow-list (`diagnostic/uds.py`): allowed/blocked
  services, read sub-functions, request-length limits, NRC names. **The Android
  read-only layer enforces this verbatim.**
* `ecus` — every ECU (`diagnostic/ecus.py`) with its CAN ids, the 29-bit module
  address (`tx29`/`rx29`), and the transport quirks the v1.5 clone needs
  (`auto_formatting`, `flow_control`, `receive_filter`), derived
  from the addressing mode:
  * `11-bit` → `ATCAF1` + bare request + `ATFCSH/ATFCSD/ATFCSM 1` + `ATCRA`
  * `29-bit` → `ATCAF1` + bare request + `ATFCSH/ATFCSD/ATFCSM 1` + `ATCRA`
  * `functional` → `ATCAF1` + functional header
* `profiles.<p>.measurements` — the DID map. Each entry carries `decode`, one of:
  * `{"kind": "scalar", offset, length, byte_order, signed, scale, divisor,
    addend}` — `value = int * scale / divisor + addend`;
  * `{"kind": "special", name, formula}` — a named decoder the client implements
    (`cell_voltage`, `cell_temp`, `energy_counters`, `charge_mode`,
    `coolant_temps`, `gear`, `vin`, `pair`);
  * `null` — raw-only (bytes preserved, decoded later).
* `profiles.<p>.cell_voltages` — the per-cell sweep (`bat_mgmt`, `0x1E40` base,
  108 slots, `0x0FFE` unused marker, `u16be/1000+1`).

## Where the decode recipes come from

`tools/export_spec.py` derives the decode recipe from the live Python decoder,
not from a second hand-maintained table:

* the Car Scanner profile fields (`_cs_field_decoder` closures) are introspected
  for their `sbi/dl/mul/div/ofs/sig`;
* the module-level arithmetic decoders (`_soc_bms`, `_pack_voltage`, …) are
  mapped by name in `_FUNCTION_RECIPES`;
* the named complex decoders are marked `special` by name.

That keeps the spec from drifting away from the decoders that actually run.
