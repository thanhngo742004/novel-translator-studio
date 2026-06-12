from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

import nts_core.manga as manga_core
from nts_core.projects import create_project
from nts_storage.workspace import init_workspace


pytestmark = pytest.mark.skipif(
    os.environ.get("NTS_REAL_MANGA_GATE") != "1",
    reason="set NTS_REAL_MANGA_GATE=1 to run the real local manga residual gate",
)

SOURCE_PAGES = (8, 9, 10)
RUN_TO_SOURCE_PAGE = {1: 8, 2: 9, 3: 10}
RESIDUAL_EDGE_RATIO_LIMIT = 0.18
PAGE_MASK_AREA_RATIO_LIMIT = 0.12


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_phase9m3_real_residual_gate_pages_8_10(tmp_path: Path) -> None:
    cbz_path = (
        _repo_root()
        / "test_data"
        / "translation_eval"
        / "han_jue"
        / "OnePiece001.cbz"
    )
    if not cbz_path.exists():
        pytest.skip(f"real manga fixture missing: {cbz_path}")
    if importlib.util.find_spec("paddleocr") is None:
        pytest.skip("PaddleOCR is unavailable")

    workspace = init_workspace(tmp_path / "workspace")
    project_slug = "phase9m3-real-residual"
    create_project(
        workspace,
        slug=project_slug,
        name="Phase 9M.3 Real Residual Gate",
        source_lang="zh",
        target_lang="vi",
        domain="manga",
        genre=None,
    )

    imported = manga_core.import_manga_pages(
        workspace,
        path=cbz_path,
        project_slug=project_slug,
        page_start=SOURCE_PAGES[0],
        page_limit=len(SOURCE_PAGES),
    )
    run_id = str(imported["run_id"])
    manifest_path = workspace.path / str(imported["page_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_page_mapping = {
        int(page["page_index"]): SOURCE_PAGES[0] + int(page["page_index"]) - 1
        for page in manifest["pages"]
    }
    assert source_page_mapping == RUN_TO_SOURCE_PAGE
    assert manifest["page_start"] == SOURCE_PAGES[0]
    assert manifest["page_limit"] == len(SOURCE_PAGES)

    manga_core.preprocess_manga_pages(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
    )
    detection = manga_core.run_manga_detection(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        adapter_id="paddleocr_text_detector",
    )
    ocr = manga_core.run_manga_ocr(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        adapter_id="paddleocr",
        language="ch",
        no_network=True,
        disable_onednn=True,
        disable_paddlex_mkldnn=True,
    )
    initial_cleaning = manga_core.run_manga_cleaning(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        mode="quality_inpaint",
        sfx_policy="leave_unchanged",
    )
    initial_jobs = {
        int(job["page_index"]): job for job in initial_cleaning["jobs"]
    }
    page_10_job = initial_jobs[3]
    diagnostics = initial_cleaning["residual_diagnostics"]
    assert diagnostics
    source_page_10_diagnostic = next(
        row for row in diagnostics if int(row["source_page"]) == 10
    )
    diagnostic_json_path = workspace.path / str(source_page_10_diagnostic["json"])
    assert diagnostic_json_path.exists()
    assert (
        workspace.path / str(source_page_10_diagnostic["markdown"])
    ).exists()
    diagnostic_payload = json.loads(
        diagnostic_json_path.read_text(encoding="utf-8")
    )
    failing_box_ids = [
        str(row["box_id"])
        for row in diagnostic_payload["boxes"]
        if row["primary_residual_contributor"]
    ]
    assert len(failing_box_ids) == 3
    assert (
        diagnostic_payload["candidate_manual_mask"]["status"]
        == "candidate_for_review"
    )
    assert diagnostic_payload["candidate_manual_mask"]["auto_approved"] is False

    reviewed_mask_fixture = (
        _repo_root()
        / "test_data"
        / "translation_eval"
        / "han_jue"
        / "phase9m3_manual_masks"
        / "page_0010_mask.png"
    )
    if not reviewed_mask_fixture.exists():
        pytest.fail(
            "Reviewed source-page-10 manual mask missing. Review the generated "
            f"candidate at {source_page_10_diagnostic['candidate_manual_mask']}."
        )
    cleaning_dir = (
        workspace.path
        / "artifacts"
        / "manga"
        / project_slug
        / run_id
        / "cleaning"
    )
    manual_masks_dir = cleaning_dir / "manual_masks"
    manual_masks_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        reviewed_mask_fixture,
        manual_masks_dir / "page_0010_mask.png",
    )
    (manual_masks_dir / "manual_mask_decisions.json").write_text(
        json.dumps(
            {
                "schema_version": "phase9m3.manual_mask_decisions.v1",
                "decisions": [
                    {
                        "source_page": 10,
                        "run_page_index": 3,
                        "page_id": str(page_10_job["page_id"]),
                        "scope": "page",
                        "box_ids": failing_box_ids,
                        "reviewer": "phase9m3-local-review",
                        "reason": (
                            "Reviewed anti-aliased glyph halo refinement for "
                            "source page 10 residual dialogue boxes."
                        ),
                        "created_at": "2026-06-12T00:00:00+07:00",
                        "safety_mode": "reviewed_manual_mask",
                        "decision": "approved",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    cleaning = manga_core.run_manga_cleaning(
        workspace,
        project_slug=project_slug,
        run_id=run_id,
        mode="quality_inpaint",
        sfx_policy="leave_unchanged",
    )
    mapping_path = (
        workspace.path
        / "artifacts"
        / "manga"
        / project_slug
        / run_id
        / "cleaning"
        / "quality"
        / "source_page_mapping.json"
    )
    mapping_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "source_cbz": str(cbz_path),
                "run_to_source_page": {
                    str(run_page): source_page
                    for run_page, source_page in source_page_mapping.items()
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    detection_regions = json.loads(
        (workspace.path / str(detection["regions_path"])).read_text(encoding="utf-8")
    )
    ocr_results = json.loads(
        (workspace.path / str(ocr["ocr_results_path"])).read_text(encoding="utf-8")
    )
    assert detection["adapter_id"] == "paddleocr_text_detector"
    assert detection["execution_mode"] == "local"
    assert detection["cloud_used"] is False
    assert detection_regions["adapter"]["adapter_id"] == "paddleocr_text_detector"
    assert detection_regions["adapter"]["execution_mode"] == "local"
    assert "mock" not in detection_regions["adapter"]["adapter_id"]
    assert detection_regions["regions"]
    assert ocr["adapter_id"] == "paddleocr"
    assert ocr["cloud_used"] is False
    assert ocr_results["adapter"]["adapter_id"] == "paddleocr"
    assert ocr_results["adapter"]["execution_mode"] == "local"
    assert "mock" not in ocr_results["adapter"]["adapter_id"]
    assert ocr_results["results"]
    assert all(result["adapter_id"] == "paddleocr" for result in ocr_results["results"])
    recorded_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert recorded_mapping["run_to_source_page"] == {
        str(run_page): source_page
        for run_page, source_page in RUN_TO_SOURCE_PAGE.items()
    }

    quality_artifacts = cleaning["quality_artifacts"]
    assert quality_artifacts
    visual_diff = json.loads(
        (workspace.path / str(quality_artifacts["visual_diff_report"])).read_text(
            encoding="utf-8"
        )
    )
    cleaning_jobs = json.loads(
        (workspace.path / str(cleaning["cleaning_jobs_path"])).read_text(
            encoding="utf-8"
        )
    )
    reports_by_run_page = {
        int(page["page_index"]): page for page in visual_diff["pages"]
    }
    jobs_by_run_page = {
        int(job["page_index"]): job for job in cleaning_jobs["jobs"]
    }
    assert set(reports_by_run_page) == set(RUN_TO_SOURCE_PAGE)
    assert set(jobs_by_run_page) == set(RUN_TO_SOURCE_PAGE)

    for run_page, source_page in RUN_TO_SOURCE_PAGE.items():
        report = reports_by_run_page[run_page]
        job = jobs_by_run_page[run_page]
        assert source_page_mapping[run_page] == source_page
        assert report["residual_edge_ratio"] < RESIDUAL_EDGE_RATIO_LIMIT, (
            f"source page {source_page}: residual_edge_ratio="
            f"{report['residual_edge_ratio']}"
        )
        assert report["large_white_block_detected"] is False, (
            f"source page {source_page}: large white block detected"
        )
        assert job["mask_area_ratio"] <= PAGE_MASK_AREA_RATIO_LIMIT, (
            f"source page {source_page}: mask_area_ratio={job['mask_area_ratio']}"
        )
    page_10_escalation = jobs_by_run_page[3]["cleaning_escalation"]
    assert page_10_escalation["status"] == "pass"
    assert page_10_escalation["attempts"][-1]["rung"] == "manual_mask"
    assert page_10_escalation["attempts"][-1]["status"] == "accepted"
