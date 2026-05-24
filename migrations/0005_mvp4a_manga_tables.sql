CREATE TABLE IF NOT EXISTS manga_pages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chapter_id TEXT,
    page_index INTEGER NOT NULL,
    image_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id)
);

CREATE TABLE IF NOT EXISTS manga_page_artifacts (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES manga_pages(id)
);

CREATE TABLE IF NOT EXISTS manga_boxes (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    current_version_id TEXT,
    deleted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(page_id) REFERENCES manga_pages(id)
);

CREATE TABLE IF NOT EXISTS manga_box_versions (
    id TEXT PRIMARY KEY,
    box_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    bbox_json TEXT NOT NULL,
    polygon_json TEXT,
    box_type TEXT NOT NULL,
    reading_order INTEGER,
    speaker_id TEXT,
    origin TEXT NOT NULL,
    previous_version_id TEXT,
    change_reason TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(box_id) REFERENCES manga_boxes(id),
    FOREIGN KEY(previous_version_id) REFERENCES manga_box_versions(id)
);

CREATE TABLE IF NOT EXISTS manga_ocr_results (
    id TEXT PRIMARY KEY,
    box_id TEXT NOT NULL,
    box_version_id TEXT,
    engine_name TEXT,
    raw_text TEXT,
    normalized_text TEXT,
    confidence REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(box_id) REFERENCES manga_boxes(id),
    FOREIGN KEY(box_version_id) REFERENCES manga_box_versions(id)
);

CREATE TABLE IF NOT EXISTS manga_box_translations (
    id TEXT PRIMARY KEY,
    box_id TEXT NOT NULL,
    translation_text TEXT,
    provider_name TEXT,
    model_run_id TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(box_id) REFERENCES manga_boxes(id),
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE TABLE IF NOT EXISTS manga_exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chapter_id TEXT,
    export_kind TEXT NOT NULL,
    export_path TEXT NOT NULL,
    checksum_sha256 TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id)
);

CREATE TABLE IF NOT EXISTS manga_visual_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    page_id TEXT,
    box_id TEXT,
    evidence_kind TEXT NOT NULL,
    artifact_ref TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(page_id) REFERENCES manga_pages(id),
    FOREIGN KEY(box_id) REFERENCES manga_boxes(id)
);

CREATE INDEX IF NOT EXISTS idx_manga_pages_project ON manga_pages(project_id, page_index);
CREATE INDEX IF NOT EXISTS idx_manga_artifacts_page ON manga_page_artifacts(page_id);
CREATE INDEX IF NOT EXISTS idx_manga_boxes_page_stable ON manga_boxes(page_id, stable_key);
CREATE INDEX IF NOT EXISTS idx_manga_box_versions_box ON manga_box_versions(box_id, revision_no);
CREATE INDEX IF NOT EXISTS idx_manga_exports_project ON manga_exports(project_id);

