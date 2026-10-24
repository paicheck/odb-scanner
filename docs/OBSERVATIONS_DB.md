# Observation database

`data/observations.db` is a normalised, read-only analysis copy of everything the
collector has gathered: live measurements, battery sweeps, diagnostic history and
the JSON evidence archives, all queryable with plain SQL.

It is **not** the collector's database. `data/odb_scanner.db` stays the live,
append-only store that the car writes to. This warehouse is rebuilt from it, so
it can be deleted and recreated at any time without losing evidence.

```text
data/odb_scanner.db          live store (collector writes, never touched)
        │
        │  python tools/build_observations_db.py
        ▼
data/observations.db         normalised warehouse (this document)
        ▲
        │  loaded into capture / capture_record / expectation_check
research/measurement_discovery/captures/*.json
data/logs/climate_*.json
```

## Why a second database

The live store is shaped for writing while a car is connected: `measurements` is
one wide table with the signal key, provenance and documentation status repeated
on every row. That is the right trade-off for the collector, but it makes
questions like "what was the latest SoC per module?" or "which raw bytes back
this number?" repetitive hand-written joins.

The warehouse adds what writing does not need:

* dimensions (`vehicle`, `module`, `signal`, `data_source`) separate from facts;
* foreign keys, `CHECK` constraints and indexes;
* typed fact tables (`observation`, `cell_voltage`, `battery_snapshot`,
  `charging_session`, `dtc`, `diagnostic_event`, `tx_log`, `frame`);
* the JSON evidence archives as rows, not files;
* views for the common questions.

Raw payloads survive normalisation:

| Source payload | Warehouse column |
|---|---|
| hex bytes (`"C7"`, `"04280400"`) | `observation.raw_hex` / `capture_record.raw_hex` |
| non-hex text (`"displayed 12.3 V"`) | `observation.raw_text` |
| everything parsed | `value_num`, `value_text`, `unit` |

A row never invents bytes: `raw_hex` and `raw_text` are mutually exclusive by
constraint.

## Build and rebuild

```powershell
# default: source from config.yaml, output data/observations.db
python tools/build_observations_db.py

# rebuild over an existing warehouse
python tools/build_observations_db.py --force

# another source or target
python tools/build_observations_db.py --source data/real_car.db --out data/obs.db

# evidence archives only, or explicit files
python tools/build_observations_db.py --no-captures
python tools/build_observations_db.py --captures path/to/live.json path/to/scan.json

# machine-readable summary
python tools/build_observations_db.py --force --json
```

The build is deliberately hard to get wrong:

* the source is opened read-only and hashed before and after the load; a changed
  source aborts the build rather than producing a half-trusted copy;
* output is written to a temporary file, integrity-checked (`foreign_key_check`
  and `integrity_check`), vacuumed, then atomically replaced;
* an existing output is refused unless `--force` is passed;
* source and output may not be the same file.

`--force --json` on 2026-10-24 produced:

| Table / fact | Rows |
|---|---:|
| vehicles | 1 |
| modules | 17 |
| signals | 338 |
| observations | 19,575 |
| battery snapshots | 133 |
| cell voltages | 3,766 |
| charging sessions | 0 |
| diagnostic events | 123 |
| tx_log | 44,051 |
| captures | 8 |
| capture records | 367 |
| expectation checks | 75 |

The extra modules and signals (beyond the 14 ECUs and 329 live keys) come from
imported third-party data such as `8C.BMS`, and from signal keys that appear only
in capture archives. Counts therefore describe the warehouse, not a loader's
input list.

## Schema

```text
vehicle ─┬─ module ──────────────┐
         ├─ observation ────────┼── signal
         │      └── data_source │
         ├─ cell_voltage
         ├─ battery_snapshot
         ├─ charging_session ── charging_sample
         ├─ dtc ── dtc_occurrence
         ├─ diagnostic_event
         ├─ frame
         └─ tx_log (keyed by module_key)

capture ── capture_record ──┬── signal
                            └── expectation_check
```

Design notes worth knowing before writing queries:

* **`signal` is the catalogue**, one row per measurable key, whether or not it
  was ever observed. Definitions come from `decoders/catalogue.py`; observed-only
  keys get `confidence = 'UNKNOWN'` and a minimal record instead of being
  dropped. `definition_json` preserves the full definition.
* **`data_source` is a dimension**, not a repeated string. `kind` is the coarse
  vocabulary (`reported`, `calculated`, `estimated`, `simulated`, `imported`,
  `unknown`); `provenance` keeps the exact label
  (`imported: Car Scanner CSV`).
* **`observation.source_measurement_id`** points back to the source
  `measurements.id`, so any warehouse row can be traced to the live store.
* **`tx_log` has no foreign key to `module`**, because the safety manifest must
  keep requests even for module keys that were never registered.
* **`capture` is one per archive file.** A `.manifest.json` does not become its
  own capture: its hashes and notes are merged into the archive it describes. A
  validation report *is* its own capture (`kind = 'validation_report'`) because
  it is an independent artifact, and its verdicts become `expectation_check`
  rows.
* **`expectation_check` stores the verdict**, not a rule: a later decoder change
  cannot silently rewrite history. Duplicate `(key, raw)` records in an archive
  are matched one-to-one, so one verdict is not attached to several rows.
  Strict checks use `method = 'capture_validation_strict'`; ordinary reports use
  `capture_validation`.

`meta` records `schema_version`, `generated_at`, `source_db`,
`source_db_sha256`, `profile`, `catalogue_version` and `registry_signals`.

## Views

| View | Answers |
|---|---|
| `v_observation` | flattened facts with VIN, module and provenance names |
| `v_latest_observation` | newest successful value per vehicle and signal |
| `v_signal_catalogue` | every signal, its definition and observation counts |
| `v_daily_signal_stats` | per-day count, mean, min and max per signal |
| `v_observation_quality` | success rate and raw-byte coverage per signal |
| `v_module_summary` | signals and observations per module |
| `v_capture_record` | archive records with decoded value and latest verdict |
| `v_capture_summary` | per-archive record counts and time span |

## Example queries

Latest value of every signal that has been seen, newest first:

```sql
SELECT signal_key, value_num, unit, observed_at, provenance_kind
FROM v_latest_observation
ORDER BY observed_at DESC
LIMIT 25;
```

Daily SoC trend:

```sql
SELECT day, observations, mean_value, min_value, max_value
FROM v_daily_signal_stats
WHERE signal_key = 'soc_abs'
ORDER BY day;
```

Which values are imported rather than measured by the car:

```sql
SELECT provenance_kind, COUNT(*) AS rows
FROM v_observation
GROUP BY provenance_kind
ORDER BY rows DESC;
```

Find cells that are out of line in the latest sweep:

```sql
SELECT cell_index, voltage_v,
       voltage_v - AVG(voltage_v) OVER () AS delta_from_mean_v
FROM cell_voltage
WHERE observed_at = (SELECT MAX(observed_at) FROM cell_voltage)
ORDER BY ABS(voltage_v - (SELECT AVG(voltage_v) FROM cell_voltage
                          WHERE observed_at = (SELECT MAX(observed_at)
                                               FROM cell_voltage))) DESC
LIMIT 10;
```

Recover the raw bytes behind a parsed value:

```sql
SELECT observed_at, signal_key, value_num, unit, raw_response
FROM v_observation
WHERE signal_key = 'pack_voltage' AND raw_response IS NOT NULL
ORDER BY observed_at DESC
LIMIT 10;
```

Archive evidence and its independent verdict for one signal:

```sql
SELECT capture_file, capture_kind, observed_at, raw_hex,
       decoded_num, unit, verdict
FROM v_capture_record
WHERE signal_key = 'hv_energy_max'
ORDER BY capture_created_at, capture_record_id;
```

Which archives are loaded, and how complete each one is:

```sql
SELECT file_name, kind, record_count, records_loaded,
       distinct_signals, first_observed_at, last_observed_at
FROM v_capture_summary
ORDER BY kind, file_name;
```

Requests around a battery sweep (safety manifest):

```sql
SELECT observed_at, direction, module_key, payload, purpose
FROM tx_log
WHERE observed_at BETWEEN '2026-10-21T20:45:00+00:00'
                      AND '2026-10-21T20:46:00+00:00'
ORDER BY observed_at, tx_log_id;
```

## Regenerating after new data

```powershell
python main.py collect --cycles 20   # or however the data was gathered
python tools/build_observations_db.py --force
```

The warehouse is gitignored (`data/*.db`) and safe to delete. If the schema or
loader changes, bump `SCHEMA_VERSION` in `tools/build_observations_db.py`,
update `database/observations_schema.sql`, rebuild, and run:

```powershell
python -m pytest tests/test_observations_db.py -q
python -m ruff check tools/build_observations_db.py tests/test_observations_db.py
```

The tests pin the promises that matter: normalised dimensions, preserved raw
payloads, queryable captures, verdicts, and a source file that is byte-identical
before and after the build.
