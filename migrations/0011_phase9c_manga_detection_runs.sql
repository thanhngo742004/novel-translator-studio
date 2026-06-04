CREATE TABLE IF NOT EXISTS manga_detection_runs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    regions_path TEXT NOT NULL,
    bubbles_path TEXT,
    boxes_merged_path TEXT NOT NULL,
    region_count INTEGER NOT NULL DEFAULT 0,
    bubble_count INTEGER NOT NULL DEFAULT 0,
    confidence_summary_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_manga_detection_runs_project ON manga_detection_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_manga_detection_runs_run ON manga_detection_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_manga_detection_runs_adapter ON manga_detection_runs(adapter_id);
