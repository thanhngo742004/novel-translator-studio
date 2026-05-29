from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.memory import create_memory_item
from nts_core.production_rollout import run_production_qa
from nts_core.projects import get_project_by_slug
from nts_storage.database import connection, json_dumps, utc_now
from nts_storage.workspace import init_workspace


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def _workspace_with_text(tmp_path: Path, text: str) -> Path:
    workspace = tmp_path / "workspace"
    raw_path = tmp_path / "raw.txt"
    raw_path.write_text(text, encoding="utf-8")
    assert runner.invoke(app, ["init", "--workspace", str(workspace), "--json"]).exit_code == 0
    created = runner.invoke(
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
    assert created.exit_code == 0, created.output
    imported = runner.invoke(
        app,
        ["import", "text", str(raw_path), "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert imported.exit_code == 0, imported.output
    return workspace


def _write_stable_prompt(workspace: Path) -> None:
    run_dir = workspace / "artifacts" / "evaluations" / "stable_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stable_prompt.md").write_text("# Stable Prompt\n\n```text\nTranslate faithfully.\n```\n", encoding="utf-8")
    (run_dir / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "prompt_id": "prompt_test",
                "prompt_version": "mvp5i-test",
                "quality_gate": "pass",
                "average_score": 91.0,
                "created_at": "2026-05-28T00:00:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (run_dir / "stable_prompt_approval.json").write_text(
        json.dumps(
            {
                "schema_version": "stable_prompt_human_review_v1",
                "decision": "approved",
                "reviewer": "pytest",
                "timestamp": "2026-05-28T00:00:01Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _insert_dictionary_entry(workspace_path: Path, source: str, target: str, entry_type: str = "name") -> None:
    workspace = init_workspace(workspace_path)
    project = get_project_by_slug(workspace, "demo")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO project_dictionary_entries (
                id, project_id, project_slug, entry_type, source_text, target_text,
                normalized_source, normalized_target, forbidden_variants_json, scope_json,
                confidence_score, provenance_json, status, approved_by, approved_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"dict_{source}",
                project["id"],
                project["slug"],
                entry_type,
                source,
                target,
                source.casefold(),
                target.casefold(),
                json_dumps([]),
                json_dumps({"project_slug": project["slug"]}),
                0.9,
                json_dumps({"source": "pytest"}),
                "active",
                "human",
                now,
                now,
                now,
            ),
        )
        conn.commit()


def _insert_verifier_only_rule(workspace_path: Path) -> None:
    workspace = init_workspace(workspace_path)
    project = get_project_by_slug(workspace, "demo")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO approved_rules (
                id, project_id, project_slug, rule_type, trigger_pattern_json,
                applies_when_json, instruction, examples_json, forbidden_variants_json,
                scope_json, confidence_score, provenance_json, status,
                approved_by, approved_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rule_verifier_only",
                project["id"],
                project["slug"],
                "format_preservation",
                json_dumps({"kind": "segment_type", "text": "system_panel"}),
                json_dumps({"source_has_brackets": True}),
                "Preserve bracketed panels.",
                json_dumps([]),
                json_dumps([]),
                json_dumps({"project_slug": project["slug"]}),
                0.8,
                json_dumps({"source": "pytest"}),
                "active_verifier_only",
                "human",
                now,
                now,
                now,
            ),
        )
        conn.commit()


def test_production_rollout_uses_safe_hybrid_config_without_rule_rendering(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(
        tmp_path,
        "第1章 一\n\nHero enters Dao Sect and checks Dao talent.\n\n第2章 二\n\nHero keeps practicing Dao.",
    )
    _write_stable_prompt(workspace_path)
    _insert_dictionary_entry(workspace_path, "Dao Sect", "Đạo Tông", "sect_org")
    _insert_dictionary_entry(workspace_path, "Dao talent", "tư chất Đạo", "realm")
    _insert_verifier_only_rule(workspace_path)
    workspace = init_workspace(workspace_path)
    create_memory_item(
        workspace,
        memory_type="name",
        status="active",
        scope={"project_slug": "demo"},
        source_key="Hero",
        target_text="Anh hùng",
        confidence_score=0.9,
    )

    result = runner.invoke(
        app,
        [
            "production",
            "rollout",
            "--workspace",
            str(workspace_path),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--chapters",
            "1-2",
            "--max-chapters",
            "2",
            "--max-real-calls",
            "4",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    rollout_dir = Path(data["run_dir"])
    assert data["final_decision"] == "PASS"
    assert data["rules_rendered_count"] == 0
    assert data["dictionary_hit_count"] > 0
    assert (rollout_dir / "production_config_snapshot.json").exists()
    assert (rollout_dir / "production_qa_report.json").exists()
    assert Path(data["human_review_path"], "human_review_summary.md").exists()
    config = json.loads((rollout_dir / "production_config_snapshot.json").read_text(encoding="utf-8"))
    assert config["use_approved_rules"] is False
    assert "--use-approved-rules" in config["forbidden"]
    prompt_samples = Path(data["human_review_path"], "prompt_samples.md").read_text(encoding="utf-8")
    assert "Rules:" not in prompt_samples
    assert "Dao Sect => Đạo Tông" in prompt_samples


def test_production_qa_detects_blocking_output_and_prompt_issues(tmp_path: Path) -> None:
    rollout_dir = tmp_path / "rollout"
    batch_dir = rollout_dir / "batch"
    chunk_dir = batch_dir / "chunk_outputs" / "chapter_1" / "chunk_001"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    output = batch_dir / "outputs" / "1.vi.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("中文" * 100, encoding="utf-8")
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps({"status": "success", "chapters_processed": ["chapter_1"], "actual_api_calls": 1}),
        encoding="utf-8",
    )
    (batch_dir / "chapter_results.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "chapter_id": "chapter_1",
                        "chapter_no": 1,
                        "status": "success",
                        "output_path": str(output),
                        "quality_summary": {"source_char_count": 10},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (chunk_dir / "prompt_used.md").write_text("Rules:\n- bad\n\"tokens\": []\n", encoding="utf-8")
    (chunk_dir / "quality_report.json").write_text(
        json.dumps({"status": "fail", "verification": {"reasons": ["truncation", "unsafe_compression"]}}),
        encoding="utf-8",
    )
    (chunk_dir / "translation.vi.txt").write_text("中文", encoding="utf-8")
    (chunk_dir / "prompt_context_bundle.json").write_text(
        json.dumps(
            {
                "selected_rule_items": [{"item_id": "rule_bad"}],
                "selected_items": [{"item_id": "pending_memory", "source_type": "memory", "status": "pending"}],
            }
        ),
        encoding="utf-8",
    )
    (chunk_dir / "prompt_budget_report.json").write_text(
        json.dumps({"support_chars": 5000, "support_lines": 30, "selected_rule_count": 1}),
        encoding="utf-8",
    )

    qa = run_production_qa(rollout_dir=rollout_dir, batch_dir=batch_dir, max_support_chars=1200)

    assert qa["pass"] is False
    issue_kinds = {row["kind"] for row in qa["blocking_issues"]}
    assert "rules_rendered_in_prompt" in issue_kinds
    assert "raw_nlp_cache_in_prompt" in issue_kinds
    assert "truncation" in issue_kinds
    assert "unsafe_compression" in issue_kinds
    assert "prompt_budget_exceeded" in issue_kinds
    assert any(row["kind"] == "chinese_residue" for row in qa["warnings"])


def test_production_rollout_rejects_rule_prompt_rendering_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.")
    _write_stable_prompt(workspace_path)

    result = runner.invoke(
        app,
        [
            "production",
            "rollout",
            "--workspace",
            str(workspace_path),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--chapters",
            "1",
            "--max-chapters",
            "1",
            "--use-approved-rules",
            "--json",
        ],
    )

    assert result.exit_code != 0
    payload = parse_json(result.output)
    assert "verifier-only" in payload["error"]["message"]
