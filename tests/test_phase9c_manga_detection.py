from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import struct
import zlib
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def png_bytes(width: int, height: int, rgb: tuple[int, int, int] = (16, 32, 48)) -> bytes:
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


def import_source(workspace: Path, source: Path) -> dict:
    result = runner.invoke(
        app,
        ["manga", "import", str(source), "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def preprocess_run(workspace: Path, run_id: str) -> dict:
    result = runner.invoke(
        app,
        [
            "manga",
            "preprocess",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def detect_run(workspace: Path, run_id: str, *extra: str) -> dict:
    result = runner.invoke(
        app,
        [
            "manga",
            "detect",
            run_id,
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def import_preprocess_detect(tmp_path: Path) -> tuple[Path, dict, dict, dict]:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes(100, 80))
    imported = import_source(workspace, image)
    preprocessed = preprocess_run(workspace, imported["run_id"])
    detected = detect_run(workspace, imported["run_id"])
    return workspace, imported, preprocessed, detected


def test_phase9c_mock_detector_outputs_stable_boxes_and_artifacts(tmp_path: Path) -> None:
    workspace, imported, _preprocessed, detected = import_preprocess_detect(tmp_path)

    assert detected["adapter_id"] == "mock_local_detector"
    assert detected["execution_mode"] == "local"
    assert detected["cloud_used"] is False
    assert detected["regions_detected"] == 2
    assert detected["bubble_regions"] == 1
    assert detected["adapter_boxes_inserted"] == 2
    assert detected["confidence_summary"]["count"] == 2
    assert detected["confidence_summary"]["low_confidence_count"] == 1

    regions_path = workspace / detected["regions_path"]
    bubbles_path = workspace / detected["bubbles_path"]
    merged_path = workspace / detected["boxes_merged_path"]
    summary_path = workspace / detected["detection_summary_path"]
    for path in [regions_path, bubbles_path, merged_path, summary_path]:
        assert path.exists()

    regions_payload = json.loads(regions_path.read_text(encoding="utf-8"))
    assert regions_payload["schema_version"] == "phase9c.detection_manifest.v1"
    assert regions_payload["adapter"]["cloud_used"] is False
    regions = regions_payload["regions"]
    assert {region["region_type"] for region in regions} == {"dialogue", "sfx"}
    for region in regions:
        assert region["page_id"]
        assert region["box_id"].startswith("mangabox_")
        assert len(region["bbox"]) == 4
        assert 0 <= region["confidence"] <= 1
        assert region["source"] == "local_adapter"
        assert region["adapter_id"] == "mock_local_detector"
        assert region["review_state"] == "needs_review"

    bubbles_payload = json.loads(bubbles_path.read_text(encoding="utf-8"))
    assert bubbles_payload["bubbles_available"] is True
    assert len(bubbles_payload["bubbles"]) == 1
    assert bubbles_payload["bubbles"][0]["region_type"] == "dialogue"

    merged_payload = json.loads(merged_path.read_text(encoding="utf-8"))
    assert merged_payload["manual_preserved"] is True
    assert merged_payload["box_count"] == 2
    assert merged_payload["adapter_boxes_inserted"] == 2

    second = detect_run(workspace, imported["run_id"])
    second_regions = json.loads((workspace / second["regions_path"]).read_text(encoding="utf-8"))["regions"]
    assert [region["box_id"] for region in second_regions] == [region["box_id"] for region in regions]
    assert second["adapter_boxes_inserted"] == 0
    assert second["adapter_boxes_skipped_existing"] == 2

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        detection_runs = conn.execute("SELECT COUNT(*) FROM manga_detection_runs").fetchone()[0]
        box_count = conn.execute("SELECT COUNT(*) FROM manga_boxes").fetchone()[0]
    assert detection_runs == 2
    assert box_count == 2


def test_phase9c_manual_boxes_are_preserved_and_listable(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes(120, 90))
    imported = import_source(workspace, image)
    preprocess_run(workspace, imported["run_id"])
    boxes_path = tmp_path / "boxes.json"
    boxes_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 1,
                        "boxes": [
                            {
                                "box_id": "manual_dialogue_001",
                                "bbox": [10, 12, 30, 20],
                                "polygon": None,
                                "box_type": "dialogue",
                                "reading_order": 1,
                                "speaker_id": None,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    imported_boxes = runner.invoke(
        app,
        [
            "manga",
            "boxes",
            "import",
            str(boxes_path),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert imported_boxes.exit_code == 0, imported_boxes.output

    detected = detect_run(workspace, imported["run_id"])
    merged = json.loads((workspace / detected["boxes_merged_path"]).read_text(encoding="utf-8"))
    merged_boxes = merged["pages"][0]["boxes"]
    manual = [box for box in merged_boxes if box["box_id"] == "manual_dialogue_001"]
    assert len(manual) == 1
    assert manual[0]["source"] == "manual"
    assert manual[0]["bbox"] == [10, 12, 30, 20]
    assert detected["adapter_boxes_inserted"] == 2
    assert merged["box_count"] == 3

    listed = runner.invoke(
        app,
        [
            "manga",
            "boxes",
            "list",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--page-index",
            "1",
            "--json",
        ],
    )
    assert listed.exit_code == 0, listed.output
    listed_boxes = parse_json(listed.output)["data"]["boxes"]
    assert {box["box_id"] for box in listed_boxes} >= {"manual_dialogue_001"}
    assert len(listed_boxes) == 3


def test_phase9c_invalid_box_coordinates_are_rejected(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes(40, 40))
    import_source(workspace, image)
    boxes_path = tmp_path / "bad_boxes.json"
    boxes_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 1,
                        "boxes": [
                            {
                                "box_id": "bad",
                                "bbox": [-1, 0, 10, 10],
                                "box_type": "dialogue",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "manga",
            "boxes",
            "import",
            str(boxes_path),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert "bbox must have non-negative origin and positive size" in parse_json(result.output)["error"]["message"]


def test_phase9c_detection_requires_preprocess_manifest(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes(40, 40))
    imported = import_source(workspace, image)

    result = runner.invoke(
        app,
        [
            "manga",
            "detect",
            imported["run_id"],
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert "BLOCKED_PREPROCESS_MISSING" in parse_json(result.output)["error"]["message"]


def test_phase9c_unsupported_adapter_fails_without_cloud_call(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes(40, 40))
    imported = import_source(workspace, image)
    preprocess_run(workspace, imported["run_id"])

    result = runner.invoke(
        app,
        [
            "manga",
            "detect",
            imported["run_id"],
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--adapter",
            "cloud_unconfigured",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert "Unsupported detection adapter" in parse_json(result.output)["error"]["message"]
