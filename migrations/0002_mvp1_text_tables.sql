CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    doc_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    language TEXT,
    metadata_json TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chapter_no INTEGER,
    title TEXT,
    boundary_start INTEGER,
    boundary_end INTEGER,
    confidence REAL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    segment_no INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    paragraph_no INTEGER,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id)
);

CREATE TABLE IF NOT EXISTS translations (
    id TEXT PRIMARY KEY,
    segment_id TEXT,
    chapter_id TEXT,
    translation_kind TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    model_run_id TEXT,
    bundle_checksum TEXT,
    quality_json TEXT,
    is_current INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(segment_id) REFERENCES segments(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id),
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_chapters_project ON chapters(project_id);
CREATE INDEX IF NOT EXISTS idx_segments_chapter ON segments(chapter_id);
CREATE INDEX IF NOT EXISTS idx_translations_chapter ON translations(chapter_id);

