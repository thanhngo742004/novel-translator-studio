from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import struct
import time
import zlib
from pathlib import Path

from PIL import Image
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


def preprocess_run(workspace: Path, run_id: str, *extra: str) -> dict:
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
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def test_phase9b_preprocess_creates_manifest_variants_and_preserves_original(
    tmp_path: Path,
) -> None:
    workspace = init_workspace_project(tmp_path)
    images = tmp_path / "images"
    images.mkdir()
    source = images / "001.png"
    source.write_bytes(png_bytes(5, 7, (20, 140, 230)))

    import_data = import_source(workspace, images)
    page_manifest = json.loads((workspace / import_data["page_manifest_path"]).read_text("utf-8"))
    source_artifact = workspace / page_manifest["pages"][0]["artifact_relpath"]
    original_bytes = source_artifact.read_bytes()

    data = preprocess_run(workspace, import_data["run_id"])
    manifest_path = workspace / data["preprocess_manifest_path"]
    summary_path = workspace / data["preprocess_summary_path"]
    manifest = json.loads(manifest_path.read_text("utf-8"))

    assert summary_path.exists()
    assert manifest["schema_version"] == "phase9b.preprocess_manifest.v1"
    assert manifest["source_manifest"] == import_data["page_manifest_path"]
    assert manifest["page_count"] == 1
    assert manifest["format_policy"]["normalized_format"] == "png"
    assert manifest["format_policy"]["ocr_variants"] == ["grayscale", "threshold"]
    assert manifest["size_policy"]["max_dimension"] == 2400
    assert manifest["size_policy"]["upscale"] is False

    page = manifest["pages"][0]
    assert page["page_id"] == page_manifest["pages"][0]["page_id"]
    assert page["source_artifact"] == page_manifest["pages"][0]["artifact_relpath"]
    assert page["width"] == 5
    assert page["height"] == 7
    assert page["format"] == "png"
    assert page["orientation_applied"] is False
    assert page["warnings"] == []

    normalized = workspace / page["normalized_artifact"]
    grayscale = workspace / page["ocr_variant_artifacts"]["grayscale"]
    threshold = workspace / page["ocr_variant_artifacts"]["threshold"]
    preview = workspace / page["preview_artifact"]
    for artifact in [normalized, grayscale, threshold, preview]:
        assert artifact.exists()
        assert artifact.is_file()
        assert str(artifact).endswith(".png")

    with Image.open(normalized) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (5, 7)
    with Image.open(grayscale) as image:
        assert image.mode == "L"
        assert image.size == (5, 7)
    with Image.open(threshold) as image:
        assert image.mode == "L"
        assert set(image.tobytes()).issubset({0, 255})
    with Image.open(preview) as image:
        assert max(image.size) <= 320

    assert source_artifact.read_bytes() == original_bytes
    assert data["pages_processed"] == 1
    assert data["rerun_reused_existing"] is False

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        preprocess_count = conn.execute("SELECT COUNT(*) FROM manga_preprocess_runs").fetchone()[0]
        artifact_count = conn.execute(
            "SELECT COUNT(*) FROM manga_page_artifacts WHERE artifact_kind LIKE 'preprocess.%'"
        ).fetchone()[0]
    assert preprocess_count == 1
    assert artifact_count == 4


def test_phase9b_rerun_preserves_artifacts_without_force_and_force_rewrites(
    tmp_path: Path,
) -> None:
    workspace = init_workspace_project(tmp_path)
    image = tmp_path / "canary.png"
    image.write_bytes(png_bytes(3, 4))
    import_data = import_source(workspace, image)
    first = preprocess_run(workspace, import_data["run_id"])
    normalized = workspace / first["manifest"]["pages"][0]["normalized_artifact"]
    first_mtime = normalized.stat().st_mtime_ns

    reused = preprocess_run(workspace, import_data["run_id"])
    assert reused["rerun_reused_existing"] is True
    assert normalized.stat().st_mtime_ns == first_mtime

    time.sleep(0.01)
    forced = preprocess_run(workspace, import_data["run_id"], "--force")
    assert forced["rerun_reused_existing"] is False
    assert forced["force"] is True
    assert normalized.stat().st_mtime_ns >= first_mtime


def test_phase9b_exif_orientation_is_applied_for_synthetic_jpeg(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    image_path = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (2, 5), color=(120, 30, 200))
    exif = Image.Exif()
    exif[274] = 6
    image.save(image_path, format="JPEG", exif=exif)

    import_data = import_source(workspace, image_path)
    data = preprocess_run(workspace, import_data["run_id"])
    page = data["manifest"]["pages"][0]

    assert page["orientation_applied"] is True
    assert page["width"] == 5
    assert page["height"] == 2
    with Image.open(workspace / page["normalized_artifact"]) as normalized:
        assert normalized.size == (5, 2)


def test_phase9b_missing_manifest_fails_cleanly(tmp_path: Path) -> None:
    workspace = init_workspace_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "manga",
            "preprocess",
            "missing_run",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert "BLOCKED_MANIFEST_INCOMPLETE" in parse_json(result.output)["error"]["message"]
