CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    domain TEXT,
    genre TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    project_id TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    input_json TEXT,
    state_json TEXT,
    result_json TEXT,
    error_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS model_runs (
    id TEXT PRIMARY KEY,
    task_run_id TEXT,
    provider_key TEXT NOT NULL,
    adapter_type TEXT NOT NULL,
    base_url TEXT,
    model_name TEXT,
    prompt_hash TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_estimate REAL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(task_run_id) REFERENCES task_runs(id)
);

CREATE TABLE IF NOT EXISTS provider_configs (
    id TEXT PRIMARY KEY,
    provider_key TEXT NOT NULL UNIQUE,
    provider_type TEXT NOT NULL,
    base_url TEXT,
    api_key_env TEXT,
    options_json TEXT,
    last_validated_at TEXT,
    status TEXT NOT NULL
);

