-- Normalised observation warehouse for odb_scanner.
--
-- WHY A SECOND DATABASE
-- ---------------------
-- data/odb_scanner.db is the collector's live store. It is deliberately
-- append-only and denormalised (`measurements` is one wide fact table with a
-- free-text `key`, `provenance` and `doc_status` on every row) because the
-- collector must never block on a schema change while a car is connected.
--
-- This schema is the read/analysis counterpart: a star-ish model with typed
-- dimensions, foreign keys, checks and indexes. It is REBUILT from the live
-- store and the JSON evidence archives, never written by the collector, so a
-- bad decoder or a new decoder never requires migrating the car's history.
--
-- The load-bearing rule from the live store is preserved verbatim: raw
-- responses are never discarded. `observation.raw_hex` holds the exact bytes,
-- and `raw_text` holds non-hex payloads (imported exports) rather than
-- pretending they are bytes.
--
-- Rebuild with:
--     python tools/build_observations_db.py
--
-- The file is safe to delete; nothing reads it in production. It exists so a
-- question can be asked in SQL without repeating the joins by hand.

PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vehicle (
    vehicle_id INTEGER PRIMARY KEY,
    vin        TEXT NOT NULL UNIQUE,
    make       TEXT,
    model      TEXT,
    variant    TEXT,
    year       INTEGER,
    first_seen TEXT,
    source_db  TEXT
);

CREATE TABLE IF NOT EXISTS module (
    module_id  INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicle(vehicle_id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    name       TEXT,
    tx_id      INTEGER,
    rx_id      INTEGER,
    status     TEXT,
    doc_status TEXT,
    first_seen TEXT,
    last_seen  TEXT,
    UNIQUE (vehicle_id, key)
);

-- One row per measurable signal, whether or not it was ever observed.
-- Definition columns come from decoders/catalogue.py; observed-only keys
-- (imports, retired decoders) get confidence='UNKNOWN' and a minimal record
-- rather than being dropped.
CREATE TABLE IF NOT EXISTS signal (
    signal_id        INTEGER PRIMARY KEY,
    key              TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    unit             TEXT,
    module_key       TEXT,
    service           INTEGER,
    did               INTEGER,
    provenance        TEXT,
    doc_status        TEXT,
    confidence        TEXT NOT NULL DEFAULT 'UNKNOWN'
                      CHECK (confidence IN ('VERIFIED', 'PROVISIONAL', 'UNKNOWN')),
    formula           TEXT,
    byte_offset       INTEGER,
    byte_length       INTEGER,
    byte_order        TEXT,
    signed            INTEGER CHECK (signed IS NULL OR signed IN (0, 1)),
    scale             REAL,
    divisor           REAL,
    addend            REAL,
    plausible_min     REAL,
    plausible_max     REAL,
    plausible_basis   TEXT,
    evidence_kind     TEXT,
    evidence_source   TEXT,
    evidence_artifact TEXT,
    limitations       TEXT,
    falsified_by      TEXT,
    definition_version TEXT,
    definition_json   TEXT
);

CREATE INDEX IF NOT EXISTS ix_signal_module ON signal(module_key);
CREATE INDEX IF NOT EXISTS ix_signal_did ON signal(did);

-- Provenance is a dimension rather than a repeated string on every fact row.
-- `kind` is the coarse vocabulary the analysis code branches on; `provenance`
-- keeps the exact label (e.g. "imported: Car Scanner CSV") so attribution is
-- never lost to normalisation.
CREATE TABLE IF NOT EXISTS data_source (
    source_id  INTEGER PRIMARY KEY,
    provenance TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL
               CHECK (kind IN ('reported', 'calculated', 'estimated',
                               'simulated', 'imported', 'unknown'))
);

-- ---------------------------------------------------------------------------
-- Observation facts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS observation (
    observation_id        INTEGER PRIMARY KEY,
    vehicle_id            INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                          ON DELETE CASCADE,
    signal_id             INTEGER NOT NULL REFERENCES signal(signal_id),
    module_id             INTEGER REFERENCES module(module_id),
    observed_at           TEXT NOT NULL,
    value_num             REAL,
    value_text            TEXT,
    unit                  TEXT,
    source_id             INTEGER NOT NULL REFERENCES data_source(source_id),
    decoding_method       TEXT,
    doc_status            TEXT,
    raw_hex               TEXT,
    raw_text              TEXT,
    success               INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0, 1)),
    error                 TEXT,
    source_measurement_id INTEGER UNIQUE,
    CHECK (raw_hex IS NULL OR raw_text IS NULL),
    CHECK (success = 0 OR value_num IS NOT NULL OR value_text IS NOT NULL
           OR raw_hex IS NOT NULL OR raw_text IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_obs_signal_ts ON observation(signal_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_ts ON observation(observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_vehicle_ts ON observation(vehicle_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_source ON observation(source_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_module ON observation(module_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_obs_success ON observation(success, observed_at);

-- Per-cell voltages are a separate fact: one sweep writes ~108 rows at one
-- timestamp, and the cell index is part of the identity.
CREATE TABLE IF NOT EXISTS cell_voltage (
    cell_voltage_id        INTEGER PRIMARY KEY,
    vehicle_id             INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                           ON DELETE CASCADE,
    observed_at            TEXT NOT NULL,
    cell_index             INTEGER NOT NULL CHECK (cell_index >= 0),
    voltage_v              REAL NOT NULL,
    source_cell_voltage_id INTEGER UNIQUE,
    UNIQUE (vehicle_id, observed_at, cell_index)
);

CREATE INDEX IF NOT EXISTS ix_cell_ts ON cell_voltage(observed_at, cell_index);
CREATE INDEX IF NOT EXISTS ix_cell_vehicle_ts ON cell_voltage(vehicle_id, observed_at);

-- Aggregated battery snapshots. This is the battery-specific projection of
-- many individual observations; keeping it separate lets a session be
-- re-summarised later without touching the raw facts.
CREATE TABLE IF NOT EXISTS battery_snapshot (
    battery_snapshot_id        INTEGER PRIMARY KEY,
    vehicle_id                 INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                               ON DELETE CASCADE,
    observed_at                TEXT NOT NULL,
    soc_abs_pct                REAL,
    soc_normal_pct             REAL,
    pack_voltage_v             REAL,
    pack_current_a             REAL,
    pack_power_kw              REAL,
    cell_min_v                 REAL,
    cell_max_v                 REAL,
    cell_delta_mv              REAL,
    battery_temp_c             REAL,
    energy_charged_kwh         REAL,
    energy_used_kwh            REAL,
    soh_pct                    REAL,
    cac_ah                     REAL,
    charge_mode                TEXT,
    source_battery_measurement_id INTEGER UNIQUE,
    UNIQUE (vehicle_id, observed_at)
);

CREATE INDEX IF NOT EXISTS ix_batt_vehicle_ts ON battery_snapshot(vehicle_id, observed_at);

CREATE TABLE IF NOT EXISTS charging_session (
    charging_session_id  INTEGER PRIMARY KEY,
    vehicle_id           INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                         ON DELETE CASCADE,
    charge_type          TEXT CHECK (charge_type IS NULL OR charge_type IN ('AC', 'DC')),
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    start_soc            REAL,
    end_soc              REAL,
    duration_s           REAL CHECK (duration_s IS NULL OR duration_s >= 0),
    energy_estimate_kwh  REAL,
    max_power_kw         REAL,
    avg_battery_temp_c   REAL,
    status               TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'completed')),
    notes                TEXT,
    source_charging_session_id INTEGER UNIQUE
);

CREATE TABLE IF NOT EXISTS charging_sample (
    charging_sample_id        INTEGER PRIMARY KEY,
    charging_session_id       INTEGER NOT NULL
                              REFERENCES charging_session(charging_session_id)
                              ON DELETE CASCADE,
    observed_at               TEXT NOT NULL,
    voltage_v                 REAL,
    current_a                 REAL,
    power_kw                  REAL,
    soc                       REAL,
    battery_temp_c            REAL,
    source_charging_sample_id INTEGER UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_csample_session
    ON charging_sample(charging_session_id, observed_at);

-- ---------------------------------------------------------------------------
-- Faults and context
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dtc (
    dtc_id           INTEGER PRIMARY KEY,
    vehicle_id       INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                     ON DELETE CASCADE,
    module_key       TEXT,
    code             TEXT NOT NULL,
    description      TEXT,
    categories_json  TEXT NOT NULL DEFAULT '[]',
    status           TEXT,
    first_seen       TEXT,
    last_seen        TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    source_dtc_id    INTEGER UNIQUE,
    UNIQUE (vehicle_id, module_key, code)
);

CREATE TABLE IF NOT EXISTS dtc_occurrence (
    dtc_occurrence_id        INTEGER PRIMARY KEY,
    dtc_id                   INTEGER NOT NULL REFERENCES dtc(dtc_id)
                             ON DELETE CASCADE,
    observed_at              TEXT NOT NULL,
    freeze_frame_json        TEXT,
    source_dtc_occurrence_id INTEGER UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_dtc_occ_dtc ON dtc_occurrence(dtc_id, observed_at);

CREATE TABLE IF NOT EXISTS diagnostic_event (
    diagnostic_event_id        INTEGER PRIMARY KEY,
    vehicle_id                 INTEGER NOT NULL REFERENCES vehicle(vehicle_id)
                               ON DELETE CASCADE,
    observed_at                TEXT NOT NULL,
    kind                       TEXT NOT NULL,
    module_key                 TEXT,
    description                TEXT NOT NULL,
    data_json                  TEXT,
    source_diagnostic_event_id INTEGER UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_event_ts ON diagnostic_event(observed_at);

-- Every request ever transmitted. Kept in this database too because a value
-- without its request is not reproducible evidence.
CREATE TABLE IF NOT EXISTS tx_log (
    tx_log_id        INTEGER PRIMARY KEY,
    observed_at      TEXT NOT NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('TX', 'RX', 'NOTE')),
    module_key       TEXT,
    payload          TEXT,
    purpose          TEXT,
    source_tx_log_id INTEGER UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_tx_ts ON tx_log(observed_at);

-- Verbatim request/response pairs, when the collector captured them.
CREATE TABLE IF NOT EXISTS frame (
    frame_id             INTEGER PRIMARY KEY,
    vehicle_id           INTEGER REFERENCES vehicle(vehicle_id) ON DELETE SET NULL,
    observed_at          TEXT NOT NULL,
    module_key           TEXT NOT NULL,
    request_bytes        TEXT NOT NULL,
    response_raw_lines   TEXT,
    response_payload     TEXT,
    did_record           TEXT,
    answer_source_id     INTEGER,
    session_state        TEXT,
    succeeded            INTEGER NOT NULL DEFAULT 0 CHECK (succeeded IN (0, 1)),
    error                TEXT,
    note                 TEXT,
    source_frame_id      INTEGER UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_frame_ts ON frame(observed_at);

-- ---------------------------------------------------------------------------
-- Evidence archives (JSON captures)
-- ---------------------------------------------------------------------------
-- These tables are why the DB is worth building: the live_*, gateway_*,
-- passive_*, and strict_expected_* archives used to be unqueryable JSON.
-- A capture record keeps the archive's own fields in metadata_json, so an
-- unknown future field is preserved rather than dropped by the loader.

CREATE TABLE IF NOT EXISTS capture (
    capture_id   INTEGER PRIMARY KEY,
    file_name    TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    schema_name  TEXT,
    created_at   TEXT,
    sha256       TEXT,
    source       TEXT,
    notes        TEXT,
    record_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS capture_record (
    capture_record_id INTEGER PRIMARY KEY,
    capture_id        INTEGER NOT NULL REFERENCES capture(capture_id)
                      ON DELETE CASCADE,
    observed_at       TEXT,
    elapsed_s         REAL,
    signal_id         INTEGER REFERENCES signal(signal_id),
    signal_key        TEXT NOT NULL,
    raw_hex           TEXT,
    decoded_num       REAL,
    decoded_text      TEXT,
    unit              TEXT,
    expected_num      REAL,
    expected_text     TEXT,
    source            TEXT,
    metadata_json     TEXT
);

CREATE INDEX IF NOT EXISTS ix_caprec_capture
    ON capture_record(capture_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_caprec_signal
    ON capture_record(signal_id, observed_at);
CREATE INDEX IF NOT EXISTS ix_caprec_key ON capture_record(signal_key);

-- A check tied to one capture record. Verdicts are stored, never inferred at
-- query time: "PASS" in an archive means an independent value matched, and a
-- later decoder change must not silently rewrite that history.
CREATE TABLE IF NOT EXISTS expectation_check (
    expectation_check_id INTEGER PRIMARY KEY,
    capture_record_id    INTEGER NOT NULL
                         REFERENCES capture_record(capture_record_id)
                         ON DELETE CASCADE,
    method               TEXT NOT NULL DEFAULT 'capture_validation',
    expected_num         REAL,
    decoded_num          REAL,
    absolute_error       REAL,
    unit                 TEXT,
    verdict              TEXT NOT NULL
                         CHECK (verdict IN ('PASS', 'FAIL', 'INDETERMINATE')),
    issue                TEXT,
    source_file          TEXT
);

CREATE INDEX IF NOT EXISTS ix_check_record
    ON expectation_check(capture_record_id);

-- ---------------------------------------------------------------------------
-- Views: the reason to use this database instead of grepping the source
-- ---------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_observation AS
SELECT
    o.observation_id,
    o.signal_id,
    v.vehicle_id,
    v.vin,
    m.key  AS module_key,
    m.name AS module_name,
    s.key  AS signal_key,
    s.name AS signal_name,
    o.observed_at,
    o.value_num,
    o.value_text,
    COALESCE(o.unit, s.unit) AS unit,
    ds.provenance,
    ds.kind AS provenance_kind,
    o.decoding_method,
    o.doc_status,
    COALESCE(o.raw_hex, o.raw_text) AS raw_response,
    o.success,
    o.error
FROM observation o
JOIN vehicle v      ON v.vehicle_id = o.vehicle_id
JOIN signal s       ON s.signal_id = o.signal_id
JOIN data_source ds ON ds.source_id = o.source_id
LEFT JOIN module m  ON m.module_id = o.module_id;

CREATE VIEW IF NOT EXISTS v_latest_observation AS
SELECT * FROM (
    SELECT
        v_observation.*,
        ROW_NUMBER() OVER (
            PARTITION BY v_observation.vehicle_id, v_observation.signal_key
            ORDER BY v_observation.observed_at DESC,
                     v_observation.observation_id DESC
        ) AS rn
    FROM v_observation
    WHERE v_observation.success = 1
      AND v_observation.observed_at IS NOT NULL
) ranked
WHERE rn = 1;

CREATE VIEW IF NOT EXISTS v_signal_catalogue AS
SELECT
    s.signal_id,
    s.key        AS signal_key,
    s.name       AS signal_name,
    s.unit,
    s.module_key,
    s.did,
    s.confidence,
    s.provenance,
    s.doc_status,
    COUNT(o.observation_id) AS observations,
    COALESCE(SUM(o.success), 0) AS successful_observations,
    MIN(o.observed_at) AS first_observed_at,
    MAX(o.observed_at) AS last_observed_at,
    l.value_num  AS latest_value_num,
    l.value_text AS latest_value_text,
    l.observed_at AS latest_observed_at
FROM signal s
LEFT JOIN observation o ON o.signal_id = s.signal_id
LEFT JOIN v_latest_observation l ON l.signal_id = s.signal_id
GROUP BY
    s.signal_id, s.key, s.name, s.unit, s.module_key, s.did, s.confidence,
    s.provenance, s.doc_status, l.value_num, l.value_text, l.observed_at;

CREATE VIEW IF NOT EXISTS v_daily_signal_stats AS
SELECT
    v.vin,
    v.signal_key,
    v.unit,
    date(v.observed_at) AS day,
    COUNT(*) AS observations,
    SUM(v.success) AS successful_observations,
    AVG(CASE WHEN v.success = 1 THEN v.value_num END) AS mean_value,
    MIN(CASE WHEN v.success = 1 THEN v.value_num END) AS min_value,
    MAX(CASE WHEN v.success = 1 THEN v.value_num END) AS max_value
FROM v_observation v
WHERE v.observed_at IS NOT NULL
GROUP BY v.vin, v.signal_key, v.unit, date(v.observed_at);

CREATE VIEW IF NOT EXISTS v_observation_quality AS
SELECT
    s.signal_id,
    s.key AS signal_key,
    s.confidence,
    COUNT(o.observation_id) AS observations,
    COALESCE(SUM(o.success), 0) AS successful_observations,
    COALESCE(SUM(CASE WHEN o.raw_hex IS NOT NULL THEN 1 ELSE 0 END), 0)
        AS observations_with_raw_hex,
    COALESCE(SUM(CASE WHEN o.value_num IS NULL THEN 1 ELSE 0 END), 0)
        AS non_numeric_observations,
    CASE WHEN COUNT(o.observation_id) = 0 THEN NULL
         ELSE 1.0 * COALESCE(SUM(o.success), 0) / COUNT(o.observation_id)
    END AS success_rate
FROM signal s
LEFT JOIN observation o ON o.signal_id = s.signal_id
GROUP BY s.signal_id, s.key, s.confidence;

CREATE VIEW IF NOT EXISTS v_module_summary AS
SELECT
    m.vehicle_id,
    m.key AS module_key,
    m.name AS module_name,
    m.status,
    COUNT(DISTINCT o.signal_id) AS distinct_signals,
    COUNT(o.observation_id) AS observations,
    MIN(o.observed_at) AS first_observed_at,
    MAX(o.observed_at) AS last_observed_at
FROM module m
LEFT JOIN observation o ON o.module_id = m.module_id
GROUP BY m.vehicle_id, m.key, m.name, m.status;

CREATE VIEW IF NOT EXISTS v_capture_record AS
SELECT
    cr.capture_record_id,
    c.file_name AS capture_file,
    c.kind AS capture_kind,
    c.created_at AS capture_created_at,
    cr.observed_at,
    cr.elapsed_s,
    COALESCE(s.key, cr.signal_key) AS signal_key,
    s.name AS signal_name,
    cr.raw_hex,
    cr.decoded_num,
    cr.decoded_text,
    COALESCE(cr.unit, s.unit) AS unit,
    cr.expected_num,
    cr.source,
    (SELECT ec.verdict FROM expectation_check ec
      WHERE ec.capture_record_id = cr.capture_record_id
      ORDER BY ec.expectation_check_id DESC LIMIT 1) AS verdict
FROM capture_record cr
JOIN capture c ON c.capture_id = cr.capture_id
LEFT JOIN signal s ON s.signal_id = cr.signal_id;

CREATE VIEW IF NOT EXISTS v_capture_summary AS
SELECT
    c.capture_id,
    c.file_name,
    c.kind,
    c.created_at,
    c.sha256,
    c.record_count,
    COUNT(cr.capture_record_id) AS records_loaded,
    COUNT(DISTINCT cr.signal_key) AS distinct_signals,
    MIN(cr.observed_at) AS first_observed_at,
    MAX(cr.observed_at) AS last_observed_at
FROM capture c
LEFT JOIN capture_record cr ON cr.capture_id = c.capture_id
GROUP BY c.capture_id, c.file_name, c.kind, c.created_at, c.sha256, c.record_count;
