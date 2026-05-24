from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

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
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        apply_migrations(conn)


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.executescript(MIGRATION_001)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
        (1, "mvp0_initial_tables", utc_now()),
    )
    conn.commit()


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(row["name"] for row in rows)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def insert_task_run(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    status: str,
    stage: str | None = None,
    project_id: str | None = None,
    input_data: dict[str, Any] | None = None,
    result_data: dict[str, Any] | None = None,
    error_data: dict[str, Any] | None = None,
) -> str:
    task_id = new_id("task")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO task_runs (
            id, task_type, project_id, status, stage, input_json, state_json, result_json,
            error_json, started_at, finished_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            task_type,
            project_id,
            status,
            stage,
            json_dumps(input_data or {}),
            json_dumps({}),
            json_dumps(result_data or {}),
            json_dumps(error_data) if error_data else None,
            now,
            now if status in {"success", "error"} else None,
            now,
        ),
    )
    return task_id


def row_to_dict(row: sqlite3.Row, json_fields: Iterable[str] = ()) -> dict[str, Any]:
    data = dict(row)
    for field in json_fields:
        if field in data:
            data[field] = json_loads(data[field])
    return data

