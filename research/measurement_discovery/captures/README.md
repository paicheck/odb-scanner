# Validation capture archive — 2026-10-21

These files are an **offline** validation archive, not a new vehicle capture.
They were produced on 2026-10-22 from the latest stored raw responses in
`data/odb_scanner.db`, opened read-only by `tools/export_validation_capture.py`.

| File | Meaning |
|---|---|
| `live_20261021.json` | 35 `{key, raw, ts}` records, latest raw rows per key |
| `live_20261021_validation.json` | checker report: 35 pass / 0 fail / 0 indeterminate |
| `live_20261021_validation_strict.json` | `--strict` report: 35 pass / 0 fail / 0 indeterminate |

Commands used:

```bat
python tools/export_validation_capture.py --out research/measurement_discovery/captures/live_20261021.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --out research/measurement_discovery/captures/live_20261021_validation.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --strict --out research/measurement_discovery/captures/live_20261021_validation_strict.json
```

**What this proves:** the stored bytes re-decode reproducibly against the
current registry and the quality plausibility ranges.

**What this does not prove:** the capture has no independent `expected` values,
so it is not an expected-value confirmation of the scales. That still needs a
known-state test (see `docs/NEXT_STEPS_20261022.md`) and, ultimately, a
project-owned capture taken under the read-only protocol.

The database was not modified; no adapter or vehicle was contacted.
