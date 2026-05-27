from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.hybrid_prompt import build_hybrid_prompt_support
from nts_core.memory import create_memory_item
from nts_core.projects import create_project
from nts_core.rules import (
    approve_rule_candidates,
    export_project_rules,
    extract_rule_candidates,
    reject_rule_candidates,
    review_rule_run,
    rule_status,
    test_project_rules as inspect_project_rules,
)
from nts_storage.database import connection, json_dumps, utc_now
from nts_storage.workspace import init_workspace


runner = CliRunner()


def _workspace_with_rule_sources(tmp_path: Path):
    workspace = init_workspace(tmp_path / "workspace")
    project = create_project(
        workspace,
        slug="han-jue",
        name="Han Jue",
        source_lang="zh",
        target_lang="vi",
        domain="novel",
        genre=None,
    )
    now = utc_now()
    with connection(workspace.db_path) as conn:
        for entry_id, source, target, entry_type in (
            ("dict_linggen_quality", "灵根资质", "Linh căn tư chất", "realm"),
            ("dict_linggen", "灵根", "linh căn", "realm"),
            ("dict_yuqing", "玉清宗", "Ngọc Thanh Tông", "sect_org"),
        ):
            conn.execute(
                """
                INSERT INTO project_dictionary_entries (
                    id, project_id, project_slug, entry_type, source_text, target_text,
                    normalized_source, normalized_target, forbidden_variants_json, scope_json,
                    confidence_score, provenance_json, status, approved_by, approved_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    project["id"],
                    project["slug"],
                    entry_type,
                    source,
                    target,
                    source,
                    target.casefold(),
                    json_dumps(["Wrong Variant"] if source == "玉清宗" else []),
                    json_dumps({"project_slug": project["slug"]}),
                    0.91,
                    json_dumps({"source_run_id": "pytest_dict"}),
                    "active",
                    "human",
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="灵根资质",
        target_text="Linh căn tư chất",
        confidence_score=0.82,
    )
    harmful_memory = create_memory_item(
        workspace,
        memory_type="term",
        status="deprecated",
        scope={"project_slug": "han-jue"},
        source_key="雷灵池",
        target_text="Lôi Linh Trì",
        value={"impact_classification": "harmful"},
        confidence_score=0.2,
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="技能",
        target_text="skills",
        value={"context_required": "system_panel_or_game_ui"},
        confidence_score=0.7,
    )

    validation_run = workspace.path / "artifacts" / "approved_memory_validation" / "fake_hybrid_run"
    validation_run.mkdir(parents=True, exist_ok=True)
    hybrid_review = workspace.path / "artifacts" / "hybrid_prompt" / "fake_hybrid_run" / "human_review"
    hybrid_review.mkdir(parents=True, exist_ok=True)
    (hybrid_review / "human_review_summary.md").write_text("Hybrid review fixture\n", encoding="utf-8")
    (validation_run / "prompt_support_items.json").write_text(
        json.dumps(
            {
                "phases": {
                    "round_1_hybrid": {
                        "sample_1": {
                            "candidate_items": [
                                {
                                    "item_id": "dict_linggen_quality",
                                    "source_anchor": "灵根资质",
                                    "target_value": "Linh căn tư chất",
                                },
                                {
                                    "item_id": "dict_linggen",
                                    "source_anchor": "灵根",
                                    "target_value": "linh căn",
                                },
                            ],
                            "selected_items": [],
                            "deduped_items": [],
                            "dropped_items": [],
                        }
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (validation_run / "prompt_conflict_report.json").write_text(
        json.dumps(
            {
                "schema_version": "prompt_conflict_report_v1",
                "phases": {
                    "round_1_hybrid": {
                        "sample_1": {
                            "chapter_id": "10",
                            "conflicts": [
                                {
                                    "conflict_type": "dictionary_memory_duplicate",
                                    "source_anchor": "灵根资质",
                                    "target_value": "Linh căn tư chất",
                                    "winner_item_id": "dict_linggen_quality",
                                    "dropped_item_id": "memory_linggen",
                                    "policy": "dictionary_canonical_when_available",
                                },
                                {
                                    "conflict_type": "overlapping_dictionary_hit",
                                    "source_anchor": "灵根",
                                    "kept_item_ids": ["dict_linggen_quality"],
                                    "dropped_item_id": "dict_linggen",
                                    "policy": "longer_exact_hit_wins",
                                },
                                {
                                    "conflict_type": "related_inactive_or_negative_memory",
                                    "source_anchor": "雷灵池",
                                    "related_memory_id": harmful_memory["id"],
                                    "related_status": "deprecated",
                                    "policy": "inactive_deprecated_harmful_memory_not_rendered",
                                },
                            ],
                        }
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (validation_run / "selected_validation_units.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "sample_id": "sample_panel",
                        "chapter_id": "1",
                        "source_text": "【姓名：韩绝】\n【灵根资质：无】",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    nlp_root = workspace.path / "artifacts" / "nlp" / "han-jue"
    nlp_root.mkdir(parents=True, exist_ok=True)
    nlp_artifact = nlp_root / "1.ltp.json"
    nlp_artifact.write_text(
        json.dumps(
            {
                "meta": {"chapter_id": "chapter_1"},
                "chapter_candidates": {
                    "phrase_candidates": [
                        {"text": "【修为：无】", "count": 3, "confidence": 0.75}
                    ]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (nlp_root / "nlp_cache_manifest.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "chapter_id": "chapter_1",
                        "chapter_no": 1,
                        "artifact_path": str(nlp_artifact),
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return workspace, project, validation_run, hybrid_review


def _extract_fixture_rules(tmp_path: Path):
    workspace, project, validation_run, hybrid_review = _workspace_with_rule_sources(tmp_path)
    result = extract_rule_candidates(
        workspace,
        project_slug="han-jue",
        from_hybrid_run=str(hybrid_review),
        from_validation_run=str(validation_run),
        from_nlp_cache=True,
        chapters="1",
    )
    return workspace, project, result


def _read_candidates(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_rule_extract_from_hybrid_dictionary_memory_and_nlp(tmp_path: Path) -> None:
    workspace, _project, result = _extract_fixture_rules(tmp_path)
    candidates = _read_candidates(result["candidates_path"])
    rule_types = {row["rule_type"] for row in candidates}

    assert result["candidate_count"] > 0
    assert "dictionary_priority_guard" in rule_types
    assert "expansion_guard" in rule_types
    assert "forbidden_variant" in rule_types
    assert "format_preservation" in rule_types
    assert "context_lexical_preference" in rule_types
    assert all(row["status"] in {"pending_review", "needs_human_review"} for row in candidates)
    assert Path(result["human_review_path"], "human_review_summary.md").exists()
    with sqlite3.connect(workspace.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approved_rules").fetchone()[0] == 0


def test_rule_review_package_and_conflicts_are_created(tmp_path: Path) -> None:
    _workspace, _project, result = _extract_fixture_rules(tmp_path)
    reviewed = review_rule_run(_workspace, project_slug="han-jue", run=result["run_dir"])

    assert reviewed["candidate_count"] == result["candidate_count"]
    assert Path(reviewed["human_review_path"], "rule_review_table.csv").exists()
    assert Path(result["run_dir"], "rule_conflicts.json").exists()
    assert Path(result["run_dir"], "rule_review.md").exists()


def test_rule_approve_reject_export_status_and_test_are_review_only(tmp_path: Path) -> None:
    workspace, _project, result = _extract_fixture_rules(tmp_path)
    candidates = _read_candidates(result["candidates_path"])
    approved_id = next(
        row["id"]
        for row in candidates
        if row["rule_type"] == "dictionary_priority_guard"
        and (row["trigger_pattern_json"] or {}).get("text") == "玉清宗"
    )
    rejected_id = next(row["id"] for row in candidates if row["id"] != approved_id)
    with sqlite3.connect(workspace.db_path) as conn:
        before_memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        before_dict_count = conn.execute("SELECT COUNT(*) FROM project_dictionary_entries").fetchone()[0]

    approved = approve_rule_candidates(
        workspace,
        project_slug="han-jue",
        run=result["run_dir"],
        rule_ids=approved_id,
        reviewer="human",
    )
    rejected = reject_rule_candidates(
        workspace,
        project_slug="han-jue",
        run=result["run_dir"],
        rule_ids=rejected_id,
        reason="pytest reject",
    )
    exported = export_project_rules(workspace, project_slug="han-jue")
    status = rule_status(workspace, project_slug="han-jue")
    tested = inspect_project_rules(
        workspace,
        project_slug="han-jue",
        source_text="韩绝进入玉清宗。",
        mode="production",
    )
    with sqlite3.connect(workspace.db_path) as conn:
        after_memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        after_dict_count = conn.execute("SELECT COUNT(*) FROM project_dictionary_entries").fetchone()[0]

    assert approved["updated_rule_ids"] == [approved_id]
    assert rejected["updated_rule_ids"] == [rejected_id]
    assert exported["rule_count"] == 1
    assert status["approved_rule_count"] == 1
    assert tested["match_count"] == 1
    assert tested["prompt_integration"] == "not_enabled_in_mvp5g"
    assert before_memory_count == after_memory_count
    assert before_dict_count == after_dict_count


def test_rule_cli_commands_exist_and_extract_review(tmp_path: Path) -> None:
    workspace, _project, validation_run, hybrid_review = _workspace_with_rule_sources(tmp_path)
    help_result = runner.invoke(app, ["--workspace", str(workspace.path), "rule", "--help"])
    assert help_result.exit_code == 0, help_result.output
    for command_name in ("extract", "review", "approve", "reject", "export", "status", "test"):
        assert command_name in help_result.output

    extract_result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "rule",
            "extract",
            "--project",
            "han-jue",
            "--from-hybrid-run",
            str(hybrid_review),
            "--from-validation-run",
            str(validation_run),
            "--from-nlp-cache",
            "--chapters",
            "1",
            "--json",
        ],
    )
    assert extract_result.exit_code == 0, extract_result.output
    run_dir = json.loads(extract_result.output)["data"]["run_dir"]

    review_result = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "rule",
            "review",
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--json",
        ],
    )
    assert review_result.exit_code == 0, review_result.output
    assert Path(json.loads(review_result.output)["data"]["human_review_path"]).exists()


def test_hybrid_prompt_builder_does_not_use_approved_rules_yet(tmp_path: Path) -> None:
    workspace, _project, result = _extract_fixture_rules(tmp_path)
    candidates = _read_candidates(result["candidates_path"])
    approved_id = next(
        row["id"]
        for row in candidates
        if row["rule_type"] == "dictionary_priority_guard"
        and (row["trigger_pattern_json"] or {}).get("text") == "玉清宗"
    )
    approve_rule_candidates(
        workspace,
        project_slug="han-jue",
        run=result["run_dir"],
        rule_ids=approved_id,
        reviewer="human",
    )

    bundle = build_hybrid_prompt_support(
        workspace,
        "han-jue",
        "韩绝进入玉清宗。",
        max_dictionary_entries=8,
        max_memory_items=6,
        max_support_chars=1200,
    )

    assert "When exact source 玉清宗 appears" not in bundle["block_text"]
    assert all(row.get("source_type") != "rule" for row in bundle["selected_items"])
