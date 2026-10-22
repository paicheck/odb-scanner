# Validation capture archive — 2026-10-21 / 2026-10-22

These files are **offline** evidence archives, not a new vehicle capture.
`live_20261021.json` was produced on 2026-10-22 from the latest stored raw
responses in `data/odb_scanner.db`, opened read-only by
`tools/export_validation_capture.py`. The passive archive was extracted from the
2026-10-15 btsnoop with `tools/mine_passive_capture.py`. The raw btsnoop is
**not** in this directory: it contains unrelated phone data.

| File | Meaning |
|---|---|
| `live_20261021.json` | 35 `{key, raw, ts}` records, latest raw rows per key |
| `live_20261021_validation.json` | checker report: 35 pass / 0 fail / 0 indeterminate |
| `live_20261021_validation_strict.json` | `--strict` report: 35 pass / 0 fail / 0 indeterminate; no `expected` fields |
| `passive_capture_evidence_20261015.json` | 278 sanitised CAN/DID records plus DB evidence from the passive HCI capture |
| `passive_capture_evidence_20261015.manifest.json` | archive SHA-256 `b2c490d523bbeecc61b9ee30022e2929e3c4d31752d69754fb7f950e03646cb7`, source-log SHA-256 `eefb…96f9`, byte count |
| `strict_expected_values_20261022.json` | five independent expected values: two energy DIDs, SoC, pack voltage, odometer |
| `strict_expected_values_20261022.manifest.json` | archive SHA-256 `52bc7026458755db0f65b9e13e9447bea34bda201b87343c2f43e82b80f9a35d` |
| `strict_expected_values_20261022_validation.json` | `--strict` report: 5 pass / 0 fail / 0 indeterminate, expected values retained |

Commands used:

```bat
python tools/export_validation_capture.py --out research/measurement_discovery/captures/live_20261021.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --out research/measurement_discovery/captures/live_20261021_validation.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --strict --out research/measurement_discovery/captures/live_20261021_validation_strict.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/strict_expected_values_20261022.json --strict --out research/measurement_discovery/captures/strict_expected_values_20261022_validation.json
```

**What the 35-record run proves:** the stored bytes re-decode reproducibly against
the current registry and the quality plausibility ranges.

**What the 35-record run does not prove:** it has no independent `expected`
values, so it is not an expected-value confirmation of the scales. The separate
five-record strict archive supplies independent displayed values for
`hv_energy_max` (53,200 Wh), `hv_energy_content` (35,175 Wh), `soc_abs` (79.6 %),
`pack_voltage` (429.5 V) and `odometer_km` (111,020 km); all five match exactly.
The checker now writes the `expected` value into each verdict, so a PASS is
visibly tied to it.

The database was not modified; no adapter or vehicle was contacted. The passive
extraction only read the local btsnoop and selected CAN/DID/payload records.
