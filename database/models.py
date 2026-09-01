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
    status TEXT,                  -- responder | no-response | nrc-<code>
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

CREATE TABLE IF NOT EXISTS diagnostic_events (
    id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,            -- wake | sleep | charge-start | charge-end | adapter | note
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
"""
