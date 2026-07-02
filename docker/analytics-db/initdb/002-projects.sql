-- Projects — groups sessions (pipeline_run_items) the way the reference
-- Copilot Cost dashboard groups them, independent of which gru pipeline
-- produced the data. A pipeline run/item is linked to a project once known;
-- until then it falls into the synthetic "Unlinked Sessions" project so the
-- dashboard always has somewhere to put every session.
--
-- This is intentionally decoupled from pipeline_id (== which gru automation
-- produced the run) so a project can later span multiple pipelines, or a
-- pipeline can feed multiple projects, without a schema change.
CREATE TABLE IF NOT EXISTS projects (
    id           SERIAL      PRIMARY KEY,
    number       INTEGER,                    -- external project/repo number (nullable — unlinked bucket has none)
    slug         TEXT        NOT NULL UNIQUE, -- stable key for linking pipeline_id -> project until real linking exists
    title        TEXT        NOT NULL,
    repo         TEXT,                        -- primary repo "owner/name", if known
    is_unlinked  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_number_uniq
    ON projects (number) WHERE number IS NOT NULL;

-- Always have a home for sessions that aren't linked to a known project yet.
INSERT INTO projects (slug, title, is_unlinked)
    VALUES ('unlinked', 'Unlinked Sessions', TRUE)
    ON CONFLICT (slug) DO NOTHING;

-- Link a pipeline run to a project. Nullable — falls back to "unlinked" in
-- queries until every run is classified.
ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id);
CREATE INDEX IF NOT EXISTS pipeline_runs_project
    ON pipeline_runs (project_id);

-- Git branch for the session, when known (not populated by all producers yet).
ALTER TABLE pipeline_run_items
    ADD COLUMN IF NOT EXISTS branch TEXT;
