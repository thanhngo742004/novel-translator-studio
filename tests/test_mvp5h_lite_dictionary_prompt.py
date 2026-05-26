from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.approved_memory_validation import _final_decision
from nts_core.dictionary import build_dictionary_prompt_support, retrieve_dictionary_hits
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
                "prompt_version": "mvp5h-lite-test",
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


def test_dictionary_prompt_support_exact_hits_budget_and_exclusions(tmp_path: Path) -> None:
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
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_irrelevant",
        source="玉清宗",
        target="Ngọc Thanh Tông",
        entry_type="sect_org",
    )
    _insert_dictionary_entry(
        workspace,
        project,
        entry_id="dict_rejected_entry",
        source="修为",
        target="tu vi",
        status="rejected",
    )
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT INTO dictionary_runs (
                id, project_id, project_slug, scope_json, source_snapshot_json, artifact_dir,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dict_run",
                project["id"],
                project["slug"],
                json_dumps({}),
                json_dumps({}),
                str(workspace.path / "artifacts" / "dictionaries" / "dict_run"),
                "built",
                utc_now(),
                utc_now(),
            ),
        )
        conn.execute(
            """
            INSERT INTO dictionary_candidates (
                id, dict_run_id, project_id, project_slug, entry_type, source_text, target_text,
                normalized_source, normalized_target, scope_json, confidence_score, confidence_json,
                status, evidence_count, chapter_spread, provenance_json, artifact_ref_json,
                conflict_group, created_at, updated_at, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pending_candidate",
                "dict_run",
                project["id"],
                project["slug"],
                "realm",
                "修为",
                "tu vi",
                "修为",
                "tu vi",
                json_dumps({}),
                0.8,
                json_dumps({}),
                "pending_review",
                1,
                1,
                json_dumps({}),
                json_dumps({}),
                None,
                utc_now(),
                utc_now(),
                None,
            ),
        )
        conn.commit()

    source = "韩绝查看灵根资质，也看见灵根和修为。"
    hits = retrieve_dictionary_hits(workspace, "han-jue", source, max_entries=8)
    context = build_dictionary_prompt_support(
        workspace,
        "han-jue",
        source,
        max_entries=1,
        max_chars=500,
    )

    assert [hit["source_text"] for hit in hits[:2]] == ["灵根资质", "灵根"]
    assert all(hit["status"] == "active" for hit in hits)
    assert context["selected_hits"][0]["source_text"] == "灵根资质"
    assert context["dropped_hits"][0]["source_text"] == "灵根"
    assert context["dropped_hits"][0]["drop_reason"] == "max_dictionary_entries"
    assert context["retrieval_report"]["excluded_pending_rejected_count"] == 2
    assert "修为 =>" not in context["block_text"]
    assert "sentences" not in context["block_text"]


def test_validate_approved_dictionary_prompt_artifacts_and_review_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace_path = tmp_path / "workspace"
    init_result = runner.invoke(app, ["init", "--workspace", str(workspace_path), "--json"])
    assert init_result.exit_code == 0, init_result.output
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
            "--use-approved-dictionary",
            "--dictionary-max-entries",
            "8",
            "--emit-prompt-artifacts",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["final_decision"] == "PASS"
    assert data["comparison_mode"] == "approved_dictionary_prompt_support"
    assert all(row["score_delta"] > 0 for row in data["round_results"])
    context = json.loads((run_dir / "prompt_context_bundle.json").read_text(encoding="utf-8"))
    prompt_text = (run_dir / "prompt_used.md").read_text(encoding="utf-8")
    assert "round_1_baseline" in json.dumps(context, ensure_ascii=False)
    assert "round_1_approved_memory" in json.dumps(context, ensure_ascii=False)
    assert "Approved project dictionary for this source:" in prompt_text
    assert "Translate Chinese into concise Vietnamese" in prompt_text
    assert "chapter_candidates" not in prompt_text
    review_path = Path(data["dictionary_prompt_review_path"])
    assert (review_path / "human_review_summary.md").exists()
    assert (review_path / "selected_dictionary_hits.csv").exists()
    assert (review_path / "prompt_samples.md").exists()


def test_translate_text_dictionary_prompt_artifacts_created(tmp_path: Path, monkeypatch) -> None:
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
            "--use-approved-dictionary",
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
    prompt_text = (artifact_dir / "prompt_used.md").read_text(encoding="utf-8")
    assert "灵根资质 => Linh căn tư chất" in prompt_text
    assert "chapter_candidates" not in prompt_text
    with sqlite3.connect(workspace.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0


def test_dictionary_validation_gate_accepts_small_positive_deltas() -> None:
    decision, reason = _final_decision(
        {
            "rounds": 2,
            "round_results": [
                {"baseline_score": 91.0, "memory_score": 91.1, "score_delta": 0.1, "severe_flags": [], "regressions_over_3": []},
                {"baseline_score": 91.0, "memory_score": 91.4, "score_delta": 0.4, "severe_flags": [], "regressions_over_3": []},
            ],
        },
        require_consecutive_improvement=True,
        min_improvement=0.0,
    )

    assert decision == "PASS"
    assert reason == "consecutive_rounds_improved"
