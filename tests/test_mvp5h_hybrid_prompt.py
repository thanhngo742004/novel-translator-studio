from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.hybrid_prompt import build_hybrid_prompt_support
from nts_core.memory import create_memory_item
from nts_core.projects import create_project, get_project_by_slug
from nts_storage.database import connection, json_dumps, utc_now
from nts_storage.workspace import init_workspace


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "raw.txt"
EPUB_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "viettranslated.epub"


def parse_json(output: str) -> dict:
    return json.loads(output)


def _write_stable_prompt(workspace: Path) -> None:
    stable_dir = workspace / "artifacts" / "evaluations" / "stable_run"
    stable_dir.mkdir(parents=True, exist_ok=True)
    (stable_dir / "stable_prompt.md").write_text(
        "# Stable Prompt\n\n```text\nTranslate Chinese into concise Vietnamese webnovel prose.\n```\n",
        encoding="utf-8",
    )
    (stable_dir / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "prompt_id": "stable_test",
                "prompt_version": "mvp5h-test",
                "source_eval_run_id": "stable_run",
                "language_pair": "zh-vi",
                "domain": "novel",
                "quality_gate": "pass",
                "average_score": 92,
                "created_at": "2026-05-25T00:00:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (stable_dir / "stable_prompt_approval.json").write_text(
        json.dumps({"decision": "approved", "reviewer": "pytest"}, sort_keys=True),
        encoding="utf-8",
    )


def _workspace_with_project(tmp_path: Path, *, slug: str = "han-jue"):
    workspace = init_workspace(tmp_path / "workspace")
    project = create_project(
        workspace,
        slug=slug,
        name="Han Jue",
        source_lang="zh",
        target_lang="vi",
        domain="novel",
        genre=None,
    )
    _write_stable_prompt(workspace.path)
    return workspace, project


def _insert_dictionary_entry(
    workspace,
    project: dict,
    *,
    entry_id: str,
    source: str,
    target: str,
    entry_type: str = "fixed_phrase",
    status: str = "active",
    confidence: float = 0.9,
) -> None:
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
                entry_id,
                project["id"],
                project["slug"],
                entry_type,
                source,
                target,
                source,
                target.casefold(),
                json_dumps([]),
                json_dumps({"project_slug": project["slug"]}),
                confidence,
                json_dumps({"source_run_id": "pytest"}),
                status,
                "human",
                now,
                now,
                now,
            ),
        )
        conn.commit()


def test_hybrid_support_dedupes_conflicts_and_filters_ineligible_memory(tmp_path: Path) -> None:
    workspace, project = _workspace_with_project(tmp_path)
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_long",
        source="灵根资质",
        target="Linh căn tư chất",
        entry_type="realm",
    )
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_short",
        source="灵根",
        target="linh căn",
        entry_type="realm",
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="灵根资质",
        target_text="Linh căn tư chất",
        confidence_score=0.8,
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="灵根资质",
        target_text="Wrong Target",
        confidence_score=0.95,
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="技能",
        target_text="skills",
        confidence_score=0.9,
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="pending",
        scope={"project_slug": "han-jue"},
        source_key="灵根资质",
        target_text="Pending Target",
        confidence_score=0.9,
    )

    bundle = build_hybrid_prompt_support(
        workspace,
        "han-jue",
        "韩绝查看灵根资质，也提到技能但不是面板。",
        max_dictionary_entries=8,
        max_memory_items=6,
        max_support_chars=1200,
    )

    rendered = bundle["block_text"]
    selected_ids = {row["item_id"] for row in bundle["selected_items"]}
    dropped_reasons = {row.get("drop_reason") for row in bundle["dropped_items"]}
    assert "Project support for this source:" in rendered
    assert "灵根资质 => Linh căn tư chất" in rendered
    assert "Wrong Target" not in rendered
    assert "skills" not in rendered
    assert "dict_long" in selected_ids
    assert "duplicate_support_item" in dropped_reasons
    assert "conflict_lower_priority" in dropped_reasons
    assert "overlapping_longer_dictionary_hit" in dropped_reasons
    assert bundle["conflict_count"] >= 2
    assert any(row["memory_id"] for row in bundle["retrieval_report"]["excluded_memory_rows"])


def test_prompt_inspect_cli_builds_hybrid_bundle(tmp_path: Path) -> None:
    workspace, project = _workspace_with_project(tmp_path)
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_yuqing",
        source="玉清宗",
        target="Ngọc Thanh Tông",
        entry_type="sect_org",
    )
    create_memory_item(
        workspace,
        memory_type="name",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="韩绝",
        target_text="Hàn Tuyệt",
        confidence_score=0.85,
    )

    result = runner.invoke(
        app,
        [
            "prompt",
            "inspect",
            "--workspace",
            str(workspace.path),
            "--project",
            "han-jue",
            "--source-text",
            "韩绝进入玉清宗。",
            "--mode",
            "production",
            "--use-hybrid-prompt",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["selected_dictionary_count"] == 1
    assert data["selected_memory_count"] == 1
    assert "玉清宗 => Ngọc Thanh Tông" in data["block_text"]
    assert "韩绝 => Hàn Tuyệt" in data["block_text"]
    assert "sentences" not in data["block_text"]


def test_translate_text_hybrid_prompt_artifacts_created(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path / "workspace"
    raw_path = tmp_path / "raw.txt"
    raw_path.write_text("第1章 一\n\n韩绝进入玉清宗，查看灵根资质。", encoding="utf-8")
    assert runner.invoke(app, ["init", "--workspace", str(workspace_path), "--json"]).exit_code == 0
    create_result = runner.invoke(
        app,
        [
            "project",
            "create",
            "--workspace",
            str(workspace_path),
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
    imported = runner.invoke(
        app,
        ["import", "text", str(raw_path), "--workspace", str(workspace_path), "--project", "demo", "--json"],
    )
    assert imported.exit_code == 0, imported.output
    chapters = runner.invoke(
        app,
        ["text", "chapters", "list", "--workspace", str(workspace_path), "--project", "demo", "--json"],
    )
    chapter_id = parse_json(chapters.output)["data"]["chapters"][0]["id"]
    _write_stable_prompt(workspace_path)
    workspace = init_workspace(workspace_path)
    project = get_project_by_slug(workspace, "demo")
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_linggen_zizhi",
        source="灵根资质",
        target="Linh căn tư chất",
        entry_type="realm",
    )
    create_memory_item(
        workspace,
        memory_type="name",
        status="active",
        scope={"project_slug": "demo"},
        source_key="韩绝",
        target_text="Hàn Tuyệt",
        confidence_score=0.9,
    )

    result = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace_path),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--use-hybrid-prompt",
            "--emit-prompt-artifacts",
            "--force",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    artifact_dir = Path(data["artifact_dir"])
    assert (artifact_dir / "prompt_context_bundle.json").exists()
    assert (artifact_dir / "prompt_budget_report.json").exists()
    assert (artifact_dir / "prompt_retrieval_report.json").exists()
    assert (artifact_dir / "prompt_conflict_report.json").exists()
    assert (artifact_dir / "prompt_support_items.json").exists()
    prompt_text = (artifact_dir / "prompt_used.md").read_text(encoding="utf-8")
    assert "Project support for this source:" in prompt_text
    assert "灵根资质 => Linh căn tư chất" in prompt_text
    assert "韩绝 => Hàn Tuyệt" in prompt_text
    assert "chapter_candidates" not in prompt_text


def test_validate_hybrid_prompt_artifacts_and_review_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path / "workspace"
    assert runner.invoke(app, ["init", "--workspace", str(workspace_path), "--json"]).exit_code == 0
    create_result = runner.invoke(
        app,
        [
            "project",
            "create",
            "--workspace",
            str(workspace_path),
            "--slug",
            "han-jue",
            "--name",
            "Han Jue",
            "--source-lang",
            "zh",
            "--target-lang",
            "vi",
            "--json",
        ],
    )
    assert create_result.exit_code == 0, create_result.output
    _write_stable_prompt(workspace_path)
    workspace = init_workspace(workspace_path)
    project = get_project_by_slug(workspace, "han-jue")
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_han_jue",
        source="韩绝",
        target="Hàn Tuyệt",
        entry_type="name",
    )
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_yuqing",
        source="玉清宗",
        target="Ngọc Thanh Tông",
        entry_type="sect_org",
    )
    create_memory_item(
        workspace,
        memory_type="name",
        status="active",
        scope={"project_slug": "han-jue"},
        source_key="韩绝",
        target_text="Hàn Tuyệt",
        confidence_score=0.9,
    )

    result = runner.invoke(
        app,
        [
            "learn",
            "validate-approved-memory",
            "--workspace",
            str(workspace_path),
            "--project",
            "han-jue",
            "--raw",
            str(RAW_PATH),
            "--translated",
            str(EPUB_PATH),
            "--provider",
            "mock",
            "--model",
            "mock-eval",
            "--fallback-model",
            "mock-eval",
            "--chapters",
            "1-2",
            "--rounds",
            "2",
            "--use-stable-prompt",
            "--resumable",
            "--use-hybrid-prompt",
            "--dictionary-max-entries",
            "8",
            "--memory-max-items",
            "6",
            "--support-max-chars",
            "1200",
            "--emit-prompt-artifacts",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["final_decision"] == "PASS"
    assert data["comparison_mode"] == "hybrid_prompt_support"
    assert all(row["score_delta"] > 0 for row in data["round_results"])
    assert (run_dir / "prompt_context_bundle.json").exists()
    assert (run_dir / "prompt_conflict_report.json").exists()
    assert (run_dir / "prompt_support_items.json").exists()
    prompt_text = (run_dir / "prompt_used.md").read_text(encoding="utf-8")
    assert "Project support for this source:" in prompt_text
    assert "chapter_candidates" not in prompt_text
    review_path = Path(data["hybrid_prompt_review_path"])
    assert (review_path / "human_review_summary.md").exists()
    assert (review_path / "selected_support_items.csv").exists()
    assert (review_path / "prompt_conflict_summary.md").exists()
    with sqlite3.connect(workspace.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_items WHERE status = 'pending'").fetchone()[0] == 0
