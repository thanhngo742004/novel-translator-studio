CREATE TABLE IF NOT EXISTS manga_projects (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    project_slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    reading_direction TEXT NOT NULL DEFAULT 'right_to_left',
    content_type TEXT NOT NULL DEFAULT 'manga_image',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS manga_import_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    manga_project_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_path_hash TEXT NOT NULL,
    artifact_root TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(manga_project_id) REFERENCES manga_projects(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_manga_projects_slug ON manga_projects(project_slug);
CREATE INDEX IF NOT EXISTS idx_manga_import_runs_project ON manga_import_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_manga_import_runs_run ON manga_import_runs(run_id);
