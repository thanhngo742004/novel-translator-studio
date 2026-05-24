CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    status TEXT NOT NULL,
    layer TEXT,
    scope_json TEXT NOT NULL,
    source_key TEXT,
    target_text TEXT,
    value_json TEXT,
    rules_json TEXT,
    confidence_score REAL NOT NULL DEFAULT 0,
    confidence_json TEXT,
    conflict_cluster_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    memory_item_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    artifact_ref TEXT,
    document_id TEXT,
    chapter_id TEXT,
    segment_id TEXT,
    excerpt_json TEXT,
    quality_score REAL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(memory_item_id) REFERENCES memory_items(id),
    FOREIGN KEY(document_id) REFERENCES documents(id),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id),
    FOREIGN KEY(segment_id) REFERENCES segments(id)
);

CREATE TABLE IF NOT EXISTS memory_audit_logs (
    id TEXT PRIMARY KEY,
    memory_item_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_ref TEXT,
    before_json TEXT,
    after_json TEXT,
    task_run_id TEXT,
    model_run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(memory_item_id) REFERENCES memory_items(id),
    FOREIGN KEY(task_run_id) REFERENCES task_runs(id),
    FOREIGN KEY(model_run_id) REFERENCES model_runs(id)
);

CREATE TABLE IF NOT EXISTS memory_conflicts (
    id TEXT PRIMARY KEY,
    cluster_key TEXT NOT NULL,
    status TEXT NOT NULL,
    winner_memory_item_id TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(winner_memory_item_id) REFERENCES memory_items(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_type_status ON memory_items(memory_type, status);
CREATE INDEX IF NOT EXISTS idx_memory_items_source_key ON memory_items(source_key);
CREATE INDEX IF NOT EXISTS idx_memory_evidence_item ON memory_evidence(memory_item_id);
CREATE INDEX IF NOT EXISTS idx_memory_audit_item ON memory_audit_logs(memory_item_id);

