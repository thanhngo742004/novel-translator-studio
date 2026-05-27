CREATE TABLE IF NOT EXISTS rule_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    source_run_refs_json TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS rule_candidates (
    id TEXT PRIMARY KEY,
    rule_run_id TEXT NOT NULL,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    trigger_pattern_json TEXT NOT NULL,
    applies_when_json TEXT NOT NULL,
    instruction TEXT NOT NULL,
    examples_json TEXT NOT NULL,
    forbidden_variants_json TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    confidence_score REAL NOT NULL DEFAULT 0,
    confidence_json TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    conflict_group TEXT,
    review_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY(rule_run_id) REFERENCES rule_runs(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS rule_candidate_evidence (
    id TEXT PRIMARY KEY,
    rule_candidate_id TEXT NOT NULL,
    chapter_id TEXT,
    chapter_no INTEGER,
    segment_id TEXT,
    source_excerpt TEXT,
    target_excerpt TEXT,
    model_output_excerpt TEXT,
    evidence_kind TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(rule_candidate_id) REFERENCES rule_candidates(id)
);

CREATE TABLE IF NOT EXISTS approved_rules (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    project_slug TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    trigger_pattern_json TEXT NOT NULL,
    applies_when_json TEXT NOT NULL,
    instruction TEXT NOT NULL,
    examples_json TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS rule_audit_logs (
    id TEXT PRIMARY KEY,
    rule_candidate_id TEXT,
    approved_rule_id TEXT,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(rule_candidate_id) REFERENCES rule_candidates(id),
    FOREIGN KEY(approved_rule_id) REFERENCES approved_rules(id)
);

CREATE TABLE IF NOT EXISTS rule_conflicts (
    id TEXT PRIMARY KEY,
    rule_run_id TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    source_key TEXT,
    candidate_ids_json TEXT NOT NULL,
    policy TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(rule_run_id) REFERENCES rule_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_rule_runs_project ON rule_runs(project_slug, created_at);
CREATE INDEX IF NOT EXISTS idx_rule_candidates_run ON rule_candidates(rule_run_id);
CREATE INDEX IF NOT EXISTS idx_rule_candidates_status ON rule_candidates(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_rule_candidates_type ON rule_candidates(project_slug, rule_type);
CREATE INDEX IF NOT EXISTS idx_rule_evidence_candidate ON rule_candidate_evidence(rule_candidate_id);
CREATE INDEX IF NOT EXISTS idx_approved_rules_status ON approved_rules(project_slug, status);
CREATE INDEX IF NOT EXISTS idx_rule_audit_candidate ON rule_audit_logs(rule_candidate_id);
CREATE INDEX IF NOT EXISTS idx_rule_conflicts_run ON rule_conflicts(rule_run_id);
