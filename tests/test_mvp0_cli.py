from __future__ import annotations

from contextlib import closing
import json
import shutil
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_storage.workspace import WORKSPACE_DIRS


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def table_names(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


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


def test_repeated_init_is_idempotent_and_does_not_overwrite_configs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    assert result.exit_code == 0, result.output

    providers_path = workspace / "config" / "providers.yaml"
    custom_providers = "providers:\n  custom_mock:\n    type: mock\n"
    providers_path.write_text(custom_providers, encoding="utf-8")

    second = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    assert second.exit_code == 0, second.output
    assert providers_path.read_text(encoding="utf-8") == custom_providers

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        migration_versions = [
            row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ]
    assert migration_versions == [1, 2, 3]


def test_file_based_migration_initializes_core_tables_and_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    result = runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    assert result.exit_code == 0, result.output

    tables = table_names(workspace / "nts.db")

    assert {
        "projects",
        "task_runs",
        "model_runs",
        "provider_configs",
        "schema_migrations",
    }.issubset(tables)


def test_doctor_accepts_command_level_and_root_level_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    command_level = runner.invoke(app, ["doctor", "--workspace", str(workspace), "--json"])
    root_level = runner.invoke(app, ["--workspace", str(workspace), "doctor", "--json"])

    assert command_level.exit_code == 0, command_level.output
    assert root_level.exit_code == 0, root_level.output
    assert parse_json(command_level.output)["data"]["ok"] is True
    assert parse_json(root_level.output)["data"]["ok"] is True


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


def test_config_validate_default_paths_uses_workspace_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    result = runner.invoke(app, ["config", "validate", "--workspace", str(workspace), "--json"])

    assert result.exit_code == 0, result.output
    payload = parse_json(result.output)
    assert payload["data"]["valid"] is True
    assert payload["data"]["providers_path"] == str(workspace / "config" / "providers.yaml")
    assert payload["data"]["routing_path"] == str(workspace / "config" / "routing.yaml")


def test_project_create_and_list_with_command_level_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    create_result = runner.invoke(
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
            "--json",
        ],
    )
    assert create_result.exit_code == 0, create_result.output
    created = parse_json(create_result.output)
    assert created["data"]["slug"] == "demo"
    assert created["data"]["task_run_id"].startswith("task_")

    list_result = runner.invoke(
        app,
        ["project", "list", "--workspace", str(workspace), "--json"],
    )
    assert list_result.exit_code == 0, list_result.output
    listed = parse_json(list_result.output)
    assert [project["slug"] for project in listed["data"]["projects"]] == ["demo"]


def test_project_create_and_list_after_workspace_discovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--workspace", "workspace", "--json"])

    create_result = runner.invoke(
        app,
        [
            "project",
            "create",
            "--slug",
            "discovered",
            "--name",
            "Discovered",
            "--source-lang",
            "zh",
            "--target-lang",
            "vi",
            "--json",
        ],
    )
    list_result = runner.invoke(app, ["project", "list", "--json"])

    assert create_result.exit_code == 0, create_result.output
    assert list_result.exit_code == 0, list_result.output
    assert parse_json(list_result.output)["data"]["projects"][0]["slug"] == "discovered"


def test_mock_provider_returns_deterministic_response_and_logs_model_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])

    args = [
        "model",
        "test",
        "--workspace",
        str(workspace),
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

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        model_run_count = conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        task_run_count = conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_type = 'model.test'"
        ).fetchone()[0]

    assert model_run_count == 2
    assert task_run_count == 2


def test_sqlite_connections_are_closed_after_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runner.invoke(app, ["init", "--workspace", str(workspace), "--json"])
    runner.invoke(app, ["doctor", "--workspace", str(workspace), "--json"])
    runner.invoke(
        app,
        [
            "model",
            "test",
            "--workspace",
            str(workspace),
            "--provider",
            "mock",
            "--json",
        ],
    )

    shutil.rmtree(workspace)
    assert not workspace.exists()
