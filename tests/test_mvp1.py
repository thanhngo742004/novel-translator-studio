from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
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


def write_text_fixture(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "source.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_mvp1_migrations_create_text_and_memory_tables(tmp_path: Path) -> None:
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
        "documents",
        "chapters",
        "segments",
        "translations",
        "memory_items",
        "memory_evidence",
        "memory_audit_logs",
        "memory_conflicts",
    }.issubset(tables)
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_import_text_creates_document_chapters_segments_and_artifact(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    source = write_text_fixture(
        tmp_path,
        "Chapter 1\n\nLong thanh kiếm.\n\nMột đoạn nữa.\n\nChapter 2\n\nKết thúc.",
    )
    expected_checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    result = runner.invoke(
        app,
        [
            "import",
            "text",
            str(source),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--lang",
            "zh",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = parse_json(result.output)
    assert payload["status"] == "success"
    document = payload["data"]["document"]
    assert document["checksum_sha256"] == expected_checksum
    assert (workspace / document["artifact_path"]).exists()
    assert payload["data"]["segments_created"] >= 2

    chapters = runner.invoke(
        app,
        ["text", "chapters", "list", "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert chapters.exit_code == 0, chapters.output
    chapter_rows = parse_json(chapters.output)["data"]["chapters"]
    assert len(chapter_rows) == 2

    segments = runner.invoke(
        app,
        [
            "text",
            "segments",
            "list",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_rows[0]["id"],
            "--json",
        ],
    )
    assert segments.exit_code == 0, segments.output
    assert parse_json(segments.output)["data"]["segments"]


def test_import_text_empty_file_and_missing_project_fail_cleanly(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    empty = write_text_fixture(tmp_path, "")
    result = runner.invoke(
        app,
        [
            "import",
            "text",
            str(empty),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert result.exit_code == 4
    assert parse_json(result.output)["status"] == "error"

    source = write_text_fixture(tmp_path, "Chapter 1\n\nText")
    missing_project = runner.invoke(
        app,
        [
            "import",
            "text",
            str(source),
            "--workspace",
            str(workspace),
            "--project",
            "missing",
            "--json",
        ],
    )
    assert missing_project.exit_code == 4
    assert "Project not found" in parse_json(missing_project.output)["error"]["message"]


def create_memory(workspace: Path, *, status: str = "pending", source_key: str = "Long") -> dict:
    result = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            "term",
            "--status",
            status,
            "--project",
            "demo",
            "--source-key",
            source_key,
            "--target-text",
            f"{source_key} target",
            "--confidence-score",
            "0.8",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]["item"]


def test_memory_create_list_show_evidence_status_and_audit(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    item = create_memory(workspace)

    listed = runner.invoke(
        app, ["memory", "list", "--workspace", str(workspace), "--type", "term", "--json"]
    )
    assert listed.exit_code == 0, listed.output
    assert parse_json(listed.output)["data"]["items"][0]["id"] == item["id"]

    evidence = runner.invoke(
        app,
        [
            "memory",
            "evidence",
            "add",
            item["id"],
            "--workspace",
            str(workspace),
            "--source-kind",
            "manual",
            "--artifact-ref",
            "note.txt",
            "--excerpt-json",
            '{"source":"Long","target":"Long target"}',
            "--quality-score",
            "0.9",
            "--json",
        ],
    )
    assert evidence.exit_code == 0, evidence.output

    status = runner.invoke(
        app,
        [
            "memory",
            "status",
            "set",
            item["id"],
            "--workspace",
            str(workspace),
            "--status",
            "active",
            "--json",
        ],
    )
    assert status.exit_code == 0, status.output
    assert parse_json(status.output)["data"]["item"]["status"] == "active"

    shown = runner.invoke(app, ["memory", "show", item["id"], "--workspace", str(workspace), "--json"])
    assert shown.exit_code == 0, shown.output
    shown_data = parse_json(shown.output)["data"]
    assert shown_data["evidence"][0]["source_kind"] == "manual"
    assert [log["action"] for log in shown_data["audit_logs"]] == ["create", "status.set"]


def test_invalid_memory_type_and_status_fail_cleanly(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    bad_type = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            "bad",
            "--scope-json",
            "{}",
            "--json",
        ],
    )
    assert bad_type.exit_code == 4
    assert parse_json(bad_type.output)["status"] == "error"

    item = create_memory(workspace)
    bad_status = runner.invoke(
        app,
        [
            "memory",
            "status",
            "set",
            item["id"],
            "--workspace",
            str(workspace),
            "--status",
            "bad",
            "--json",
        ],
    )
    assert bad_status.exit_code == 4
    assert parse_json(bad_status.output)["status"] == "error"


def test_retrieval_bundle_filters_orders_limits_and_checksums(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    low = create_memory(workspace, status="active", source_key="Long")
    high_result = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            "name",
            "--status",
            "active",
            "--project",
            "demo",
            "--source-key",
            "Kiếm",
            "--target-text",
            "Kiem target",
            "--confidence-score",
            "0.95",
            "--json",
        ],
    )
    assert high_result.exit_code == 0, high_result.output
    draft = create_memory(workspace, status="draft", source_key="Ẩn")

    first = runner.invoke(
        app,
        [
            "memory",
            "bundle",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--text",
            "Long cầm Kiếm. Ẩn không nên vào bundle.",
            "--top-k",
            "1",
            "--json",
        ],
    )
    second = runner.invoke(
        app,
        [
            "memory",
            "bundle",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--text",
            "Long cầm Kiếm. Ẩn không nên vào bundle.",
            "--top-k",
            "1",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_bundle = parse_json(first.output)["data"]
    second_bundle = parse_json(second.output)["data"]
    assert first_bundle["checksum"] == second_bundle["checksum"]
    assert first_bundle["items"]["names"][0]["confidence_score"] == 0.95
    bundle_ids = json.dumps(first_bundle["items"], ensure_ascii=False)
    assert low["id"] not in bundle_ids
    assert draft["id"] not in bundle_ids


def test_mock_translate_creates_rows_artifact_task_and_model_runs(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    source = write_text_fixture(tmp_path, "Chapter 1\n\nLong cầm kiếm.\n\nĐoạn hai.")
    imported = runner.invoke(
        app,
        [
            "import",
            "text",
            str(source),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert imported.exit_code == 0, imported.output
    chapter_id = parse_json(imported.output)["data"]["chapters"][0]["id"]
    create_memory(workspace, status="active", source_key="Long")

    chapter_bundle = runner.invoke(
        app,
        ["memory", "bundle", "--workspace", str(workspace), "--chapter", chapter_id, "--json"],
    )
    assert chapter_bundle.exit_code == 0, chapter_bundle.output
    assert parse_json(chapter_bundle.output)["data"]["items"]["terms"]

    translated = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--json",
        ],
    )

    assert translated.exit_code == 0, translated.output
    payload = parse_json(translated.output)
    assert payload["status"] == "success"
    output_path = workspace / payload["data"]["output_path"]
    assert output_path.exists()
    assert "[mock-vi:" in output_path.read_text(encoding="utf-8")

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        translations = conn.execute("SELECT bundle_checksum FROM translations").fetchall()
        model_run_count = conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        task_run_count = conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_type = 'translate.text'"
        ).fetchone()[0]

    assert translations
    assert all(row[0].startswith("sha256:") for row in translations)
    assert model_run_count == len(translations)
    assert task_run_count == 1
