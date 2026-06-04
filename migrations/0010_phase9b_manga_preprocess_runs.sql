CREATE TABLE IF NOT EXISTS manga_preprocess_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    source_manifest_path TEXT NOT NULL,
    artifact_root TEXT NOT NULL,
    preprocess_manifest_path TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    force INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_manga_preprocess_runs_project ON manga_preprocess_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_manga_preprocess_runs_run ON manga_preprocess_runs(run_id);
