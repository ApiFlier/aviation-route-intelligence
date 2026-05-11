-- FlightConn SWIM Schema — Phase 1
--
-- Optional tables for FAA SWIM recent flight activity ingestion.
-- These tables are NOT required for the main app to run.
-- The main app (Route Map, Opportunities, Airline Health) functions normally
-- whether or not these tables exist.
--
-- Apply manually when ready:
--
--   docker exec -i \
--     -e MYSQL_PWD=<DB_PASSWORD> \
--     flightconn-db \
--     mysql -u flightconn flightconn < api/Data/swim_schema.sql
--
-- All statements use CREATE TABLE IF NOT EXISTS — safe to re-run.
-- These tables do not alter or reference existing BTS history tables.

-- ─────────────────────────────────────────────────────────────────────────────
-- Layer 0: Ingestion Audit Trail
--
-- Tracks every ingestor run so the app can distinguish
-- "no flights observed on this route" from "ingestor was down."
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS swim_ingestion_runs (
    id              INT UNSIGNED    NOT NULL AUTO_INCREMENT PRIMARY KEY,
    started_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at        DATETIME,
    status          ENUM('running','completed','failed','paused')
                        NOT NULL DEFAULT 'running',
    source          VARCHAR(100)    NOT NULL DEFAULT 'FAA_SWIM',
    messages_recv   INT UNSIGNED    NOT NULL DEFAULT 0,
    messages_ok     INT UNSIGNED    NOT NULL DEFAULT 0,
    messages_err    INT UNSIGNED    NOT NULL DEFAULT 0,
    flights_new     INT UNSIGNED    NOT NULL DEFAULT 0,
    flights_updated INT UNSIGNED    NOT NULL DEFAULT 0,
    error_detail    TEXT,
    INDEX idx_started       (started_at),
    INDEX idx_status_time   (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Short-lived raw message store for debugging and potential reprocessing.
-- Purged by the sidecar retention cleanup on SWIM_RAW_RETENTION_DAYS schedule.
CREATE TABLE IF NOT EXISTS swim_source_messages (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    run_id      INT UNSIGNED    NOT NULL,
    received_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    topic       VARCHAR(200),
    msg_type    VARCHAR(50),
    raw_body    MEDIUMTEXT,
    parsed_ok   TINYINT(1)      NOT NULL DEFAULT 0,
    error_note  VARCHAR(500),
    INDEX idx_received  (received_at),
    INDEX idx_run       (run_id),
    FOREIGN KEY (run_id) REFERENCES swim_ingestion_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─────────────────────────────────────────────────────────────────────────────
-- Layer 1: Normalized Operational Data
--
-- One row per observed flight leg. Source-agnostic — can accept SWIM TFMS,
-- SFDPS, STDDS, or future alternative feeds (e.g. OpenSky) without schema
-- changes by varying the data_source column.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS observed_flights (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    -- Source tracking (source_msg_id is nullable: raw messages may be purged)
    source_msg_id    BIGINT UNSIGNED,
    source_flight_id VARCHAR(50),
    data_source      VARCHAR(50)     NOT NULL DEFAULT 'FAA_SWIM',
    -- Flight identity (from callsign/ACID; carrier_code is derived)
    callsign         VARCHAR(10),
    tail_number      VARCHAR(12),
    acid             VARCHAR(20),
    carrier_code     VARCHAR(10),
    flight_number    VARCHAR(10),
    -- Route
    origin_iata      VARCHAR(3),
    dest_iata        VARCHAR(3),
    -- Schedule and actuals (UTC)
    sched_dep_utc    DATETIME,
    actual_dep_utc   DATETIME,
    sched_arr_utc    DATETIME,
    actual_arr_utc   DATETIME,
    dep_delay_mins   SMALLINT,
    arr_delay_mins   SMALLINT,
    -- Status
    flight_status    ENUM('scheduled','active','completed','cancelled','diverted','unknown')
                         NOT NULL DEFAULT 'unknown',
    cancel_code      VARCHAR(5),
    divert_airport   VARCHAR(3),
    aircraft_type    VARCHAR(10),
    -- Audit
    first_seen_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                         ON UPDATE CURRENT_TIMESTAMP,
    -- Deduplication: one row per source flight ID per data source
    UNIQUE KEY uk_source_flight     (source_flight_id, data_source),
    -- Query patterns: route+time, carrier-route+time, carrier+time, tail+time
    INDEX idx_od_dep                (origin_iata, dest_iata, sched_dep_utc),
    INDEX idx_od_carrier_dep        (origin_iata, dest_iata, carrier_code, sched_dep_utc),
    INDEX idx_carrier_dep           (carrier_code, sched_dep_utc),
    INDEX idx_tail_dep              (tail_number, sched_dep_utc),
    INDEX idx_origin_dep            (origin_iata, sched_dep_utc),
    INDEX idx_dest_arr              (dest_iata, sched_arr_utc),
    INDEX idx_status                (flight_status),
    INDEX idx_first_seen            (first_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Timeline of status-change events for an observed flight.
-- Only populated when the feed provides event messages (gate-out, wheels-off, etc.).
-- Designed to support future airport ground efficiency and turnaround analytics.
CREATE TABLE IF NOT EXISTS observed_flight_events (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    flight_id   BIGINT UNSIGNED NOT NULL,
    event_type  ENUM(
                    'gate_out',
                    'wheels_off',
                    'wheels_on',
                    'gate_in',
                    'cancelled',
                    'diverted',
                    'delay_update',
                    'position'
                ) NOT NULL,
    event_utc   DATETIME(3)     NOT NULL,
    airport     VARCHAR(3),
    detail_json JSON,
    INDEX idx_flight        (flight_id),
    INDEX idx_type_time     (event_type, event_utc),
    FOREIGN KEY (flight_id) REFERENCES observed_flights(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Companion enrichment table for analysis-worthy fields
-- Stores fields that support future route, carrier, reliability, and delay analysis
-- Keyed by source_flight_id to allow easy upserts alongside observed_flights
CREATE TABLE IF NOT EXISTS observed_flight_enrichment (
    source_flight_id       VARCHAR(50) NOT NULL PRIMARY KEY,
    source_system          VARCHAR(50),
    message_type           VARCHAR(50),
    operating_carrier_code VARCHAR(10),
    major_carrier_code     VARCHAR(10),
    flight_type            VARCHAR(50),
    user_category          VARCHAR(50),
    aircraft_category      VARCHAR(50),
    route_of_flight        TEXT,
    departure_procedure    VARCHAR(50),
    arrival_procedure      VARCHAR(50),
    route_amended          TINYINT(1) DEFAULT 0,
    last_updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_cat     (user_category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ─────────────────────────────────────────────────────────────────────────────
-- Layer 2: App-Facing Intelligence Summaries
--
-- These are the tables the Flask app actually queries.
-- Populated and refreshed by the sidecar aggregator (Phase 3).
-- The app reads these — it never reads observed_flights directly.
-- ─────────────────────────────────────────────────────────────────────────────

-- Route-level recent activity summary.
-- One row per origin/destination pair. Refreshed by the sidecar on a schedule.
-- The display_mode and confidence fields drive frontend rendering decisions —
-- the backend makes this call so the frontend does not hardcode thresholds.
CREATE TABLE IF NOT EXISTS recent_route_activity (
    id                     INT UNSIGNED    NOT NULL AUTO_INCREMENT PRIMARY KEY,
    origin_iata            VARCHAR(3)      NOT NULL,
    dest_iata              VARCHAR(3)      NOT NULL,
    coverage_start_date    DATE            NOT NULL,
    coverage_end_date      DATE            NOT NULL,
    coverage_days          SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    observation_count      INT UNSIGNED    NOT NULL DEFAULT 0,
    -- JSON arrays: observed_carriers = ["DL","WN","AA"]
    observed_carriers      JSON,
    carrier_count_observed TINYINT UNSIGNED NOT NULL DEFAULT 0,
    -- JSON objects: {"Mon":4,"Tue":3,...} and [{"window":"06-09","count":9},...]
    common_dep_days        JSON,
    common_dep_windows     JSON,
    last_observed_at       DATETIME,
    last_observed_carrier  VARCHAR(10),
    -- Integration metadata
    display_mode           ENUM(
                               'historical_only',
                               'early_recent_signal',
                               'blend_recent_and_historical',
                               'recent_activity_primary'
                           ) NOT NULL DEFAULT 'historical_only',
    activity_classification VARCHAR(50) DEFAULT 'insufficient_data',
    commercial_confidence   ENUM('none', 'low', 'medium', 'high') DEFAULT 'none',
    classification_note     VARCHAR(500) NULL,
    confidence             ENUM('none','low','medium','high') NOT NULL DEFAULT 'none',
    confidence_note        VARCHAR(500),
    -- Lets the app detect if the ingestor has gone silent
    ingestor_last_seen_at  DATETIME,
    computed_at            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_route     (origin_iata, dest_iata),
    INDEX idx_origin        (origin_iata),
    INDEX idx_computed      (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Carrier-route recent activity summary.
-- One row per (origin, destination, carrier). Refreshed alongside recent_route_activity.
CREATE TABLE IF NOT EXISTS recent_route_carrier_activity (
    id                  INT UNSIGNED    NOT NULL AUTO_INCREMENT PRIMARY KEY,
    origin_iata         VARCHAR(3)      NOT NULL,
    dest_iata           VARCHAR(3)      NOT NULL,
    carrier_code        VARCHAR(10)     NOT NULL,
    coverage_start_date DATE            NOT NULL,
    coverage_end_date   DATE            NOT NULL,
    coverage_days       SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    observation_count   INT UNSIGNED    NOT NULL DEFAULT 0,
    activity_classification VARCHAR(50) DEFAULT 'insufficient_data',
    commercial_confidence   ENUM('none', 'low', 'medium', 'high') DEFAULT 'none',

    common_dep_days     JSON,
    common_dep_windows  JSON,
    avg_dep_delay_mins  DECIMAL(6,1),
    avg_arr_delay_mins  DECIMAL(6,1),
    cancel_count        INT UNSIGNED    NOT NULL DEFAULT 0,
    last_observed_at    DATETIME,
    computed_at         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_route_carrier     (origin_iata, dest_iata, carrier_code),
    INDEX idx_origin_carrier        (origin_iata, carrier_code),
    INDEX idx_computed              (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Historical vs recent carrier comparison.
-- One row per (origin, destination, carrier).
-- in_historical_data: carrier appears in BTS route_carriers for this route.
-- in_recent_activity: carrier has been observed recently.
-- mismatch_flag=1 means: historically active but not recently observed —
-- this is flagged in the UI as a possible service-change signal, with careful
-- language noting it may reflect seasonality, incomplete data, or carrier-code
-- differences rather than a confirmed route exit.
CREATE TABLE IF NOT EXISTS route_historical_recent_comparison (
    id                       INT UNSIGNED    NOT NULL AUTO_INCREMENT PRIMARY KEY,
    origin_iata              VARCHAR(3)      NOT NULL,
    dest_iata                VARCHAR(3)      NOT NULL,
    carrier_code             VARCHAR(10)     NOT NULL,
    in_historical_data       TINYINT(1)      NOT NULL DEFAULT 0,
    in_recent_activity       TINYINT(1)      NOT NULL DEFAULT 0,
    historical_passengers    BIGINT,
    recent_observation_count INT UNSIGNED    NOT NULL DEFAULT 0,
    -- mismatch_flag: 1 when in_historical_data=1 and in_recent_activity=0
    mismatch_flag            TINYINT(1)      NOT NULL DEFAULT 0,
    mismatch_note            VARCHAR(500),
    computed_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_comparison    (origin_iata, dest_iata, carrier_code),
    INDEX idx_mismatch          (mismatch_flag),
    INDEX idx_origin            (origin_iata),
    INDEX idx_computed          (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
