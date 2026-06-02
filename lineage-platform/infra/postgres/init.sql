-- Postgres init. Runs once on first volume creation.
-- TWS schema lands here in Phase 2 (sourced from lineage-contracts/schema/postgres/tws-schema.sql).
-- For Phase 0 we just create the schema and a sanity row.

CREATE SCHEMA IF NOT EXISTS lineage_meta;

CREATE TABLE IF NOT EXISTS lineage_meta.bootstrap (
    component  TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO lineage_meta.bootstrap (component) VALUES ('phase-0')
ON CONFLICT (component) DO NOTHING;

-- ============================================================================
-- Projects — user-named groupings of parsed files across all source types.
-- Within a project, files are auto-organised by their source_type
-- (tableau/tws/qlikview/spark) for the Files page's project view.
-- ============================================================================
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_files (
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    neo4j_id     TEXT NOT NULL,                          -- Neo4j node id of the file's top-level node
    source_type  TEXT NOT NULL CHECK (source_type IN ('tableau', 'tws', 'qlikview', 'spark')),
    file_name    TEXT,                                   -- snapshot of name at time of grouping
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, neo4j_id)
);
CREATE INDEX IF NOT EXISTS project_files_project_idx ON project_files (project_id);
CREATE INDEX IF NOT EXISTS project_files_source_idx ON project_files (source_type);
