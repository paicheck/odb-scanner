# Read-only validation capture protocol

**Status: prepared, not executed.** No connection has been made and no
diagnostic request has been sent. This document records exactly what a future,
separately-approved capture must look like so the decoder repairs can be
confirmed on a project-owned response instead of only stored bytes.

Everything below is read-only (`0x22 ReadDataByIdentifier`). Nothing here
performs a write, session change, coding, adaptation, security access, routine
or actuator test. `0x10 03` (extended session) is **not** part of this capture.

## Preconditions

- Owner has explicitly approved this DID list and the read-only method.
- The car is parked, ignition/ready state and ambient temperature are recorded.
- The adapter is the known ELM327 clone with the transport quirks documented in
  `diagnostic/elm327.py`; raw logging is on.
- A second person or equivalent is present; abort criteria are written down.

## Scope

One capture per DID, one DID at a time, on modules already known to answer.
Prefer DIDs whose raw bytes already exist in `data/odb_scanner.db`, so the
capture is a reproduction rather than a discovery.

| DID | key | expected kind |
|---|---|---|
| `0x3E93` | `dc_link_voltage` | float32 ~ 400–450 V parked |
| `0x4FAE` | `electric_drive_inflow_coolant_temperature` | float32 near ambient |
| `0x1E32` | `energy_counters` | 16-byte four-field Ah/kWh counters |
| `0x1E33` / `0x1E34` | `cell_voltage_max_cell` / `cell_voltage_min_cell` | u16 index at bytes 2–3, 1-based |
| `0x1E3D` | `pack_current` | signed current, negative while discharging |

Optional, equally read-only: `0x1E0E` / `0x1E0F` (temperature indices),
`0x7448` (charge-port bits), `0x3EE7` / `0x3EE8` (motor min/max current).

## Recording

For every DID record and keep, even on NRC or failure:

- timestamp (UTC, ISO-8601);
- ECU key, DID, request bytes;
- the raw data record (the bytes after the `0x62` marker and echoed DID);
- the decoded value under the current registry;
- ignition state, ambient temperature, charger state, gear.

Do not discard raw bytes. Do not pool these rows with imported Car Scanner
CSV rows as if they were the same evidence.

## Verdicts

Feed the raw data records into the offline checker:

```
python tools/validate_measurement_capture.py --in capture.json --strict
```

A key passes when the raw bytes decode to a finite value inside the quality
registry's plausibility range (or, with `--strict`, to the supplied `expected`).
A failure means the decoder and the capture disagree and the capture must be
reviewed before the definition is changed.

The same checker runs offline against stored rows, so the flow can be dry-run
today without the car:

```
python tools/export_validation_capture.py --out capture.json
python tools/validate_measurement_capture.py --in capture.json
```

## What this capture does NOT authorise

- No `0x10 03` session change (ABS wheel speeds stay deferred).
- No gateway `0x2A26` / `0x2A28` / `0x2A2F` blind-probing (no payload/scaling
  evidence exists; they remain quarantined).
- No writes, no routines, no actuator tests, no fault clearing.
