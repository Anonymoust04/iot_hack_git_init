-- =====================================================================
-- Car Park Management — MySQL 8 schema (Level 1)
--
-- This file is the SINGLE SOURCE OF TRUTH for the database structure.
-- The SQLAlchemy models in backend/app/models/ must match it
-- (backend/tests/test_schema_matches_models.py checks this).
--
-- Apply:   cd backend && python -m app.db.init_db
-- Reset:   cd backend && python -m app.db.init_db --reset   (DROPS ALL DATA)
--
-- All DATETIME values are UTC (the backend forces the session time zone to +00:00).
-- Statements are separated by ";" at end of line — keep it that way (init_db splits on it).
-- =====================================================================


-- ---------------------------------------------------------------------
-- users: dashboard logins (NOT simulator logins)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    username      VARCHAR(64)   NOT NULL,
    password_hash VARCHAR(255)  NOT NULL,            -- bcrypt hash, never plaintext
    role          ENUM('ADMIN','OPERATOR') NOT NULL DEFAULT 'OPERATOR',
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- parking_spots: latest known state of every spot.
-- Rows are UPSERTED from GET /api/v1/list-parking-spots at level start.
-- Never insert spots by hand.
--
-- status (our app's view):
--   FREE         can be assigned
--   RESERVED     assigned to current_car, car is driving there
--   OCCUPIED     car detected in the spot
--   BROKEN       simulator says broken
--   MAINTENANCE  under repair
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parking_spots (
    id                INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name              VARCHAR(32)  NOT NULL,          -- simulator name, e.g. S150
    zone              VARCHAR(32)  NOT NULL DEFAULT '',
    purpose           ENUM('Park','EntrySpot','ExitSpot') NOT NULL,
    car_type          ENUM('Electric','Accessible','Any') NOT NULL DEFAULT 'Any',
    status            ENUM('FREE','RESERVED','OCCUPIED','BROKEN','MAINTENANCE') NOT NULL DEFAULT 'FREE',
    current_car       VARCHAR(32)  NULL,              -- plate reserved/parked here
    broken            BOOLEAN      NOT NULL DEFAULT FALSE,
    under_maintenance BOOLEAN      NOT NULL DEFAULT FALSE,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_parking_spots_name (name),
    -- used by the allocation query: WHERE purpose='Park' AND status='FREE' AND car_type=?
    KEY ix_parking_spots_alloc (purpose, status, car_type),
    KEY ix_parking_spots_zone (zone),
    KEY ix_parking_spots_current_car (current_car)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- gates: latest known barrier state.
-- Rows are UPSERTED from GET /api/v1/list-barriers at level start,
-- then kept up to date from webhook events.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gates (
    id                INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name              VARCHAR(32)  NOT NULL,          -- simulator name, e.g. gate1
    zone              VARCHAR(32)  NOT NULL DEFAULT '',
    state             ENUM('Open','Closed','Opening','Closing') NOT NULL DEFAULT 'Closed',
    broken            BOOLEAN      NOT NULL DEFAULT FALSE,
    under_maintenance BOOLEAN      NOT NULL DEFAULT FALSE,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_gates_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- parking_sessions: ONE ROW = ONE CAR VISIT. Never deleted (history).
--
-- status:          ENTERING -> PARKED -> EXITING -> COMPLETED
-- payment_status:  PENDING  -> PAID
-- A car has at most one session whose status <> 'COMPLETED' (enforced in code).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parking_sessions (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    car_plate       VARCHAR(32)   NOT NULL,
    car_type        ENUM('Electric','Accessible','Any') NULL,  -- NULL until known
    parking_spot_id INT UNSIGNED  NULL,                -- NULL until a spot is assigned
    entry_time      DATETIME      NOT NULL,            -- car arrived at entrance
    parked_time     DATETIME      NULL,                -- car detected in its spot
    exit_time       DATETIME      NULL,                -- car charged at exit (billing end)
    parking_cost    DECIMAL(10,2) NULL,                -- NULL = not charged yet
    charging_cost   DECIMAL(10,2) NULL,
    payment_status  ENUM('PENDING','PAID') NOT NULL DEFAULT 'PENDING',
    status          ENUM('ENTERING','PARKED','EXITING','COMPLETED') NOT NULL DEFAULT 'ENTERING',
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_sessions_spot FOREIGN KEY (parking_spot_id) REFERENCES parking_spots (id),
    KEY ix_sessions_plate_status (car_plate, status),   -- "active session for plate X"
    KEY ix_sessions_status (status),                    -- active sessions / cars inside
    KEY ix_sessions_entry_time (entry_time),            -- history search by date
    KEY ix_sessions_exit_time (exit_time)               -- today's completed / revenue
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- ---------------------------------------------------------------------
-- events: audit log + dashboard activity feed.
-- Raw simulator webhook payloads are stored in raw_data, untouched.
-- event_type examples: WEBHOOK (raw, not yet understood), CAR_ARRIVED, SPOT_ASSIGNED,
--   CAR_REJECTED, CAR_PARKED, CAR_EXITING, CAR_CHARGED, CAR_DEPARTED, GATE_COMMAND, SYNC,
--   PAYMENT_ACCEPTED, PAYMENT_REJECTED, PENALTY, CO_ALERT, WEBHOOK_BAD_SIGNATURE, SEQUENCE_GAP
-- event_id / sequence_id are only set on WEBHOOK rows (simulator EventId / SequenceId).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_type   VARCHAR(64)  NOT NULL,
    car_plate    VARCHAR(32)  NULL,
    parking_spot VARCHAR(32)  NULL,               -- spot NAME (kept even if spot changes)
    gate_name    VARCHAR(32)  NULL,
    event_time   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_data     JSON         NULL,
    event_id     VARCHAR(64)  NULL,               -- simulator EventId; UNIQUE = each webhook handled once
    sequence_id  BIGINT UNSIGNED NULL,            -- simulator SequenceId (gap / order detection)
    PRIMARY KEY (id),
    UNIQUE KEY uq_events_event_id (event_id),
    KEY ix_events_sequence_id (sequence_id),
    KEY ix_events_time (event_time),
    KEY ix_events_plate (car_plate),
    KEY ix_events_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- login_attempts: every dashboard login, successful or failed (Level 2).
-- Never stores passwords, hashes or tokens. username is what was typed, so
-- failed attempts may name users that don't exist (kept on purpose).
-- Written by app/services/login_attempts.py.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS login_attempts (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username      VARCHAR(64)  NOT NULL,
    success       BOOLEAN      NOT NULL,
    ip_address    VARCHAR(45)  NULL,              -- IPv4 or IPv6
    attempted_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY ix_login_attempts_user_time (username, attempted_at),  -- "last 3 attempts of this user"
    KEY ix_login_attempts_time (attempted_at)                  -- recent / failed attempts overall
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------
-- audit_logs: who did what, to which component, and whether it worked
-- (Level 2). Covers operator actions (gate open, repairs, user changes) and
-- system actions (automation: actor NULL). Only Admin can read it.
-- details never holds passwords or tokens (services/audit.py masks them).
-- Written by app/services/audit.py.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    actor        VARCHAR(64)  NULL,                -- username; NULL = the system (automation)
    action       VARCHAR(64)  NOT NULL,            -- GATE_OPEN, SPOT_REPAIR, FAN_ON, USER_CREATED...
    target_type  VARCHAR(32)  NULL,                -- gate | spot | light | fan | user | system
    target_name  VARCHAR(64)  NULL,                -- gateA, S12, op1...
    success      BOOLEAN      NOT NULL DEFAULT TRUE,
    details      JSON         NULL,
    ip_address   VARCHAR(45)  NULL,                -- IPv4 or IPv6
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY ix_audit_logs_time (created_at),                        -- newest first / by date
    KEY ix_audit_logs_actor_time (actor, created_at),           -- "what did this user do"
    KEY ix_audit_logs_action_time (action, created_at),         -- "all repairs today"
    KEY ix_audit_logs_target (target_type, target_name)         -- "history of gateA"
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
