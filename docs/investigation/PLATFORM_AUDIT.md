# Vehicle-health investigation platform — Stage A audit

Read-only audit of what exists, what the data actually contains, and what is
missing before the app can be used as a state-of-the-art local investigation
tool. No code was changed to produce this document; every number is read from
`data/odb_scanner.db` (the file `config.database.path` names) and from the
repository at `HEAD = ab68c5b`.

## 0. The single most important finding first

The task brief describes a database with **281,475 rows, 327 keys, 1 Sep–18 Oct,
2 DTCs with 488 occurrences, and charging sessions**. The database the
configuration actually points at holds none of the faults or sessions:

| quantity | brief (pre-purge) | `data/odb_scanner.db` (config) | pre-purge backup |
|---|---:|---:|---:|
| measurement rows | 281,475 | **16,461** | 281,475 |
| distinct keys | 327 | **318** | 327 |
| window | 1 Sep–18 Oct | **5 Oct–18 Oct** | 1 Sep–18 Oct |
| DTCs | 2 (488 occ.) | **0** | 2 / 488 |
| charging sessions | present | **0** | 43 |
| anomalies | present | **0** | 72 |

Reason: `tools/purge_synthetic_data.py` deliberately deleted the September
portion because it was produced by the **simulator** and the DTC/charging
records came from `tools/seed_demo_data.py`. That was the correct call — see
§5 — and it is why this platform must treat September / U112300 / P0A7F00 as
**synthetic history, not evidence about the car**.

Consequence for every later stage: the mission's headline numbers are not
facts about this vehicle. The real record is 5–18 Oct, has **no DTC at all**,
and the "electric-drive warning" is a *manually recorded symptom* anchored on
real `eldrive` readings, never a code the car emitted.

## 1. What already works (do not rebuild)

* **Read-only by construction.** `tx_log` is the safety manifest: 37,730 rows
  of every request ever sent. `main.py guard-test` enforces the safety
  invariants. No endpoint or analysis path writes to the car.
* **Collector + decoders.** `collector.py`, `decoders/registry.py`
  (`DIDSpec`/`Provenance`/`DocStatus`), `decoders/catalogue.py`
  (`MeasurementDefinition`, `ByteLayout`, `Confidence`, `OVERLAY`,
  `validate_catalogue`) with two profiles (`eup`, `meb`).
* **Definition store.** `measurement_definitions`, `definition_evidence`,
  `definition_evaluations`, `captured_frames` (additive, SCHEMA_VERSION stays 1).
  Currently **empty in the DB** — the code catalogue exists, but nothing has
  ever called `sync_definitions()` against the car's database.
* **Provenance state machine.** `analysis/provenance.py` already separates
  live / imported / stale / simulated / unavailable and carries an `unverified`
  flag. Reuse this rather than inventing a second vocabulary.
* **Offline replay / evidence discipline.** `analysis/replay.py`
  (`Observation`, `EvidenceKind`, `Verdict`) — synthetic fixtures never
  corroborate. Reuse for the decoder lab.
* **Dashboard.** FastAPI + Jinja2 + Chart.js, no external deps beyond CDN.
  Pages: Overview, Progress, Measurements, Battery, Faults, Charging, AI,
  Sources; APIs: `/api/battery`, `/api/measurements/{key}`, `/api/progress`,
  `/api/health`.
* **Analytics.** `analysis/battery.py`, `dtc.py`, `charging.py`,
  `anomaly.py`, `stats.py` (transparent, no numpy).
* **AI.** `ai/service.py`, `ai/prompts.py`, `ai/reports.py` — evidence packet +
  post-generation validator, Ollama optional.
* **Tests.** `python -m pytest -q` → **658 passed** in ~118 s at baseline.

## 2. What the real data contains (key inventory highlights)

13 ECUs with readings. Known-bad scalings present in the real DB and **excluded
from scoring** by the brief (see `docs/DATA_CATALOGUE.md` for the full list):

`dc_link_voltage`/`dc_link_voltage_reserve` ≈1738 V (pack ≈435 V),
`ecu_pcb_temperature_1/2` ≈1681–1685 °C, `stator_temperature`,
`electric_drive_inflow_coolant_temperature` ≈1679–1680 °C,
`rotor_temperature_model`, `dmcm_stator_voltage_*` ≈1348–4918 V,
`actual_mechanical_torque_calculated` 1528–4945 Nm,
`inverter_power_loss_phase_u/v/w`, `windshield_temperature`, `dc_link_current`,
`co2_content_interior`=25400, `bat_charging_voltage` max 2048.

Healthy signals: `cell_v_*` 3.883–4.055 V, `soc_*`, `pack_voltage`, 12 V system.

Data kinds that must be handled, not averaged:
* **text** — `gear` ("P"), `vin`;
* **structured JSON** (numeric column NULL, payload in `text_value`) —
  `cell_soc_max`/`cell_soc_min`, `energy_counters`, `coolant_temps`,
  `battery_serial`;
* **imported Car Scanner** rows (`provenance` starts `imported`);
* **derived** — `cell_delta_mv` (calculated), `pack_power_kw` (calculated),
  `soh_pct` (estimated);
* **same quantity from independent ECUs** — odometer, motor rpm, HV current
  appear in several modules and may legitimately disagree.

## 3. What is missing for the brief

1. **Measurement-quality metadata** — decoder verification, provenance,
   known-scaling problems, plausibility, freshness, confidence, and an explicit
   *use-in-scoring* policy. Today this lives only as prose in
   `docs/DATA_CATALOGUE.md`; nothing machine-readable gates analysis on it.
2. **Real freshness classification** — "latest row = current" is used in
   places; carried-forward snapshots can mix 900 s-old values into a row.
   Silent modules (e.g. `chg` since 2026-09-20, `gps`) are indistinguishable
   from healthy empty ones on most pages.
3. **Longitudinal explorer** — no search across keys, no server-side
   downsampling, no event markers; `/api/measurements/{key}` returns every
   point.
4. **Vehicle-specific baselines** — anomaly detection uses a single whole-window
   mean/std (`analysis/anomaly.py`), so a slow trend poisons its own baseline.
5. **Fault investigation** — no separation of observation / association /
   explanation / confirmed diagnosis; no persisted, reproducible investigations.
6. **Decoder lab** — `validate_catalogue()` exists but is never run against the
   stored `raw_response` bytes; no workspace that isolates known-invalid keys.
7. **Reporting** — no export of a scoped evidence packet (CSV/JSON) with
   limitations and sample sizes.

## 4. Highest-value improvements, in order

1. Machine-readable **quality registry** (`decoders/quality.py`) and a
   **freshness** module (`analysis/freshness.py`) — everything else depends on
   them; they are also where the known-bad list becomes enforced rather than
   advisory.
2. **Server-side downsampling** (`analysis/series.py`) so hundreds of keys and
   tens of thousands of points stay responsive.
3. **Searchable discovery** (`analysis/discovery.py`) across all keys with
   quality + freshness filters.
4. **Vehicle-specific robust baselines** (`analysis/baseline.py`, median/MAD).
5. **Investigation workspace** (`analysis/investigation.py`) with evidence
   grading and persisted records.
6. **Decoder lab** (`analysis/decoder_lab.py`) re-decoding stored raw bytes.
7. **Timeline** (`analysis/timeline.py`) merging events, DTCs, charge
   boundaries and investigation anchors.
8. **Report/export** with citations and stated limitations.

## 5. Migrations required

All additive, `CREATE TABLE IF NOT EXISTS`, so **SCHEMA_VERSION stays 1** and
existing databases keep opening (the same convention documented above
`SCHEMA_VERSION` in `database/models.py`, and asserted by
`tests/test_definition_store.py`). New tables:

* `investigations` — a persisted, reproducible investigation with criteria and
  conclusion.
* `investigation_evidence` — the observation/association/explanation trail.
* `baselines` — computed vehicle-specific robust baselines with their window,
  method and sample size, so a report can be reproduced.

No existing column is altered and no data is rewritten. Existing tables
(`measurement_definitions`, `definition_evaluations`) are reused for the decoder
lab.

## 6. Non-negotiable constraints carried into every stage

* Never write to the vehicle; never add polling; analysis reads SQLite only.
* Never silently change a decoder scale factor. Known-invalid measurements stay
  visible in the decoder-lab workspace but are excluded from health scoring,
  fault attribution and rankings.
* Use each row's own `ts`; never interpolate across fault events, charging
  transitions or comms gaps; mark a relationship uncertain when a value was not
  freshly observed.
* Distinguish a silent module from a normal empty reading; never present an old
  reading as current.
* Synthetic fixtures are clearly separated from real vehicle records and never
  count as corroboration.
* AI is optional and must cite measurement IDs/timestamps/event IDs, state
  sample size and limitations, and include counter-evidence.
