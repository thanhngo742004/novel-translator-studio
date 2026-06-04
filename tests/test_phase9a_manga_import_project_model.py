from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import struct
import zipfile
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


def read_manifest(workspace: Path, data: dict) -> dict:
    manifest_path = workspace / data["page_manifest_path"]
    assert manifest_path.exists()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def test_phase9a_image_folder_import_writes_required_manifest_and_artifacts(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    images = tmp_path / "images"
    images.mkdir()
    (images / "002.png").write_bytes(png_bytes(4, 5, (80, 90, 100)))
    (images / "001.png").write_bytes(png_bytes(2, 3, (10, 20, 30)))
    (images / "notes.txt").write_text("unsupported", encoding="utf-8")

    data = import_source(workspace, images)
    manifest = read_manifest(workspace, data)

    assert data["manifest_schema_version"] == "phase9a.page_manifest.v1"
    assert data["hash_algorithm"] == "sha256"
    assert data["source_type"] == "folder"
    assert data["source_label"] == "images"
    assert data["pdf_import_status"] == "BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED"
    assert data["artifact_root"] == f"artifacts/manga/demo/{data['run_id']}"
    assert data["page_manifest_path"] == f"{data['artifact_root']}/page_manifest.json"
    assert data["import_summary_path"] == f"{data['artifact_root']}/import/import_summary.md"
    assert data["import_warnings_path"] == f"{data['artifact_root']}/import/import_warnings.json"

    assert manifest["schema_version"] == "phase9a.page_manifest.v1"
    assert manifest["project_id"] == data["project_id"]
    assert manifest["project_slug"] == "demo"
    assert manifest["run_id"] == data["run_id"]
    assert manifest["source_type"] == "folder"
    assert manifest["source_label"] == "images"
    assert manifest["page_count"] == 2
    assert manifest["hash_algorithm"] == "sha256"
    assert manifest["warnings"] == ["unsupported_file_ignored:notes.txt"]

    pages = manifest["pages"]
    assert [page["display_name"] for page in pages] == ["001.png", "002.png"]
    assert pages[0]["page_index"] == 1
    assert pages[0]["width"] == 2
    assert pages[0]["height"] == 3
    assert pages[0]["format"] == "png"
    assert pages[0]["source_relpath"] == "001.png"
    assert pages[0]["artifact_relpath"].startswith(f"{data['artifact_root']}/import/pages/")
    assert pages[0]["excluded"] is False
    assert pages[0]["exclude_reason"] is None
    assert len(pages[0]["image_hash"]) == 64

    for required_dir in [
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
    ]:
        assert (workspace / data["artifact_root"] / required_dir).is_dir()
    assert (workspace / data["import_summary_path"]).exists()
    warnings_payload = json.loads((workspace / data["import_warnings_path"]).read_text(encoding="utf-8"))
    assert warnings_payload["warnings"] == ["unsupported_file_ignored:notes.txt"]

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        conn.row_factory = sqlite3.Row
        manga_project = conn.execute("SELECT * FROM manga_projects WHERE project_slug = 'demo'").fetchone()
        import_run = conn.execute("SELECT * FROM manga_import_runs WHERE run_id = ?", (data["run_id"],)).fetchone()
    assert manga_project is not None
    assert manga_project["project_id"] == data["project_id"]
    assert import_run is not None
    assert import_run["manifest_path"] == data["page_manifest_path"]
    assert import_run["page_count"] == 2


def test_phase9a_single_image_import_for_canary(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "canary.png"
    image.write_bytes(png_bytes(7, 8))

    data = import_source(workspace, image)
    manifest = read_manifest(workspace, data)

    assert data["source_type"] == "single_image"
    assert data["pages_imported"] == 1
    assert manifest["page_count"] == 1
    assert manifest["pages"][0]["display_name"] == "canary.png"
    assert manifest["pages"][0]["width"] == 7
    assert manifest["pages"][0]["height"] == 8


def test_phase9a_zip_import_has_deterministic_page_order(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    archive_path = tmp_path / "chapter.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("b/002.png", png_bytes(2, 2, (2, 2, 2)))
        archive.writestr("a/001.png", png_bytes(1, 1, (1, 1, 1)))
        archive.writestr("z/readme.txt", b"ignored")

    first = import_source(workspace, archive_path)
    second = import_source(workspace, archive_path)
    first_manifest = read_manifest(workspace, first)
    second_manifest = read_manifest(workspace, second)

    assert first["source_type"] == "zip"
    assert [page["source_relpath"] for page in first_manifest["pages"]] == ["a/001.png", "b/002.png"]
    assert [page["page_index"] for page in first_manifest["pages"]] == [1, 2]
    assert first_manifest["warnings"] == ["unsupported_file_ignored:z/readme.txt"]
    assert [page["page_id"] for page in first_manifest["pages"]] == [
        page["page_id"] for page in second_manifest["pages"]
    ]

    listed = runner.invoke(
        app,
        ["--workspace", str(workspace), "manga", "pages", "list", "--project", "demo", "--json"],
    )
    assert listed.exit_code == 0, listed.output
    assert len(parse_json(listed.output)["data"]["pages"]) == 2


def test_phase9a_cbz_import_and_duplicate_hash_warning(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    cbz_path = tmp_path / "chapter.cbz"
    duplicate = png_bytes(3, 3, (9, 9, 9))
    with zipfile.ZipFile(cbz_path, "w") as archive:
        archive.writestr("001.png", duplicate)
        archive.writestr("002.png", duplicate)

    data = import_source(workspace, cbz_path)
    manifest = read_manifest(workspace, data)

    assert data["source_type"] == "cbz"
    assert manifest["page_count"] == 2
    assert len(manifest["pages"]) == 2
    assert manifest["pages"][0]["image_hash"] == manifest["pages"][1]["image_hash"]
    assert any(warning.startswith("duplicate_page_hash:") for warning in manifest["warnings"])
    assert (workspace / manifest["pages"][0]["artifact_relpath"]).exists()
    assert (workspace / manifest["pages"][1]["artifact_relpath"]).exists()


def test_phase9a_empty_source_and_pdf_adapter_fail_cleanly(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    empty_result = runner.invoke(
        app,
        ["manga", "import", str(empty), "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert empty_result.exit_code == 4
    assert "No supported manga image files found" in parse_json(empty_result.output)["error"]["message"]

    pdf_result = runner.invoke(
        app,
        ["manga", "import", str(pdf), "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert pdf_result.exit_code == 4
    assert (
        "BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED"
        in parse_json(pdf_result.output)["error"]["message"]
    )
