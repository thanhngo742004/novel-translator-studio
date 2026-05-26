CREATE TABLE IF NOT EXISTS dictionary_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS dictionary_candidates (
    id TEXT PRIMARY KEY,
    dict_run_id TEXT NOT NULL,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT,
    normalized_source TEXT NOT NULL,
    normalized_target TEXT,
    scope_json TEXT NOT NULL,
    confidence_score REAL NOT NULL DEFAULT 0,
    confidence_json TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    chapter_spread INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    conflict_group TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY(dict_run_id) REFERENCES dictionary_runs(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS dictionary_candidate_evidence (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    chapter_id TEXT,
    chapter_no INTEGER,
    segment_id TEXT,
    source_excerpt TEXT,
    target_excerpt TEXT,
    evidence_kind TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES dictionary_candidates(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id),
    FOREIGN KEY(segment_id) REFERENCES segments(id)
);

CREATE TABLE IF NOT EXISTS project_dictionary_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    normalized_source TEXT NOT NULL,
    normalized_target TEXT NOT NULL,
    forbidden_variants_json TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    confidence_score REAL NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS dictionary_audit_logs (
    id TEXT PRIMARY KEY,
    dictionary_entry_id TEXT,
    candidate_id TEXT,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(dictionary_entry_id) REFERENCES project_dictionary_entries(id),
    FOREIGN KEY(candidate_id) REFERENCES dictionary_candidates(id)
);

CREATE INDEX IF NOT EXISTS idx_dictionary_runs_project ON dictionary_runs(project_slug, created_at);
CREATE INDEX IF NOT EXISTS idx_dictionary_candidates_run ON dictionary_candidates(dict_run_id);
CREATE INDEX IF NOT EXISTS idx_dictionary_candidates_status ON dictionary_candidates(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_dictionary_candidates_source ON dictionary_candidates(project_slug, normalized_source);
CREATE INDEX IF NOT EXISTS idx_dictionary_evidence_candidate ON dictionary_candidate_evidence(candidate_id);
CREATE INDEX IF NOT EXISTS idx_project_dictionary_entries_status ON project_dictionary_entries(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_project_dictionary_entries_source ON project_dictionary_entries(project_slug, normalized_source);
CREATE INDEX IF NOT EXISTS idx_dictionary_audit_candidate ON dictionary_audit_logs(candidate_id);
