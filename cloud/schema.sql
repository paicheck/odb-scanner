-- Cloud schema for odb_scanner -- MySQL / MariaDB, InnoDB.
--
-- One shared destination for BOTH writers: the Windows Python collector and
-- the Android app. Neither talks to MySQL directly; both POST decoded
-- measurements to the ingest API (cloud/api.py), which owns this schema and
-- the database credentials. That is why there is no per-client user or grant
-- here -- the API is the single choke point and the single place a password
-- lives.
--
-- Design rules carried over verbatim from the local SQLite schema
-- (database/models.py), because rows from the two writers must be
-- indistinguishable:
--   * every row carries a UTC timestamp;
--   * the raw vehicle bytes are never discarded (`raw_response`);
--   * a row is an immutable observation, so a duplicate id is IGNORED rather
--     than merged -- re-sends after a dropped connection cannot corrupt it.
--
-- `id` is a client-generated UUID and doubles as the idempotency key. The
-- Python collector derives it deterministically from (source_id, local row id)
-- so a retry re-sends the same id; the Android app stores a random UUID on the
-- row when it is written. Either way the cloud can deduplicate without trusting
-- the transport.

CREATE TABLE IF NOT EXISTS sources (
    source_id  VARCHAR(120) NOT NULL,
    kind       VARCHAR(16)  NOT NULL,          -- 'python' | 'android'
    label      VARCHAR(200) NOT NULL DEFAULT '',
    first_seen DATETIME(3)  NOT NULL,
    last_seen  DATETIME(3)  NOT NULL,
    PRIMARY KEY (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS measurements (
    id              CHAR(36)     NOT NULL,     -- client UUID: the idempotency key
    source_id       VARCHAR(120) NOT NULL,
    vin             VARCHAR(17)  NOT NULL,
    ts              DATETIME(3)  NOT NULL,     -- UTC, normalised by the API
    ts_raw          VARCHAR(32)  NOT NULL,     -- exact stamp the client sent
    ecu             VARCHAR(40)  NULL,
    service         VARCHAR(40)  NULL,
    pid             VARCHAR(40)  NULL,
    `key`           VARCHAR(80)  NOT NULL,     -- `key` is reserved in MySQL
    value           DOUBLE       NULL,
    text_value      TEXT         NULL,
    unit            VARCHAR(24)  NULL,
    provenance      VARCHAR(24)  NOT NULL,     -- reported | calculated | estimated
    decoding_method VARCHAR(120) NOT NULL,
    doc_status      VARCHAR(160) NULL,
    raw_response    TEXT         NULL,
    success         TINYINT(1)   NOT NULL DEFAULT 1,
    error           TEXT         NULL,
    received_at     DATETIME(3)  NOT NULL,     -- when the API accepted the row
    PRIMARY KEY (id),
    KEY ix_meas_vin_ts (vin, ts),
    KEY ix_meas_key_ts (`key`, ts),
    KEY ix_meas_source_ts (source_id, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
