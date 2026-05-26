CREATE TABLE IF NOT EXISTS nlp_analysis_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    provider_version TEXT,
    heuristics_version TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    manifest_path TEXT,
    status TEXT NOT NULL,
    degraded INTEGER NOT NULL DEFAULT 0,
    sentence_count INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id)
);

CREATE INDEX IF NOT EXISTS idx_nlp_analysis_runs_chapter ON nlp_analysis_runs(chapter_id);
CREATE INDEX IF NOT EXISTS idx_nlp_analysis_runs_cache
ON nlp_analysis_runs(project_slug, chapter_id, provider_kind, heuristics_version, source_sha256);
