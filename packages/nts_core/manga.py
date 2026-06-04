from __future__ import annotations

import json
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nts_core.projects import get_project_by_slug
from nts_core.text_import import sha256_file
from nts_storage.database import (
    connection,
    insert_task_run,
    json_dumps,
    new_id,
    row_to_dict,
    update_task_run,
    utc_now,
)
from nts_storage.workspace import Workspace


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_ARCHIVE_EXTENSIONS = {".cbz", ".zip"}
MANGA_MANIFEST_SCHEMA_VERSION = "phase9a.page_manifest.v1"
MANGA_HASH_ALGORITHM = "sha256"
MANGA_ARTIFACT_SUBDIRS = [
    "import",
    "preprocessing",
    "detection",
    "ocr",
    "reading_order",
    "translation",
    "cleaning",
    "rendering",
    "qa",
    "export",
    "provider",
    "human_review",
]


@dataclass(frozen=True)
class ImageSource:
    name: str
    data: bytes | None
    path: Path | None
    source_relpath: str


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)
    return cleaned or "page"


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    return f"{prefix}_{_sha256_bytes(payload.encode('utf-8'))[:32]}"


def _source_path_hash(path: Path) -> str:
    return _sha256_bytes(str(path.resolve()).encode("utf-8"))


def _image_format_from_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix == "jpg":
        return "jpeg"
    return suffix


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    return None, None


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None, None
    index = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while index + 4 <= len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = struct.unpack(">H", data[index + 3 : index + 5])[0]
            width = struct.unpack(">H", data[index + 5 : index + 7])[0]
            return width, height
        index += segment_length
    return None, None


def _webp_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return width, height
    return None, None


def _image_dimensions(data: bytes, name: str) -> tuple[int | None, int | None]:
    suffix = Path(name).suffix.lower()
    if suffix == ".png":
        return _png_dimensions(data)
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(data)
    if suffix == ".webp":
        return _webp_dimensions(data)
    return None, None


def _collect_folder_images(path: Path) -> tuple[list[ImageSource], list[str]]:
    warnings: list[str] = []
    images: list[ImageSource] = []
    for entry in sorted(path.iterdir(), key=lambda item: item.name.lower()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            images.append(ImageSource(name=entry.name, data=None, path=entry, source_relpath=entry.name))
        else:
            warnings.append(f"unsupported_file_ignored:{entry.name}")
    return images, warnings


def _collect_archive_images(path: Path) -> tuple[list[ImageSource], list[str]]:
    warnings: list[str] = []
    images: list[ImageSource] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename.lower()):
                if info.is_dir():
                    continue
                suffix = Path(info.filename).suffix.lower()
                if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
                    warnings.append(f"unsupported_file_ignored:{info.filename}")
                    continue
                images.append(
                    ImageSource(
                        name=Path(info.filename).name,
                        data=archive.read(info),
                        path=None,
                        source_relpath=info.filename,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid CBZ/ZIP archive: {path}") from exc
    return images, warnings


def _collect_images(path: Path) -> tuple[list[ImageSource], list[str], str]:
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError(f"Manga input not found: {path}")
    if resolved.is_dir():
        images, warnings = _collect_folder_images(resolved)
        source_kind = "folder"
    elif resolved.is_file() and resolved.suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS:
        images, warnings = _collect_archive_images(resolved)
        source_kind = "cbz" if resolved.suffix.lower() == ".cbz" else "zip"
    elif resolved.is_file() and resolved.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        images = [ImageSource(name=resolved.name, data=None, path=resolved, source_relpath=resolved.name)]
        warnings = []
        source_kind = "single_image"
    elif resolved.is_file() and resolved.suffix.lower() == ".pdf":
        raise ValueError(
            "BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED: PDF import adapter is not configured."
        )
    else:
        raise ValueError("Manga import supports folders, single images, .cbz, and .zip archives only.")
    if not images:
        raise ValueError("No supported manga image files found.")
    return images, warnings, source_kind


def _read_image_source(source: ImageSource) -> tuple[bytes, str]:
    if source.data is not None:
        return source.data, _sha256_bytes(source.data)
    if source.path is None:
        raise ValueError(f"Image source has no data: {source.name}")
    return source.path.read_bytes(), sha256_file(source.path)


def _create_artifact_root(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    artifact_root = workspace.path / "artifacts" / "manga" / project_slug / run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    for subdir in MANGA_ARTIFACT_SUBDIRS:
        (artifact_root / subdir).mkdir(parents=True, exist_ok=True)
    (artifact_root / "import" / "pages").mkdir(parents=True, exist_ok=True)
    return artifact_root


def _ensure_manga_project(conn, *, project: dict[str, Any], now: str) -> str:
    manga_project_id = _stable_id("mangaproject", project["id"], project["slug"])
    conn.execute(
        """
        INSERT INTO manga_projects (
            id, project_id, project_slug, title, source_lang, target_lang,
            reading_direction, content_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            project_slug = excluded.project_slug,
            title = excluded.title,
            source_lang = excluded.source_lang,
            target_lang = excluded.target_lang,
            updated_at = excluded.updated_at
        """,
        (
            manga_project_id,
            project["id"],
            project["slug"],
            project["name"],
            project["source_lang"],
            project["target_lang"],
            "right_to_left",
            "manga_image",
            now,
            now,
        ),
    )
    return manga_project_id


def _write_import_artifacts(
    *,
    artifact_root: Path,
    manifest: dict[str, Any],
    warnings: list[str],
    source_kind: str,
    source_label: str,
) -> tuple[Path, Path, Path]:
    manifest_path = artifact_root / "page_manifest.json"
    warnings_path = artifact_root / "import" / "import_warnings.json"
    summary_path = artifact_root / "import" / "import_summary.md"
    manifest_path.write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    warnings_path.write_text(
        json_dumps(
            {
                "schema_version": MANGA_MANIFEST_SCHEMA_VERSION,
                "warning_count": len(warnings),
                "warnings": warnings,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    summary_lines = [
        "# Manga Import Summary",
        "",
        f"- Schema version: `{MANGA_MANIFEST_SCHEMA_VERSION}`",
        f"- Source type: `{source_kind}`",
        f"- Source label: `{source_label}`",
        f"- Page count: `{manifest['page_count']}`",
        f"- Hash algorithm: `{MANGA_HASH_ALGORITHM}`",
        f"- Warning count: `{len(warnings)}`",
        "- PDF import: `BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED`",
        "",
    ]
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    return manifest_path, summary_path, warnings_path


def import_manga_pages(
    workspace: Workspace,
    *,
    path: Path,
    project_slug: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    images, warnings, source_kind = _collect_images(path)
    now = utc_now()
    run_id = new_id("mangarun")
    artifact_root = _create_artifact_root(workspace, project_slug=project_slug, run_id=run_id)
    page_artifact_dir = artifact_root / "import" / "pages"
    source_label = path.resolve().name
    duplicate_first_seen: dict[str, str] = {}
    pages: list[dict[str, Any]] = []

    with connection(workspace.db_path) as conn:
        manga_project_id = _ensure_manga_project(conn, project=project, now=now)
        task_id = insert_task_run(
            conn,
            task_type="manga.import",
            status="running",
            stage="import_pages",
            project_id=project["id"],
            input_data={
                "source_label": source_label,
                "source_path_hash": _source_path_hash(path),
                "source_kind": source_kind,
                "project": project_slug,
            },
            result_data={},
        )
        conn.execute(
            "UPDATE manga_pages SET status = ?, updated_at = ? WHERE project_id = ? AND status = ?",
            ("superseded", now, project["id"], "active"),
        )
        for page_index, source in enumerate(images, start=1):
            data, checksum = _read_image_source(source)
            width, height = _image_dimensions(data, source.name)
            page_id = _stable_id("mangapage", project["id"], page_index, checksum)
            duplicate_of = duplicate_first_seen.get(checksum)
            if duplicate_of is None:
                duplicate_first_seen[checksum] = page_id
            else:
                warnings.append(f"duplicate_page_hash:{page_id}:duplicates:{duplicate_of}:{checksum}")
            dest_name = f"{page_index:04d}_{checksum[:12]}_{_safe_name(source.name)}"
            dest_path = page_artifact_dir / dest_name
            if not dest_path.exists():
                dest_path.write_bytes(data)
            rel_path = dest_path.relative_to(workspace.path).as_posix()
            page = {
                "id": page_id,
                "page_id": page_id,
                "project_id": project["id"],
                "chapter_id": None,
                "page_index": page_index,
                "display_name": source.name,
                "source_relpath": source.source_relpath,
                "image_path": rel_path,
                "artifact_relpath": rel_path,
                "checksum_sha256": checksum,
                "image_hash": checksum,
                "width": None,
                "height": None,
                "format": _image_format_from_suffix(source.name),
                "status": "active",
                "excluded": False,
                "exclude_reason": None,
                "created_at": now,
                "updated_at": now,
            }
            page["width"] = width
            page["height"] = height
            conn.execute(
                """
                INSERT INTO manga_pages (
                    id, project_id, chapter_id, page_index, image_path, checksum_sha256,
                    width, height, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    page_index = excluded.page_index,
                    image_path = excluded.image_path,
                    checksum_sha256 = excluded.checksum_sha256,
                    width = excluded.width,
                    height = excluded.height,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    page_id,
                    project["id"],
                    None,
                    page_index,
                    rel_path,
                    checksum,
                    None,
                    None,
                    "active",
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO manga_page_artifacts (
                    id, page_id, artifact_kind, path, checksum_sha256, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("mangaartifact"),
                    page_id,
                    "original",
                    rel_path,
                    checksum,
                    json_dumps(
                        {
                            "source_name": source.name,
                            "source_relpath": source.source_relpath,
                            "source_kind": source_kind,
                            "run_id": run_id,
                            "format": page["format"],
                            "width": width,
                            "height": height,
                        }
                    ),
                    now,
                ),
            )
            pages.append(page)

        manifest_pages = [
            {
                "page_id": page["page_id"],
                "page_index": page["page_index"],
                "display_name": page["display_name"],
                "source_relpath": page["source_relpath"],
                "image_hash": page["image_hash"],
                "width": page["width"],
                "height": page["height"],
                "format": page["format"],
                "artifact_relpath": page["artifact_relpath"],
                "excluded": page["excluded"],
                "exclude_reason": page["exclude_reason"],
            }
            for page in pages
        ]
        manifest = {
            "schema_version": MANGA_MANIFEST_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "source_type": source_kind,
            "source_label": source_label,
            "created_at": now,
            "pages": manifest_pages,
            "page_count": len(manifest_pages),
            "hash_algorithm": MANGA_HASH_ALGORITHM,
            "warnings": warnings,
        }
        manifest_path, summary_path, warnings_path = _write_import_artifacts(
            artifact_root=artifact_root,
            manifest=manifest,
            warnings=warnings,
            source_kind=source_kind,
            source_label=source_label,
        )
        rel_artifact_root = artifact_root.relative_to(workspace.path).as_posix()
        rel_manifest = manifest_path.relative_to(workspace.path).as_posix()
        rel_summary = summary_path.relative_to(workspace.path).as_posix()
        rel_warnings = warnings_path.relative_to(workspace.path).as_posix()
        conn.execute(
            """
            INSERT INTO manga_import_runs (
                id, run_id, manga_project_id, project_id, project_slug, source_type,
                source_label, source_path_hash, artifact_root, manifest_path, page_count,
                errors_json, warnings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangaimport"),
                run_id,
                manga_project_id,
                project["id"],
                project_slug,
                source_kind,
                source_label,
                _source_path_hash(path),
                rel_artifact_root,
                rel_manifest,
                len(pages),
                json_dumps([]),
                json_dumps(warnings),
                now,
                now,
            ),
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "source_kind": source_kind,
            "source_type": source_kind,
            "source_label": source_label,
            "artifact_root": rel_artifact_root,
            "manifest_path": rel_manifest,
            "page_manifest_path": rel_manifest,
            "import_summary_path": rel_summary,
            "import_warnings_path": rel_warnings,
            "manifest_schema_version": MANGA_MANIFEST_SCHEMA_VERSION,
            "hash_algorithm": MANGA_HASH_ALGORITHM,
            "pdf_import_status": "BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED",
            "pages_imported": len(pages),
            "pages": pages,
            "warnings": warnings,
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    return {"task_run_id": task_id, **result}


def list_manga_pages(workspace: Workspace, *, project_slug: str) -> list[dict[str, Any]]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, chapter_id, page_index, image_path, checksum_sha256,
                   width, height, status, created_at, updated_at
            FROM manga_pages
            WHERE project_id = ?
            ORDER BY page_index ASC, created_at ASC, id ASC
            """,
            (project["id"],),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def _page_by_index(conn, *, project_id: str, page_index: int):
    row = conn.execute(
        """
        SELECT id, project_id, chapter_id, page_index, image_path, checksum_sha256,
               width, height, status, created_at, updated_at
        FROM manga_pages
        WHERE project_id = ? AND page_index = ? AND status = 'active'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (project_id, page_index),
    ).fetchone()
    if row is None:
        raise ValueError(f"Manga page not found for page_index={page_index}")
    return row_to_dict(row)


def _validate_box_payload(box: dict[str, Any]) -> None:
    if "box_id" not in box:
        raise ValueError("Each manga box requires box_id.")
    bbox = box.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"Box {box.get('box_id')} requires bbox with four numbers.")
    if any(not isinstance(value, (int, float)) for value in bbox):
        raise ValueError(f"Box {box.get('box_id')} bbox values must be numeric.")
    if not box.get("box_type"):
        raise ValueError(f"Box {box.get('box_id')} requires box_type.")


def import_manga_boxes(
    workspace: Workspace,
    *,
    boxes_path: Path,
    project_slug: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if not boxes_path.exists():
        raise ValueError(f"Boxes JSON not found: {boxes_path}")
    try:
        payload = json.loads(boxes_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Boxes file must contain valid JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
        raise ValueError("Boxes JSON must contain a pages array.")

    now = utc_now()
    boxes_created = 0
    versions_created = 0
    imported_boxes: list[dict[str, Any]] = []
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.boxes.import",
            status="running",
            stage="import_boxes",
            project_id=project["id"],
            input_data={"boxes_path": str(boxes_path.resolve()), "project": project_slug},
            result_data={},
        )
        for page_payload in payload["pages"]:
            if not isinstance(page_payload, dict):
                raise ValueError("Each page entry must be an object.")
            page_index = page_payload.get("page_index")
            if not isinstance(page_index, int):
                raise ValueError("Each page entry requires integer page_index.")
            boxes = page_payload.get("boxes") or []
            if not isinstance(boxes, list):
                raise ValueError("Page boxes must be an array.")
            page = _page_by_index(conn, project_id=project["id"], page_index=page_index)
            for box in boxes:
                if not isinstance(box, dict):
                    raise ValueError("Each box entry must be an object.")
                _validate_box_payload(box)
                stable_key = str(box["box_id"])
                existing = conn.execute(
                    """
                    SELECT id, current_version_id
                    FROM manga_boxes
                    WHERE page_id = ? AND stable_key = ? AND deleted = 0
                    """,
                    (page["id"], stable_key),
                ).fetchone()
                if existing:
                    box_id = existing["id"]
                    previous_version_id = existing["current_version_id"]
                    revision_no = (
                        conn.execute(
                            "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM manga_box_versions WHERE box_id = ?",
                            (box_id,),
                        ).fetchone()[0]
                    )
                else:
                    box_id = new_id("mangabox")
                    previous_version_id = None
                    revision_no = 1
                    boxes_created += 1
                    conn.execute(
                        """
                        INSERT INTO manga_boxes (
                            id, page_id, stable_key, current_version_id, deleted,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (box_id, page["id"], stable_key, None, 0, now, now),
                    )
                version_id = new_id("mangaboxver")
                conn.execute(
                    """
                    INSERT INTO manga_box_versions (
                        id, box_id, revision_no, bbox_json, polygon_json, box_type,
                        reading_order, speaker_id, origin, previous_version_id,
                        change_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        box_id,
                        revision_no,
                        json_dumps(box["bbox"]),
                        json_dumps(box.get("polygon")) if box.get("polygon") is not None else None,
                        str(box["box_type"]),
                        box.get("reading_order"),
                        box.get("speaker_id"),
                        "manual_import",
                        previous_version_id,
                        "boxes_json_import",
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE manga_boxes SET current_version_id = ?, updated_at = ? WHERE id = ?",
                    (version_id, now, box_id),
                )
                versions_created += 1
                imported_boxes.append(
                    {
                        "box_id": stable_key,
                        "internal_box_id": box_id,
                        "version_id": version_id,
                        "revision_no": revision_no,
                        "page_id": page["id"],
                        "page_index": page_index,
                    }
                )

        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "boxes_created": boxes_created,
            "versions_created": versions_created,
            "boxes": imported_boxes,
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    return {"task_run_id": task_id, **result}


def _current_boxes_for_project(conn, *, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id AS page_id, p.page_index, b.id AS internal_box_id, b.stable_key,
               v.id AS version_id, v.revision_no, v.bbox_json, v.polygon_json,
               v.box_type, v.reading_order, v.speaker_id
        FROM manga_pages p
        LEFT JOIN manga_boxes b ON b.page_id = p.id AND b.deleted = 0
        LEFT JOIN manga_box_versions v ON v.id = b.current_version_id
        WHERE p.project_id = ? AND p.status = 'active'
        ORDER BY p.page_index ASC, v.reading_order ASC, b.stable_key ASC
        """,
        (project_id,),
    ).fetchall()
    return [row_to_dict(row, json_fields=("bbox_json", "polygon_json")) for row in rows]


def export_manga_boxes(workspace: Workspace, *, project_slug: str) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    export_dir = workspace.path / "artifacts" / "manga" / project_slug
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / "boxes.json"
    with connection(workspace.db_path) as conn:
        rows = _current_boxes_for_project(conn, project_id=project["id"])
    pages: dict[int, dict[str, Any]] = {}
    for row in rows:
        page = pages.setdefault(row["page_index"], {"page_index": row["page_index"], "boxes": []})
        if row.get("stable_key") is None:
            continue
        page["boxes"].append(
            {
                "box_id": row["stable_key"],
                "bbox": row["bbox_json"],
                "polygon": row["polygon_json"],
                "box_type": row["box_type"],
                "reading_order": row["reading_order"],
                "speaker_id": row["speaker_id"],
            }
        )
    payload = {"pages": [pages[key] for key in sorted(pages)]}
    export_path.write_text(json_dumps(payload) + "\n", encoding="utf-8")
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "boxes_path": export_path.relative_to(workspace.path).as_posix(),
        "boxes_json": payload,
    }


def export_manga_manifest(workspace: Workspace, *, project_slug: str) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    export_dir = workspace.path / "artifacts" / "manga" / project_slug
    export_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = export_dir / "manifest.json"
    with connection(workspace.db_path) as conn:
        page_rows = conn.execute(
            """
            SELECT id, project_id, chapter_id, page_index, image_path, checksum_sha256,
                   width, height, status, created_at, updated_at
            FROM manga_pages
            WHERE project_id = ? AND status = 'active'
            ORDER BY page_index ASC, created_at ASC, id ASC
            """,
            (project["id"],),
        ).fetchall()
        box_rows = _current_boxes_for_project(conn, project_id=project["id"])
    boxes_by_page: dict[str, list[dict[str, Any]]] = {}
    for row in box_rows:
        if row.get("stable_key") is None:
            continue
        boxes_by_page.setdefault(row["page_id"], []).append(
            {
                "box_id": row["stable_key"],
                "bbox": row["bbox_json"],
                "polygon": row["polygon_json"],
                "box_type": row["box_type"],
                "reading_order": row["reading_order"],
                "speaker_id": row["speaker_id"],
                "ocr_text": None,
                "translation_text": None,
            }
        )
    manifest = {
        "project_id": project["id"],
        "project_slug": project_slug,
        "pages": [
            {
                "page_id": row["id"],
                "page_index": row["page_index"],
                "image_path": row["image_path"],
                "boxes": boxes_by_page.get(row["id"], []),
            }
            for row in page_rows
        ],
    }
    manifest_path.write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    checksum = sha256_file(manifest_path)
    now = utc_now()
    rel_manifest = manifest_path.relative_to(workspace.path).as_posix()
    with connection(workspace.db_path) as conn:
        export_id = new_id("mangaexport")
        conn.execute(
            """
            INSERT INTO manga_exports (
                id, project_id, chapter_id, export_kind, export_path, checksum_sha256,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export_id,
                project["id"],
                None,
                "manifest",
                rel_manifest,
                checksum,
                json_dumps({"page_count": len(manifest["pages"])}),
                now,
            ),
        )
        conn.commit()
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "manifest_path": rel_manifest,
        "checksum_sha256": checksum,
        "manifest": manifest,
    }
