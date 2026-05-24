from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_storage.workspace import WORKSPACE_DIRS


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def test_cli_smoke() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Novel Translator Studio" in result.output


def test_workspace_init_creates_expected_folders_and_db(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    assert result.exit_code == 0, result.output
    payload = parse_json(result.output)
    assert payload["status"] == "success"
    assert (workspace / "nts.db").exists()
    for rel_path in WORKSPACE_DIRS:
        assert (workspace / rel_path).is_dir()


def test_db_migration_initializes_core_tables(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    assert result.exit_code == 0, result.output

    with sqlite3.connect(workspace / "nts.db") as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert {"projects", "task_runs", "model_runs", "provider_configs"}.issubset(tables)


def test_config_loader_parses_example_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "config",
            "validate",
            "--providers",
            "config/providers.example.yaml",
            "--routing",
            "config/task-routing.example.yaml",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = parse_json(result.output)
    assert payload["status"] == "success"
    assert payload["data"]["valid"] is True
    assert "mock" in payload["data"]["providers"]
    assert "language_detect" in payload["data"]["tasks"]


def test_project_create_and_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    create_result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "project",
            "create",
            "--slug",
            "demo",
            "--name",
            "Demo",
            "--source-lang",
            "zh",
            "--target-lang",
            "vi",
            "--json",
        ],
    )
    assert create_result.exit_code == 0, create_result.output
    created = parse_json(create_result.output)
    assert created["data"]["slug"] == "demo"
    assert created["data"]["task_run_id"].startswith("task_")

    list_result = runner.invoke(
        app,
        ["--workspace", str(workspace), "project", "list", "--json"],
    )
    assert list_result.exit_code == 0, list_result.output
    listed = parse_json(list_result.output)
    assert [project["slug"] for project in listed["data"]["projects"]] == ["demo"]


def test_mock_provider_returns_deterministic_response_and_logs_model_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    args = [
        "--workspace",
        str(workspace),
        "model",
        "test",
        "--provider",
        "mock",
        "--prompt",
        "same prompt",
        "--json",
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = parse_json(first.output)
    second_payload = parse_json(second.output)
    assert (
        first_payload["data"]["response"]["output"]
        == second_payload["data"]["response"]["output"]
        == "mock:66fddd00ccb86fb2"
    )

    with sqlite3.connect(workspace / "nts.db") as conn:
        model_run_count = conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        task_run_count = conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_type = 'model.test'"
        ).fetchone()[0]

    assert model_run_count == 2
    assert task_run_count == 2
