CREATE TABLE IF NOT EXISTS export_bundles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    profile_id TEXT,
    bundle_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    bundle_path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    stats_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_export_bundles_project ON export_bundles(project_id);
CREATE INDEX IF NOT EXISTS idx_export_bundles_checksum ON export_bundles(checksum);

