from __future__ import annotations

from contextlib import closing
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


def write_file(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_learn_correction_from_parallel_files_creates_pending_memory_evidence_audit_report(
    tmp_path: Path,
) -> None:
    workspace, _project = init_project(tmp_path)
    raw = write_file(tmp_path / "raw.txt", "Long cầm kiếm.\n\nHắn đi.")
    ai = write_file(tmp_path / "ai.txt", "Long holds sword.\n\nHắn đi.")
    human = write_file(tmp_path / "human.txt", "Long cầm thanh kiếm.\n\nHắn đi.")

    result = runner.invoke(
        app,
        [
            "learn",
            "correction",
            "--workspace",
            str(workspace),
            "--raw",
            str(raw),
            "--ai",
            str(ai),
            "--human",
            str(human),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = parse_json(result.output)
    data = payload["data"]
    assert payload["status"] == "success"
    assert data["total_records"] == 2
    assert data["corrections_created"] == 1
    assert data["skipped_records"] == 1
    assert data["memory_ids"]
    report_path = workspace / data["report_path"]
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["memory_ids"] == data["memory_ids"]

    memory_id = data["memory_ids"][0]
    shown = runner.invoke(
        app, ["memory", "show", memory_id, "--workspace", str(workspace), "--json"]
    )
    assert shown.exit_code == 0, shown.output
    memory = parse_json(shown.output)["data"]
    item = memory["item"]
    assert item["memory_type"] == "correction"
    assert item["status"] == "pending"
    assert item["confidence_score"] == 0.45
    assert item["scope_json"]["project_slug"] == "demo"
    assert item["value_json"]["error_type"] in {
        "changed_text",
        "possible_terminology_change",
        "possible_style_change",
        "possible_omission_or_addition",
    }
    assert memory["evidence"][0]["source_kind"] == "correction_import"
    assert memory["audit_logs"][0]["action"] == "create"

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_type = 'learn.correction'"
        ).fetchone()[0]
        model_count = conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
    assert task_count == 1
    assert model_count == 0


def test_learn_correction_from_jsonl_with_root_workspace(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    jsonl = write_file(
        tmp_path / "corrections.jsonl",
        json.dumps(
            {
                "raw_text": "Thiên hạ.",
                "ai_translation": "world.",
                "human_translation": "thiên hạ.",
                "context": {"chapter": "1", "segment": "2"},
            },
            ensure_ascii=False,
        )
        + "\n",
    )

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "learn",
            "correction",
            "--file",
            str(jsonl),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["corrections_created"] == 1
    shown = runner.invoke(app, ["--workspace", str(workspace), "memory", "show", data["memory_ids"][0], "--json"])
    assert shown.exit_code == 0, shown.output
    value = parse_json(shown.output)["data"]["item"]["value_json"]
    assert value["context"] == {"chapter": "1", "segment": "2"}


def test_identical_ai_human_creates_no_correction_and_reports_skipped(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    raw = write_file(tmp_path / "raw.txt", "Một câu.")
    ai = write_file(tmp_path / "ai.txt", "Một câu dịch.")
    human = write_file(tmp_path / "human.txt", "Một câu dịch.")

    result = runner.invoke(
        app,
        [
            "learn",
            "correction",
            "--workspace",
            str(workspace),
            "--raw",
            str(raw),
            "--ai",
            str(ai),
            "--human",
            str(human),
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["corrections_created"] == 0
    assert data["skipped_records"] == 1
    assert data["memory_ids"] == []
    assert (workspace / data["report_path"]).exists()

    listed = runner.invoke(
        app,
        [
            "memory",
            "list",
            "--workspace",
            str(workspace),
            "--type",
            "correction",
            "--json",
        ],
    )
    assert listed.exit_code == 0, listed.output
    assert parse_json(listed.output)["data"]["items"] == []


def test_learn_correction_invalid_files_and_missing_project_fail_cleanly(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    raw = write_file(tmp_path / "raw.txt", "Raw")
    ai = write_file(tmp_path / "ai.txt", "AI")
    human = write_file(tmp_path / "human.txt", "Human")

    missing_file = runner.invoke(
        app,
        [
            "learn",
            "correction",
            "--workspace",
            str(workspace),
            "--raw",
            str(tmp_path / "missing.txt"),
            "--ai",
            str(ai),
            "--human",
            str(human),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert missing_file.exit_code == 4
    assert parse_json(missing_file.output)["status"] == "error"

    missing_project = runner.invoke(
        app,
        [
            "learn",
            "correction",
            "--workspace",
            str(workspace),
            "--raw",
            str(raw),
            "--ai",
            str(ai),
            "--human",
            str(human),
            "--project",
            "missing",
            "--json",
        ],
    )
    assert missing_project.exit_code == 4
    assert "Project not found" in parse_json(missing_project.output)["error"]["message"]

    invalid_jsonl = write_file(tmp_path / "bad.jsonl", "{bad json}\n")
    bad_jsonl_result = runner.invoke(
        app,
        [
            "learn",
            "correction",
            "--workspace",
            str(workspace),
            "--file",
            str(invalid_jsonl),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert bad_jsonl_result.exit_code == 4
    assert parse_json(bad_jsonl_result.output)["status"] == "error"
