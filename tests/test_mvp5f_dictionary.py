from __future__ import annotations

import inspect
import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core import production_translation
from nts_core.dictionary import (
    approve_dictionary_candidates,
    build_dictionary_run,
    dictionary_status,
    export_project_dictionary,
    inspect_dictionary_hits,
    prepare_dictionary_run,
    reject_dictionary_candidates,
    review_dictionary_run,
)
from nts_core.memory import create_memory_item
from nts_core.projects import create_project
from nts_storage.workspace import init_workspace


runner = CliRunner()


def _workspace_with_ltp_cache(tmp_path: Path, *, include_conflict: bool = False):
    workspace = init_workspace(tmp_path / "workspace")
    create_project(
        workspace,
        slug="han-jue",
        name="Han Jue",
        source_lang="zh",
        target_lang="vi",
        domain="novel",
        genre=None,
    )
    root = workspace.path / "artifacts" / "nlp" / "han-jue"
    root.mkdir(parents=True, exist_ok=True)
    chapters = []
    for chapter_no in (1, 2):
        chapter_id = f"chapter_{chapter_no}"
        entity_candidates = [
            {"text": "韩绝", "count": 5, "confidence": 0.72, "entity_type": "person"},
            {"text": "玉清宗", "count": 4, "confidence": 0.72, "entity_type": "organization"},
            {"text": "------------", "count": 2, "confidence": 0.72, "entity_type": "other"},
            {"text": "章", "count": 2, "confidence": 0.72, "entity_type": "other"},
            {"text": "韩绝不", "count": 2, "confidence": 0.72, "entity_type": "person"},
            {"text": "邢红", "count": 2, "confidence": 0.72, "entity_type": "person"},
            {"text": "邢红璇", "count": 4, "confidence": 0.72, "entity_type": "person"},
        ]
        term_candidates = [
            {"text": "韩绝", "count": 8, "confidence": 0.7},
            {"text": "玉清宗", "count": 6, "confidence": 0.7},
            {"text": "灵根", "count": 6, "confidence": 0.7},
            {"text": "筑基", "count": 5, "confidence": 0.7},
            {"text": "没有", "count": 9, "confidence": 0.7},
            {"text": "邢红璇", "count": 6, "confidence": 0.7},
        ]
        if include_conflict:
            term_candidates.append({"text": "冲突术", "count": 5, "confidence": 0.7})
        phrase_candidates = [
            {"text": "【姓名：韩绝】", "count": 4, "confidence": 0.8},
            {"text": "【修为：无】", "count": 4, "confidence": 0.8},
        ]
        data = {
            "meta": {
                "project_slug": "han-jue",
                "chapter_id": chapter_id,
                "source_sha256": f"sha-{chapter_no}",
                "provider": "ltp_server",
                "provider_version": "fake-ltp",
                "heuristics_version": "mvp5e-v1",
                "degraded": False,
            },
            "sentences": [
                {
                    "sentence_id": "s1",
                    "text": "韩绝进入玉清宗。",
                    "tokens": [
                        {"text": "韩绝", "provider_pos": "nh", "norm_pos": "name"},
                        {"text": "进入", "provider_pos": "v", "norm_pos": "verb"},
                        {"text": "玉清宗", "provider_pos": "ni", "norm_pos": "noun"},
                    ],
                    "ner_tags": ["S-Nh", "O", "S-Ni"],
                }
            ],
            "chapter_candidates": {
                "entity_candidates": entity_candidates,
                "term_candidates": term_candidates,
                "phrase_candidates": phrase_candidates,
            },
        }
        artifact = root / f"{chapter_no}.ltp.json"
        artifact.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        chapters.append(
            {
                "chapter_id": chapter_id,
                "chapter_no": chapter_no,
                "source_sha256": f"sha-{chapter_no}",
                "provider": "ltp_server",
                "provider_version": "fake-ltp",
                "heuristics_version": "mvp5e-v1",
                "degraded": False,
                "sentence_count": 1,
                "token_count": 3,
                "artifact_path": str(artifact),
            }
        )
    manifest = {
        "project_slug": "han-jue",
        "coverage_count": 2,
        "degraded_chapter_count": 0,
        "sentence_count": 2,
        "token_count": 6,
        "chapters": chapters,
        "sidecar_status": {"healthy": True, "degraded": False, "provider": "ltp_server"},
    }
    (root / "nlp_cache_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return workspace


def _prepare_and_build(workspace):
    prepared = prepare_dictionary_run(workspace, project_slug="han-jue", chapters="1-2")
    built = build_dictionary_run(
        workspace,
        project_slug="han-jue",
        run=prepared["run_dir"],
        resume=True,
    )
    return prepared, built


def test_dict_prepare_creates_manifest_and_chunk_plan(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)

    result = prepare_dictionary_run(workspace, project_slug="han-jue", chapters="1-2")

    assert Path(result["manifest_path"]).exists()
    assert Path(result["chunk_plan_path"]).exists()
    assert result["chunk_count"] == 2


def test_dict_build_reads_nlp_cache_filters_noise_and_dedups(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    _prepared, built = _prepare_and_build(workspace)
    candidates = [
        json.loads(line)
        for line in Path(built["candidates_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert built["candidate_count"] > 0
    assert built["candidate_counts_by_type"]["name"] >= 1
    assert Path(built["dictionary_review_path"]).exists()
    assert Path(built["human_review_path"]).exists()
    by_source = {row["source_text"]: row for row in candidates}
    assert by_source["------------"]["confidence_json"]["group"] == "likely_reject"
    assert by_source["章"]["confidence_json"]["group"] == "likely_reject"
    assert by_source["韩绝不"]["confidence_json"]["group"] == "likely_reject"
    assert by_source["邢红"]["confidence_json"]["group"] == "likely_reject"
    assert by_source["玉清宗"]["evidence_count"] > 1
    dedup = json.loads((Path(built["run_dir"]) / "candidate_dedup_report.json").read_text(encoding="utf-8"))
    assert dedup["merged_duplicate_count"] > 0


def test_conflicts_are_detected_from_ambiguous_memory_targets(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path, include_conflict=True)
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        source_key="冲突术",
        target_text="Thuật A",
        confidence_score=0.8,
    )
    create_memory_item(
        workspace,
        memory_type="term",
        status="active",
        source_key="冲突术",
        target_text="Thuật B",
        confidence_score=0.8,
    )

    _prepared, built = _prepare_and_build(workspace)
    conflicts = json.loads((Path(built["run_dir"]) / "candidate_conflicts.json").read_text(encoding="utf-8"))

    assert conflicts["conflict_count"] >= 1


def test_review_package_is_created(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    prepared, _built = _prepare_and_build(workspace)

    review = review_dictionary_run(workspace, project_slug="han-jue", run=prepared["run_dir"])

    assert Path(review["human_review_path"], "human_review_summary.md").exists()
    assert Path(review["human_review_path"], "candidate_review_table.csv").exists()


def test_approve_reject_export_and_inspect_do_not_create_memory(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    prepared, built = _prepare_and_build(workspace)
    candidates = [
        json.loads(line)
        for line in Path(built["candidates_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    approved_id = next(row["id"] for row in candidates if row["source_text"] == "玉清宗")
    rejected_id = next(row["id"] for row in candidates if row["source_text"] == "没有")
    with sqlite3.connect(workspace.db_path) as conn:
        before_memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]

    approved = approve_dictionary_candidates(
        workspace,
        project_slug="han-jue",
        run=prepared["run_dir"],
        candidate_ids=approved_id,
        reviewer="human",
    )
    rejected = reject_dictionary_candidates(
        workspace,
        project_slug="han-jue",
        run=prepared["run_dir"],
        candidate_ids=rejected_id,
        reason="generic noise",
    )
    exported = export_project_dictionary(workspace, project_slug="han-jue")
    inspected = inspect_dictionary_hits(
        workspace,
        project_slug="han-jue",
        source_text="韩绝进入玉清宗。",
    )
    irrelevant = inspect_dictionary_hits(
        workspace,
        project_slug="han-jue",
        source_text="这里没有宗门名称。",
    )
    with sqlite3.connect(workspace.db_path) as conn:
        after_memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]

    assert approved["updated_candidate_ids"] == [approved_id]
    assert rejected["updated_candidate_ids"] == [rejected_id]
    assert exported["entry_count"] == 1
    assert inspected["hit_count"] == 1
    assert inspected["hits"][0]["source_text"] == "玉清宗"
    assert irrelevant["hit_count"] == 0
    assert before_memory_count == after_memory_count


def test_dict_cli_commands_exist(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    result = runner.invoke(app, ["--workspace", str(workspace.path), "dict", "--help"])

    assert result.exit_code == 0, result.output
    for command in ("prepare", "build", "review", "approve", "reject", "export", "status", "inspect"):
        assert command in result.output


def test_dict_prepare_build_review_cli_flow(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    prepared = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "dict",
            "prepare",
            "--project",
            "han-jue",
            "--chapters",
            "1-2",
            "--json",
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    run_dir = json.loads(prepared.output)["data"]["run_dir"]
    built = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "dict",
            "build",
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--resume",
            "--json",
        ],
    )
    reviewed = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "dict",
            "review",
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--json",
        ],
    )

    assert built.exit_code == 0, built.output
    assert reviewed.exit_code == 0, reviewed.output


def test_dictionary_status_counts_pending_and_approved(tmp_path: Path) -> None:
    workspace = _workspace_with_ltp_cache(tmp_path)
    prepared, built = _prepare_and_build(workspace)
    candidates = [
        json.loads(line)
        for line in Path(built["candidates_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidate_id = next(row["id"] for row in candidates if row["source_text"] == "韩绝")
    approve_dictionary_candidates(
        workspace,
        project_slug="han-jue",
        run=prepared["run_dir"],
        candidate_ids=candidate_id,
    )

    status = dictionary_status(workspace, project_slug="han-jue")

    assert status["approved_entry_count"] == 1
    assert status["pending_candidate_count"] >= 1
    assert status["last_run"]["id"] == prepared["dict_run_id"]


def test_production_translation_prompt_behavior_is_unchanged() -> None:
    source = inspect.getsource(production_translation)

    assert "retrieve_dictionary_hits" not in source
    assert "project_dictionary_entries" not in source
