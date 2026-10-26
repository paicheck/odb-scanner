# Chart semantics, battery telemetry and range — progress ledger

Resumable log. Each checkpoint records what is done so an interrupted run can
continue without repeating work.

## Safety

- Static software task. No vehicle/adapter communication, no collectors, no
  hardware probes were run.
- Production DB `data/odb_scanner.db` was only opened read-only
  (`file:...?mode=ro`) for the signal inventory. Tests use temporary DBs.
- Nothing staged, committed or discarded. Concurrent/unrelated changes in the
  tree (decoders/, spec/, android/, cloud/, audit docs, tests/test_platform.py,
  tests/test_catalogue.py, decoders/quality.py) are left untouched.

## Checkpoint 1 — baseline (done)

- `git status`: same tree as the end of the previous (dashboard-workbench) task;
  no new concurrent edits at start.
- Baseline: `python -m pytest -m "not hardware" -q -p no:cacheprovider`
  → **805 passed** (2m46s). `python -m ruff check web analysis database tests`
  → clean.

## Checkpoint 2 — signal inventory (done, read-only)

Real data in the production DB (counts are successful rows):

| Signal family | Keys present | Verification (quality_for) |
|---|---|---|
| SoC | soc_abs (53), soc_normal (53) | provisional / unknown |
| Pack | pack_voltage (53), pack_current (41), pack_power_kw calc (38) | provisional |
| Cell voltage | cell_voltage_max/min (53); `cell_voltages` table: 38 sweeps x 108 cells | provisional / unknown |
| Cell delta | cell_delta_mv (calculated) | provisional (derived) |
| Battery temp | battery_temp, bat_temp_max, bat_temp_min (52 each) | provisional / unknown |
| HV energy | hv_energy_content, hv_energy_max (42, Wh) | verified |
| SoH | soh_pct (38, estimated = rated max energy / 58 kWh) | provisional (derived) |
| Range | range_displayed / range_can / range_internal (50, km) | unknown |
| Distance | odometer_km (23, provisional), several other odometers (unknown) | |
| Consumption | road_avg_consumption_0..6 (40, kWh/100km, category meaning unresolved) | unknown |
| 12 V | lv_voltage_obd (137, OBD PID), aux_12v_voltage (42, BMS) | unknown |
| Charging | **0 charging sessions, 0 samples**; charge_mode always `idle/ready` | |

Provisional DIDs 0x2A25 (`bat_internal_resistance_raw`) and 0x2A0E
(`aux_battery_resistance_mohm`) have no stored rows and are not charted.

## Checkpoint 3 — chart inventory and audit findings (done)

Charts before this work (all in `web/static/dashboard.js`, data inline JSON or
`/api/measurements/{key}`):

| Page | Chart | Data source | Problems found |
|---|---|---|---|
| Overview | Battery trend: SoC (L) + pack V (R) | `battery_measurements` snapshot | F1, F2, F3, F4 |
| Battery | SoC, pack V, cell delta, temp, 12 V | snapshot table + `lv_voltage_obd` | F1, F2, F3, F4, F5 |
| Measurements | one key, picker | `/api/measurements/{key}` | F3, F4, F6, F7, F8, F9 |
| Explore | one key + event markers | `series_for` inline | F3, F4, F7, F8, F9 |
| Progress | ECU answer bar chart | `progress_data` | none material |
| Charging | table only (no chart) | `charging_sessions` | F10 |

Findings:

- **F1 SoC definitions silently merged.** Overview/Battery plot
  `soc_normal_pct if not None else soc_abs_pct` as ONE series. Absolute (BMS)
  and normalised (display) SoC are different definitions (68.4 vs 69.5 % at the
  same instant here).
- **F2 Snapshot timestamps are not observation timestamps.** The
  `battery_measurements` row carries values forward up to
  `collector.max_value_age_s`; plotting it stamps a value with a time it was not
  read at. Per-key `measurements` rows carry their own real `ts`.
- **F3 Curve smoothing fabricates values.** `tension: 0.15` draws a spline that
  can overshoot between two samples (a value never recorded).
- **F4 No provenance in charts.** Imported Car Scanner rows (e.g. soc_abs on
  2026-10-05) are drawn in the same series as live reads with no marker or
  tooltip note.
- **F5 Off-by-one cell labels.** `key_meta` named `cell_v_001` "Cell 1 voltage";
  the catalogue, decoders and importer all call it cell #002.
- **F6 Missing units for non-registry keys.** `lv_voltage_obd` has no registry
  spec, so its chart axis/tooltip had no unit although every stored row says V.
- **F7 Summary stats computed on downsampled points.** "n / min / max / mean"
  under the chart used the *returned* points; with min/max sampling the mean is
  biased toward extremes and n is not the raw count.
- **F8 No axis/precision policy.** One generic `fmtNum` for everything; no
  per-unit range policy (SoC not bounded to 0–100, signed current not anchored
  at zero, cumulative counters treated like any other value); `degC` shown raw.
- **F9 Tooltip time to the minute only.** Real observations are second
  resolution; the tooltip dropped seconds.
- **F10 Missing shown as zero.** Charging table renders `energy or 0`,
  `max_power or 0`, `avg_temp or 0`, `duration or 0` -> unknown becomes
  `0.00 kWh` / `0.0 kW` / `0.0 °C` / `0 min`.
- **F11 Non-deterministic duplicate ordering.** `measurement_series` and
  `raw_measurement_series` order by `ts` only; rows sharing a timestamp come back
  in arbitrary order.
- **F12 Plausibility not surfaced.** Out-of-range values are neither removed
  (good) nor indicated (bad).

## Plan

1. `analysis/chart_semantics.py`: one place for unit display labels, unit
   groups, per-unit precision, scale policies (bounded / signed / cumulative /
   zero-based / auto), axis range computation, raw stats, plausibility counts,
   panel grouping (never mix units on one axis), nearest-timestamp pairing.
2. `numeric_payload`: carry provenance through downsampling; add raw stats.
3. `Repository.measurement_series_many` (single query) + deterministic
   `ORDER BY ts, id`.
4. `/api/chart/series` batch endpoint returning annotated series.
5. JS: straight segments, provenance point styles + tooltips, server axis
   policy, second-resolution UTC tooltips, shared time axis across panels,
   selectable series, server-side stats.
6. Battery page from per-key series; cell-sweep spread with cell identity.
7. `/range` page: range estimates vs SoC vs odometer vs vehicle-reported
   consumption, kept distinct.
8. Charging: fix F10; session detail with aligned panels from
   `charging_samples` when sessions exist.

## Checkpoint 4 — implementation (done)

| Finding | Fix | Where |
|---|---|---|
| F1 | SoC abs/normal drawn as two labelled series; Overview uses soc_abs | `BATTERY_PANELS`, `OVERVIEW_PANELS` |
| F2 | Battery/Overview charts read per-key `measurements` rows (own `ts`), not the carry-forward snapshot table | `panels_context`, `chart_series` |
| F3 | `tension: 0` everywhere (straight segments) | `dashboard.js` |
| F4 | Per-point provenance through downsampling; ▲ imported / ◆ simulated point shapes; tooltip + text counts | `series.numeric_payload`, `build_series`, JS |
| F5 | `cell_v_NNN` → "Cell voltage #NNN+1", matching catalogue/decoders/importer | `key_meta` |
| F6 | Unit falls back to the stored rows' unit only when unambiguous, labelled "from stored rows"; disagreements listed | `key_meta`, `Repository.recent_units` |
| F7 | Summary stats computed server-side on raw values (n, min, max, mean, latest) | `raw_stats`, `renderSummary` |
| F8 | Per-unit/per-key scale policies + 1-2-5 rounded ranges + precision; `degC`→`°C` display only | `analysis/chart_semantics.py` |
| F9 | Tooltip time to the second, explicit UTC; template `fmtts` keeps non-UTC offsets | JS `fmtFull`, `_format_ts` |
| F10 | Charging table shows "—" for unrecorded values | `charging.html` |
| F11 | `ORDER BY ts, id` for series reads | `Repository` |
| F12 | Out-of-range values flagged (✕ point, tooltip, count) and kept | `plausibility`, `out_of_range` |

Scale policies introduced (`analysis/chart_semantics.py`):

| Policy | Applies to | Behaviour |
|---|---|---|
| bounded | `%` | fit data, clamped to 0–100 |
| signed | `A`, `kW`, `W` | always includes zero |
| zero-based | `mV`, `km` (range), `Wh`, `kWh/100km`, `km/h` | starts at zero |
| cumulative | odometer keys, `Ah`, `kWh` | fit data, never forced to zero |
| auto | everything else (`V`, `°C`…) | fit data; note says "does not start at zero" |

No threshold bands were added: the catalogue documents none for these signals.

New pages/routes: `/range`, `/charging/{id}`, `/api/chart/series`
(≤12 keys, validated, one query). Battery page rebuilt (8 panels + cell-spread
per sweep with cell identity + latest-sweep bar chart). Overview chart rebuilt.
Every chart has a text summary from the same payload.

Derivations introduced:

- Cell spread per sweep = max − min of the individual `cell_voltages` readings
  at one timestamp (mV), with the min/max cell numbers; missing slots counted
  against the registry's cell DID count.
- Distance in window = last − first `odometer_km` reading; refused when fewer
  than two readings or the counter goes backwards.
- Range-vs-SoC pairing = nearest displayed-range reading within 120 s of each
  SoC reading; no interpolation, no fitted line.
- No consumption derivation (see limitations).

## Checkpoint 5 — verification (done)

- `python -m pytest tests/test_chart_semantics.py -q -p no:cacheprovider` →
  **54 passed**.
- `python -m pytest -m "not hardware" -q -p no:cacheprovider` →
  **859 passed** (805 baseline + 54 new), 0 failed, 0 skipped.
- `python -m ruff check web analysis database tests` → **All checks passed**.
- Disposable copy of the production DB: all 13 chart/related pages render 200;
  copy deleted afterwards.
- Browser (local dashboard on 127.0.0.1): no console warnings/errors on
  Overview, Battery, Range, Explore, Measurements, Charging; every canvas drew
  with tension 0 and the server axis ranges.
- `git diff HEAD -- spec android decoders diagnostic collector.py` is empty:
  shared spec, Android, decoders and read-only guards untouched.
- tx_log stays empty after browsing every new page (test).

Concurrent activity: while this ran another workstream committed `a05a781`,
`011d2eb`, `17430f6` (decoder accuracy, gateway resistance DIDs, commit audit).
None touch files changed here. The tree may still be changing.

## Limitations and signals lacking evidence

- **No charging sessions exist** in the database, so the session view is
  verified only on synthetic data. `charging_samples` has no provenance or
  source-DID column, so its panels show evidence UNKNOWN.
- `pack_current` sign convention is undocumented in the catalogue; shown as
  decoded with a note.
- Range keys (`range_*`), `road_avg_consumption_*` (category meaning),
  `soc_normal`, `bat_temp_max/min`, `lv_voltage_obd` are UNKNOWN confidence.
- SoH (`soh_pct`) remains an ESTIMATE (rated max energy ÷ marketed 58 kWh);
  no uncertainty band exists, so no trend is drawn from it.
- `hv_energy_content`/`hv_energy_max` plausibility comes from the catalogue
  overlay, which is the encoding range (0–1.6 MWh), not a physical bound, so
  it will not flag a mis-decode. Left for the decoder workstream.
- `cell_voltages` rows have no provenance column ("provenance not recorded").
- One sweep shows an ~80 mV spread versus a ~13–18 mV norm; it is shown and
  attributed to its cells, not interpreted.

## Deferred — needs a human decision

1. Whether to derive consumption (kWh/100 km) from `hv_energy_content` deltas
   over odometer deltas — needs agreed journey segmentation and confirmation
   that energy-content changes are not dominated by regen/auxiliary load.
2. Whether `charging_samples` should record source keys/provenance per field.
3. Whether to document the `pack_current` sign convention in the catalogue
   (requires a capture with a known charge/discharge state).
