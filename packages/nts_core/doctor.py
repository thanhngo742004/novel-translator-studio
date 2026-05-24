from __future__ import annotations

from nts_storage.database import connect, table_names
from nts_storage.workspace import WORKSPACE_DIRS, Workspace


def build_doctor_report(workspace: Workspace) -> dict:
    with connect(workspace.db_path) as conn:
        tables = table_names(conn)
    dirs = {path: (workspace.path / path).exists() for path in WORKSPACE_DIRS}
    required_tables = {"projects", "task_runs", "model_runs", "provider_configs"}
    return {
        "workspace": str(workspace.path),
        "db_path": str(workspace.db_path),
        "db_exists": workspace.db_path.exists(),
        "directories": dirs,
        "tables": tables,
        "ok": workspace.db_path.exists() and required_tables.issubset(set(tables)) and all(dirs.values()),
    }

