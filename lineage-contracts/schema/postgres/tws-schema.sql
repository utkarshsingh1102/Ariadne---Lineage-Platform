-- =============================================================================
-- lineage-contracts :: postgres/tws-schema.sql
-- =============================================================================
-- TWS-parser-owned mirror schema. Applied by the tws-parser at startup
-- (alembic upgrade head) or manually via `psql -f tws-schema.sql`.
-- Idempotent: every statement uses IF NOT EXISTS.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS tws;

CREATE TABLE IF NOT EXISTS tws.schedules (
    schedule_id        TEXT PRIMARY KEY,
    workstation        TEXT NOT NULL,
    scheduler          TEXT,
    name               TEXT NOT NULL,
    run_cycle          TEXT,
    cron_equivalent    TEXT,
    valid_from         DATE,
    valid_to           DATE,
    start_time         TIME,
    end_time           TIME,
    priority           INT,
    carry_forward      BOOLEAN,
    raw_definition     TEXT,
    source_file        TEXT,
    parsed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_schedules_name        ON tws.schedules(name);
CREATE INDEX IF NOT EXISTS idx_schedules_start_time  ON tws.schedules(start_time);
CREATE INDEX IF NOT EXISTS idx_schedules_workstation ON tws.schedules(workstation);

CREATE TABLE IF NOT EXISTS tws.jobs (
    job_id             TEXT PRIMARY KEY,
    schedule_id        TEXT NOT NULL REFERENCES tws.schedules(schedule_id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    script_path        TEXT,
    script_args        TEXT,
    script_type        TEXT,
    stream_logon       TEXT,
    recovery           TEXT,
    description        TEXT,
    priority           INT,
    order_in_schedule  INT,
    parsed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_jobs_name        ON tws.jobs(name);
CREATE INDEX IF NOT EXISTS idx_jobs_script_path ON tws.jobs(script_path);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule_id ON tws.jobs(schedule_id);

CREATE TABLE IF NOT EXISTS tws.job_dependencies (
    job_id             TEXT NOT NULL REFERENCES tws.jobs(job_id) ON DELETE CASCADE,
    depends_on_job_id  TEXT NOT NULL REFERENCES tws.jobs(job_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, depends_on_job_id)
);

CREATE TABLE IF NOT EXISTS tws.schedule_dependencies (
    schedule_id            TEXT NOT NULL REFERENCES tws.schedules(schedule_id) ON DELETE CASCADE,
    depends_on_schedule_id TEXT NOT NULL,
    PRIMARY KEY (schedule_id, depends_on_schedule_id)
);

CREATE TABLE IF NOT EXISTS tws.resources (
    resource_id        TEXT PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    quantity           INT
);

CREATE TABLE IF NOT EXISTS tws.job_resources (
    job_id             TEXT NOT NULL REFERENCES tws.jobs(job_id) ON DELETE CASCADE,
    resource_id        TEXT NOT NULL REFERENCES tws.resources(resource_id),
    quantity_needed    INT,
    PRIMARY KEY (job_id, resource_id)
);

CREATE TABLE IF NOT EXISTS tws.file_watchers (
    file_watcher_id    TEXT PRIMARY KEY,
    path               TEXT NOT NULL,
    pattern            TEXT
);

CREATE TABLE IF NOT EXISTS tws.job_file_dependencies (
    job_id             TEXT NOT NULL REFERENCES tws.jobs(job_id) ON DELETE CASCADE,
    file_watcher_id    TEXT NOT NULL REFERENCES tws.file_watchers(file_watcher_id),
    PRIMARY KEY (job_id, file_watcher_id)
);

-- =============================================================================
-- Views — surface the most common operational lookups directly in SQL.
-- =============================================================================

CREATE OR REPLACE VIEW tws.v_jobs_with_schedule AS
SELECT
    j.job_id, j.name AS job_name, j.script_path, j.script_type,
    j.stream_logon, j.recovery,
    s.schedule_id, s.name AS schedule_name, s.workstation,
    s.start_time, s.end_time, s.run_cycle, s.valid_from, s.valid_to
FROM tws.jobs j
JOIN tws.schedules s ON j.schedule_id = s.schedule_id;

CREATE OR REPLACE VIEW tws.v_runtime_window AS
SELECT
    j.job_id, j.name AS job_name, j.script_path,
    s.name AS schedule_name, s.workstation,
    s.start_time, s.end_time
FROM tws.jobs j
JOIN tws.schedules s ON j.schedule_id = s.schedule_id
WHERE s.start_time IS NOT NULL;
