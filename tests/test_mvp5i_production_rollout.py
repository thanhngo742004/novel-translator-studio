from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.eval_harness import terminology_mismatches_for
from nts_core.memory import create_memory_item
from nts_core.production_rollout import run_production_qa
from nts_core.projects import get_project_by_slug
from nts_core.production_translation import (
    _apply_production_unit_plan,
    _compact_pair_budget,
    _classify_production_pair,
    _production_verification,
    _repair_prompt,
    _repair_unsafe_units,
    _split_source_text,
    _terminal_ok,
    build_production_prompt,
)
from nts_core.stable_prompts import StablePromptRecord
from nts_storage.database import connection, json_dumps, utc_now
from nts_storage.workspace import init_workspace


runner = CliRunner()


def test_terminal_ok_accepts_smart_closing_quotes() -> None:
    assert _terminal_ok("Hi Tuyền tiên tử nói: “Chọn việc liên quan Thanh Minh Ma Giáo.”")
    assert _terminal_ok("Nàng đáp: ‘Được.’")


def test_production_verification_allows_nonfinal_split_fragment_terminal() -> None:
    sample = {
        "paragraph_pairs": [
            {"paragraph_id": "u001_a", "source_text": "Một nửa câu", "target_text": "Nửa đầu"},
            {"paragraph_id": "u001_b", "source_text": "nửa còn lại。", "target_text": "nửa cuối."},
        ],
        "production_unit_split_rows": [
            {"unit_id": "u001", "split_required": True, "child_ids": ["u001_a", "u001_b"]},
        ],
    }
    adjusted = _production_verification(
        {
            "reasons": ["paragraph_truncation_detected"],
            "truncated_paragraphs": [{"paragraph_id": "u001_a", "reasons": ["missing_terminal_punctuation"]}],
            "warnings": [],
        },
        sample=sample,
    )

    assert "paragraph_truncation_detected" not in adjusted["reasons"]
    assert adjusted["truncated_paragraphs"] == []
    assert "nonfinal_split_fragment_terminal_allowed" in adjusted["warnings"]


def test_production_verification_allows_heading_without_terminal() -> None:
    sample = {"paragraph_pairs": [{"paragraph_id": "u001", "source_text": "第22章 获得灵宝"}]}
    adjusted = _production_verification(
        {
            "reasons": ["paragraph_truncation_detected"],
            "truncated_paragraphs": [{"paragraph_id": "u001", "reasons": ["suspicious_incomplete_final_token"]}],
            "warnings": [],
        },
        sample=sample,
    )

    assert "paragraph_truncation_detected" not in adjusted["reasons"]
    assert adjusted["truncated_paragraphs"] == []
    assert "heading_without_terminal_punctuation_allowed" in adjusted["warnings"]


def test_production_verification_allows_complete_panel_over_strict() -> None:
    adjusted = _production_verification(
        {
            "reasons": ["paragraph_exceeds_strict_max"],
            "truncated_paragraphs": [],
            "terminology_mismatches": [],
            "per_paragraph_length_table": [
                {
                    "paragraph_id": "u001",
                    "unit_type": "panel",
                    "over_strict_max": True,
                    "output_reference_ratio": 2.2,
                    "truncation_detected": False,
                }
            ],
            "warnings": [],
        },
        sample={"paragraph_pairs": [{"paragraph_id": "u001", "source_text": "【法术：无】"}]},
    )

    assert "paragraph_exceeds_strict_max" not in adjusted["reasons"]
    assert "complete_panel_over_strict_allowed" in adjusted["warnings"]


def test_repair_prompt_requires_balanced_quotes_and_strict_max() -> None:
    payload = json.loads(
        _repair_prompt(
            {"paragraph_id": "u001", "source_text": "他说。", "strict_max": 120},
            "Ông nói: “dở dang",
            ["unmatched_curly_quote", "paragraph_exceeds_strict_max"],
            {"fixed_terms": []},
        )
    )
    instructions = "\n".join(payload["instructions"])

    assert "close with ”" in instructions
    assert "no longer than strict_max" in instructions
    assert payload["strict_max"] == 120


def parse_json(output: str) -> dict:
    return json.loads(output)


def test_production_prompt_strips_cross_project_stable_glossary() -> None:
    record = StablePromptRecord(
        prompt_id="han-jue_mvp48_candidate",
        prompt_version=None,
        source_eval_run_id="han-jue_eval_123",
        language_pair=None,
        domain=None,
        quality_summary={},
        stable_gate_summary={},
        approval_status="approved",
        approval_path=None,
        prompt_text=(
            "Stable body\n"
            "Required glossary mappings when the source term appears: [{\"source\": \"韩绝\", \"target\": \"Hàn Tuyệt\"}]\n"
            "Candidate Vietnamese renderings to consider, not hard rules: {\"names\": [\"Hàn\"]}\n"
            "Return JSON only"
        ),
        prompt_path="stable.md",
        metadata_path="stable.json",
        created_at=None,
    )
    sample = {"chapter_id": 1, "paragraph_pairs": [], "source_text": "王林看着众人。"}

    system_prompt, _user_prompt = build_production_prompt(
        stable_prompt=record,
        project_slug="tien-nghich",
        sample=sample,
        memory_bundle={"items": []},
        glossary={},
    )

    assert "Stable body" in system_prompt
    assert "Production translation mode:" in system_prompt
    assert "韩绝" not in system_prompt
    assert "Candidate Vietnamese renderings" not in system_prompt


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


def test_provider_preflight_primary_404_fallback_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.")
    _write_stable_prompt(workspace_path)
    result = runner.invoke(app, ["production", "preflight", "--workspace", str(workspace_path), "--provider", "mock", "--model", "mock-404", "--fallback-model", "mock-production", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["pass"] is True
    assert data["primary_status"]["status"] == "model_route_not_found"
    assert data["chosen_model"] == "mock-production"
    assert data["fallback_model_used"] is True
    assert Path(data["provider_preflight_path"]).exists()


def test_provider_preflight_both_404_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.")
    result = runner.invoke(app, ["production", "preflight", "--workspace", str(workspace_path), "--provider", "mock", "--model", "mock-404", "--fallback-model", "fallback-404", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["pass"] is False
    assert data["blocker_reason"] == "primary_and_fallback_unavailable"


def test_rollout_stops_before_batch_when_preflight_blocked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.")
    _write_stable_prompt(workspace_path)
    result = runner.invoke(app, ["production", "rollout", "--workspace", str(workspace_path), "--project", "demo", "--provider", "mock", "--model", "mock-404", "--fallback-model", "fallback-404", "--chapters", "1", "--max-chapters", "1", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["final_decision"] == "BLOCKED"
    assert data["api_calls_used"] == 0
    assert Path(data["provider_preflight_path"]).exists()
    assert not (Path(data["run_dir"]).parent.parent / "prod_batch" / Path(data["run_dir"]).name / "batch_manifest.json").exists()


def test_canary_mode_limited_and_no_rule_rendering(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.\n\n第2章 二\n\nHero keeps practicing Dao.")
    _write_stable_prompt(workspace_path)
    result = runner.invoke(app, ["production", "rollout", "--workspace", str(workspace_path), "--project", "demo", "--provider", "mock", "--model", "mock-production", "--chapters", "1-2", "--max-chapters", "2", "--canary", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["final_decision"] == "PASS"
    assert data["chapters_processed"] <= 2
    assert data["rules_rendered_count"] == 0
    canary = json.loads(Path(data["canary_report_path"]).read_text(encoding="utf-8"))
    assert canary["pass"] is True
    assert canary["raw_nlp_cache_injected"] is False


def test_diagnose_qa_identifies_truncation_and_strict_max(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "ws")
    run_dir = workspace.path / "artifacts" / "production_rollout" / "demo_run"
    batch_dir = workspace.path / "artifacts" / "prod_batch" / "demo_run"
    chunk_dir = batch_dir / "chunk_outputs" / "chapter_2" / "chunk_001"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / "source.zh.txt").write_text("源段一很长。\n\n源段二也很长。", encoding="utf-8")
    (chunk_dir / "translation.vi.txt").write_text("Đoạn một bị", encoding="utf-8")
    (chunk_dir / "quality_report.json").write_text(json.dumps({"status":"fail","verification":{"reasons":["paragraph_exceeds_strict_max","paragraph_truncation_detected"],"truncated_paragraphs":[{"paragraph_id":"p1"}],"over_budget_paragraphs":[{"paragraph_id":"p1","strict_max":10,"output_length":20}]}}), encoding="utf-8")
    (batch_dir / "chapter_results.json").parent.mkdir(parents=True, exist_ok=True)
    (batch_dir / "chapter_results.json").write_text(json.dumps({"chapters":[{"chapter_id":"chapter_2","chapter_no":2,"status":"failed","error":"Production translation failed deterministic quality checks."}]}), encoding="utf-8")
    (batch_dir / "batch_manifest.json").write_text(json.dumps({"status":"partial_failure"}), encoding="utf-8")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "production_rollout_summary.json").write_text(json.dumps({"batch_dir": str(batch_dir)}), encoding="utf-8")
    result = runner.invoke(app, ["production", "diagnose-qa", "--workspace", str(workspace.path), "--run", str(run_dir), "--chapter", "2", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["paragraph_truncation_detected"] is True
    assert data["paragraph_exceeds_strict_max"] is True
    assert Path(data["diagnostic_path"]).exists()
    assert (run_dir / "chapter_2_paragraph_safety_table.csv").exists()
    assert Path(data["safety_repair_report_path"]).exists()


def test_rollout_model_policy_artifacts_and_call_usage_written(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect and checks Dao talent.")
    _write_stable_prompt(workspace_path)
    result = runner.invoke(app, ["production", "rollout", "--workspace", str(workspace_path), "--project", "demo", "--provider", "mock", "--model", "mock-production", "--fallback-model", "mock-fallback", "--chapters", "1", "--max-chapters", "1", "--max-real-calls", "2", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    policy_path = Path(data["model_policy_snapshot_path"])
    assert policy_path.exists()
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    assert policy["chosen_model"] == "mock-production"
    batch_dir = Path(data["batch_dir"])
    usage_files = list((batch_dir / "chunk_outputs").glob("*/chunk_001/per_call_model_usage.jsonl"))
    assert usage_files
    rows = [json.loads(line) for line in usage_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {row["call_type"] for row in rows} >= {"selector"}
    assert any(row["call_type"] == "translation" and row["chosen_model"] == "mock-production" for row in rows)
    assert (usage_files[0].parent / "compression_model_usage_report.json").exists()
    assert (usage_files[0].parent / "repair_model_usage_report.json").exists()


def test_fallback_policy_prevents_bad_primary_route_in_translation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = _workspace_with_text(tmp_path, "第1章 一\n\nHero enters Dao Sect.")
    _write_stable_prompt(workspace_path)
    result = runner.invoke(app, ["production", "rollout", "--workspace", str(workspace_path), "--project", "demo", "--provider", "mock", "--model", "mock-404", "--fallback-model", "mock-production", "--chapters", "1", "--max-chapters", "1", "--max-real-calls", "2", "--json"])
    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["fallback_model_used"] is True
    assert data["chosen_model"] == "mock-production"
    usage_file = next((Path(data["batch_dir"]) / "chunk_outputs").glob("*/chunk_001/per_call_model_usage.jsonl"))
    rows = [json.loads(line) for line in usage_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row["call_type"] == "translation" and row["chosen_model"] == "mock-production" for row in rows)
    assert not any(row["call_type"] == "translation" and row["chosen_model"] == "mock-404" for row in rows)


def test_diagnostic_markdown_maps_unit_rows(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "ws2")
    run_dir = workspace.path / "artifacts" / "production_rollout" / "demo_run2"
    batch_dir = workspace.path / "artifacts" / "prod_batch" / "demo_run2"
    chunk_dir = batch_dir / "chunk_outputs" / "chapter_2" / "chunk_001"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / "source.txt").write_text("源一。\n\n源二。", encoding="utf-8")
    (chunk_dir / "translation.vi.txt").write_text("Ra một.\n\nRa hai", encoding="utf-8")
    (chunk_dir / "per_call_model_usage.jsonl").write_text(json.dumps({"call_type":"compression","chapter":"2","unit_id":"u002","provider":"mock","requested_model":"mock-production","chosen_model":"mock-production","fallback_model_used":False,"route_status":"ok","error_class":None})+"\n", encoding="utf-8")
    quality = {"status":"fail","verification":{"reasons":["paragraph_truncation_detected"],"truncated_paragraphs":[{"paragraph_id":"u002","reasons":["missing_terminal_punctuation"]}],"per_paragraph_length_table":[{"paragraph_id":"u002","source_text":"源二。","source_char_count":3,"output_char_count":5}]}}
    (chunk_dir / "quality_report.json").write_text(json.dumps(quality), encoding="utf-8")
    (batch_dir / "chapter_results.json").parent.mkdir(parents=True, exist_ok=True)
    (batch_dir / "chapter_results.json").write_text(json.dumps({"chapters":[{"chapter_id":"chapter_2","chapter_no":2,"status":"failed","error":"qa"}]}), encoding="utf-8")
    (batch_dir / "batch_manifest.json").write_text(json.dumps({"status":"partial_failure"}), encoding="utf-8")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "production_rollout_summary.json").write_text(json.dumps({"batch_dir": str(batch_dir)}), encoding="utf-8")
    result = runner.invoke(app, ["production", "diagnose-qa", "--workspace", str(workspace.path), "--run", str(run_dir), "--chapter", "2", "--json"])
    assert result.exit_code == 0, result.output
    md = (run_dir / "chapter_2_qa_diagnostic.md").read_text(encoding="utf-8")
    assert "### u002" in md
    assert "源二。" in md
    assert "Ra hai" in md
    assert "mock-production" in md


def test_oversized_unit_creates_split_plan_and_preserves_order() -> None:
    sample = {
        "paragraph_pairs": [
            {
                "paragraph_id": "p001",
                "source_text": "短句。",
                "source_char_count": 3,
                "target_char_count": 8,
                "target_min": 5,
                "target_max": 10,
                "strict_max": 12,
                "strict_max_ratio": 1.5,
            },
            {
                "paragraph_id": "p002",
                "source_text": "第一句。第二句。第三句。第四句。第五句。第六句。" * 12,
                "source_char_count": len("第一句。第二句。第三句。第四句。第五句。第六句。" * 12),
                "target_char_count": 120,
                "target_min": 80,
                "target_max": 140,
                "strict_max": 180,
                "strict_max_ratio": 1.5,
            },
        ]
    }
    planned = _apply_production_unit_plan(sample)
    unit_ids = [unit["unit_id"] for unit in planned["translation_units"]]
    assert unit_ids[0] == "u001"
    assert unit_ids[1].startswith("u002_")
    assert planned["production_unit_split_rows"][1]["split_required"] is True
    assert planned["production_unit_split_rows"][1]["child_ids"] == [unit_id for unit_id in unit_ids if unit_id.startswith("u002_")]


def test_production_classifier_covers_short_and_glossary_units() -> None:
    assert _classify_production_pair("姓名：") == "pre_panel_label"
    assert _classify_production_pair("1、剑修") == "glossary_label"
    assert _classify_production_pair("冲！") == "short_action"


def test_long_bracketed_destiny_panel_classified_as_mixed_panel_narration() -> None:
    text = "【韩绝，你出生在凡间一修仙宗门内，从小到大，容颜绝世，受人喜爱，你的父母在你年幼时弃你而去，冥冥之中，似乎有什么命运需要你背负。】"
    assert _classify_production_pair(text) == "mixed_panel_narration"


def test_mixed_panel_narration_splits_before_translation() -> None:
    source_text = "【韩绝，你出生在凡间一修仙宗门内，从小到大，容颜绝世，受人喜爱，你的父母在你年幼时弃你而去，冥冥之中，似乎有什么命运需要你背负。】"
    sample = {
        "paragraph_pairs": [
            {
                "paragraph_id": "p001",
                "source_text": source_text,
                "source_char_count": len(source_text),
                "target_char_count": 120,
                "target_min": 80,
                "target_max": 140,
                "strict_max": 180,
                "strict_max_ratio": 1.5,
            }
        ]
    }
    planned = _apply_production_unit_plan(sample)
    unit_ids = [unit["unit_id"] for unit in planned["translation_units"]]
    assert unit_ids == ["u001_a", "u001_b"]
    assert all(unit["production_unit_class"] == "mixed_panel_narration" for unit in planned["translation_units"])


def test_production_stat_line_budget_allows_safe_panel_complete_length() -> None:
    pair = _compact_pair_budget({"paragraph_id": "p001", "source_text": "【身法绝尘：身法资质顶级】", "source_char_count": 13})
    assert pair["production_unit_class"] == "stat_line"
    assert pair["strict_max"] >= 50


def test_production_verification_keeps_non_panel_strict_max_blocking() -> None:
    verification = {
        "reasons": ["paragraph_exceeds_strict_max"],
        "warnings": [],
        "per_paragraph_length_table": [
            {"paragraph_id": "u002", "over_strict_max": True, "output_reference_ratio": 1.2, "unit_type": "dialogue"}
        ],
        "truncated_paragraphs": [],
        "terminology_mismatches": [],
    }
    sample = {"translation_units": [{"paragraph_id": "u002", "source_text": "他说。", "production_unit_class": "dialogue"}], "use_translation_units": True}
    adjusted = _production_verification(verification, sample=sample)
    assert "paragraph_exceeds_strict_max" in adjusted["reasons"]
    assert adjusted["pass"] is False


def test_repair_attempt_cap_works(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    sample = {
        "translation_units": [
            {
                "paragraph_id": "u001",
                "source_text": "原文一。",
                "source_char_count": 4,
                "target_char_count": 8,
                "target_min": 4,
                "target_max": 8,
                "strict_max": 8,
                "unit_type": "narration",
            }
        ],
        "use_translation_units": True,
        "target_char_count": 8,
        "accepted_for_stable_validation": True,
        "source_text": "原文一。",
    }
    paragraphs = [{"paragraph_id": "u001", "text": "dangling:"}]
    verification = {
        "truncated_paragraphs": [{"paragraph_id": "u001", "reasons": ["dangling_glossary_label", "missing_terminal_punctuation"]}],
        "per_paragraph_length_table": [{"paragraph_id": "u001", "over_strict_max": False}],
    }
    policy = {"provider": "mock", "primary_model": "mock-production", "chosen_model": "mock-production", "fallback_model_used": False, "model_route_status": "ok"}
    class Provider: key = "mock"
    updated, report = _repair_unsafe_units(
        provider=Provider(),
        policy=policy,
        artifact_dir=artifact_dir,
        chapter_label="2",
        sample=sample,
        paragraphs=paragraphs,
        verification=verification,
        glossary={"fixed_terms": []},
        max_attempts=2,
    )
    assert updated[0]["text"] == "dangling:"
    assert report["max_unit_repair_attempts"] == 2
    rows = [json.loads(line) for line in (artifact_dir / "unit_repair_attempts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2


def test_separator_line_classified_as_system_panel() -> None:
    assert _classify_production_pair("------------") == "system_panel"


def test_dialogue_with_separator_splits_into_child_units() -> None:
    source_text = '"他说完了。" ------------'
    sample = {
        "paragraph_pairs": [
            {
                "paragraph_id": "p001",
                "source_text": source_text,
                "source_char_count": len(source_text),
                "target_char_count": 30,
                "target_min": 18,
                "target_max": 36,
                "strict_max": 45,
                "strict_max_ratio": 1.5,
            }
        ]
    }
    planned = _apply_production_unit_plan(sample)
    unit_ids = [unit["unit_id"] for unit in planned["translation_units"]]
    assert unit_ids == ["u001_a", "u001_b"]
    assert planned["translation_units"][1]["production_unit_class"] == "system_panel"


def test_sentence_split_parts_long_narration_before_translation() -> None:
    parts = _split_source_text("第一句。第二句。第三句。第四句。第五句。第六句。" * 12)
    assert len(parts) >= 2
    assert all(part.strip() for part in parts)


def test_medium_narration_splits_before_translation_when_over_budget_risk() -> None:
    text = "韩绝是重生人士，前世来自地球二十一世纪，年纪轻轻就被查出癌症晚期，他不愿痛苦地治疗，回家等死，当天晚上为了麻痹自己，他找了一款怀旧修仙游戏玩。"
    parts = _split_source_text(text, production_unit_class="narration")
    assert len(parts) >= 2
    assert all(part.strip() for part in parts)


def test_terminology_mismatch_accepts_alignment_alias_for_longer_dictionary_term() -> None:
    glossary = {"fixed_terms": [{"source": "灵根资质", "target": "Linh căn tư chất"}]}
    assert terminology_mismatches_for("灵根资质", "Hắn có linh căn cực phẩm.", glossary) == []
