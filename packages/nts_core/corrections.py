from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nts_core.memory import write_audit_log
from nts_core.projects import get_project_by_slug
from nts_core.text_import import normalize_text, split_segments
from nts_storage.database import (
    connection,
    insert_task_run,
    json_dumps,
    new_id,
    update_task_run,
    utc_now,
)
from nts_storage.workspace import Workspace


TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class CorrectionRecord:
    raw_text: str
    ai_translation: str
    human_translation: str
    context: dict[str, Any]


def _read_utf8_text(path: Path, label: str) -> str:
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"{label} file not found: {path}")
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} file must be UTF-8 encoded.") from exc


def _paragraphs(text: str) -> list[str]:
    normalized = normalize_text(text)
    return split_segments(normalized) if normalized else []


def records_from_parallel_files(raw_path: Path, ai_path: Path, human_path: Path) -> list[CorrectionRecord]:
    raw_parts = _paragraphs(_read_utf8_text(raw_path, "raw"))
    ai_parts = _paragraphs(_read_utf8_text(ai_path, "ai"))
    human_parts = _paragraphs(_read_utf8_text(human_path, "human"))
    if not raw_parts and not ai_parts and not human_parts:
        raise ValueError("Correction input files are empty.")
    if not ai_parts or not human_parts:
        raise ValueError("AI and human correction files must contain text.")

    count = max(len(raw_parts), len(ai_parts), len(human_parts))
    records: list[CorrectionRecord] = []
    for index in range(count):
        records.append(
            CorrectionRecord(
                raw_text=raw_parts[index] if index < len(raw_parts) else "",
                ai_translation=ai_parts[index] if index < len(ai_parts) else "",
                human_translation=human_parts[index] if index < len(human_parts) else "",
                context={"paragraph_index": index + 1},
            )
        )
    return records


def records_from_jsonl(path: Path) -> list[CorrectionRecord]:
    text = _read_utf8_text(path, "corrections")
    records: list[CorrectionRecord] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_no}.") from exc
        if not isinstance(data, dict):
            raise ValueError(f"JSONL line {line_no} must be an object.")
        missing = [
            key
            for key in ("raw_text", "ai_translation", "human_translation")
            if key not in data
        ]
        if missing:
            raise ValueError(f"JSONL line {line_no} missing field(s): {', '.join(missing)}")
        context = data.get("context") or {}
        if not isinstance(context, dict):
            raise ValueError(f"JSONL line {line_no} context must be an object.")
        records.append(
            CorrectionRecord(
                raw_text=str(data["raw_text"]),
                ai_translation=str(data["ai_translation"]),
                human_translation=str(data["human_translation"]),
                context=context,
            )
        )
    if not records:
        raise ValueError("Correction JSONL file contains no records.")
    return records


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def classify_correction(ai_translation: str, human_translation: str) -> tuple[str, str | None]:
    ai_norm = normalize_text(ai_translation)
    human_norm = normalize_text(human_translation)
    ai_tokens = _tokens(ai_norm)
    human_tokens = _tokens(human_norm)
    ai_len = max(len(ai_norm), 1)
    length_delta = abs(len(human_norm) - len(ai_norm)) / max(len(human_norm), ai_len)

    if length_delta > 0.35 or abs(len(human_tokens) - len(ai_tokens)) >= 4:
        return "possible_omission_or_addition", "review_added_or_omitted_content_against_source"

    ai_set = set(ai_tokens)
    human_set = set(human_tokens)
    overlap = len(ai_set & human_set) / max(len(ai_set | human_set), 1)
    changed_terms = len(ai_set ^ human_set)
    if overlap >= 0.65 and 0 < changed_terms <= 4:
        return "possible_terminology_change", "prefer_human_term_in_matching_context"
    if overlap >= 0.8:
        return "possible_style_change", "prefer_human_style_in_matching_context"
    return "changed_text", None


def _meaningfully_differs(ai_translation: str, human_translation: str) -> bool:
    return normalize_text(ai_translation) != normalize_text(human_translation)


def _signature(record: CorrectionRecord) -> str:
    payload = json_dumps(
        {
            "raw_text": normalize_text(record.raw_text),
            "ai_translation": normalize_text(record.ai_translation),
            "human_translation": normalize_text(record.human_translation),
        }
    )
    import hashlib

    return "corr:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def learn_corrections(
    workspace: Workspace,
    *,
    project_slug: str,
    records: list[CorrectionRecord],
    input_ref: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    report_dir = workspace.path / "artifacts" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if any(not record.raw_text for record in records):
        warnings.append("some_records_missing_raw_text")
    scope = {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "domain": project.get("domain"),
        "source_lang": project.get("source_lang"),
        "target_lang": project.get("target_lang"),
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }

    memory_ids: list[str] = []
    error_type_counts: dict[str, int] = {}
    skipped_records = 0
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.correction",
            status="running",
            stage="classify",
            project_id=project["id"],
            input_data={"project": project_slug, "input_ref": input_ref, "records": len(records)},
            result_data={},
        )

        for record in records:
            if not _meaningfully_differs(record.ai_translation, record.human_translation):
                skipped_records += 1
                continue

            error_type, fix_rule = classify_correction(
                record.ai_translation, record.human_translation
            )
            error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1
            memory_id = new_id("memory")
            value = {
                "raw_text": record.raw_text,
                "ai_translation": record.ai_translation,
                "human_translation": record.human_translation,
                "error_type": error_type,
                "fix_rule": fix_rule,
                "context": record.context,
            }
            confidence = {
                "level": "medium",
                "reason": "deterministic_ai_vs_human_correction_diff",
            }
            item = {
                "id": memory_id,
                "memory_type": "correction",
                "status": "pending",
                "layer": "correction",
                "scope_json": scope,
                "source_key": _signature(record),
                "target_text": record.human_translation,
                "value_json": value,
                "rules_json": {},
                "confidence_score": 0.45,
                "confidence_json": confidence,
                "conflict_cluster_id": None,
                "created_at": now,
                "updated_at": now,
            }
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
                    "correction",
                    "pending",
                    "correction",
                    json_dumps(scope),
                    item["source_key"],
                    record.human_translation,
                    json_dumps(value),
                    json_dumps({}),
                    0.45,
                    json_dumps(confidence),
                    None,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_evidence (
                    id, memory_item_id, source_kind, artifact_ref, document_id, chapter_id,
                    segment_id, excerpt_json, quality_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("evidence"),
                    memory_id,
                    "correction_import",
                    input_ref,
                    None,
                    None,
                    None,
                    json_dumps(
                        {
                            "raw_text": record.raw_text,
                            "ai_translation": record.ai_translation,
                            "human_translation": record.human_translation,
                            "context": record.context,
                            "error_type": error_type,
                        }
                    ),
                    0.8,
                    now,
                ),
            )
            write_audit_log(
                conn,
                memory_item_id=memory_id,
                action="create",
                before=None,
                after=item,
                actor_type="service",
                actor_ref="learn.correction",
                task_run_id=task_id,
            )
            memory_ids.append(memory_id)

        report = {
            "task_run_id": task_id,
            "project_id": project["id"],
            "project_slug": project["slug"],
            "total_records": len(records),
            "corrections_created": len(memory_ids),
            "skipped_records": skipped_records,
            "error_type_counts": error_type_counts,
            "warnings": warnings,
            "memory_ids": memory_ids,
        }
        report_path = report_dir / f"correction_report_{task_id}.json"
        rel_report_path = report_path.relative_to(workspace.path).as_posix()
        report["report_path"] = rel_report_path
        report_path.write_text(json_dumps(report) + "\n", encoding="utf-8")
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=report,
        )
        conn.commit()

    return report
