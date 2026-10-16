"""SQLite schema.

Design principles:
  * Every measurement carries a UTC timestamp.
  * Raw responses are NEVER discarded (measurements.raw_response holds the
    exact bytes returned by the vehicle, plus decoding method and provenance).
  * Charging samples are kept separately from session summaries so sessions
    can be re-summarised later with better logic.
  * tx_log documents every byte transmitted to the vehicle (safety manifest).
  * analysis_results / llm_reports / anomalies separate machine analysis from
    LLM interpretation.
"""
from __future__ import annotations

SCHEMA_VERSION = 1

# Additive schema notes (Phase B/D, 2026-10-15)
# -------------------------------------------
# `measurement_definitions`, `captured_frames`, `definition_evidence` and
# `definition_evaluations` are all CREATE TABLE IF NOT EXISTS additions that
# touch no existing table or column, so SCHEMA_VERSION deliberately stays 1.
# Bumping it would make every existing database -- including data/real_car.db,
# which holds the only real-world readings of 0x2AB8/0x2AB2 -- refuse to open
# behind SchemaMismatchError, and that refusal is the guard working as designed
# for a change it does not need to catch. The existing guard test asserts the
# literal "expects 1", so the guard's behaviour is unchanged.

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY,
    vin TEXT NOT NULL UNIQUE,
    make TEXT, model TEXT, variant TEXT, year INTEGER,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ecus (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    key TEXT NOT NULL,
    name TEXT, tx_id INTEGER, rx_id INTEGER,
    status TEXT,                  -- responder | refused | nrc-<code> |
                                  -- no-response | no-dids-registered
    doc_status TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    UNIQUE(vehicle_id, key)
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,             -- ISO-8601 UTC
    ecu TEXT, service TEXT, pid TEXT,
    key TEXT NOT NULL,
    value REAL,                   -- NULL when not decodable
    text_value TEXT,              -- for non-numeric decodes (JSON)
    unit TEXT,
    provenance TEXT NOT NULL,     -- reported | calculated | estimated
    decoding_method TEXT NOT NULL,
    doc_status TEXT,
    raw_response TEXT,            -- exact vehicle bytes (hex), never discarded
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_measurements_key_ts ON measurements(key, ts);
CREATE INDEX IF NOT EXISTS ix_measurements_ts ON measurements(ts);

CREATE TABLE IF NOT EXISTS battery_measurements (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    soc_abs_pct REAL, soc_normal_pct REAL,
    pack_voltage_v REAL, pack_current_a REAL, pack_power_kw REAL,
    cell_min_v REAL, cell_max_v REAL, cell_delta_mv REAL,
    battery_temp_c REAL,
    energy_charged_kwh REAL, energy_used_kwh REAL,
    soh_pct REAL, cac_ah REAL,
    charge_mode TEXT
);
CREATE INDEX IF NOT EXISTS ix_batt_ts ON battery_measurements(ts);

CREATE TABLE IF NOT EXISTS cell_voltages (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    cell_index INTEGER NOT NULL,
    voltage_v REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cell_ts ON cell_voltages(ts, cell_index);

CREATE TABLE IF NOT EXISTS charging_sessions (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    charge_type TEXT,             -- AC | DC
    started_at TEXT NOT NULL, ended_at TEXT,
    start_soc REAL, end_soc REAL,
    duration_s REAL, energy_estimate_kwh REAL,
    max_power_kw REAL, avg_battery_temp_c REAL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | completed
    notes TEXT
);

CREATE TABLE IF NOT EXISTS charging_samples (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES charging_sessions(id),
    ts TEXT NOT NULL,
    voltage_v REAL, current_a REAL, power_kw REAL,
    soc REAL, battery_temp_c REAL
);
CREATE INDEX IF NOT EXISTS ix_csample_session ON charging_samples(session_id, ts);

CREATE TABLE IF NOT EXISTS dtcs (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ecu TEXT NOT NULL,
    code TEXT NOT NULL,
    description TEXT,
    categories TEXT NOT NULL,      -- JSON list: informational, communication...
    status TEXT,                   -- active | stored | unknown
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(vehicle_id, ecu, code)
);

CREATE TABLE IF NOT EXISTS dtc_occurrences (
    id INTEGER PRIMARY KEY,
    dtc_id INTEGER NOT NULL REFERENCES dtcs(id),
    ts TEXT NOT NULL,
    freeze_frame TEXT              -- JSON, when available
);
-- No index here, and that is a measured decision rather than an oversight.
-- latest_freeze_frames() reads every row for the vehicle and json.loads each
-- one, so it is O(rows) whatever the index does; adding ix_occ_dtc_ts made it
-- 43% SLOWER (2439 ms -> 3492 ms over 518k rows, measured) by turning a
-- sequential scan into random I/O, while speeding up the 500-row
-- dtc_occurrences() read from 178 ms to 67 ms. The cost was on the query that
-- runs on every /dtcs page, /charging page and AI question; the benefit was on
-- one that was already fast. The actual fix is upstream in upsert_dtc(), which
-- stopped writing a row per poll: one continuously-stored fault now costs one
-- row instead of 17,280 a day.

CREATE TABLE IF NOT EXISTS diagnostic_events (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,            -- wake|sleep|charge-start|charge-end|adapter|note
    ecu TEXT,
    description TEXT NOT NULL,
    data TEXT                      -- JSON
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    analysis_type TEXT NOT NULL,   -- battery-cell-delta | 12v-events | ...
    subject TEXT,
    window_days INTEGER,
    result_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_reports (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    question TEXT,
    model TEXT,
    report_text TEXT NOT NULL,
    context_json TEXT,             -- the structured data the LLM received
    warnings TEXT                  -- JSON list of validator warnings
);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL, baseline_mean REAL, baseline_std REAL, zscore REAL,
    direction TEXT,                -- high | low
    description TEXT
);

CREATE TABLE IF NOT EXISTS tx_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,       -- TX | RX | NOTE
    ecu TEXT, payload TEXT,
    purpose TEXT                   -- why this was transmitted (safety manifest)
);

-- ======================================================================
-- Definition catalogue (additive; see the note above SCHEMA_VERSION)
-- ======================================================================
-- Why these tables exist. `measurements` stores OBSERVATIONS and
-- `decoders/registry.py` stores DECODERS, but nothing recorded the thing a
-- reader actually needs to judge a number: where the definition came from,
-- how sure we are, and what would overturn it. That gap had a concrete cost
-- here -- two byte layouts for DID 0x2AB8 existed for months, the refuted one
-- shipped as the default, and nothing recorded that it had been refuted.
-- See docs/investigation/phase_c_energy_validation.md.
--
-- `measurement_definitions` is the versioned definition record.
-- `definition_evidence` is the audit trail of what moved a definition's
-- confidence, kept separate so the current value and the history cannot
-- disagree. `captured_frames` stores raw request/response pairs verbatim so
-- a definition can be re-decoded later under a different version -- the
-- project rule "raw bytes are never discarded" extended to definitions.
CREATE TABLE IF NOT EXISTS measurement_definitions (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    version TEXT NOT NULL,         -- catalogue version + digest of the layout
    name TEXT NOT NULL,
    unit TEXT,
    confidence TEXT NOT NULL,      -- VERIFIED | PROVISIONAL | UNKNOWN
    ecu_key TEXT, ecu_name TEXT,
    request_can_id INTEGER, response_can_id INTEGER,
    address_confidence TEXT,
    transport TEXT, session_required TEXT,
    service INTEGER, did INTEGER,
    request_bytes TEXT,
    byte_offset INTEGER, byte_length INTEGER, byte_order TEXT, signed INTEGER,
    scale REAL, divisor REAL, addend REAL, formula TEXT,
    response_length_hint INTEGER,
    plausible_min REAL, plausible_max REAL, plausible_basis TEXT,
    provenance TEXT, doc_status TEXT,
    evidence_kind TEXT, evidence_source TEXT, evidence_artifact TEXT,
    recorded_at TEXT NOT NULL,
    validation_method TEXT,
    limitations TEXT, falsified_by TEXT, competing_interpretations TEXT,
    definition_json TEXT NOT NULL,  -- full record, so nothing is lost
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(key, version)
);
CREATE INDEX IF NOT EXISTS ix_defs_key ON measurement_definitions(key, active);

CREATE TABLE IF NOT EXISTS definition_evidence (
    id INTEGER PRIMARY KEY,
    definition_id INTEGER NOT NULL REFERENCES measurement_definitions(id),
    ts TEXT NOT NULL,
    observation TEXT NOT NULL,    -- what was seen / computed
    kind TEXT NOT NULL,           -- vehicle_response | arithmetic | refutation
                                    -- | third_party_definition | review
    confidence_before TEXT, confidence_after TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_defev_def ON definition_evidence(definition_id, ts);

CREATE TABLE IF NOT EXISTS captured_frames (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    ecu TEXT NOT NULL,
    request_bytes TEXT NOT NULL,   -- exact bytes transmitted
    response_raw_lines TEXT,       -- verbatim adapter lines, unaltered
    response_payload TEXT,         -- reassembled UDS payload, hex
    did_record TEXT,               -- after marker + DID echo stripped
    answer_source_id INTEGER,      -- CAN id the reply arrived on, when known
    session_state TEXT,            -- e.g. ignition, charging, soC -- context only
    succeeded INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_frames_ecu_ts ON captured_frames(ecu, ts);
CREATE INDEX IF NOT EXISTS ix_frames_did ON captured_frames(request_bytes, ts);

CREATE TABLE IF NOT EXISTS definition_evaluations (
    id INTEGER PRIMARY KEY,
    definition_id INTEGER NOT NULL REFERENCES measurement_definitions(id),
    ts TEXT NOT NULL,
    method TEXT NOT NULL,          -- offline_replay | arithmetic | live_compare
    verdict TEXT NOT NULL,         -- PASS | FAIL | INDETERMINATE
    n_samples INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

-- DIDs the collector has retired from polling. Two kinds:
--   * NRC 0x31 (requestOutOfRange) -- the car definitively says it does not
--     implement the DID (the generic Car Scanner MEB profile names it, this
--     specific ID.3 does not).
--   * consistently silent -- the DID has never succeeded and has returned NO
--     DATA for collector.silent_retire_threshold consecutive reads (a sleeping
--     car looks like this, so a DID that has EVER succeeded is never retired).
-- The collector skips these rather than re-polling known-dead DIDs every cycle.
-- Additive, so SCHEMA_VERSION stays 1 (see the note above).
CREATE TABLE IF NOT EXISTS unsupported_dids (
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ecu_key TEXT NOT NULL,
    did INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (vehicle_id, ecu_key, did)
);
"""
