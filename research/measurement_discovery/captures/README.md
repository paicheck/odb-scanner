# Validation capture archive — 2026-10-21 / 2026-10-22

These files are evidence archives; most are offline. `live_20261021.json` was
produced on 2026-10-22 from the latest stored raw responses in
`data/odb_scanner.db`, opened read-only by
`tools/export_validation_capture.py`. `live_gateway_scan_20261022.json` is the
direct output of the approved live 9-DID gateway scan. The passive archive was
extracted from the 2026-10-15 btsnoop with `tools/mine_passive_capture.py`. The
raw btsnoop is **not** in this directory: it contains unrelated phone data.

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
| `live_gateway_scan_20261022.json` | 9-DID `[19.Gate]` live scan: 9/9 positive, 0 NRC, only UDS `0x22` |
| `live_gateway_scan_20261022.manifest.json` | archive SHA-256 `05bf1e4a690c1ca88b38bc1e32ae59aa1d15ff3c39ec7e05b35ee5a9975f9595`, source hash, DID list, safety notes |

Commands used:

```bat
python tools/export_validation_capture.py --out research/measurement_discovery/captures/live_20261021.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --out research/measurement_discovery/captures/live_20261021_validation.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/live_20261021.json --strict --out research/measurement_discovery/captures/live_20261021_validation_strict.json
python tools/validate_measurement_capture.py --in research/measurement_discovery/captures/strict_expected_values_20261022.json --strict --out research/measurement_discovery/captures/strict_expected_values_20261022_validation.json
```

The live gateway archive was produced with `python tools/scan_gateway.py` while
the car was present; it is an approved on-car result, not reproducible offline.

**What the 35-record run proves:** the stored bytes re-decode reproducibly against
the current registry and the quality plausibility ranges.

**What the 35-record run does not prove:** it has no independent `expected`
values, so it is not an expected-value confirmation of the scales. The separate
five-record strict archive supplies independent displayed values for
`hv_energy_max` (53,200 Wh), `hv_energy_content` (35,175 Wh), `soc_abs` (79.6 %),
`pack_voltage` (429.5 V) and `odometer_km` (111,020 km); all five match exactly.
The checker now writes the `expected` value into each verdict, so a PASS is
visibly tied to it.

**2026-10-22 live gateway scan:** `tools/scan_gateway.py` read exactly the nine
profile DIDs on `0x710 -> 0x77A`. All nine answered `0x62`; there were no NRCs
or timeouts. Only UDS `0x22` reads were sent, with no session change. The
independent cross-check inside the capture is `0x2AB8` = `0x15D1` last field =
**39,950 Wh**; `0x2AB2` = **53,200 Wh**. This is a successful live scan, not a
decoder-scale confirmation for every value. The archived JSON is the unchanged
scan output; the adjacent manifest records its SHA-256 and exact DID list.

The separate `0x27C4` cold-soak/defroster attempt on the same session ended
before the defroster state was captured and is therefore **not** archived as
evidence. It does not resolve the windshield-vs-ambient label.

The database was not modified; no adapter or vehicle was contacted for the
archiving step. The passive extraction only read the local btsnoop and selected
CAN/DID/payload records.
