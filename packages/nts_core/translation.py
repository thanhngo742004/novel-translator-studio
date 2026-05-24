from __future__ import annotations

from pathlib import Path
from typing import Any

from nts_core.memory import build_bundle
from nts_core.model_test import log_mock_model_run
from nts_core.projects import get_project_by_id
from nts_core.text_import import get_chapter, list_segments
from nts_storage.database import (
    connection,
    insert_task_run,
    json_dumps,
    new_id,
    update_task_run,
    utc_now,
)
from nts_storage.workspace import Workspace


def translate_chapter_mock(
    workspace: Workspace,
    *,
    chapter_id: str,
    provider_key: str,
) -> dict[str, Any]:
    if provider_key != "mock":
        raise ValueError("MVP1 text translation only supports provider `mock`.")

    chapter = get_chapter(workspace, chapter_id)
    project = get_project_by_id(workspace, chapter["project_id"])
    segments = list_segments(workspace, chapter_id=chapter_id)
    if not segments:
        raise ValueError(f"Chapter has no segments: {chapter_id}")

    source_text = "\n\n".join(segment["normalized_text"] for segment in segments)
    bundle = build_bundle(
        workspace,
        project_id=project["id"],
        text=source_text,
        top_k=20,
    )
    output_dir = workspace.path / "artifacts" / "translated"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{chapter_id}.vi.txt"
    rel_output_path = output_path.relative_to(workspace.path).as_posix()

    translation_rows: list[dict[str, Any]] = []
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="translate.text",
            status="running",
            stage="mock_translate",
            project_id=project["id"],
            input_data={"chapter_id": chapter_id, "provider": provider_key},
            result_data={},
        )
        output_parts: list[str] = []
        for segment in segments:
            prompt = (
                "MVP1 MOCK TRANSLATE\n"
                f"bundle_checksum={bundle['checksum']}\n"
                f"segment_id={segment['id']}\n"
                f"source={segment['normalized_text']}"
            )
            logged = log_mock_model_run(
                conn,
                task_run_id=task_id,
                provider_key=provider_key,
                prompt=prompt,
            )
            model_run_id = logged["model_run_id"]
            mock_text = f"[mock-vi:{logged['response']['output']}] {segment['normalized_text']}"
            translation_id = new_id("translation")
            quality = {
                "provider": "mock",
                "mock": True,
                "warnings": ["mock_translation_not_real"],
            }
            conn.execute(
                """
                INSERT INTO translations (
                    id, segment_id, chapter_id, translation_kind, text, status,
                    model_run_id, bundle_checksum, quality_json, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    translation_id,
                    segment["id"],
                    chapter_id,
                    "mock",
                    mock_text,
                    "draft",
                    model_run_id,
                    bundle["checksum"],
                    json_dumps(quality),
                    1,
                    now,
                ),
            )
            translation_rows.append(
                {
                    "id": translation_id,
                    "segment_id": segment["id"],
                    "chapter_id": chapter_id,
                    "text": mock_text,
                    "model_run_id": model_run_id,
                    "bundle_checksum": bundle["checksum"],
                    "quality_json": quality,
                }
            )
            output_parts.append(mock_text)

        output_path.write_text("\n\n".join(output_parts) + "\n", encoding="utf-8")
        result = {
            "chapter_id": chapter_id,
            "project_id": project["id"],
            "translations_created": len(translation_rows),
            "output_path": rel_output_path,
            "bundle_checksum": bundle["checksum"],
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()

    return {
        "task_run_id": task_id,
        "chapter_id": chapter_id,
        "project_id": project["id"],
        "bundle": bundle,
        "translations": translation_rows,
        "output_path": rel_output_path,
    }

