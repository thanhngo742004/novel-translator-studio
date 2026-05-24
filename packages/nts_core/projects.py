from __future__ import annotations

import sqlite3
from typing import Any

from nts_storage.database import connection, insert_task_run, new_id, row_to_dict, utc_now
from nts_storage.workspace import Workspace


def create_project(
    workspace: Workspace,
    *,
    slug: str,
    name: str,
    source_lang: str,
    target_lang: str,
    domain: str,
    genre: str | None,
) -> dict[str, Any]:
    if not slug.strip():
        raise ValueError("slug is required")
    if not name.strip():
        raise ValueError("name is required")
    if not source_lang.strip() or not target_lang.strip():
        raise ValueError("source_lang and target_lang are required")

    project_id = new_id("project")
    now = utc_now()
    project = {
        "id": project_id,
        "slug": slug,
        "name": name,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "domain": domain,
        "genre": genre,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }

    with connection(workspace.db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO projects (
                    id, slug, name, source_lang, target_lang, domain, genre, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    slug,
                    name,
                    source_lang,
                    target_lang,
                    domain,
                    genre,
                    "active",
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Project slug already exists: {slug}") from exc
        task_id = insert_task_run(
            conn,
            task_type="project.create",
            status="success",
            stage="created",
            project_id=project_id,
            input_data={
                "slug": slug,
                "name": name,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "domain": domain,
                "genre": genre,
            },
            result_data={"project_id": project_id},
        )
        conn.commit()

    project["task_run_id"] = task_id
    return project


def list_projects(workspace: Workspace) -> list[dict[str, Any]]:
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, slug, name, source_lang, target_lang, domain, genre, status,
                   created_at, updated_at
            FROM projects
            ORDER BY created_at ASC
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]
