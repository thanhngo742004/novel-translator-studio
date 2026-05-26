from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def init_project(tmp_path: Path) -> tuple[Path, dict]:
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
            "Demo",
            "--source-lang",
            "zh",
            "--target-lang",
            "vi",
            "--domain",
            "novel",
            "--json",
        ],
    )
    assert project.exit_code == 0, project.output
    return workspace, parse_json(project.output)["data"]


def write_image(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def import_one_page(workspace: Path, tmp_path: Path) -> dict:
    images = tmp_path / "images"
    images.mkdir()
    write_image(images / "001.png", b"png-one")
    result = runner.invoke(
        app,
        ["manga", "import", str(images), "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def test_manga_migration_creates_required_tables(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert {
        "manga_pages",
        "manga_page_artifacts",
        "manga_boxes",
        "manga_box_versions",
        "manga_ocr_results",
        "manga_box_translations",
        "manga_exports",
        "manga_visual_evidence",
    }.issubset(tables)
    assert versions == [1, 2, 3, 4, 5, 6, 7]


def test_import_image_folder_registers_pages_checksum_and_warns_for_unsupported(
    tmp_path: Path,
) -> None:
    workspace, _project = init_project(tmp_path)
    images = tmp_path / "pages"
    images.mkdir()
    first = write_image(images / "001.png", b"fake-png-1")
    second = write_image(images / "002.jpg", b"fake-jpg-2")
    (images / "notes.txt").write_text("ignore me", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "manga",
            "import",
            str(images),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["pages_imported"] == 2
    assert data["warnings"] == ["unsupported_file_ignored:notes.txt"]
    assert data["pages"][0]["checksum_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert data["pages"][1]["checksum_sha256"] == hashlib.sha256(second.read_bytes()).hexdigest()
    for page in data["pages"]:
        assert (workspace / page["image_path"]).exists()
        assert page["width"] is None
        assert page["height"] is None

    listed = runner.invoke(
        app,
        ["--workspace", str(workspace), "manga", "pages", "list", "--project", "demo", "--json"],
    )
    assert listed.exit_code == 0, listed.output
    assert len(parse_json(listed.output)["data"]["pages"]) == 2


def test_cbz_import_works_with_zipfile(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    cbz = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.writestr("002.jpg", b"jpg-two")
        archive.writestr("001.png", b"png-one")
        archive.writestr("readme.txt", b"ignored")

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "manga",
            "import",
            str(cbz),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["source_kind"] == "cbz"
    assert data["pages_imported"] == 2
    assert "unsupported_file_ignored:readme.txt" in data["warnings"]
    assert data["pages"][0]["page_index"] == 1


def test_manga_import_empty_folder_and_missing_project_fail_cleanly(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()

    empty_result = runner.invoke(
        app,
        [
            "manga",
            "import",
            str(empty),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert empty_result.exit_code == 4
    assert parse_json(empty_result.output)["status"] == "error"

    images = tmp_path / "images"
    images.mkdir()
    write_image(images / "001.webp", b"webp")
    missing_project = runner.invoke(
        app,
        [
            "manga",
            "import",
            str(images),
            "--workspace",
            str(workspace),
            "--project",
            "missing",
            "--json",
        ],
    )
    assert missing_project.exit_code == 4
    assert "Project not found" in parse_json(missing_project.output)["error"]["message"]


def test_boxes_import_export_roundtrip_and_reimport_creates_new_version(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    import_one_page(workspace, tmp_path)
    boxes_path = tmp_path / "boxes.json"
    boxes_payload = {
        "pages": [
            {
                "page_index": 1,
                "boxes": [
                    {
                        "box_id": "box_001",
                        "bbox": [120, 240, 330, 180],
                        "polygon": None,
                        "box_type": "speech",
                        "reading_order": 1,
                        "speaker_id": None,
                    }
                ],
            }
        ]
    }
    boxes_path.write_text(json.dumps(boxes_payload), encoding="utf-8")

    first_import = runner.invoke(
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
    assert first_import.exit_code == 0, first_import.output
    first_data = parse_json(first_import.output)["data"]
    assert first_data["boxes_created"] == 1
    assert first_data["versions_created"] == 1
    assert first_data["boxes"][0]["revision_no"] == 1

    exported = runner.invoke(
        app,
        ["manga", "boxes", "export", "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert exported.exit_code == 0, exported.output
    exported_payload = parse_json(exported.output)["data"]["boxes_json"]
    assert exported_payload == boxes_payload

    boxes_payload["pages"][0]["boxes"][0]["bbox"] = [121, 240, 330, 180]
    boxes_path.write_text(json.dumps(boxes_payload), encoding="utf-8")
    second_import = runner.invoke(
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
    assert second_import.exit_code == 0, second_import.output
    second_data = parse_json(second_import.output)["data"]
    assert second_data["boxes_created"] == 0
    assert second_data["versions_created"] == 1
    assert second_data["boxes"][0]["revision_no"] == 2

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        version_count = conn.execute("SELECT COUNT(*) FROM manga_box_versions").fetchone()[0]
    assert version_count == 2

    reexported = runner.invoke(
        app,
        ["manga", "boxes", "export", "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert parse_json(reexported.output)["data"]["boxes_json"] == boxes_payload


def test_manga_manifest_export_creates_manifest_and_export_row(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    import_one_page(workspace, tmp_path)
    boxes_path = tmp_path / "boxes.json"
    boxes_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_index": 1,
                        "boxes": [
                            {
                                "box_id": "box_001",
                                "bbox": [1, 2, 3, 4],
                                "polygon": None,
                                "box_type": "speech",
                                "reading_order": 1,
                                "speaker_id": "char_a",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runner.invoke(
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

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "manga",
            "manifest",
            "export",
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    manifest_path = workspace / data["manifest_path"]
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == data["manifest"]
    page = manifest["pages"][0]
    assert page["page_id"]
    assert page["page_index"] == 1
    assert page["image_path"].startswith("artifacts/manga/")
    box = page["boxes"][0]
    assert box["box_id"] == "box_001"
    assert box["ocr_text"] is None
    assert box["translation_text"] is None

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        export_count = conn.execute("SELECT COUNT(*) FROM manga_exports").fetchone()[0]
    assert export_count == 1
