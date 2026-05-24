from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from nts_core.memory import memory_item_to_dict, scope_matches
from nts_core.projects import get_project_by_slug
from nts_storage.database import connection, json_dumps, new_id, utc_now
from nts_storage.workspace import Workspace


SCHEMA_VERSION = "lamm_t_compact_v1"


def _project_context(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "domain": project.get("domain"),
        "source_lang": project.get("source_lang"),
        "target_lang": project.get("target_lang"),
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "source_key": item.get("source_key"),
        "target_text": item.get("target_text"),
        "value": item.get("value_json") or {},
        "rules": item.get("rules_json") or {},
        "confidence_score": item.get("confidence_score", 0),
        "scope": item.get("scope_json") or {},
    }


def _active_project_memory(workspace: Workspace, project: dict[str, Any]) -> list[dict[str, Any]]:
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            WHERE status = 'active'
            """
        ).fetchall()
    context = _project_context(project)
    items = [memory_item_to_dict(row) for row in rows]
    return [item for item in items if scope_matches(item.get("scope_json") or {}, context)]


def _group_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {
        "force_terms": [],
        "force_names": [],
        "pronoun_rules": [],
        "style_rules": [],
        "correction_rules": [],
        "warnings": [],
    }
    groups = {
        "term": "force_terms",
        "name": "force_names",
        "pronoun": "pronoun_rules",
        "style": "style_rules",
        "correction": "correction_rules",
    }
    for item in sorted(
        items,
        key=lambda row: (
            row["memory_type"],
            row.get("source_key") or "",
            row.get("target_text") or "",
            row["id"],
        ),
    ):
        group = groups.get(item["memory_type"])
        if group:
            grouped[group].append(_compact_item(item))
    return grouped


def _deterministic_exported_at(project: dict[str, Any], items: list[dict[str, Any]]) -> str:
    timestamps = [item.get("updated_at") for item in items if item.get("updated_at")]
    return max(timestamps) if timestamps else project["updated_at"]


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json_dumps(payload) + "\n", encoding="utf-8")


def _write_compat_files(export_dir: Path, bundle: dict[str, Any]) -> list[str]:
    compat_dir = export_dir / "compat"
    compat_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    style_lines = []
    if bundle.get("style_summary"):
        style_lines.append(bundle["style_summary"])
    style_lines.extend(
        item.get("target_text") or item.get("value", {}).get("summary", "")
        for item in bundle["style_rules"]
    )
    (compat_dir / "StyleSummary.txt").write_text(
        "\n".join(line for line in style_lines if line) + ("\n" if style_lines else ""),
        encoding="utf-8",
    )
    written.append("compat/StyleSummary.txt")

    pronoun_lines = [
        json_dumps({"source_key": item.get("source_key"), "rules": item.get("rules", {})})
        for item in bundle["pronoun_rules"]
    ]
    (compat_dir / "Pronouns.txt").write_text(
        "\n".join(pronoun_lines) + ("\n" if pronoun_lines else ""),
        encoding="utf-8",
    )
    written.append("compat/Pronouns.txt")

    rule_lines = [
        json_dumps({"source_key": item.get("source_key"), "rules": item.get("rules", {})})
        for item in bundle["correction_rules"]
    ]
    (compat_dir / "LuatNhan.txt").write_text(
        "\n".join(rule_lines) + ("\n" if rule_lines else ""),
        encoding="utf-8",
    )
    written.append("compat/LuatNhan.txt")
    return written


def compile_export_bundle(
    workspace: Workspace,
    *,
    project_slug: str,
    bundle_kind: str = "bundle",
    profile_id: str | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    items = _active_project_memory(workspace, project)
    grouped = _group_items(items)
    exported_at = _deterministic_exported_at(project, items)

    base_payload = {
        "project_id": project["id"],
        "profile_id": profile_id,
        "schema_version": SCHEMA_VERSION,
        "style_summary": "",
        "force_terms": grouped["force_terms"],
        "force_names": grouped["force_names"],
        "pronoun_rules": grouped["pronoun_rules"],
        "style_rules": grouped["style_rules"],
        "correction_rules": grouped["correction_rules"],
        "warnings": grouped["warnings"],
        "exported_at": exported_at,
    }
    checksum = "sha256:" + hashlib.sha256(json_dumps(base_payload).encode("utf-8")).hexdigest()
    bundle_id = f"bundle_{checksum.removeprefix('sha256:')[:16]}"
    bundle = {"bundle_id": bundle_id, **base_payload, "checksum": checksum}

    export_dir = workspace.path / "artifacts" / "exports" / bundle_id
    export_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = export_dir / "bundle.json"
    manifest_path = export_dir / "manifest.json"
    checksums_path = export_dir / "checksums.txt"

    stats = {
        "active_items_exported": len(items),
        "force_terms": len(bundle["force_terms"]),
        "force_names": len(bundle["force_names"]),
        "pronoun_rules": len(bundle["pronoun_rules"]),
        "style_rules": len(bundle["style_rules"]),
        "correction_rules": len(bundle["correction_rules"]),
        "warnings": len(bundle["warnings"]),
    }
    manifest = {
        "bundle_id": bundle_id,
        "project_id": project["id"],
        "project_slug": project["slug"],
        "bundle_kind": bundle_kind,
        "schema_version": SCHEMA_VERSION,
        "bundle_file": "bundle.json",
        "checksum": checksum,
        "exported_at": exported_at,
        "stats": stats,
    }

    _write_json(bundle_path, bundle)
    _write_json(manifest_path, manifest)
    compat_files = _write_compat_files(export_dir, bundle)

    checksum_lines = [
        f"{_file_sha256(bundle_path)}  bundle.json",
        f"{_file_sha256(manifest_path)}  manifest.json",
    ]
    for rel_path in compat_files:
        checksum_lines.append(f"{_file_sha256(export_dir / rel_path)}  {rel_path}")
    checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    rel_export_dir = export_dir.relative_to(workspace.path).as_posix()
    export_id = new_id("export")
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT INTO export_bundles (
                id, project_id, profile_id, bundle_kind, schema_version, bundle_path,
                checksum, stats_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export_id,
                project["id"],
                profile_id,
                bundle_kind,
                SCHEMA_VERSION,
                rel_export_dir,
                checksum,
                json_dumps(stats),
                utc_now(),
            ),
        )
        conn.commit()

    return {
        "id": export_id,
        "bundle_id": bundle_id,
        "project_id": project["id"],
        "project_slug": project["slug"],
        "bundle_kind": bundle_kind,
        "schema_version": SCHEMA_VERSION,
        "bundle_path": rel_export_dir,
        "bundle_file": f"{rel_export_dir}/bundle.json",
        "manifest_file": f"{rel_export_dir}/manifest.json",
        "checksums_file": f"{rel_export_dir}/checksums.txt",
        "checksum": checksum,
        "stats": stats,
    }

