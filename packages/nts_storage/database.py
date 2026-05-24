from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
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


@contextmanager
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def initialize_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connection(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        apply_migrations(conn)


def default_migrations_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "migrations"


def migration_version(path: Path) -> int:
    prefix = path.name.split("_", 1)[0]
    try:
        return int(prefix)
    except ValueError as exc:
        raise ValueError(f"Migration filename must start with a numeric version: {path.name}") from exc


def applied_migration_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row["version"]) for row in rows}


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    migrations_path = migrations_dir or default_migrations_dir()
    if not migrations_path.exists():
        raise FileNotFoundError(f"Migrations directory not found: {migrations_path}")

    conn.executescript(SCHEMA_MIGRATIONS_SQL)
    applied = applied_migration_versions(conn)
    for migration in sorted(migrations_path.glob("*.sql"), key=migration_version):
        version = migration_version(migration)
        if version in applied:
            continue
        sql = migration.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (version, migration.name, utc_now()),
        )
        applied.add(version)
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
