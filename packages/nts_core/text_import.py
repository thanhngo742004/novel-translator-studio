from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nts_core.projects import get_project_by_slug
from nts_storage.database import (
    connection,
    insert_task_run,
    json_dumps,
    new_id,
    row_to_dict,
    utc_now,
)
from nts_storage.workspace import Workspace


HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+.+|(?:chapter|chuong|chương)\s+\S+.*|第.{1,12}[章节回].*)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChapterBlock:
    title: str | None
    text: str
    start: int
    end: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines).strip()
    return normalized


def split_chapters(text: str) -> list[ChapterBlock]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for line in lines:
        line_start = offset
        line_end = offset + len(line)
        if HEADING_RE.match(line):
            headings.append((line_start, line_end, line.strip()))
        offset = line_end + 1

    if not headings:
        return [ChapterBlock(title=None, text=text, start=0, end=len(text))]

    chapters: list[ChapterBlock] = []
    for index, (start, _heading_end, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        chapter_text = text[start:end].strip()
        if chapter_text:
            chapters.append(ChapterBlock(title=title, text=chapter_text, start=start, end=end))
    return chapters or [ChapterBlock(title=None, text=text, start=0, end=len(text))]


def split_segments(chapter_text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", chapter_text) if part.strip()]
    return paragraphs or ([chapter_text.strip()] if chapter_text.strip() else [])


def _artifact_path(workspace: Workspace, doc_id: str, source_path: Path) -> Path:
    safe_name = source_path.name.replace("/", "_").replace("\\", "_")
    return workspace.path / "artifacts" / "raw" / f"{doc_id}_{safe_name}"


def import_text_file(
    workspace: Workspace,
    *,
    path: Path,
    project_slug: str,
    language: str | None = None,
) -> dict[str, Any]:
    source_path = path.resolve()
    if not source_path.exists():
        raise ValueError(f"Text file not found: {path}")
    if source_path.suffix.lower() != ".txt":
        raise ValueError("Only UTF-8 .txt files are supported in MVP1.")

    try:
        raw_text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Text file must be UTF-8 encoded.") from exc

    normalized = normalize_text(raw_text)
    if not normalized:
        raise ValueError("Text file is empty.")

    project = get_project_by_slug(workspace, project_slug)
    doc_id = new_id("document")
    artifact_path = _artifact_path(workspace, doc_id, source_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, artifact_path)

    checksum = sha256_file(source_path)
    chapters = split_chapters(normalized)
    imported_at = utc_now()
    rel_artifact = artifact_path.relative_to(workspace.path).as_posix()
    document_row = {
        "id": doc_id,
        "project_id": project["id"],
        "doc_kind": "raw_text",
        "source_path": str(source_path),
        "artifact_path": rel_artifact,
        "checksum_sha256": checksum,
        "language": language or project["source_lang"],
        "metadata_json": {"original_name": source_path.name},
        "imported_at": imported_at,
    }

    chapter_rows: list[dict[str, Any]] = []
    segment_rows: list[dict[str, Any]] = []

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="import.text",
            status="success",
            stage="completed",
            project_id=project["id"],
            input_data={"path": str(source_path), "project": project_slug, "language": language},
            result_data={},
        )
        conn.execute(
            """
            INSERT INTO documents (
                id, project_id, doc_kind, source_path, artifact_path, checksum_sha256,
                language, metadata_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                document_row["project_id"],
                document_row["doc_kind"],
                document_row["source_path"],
                document_row["artifact_path"],
                document_row["checksum_sha256"],
                document_row["language"],
                json_dumps(document_row["metadata_json"]),
                imported_at,
            ),
        )

        for chapter_index, chapter in enumerate(chapters, start=1):
            chapter_id = new_id("chapter")
            chapter_row = {
                "id": chapter_id,
                "project_id": project["id"],
                "document_id": doc_id,
                "chapter_no": chapter_index,
                "title": chapter.title,
                "boundary_start": chapter.start,
                "boundary_end": chapter.end,
                "confidence": 1.0,
                "created_at": imported_at,
            }
            chapter_rows.append(chapter_row)
            conn.execute(
                """
                INSERT INTO chapters (
                    id, project_id, document_id, chapter_no, title, boundary_start,
                    boundary_end, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chapter_id,
                    project["id"],
                    doc_id,
                    chapter_index,
                    chapter.title,
                    chapter.start,
                    chapter.end,
                    1.0,
                    imported_at,
                ),
            )

            for segment_index, segment_text in enumerate(split_segments(chapter.text), start=1):
                segment_id = new_id("segment")
                segment_row = {
                    "id": segment_id,
                    "project_id": project["id"],
                    "chapter_id": chapter_id,
                    "segment_no": segment_index,
                    "source_text": segment_text,
                    "normalized_text": normalize_text(segment_text),
                    "paragraph_no": segment_index,
                    "metadata_json": {},
                    "created_at": imported_at,
                }
                segment_rows.append(segment_row)
                conn.execute(
                    """
                    INSERT INTO segments (
                        id, project_id, chapter_id, segment_no, source_text, normalized_text,
                        paragraph_no, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        segment_id,
                        project["id"],
                        chapter_id,
                        segment_index,
                        segment_text,
                        segment_row["normalized_text"],
                        segment_index,
                        json_dumps({}),
                        imported_at,
                    ),
                )

        result = {
            "document_id": doc_id,
            "chapters_created": len(chapter_rows),
            "segments_created": len(segment_rows),
            "artifact_path": rel_artifact,
            "checksum_sha256": checksum,
        }
        conn.execute(
            "UPDATE task_runs SET result_json = ? WHERE id = ?",
            (json_dumps(result), task_id),
        )
        conn.commit()

    return {
        "task_run_id": task_id,
        "document": document_row,
        "chapters": chapter_rows,
        "segments_created": len(segment_rows),
    }


def list_chapters(workspace: Workspace, *, project_slug: str) -> list[dict[str, Any]]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, document_id, chapter_no, title, boundary_start,
                   boundary_end, confidence, created_at
            FROM chapters
            WHERE project_id = ?
            ORDER BY document_id, chapter_no
            """,
            (project["id"],),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_chapter(workspace: Workspace, chapter_id: str) -> dict[str, Any]:
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT id, project_id, document_id, chapter_no, title, boundary_start,
                   boundary_end, confidence, created_at
            FROM chapters
            WHERE id = ?
            """,
            (chapter_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Chapter not found: {chapter_id}")
    return row_to_dict(row)


def list_segments(workspace: Workspace, *, chapter_id: str) -> list[dict[str, Any]]:
    get_chapter(workspace, chapter_id)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, chapter_id, segment_no, source_text, normalized_text,
                   paragraph_no, metadata_json, created_at
            FROM segments
            WHERE chapter_id = ?
            ORDER BY segment_no
            """,
            (chapter_id,),
        ).fetchall()
    return [row_to_dict(row, json_fields=("metadata_json",)) for row in rows]

