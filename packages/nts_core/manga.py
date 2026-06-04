from __future__ import annotations

import json
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
MANGA_PREPROCESS_SCHEMA_VERSION = "phase9b.preprocess_manifest.v1"
MANGA_DETECTION_SCHEMA_VERSION = "phase9c.detection_manifest.v1"
MANGA_HASH_ALGORITHM = "sha256"
MANGA_PREPROCESS_NORMALIZED_FORMAT = "png"
MANGA_PREPROCESS_MAX_DIMENSION = 2400
MANGA_PREVIEW_MAX_DIMENSION = 320
MANGA_THRESHOLD_VALUE = 180
MANGA_REGION_TYPES = {"dialogue", "caption", "narration", "sfx", "sign", "note", "unknown"}
MANGA_DETECTION_REVIEW_STATE = "needs_review"
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


@dataclass(frozen=True)
class DetectedRegion:
    page_id: str
    region_type: str
    bbox: list[float]
    polygon: list[list[float]] | None
    confidence: float
    orientation: str
    source: str
    adapter_id: str
    review_state: str = MANGA_DETECTION_REVIEW_STATE


class DetectionAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    execution_mode: str
    provides_bubbles: bool

    def detect(self, *, preprocess_manifest: dict[str, Any]) -> list[DetectedRegion]:
        ...


class MockDetectionAdapter:
    adapter_id = "mock_local_detector"
    adapter_version = "phase9c.v1"
    execution_mode = "local"
    provides_bubbles = True

    def detect(self, *, preprocess_manifest: dict[str, Any]) -> list[DetectedRegion]:
        regions: list[DetectedRegion] = []
        for page in preprocess_manifest["pages"]:
            width = int(page["width"])
            height = int(page["height"])
            primary_bbox = [
                max(0, round(width * 0.15, 2)),
                max(0, round(height * 0.18, 2)),
                max(1, round(width * 0.5, 2)),
                max(1, round(height * 0.22, 2)),
            ]
            sfx_bbox = [
                max(0, round(width * 0.62, 2)),
                max(0, round(height * 0.58, 2)),
                max(1, round(width * 0.23, 2)),
                max(1, round(height * 0.18, 2)),
            ]
            regions.append(
                DetectedRegion(
                    page_id=str(page["page_id"]),
                    region_type="dialogue",
                    bbox=primary_bbox,
                    polygon=None,
                    confidence=0.91,
                    orientation="horizontal",
                    source="local_adapter",
                    adapter_id=self.adapter_id,
                )
            )
            regions.append(
                DetectedRegion(
                    page_id=str(page["page_id"]),
                    region_type="sfx",
                    bbox=sfx_bbox,
                    polygon=None,
                    confidence=0.74,
                    orientation="unknown",
                    source="local_adapter",
                    adapter_id=self.adapter_id,
                )
            )
        return regions


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


def _load_pillow():
    try:
        from PIL import Image, ImageOps
    except Exception as exc:
        raise ValueError(
            "BLOCKED_IMAGE_LIBRARY: Pillow is required for deterministic image preprocessing."
        ) from exc
    return Image, ImageOps


def _relative_to_workspace(workspace: Workspace, path: Path) -> str:
    return path.relative_to(workspace.path).as_posix()


def _artifact_root_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    return workspace.path / "artifacts" / "manga" / project_slug / run_id


def _page_manifest_path(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    return _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "page_manifest.json"


def _load_page_manifest(workspace: Workspace, *, project_slug: str, run_id: str) -> dict[str, Any]:
    manifest_path = _page_manifest_path(workspace, project_slug=project_slug, run_id=run_id)
    if not manifest_path.exists():
        raise ValueError(f"BLOCKED_MANIFEST_INCOMPLETE: page manifest not found for run {run_id}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("BLOCKED_MANIFEST_INCOMPLETE: page manifest is not valid JSON.") from exc
    if manifest.get("project_slug") != project_slug or manifest.get("run_id") != run_id:
        raise ValueError("BLOCKED_MANIFEST_INCOMPLETE: page manifest project/run mismatch.")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("BLOCKED_MANIFEST_INCOMPLETE: page manifest has no pages.")
    for page in pages:
        if not isinstance(page, dict) or not page.get("page_id") or not page.get("artifact_relpath"):
            raise ValueError("BLOCKED_MANIFEST_INCOMPLETE: page entry lacks image reference.")
    return manifest


def _preprocess_manifest_path(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    return (
        _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
        / "preprocessing"
        / "preprocess_manifest.json"
    )


def _load_preprocess_manifest(workspace: Workspace, *, project_slug: str, run_id: str) -> dict[str, Any]:
    manifest_path = _preprocess_manifest_path(workspace, project_slug=project_slug, run_id=run_id)
    if not manifest_path.exists():
        raise ValueError(f"BLOCKED_PREPROCESS_MISSING: preprocess manifest not found for run {run_id}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("BLOCKED_PREPROCESS_MISSING: preprocess manifest is not valid JSON.") from exc
    if manifest.get("project_slug") != project_slug or manifest.get("run_id") != run_id:
        raise ValueError("BLOCKED_PREPROCESS_MISSING: preprocess manifest project/run mismatch.")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("BLOCKED_PREPROCESS_MISSING: preprocess manifest has no pages.")
    for page in pages:
        if not isinstance(page, dict) or not page.get("page_id") or not page.get("normalized_artifact"):
            raise ValueError("BLOCKED_PREPROCESS_MISSING: preprocess page lacks normalized artifact.")
    return manifest


def _detection_adapter(adapter_id: str) -> DetectionAdapter:
    if adapter_id == MockDetectionAdapter.adapter_id or adapter_id == "mock":
        return MockDetectionAdapter()
    raise ValueError(f"Unsupported detection adapter: {adapter_id}")


def _validate_region_type(region_type: str) -> str:
    if region_type not in MANGA_REGION_TYPES:
        raise ValueError(
            f"Invalid manga region_type: {region_type}. Expected one of {sorted(MANGA_REGION_TYPES)}."
        )
    return region_type


def _validate_bbox(
    bbox: Any,
    *,
    page_width: int | float | None = None,
    page_height: int | float | None = None,
    box_label: str = "box",
) -> list[float]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError(f"{box_label} requires bbox with four numbers.")
    values: list[float] = []
    for value in bbox:
        if not isinstance(value, (int, float)):
            raise ValueError(f"{box_label} bbox values must be numeric.")
        values.append(float(value))
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError(f"{box_label} bbox must have non-negative origin and positive size.")
    if page_width is not None and x + width > float(page_width):
        raise ValueError(f"{box_label} bbox exceeds page width.")
    if page_height is not None and y + height > float(page_height):
        raise ValueError(f"{box_label} bbox exceeds page height.")
    return values


def _validate_polygon(polygon: Any, *, box_label: str = "box") -> list[list[float]] | None:
    if polygon is None:
        return None
    if not isinstance(polygon, list):
        raise ValueError(f"{box_label} polygon must be a list of points.")
    normalized: list[list[float]] = []
    for point in polygon:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or any(not isinstance(value, (int, float)) for value in point)
        ):
            raise ValueError(f"{box_label} polygon points must be [x, y] numbers.")
        normalized.append([float(point[0]), float(point[1])])
    return normalized


def _stable_detection_box_id(region: DetectedRegion) -> str:
    bbox_key = ",".join(f"{value:.3f}" for value in region.bbox)
    return _stable_id(
        "mangabox",
        region.page_id,
        region.adapter_id,
        region.region_type,
        bbox_key,
        region.orientation,
    )


def _region_to_payload(region: DetectedRegion, *, page_size: tuple[int, int]) -> dict[str, Any]:
    width, height = page_size
    bbox = _validate_bbox(
        region.bbox,
        page_width=width,
        page_height=height,
        box_label=f"detected region {region.page_id}",
    )
    polygon = _validate_polygon(region.polygon, box_label=f"detected region {region.page_id}")
    region_type = _validate_region_type(region.region_type)
    if not 0 <= region.confidence <= 1:
        raise ValueError(f"Detected region confidence must be between 0 and 1: {region.page_id}")
    payload = {
        "page_id": region.page_id,
        "box_id": _stable_detection_box_id(
            DetectedRegion(
                page_id=region.page_id,
                region_type=region_type,
                bbox=bbox,
                polygon=polygon,
                confidence=region.confidence,
                orientation=region.orientation,
                source=region.source,
                adapter_id=region.adapter_id,
                review_state=region.review_state,
            )
        ),
        "region_type": region_type,
        "bbox": bbox,
        "polygon": polygon,
        "confidence": float(region.confidence),
        "orientation": region.orientation or "unknown",
        "source": region.source,
        "adapter_id": region.adapter_id,
        "review_state": region.review_state,
    }
    if payload["source"] not in {"manual", "imported", "local_adapter", "cloud_adapter"}:
        raise ValueError(f"Invalid detection source: {payload['source']}")
    return payload


def _confidence_summary(regions: list[dict[str, Any]]) -> dict[str, Any]:
    if not regions:
        return {"count": 0, "min": None, "max": None, "average": None, "low_confidence_count": 0}
    values = [float(region["confidence"]) for region in regions]
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "average": round(sum(values) / len(values), 6),
        "low_confidence_count": len([value for value in values if value < 0.8]),
    }


def _ensure_preprocess_dirs(artifact_root: Path) -> dict[str, Path]:
    base = artifact_root / "preprocessing"
    dirs = {
        "base": base,
        "pages": base / "pages",
        "ocr_variants": base / "ocr_variants",
        "previews": base / "previews",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def _save_png(image: Any, path: Path, *, force: bool) -> None:
    if path.exists() and not force:
        return
    image.save(path, format="PNG", optimize=False)


def _resize_for_policy(image: Any, *, max_dimension: int) -> tuple[Any, bool]:
    width, height = image.size
    largest = max(width, height)
    if largest <= max_dimension:
        return image.copy(), False
    scale = max_dimension / largest
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resample = getattr(type(image), "Resampling", None)
    method = resample.LANCZOS if resample is not None else 1
    return image.resize(new_size, method), True


def _image_checksum(path: Path) -> str:
    return sha256_file(path)


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


def preprocess_manga_pages(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    force: bool = False,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    Image, ImageOps = _load_pillow()
    page_manifest = _load_page_manifest(workspace, project_slug=project_slug, run_id=run_id)
    artifact_root = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
    preprocess_dirs = _ensure_preprocess_dirs(artifact_root)
    preprocess_manifest_path = preprocess_dirs["base"] / "preprocess_manifest.json"
    preprocess_summary_path = preprocess_dirs["base"] / "preprocess_summary.md"

    if preprocess_manifest_path.exists() and not force:
        existing_manifest = json.loads(preprocess_manifest_path.read_text(encoding="utf-8"))
        return {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "preprocess_manifest_path": _relative_to_workspace(workspace, preprocess_manifest_path),
            "preprocess_summary_path": _relative_to_workspace(workspace, preprocess_summary_path),
            "pages_processed": existing_manifest.get("page_count", 0),
            "warnings": existing_manifest.get("warnings", []),
            "rerun_reused_existing": True,
            "force": False,
            "manifest": existing_manifest,
        }

    now = utc_now()
    records: list[dict[str, Any]] = []
    warnings: list[str] = []

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.preprocess",
            status="running",
            stage="preprocess_pages",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "force": force},
            result_data={},
        )
        for page in page_manifest["pages"]:
            page_id = str(page["page_id"])
            page_index = int(page["page_index"])
            source_rel = str(page["artifact_relpath"])
            source_path = workspace.path / source_rel
            page_warnings: list[str] = []
            if not source_path.exists():
                raise ValueError(
                    f"BLOCKED_MANIFEST_INCOMPLETE: source artifact missing for page {page_id}."
                )
            original_checksum = sha256_file(source_path)
            with Image.open(source_path) as source_image:
                exif_orientation = None
                try:
                    exif_orientation = source_image.getexif().get(274)
                except Exception:
                    page_warnings.append("exif_orientation_unreadable")
                oriented = ImageOps.exif_transpose(source_image)
                orientation_applied = exif_orientation not in (None, 1)
                normalized_rgb = oriented.convert("RGB")
                normalized, resized = _resize_for_policy(
                    normalized_rgb, max_dimension=MANGA_PREPROCESS_MAX_DIMENSION
                )
                if resized:
                    page_warnings.append(
                        f"resized_to_max_dimension:{MANGA_PREPROCESS_MAX_DIMENSION}"
                    )
                stem = f"{page_index:04d}_{page_id}"
                normalized_path = preprocess_dirs["pages"] / f"{stem}_normalized.png"
                grayscale_path = preprocess_dirs["ocr_variants"] / f"{stem}_grayscale.png"
                threshold_path = preprocess_dirs["ocr_variants"] / f"{stem}_threshold.png"
                preview_path = preprocess_dirs["previews"] / f"{stem}_preview.png"

                _save_png(normalized, normalized_path, force=force)
                grayscale = normalized.convert("L")
                _save_png(grayscale, grayscale_path, force=force)
                contrast = ImageOps.autocontrast(grayscale)
                threshold = contrast.point(
                    lambda value: 255 if value >= MANGA_THRESHOLD_VALUE else 0,
                    mode="L",
                )
                _save_png(threshold, threshold_path, force=force)
                preview, _preview_resized = _resize_for_policy(
                    normalized_rgb, max_dimension=MANGA_PREVIEW_MAX_DIMENSION
                )
                _save_png(preview, preview_path, force=force)

            if sha256_file(source_path) != original_checksum:
                raise ValueError(f"Source artifact changed during preprocessing for page {page_id}.")

            page_warnings.extend(
                warning
                for warning in [
                    "width_missing_in_source_manifest" if page.get("width") is None else None,
                    "height_missing_in_source_manifest" if page.get("height") is None else None,
                ]
                if warning is not None
            )
            normalized_width, normalized_height = _png_dimensions(
                normalized_path.read_bytes()
            )
            record = {
                "page_id": page_id,
                "source_artifact": source_rel,
                "normalized_artifact": _relative_to_workspace(workspace, normalized_path),
                "ocr_variant_artifacts": {
                    "grayscale": _relative_to_workspace(workspace, grayscale_path),
                    "threshold": _relative_to_workspace(workspace, threshold_path),
                },
                "preview_artifact": _relative_to_workspace(workspace, preview_path),
                "width": normalized_width,
                "height": normalized_height,
                "format": MANGA_PREPROCESS_NORMALIZED_FORMAT,
                "orientation_applied": orientation_applied,
                "warnings": page_warnings,
            }
            records.append(record)
            warnings.extend(f"{page_id}:{warning}" for warning in page_warnings)

            for artifact_kind, artifact_path in [
                ("preprocess.normalized", normalized_path),
                ("preprocess.ocr.grayscale", grayscale_path),
                ("preprocess.ocr.threshold", threshold_path),
                ("preprocess.preview", preview_path),
            ]:
                conn.execute(
                    """
                    INSERT INTO manga_page_artifacts (
                        id, page_id, artifact_kind, path, checksum_sha256, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("mangaartifact"),
                        page_id,
                        artifact_kind,
                        _relative_to_workspace(workspace, artifact_path),
                        _image_checksum(artifact_path),
                        json_dumps({"run_id": run_id, "stage": "preprocessing"}),
                        now,
                    ),
                )

        manifest = {
            "schema_version": MANGA_PREPROCESS_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "source_manifest": _relative_to_workspace(
                workspace, _page_manifest_path(workspace, project_slug=project_slug, run_id=run_id)
            ),
            "created_at": now,
            "force": force,
            "page_count": len(records),
            "pages": records,
            "format_policy": {
                "normalized_format": MANGA_PREPROCESS_NORMALIZED_FORMAT,
                "ocr_variants": ["grayscale", "threshold"],
                "threshold_value": MANGA_THRESHOLD_VALUE,
            },
            "size_policy": {
                "max_dimension": MANGA_PREPROCESS_MAX_DIMENSION,
                "preview_max_dimension": MANGA_PREVIEW_MAX_DIMENSION,
                "upscale": False,
            },
            "warnings": warnings,
        }
        preprocess_manifest_path.write_text(json_dumps(manifest) + "\n", encoding="utf-8")
        summary_lines = [
            "# Manga Preprocessing Summary",
            "",
            f"- Schema version: `{MANGA_PREPROCESS_SCHEMA_VERSION}`",
            f"- Project: `{project_slug}`",
            f"- Run ID: `{run_id}`",
            f"- Pages processed: `{len(records)}`",
            f"- Normalized format: `{MANGA_PREPROCESS_NORMALIZED_FORMAT}`",
            "- OCR variants: `grayscale`, `threshold`",
            f"- Preview max dimension: `{MANGA_PREVIEW_MAX_DIMENSION}`",
            f"- Warning count: `{len(warnings)}`",
            "",
        ]
        preprocess_summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
        rel_manifest = _relative_to_workspace(workspace, preprocess_manifest_path)
        rel_summary = _relative_to_workspace(workspace, preprocess_summary_path)
        conn.execute(
            """
            INSERT INTO manga_preprocess_runs (
                id, run_id, project_id, project_slug, source_manifest_path,
                artifact_root, preprocess_manifest_path, page_count, force,
                warnings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangapreprocess"),
                run_id,
                project["id"],
                project_slug,
                manifest["source_manifest"],
                _relative_to_workspace(workspace, artifact_root),
                rel_manifest,
                len(records),
                1 if force else 0,
                json_dumps(warnings),
                now,
                now,
            ),
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "preprocess_manifest_path": rel_manifest,
            "preprocess_summary_path": rel_summary,
            "pages_processed": len(records),
            "warnings": warnings,
            "rerun_reused_existing": False,
            "force": force,
            "manifest": manifest,
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


def _current_boxes_for_page(conn, *, project_id: str, page_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id AS page_id, p.page_index, b.id AS internal_box_id, b.stable_key,
               v.id AS version_id, v.revision_no, v.bbox_json, v.polygon_json,
               v.box_type, v.reading_order, v.speaker_id, v.origin
        FROM manga_pages p
        JOIN manga_boxes b ON b.page_id = p.id AND b.deleted = 0
        JOIN manga_box_versions v ON v.id = b.current_version_id
        WHERE p.project_id = ? AND p.id = ? AND p.status = 'active'
        ORDER BY v.reading_order ASC, b.stable_key ASC
        """,
        (project_id, page_id),
    ).fetchall()
    return [row_to_dict(row, json_fields=("bbox_json", "polygon_json")) for row in rows]


def _box_row_to_region(row: dict[str, Any]) -> dict[str, Any]:
    source = "manual" if row.get("origin") == "manual_import" else str(row.get("origin") or "imported")
    if source not in {"manual", "imported", "local_adapter", "cloud_adapter"}:
        source = "imported"
    return {
        "page_id": row["page_id"],
        "box_id": row["stable_key"],
        "region_type": "dialogue" if row["box_type"] == "speech" else row["box_type"],
        "bbox": row["bbox_json"],
        "polygon": row["polygon_json"],
        "confidence": 1.0 if source in {"manual", "imported"} else None,
        "orientation": "unknown",
        "source": source,
        "adapter_id": None,
        "review_state": "manual" if source == "manual" else "active",
    }


def run_manga_detection(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    adapter_id: str = "mock",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    preprocess_manifest = _load_preprocess_manifest(workspace, project_slug=project_slug, run_id=run_id)
    adapter = _detection_adapter(adapter_id)
    if adapter.execution_mode == "cloud":
        raise ValueError("Cloud detection adapters require explicit opt-in and are not enabled in Phase 9C.")
    artifact_root = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
    detection_dir = artifact_root / "detection"
    detection_dir.mkdir(parents=True, exist_ok=True)
    regions_path = detection_dir / "regions.json"
    bubbles_path = detection_dir / "bubbles.json"
    merged_path = detection_dir / "boxes_merged.json"
    summary_path = detection_dir / "detection_summary.md"
    now = utc_now()

    page_sizes = {
        str(page["page_id"]): (int(page["width"]), int(page["height"]))
        for page in preprocess_manifest["pages"]
    }
    detected_regions = [
        _region_to_payload(region, page_size=page_sizes[str(region.page_id)])
        for region in adapter.detect(preprocess_manifest=preprocess_manifest)
    ]
    bubble_regions = (
        [region for region in detected_regions if region["region_type"] == "dialogue"]
        if adapter.provides_bubbles
        else []
    )
    inserted_count = 0
    skipped_existing_count = 0
    merged_pages: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.detect",
            status="running",
            stage="detect_regions",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "adapter_id": adapter.adapter_id},
            result_data={},
        )
        page_rows = {
            row["id"]: row_to_dict(row)
            for row in conn.execute(
                """
                SELECT id, page_index
                FROM manga_pages
                WHERE project_id = ? AND status = 'active'
                """,
                (project["id"],),
            ).fetchall()
        }
        for page_id, page_size in page_sizes.items():
            if page_id not in page_rows:
                raise ValueError(f"BLOCKED_MANIFEST_INCOMPLETE: active page missing for {page_id}.")
            manual_boxes = _current_boxes_for_page(conn, project_id=project["id"], page_id=page_id)
            merged_pages[page_id] = {
                "page_id": page_id,
                "page_index": page_rows[page_id]["page_index"],
                "boxes": [_box_row_to_region(row) for row in manual_boxes],
            }
            existing_stable_keys = {str(row["stable_key"]) for row in manual_boxes}
            for region in [item for item in detected_regions if item["page_id"] == page_id]:
                if region["box_id"] in existing_stable_keys:
                    skipped_existing_count += 1
                    warnings.append(f"adapter_box_preserved_existing:{region['box_id']}")
                    continue
                internal_box_id = new_id("mangabox")
                version_id = new_id("mangaboxver")
                conn.execute(
                    """
                    INSERT INTO manga_boxes (
                        id, page_id, stable_key, current_version_id, deleted,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (internal_box_id, page_id, region["box_id"], version_id, 0, now, now),
                )
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
                        internal_box_id,
                        1,
                        json_dumps(region["bbox"]),
                        json_dumps(region["polygon"]) if region.get("polygon") is not None else None,
                        region["region_type"],
                        None,
                        None,
                        region["source"],
                        None,
                        f"detection_adapter:{adapter.adapter_id}",
                        now,
                    ),
                )
                inserted_count += 1
                existing_stable_keys.add(region["box_id"])
                merged_pages[page_id]["boxes"].append(region)

        regions_payload = {
            "schema_version": MANGA_DETECTION_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "adapter": {
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.adapter_version,
                "execution_mode": adapter.execution_mode,
                "cloud_used": adapter.execution_mode == "cloud",
            },
            "regions": detected_regions,
            "confidence_summary": _confidence_summary(detected_regions),
            "warnings": warnings,
        }
        bubbles_payload = {
            "schema_version": MANGA_DETECTION_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "adapter_id": adapter.adapter_id,
            "bubbles_available": adapter.provides_bubbles,
            "bubbles": bubble_regions,
        }
        merged_payload = {
            "schema_version": MANGA_DETECTION_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "pages": [
                merged_pages[page_id]
                for page_id in sorted(
                    merged_pages,
                    key=lambda item: (merged_pages[item]["page_index"], item),
                )
            ],
            "box_count": sum(len(page["boxes"]) for page in merged_pages.values()),
            "manual_preserved": True,
            "adapter_boxes_inserted": inserted_count,
            "adapter_boxes_skipped_existing": skipped_existing_count,
        }
        regions_path.write_text(json_dumps(regions_payload) + "\n", encoding="utf-8")
        bubbles_path.write_text(json_dumps(bubbles_payload) + "\n", encoding="utf-8")
        merged_path.write_text(json_dumps(merged_payload) + "\n", encoding="utf-8")
        confidence = regions_payload["confidence_summary"]
        summary_lines = [
            "# Manga Detection Summary",
            "",
            f"- Schema version: `{MANGA_DETECTION_SCHEMA_VERSION}`",
            f"- Project: `{project_slug}`",
            f"- Run ID: `{run_id}`",
            f"- Adapter: `{adapter.adapter_id}`",
            f"- Execution mode: `{adapter.execution_mode}`",
            f"- Cloud used: `{adapter.execution_mode == 'cloud'}`",
            f"- Regions detected: `{len(detected_regions)}`",
            f"- Bubble regions available: `{adapter.provides_bubbles}`",
            f"- Adapter boxes inserted: `{inserted_count}`",
            f"- Adapter boxes skipped existing: `{skipped_existing_count}`",
            f"- Low confidence regions: `{confidence['low_confidence_count']}`",
            "",
        ]
        summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
        rel_regions = _relative_to_workspace(workspace, regions_path)
        rel_bubbles = _relative_to_workspace(workspace, bubbles_path)
        rel_merged = _relative_to_workspace(workspace, merged_path)
        rel_summary = _relative_to_workspace(workspace, summary_path)
        conn.execute(
            """
            INSERT INTO manga_detection_runs (
                id, run_id, project_id, project_slug, adapter_id, adapter_version,
                execution_mode, regions_path, bubbles_path, boxes_merged_path,
                region_count, bubble_count, confidence_summary_json, warnings_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangadetect"),
                run_id,
                project["id"],
                project_slug,
                adapter.adapter_id,
                adapter.adapter_version,
                adapter.execution_mode,
                rel_regions,
                rel_bubbles,
                rel_merged,
                len(detected_regions),
                len(bubble_regions),
                json_dumps(confidence),
                json_dumps(warnings),
                now,
                now,
            ),
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
            "execution_mode": adapter.execution_mode,
            "cloud_used": adapter.execution_mode == "cloud",
            "regions_path": rel_regions,
            "bubbles_path": rel_bubbles,
            "boxes_merged_path": rel_merged,
            "detection_summary_path": rel_summary,
            "regions_detected": len(detected_regions),
            "bubble_regions": len(bubble_regions),
            "adapter_boxes_inserted": inserted_count,
            "adapter_boxes_skipped_existing": skipped_existing_count,
            "confidence_summary": confidence,
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


def list_manga_boxes(
    workspace: Workspace,
    *,
    project_slug: str,
    page_index: int | None = None,
) -> list[dict[str, Any]]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        rows = _current_boxes_for_project(conn, project_id=project["id"])
    boxes = [
        {
            "page_id": row["page_id"],
            "page_index": row["page_index"],
            "box_id": row["stable_key"],
            "internal_box_id": row["internal_box_id"],
            "version_id": row["version_id"],
            "revision_no": row["revision_no"],
            "region_type": "dialogue" if row["box_type"] == "speech" else row["box_type"],
            "bbox": row["bbox_json"],
            "polygon": row["polygon_json"],
            "reading_order": row["reading_order"],
            "speaker_id": row["speaker_id"],
            "source": row.get("origin") or "imported",
        }
        for row in rows
        if row.get("stable_key") is not None
    ]
    if page_index is not None:
        boxes = [box for box in boxes if box["page_index"] == page_index]
    return boxes


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
    _validate_bbox(box.get("bbox"), box_label=f"Box {box.get('box_id')}")
    if not box.get("box_type"):
        raise ValueError(f"Box {box.get('box_id')} requires box_type.")
    box_type = str(box["box_type"])
    if box_type not in MANGA_REGION_TYPES and box_type != "speech":
        raise ValueError(
            f"Box {box.get('box_id')} has invalid box_type: {box_type}. "
            f"Expected one of {sorted(MANGA_REGION_TYPES)}."
        )
    _validate_polygon(box.get("polygon"), box_label=f"Box {box.get('box_id')}")


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
               v.box_type, v.reading_order, v.speaker_id, v.origin
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
