from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import struct
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from nts_core.dictionary import build_dictionary_prompt_support
from nts_core.eval_harness import chat_completion_with_provider_retry, classify_provider_error
from nts_core.hybrid_prompt import build_hybrid_prompt_support
from nts_core.model_test import log_mock_model_run
from nts_core.production_rollout import write_provider_preflight
from nts_core.production_translation import build_rollout_model_policy, load_production_provider
from nts_core.projects import create_project, get_project_by_slug
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
MANGA_OCR_SCHEMA_VERSION = "phase9d.ocr_manifest.v1"
MANGA_READING_ORDER_SCHEMA_VERSION = "phase9e.reading_order.v1"
MANGA_PAGE_CONTEXT_SCHEMA_VERSION = "phase9e.page_context_bundle.v1"
MANGA_TRANSLATION_SCHEMA_VERSION = "phase9f.translation.v1"
MANGA_TRANSLATION_CONTEXT_SCHEMA_VERSION = "phase9f.translation_context_bundle.v1"
MANGA_TRANSLATION_QA_SCHEMA_VERSION = "phase9f.translation_qa.v1"
MANGA_CLEANING_SCHEMA_VERSION = "phase9g.cleaning.v1"
MANGA_RENDERING_SCHEMA_VERSION = "phase9h.rendering.v1"
MANGA_RENDERING_RENDERER_VERSION = "phase9h.pillow_renderer.v1"
MANGA_VISUAL_QA_SCHEMA_VERSION = "phase9i.visual_qa.v1"
MANGA_EXPORT_SCHEMA_VERSION = "phase9j.export.v1"
MANGA_CLEANING_MODES = {"mask", "fill", "opencv_inpaint", "quality_inpaint"}
MANGA_SFX_POLICIES = {"leave_unchanged", "translate_as_note", "clean"}
MANGA_CLEANING_REGION_TYPES = {
    "plain_white_bubble",
    "textured_bubble",
    "caption_box",
    "background_text",
    "title_art",
    "sfx",
    "unknown_art",
}
MANGA_RENDERING_ALIGNMENTS = {"left", "center", "right"}
MANGA_RENDERING_DIRECTIONS = {"horizontal", "vertical"}
MANGA_VISUAL_QA_REVIEW_STATUSES = {"open", "accepted", "resolved"}
MANGA_EXPORT_PDF_ADAPTERS = {"pillow"}
MANGA_RESIDUAL_EDGE_RATIO_LIMIT = 0.18
MANGA_PAGE_MASK_AREA_RATIO_LIMIT = 0.12
MANGA_BOX_GLYPH_AREA_RATIO_LIMIT = 0.35
MANGA_MIN_LEGIBLE_FONT_SIZE = 10
MANGA_HASH_ALGORITHM = "sha256"
MANGA_PREPROCESS_NORMALIZED_FORMAT = "png"
MANGA_PREPROCESS_MAX_DIMENSION = 2400
MANGA_PREVIEW_MAX_DIMENSION = 320
MANGA_THRESHOLD_VALUE = 180
MANGA_REGION_TYPES = {"dialogue", "caption", "narration", "sfx", "sign", "note", "unknown"}
MANGA_DETECTION_REVIEW_STATE = "needs_review"
MANGA_OCR_REVIEW_STATES = {"pending", "approved", "corrected", "ignored", "not_translatable"}
MANGA_OCR_TEXT_PREVIEW_CHARS = 80
MANGA_OCR_VARIANTS = {"auto", "normalized", "grayscale", "threshold"}
PADDLEOCR_MISSING_MESSAGE = (
    "PaddleOCR chưa được cài. Chạy nts manga ocr bootstrap --engine paddleocr "
    "hoặc cài extra OCR."
)
PADDLEOCR_ADAPTER_VERSION = "phase9d1.paddleocr.v1"
PADDLEOCR_REAL_SMOKE_SCHEMA_VERSION = "phase9d2.paddleocr_real_smoke.v1"
PADDLEOCR_RUNTIME_MATRIX_SCHEMA_VERSION = "phase9d2.ocr_runtime_matrix.v1"
PADDLEOCR_ONEDNN_PIR_ERROR_FRAGMENT = "ConvertPirAttribute2RuntimeAttribute not support"
MANGA_READING_DIRECTIONS = {"right-to-left", "left-to-right", "top-to-bottom", "manual"}
MANGA_READING_ORDER_ALGORITHM_VERSION = "phase9e.coordinates.v1"
MANGA_CONTEXT_EXCLUDED_REVIEW_STATES = {"ignored", "not_translatable"}
MANGA_TRANSLATION_PROVIDER_MODES = {"mock", "gui_saved"}
PHASE9L_CANARY_SCHEMA_VERSION = "phase9l.real_manga_canary.v1"
PHASE9L5_DETECTOR_SCHEMA_VERSION = "phase9l5.detector_hardening.v1"
PHASE9M_PRODUCTION_SCHEMA_VERSION = "phase9m.production_rollout.v1"
PHASE9M_PROGRESS_SCHEMA_VERSION = "phase9m.production_progress.v1"
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
    recognized_text: str | None = None
    recognition_line_count: int | None = None


class DetectionAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    execution_mode: str
    provides_bubbles: bool

    def detect(self, *, preprocess_manifest: dict[str, Any]) -> list[DetectedRegion]:
        ...


@dataclass(frozen=True)
class OcrResult:
    page_id: str
    box_id: str
    text: str
    confidence: float
    language_detected: str
    orientation_detected: str
    raw_output: dict[str, Any]
    review_state: str = "pending"


class OcrAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    execution_mode: str

    def recognize(self, *, boxes: list[dict[str, Any]]) -> list[OcrResult]:
        ...


@dataclass(frozen=True)
class CleaningJobResult:
    page_id: str
    box_ids: list[str]
    adapter_id: str
    mode: str
    mask_artifact: str
    input_image_artifact: str
    output_image_artifact: str | None
    warnings: list[str]
    cloud_used: bool = False
    status: str = "success"


class CleaningAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    execution_mode: str

    def clean(
        self,
        *,
        workspace: Workspace,
        image_path: Path,
        mask_path: Path,
        output_path: Path,
        fill_color: tuple[int, int, int],
    ) -> tuple[Path | None, list[str], str]:
        ...


@dataclass(frozen=True)
class RenderFit:
    lines: list[str]
    font_size: int
    line_height_px: int
    line_count: int
    overflow: bool
    overflow_reason: str | None


class RendererAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    execution_mode: str

    def render_page(
        self,
        *,
        workspace: Workspace,
        cleaned_image_path: Path,
        output_path: Path,
        page_records: list[dict[str, Any]],
        box_lookup: dict[str, dict[str, Any]],
        default_settings: dict[str, Any],
    ) -> tuple[Path, list[dict[str, Any]]]:
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


class OpenCvTextDetectionAdapter:
    adapter_id = "opencv_text"
    adapter_version = "phase9l5.opencv_text.v1"
    execution_mode = "local"
    provides_bubbles = False

    def __init__(self, *, workspace: Workspace) -> None:
        self.workspace = workspace
        self.diagnostics: list[dict[str, Any]] = []

    @staticmethod
    def _load_runtime() -> tuple[Any, Any]:
        try:
            cv2 = importlib.import_module("cv2")
            np = importlib.import_module("numpy")
        except Exception as exc:
            raise ValueError(
                "OpenCV detector is not installed. Install the OCR extra with "
                "`uv sync --extra ocr`."
            ) from exc
        return cv2, np

    @staticmethod
    def _component_rectangles(cv2: Any, binary: Any) -> list[tuple[int, int, int, int]]:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
        height, width = binary.shape[:2]
        page_area = width * height
        components: list[tuple[int, int, int, int]] = []
        for label in range(1, count):
            x, y, box_width, box_height, area = [int(value) for value in stats[label]]
            if area < max(8, page_area * 0.000002):
                continue
            if box_width < 2 or box_height < max(5, round(height * 0.003)):
                continue
            if box_width > width * 0.12 or box_height > height * 0.075:
                continue
            if area > page_area * 0.006:
                continue
            aspect_ratio = box_width / max(1, box_height)
            fill_ratio = area / max(1, box_width * box_height)
            if not 0.08 <= aspect_ratio <= 6.0:
                continue
            if fill_ratio < 0.08:
                continue
            components.append((x, y, box_width, box_height))
        return components

    @staticmethod
    def _candidate_confidence(
        *,
        component_count: int,
        ink_density: float,
        white_ratio: float,
        area_ratio: float,
    ) -> float:
        component_score = min(1.0, component_count / 8.0)
        density_score = max(0.0, 1.0 - abs(ink_density - 0.18) / 0.28)
        background_score = min(1.0, max(0.0, (white_ratio - 0.25) / 0.65))
        size_score = min(1.0, max(0.0, area_ratio / 0.012))
        return round(
            min(
                0.98,
                0.42
                + component_score * 0.24
                + density_score * 0.14
                + background_score * 0.12
                + size_score * 0.08,
            ),
            6,
        )

    @staticmethod
    def _overlap_count(
        components: list[tuple[int, int, int, int]],
        candidate: tuple[int, int, int, int],
    ) -> int:
        x, y, width, height = candidate
        right = x + width
        bottom = y + height
        return sum(
            1
            for component_x, component_y, component_width, component_height in components
            if component_x < right
            and component_x + component_width > x
            and component_y < bottom
            and component_y + component_height > y
        )

    @staticmethod
    def _deduplicate(
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (float(item["confidence"]), int(item["component_count"])),
            reverse=True,
        ):
            x, y, width, height = candidate["bbox"]
            area = width * height
            duplicate = False
            for existing in selected:
                other_x, other_y, other_width, other_height = existing["bbox"]
                intersection_width = max(
                    0, min(x + width, other_x + other_width) - max(x, other_x)
                )
                intersection_height = max(
                    0, min(y + height, other_y + other_height) - max(y, other_y)
                )
                intersection = intersection_width * intersection_height
                other_area = other_width * other_height
                union = area + other_area - intersection
                if union and intersection / union >= 0.42:
                    duplicate = True
                    break
                if intersection >= min(area, other_area) * 0.72:
                    duplicate = True
                    break
            if not duplicate:
                selected.append(candidate)
        return sorted(selected[:40], key=lambda item: (item["bbox"][1], item["bbox"][0]))

    def detect(self, *, preprocess_manifest: dict[str, Any]) -> list[DetectedRegion]:
        cv2, np = self._load_runtime()
        regions: list[DetectedRegion] = []
        self.diagnostics = []
        for fallback_page_index, page in enumerate(preprocess_manifest["pages"], start=1):
            page_id = str(page["page_id"])
            page_index = int(page.get("page_index") or fallback_page_index)
            image_path = self.workspace.path / str(page["normalized_artifact"])
            image = cv2.imdecode(
                np.frombuffer(_read_binary_file(image_path), dtype=np.uint8),
                cv2.IMREAD_GRAYSCALE,
            )
            if image is None:
                raise ValueError(f"BLOCKED_DETECTION: OpenCV could not decode page {page_id}.")
            height, width = image.shape[:2]
            page_area = width * height
            blurred = cv2.GaussianBlur(image, (3, 3), 0)
            adaptive = cv2.adaptiveThreshold(
                blurred,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                31,
                15,
            )
            _threshold, otsu = cv2.threshold(
                blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU
            )
            binary = cv2.bitwise_or(adaptive, otsu)
            components = self._component_rectangles(cv2, binary)
            glyph_mask = np.zeros_like(binary)
            for x, y, box_width, box_height in components:
                cv2.rectangle(
                    glyph_mask,
                    (x, y),
                    (x + box_width, y + box_height),
                    255,
                    -1,
                )
            horizontal_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(7, width // 60), max(3, height // 500))
            )
            vertical_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (max(3, width // 500), max(7, height // 90))
            )
            horizontal = cv2.morphologyEx(
                glyph_mask, cv2.MORPH_CLOSE, horizontal_kernel, iterations=2
            )
            vertical = cv2.morphologyEx(
                glyph_mask, cv2.MORPH_CLOSE, vertical_kernel, iterations=2
            )
            grouped = cv2.bitwise_or(horizontal, vertical)
            contours, _hierarchy = cv2.findContours(
                grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            accepted: list[dict[str, Any]] = []
            rejected_reasons: dict[str, int] = {}
            for contour in contours:
                x, y, box_width, box_height = [
                    int(value) for value in cv2.boundingRect(contour)
                ]
                area_ratio = (box_width * box_height) / page_area
                reason: str | None = None
                if box_width < 12 or box_height < 10:
                    reason = "too_small"
                elif area_ratio < 0.00008:
                    reason = "area_too_small"
                elif area_ratio > 0.12:
                    reason = "area_too_large"
                elif box_width > width * 0.82 or box_height > height * 0.58:
                    reason = "span_too_large"
                component_count = self._overlap_count(
                    components, (x, y, box_width, box_height)
                )
                crop_binary = binary[y : y + box_height, x : x + box_width]
                crop_gray = image[y : y + box_height, x : x + box_width]
                ink_density = float(cv2.countNonZero(crop_binary)) / max(
                    1, box_width * box_height
                )
                white_ratio = float(np.mean(crop_gray >= 180))
                if reason is None and component_count < 3:
                    reason = "insufficient_components"
                elif reason is None and not 0.012 <= ink_density <= 0.52:
                    reason = "ink_density_outlier"
                elif reason is None and white_ratio < 0.58:
                    reason = "low_background_contrast"
                if reason is not None:
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                    continue
                padding_x = max(4, round(box_width * 0.08))
                padding_y = max(4, round(box_height * 0.12))
                left = max(0, x - padding_x)
                top = max(0, y - padding_y)
                right = min(width, x + box_width + padding_x)
                bottom = min(height, y + box_height + padding_y)
                confidence = self._candidate_confidence(
                    component_count=component_count,
                    ink_density=ink_density,
                    white_ratio=white_ratio,
                    area_ratio=area_ratio,
                )
                if confidence < 0.7:
                    rejected_reasons["low_confidence"] = (
                        rejected_reasons.get("low_confidence", 0) + 1
                    )
                    continue
                accepted.append(
                    {
                        "bbox": [left, top, right - left, bottom - top],
                        "confidence": confidence,
                        "component_count": component_count,
                        "ink_density": round(ink_density, 6),
                        "white_ratio": round(white_ratio, 6),
                        "area_ratio": round(area_ratio, 8),
                    }
                )
            selected = self._deduplicate(accepted)
            for candidate in selected:
                regions.append(
                    DetectedRegion(
                        page_id=page_id,
                        region_type="dialogue",
                        bbox=[float(value) for value in candidate["bbox"]],
                        polygon=None,
                        confidence=float(candidate["confidence"]),
                        orientation="unknown",
                        source="local_adapter",
                        adapter_id=self.adapter_id,
                    )
                )
            self.diagnostics.append(
                {
                    "page_id": page_id,
                    "page_index": page_index,
                    "width": width,
                    "height": height,
                    "component_count": len(components),
                    "grouped_candidate_count": len(contours),
                    "accepted_candidate_count": len(accepted),
                    "selected_box_count": len(selected),
                    "rejected_reasons": rejected_reasons,
                    "selected": selected,
                }
            )
        return regions


class MockOcrAdapter:
    adapter_id = "mock_local_ocr"
    adapter_version = "phase9d.v1"
    execution_mode = "local"

    def recognize(self, *, boxes: list[dict[str, Any]]) -> list[OcrResult]:
        results: list[OcrResult] = []
        for index, box in enumerate(boxes, start=1):
            region_type = str(box.get("region_type") or "unknown")
            if region_type == "sfx":
                text = f"mock sfx {index}"
                confidence = 0.63
                orientation = "stylized"
            else:
                text = f"mock text {index}"
                confidence = 0.92
                orientation = str(box.get("orientation") or "horizontal")
            results.append(
                OcrResult(
                    page_id=str(box["page_id"]),
                    box_id=str(box["box_id"]),
                    text=text,
                    confidence=confidence,
                    language_detected="ja",
                    orientation_detected=orientation,
                    raw_output={
                        "adapter_id": self.adapter_id,
                        "box_id": box["box_id"],
                        "text_preview": text[:MANGA_OCR_TEXT_PREVIEW_CHARS],
                        "confidence": confidence,
                    },
                )
            )
        return results


def _module_version(distribution_name: str) -> str | None:
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _import_module_status(module_name: str, distribution_name: str | None = None) -> dict[str, Any]:
    distribution = distribution_name or module_name
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return {
            "module": module_name,
            "available": False,
            "version": _module_version(distribution),
            "error": str(exc),
        }
    version = getattr(module, "__version__", None) or _module_version(distribution)
    return {"module": module_name, "available": True, "version": version, "error": None}


def _default_ocr_model_cache_dir(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "manga_ocr_bootstrap" / "model_cache" / "paddleocr"


def _paddlex_default_model_cache_dir() -> Path:
    return Path.home() / ".paddlex" / "official_models"


def _configure_paddleocr_cache(cache_dir: Path | str | None) -> None:
    if cache_dir is None:
        return
    resolved = Path(str(cache_dir)).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(resolved))


def _paddle_runtime_env_snapshot() -> dict[str, str | None]:
    keys = [
        "FLAGS_use_mkldnn",
        "FLAGS_use_onednn",
        "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK",
        "PADDLE_PDX_CACHE_HOME",
        "PADDLE_PDX_MODEL_SOURCE",
    ]
    return {key: os.environ.get(key) for key in keys}


def _apply_paddleocr_runtime_flags(
    *,
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool = False,
    no_network: bool = False,
    disable_model_source_check: bool = False,
) -> dict[str, Any]:
    applied: dict[str, str] = {}
    if disable_onednn:
        for key in ["FLAGS_use_mkldnn", "FLAGS_use_onednn"]:
            os.environ[key] = "0"
            applied[key] = "0"
    if disable_paddlex_mkldnn:
        os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
        applied["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
    if no_network or disable_model_source_check:
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        applied["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    return {
        "applied": applied,
        "snapshot_after_apply": _paddle_runtime_env_snapshot(),
    }


def _default_disable_paddlex_mkldnn_for_cpu() -> bool:
    return platform.system() == "Windows"


def _classify_paddleocr_runtime_error(error: str | None) -> str | None:
    if not error:
        return None
    if PADDLEOCR_MISSING_MESSAGE in error or "No module named 'paddleocr'" in error:
        return "missing_dependency"
    if PADDLEOCR_ONEDNN_PIR_ERROR_FRAGMENT in error:
        return "paddle_onednn_pir_array_attribute"
    if "download" in error.lower() or "model hoster" in error.lower() or "network" in error.lower():
        return "model_download_or_network"
    return "runtime_error"


def _validate_ocr_variant(variant: str) -> str:
    if variant not in MANGA_OCR_VARIANTS:
        raise ValueError(f"Invalid OCR variant: {variant}. Expected one of {sorted(MANGA_OCR_VARIANTS)}.")
    return variant


def _paddleocr_config(adapter_config: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(adapter_config or {})
    language = str(config.get("language") or config.get("lang") or "ch")
    device = str(config.get("device") or "cpu")
    default_disable_paddlex_mkldnn = device == "cpu" and _default_disable_paddlex_mkldnn_for_cpu()
    default_disable_model_source_check = default_disable_paddlex_mkldnn
    use_textline_orientation = bool(config.get("use_textline_orientation", config.get("use_angle_cls", False)))
    normalized = {
        "language": language,
        "lang": language,
        "device": device,
        "use_textline_orientation": use_textline_orientation,
        "use_angle_cls": use_textline_orientation,
        "use_doc_orientation_classify": bool(config.get("use_doc_orientation_classify", False)),
        "use_doc_unwarping": bool(config.get("use_doc_unwarping", False)),
        "engine": str(config.get("engine") or "paddle"),
        "ocr_variant": _validate_ocr_variant(str(config.get("ocr_variant") or "auto")),
        "no_network": bool(config.get("no_network", False)),
        "disable_model_source_check": bool(
            config.get("disable_model_source_check", default_disable_model_source_check)
        ),
        "disable_onednn": bool(config.get("disable_onednn", False)),
        "disable_paddlex_mkldnn": bool(
            config.get("disable_paddlex_mkldnn", default_disable_paddlex_mkldnn)
        ),
        "bootstrap_if_missing": bool(config.get("bootstrap_if_missing", False)),
        "cache_dir": str(config.get("cache_dir") or "") or None,
        "model_dir": str(config.get("model_dir") or "") or None,
        "text_detection_model_dir": str(config.get("text_detection_model_dir") or "") or None,
        "text_recognition_model_dir": str(config.get("text_recognition_model_dir") or "") or None,
        "textline_orientation_model_dir": str(config.get("textline_orientation_model_dir") or "") or None,
        "text_detection_model_name": str(config.get("text_detection_model_name") or "") or None,
        "text_recognition_model_name": str(config.get("text_recognition_model_name") or "") or None,
    }
    if normalized["model_dir"]:
        model_dir = str(normalized["model_dir"])
        normalized["text_detection_model_dir"] = normalized["text_detection_model_dir"] or str(
            Path(model_dir) / "det"
        )
        normalized["text_recognition_model_dir"] = normalized["text_recognition_model_dir"] or str(
            Path(model_dir) / "rec"
        )
        normalized["textline_orientation_model_dir"] = normalized["textline_orientation_model_dir"] or str(
            Path(model_dir) / "cls"
        )
    return normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _flatten_paddleocr_lines(raw_result: Any) -> list[Any]:
    if raw_result is None:
        return []
    if isinstance(raw_result, dict):
        payload = raw_result.get("res") if isinstance(raw_result.get("res"), dict) else raw_result
        texts = payload.get("rec_texts")
        scores = payload.get("rec_scores")
        polys = payload.get("rec_polys")
        if polys is None:
            polys = payload.get("dt_polys")
        if polys is None:
            polys = payload.get("rec_boxes")
        if isinstance(texts, list):
            lines = []
            for index, text in enumerate(texts):
                score = 0.0
                if isinstance(scores, list) and index < len(scores):
                    score = scores[index]
                elif hasattr(scores, "__len__") and index < len(scores):
                    score = scores[index]
                poly = None
                if isinstance(polys, list) and index < len(polys):
                    poly = polys[index]
                elif hasattr(polys, "__len__") and index < len(polys):
                    poly = polys[index]
                lines.append([poly, (text, score)])
            return lines
        if "rec_text" in payload:
            return [[None, (payload.get("rec_text") or "", payload.get("rec_score") or 0.0)]]
        return []
    if isinstance(raw_result, tuple):
        raw_result = list(raw_result)
    if isinstance(raw_result, list):
        if len(raw_result) == 2 and isinstance(raw_result[0], str):
            return [[None, (raw_result[0], raw_result[1])]]
        if len(raw_result) == 2 and isinstance(raw_result[1], (int, float)):
            return [[None, (str(raw_result[0]), raw_result[1])]]
        if raw_result and all(
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[1], (list, tuple))
            and len(item[1]) >= 2
            for item in raw_result
        ):
            return raw_result
        flattened: list[Any] = []
        for item in raw_result:
            flattened.extend(_flatten_paddleocr_lines(item))
        return flattened
    return []


def _extract_paddleocr_text(raw_result: Any) -> tuple[str, float]:
    lines = _flatten_paddleocr_lines(raw_result)
    texts: list[str] = []
    scores: list[float] = []
    for line in lines:
        try:
            text = str(line[1][0] or "")
            score = float(line[1][1] or 0.0)
        except Exception:
            continue
        if text:
            texts.append(text)
        scores.append(score)
    if not texts:
        return "", 0.0
    confidence = sum(scores) / len(scores) if scores else 0.0
    return "\n".join(texts), max(0.0, min(1.0, float(confidence)))


class PaddleOcrAdapter:
    adapter_id = "paddleocr"
    adapter_version = PADDLEOCR_ADAPTER_VERSION
    execution_mode = "local"

    def __init__(
        self,
        *,
        workspace: Workspace,
        preprocess_manifest: dict[str, Any],
        ocr_dir: Path,
        adapter_config: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.preprocess_manifest = preprocess_manifest
        self.ocr_dir = ocr_dir
        self.config = _paddleocr_config(adapter_config)
        self.raw_dir = ocr_dir / "paddleocr_raw"
        self.crop_dir = self.raw_dir / "crops"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.crop_dir.mkdir(parents=True, exist_ok=True)
        self._engine: Any | None = None
        self._api_mode: str | None = None
        self._runtime_flags: dict[str, Any] = {
            "applied": {},
            "snapshot_after_apply": _paddle_runtime_env_snapshot(),
        }
        self._detection_ocr_cache: dict[str, dict[str, Any]] | None = None

    def _page_artifact(self, page_id: str, variant: str) -> Path:
        pages = {str(page["page_id"]): page for page in self.preprocess_manifest.get("pages", [])}
        page = pages.get(page_id)
        if page is None:
            raise ValueError(f"BLOCKED_OCR_SCHEMA: preprocess page not found for {page_id}.")
        selected = variant
        if selected == "auto":
            selected = "threshold"
        if selected == "normalized":
            relpath = page.get("normalized_artifact")
        else:
            relpath = (page.get("ocr_variant_artifacts") or {}).get(selected)
        if not relpath:
            relpath = page.get("normalized_artifact")
        path = self.workspace.path / str(relpath)
        if not os.path.exists(_windows_long_path(path)):
            raise ValueError(f"BLOCKED_OCR_SCHEMA: OCR source image missing: {relpath}")
        return path

    def _crop_box(self, box: dict[str, Any]) -> tuple[Path, Path]:
        Image, _ImageOps = _load_pillow()
        variant = str(self.config["ocr_variant"])
        source = self._page_artifact(str(box["page_id"]), variant)
        bbox = _validate_bbox(box.get("bbox"), box_label=f"OCR box {box.get('box_id')}")
        x, y, width, height = [int(round(value)) for value in bbox]
        with Image.open(_windows_long_path(source)) as image:
            crop = image.crop((x, y, x + max(1, width), y + max(1, height)))
            if crop.mode not in {"RGB", "L"}:
                crop = crop.convert("RGB")
            crop_path = self.crop_dir / f"{box['page_index']:04d}_{box['box_id']}_{variant}.png"
            _save_png(crop, crop_path, force=True)
        return source, crop_path

    def _load_detection_ocr_cache(self) -> dict[str, dict[str, Any]]:
        if self._detection_ocr_cache is not None:
            return self._detection_ocr_cache
        project_slug = str(self.preprocess_manifest["project_slug"])
        run_id = str(self.preprocess_manifest["run_id"])
        regions_path = (
            _artifact_root_for_run(
                self.workspace,
                project_slug=project_slug,
                run_id=run_id,
            )
            / "detection"
            / "regions.json"
        )
        cache: dict[str, dict[str, Any]] = {}
        if regions_path.exists():
            try:
                payload = json.loads(regions_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            adapter_id = str((payload.get("adapter") or {}).get("adapter_id") or "")
            if adapter_id == PaddleOcrTextDetectionAdapter.adapter_id:
                for region in payload.get("regions") or []:
                    box_id = str(region.get("box_id") or "")
                    text = str(region.get("recognized_text") or "").strip()
                    if box_id and text:
                        cache[box_id] = region
        self._detection_ocr_cache = cache
        return cache

    def _recognize_from_detection_cache(
        self,
        box: dict[str, Any],
    ) -> OcrResult | None:
        cached = self._load_detection_ocr_cache().get(str(box["box_id"]))
        if cached is None:
            return None
        source_path, crop_path = self._crop_box(box)
        text = str(cached["recognized_text"]).strip()
        confidence = max(
            0.0,
            min(1.0, float(cached.get("confidence") or 0.0)),
        )
        return OcrResult(
            page_id=str(box["page_id"]),
            box_id=str(box["box_id"]),
            text=text,
            confidence=confidence,
            language_detected=str(self.config["language"]),
            orientation_detected="unknown",
            raw_output={
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "api_mode": "paddleocr_3_detection_result_reuse",
                "box_id": box["box_id"],
                "source_image_path": _relative_to_workspace(
                    self.workspace, source_path
                ),
                "cropped_image_path": _relative_to_workspace(
                    self.workspace, crop_path
                ),
                "language": self.config["language"],
                "device": self.config["device"],
                "ocr_variant": self.config["ocr_variant"],
                "runtime_flags": self._runtime_flags,
                "text_preview": _truncate_text(text),
                "confidence": confidence,
                "recognition_line_count": cached.get("recognition_line_count"),
                "recognition_source_adapter": PaddleOcrTextDetectionAdapter.adapter_id,
                "raw_result": {
                    "reused_real_paddleocr_detection_result": True,
                },
            },
        )

    def _load_engine(self):
        if self._engine is not None:
            return self._engine
        _configure_paddleocr_cache(self.config.get("cache_dir"))
        if self.config.get("no_network"):
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            has_model_dirs = bool(
                self.config.get("text_detection_model_dir")
                and self.config.get("text_recognition_model_dir")
            )
            cache_value = self.config.get("cache_dir")
            cache_path = Path(str(cache_value)) if cache_value else None
            cache_has_files = (
                cache_path is not None
                and cache_path.exists()
                and _scan_model_cache(cache_path)["file_count"] > 0
            )
            fallback_has_files = _scan_model_cache(_paddlex_default_model_cache_dir(), create=False)["file_count"] > 0
            if not has_model_dirs and not cache_has_files and not fallback_has_files:
                raise ValueError(
                    "PaddleOCR --no-network requires --model-dir, explicit local model dirs, "
                    "or a prewarmed PaddleOCR model cache."
                )
        self._runtime_flags = _apply_paddleocr_runtime_flags(
            disable_onednn=bool(self.config.get("disable_onednn")),
            disable_paddlex_mkldnn=bool(self.config.get("disable_paddlex_mkldnn")),
            no_network=bool(self.config.get("no_network")),
            disable_model_source_check=bool(self.config.get("disable_model_source_check")),
        )
        status = _import_module_status("paddleocr")
        if not status["available"]:
            raise ValueError(PADDLEOCR_MISSING_MESSAGE)
        module = importlib.import_module("paddleocr")
        paddle_ocr_class = getattr(module, "PaddleOCR")
        init_kwargs: dict[str, Any] = {
            "lang": self.config["language"],
            "engine": self.config["engine"],
            "device": self.config["device"],
            "use_doc_orientation_classify": self.config["use_doc_orientation_classify"],
            "use_doc_unwarping": self.config["use_doc_unwarping"],
            "use_textline_orientation": self.config["use_textline_orientation"],
        }
        if self.config.get("disable_paddlex_mkldnn"):
            init_kwargs["enable_mkldnn"] = False
        for key in [
            "text_detection_model_dir",
            "text_recognition_model_dir",
            "textline_orientation_model_dir",
            "text_detection_model_name",
            "text_recognition_model_name",
        ]:
            if self.config.get(key):
                init_kwargs[key] = self.config[key]
        try:
            self._engine = paddle_ocr_class(**init_kwargs)
        except TypeError:
            legacy_kwargs = {
                "lang": self.config["language"],
                "use_angle_cls": self.config["use_angle_cls"],
                "use_gpu": str(self.config["device"]).startswith("gpu"),
            }
            if self.config.get("disable_paddlex_mkldnn"):
                legacy_kwargs["enable_mkldnn"] = False
            for old_key, new_key in [
                ("det_model_dir", "text_detection_model_dir"),
                ("rec_model_dir", "text_recognition_model_dir"),
                ("cls_model_dir", "textline_orientation_model_dir"),
            ]:
                if self.config.get(new_key):
                    legacy_kwargs[old_key] = self.config[new_key]
            self._engine = paddle_ocr_class(**legacy_kwargs)
        return self._engine

    def _predict(self, image_path: Path) -> tuple[Any, str]:
        engine = self._load_engine()
        inference_path, temp_dir = _paddleocr_input_path(image_path)
        try:
            if hasattr(engine, "predict"):
                self._api_mode = "paddleocr_3_predict"
                return engine.predict(str(inference_path)), self._api_mode
            if hasattr(engine, "ocr"):
                self._api_mode = "paddleocr_2_ocr"
                return engine.ocr(str(inference_path), cls=bool(self.config["use_angle_cls"])), self._api_mode
            raise ValueError("PaddleOCR object does not expose predict() or ocr().")
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def recognize(self, *, boxes: list[dict[str, Any]]) -> list[OcrResult]:
        results: list[OcrResult] = []
        for box in boxes:
            cached_result = self._recognize_from_detection_cache(box)
            if cached_result is not None:
                results.append(cached_result)
                continue
            source_path, crop_path = self._crop_box(box)
            raw_result, api_mode = self._predict(crop_path)
            text, confidence = _extract_paddleocr_text(raw_result)
            raw_output = {
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "api_mode": api_mode,
                "box_id": box["box_id"],
                "source_image_path": _relative_to_workspace(self.workspace, source_path),
                "cropped_image_path": _relative_to_workspace(self.workspace, crop_path),
                "language": self.config["language"],
                "device": self.config["device"],
                "ocr_variant": self.config["ocr_variant"],
                "runtime_flags": self._runtime_flags,
                "text_preview": _truncate_text(text),
                "confidence": confidence,
                "raw_result": _json_safe(raw_result),
            }
            results.append(
                OcrResult(
                    page_id=str(box["page_id"]),
                    box_id=str(box["box_id"]),
                    text=text,
                    confidence=confidence,
                    language_detected=str(self.config["language"]),
                    orientation_detected="textline_orientation"
                    if self.config["use_textline_orientation"]
                    else "unknown",
                    raw_output=raw_output,
                )
            )
        return results


def _paddleocr_line_detection_payloads(raw_result: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in _flatten_paddleocr_lines(raw_result):
        try:
            polygon = line[0]
            text = str(line[1][0] or "").strip()
            confidence = max(0.0, min(1.0, float(line[1][1] or 0.0)))
        except Exception:
            continue
        if not text or confidence < 0.65 or polygon is None:
            continue
        if hasattr(polygon, "tolist"):
            polygon = polygon.tolist()
        points: list[list[float]]
        if (
            isinstance(polygon, (list, tuple))
            and len(polygon) == 4
            and all(isinstance(value, (int, float)) for value in polygon)
        ):
            left, top, right, bottom = [float(value) for value in polygon]
            points = [[left, top], [right, top], [right, bottom], [left, bottom]]
        elif isinstance(polygon, (list, tuple)):
            try:
                points = [
                    [float(point[0]), float(point[1])]
                    for point in polygon
                    if isinstance(point, (list, tuple)) and len(point) >= 2
                ]
            except (TypeError, ValueError):
                continue
        else:
            continue
        if len(points) < 2:
            continue
        left = min(point[0] for point in points)
        top = min(point[1] for point in points)
        right = max(point[0] for point in points)
        bottom = max(point[1] for point in points)
        if right <= left or bottom <= top:
            continue
        payloads.append(
            {
                "bbox": [left, top, right - left, bottom - top],
                "confidence": confidence,
                "line_count": 1,
                "recognized_text": text,
            }
        )
    return payloads


def _merge_paddleocr_line_boxes(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for line in sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        x, y, width, height = [float(value) for value in line["bbox"]]
        best_group: dict[str, Any] | None = None
        best_gap: float | None = None
        for group in groups:
            gx, gy, group_width, group_height = [
                float(value) for value in group["bbox"]
            ]
            overlap = max(0.0, min(x + width, gx + group_width) - max(x, gx))
            overlap_ratio = overlap / max(1.0, min(width, group_width))
            vertical_gap = max(0.0, y - (gy + group_height), gy - (y + height))
            if overlap_ratio < 0.3:
                continue
            if vertical_gap > max(height, group_height / max(1, group["line_count"])) * 1.7:
                continue
            if best_gap is None or vertical_gap < best_gap:
                best_group = group
                best_gap = vertical_gap
        if best_group is None:
            groups.append(dict(line))
            continue
        gx, gy, group_width, group_height = [
            float(value) for value in best_group["bbox"]
        ]
        right = max(x + width, gx + group_width)
        bottom = max(y + height, gy + group_height)
        left = min(x, gx)
        top = min(y, gy)
        old_count = int(best_group["line_count"])
        best_group["bbox"] = [left, top, right - left, bottom - top]
        best_group["confidence"] = round(
            (
                float(best_group["confidence"]) * old_count
                + float(line["confidence"])
            )
            / (old_count + 1),
            6,
        )
        best_group["line_count"] = old_count + 1
        best_group["recognized_text"] = "\n".join(
            part
            for part in [
                str(best_group.get("recognized_text") or "").strip(),
                str(line.get("recognized_text") or "").strip(),
            ]
            if part
        )
    return groups


def _filter_paddleocr_detection_groups(
    groups: list[dict[str, Any]],
    *,
    page_width: int,
    page_height: int,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    oversized_sparse_count = 0
    page_area = max(1.0, float(page_width * page_height))
    for group in groups:
        _x, _y, group_width, group_height = [
            float(value) for value in group["bbox"]
        ]
        area_ratio = (group_width * group_height) / page_area
        if area_ratio > 0.25 and int(group["line_count"]) < 8:
            oversized_sparse_count += 1
            continue
        filtered.append(group)
    return filtered, oversized_sparse_count


def _filter_nested_paddleocr_detection_groups(
    groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    retained: list[dict[str, Any]] = []
    nested_count = 0
    for index, candidate in enumerate(groups):
        x, y, width, height = [float(value) for value in candidate["bbox"]]
        candidate_area = max(1.0, width * height)
        nested = False
        for other_index, other in enumerate(groups):
            if index == other_index:
                continue
            ox, oy, other_width, other_height = [
                float(value) for value in other["bbox"]
            ]
            other_area = max(1.0, other_width * other_height)
            if candidate_area > other_area * 0.20:
                continue
            overlap_width = max(
                0.0,
                min(x + width, ox + other_width) - max(x, ox),
            )
            overlap_height = max(
                0.0,
                min(y + height, oy + other_height) - max(y, oy),
            )
            if (overlap_width * overlap_height) / candidate_area >= 0.90:
                nested = True
                break
        if nested:
            nested_count += 1
        else:
            retained.append(candidate)
    return retained, nested_count


def _filter_tiny_paddleocr_detection_groups(
    groups: list[dict[str, Any]],
    *,
    page_width: int,
    page_height: int,
) -> tuple[list[dict[str, Any]], int]:
    retained: list[dict[str, Any]] = []
    tiny_count = 0
    min_width = max(32.0, float(page_width) * 0.045)
    min_height = max(20.0, float(page_height) * 0.02)
    for candidate in groups:
        _x, _y, width, height = [
            float(value) for value in candidate["bbox"]
        ]
        if width < min_width and height < min_height:
            tiny_count += 1
        else:
            retained.append(candidate)
    return retained, tiny_count


class PaddleOcrTextDetectionAdapter:
    adapter_id = "paddleocr_text_detector"
    adapter_version = "phase9l5.paddleocr_text_detector.v1"
    execution_mode = "local"
    provides_bubbles = False

    def __init__(
        self,
        *,
        workspace: Workspace,
        preprocess_manifest: dict[str, Any],
    ) -> None:
        self.workspace = workspace
        self.preprocess_manifest = preprocess_manifest
        project_slug = str(preprocess_manifest["project_slug"])
        run_id = str(preprocess_manifest["run_id"])
        runtime_dir = (
            _artifact_root_for_run(
                workspace, project_slug=project_slug, run_id=run_id
            )
            / "detection"
            / "paddleocr_runtime"
        )
        workspace_models = (
            _default_ocr_model_cache_dir(workspace) / "official_models"
        )
        default_models = (
            workspace_models
            if (workspace_models / "PP-OCRv5_server_det").exists()
            else _paddlex_default_model_cache_dir()
        )
        detection_model = default_models / "PP-OCRv5_server_det"
        recognition_model = default_models / "PP-OCRv5_server_rec"
        self.ocr = PaddleOcrAdapter(
            workspace=workspace,
            preprocess_manifest=preprocess_manifest,
            ocr_dir=runtime_dir,
            adapter_config={
                "language": "ch",
                "device": "cpu",
                "ocr_variant": "normalized",
                "cache_dir": None,
                "text_detection_model_dir": (
                    str(detection_model) if detection_model.exists() else None
                ),
                "text_recognition_model_dir": (
                    str(recognition_model) if recognition_model.exists() else None
                ),
                "no_network": True,
                "disable_onednn": True,
                "disable_paddlex_mkldnn": True,
                "disable_model_source_check": True,
            },
        )
        self.diagnostics: list[dict[str, Any]] = []
        self.checkpoint_path = runtime_dir / "recognition_checkpoint.json"
        self.shared_checkpoint_path = (
            workspace.path
            / "artifacts"
            / "manga_detector_cache"
            / self.adapter_id
            / "recognition_checkpoint.json"
        )
        self._checkpoint_profile = {
            "schema_version": PHASE9L5_DETECTOR_SCHEMA_VERSION,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "max_inference_dimension": 960,
        }
        self._checkpoint = self._load_checkpoint()

    def _load_checkpoint(self) -> dict[str, Any]:
        empty = {**self._checkpoint_profile, "pages": {}}
        for path in [self.shared_checkpoint_path, self.checkpoint_path]:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if any(
                payload.get(key) != expected
                for key, expected in self._checkpoint_profile.items()
            ):
                continue
            if isinstance(payload.get("pages"), dict):
                empty["pages"].update(payload["pages"])
        return empty

    def _save_checkpoint(self) -> None:
        for path in [self.checkpoint_path, self.shared_checkpoint_path]:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".json.tmp")
            temp_path.write_text(
                json_dumps(self._checkpoint) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, path)

    def _bounded_detection_input(
        self,
        image_path: Path,
        *,
        page_id: str,
        width: int,
        height: int,
    ) -> tuple[Path, float]:
        max_dimension = max(width, height)
        if max_dimension <= 960:
            return image_path, 1.0
        Image, _ImageOps = _load_pillow()
        scale = 960.0 / float(max_dimension)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        input_dir = self.ocr.ocr_dir / "detection_inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        resized_path = input_dir / f"{page_id}_max960.png"
        with Image.open(_windows_long_path(image_path)) as image:
            resized = image.convert("RGB").resize(
                (resized_width, resized_height),
                resample=Image.Resampling.LANCZOS,
            )
            _save_png(resized, resized_path, force=True)
        return resized_path, scale

    def detect(self, *, preprocess_manifest: dict[str, Any]) -> list[DetectedRegion]:
        regions: list[DetectedRegion] = []
        self.diagnostics = []
        for page_index, page in enumerate(preprocess_manifest["pages"], start=1):
            page_id = str(page["page_id"])
            image_path = self.workspace.path / str(page["normalized_artifact"])
            width = int(page["width"])
            height = int(page["height"])
            detection_input, scale = self._bounded_detection_input(
                image_path,
                page_id=page_id,
                width=width,
                height=height,
            )
            input_sha256 = _sha256_file_long_path(detection_input)
            cached_page = self._checkpoint["pages"].get(page_id)
            if not (
                isinstance(cached_page, dict)
                and cached_page.get("input_sha256") == input_sha256
            ):
                cached_page = next(
                    (
                        candidate
                        for candidate in self._checkpoint["pages"].values()
                        if isinstance(candidate, dict)
                        and candidate.get("input_sha256") == input_sha256
                    ),
                    None,
                )
            cache_hit = bool(
                isinstance(cached_page, dict)
                and cached_page.get("input_sha256") == input_sha256
                and int(cached_page.get("width") or 0) == width
                and int(cached_page.get("height") or 0) == height
            )
            if cache_hit:
                selected = list(cached_page.get("selected") or [])
                api_mode = str(cached_page.get("api_mode") or "checkpoint")
                grouped_candidate_count = int(
                    cached_page.get("grouped_candidate_count") or 0
                )
                rejected_reasons = dict(
                    cached_page.get("rejected_reasons") or {}
                )
                selected, checkpoint_sparse_count = (
                    _filter_paddleocr_detection_groups(
                        selected,
                        page_width=width,
                        page_height=height,
                    )
                )
                selected, checkpoint_nested_count = (
                    _filter_nested_paddleocr_detection_groups(selected)
                )
                selected, checkpoint_tiny_count = (
                    _filter_tiny_paddleocr_detection_groups(
                        selected,
                        page_width=width,
                        page_height=height,
                    )
                )
                rejected_reasons["oversized_sparse_region"] = int(
                    rejected_reasons.get("oversized_sparse_region") or 0
                ) + checkpoint_sparse_count
                rejected_reasons["nested_duplicate_region"] = int(
                    rejected_reasons.get("nested_duplicate_region") or 0
                ) + checkpoint_nested_count
                rejected_reasons["tiny_isolated_region"] = int(
                    rejected_reasons.get("tiny_isolated_region") or 0
                ) + checkpoint_tiny_count
                self._checkpoint["pages"][page_id] = {
                    **cached_page,
                    "page_index": page_index,
                    "width": width,
                    "height": height,
                }
            else:
                raw_result, api_mode = self.ocr._predict(detection_input)
                lines = _paddleocr_line_detection_payloads(raw_result)
                raw_line_count = len(_flatten_paddleocr_lines(raw_result))
                if scale != 1.0:
                    for line in lines:
                        line["bbox"] = [
                            float(value) / scale for value in line["bbox"]
                        ]
                groups = _merge_paddleocr_line_boxes(lines)
                filtered_groups, oversized_sparse_count = (
                    _filter_paddleocr_detection_groups(
                        groups,
                        page_width=width,
                        page_height=height,
                    )
                )
                filtered_groups, nested_duplicate_count = (
                    _filter_nested_paddleocr_detection_groups(filtered_groups)
                )
                filtered_groups, tiny_isolated_count = (
                    _filter_tiny_paddleocr_detection_groups(
                        filtered_groups,
                        page_width=width,
                        page_height=height,
                    )
                )
                selected = []
                for group in filtered_groups:
                    x, y, box_width, box_height = [
                        float(value) for value in group["bbox"]
                    ]
                    padding_x = max(4.0, box_width * 0.025)
                    padding_y = max(4.0, box_height * 0.05)
                    left = max(0.0, x - padding_x)
                    top = max(0.0, y - padding_y)
                    right = min(float(width), x + box_width + padding_x)
                    bottom = min(float(height), y + box_height + padding_y)
                    selected.append(
                        {
                            "bbox": [left, top, right - left, bottom - top],
                            "confidence": float(group["confidence"]),
                            "line_count": int(group["line_count"]),
                            "recognized_text": str(
                                group.get("recognized_text") or ""
                            ).strip(),
                        }
                    )
                grouped_candidate_count = len(lines)
                rejected_reasons = {
                    "empty_or_below_recognition_threshold": max(
                        0, raw_line_count - len(lines)
                    ),
                    "oversized_sparse_region": oversized_sparse_count,
                    "nested_duplicate_region": nested_duplicate_count,
                    "tiny_isolated_region": tiny_isolated_count,
                }
                self._checkpoint["pages"][page_id] = {
                    "page_index": page_index,
                    "width": width,
                    "height": height,
                    "input_sha256": input_sha256,
                    "inference_scale": round(scale, 6),
                    "api_mode": api_mode,
                    "grouped_candidate_count": grouped_candidate_count,
                    "rejected_reasons": rejected_reasons,
                    "selected": selected,
                }
                self._save_checkpoint()
            for payload in selected:
                regions.append(
                    DetectedRegion(
                        page_id=page_id,
                        region_type="dialogue",
                        bbox=payload["bbox"],
                        polygon=None,
                        confidence=payload["confidence"],
                        orientation="unknown",
                        source="local_adapter",
                        adapter_id=self.adapter_id,
                        recognized_text=payload["recognized_text"],
                        recognition_line_count=payload["line_count"],
                    )
                )
            self.diagnostics.append(
                {
                    "page_id": page_id,
                    "page_index": page_index,
                    "width": width,
                    "height": height,
                    "inference_width": max(1, int(round(width * scale))),
                    "inference_height": max(1, int(round(height * scale))),
                    "inference_scale": round(scale, 6),
                    "api_mode": api_mode,
                    "checkpoint_hit": cache_hit,
                    "component_count": None,
                    "grouped_candidate_count": grouped_candidate_count,
                    "accepted_candidate_count": len(selected),
                    "selected_box_count": len(selected),
                    "rejected_reasons": rejected_reasons,
                    "selected": selected,
                }
            )
        self._save_checkpoint()
        return regions


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)
    return cleaned or "page"


def _bounded_safe_filename(name: str, *, max_length: int = 96) -> str:
    safe = _safe_name(name)
    if len(safe) <= max_length:
        return safe
    suffix = Path(safe).suffix
    stem = safe[: -len(suffix)] if suffix else safe
    digest = _sha256_text(safe)[:10]
    suffix_budget = len(suffix)
    stem_budget = max(1, max_length - suffix_budget - len(digest) - 1)
    return f"{stem[:stem_budget]}_{digest}{suffix}"


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha256_file_long_path(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(_windows_long_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


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


def _detection_adapter(
    adapter_id: str,
    *,
    workspace: Workspace | None = None,
    preprocess_manifest: dict[str, Any] | None = None,
) -> DetectionAdapter:
    if adapter_id == MockDetectionAdapter.adapter_id or adapter_id == "mock":
        return MockDetectionAdapter()
    if adapter_id in {OpenCvTextDetectionAdapter.adapter_id, "opencv"}:
        if workspace is None:
            raise ValueError("BLOCKED_DETECTION: OpenCV detector requires workspace context.")
        return OpenCvTextDetectionAdapter(workspace=workspace)
    if adapter_id in {PaddleOcrTextDetectionAdapter.adapter_id, "paddleocr_detector"}:
        if workspace is None:
            raise ValueError("BLOCKED_DETECTION: PaddleOCR detector requires workspace context.")
        if not isinstance(preprocess_manifest, dict):
            raise ValueError(
                "BLOCKED_DETECTION: PaddleOCR detector requires preprocessing context."
            )
        return PaddleOcrTextDetectionAdapter(
            workspace=workspace,
            preprocess_manifest=preprocess_manifest,
        )
    raise ValueError(f"Unsupported detection adapter: {adapter_id}")


def _ocr_adapter(
    adapter_id: str,
    *,
    allow_cloud: bool = False,
    workspace: Workspace | None = None,
    preprocess_manifest: dict[str, Any] | None = None,
    ocr_dir: Path | None = None,
    adapter_config: dict[str, Any] | None = None,
) -> OcrAdapter:
    if adapter_id in {"mock", MockOcrAdapter.adapter_id}:
        return MockOcrAdapter()
    if adapter_id == "paddleocr":
        if workspace is None or preprocess_manifest is None or ocr_dir is None:
            raise ValueError("BLOCKED_OCR_SCHEMA: PaddleOCR adapter requires workspace OCR context.")
        return PaddleOcrAdapter(
            workspace=workspace,
            preprocess_manifest=preprocess_manifest,
            ocr_dir=ocr_dir,
            adapter_config=adapter_config,
        )
    if adapter_id in {"manga_ocr_plan", "paddleocr_plan"}:
        raise ValueError(
            f"BLOCKED_DEPENDENCY_APPROVAL: {adapter_id} is planned but not installed or approved."
        )
    if adapter_id.startswith("cloud_"):
        if not allow_cloud:
            raise ValueError("Cloud OCR adapters require explicit opt-in and are disabled by default.")
        raise ValueError("Cloud OCR adapter boundary exists, but no cloud OCR adapter is configured.")
    raise ValueError(f"Unsupported OCR adapter: {adapter_id}")


def list_ocr_adapters() -> dict[str, Any]:
    return {
        "implemented": [
            {
                "adapter_id": MockOcrAdapter.adapter_id,
                "adapter_version": MockOcrAdapter.adapter_version,
                "execution_mode": MockOcrAdapter.execution_mode,
                "status": "implemented",
                "network_required": False,
            },
            {
                "adapter_id": PaddleOcrAdapter.adapter_id,
                "adapter_version": PaddleOcrAdapter.adapter_version,
                "execution_mode": PaddleOcrAdapter.execution_mode,
                "status": "implemented_optional_dependency",
                "network_required": "first_run_model_download_unless_cache_prewarmed",
                "languages": ["ch", "japan", "korean", "en"],
                "install": "uv sync --extra ocr",
            }
        ],
        "planned": [
            {
                "adapter_id": "manga_ocr_plan",
                "preferred_for": "Japanese manga, vertical and horizontal text",
                "execution_mode": "local",
                "status": "planned_dependency_not_enabled",
                "dependency": "manga-ocr",
                "license_note": "Apache-2.0 research-preferred adapter; not added to core dependencies in Phase 9D.",
            },
            {
                "adapter_id": "paddleocr_plan",
                "preferred_for": "Chinese, Korean, English, mixed CJK, general image OCR",
                "execution_mode": "local",
                "status": "replaced_by_paddleocr_adapter",
                "dependency": "PaddleOCR",
                "license_note": "Apache-2.0 candidate; exact model/version must be locked later.",
            },
            {
                "adapter_id": "cloud_ocr_boundary",
                "preferred_for": "explicit opt-in cloud OCR experiments",
                "execution_mode": "cloud",
                "status": "boundary_only_not_enabled",
                "cloud_opt_in_required": True,
            },
        ],
    }


def _redact_adapter_config(config: dict[str, Any] | None) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in (config or {}).items():
        lowered = key.lower()
        if any(secret in lowered for secret in ["key", "token", "secret", "authorization", "password"]):
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _ocr_confidence_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"count": 0, "min": None, "max": None, "average": None, "low_confidence_count": 0}
    values = [float(result["confidence"]) for result in results]
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "average": round(sum(values) / len(values), 6),
        "low_confidence_count": len([value for value in values if value < 0.8]),
    }


def _truncate_text(value: str, max_chars: int = MANGA_OCR_TEXT_PREVIEW_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "..."


def _ocr_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    ocr_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    return ocr_dir


def _ensure_ocr_review_artifacts(ocr_dir: Path) -> tuple[Path, Path]:
    corrections_path = ocr_dir / "ocr_corrections.jsonl"
    candidates_path = ocr_dir / "memory_dictionary_candidates.jsonl"
    for path in [corrections_path, candidates_path]:
        if not path.exists():
            path.write_text("", encoding="utf-8")
    return corrections_path, candidates_path


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
                recognized_text=region.recognized_text,
                recognition_line_count=region.recognition_line_count,
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
    if region.recognized_text:
        payload["recognized_text"] = region.recognized_text
        payload["recognition_line_count"] = region.recognition_line_count
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
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(_windows_long_path(path), format="PNG", optimize=False)


def _windows_long_path(path: Path) -> str | Path:
    if os.name != "nt":
        return path
    raw = str(path.resolve())
    if raw.startswith("\\\\?\\"):
        return raw
    return "\\\\?\\" + raw


def _path_exists(path: Path) -> bool:
    return os.path.exists(_windows_long_path(path))


def _read_binary_file(path: Path) -> bytes:
    with open(_windows_long_path(path), "rb") as handle:
        return handle.read()


def _paddleocr_input_path(image_path: Path) -> tuple[Path, Path | None]:
    if os.name != "nt" or len(str(image_path.resolve())) < 240:
        return image_path, None
    temp_dir = Path(tempfile.mkdtemp(prefix="nts_paddleocr_"))
    suffix = image_path.suffix if image_path.suffix else ".png"
    temp_path = temp_dir / f"input{suffix}"
    with open(_windows_long_path(image_path), "rb") as source, open(temp_path, "wb") as dest:
        shutil.copyfileobj(source, dest)
    return temp_path, temp_dir


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
    import hashlib

    digest = hashlib.sha256()
    with open(_windows_long_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    return _read_binary_file(source.path), sha256_file(source.path)


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
    page_limit: int | None = None,
    page_start: int = 1,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    images, warnings, source_kind = _collect_images(path)
    total_source_pages = len(images)
    if page_start <= 0:
        raise ValueError("Manga import page_start must be greater than zero.")
    if page_start > 1:
        if page_start > len(images):
            raise ValueError(f"Manga import page_start {page_start} exceeds source page count {len(images)}.")
        warnings.append(f"page_start_applied:{page_start}:of:{len(images)}")
        images = images[page_start - 1 :]
    if page_limit is not None:
        if page_limit <= 0:
            raise ValueError("Manga import page_limit must be greater than zero.")
        if len(images) > page_limit:
            warnings.append(f"page_limit_applied:{page_limit}:from:{page_start}:of:{total_source_pages}")
            images = images[:page_limit]
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
                "page_limit": page_limit,
                "page_start": page_start,
                "total_source_pages": total_source_pages,
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
            dest_name = f"{page_index:04d}_{checksum[:12]}_{_bounded_safe_filename(source.name)}"
            dest_path = page_artifact_dir / dest_name
            if not _path_exists(dest_path):
                with open(_windows_long_path(dest_path), "wb") as handle:
                    handle.write(data)
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
            "total_source_pages": total_source_pages,
            "page_limit": page_limit,
            "page_start": page_start,
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
            "total_source_pages": total_source_pages,
            "page_limit": page_limit,
            "page_start": page_start,
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
            if not _path_exists(source_path):
                raise ValueError(
                    f"BLOCKED_MANIFEST_INCOMPLETE: source artifact missing for page {page_id}."
                )
            original_checksum = _image_checksum(source_path)
            with Image.open(_windows_long_path(source_path)) as source_image:
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

            if _image_checksum(source_path) != original_checksum:
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
                _read_binary_file(normalized_path)
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
               v.box_type, v.reading_order, v.speaker_id, v.origin,
               v.change_reason
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


def _write_detection_diagnostics(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    adapter: DetectionAdapter,
    preprocess_manifest: dict[str, Any],
    detected_regions: list[dict[str, Any]],
    detection_dir: Path,
) -> dict[str, str]:
    Image, _ImageOps = _load_pillow()
    try:
        from PIL import ImageDraw
    except Exception as exc:
        raise ValueError("BLOCKED_IMAGE_LIBRARY: Pillow ImageDraw is required.") from exc

    overlays_dir = detection_dir / "overlays"
    overlays_dir.mkdir(parents=True, exist_ok=True)
    pages_by_id = {
        str(page["page_id"]): (index, page)
        for index, page in enumerate(preprocess_manifest.get("pages", []), start=1)
    }
    adapter_diagnostics = getattr(adapter, "diagnostics", None)
    if not isinstance(adapter_diagnostics, list):
        adapter_diagnostics = []
    diagnostics_by_page = {
        str(item.get("page_id")): dict(item)
        for item in adapter_diagnostics
        if isinstance(item, dict) and item.get("page_id")
    }
    page_diagnostics: list[dict[str, Any]] = []
    quality_boxes: list[dict[str, Any]] = []
    for page_id, (fallback_page_index, page) in pages_by_id.items():
        page_index = int(page.get("page_index") or fallback_page_index)
        width = int(page["width"])
        height = int(page["height"])
        page_regions = [
            region for region in detected_regions if str(region["page_id"]) == page_id
        ]
        diagnostic = diagnostics_by_page.get(
            page_id,
            {
                "page_id": page_id,
                "page_index": page_index,
                "width": width,
                "height": height,
                "component_count": None,
                "grouped_candidate_count": len(page_regions),
                "accepted_candidate_count": len(page_regions),
                "selected_box_count": len(page_regions),
                "rejected_reasons": {},
                "selected": [],
            },
        )
        diagnostic["selected_box_ids"] = [region["box_id"] for region in page_regions]
        diagnostic.setdefault("ocr_empty_count", None)
        diagnostic.setdefault("ocr_zero_confidence_count", None)
        diagnostic.setdefault("ocr_missing_count", None)
        page_diagnostics.append(diagnostic)

        source_path = workspace.path / str(page["normalized_artifact"])
        with Image.open(_windows_long_path(source_path)) as source:
            overlay = source.convert("RGB")
        draw = ImageDraw.Draw(overlay)
        for region in page_regions:
            x, y, box_width, box_height = [float(value) for value in region["bbox"]]
            color = (22, 163, 74) if float(region["confidence"]) >= 0.8 else (220, 38, 38)
            draw.rectangle(
                (x, y, x + box_width, y + box_height),
                outline=color,
                width=max(2, round(min(width, height) / 500)),
            )
            draw.text(
                (x + 2, max(0, y - 12)),
                f"{region['box_id'][-8:]} {float(region['confidence']):.2f}",
                fill=color,
            )
            within_bounds = (
                x >= 0
                and y >= 0
                and box_width > 0
                and box_height > 0
                and x + box_width <= width
                and y + box_height <= height
            )
            quality_boxes.append(
                {
                    "page_id": page_id,
                    "page_index": page_index,
                    "box_id": region["box_id"],
                    "bbox": region["bbox"],
                    "confidence": region["confidence"],
                    "low_confidence": float(region["confidence"]) < 0.8,
                    "within_bounds": within_bounds,
                    "ocr_status": "not_run",
                }
            )
        overlay_path = overlays_dir / f"page_{page_index:04d}.png"
        overlay.save(_windows_long_path(overlay_path), format="PNG", optimize=False)
        diagnostic["overlay_path"] = _relative_to_workspace(workspace, overlay_path)

    diagnostics_path = detection_dir / "detection_diagnostics.json"
    diagnostics_md_path = detection_dir / "detection_diagnostics.md"
    quality_path = detection_dir / "box_quality_report.json"
    diagnostics_payload = {
        "schema_version": PHASE9L5_DETECTOR_SCHEMA_VERSION,
        "project_slug": project_slug,
        "run_id": run_id,
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "page_count": len(page_diagnostics),
        "selected_box_count": len(detected_regions),
        "pages": page_diagnostics,
    }
    quality_payload = {
        "schema_version": PHASE9L5_DETECTOR_SCHEMA_VERSION,
        "project_slug": project_slug,
        "run_id": run_id,
        "adapter_id": adapter.adapter_id,
        "box_count": len(quality_boxes),
        "low_confidence_count": len(
            [box for box in quality_boxes if box["low_confidence"]]
        ),
        "out_of_bounds_count": len(
            [box for box in quality_boxes if not box["within_bounds"]]
        ),
        "ocr_empty_count": None,
        "ocr_zero_confidence_count": None,
        "ocr_missing_count": None,
        "boxes": quality_boxes,
    }
    diagnostics_path.write_text(json_dumps(diagnostics_payload) + "\n", encoding="utf-8")
    quality_path.write_text(json_dumps(quality_payload) + "\n", encoding="utf-8")
    diagnostics_md_path.write_text(
        "\n".join(
            [
                "# Detection Diagnostics",
                "",
                f"- Adapter: `{adapter.adapter_id}`",
                f"- Pages: `{len(page_diagnostics)}`",
                f"- Selected boxes: `{len(detected_regions)}`",
                f"- Low-confidence boxes: `{quality_payload['low_confidence_count']}`",
                f"- Out-of-bounds boxes: `{quality_payload['out_of_bounds_count']}`",
                "- OCR-empty boxes: `not_run`",
                "- OCR-zero-confidence boxes: `not_run`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "detection_diagnostics_path": _relative_to_workspace(workspace, diagnostics_path),
        "detection_diagnostics_md_path": _relative_to_workspace(
            workspace, diagnostics_md_path
        ),
        "box_quality_report_path": _relative_to_workspace(workspace, quality_path),
        "detection_overlays_dir": _relative_to_workspace(workspace, overlays_dir),
    }


def _update_detection_ocr_diagnostics(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    ocr_results: list[dict[str, Any]],
) -> None:
    detection_dir = (
        _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
        / "detection"
    )
    diagnostics_path = detection_dir / "detection_diagnostics.json"
    diagnostics_md_path = detection_dir / "detection_diagnostics.md"
    quality_path = detection_dir / "box_quality_report.json"
    if not diagnostics_path.exists() or not quality_path.exists():
        return
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    ocr_by_box = {str(item["box_id"]): item for item in ocr_results}
    empty_count = 0
    zero_confidence_count = 0
    missing_count = 0
    page_counts: dict[str, dict[str, int]] = {}
    for box in quality.get("boxes", []):
        page_id = str(box["page_id"])
        counts = page_counts.setdefault(
            page_id, {"empty": 0, "zero_confidence": 0, "missing": 0}
        )
        ocr = ocr_by_box.get(str(box["box_id"]))
        if ocr is None:
            box["ocr_status"] = "missing"
            missing_count += 1
            counts["missing"] += 1
            continue
        text_empty = not str(ocr.get("text") or "").strip()
        zero_confidence = float(ocr.get("confidence") or 0.0) <= 0.0
        box["ocr_confidence"] = float(ocr.get("confidence") or 0.0)
        box["ocr_text_empty"] = text_empty
        box["ocr_status"] = (
            "empty_zero_confidence"
            if text_empty and zero_confidence
            else "empty"
            if text_empty
            else "zero_confidence"
            if zero_confidence
            else "recognized"
        )
        if text_empty:
            empty_count += 1
            counts["empty"] += 1
        if zero_confidence:
            zero_confidence_count += 1
            counts["zero_confidence"] += 1
    for page in diagnostics.get("pages", []):
        counts = page_counts.get(
            str(page["page_id"]), {"empty": 0, "zero_confidence": 0, "missing": 0}
        )
        page["ocr_empty_count"] = counts["empty"]
        page["ocr_zero_confidence_count"] = counts["zero_confidence"]
        page["ocr_missing_count"] = counts["missing"]
    quality["ocr_empty_count"] = empty_count
    quality["ocr_zero_confidence_count"] = zero_confidence_count
    quality["ocr_missing_count"] = missing_count
    diagnostics["ocr_empty_count"] = empty_count
    diagnostics["ocr_zero_confidence_count"] = zero_confidence_count
    diagnostics["ocr_missing_count"] = missing_count
    diagnostics_path.write_text(json_dumps(diagnostics) + "\n", encoding="utf-8")
    quality_path.write_text(json_dumps(quality) + "\n", encoding="utf-8")
    diagnostics_md_path.write_text(
        "\n".join(
            [
                "# Detection Diagnostics",
                "",
                f"- Adapter: `{diagnostics.get('adapter_id')}`",
                f"- Pages: `{diagnostics.get('page_count')}`",
                f"- Selected boxes: `{diagnostics.get('selected_box_count')}`",
                f"- Low-confidence boxes: `{quality.get('low_confidence_count')}`",
                f"- Out-of-bounds boxes: `{quality.get('out_of_bounds_count')}`",
                f"- OCR-empty boxes: `{empty_count}`",
                f"- OCR-zero-confidence boxes: `{zero_confidence_count}`",
                f"- OCR-missing boxes: `{missing_count}`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_manga_detection(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    adapter_id: str = "mock",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    preprocess_manifest = _load_preprocess_manifest(workspace, project_slug=project_slug, run_id=run_id)
    adapter = _detection_adapter(
        adapter_id,
        workspace=workspace,
        preprocess_manifest=preprocess_manifest,
    )
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
    diagnostic_paths = _write_detection_diagnostics(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        adapter=adapter,
        preprocess_manifest=preprocess_manifest,
        detected_regions=detected_regions,
        detection_dir=detection_dir,
    )
    bubble_regions = (
        [region for region in detected_regions if region["region_type"] == "dialogue"]
        if adapter.provides_bubbles
        else []
    )
    inserted_count = 0
    skipped_existing_count = 0
    retired_stale_adapter_count = 0
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
            current_adapter_keys = {
                str(item["box_id"])
                for item in detected_regions
                if item["page_id"] == page_id
            }
            stale_adapter_rows = [
                row
                for row in manual_boxes
                if row.get("origin") == "local_adapter"
                and row.get("change_reason")
                == f"detection_adapter:{adapter.adapter_id}"
                and str(row["stable_key"]) not in current_adapter_keys
            ]
            for row in stale_adapter_rows:
                conn.execute(
                    """
                    UPDATE manga_boxes
                    SET deleted = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["internal_box_id"]),
                )
            retired_stale_adapter_count += len(stale_adapter_rows)
            stale_internal_ids = {
                str(row["internal_box_id"]) for row in stale_adapter_rows
            }
            manual_boxes = [
                row
                for row in manual_boxes
                if str(row["internal_box_id"]) not in stale_internal_ids
            ]
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
            "stale_adapter_boxes_retired": retired_stale_adapter_count,
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
            **diagnostic_paths,
            "regions_detected": len(detected_regions),
            "box_count": sum(len(page["boxes"]) for page in merged_pages.values()),
            "bubble_regions": len(bubble_regions),
            "adapter_boxes_inserted": inserted_count,
            "adapter_boxes_skipped_existing": skipped_existing_count,
            "stale_adapter_boxes_retired": retired_stale_adapter_count,
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


def _ocr_review_states(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT stable_box_id, review_state, active_ocr_result_id, updated_at
        FROM manga_ocr_review_states
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchall()
    return {str(row["stable_box_id"]): row_to_dict(row) for row in rows}


def _set_ocr_review_state(
    conn,
    *,
    project_id: str,
    project_slug: str,
    run_id: str,
    page_id: str,
    internal_box_id: str,
    stable_box_id: str,
    review_state: str,
    active_ocr_result_id: str | None,
    reviewer: str | None,
    note: str | None,
    now: str,
) -> None:
    if review_state not in MANGA_OCR_REVIEW_STATES:
        raise ValueError(f"Invalid OCR review_state: {review_state}")
    conn.execute(
        """
        INSERT INTO manga_ocr_review_states (
            id, project_id, project_slug, run_id, page_id, box_id, stable_box_id,
            active_ocr_result_id, review_state, reviewer, note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, stable_box_id) DO UPDATE SET
            run_id = excluded.run_id,
            page_id = excluded.page_id,
            box_id = excluded.box_id,
            active_ocr_result_id = COALESCE(excluded.active_ocr_result_id, active_ocr_result_id),
            review_state = excluded.review_state,
            reviewer = excluded.reviewer,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (
            new_id("mangaocrstate"),
            project_id,
            project_slug,
            run_id,
            page_id,
            internal_box_id,
            stable_box_id,
            active_ocr_result_id,
            review_state,
            reviewer,
            note,
            now,
            now,
        ),
    )


def run_manga_ocr(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    adapter_id: str = "mock",
    allow_cloud: bool = False,
    adapter_config: dict[str, Any] | None = None,
    language: str | None = None,
    model_dir: str | None = None,
    cache_dir: str | None = None,
    page_index: int | None = None,
    max_pages: int | None = None,
    ocr_variant: str = "auto",
    force: bool = False,
    no_network: bool = False,
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool | None = None,
    bootstrap_if_missing: bool = False,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    preprocess_manifest = _load_preprocess_manifest(workspace, project_slug=project_slug, run_id=run_id)
    ocr_dir = _ocr_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    effective_config = dict(adapter_config or {})
    if language is not None:
        effective_config["language"] = language
    if model_dir is not None:
        effective_config["model_dir"] = model_dir
    if cache_dir is not None:
        effective_config["cache_dir"] = cache_dir
    elif adapter_id == "paddleocr":
        effective_config.setdefault("cache_dir", str(_default_ocr_model_cache_dir(workspace)))
    if ocr_variant:
        effective_config["ocr_variant"] = _validate_ocr_variant(ocr_variant)
    effective_config["force"] = bool(force)
    effective_config["no_network"] = bool(no_network)
    effective_config["disable_onednn"] = bool(disable_onednn)
    if disable_paddlex_mkldnn is not None:
        effective_config["disable_paddlex_mkldnn"] = bool(disable_paddlex_mkldnn)
    effective_config["bootstrap_if_missing"] = bool(bootstrap_if_missing)
    adapter = _ocr_adapter(
        adapter_id,
        allow_cloud=allow_cloud,
        workspace=workspace,
        preprocess_manifest=preprocess_manifest,
        ocr_dir=ocr_dir,
        adapter_config=effective_config,
    )
    if adapter.execution_mode == "cloud" and not allow_cloud:
        raise ValueError("Cloud OCR adapters require explicit opt-in and are disabled by default.")
    raw_dir = ocr_dir / ("paddleocr_raw" if adapter.adapter_id == PaddleOcrAdapter.adapter_id else "raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    corrections_path, candidates_path = _ensure_ocr_review_artifacts(ocr_dir)
    review_corrections_path = ocr_dir / "review_corrections.jsonl"
    if not review_corrections_path.exists():
        review_corrections_path.write_text(corrections_path.read_text(encoding="utf-8"), encoding="utf-8")
    ocr_results_path = ocr_dir / "ocr_results.json"
    confidence_json_path = ocr_dir / "ocr_confidence_report.json"
    confidence_md_path = ocr_dir / "ocr_confidence_report.md"
    confidence_alias_json_path = ocr_dir / "confidence_report.json"
    confidence_alias_md_path = ocr_dir / "confidence_report.md"
    review_summary_path = ocr_dir / "ocr_review_summary.md"
    review_summary_alias_path = ocr_dir / "review_summary.md"
    pages_dir = ocr_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    paddle_report_json_path = ocr_dir / "paddleocr_adapter_report.json"
    paddle_report_md_path = ocr_dir / "paddleocr_adapter_report.md"
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.ocr",
            status="running",
            stage="ocr_boxes",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "adapter_id": adapter.adapter_id,
                "allow_cloud": allow_cloud,
                "adapter_config": _redact_adapter_config(effective_config),
                "page_index": page_index,
                "max_pages": max_pages,
                "force": bool(force),
            },
            result_data={},
        )
        box_rows = [
            row
            for row in _current_boxes_for_project(conn, project_id=project["id"])
            if row.get("stable_key") is not None
        ]
        if not box_rows:
            raise ValueError("BLOCKED_OCR_SCHEMA: no stable boxes are available for OCR.")
        states = _ocr_review_states(conn, project_id=project["id"])
        translatable_rows = [
            row
            for row in box_rows
            if states.get(str(row["stable_key"]), {}).get("review_state") != "not_translatable"
        ]
        if page_index is not None:
            translatable_rows = [row for row in translatable_rows if int(row["page_index"]) == int(page_index)]
        if max_pages is not None:
            allowed_pages = []
            for row in translatable_rows:
                page_no = int(row["page_index"])
                if page_no not in allowed_pages:
                    allowed_pages.append(page_no)
            allowed_page_set = set(sorted(allowed_pages)[: max(0, int(max_pages))])
            translatable_rows = [row for row in translatable_rows if int(row["page_index"]) in allowed_page_set]
        adapter_boxes = [
            {
                "page_id": row["page_id"],
                "page_index": row["page_index"],
                "box_id": row["stable_key"],
                "region_type": "dialogue" if row["box_type"] == "speech" else row["box_type"],
                "bbox": row["bbox_json"],
                "polygon": row["polygon_json"],
                "orientation": "unknown",
            }
            for row in translatable_rows
        ]
        adapter_results = adapter.recognize(boxes=adapter_boxes)
        rows_by_stable = {str(row["stable_key"]): row for row in translatable_rows}
        result_records: list[dict[str, Any]] = []
        page_records: dict[int, list[dict[str, Any]]] = {}
        for result_index, adapter_result in enumerate(adapter_results, start=1):
            if adapter_result.box_id not in rows_by_stable:
                raise ValueError(f"BLOCKED_OCR_SCHEMA: OCR adapter returned unknown box_id {adapter_result.box_id}.")
            row = rows_by_stable[adapter_result.box_id]
            effective_review_state = str(
                states.get(adapter_result.box_id, {}).get("review_state")
                or adapter_result.review_state
            )
            if effective_review_state not in MANGA_OCR_REVIEW_STATES:
                raise ValueError(f"Invalid OCR review_state: {effective_review_state}")
            if not 0 <= adapter_result.confidence <= 1:
                raise ValueError(f"OCR confidence must be between 0 and 1: {adapter_result.box_id}")
            raw_path = raw_dir / f"{row['page_index']:04d}_{result_index:04d}_raw.json"
            raw_payload = {
                "schema_version": MANGA_OCR_SCHEMA_VERSION,
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.adapter_version,
                "execution_mode": adapter.execution_mode,
                "cloud_used": adapter.execution_mode == "cloud",
                "page_id": adapter_result.page_id,
                "box_id": adapter_result.box_id,
                "raw_output": adapter_result.raw_output,
            }
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_windows_long_path(raw_path), "w", encoding="utf-8") as handle:
                handle.write(json_dumps(raw_payload) + "\n")
            source_image_path = adapter_result.raw_output.get("source_image_path")
            cropped_image_path = adapter_result.raw_output.get("cropped_image_path")
            ocr_result_id = new_id("mangaocr")
            record = {
                "page_id": adapter_result.page_id,
                "page_index": row["page_index"],
                "box_id": adapter_result.box_id,
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.adapter_version,
                "text": adapter_result.text,
                "confidence": float(adapter_result.confidence),
                "language_detected": adapter_result.language_detected,
                "orientation_detected": adapter_result.orientation_detected,
                "source_image_path": source_image_path,
                "cropped_image_path": cropped_image_path,
                "model_metadata": {
                    "language": adapter_result.language_detected,
                    "adapter_config": _redact_adapter_config(effective_config),
                },
                "raw_output_artifact": _relative_to_workspace(workspace, raw_path),
                "review_state": effective_review_state,
            }
            result_records.append(record)
            page_records.setdefault(int(row["page_index"]), []).append(record)
            conn.execute(
                """
                INSERT INTO manga_ocr_results (
                    id, box_id, box_version_id, engine_name, raw_text, normalized_text,
                    confidence, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ocr_result_id,
                    row["internal_box_id"],
                    row["version_id"],
                    adapter.adapter_id,
                    adapter_result.text,
                    adapter_result.text.strip(),
                    float(adapter_result.confidence),
                    json_dumps(
                        {
                            "schema_version": MANGA_OCR_SCHEMA_VERSION,
                            "run_id": run_id,
                            "stable_box_id": adapter_result.box_id,
                            "adapter_version": adapter.adapter_version,
                            "language_detected": adapter_result.language_detected,
                            "orientation_detected": adapter_result.orientation_detected,
                            "source_image_path": source_image_path,
                            "cropped_image_path": cropped_image_path,
                            "raw_output_artifact": _relative_to_workspace(workspace, raw_path),
                            "review_state": effective_review_state,
                        }
                    ),
                    now,
                ),
            )
            _set_ocr_review_state(
                conn,
                project_id=project["id"],
                project_slug=project_slug,
                run_id=run_id,
                page_id=adapter_result.page_id,
                internal_box_id=row["internal_box_id"],
                stable_box_id=adapter_result.box_id,
                review_state=effective_review_state,
                active_ocr_result_id=ocr_result_id,
                reviewer=None,
                note=None,
                now=now,
            )

        for page_index, records in sorted(page_records.items()):
            page_path = ocr_dir / f"page_{page_index:04d}_ocr.json"
            page_payload = {
                "schema_version": MANGA_OCR_SCHEMA_VERSION,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "page_index": page_index,
                "results": records,
            }
            with open(_windows_long_path(page_path), "w", encoding="utf-8") as handle:
                handle.write(json_dumps(page_payload) + "\n")
            page_id_for_file = str(records[0]["page_id"]) if records else str(page_index)
            page_id_path = pages_dir / f"{_safe_name(page_id_for_file)}.json"
            with open(_windows_long_path(page_id_path), "w", encoding="utf-8") as handle:
                handle.write(json_dumps(page_payload) + "\n")

        confidence = _ocr_confidence_summary(result_records)
        ocr_payload = {
            "schema_version": MANGA_OCR_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "adapter": {
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.adapter_version,
                "execution_mode": adapter.execution_mode,
                "cloud_used": adapter.execution_mode == "cloud",
                "config_snapshot": _redact_adapter_config(adapter_config),
            },
            "results": result_records,
            "result_count": len(result_records),
            "skipped_not_translatable_count": len(box_rows) - len(translatable_rows),
            "confidence_summary": confidence,
        }
        ocr_results_path.write_text(json_dumps(ocr_payload) + "\n", encoding="utf-8")
        confidence_json_path.write_text(
            json_dumps(
                {
                    "schema_version": MANGA_OCR_SCHEMA_VERSION,
                    "project_id": project["id"],
                    "project_slug": project_slug,
                    "run_id": run_id,
                    "confidence_summary": confidence,
                    "low_confidence_box_ids": [
                        record["box_id"] for record in result_records if record["confidence"] < 0.8
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        confidence_alias_json_path.write_text(confidence_json_path.read_text(encoding="utf-8"), encoding="utf-8")
        confidence_md_text = "\n".join(
            [
                "# OCR Confidence Report",
                "",
                f"- Schema version: `{MANGA_OCR_SCHEMA_VERSION}`",
                f"- Result count: `{confidence['count']}`",
                f"- Average confidence: `{confidence['average']}`",
                f"- Low confidence count: `{confidence['low_confidence_count']}`",
                "",
            ]
        )
        confidence_md_path.write_text(confidence_md_text, encoding="utf-8")
        confidence_alias_md_path.write_text(confidence_md_text, encoding="utf-8")
        review_summary_text = "\n".join(
            [
                "# OCR Review Summary",
                "",
                f"- Pending: `{len([record for record in result_records if record['review_state'] == 'pending'])}`",
                "- Approved: `0`",
                "- Corrected: `0`",
                "- Ignored: `0`",
                f"- Not translatable skipped: `{len(box_rows) - len(translatable_rows)}`",
                f"- Corrections artifact: `{_relative_to_workspace(workspace, corrections_path)}`",
                f"- Review corrections artifact: `{_relative_to_workspace(workspace, review_corrections_path)}`",
                f"- Candidate artifact: `{_relative_to_workspace(workspace, candidates_path)}`",
                "",
            ]
        )
        review_summary_path.write_text(review_summary_text, encoding="utf-8")
        review_summary_alias_path.write_text(review_summary_text, encoding="utf-8")
        if adapter.adapter_id == PaddleOcrAdapter.adapter_id:
            paddle_report = {
                "schema_version": MANGA_OCR_SCHEMA_VERSION,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "adapter_id": adapter.adapter_id,
                "adapter_version": adapter.adapter_version,
                "config": _redact_adapter_config(effective_config),
                "raw_dir": _relative_to_workspace(workspace, raw_dir),
                "result_count": len(result_records),
                "skipped_not_translatable_count": len(box_rows) - len(translatable_rows),
            }
            paddle_report_json_path.write_text(json_dumps(paddle_report) + "\n", encoding="utf-8")
            paddle_report_md_path.write_text(
                "\n".join(
                    [
                        "# PaddleOCR Adapter Report",
                        "",
                        f"- Adapter version: `{adapter.adapter_version}`",
                        f"- Language: `{effective_config.get('language', 'ch')}`",
                        f"- Device: `{effective_config.get('device', 'cpu')}`",
                        f"- OCR variant: `{effective_config.get('ocr_variant', 'auto')}`",
                        f"- Result count: `{len(result_records)}`",
                        f"- Raw directory: `{_relative_to_workspace(workspace, raw_dir)}`",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        rel_results = _relative_to_workspace(workspace, ocr_results_path)
        rel_conf_json = _relative_to_workspace(workspace, confidence_json_path)
        rel_conf_md = _relative_to_workspace(workspace, confidence_md_path)
        rel_corrections = _relative_to_workspace(workspace, corrections_path)
        rel_review = _relative_to_workspace(workspace, review_summary_path)
        rel_candidates = _relative_to_workspace(workspace, candidates_path)
        rel_review_corrections = _relative_to_workspace(workspace, review_corrections_path)
        conn.execute(
            """
            INSERT INTO manga_ocr_runs (
                id, run_id, project_id, project_slug, adapter_id, adapter_version,
                execution_mode, cloud_used, ocr_results_path, confidence_report_path,
                review_summary_path, correction_count, result_count, warnings_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangaocrun"),
                run_id,
                project["id"],
                project_slug,
                adapter.adapter_id,
                adapter.adapter_version,
                adapter.execution_mode,
                1 if adapter.execution_mode == "cloud" else 0,
                rel_results,
                rel_conf_json,
                rel_review,
                0,
                len(result_records),
                json_dumps([]),
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
            "ocr_results_path": rel_results,
            "ocr_confidence_report_path": rel_conf_json,
            "ocr_confidence_report_md_path": rel_conf_md,
            "ocr_corrections_path": rel_corrections,
            "review_corrections_path": rel_review_corrections,
            "ocr_review_summary_path": rel_review,
            "review_summary_path": _relative_to_workspace(workspace, review_summary_alias_path),
            "confidence_report_path": _relative_to_workspace(workspace, confidence_alias_json_path),
            "memory_dictionary_candidates_path": rel_candidates,
            "paddleocr_adapter_report_path": _relative_to_workspace(workspace, paddle_report_json_path)
            if adapter.adapter_id == PaddleOcrAdapter.adapter_id
            else None,
            "paddleocr_adapter_report_md_path": _relative_to_workspace(workspace, paddle_report_md_path)
            if adapter.adapter_id == PaddleOcrAdapter.adapter_id
            else None,
            "result_count": len(result_records),
            "skipped_not_translatable_count": len(box_rows) - len(translatable_rows),
            "confidence_summary": confidence,
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    _update_detection_ocr_diagnostics(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        ocr_results=result_records,
    )
    return {"task_run_id": task_id, **result}


def _latest_ocr_for_stable_box(conn, *, project_id: str, stable_box_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT p.id AS page_id, b.id AS internal_box_id, b.stable_key, o.id AS ocr_result_id,
               o.raw_text, o.normalized_text, o.confidence, o.metadata_json
        FROM manga_ocr_results o
        JOIN manga_boxes b ON b.id = o.box_id
        JOIN manga_pages p ON p.id = b.page_id
        WHERE p.project_id = ? AND b.stable_key = ?
        ORDER BY o.created_at DESC, o.id DESC
        LIMIT 1
        """,
        (project_id, stable_box_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"OCR result not found for box_id: {stable_box_id}")
    return row_to_dict(row, json_fields=("metadata_json",))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")


def import_manga_ocr_corrections(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    corrections_path: Path,
    reviewer: str = "cli",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if not corrections_path.exists():
        raise ValueError(f"OCR corrections file not found: {corrections_path}")
    ocr_dir = _ocr_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    artifact_corrections, candidates_path = _ensure_ocr_review_artifacts(ocr_dir)
    review_corrections_path = ocr_dir / "review_corrections.jsonl"
    if not review_corrections_path.exists():
        review_corrections_path.write_text(
            artifact_corrections.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    imported = 0
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.ocr.corrections.import",
            status="running",
            stage="import_ocr_corrections",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "reviewer": reviewer},
            result_data={},
        )
        for line_no, line in enumerate(corrections_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"OCR correction line {line_no} is not valid JSON.") from exc
            box_id = str(payload.get("box_id") or "")
            corrected_text = str(payload.get("corrected_text") or "")
            if not box_id or not corrected_text:
                raise ValueError(f"OCR correction line {line_no} requires box_id and corrected_text.")
            source = _latest_ocr_for_stable_box(conn, project_id=project["id"], stable_box_id=box_id)
            previous_text = str(source.get("normalized_text") or source.get("raw_text") or "")
            correction_id = new_id("mangaocrcorrection")
            correction_payload = {
                "schema_version": MANGA_OCR_SCHEMA_VERSION,
                "correction_id": correction_id,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "page_id": source["page_id"],
                "box_id": box_id,
                "ocr_result_id": source["ocr_result_id"],
                "previous_text": previous_text,
                "corrected_text": corrected_text,
                "reviewer": str(payload.get("reviewer") or reviewer),
                "reason": payload.get("reason"),
                "created_at": now,
            }
            candidate_payload = {
                "schema_version": MANGA_OCR_SCHEMA_VERSION,
                "candidate_id": new_id("mangaocrcandidate"),
                "candidate_type": "ocr_correction",
                "auto_promote": False,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "page_id": source["page_id"],
                "box_id": box_id,
                "before_text": previous_text,
                "after_text": corrected_text,
                "confidence": source.get("confidence"),
                "reviewer": correction_payload["reviewer"],
                "scope": "project",
                "source_artifact": _relative_to_workspace(workspace, artifact_corrections),
            }
            _append_jsonl(artifact_corrections, correction_payload)
            _append_jsonl(review_corrections_path, correction_payload)
            _append_jsonl(candidates_path, candidate_payload)
            conn.execute(
                """
                INSERT INTO manga_ocr_corrections (
                    id, project_id, project_slug, run_id, page_id, box_id, stable_box_id,
                    ocr_result_id, previous_text, corrected_text, reviewer, reason,
                    candidate_artifact, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    correction_id,
                    project["id"],
                    project_slug,
                    run_id,
                    source["page_id"],
                    source["internal_box_id"],
                    box_id,
                    source["ocr_result_id"],
                    previous_text,
                    corrected_text,
                    correction_payload["reviewer"],
                    correction_payload["reason"],
                    _relative_to_workspace(workspace, candidates_path),
                    now,
                ),
            )
            _set_ocr_review_state(
                conn,
                project_id=project["id"],
                project_slug=project_slug,
                run_id=run_id,
                page_id=source["page_id"],
                internal_box_id=source["internal_box_id"],
                stable_box_id=box_id,
                review_state="corrected",
                active_ocr_result_id=source["ocr_result_id"],
                reviewer=correction_payload["reviewer"],
                note=correction_payload["reason"],
                now=now,
            )
            imported += 1

        review_summary_path = ocr_dir / "ocr_review_summary.md"
        review_summary_path.write_text(
            "\n".join(
                [
                    "# OCR Review Summary",
                    "",
                    f"- Corrections imported: `{imported}`",
                    f"- Corrections artifact: `{_relative_to_workspace(workspace, artifact_corrections)}`",
                    f"- Review corrections artifact: `{_relative_to_workspace(workspace, review_corrections_path)}`",
                    f"- Candidate artifact: `{_relative_to_workspace(workspace, candidates_path)}`",
                    "- Auto promotion: `false`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "corrections_imported": imported,
            "ocr_corrections_path": _relative_to_workspace(workspace, artifact_corrections),
            "review_corrections_path": _relative_to_workspace(workspace, review_corrections_path),
            "memory_dictionary_candidates_path": _relative_to_workspace(workspace, candidates_path),
            "ocr_review_summary_path": _relative_to_workspace(workspace, review_summary_path),
            "auto_promote": False,
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


def import_manga_ocr_review(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    review_path: Path,
    reviewer: str = "cli",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if not review_path.exists():
        raise ValueError(f"OCR review file not found: {review_path}")
    now = utc_now()
    imported = 0
    corrections_temp: list[dict[str, Any]] = []
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.ocr.review.import",
            status="running",
            stage="import_ocr_review",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "reviewer": reviewer},
            result_data={},
        )
        for line_no, line in enumerate(review_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"OCR review line {line_no} is not valid JSON.") from exc
            box_id = str(payload.get("box_id") or "")
            review_state = str(payload.get("review_state") or "")
            if not box_id or review_state not in MANGA_OCR_REVIEW_STATES:
                raise ValueError(f"OCR review line {line_no} requires box_id and valid review_state.")
            if review_state == "corrected":
                corrections_temp.append(payload)
                continue
            source = _latest_ocr_for_stable_box(conn, project_id=project["id"], stable_box_id=box_id)
            _set_ocr_review_state(
                conn,
                project_id=project["id"],
                project_slug=project_slug,
                run_id=run_id,
                page_id=source["page_id"],
                internal_box_id=source["internal_box_id"],
                stable_box_id=box_id,
                review_state=review_state,
                active_ocr_result_id=source["ocr_result_id"],
                reviewer=str(payload.get("reviewer") or reviewer),
                note=payload.get("note"),
                now=now,
            )
            imported += 1
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "review_states_imported": imported,
            "corrections_deferred": len(corrections_temp),
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    if corrections_temp:
        temp_path = workspace.path / "artifacts" / "manga" / project_slug / run_id / "ocr" / "_review_corrections_tmp.jsonl"
        temp_path.write_text(
            "\n".join(json_dumps(item) for item in corrections_temp) + "\n",
            encoding="utf-8",
        )
        correction_result = import_manga_ocr_corrections(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            corrections_path=temp_path,
            reviewer=reviewer,
        )
        result["corrections_imported"] = correction_result["corrections_imported"]
    return {"task_run_id": task_id, **result}


def export_manga_ocr(workspace: Workspace, *, project_slug: str, run_id: str) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    ocr_results_path = _ocr_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "ocr_results.json"
    if not ocr_results_path.exists():
        raise ValueError(f"OCR results not found for run {run_id}.")
    payload = json.loads(ocr_results_path.read_text(encoding="utf-8"))
    bounded_results = [
        {
            **{key: value for key, value in result.items() if key != "text"},
            "text_preview": _truncate_text(str(result.get("text") or "")),
        }
        for result in payload.get("results", [])
    ]
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "ocr_results_path": _relative_to_workspace(workspace, ocr_results_path),
        "result_count": payload.get("result_count", len(bounded_results)),
        "results_preview": bounded_results,
    }


def _ocr_bootstrap_dir(workspace: Workspace, *, run_id: str) -> Path:
    path = workspace.path / "artifacts" / "manga_ocr_bootstrap" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scan_model_cache(cache_dir: Path, *, create: bool = True) -> dict[str, Any]:
    if create:
        cache_dir.mkdir(parents=True, exist_ok=True)
    exists = cache_dir.exists()
    files: list[dict[str, Any]] = []
    total_bytes = 0
    all_files: list[Path] = []
    if exists:
        try:
            all_files = sorted(item for item in cache_dir.rglob("*") if item.is_file())
        except OSError:
            all_files = []
    for path in all_files:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        if len(files) < 200:
            file_hash = None
            if size <= 50_000_000:
                try:
                    file_hash = sha256_file(path)
                except OSError:
                    file_hash = "unavailable_permission_denied"
            files.append(
                {
                    "path": path.relative_to(cache_dir).as_posix(),
                    "size_bytes": size,
                    "sha256": file_hash,
                }
            )
    return {
        "cache_dir": str(cache_dir),
        "exists": exists,
        "file_count": len(all_files),
        "total_bytes": total_bytes,
        "files_sample": files,
    }


def _write_ocr_doctor_markdown(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(
            [
                "# OCR Doctor Report",
                "",
                f"- Engine: `{report['engine']}`",
                f"- Python: `{report['python']['version']}`",
                f"- OS: `{report['platform']['system']} {report['platform']['release']}`",
                f"- PaddleOCR import: `{report['paddleocr']['available']}`",
                f"- PaddleOCR version: `{report['paddleocr'].get('version')}`",
                f"- PaddlePaddle import: `{report['paddlepaddle']['available']}`",
                f"- PaddlePaddle version: `{report['paddlepaddle'].get('version')}`",
                f"- Cache dir: `{report['model_cache']['cache_dir']}`",
                f"- Cache files: `{report['model_cache']['file_count']}`",
                f"- PaddleX default cache dir: `{report['paddlex_default_model_cache']['cache_dir']}`",
                f"- PaddleX default cache files: `{report['paddlex_default_model_cache']['file_count']}`",
                f"- Tiny smoke status: `{report['tiny_smoke']['status']}`",
                f"- Install command: `{report['install_instructions']['uv_extra']}`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_paddleocr_bootstrap_markdown(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(
            [
                "# PaddleOCR Bootstrap Report",
                "",
                f"- Engine: `{report['engine']}`",
                f"- Download models requested: `{report['download_models']}`",
                f"- Status: `{report['status']}`",
                f"- Cache dir: `{report['model_cache_manifest']['cache_dir']}`",
                f"- Cache files: `{report['model_cache_manifest']['file_count']}`",
                f"- PaddleX default cache dir: `{report['model_cache_manifest']['paddlex_default_model_cache']['cache_dir']}`",
                f"- PaddleX default cache files: `{report['model_cache_manifest']['paddlex_default_model_cache']['file_count']}`",
                f"- Tiny smoke status: `{report['tiny_smoke']['status']}`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _create_tiny_ocr_smoke_image(path: Path) -> None:
    Image, _ImageOps = _load_pillow()
    image = Image.new("RGB", (180, 60), "white")
    try:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        draw.text((12, 20), "OCR 123", fill="black")
    except Exception:
        pass
    image.save(path, format="PNG")


def _run_paddleocr_tiny_smoke(
    *,
    image_path: Path,
    cache_dir: Path,
    language: str = "ch",
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool = False,
    no_network: bool = False,
    disable_model_source_check: bool = False,
    init_profile: str = "minimal",
) -> dict[str, Any]:
    _configure_paddleocr_cache(cache_dir)
    flags = _apply_paddleocr_runtime_flags(
        disable_onednn=disable_onednn,
        disable_paddlex_mkldnn=disable_paddlex_mkldnn,
        no_network=no_network,
        disable_model_source_check=disable_model_source_check,
    )
    status = _import_module_status("paddleocr")
    if not status["available"]:
        return {
            "status": "missing_dependency",
            "ok": False,
            "error": PADDLEOCR_MISSING_MESSAGE,
            "error_category": "missing_dependency",
            "runtime_flags": flags,
        }
    try:
        module = importlib.import_module("paddleocr")
        paddle_ocr_class = getattr(module, "PaddleOCR")
        try:
            init_kwargs: dict[str, Any] = {"lang": language, "engine": "paddle", "device": "cpu"}
            if init_profile == "minimal":
                init_kwargs.update(
                    {
                        "use_doc_orientation_classify": False,
                        "use_doc_unwarping": False,
                        "use_textline_orientation": False,
                    }
                )
            if disable_paddlex_mkldnn:
                init_kwargs["enable_mkldnn"] = False
            engine = paddle_ocr_class(**init_kwargs)
        except TypeError:
            legacy_kwargs: dict[str, Any] = {
                "lang": language,
                "use_angle_cls": False,
                "use_gpu": False,
            }
            if disable_paddlex_mkldnn:
                legacy_kwargs["enable_mkldnn"] = False
            engine = paddle_ocr_class(**legacy_kwargs)
        if hasattr(engine, "predict"):
            raw_result = engine.predict(str(image_path))
            api_mode = "paddleocr_3_predict"
        elif hasattr(engine, "ocr"):
            raw_result = engine.ocr(str(image_path), cls=False)
            api_mode = "paddleocr_2_ocr"
        else:
            raise ValueError("PaddleOCR object does not expose predict() or ocr().")
        text, confidence = _extract_paddleocr_text(raw_result)
        return {
            "status": "success",
            "ok": True,
            "api_mode": api_mode,
            "init_profile": init_profile,
            "text_preview": _truncate_text(text),
            "confidence": confidence,
            "raw_result_summary": _summarize_paddleocr_raw_result(raw_result),
            "error_category": None,
            "runtime_flags": flags,
        }
    except Exception as exc:
        error = str(exc)
        return {
            "status": "failed",
            "ok": False,
            "error": error,
            "error_category": _classify_paddleocr_runtime_error(error),
            "runtime_flags": flags,
            "init_profile": init_profile,
        }


def _summarize_paddleocr_raw_result(raw_result: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": type(raw_result).__name__,
        "repr_preview": _truncate_text(repr(raw_result), max_chars=500),
    }
    if isinstance(raw_result, (list, tuple)):
        summary["length"] = len(raw_result)
        if raw_result:
            summary["first_item_type"] = type(raw_result[0]).__name__
            summary["first_item_keys"] = (
                sorted(str(key) for key in raw_result[0].keys())[:20]
                if isinstance(raw_result[0], dict)
                else []
            )
    elif isinstance(raw_result, dict):
        summary["keys"] = sorted(str(key) for key in raw_result.keys())[:20]
    return summary


def doctor_manga_ocr_runtime(
    workspace: Workspace,
    *,
    engine: str = "paddleocr",
    run_id: str | None = None,
    cache_dir: Path | None = None,
    run_smoke: bool = False,
    language: str = "ch",
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool | None = None,
    no_network: bool = False,
    disable_model_source_check: bool | None = None,
    init_profile: str = "minimal",
) -> dict[str, Any]:
    if engine != "paddleocr":
        raise ValueError(f"Unsupported OCR doctor engine: {engine}")
    effective_run_id = run_id or f"ocrdoctor_{uuid.uuid4().hex[:12]}"
    report_dir = _ocr_bootstrap_dir(workspace, run_id=effective_run_id)
    effective_cache = cache_dir or _default_ocr_model_cache_dir(workspace)
    _configure_paddleocr_cache(effective_cache)
    effective_disable_paddlex = (
        _default_disable_paddlex_mkldnn_for_cpu()
        if run_smoke and disable_paddlex_mkldnn is None
        else bool(disable_paddlex_mkldnn)
    )
    effective_disable_source_check = (
        effective_disable_paddlex if disable_model_source_check is None else disable_model_source_check
    )
    doctor_runtime_flags = {"applied": {}, "snapshot_after_apply": _paddle_runtime_env_snapshot()}
    if disable_onednn or effective_disable_paddlex or no_network or effective_disable_source_check:
        doctor_runtime_flags = _apply_paddleocr_runtime_flags(
            disable_onednn=disable_onednn,
            disable_paddlex_mkldnn=effective_disable_paddlex,
            no_network=no_network,
            disable_model_source_check=effective_disable_source_check,
        )
    tiny_image_path = report_dir / "tiny_ocr_smoke.png"
    tiny_smoke = {"status": "not_run", "ok": None, "reason": "pass --smoke or bootstrap --download-models"}
    if run_smoke:
        _create_tiny_ocr_smoke_image(tiny_image_path)
        tiny_smoke = _run_paddleocr_tiny_smoke(
            image_path=tiny_image_path,
            cache_dir=effective_cache,
            language=language,
            disable_onednn=disable_onednn,
            disable_paddlex_mkldnn=effective_disable_paddlex,
            no_network=no_network,
            disable_model_source_check=effective_disable_source_check,
            init_profile=init_profile,
        )
    report = {
        "schema_version": "phase9d1.ocr_doctor.v1",
        "engine": engine,
        "run_id": effective_run_id,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "architecture": platform.architecture()[0],
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "paddleocr": _import_module_status("paddleocr"),
        "paddlepaddle": _import_module_status("paddle", "paddlepaddle"),
        "model_cache": _scan_model_cache(effective_cache),
        "paddlex_default_model_cache": _scan_model_cache(_paddlex_default_model_cache_dir(), create=False),
        "runtime_flags": doctor_runtime_flags,
        "tiny_smoke": tiny_smoke,
        "install_instructions": {
            "uv_extra": "uv sync --extra ocr",
            "windows_cpu_official_index": (
                "uv pip install paddlepaddle==3.2.0 "
                "-i https://www.paddlepaddle.org.cn/packages/stable/cpu/"
            ),
        },
    }
    report_json = report_dir / "ocr_doctor_report.json"
    report_md = report_dir / "ocr_doctor_report.md"
    report_json.write_text(json_dumps(report) + "\n", encoding="utf-8")
    _write_ocr_doctor_markdown(report_md, report)
    return {
        **report,
        "ocr_doctor_report_path": _relative_to_workspace(workspace, report_json),
        "ocr_doctor_report_md_path": _relative_to_workspace(workspace, report_md),
    }


def bootstrap_manga_ocr_runtime(
    workspace: Workspace,
    *,
    engine: str = "paddleocr",
    download_models: bool = False,
    offline_cache: Path | None = None,
    language: str = "ch",
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool | None = None,
    no_network: bool = False,
    disable_model_source_check: bool | None = None,
    init_profile: str = "minimal",
) -> dict[str, Any]:
    if engine != "paddleocr":
        raise ValueError(f"Unsupported OCR bootstrap engine: {engine}")
    run_id = f"ocrbootstrap_{uuid.uuid4().hex[:12]}"
    report_dir = _ocr_bootstrap_dir(workspace, run_id=run_id)
    effective_cache = offline_cache or _default_ocr_model_cache_dir(workspace)
    if offline_cache is not None and not offline_cache.exists():
        raise ValueError(f"Offline OCR cache not found: {offline_cache}")
    effective_disable_paddlex = (
        _default_disable_paddlex_mkldnn_for_cpu()
        if disable_paddlex_mkldnn is None
        else disable_paddlex_mkldnn
    )
    effective_disable_source_check = (
        effective_disable_paddlex if disable_model_source_check is None else disable_model_source_check
    )
    tiny_image_path = report_dir / "tiny_ocr_smoke.png"
    doctor = doctor_manga_ocr_runtime(
        workspace,
        engine=engine,
        run_id=run_id,
        cache_dir=effective_cache,
        run_smoke=False,
        language=language,
        disable_onednn=disable_onednn,
        disable_paddlex_mkldnn=effective_disable_paddlex,
        no_network=no_network,
        disable_model_source_check=effective_disable_source_check,
        init_profile=init_profile,
    )
    tiny_smoke = {"status": "not_run", "ok": None, "reason": "download_models not requested"}
    if download_models:
        _create_tiny_ocr_smoke_image(tiny_image_path)
        tiny_smoke = _run_paddleocr_tiny_smoke(
            image_path=tiny_image_path,
            cache_dir=effective_cache,
            language=language,
            disable_onednn=disable_onednn,
            disable_paddlex_mkldnn=effective_disable_paddlex,
            no_network=no_network,
            disable_model_source_check=effective_disable_source_check,
            init_profile=init_profile,
        )
    manifest = {
        "schema_version": "phase9d1.paddleocr_model_cache_manifest.v1",
        "engine": engine,
        "run_id": run_id,
        "cache_dir": str(effective_cache),
        "offline_cache": str(offline_cache) if offline_cache is not None else None,
        "paddlex_default_model_cache": _scan_model_cache(_paddlex_default_model_cache_dir(), create=False),
        **_scan_model_cache(effective_cache),
    }
    manifest_path = report_dir / "model_cache_manifest.json"
    manifest_path.write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    status = "success" if not download_models or tiny_smoke.get("ok") else "blocked"
    report = {
        "schema_version": "phase9d1.paddleocr_bootstrap.v1",
        "engine": engine,
        "run_id": run_id,
        "download_models": download_models,
        "status": status,
        "doctor_report_path": doctor["ocr_doctor_report_path"],
        "model_cache_manifest": manifest,
        "tiny_smoke": tiny_smoke,
    }
    report_json = report_dir / "paddleocr_bootstrap_report.json"
    report_md = report_dir / "paddleocr_bootstrap_report.md"
    report_json.write_text(json_dumps(report) + "\n", encoding="utf-8")
    _write_paddleocr_bootstrap_markdown(report_md, report)
    return {
        **report,
        "paddleocr_bootstrap_report_path": _relative_to_workspace(workspace, report_json),
        "paddleocr_bootstrap_report_md_path": _relative_to_workspace(workspace, report_md),
        "model_cache_manifest_path": _relative_to_workspace(workspace, manifest_path),
        "ocr_doctor_report_path": doctor["ocr_doctor_report_path"],
        "ocr_doctor_report_md_path": doctor["ocr_doctor_report_md_path"],
    }


def _runtime_recommendation(error_category: str | None) -> str:
    if error_category == "paddle_onednn_pir_array_attribute":
        return (
            "Windows CPU PaddleOCR is blocked by PaddlePaddle oneDNN/PIR runtime. "
            "First retry with PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0 before import. "
            "If it still fails, try a fresh OCR environment on Python 3.10/3.11 "
            "with paddleocr==3.3.3 and paddlepaddle==3.2.0, or use WSL/Linux/Docker "
            "until a PaddlePaddle wheel containing upstream PR #77430 is available."
        )
    if error_category == "missing_dependency":
        return "Install the OCR extra with `uv sync --extra dev --extra ocr`."
    if error_category == "model_download_or_network":
        return "Run bootstrap with network access on the release builder or provide a prewarmed offline cache."
    if error_category == "runtime_error":
        return "Keep the smoke report and test a known-good OCR runtime matrix before packaging."
    return "Runtime passed on this environment."


def _paddleocr_runtime_matrix_entry(report: dict[str, Any]) -> dict[str, Any]:
    tiny_smoke = report["tiny_smoke"]
    runtime_flags = tiny_smoke.get("runtime_flags") or {}
    env_snapshot = runtime_flags.get("snapshot_after_apply") or report.get("env_flags") or {}
    error_category = tiny_smoke.get("error_category")
    return {
        "python_version": report["python"]["version"],
        "python_executable": report["python"]["executable"],
        "windows_version": report["platform"]["release"] if report["platform"]["system"] == "Windows" else None,
        "platform": report["platform"],
        "paddleocr_version": report["paddleocr"].get("version"),
        "paddlepaddle_version": report["paddlepaddle"].get("version"),
        "device": "cpu",
        "oneDNN_MKLDNN_flags": {
            "FLAGS_use_mkldnn": env_snapshot.get("FLAGS_use_mkldnn"),
            "FLAGS_use_onednn": env_snapshot.get("FLAGS_use_onednn"),
            "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": env_snapshot.get("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"),
        },
        "result": "pass" if tiny_smoke.get("ok") else "blocked",
        "error_category": error_category,
        "error_summary": _truncate_text(str(tiny_smoke.get("error") or ""), max_chars=220),
        "recommendation": _runtime_recommendation(error_category),
    }


def _write_paddleocr_real_smoke_markdown(path: Path, report: dict[str, Any]) -> None:
    matrix = report["runtime_matrix"]["entries"][0]
    path.write_text(
        "\n".join(
            [
                "# PaddleOCR Real Smoke Report",
                "",
                f"- Engine: `{report['engine']}`",
                f"- Status: `{report['status']}`",
                f"- Python: `{matrix['python_version']}`",
                f"- OS: `{report['platform']['system']} {report['platform']['release']}`",
                f"- PaddleOCR: `{matrix['paddleocr_version']}`",
                f"- PaddlePaddle: `{matrix['paddlepaddle_version']}`",
                f"- Device: `{matrix['device']}`",
                f"- FLAGS_use_mkldnn: `{matrix['oneDNN_MKLDNN_flags']['FLAGS_use_mkldnn']}`",
                f"- FLAGS_use_onednn: `{matrix['oneDNN_MKLDNN_flags']['FLAGS_use_onednn']}`",
                (
                    "- PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT: "
                    f"`{matrix['oneDNN_MKLDNN_flags']['PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT']}`"
                ),
                f"- Error category: `{matrix['error_category']}`",
                f"- Error summary: `{matrix['error_summary']}`",
                f"- Recommendation: `{matrix['recommendation']}`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_manga_ocr_real_smoke(
    workspace: Workspace,
    *,
    engine: str = "paddleocr",
    cache_dir: Path | None = None,
    language: str = "ch",
    disable_onednn: bool = False,
    disable_paddlex_mkldnn: bool = False,
    no_network: bool = False,
    disable_model_source_check: bool | None = None,
    init_profile: str = "minimal",
) -> dict[str, Any]:
    if engine != "paddleocr":
        raise ValueError(f"Unsupported OCR real-smoke engine: {engine}")
    run_id = f"ocrsmoke_{uuid.uuid4().hex[:12]}"
    report_dir = _ocr_bootstrap_dir(workspace, run_id=run_id)
    effective_cache = cache_dir or _default_ocr_model_cache_dir(workspace)
    image_path = report_dir / "paddleocr_real_smoke_input.png"
    _create_tiny_ocr_smoke_image(image_path)
    tiny_smoke = _run_paddleocr_tiny_smoke(
        image_path=image_path,
        cache_dir=effective_cache,
        language=language,
        disable_onednn=disable_onednn,
        disable_paddlex_mkldnn=disable_paddlex_mkldnn,
        no_network=no_network,
        disable_model_source_check=(
            disable_paddlex_mkldnn if disable_model_source_check is None else disable_model_source_check
        ),
        init_profile=init_profile,
    )
    status = "pass" if tiny_smoke.get("ok") else "blocked"
    report = {
        "schema_version": PADDLEOCR_REAL_SMOKE_SCHEMA_VERSION,
        "engine": engine,
        "run_id": run_id,
        "status": status,
        "language": language,
        "init_profile": init_profile,
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "architecture": platform.architecture()[0],
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "paddleocr": _import_module_status("paddleocr"),
        "paddlepaddle": _import_module_status("paddle", "paddlepaddle"),
        "env_flags": _paddle_runtime_env_snapshot(),
        "model_cache": _scan_model_cache(effective_cache),
        "paddlex_default_model_cache": _scan_model_cache(_paddlex_default_model_cache_dir(), create=False),
        "input_image_path": _relative_to_workspace(workspace, image_path),
        "tiny_smoke": tiny_smoke,
    }
    matrix = {
        "schema_version": PADDLEOCR_RUNTIME_MATRIX_SCHEMA_VERSION,
        "run_id": run_id,
        "entries": [_paddleocr_runtime_matrix_entry(report)],
    }
    report["runtime_matrix"] = matrix
    report_json = report_dir / "paddleocr_real_smoke_report.json"
    report_md = report_dir / "paddleocr_real_smoke_report.md"
    matrix_json = report_dir / "runtime_matrix_report.json"
    matrix_md = report_dir / "runtime_matrix_report.md"
    report_json.write_text(json_dumps(report) + "\n", encoding="utf-8")
    _write_paddleocr_real_smoke_markdown(report_md, report)
    matrix_json.write_text(json_dumps(matrix) + "\n", encoding="utf-8")
    _write_paddleocr_real_smoke_markdown(matrix_md, report)
    return {
        **report,
        "paddleocr_real_smoke_report_path": _relative_to_workspace(workspace, report_json),
        "paddleocr_real_smoke_report_md_path": _relative_to_workspace(workspace, report_md),
        "runtime_matrix_report_path": _relative_to_workspace(workspace, matrix_json),
        "runtime_matrix_report_md_path": _relative_to_workspace(workspace, matrix_md),
    }


def _reading_order_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    order_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "reading_order"
    order_dir.mkdir(parents=True, exist_ok=True)
    return order_dir


def _validate_reading_direction(direction: str) -> str:
    if direction not in MANGA_READING_DIRECTIONS:
        raise ValueError(
            f"Invalid reading direction: {direction}. Expected one of {sorted(MANGA_READING_DIRECTIONS)}."
        )
    return direction


def _get_project_reading_direction(conn, *, project_id: str) -> str:
    row = conn.execute(
        """
        SELECT reading_direction
        FROM manga_project_reading_settings
        WHERE project_id = ?
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return "right-to-left"
    return _validate_reading_direction(str(row["reading_direction"]))


def _set_project_reading_direction(
    conn,
    *,
    project_id: str,
    project_slug: str,
    direction_preset: str,
    now: str,
) -> None:
    direction = _validate_reading_direction(direction_preset)
    conn.execute(
        """
        INSERT INTO manga_project_reading_settings (
            id, project_id, project_slug, reading_direction, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            project_slug = excluded.project_slug,
            reading_direction = excluded.reading_direction,
            updated_at = excluded.updated_at
        """,
        (new_id("mangareadsetting"), project_id, project_slug, direction, now, now),
    )


def update_manga_reading_direction(
    workspace: Workspace,
    *,
    project_slug: str,
    direction_preset: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    direction = _validate_reading_direction(direction_preset)
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.reading_order.direction.update",
            status="running",
            stage="update_direction",
            project_id=project["id"],
            input_data={"project": project_slug, "direction_preset": direction},
            result_data={},
        )
        _set_project_reading_direction(
            conn,
            project_id=project["id"],
            project_slug=project_slug,
            direction_preset=direction,
            now=now,
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "direction_preset": direction,
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


def _reading_order_box_rows(
    conn,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    states = _ocr_review_states(conn, project_id=project_id)
    rows = []
    for row in _current_boxes_for_project(conn, project_id=project_id):
        stable_key = row.get("stable_key")
        if stable_key is None:
            continue
        review_state = str(states.get(str(stable_key), {}).get("review_state") or "pending")
        if review_state in MANGA_CONTEXT_EXCLUDED_REVIEW_STATES:
            continue
        row = dict(row)
        row["ocr_review_state"] = review_state
        rows.append(row)
    return rows


def _box_region_type(row: dict[str, Any]) -> str:
    return "dialogue" if row.get("box_type") == "speech" else str(row.get("box_type") or "unknown")


def _bbox_xywh(row: dict[str, Any]) -> tuple[float, float, float, float]:
    bbox = row.get("bbox_json")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0, 0.0, 0.0, 0.0
    return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])


def _infer_order_for_boxes(boxes: list[dict[str, Any]], *, direction_preset: str) -> tuple[list[str], list[str]]:
    direction = _validate_reading_direction(direction_preset)
    warnings: list[str] = []

    def stable_key(row: dict[str, Any]) -> str:
        return str(row["stable_key"])

    if direction == "manual":
        missing_manual = [stable_key(row) for row in boxes if row.get("reading_order") is None]
        if missing_manual:
            warnings.append("manual_direction_missing_box_reading_order")
        ordered = sorted(
            boxes,
            key=lambda row: (
                row.get("reading_order") is None,
                int(row.get("reading_order") or 0),
                stable_key(row),
            ),
        )
    elif direction == "right-to-left":
        ordered = sorted(
            boxes,
            key=lambda row: (
                round(_bbox_xywh(row)[1], 3),
                -round(_bbox_xywh(row)[0], 3),
                stable_key(row),
            ),
        )
    elif direction == "left-to-right":
        ordered = sorted(
            boxes,
            key=lambda row: (
                round(_bbox_xywh(row)[1], 3),
                round(_bbox_xywh(row)[0], 3),
                stable_key(row),
            ),
        )
    else:
        ordered = sorted(
            boxes,
            key=lambda row: (
                round(_bbox_xywh(row)[1], 3),
                round(_bbox_xywh(row)[0], 3),
                stable_key(row),
            ),
        )
    return [stable_key(row) for row in ordered], warnings


def _validate_order_ids(ordered_box_ids: list[str], expected_box_ids: list[str]) -> dict[str, Any]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for box_id in ordered_box_ids:
        if box_id in seen and box_id not in duplicates:
            duplicates.append(box_id)
        seen.add(box_id)
    expected = set(expected_box_ids)
    ordered = set(ordered_box_ids)
    missing = sorted(expected - ordered)
    unknown = sorted(ordered - expected)
    warnings: list[str] = []
    warnings.extend(f"duplicate_box_id:{box_id}" for box_id in duplicates)
    warnings.extend(f"missing_box_id:{box_id}" for box_id in missing)
    warnings.extend(f"unknown_box_id:{box_id}" for box_id in unknown)
    return {
        "validation_status": "valid" if not duplicates and not missing and not unknown else "invalid",
        "duplicate_box_ids": duplicates,
        "missing_box_ids": missing,
        "unknown_box_ids": unknown,
        "warnings": warnings,
    }


def _boxes_by_page(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["page_id"]), []).append(row)
    return grouped


def _existing_user_orders(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT page_id, ordered_box_ids_json, direction_preset, warnings_json, validation_status
        FROM manga_reading_order_pages
        WHERE project_id = ? AND user_edited = 1
        """,
        (project_id,),
    ).fetchall()
    return {
        str(row["page_id"]): row_to_dict(row, json_fields=("ordered_box_ids_json", "warnings_json"))
        for row in rows
    }


def _latest_ocr_text_by_box(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.stable_key, o.id AS ocr_result_id, o.raw_text, o.normalized_text,
               o.confidence, o.metadata_json, o.created_at
        FROM manga_ocr_results o
        JOIN manga_boxes b ON b.id = o.box_id
        JOIN manga_pages p ON p.id = b.page_id
        WHERE p.project_id = ?
        ORDER BY o.created_at ASC, o.id ASC
        """,
        (project_id,),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row["stable_key"])] = row_to_dict(row, json_fields=("metadata_json",))
    return latest


def _latest_corrections_by_box(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT stable_box_id, corrected_text, reviewer, reason, created_at
        FROM manga_ocr_corrections
        WHERE project_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (project_id,),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row["stable_box_id"])] = row_to_dict(row)
    return latest


def _page_rows_for_project(conn, *, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, project_id, chapter_id, page_index, image_path, checksum_sha256,
               width, height, status, created_at, updated_at
        FROM manga_pages
        WHERE project_id = ? AND status = 'active'
        ORDER BY page_index ASC, created_at ASC, id ASC
        """,
        (project_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def _build_reading_order_graph(
    *,
    project_id: str,
    project_slug: str,
    run_id: str,
    records: list[dict[str, Any]],
    box_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for record in records:
        previous_node_id: str | None = None
        for order_index, box_id in enumerate(record["ordered_box_ids"], start=1):
            row = box_lookup[box_id]
            node_id = f"{record['page_id']}:{box_id}"
            nodes.append(
                {
                    "id": node_id,
                    "page_id": record["page_id"],
                    "box_id": box_id,
                    "order_index": order_index,
                    "region_type": _box_region_type(row),
                    "bbox": row["bbox_json"],
                }
            )
            if previous_node_id is not None:
                edges.append(
                    {
                        "from": previous_node_id,
                        "to": node_id,
                        "edge_type": "next_box",
                        "page_id": record["page_id"],
                    }
                )
            previous_node_id = node_id
    return {
        "schema_version": MANGA_READING_ORDER_SCHEMA_VERSION,
        "project_id": project_id,
        "project_slug": project_slug,
        "run_id": run_id,
        "nodes": nodes,
        "edges": edges,
    }


def _build_page_context_bundle(
    *,
    project_id: str,
    project_slug: str,
    run_id: str,
    direction_preset: str,
    records: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    box_lookup: dict[str, dict[str, Any]],
    latest_ocr: dict[str, dict[str, Any]],
    corrections: dict[str, dict[str, Any]],
    include_neighbor_hints: bool,
) -> dict[str, Any]:
    pages_by_id = {str(page["id"]): page for page in page_rows}
    ordered_pages = sorted(page_rows, key=lambda page: (int(page["page_index"]), str(page["id"])))
    page_neighbors: dict[str, dict[str, Any]] = {}
    record_counts = {record["page_id"]: len(record["ordered_box_ids"]) for record in records}
    for index, page in enumerate(ordered_pages):
        previous_page = ordered_pages[index - 1] if index > 0 else None
        next_page = ordered_pages[index + 1] if index + 1 < len(ordered_pages) else None
        page_neighbors[str(page["id"])] = {
            "previous": (
                {
                    "page_id": previous_page["id"],
                    "page_index": previous_page["page_index"],
                    "ordered_box_count": record_counts.get(str(previous_page["id"]), 0),
                }
                if previous_page is not None
                else None
            ),
            "next": (
                {
                    "page_id": next_page["id"],
                    "page_index": next_page["page_index"],
                    "ordered_box_count": record_counts.get(str(next_page["id"]), 0),
                }
                if next_page is not None
                else None
            ),
        }

    page_contexts: list[dict[str, Any]] = []
    for record in records:
        page = pages_by_id[record["page_id"]]
        ordered_text: list[dict[str, Any]] = []
        warnings = list(record.get("warnings") or [])
        for order_index, box_id in enumerate(record["ordered_box_ids"], start=1):
            row = box_lookup[box_id]
            correction = corrections.get(box_id)
            ocr = latest_ocr.get(box_id)
            if correction:
                text = str(correction.get("corrected_text") or "")
                text_source = "ocr_correction"
            elif ocr:
                text = str(ocr.get("normalized_text") or ocr.get("raw_text") or "")
                text_source = "ocr"
            else:
                text = ""
                text_source = "missing_ocr"
                warnings.append(f"missing_ocr_text:{box_id}")
            ordered_text.append(
                {
                    "order_index": order_index,
                    "box_id": box_id,
                    "region_type": _box_region_type(row),
                    "ocr_review_state": row.get("ocr_review_state"),
                    "bbox": row["bbox_json"],
                    "speaker_id": row.get("speaker_id"),
                    "text": text,
                    "text_source": text_source,
                    "ocr_confidence": ocr.get("confidence") if ocr else None,
                }
            )
        page_contexts.append(
            {
                "page_id": record["page_id"],
                "page_index": page["page_index"],
                "page_metadata": {
                    "width": page["width"],
                    "height": page["height"],
                    "image_path": page["image_path"],
                    "checksum_sha256": page["checksum_sha256"],
                },
                "direction_preset": record["direction_preset"],
                "ordered_text": ordered_text,
                "region_types": [item["region_type"] for item in ordered_text],
                "neighbor_page_hints": page_neighbors[record["page_id"]] if include_neighbor_hints else None,
                "warnings": sorted(set(warnings)),
            }
        )
    return {
        "schema_version": MANGA_PAGE_CONTEXT_SCHEMA_VERSION,
        "project_id": project_id,
        "project_slug": project_slug,
        "run_id": run_id,
        "direction_preset": direction_preset,
        "config": {"include_neighbor_page_hints": include_neighbor_hints},
        "page_contexts": page_contexts,
        "page_count": len(page_contexts),
        "box_count": sum(len(page["ordered_text"]) for page in page_contexts),
    }


def _write_reading_order_artifacts(
    workspace: Workspace,
    *,
    project_id: str,
    project_slug: str,
    run_id: str,
    direction_preset: str,
    records: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    box_lookup: dict[str, dict[str, Any]],
    latest_ocr: dict[str, dict[str, Any]],
    corrections: dict[str, dict[str, Any]],
    include_neighbor_hints: bool,
) -> dict[str, Any]:
    order_dir = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    order_path = order_dir / "reading_order.json"
    graph_path = order_dir / "reading_order_graph.json"
    context_path = order_dir / "page_context_bundle.json"
    summary_path = order_dir / "reading_order_summary.md"
    order_payload = {
        "schema_version": MANGA_READING_ORDER_SCHEMA_VERSION,
        "project_id": project_id,
        "project_slug": project_slug,
        "run_id": run_id,
        "direction_preset": direction_preset,
        "algorithm_version": MANGA_READING_ORDER_ALGORITHM_VERSION,
        "records": records,
        "record_count": len(records),
        "warnings": sorted({warning for record in records for warning in (record.get("warnings") or [])}),
    }
    graph_payload = _build_reading_order_graph(
        project_id=project_id,
        project_slug=project_slug,
        run_id=run_id,
        records=records,
        box_lookup=box_lookup,
    )
    context_payload = _build_page_context_bundle(
        project_id=project_id,
        project_slug=project_slug,
        run_id=run_id,
        direction_preset=direction_preset,
        records=records,
        page_rows=page_rows,
        box_lookup=box_lookup,
        latest_ocr=latest_ocr,
        corrections=corrections,
        include_neighbor_hints=include_neighbor_hints,
    )
    order_path.write_text(json_dumps(order_payload) + "\n", encoding="utf-8")
    graph_path.write_text(json_dumps(graph_payload) + "\n", encoding="utf-8")
    context_path.write_text(json_dumps(context_payload) + "\n", encoding="utf-8")
    valid_count = len([record for record in records if record["validation_status"] == "valid"])
    edited_count = len([record for record in records if record["user_edited"]])
    warning_count = sum(len(record.get("warnings") or []) for record in records)
    summary_path.write_text(
        "\n".join(
            [
                "# Reading Order Summary",
                "",
                f"- Schema version: `{MANGA_READING_ORDER_SCHEMA_VERSION}`",
                f"- Project: `{project_slug}`",
                f"- Run ID: `{run_id}`",
                f"- Direction preset: `{direction_preset}`",
                f"- Algorithm version: `{MANGA_READING_ORDER_ALGORITHM_VERSION}`",
                f"- Pages ordered: `{len(records)}`",
                f"- Valid pages: `{valid_count}`",
                f"- User-edited pages preserved: `{edited_count}`",
                f"- Warning count: `{warning_count}`",
                f"- Page context bundle: `{_relative_to_workspace(workspace, context_path)}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "reading_order_path": _relative_to_workspace(workspace, order_path),
        "reading_order_graph_path": _relative_to_workspace(workspace, graph_path),
        "page_context_bundle_path": _relative_to_workspace(workspace, context_path),
        "reading_order_summary_path": _relative_to_workspace(workspace, summary_path),
    }


def generate_manga_reading_order(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    direction_preset: str | None = None,
    include_neighbor_hints: bool = False,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    preprocess_manifest = _load_preprocess_manifest(workspace, project_slug=project_slug, run_id=run_id)
    ocr_results_path = _ocr_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "ocr_results.json"
    if not ocr_results_path.exists():
        raise ValueError(f"BLOCKED_OCR_MISSING: OCR results not found for run {run_id}.")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.reading_order.generate",
            status="running",
            stage="generate_reading_order",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "direction_preset": direction_preset,
                "include_neighbor_hints": include_neighbor_hints,
            },
            result_data={},
        )
        if direction_preset is not None:
            direction = _validate_reading_direction(direction_preset)
            _set_project_reading_direction(
                conn,
                project_id=project["id"],
                project_slug=project_slug,
                direction_preset=direction,
                now=now,
            )
        else:
            direction = _get_project_reading_direction(conn, project_id=project["id"])
        page_rows = _page_rows_for_project(conn, project_id=project["id"])
        preprocess_pages = {str(page["page_id"]): page for page in preprocess_manifest.get("pages", [])}
        for page in page_rows:
            preprocess_page = preprocess_pages.get(str(page["id"]))
            if preprocess_page is None:
                continue
            if page.get("width") is None:
                page["width"] = preprocess_page.get("width")
            if page.get("height") is None:
                page["height"] = preprocess_page.get("height")
            page["normalized_artifact"] = preprocess_page.get("normalized_artifact")
            page["preview_artifact"] = preprocess_page.get("preview_artifact")
        box_rows = _reading_order_box_rows(conn, project_id=project["id"])
        if not box_rows:
            raise ValueError("BLOCKED_BOX_IDS: stable box IDs are not available for reading order.")
        boxes_by_page = _boxes_by_page(box_rows)
        box_lookup = {str(row["stable_key"]): row for row in box_rows}
        existing_user_orders = _existing_user_orders(conn, project_id=project["id"])
        records: list[dict[str, Any]] = []
        for page in page_rows:
            page_id = str(page["id"])
            page_boxes = boxes_by_page.get(page_id, [])
            if not page_boxes:
                continue
            expected_box_ids = [str(row["stable_key"]) for row in page_boxes]
            existing = existing_user_orders.get(page_id)
            if existing:
                ordered_box_ids = [str(item) for item in (existing.get("ordered_box_ids_json") or [])]
                effective_direction = str(existing.get("direction_preset") or direction)
                algorithm_warnings = ["user_edited_order_preserved"]
                user_edited = True
            else:
                ordered_box_ids, algorithm_warnings = _infer_order_for_boxes(
                    page_boxes,
                    direction_preset=direction,
                )
                effective_direction = direction
                user_edited = False
            validation = _validate_order_ids(ordered_box_ids, expected_box_ids)
            warnings = sorted(set(algorithm_warnings + validation["warnings"]))
            record = {
                "page_id": page_id,
                "page_index": page["page_index"],
                "ordered_box_ids": ordered_box_ids,
                "direction_preset": effective_direction,
                "algorithm_version": MANGA_READING_ORDER_ALGORITHM_VERSION,
                "user_edited": user_edited,
                "warnings": warnings,
                "validation_status": validation["validation_status"],
            }
            records.append(record)
            conn.execute(
                """
                INSERT INTO manga_reading_order_pages (
                    id, project_id, project_slug, run_id, page_id, page_index,
                    direction_preset, algorithm_version, ordered_box_ids_json,
                    user_edited, warnings_json, validation_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, page_id) DO UPDATE SET
                    project_slug = excluded.project_slug,
                    run_id = excluded.run_id,
                    page_index = excluded.page_index,
                    direction_preset = excluded.direction_preset,
                    algorithm_version = excluded.algorithm_version,
                    ordered_box_ids_json = excluded.ordered_box_ids_json,
                    user_edited = excluded.user_edited,
                    warnings_json = excluded.warnings_json,
                    validation_status = excluded.validation_status,
                    updated_at = excluded.updated_at
                """,
                (
                    new_id("mangareadpage"),
                    project["id"],
                    project_slug,
                    run_id,
                    page_id,
                    page["page_index"],
                    effective_direction,
                    MANGA_READING_ORDER_ALGORITHM_VERSION,
                    json_dumps(ordered_box_ids),
                    1 if user_edited else 0,
                    json_dumps(warnings),
                    validation["validation_status"],
                    now,
                    now,
                ),
            )
        latest_ocr = _latest_ocr_text_by_box(conn, project_id=project["id"])
        corrections = _latest_corrections_by_box(conn, project_id=project["id"])
        paths = _write_reading_order_artifacts(
            workspace,
            project_id=project["id"],
            project_slug=project_slug,
            run_id=run_id,
            direction_preset=direction,
            records=records,
            page_rows=page_rows,
            box_lookup=box_lookup,
            latest_ocr=latest_ocr,
            corrections=corrections,
            include_neighbor_hints=include_neighbor_hints,
        )
        validation_status = "valid" if all(record["validation_status"] == "valid" for record in records) else "invalid"
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "direction_preset": direction,
            "algorithm_version": MANGA_READING_ORDER_ALGORITHM_VERSION,
            "validation_status": validation_status,
            "record_count": len(records),
            "box_count": sum(len(record["ordered_box_ids"]) for record in records),
            "user_edited_preserved_count": len([record for record in records if record["user_edited"]]),
            "include_neighbor_hints": include_neighbor_hints,
            **paths,
        }
        conn.execute(
            """
            INSERT INTO manga_reading_order_runs (
                id, run_id, project_id, project_slug, direction_preset, algorithm_version,
                reading_order_path, graph_path, context_bundle_path, summary_path,
                validation_status, user_edited_count, warning_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangareadrun"),
                run_id,
                project["id"],
                project_slug,
                direction,
                MANGA_READING_ORDER_ALGORITHM_VERSION,
                paths["reading_order_path"],
                paths["reading_order_graph_path"],
                paths["page_context_bundle_path"],
                paths["reading_order_summary_path"],
                validation_status,
                result["user_edited_preserved_count"],
                sum(len(record["warnings"]) for record in records),
                now,
                now,
            ),
        )
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    return {"task_run_id": task_id, **result}


def _load_reading_order_payload(workspace: Workspace, *, project_slug: str, run_id: str) -> dict[str, Any]:
    order_path = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "reading_order.json"
    if not order_path.exists():
        raise ValueError(f"Reading order artifact not found for run {run_id}.")
    return json.loads(order_path.read_text(encoding="utf-8"))


def validate_manga_reading_order(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    payload = _load_reading_order_payload(workspace, project_slug=project_slug, run_id=run_id)
    with connection(workspace.db_path) as conn:
        box_rows = _reading_order_box_rows(conn, project_id=project["id"])
    boxes_by_page = _boxes_by_page(box_rows)
    page_results: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        page_id = str(record.get("page_id") or "")
        expected_box_ids = [str(row["stable_key"]) for row in boxes_by_page.get(page_id, [])]
        ordered_box_ids = [str(item) for item in (record.get("ordered_box_ids") or [])]
        validation = _validate_order_ids(ordered_box_ids, expected_box_ids)
        page_results.append(
            {
                "page_id": page_id,
                "validation_status": validation["validation_status"],
                "duplicate_box_ids": validation["duplicate_box_ids"],
                "missing_box_ids": validation["missing_box_ids"],
                "unknown_box_ids": validation["unknown_box_ids"],
            }
        )
    validation_status = "valid" if all(item["validation_status"] == "valid" for item in page_results) else "invalid"
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "validation_status": validation_status,
        "page_results": page_results,
    }


def import_manga_reading_order(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    order_path: Path,
    reviewer: str = "cli",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if not order_path.exists():
        raise ValueError(f"Reading order file not found: {order_path}")
    try:
        payload = json.loads(order_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Reading order file must contain valid JSON.") from exc
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Reading order import requires a records array.")
    direction = _validate_reading_direction(str(payload.get("direction_preset") or "manual"))
    now = utc_now()
    imported = 0
    audit_path = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "manual_override_audit.jsonl"
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.reading_order.import",
            status="running",
            stage="import_reading_order",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "reviewer": reviewer},
            result_data={},
        )
        _set_project_reading_direction(
            conn,
            project_id=project["id"],
            project_slug=project_slug,
            direction_preset=direction,
            now=now,
        )
        box_rows = _reading_order_box_rows(conn, project_id=project["id"])
        boxes_by_page = _boxes_by_page(box_rows)
        pages_by_index = {
            int(page["page_index"]): str(page["id"])
            for page in _page_rows_for_project(conn, project_id=project["id"])
        }
        for line_index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise ValueError(f"Reading order record {line_index} must be an object.")
            page_id = str(record.get("page_id") or "")
            if not page_id and isinstance(record.get("page_index"), int):
                page_id = pages_by_index.get(int(record["page_index"]), "")
            if not page_id:
                raise ValueError(f"Reading order record {line_index} requires page_id or page_index.")
            expected_box_ids = [str(row["stable_key"]) for row in boxes_by_page.get(page_id, [])]
            ordered_box_ids = [str(item) for item in (record.get("ordered_box_ids") or [])]
            validation = _validate_order_ids(ordered_box_ids, expected_box_ids)
            if validation["validation_status"] != "valid":
                detail = ", ".join(validation["warnings"])
                raise ValueError(f"BLOCKED_READING_ORDER_VALIDATION: page {page_id} has invalid order: {detail}")
            page_index_row = conn.execute(
                "SELECT page_index FROM manga_pages WHERE id = ? AND project_id = ?",
                (page_id, project["id"]),
            ).fetchone()
            if page_index_row is None:
                raise ValueError(f"Reading order record {line_index} references unknown page_id: {page_id}")
            previous = conn.execute(
                """
                SELECT ordered_box_ids_json, direction_preset, user_edited
                FROM manga_reading_order_pages
                WHERE project_id = ? AND page_id = ?
                """,
                (project["id"], page_id),
            ).fetchone()
            previous_order = None
            if previous is not None:
                previous_order = row_to_dict(previous, json_fields=("ordered_box_ids_json",))
            conn.execute(
                """
                INSERT INTO manga_reading_order_pages (
                    id, project_id, project_slug, run_id, page_id, page_index,
                    direction_preset, algorithm_version, ordered_box_ids_json,
                    user_edited, warnings_json, validation_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, page_id) DO UPDATE SET
                    project_slug = excluded.project_slug,
                    run_id = excluded.run_id,
                    page_index = excluded.page_index,
                    direction_preset = excluded.direction_preset,
                    algorithm_version = excluded.algorithm_version,
                    ordered_box_ids_json = excluded.ordered_box_ids_json,
                    user_edited = 1,
                    warnings_json = excluded.warnings_json,
                    validation_status = excluded.validation_status,
                    updated_at = excluded.updated_at
                """,
                (
                    new_id("mangareadpage"),
                    project["id"],
                    project_slug,
                    run_id,
                    page_id,
                    int(page_index_row["page_index"]),
                    direction,
                    MANGA_READING_ORDER_ALGORITHM_VERSION,
                    json_dumps(ordered_box_ids),
                    1,
                    json_dumps([]),
                    "valid",
                    now,
                    now,
                ),
            )
            audit_payload = {
                "schema_version": MANGA_READING_ORDER_SCHEMA_VERSION,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "page_id": page_id,
                "direction_preset": direction,
                "previous_order": previous_order.get("ordered_box_ids_json") if previous_order else None,
                "new_order": ordered_box_ids,
                "reviewer": str(record.get("reviewer") or reviewer),
                "note": record.get("note"),
                "created_at": now,
            }
            _append_jsonl(audit_path, audit_payload)
            conn.execute(
                """
                INSERT INTO manga_reading_order_audit (
                    id, project_id, project_slug, run_id, page_id, direction_preset,
                    previous_order_json, new_order_json, reviewer, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("mangareadaudit"),
                    project["id"],
                    project_slug,
                    run_id,
                    page_id,
                    direction,
                    json_dumps(audit_payload["previous_order"]),
                    json_dumps(ordered_box_ids),
                    audit_payload["reviewer"],
                    audit_payload["note"],
                    now,
                ),
            )
            imported += 1
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "direction_preset": direction,
            "records_imported": imported,
            "manual_override_audit_path": _relative_to_workspace(workspace, audit_path),
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    generated = generate_manga_reading_order(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        direction_preset=None,
    )
    result.update(
        {
            "generated_task_run_id": generated["task_run_id"],
            "reading_order_path": generated["reading_order_path"],
            "reading_order_graph_path": generated["reading_order_graph_path"],
            "page_context_bundle_path": generated["page_context_bundle_path"],
            "reading_order_summary_path": generated["reading_order_summary_path"],
            "validation_status": generated["validation_status"],
        }
    )
    return {"task_run_id": task_id, **result}


def export_manga_reading_order(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    order_dir = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    order_path = order_dir / "reading_order.json"
    graph_path = order_dir / "reading_order_graph.json"
    context_path = order_dir / "page_context_bundle.json"
    summary_path = order_dir / "reading_order_summary.md"
    for path in [order_path, graph_path, context_path, summary_path]:
        if not path.exists():
            raise ValueError(f"Reading order artifact missing: {_relative_to_workspace(workspace, path)}")
    payload = json.loads(order_path.read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "direction_preset": payload.get("direction_preset"),
        "record_count": payload.get("record_count", len(payload.get("records") or [])),
        "records": payload.get("records") or [],
        "reading_order_path": _relative_to_workspace(workspace, order_path),
        "reading_order_graph_path": _relative_to_workspace(workspace, graph_path),
        "page_context_bundle_path": _relative_to_workspace(workspace, context_path),
        "reading_order_summary_path": _relative_to_workspace(workspace, summary_path),
    }


def _translation_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    translation_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "translation"
    translation_dir.mkdir(parents=True, exist_ok=True)
    return translation_dir


def _load_page_context_bundle(workspace: Workspace, *, project_slug: str, run_id: str) -> dict[str, Any]:
    path = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "page_context_bundle.json"
    if not path.exists():
        raise ValueError(f"BLOCKED_PAGE_CONTEXT_MISSING: page context bundle not found for run {run_id}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_PAGE_CONTEXT_SCHEMA_VERSION:
        raise ValueError("BLOCKED_PAGE_CONTEXT_SCHEMA: unsupported page context bundle schema.")
    return payload


def _relative_artifact(workspace: Workspace, path: Path) -> str:
    return _relative_to_workspace(workspace, path)


def _write_json_artifact(workspace: Workspace, path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(payload) + "\n", encoding="utf-8")
    return _relative_artifact(workspace, path)


def _page_contexts_for_translation(
    context_bundle: dict[str, Any],
    *,
    page_index: int | None,
    box_ids: set[str] | None,
) -> list[dict[str, Any]]:
    selected_pages: list[dict[str, Any]] = []
    for page in context_bundle.get("page_contexts") or []:
        if page_index is not None and int(page.get("page_index") or 0) != int(page_index):
            continue
        ordered_text = [
            item
            for item in (page.get("ordered_text") or [])
            if box_ids is None or str(item.get("box_id") or "") in box_ids
        ]
        if not ordered_text:
            continue
        selected = dict(page)
        selected["ordered_text"] = ordered_text
        selected["region_types"] = [item.get("region_type") for item in ordered_text]
        selected_pages.append(selected)
    return selected_pages


def _page_source_text(page_context: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in page_context.get("ordered_text") or []
        if str(item.get("text") or "").strip()
    )


def _memory_only_support_block(hybrid_bundle: dict[str, Any]) -> str:
    lines = ["Project support for this source:"]
    memory_lines = [
        f"- {item.get('source_anchor')} => {item.get('target_value')}"
        for item in hybrid_bundle.get("selected_memory_items") or []
        if item.get("source_anchor") and item.get("target_value")
    ]
    if not memory_lines:
        return ""
    lines.extend(["Memory:", *memory_lines])
    lines.extend(
        [
            "- Use entries only when the exact Chinese source appears in this chunk.",
            "- Treat memory as local terminology/style support only.",
            "- Do not apply unrelated memory entries.",
        ]
    )
    return "\n".join(lines)


def _legacy_manga_desired_max(source_text: str) -> int:
    return max(12, min(120, int(len(source_text) * 1.35)))


def _manga_bubble_capacity(
    box_item: dict[str, Any],
    *,
    source_text: str,
    min_font_size: int = MANGA_MIN_LEGIBLE_FONT_SIZE,
) -> tuple[int, str]:
    legacy = _legacy_manga_desired_max(source_text)
    try:
        bbox = _validate_bbox(
            box_item.get("bbox"),
            box_label=f"translation box {box_item.get('box_id')}",
        )
        _x, _y, width, height = bbox
        Image, _ImageOps = _load_pillow()
        from PIL import ImageDraw

        canvas = Image.new("L", (1, 1), 0)
        draw = ImageDraw.Draw(canvas)
        font, _family, font_source = _load_render_font(
            font_path=None,
            font_family=None,
            font_size=max(1, int(min_font_size)),
        )
        if font_source == "pillow_default":
            return legacy, "char_heuristic"
        max_width = max(1, int(round(width)))
        max_height = max(1, int(round(height)))
        line_height_px = max(1, int(round(min_font_size * 1.15)))
        sample = (
            "Người dịch manga Việt Nam cần lời thoại tự nhiên, ngắn gọn và rõ ràng. "
        )

        def fits(char_count: int) -> bool:
            text = (sample * ((char_count // len(sample)) + 1))[:char_count]
            lines, _long_word_split = _wrap_vietnamese_text(
                text,
                draw=draw,
                font=font,
                max_width=max_width,
                stroke_width=0,
            )
            return len(lines) * line_height_px <= max_height

        low, high = 0, 512
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        return max(1, low), "geometry"
    except (ImportError, OSError, ValueError):
        return legacy, "char_heuristic"


def _build_manga_translation_prompt(
    *,
    project_slug: str,
    page_context: dict[str, Any],
    box_item: dict[str, Any],
    dictionary_block: str,
    memory_block: str,
) -> str:
    support_sections = []
    if dictionary_block:
        support_sections.append(dictionary_block)
    if memory_block:
        support_sections.append(memory_block)
    support_text = "\n\n".join(support_sections)
    ordered_neighbors = [
        {
            "order_index": item.get("order_index"),
            "box_id": item.get("box_id"),
            "region_type": item.get("region_type"),
            "text": item.get("text"),
        }
        for item in page_context.get("ordered_text") or []
    ]
    source_text = str(box_item.get("text") or "")
    desired_max_chars, capacity_source = _manga_bubble_capacity(
        box_item,
        source_text=source_text,
    )
    prompt = {
        "mode": "phase9m1_manga_dialogue_translation",
        "project_slug": project_slug,
        "target_language": "vi",
        "instructions": [
            "Translate only the selected OCR text into Vietnamese.",
            "Write natural, concise Vietnamese manga dialogue rather than stiff literal prose.",
            "Preserve character voice, humor, interjections, names, and emotional force.",
            "Prefer short spoken lines that fit the bubble; remove avoidable repetition.",
            "Use conservative neutral Vietnamese xung ho when speaker or relationship evidence is unknown.",
            "Do not invent relationships, honorifics, names, or visual context.",
            "Avoid formal wording unless the character and scene clearly require it.",
            "Use page reading order and neighboring OCR text as context.",
            "Use approved dictionary and approved memory support only when applicable.",
            "Approved rules are verifier-only and must not be used in this prompt.",
            "Raw NLP cache is not allowed in this prompt.",
        ],
        "page_context": {
            "page_id": page_context.get("page_id"),
            "page_index": page_context.get("page_index"),
            "direction_preset": page_context.get("direction_preset"),
            "ordered_neighbors": ordered_neighbors,
        },
        "selected_box": {
            "box_id": box_item.get("box_id"),
            "order_index": box_item.get("order_index"),
            "region_type": box_item.get("region_type"),
            "speaker_hint": box_item.get("speaker_hint") or box_item.get("speaker_id"),
            "source_text": source_text,
            "desired_max_characters": desired_max_chars,
            "desired_max_source": capacity_source,
        },
        "approved_support": support_text,
        "security": {
            "approved_rules_included": False,
            "raw_nlp_cache_included": False,
            "cloud_image_upload": False,
        },
    }
    return json_dumps(prompt)


def _manga_dialogue_style_profile() -> dict[str, Any]:
    return {
        "schema_version": "phase9m1.manga_dialogue_style_profile.v1",
        "target_language": "vi",
        "profile_id": "natural_concise_vietnamese_manga",
        "principles": [
            "natural_spoken_vietnamese",
            "concise_bubble_text",
            "preserve_character_voice",
            "consistent_names",
            "conservative_xung_ho_when_speaker_unknown",
            "no_unjustified_relationship_inference",
            "avoid_stiff_literal_wording",
        ],
        "approved_rules_included": False,
        "raw_nlp_cache_included": False,
    }


def _load_speaker_assignments(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_assignments = payload.get("assignments") if isinstance(payload, dict) else None
    if not isinstance(raw_assignments, dict):
        raw_assignments = payload if isinstance(payload, dict) else {}
    assignments: dict[str, str] = {}
    for box_id, item in raw_assignments.items():
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker_label") or item.get("speaker") or "").strip()
        reviewer = str(item.get("reviewer") or "").strip()
        if speaker and reviewer:
            assignments[str(box_id)] = speaker
    return assignments


def _manga_dialogue_style_audit(
    results: list[dict[str, Any]],
    *,
    speaker_assignments: dict[str, str] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    pronouns_by_page_without_speaker: dict[str, set[str]] = {}
    pronouns_by_speaker: dict[tuple[str, str], set[str]] = {}
    pronouns = {"tôi", "ta", "tao", "mày", "bạn", "cậu", "anh", "chị", "em", "ông", "bà"}
    stiff_markers = ("do đó", "tuy nhiên", "vì vậy", "xin hãy", "quý vị")
    assignments = speaker_assignments or {}

    def conflicting(used: set[str]) -> bool:
        return ({"tôi", "tao"} <= used) or ({"bạn", "mày"} <= used)

    for result in results:
        target = str(result.get("translated_text") or "").strip()
        source = str(result.get("source_text") or "").strip()
        box_id = str(result.get("box_id") or "")
        page_id = str(result.get("page_id") or "")
        speaker = str(
            result.get("speaker_id")
            or result.get("speaker_hint")
            or assignments.get(box_id)
            or ""
        ).strip()
        lowered = target.lower()
        used = {token for token in pronouns if token in lowered.split()}
        if used:
            if conflicting(used):
                issues.append(
                    {
                        "kind": "xung_ho_consistency_failed",
                        "page_id": page_id,
                        "box_id": box_id,
                        "speaker": speaker or None,
                        "severity": "blocker",
                    }
                )
            if speaker:
                pronouns_by_speaker.setdefault((page_id, speaker), set()).update(used)
            else:
                pronouns_by_page_without_speaker.setdefault(page_id, set()).update(used)
        desired_max, capacity_source = _manga_bubble_capacity(
            result,
            source_text=source,
        )
        over_capacity = (
            len(target) > desired_max
            if capacity_source == "geometry"
            else len(target) > max(desired_max + 20, int(desired_max * 1.6))
        )
        if over_capacity:
            issues.append(
                {
                    "kind": "too_long_for_bubble",
                    "box_id": box_id,
                    "page_id": page_id,
                    "severity": "blocker",
                    "translated_char_count": len(target),
                    "desired_max_characters": desired_max,
                    "desired_max_source": capacity_source,
                }
            )
        if any(marker in lowered for marker in stiff_markers) and len(target) > 45:
            issues.append(
                {
                    "kind": "stiff_or_overformal_phrase",
                    "box_id": box_id,
                    "page_id": page_id,
                    "severity": "warning",
                }
            )
    for (page_id, speaker), used in sorted(pronouns_by_speaker.items()):
        if not conflicting(used):
            continue
        issues.append(
            {
                "kind": "xung_ho_consistency_failed",
                "page_id": page_id,
                "box_id": None,
                "speaker": speaker,
                "severity": "blocker",
            }
        )
    for page_id, used in sorted(pronouns_by_page_without_speaker.items()):
        if not conflicting(used):
            continue
        issues.append(
            {
                "kind": "xung_ho_consistency_failed",
                "page_id": page_id,
                "box_id": None,
                "speaker": None,
                "severity": "blocker",
            }
        )
    blockers = [issue for issue in issues if issue["severity"] == "blocker"]
    return {
        "schema_version": "phase9m1.dialogue_style_audit.v1",
        "validation_status": "blocked" if blockers else "pass",
        "blocker_count": len(blockers),
        "warning_count": len(issues) - len(blockers),
        "issues": issues,
        "manual_review_required": True,
    }


def _translation_qa(results: list[dict[str, Any]], expected_box_ids: list[str]) -> dict[str, Any]:
    result_ids = [str(result.get("box_id") or "") for result in results]
    result_set = set(result_ids)
    expected_set = set(expected_box_ids)
    duplicates = sorted({box_id for box_id in result_ids if result_ids.count(box_id) > 1})
    missing = sorted(expected_set - result_set)
    extra = sorted(result_set - expected_set)
    untranslated = sorted(
        str(result.get("box_id"))
        for result in results
        if not str(result.get("translated_text") or "").strip()
    )
    source_missing = sorted(
        str(result.get("box_id"))
        for result in results
        if not str(result.get("source_text") or "").strip()
    )
    issues: list[dict[str, Any]] = []
    issues.extend({"kind": "missing_translation", "box_id": box_id} for box_id in missing)
    issues.extend({"kind": "extra_translation", "box_id": box_id} for box_id in extra)
    issues.extend({"kind": "duplicate_translation", "box_id": box_id} for box_id in duplicates)
    issues.extend({"kind": "empty_translated_text", "box_id": box_id} for box_id in untranslated)
    issues.extend({"kind": "empty_source_text", "box_id": box_id} for box_id in source_missing)
    return {
        "schema_version": MANGA_TRANSLATION_QA_SCHEMA_VERSION,
        "validation_status": "valid" if not issues else "invalid",
        "expected_box_count": len(expected_box_ids),
        "translated_box_count": len(results),
        "missing_box_ids": missing,
        "extra_box_ids": extra,
        "duplicate_box_ids": duplicates,
        "untranslated_box_ids": untranslated,
        "empty_source_box_ids": source_missing,
        "issues": issues,
    }


def _manga_model_run(
    conn,
    *,
    task_run_id: str,
    provider_key: str,
    provider_type: str,
    base_url: str,
    model: str,
    prompt: str,
    response: str,
    status: str = "success",
) -> str:
    model_run_id = new_id("modelrun")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO model_runs (
            id, task_run_id, provider_key, adapter_type, base_url, model_name,
            prompt_hash, input_tokens, output_tokens, cost_estimate, status,
            started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_run_id,
            task_run_id,
            provider_key,
            provider_type,
            base_url,
            model,
            _sha256_text(prompt),
            max(1, len(prompt) // 4),
            max(1, len(response) // 4),
            0.0,
            status,
            now,
            now,
        ),
    )
    return model_run_id


def _load_gui_provider_model_pair(workspace: Workspace) -> tuple[str, str | None]:
    path = workspace.config_dir / "gui_provider.local.json"
    if not path.exists():
        raise ValueError(f"BLOCKED_PROVIDER_CONFIG: GUI provider config not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    primary = str(payload.get("primary_model") or "").strip()
    fallback = str(payload.get("fallback_model") or "").strip() or None
    if not primary:
        raise ValueError("BLOCKED_PROVIDER_CONFIG: GUI provider primary_model is missing.")
    if not payload.get("api_key"):
        raise ValueError("BLOCKED_PROVIDER_CONFIG: GUI provider API key is missing.")
    return primary, fallback


def _redacted_gui_provider_snapshot(workspace: Workspace) -> dict[str, Any]:
    path = workspace.config_dir / "gui_provider.local.json"
    if not path.exists():
        return {"config_path": str(path), "configured": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "config_path": str(path),
        "configured": True,
        "provider_name": payload.get("provider_name"),
        "provider_type": payload.get("provider_type"),
        "base_url": payload.get("base_url"),
        "route": payload.get("route") or "chat/completions",
        "primary_model": payload.get("primary_model"),
        "fallback_model": payload.get("fallback_model"),
        "api_key_configured": bool(payload.get("api_key")),
        "api_key_value": None,
    }


def _manga_real_provider_call(
    *,
    provider: Any,
    provider_key: str,
    project_id: str,
    run_id: str,
    primary_model: str,
    fallback_model: str | None,
    prompt: str,
    page_id: str,
    box_id: str,
    model_usage_path: Path,
) -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                "Translate manga OCR into concise, natural spoken Vietnamese. Preserve "
                "character voice and use conservative xung ho when relationships are "
                "unknown. Return only the selected box translation, without markdown."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    models = [primary_model]
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)
    last_exc: Exception | None = None
    for index, model in enumerate(models):
        try:
            raw = chat_completion_with_provider_retry(
                provider,
                model=model,
                messages=messages,
                max_tokens=320,
                retry_attempts=1,
                retry_context={"phase": "phase9l_manga_translation", "page_id": page_id, "box_id": box_id},
            )
            _append_jsonl(
                model_usage_path,
                {
                    "timestamp": utc_now(),
                    "call_type": "phase9l_manga_box_translation",
                    "project_id": project_id,
                    "run_id": run_id,
                    "page_id": page_id,
                    "box_id": box_id,
                    "provider": provider_key,
                    "provider_type": str(provider.type),
                    "provider_name": provider_key,
                    "requested_model": primary_model,
                    "chosen_model": model,
                    "model": model,
                    "route": f"{provider.base_url}/{provider.route}",
                    "fallback_model_used": index > 0,
                    "fallback_used": index > 0,
                    "route_status": "fallback_runtime_success" if index > 0 else "ok",
                    "error_class": None,
                    "request_id": None,
                    "input_char_count": len(prompt),
                    "output_char_count": len(raw or ""),
                },
            )
            return {
                "raw": raw,
                "model": model,
                "fallback_used": index > 0,
                "route": f"{provider.base_url}/{provider.route}",
            }
        except Exception as exc:
            last_exc = exc
            error_class = classify_provider_error(exc)
            _append_jsonl(
                model_usage_path,
                {
                    "timestamp": utc_now(),
                    "call_type": "phase9l_manga_box_translation",
                    "project_id": project_id,
                    "run_id": run_id,
                    "page_id": page_id,
                    "box_id": box_id,
                    "provider": provider_key,
                    "provider_type": str(provider.type),
                    "provider_name": provider_key,
                    "requested_model": primary_model,
                    "chosen_model": model,
                    "model": model,
                    "route": f"{provider.base_url}/{provider.route}",
                    "fallback_model_used": index > 0,
                    "fallback_used": index > 0,
                    "route_status": error_class.get("provider_error_type") or "provider_error",
                    "error_class": error_class,
                    "request_id": None,
                    "input_char_count": len(prompt),
                    "output_char_count": 0,
                },
            )
            if error_class.get("http_status") == 404 and index + 1 < len(models):
                continue
            raise ValueError(
                "BLOCKED_PROVIDER_OR_ENVIRONMENT: "
                f"{error_class.get('error_message_masked') or str(exc)}"
            ) from exc
    if last_exc is not None:
        raise last_exc
    raise ValueError("BLOCKED_PROVIDER_OR_ENVIRONMENT: provider call failed without a response.")


def run_manga_translation(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    provider_key: str = "mock",
    page_index: int | None = None,
    box_ids: list[str] | None = None,
    dictionary_max_entries: int = 8,
    memory_max_items: int = 6,
    support_max_chars: int = 1200,
) -> dict[str, Any]:
    if provider_key not in MANGA_TRANSLATION_PROVIDER_MODES:
        raise ValueError(
            "Manga translation supports providers `mock` and `gui_saved` in Phase 9L; "
            "other real provider calls are deferred."
        )
    if dictionary_max_entries < 0 or memory_max_items < 0:
        raise ValueError("Dictionary and memory limits cannot be negative.")
    real_provider = None
    primary_model = "mock-deterministic-v1"
    fallback_model: str | None = None
    provider_snapshot: dict[str, Any] = {"provider_mode": provider_key, "mock_mode": provider_key == "mock"}
    model_policy: dict[str, Any] | None = None
    if provider_key == "gui_saved":
        primary_model, fallback_model = _load_gui_provider_model_pair(workspace)
        real_provider = load_production_provider(workspace, "gui_saved")
        model_policy = build_rollout_model_policy(
            provider_key="gui_saved",
            primary_model=primary_model,
            fallback_model=fallback_model,
            chosen_model=primary_model,
            fallback_model_used=False,
            primary_status={"status": "selected_for_runtime_call", "ok": True},
            fallback_status={"status": "configured" if fallback_model else "not_configured", "model": fallback_model},
        )
        provider_snapshot = _redacted_gui_provider_snapshot(workspace)
    project = get_project_by_slug(workspace, project_slug)
    context_bundle = _load_page_context_bundle(workspace, project_slug=project_slug, run_id=run_id)
    selected_box_ids = set(str(item) for item in box_ids) if box_ids else None
    page_contexts = _page_contexts_for_translation(
        context_bundle,
        page_index=page_index,
        box_ids=selected_box_ids,
    )
    if not page_contexts:
        raise ValueError("BLOCKED_BOX_TRANSLATION_LINK: no page-context boxes matched the translation selection.")

    translation_dir = _translation_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    support_dir = translation_dir / "support"
    prompt_context_path = translation_dir / "prompt_context_bundle.json"
    requests_path = translation_dir / "box_translation_requests.jsonl"
    results_path = translation_dir / "translation_results.json"
    summary_path = translation_dir / "translation_summary.md"
    qa_path = translation_dir / "translation_qa.json"
    style_profile_path = translation_dir / "manga_dialogue_style_profile.json"
    voice_glossary_path = translation_dir / "character_voice_glossary.json"
    style_audit_path = translation_dir / "dialogue_style_audit.json"
    xung_ho_candidates_path = translation_dir / "xung_ho_memory_candidates.jsonl"
    provider_usage_path = translation_dir / "model_usage.jsonl"
    requests_path.write_text("", encoding="utf-8")
    provider_usage_path.write_text("", encoding="utf-8")
    if not xung_ho_candidates_path.exists():
        xung_ho_candidates_path.write_text("", encoding="utf-8")
    style_profile_rel = _write_json_artifact(
        workspace,
        style_profile_path,
        _manga_dialogue_style_profile(),
    )
    voice_glossary_rel = _write_json_artifact(
        workspace,
        voice_glossary_path,
        {
            "schema_version": "phase9m1.character_voice_glossary.v1",
            "project_slug": project_slug,
            "entries": [],
            "speaker_policy": "unknown_uses_conservative_neutral_vietnamese",
            "auto_promote_corrections": False,
        },
    )

    page_support: dict[str, dict[str, Any]] = {}
    request_records: list[dict[str, Any]] = []
    result_records: list[dict[str, Any]] = []
    expected_box_ids: list[str] = []
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.translation",
            status="running",
            stage="translate_boxes",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "provider": provider_key,
                "page_index": page_index,
                "box_ids": sorted(selected_box_ids) if selected_box_ids else None,
                "use_approved_rules": False,
                "raw_nlp_cache": False,
                "provider_snapshot": provider_snapshot,
                "model_policy": model_policy,
            },
            result_data={},
        )
        for page in page_contexts:
            page_id = str(page["page_id"])
            source_text = _page_source_text(page)
            dictionary_bundle = build_dictionary_prompt_support(
                workspace,
                project_slug,
                source_text,
                max_entries=max(1, dictionary_max_entries),
                max_chars=max(1, support_max_chars),
            )
            memory_bundle = build_hybrid_prompt_support(
                workspace,
                project_slug,
                source_text,
                mode="production",
                max_dictionary_entries=0,
                max_memory_items=memory_max_items,
                use_approved_rules=False,
                max_rule_hints=0,
                max_support_chars=max(1, support_max_chars),
            )
            dictionary_artifact = _write_json_artifact(
                workspace,
                support_dir / f"page_{int(page.get('page_index') or 0):04d}_dictionary_bundle.json",
                dictionary_bundle,
            )
            memory_payload = {
                "schema_version": "phase9f.memory_prompt_context_bundle.v1",
                "project_slug": project_slug,
                "source_sha256": memory_bundle.get("source_sha256"),
                "block_text": _memory_only_support_block(memory_bundle),
                "block_rendered": bool(memory_bundle.get("selected_memory_items")),
                "selected_memory_items": memory_bundle.get("selected_memory_items") or [],
                "excluded_memory_rows": (memory_bundle.get("retrieval_report") or {}).get("excluded_memory_rows") or [],
                "inactive_or_negative_memory_matches": (memory_bundle.get("retrieval_report") or {}).get("inactive_or_negative_memory_matches") or [],
                "selected_rule_items": memory_bundle.get("selected_rule_items") or [],
                "rules_enabled": False,
            }
            memory_artifact = _write_json_artifact(
                workspace,
                support_dir / f"page_{int(page.get('page_index') or 0):04d}_memory_bundle.json",
                memory_payload,
            )
            page_support[page_id] = {
                "dictionary_bundle_artifact": dictionary_artifact,
                "memory_bundle_artifact": memory_artifact,
                "dictionary_selected_count": len(dictionary_bundle.get("selected_hits") or []),
                "memory_selected_count": len(memory_payload.get("selected_memory_items") or []),
                "approved_rules_included": False,
                "raw_nlp_cache_included": False,
            }
            for item in page.get("ordered_text") or []:
                box_id = str(item.get("box_id") or "")
                expected_box_ids.append(box_id)
                source = str(item.get("text") or "")
                prompt = _build_manga_translation_prompt(
                    project_slug=project_slug,
                    page_context=page,
                    box_item=item,
                    dictionary_block=str(dictionary_bundle.get("block_text") or ""),
                    memory_block=str(memory_payload.get("block_text") or ""),
                )
                if provider_key == "mock":
                    logged = log_mock_model_run(
                        conn,
                        task_run_id=task_id,
                        provider_key=provider_key,
                        prompt=prompt,
                    )
                    response = logged["response"]
                    translated_text = f"[mock-vi:{response['output']}] {source}".strip()
                    model_name = str(response.get("model") or "mock-deterministic-v1")
                    route = "mock://local"
                    fallback_used = False
                    provider_type = "mock"
                    model_run_id = logged["model_run_id"]
                    prompt_sha256 = str(response.get("prompt_hash") or _sha256_text(prompt))
                else:
                    if real_provider is None:
                        raise ValueError("BLOCKED_PROVIDER_OR_ENVIRONMENT: real provider is not configured.")
                    call = _manga_real_provider_call(
                        provider=real_provider,
                        provider_key=provider_key,
                        project_id=str(project["id"]),
                        run_id=run_id,
                        primary_model=primary_model,
                        fallback_model=fallback_model,
                        prompt=prompt,
                        page_id=page_id,
                        box_id=box_id,
                        model_usage_path=provider_usage_path,
                    )
                    translated_text = str(call["raw"] or "").strip()
                    model_name = str(call["model"])
                    route = str(call["route"])
                    fallback_used = bool(call["fallback_used"])
                    provider_type = str(real_provider.type)
                    model_run_id = _manga_model_run(
                        conn,
                        task_run_id=task_id,
                        provider_key=provider_key,
                        provider_type=provider_type,
                        base_url=str(real_provider.base_url),
                        model=model_name,
                        prompt=prompt,
                        response=translated_text,
                    )
                    prompt_sha256 = _sha256_text(prompt)
                prompt_context_artifact = _relative_artifact(workspace, prompt_context_path)
                record = {
                    "page_id": page_id,
                    "page_index": page.get("page_index"),
                    "box_id": box_id,
                    "order_index": item.get("order_index"),
                    "region_type": item.get("region_type"),
                    "bbox": item.get("bbox"),
                    "speaker_id": item.get("speaker_id"),
                    "speaker_hint": item.get("speaker_hint"),
                    "source_text": source,
                    "translated_text": translated_text,
                    "provider_type": provider_type,
                    "provider_name": provider_key,
                    "model": model_name,
                    "route": route,
                    "fallback_used": fallback_used,
                    "dictionary_bundle_artifact": dictionary_artifact,
                    "memory_bundle_artifact": memory_artifact,
                    "prompt_context_bundle_artifact": prompt_context_artifact,
                    "model_run_id": model_run_id,
                    "mock_mode": provider_key == "mock",
                }
                request_record = {
                    "schema_version": MANGA_TRANSLATION_SCHEMA_VERSION,
                    "run_id": run_id,
                    "page_id": page_id,
                    "box_id": box_id,
                    "provider_name": provider_key,
                    "provider_type": provider_type,
                    "model": model_name,
                    "route": route,
                    "prompt_sha256": prompt_sha256,
                    "source_char_count": len(source),
                    "approved_rules_included": False,
                    "raw_nlp_cache_included": False,
                }
                _append_jsonl(requests_path, request_record)
                if provider_key == "mock":
                    _append_jsonl(
                        provider_usage_path,
                        {
                            "timestamp": now,
                            "project_id": project["id"],
                            "run_id": run_id,
                            "page_id": page_id,
                            "box_id": box_id,
                            "provider_type": "mock",
                            "provider_name": provider_key,
                            "model": model_name,
                            "route": route,
                            "fallback_used": False,
                            "request_id": model_run_id,
                            "input_char_count": len(prompt),
                            "output_char_count": len(translated_text),
                        },
                    )
                request_records.append(request_record)
                result_records.append(record)
                conn.execute(
                    """
                    INSERT INTO manga_box_translations (
                        id, box_id, translation_text, provider_name, model_run_id, metadata_json, created_at
                    )
                    SELECT ?, b.id, ?, ?, ?, ?, ?
                    FROM manga_boxes b
                    JOIN manga_pages p ON p.id = b.page_id
                    WHERE p.project_id = ? AND b.stable_key = ? AND b.deleted = 0
                    """,
                    (
                        new_id("mangatrans"),
                        translated_text,
                        provider_key,
                        model_run_id,
                        json_dumps(record),
                        now,
                        project["id"],
                        box_id,
                    ),
                )

        prompt_context_payload = {
            "schema_version": MANGA_TRANSLATION_CONTEXT_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "provider_mode": provider_key,
            "stable_prompt_posture": "phase9m1_natural_vietnamese_manga_dialogue_v1",
            "manga_dialogue_style_profile_artifact": style_profile_rel,
            "character_voice_glossary_artifact": voice_glossary_rel,
            "hybrid_prompt_enabled": True,
            "approved_dictionary_enabled": True,
            "approved_memory_enabled": True,
            "approved_rules_included": False,
            "use_approved_rules": False,
            "raw_nlp_cache_included": False,
            "raw_nlp_cache_artifacts_included": [],
            "page_context_bundle_artifact": _relative_artifact(
                workspace,
                _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "page_context_bundle.json",
            ),
            "page_support": page_support,
            "page_contexts": page_contexts,
            "requests": request_records,
            "provider_snapshot": provider_snapshot,
            "model_policy": model_policy,
        }
        prompt_context_rel = _write_json_artifact(workspace, prompt_context_path, prompt_context_payload)
        for record in result_records:
            record["prompt_context_bundle_artifact"] = prompt_context_rel
        qa_payload = _translation_qa(result_records, expected_box_ids)
        speaker_assignments_path = translation_dir / "speaker_assignments.json"
        style_audit = _manga_dialogue_style_audit(
            result_records,
            speaker_assignments=_load_speaker_assignments(speaker_assignments_path),
        )
        style_audit["speaker_assignments_artifact"] = (
            _relative_artifact(workspace, speaker_assignments_path)
            if speaker_assignments_path.exists()
            else None
        )
        style_audit["enforced"] = provider_key != "mock"
        style_audit_rel = _write_json_artifact(workspace, style_audit_path, style_audit)
        qa_payload["dialogue_style_validation_status"] = style_audit["validation_status"]
        qa_payload["dialogue_style_blocker_count"] = style_audit["blocker_count"]
        qa_payload["dialogue_style_warning_count"] = style_audit["warning_count"]
        qa_payload["dialogue_style_audit_artifact"] = style_audit_rel
        if style_audit["blocker_count"] and style_audit["enforced"]:
            qa_payload["validation_status"] = "invalid"
        results_payload = {
            "schema_version": MANGA_TRANSLATION_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "provider_mode": provider_key,
            "mock_mode": provider_key == "mock",
            "provider_snapshot": provider_snapshot,
            "model_policy": model_policy,
            "results": result_records,
            "result_count": len(result_records),
            "prompt_context_bundle_artifact": prompt_context_rel,
            "box_translation_requests_artifact": _relative_artifact(workspace, requests_path),
            "translation_qa_artifact": _relative_artifact(workspace, qa_path),
            "manga_dialogue_style_profile_artifact": style_profile_rel,
            "character_voice_glossary_artifact": voice_glossary_rel,
            "dialogue_style_audit_artifact": style_audit_rel,
            "xung_ho_memory_candidates_artifact": _relative_artifact(
                workspace, xung_ho_candidates_path
            ),
            "approved_rules_used": False,
            "raw_nlp_cache_injected": False,
        }
        results_rel = _write_json_artifact(workspace, results_path, results_payload)
        qa_rel = _write_json_artifact(workspace, qa_path, qa_payload)
        summary_path.write_text(
            "\n".join(
                [
                    "# Manga Translation Summary",
                    "",
                    f"- Schema version: `{MANGA_TRANSLATION_SCHEMA_VERSION}`",
                    f"- Project: `{project_slug}`",
                    f"- Run ID: `{run_id}`",
                    f"- Provider mode: `{provider_key}`",
                    f"- Mock mode: `{provider_key == 'mock'}`",
                    f"- Results: `{len(result_records)}`",
                    f"- QA status: `{qa_payload['validation_status']}`",
                    f"- Dialogue style blockers: `{style_audit['blocker_count']}`",
                    f"- Dialogue style warnings: `{style_audit['warning_count']}`",
                    "- Approved rules in prompts: `false`",
                    "- Raw NLP cache in prompts: `false`",
                    f"- Prompt context bundle: `{prompt_context_rel}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "provider_mode": provider_key,
            "mock_mode": provider_key == "mock",
            "result_count": len(result_records),
            "qa_status": qa_payload["validation_status"],
            "prompt_context_bundle_path": prompt_context_rel,
            "box_translation_requests_path": _relative_artifact(workspace, requests_path),
            "translation_results_path": results_rel,
            "translation_summary_path": _relative_artifact(workspace, summary_path),
            "translation_qa_path": qa_rel,
            "manga_dialogue_style_profile_path": style_profile_rel,
            "character_voice_glossary_path": voice_glossary_rel,
            "dialogue_style_audit_path": style_audit_rel,
            "xung_ho_memory_candidates_path": _relative_artifact(
                workspace, xung_ho_candidates_path
            ),
            "model_usage_path": _relative_artifact(workspace, provider_usage_path),
            "approved_rules_included": False,
            "raw_nlp_cache_included": False,
        }
        conn.execute(
            """
            INSERT INTO manga_translation_runs (
                id, run_id, project_id, project_slug, provider_name, provider_type, model,
                prompt_context_path, requests_path, results_path, qa_path, summary_path,
                validation_status, result_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangatransrun"),
                run_id,
                project["id"],
                project_slug,
                provider_key,
                "mock" if provider_key == "mock" else "openai_chat_compatible",
                "mock-deterministic-v1" if provider_key == "mock" else primary_model,
                result["prompt_context_bundle_path"],
                result["box_translation_requests_path"],
                result["translation_results_path"],
                result["translation_qa_path"],
                result["translation_summary_path"],
                result["qa_status"],
                result["result_count"],
                now,
                now,
            ),
        )
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    return {"task_run_id": task_id, **result}


def export_manga_translation(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    translation_dir = _translation_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    paths = {
        "prompt_context_bundle_path": translation_dir / "prompt_context_bundle.json",
        "box_translation_requests_path": translation_dir / "box_translation_requests.jsonl",
        "translation_results_path": translation_dir / "translation_results.json",
        "translation_summary_path": translation_dir / "translation_summary.md",
        "translation_qa_path": translation_dir / "translation_qa.json",
    }
    for path in paths.values():
        if not path.exists():
            raise ValueError(f"Translation artifact missing: {_relative_artifact(workspace, path)}")
    results = json.loads(paths["translation_results_path"].read_text(encoding="utf-8"))
    qa = json.loads(paths["translation_qa_path"].read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "result_count": results.get("result_count", len(results.get("results") or [])),
        "qa_status": qa.get("validation_status"),
        **{key: _relative_artifact(workspace, path) for key, path in paths.items()},
    }


def validate_manga_translation(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    context_bundle = _load_page_context_bundle(workspace, project_slug=project_slug, run_id=run_id)
    expected_box_ids = [
        str(item.get("box_id") or "")
        for page in context_bundle.get("page_contexts") or []
        for item in page.get("ordered_text") or []
        if item.get("box_id")
    ]
    translation_dir = _translation_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    results_path = translation_dir / "translation_results.json"
    if not results_path.exists():
        raise ValueError(f"Translation results artifact not found for run {run_id}.")
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    qa = _translation_qa(payload.get("results") or [], expected_box_ids)
    qa_path = translation_dir / "translation_qa.json"
    qa_rel = _write_json_artifact(workspace, qa_path, qa)
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "validation_status": qa["validation_status"],
        "translation_qa_path": qa_rel,
        "issues": qa["issues"],
    }


def import_manga_translation_corrections(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    corrections_path: Path,
    reviewer: str = "cli",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if not corrections_path.exists():
        raise ValueError(f"Translation corrections file not found: {corrections_path}")
    translation_dir = _translation_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    review_path = translation_dir / "translation_corrections.jsonl"
    imported = 0
    now = utc_now()
    results_path = translation_dir / "translation_results.json"
    results_payload = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {"results": []}
    results_by_box = {str(result.get("box_id")): result for result in results_payload.get("results") or []}
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.translation.corrections.import",
            status="running",
            stage="import_translation_corrections",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "reviewer": reviewer},
            result_data={},
        )
        for line_no, line in enumerate(corrections_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Correction line {line_no} must be valid JSON.") from exc
            box_id = str(payload.get("box_id") or "")
            corrected_text = str(payload.get("corrected_text") or "")
            if not box_id or not corrected_text:
                raise ValueError(f"Correction line {line_no} requires box_id and corrected_text.")
            previous = str((results_by_box.get(box_id) or {}).get("translated_text") or "")
            correction = {
                "schema_version": MANGA_TRANSLATION_SCHEMA_VERSION,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "box_id": box_id,
                "previous_translation": previous,
                "corrected_text": corrected_text,
                "reviewer": str(payload.get("reviewer") or reviewer),
                "reason": payload.get("reason"),
                "created_at": now,
            }
            _append_jsonl(review_path, correction)
            conn.execute(
                """
                INSERT INTO manga_translation_corrections (
                    id, project_id, project_slug, run_id, stable_box_id,
                    previous_translation, corrected_text, reviewer, reason,
                    correction_artifact, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("mangatranscorr"),
                    project["id"],
                    project_slug,
                    run_id,
                    box_id,
                    previous,
                    corrected_text,
                    correction["reviewer"],
                    correction["reason"],
                    _relative_artifact(workspace, review_path),
                    now,
                ),
            )
            imported += 1
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "corrections_imported": imported,
            "translation_corrections_path": _relative_artifact(workspace, review_path),
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


class FillCleaningAdapter:
    adapter_id = "fill_local"
    adapter_version = "phase9g.fill.v1"
    execution_mode = "local"

    def clean(
        self,
        *,
        workspace: Workspace,
        image_path: Path,
        mask_path: Path,
        output_path: Path,
        fill_color: tuple[int, int, int],
    ) -> tuple[Path | None, list[str], str]:
        Image, _ImageOps = _load_pillow()
        with Image.open(_windows_long_path(image_path)) as image:
            base = image.convert("RGB")
            with Image.open(_windows_long_path(mask_path)) as mask:
                normalized_mask = mask.convert("L")
                fill = Image.new("RGB", base.size, fill_color)
                cleaned = base.copy()
                cleaned.paste(fill, (0, 0), normalized_mask)
                _save_png(cleaned, output_path, force=True)
        return output_path, [], "success"


class OpenCvInpaintCleaningAdapter:
    adapter_id = "opencv_inpaint"
    adapter_version = "phase9m1.opencv_inpaint.v2"
    execution_mode = "local_optional"

    def clean(
        self,
        *,
        workspace: Workspace,
        image_path: Path,
        mask_path: Path,
        output_path: Path,
        fill_color: tuple[int, int, int],
    ) -> tuple[Path | None, list[str], str]:
        try:
            cv2 = importlib.import_module("cv2")
            np = importlib.import_module("numpy")
        except Exception as exc:
            return None, [f"opencv_unavailable:{type(exc).__name__}"], "unavailable"
        source = cv2.imdecode(np.fromfile(_windows_long_path(image_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        mask = cv2.imdecode(np.fromfile(_windows_long_path(mask_path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if source is None or mask is None:
            return None, ["opencv_input_read_failed"], "failed"
        if int(cv2.countNonZero(mask)) == 0:
            cleaned = source
            algorithm = "preserve_source_no_mask"
            radius = 0
        else:
            # Adaptive inpaint radius derived from the mask's own stroke
            # thickness. Because preserved title/SFX/unknown-art regions are
            # never painted into the page mask, a wider radius here only affects
            # cleanable background_text / bubble glyphs and cannot damage
            # preserved artwork. Bounded to stay conservative.
            distance = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
            stroke_values = distance[mask > 0]
            stroke_half_width = (
                float(stroke_values.mean()) if int(stroke_values.size) else 1.0
            )
            radius = int(min(8, max(3, round(stroke_half_width * 1.5 + 1))))
            telea = cv2.inpaint(source, mask, radius, cv2.INPAINT_TELEA)
            navier_stokes = cv2.inpaint(source, mask, radius, cv2.INPAINT_NS)
            ring = cv2.dilate(mask, np.ones((5, 5), dtype=np.uint8), iterations=1)
            ring = cv2.subtract(ring, mask)

            def candidate_score(candidate: Any) -> float:
                mask_pixels = candidate[mask > 0]
                ring_pixels = candidate[ring > 0]
                if not len(mask_pixels) or not len(ring_pixels):
                    return float("inf")
                return abs(float(mask_pixels.std()) - float(ring_pixels.std()))

            if candidate_score(navier_stokes) < candidate_score(telea):
                cleaned = navier_stokes
                algorithm = "navier_stokes"
            else:
                cleaned = telea
                algorithm = "telea"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encoded, buffer = cv2.imencode(".png", cleaned)
        if not encoded:
            return None, ["opencv_output_write_failed"], "failed"
        buffer.tofile(_windows_long_path(output_path))
        return output_path, [f"opencv_selected:{algorithm}", f"inpaint_radius:{radius}"], "success"


def _cleaning_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    cleaning_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "cleaning"
    for child in [
        cleaning_dir,
        cleaning_dir / "masks",
        cleaning_dir / "manual_masks",
        cleaning_dir / "cleaned_pages",
        cleaning_dir / "quality",
        cleaning_dir / "diagnostics",
    ]:
        child.mkdir(parents=True, exist_ok=True)
    return cleaning_dir


def _source_page_for_run_page(cleaning_dir: Path, run_page_index: int) -> int:
    manifest_path = cleaning_dir.parent / "page_manifest.json"
    if not manifest_path.exists():
        return int(run_page_index)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        page_start = int(manifest.get("page_start") or 1)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return int(run_page_index)
    return page_start + int(run_page_index) - 1


def _load_manual_mask_decisions(cleaning_dir: Path) -> list[dict[str, Any]]:
    decisions_path = cleaning_dir / "manual_masks" / "manual_mask_decisions.json"
    if not decisions_path.exists():
        return []
    try:
        payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("decisions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _matching_manual_mask_decision(
    decisions: list[dict[str, Any]],
    *,
    source_page: int,
    run_page_index: int,
    page_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    for row in decisions:
        if int(row.get("source_page") or 0) != int(source_page):
            continue
        if int(row.get("run_page_index") or 0) != int(run_page_index):
            continue
        if str(row.get("page_id") or "") != page_id:
            continue
        if str(row.get("decision") or "").lower() not in {"approved", "accept"}:
            return None, "manual_mask_not_approved"
        if str(row.get("safety_mode") or "") != "reviewed_manual_mask":
            return None, "manual_mask_invalid_safety_mode"
        for field in ("reviewer", "reason", "created_at"):
            if not str(row.get(field) or "").strip():
                return None, f"manual_mask_decision_missing_{field}"
        scope = str(row.get("scope") or "")
        if scope not in {"page", "boxes"}:
            return None, "manual_mask_invalid_scope"
        if scope == "boxes" and not row.get("box_ids"):
            return None, "manual_mask_box_scope_missing_box_ids"
        return row, None
    return None, "manual_mask_reviewer_decision_missing"


def _validate_cleaning_mode(mode: str) -> str:
    if mode not in MANGA_CLEANING_MODES:
        raise ValueError(f"Invalid cleaning mode: {mode}. Expected one of {sorted(MANGA_CLEANING_MODES)}.")
    return mode


def _validate_sfx_policy(policy: str) -> str:
    if policy not in MANGA_SFX_POLICIES:
        raise ValueError(f"Invalid SFX policy: {policy}. Expected one of {sorted(MANGA_SFX_POLICIES)}.")
    return policy


def _parse_fill_color(value: str) -> tuple[int, int, int]:
    color = value.strip()
    if color.lower() == "white":
        return 255, 255, 255
    if color.startswith("#"):
        color = color[1:]
    if len(color) != 6 or any(char not in "0123456789abcdefABCDEF" for char in color):
        raise ValueError("Fill color must be `white` or a hex RGB value like #ffffff.")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _cleaning_adapter_for_mode(mode: str) -> CleaningAdapter | None:
    if mode == "fill":
        return FillCleaningAdapter()
    if mode in {"opencv_inpaint", "quality_inpaint"}:
        return OpenCvInpaintCleaningAdapter()
    if mode == "mask":
        return None
    raise ValueError(f"Invalid cleaning mode: {mode}")


def _cleaning_decisions_by_box(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT stable_box_id, mode, sfx_policy, reviewer, note, updated_at
        FROM manga_cleaning_decisions
        WHERE project_id = ?
        ORDER BY updated_at ASC, id ASC
        """,
        (project_id,),
    ).fetchall()
    decisions: dict[str, dict[str, Any]] = {}
    for row in rows:
        decisions[str(row["stable_box_id"])] = row_to_dict(row)
    return decisions


def _draw_box_mask(draw: Any, row: dict[str, Any], *, padding: int) -> None:
    polygon = row.get("polygon_json")
    if isinstance(polygon, list) and len(polygon) >= 3:
        points = [(float(point[0]), float(point[1])) for point in polygon if isinstance(point, list) and len(point) == 2]
        if len(points) >= 3:
            draw.polygon(points, fill=255)
            return
    x, y, width, height = _validate_bbox(row.get("bbox_json"), box_label=f"cleaning box {row.get('stable_key')}")
    draw.rectangle(
        [
            max(0, int(round(x)) - padding),
            max(0, int(round(y)) - padding),
            max(0, int(round(x + width)) + padding),
            max(0, int(round(y + height)) + padding),
        ],
        fill=255,
    )


def _write_cleaning_mask(
    *,
    image_size: tuple[int, int],
    rows: list[dict[str, Any]],
    mask_path: Path,
    padding: int,
) -> None:
    Image, _ImageOps = _load_pillow()
    from PIL import ImageDraw

    mask = Image.new("L", image_size, 0)
    draw = ImageDraw.Draw(mask)
    for row in rows:
        _draw_box_mask(draw, row, padding=padding)
    _save_png(mask, mask_path, force=True)


def _classify_cleaning_region(
    image: Any,
    row: dict[str, Any],
    *,
    page_has_large_region: bool,
) -> dict[str, Any]:
    from PIL import ImageStat

    x, y, width, height = _validate_bbox(
        row.get("bbox_json"),
        box_label=f"quality cleaning box {row.get('stable_key')}",
    )
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(image.width, max(left + 1, int(round(x + width))))
    bottom = min(image.height, max(top + 1, int(round(y + height))))
    crop = image.crop((left, top, right, bottom)).convert("L")
    stat = ImageStat.Stat(crop)
    mean = float(stat.mean[0])
    stddev = float(stat.stddev[0])
    area_ratio = ((right - left) * (bottom - top)) / max(1, image.width * image.height)
    region_type = _box_region_type(row)
    if region_type == "sfx":
        quality_type = "sfx"
    elif page_has_large_region and len(str(row.get("stable_key") or "")):
        quality_type = "title_art"
    elif area_ratio >= 0.12:
        quality_type = "title_art"
    elif area_ratio >= 0.02 and stddev >= 45 and top < image.height * 0.45:
        quality_type = "title_art"
    elif region_type == "caption" and mean >= 210 and stddev <= 50:
        quality_type = "caption_box"
    elif mean >= 230 and stddev <= 38 and area_ratio <= 0.12:
        quality_type = "plain_white_bubble"
    elif mean >= 205 and stddev <= 55 and area_ratio <= 0.10:
        quality_type = "textured_bubble"
    elif stddev >= 42 and area_ratio <= 0.06:
        quality_type = "background_text"
    else:
        quality_type = "unknown_art"
    return {
        "box_id": str(row.get("stable_key") or ""),
        "quality_region_type": quality_type,
        "source_region_type": region_type,
        "bbox": [x, y, width, height],
        "box_area_ratio": round(area_ratio, 6),
        "crop_mean_luma": round(mean, 3),
        "crop_luma_stddev": round(stddev, 3),
        "cleaning_policy": (
            "glyph_inpaint"
            if quality_type
            in {"plain_white_bubble", "textured_bubble", "caption_box", "background_text"}
            else "preserve"
        ),
    }


def _glyph_mask_for_crop(crop: Any, *, padding: int) -> tuple[Any, dict[str, Any]]:
    Image, _ImageOps = _load_pillow()
    try:
        cv2 = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
    except Exception as exc:
        return Image.new("L", crop.size, 0), {
            "status": "unavailable",
            "warning": f"opencv_unavailable:{type(exc).__name__}",
            "glyph_area_ratio": 0.0,
        }
    gray = np.asarray(crop.convert("L"))
    crop_height, crop_width = int(gray.shape[0]), int(gray.shape[1])
    crop_area = max(1, crop_height * crop_width)
    # Genuinely-dark ink, used to score which threshold polarity actually
    # captures the text (rather than the background plate). Cleanable manga
    # text in background_text / bubble regions is dark-on-light.
    otsu_threshold, _otsu_binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )
    dark_pixels = gray < otsu_threshold
    dark_total = int(dark_pixels.sum())
    candidates: list[tuple[float, Any, int]] = []
    for threshold_type in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
        _threshold, binary = cv2.threshold(
            gray, 0, 255, threshold_type | cv2.THRESH_OTSU
        )
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
            binary, connectivity=8
        )
        filtered = np.zeros_like(binary)
        kept = 0
        for label in range(1, count):
            x, y, width, height, area = [int(value) for value in stats[label]]
            if area < 3:
                continue
            # Reject ONLY components that nearly fill the whole crop (a solid
            # background plate or full-frame art). Border-touching glyphs and
            # merged glyph blobs are legitimate text and must be kept — the
            # Phase 9M.1 audit showed the old border/area filters were the main
            # cause of residual source-text ghosts on pages 8-10.
            fills_crop = (
                width >= crop_width * 0.95 and height >= crop_height * 0.95
            ) or area > crop_area * 0.9
            if fills_crop:
                continue
            filtered[labels == label] = 255
            kept += 1
        if kept:
            # Bind glyph cores to their anti-aliased outline halo, then apply a
            # small bounded dilation so the mask covers stroke edges (not just
            # cores) while staying glyph-shaped rather than a full rectangle.
            close_kernel = np.ones((5, 5), dtype=np.uint8)
            filtered = cv2.morphologyEx(
                filtered, cv2.MORPH_CLOSE, close_kernel, iterations=1
            )
            grow = max(2, min(4, int(padding) + 2))
            filtered = cv2.dilate(
                filtered,
                np.ones((grow, grow), dtype=np.uint8),
                iterations=1,
            )
        ratio = float(cv2.countNonZero(filtered)) / crop_area
        covered_dark = int((dark_pixels & (filtered > 0)).sum())
        dark_coverage = covered_dark / dark_total if dark_total else 0.0
        # Choose the polarity that covers the most genuinely-dark ink (so the
        # mask spans glyph cores AND their anti-aliased outline), penalising
        # masks that collapse to nothing or balloon toward a full rectangle.
        penalty = 0.0
        if ratio > 0.55 or (kept == 0):
            penalty += 1.0
        if ratio < 0.002:
            penalty += 1.0
        score = (1.0 - dark_coverage) + penalty
        candidates.append((score, filtered, kept))
    _score, selected, kept = min(candidates, key=lambda item: item[0])
    ratio = float(cv2.countNonZero(selected)) / crop_area
    # Glyph-shaped masks may legitimately cover a sizable share of a text-dense
    # crop; only block when the mask collapses or balloons into a near-full
    # rectangle. The page-level mask_area_ratio gate (>0.12) and the per-box
    # glyph_area_ratio gate (>0.35) in QA remain the production destructive
    # guards and are intentionally unchanged here.
    status = (
        "pass"
        if 0.002 <= ratio <= MANGA_BOX_GLYPH_AREA_RATIO_LIMIT and kept > 0
        else "blocked"
    )
    return Image.fromarray(selected, mode="L"), {
        "status": status,
        "component_count": kept,
        "glyph_area_ratio": round(ratio, 6),
        "warning": None if status == "pass" else "unsafe_glyph_mask_ratio",
    }


def _write_quality_cleaning_mask(
    *,
    image_path: Path,
    rows: list[dict[str, Any]],
    mask_path: Path,
    padding: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    Image, _ImageOps = _load_pillow()
    with Image.open(_windows_long_path(image_path)) as source:
        image = source.convert("RGB")
        page_area = max(1, image.width * image.height)
        page_has_large_region = any(
            (_validate_bbox(row.get("bbox_json"), box_label="quality region")[2]
             * _validate_bbox(row.get("bbox_json"), box_label="quality region")[3])
            / page_area
            >= 0.12
            for row in rows
        ) and len(rows) <= 5
        decisions = [
            _classify_cleaning_region(
                image, row, page_has_large_region=page_has_large_region
            )
            for row in rows
        ]
        decisions_by_box = {item["box_id"]: item for item in decisions}
        page_mask = Image.new("L", image.size, 0)
        cleanable: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row in rows:
            box_id = str(row.get("stable_key") or "")
            decision = decisions_by_box[box_id]
            if decision["cleaning_policy"] != "glyph_inpaint":
                warnings.append(
                    f"{decision['quality_region_type']}_preserved:{box_id}"
                )
                continue
            x, y, width, height = decision["bbox"]
            left = max(0, int(round(x)))
            top = max(0, int(round(y)))
            right = min(image.width, max(left + 1, int(round(x + width))))
            bottom = min(image.height, max(top + 1, int(round(y + height))))
            glyph_mask, metrics = _glyph_mask_for_crop(
                image.crop((left, top, right, bottom)),
                padding=padding,
            )
            decision["mask_metrics"] = metrics
            if metrics["status"] != "pass":
                decision["cleaning_policy"] = "preserve"
                warnings.append(f"unsafe_glyph_mask_preserved:{box_id}")
                continue
            page_mask.paste(glyph_mask, (left, top), glyph_mask)
            cleanable.append(row)
        from PIL import ImageDraw

        mask_draw = ImageDraw.Draw(page_mask)
        for decision in decisions:
            if decision.get("cleaning_policy") != "preserve":
                continue
            x, y, width, height = decision["bbox"]
            mask_draw.rectangle(
                [
                    max(0, int(round(x))),
                    max(0, int(round(y))),
                    min(image.width, max(0, int(round(x + width)))),
                    min(image.height, max(0, int(round(y + height)))),
                ],
                fill=0,
            )
        _save_png(page_mask, mask_path, force=True)
    return cleanable, decisions, warnings


def _write_cleaning_quality_contact_sheet(
    workspace: Workspace,
    *,
    jobs: list[dict[str, Any]],
    output_path: Path,
) -> list[dict[str, Any]]:
    Image, _ImageOps = _load_pillow()
    from PIL import ImageChops, ImageDraw, ImageFont

    rows: list[Any] = []
    reports: list[dict[str, Any]] = []
    font = ImageFont.load_default()
    try:
        cv2 = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
    except Exception:
        cv2 = None
        np = None
    for job in jobs:
        output_rel = job.get("output_image_artifact")
        if not output_rel:
            continue
        source_path = workspace.path / str(job["input_image_artifact"])
        mask_path = workspace.path / str(job["mask_artifact"])
        cleaned_path = workspace.path / str(output_rel)
        with Image.open(_windows_long_path(source_path)) as source_file, Image.open(
            _windows_long_path(mask_path)
        ) as mask_file, Image.open(_windows_long_path(cleaned_path)) as cleaned_file:
            source = source_file.convert("RGB")
            mask = mask_file.convert("L")
            cleaned = cleaned_file.convert("RGB")
            page_area = max(1, source.width * source.height)
            changed = ImageChops.difference(source, cleaned).convert("L")
            changed_ratio = sum(changed.histogram()[8:]) / page_area
            source_luma = source.convert("L")
            cleaned_luma = cleaned.convert("L")
            cleaned_white = cleaned_luma.point(
                lambda value: 255 if value >= 248 else 0
            )
            source_not_white = source_luma.point(
                lambda value: 255 if value < 230 else 0
            )
            white_change = ImageChops.multiply(cleaned_white, source_not_white)
            white_pixels = sum(white_change.histogram()[1:])
            residual_edge_ratio = 0.0
            residual_edge_density = 0.0
            if cv2 is not None and np is not None and sum(mask.histogram()[1:]):
                source_array = np.asarray(source_luma)
                cleaned_array = np.asarray(cleaned_luma)
                mask_array = np.asarray(mask) > 0
                source_edges = cv2.Canny(source_array, 80, 160)
                cleaned_edges = cv2.Canny(cleaned_array, 80, 160)
                source_edge_count = int((source_edges[mask_array] > 0).sum())
                cleaned_edge_count = int((cleaned_edges[mask_array] > 0).sum())
                residual_edge_ratio = cleaned_edge_count / max(1, source_edge_count)
                residual_edge_density = cleaned_edge_count / max(
                    1, int(mask_array.sum())
                )
            reports.append(
                {
                    "page_id": job.get("page_id"),
                    "page_index": job.get("page_index"),
                    "mask_area_ratio": job.get("mask_area_ratio", 0.0),
                    "changed_area_ratio": round(changed_ratio, 6),
                    "new_white_pixel_ratio": round(white_pixels / page_area, 6),
                    "large_white_block_detected": white_pixels / page_area > 0.04,
                    "residual_edge_ratio": round(residual_edge_ratio, 6),
                    "residual_edge_density": round(residual_edge_density, 6),
                    "residual_text_suspected": (
                        residual_edge_ratio >= MANGA_RESIDUAL_EDGE_RATIO_LIMIT
                        and residual_edge_density > 0.04
                    ),
                }
            )
            panels = []
            for label, image in (
                ("BEFORE", source),
                ("GLYPH MASK", mask.convert("RGB")),
                ("AFTER", cleaned),
            ):
                thumbnail = image.copy()
                thumbnail.thumbnail((300, 400))
                panel = Image.new("RGB", (310, 430), "white")
                panel.paste(
                    thumbnail,
                    ((310 - thumbnail.width) // 2, 25),
                )
                ImageDraw.Draw(panel).text(
                    (8, 7),
                    f"P{job.get('page_index')} {label}",
                    fill="black",
                    font=font,
                )
                panels.append(panel)
            row = Image.new("RGB", (930, 430), "#cccccc")
            for index, panel in enumerate(panels):
                row.paste(panel, (index * 310, 0))
            rows.append(row)
    sheet = Image.new("RGB", (930, max(1, len(rows)) * 430), "white")
    for index, row in enumerate(rows):
        sheet.paste(row, (0, index * 430))
    _save_png(sheet, output_path, force=True)
    return reports


def _cleaning_box_residual_rows(
    *,
    source: Any,
    mask: Any,
    cleaned: Any,
    region_decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        cv2 = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
    except Exception:
        return []
    source_gray = np.asarray(source.convert("L"))
    cleaned_gray = np.asarray(cleaned.convert("L"))
    mask_array = np.asarray(mask.convert("L")) > 0
    source_edges = cv2.Canny(source_gray, 80, 160) > 0
    cleaned_edges = cv2.Canny(cleaned_gray, 80, 160) > 0
    rows: list[dict[str, Any]] = []
    for decision in region_decisions:
        bbox = decision.get("bbox")
        if not isinstance(bbox, list):
            continue
        x, y, width, height = bbox
        left = max(0, int(round(x)))
        top = max(0, int(round(y)))
        right = min(source.width, max(left + 1, int(round(x + width))))
        bottom = min(source.height, max(top + 1, int(round(y + height))))
        box_mask = mask_array[top:bottom, left:right]
        masked_pixels = int(box_mask.sum())
        source_edge_count = int(
            (source_edges[top:bottom, left:right] & box_mask).sum()
        )
        cleaned_edge_count = int(
            (cleaned_edges[top:bottom, left:right] & box_mask).sum()
        )
        rows.append(
            {
                "box_id": str(decision.get("box_id") or ""),
                "source_region_type": str(
                    decision.get("source_region_type") or ""
                ),
                "quality_region_type": str(
                    decision.get("quality_region_type") or ""
                ),
                "cleaning_policy": str(decision.get("cleaning_policy") or ""),
                "bbox": [left, top, right - left, bottom - top],
                "glyph_area_ratio": (decision.get("mask_metrics") or {}).get(
                    "glyph_area_ratio"
                ),
                "mask_area_ratio": round(
                    masked_pixels / max(1, (right - left) * (bottom - top)),
                    6,
                ),
                "source_edge_count": source_edge_count,
                "cleaned_edge_count": cleaned_edge_count,
                "residual_edge_ratio": round(
                    cleaned_edge_count / max(1, source_edge_count),
                    6,
                ),
                "residual_edge_density": round(
                    cleaned_edge_count / max(1, masked_pixels),
                    6,
                ),
            }
        )
    rows = sorted(
        rows,
        key=lambda row: (
            int(row["cleaned_edge_count"]),
            float(row["residual_edge_ratio"]),
        ),
        reverse=True,
    )
    total_cleaned_edges = sum(int(row["cleaned_edge_count"]) for row in rows)
    contributor_floor = max(100, int(total_cleaned_edges * 0.05))
    for row in rows:
        row["primary_residual_contributor"] = (
            row["cleaning_policy"] == "glyph_inpaint"
            and float(row["residual_edge_ratio"])
            >= MANGA_RESIDUAL_EDGE_RATIO_LIMIT
            and int(row["cleaned_edge_count"]) >= contributor_floor
        )
    return rows


def _candidate_manual_mask_for_review(
    *,
    source: Any,
    mask: Any,
    residual_rows: list[dict[str, Any]],
    region_decisions: list[dict[str, Any]],
) -> tuple[Any | None, dict[str, Any]]:
    try:
        cv2 = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
    except Exception as exc:
        return None, {
            "status": "unavailable",
            "reason": f"opencv_unavailable:{type(exc).__name__}",
        }
    source_gray = np.asarray(source.convert("L"))
    candidate = np.asarray(mask.convert("L")).copy()
    failing_box_ids = {
        str(row["box_id"])
        for row in residual_rows
        if bool(row.get("primary_residual_contributor"))
    }
    for decision in region_decisions:
        if str(decision.get("box_id") or "") not in failing_box_ids:
            continue
        bbox = decision.get("bbox")
        if not isinstance(bbox, list):
            continue
        x, y, width, height = bbox
        left = max(0, int(round(x)))
        top = max(0, int(round(y)))
        right = min(source.width, max(left + 1, int(round(x + width))))
        bottom = min(source.height, max(top + 1, int(round(y + height))))
        crop = candidate[top:bottom, left:right]
        near_existing = cv2.dilate(
            (crop > 0).astype(np.uint8),
            np.ones((5, 5), dtype=np.uint8),
            iterations=1,
        ) > 0
        antialias_halo = source_gray[top:bottom, left:right] < 235
        candidate[top:bottom, left:right] = np.where(
            (crop > 0) | (near_existing & antialias_halo),
            255,
            0,
        ).astype(np.uint8)
    candidate_image = _load_pillow()[0].fromarray(candidate, mode="L")
    valid, reason = _validate_escalation_mask(
        candidate_image,
        region_decisions=region_decisions,
    )
    if not valid:
        return None, {"status": "rejected", "reason": reason}
    return candidate_image, {
        "status": "candidate_for_review",
        "auto_approved": False,
        "failing_box_ids": sorted(failing_box_ids),
        "generation_method": "bounded_antialias_halo_luma_lt_235",
        "mask_area_ratio": round(
            sum(candidate_image.histogram()[1:])
            / max(1, candidate_image.width * candidate_image.height),
            6,
        ),
        "box_mask_area_ratios": {
            str(item.get("box_id") or ""): round(
                _mask_crop_ratio(candidate_image, list(item["bbox"])),
                6,
            )
            for item in region_decisions
            if isinstance(item.get("bbox"), list)
        },
    }


def _write_source_page_residual_diagnostics(
    workspace: Workspace,
    *,
    job: dict[str, Any],
    page_report: dict[str, Any],
    cleaning_dir: Path,
) -> dict[str, Any] | None:
    output_rel = job.get("output_image_artifact")
    if not output_rel:
        return None
    Image, _ImageOps = _load_pillow()
    from PIL import ImageChops, ImageDraw, ImageFont

    source_page = int(
        job.get("source_page")
        or _source_page_for_run_page(
            cleaning_dir,
            int(job.get("page_index") or 0),
        )
    )
    source_path = workspace.path / str(job["input_image_artifact"])
    mask_path = workspace.path / str(job["mask_artifact"])
    cleaned_path = workspace.path / str(output_rel)
    with Image.open(_windows_long_path(source_path)) as source_file, Image.open(
        _windows_long_path(mask_path)
    ) as mask_file, Image.open(_windows_long_path(cleaned_path)) as cleaned_file:
        source = source_file.convert("RGB")
        mask = mask_file.convert("L")
        cleaned = cleaned_file.convert("RGB")
        residual_rows = _cleaning_box_residual_rows(
            source=source,
            mask=mask,
            cleaned=cleaned,
            region_decisions=list(job.get("region_decisions") or []),
        )
        diagnostics_dir = cleaning_dir / "diagnostics"
        crops_dir = diagnostics_dir / f"source_page_{source_page:04d}_crops"
        crops_dir.mkdir(parents=True, exist_ok=True)
        font = ImageFont.load_default()
        contact_rows: list[Any] = []
        crop_artifacts: dict[str, dict[str, str]] = {}
        for row in residual_rows:
            box_id = str(row["box_id"])
            left, top, width, height = row["bbox"]
            box = (left, top, left + width, top + height)
            original_crop = source.crop(box)
            mask_crop = mask.crop(box).convert("RGB")
            cleaned_crop = cleaned.crop(box)
            diff_crop = ImageChops.difference(original_crop, cleaned_crop)
            paths = {
                "original_crop": crops_dir / f"{box_id}_original.png",
                "mask_crop": crops_dir / f"{box_id}_mask.png",
                "cleaned_crop": crops_dir / f"{box_id}_cleaned.png",
                "diff_crop": crops_dir / f"{box_id}_diff.png",
            }
            for key, image in (
                ("original_crop", original_crop),
                ("mask_crop", mask_crop),
                ("cleaned_crop", cleaned_crop),
                ("diff_crop", diff_crop),
            ):
                _save_png(image, paths[key], force=True)
            crop_artifacts[box_id] = {
                key: _relative_artifact(workspace, path)
                for key, path in paths.items()
            }
            panels = []
            for label, image in (
                ("ORIGINAL", original_crop),
                ("MASK", mask_crop),
                ("CLEANED", cleaned_crop),
                ("DIFF", diff_crop),
            ):
                thumbnail = image.copy()
                thumbnail.thumbnail((280, 260))
                panel = Image.new("RGB", (290, 300), "white")
                panel.paste(thumbnail, ((290 - thumbnail.width) // 2, 30))
                ImageDraw.Draw(panel).text((6, 8), label, fill="black", font=font)
                panels.append(panel)
            contact_row = Image.new("RGB", (1160, 330), "#dddddd")
            for index, panel in enumerate(panels):
                contact_row.paste(panel, (index * 290, 0))
            ImageDraw.Draw(contact_row).text(
                (6, 306),
                (
                    f"{box_id} residual={row['residual_edge_ratio']} "
                    f"mask={row['mask_area_ratio']}"
                ),
                fill="black",
                font=font,
            )
            contact_rows.append(contact_row)
        contact_sheet_path = (
            diagnostics_dir / f"source_page_{source_page}_residual_contact_sheet.png"
        )
        sheet = Image.new(
            "RGB",
            (1160, max(1, len(contact_rows)) * 330),
            "white",
        )
        for index, contact_row in enumerate(contact_rows):
            sheet.paste(contact_row, (0, index * 330))
        _save_png(sheet, contact_sheet_path, force=True)
        candidate_mask, candidate = _candidate_manual_mask_for_review(
            source=source,
            mask=mask,
            residual_rows=residual_rows,
            region_decisions=list(job.get("region_decisions") or []),
        )
        candidate_path = (
            diagnostics_dir / f"source_page_{source_page}_candidate_manual_mask.png"
        )
        if candidate_mask is not None:
            _save_png(candidate_mask, candidate_path, force=True)
            candidate["artifact"] = _relative_artifact(workspace, candidate_path)

    report = {
        "schema_version": "phase9m3.source_page_residual.v1",
        "source_page": source_page,
        "run_page_index": int(job.get("page_index") or 0),
        "page_id": str(job.get("page_id") or ""),
        "page_residual_edge_ratio": float(
            page_report.get("residual_edge_ratio") or 0.0
        ),
        "page_residual_edge_threshold": MANGA_RESIDUAL_EDGE_RATIO_LIMIT,
        "page_mask_area_ratio": float(job.get("mask_area_ratio") or 0.0),
        "page_mask_area_threshold": MANGA_PAGE_MASK_AREA_RATIO_LIMIT,
        "large_white_block_detected": bool(
            page_report.get("large_white_block_detected")
        ),
        "widen_mask_retry": (job.get("cleaning_escalation") or {}).get(
            "attempts",
            [],
        ),
        "boxes": [
            {**row, "artifacts": crop_artifacts.get(str(row["box_id"]), {})}
            for row in residual_rows
        ],
        "candidate_manual_mask": candidate,
        "manual_mask_destination": _relative_artifact(
            workspace,
            cleaning_dir
            / "manual_masks"
            / f"page_{source_page:04d}_mask.png",
        ),
        "manual_mask_decisions_destination": _relative_artifact(
            workspace,
            cleaning_dir / "manual_masks" / "manual_mask_decisions.json",
        ),
        "contact_sheet": _relative_artifact(workspace, contact_sheet_path),
        "auto_approved": False,
    }
    report_path = (
        cleaning_dir
        / "diagnostics"
        / f"source_page_{source_page}_residual_report.json"
    )
    markdown_path = report_path.with_suffix(".md")
    _write_json_artifact(workspace, report_path, report)
    failing_rows = [
        row
        for row in residual_rows
        if bool(row.get("primary_residual_contributor"))
    ]
    markdown_path.write_text(
        "\n".join(
            [
                f"# Source Page {source_page} Residual Report",
                "",
                f"- Run page index: `{report['run_page_index']}`",
                f"- Page ID: `{report['page_id']}`",
                (
                    "- Page residual edge ratio: "
                    f"`{report['page_residual_edge_ratio']}` "
                    f"(limit `{MANGA_RESIDUAL_EDGE_RATIO_LIMIT}`)"
                ),
                (
                    "- Page mask area ratio: "
                    f"`{report['page_mask_area_ratio']}` "
                    f"(limit `{MANGA_PAGE_MASK_AREA_RATIO_LIMIT}`)"
                ),
                f"- Large white block: `{str(report['large_white_block_detected']).lower()}`",
                "- Candidate mask auto-approved: `false`",
                "",
                "## Failing Cleanable Boxes",
                *(
                    (
                        f"- `{row['box_id']}`: region "
                        f"`{row['quality_region_type']}`, residual "
                        f"`{row['residual_edge_ratio']}`, mask "
                        f"`{row['mask_area_ratio']}`."
                    )
                    for row in failing_rows
                ),
                "",
                "## Review Instructions",
                (
                    f"- Review `{report['contact_sheet']}` and the candidate "
                    f"`{candidate.get('artifact', 'unavailable')}`."
                ),
                (
                    f"- Place the approved mask at "
                    f"`{report['manual_mask_destination']}`."
                ),
                (
                    f"- Record approval at "
                    f"`{report['manual_mask_decisions_destination']}`."
                ),
                "- Approval does not bypass residual or destructive-cleaning QA.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "source_page": source_page,
        "json": _relative_artifact(workspace, report_path),
        "markdown": _relative_artifact(workspace, markdown_path),
        "contact_sheet": _relative_artifact(workspace, contact_sheet_path),
        "candidate_manual_mask": candidate.get("artifact"),
    }


def export_manga_manual_mask_review_package(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    source_page: int,
) -> dict[str, Any]:
    cleaning_dir = _cleaning_dir_for_run(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
    )
    jobs_payload = json.loads(
        (cleaning_dir / "cleaning_jobs.json").read_text(encoding="utf-8")
    )
    visual_payload = json.loads(
        (cleaning_dir / "quality" / "visual_diff_report.json").read_text(
            encoding="utf-8"
        )
    )
    job = next(
        (
            row
            for row in jobs_payload.get("jobs") or []
            if int(
                row.get("source_page")
                or _source_page_for_run_page(
                    cleaning_dir,
                    int(row.get("page_index") or 0),
                )
            )
            == int(source_page)
        ),
        None,
    )
    if job is None:
        raise ValueError(f"Cleaning job not found for source page {source_page}.")
    page_report = next(
        (
            row
            for row in visual_payload.get("pages") or []
            if int(row.get("page_index") or 0)
            == int(job.get("page_index") or 0)
        ),
        None,
    )
    if page_report is None:
        raise ValueError(f"Visual diff report not found for source page {source_page}.")
    package = _write_source_page_residual_diagnostics(
        workspace,
        job=job,
        page_report=page_report,
        cleaning_dir=cleaning_dir,
    )
    if package is None:
        raise ValueError(f"Unable to export review package for source page {source_page}.")
    return package


def _mask_crop_ratio(mask: Any, bbox: list[float]) -> float:
    x, y, width, height = bbox
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(mask.width, max(left + 1, int(round(x + width))))
    bottom = min(mask.height, max(top + 1, int(round(y + height))))
    crop = mask.crop((left, top, right, bottom)).convert("L")
    return sum(crop.histogram()[1:]) / max(1, crop.width * crop.height)


def _validate_escalation_mask(
    mask: Any,
    *,
    region_decisions: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    page_ratio = sum(mask.convert("L").histogram()[1:]) / max(
        1, mask.width * mask.height
    )
    if page_ratio > MANGA_PAGE_MASK_AREA_RATIO_LIMIT:
        return False, f"page_mask_area_ratio_exceeded:{page_ratio:.6f}"
    for decision in region_decisions:
        bbox = decision.get("bbox")
        if not isinstance(bbox, list):
            continue
        policy = str(decision.get("cleaning_policy") or "")
        ratio = _mask_crop_ratio(mask, bbox)
        if policy == "preserve" and ratio > 0:
            return False, f"preserved_region_masked:{decision.get('box_id')}"
        if (
            policy == "glyph_inpaint"
            and ratio > MANGA_BOX_GLYPH_AREA_RATIO_LIMIT
        ):
            return False, (
                f"box_glyph_area_ratio_exceeded:{decision.get('box_id')}:{ratio:.6f}"
            )
    return True, None


def _widen_quality_mask_once(
    *,
    mask_path: Path,
    region_decisions: list[dict[str, Any]],
    output_path: Path,
) -> dict[str, Any]:
    Image, _ImageOps = _load_pillow()
    from PIL import ImageFilter

    with Image.open(_windows_long_path(mask_path)) as source:
        original = source.convert("L")
    widened = original.copy()
    retried_box_ids: list[str] = []
    skipped_box_ids: list[str] = []
    for decision in region_decisions:
        if decision.get("cleaning_policy") != "glyph_inpaint":
            continue
        bbox = decision.get("bbox")
        if not isinstance(bbox, list):
            skipped_box_ids.append(str(decision.get("box_id") or ""))
            continue
        x, y, width, height = bbox
        left = max(0, int(round(x)))
        top = max(0, int(round(y)))
        right = min(original.width, max(left + 1, int(round(x + width))))
        bottom = min(original.height, max(top + 1, int(round(y + height))))
        crop = original.crop((left, top, right, bottom))
        grown = crop.filter(ImageFilter.MaxFilter(3))
        ratio = sum(grown.histogram()[1:]) / max(1, grown.width * grown.height)
        box_id = str(decision.get("box_id") or "")
        if ratio > MANGA_BOX_GLYPH_AREA_RATIO_LIMIT:
            skipped_box_ids.append(box_id)
            continue
        widened.paste(grown, (left, top))
        retried_box_ids.append(box_id)
    from PIL import ImageDraw

    mask_draw = ImageDraw.Draw(widened)
    for decision in region_decisions:
        if decision.get("cleaning_policy") != "preserve":
            continue
        x, y, width, height = decision["bbox"]
        mask_draw.rectangle(
            [
                max(0, int(round(x))),
                max(0, int(round(y))),
                min(widened.width, max(0, int(round(x + width)))),
                min(widened.height, max(0, int(round(y + height)))),
            ],
            fill=0,
        )
    valid, reason = _validate_escalation_mask(
        widened,
        region_decisions=region_decisions,
    )
    if not retried_box_ids:
        valid = False
        reason = reason or "no_boxes_within_retry_caps"
    if valid:
        _save_png(widened, output_path, force=True)
    return {
        "status": "applied" if valid else "skipped",
        "reason": reason,
        "retried_box_ids": retried_box_ids,
        "skipped_box_ids": skipped_box_ids,
        "mask_area_ratio": round(
            sum(widened.histogram()[1:]) / max(1, widened.width * widened.height),
            6,
        ),
    }


def _reviewed_manual_mask(
    *,
    manual_mask_path: Path,
    expected_size: tuple[int, int],
    region_decisions: list[dict[str, Any]],
    manual_mask_decisions: list[dict[str, Any]],
    source_page: int,
    run_page_index: int,
    page_id: str,
) -> tuple[Any | None, dict[str, Any]]:
    decision, decision_error = _matching_manual_mask_decision(
        manual_mask_decisions,
        source_page=source_page,
        run_page_index=run_page_index,
        page_id=page_id,
    )
    if not manual_mask_path.exists():
        return None, {"status": "unavailable", "reason": "manual_mask_missing"}
    if decision is None:
        return None, {
            "status": "unavailable",
            "reason": decision_error,
        }
    Image, _ImageOps = _load_pillow()
    with Image.open(_windows_long_path(manual_mask_path)) as source:
        mask = source.convert("L")
    clipped_to_page = False
    if mask.width < expected_size[0] or mask.height < expected_size[1]:
        return None, {"status": "rejected", "reason": "manual_mask_size_mismatch"}
    if mask.size != expected_size:
        mask = mask.crop((0, 0, expected_size[0], expected_size[1]))
        clipped_to_page = True
    mask = mask.point(lambda value: 255 if value > 0 else 0)
    valid, reason = _validate_escalation_mask(
        mask,
        region_decisions=region_decisions,
    )
    if not valid:
        return None, {"status": "rejected", "reason": reason}
    box_ratios = {
        str(item.get("box_id") or ""): round(
            _mask_crop_ratio(mask, list(item["bbox"])),
            6,
        )
        for item in region_decisions
        if isinstance(item.get("bbox"), list)
    }
    return mask, {
        "status": "accepted",
        "reviewer": str(decision["reviewer"]),
        "reason": str(decision["reason"]),
        "created_at": str(decision["created_at"]),
        "safety_mode": str(decision["safety_mode"]),
        "scope": str(decision["scope"]),
        "box_ids": list(decision.get("box_ids") or []),
        "source_page": source_page,
        "run_page_index": run_page_index,
        "page_id": page_id,
        "clipped_to_page": clipped_to_page,
        "box_mask_area_ratios": box_ratios,
        "mask_area_ratio": round(
            sum(mask.histogram()[1:]) / max(1, mask.width * mask.height),
            6,
        ),
    }


def _cleaning_report_blocks(report: dict[str, Any]) -> bool:
    return (
        float(report.get("residual_edge_ratio") or 0.0)
        >= MANGA_RESIDUAL_EDGE_RATIO_LIMIT
        or bool(report.get("large_white_block_detected"))
    )


def _apply_cleaning_escalation_ladder(
    workspace: Workspace,
    *,
    jobs: list[dict[str, Any]],
    initial_reports: list[dict[str, Any]],
    adapter: CleaningAdapter,
    fill_color: tuple[int, int, int],
    cleaning_dir: Path,
) -> list[dict[str, Any]]:
    manual_mask_decisions = _load_manual_mask_decisions(cleaning_dir)
    reports_by_page = {
        int(report.get("page_index") or 0): report for report in initial_reports
    }
    for job in jobs:
        page_index = int(job.get("page_index") or 0)
        source_page = int(
            job.get("source_page")
            or _source_page_for_run_page(cleaning_dir, page_index)
        )
        report = reports_by_page.get(page_index)
        if report is None or not _cleaning_report_blocks(report):
            continue
        escalation = {
            "activated": True,
            "attempts": [],
            "status": "blocked",
            "recommended_action": (
                "widen_mask_retry tried -> manual_mask -> neural_inpaint_or_manual"
            ),
        }
        job["cleaning_escalation"] = escalation
        source_path = workspace.path / str(job["input_image_artifact"])
        current_mask_path = workspace.path / str(job["mask_artifact"])
        region_decisions = list(job.get("region_decisions") or [])

        widened_path = cleaning_dir / "masks" / f"page_{page_index:04d}_mask_widen_retry.png"
        widen = _widen_quality_mask_once(
            mask_path=current_mask_path,
            region_decisions=region_decisions,
            output_path=widened_path,
        )
        widen["rung"] = "widen_mask_retry"
        escalation["attempts"].append(widen)
        if widen["status"] == "applied":
            widened_output = (
                cleaning_dir
                / "cleaned_pages"
                / f"page_{page_index:04d}_quality_inpaint_widen_retry.png"
            )
            output, warnings, status = adapter.clean(
                workspace=workspace,
                image_path=source_path,
                mask_path=widened_path,
                output_path=widened_output,
                fill_color=fill_color,
            )
            widen.update({"adapter_status": status, "warnings": warnings})
            if output is not None:
                job["mask_artifact"] = _relative_artifact(workspace, widened_path)
                job["output_image_artifact"] = _relative_artifact(workspace, output)
                job["mask_area_ratio"] = widen["mask_area_ratio"]
                measured = _write_cleaning_quality_contact_sheet(
                    workspace,
                    jobs=[job],
                    output_path=cleaning_dir
                    / "quality"
                    / f"page_{page_index:04d}_widen_retry_contact_sheet.png",
                )[0]
                widen["quality"] = measured
                if not _cleaning_report_blocks(measured):
                    escalation["status"] = "pass"
                    continue

        manual_path = cleaning_dir / "manual_masks" / f"page_{source_page:04d}_mask.png"
        Image, _ImageOps = _load_pillow()
        with Image.open(_windows_long_path(source_path)) as source_image:
            expected_size = source_image.size
        manual_mask, manual = _reviewed_manual_mask(
            manual_mask_path=manual_path,
            expected_size=expected_size,
            region_decisions=region_decisions,
            manual_mask_decisions=manual_mask_decisions,
            source_page=source_page,
            run_page_index=page_index,
            page_id=str(job.get("page_id") or ""),
        )
        manual["rung"] = "manual_mask"
        escalation["attempts"].append(manual)
        if manual_mask is None:
            continue
        _save_png(manual_mask, manual_path, force=True)
        manual_output = (
            cleaning_dir
            / "cleaned_pages"
            / f"source_page_{source_page:04d}_quality_inpaint_manual_mask.png"
        )
        output, warnings, status = adapter.clean(
            workspace=workspace,
            image_path=source_path,
            mask_path=manual_path,
            output_path=manual_output,
            fill_color=fill_color,
        )
        manual.update({"adapter_status": status, "warnings": warnings})
        if output is None:
            continue
        job["mask_artifact"] = _relative_artifact(workspace, manual_path)
        job["output_image_artifact"] = _relative_artifact(workspace, output)
        job["mask_area_ratio"] = manual["mask_area_ratio"]
        measured = _write_cleaning_quality_contact_sheet(
            workspace,
            jobs=[job],
            output_path=cleaning_dir
            / "quality"
            / f"source_page_{source_page:04d}_manual_mask_contact_sheet.png",
        )[0]
        manual["quality"] = measured
        if not _cleaning_report_blocks(measured):
            escalation["status"] = "pass"
    return _write_cleaning_quality_contact_sheet(
        workspace,
        jobs=jobs,
        output_path=cleaning_dir / "quality" / "per_page_before_after_contact_sheet.png",
    )


def _select_cleaning_rows(
    rows: list[dict[str, Any]],
    *,
    decisions: dict[str, dict[str, Any]],
    box_ids: set[str] | None,
    sfx_policy: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    selected: list[dict[str, Any]] = []
    warnings: list[str] = []
    sfx_decisions: dict[str, str] = {}
    for row in rows:
        stable_key = str(row.get("stable_key") or "")
        if not stable_key:
            continue
        if box_ids is not None and stable_key not in box_ids:
            continue
        region_type = _box_region_type(row)
        if region_type == "sfx":
            decision = decisions.get(stable_key) or {}
            policy = _validate_sfx_policy(str(decision.get("sfx_policy") or sfx_policy))
            sfx_decisions[stable_key] = policy
            if policy != "clean":
                warnings.append(f"sfx_{policy}:{stable_key}")
                continue
        selected.append(row)
    return selected, warnings, sfx_decisions


def run_manga_cleaning(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    mode: str = "fill",
    page_index: int | None = None,
    box_ids: list[str] | None = None,
    fill_color: str = "#ffffff",
    mask_padding: int = 0,
    sfx_policy: str = "leave_unchanged",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    mode = _validate_cleaning_mode(mode)
    sfx_policy = _validate_sfx_policy(sfx_policy)
    color = _parse_fill_color(fill_color)
    selected_box_ids = {str(box_id) for box_id in box_ids} if box_ids else None
    cleaning_dir = _cleaning_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    masks_dir = cleaning_dir / "masks"
    cleaned_dir = cleaning_dir / "cleaned_pages"
    jobs_path = cleaning_dir / "cleaning_jobs.json"
    summary_path = cleaning_dir / "cleaning_summary.md"
    quality_dir = cleaning_dir / "quality"
    visual_diff_path = quality_dir / "visual_diff_report.json"
    white_block_path = quality_dir / "white_block_audit.json"
    mask_quality_path = quality_dir / "mask_quality_report.json"
    destructive_path = quality_dir / "destructive_cleaning_blockers.json"
    contact_sheet_path = quality_dir / "per_page_before_after_contact_sheet.png"
    adapter = _cleaning_adapter_for_mode(mode)
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.cleaning",
            status="running",
            stage="clean_pages",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "mode": mode,
                "page_index": page_index,
                "box_ids": sorted(selected_box_ids) if selected_box_ids else None,
                "sfx_policy": sfx_policy,
                "cloud_used": False,
            },
            result_data={},
        )
        decisions = _cleaning_decisions_by_box(conn, project_id=project["id"])
        page_rows = _page_rows_for_project(conn, project_id=project["id"])
        box_rows = _current_boxes_for_project(conn, project_id=project["id"])
        pages_by_id = {str(row["id"]): row for row in page_rows}
        boxes_by_page: dict[str, list[dict[str, Any]]] = {}
        for row in box_rows:
            if row.get("stable_key") is None:
                continue
            if page_index is not None and int(row.get("page_index") or 0) != int(page_index):
                continue
            boxes_by_page.setdefault(str(row["page_id"]), []).append(row)
        jobs: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        cleaned_count = 0
        mask_count = 0
        quality_pages: list[dict[str, Any]] = []
        destructive_blockers: list[dict[str, Any]] = []
        for page_id, rows in boxes_by_page.items():
            page = pages_by_id.get(page_id)
            if page is None:
                continue
            selected_rows, sfx_warnings, sfx_decisions = _select_cleaning_rows(
                rows,
                decisions=decisions,
                box_ids=selected_box_ids,
                sfx_policy=sfx_policy,
            )
            if selected_box_ids is not None and not selected_rows and not sfx_warnings:
                continue
            input_image = workspace.path / str(page["image_path"])
            if not os.path.exists(_windows_long_path(input_image)):
                raise ValueError(f"BLOCKED_FILESYSTEM: source page image missing: {page['image_path']}")
            Image, _ImageOps = _load_pillow()
            with Image.open(_windows_long_path(input_image)) as source_image:
                image_size = source_image.size
            page_no = int(page["page_index"])
            source_page = _source_page_for_run_page(cleaning_dir, page_no)
            mask_path = masks_dir / f"page_{page_no:04d}_mask.png"
            output_path = cleaned_dir / f"page_{page_no:04d}_{mode}.png"
            region_decisions: list[dict[str, Any]] = []
            quality_warnings: list[str] = []
            if mode == "quality_inpaint":
                selected_rows, region_decisions, quality_warnings = (
                    _write_quality_cleaning_mask(
                        image_path=input_image,
                        rows=selected_rows,
                        mask_path=mask_path,
                        padding=max(0, int(mask_padding)),
                    )
                )
            else:
                _write_cleaning_mask(
                    image_size=image_size,
                    rows=selected_rows,
                    mask_path=mask_path,
                    padding=max(0, int(mask_padding)),
                )
            mask_count += 1
            output_image: Path | None = None
            adapter_warnings: list[str] = []
            status = "success"
            if adapter is not None and (selected_rows or mode == "quality_inpaint"):
                output_image, adapter_warnings, status = adapter.clean(
                    workspace=workspace,
                    image_path=input_image,
                    mask_path=mask_path,
                    output_path=output_path,
                    fill_color=color,
                )
                if mode == "quality_inpaint" and output_image is None:
                    with Image.open(_windows_long_path(input_image)) as source:
                        _save_png(
                            source.convert("RGB"),
                            output_path,
                            force=True,
                        )
                    output_image = output_path
                    adapter_warnings.append(
                        "conservative_source_preserved_without_inpaint"
                    )
                    status = "success"
                if output_image is not None:
                    cleaned_count += 1
            elif adapter is not None:
                adapter_warnings.append("no_cleanable_boxes_selected")
                status = "skipped"
            warnings = [*sfx_warnings, *quality_warnings, *adapter_warnings]
            all_warnings.extend(warnings)
            preserved_box_ids = [
                str(item["box_id"])
                for item in region_decisions
                if item.get("cleaning_policy") == "preserve"
            ]
            preserved_box_ids.extend(
                box_id
                for box_id, policy in sfx_decisions.items()
                if policy != "clean"
            )
            mask_ratio = 0.0
            if mode == "quality_inpaint":
                with Image.open(_windows_long_path(mask_path)) as generated_mask:
                    histogram = generated_mask.convert("L").histogram()
                    mask_ratio = sum(histogram[1:]) / max(
                        1, generated_mask.width * generated_mask.height
                    )
                page_quality = {
                    "page_id": page_id,
                    "page_index": page_no,
                    "source_page": source_page,
                    "mask_area_ratio": round(mask_ratio, 6),
                    "cleaned_box_count": len(selected_rows),
                    "preserved_box_count": len(preserved_box_ids),
                    "region_decisions": region_decisions,
                    "status": status,
                }
                quality_pages.append(page_quality)
                if mask_ratio > MANGA_PAGE_MASK_AREA_RATIO_LIMIT:
                    destructive_blockers.append(
                        {
                            "code": "excessive_cleaned_area_ratio",
                            "page_id": page_id,
                            "page_index": page_no,
                            "mask_area_ratio": round(mask_ratio, 6),
                        }
                    )
            job = {
                "schema_version": MANGA_CLEANING_SCHEMA_VERSION,
                "page_id": page_id,
                "page_index": page_no,
                "source_page": source_page,
                "box_ids": [str(row["stable_key"]) for row in selected_rows],
                "preserved_box_ids": sorted(set(preserved_box_ids)),
                "region_decisions": region_decisions,
                "mask_area_ratio": round(mask_ratio, 6),
                "adapter_id": adapter.adapter_id if adapter is not None else "mask_generator",
                "adapter_version": adapter.adapter_version if adapter is not None else "phase9g.mask.v1",
                "mode": mode,
                "mask_artifact": _relative_artifact(workspace, mask_path),
                "input_image_artifact": str(page["image_path"]),
                "output_image_artifact": _relative_artifact(workspace, output_image) if output_image else None,
                "warnings": warnings,
                "sfx_decisions": sfx_decisions,
                "cloud_used": False,
                "status": status,
            }
            jobs.append(job)
        if selected_box_ids is not None:
            found = {box_id for job in jobs for box_id in job["box_ids"]}
            missing = sorted(selected_box_ids - found)
            for box_id in missing:
                all_warnings.append(f"box_not_cleaned_or_sfx_skipped:{box_id}")
        status = "success"
        if jobs and all(job["status"] == "unavailable" for job in jobs):
            status = "unavailable"
        payload = {
            "schema_version": MANGA_CLEANING_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "mode": mode,
            "adapter_id": adapter.adapter_id if adapter is not None else "mask_generator",
            "status": status,
            "fill_color": f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}",
            "mask_padding": max(0, int(mask_padding)),
            "sfx_policy": sfx_policy,
            "cloud_used": False,
            "job_count": len(jobs),
            "mask_count": mask_count,
            "cleaned_page_count": cleaned_count,
            "warnings": all_warnings,
            "jobs": jobs,
        }
        if mode == "quality_inpaint":
            visual_diff_pages = _write_cleaning_quality_contact_sheet(
                workspace,
                jobs=jobs,
                output_path=contact_sheet_path,
            )
            if adapter is not None and any(
                _cleaning_report_blocks(report) for report in visual_diff_pages
            ):
                visual_diff_pages = _apply_cleaning_escalation_ladder(
                    workspace,
                    jobs=jobs,
                    initial_reports=visual_diff_pages,
                    adapter=adapter,
                    fill_color=color,
                    cleaning_dir=cleaning_dir,
                )
                jobs_by_page = {
                    int(job.get("page_index") or 0): job for job in jobs
                }
                for page_quality in quality_pages:
                    job = jobs_by_page.get(int(page_quality.get("page_index") or 0))
                    if job is not None:
                        page_quality["mask_area_ratio"] = job.get(
                            "mask_area_ratio", page_quality["mask_area_ratio"]
                        )
                        page_quality["cleaning_escalation"] = job.get(
                            "cleaning_escalation"
                        )
            for page_report in visual_diff_pages:
                if page_report["large_white_block_detected"]:
                    destructive_blockers.append(
                        {
                            "code": "large_white_block_detected",
                            "page_id": page_report.get("page_id"),
                            "page_index": page_report.get("page_index"),
                            "new_white_pixel_ratio": page_report[
                                "new_white_pixel_ratio"
                            ],
                        }
                    )
                if page_report["residual_text_suspected"]:
                    destructive_blockers.append(
                        {
                            "code": "residual_text_after_cleaning",
                            "page_id": page_report.get("page_id"),
                            "page_index": page_report.get("page_index"),
                            "residual_edge_ratio": page_report[
                                "residual_edge_ratio"
                            ],
                            "residual_edge_density": page_report[
                                "residual_edge_density"
                            ],
                        }
                    )
            jobs_by_page = {
                int(job.get("page_index") or 0): job for job in jobs
            }
            residual_diagnostics = []
            for page_report in visual_diff_pages:
                if not _cleaning_report_blocks(page_report):
                    continue
                job = jobs_by_page.get(int(page_report.get("page_index") or 0))
                if job is None:
                    continue
                diagnostic = _write_source_page_residual_diagnostics(
                    workspace,
                    job=job,
                    page_report=page_report,
                    cleaning_dir=cleaning_dir,
                )
                if diagnostic is not None:
                    residual_diagnostics.append(diagnostic)
            visual_diff_rel = _write_json_artifact(
                workspace,
                visual_diff_path,
                {
                    "schema_version": "phase9m1.cleaning_visual_diff.v1",
                    "project_slug": project_slug,
                    "run_id": run_id,
                    "pages": visual_diff_pages,
                },
            )
            white_block_rel = _write_json_artifact(
                workspace,
                white_block_path,
                {
                    "schema_version": "phase9m1.white_block_audit.v1",
                    "page_count": len(quality_pages),
                    "large_rectangle_fill_used": False,
                    "pages": quality_pages,
                },
            )
            mask_quality_rel = _write_json_artifact(
                workspace,
                mask_quality_path,
                {
                    "schema_version": "phase9m1.mask_quality.v1",
                    "pages": quality_pages,
                },
            )
            destructive_rel = _write_json_artifact(
                workspace,
                destructive_path,
                {
                    "schema_version": "phase9m1.destructive_cleaning_blockers.v1",
                    "blocker_count": len(destructive_blockers),
                    "blockers": destructive_blockers,
                },
            )
            payload["quality_artifacts"] = {
                "visual_diff_report": visual_diff_rel,
                "white_block_audit": white_block_rel,
                "mask_quality_report": mask_quality_rel,
                "destructive_cleaning_blockers": destructive_rel,
                "per_page_before_after_contact_sheet": _relative_artifact(
                    workspace, contact_sheet_path
                ),
            }
            payload["residual_diagnostics"] = residual_diagnostics
            payload["destructive_cleaning_blocker_count"] = len(
                destructive_blockers
            )
        jobs_rel = _write_json_artifact(workspace, jobs_path, payload)
        summary_path.write_text(
            "\n".join(
                [
                    "# Manga Cleaning Summary",
                    "",
                    f"- Schema version: `{MANGA_CLEANING_SCHEMA_VERSION}`",
                    f"- Project: `{project_slug}`",
                    f"- Run ID: `{run_id}`",
                    f"- Mode: `{mode}`",
                    f"- Status: `{status}`",
                    f"- Jobs: `{len(jobs)}`",
                    f"- Masks: `{mask_count}`",
                    f"- Cleaned pages: `{cleaned_count}`",
                    "- Cloud used: `false`",
                    f"- Warning count: `{len(all_warnings)}`",
                    "",
                    "## Warnings",
                    *(f"- `{warning}`" for warning in all_warnings),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        summary_rel = _relative_artifact(workspace, summary_path)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "mode": mode,
            "status": status,
            "job_count": len(jobs),
            "mask_count": mask_count,
            "cleaned_page_count": cleaned_count,
            "warning_count": len(all_warnings),
            "cloud_used": False,
            "cleaning_jobs_path": jobs_rel,
            "cleaning_summary_path": summary_rel,
            "jobs": jobs,
            "quality_artifacts": payload.get("quality_artifacts"),
            "residual_diagnostics": payload.get("residual_diagnostics", []),
            "destructive_cleaning_blocker_count": payload.get(
                "destructive_cleaning_blocker_count", 0
            ),
        }
        conn.execute(
            """
            INSERT INTO manga_cleaning_runs (
                id, run_id, project_id, project_slug, mode, adapter_id, jobs_path,
                summary_path, status, mask_count, cleaned_page_count, warning_count,
                cloud_used, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangacleanrun"),
                run_id,
                project["id"],
                project_slug,
                mode,
                payload["adapter_id"],
                jobs_rel,
                summary_rel,
                status,
                mask_count,
                cleaned_count,
                len(all_warnings),
                0,
                now,
                now,
            ),
        )
        update_task_run(
            conn,
            task_id=task_id,
            status="success",
            stage="completed",
            result_data=result,
        )
        conn.commit()
    return {"task_run_id": task_id, **result}


def generate_manga_cleaning_masks(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    page_index: int | None = None,
    box_ids: list[str] | None = None,
    mask_padding: int = 0,
    sfx_policy: str = "leave_unchanged",
) -> dict[str, Any]:
    return run_manga_cleaning(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        mode="mask",
        page_index=page_index,
        box_ids=box_ids,
        mask_padding=mask_padding,
        sfx_policy=sfx_policy,
    )


def save_manga_cleaning_decision(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    box_id: str,
    mode: str = "fill",
    sfx_policy: str = "leave_unchanged",
    reviewer: str = "cli",
    note: str | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    mode = _validate_cleaning_mode(mode)
    sfx_policy = _validate_sfx_policy(sfx_policy)
    now = utc_now()
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT b.stable_key
            FROM manga_boxes b
            JOIN manga_pages p ON p.id = b.page_id
            WHERE p.project_id = ? AND b.stable_key = ? AND b.deleted = 0
            LIMIT 1
            """,
            (project["id"], box_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Cleaning decision box not found: {box_id}")
        task_id = insert_task_run(
            conn,
            task_type="manga.cleaning.decision",
            status="running",
            stage="save_cleaning_decision",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "box_id": box_id,
                "mode": mode,
                "sfx_policy": sfx_policy,
            },
            result_data={},
        )
        existing = conn.execute(
            """
            SELECT id FROM manga_cleaning_decisions
            WHERE project_id = ? AND stable_box_id = ?
            LIMIT 1
            """,
            (project["id"], box_id),
        ).fetchone()
        if existing is None:
            decision_id = new_id("mangacleandec")
            conn.execute(
                """
                INSERT INTO manga_cleaning_decisions (
                    id, project_id, project_slug, run_id, stable_box_id,
                    mode, sfx_policy, reviewer, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    project["id"],
                    project_slug,
                    run_id,
                    box_id,
                    mode,
                    sfx_policy,
                    reviewer,
                    note,
                    now,
                    now,
                ),
            )
        else:
            decision_id = str(existing["id"])
            conn.execute(
                """
                UPDATE manga_cleaning_decisions
                SET run_id = ?, mode = ?, sfx_policy = ?, reviewer = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_id, mode, sfx_policy, reviewer, note, now, decision_id),
            )
        audit_path = _cleaning_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "cleaning_decisions.jsonl"
        audit_record = {
            "schema_version": MANGA_CLEANING_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "box_id": box_id,
            "mode": mode,
            "sfx_policy": sfx_policy,
            "reviewer": reviewer,
            "note": note,
            "created_at": now,
        }
        _append_jsonl(audit_path, audit_record)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "decision_id": decision_id,
            "box_id": box_id,
            "mode": mode,
            "sfx_policy": sfx_policy,
            "cleaning_decisions_path": _relative_artifact(workspace, audit_path),
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


def export_manga_cleaning(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    cleaning_dir = _cleaning_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    jobs_path = cleaning_dir / "cleaning_jobs.json"
    summary_path = cleaning_dir / "cleaning_summary.md"
    if not jobs_path.exists():
        raise ValueError(f"Cleaning jobs artifact not found for run {run_id}.")
    if not summary_path.exists():
        raise ValueError(f"Cleaning summary artifact not found for run {run_id}.")
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "status": payload.get("status"),
        "mode": payload.get("mode"),
        "job_count": payload.get("job_count", len(payload.get("jobs") or [])),
        "mask_count": payload.get("mask_count"),
        "cleaned_page_count": payload.get("cleaned_page_count"),
        "warning_count": len(payload.get("warnings") or []),
        "cleaning_jobs_path": _relative_artifact(workspace, jobs_path),
        "cleaning_summary_path": _relative_artifact(workspace, summary_path),
        "jobs": payload.get("jobs") or [],
    }


def _rendering_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    rendering_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "rendering"
    for child in [rendering_dir, rendering_dir / "rendered_pages"]:
        child.mkdir(parents=True, exist_ok=True)
    return rendering_dir


def _load_translation_results_artifact(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    translation_path = _translation_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "translation_results.json"
    if not translation_path.exists():
        raise ValueError(f"BLOCKED_TRANSLATION_ARTIFACTS: translation results not found for run {run_id}.")
    payload = json.loads(translation_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_TRANSLATION_SCHEMA_VERSION:
        raise ValueError("BLOCKED_TRANSLATION_ARTIFACTS: unsupported translation results schema.")
    if payload.get("project_slug") != project_slug or payload.get("run_id") != run_id:
        raise ValueError("BLOCKED_TRANSLATION_ARTIFACTS: translation project/run mismatch.")
    return payload


def _load_cleaning_jobs_artifact(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    jobs_path = _cleaning_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "cleaning_jobs.json"
    if not jobs_path.exists():
        raise ValueError(f"BLOCKED_CLEANED_ARTIFACTS: cleaning jobs not found for run {run_id}.")
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_CLEANING_SCHEMA_VERSION:
        raise ValueError("BLOCKED_CLEANED_ARTIFACTS: unsupported cleaning jobs schema.")
    if payload.get("project_slug") != project_slug or payload.get("run_id") != run_id:
        raise ValueError("BLOCKED_CLEANED_ARTIFACTS: cleaning project/run mismatch.")
    return payload


def _latest_cleaned_pages_by_page_id(cleaning_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cleaned: dict[str, dict[str, Any]] = {}
    for job in cleaning_payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        output = job.get("output_image_artifact")
        if job.get("status") == "success" and output:
            cleaned[str(job.get("page_id"))] = job
    return cleaned


def _validate_render_alignment(alignment: str) -> str:
    if alignment not in MANGA_RENDERING_ALIGNMENTS:
        raise ValueError(f"Invalid rendering alignment: {alignment}. Expected one of {sorted(MANGA_RENDERING_ALIGNMENTS)}.")
    return alignment


def _validate_render_direction(direction: str) -> str:
    if direction not in MANGA_RENDERING_DIRECTIONS:
        raise ValueError(f"Invalid rendering direction: {direction}. Expected one of {sorted(MANGA_RENDERING_DIRECTIONS)}.")
    return direction


def _parse_rgb_color(value: str, *, label: str) -> tuple[int, int, int]:
    color = str(value or "").strip()
    aliases = {"black": "#000000", "white": "#ffffff"}
    color = aliases.get(color.lower(), color)
    if color.startswith("#"):
        color = color[1:]
    if len(color) != 6 or any(char not in "0123456789abcdefABCDEF" for char in color):
        raise ValueError(f"{label} must be a hex RGB value like #111111.")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _color_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _font_candidates(font_family: str | None) -> list[str]:
    candidates: list[str] = []
    if font_family:
        candidates.append(font_family)
    candidates.extend(
        [
            "arial.ttf",
            "segoeui.ttf",
            "calibri.ttf",
            "tahoma.ttf",
            "DejaVuSans.ttf",
            "NotoSans-Regular.ttf",
            "LiberationSans-Regular.ttf",
        ]
    )
    windows_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    candidates.extend(
        [
            str(windows_fonts / "arial.ttf"),
            str(windows_fonts / "segoeui.ttf"),
            str(windows_fonts / "calibri.ttf"),
            str(windows_fonts / "tahoma.ttf"),
        ]
    )
    return candidates


def _load_render_font(
    *,
    font_path: str | None,
    font_family: str | None,
    font_size: int,
) -> tuple[Any, str, str]:
    _Image, _ImageOps = _load_pillow()
    from PIL import ImageFont

    if font_path:
        path = Path(font_path).expanduser()
        if not path.exists():
            raise ValueError(f"BLOCKED_FONT_CONFIG: font path not found: {font_path}")
        try:
            return ImageFont.truetype(str(path), font_size), path.stem, str(path)
        except Exception as exc:
            raise ValueError(f"BLOCKED_FONT_CONFIG: unable to load font path: {font_path}") from exc
    for candidate in _font_candidates(font_family):
        try:
            font = ImageFont.truetype(candidate, font_size)
            return font, font_family or Path(candidate).stem, candidate
        except Exception:
            continue
    return ImageFont.load_default(), font_family or "PillowDefault", "pillow_default"


def _text_bbox(draw: Any, text: str, font: Any, *, stroke_width: int) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text or " ", font=font, stroke_width=stroke_width)


def _text_width(draw: Any, text: str, font: Any, *, stroke_width: int) -> int:
    left, _top, right, _bottom = _text_bbox(draw, text, font, stroke_width=stroke_width)
    return int(right - left)


def _split_long_word(
    word: str,
    *,
    draw: Any,
    font: Any,
    max_width: int,
    stroke_width: int,
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_width(draw, candidate, font, stroke_width=stroke_width) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [word]


def _wrap_vietnamese_text(
    text: str,
    *,
    draw: Any,
    font: Any,
    max_width: int,
    stroke_width: int,
) -> tuple[list[str], bool]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return [""], False
    if max_width <= 0:
        return [normalized], True
    lines: list[str] = []
    current = ""
    long_word_split = False
    for word in normalized.split(" "):
        if _text_width(draw, word, font, stroke_width=stroke_width) > max_width:
            long_word_split = True
            chunks = _split_long_word(word, draw=draw, font=font, max_width=max_width, stroke_width=stroke_width)
            if current:
                lines.append(current)
                current = ""
            lines.extend(chunks[:-1])
            current = chunks[-1]
            continue
        candidate = f"{current} {word}".strip()
        if not current or _text_width(draw, candidate, font, stroke_width=stroke_width) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""], long_word_split


def _fit_text_to_box(
    text: str,
    *,
    bbox: list[float],
    settings: dict[str, Any],
    draw: Any,
) -> tuple[RenderFit, Any, str, str]:
    _x, _y, width, height = bbox
    max_width = max(1, int(round(width)))
    max_height = max(1, int(round(height)))
    min_font = max(1, int(settings["min_font_size"]))
    max_font = max(min_font, int(settings["max_font_size"]))
    line_height = max(1.0, float(settings["line_height"]))
    stroke_width = max(0, int(settings["stroke_width"]))
    direction = _validate_render_direction(str(settings["direction"]))
    last_fit: RenderFit | None = None
    last_font: Any | None = None
    last_family = ""
    last_source = ""
    if direction == "vertical":
        font, family, source = _load_render_font(
            font_path=settings.get("font_path"),
            font_family=settings.get("font_family"),
            font_size=max_font,
        )
        return (
            RenderFit(
                lines=[str(text or "")],
                font_size=max_font,
                line_height_px=max(1, int(round(max_font * line_height))),
                line_count=1,
                overflow=True,
                overflow_reason="vertical_rendering_not_supported",
            ),
            font,
            family,
            source,
        )
    for size in range(max_font, min_font - 1, -1):
        font, family, source = _load_render_font(
            font_path=settings.get("font_path"),
            font_family=settings.get("font_family"),
            font_size=size,
        )
        lines, long_word_split = _wrap_vietnamese_text(
            text,
            draw=draw,
            font=font,
            max_width=max_width,
            stroke_width=stroke_width,
        )
        line_height_px = max(1, int(round(size * line_height)))
        total_height = line_height_px * len(lines)
        max_line_width = max(_text_width(draw, line, font, stroke_width=stroke_width) for line in lines)
        overflow = max_line_width > max_width or total_height > max_height
        reason: str | None = None
        if overflow:
            if max_line_width > max_width:
                reason = "width_exceeds_box"
            if total_height > max_height:
                reason = "height_exceeds_box" if reason is None else f"{reason};height_exceeds_box"
        elif long_word_split:
            reason = "long_word_split"
        fit = RenderFit(
            lines=lines,
            font_size=size,
            line_height_px=line_height_px,
            line_count=len(lines),
            overflow=overflow,
            overflow_reason=reason,
        )
        last_fit = fit
        last_font = font
        last_family = family
        last_source = source
        if not overflow:
            return fit, font, family, source
    assert last_fit is not None and last_font is not None
    return last_fit, last_font, last_family, last_source


def _draw_rendered_text(
    *,
    draw: Any,
    bbox: list[float],
    fit: RenderFit,
    font: Any,
    settings: dict[str, Any],
    text_color: tuple[int, int, int],
    stroke_fill: tuple[int, int, int],
    shadow_fill: tuple[int, int, int],
) -> None:
    x, y, width, height = bbox
    if fit.overflow_reason == "vertical_rendering_not_supported":
        return
    alignment = _validate_render_alignment(str(settings["alignment"]))
    stroke_width = max(0, int(settings["stroke_width"]))
    shadow_offset_x = int(settings["shadow_offset_x"])
    shadow_offset_y = int(settings["shadow_offset_y"])
    total_height = fit.line_height_px * fit.line_count
    current_y = int(round(y)) if fit.overflow else int(round(y + max(0, (height - total_height) / 2)))
    for line in fit.lines:
        line_width = _text_width(draw, line, font, stroke_width=stroke_width)
        if alignment == "right":
            current_x = int(round(x + width - line_width))
        elif alignment == "center":
            current_x = int(round(x + max(0, (width - line_width) / 2)))
        else:
            current_x = int(round(x))
        if shadow_offset_x or shadow_offset_y:
            draw.text(
                (current_x + shadow_offset_x, current_y + shadow_offset_y),
                line,
                font=font,
                fill=shadow_fill,
                stroke_width=stroke_width,
                stroke_fill=shadow_fill,
            )
        draw.text(
            (current_x, current_y),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        current_y += fit.line_height_px


def _default_render_settings(
    *,
    font_path: str | None,
    font_family: str | None,
    min_font_size: int,
    max_font_size: int,
    line_height: float,
    alignment: str,
    text_color: str,
    stroke_width: int,
    stroke_fill: str,
    shadow_offset_x: int,
    shadow_offset_y: int,
    shadow_fill: str,
    direction: str,
) -> dict[str, Any]:
    if min_font_size <= 0 or max_font_size <= 0 or min_font_size > max_font_size:
        raise ValueError("Font size range must be positive and min_font_size <= max_font_size.")
    if line_height < 1.0:
        raise ValueError("Line height must be >= 1.0.")
    if stroke_width < 0:
        raise ValueError("Stroke width must be >= 0.")
    return {
        "font_path": font_path,
        "font_family": font_family,
        "min_font_size": int(min_font_size),
        "max_font_size": int(max_font_size),
        "line_height": float(line_height),
        "alignment": _validate_render_alignment(alignment),
        "text_color": _color_hex(_parse_rgb_color(text_color, label="Text color")),
        "stroke_width": int(stroke_width),
        "stroke_fill": _color_hex(_parse_rgb_color(stroke_fill, label="Stroke fill")),
        "shadow_offset_x": int(shadow_offset_x),
        "shadow_offset_y": int(shadow_offset_y),
        "shadow_fill": _color_hex(_parse_rgb_color(shadow_fill, label="Shadow fill")),
        "direction": _validate_render_direction(direction),
    }


def _render_settings_by_box(conn, *, project_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT stable_box_id, font_family, font_path, min_font_size, max_font_size,
               line_height, alignment, text_color, stroke_width, stroke_fill,
               shadow_offset_x, shadow_offset_y, shadow_fill, direction, manual_fit_json,
               reviewer, note, updated_at
        FROM manga_render_settings
        WHERE project_id = ?
        ORDER BY updated_at ASC, id ASC
        """,
        (project_id,),
    ).fetchall()
    settings: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = row_to_dict(row, json_fields=("manual_fit_json",))
        settings[str(row["stable_box_id"])] = {key: value for key, value in item.items() if value is not None}
    return settings


class PillowMvpRenderer:
    adapter_id = "pillow"
    adapter_version = MANGA_RENDERING_RENDERER_VERSION
    execution_mode = "local"

    def render_page(
        self,
        *,
        workspace: Workspace,
        cleaned_image_path: Path,
        output_path: Path,
        page_records: list[dict[str, Any]],
        box_lookup: dict[str, dict[str, Any]],
        default_settings: dict[str, Any],
    ) -> tuple[Path, list[dict[str, Any]]]:
        Image, _ImageOps = _load_pillow()
        from PIL import ImageDraw

        with Image.open(_windows_long_path(cleaned_image_path)) as image:
            rendered = image.convert("RGB").copy()
        draw = ImageDraw.Draw(rendered)
        decisions: list[dict[str, Any]] = []
        for record in page_records:
            box_id = str(record["box_id"])
            box = box_lookup[box_id]
            settings = {**default_settings, **(record.get("render_settings") or {})}
            text_color = _parse_rgb_color(str(settings["text_color"]), label="Text color")
            stroke_fill = _parse_rgb_color(str(settings["stroke_fill"]), label="Stroke fill")
            shadow_fill = _parse_rgb_color(str(settings["shadow_fill"]), label="Shadow fill")
            bbox = _validate_bbox(box.get("bbox_json"), box_label=f"rendering box {box_id}")
            fit, font, font_family, font_source = _fit_text_to_box(
                str(record.get("translated_text") or ""),
                bbox=bbox,
                settings=settings,
                draw=draw,
            )
            _draw_rendered_text(
                draw=draw,
                bbox=bbox,
                fit=fit,
                font=font,
                settings=settings,
                text_color=text_color,
                stroke_fill=stroke_fill,
                shadow_fill=shadow_fill,
            )
            decisions.append(
                {
                    "schema_version": MANGA_RENDERING_SCHEMA_VERSION,
                    "page_id": record["page_id"],
                    "page_index": record.get("page_index"),
                    "box_id": box_id,
                    "translated_text": str(record.get("translated_text") or ""),
                    "font_family": font_family,
                    "font_source": font_source,
                    "font_size": fit.font_size,
                    "line_count": fit.line_count,
                    "lines": fit.lines,
                    "line_height_px": fit.line_height_px,
                    "alignment": settings["alignment"],
                    "bbox": bbox,
                    "overflow": fit.overflow,
                    "overflow_reason": fit.overflow_reason,
                    "direction": settings["direction"],
                    "text_color": settings["text_color"],
                    "stroke_width": settings["stroke_width"],
                    "stroke_fill": settings["stroke_fill"],
                    "shadow_offset": [settings["shadow_offset_x"], settings["shadow_offset_y"]],
                    "shadow_fill": settings["shadow_fill"],
                    "output_artifact": _relative_artifact(workspace, output_path),
                    "renderer_id": self.adapter_id,
                    "renderer_version": self.adapter_version,
                }
            )
        _save_png(rendered, output_path, force=True)
        return output_path, decisions


def _rendering_adapter(renderer_id: str) -> RendererAdapter:
    if renderer_id in {"pillow", "pillow_mvp"}:
        return PillowMvpRenderer()
    raise ValueError(f"Unsupported rendering adapter: {renderer_id}")


def run_manga_rendering(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    renderer_id: str = "pillow",
    page_index: int | None = None,
    box_ids: list[str] | None = None,
    font_path: str | None = None,
    font_family: str | None = None,
    min_font_size: int = 10,
    max_font_size: int = 24,
    line_height: float = 1.15,
    alignment: str = "center",
    text_color: str = "#111111",
    stroke_width: int = 0,
    stroke_fill: str = "#ffffff",
    shadow_offset_x: int = 0,
    shadow_offset_y: int = 0,
    shadow_fill: str = "#000000",
    direction: str = "horizontal",
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    selected_box_ids = {str(box_id) for box_id in box_ids} if box_ids else None
    default_settings = _default_render_settings(
        font_path=font_path,
        font_family=font_family,
        min_font_size=min_font_size,
        max_font_size=max_font_size,
        line_height=line_height,
        alignment=alignment,
        text_color=text_color,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
        shadow_offset_x=shadow_offset_x,
        shadow_offset_y=shadow_offset_y,
        shadow_fill=shadow_fill,
        direction=direction,
    )
    translation_payload = _load_translation_results_artifact(workspace, project_slug=project_slug, run_id=run_id)
    cleaning_payload = _load_cleaning_jobs_artifact(workspace, project_slug=project_slug, run_id=run_id)
    cleaned_pages = _latest_cleaned_pages_by_page_id(cleaning_payload)
    quality_mode = cleaning_payload.get("mode") == "quality_inpaint"
    if not cleaned_pages:
        raise ValueError(f"BLOCKED_CLEANED_ARTIFACTS: no successful cleaned page outputs found for run {run_id}.")
    adapter = _rendering_adapter(renderer_id)
    rendering_dir = _rendering_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    rendered_pages_dir = rendering_dir / "rendered_pages"
    decisions_path = rendering_dir / "typeset_decisions.json"
    overflow_path = rendering_dir / "overflow_report.json"
    summary_path = rendering_dir / "rendering_summary.md"
    now = utc_now()

    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.rendering",
            status="running",
            stage="render_pages",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "renderer_id": renderer_id,
                "page_index": page_index,
                "box_ids": sorted(selected_box_ids) if selected_box_ids else None,
                "cloud_used": False,
            },
            result_data={},
        )
        box_rows = _current_boxes_for_project(conn, project_id=project["id"])
        box_lookup = {str(row["stable_key"]): row for row in box_rows if row.get("stable_key")}
        saved_settings = _render_settings_by_box(conn, project_id=project["id"])
        page_records: dict[str, list[dict[str, Any]]] = {}
        skipped: list[str] = []
        for result in translation_payload.get("results") or []:
            box_id = str(result.get("box_id") or "")
            page_id = str(result.get("page_id") or "")
            if not box_id or not page_id:
                continue
            if page_index is not None and int(result.get("page_index") or 0) != int(page_index):
                continue
            if selected_box_ids is not None and box_id not in selected_box_ids:
                continue
            cleaned_job = cleaned_pages.get(page_id)
            if cleaned_job is None:
                skipped.append(f"page_not_cleaned:{page_id}:{box_id}")
                continue
            cleaned_box_ids = {str(item) for item in cleaned_job.get("box_ids") or []}
            if box_id not in cleaned_box_ids:
                if box_id in {
                    str(item) for item in cleaned_job.get("preserved_box_ids") or []
                }:
                    skipped.append(f"box_preserved_artwork:{box_id}")
                else:
                    skipped.append(f"box_not_cleaned_skipped:{box_id}")
                continue
            if box_id not in box_lookup:
                raise ValueError(f"BLOCKED_RENDERING_BOX_LINK: current box not found for translation box {box_id}.")
            record = dict(result)
            record["render_settings"] = saved_settings.get(box_id, {})
            page_records.setdefault(page_id, []).append(record)
        if selected_box_ids is not None:
            rendered_ids = {str(record["box_id"]) for records in page_records.values() for record in records}
            missing = sorted(selected_box_ids - rendered_ids)
            if missing:
                raise ValueError(
                    "BLOCKED_CLEANED_ARTIFACTS: selected boxes do not have cleaned regions: "
                    + ", ".join(missing)
                )
        if quality_mode:
            for preserved_page_id, cleaned_job in cleaned_pages.items():
                if page_index is not None and int(
                    cleaned_job.get("page_index") or 0
                ) != int(page_index):
                    continue
                page_records.setdefault(preserved_page_id, [])
        if not page_records:
            raise ValueError("BLOCKED_CLEANED_ARTIFACTS: no translated boxes have cleaned regions to render.")
        all_decisions: list[dict[str, Any]] = []
        rendered_pages: list[dict[str, Any]] = []
        for page_id, records in sorted(
            page_records.items(),
            key=lambda item: int(
                (item[1][0].get("page_index") if item[1] else None)
                or cleaned_pages[item[0]].get("page_index")
                or 0
            ),
        ):
            cleaned_job = cleaned_pages[page_id]
            cleaned_path = workspace.path / str(cleaned_job["output_image_artifact"])
            if not cleaned_path.exists():
                raise ValueError(
                    f"BLOCKED_CLEANED_ARTIFACTS: cleaned page image missing: {cleaned_job['output_image_artifact']}"
                )
            page_no = int(
                (records[0].get("page_index") if records else None)
                or cleaned_job.get("page_index")
                or 0
            )
            output_path = rendered_pages_dir / f"page_{page_no:04d}_rendered.png"
            output, page_decisions = adapter.render_page(
                workspace=workspace,
                cleaned_image_path=cleaned_path,
                output_path=output_path,
                page_records=records,
                box_lookup=box_lookup,
                default_settings=default_settings,
            )
            all_decisions.extend(page_decisions)
            rendered_pages.append(
                {
                    "page_id": page_id,
                    "page_index": page_no,
                    "input_cleaned_artifact": cleaned_job["output_image_artifact"],
                    "output_artifact": _relative_artifact(workspace, output),
                    "decision_count": len(page_decisions),
                    "overflow_count": len([decision for decision in page_decisions if decision["overflow"]]),
                }
            )
        blockers = [
            {
                "page_id": decision["page_id"],
                "page_index": decision.get("page_index"),
                "box_id": decision["box_id"],
                "overflow_reason": decision.get("overflow_reason") or "unknown",
                "bbox": decision["bbox"],
                "font_size": decision["font_size"],
                "line_count": decision["line_count"],
            }
            for decision in all_decisions
            if decision["overflow"]
        ]
        validation_status = "blocked" if blockers else "pass"
        decisions_payload = {
            "schema_version": MANGA_RENDERING_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "renderer_id": adapter.adapter_id,
            "renderer_version": adapter.adapter_version,
            "cloud_used": False,
            "decision_count": len(all_decisions),
            "rendered_page_count": len(rendered_pages),
            "rendered_pages": rendered_pages,
            "skipped": skipped,
            "preserved_box_ids": sorted(
                {
                    str(box_id)
                    for job in cleaning_payload.get("jobs") or []
                    for box_id in job.get("preserved_box_ids") or []
                }
            ),
            "decisions": all_decisions,
        }
        overflow_payload = {
            "schema_version": MANGA_RENDERING_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "validation_status": validation_status,
            "overflow_count": len(blockers),
            "blockers": blockers,
            "skipped": skipped,
        }
        decisions_rel = _write_json_artifact(workspace, decisions_path, decisions_payload)
        overflow_rel = _write_json_artifact(workspace, overflow_path, overflow_payload)
        summary_path.write_text(
            "\n".join(
                [
                    "# Manga Rendering Summary",
                    "",
                    f"- Schema version: `{MANGA_RENDERING_SCHEMA_VERSION}`",
                    f"- Project: `{project_slug}`",
                    f"- Run ID: `{run_id}`",
                    f"- Renderer: `{adapter.adapter_id}`",
                    f"- Rendered pages: `{len(rendered_pages)}`",
                    f"- Decisions: `{len(all_decisions)}`",
                    f"- Overflow blockers: `{len(blockers)}`",
                    f"- Validation status: `{validation_status}`",
                    "- Cloud used: `false`",
                    "",
                    "## Artifacts",
                    f"- Typeset decisions: `{decisions_rel}`",
                    f"- Overflow report: `{overflow_rel}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        summary_rel = _relative_artifact(workspace, summary_path)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "renderer_id": adapter.adapter_id,
            "renderer_version": adapter.adapter_version,
            "validation_status": validation_status,
            "rendered_page_count": len(rendered_pages),
            "decision_count": len(all_decisions),
            "overflow_count": len(blockers),
            "cloud_used": False,
            "rendered_pages_dir": _relative_artifact(workspace, rendered_pages_dir),
            "rendered_pages": rendered_pages,
            "typeset_decisions_path": decisions_rel,
            "overflow_report_path": overflow_rel,
            "rendering_summary_path": summary_rel,
            "skipped": skipped,
        }
        conn.execute(
            """
            INSERT INTO manga_rendering_runs (
                id, run_id, project_id, project_slug, renderer_id, rendered_pages_path,
                decisions_path, overflow_path, summary_path, validation_status,
                decision_count, overflow_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangarendrun"),
                run_id,
                project["id"],
                project_slug,
                adapter.adapter_id,
                result["rendered_pages_dir"],
                decisions_rel,
                overflow_rel,
                summary_rel,
                validation_status,
                len(all_decisions),
                len(blockers),
                now,
                now,
            ),
        )
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def save_manga_render_settings(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    box_id: str,
    font_path: str | None = None,
    font_family: str | None = None,
    min_font_size: int | None = None,
    max_font_size: int | None = None,
    line_height: float | None = None,
    alignment: str | None = None,
    text_color: str | None = None,
    stroke_width: int | None = None,
    stroke_fill: str | None = None,
    shadow_offset_x: int | None = None,
    shadow_offset_y: int | None = None,
    shadow_fill: str | None = None,
    direction: str | None = None,
    manual_fit_json: str | None = None,
    reviewer: str = "cli",
    note: str | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    now = utc_now()
    if alignment is not None:
        alignment = _validate_render_alignment(alignment)
    if direction is not None:
        direction = _validate_render_direction(direction)
    if text_color is not None:
        text_color = _color_hex(_parse_rgb_color(text_color, label="Text color"))
    if stroke_fill is not None:
        stroke_fill = _color_hex(_parse_rgb_color(stroke_fill, label="Stroke fill"))
    if shadow_fill is not None:
        shadow_fill = _color_hex(_parse_rgb_color(shadow_fill, label="Shadow fill"))
    manual_fit_payload: dict[str, Any] | None = None
    if manual_fit_json is not None:
        try:
            parsed_manual_fit = json.loads(manual_fit_json)
        except json.JSONDecodeError as exc:
            raise ValueError("Manual fit JSON must be a JSON object.") from exc
        if not isinstance(parsed_manual_fit, dict):
            raise ValueError("Manual fit JSON must be a JSON object.")
        manual_fit_payload = parsed_manual_fit
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT b.stable_key
            FROM manga_boxes b
            JOIN manga_pages p ON p.id = b.page_id
            WHERE p.project_id = ? AND b.stable_key = ? AND b.deleted = 0
            LIMIT 1
            """,
            (project["id"], box_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Render settings box not found: {box_id}")
        task_id = insert_task_run(
            conn,
            task_type="manga.rendering.settings",
            status="running",
            stage="save_render_settings",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "box_id": box_id},
            result_data={},
        )
        existing = conn.execute(
            """
            SELECT id FROM manga_render_settings
            WHERE project_id = ? AND stable_box_id = ?
            LIMIT 1
            """,
            (project["id"], box_id),
        ).fetchone()
        payload = {
            "font_family": font_family,
            "font_path": font_path,
            "min_font_size": min_font_size,
            "max_font_size": max_font_size,
            "line_height": line_height,
            "alignment": alignment,
            "text_color": text_color,
            "stroke_width": stroke_width,
            "stroke_fill": stroke_fill,
            "shadow_offset_x": shadow_offset_x,
            "shadow_offset_y": shadow_offset_y,
            "shadow_fill": shadow_fill,
            "direction": direction,
            "manual_fit_json": manual_fit_payload,
            "reviewer": reviewer,
            "note": note,
        }
        if existing is None:
            setting_id = new_id("mangarendset")
            conn.execute(
                """
                INSERT INTO manga_render_settings (
                    id, project_id, project_slug, run_id, stable_box_id, font_family, font_path,
                    min_font_size, max_font_size, line_height, alignment, text_color,
                    stroke_width, stroke_fill, shadow_offset_x, shadow_offset_y, shadow_fill,
                    direction, manual_fit_json, reviewer, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    setting_id,
                    project["id"],
                    project_slug,
                    run_id,
                    box_id,
                    font_family,
                    font_path,
                    min_font_size,
                    max_font_size,
                    line_height,
                    alignment,
                    text_color,
                    stroke_width,
                    stroke_fill,
                    shadow_offset_x,
                    shadow_offset_y,
                    shadow_fill,
                    direction,
                    json_dumps(manual_fit_payload) if manual_fit_payload is not None else None,
                    reviewer,
                    note,
                    now,
                    now,
                ),
            )
        else:
            setting_id = str(existing["id"])
            conn.execute(
                """
                UPDATE manga_render_settings
                SET run_id = ?, font_family = COALESCE(?, font_family),
                    font_path = COALESCE(?, font_path),
                    min_font_size = COALESCE(?, min_font_size),
                    max_font_size = COALESCE(?, max_font_size),
                    line_height = COALESCE(?, line_height),
                    alignment = COALESCE(?, alignment),
                    text_color = COALESCE(?, text_color),
                    stroke_width = COALESCE(?, stroke_width),
                    stroke_fill = COALESCE(?, stroke_fill),
                    shadow_offset_x = COALESCE(?, shadow_offset_x),
                    shadow_offset_y = COALESCE(?, shadow_offset_y),
                    shadow_fill = COALESCE(?, shadow_fill),
                    direction = COALESCE(?, direction),
                    manual_fit_json = COALESCE(?, manual_fit_json),
                    reviewer = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    run_id,
                    font_family,
                    font_path,
                    min_font_size,
                    max_font_size,
                    line_height,
                    alignment,
                    text_color,
                    stroke_width,
                    stroke_fill,
                    shadow_offset_x,
                    shadow_offset_y,
                    shadow_fill,
                    direction,
                    json_dumps(manual_fit_payload) if manual_fit_payload is not None else None,
                    reviewer,
                    note,
                    now,
                    setting_id,
                ),
            )
        audit_path = _rendering_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "render_settings.jsonl"
        audit_record = {
            "schema_version": MANGA_RENDERING_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "box_id": box_id,
            "settings": {key: value for key, value in payload.items() if value is not None},
            "created_at": now,
        }
        _append_jsonl(audit_path, audit_record)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "setting_id": setting_id,
            "box_id": box_id,
            "render_settings_path": _relative_artifact(workspace, audit_path),
        }
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def export_manga_rendering_overflow(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    rendering_dir = _rendering_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    overflow_path = rendering_dir / "overflow_report.json"
    if not overflow_path.exists():
        raise ValueError(f"Rendering overflow report not found for run {run_id}.")
    payload = json.loads(overflow_path.read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "validation_status": payload.get("validation_status"),
        "overflow_count": payload.get("overflow_count", 0),
        "blockers": payload.get("blockers") or [],
        "overflow_report_path": _relative_artifact(workspace, overflow_path),
    }


def validate_manga_rendering(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    rendering_dir = _rendering_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    decisions_path = rendering_dir / "typeset_decisions.json"
    overflow_path = rendering_dir / "overflow_report.json"
    if not decisions_path.exists():
        raise ValueError(f"Rendering decisions artifact not found for run {run_id}.")
    if not overflow_path.exists():
        raise ValueError(f"Rendering overflow report not found for run {run_id}.")
    decisions_payload = json.loads(decisions_path.read_text(encoding="utf-8"))
    overflow_payload = json.loads(overflow_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    seen_outputs: set[str] = set()
    for decision in decisions_payload.get("decisions") or []:
        output = str(decision.get("output_artifact") or "")
        if not output:
            issues.append(f"missing_output_artifact:{decision.get('box_id')}")
            continue
        if output not in seen_outputs and not (workspace.path / output).exists():
            issues.append(f"rendered_page_missing:{output}")
        seen_outputs.add(output)
    overflow_count = int(overflow_payload.get("overflow_count") or 0)
    validation_status = "valid" if not issues and overflow_count == 0 else "blocked"
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "validation_status": validation_status,
        "decision_count": decisions_payload.get("decision_count", 0),
        "rendered_output_count": len(seen_outputs),
        "overflow_count": overflow_count,
        "issues": issues,
        "typeset_decisions_path": _relative_artifact(workspace, decisions_path),
        "overflow_report_path": _relative_artifact(workspace, overflow_path),
    }


def _qa_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    artifact_root = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
    qa_dir = artifact_root / "qa"
    human_review_dir = artifact_root / "human_review"
    for child in [qa_dir, human_review_dir, human_review_dir / "previews"]:
        child.mkdir(parents=True, exist_ok=True)
    return qa_dir


def _human_review_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    return _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "human_review"


def _load_ocr_results_artifact(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    path = _ocr_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "ocr_results.json"
    if not path.exists():
        raise ValueError(f"BLOCKED_OCR_MISSING: OCR results not found for run {run_id}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_OCR_SCHEMA_VERSION:
        raise ValueError("BLOCKED_OCR_MISSING: unsupported OCR results schema.")
    return payload


def _load_detection_artifact_if_available(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any] | None:
    path = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "detection" / "regions.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_DETECTION_SCHEMA_VERSION:
        return None
    return payload


def _load_reading_order_artifact_if_available(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any] | None:
    path = _reading_order_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "reading_order.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANGA_READING_ORDER_SCHEMA_VERSION:
        return None
    return payload


def _load_rendering_artifacts(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rendering_dir = _rendering_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    decisions_path = rendering_dir / "typeset_decisions.json"
    overflow_path = rendering_dir / "overflow_report.json"
    if not decisions_path.exists() or not overflow_path.exists():
        raise ValueError(f"BLOCKED_RENDERED_ARTIFACTS: rendering artifacts not found for run {run_id}.")
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    overflow = json.loads(overflow_path.read_text(encoding="utf-8"))
    if decisions.get("schema_version") != MANGA_RENDERING_SCHEMA_VERSION:
        raise ValueError("BLOCKED_RENDERED_ARTIFACTS: unsupported rendering decisions schema.")
    if overflow.get("schema_version") != MANGA_RENDERING_SCHEMA_VERSION:
        raise ValueError("BLOCKED_RENDERED_ARTIFACTS: unsupported rendering overflow schema.")
    return decisions, overflow


def _issue_severity_from_score(
    value: float | None,
    *,
    warning_threshold: float,
    blocker_threshold: float,
) -> str | None:
    if value is None:
        return None
    if value < blocker_threshold:
        return "blocker"
    if value < warning_threshold:
        return "warning"
    return None


def _qa_issue(
    *,
    run_id: str,
    code: str,
    severity: str,
    page_id: str | None,
    box_id: str | None,
    stage: str,
    message: str,
    artifact_ref: str | None,
    recommended_action: str,
    blocks_export: bool | None = None,
) -> dict[str, Any]:
    normalized_severity = "blocker" if severity == "blocker" else "warning"
    return {
        "issue_id": _stable_id("mangaqa", run_id, code, page_id or "", box_id or "", artifact_ref or ""),
        "code": code,
        "severity": normalized_severity,
        "page_id": page_id,
        "box_id": box_id,
        "stage": stage,
        "message": _truncate_text(message, 220),
        "artifact_ref": artifact_ref,
        "recommended_action": _truncate_text(recommended_action, 220),
        "blocks_export": bool(blocks_export if blocks_export is not None else normalized_severity == "blocker"),
    }


def _issue_statuses_by_id(conn, *, project_id: str, run_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT issue_id, status, reviewer, note, updated_at
        FROM manga_visual_qa_issue_statuses
        WHERE project_id = ? AND run_id = ?
        """,
        (project_id, run_id),
    ).fetchall()
    return {str(row["issue_id"]): row_to_dict(row) for row in rows}


def _apply_issue_statuses(issues: list[dict[str, Any]], statuses: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for issue in issues:
        status = statuses.get(str(issue["issue_id"]), {})
        normalized = dict(issue)
        review_status = str(status.get("status") or "open")
        normalized["review_status"] = review_status
        normalized["reviewer"] = status.get("reviewer")
        normalized["review_note"] = _truncate_text(str(status.get("note") or ""), 220) if status.get("note") else None
        normalized["effective_blocks_export"] = bool(issue["blocks_export"] and review_status != "resolved")
        applied.append(normalized)
    return applied


def _page_metadata_by_id(page_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}
    for page in page_manifest.get("pages") or []:
        if isinstance(page, dict) and page.get("page_id"):
            pages[str(page["page_id"])] = page
    return pages


def _make_bounded_preview(
    workspace: Workspace,
    *,
    source_relpath: str,
    preview_path: Path,
    max_dimension: int,
) -> str:
    Image, _ImageOps = _load_pillow()
    source_path = workspace.path / source_relpath
    if not source_path.exists():
        raise ValueError(f"BLOCKED_RENDERED_ARTIFACTS: rendered preview source missing: {source_relpath}")
    with Image.open(_windows_long_path(source_path)) as image:
        preview = image.convert("RGB")
        preview.thumbnail((max(1, int(max_dimension)), max(1, int(max_dimension))))
        _save_png(preview, preview_path, force=True)
    return _relative_artifact(workspace, preview_path)


def _check_page_order(
    *,
    run_id: str,
    page_manifest: dict[str, Any],
    rendering_decisions: dict[str, Any],
    artifact_ref: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pages = [page for page in page_manifest.get("pages") or [] if isinstance(page, dict)]
    page_indexes = [int(page.get("page_index") or 0) for page in pages]
    expected_indexes = list(range(1, len(page_indexes) + 1))
    if page_indexes != expected_indexes:
        issues.append(
            _qa_issue(
                run_id=run_id,
                code="page_order_mismatch",
                severity="blocker",
                page_id=None,
                box_id=None,
                stage="page_manifest",
                message=f"Page indexes are not contiguous from 1: {page_indexes}",
                artifact_ref=artifact_ref,
                recommended_action="Review page manifest ordering before export.",
            )
        )
    rendered_indexes = sorted(
        {
            int(page.get("page_index") or 0)
            for page in (rendering_decisions.get("rendered_pages") or [])
            if isinstance(page, dict)
        }
    )
    if rendered_indexes and rendered_indexes != sorted(page_indexes):
        issues.append(
            _qa_issue(
                run_id=run_id,
                code="rendered_page_order_mismatch",
                severity="blocker",
                page_id=None,
                box_id=None,
                stage="rendering",
                message=f"Rendered page indexes do not match manifest: {rendered_indexes} vs {sorted(page_indexes)}",
                artifact_ref="rendering/typeset_decisions.json",
                recommended_action="Rerun rendering after page order is corrected.",
            )
        )
    return issues


def _check_box_linkage(
    *,
    run_id: str,
    box_rows: list[dict[str, Any]],
    ocr_results: dict[str, Any],
    translation_results: dict[str, Any],
    rendering_decisions: dict[str, Any],
    ocr_warning_threshold: float,
    ocr_blocker_threshold: float,
    min_readable_font_size: int,
    page_metadata: dict[str, dict[str, Any]],
    preserved_box_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ocr_by_box = {str(record.get("box_id")): record for record in ocr_results.get("results") or [] if record.get("box_id")}
    translation_by_box = {
        str(record.get("box_id")): record
        for record in translation_results.get("results") or []
        if record.get("box_id")
    }
    decisions_by_box = {
        str(record.get("box_id")): record
        for record in rendering_decisions.get("decisions") or []
        if record.get("box_id")
    }
    preserved_box_ids = preserved_box_ids or set()
    for row in box_rows:
        box_id = str(row.get("stable_key") or "")
        page_id = str(row.get("page_id") or "")
        if not box_id:
            continue
        ocr = ocr_by_box.get(box_id)
        if ocr is None:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="missing_ocr",
                    severity="blocker",
                    page_id=page_id,
                    box_id=box_id,
                    stage="ocr",
                    message="Stable box has no OCR result.",
                    artifact_ref="ocr/ocr_results.json",
                    recommended_action="Run OCR or mark the box not translatable before export.",
                )
            )
            continue
        confidence = ocr.get("confidence")
        severity = _issue_severity_from_score(
            float(confidence) if isinstance(confidence, (int, float)) else None,
            warning_threshold=ocr_warning_threshold,
            blocker_threshold=ocr_blocker_threshold,
        )
        if severity is not None:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="low_ocr_confidence",
                    severity=severity,
                    page_id=page_id,
                    box_id=box_id,
                    stage="ocr",
                    message=f"OCR confidence is {confidence}.",
                    artifact_ref=str(ocr.get("raw_output_artifact") or "ocr/ocr_results.json"),
                    recommended_action="Review or correct OCR text.",
                )
            )
        translation = translation_by_box.get(box_id)
        if translation is None or not str(translation.get("translated_text") or "").strip():
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="missing_translation",
                    severity="blocker",
                    page_id=page_id,
                    box_id=box_id,
                    stage="translation",
                    message="Stable box has OCR but no translated text.",
                    artifact_ref="translation/translation_results.json",
                    recommended_action="Run translation or import a reviewed translation correction.",
                )
            )
        elif str(ocr.get("text") or "").strip() and str(ocr.get("text") or "").strip() in str(
            translation.get("translated_text") or ""
        ):
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="raw_text_residue_heuristic",
                    severity="warning",
                    page_id=page_id,
                    box_id=box_id,
                    stage="translation",
                    message="Best-effort heuristic found OCR/source text inside translated text.",
                    artifact_ref="translation/translation_results.json",
                    recommended_action="Review the translated text for untranslated residue.",
                    blocks_export=False,
                )
            )
        decision = decisions_by_box.get(box_id)
        if translation is not None and decision is None and box_id not in preserved_box_ids:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="missing_rendered_text",
                    severity="blocker",
                    page_id=page_id,
                    box_id=box_id,
                    stage="rendering",
                    message="Translated box has no typeset rendering decision.",
                    artifact_ref="rendering/typeset_decisions.json",
                    recommended_action="Rerun rendering for this box.",
                )
            )
            continue
        if decision is None:
            continue
        font_size = decision.get("font_size")
        if isinstance(font_size, int | float) and font_size < min_readable_font_size:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="unreadable_small_text",
                    severity="warning",
                    page_id=page_id,
                    box_id=box_id,
                    stage="rendering",
                    message=f"Rendered font size {font_size} is below minimum readable size {min_readable_font_size}.",
                    artifact_ref=str(decision.get("output_artifact") or "rendering/typeset_decisions.json"),
                    recommended_action="Increase box size, shorten translation, or adjust fit settings.",
                    blocks_export=False,
                )
            )
        bbox = _validate_bbox(decision.get("bbox"), box_label=f"QA rendering decision {box_id}")
        page = page_metadata.get(page_id) or {}
        page_width = float(page.get("width") or 0)
        page_height = float(page.get("height") or 0)
        if page_width and page_height and (bbox[0] + bbox[2] > page_width or bbox[1] + bbox[3] > page_height):
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="rendered_text_outside_page",
                    severity="blocker",
                    page_id=page_id,
                    box_id=box_id,
                    stage="rendering",
                    message="Rendered box extends outside the page bounds.",
                    artifact_ref=str(decision.get("output_artifact") or "rendering/typeset_decisions.json"),
                    recommended_action="Correct the box coordinates and rerun rendering.",
                )
            )
        line_count = int(decision.get("line_count") or 0)
        line_height_px = int(decision.get("line_height_px") or 0)
        if line_count > 0 and line_height_px > 0 and line_count * line_height_px > bbox[3]:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="rendered_text_outside_box",
                    severity="blocker",
                    page_id=page_id,
                    box_id=box_id,
                    stage="rendering",
                    message="Rendered text line height exceeds the target box height.",
                    artifact_ref=str(decision.get("output_artifact") or "rendering/typeset_decisions.json"),
                    recommended_action="Resolve rendering overflow or adjust fit settings.",
                )
            )
    return issues


def _check_phase9m1_quality_artifacts(
    *,
    run_id: str,
    cleaning_payload: dict[str, Any],
    style_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if int(cleaning_payload.get("destructive_cleaning_blocker_count") or 0):
        issues.append(
            _qa_issue(
                run_id=run_id,
                code="destructive_cleaning_detected",
                severity="blocker",
                page_id=None,
                box_id=None,
                stage="cleaning",
                message="Cleaning quality audit found destructive image changes.",
                artifact_ref="cleaning/quality/destructive_cleaning_blockers.json",
                recommended_action=(
                    "Residual source text remains after cleaning. Escalate: "
                    "widen_mask_retry, then manual_mask, then "
                    "neural_inpaint_or_manual. Preserve regions that cannot be "
                    "cleaned non-destructively."
                ),
            )
        )
    for job in cleaning_payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        page_id = str(job.get("page_id") or "") or None
        page_index = job.get("page_index")
        mask_ratio = float(job.get("mask_area_ratio") or 0.0)
        if mask_ratio > MANGA_PAGE_MASK_AREA_RATIO_LIMIT:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="destructive_cleaning_detected",
                    severity="blocker",
                    page_id=page_id,
                    box_id=None,
                    stage="cleaning",
                    message=(
                        f"Cleaning mask covers {mask_ratio:.1%} of page {page_index}, "
                        "above the conservative production limit."
                    ),
                    artifact_ref="cleaning/quality/mask_quality_report.json",
                    recommended_action="Reduce the mask to text glyphs or preserve the region.",
                )
            )
        for decision in job.get("region_decisions") or []:
            if not isinstance(decision, dict):
                continue
            box_id = str(decision.get("box_id") or "") or None
            region_type = str(decision.get("quality_region_type") or "")
            policy = str(decision.get("cleaning_policy") or "")
            metrics = decision.get("mask_metrics") or {}
            if policy == "glyph_inpaint" and float(
                metrics.get("glyph_area_ratio") or 0.0
            ) > MANGA_BOX_GLYPH_AREA_RATIO_LIMIT:
                issues.append(
                    _qa_issue(
                        run_id=run_id,
                        code="mask_outside_allowed_region",
                        severity="blocker",
                        page_id=page_id,
                        box_id=box_id,
                        stage="cleaning",
                        message="Glyph mask is too large relative to its detected region.",
                        artifact_ref="cleaning/quality/mask_quality_report.json",
                        recommended_action="Review the mask or preserve the source artwork.",
                    )
                )
            if region_type in {"title_art", "sfx"} and policy != "preserve":
                issues.append(
                    _qa_issue(
                        run_id=run_id,
                        code=(
                            "title_art_destroyed"
                            if region_type == "title_art"
                            else "sfx_destroyed_without_policy"
                        ),
                        severity="blocker",
                        page_id=page_id,
                        box_id=box_id,
                        stage="cleaning",
                        message=f"{region_type} was selected for destructive cleaning.",
                        artifact_ref="cleaning/cleaning_jobs.json",
                        recommended_action="Preserve the source or add an explicit reviewed redraw decision.",
                    )
                )
    if style_audit and style_audit.get("enforced", True):
        for style_issue in style_audit.get("issues") or []:
            if not isinstance(style_issue, dict):
                continue
            kind = str(style_issue.get("kind") or "")
            if kind == "xung_ho_consistency_failed":
                code = "xung_ho_consistency_failed"
            elif kind == "too_long_for_bubble":
                code = "bubble_fit_quality_failed"
            elif kind == "stiff_or_overformal_phrase":
                code = "dialogue_style_quality_failed"
            else:
                code = "dialogue_style_quality_failed"
            severity = str(style_issue.get("severity") or "warning")
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code=code,
                    severity=severity,
                    page_id=str(style_issue.get("page_id") or "") or None,
                    box_id=str(style_issue.get("box_id") or "") or None,
                    stage="translation",
                    message=f"Manga dialogue style audit reported: {kind}.",
                    artifact_ref="translation/dialogue_style_audit.json",
                    recommended_action="Review wording, xung ho, and bubble length with page context.",
                    blocks_export=severity == "blocker",
                )
            )
    return issues


def _check_detection_confidence(
    *,
    run_id: str,
    detection_payload: dict[str, Any] | None,
    warning_threshold: float,
    blocker_threshold: float,
) -> list[dict[str, Any]]:
    if detection_payload is None:
        return []
    issues: list[dict[str, Any]] = []
    for region in detection_payload.get("regions") or []:
        if not isinstance(region, dict):
            continue
        confidence = region.get("confidence")
        severity = _issue_severity_from_score(
            float(confidence) if isinstance(confidence, (int, float)) else None,
            warning_threshold=warning_threshold,
            blocker_threshold=blocker_threshold,
        )
        if severity is None:
            continue
        issues.append(
            _qa_issue(
                run_id=run_id,
                code="low_detection_confidence",
                severity=severity,
                page_id=str(region.get("page_id") or "") or None,
                box_id=str(region.get("box_id") or "") or None,
                stage="detection",
                message=f"Detection confidence is {confidence}.",
                artifact_ref="detection/regions.json",
                recommended_action="Review detected region geometry before export.",
            )
        )
    return issues


def _check_rendering_overflow(
    *,
    run_id: str,
    overflow_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for blocker in overflow_payload.get("blockers") or []:
        if not isinstance(blocker, dict):
            continue
        issues.append(
            _qa_issue(
                run_id=run_id,
                code="rendering_overflow",
                severity="blocker",
                page_id=str(blocker.get("page_id") or "") or None,
                box_id=str(blocker.get("box_id") or "") or None,
                stage="rendering",
                message=f"Rendering overflow: {blocker.get('overflow_reason') or 'unknown'}.",
                artifact_ref="rendering/overflow_report.json",
                recommended_action="Adjust translation or fit settings, then rerun rendering.",
            )
        )
    return issues


def _build_page_review_package(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    page_manifest: dict[str, Any],
    rendering_decisions: dict[str, Any],
    issues: list[dict[str, Any]],
    preview_max_dimension: int,
) -> tuple[dict[str, Any], str, str]:
    qa_dir = _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    human_review_dir = _human_review_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    previews_dir = human_review_dir / "previews"
    review_notes_path = human_review_dir / "review_notes.jsonl"
    if not review_notes_path.exists():
        review_notes_path.write_text("", encoding="utf-8")
    issues_by_page: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        if issue.get("page_id"):
            issues_by_page.setdefault(str(issue["page_id"]), []).append(issue)
    rendered_by_page = {
        str(page.get("page_id")): page
        for page in rendering_decisions.get("rendered_pages") or []
        if isinstance(page, dict) and page.get("page_id")
    }
    pages: list[dict[str, Any]] = []
    for page in page_manifest.get("pages") or []:
        if not isinstance(page, dict) or not page.get("page_id"):
            continue
        page_id = str(page["page_id"])
        rendered = rendered_by_page.get(page_id)
        preview_artifact = None
        rendered_artifact = str(rendered.get("output_artifact")) if rendered else None
        if rendered_artifact:
            preview_path = previews_dir / f"page_{int(page.get('page_index') or 0):04d}_preview.png"
            preview_artifact = _make_bounded_preview(
                workspace,
                source_relpath=rendered_artifact,
                preview_path=preview_path,
                max_dimension=preview_max_dimension,
            )
        page_issues = issues_by_page.get(page_id, [])
        pages.append(
            {
                "page_id": page_id,
                "page_index": page.get("page_index"),
                "source_artifact": page.get("artifact_relpath"),
                "rendered_artifact": rendered_artifact,
                "preview_artifact": preview_artifact,
                "issue_count": len(page_issues),
                "blocker_count": len([issue for issue in page_issues if issue["effective_blocks_export"]]),
                "warning_count": len([issue for issue in page_issues if issue["severity"] == "warning"]),
                "issue_ids": [issue["issue_id"] for issue in page_issues],
            }
        )
    page_review = {
        "schema_version": MANGA_VISUAL_QA_SCHEMA_VERSION,
        "project_slug": project_slug,
        "run_id": run_id,
        "page_count": len(pages),
        "pages": pages,
        "review_notes_path": _relative_artifact(workspace, review_notes_path),
    }
    page_review_path = qa_dir / "page_review_index.json"
    page_review_rel = _write_json_artifact(workspace, page_review_path, page_review)
    review_index_path = human_review_dir / "review_index.md"
    lines = [
        "# Manga Human Review Index",
        "",
        f"- Project: `{project_slug}`",
        f"- Run ID: `{run_id}`",
        f"- Page count: `{len(pages)}`",
        f"- Review notes: `{_relative_artifact(workspace, review_notes_path)}`",
        "",
        "## Pages",
    ]
    for page in pages:
        lines.extend(
            [
                "",
                f"### Page {page['page_index']}",
                f"- Page ID: `{page['page_id']}`",
                f"- Preview: `{page.get('preview_artifact')}`",
                f"- Rendered: `{page.get('rendered_artifact')}`",
                f"- Blockers: `{page['blocker_count']}`",
                f"- Warnings: `{page['warning_count']}`",
            ]
        )
    review_index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return page_review, page_review_rel, _relative_artifact(workspace, review_index_path)


def run_manga_visual_qa(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    ocr_warning_threshold: float = 0.8,
    ocr_blocker_threshold: float = 0.5,
    detection_warning_threshold: float = 0.8,
    detection_blocker_threshold: float = 0.5,
    min_readable_font_size: int = 8,
    preview_max_dimension: int = 360,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    page_manifest = _load_page_manifest(workspace, project_slug=project_slug, run_id=run_id)
    ocr_results = _load_ocr_results_artifact(workspace, project_slug=project_slug, run_id=run_id)
    translation_results = _load_translation_results_artifact(workspace, project_slug=project_slug, run_id=run_id)
    cleaning_payload = _load_cleaning_jobs_artifact(
        workspace, project_slug=project_slug, run_id=run_id
    )
    rendering_decisions, overflow_payload = _load_rendering_artifacts(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
    )
    detection_payload = _load_detection_artifact_if_available(workspace, project_slug=project_slug, run_id=run_id)
    reading_order_payload = _load_reading_order_artifact_if_available(workspace, project_slug=project_slug, run_id=run_id)
    style_audit_path = (
        _translation_dir_for_run(
            workspace, project_slug=project_slug, run_id=run_id
        )
        / "dialogue_style_audit.json"
    )
    style_audit = (
        json.loads(style_audit_path.read_text(encoding="utf-8"))
        if style_audit_path.exists()
        else None
    )
    preserved_box_ids = {
        str(box_id)
        for job in cleaning_payload.get("jobs") or []
        for box_id in job.get("preserved_box_ids") or []
    }
    qa_dir = _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    report_json_path = qa_dir / "visual_qa_report.json"
    report_md_path = qa_dir / "visual_qa_report.md"
    blockers_path = qa_dir / "blockers.json"
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.visual_qa",
            status="running",
            stage="run_visual_qa",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "ocr_warning_threshold": ocr_warning_threshold,
                "ocr_blocker_threshold": ocr_blocker_threshold,
                "detection_warning_threshold": detection_warning_threshold,
                "detection_blocker_threshold": detection_blocker_threshold,
                "min_readable_font_size": min_readable_font_size,
                "cloud_used": False,
            },
            result_data={},
        )
        ocr_review_states = _ocr_review_states(conn, project_id=project["id"])
        box_rows = [
            row
            for row in _current_boxes_for_project(conn, project_id=project["id"])
            if row.get("stable_key")
            and ocr_review_states.get(str(row["stable_key"]), {}).get("review_state") != "not_translatable"
        ]
        page_metadata = _page_metadata_by_id(page_manifest)
        issues: list[dict[str, Any]] = []
        issues.extend(
            _check_page_order(
                run_id=run_id,
                page_manifest=page_manifest,
                rendering_decisions=rendering_decisions,
                artifact_ref="page_manifest.json",
            )
        )
        if reading_order_payload is None:
            issues.append(
                _qa_issue(
                    run_id=run_id,
                    code="missing_reading_order",
                    severity="blocker",
                    page_id=None,
                    box_id=None,
                    stage="reading_order",
                    message="Reading-order artifact is missing.",
                    artifact_ref="reading_order/reading_order.json",
                    recommended_action="Generate reading order before export readiness validation.",
                )
            )
        issues.extend(
            _check_box_linkage(
                run_id=run_id,
                box_rows=box_rows,
                ocr_results=ocr_results,
                translation_results=translation_results,
                rendering_decisions=rendering_decisions,
                ocr_warning_threshold=ocr_warning_threshold,
                ocr_blocker_threshold=ocr_blocker_threshold,
                min_readable_font_size=min_readable_font_size,
                page_metadata=page_metadata,
                preserved_box_ids=preserved_box_ids,
            )
        )
        issues.extend(
            _check_detection_confidence(
                run_id=run_id,
                detection_payload=detection_payload,
                warning_threshold=detection_warning_threshold,
                blocker_threshold=detection_blocker_threshold,
            )
        )
        issues.extend(_check_rendering_overflow(run_id=run_id, overflow_payload=overflow_payload))
        issues.extend(
            _check_phase9m1_quality_artifacts(
                run_id=run_id,
                cleaning_payload=cleaning_payload,
                style_audit=style_audit,
            )
        )
        statuses = _issue_statuses_by_id(conn, project_id=project["id"], run_id=run_id)
        issues = _apply_issue_statuses(issues, statuses)
        blockers = [issue for issue in issues if issue["effective_blocks_export"]]
        warnings = [issue for issue in issues if issue["severity"] == "warning"]
        export_ready = not blockers
        page_review, page_review_rel, human_review_rel = _build_page_review_package(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            page_manifest=page_manifest,
            rendering_decisions=rendering_decisions,
            issues=issues,
            preview_max_dimension=preview_max_dimension,
        )
        blockers_payload = {
            "schema_version": MANGA_VISUAL_QA_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "export_ready": export_ready,
            "blocker_count": len(blockers),
            "blockers": blockers,
        }
        blockers_rel = _write_json_artifact(workspace, blockers_path, blockers_payload)
        report = {
            "schema_version": MANGA_VISUAL_QA_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "cloud_used": False,
            "export_ready": export_ready,
            "validation_status": "pass" if export_ready else "blocked",
            "issue_count": len(issues),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "thresholds": {
                "ocr_warning_threshold": float(ocr_warning_threshold),
                "ocr_blocker_threshold": float(ocr_blocker_threshold),
                "detection_warning_threshold": float(detection_warning_threshold),
                "detection_blocker_threshold": float(detection_blocker_threshold),
                "min_readable_font_size": int(min_readable_font_size),
            },
            "phase9m1_quality": {
                "cleaning_mode": cleaning_payload.get("mode"),
                "preserved_box_count": len(preserved_box_ids),
                "dialogue_style_audit_present": style_audit is not None,
            },
            "issues": issues,
            "artifacts": {
                "blockers": blockers_rel,
                "page_review_index": page_review_rel,
                "human_review_index": human_review_rel,
                "review_notes": page_review["review_notes_path"],
            },
        }
        report_rel = _write_json_artifact(workspace, report_json_path, report)
        report_md_path.write_text(
            "\n".join(
                [
                    "# Manga Visual QA Report",
                    "",
                    f"- Schema version: `{MANGA_VISUAL_QA_SCHEMA_VERSION}`",
                    f"- Project: `{project_slug}`",
                    f"- Run ID: `{run_id}`",
                    f"- Export ready: `{str(export_ready).lower()}`",
                    f"- Blockers: `{len(blockers)}`",
                    f"- Warnings: `{len(warnings)}`",
                    f"- Human review index: `{human_review_rel}`",
                    "",
                    "## Issues",
                    *(
                        f"- `{issue['severity']}` `{issue['code']}` page=`{issue.get('page_id')}` "
                        f"box=`{issue.get('box_id')}` blocks_export=`{issue['effective_blocks_export']}`"
                        for issue in issues
                    ),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        report_md_rel = _relative_artifact(workspace, report_md_path)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "export_ready": export_ready,
            "validation_status": report["validation_status"],
            "issue_count": len(issues),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "cloud_used": False,
            "visual_qa_report_path": report_rel,
            "visual_qa_report_md_path": report_md_rel,
            "blockers_path": blockers_rel,
            "page_review_index_path": page_review_rel,
            "human_review_index_path": human_review_rel,
            "review_notes_path": page_review["review_notes_path"],
        }
        conn.execute(
            """
            INSERT INTO manga_visual_qa_runs (
                id, run_id, project_id, project_slug, report_path, report_md_path,
                blockers_path, page_review_index_path, human_review_index_path,
                validation_status, blocker_count, warning_count, export_ready,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangaqarun"),
                run_id,
                project["id"],
                project_slug,
                report_rel,
                report_md_rel,
                blockers_rel,
                page_review_rel,
                human_review_rel,
                report["validation_status"],
                len(blockers),
                len(warnings),
                1 if export_ready else 0,
                now,
                now,
            ),
        )
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def export_manga_visual_qa(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    qa_dir = _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    report_path = qa_dir / "visual_qa_report.json"
    blockers_path = qa_dir / "blockers.json"
    page_review_path = qa_dir / "page_review_index.json"
    human_review_path = _human_review_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "review_index.md"
    for path in [report_path, blockers_path, page_review_path, human_review_path]:
        if not path.exists():
            raise ValueError(f"Visual QA artifact missing: {_relative_artifact(workspace, path)}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "export_ready": bool(report.get("export_ready")),
        "validation_status": report.get("validation_status"),
        "issue_count": report.get("issue_count", 0),
        "blocker_count": report.get("blocker_count", 0),
        "warning_count": report.get("warning_count", 0),
        "visual_qa_report_path": _relative_artifact(workspace, report_path),
        "visual_qa_report_md_path": _relative_artifact(workspace, qa_dir / "visual_qa_report.md"),
        "blockers_path": _relative_artifact(workspace, blockers_path),
        "page_review_index_path": _relative_artifact(workspace, page_review_path),
        "human_review_index_path": _relative_artifact(workspace, human_review_path),
    }


def get_manga_visual_qa_status(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT id, report_path, report_md_path, blockers_path, page_review_index_path,
                   human_review_index_path, validation_status, blocker_count,
                   warning_count, export_ready, created_at, updated_at
            FROM manga_visual_qa_runs
            WHERE project_id = ? AND run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project["id"], run_id),
        ).fetchone()
    if row is None:
        return {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "status": "not_started",
            "export_ready": False,
            "blocker_count": None,
            "warning_count": None,
        }
    data = row_to_dict(row)
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "status": "completed",
        "qa_run_id": data["id"],
        "validation_status": data["validation_status"],
        "export_ready": bool(data["export_ready"]),
        "blocker_count": data["blocker_count"],
        "warning_count": data["warning_count"],
        "visual_qa_report_path": data["report_path"],
        "visual_qa_report_md_path": data["report_md_path"],
        "blockers_path": data["blockers_path"],
        "page_review_index_path": data["page_review_index_path"],
        "human_review_index_path": data["human_review_index_path"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def validate_manga_export_readiness(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    blockers_path = _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "blockers.json"
    if not blockers_path.exists():
        raise ValueError(f"Visual QA blockers artifact not found for run {run_id}.")
    payload = json.loads(blockers_path.read_text(encoding="utf-8"))
    blockers = payload.get("blockers") or []
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "export_ready": bool(payload.get("export_ready")),
        "validation_status": "ready" if not blockers else "blocked",
        "blocker_count": len(blockers),
        "blockers_path": _relative_artifact(workspace, blockers_path),
        "blockers": blockers,
    }


def get_manga_human_review_package(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    human_review_dir = _human_review_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    review_index_path = human_review_dir / "review_index.md"
    notes_path = human_review_dir / "review_notes.jsonl"
    if not review_index_path.exists():
        raise ValueError(f"Human review package not found for run {run_id}.")
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "human_review_dir": _relative_artifact(workspace, human_review_dir),
        "human_review_index_path": _relative_artifact(workspace, review_index_path),
        "review_notes_path": _relative_artifact(workspace, notes_path),
        "previews_dir": _relative_artifact(workspace, human_review_dir / "previews"),
    }


def save_manga_human_review_note(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    issue_id: str,
    note: str,
    reviewer: str = "cli",
    page_id: str | None = None,
    box_id: str | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    now = utc_now()
    human_review_dir = _human_review_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    notes_path = human_review_dir / "review_notes.jsonl"
    record = {
        "schema_version": MANGA_VISUAL_QA_SCHEMA_VERSION,
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "issue_id": issue_id,
        "page_id": page_id,
        "box_id": box_id,
        "reviewer": reviewer,
        "note": _truncate_text(note, 1000),
        "created_at": now,
    }
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.visual_qa.note",
            status="running",
            stage="save_review_note",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "issue_id": issue_id},
            result_data={},
        )
        note_id = new_id("mangaqanote")
        conn.execute(
            """
            INSERT INTO manga_visual_qa_review_notes (
                id, project_id, project_slug, run_id, issue_id, page_id, box_id,
                reviewer, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                project["id"],
                project_slug,
                run_id,
                issue_id,
                page_id,
                box_id,
                reviewer,
                record["note"],
                now,
            ),
        )
        _append_jsonl(notes_path, record)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "note_id": note_id,
            "issue_id": issue_id,
            "review_notes_path": _relative_artifact(workspace, notes_path),
        }
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def save_manga_visual_qa_issue_status(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    issue_id: str,
    status: str,
    reviewer: str = "cli",
    note: str | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    if status not in MANGA_VISUAL_QA_REVIEW_STATUSES:
        raise ValueError(f"Invalid QA issue status: {status}. Expected one of {sorted(MANGA_VISUAL_QA_REVIEW_STATUSES)}.")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.visual_qa.issue_status",
            status="running",
            stage="save_issue_status",
            project_id=project["id"],
            input_data={"project": project_slug, "run_id": run_id, "issue_id": issue_id, "status": status},
            result_data={},
        )
        existing = conn.execute(
            """
            SELECT id FROM manga_visual_qa_issue_statuses
            WHERE project_id = ? AND run_id = ? AND issue_id = ?
            LIMIT 1
            """,
            (project["id"], run_id, issue_id),
        ).fetchone()
        if existing is None:
            status_id = new_id("mangaqastatus")
            conn.execute(
                """
                INSERT INTO manga_visual_qa_issue_statuses (
                    id, project_id, project_slug, run_id, issue_id, status,
                    reviewer, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (status_id, project["id"], project_slug, run_id, issue_id, status, reviewer, note, now, now),
            )
        else:
            status_id = str(existing["id"])
            conn.execute(
                """
                UPDATE manga_visual_qa_issue_statuses
                SET status = ?, reviewer = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, reviewer, note, now, status_id),
            )
        audit_path = _human_review_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "review_notes.jsonl"
        _append_jsonl(
            audit_path,
            {
                "schema_version": MANGA_VISUAL_QA_SCHEMA_VERSION,
                "project_id": project["id"],
                "project_slug": project_slug,
                "run_id": run_id,
                "issue_id": issue_id,
                "status": status,
                "reviewer": reviewer,
                "note": note,
                "created_at": now,
            },
        )
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "status_id": status_id,
            "issue_id": issue_id,
            "status": status,
            "review_notes_path": _relative_artifact(workspace, audit_path),
        }
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def _export_dir_for_run(workspace: Workspace, *, project_slug: str, run_id: str) -> Path:
    export_dir = _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id) / "export"
    for child in [export_dir, export_dir / "images", export_dir / "cbz", export_dir / "pdf"]:
        child.mkdir(parents=True, exist_ok=True)
    return export_dir


def _load_rendered_pages_for_export(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> list[dict[str, Any]]:
    try:
        rendering_decisions, _overflow = _load_rendering_artifacts(workspace, project_slug=project_slug, run_id=run_id)
    except ValueError as exc:
        if "BLOCKED_RENDERED_ARTIFACTS" in str(exc):
            raise ValueError(f"BLOCKED_RENDERED_PAGES: rendering artifacts not found for run {run_id}.") from exc
        raise
    rendered_pages = [
        page
        for page in rendering_decisions.get("rendered_pages") or []
        if isinstance(page, dict) and page.get("output_artifact")
    ]
    if not rendered_pages:
        by_page: dict[str, dict[str, Any]] = {}
        for decision in rendering_decisions.get("decisions") or []:
            if not isinstance(decision, dict) or not decision.get("output_artifact"):
                continue
            page_id = str(decision.get("page_id") or "")
            by_page.setdefault(
                page_id,
                {
                    "page_id": page_id,
                    "page_index": decision.get("page_index"),
                    "output_artifact": decision.get("output_artifact"),
                },
            )
        rendered_pages = list(by_page.values())
    if not rendered_pages:
        raise ValueError(f"BLOCKED_RENDERED_PAGES: no rendered pages found for run {run_id}.")
    normalized: list[dict[str, Any]] = []
    for page in sorted(rendered_pages, key=lambda item: (int(item.get("page_index") or 0), str(item.get("page_id") or ""))):
        relpath = str(page["output_artifact"])
        path = workspace.path / relpath
        if not path.exists():
            raise ValueError(f"BLOCKED_RENDERED_PAGES: rendered page missing: {relpath}")
        normalized.append(
            {
                "page_id": str(page.get("page_id") or ""),
                "page_index": int(page.get("page_index") or len(normalized) + 1),
                "output_artifact": relpath,
                "checksum_sha256": sha256_file(path),
            }
        )
    return normalized


def _qa_export_gate(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    allow_qa_blockers: bool,
) -> tuple[dict[str, Any], list[str]]:
    blockers_path = _qa_dir_for_run(workspace, project_slug=project_slug, run_id=run_id) / "blockers.json"
    if not blockers_path.exists():
        raise ValueError(f"BLOCKED_QA_BLOCKERS: visual QA blockers artifact not found for run {run_id}.")
    payload = json.loads(blockers_path.read_text(encoding="utf-8"))
    blockers = payload.get("blockers") or []
    warnings: list[str] = []
    if blockers and not allow_qa_blockers:
        raise ValueError(f"BLOCKED_QA_BLOCKERS: {len(blockers)} QA blocker(s) remain for run {run_id}.")
    if blockers:
        warnings.append(f"unsafe_export_with_qa_blockers:{len(blockers)}")
    return payload, warnings


def _deterministic_export_filename(page: dict[str, Any]) -> str:
    return f"{int(page['page_index']):04d}.png"


def _copy_rendered_export_images(
    workspace: Workspace,
    *,
    rendered_pages: list[dict[str, Any]],
    images_dir: Path,
) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for page in rendered_pages:
        filename = _deterministic_export_filename(page)
        source = workspace.path / str(page["output_artifact"])
        target = images_dir / filename
        with open(_windows_long_path(source), "rb") as source_handle, open(
            _windows_long_path(target), "wb"
        ) as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
        exported.append(
            {
                "page_id": page["page_id"],
                "page_index": page["page_index"],
                "source_rendered_page": page["output_artifact"],
                "export_path": _relative_artifact(workspace, target),
                "filename": filename,
                "checksum_sha256": sha256_file(target),
            }
        )
    return exported


def _write_deterministic_cbz(
    workspace: Workspace,
    *,
    image_exports: list[dict[str, Any]],
    cbz_path: Path,
) -> str:
    cbz_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(_windows_long_path(cbz_path), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for record in image_exports:
            source = workspace.path / str(record["export_path"])
            info = zipfile.ZipInfo(str(record["filename"]))
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())
    return _relative_artifact(workspace, cbz_path)


def _pdf_adapter_status(pdf_adapter: str) -> dict[str, Any]:
    if pdf_adapter == "pillow":
        return {
            "adapter_id": "pillow",
            "available": True,
            "approved": True,
            "reason": "Pillow is already an approved local image dependency for Phase 9 rendering.",
        }
    return {
        "adapter_id": pdf_adapter,
        "available": False,
        "approved": False,
        "reason": f"PDF adapter is not approved or available: {pdf_adapter}",
    }


def _write_pillow_pdf(
    workspace: Workspace,
    *,
    image_exports: list[dict[str, Any]],
    pdf_path: Path,
) -> str:
    Image, _ImageOps = _load_pillow()
    images: list[Any] = []
    for record in image_exports:
        path = workspace.path / str(record["export_path"])
        with Image.open(_windows_long_path(path)) as image:
            images.append(image.convert("RGB").copy())
    if not images:
        raise ValueError("BLOCKED_RENDERED_PAGES: no images available for PDF export.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    first, rest = images[0], images[1:]
    first.save(_windows_long_path(pdf_path), "PDF", save_all=True, append_images=rest, resolution=100.0)
    return _relative_artifact(workspace, pdf_path)


def run_manga_export(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    include_images: bool = True,
    include_cbz: bool = True,
    include_pdf: bool = False,
    pdf_adapter: str = "pillow",
    allow_qa_blockers: bool = False,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    rendered_pages = _load_rendered_pages_for_export(workspace, project_slug=project_slug, run_id=run_id)
    qa_payload, warnings = _qa_export_gate(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        allow_qa_blockers=allow_qa_blockers,
    )
    export_dir = _export_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    images_dir = export_dir / "images"
    cbz_dir = export_dir / "cbz"
    pdf_dir = export_dir / "pdf"
    manifest_path = export_dir / "export_manifest.json"
    summary_path = export_dir / "export_summary.md"
    cbz_path = cbz_dir / f"{_safe_name(project_slug)}.cbz"
    pdf_path = pdf_dir / f"{_safe_name(project_slug)}.pdf"
    now = utc_now()
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="manga.export",
            status="running",
            stage="export_rendered_pages",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "run_id": run_id,
                "include_images": include_images,
                "include_cbz": include_cbz,
                "include_pdf": include_pdf,
                "pdf_adapter": pdf_adapter,
                "allow_qa_blockers": allow_qa_blockers,
            },
            result_data={},
        )
        image_exports: list[dict[str, Any]] = []
        if include_images or include_cbz or include_pdf:
            image_exports = _copy_rendered_export_images(workspace, rendered_pages=rendered_pages, images_dir=images_dir)
        cbz_rel: str | None = None
        if include_cbz:
            cbz_rel = _write_deterministic_cbz(workspace, image_exports=image_exports, cbz_path=cbz_path)
        pdf_rel: str | None = None
        pdf_status = "not_requested"
        pdf_warning: str | None = None
        if include_pdf:
            adapter_status = _pdf_adapter_status(pdf_adapter)
            if adapter_status["available"] and adapter_status["approved"]:
                pdf_rel = _write_pillow_pdf(workspace, image_exports=image_exports, pdf_path=pdf_path)
                pdf_status = "created"
            else:
                pdf_status = "unavailable"
                pdf_warning = str(adapter_status["reason"])
                warnings.append(pdf_warning)
        source_rendered_pages = [
            {
                "page_id": page["page_id"],
                "page_index": page["page_index"],
                "path": page["output_artifact"],
                "checksum_sha256": page["checksum_sha256"],
            }
            for page in rendered_pages
        ]
        manifest = {
            "schema_version": MANGA_EXPORT_SCHEMA_VERSION,
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "page_count": len(rendered_pages),
            "source_rendered_pages": source_rendered_pages,
            "image_export_paths": [record["export_path"] for record in image_exports] if include_images else [],
            "image_exports": image_exports,
            "cbz_path": cbz_rel,
            "pdf_path": pdf_rel,
            "pdf_status": pdf_status,
            "pdf_adapter": pdf_adapter,
            "qa_report_ref": str(qa_payload.get("visual_qa_report_path") or "qa/visual_qa_report.json"),
            "qa_blockers_ref": "qa/blockers.json",
            "qa_export_ready": bool(qa_payload.get("export_ready")),
            "allow_qa_blockers": bool(allow_qa_blockers),
            "warnings": warnings,
        }
        manifest_rel = _write_json_artifact(workspace, manifest_path, manifest)
        summary_path.write_text(
            "\n".join(
                [
                    "# Manga Export Summary",
                    "",
                    f"- Schema version: `{MANGA_EXPORT_SCHEMA_VERSION}`",
                    f"- Project: `{project_slug}`",
                    f"- Run ID: `{run_id}`",
                    f"- Page count: `{len(rendered_pages)}`",
                    f"- Image folder: `{_relative_artifact(workspace, images_dir)}`",
                    f"- CBZ: `{cbz_rel}`",
                    f"- PDF status: `{pdf_status}`",
                    f"- PDF: `{pdf_rel}`",
                    f"- QA export ready: `{bool(qa_payload.get('export_ready'))}`",
                    f"- Unsafe QA override: `{bool(allow_qa_blockers)}`",
                    f"- Warning count: `{len(warnings)}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        summary_rel = _relative_artifact(workspace, summary_path)
        result = {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "page_count": len(rendered_pages),
            "image_export_count": len(image_exports) if include_images else 0,
            "cbz_created": cbz_rel is not None,
            "pdf_status": pdf_status,
            "pdf_path": pdf_rel,
            "qa_export_ready": bool(qa_payload.get("export_ready")),
            "allow_qa_blockers": bool(allow_qa_blockers),
            "export_manifest_path": manifest_rel,
            "export_summary_path": summary_rel,
            "images_dir": _relative_artifact(workspace, images_dir),
            "cbz_path": cbz_rel,
            "warnings": warnings,
        }
        export_records = [
            ("manifest", manifest_rel, sha256_file(manifest_path), {"page_count": len(rendered_pages)}),
            ("summary", summary_rel, sha256_file(summary_path), {"page_count": len(rendered_pages)}),
        ]
        if include_images:
            export_records.extend(
                ("image", record["export_path"], record["checksum_sha256"], {"page_index": record["page_index"]})
                for record in image_exports
            )
        if cbz_rel is not None:
            export_records.append(("cbz", cbz_rel, sha256_file(workspace.path / cbz_rel), {"page_count": len(rendered_pages)}))
        if pdf_rel is not None:
            export_records.append(("pdf", pdf_rel, sha256_file(workspace.path / pdf_rel), {"page_count": len(rendered_pages)}))
        for export_kind, export_path, checksum, metadata in export_records:
            conn.execute(
                """
                INSERT INTO manga_exports (
                    id, project_id, chapter_id, export_kind, export_path, checksum_sha256,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("mangaexport"),
                    project["id"],
                    None,
                    export_kind,
                    export_path,
                    checksum,
                    json_dumps({"run_id": run_id, **metadata}),
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO manga_export_runs (
                id, run_id, project_id, project_slug, manifest_path, summary_path,
                images_dir, cbz_path, pdf_path, pdf_status, page_count,
                qa_report_ref, qa_export_ready, allow_qa_blockers, warnings_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mangaexportrun"),
                run_id,
                project["id"],
                project_slug,
                manifest_rel,
                summary_rel,
                result["images_dir"],
                cbz_rel,
                pdf_rel,
                pdf_status,
                len(rendered_pages),
                manifest["qa_report_ref"],
                1 if qa_payload.get("export_ready") else 0,
                1 if allow_qa_blockers else 0,
                json_dumps(warnings),
                now,
                now,
            ),
        )
        update_task_run(conn, task_id=task_id, status="success", stage="completed", result_data=result)
        conn.commit()
    return {"task_run_id": task_id, **result}


def export_manga_image_folder(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    allow_qa_blockers: bool = False,
) -> dict[str, Any]:
    return run_manga_export(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        include_images=True,
        include_cbz=False,
        include_pdf=False,
        allow_qa_blockers=allow_qa_blockers,
    )


def export_manga_cbz(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    allow_qa_blockers: bool = False,
) -> dict[str, Any]:
    return run_manga_export(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        include_images=True,
        include_cbz=True,
        include_pdf=False,
        allow_qa_blockers=allow_qa_blockers,
    )


def export_manga_pdf(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
    pdf_adapter: str = "pillow",
    allow_qa_blockers: bool = False,
) -> dict[str, Any]:
    return run_manga_export(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        include_images=True,
        include_cbz=False,
        include_pdf=True,
        pdf_adapter=pdf_adapter,
        allow_qa_blockers=allow_qa_blockers,
    )


def export_manga_export_manifest(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    export_dir = _export_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    manifest_path = export_dir / "export_manifest.json"
    summary_path = export_dir / "export_summary.md"
    if not manifest_path.exists():
        raise ValueError(f"Export manifest not found for run {run_id}.")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "page_count": payload.get("page_count", 0),
        "cbz_path": payload.get("cbz_path"),
        "pdf_path": payload.get("pdf_path"),
        "pdf_status": payload.get("pdf_status"),
        "qa_report_ref": payload.get("qa_report_ref"),
        "export_manifest_path": _relative_artifact(workspace, manifest_path),
        "export_summary_path": _relative_artifact(workspace, summary_path),
        "manifest": payload,
    }


def validate_manga_export_manifest(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    export_dir = _export_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    manifest_path = export_dir / "export_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Export manifest not found for run {run_id}.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    source_pages = manifest.get("source_rendered_pages") or []
    image_exports = manifest.get("image_exports") or []
    page_count = int(manifest.get("page_count") or 0)
    if page_count != len(source_pages):
        issues.append(f"source_page_count_mismatch:{page_count}:{len(source_pages)}")
    if manifest.get("image_export_paths") and len(manifest.get("image_export_paths") or []) != page_count:
        issues.append("image_export_count_mismatch")
    source_indexes = [int(page.get("page_index") or 0) for page in source_pages]
    if source_indexes != sorted(source_indexes) or source_indexes != list(range(1, len(source_indexes) + 1)):
        issues.append("source_page_order_invalid")
    image_indexes = [int(record.get("page_index") or 0) for record in image_exports]
    if image_indexes and image_indexes != source_indexes:
        issues.append("image_page_order_mismatch")
    for relpath in manifest.get("image_export_paths") or []:
        if not (workspace.path / str(relpath)).exists():
            issues.append(f"missing_image_export:{relpath}")
    cbz_path = manifest.get("cbz_path")
    if cbz_path:
        cbz_abs = workspace.path / str(cbz_path)
        if not cbz_abs.exists():
            issues.append(f"missing_cbz:{cbz_path}")
        else:
            with zipfile.ZipFile(_windows_long_path(cbz_abs), "r") as archive:
                names = archive.namelist()
            expected = [_deterministic_export_filename(page) for page in source_pages]
            if names != expected:
                issues.append(f"cbz_page_order_mismatch:{names}:{expected}")
    pdf_path = manifest.get("pdf_path")
    if manifest.get("pdf_status") == "created" and (not pdf_path or not (workspace.path / str(pdf_path)).exists()):
        issues.append(f"missing_pdf:{pdf_path}")
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "validation_status": "valid" if not issues else "invalid",
        "page_count": page_count,
        "issues": issues,
        "export_manifest_path": _relative_artifact(workspace, manifest_path),
    }


def _phase9l_report_dir(workspace: Workspace, *, project_slug: str, run_id: str | None) -> Path:
    if run_id:
        return _artifact_root_for_run(workspace, project_slug=project_slug, run_id=run_id)
    path = workspace.path / "artifacts" / "manga" / project_slug / f"phase9l_blocked_{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _phase9l_write_report(workspace: Workspace, report_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    report_json = report_dir / "phase9l_canary_report.json"
    report_md = report_dir / "phase9l_canary_report.md"
    report_json.write_text(json_dumps(report) + "\n", encoding="utf-8")
    export_status = report.get("export") or {}
    qa = report.get("qa") or {}
    provider = report.get("provider_preflight") or {}
    lines = [
        "# Phase 9L Real Manga E2E Canary Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Blocker category: `{report.get('blocker_category')}`",
        f"- Project: `{report.get('project_slug')}`",
        f"- Run ID: `{report.get('run_id')}`",
        f"- Page range: `{report.get('page_range')}`",
        f"- OCR adapter: `{report.get('ocr_adapter')}`",
        f"- Translation provider: `{report.get('translation_provider')}`",
        f"- Provider preflight pass: `{provider.get('pass')}`",
        f"- API calls: `{report.get('api_call_count')}`",
        f"- QA blockers: `{qa.get('blocker_count')}`",
        f"- Export status: `{export_status.get('status')}`",
        f"- CBZ path: `{export_status.get('cbz_path')}`",
        f"- PDF path: `{export_status.get('pdf_path')}`",
        f"- Approved rules used: `{report.get('approved_rules_used')}`",
        f"- Raw NLP cache injected: `{report.get('raw_nlp_cache_injected')}`",
        "",
    ]
    if report.get("error"):
        lines.extend(["## Error", "", f"`{_truncate_text(str(report.get('error')), 500)}`", ""])
    report_md.write_text("\n".join(lines), encoding="utf-8")
    report["phase9l_canary_report_path"] = _relative_artifact(workspace, report_json)
    report["phase9l_canary_report_md_path"] = _relative_artifact(workspace, report_md)
    report_json.write_text(json_dumps(report) + "\n", encoding="utf-8")
    return report


def _phase9l5_write_report(
    workspace: Workspace,
    report_dir: Path,
    report: dict[str, Any],
) -> dict[str, str]:
    quality_path = report_dir / "detection" / "box_quality_report.json"
    quality = (
        json.loads(quality_path.read_text(encoding="utf-8"))
        if quality_path.exists()
        else {}
    )
    qa = report.get("qa") or {}
    if report.get("status") == "PASS":
        status = "PASS"
    elif report.get("blocker_category") in {"BLOCKED_DETECTION", "BLOCKED_QA"}:
        status = "BLOCKED_DETECTOR_LIMITATION"
    else:
        status = "FAIL"
    payload = {
        "schema_version": PHASE9L5_DETECTOR_SCHEMA_VERSION,
        "created_at": report.get("created_at"),
        "updated_at": report.get("updated_at"),
        "status": status,
        "phase9l_status": report.get("status"),
        "phase9l_blocker_category": report.get("blocker_category"),
        "project_slug": report.get("project_slug"),
        "run_id": report.get("run_id"),
        "page_range": report.get("page_range"),
        "detector": report.get("detection_adapter"),
        "ocr_adapter": report.get("ocr_adapter"),
        "translation_provider": report.get("translation_provider"),
        "api_call_count": report.get("api_call_count"),
        "approved_rules_used": report.get("approved_rules_used"),
        "raw_nlp_cache_injected": report.get("raw_nlp_cache_injected"),
        "baseline": {
            "detector": "mock_local_detector",
            "failing_source_pages": [1, 4],
            "ocr_empty_or_zero_confidence_count": 4,
            "qa_blocker_count": 8,
            "evidence": [
                "mangarun_a85ea2a9edb74232b6be9f7a6643e57a",
                "mangarun_10ee1aaa09f243b0aea543e597f3d805",
            ],
        },
        "after": {
            "selected_box_count": quality.get("box_count"),
            "low_confidence_box_count": quality.get("low_confidence_count"),
            "out_of_bounds_box_count": quality.get("out_of_bounds_count"),
            "ocr_empty_count": quality.get("ocr_empty_count"),
            "ocr_zero_confidence_count": quality.get("ocr_zero_confidence_count"),
            "ocr_missing_count": quality.get("ocr_missing_count"),
            "qa_blocker_count": qa.get("blocker_count"),
        },
        "diagnostic_artifacts": {
            "detection_diagnostics": (
                _relative_to_workspace(
                    workspace, report_dir / "detection" / "detection_diagnostics.json"
                )
                if (report_dir / "detection" / "detection_diagnostics.json").exists()
                else None
            ),
            "box_quality_report": (
                _relative_to_workspace(workspace, quality_path)
                if quality_path.exists()
                else None
            ),
            "overlays": (
                _relative_to_workspace(workspace, report_dir / "detection" / "overlays")
                if (report_dir / "detection" / "overlays").exists()
                else None
            ),
        },
        "export": report.get("export"),
        "error": report.get("error"),
    }
    json_path = report_dir / "phase9l5_detector_hardening_report.json"
    md_path = report_dir / "phase9l5_detector_hardening_report.md"
    json_path.write_text(json_dumps(payload) + "\n", encoding="utf-8")
    after = payload["after"]
    md_path.write_text(
        "\n".join(
            [
                "# Phase 9L.5 Detector Hardening Report",
                "",
                f"- Status: `{status}`",
                f"- Run ID: `{report.get('run_id')}`",
                f"- Page range: `{report.get('page_range')}`",
                f"- Detector: `{report.get('detection_adapter')}`",
                f"- OCR adapter: `{report.get('ocr_adapter')}`",
                f"- Real API calls: `{report.get('api_call_count')}`",
                "- Baseline OCR empty/zero-confidence boxes: `4`",
                f"- Current OCR-empty boxes: `{after.get('ocr_empty_count')}`",
                f"- Current OCR-zero-confidence boxes: `{after.get('ocr_zero_confidence_count')}`",
                "- Baseline QA blockers: `8`",
                f"- Current QA blockers: `{after.get('qa_blocker_count')}`",
                f"- Out-of-bounds boxes: `{after.get('out_of_bounds_box_count')}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "phase9l5_detector_hardening_report_path": _relative_to_workspace(
            workspace, json_path
        ),
        "phase9l5_detector_hardening_report_md_path": _relative_to_workspace(
            workspace, md_path
        ),
        "phase9l5_status": status,
    }


def _model_usage_success_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("error_class") is None and payload.get("route_status") in {"ok", "fallback_runtime_success"}:
            count += 1
    return count


def _phase9l_blocker_category(error: Exception) -> str:
    text = str(error)
    if "PaddleOCR" in text or "BLOCKED_OCR" in text or PADDLEOCR_ONEDNN_PIR_ERROR_FRAGMENT in text:
        return "BLOCKED_OCR_RUNTIME"
    if (
        "BLOCKED_PROVIDER" in text
        or "Provider" in text
        or "provider" in text
        or "API key" in text
        or "primary_and_fallback_unavailable" in text
    ):
        return "BLOCKED_PROVIDER"
    if "BLOCKED_QA" in text or "blocker" in text.lower():
        return "BLOCKED_QA"
    if "BLOCKED_DETECTION" in text:
        return "BLOCKED_DETECTION"
    return "BLOCKED_ENVIRONMENT"


def run_phase9l_real_manga_canary(
    workspace: Workspace,
    *,
    input_path: Path,
    project_slug: str = "onepiece-canary",
    pages: int = 1,
    page_start: int = 1,
    ocr_adapter: str = "paddleocr",
    translation_provider: str = "gui_saved",
    detection_adapter: str = "mock",
    language: str = "ch",
    disable_onednn: bool = True,
    disable_paddlex_mkldnn: bool = True,
    no_network: bool = True,
) -> dict[str, Any]:
    if pages < 1 or pages > 4:
        raise ValueError("Phase 9L canary pages must be between 1 and 4.")
    if page_start < 1:
        raise ValueError("Phase 9L canary start page must be at least 1.")
    if translation_provider == "mock":
        raise ValueError("Phase 9L requires a real provider; mock translation cannot produce PASS.")
    report: dict[str, Any] = {
        "schema_version": PHASE9L_CANARY_SCHEMA_VERSION,
        "created_at": utc_now(),
        "status": "running",
        "blocker_category": None,
        "input_file_path": str(input_path),
        "project_slug": project_slug,
        "page_range": f"{page_start}-{page_start + pages - 1}",
        "page_start": page_start,
        "page_limit": pages,
        "ocr_adapter": ocr_adapter,
        "translation_provider": translation_provider,
        "detection_adapter": detection_adapter,
        "runtime_flags": {
            "FLAGS_use_mkldnn": "0" if disable_onednn else None,
            "FLAGS_use_onednn": "0" if disable_onednn else None,
            "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "0" if disable_paddlex_mkldnn else None,
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True" if no_network else None,
        },
        "approved_rules_used": False,
        "raw_nlp_cache_injected": False,
        "warnings": [],
        "stages": [],
        "api_call_count": 0,
    }
    run_id: str | None = None
    report_dir: Path | None = None
    try:
        if not input_path.exists():
            raise ValueError(f"BLOCKED_ENVIRONMENT: input CBZ not found: {input_path}")
        try:
            get_project_by_slug(workspace, project_slug)
        except ValueError:
            create_project(
                workspace,
                slug=project_slug,
                name="OnePiece Phase 9L Canary",
                source_lang="ja",
                target_lang="vi",
                domain="manga",
                genre="canary",
            )
        imported = import_manga_pages(
            workspace,
            path=input_path,
            project_slug=project_slug,
            page_limit=pages,
            page_start=page_start,
        )
        run_id = str(imported["run_id"])
        report["run_id"] = run_id
        report_dir = _phase9l_report_dir(workspace, project_slug=project_slug, run_id=run_id)
        report["stages"].append({"stage": "import", "status": "pass", "pages_imported": imported["pages_imported"]})

        preprocessed = preprocess_manga_pages(workspace, project_slug=project_slug, run_id=run_id, force=True)
        report["stages"].append({"stage": "preprocess", "status": "pass", "pages_processed": preprocessed["pages_processed"]})

        detected = run_manga_detection(workspace, project_slug=project_slug, run_id=run_id, adapter_id=detection_adapter)
        report["detection"] = detected
        detected_count = int(detected.get("box_count") or detected.get("regions_detected") or 0)
        report["stages"].append({"stage": "detection", "status": "pass", "box_count": detected_count})
        if detected_count <= 0:
            raise ValueError("BLOCKED_DETECTION: no boxes were produced for canary pages.")

        ocr = run_manga_ocr(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            adapter_id=ocr_adapter,
            language=language,
            max_pages=pages,
            ocr_variant="auto",
            no_network=no_network,
            disable_onednn=disable_onednn,
            disable_paddlex_mkldnn=disable_paddlex_mkldnn,
            force=True,
        )
        report["ocr"] = ocr
        report["stages"].append({"stage": "ocr", "status": "pass", "result_count": ocr["result_count"]})

        reading_order = generate_manga_reading_order(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            direction_preset="right-to-left",
            include_neighbor_hints=True,
        )
        report["stages"].append({"stage": "reading_order", "status": "pass", "box_count": reading_order["box_count"]})

        primary_model, fallback_model = _load_gui_provider_model_pair(workspace)
        provider_dir = report_dir / "provider"
        provider_preflight = write_provider_preflight(
            workspace,
            run_dir=provider_dir,
            provider_key=translation_provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )
        report["provider_snapshot"] = _redacted_gui_provider_snapshot(workspace)
        report["provider_preflight"] = provider_preflight
        report["stages"].append({"stage": "provider_preflight", "status": "pass" if provider_preflight.get("pass") else "blocked"})
        if not provider_preflight.get("pass"):
            raise ValueError(f"BLOCKED_PROVIDER_OR_ENVIRONMENT: {provider_preflight.get('blocker_reason')}")

        translated = run_manga_translation(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            provider_key=translation_provider,
        )
        report["translation"] = translated
        report["stages"].append({"stage": "translation", "status": "pass", "result_count": translated["result_count"]})
        usage_path = workspace.path / str(translated["model_usage_path"])
        report["api_call_count"] = _model_usage_success_count(usage_path)
        if report["api_call_count"] <= 0 or translated.get("mock_mode"):
            raise ValueError("BLOCKED_PROVIDER_OR_ENVIRONMENT: no real provider translation call was recorded.")

        cleaning = run_manga_cleaning(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            mode="quality_inpaint",
            sfx_policy="leave_unchanged",
        )
        report["stages"].append(
            {
                "stage": "cleaning",
                "status": "pass",
                "cleaned_page_count": cleaning.get("cleaned_page_count"),
            }
        )

        rendering = run_manga_rendering(workspace, project_slug=project_slug, run_id=run_id)
        report["stages"].append(
            {
                "stage": "rendering",
                "status": "pass",
                "rendered_page_count": rendering.get("rendered_page_count"),
            }
        )

        qa = run_manga_visual_qa(workspace, project_slug=project_slug, run_id=run_id)
        readiness = validate_manga_export_readiness(workspace, project_slug=project_slug, run_id=run_id)
        report["qa"] = {
            "blocker_count": readiness["blocker_count"],
            "export_ready": readiness["export_ready"],
            "blockers_path": readiness["blockers_path"],
            "visual_qa_report_path": qa.get("visual_qa_report_path"),
        }
        report["stages"].append({"stage": "visual_qa", "status": "pass" if readiness["export_ready"] else "blocked", "blocker_count": readiness["blocker_count"]})
        if readiness["blocker_count"]:
            raise ValueError("BLOCKED_QA: visual QA blockers prevent Phase 9L export.")

        exported = run_manga_export(
            workspace,
            project_slug=project_slug,
            run_id=run_id,
            include_images=True,
            include_cbz=True,
            include_pdf=True,
            pdf_adapter="pillow",
            allow_qa_blockers=False,
        )
        export_validation = validate_manga_export_manifest(workspace, project_slug=project_slug, run_id=run_id)
        report["export"] = exported
        report["export_validation"] = export_validation
        report["stages"].append({"stage": "export", "status": "pass", "cbz_path": exported.get("cbz_path"), "pdf_path": exported.get("pdf_path")})
        if export_validation["validation_status"] != "valid":
            raise ValueError("BLOCKED_EXPORT: export manifest validation failed.")

        try:
            report["human_review"] = get_manga_human_review_package(workspace, project_slug=project_slug, run_id=run_id)
        except ValueError as exc:
            report["warnings"].append(f"human_review_package_unavailable:{_truncate_text(str(exc), 160)}")

        report["status"] = "PASS"
        report["blocker_category"] = None
    except Exception as exc:
        report["status"] = "BLOCKED_PROVIDER_OR_ENVIRONMENT"
        report["blocker_category"] = _phase9l_blocker_category(exc)
        report["error"] = _truncate_text(str(exc), 1000)
    finally:
        if report_dir is None:
            report_dir = _phase9l_report_dir(workspace, project_slug=project_slug, run_id=run_id)
        report["updated_at"] = utc_now()
        _phase9l_write_report(workspace, report_dir, report)
        if detection_adapter in {
            "opencv",
            OpenCvTextDetectionAdapter.adapter_id,
            "paddleocr_detector",
            PaddleOcrTextDetectionAdapter.adapter_id,
        }:
            report.update(_phase9l5_write_report(workspace, report_dir, report))
            _phase9l_write_report(workspace, report_dir, report)
    return report


PHASE9M_STAGE_ARTIFACTS = {
    "import": "page_manifest.json",
    "provider_preflight": "provider/provider_preflight.json",
    "preprocess": "preprocessing/preprocess_manifest.json",
    "detection": "detection/boxes_merged.json",
    "ocr": "ocr/ocr_results.json",
    "reading_order": "reading_order/reading_order.json",
    "translation": "translation/translation_results.json",
    "cleaning": "cleaning/cleaning_jobs.json",
    "rendering": "rendering/typeset_decisions.json",
    "visual_qa": "qa/visual_qa_report.json",
    "export": "export/export_manifest.json",
    "human_review": "human_review/review_index.md",
}


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _phase9m_progress_path(report_dir: Path) -> Path:
    return report_dir / "phase9m_progress.json"


def _phase9m_write_progress(
    workspace: Workspace,
    report_dir: Path,
    progress: dict[str, Any],
) -> None:
    progress["updated_at"] = utc_now()
    _phase9m_progress_path(report_dir).write_text(
        json_dumps(progress) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Phase 9M Production Progress",
        "",
        f"- Status: `{progress.get('status')}`",
        f"- Current stage: `{progress.get('current_stage')}`",
        f"- Project: `{progress.get('project_slug')}`",
        f"- Run ID: `{progress.get('run_id')}`",
        f"- Pages: `{progress.get('page_limit')}`",
        "",
        "## Stages",
        "",
    ]
    for stage, payload in (progress.get("stages") or {}).items():
        lines.append(
            f"- `{stage}`: `{payload.get('status')}` "
            f"({payload.get('artifact_path') or 'no artifact'})"
        )
    (report_dir / "phase9m_progress.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _phase9m_mark_stage(
    workspace: Workspace,
    report_dir: Path,
    progress: dict[str, Any],
    stage: str,
    *,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    artifact_rel = PHASE9M_STAGE_ARTIFACTS.get(stage)
    progress["current_stage"] = stage
    progress.setdefault("stages", {})[stage] = {
        "status": status,
        "artifact_path": (
            _relative_to_workspace(workspace, report_dir / artifact_rel)
            if artifact_rel
            else None
        ),
        "updated_at": utc_now(),
        **(details or {}),
    }
    _phase9m_write_progress(workspace, report_dir, progress)


def _phase9m_stage_complete(
    report_dir: Path,
    progress: dict[str, Any],
    stage: str,
    *,
    force: bool,
) -> bool:
    if force:
        return False
    stage_state = (progress.get("stages") or {}).get(stage) or {}
    artifact_rel = PHASE9M_STAGE_ARTIFACTS.get(stage)
    return (
        stage_state.get("status") in {"pass", "resumed_existing"}
        and bool(artifact_rel)
        and (report_dir / str(artifact_rel)).exists()
    )


def _phase9m_five_page_gate(
    workspace: Workspace,
    *,
    project_slug: str,
) -> dict[str, Any] | None:
    project_root = workspace.path / "artifacts" / "manga" / project_slug
    if not project_root.exists():
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    for report_path in project_root.glob("*/phase9m_production_report.json"):
        payload = _read_json_file(report_path)
        if payload.get("status") == "PASS" and int(payload.get("page_limit") or 0) == 5:
            candidates.append((str(payload.get("updated_at") or ""), payload))
    return sorted(candidates, key=lambda item: item[0])[-1][1] if candidates else None


def _phase9m_blocked_status(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    lowered = text.lower()
    if (
        "paddleocr" in lowered
        or "blocked_ocr" in lowered
        or PADDLEOCR_ONEDNN_PIR_ERROR_FRAGMENT.lower() in lowered
    ):
        return "BLOCKED_OCR_RUNTIME", "BLOCKED_OCR_RUNTIME"
    if "provider" in lowered or "api key" in lowered or "model route" in lowered:
        return "BLOCKED_PROVIDER_OR_ENVIRONMENT", "BLOCKED_PROVIDER_OR_ENVIRONMENT"
    if (
        "blocked_qa" in lowered
        or "blocker" in lowered
        or "detection" in lowered
        or "export" in lowered
    ):
        return "BLOCKED_QA", "BLOCKED_QA"
    return "BLOCKED_PROVIDER_OR_ENVIRONMENT", "BLOCKED_ENVIRONMENT"


def _phase9m_provider_usage_summary(path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    successful = [
        item
        for item in records
        if item.get("error_class") is None
        and item.get("route_status") in {"ok", "fallback_runtime_success"}
    ]
    models: dict[str, int] = {}
    for item in successful:
        model = str(item.get("model") or item.get("chosen_model") or "unknown")
        models[model] = models.get(model, 0) + 1
    return {
        "record_count": len(records),
        "successful_call_count": len(successful),
        "failed_call_count": len(records) - len(successful),
        "fallback_call_count": sum(
            1
            for item in successful
            if bool(item.get("fallback_used") or item.get("fallback_model_used"))
        ),
        "model_call_counts": models,
    }


def _phase9m_sync_provider_artifacts(
    workspace: Workspace,
    *,
    report_dir: Path,
    preflight: dict[str, Any],
    translation: dict[str, Any] | None,
    provider_key: str,
    primary_model: str,
    fallback_model: str | None,
) -> dict[str, Any]:
    provider_dir = report_dir / "provider"
    provider_dir.mkdir(parents=True, exist_ok=True)
    model_policy = build_rollout_model_policy(
        provider_key=provider_key,
        primary_model=primary_model,
        fallback_model=fallback_model,
        chosen_model=preflight.get("chosen_model"),
        fallback_model_used=bool(preflight.get("fallback_model_used")),
        primary_status=preflight.get("primary_status"),
        fallback_status=preflight.get("fallback_status"),
    )
    policy_path = provider_dir / "model_policy_snapshot.json"
    policy_path.write_text(json_dumps(model_policy) + "\n", encoding="utf-8")
    usage_path = provider_dir / "model_usage.jsonl"
    if translation and translation.get("model_usage_path"):
        source = workspace.path / str(translation["model_usage_path"])
        if source.exists():
            shutil.copyfile(source, usage_path)
    if not usage_path.exists():
        usage_path.write_text("", encoding="utf-8")
    usage_summary = _phase9m_provider_usage_summary(usage_path)
    (provider_dir / "model_usage_summary.json").write_text(
        json_dumps(usage_summary) + "\n",
        encoding="utf-8",
    )
    return {
        "model_policy_snapshot_path": _relative_to_workspace(workspace, policy_path),
        "model_usage_path": _relative_to_workspace(workspace, usage_path),
        "model_usage_summary_path": _relative_to_workspace(
            workspace, provider_dir / "model_usage_summary.json"
        ),
        "usage": usage_summary,
    }


def _phase9m_enrich_provider_preflight(
    workspace: Workspace,
    *,
    provider_dir: Path,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    snapshot = _redacted_gui_provider_snapshot(workspace)
    primary_status = preflight.get("primary_status") or {}
    fallback_status = preflight.get("fallback_status") or {}
    enriched = {
        **preflight,
        "provider_type": snapshot.get("provider_type"),
        "provider_name": snapshot.get("provider_name"),
        "base_url_normalized": str(snapshot.get("base_url") or "").rstrip("/"),
        "api_key_env_var": None,
        "api_key_configured": bool(snapshot.get("api_key_configured")),
        "api_key_redacted": True,
        "primary_route_status": primary_status.get("status"),
        "fallback_route_status": fallback_status.get("status"),
        "minimal_request_status": (
            "pass" if primary_status.get("ok") or fallback_status.get("ok") else "blocked"
        ),
        "warnings": [],
        "errors": (
            []
            if preflight.get("pass")
            else [str(preflight.get("blocker_reason") or "provider_preflight_failed")]
        ),
    }
    (provider_dir / "provider_preflight.json").write_text(
        json_dumps(enriched) + "\n",
        encoding="utf-8",
    )
    (provider_dir / "provider_preflight.md").write_text(
        "\n".join(
            [
                "# Phase 9M Provider Preflight",
                "",
                f"- Status: `{'PASS' if enriched.get('pass') else 'BLOCKED'}`",
                f"- Provider type: `{enriched.get('provider_type')}`",
                f"- Provider name: `{enriched.get('provider_name')}`",
                f"- Base URL: `{enriched.get('base_url_normalized')}`",
                f"- Primary model: `{enriched.get('primary_model')}`",
                f"- Primary route: `{enriched.get('primary_route_status')}`",
                f"- Fallback model: `{enriched.get('fallback_model')}`",
                f"- Fallback route: `{enriched.get('fallback_route_status')}`",
                f"- Minimal request: `{enriched.get('minimal_request_status')}`",
                "- API key configured: "
                f"`{str(bool(enriched.get('api_key_configured'))).lower()}`",
                "- API key redacted: `true`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return enriched


def _phase9m_write_report(
    workspace: Workspace,
    report_dir: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    report["updated_at"] = utc_now()
    json_paths = [
        report_dir / "phase9m_production_report.json",
        report_dir / "final_rollout_report.json",
    ]
    md_paths = [
        report_dir / "phase9m_production_report.md",
        report_dir / "final_rollout_report.md",
    ]
    for path in json_paths:
        path.write_text(json_dumps(report) + "\n", encoding="utf-8")
    qa = report.get("qa") or {}
    provider = report.get("provider") or {}
    export = report.get("export") or {}
    markdown = "\n".join(
        [
            "# Phase 9M Manga Production Rollout Report",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Blocker category: `{report.get('blocker_category')}`",
            f"- Project: `{report.get('project_slug')}`",
            f"- Run ID: `{report.get('run_id')}`",
            f"- Page range: `{report.get('page_range')}`",
            f"- Detector: `{report.get('detection_adapter')}`",
            f"- OCR: `{report.get('ocr_adapter')}`",
            f"- Provider preflight: `{provider.get('preflight_pass')}`",
            f"- Primary model: `{provider.get('primary_model')}`",
            f"- Fallback model: `{provider.get('fallback_model')}`",
            f"- API calls: `{report.get('api_call_count')}`",
            f"- QA blockers: `{qa.get('blocker_count')}`",
            f"- QA warnings: `{qa.get('warning_count')}`",
            f"- Images exported: `{export.get('image_export_count')}`",
            f"- CBZ path: `{export.get('cbz_path')}`",
            f"- PDF status: `{export.get('pdf_status')}`",
            f"- PDF path: `{export.get('pdf_path')}`",
            f"- Approved rules used: `{report.get('approved_rules_used')}`",
            f"- Raw NLP cache injected: `{report.get('raw_nlp_cache_injected')}`",
            "",
        ]
    )
    if report.get("error"):
        markdown += f"## Error\n\n`{_truncate_text(str(report['error']), 500)}`\n"
    for path in md_paths:
        path.write_text(markdown, encoding="utf-8")
    report.update(
        {
            "phase9m_production_report_path": _relative_to_workspace(
                workspace, json_paths[0]
            ),
            "phase9m_production_report_md_path": _relative_to_workspace(
                workspace, md_paths[0]
            ),
            "final_rollout_report_path": _relative_to_workspace(
                workspace, json_paths[1]
            ),
            "final_rollout_report_md_path": _relative_to_workspace(
                workspace, md_paths[1]
            ),
        }
    )
    for path in json_paths:
        path.write_text(json_dumps(report) + "\n", encoding="utf-8")
    return report


def run_phase9m_manga_production_rollout(
    workspace: Workspace,
    *,
    input_path: Path,
    project_slug: str = "onepiece-production",
    pages: int = 5,
    page_start: int = 1,
    ocr_adapter: str = "paddleocr",
    translation_provider: str = "gui_saved",
    detection_adapter: str = "paddleocr_text_detector",
    language: str = "ch",
    disable_onednn: bool = True,
    disable_paddlex_mkldnn: bool = True,
    no_network: bool = True,
    resume_run_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if pages not in {5, 10}:
        raise ValueError("Phase 9M rollout supports exactly 5 or 10 pages.")
    if page_start < 1:
        raise ValueError("Phase 9M start page must be at least 1.")
    if translation_provider == "mock":
        raise ValueError("Phase 9M requires a real provider; mock translation cannot produce PASS.")
    if ocr_adapter != "paddleocr":
        raise ValueError("Phase 9M final rollout requires the real `paddleocr` adapter.")
    if detection_adapter != PaddleOcrTextDetectionAdapter.adapter_id:
        raise ValueError(
            "Phase 9M final rollout requires `paddleocr_text_detector`."
        )
    if pages == 10 and _phase9m_five_page_gate(
        workspace, project_slug=project_slug
    ) is None:
        raise ValueError(
            "BLOCKED_CANARY_NOT_PASS: a five-page Phase 9M PASS is required before ten pages."
        )

    report: dict[str, Any] = {
        "schema_version": PHASE9M_PRODUCTION_SCHEMA_VERSION,
        "created_at": utc_now(),
        "status": "running",
        "blocker_category": None,
        "input_file_path": str(input_path),
        "project_slug": project_slug,
        "page_start": page_start,
        "page_limit": pages,
        "page_range": f"{page_start}-{page_start + pages - 1}",
        "detection_adapter": detection_adapter,
        "ocr_adapter": ocr_adapter,
        "translation_provider": translation_provider,
        "runtime_flags": {
            "FLAGS_use_mkldnn": "0" if disable_onednn else None,
            "FLAGS_use_onednn": "0" if disable_onednn else None,
            "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": (
                "0" if disable_paddlex_mkldnn else None
            ),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True" if no_network else None,
        },
        "approved_rules_used": False,
        "raw_nlp_cache_injected": False,
        "cloud_image_upload": False,
        "api_call_count": 0,
        "warnings": [],
        "resumed": bool(resume_run_id),
    }
    run_id = resume_run_id
    report_dir: Path | None = None
    progress: dict[str, Any] = {}
    try:
        if not input_path.exists():
            raise ValueError(f"BLOCKED_ENVIRONMENT: input CBZ not found: {input_path}")
        try:
            get_project_by_slug(workspace, project_slug)
        except ValueError:
            create_project(
                workspace,
                slug=project_slug,
                name="OnePiece Phase 9M Production",
                source_lang="ja",
                target_lang="vi",
                domain="manga",
                genre="production",
            )

        if resume_run_id:
            report_dir = _artifact_root_for_run(
                workspace, project_slug=project_slug, run_id=resume_run_id
            )
            manifest = _read_json_file(report_dir / "page_manifest.json")
            if not manifest:
                raise ValueError(
                    f"BLOCKED_FILESYSTEM: resume run manifest not found: {resume_run_id}"
                )
            if int(manifest.get("page_count") or 0) != pages:
                raise ValueError(
                    "BLOCKED_FILESYSTEM: resume page count does not match requested rollout."
                )
            progress = _read_json_file(_phase9m_progress_path(report_dir))
            if not progress:
                raise ValueError(
                    f"BLOCKED_FILESYSTEM: Phase 9M progress not found: {resume_run_id}"
                )
            run_id = resume_run_id
            report["run_id"] = run_id
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "import",
                status="resumed_existing",
                details={"pages_imported": pages},
            )
        else:
            imported = import_manga_pages(
                workspace,
                path=input_path,
                project_slug=project_slug,
                page_limit=pages,
                page_start=page_start,
            )
            run_id = str(imported["run_id"])
            report["run_id"] = run_id
            report_dir = _artifact_root_for_run(
                workspace, project_slug=project_slug, run_id=run_id
            )
            progress = {
                "schema_version": PHASE9M_PROGRESS_SCHEMA_VERSION,
                "created_at": utc_now(),
                "status": "running",
                "current_stage": "import",
                "project_slug": project_slug,
                "run_id": run_id,
                "page_start": page_start,
                "page_limit": pages,
                "page_range": report["page_range"],
                "stages": {},
            }
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "import",
                status="pass",
                details={"pages_imported": imported["pages_imported"]},
            )

        primary_model, fallback_model = _load_gui_provider_model_pair(workspace)
        provider_dir = report_dir / "provider"
        provider_preflight = write_provider_preflight(
            workspace,
            run_dir=provider_dir,
            provider_key=translation_provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )
        provider_preflight = _phase9m_enrich_provider_preflight(
            workspace,
            provider_dir=provider_dir,
            preflight=provider_preflight,
        )
        provider_artifacts = _phase9m_sync_provider_artifacts(
            workspace,
            report_dir=report_dir,
            preflight=provider_preflight,
            translation=None,
            provider_key=translation_provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )
        report["provider"] = {
            "preflight_pass": bool(provider_preflight.get("pass")),
            "provider_snapshot": _redacted_gui_provider_snapshot(workspace),
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "chosen_model": provider_preflight.get("chosen_model"),
            "fallback_model_used": bool(
                provider_preflight.get("fallback_model_used")
            ),
            "provider_preflight_path": _relative_to_workspace(
                workspace, provider_dir / "provider_preflight.json"
            ),
            "provider_preflight_md_path": _relative_to_workspace(
                workspace, provider_dir / "provider_preflight.md"
            ),
            **provider_artifacts,
        }
        _phase9m_mark_stage(
            workspace,
            report_dir,
            progress,
            "provider_preflight",
            status="pass" if provider_preflight.get("pass") else "blocked",
            details={"chosen_model": provider_preflight.get("chosen_model")},
        )
        if not provider_preflight.get("pass"):
            raise ValueError(
                "BLOCKED_PROVIDER_OR_ENVIRONMENT: "
                f"{provider_preflight.get('blocker_reason')}"
            )

        if _phase9m_stage_complete(report_dir, progress, "preprocess", force=force):
            _phase9m_mark_stage(
                workspace, report_dir, progress, "preprocess", status="resumed_existing"
            )
        else:
            preprocessed = preprocess_manga_pages(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                force=True,
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "preprocess",
                status="pass",
                details={"pages_processed": preprocessed["pages_processed"]},
            )

        if _phase9m_stage_complete(report_dir, progress, "detection", force=force):
            _phase9m_mark_stage(
                workspace, report_dir, progress, "detection", status="resumed_existing"
            )
        else:
            detected = run_manga_detection(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                adapter_id=detection_adapter,
            )
            detected_count = int(
                detected.get("box_count") or detected.get("regions_detected") or 0
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "detection",
                status="pass" if detected_count > 0 else "blocked",
                details={"box_count": detected_count},
            )
            if detected_count <= 0:
                raise ValueError(
                    "BLOCKED_DETECTION: no boxes were produced for production pages."
                )

        if _phase9m_stage_complete(report_dir, progress, "ocr", force=force):
            _phase9m_mark_stage(
                workspace, report_dir, progress, "ocr", status="resumed_existing"
            )
        else:
            ocr = run_manga_ocr(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                adapter_id=ocr_adapter,
                language=language,
                max_pages=pages,
                ocr_variant="auto",
                no_network=no_network,
                disable_onednn=disable_onednn,
                disable_paddlex_mkldnn=disable_paddlex_mkldnn,
                force=True,
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "ocr",
                status="pass",
                details={"result_count": ocr["result_count"]},
            )

        if _phase9m_stage_complete(report_dir, progress, "reading_order", force=force):
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "reading_order",
                status="resumed_existing",
            )
        else:
            reading_order = generate_manga_reading_order(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                direction_preset="right-to-left",
                include_neighbor_hints=True,
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "reading_order",
                status="pass",
                details={"box_count": reading_order["box_count"]},
            )

        translation: dict[str, Any] | None = None
        if _phase9m_stage_complete(report_dir, progress, "translation", force=force):
            translation = {
                "model_usage_path": _relative_to_workspace(
                    workspace, report_dir / "translation" / "model_usage.jsonl"
                ),
                "translation_results_path": _relative_to_workspace(
                    workspace, report_dir / "translation" / "translation_results.json"
                ),
                "mock_mode": False,
            }
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "translation",
                status="resumed_existing",
            )
        else:
            translation = run_manga_translation(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                provider_key=translation_provider,
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "translation",
                status="pass",
                details={"result_count": translation["result_count"]},
            )
        provider_artifacts = _phase9m_sync_provider_artifacts(
            workspace,
            report_dir=report_dir,
            preflight=provider_preflight,
            translation=translation,
            provider_key=translation_provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )
        report["provider"].update(provider_artifacts)
        report["api_call_count"] = int(
            provider_artifacts["usage"]["successful_call_count"]
        )
        if report["api_call_count"] <= 0 or translation.get("mock_mode"):
            raise ValueError(
                "BLOCKED_PROVIDER_OR_ENVIRONMENT: no real provider translation call was recorded."
            )

        if _phase9m_stage_complete(report_dir, progress, "cleaning", force=force):
            _phase9m_mark_stage(
                workspace, report_dir, progress, "cleaning", status="resumed_existing"
            )
        else:
            cleaning = run_manga_cleaning(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                mode="quality_inpaint",
                sfx_policy="leave_unchanged",
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "cleaning",
                status="pass",
                details={"cleaned_page_count": cleaning.get("cleaned_page_count")},
            )

        if _phase9m_stage_complete(report_dir, progress, "rendering", force=force):
            _phase9m_mark_stage(
                workspace, report_dir, progress, "rendering", status="resumed_existing"
            )
        else:
            rendering = run_manga_rendering(
                workspace, project_slug=project_slug, run_id=run_id
            )
            _phase9m_mark_stage(
                workspace,
                report_dir,
                progress,
                "rendering",
                status="pass",
                details={
                    "rendered_page_count": rendering.get("rendered_page_count")
                },
            )

        if _phase9m_stage_complete(report_dir, progress, "visual_qa", force=force):
            qa = {
                "visual_qa_report_path": _relative_to_workspace(
                    workspace, report_dir / "qa" / "visual_qa_report.json"
                ),
                "visual_qa_report_md_path": _relative_to_workspace(
                    workspace, report_dir / "qa" / "visual_qa_report.md"
                ),
            }
            qa_stage_status = "resumed_existing"
        else:
            qa = run_manga_visual_qa(
                workspace, project_slug=project_slug, run_id=run_id
            )
            qa_stage_status = "pass"
        readiness = validate_manga_export_readiness(
            workspace, project_slug=project_slug, run_id=run_id
        )
        qa_payload = _read_json_file(report_dir / "qa" / "visual_qa_report.json")
        report["qa"] = {
            "blocker_count": int(readiness["blocker_count"]),
            "warning_count": int(qa_payload.get("warning_count") or 0),
            "issue_count": int(qa_payload.get("issue_count") or 0),
            "export_ready": bool(readiness["export_ready"]),
            "blockers_path": readiness["blockers_path"],
            "visual_qa_report_path": qa.get("visual_qa_report_path"),
            "visual_qa_report_md_path": qa.get("visual_qa_report_md_path"),
        }
        _phase9m_mark_stage(
            workspace,
            report_dir,
            progress,
            "visual_qa",
            status=(
                qa_stage_status if readiness["export_ready"] else "blocked"
            ),
            details={
                "blocker_count": readiness["blocker_count"],
                "warning_count": report["qa"]["warning_count"],
            },
        )
        if readiness["blocker_count"]:
            raise ValueError(
                "BLOCKED_QA: visual QA blockers prevent Phase 9M export."
            )

        if _phase9m_stage_complete(report_dir, progress, "export", force=force):
            exported = _read_json_file(report_dir / "export" / "export_manifest.json")
            exported.setdefault(
                "image_export_count", len(exported.get("image_export_paths") or [])
            )
            exported.setdefault(
                "cbz_created",
                bool(
                    exported.get("cbz_path")
                    and (workspace.path / str(exported["cbz_path"])).exists()
                ),
            )
            exported.setdefault(
                "export_manifest_path",
                _relative_to_workspace(
                    workspace, report_dir / "export" / "export_manifest.json"
                ),
            )
            exported.setdefault(
                "export_summary_path",
                _relative_to_workspace(
                    workspace, report_dir / "export" / "export_summary.md"
                ),
            )
            export_stage_status = "resumed_existing"
        else:
            exported = run_manga_export(
                workspace,
                project_slug=project_slug,
                run_id=run_id,
                include_images=True,
                include_cbz=True,
                include_pdf=True,
                pdf_adapter="pillow",
                allow_qa_blockers=False,
            )
            export_stage_status = "pass"
        export_validation = validate_manga_export_manifest(
            workspace, project_slug=project_slug, run_id=run_id
        )
        report["export"] = {
            "project_id": exported.get("project_id"),
            "project_slug": exported.get("project_slug"),
            "run_id": exported.get("run_id"),
            "page_count": exported.get("page_count"),
            "image_export_count": exported.get("image_export_count"),
            "images_dir": exported.get("images_dir"),
            "cbz_created": exported.get("cbz_created"),
            "cbz_path": exported.get("cbz_path"),
            "pdf_status": exported.get("pdf_status"),
            "pdf_path": exported.get("pdf_path"),
            "qa_export_ready": exported.get("qa_export_ready"),
            "allow_qa_blockers": exported.get("allow_qa_blockers"),
            "export_manifest_path": exported.get("export_manifest_path"),
            "export_summary_path": exported.get("export_summary_path"),
            "warnings": exported.get("warnings") or [],
            "validation_status": export_validation.get("validation_status"),
            "validation_issues": export_validation.get("issues") or [],
        }
        _phase9m_mark_stage(
            workspace,
            report_dir,
            progress,
            "export",
            status=(
                export_stage_status
                if export_validation.get("validation_status") == "valid"
                else "blocked"
            ),
            details={
                "image_export_count": exported.get("image_export_count"),
                "pdf_status": exported.get("pdf_status"),
            },
        )
        if export_validation.get("validation_status") != "valid":
            raise ValueError("BLOCKED_EXPORT: export manifest validation failed.")

        human_review = get_manga_human_review_package(
            workspace, project_slug=project_slug, run_id=run_id
        )
        report["human_review"] = human_review
        _phase9m_mark_stage(
            workspace,
            report_dir,
            progress,
            "human_review",
            status="pass",
            details={
                "review_index_path": human_review.get("human_review_index_path")
            },
        )
        report["status"] = "PASS"
        report["blocker_category"] = None
        progress["status"] = "PASS"
        progress["current_stage"] = "completed"
    except Exception as exc:
        status, category = _phase9m_blocked_status(exc)
        report["status"] = status
        report["blocker_category"] = category
        report["error"] = _truncate_text(str(exc), 1000)
        if progress:
            progress["status"] = status
    finally:
        if report_dir is None:
            blocked_id = f"phase9m_blocked_{uuid.uuid4().hex[:10]}"
            report_dir = (
                workspace.path
                / "artifacts"
                / "manga"
                / project_slug
                / blocked_id
            )
            report_dir.mkdir(parents=True, exist_ok=True)
            report["run_id"] = report.get("run_id") or blocked_id
        if progress:
            _phase9m_write_progress(workspace, report_dir, progress)
            report["progress_path"] = _relative_to_workspace(
                workspace, _phase9m_progress_path(report_dir)
            )
            report["progress_md_path"] = _relative_to_workspace(
                workspace, report_dir / "phase9m_progress.md"
            )
        _phase9m_write_report(workspace, report_dir, report)
    return report


def get_phase9m_rollout_status(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    report_dir = _artifact_root_for_run(
        workspace, project_slug=project_slug, run_id=run_id
    )
    report = _read_json_file(report_dir / "phase9m_production_report.json")
    progress = _read_json_file(report_dir / "phase9m_progress.json")
    if not report and not progress:
        return {"status": "not_started", "project_slug": project_slug, "run_id": run_id}
    return {
        "status": report.get("status") or progress.get("status") or "running",
        "project_slug": project_slug,
        "run_id": run_id,
        "page_range": report.get("page_range") or progress.get("page_range"),
        "current_stage": progress.get("current_stage"),
        "stages": progress.get("stages") or {},
        "provider": report.get("provider") or {},
        "qa": report.get("qa") or {},
        "export": report.get("export") or {},
        "human_review": report.get("human_review") or {},
        "phase9m_production_report_path": report.get(
            "phase9m_production_report_path"
        ),
        "final_rollout_report_path": report.get("final_rollout_report_path"),
    }


def get_manga_export_status(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT id, manifest_path, summary_path, images_dir, cbz_path, pdf_path,
                   pdf_status, page_count, qa_report_ref, qa_export_ready,
                   allow_qa_blockers, warnings_json, created_at, updated_at
            FROM manga_export_runs
            WHERE project_id = ? AND run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (project["id"], run_id),
        ).fetchone()
    if row is None:
        return {
            "project_id": project["id"],
            "project_slug": project_slug,
            "run_id": run_id,
            "status": "not_started",
        }
    data = row_to_dict(row, json_fields=("warnings_json",))
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "status": "completed",
        "export_run_id": data["id"],
        "export_manifest_path": data["manifest_path"],
        "export_summary_path": data["summary_path"],
        "images_dir": data["images_dir"],
        "cbz_path": data["cbz_path"],
        "pdf_path": data["pdf_path"],
        "pdf_status": data["pdf_status"],
        "page_count": data["page_count"],
        "qa_report_ref": data["qa_report_ref"],
        "qa_export_ready": bool(data["qa_export_ready"]),
        "allow_qa_blockers": bool(data["allow_qa_blockers"]),
        "warnings": data.get("warnings_json") or [],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
    }


def get_manga_export_folder(
    workspace: Workspace,
    *,
    project_slug: str,
    run_id: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    export_dir = _export_dir_for_run(workspace, project_slug=project_slug, run_id=run_id)
    return {
        "project_id": project["id"],
        "project_slug": project_slug,
        "run_id": run_id,
        "export_dir": _relative_artifact(workspace, export_dir),
        "absolute_export_dir": str(export_dir),
    }


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
