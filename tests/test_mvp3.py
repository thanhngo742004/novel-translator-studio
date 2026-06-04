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


def create_memory(
    workspace: Path,
    *,
    memory_type: str,
    status: str,
    source_key: str,
    target_text: str,
    confidence: str = "0.8",
) -> dict:
    result = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            memory_type,
            "--status",
            status,
            "--project",
            "demo",
            "--source-key",
            source_key,
            "--target-text",
            target_text,
            "--confidence-score",
            confidence,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]["item"]


def test_export_bundles_migration_exists(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert "export_bundles" in tables
    assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_export_bundle_includes_active_memory_and_excludes_non_active(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    active_term = create_memory(
        workspace,
        memory_type="term",
        status="active",
        source_key="Long",
        target_text="Long",
    )
    active_name = create_memory(
        workspace,
        memory_type="name",
        status="active",
        source_key="A Ly",
        target_text="A Ly",
    )
    active_pronoun = create_memory(
        workspace,
        memory_type="pronoun",
        status="active",
        source_key="ta-ngươi",
        target_text="ta-ngươi",
    )
    active_style = create_memory(
        workspace,
        memory_type="style",
        status="active",
        source_key="style",
        target_text="Ngắn gọn.",
    )
    active_correction = create_memory(
        workspace,
        memory_type="correction",
        status="active",
        source_key="corr",
        target_text="Sửa văn máy.",
    )
    excluded = [
        create_memory(
            workspace,
            memory_type="term",
            status=status,
            source_key=f"excluded-{status}",
            target_text=status,
        )
        for status in ("pending", "rejected", "deprecated", "draft")
    ]

    result = runner.invoke(
        app,
        ["export", "bundle", "--workspace", str(workspace), "--project", "demo", "--json"],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    export_dir = workspace / data["bundle_path"]
    bundle_path = workspace / data["bundle_file"]
    manifest_path = workspace / data["manifest_file"]
    checksums_path = workspace / data["checksums_file"]
    assert export_dir.is_dir()
    assert bundle_path.exists()
    assert manifest_path.exists()
    assert checksums_path.exists()
    assert (export_dir / "compat" / "StyleSummary.txt").exists()
    assert (export_dir / "compat" / "Pronouns.txt").exists()
    assert (export_dir / "compat" / "LuatNhan.txt").exists()

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["schema_version"] == "lamm_t_compact_v1"
    assert bundle["checksum"] == data["checksum"]
    assert [item["id"] for item in bundle["force_terms"]] == [active_term["id"]]
    assert [item["id"] for item in bundle["force_names"]] == [active_name["id"]]
    assert [item["id"] for item in bundle["pronoun_rules"]] == [active_pronoun["id"]]
    assert [item["id"] for item in bundle["style_rules"]] == [active_style["id"]]
    assert [item["id"] for item in bundle["correction_rules"]] == [active_correction["id"]]
    serialized_bundle = json.dumps(bundle, ensure_ascii=False)
    for item in excluded:
        assert item["id"] not in serialized_bundle

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["checksum"] == data["checksum"]
    assert "bundle.json" in checksums_path.read_text(encoding="utf-8")

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        row = conn.execute(
            "SELECT bundle_path, checksum FROM export_bundles WHERE checksum = ?",
            (data["checksum"],),
        ).fetchone()
    assert row == (data["bundle_path"], data["checksum"])


def test_export_checksum_is_deterministic_when_repeated(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    create_memory(
        workspace,
        memory_type="term",
        status="active",
        source_key="Long",
        target_text="Long",
    )

    first = runner.invoke(
        app,
        ["export", "bundle", "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    second = runner.invoke(
        app,
        ["export", "bundle", "--workspace", str(workspace), "--project", "demo", "--json"],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_data = parse_json(first.output)["data"]
    second_data = parse_json(second.output)["data"]
    assert first_data["checksum"] == second_data["checksum"]
    assert first_data["bundle_id"] == second_data["bundle_id"]

    first_bundle = json.loads((workspace / first_data["bundle_file"]).read_text(encoding="utf-8"))
    second_bundle = json.loads((workspace / second_data["bundle_file"]).read_text(encoding="utf-8"))
    assert first_bundle == second_bundle

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        count = conn.execute("SELECT COUNT(*) FROM export_bundles").fetchone()[0]
    assert count == 2


def test_export_vbook_profile_and_root_workspace_form(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    create_memory(
        workspace,
        memory_type="pronoun",
        status="active",
        source_key="ta-ngươi",
        target_text="ta-ngươi",
    )

    result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace),
            "export",
            "vbook-profile",
            "--project",
            "demo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["bundle_kind"] == "vbook-profile"
    manifest = json.loads((workspace / data["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["bundle_kind"] == "vbook-profile"


def test_export_missing_project_fails_cleanly(tmp_path: Path) -> None:
    workspace, _project = init_project(tmp_path)
    result = runner.invoke(
        app,
        ["export", "bundle", "--workspace", str(workspace), "--project", "missing", "--json"],
    )
    assert result.exit_code == 4
    payload = parse_json(result.output)
    assert payload["status"] == "error"
    assert "Project not found" in payload["error"]["message"]
