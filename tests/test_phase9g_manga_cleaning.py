from __future__ import annotations

from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import struct
import traceback
import zlib

from typer.testing import CliRunner

from nts_cli.main import app
import nts_core.manga as manga_core
from nts_core.text_import import sha256_file


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def png_bytes(width: int, height: int, rgb: tuple[int, int, int] = (40, 90, 140)) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    raw_rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw_rows))
        + chunk(b"IEND", b"")
    )


def invoke_ok(args: list[str]) -> dict:
    result = runner.invoke(app, args)
    details = ""
    if result.exception is not None:
        details = "".join(traceback.format_exception(result.exception))
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}\n{details}"
    return parse_json(result.output)["data"]


def init_workspace_project(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    init = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    assert init.exit_code == 0, init.output
    project = runner.invoke(
        app,
        [
            "project",
            "create",
            "--workspace",
            str(workspace),
            "--slug",
            "demo",
            "--name",
            "Demo Manga",
            "--source-lang",
            "ja",
            "--target-lang",
            "vi",
            "--domain",
            "manga",
            "--json",
        ],
    )
    assert project.exit_code == 0, project.output
    return workspace


def build_phase9f_workspace(tmp_path: Path) -> tuple[Path, str, Path]:
    workspace = init_workspace_project(tmp_path)
    source_image = tmp_path / "page.png"
    source_image.write_bytes(png_bytes(120, 90))
    imported = invoke_ok(
        ["manga", "import", str(source_image), "--workspace", str(workspace), "--project", "demo", "--json"]
    )
    run_id = imported["run_id"]
    invoke_ok(["manga", "preprocess", run_id, "--workspace", str(workspace), "--project", "demo", "--json"])
    boxes_path = tmp_path / "boxes.json"
    boxes_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 1,
                        "boxes": [
                            {
                                "box_id": "b_rect",
                                "bbox": [10, 10, 20, 12],
                                "polygon": None,
                                "box_type": "dialogue",
                                "reading_order": 1,
                            },
                            {
                                "box_id": "b_poly",
                                "bbox": [55, 15, 22, 18],
                                "polygon": [[55, 15], [77, 15], [66, 33]],
                                "box_type": "caption",
                                "reading_order": 2,
                            },
                            {
                                "box_id": "b_sfx",
                                "bbox": [40, 58, 24, 16],
                                "polygon": None,
                                "box_type": "sfx",
                                "reading_order": 3,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    invoke_ok(["manga", "boxes", "import", str(boxes_path), "--workspace", str(workspace), "--project", "demo", "--json"])
    invoke_ok(["manga", "ocr", "run", run_id, "--workspace", str(workspace), "--project", "demo", "--json"])
    invoke_ok(
        [
            "manga",
            "reading-order",
            "generate",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--direction",
            "manual",
            "--json",
        ]
    )
    invoke_ok(["manga", "translate", "run", run_id, "--workspace", str(workspace), "--project", "demo", "--json"])
    return workspace, run_id, source_image


def pixel(path: Path, xy: tuple[int, int]) -> tuple[int, ...]:
    Image, _ImageOps = manga_core._load_pillow()
    with Image.open(manga_core._windows_long_path(path)) as image:
        return image.convert("RGB").getpixel(xy)


def imported_page_path(workspace: Path, run_id: str) -> Path:
    manifest_path = workspace / "artifacts" / "manga" / "demo" / run_id / "page_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return workspace / manifest["pages"][0]["artifact_relpath"]


def test_phase9m3_widen_retry_is_once_capped_and_skips_preserved_regions(
    tmp_path: Path,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    mask = Image.new("L", (80, 40), 0)
    for x in range(5, 9):
        for y in range(5, 9):
            mask.putpixel((x, y), 255)
    for x in range(31, 41):
        for y in range(3, 16):
            mask.putpixel((x, y), 255)
    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "widened.png"
    mask.save(mask_path)
    decisions = [
        {
            "box_id": "retry",
            "bbox": [0, 0, 20, 20],
            "cleaning_policy": "glyph_inpaint",
            "quality_region_type": "background_text",
        },
        {
            "box_id": "cap",
            "bbox": [30, 0, 20, 20],
            "cleaning_policy": "glyph_inpaint",
            "quality_region_type": "background_text",
        },
        {
            "box_id": "preserve",
            "bbox": [45, 0, 15, 20],
            "cleaning_policy": "preserve",
            "quality_region_type": "title_art",
        },
    ]

    result = manga_core._widen_quality_mask_once(
        mask_path=mask_path,
        region_decisions=decisions,
        output_path=output_path,
    )

    assert result["status"] == "applied"
    assert result["retried_box_ids"] == ["retry"]
    assert result["skipped_box_ids"] == ["cap"]
    assert result["mask_area_ratio"] <= manga_core.MANGA_PAGE_MASK_AREA_RATIO_LIMIT
    with Image.open(output_path) as widened:
        assert manga_core._mask_crop_ratio(widened, decisions[0]["bbox"]) <= 0.35
        assert manga_core._mask_crop_ratio(widened, decisions[2]["bbox"]) == 0.0


def test_phase9m3_ladder_logs_rungs_and_prefers_reviewed_manual_mask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    root = tmp_path / "workspace"
    cleaning_dir = root / "artifacts" / "manga" / "demo" / "run" / "cleaning"
    for child in ("masks", "manual_masks", "cleaned_pages", "quality"):
        (cleaning_dir / child).mkdir(parents=True, exist_ok=True)
    source_path = cleaning_dir / "source.png"
    original_mask = cleaning_dir / "masks" / "page_0001_mask.png"
    manual_mask_path = cleaning_dir / "manual_masks" / "page_0001_mask.png"
    Image.new("RGB", (80, 60), "white").save(source_path)
    mask = Image.new("L", (80, 60), 0)
    for x in range(10, 15):
        for y in range(10, 15):
            mask.putpixel((x, y), 255)
    mask.save(original_mask)
    manual = Image.new("L", (80, 60), 0)
    for x in range(9, 17):
        for y in range(9, 17):
            manual.putpixel((x, y), 255)
    manual.save(manual_mask_path)
    (cleaning_dir / "manual_masks" / "manual_mask_decisions.json").write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "source_page": 1,
                        "run_page_index": 1,
                        "page_id": "p",
                        "scope": "page",
                        "reviewer": "reviewer-a",
                        "reason": "Reviewed synthetic residual mask.",
                        "created_at": "2026-06-12T00:00:00Z",
                        "safety_mode": "reviewed_manual_mask",
                        "decision": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class WorkspaceStub:
        path = root

    class AdapterStub:
        adapter_id = "test"
        adapter_version = "test"
        execution_mode = "local"

        def clean(self, *, image_path, output_path, **kwargs):
            with Image.open(image_path) as source:
                source.save(output_path)
            return output_path, [], "success"

    def fake_measure(workspace, *, jobs, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1, 1), "white").save(output_path)
        mask_artifact = str(jobs[0]["mask_artifact"])
        residual = 0.1 if "manual_masks" in mask_artifact else 0.3
        return [
            {
                "page_id": "p",
                "page_index": 1,
                "mask_area_ratio": jobs[0].get("mask_area_ratio", 0.0),
                "large_white_block_detected": False,
                "residual_edge_ratio": residual,
            }
        ]

    monkeypatch.setattr(
        manga_core,
        "_write_cleaning_quality_contact_sheet",
        fake_measure,
    )
    decisions = [
        {
            "box_id": "box",
            "bbox": [5, 5, 30, 30],
            "cleaning_policy": "glyph_inpaint",
            "quality_region_type": "background_text",
        },
        {
            "box_id": "title",
            "bbox": [45, 5, 30, 30],
            "cleaning_policy": "preserve",
            "quality_region_type": "title_art",
        },
    ]
    job = {
        "page_id": "p",
        "page_index": 1,
        "input_image_artifact": source_path.relative_to(root).as_posix(),
        "mask_artifact": original_mask.relative_to(root).as_posix(),
        "output_image_artifact": None,
        "region_decisions": decisions,
        "mask_area_ratio": 0.01,
    }

    reports = manga_core._apply_cleaning_escalation_ladder(
        WorkspaceStub(),
        jobs=[job],
        initial_reports=[
            {
                "page_id": "p",
                "page_index": 1,
                "large_white_block_detected": False,
                "residual_edge_ratio": 0.3,
            }
        ],
        adapter=AdapterStub(),
        fill_color=(255, 255, 255),
        cleaning_dir=cleaning_dir,
    )

    escalation = job["cleaning_escalation"]
    assert [attempt["rung"] for attempt in escalation["attempts"]] == [
        "widen_mask_retry",
        "manual_mask",
    ]
    assert len(
        [
            attempt
            for attempt in escalation["attempts"]
            if attempt["rung"] == "widen_mask_retry"
        ]
    ) == 1
    assert escalation["status"] == "pass"
    assert "manual_masks" in job["mask_artifact"]
    assert reports[0]["residual_edge_ratio"] == 0.1


def test_phase9m3_manual_mask_requires_matching_reviewer_decision(
    tmp_path: Path,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    manual_mask_path = tmp_path / "page_0010_mask.png"
    Image.new("L", (80, 60), 0).save(manual_mask_path)
    decisions = [
        {
            "box_id": "box",
            "bbox": [5, 5, 30, 30],
            "cleaning_policy": "glyph_inpaint",
        }
    ]

    mask, result = manga_core._reviewed_manual_mask(
        manual_mask_path=manual_mask_path,
        expected_size=(80, 60),
        region_decisions=decisions,
        manual_mask_decisions=[],
        source_page=10,
        run_page_index=3,
        page_id="page-10",
    )

    assert mask is None
    assert result["reason"] == "manual_mask_reviewer_decision_missing"


def test_phase9m3_no_manual_mask_path_leaves_rung_unavailable(
    tmp_path: Path,
) -> None:
    review = [
        {
            "source_page": 10,
            "run_page_index": 3,
            "page_id": "page-10",
            "scope": "page",
            "reviewer": "reviewer-a",
            "reason": "No-mask behavior guard.",
            "created_at": "2026-06-12T00:00:00Z",
            "safety_mode": "reviewed_manual_mask",
            "decision": "approved",
        }
    ]

    mask, result = manga_core._reviewed_manual_mask(
        manual_mask_path=tmp_path / "missing_page_0010_mask.png",
        expected_size=(80, 60),
        region_decisions=[],
        manual_mask_decisions=review,
        source_page=10,
        run_page_index=3,
        page_id="page-10",
    )

    assert mask is None
    assert result == {"status": "unavailable", "reason": "manual_mask_missing"}


def test_phase9m3_huge_manual_mask_triggers_destructive_cleaning_blocker(
    tmp_path: Path,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    manual_mask_path = tmp_path / "page_0010_mask.png"
    Image.new("L", (100, 100), 255).save(manual_mask_path)
    decisions = [
        {
            "box_id": "box",
            "bbox": [10, 10, 40, 40],
            "cleaning_policy": "glyph_inpaint",
        }
    ]
    review = [
        {
            "source_page": 10,
            "run_page_index": 3,
            "page_id": "page-10",
            "scope": "page",
            "reviewer": "reviewer-a",
            "reason": "Synthetic destructive mask rejection.",
            "created_at": "2026-06-12T00:00:00Z",
            "safety_mode": "reviewed_manual_mask",
            "decision": "approved",
        }
    ]

    mask, result = manga_core._reviewed_manual_mask(
        manual_mask_path=manual_mask_path,
        expected_size=(100, 100),
        region_decisions=decisions,
        manual_mask_decisions=review,
        source_page=10,
        run_page_index=3,
        page_id="page-10",
    )

    assert mask is None
    assert str(result["reason"]).startswith("page_mask_area_ratio_exceeded:")


def test_phase9m3_manual_mask_source_mapping_uses_source_page_filename(
    tmp_path: Path,
    monkeypatch,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    root = tmp_path / "workspace"
    run_root = root / "artifacts" / "manga" / "demo" / "run"
    cleaning_dir = run_root / "cleaning"
    for child in ("masks", "manual_masks", "cleaned_pages", "quality"):
        (cleaning_dir / child).mkdir(parents=True, exist_ok=True)
    (run_root / "page_manifest.json").write_text(
        json.dumps({"page_start": 8}),
        encoding="utf-8",
    )
    source_path = cleaning_dir / "source.png"
    original_mask = cleaning_dir / "masks" / "page_0003_mask.png"
    manual_mask = cleaning_dir / "manual_masks" / "page_0010_mask.png"
    Image.new("RGB", (80, 60), "white").save(source_path)
    Image.new("L", (80, 60), 0).save(original_mask)
    reviewed = Image.new("L", (80, 60), 0)
    for x in range(10, 16):
        for y in range(10, 16):
            reviewed.putpixel((x, y), 255)
    reviewed.save(manual_mask)
    (cleaning_dir / "manual_masks" / "manual_mask_decisions.json").write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "source_page": 10,
                        "run_page_index": 3,
                        "page_id": "page-10",
                        "scope": "boxes",
                        "box_ids": ["box"],
                        "reviewer": "reviewer-a",
                        "reason": "Source page mapping guard.",
                        "created_at": "2026-06-12T00:00:00Z",
                        "safety_mode": "reviewed_manual_mask",
                        "decision": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class WorkspaceStub:
        path = root

    class AdapterStub:
        adapter_id = "test"
        adapter_version = "test"
        execution_mode = "local"

        def clean(self, *, image_path, output_path, **kwargs):
            with Image.open(image_path) as source:
                source.save(output_path)
            return output_path, [], "success"

    def fake_measure(workspace, *, jobs, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1, 1), "white").save(output_path)
        is_manual = "page_0010_mask.png" in str(jobs[0]["mask_artifact"])
        return [
            {
                "page_id": "page-10",
                "page_index": 3,
                "mask_area_ratio": jobs[0].get("mask_area_ratio", 0.0),
                "large_white_block_detected": False,
                "residual_edge_ratio": 0.1 if is_manual else 0.3,
            }
        ]

    monkeypatch.setattr(
        manga_core,
        "_write_cleaning_quality_contact_sheet",
        fake_measure,
    )
    job = {
        "page_id": "page-10",
        "page_index": 3,
        "source_page": 10,
        "input_image_artifact": source_path.relative_to(root).as_posix(),
        "mask_artifact": original_mask.relative_to(root).as_posix(),
        "output_image_artifact": None,
        "region_decisions": [
            {
                "box_id": "box",
                "bbox": [5, 5, 30, 30],
                "cleaning_policy": "glyph_inpaint",
                "quality_region_type": "background_text",
            }
        ],
        "mask_area_ratio": 0.0,
    }

    reports = manga_core._apply_cleaning_escalation_ladder(
        WorkspaceStub(),
        jobs=[job],
        initial_reports=[
            {
                "page_id": "page-10",
                "page_index": 3,
                "large_white_block_detected": False,
                "residual_edge_ratio": 0.3,
            }
        ],
        adapter=AdapterStub(),
        fill_color=(255, 255, 255),
        cleaning_dir=cleaning_dir,
    )

    assert reports[0]["residual_edge_ratio"] == 0.1
    assert job["source_page"] == 10
    assert job["mask_artifact"].endswith("manual_masks/page_0010_mask.png")


def test_phase9m3_manual_mask_cannot_bypass_residual_qa(
    tmp_path: Path,
    monkeypatch,
) -> None:
    Image, _ImageOps = manga_core._load_pillow()
    root = tmp_path / "workspace"
    cleaning_dir = root / "artifacts" / "manga" / "demo" / "run" / "cleaning"
    for child in ("masks", "manual_masks", "cleaned_pages", "quality"):
        (cleaning_dir / child).mkdir(parents=True, exist_ok=True)
    source_path = cleaning_dir / "source.png"
    original_mask = cleaning_dir / "masks" / "page_0001_mask.png"
    manual_mask = cleaning_dir / "manual_masks" / "page_0001_mask.png"
    Image.new("RGB", (80, 60), "white").save(source_path)
    mask = Image.new("L", (80, 60), 0)
    for x in range(10, 15):
        for y in range(10, 15):
            mask.putpixel((x, y), 255)
    mask.save(original_mask)
    mask.save(manual_mask)
    (cleaning_dir / "manual_masks" / "manual_mask_decisions.json").write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "source_page": 1,
                        "run_page_index": 1,
                        "page_id": "p",
                        "scope": "page",
                        "reviewer": "reviewer-a",
                        "reason": "Residual QA bypass guard.",
                        "created_at": "2026-06-12T00:00:00Z",
                        "safety_mode": "reviewed_manual_mask",
                        "decision": "approved",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class WorkspaceStub:
        path = root

    class AdapterStub:
        adapter_id = "test"
        adapter_version = "test"
        execution_mode = "local"

        def clean(self, *, image_path, output_path, **kwargs):
            with Image.open(image_path) as source:
                source.save(output_path)
            return output_path, [], "success"

    def always_block(workspace, *, jobs, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1, 1), "white").save(output_path)
        return [
            {
                "page_id": "p",
                "page_index": 1,
                "large_white_block_detected": False,
                "residual_edge_ratio": 0.3,
            }
        ]

    monkeypatch.setattr(
        manga_core,
        "_write_cleaning_quality_contact_sheet",
        always_block,
    )
    job = {
        "page_id": "p",
        "page_index": 1,
        "source_page": 1,
        "input_image_artifact": source_path.relative_to(root).as_posix(),
        "mask_artifact": original_mask.relative_to(root).as_posix(),
        "output_image_artifact": None,
        "region_decisions": [
            {
                "box_id": "box",
                "bbox": [5, 5, 30, 30],
                "cleaning_policy": "glyph_inpaint",
                "quality_region_type": "background_text",
            }
        ],
        "mask_area_ratio": 0.01,
    }

    reports = manga_core._apply_cleaning_escalation_ladder(
        WorkspaceStub(),
        jobs=[job],
        initial_reports=[
            {
                "page_id": "p",
                "page_index": 1,
                "large_white_block_detected": False,
                "residual_edge_ratio": 0.3,
            }
        ],
        adapter=AdapterStub(),
        fill_color=(255, 255, 255),
        cleaning_dir=cleaning_dir,
    )

    assert job["cleaning_escalation"]["status"] == "blocked"
    assert reports[0]["residual_edge_ratio"] == 0.3


def test_phase9g_mask_creation_from_rectangles_and_polygons(tmp_path: Path) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)

    data = invoke_ok(
        [
            "manga",
            "cleaning",
            "masks",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ]
    )

    assert data["mode"] == "mask"
    assert data["mask_count"] == 1
    assert data["cleaned_page_count"] == 0
    assert data["cloud_used"] is False
    assert data["jobs"][0]["box_ids"] == ["b_rect", "b_poly"]
    assert any("sfx_leave_unchanged:b_sfx" == warning for warning in data["jobs"][0]["warnings"])
    mask_path = workspace / data["jobs"][0]["mask_artifact"]
    assert pixel(mask_path, (12, 12)) == (255, 255, 255)
    assert pixel(mask_path, (66, 20)) == (255, 255, 255)
    assert pixel(mask_path, (5, 5)) == (0, 0, 0)
    assert (workspace / data["cleaning_jobs_path"]).exists()
    assert (workspace / data["cleaning_summary_path"]).exists()


def test_phase9g_fill_cleaning_output_and_original_unchanged(tmp_path: Path) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    imported_image = imported_page_path(workspace, run_id)
    before_sha = sha256_file(imported_image)

    data = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "fill",
            "--fill-color",
            "#ffffff",
            "--json",
        ]
    )

    assert data["status"] == "success"
    assert data["cleaned_page_count"] == 1
    assert sha256_file(imported_image) == before_sha
    job = data["jobs"][0]
    output = workspace / job["output_image_artifact"]
    assert output.exists()
    assert pixel(output, (12, 12)) == (255, 255, 255)
    assert pixel(output, (5, 5)) == (40, 90, 140)
    assert job["cloud_used"] is False


def test_phase9g_opencv_adapter_reports_unavailable_without_cloud(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name == "cv2":
            raise ModuleNotFoundError("No module named 'cv2'")
        return real_import(name, package)

    monkeypatch.setattr(manga_core.importlib, "import_module", fake_import)
    data = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "opencv_inpaint",
            "--json",
        ]
    )

    assert data["status"] == "unavailable"
    assert data["cleaned_page_count"] == 0
    assert data["jobs"][0]["output_image_artifact"] is None
    assert any(warning.startswith("opencv_unavailable") for warning in data["jobs"][0]["warnings"])
    assert data["cloud_used"] is False


def test_phase9m1_glyph_mask_does_not_fill_whole_non_bubble_rectangle() -> None:
    Image, _ImageOps = manga_core._load_pillow()
    from PIL import ImageDraw

    crop = Image.new("RGB", (180, 80), "white")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((25, 20, 45, 58), fill="black")
    draw.rectangle((58, 20, 78, 58), fill="black")
    draw.rectangle((91, 20, 111, 58), fill="black")

    mask, metrics = manga_core._glyph_mask_for_crop(crop, padding=1)

    assert metrics["status"] == "pass"
    assert 0.002 < metrics["glyph_area_ratio"] < 0.35
    assert sum(mask.histogram()[1:]) < crop.width * crop.height * 0.5
    assert mask.getpixel((5, 5)) == 0


def test_phase9m1_title_and_sfx_are_preserved_by_default() -> None:
    Image, _ImageOps = manga_core._load_pillow()
    image = Image.new("RGB", (200, 300), (80, 90, 100))
    title = manga_core._classify_cleaning_region(
        image,
        {
            "stable_key": "title",
            "bbox_json": [10, 10, 180, 80],
            "box_type": "dialogue",
        },
        page_has_large_region=False,
    )
    sfx = manga_core._classify_cleaning_region(
        image,
        {
            "stable_key": "sfx",
            "bbox_json": [50, 180, 60, 40],
            "box_type": "sfx",
        },
        page_has_large_region=False,
    )

    assert title["quality_region_type"] == "title_art"
    assert title["cleaning_policy"] == "preserve"
    assert sfx["quality_region_type"] == "sfx"
    assert sfx["cleaning_policy"] == "preserve"


def test_phase9m1_quality_mode_preserves_unsafe_art_and_writes_audits(tmp_path: Path) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    imported_image = imported_page_path(workspace, run_id)

    data = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "quality_inpaint",
            "--json",
        ]
    )

    job = data["jobs"][0]
    assert data["status"] == "success"
    assert job["box_ids"] == []
    assert set(job["preserved_box_ids"]) == {"b_rect", "b_poly", "b_sfx"}
    Image, _ImageOps = manga_core._load_pillow()
    with Image.open(imported_image) as source, Image.open(
        workspace / job["output_image_artifact"]
    ) as output:
        assert source.convert("RGB").tobytes() == output.convert("RGB").tobytes()
    assert data["destructive_cleaning_blocker_count"] == 0
    for artifact_path in data["quality_artifacts"].values():
        assert (workspace / artifact_path).exists()


def test_phase9m1_quality_mode_preserves_source_when_opencv_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    imported_image = imported_page_path(workspace, run_id)
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None):
        if name == "cv2":
            raise ModuleNotFoundError("No module named 'cv2'")
        return real_import(name, package)

    monkeypatch.setattr(manga_core.importlib, "import_module", fake_import)
    data = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "quality_inpaint",
            "--json",
        ]
    )

    job = data["jobs"][0]
    assert job["status"] == "success"
    Image, _ImageOps = manga_core._load_pillow()
    with Image.open(imported_image) as source, Image.open(
        workspace / job["output_image_artifact"]
    ) as output:
        assert source.convert("RGB").tobytes() == output.convert("RGB").tobytes()
    assert "conservative_source_preserved_without_inpaint" in job["warnings"]


def test_phase9g_sfx_decision_is_recorded_and_preserved_on_rerun(tmp_path: Path) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    first = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "fill",
            "--json",
        ]
    )
    assert "b_sfx" not in first["jobs"][0]["box_ids"]

    decision = invoke_ok(
        [
            "manga",
            "cleaning",
            "set-decision",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--box-id",
            "b_sfx",
            "--mode",
            "fill",
            "--sfx-policy",
            "clean",
            "--reviewer",
            "tester",
            "--json",
        ]
    )
    assert decision["sfx_policy"] == "clean"
    assert (workspace / decision["cleaning_decisions_path"]).exists()

    rerun = invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "fill",
            "--json",
        ]
    )
    assert "b_sfx" in rerun["jobs"][0]["box_ids"]
    assert rerun["jobs"][0]["sfx_decisions"]["b_sfx"] == "clean"
    output = workspace / rerun["jobs"][0]["output_image_artifact"]
    assert pixel(output, (42, 60)) == (255, 255, 255)

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        count = conn.execute("SELECT COUNT(*) FROM manga_cleaning_decisions").fetchone()[0]
    assert count == 1


def test_phase9g_export_cleaning_summary(tmp_path: Path) -> None:
    workspace, run_id, _source = build_phase9f_workspace(tmp_path)
    invoke_ok(
        [
            "manga",
            "cleaning",
            "run",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--mode",
            "fill",
            "--json",
        ]
    )

    exported = invoke_ok(
        [
            "manga",
            "cleaning",
            "export",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ]
    )

    assert exported["job_count"] == 1
    assert exported["cleaned_page_count"] == 1
    assert (workspace / exported["cleaning_jobs_path"]).exists()
    assert (workspace / exported["cleaning_summary_path"]).exists()
