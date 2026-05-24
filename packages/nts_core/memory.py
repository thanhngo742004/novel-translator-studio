from __future__ import annotations

import hashlib
from typing import Any

from nts_core.projects import get_project_by_slug
from nts_storage.database import connection, json_dumps, json_loads, new_id, row_to_dict, utc_now
from nts_storage.workspace import Workspace


ALLOWED_MEMORY_TYPES = {"term", "name", "pronoun", "style", "correction"}
ALLOWED_MEMORY_STATUSES = {"draft", "pending", "active", "deprecated", "rejected"}
JSON_FIELDS = ("scope_json", "value_json", "rules_json", "confidence_json")


def parse_json_object(raw: str | None, *, field_name: str) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    try:
        parsed = json_loads(raw)
    except Exception as exc:
        raise ValueError(f"{field_name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return parsed


def validate_memory_type(memory_type: str) -> None:
    if memory_type not in ALLOWED_MEMORY_TYPES:
        raise ValueError(f"Invalid memory type: {memory_type}")


def validate_status(status: str) -> None:
    if status not in ALLOWED_MEMORY_STATUSES:
        raise ValueError(f"Invalid memory status: {status}")


def _memory_row(conn, memory_id: str):
    row = conn.execute(
        """
        SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
               value_json, rules_json, confidence_score, confidence_json,
               conflict_cluster_id, created_at, updated_at
        FROM memory_items
        WHERE id = ?
        """,
        (memory_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Memory item not found: {memory_id}")
    return row


def memory_item_to_dict(row) -> dict[str, Any]:
    return row_to_dict(row, json_fields=JSON_FIELDS)


def write_audit_log(
    conn,
    *,
    memory_item_id: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    actor_type: str = "cli",
    actor_ref: str = "local",
    task_run_id: str | None = None,
    model_run_id: str | None = None,
) -> str:
    audit_id = new_id("audit")
    conn.execute(
        """
        INSERT INTO memory_audit_logs (
            id, memory_item_id, action, actor_type, actor_ref, before_json, after_json,
            task_run_id, model_run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            memory_item_id,
            action,
            actor_type,
            actor_ref,
            json_dumps(before) if before is not None else None,
            json_dumps(after) if after is not None else None,
            task_run_id,
            model_run_id,
            utc_now(),
        ),
    )
    return audit_id


def create_memory_item(
    workspace: Workspace,
    *,
    memory_type: str,
    status: str = "pending",
    layer: str | None = None,
    scope: dict[str, Any] | None = None,
    source_key: str | None = None,
    target_text: str | None = None,
    value: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
    confidence_score: float = 0.0,
    confidence: dict[str, Any] | None = None,
    conflict_cluster_id: str | None = None,
) -> dict[str, Any]:
    validate_memory_type(memory_type)
    validate_status(status)
    if not 0 <= confidence_score <= 1:
        raise ValueError("confidence_score must be between 0 and 1.")

    memory_id = new_id("memory")
    now = utc_now()
    item = {
        "id": memory_id,
        "memory_type": memory_type,
        "status": status,
        "layer": layer,
        "scope_json": scope or {},
        "source_key": source_key,
        "target_text": target_text,
        "value_json": value or {},
        "rules_json": rules or {},
        "confidence_score": confidence_score,
        "confidence_json": confidence or {},
        "conflict_cluster_id": conflict_cluster_id,
        "created_at": now,
        "updated_at": now,
    }
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT INTO memory_items (
                id, memory_type, status, layer, scope_json, source_key, target_text,
                value_json, rules_json, confidence_score, confidence_json,
                conflict_cluster_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                memory_type,
                status,
                layer,
                json_dumps(item["scope_json"]),
                source_key,
                target_text,
                json_dumps(item["value_json"]),
                json_dumps(item["rules_json"]),
                confidence_score,
                json_dumps(item["confidence_json"]),
                conflict_cluster_id,
                now,
                now,
            ),
        )
        write_audit_log(conn, memory_item_id=memory_id, action="create", before=None, after=item)
        conn.commit()
    return item


def list_memory_items(
    workspace: Workspace,
    *,
    memory_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if memory_type:
        validate_memory_type(memory_type)
    if status:
        validate_status(status)
    conditions: list[str] = []
    params: list[Any] = []
    if memory_type:
        conditions.append("memory_type = ?")
        params.append(memory_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            {where}
            ORDER BY created_at ASC, id ASC
            """,
            params,
        ).fetchall()
    return [memory_item_to_dict(row) for row in rows]


def show_memory_item(workspace: Workspace, memory_id: str) -> dict[str, Any]:
    with connection(workspace.db_path) as conn:
        item = memory_item_to_dict(_memory_row(conn, memory_id))
        evidence_rows = conn.execute(
            """
            SELECT id, memory_item_id, source_kind, artifact_ref, document_id, chapter_id,
                   segment_id, excerpt_json, quality_score, created_at
            FROM memory_evidence
            WHERE memory_item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (memory_id,),
        ).fetchall()
        audit_rows = conn.execute(
            """
            SELECT id, memory_item_id, action, actor_type, actor_ref, before_json,
                   after_json, task_run_id, model_run_id, created_at
            FROM memory_audit_logs
            WHERE memory_item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (memory_id,),
        ).fetchall()
    return {
        "item": item,
        "evidence": [row_to_dict(row, json_fields=("excerpt_json",)) for row in evidence_rows],
        "audit_logs": [
            row_to_dict(row, json_fields=("before_json", "after_json")) for row in audit_rows
        ],
    }


def add_evidence(
    workspace: Workspace,
    *,
    memory_item_id: str,
    source_kind: str,
    artifact_ref: str | None = None,
    document_id: str | None = None,
    chapter_id: str | None = None,
    segment_id: str | None = None,
    excerpt: dict[str, Any] | None = None,
    quality_score: float | None = None,
) -> dict[str, Any]:
    evidence_id = new_id("evidence")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        _memory_row(conn, memory_item_id)
        conn.execute(
            """
            INSERT INTO memory_evidence (
                id, memory_item_id, source_kind, artifact_ref, document_id, chapter_id,
                segment_id, excerpt_json, quality_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                memory_item_id,
                source_kind,
                artifact_ref,
                document_id,
                chapter_id,
                segment_id,
                json_dumps(excerpt or {}),
                quality_score,
                now,
            ),
        )
        conn.commit()
    return {
        "id": evidence_id,
        "memory_item_id": memory_item_id,
        "source_kind": source_kind,
        "artifact_ref": artifact_ref,
        "document_id": document_id,
        "chapter_id": chapter_id,
        "segment_id": segment_id,
        "excerpt_json": excerpt or {},
        "quality_score": quality_score,
        "created_at": now,
    }


def update_memory_status(workspace: Workspace, *, memory_item_id: str, status: str) -> dict[str, Any]:
    validate_status(status)
    now = utc_now()
    with connection(workspace.db_path) as conn:
        before = memory_item_to_dict(_memory_row(conn, memory_item_id))
        conn.execute(
            "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, memory_item_id),
        )
        after = memory_item_to_dict(_memory_row(conn, memory_item_id))
        write_audit_log(
            conn,
            memory_item_id=memory_item_id,
            action="status.set",
            before=before,
            after=after,
        )
        conn.commit()
    return after


def _project_scope_context(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "domain": project.get("domain"),
        "source_lang": project.get("source_lang"),
        "target_lang": project.get("target_lang"),
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }


def scope_matches(item_scope: dict[str, Any], context: dict[str, Any]) -> bool:
    for key in ("project_id", "project_slug", "domain", "source_lang", "target_lang", "language_pair"):
        if key in item_scope and item_scope[key] not in (None, context.get(key)):
            return False
    return True


def _item_matches_text(item: dict[str, Any], text: str) -> bool:
    source_key = item.get("source_key")
    if not source_key:
        return item["memory_type"] in {"pronoun", "style", "correction"}
    return source_key in text


def _bundle_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "memory_type": item["memory_type"],
        "source_key": item.get("source_key"),
        "target_text": item.get("target_text"),
        "value": item.get("value_json") or {},
        "rules": item.get("rules_json") or {},
        "confidence_score": item["confidence_score"],
        "scope": item.get("scope_json") or {},
    }


def build_bundle(
    workspace: Workspace,
    *,
    project_slug: str | None = None,
    project_id: str | None = None,
    text: str,
    memory_types: set[str] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be at least 1.")
    with connection(workspace.db_path) as conn:
        if project_slug:
            project_row = conn.execute(
                """
                SELECT id, slug, name, source_lang, target_lang, domain, genre, status,
                       created_at, updated_at
                FROM projects WHERE slug = ?
                """,
                (project_slug,),
            ).fetchone()
        elif project_id:
            project_row = conn.execute(
                """
                SELECT id, slug, name, source_lang, target_lang, domain, genre, status,
                       created_at, updated_at
                FROM projects WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
        else:
            project_row = None
        if project_row is None:
            raise ValueError("Project is required for memory bundle retrieval.")
        project = row_to_dict(project_row)
        rows = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            WHERE status = 'active'
            """
        ).fetchall()

    context = _project_scope_context(project)
    allowed_types = memory_types or ALLOWED_MEMORY_TYPES
    for memory_type in allowed_types:
        validate_memory_type(memory_type)

    selected: list[dict[str, Any]] = []
    for row in rows:
        item = memory_item_to_dict(row)
        if item["memory_type"] not in allowed_types:
            continue
        if not scope_matches(item.get("scope_json") or {}, context):
            continue
        if not _item_matches_text(item, text):
            continue
        selected.append(item)

    selected.sort(key=lambda item: (-float(item["confidence_score"]), item["memory_type"], item["id"]))
    selected = selected[:top_k]

    grouped = {
        "terms": [],
        "names": [],
        "pronouns": [],
        "style_rules": [],
        "corrections": [],
    }
    group_by_type = {
        "term": "terms",
        "name": "names",
        "pronoun": "pronouns",
        "style": "style_rules",
        "correction": "corrections",
    }
    for item in selected:
        grouped[group_by_type[item["memory_type"]]].append(_bundle_item(item))

    checksum_payload = {
        "project_id": project["id"],
        "items": grouped,
        "warnings": [],
    }
    checksum = "sha256:" + hashlib.sha256(json_dumps(checksum_payload).encode("utf-8")).hexdigest()
    return {
        "bundle_id": f"bundle_{checksum.removeprefix('sha256:')[:16]}",
        "project_id": project["id"],
        "items": grouped,
        "warnings": [],
        "checksum": checksum,
    }


class MemoryRetriever:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def build_bundle(
        self,
        *,
        project_slug: str | None = None,
        project_id: str | None = None,
        text: str,
        memory_types: set[str] | None = None,
        top_k: int = 20,
    ) -> dict[str, Any]:
        return build_bundle(
            self.workspace,
            project_slug=project_slug,
            project_id=project_id,
            text=text,
            memory_types=memory_types,
            top_k=top_k,
        )
