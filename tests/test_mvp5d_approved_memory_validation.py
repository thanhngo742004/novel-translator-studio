from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nts_core.approved_memory_validation import _chapter_title_target_map
from nts_cli.main import app
from nts_core.eval_harness import detect_truncated_vietnamese


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "raw.txt"
EPUB_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "viettranslated.epub"


def parse_json(output: str) -> dict:
    return json.loads(output)


def init_workspace(tmp_path: Path, monkeypatch, *, active_memory: bool = True) -> Path:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", "--workspace", str(workspace), "--json"]).exit_code == 0
    created = runner.invoke(
        app,
        [
            "project",
            "create",
            "--workspace",
            str(workspace),
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
    assert created.exit_code == 0, created.output
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
                "prompt_version": "mvp5d-test",
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
    if active_memory:
        active = runner.invoke(
            app,
            [
                "memory",
                "create",
                "--workspace",
                str(workspace),
                "--type",
                "term",
                "--status",
                "active",
                "--layer",
                "learning_candidate",
                "--project",
                "han-jue",
                "--source-key",
                "term_source",
                "--target-text",
                "Preferred Term",
                "--confidence-score",
                "0.8",
                "--json",
            ],
        )
        assert active.exit_code == 0, active.output
        pending = runner.invoke(
            app,
            [
                "memory",
                "create",
                "--workspace",
                str(workspace),
                "--type",
                "term",
                "--status",
                "pending",
                "--layer",
                "learning_candidate",
                "--project",
                "han-jue",
                "--source-key",
                "pending_source",
                "--target-text",
                "Pending Term",
                "--confidence-score",
                "0.8",
                "--json",
            ],
        )
        assert pending.exit_code == 0, pending.output
    return workspace


def validate_command(workspace: Path, *, model: str = "mock-eval", extra: list[str] | None = None):
    return runner.invoke(
        app,
        [
            "learn",
            "validate-approved-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--raw",
            str(RAW_PATH),
            "--translated",
            str(EPUB_PATH),
            "--provider",
            "mock",
            "--model",
            model,
            "--fallback-model",
            "mock-eval",
            "--chapters",
            "1-2",
            "--rounds",
            "2",
            "--use-stable-prompt",
            "--resumable",
            *(extra or []),
            "--json",
        ],
    )


def test_validate_approved_memory_pass_requires_two_improving_rounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    result = validate_command(workspace)

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["final_decision"] == "PASS"
    assert data["rounds_completed"] == 2
    assert all(row["score_delta"] > 0 for row in data["round_results"])
    assert (run_dir / "validation_job_state.json").exists()
    assert (run_dir / "round_1" / "baseline_evaluation.json").exists()
    assert (run_dir / "round_1" / "memory_evaluation.json").exists()
    assert (run_dir / "round_2" / "score_delta.json").exists()
    assert (run_dir / "final_validation_summary.md").exists()
    sample_selection = json.loads(
        (run_dir / "approved_memory_validation_sample_selection.json").read_text(
            encoding="utf-8"
        )
    )
    assert sample_selection["selected_chapters"] == [1, 2]


def test_validate_approved_memory_fails_when_only_one_round_improves(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    result = validate_command(workspace, model="mock-one-round-fails")

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["final_decision"] == "FAIL"
    assert data["round_results"][0]["score_delta"] > 0
    assert data["round_results"][1]["score_delta"] == 0


def test_validate_approved_memory_blocks_when_approved_memory_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch, active_memory=False)

    result = validate_command(workspace)

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["final_decision"] == "BLOCKED"
    assert data["last_error"] == "approved_learning_memory_missing"


def test_validate_approved_memory_checkpoint_resume_and_memory_sets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    paused = validate_command(workspace, extra=["--max-real-calls", "2"])
    assert paused.exit_code == 0, paused.output
    paused_data = parse_json(paused.output)["data"]
    run_dir = Path(paused_data["run_dir"])
    assert paused_data["status"] == "paused"
    assert paused_data["can_resume"] is True

    resumed = runner.invoke(
        app,
        [
            "learn",
            "resume-approved-memory-validation",
            "--workspace",
            str(workspace),
            "--run",
            str(run_dir),
            "--max-real-calls",
            "8",
            "--json",
        ],
    )
    assert resumed.exit_code == 0, resumed.output
    resumed_data = parse_json(resumed.output)["data"]
    assert resumed_data["final_decision"] == "PASS"

    used = json.loads((run_dir / "approved_memory_used.json").read_text(encoding="utf-8"))
    excluded = json.loads((run_dir / "baseline_memory_exclusion.json").read_text(encoding="utf-8"))
    assert len(used["items"]) == 1
    assert used["items"][0]["status"] == "active"
    assert excluded["excluded_memory_ids"] == [used["items"][0]["id"]]


def test_validate_approved_memory_snapshot_splits_new_mined_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    mined = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            "term",
            "--status",
            "active",
            "--layer",
            "learning_candidate",
            "--project",
            "han-jue",
            "--source-key",
            "雷灵池",
            "--target-text",
            "Lôi Linh Trì",
            "--value-json",
            json.dumps(
                {
                    "candidate_id": "candidate_test_mined",
                    "candidate_type": "term_memory",
                    "mining_run_id": "test_mining_run",
                    "source_pattern": "雷灵池",
                    "preferred_target": "Lôi Linh Trì",
                    "review_status": "approved_by_human",
                },
                ensure_ascii=False,
            ),
            "--confidence-score",
            "0.9",
            "--json",
        ],
    )
    assert mined.exit_code == 0, mined.output
    pending = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--type",
            "term",
            "--status",
            "pending",
            "--layer",
            "learning_candidate",
            "--project",
            "han-jue",
            "--source-key",
            "pending_mined",
            "--target-text",
            "Pending mined",
            "--value-json",
            json.dumps(
                {
                    "candidate_id": "candidate_pending_mined",
                    "mining_run_id": "test_mining_run",
                },
                ensure_ascii=False,
            ),
            "--confidence-score",
            "0.9",
            "--json",
        ],
    )
    assert pending.exit_code == 0, pending.output

    result = validate_command(workspace, extra=["--dry-run"])

    assert result.exit_code == 0, result.output
    run_dir = Path(parse_json(result.output)["data"]["run_dir"])
    snapshot = json.loads((run_dir / "active_memory_snapshot.json").read_text(encoding="utf-8"))
    context = json.loads((run_dir / "memory_delta_context.json").read_text(encoding="utf-8"))
    excluded = json.loads((run_dir / "baseline_memory_exclusion.json").read_text(encoding="utf-8"))
    mined_row = next(row for row in snapshot["active_memory"] if row.get("candidate_id") == "candidate_test_mined")
    original_row = next(row for row in snapshot["active_memory"] if row.get("source_pattern") == "term_source")
    assert mined_row["origin"] == "MVP5D.5 mining"
    assert mined_row["included_in_baseline_pass"] is False
    assert mined_row["included_in_memory_pass"] is True
    assert original_row["included_in_baseline_pass"] is True
    assert "candidate_test_mined" in context["newly_approved_mined_candidate_ids"]
    assert "candidate_test_mined" in excluded["excluded_candidate_ids"]
    assert all(row.get("candidate_id") != "candidate_pending_mined" for row in snapshot["active_memory"])


def test_validate_approved_memory_status_command(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    result = validate_command(workspace)
    data = parse_json(result.output)["data"]

    status = runner.invoke(
        app,
        [
            "learn",
            "approved-memory-validation-status",
            "--workspace",
            str(workspace),
            "--run",
            data["run_dir"],
            "--json",
        ],
    )

    assert status.exit_code == 0, status.output
    status_data = parse_json(status.output)["data"]
    assert status_data["final_decision"] == "PASS"
    assert status_data["round_results"]


def test_provider_empty_output_blocks_before_scoring(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    fake_eval_run = tmp_path / "fake_eval_run"
    fake_eval_run.mkdir()

    def fake_translate_samples(**kwargs):
        return {
            "run_dir": str(fake_eval_run),
            "outputs": {
                "sample_1": {
                    "mock-eval": {
                        "provider_error": "Provider HTTP error 524: timeout",
                        "provider_error_classification": {
                            "retryable": True,
                            "http_status": 524,
                        },
                        "output_char_count": 0,
                        "verification_after_compression": {
                            "provider_failure_empty_output": True,
                            "reasons": [
                                "provider_error",
                                "provider_failure_empty_output",
                                "provider_retry_exhausted",
                            ],
                        },
                    }
                }
            },
        }

    monkeypatch.setattr(
        "nts_core.approved_memory_validation.translate_samples",
        fake_translate_samples,
    )

    result = validate_command(workspace)

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["final_decision"] == "BLOCKED"
    assert data["can_resume"] is True
    assert "provider_failure" in data["last_error"]
    assert data["round_results"] == []


def test_truncation_detector_allows_headings_and_separators() -> None:
    heading = "------------ Chương 3: Luyện Khí cảnh tầng bảy, sức hút chết tiệt"
    separator = "------------"

    assert detect_truncated_vietnamese(heading, source_text="第3章 炼气境七层，该死的魅力")[
        "is_truncated"
    ] is False
    assert detect_truncated_vietnamese(separator, source_text="第9章 筑基境三层，莫复仇")[
        "is_truncated"
    ] is False
    assert detect_truncated_vietnamese("Click bắt đ")["is_truncated"] is True


def test_replay_approved_memory_validation_reports_cached_failures_without_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    result = validate_command(workspace)
    assert result.exit_code == 0, result.output
    run_dir = Path(parse_json(result.output)["data"]["run_dir"])

    evaluation_path = run_dir / "round_1" / "memory_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    model = evaluation["best_model"]
    sample = evaluation["models"][model]["samples"][0]
    sample["truncated_paragraphs"] = [
        {"paragraph_id": "u001", "reasons": ["missing_terminal_punctuation"]}
    ]
    sample.setdefault("verification_reasons", []).append("paragraph_truncation_detected")
    evaluation_path.write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")

    replay = runner.invoke(
        app,
        [
            "learn",
            "replay-approved-memory-validation",
            "--workspace",
            str(workspace),
            "--run",
            str(run_dir),
            "--json",
        ],
    )

    assert replay.exit_code == 0, replay.output
    data = parse_json(replay.output)["data"]
    assert data["failure_count"] >= 1
    assert (run_dir / "failing_samples_report.json").exists()
    assert (run_dir / "failing_samples_report.md").exists()
    assert (run_dir / "safety_failure_table.csv").exists()
    assert (run_dir / "targeted_failure_report.json").exists()
    assert (run_dir / "targeted_failure_report.md").exists()
    assert (run_dir / "validation_candidate_exclusions.json").exists()
    report = json.loads((run_dir / "failing_samples_report.json").read_text(encoding="utf-8"))
    assert any(row["sample_id"] == sample["sample_id"] for row in report["failures"])
    assert set(report["root_cause_counts"]).issubset(
        {
            "evaluator_false_positive",
            "real_truncation",
            "unsafe_compression_rewrite",
            "unit_merge_boundary_problem",
            "over_strict_micro_unit_budget",
            "formatting/bracket safety issue",
            "missing_diagnostics",
        }
    )
    assert report["failures"][0]["root_cause"] in {
        "evaluator_false_positive",
        "real_truncation",
        "unsafe_compression_rewrite",
        "unit_merge_boundary_problem",
        "over_strict_micro_unit_budget",
        "formatting/bracket safety issue",
        "missing_diagnostics",
    }
    exclusions = json.loads(
        (workspace / "artifacts" / "approved_memory_validation" / "validation_candidate_exclusions.json").read_text(
            encoding="utf-8"
        )
    )
    assert exclusions["exclusions"]
    assert exclusions["exclusions"][0]["validation_purpose"] == "approved_memory_validation"


def test_title_guided_selection_uses_split_epub_chapters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    result = validate_command(workspace, extra=["--chapters", "1-10", "--dry-run"])
    assert result.exit_code == 0, result.output
    run_dir = Path(parse_json(result.output)["data"]["run_dir"])

    resumed = runner.invoke(
        app,
        [
            "learn",
            "resume-approved-memory-validation",
            "--workspace",
            str(workspace),
            "--run",
            str(run_dir),
            "--max-real-calls",
            "0",
            "--json",
        ],
    )

    assert resumed.exit_code == 0, resumed.output
    selection = json.loads(
        (run_dir / "approved_memory_validation_sample_selection.json").read_text(
            encoding="utf-8"
        )
    )
    assert selection["selected_chapters"] == list(range(1, 11))
    assert selection["selected_target_chapters"] == [1, 2, 4, 5, 6, 8, 9, 11, 12, 13]
    assert (run_dir / "selected_validation_units.json").exists()
    assert (run_dir / "selected_validation_units.md").exists()
    assert (run_dir / "unit_candidate_ranking.json").exists()
    assert (run_dir / "unit_candidate_ranking.md").exists()
    units = json.loads((run_dir / "selected_validation_units.json").read_text(encoding="utf-8"))
    assert all(
        sample["validation_unit_safety"]["compression_risk"] == "low"
        for sample in units["samples"]
    )
    ranking = json.loads((run_dir / "unit_candidate_ranking.json").read_text(encoding="utf-8"))
    assert any(not row["accepted"] for row in ranking["candidates"])


def test_explicit_candidate_exclusion_selects_alternate_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    default = validate_command(workspace, extra=["--chapters", "8,10", "--dry-run"])
    assert default.exit_code == 0, default.output
    default_run = Path(parse_json(default.output)["data"]["run_dir"])
    default_resume = runner.invoke(
        app,
        [
            "learn",
            "resume-approved-memory-validation",
            "--workspace",
            str(workspace),
            "--run",
            str(default_run),
            "--max-real-calls",
            "0",
            "--json",
        ],
    )
    assert default_resume.exit_code == 0, default_resume.output
    default_samples = json.loads((default_run / "selected_samples.json").read_text(encoding="utf-8"))[
        "samples"
    ]
    chapter_8_candidate = next(
        sample["block_alignment_candidate_id"]
        for sample in default_samples
        if sample["chapter_id"] == 8
    )

    excluded = validate_command(
        workspace,
        extra=[
            "--chapters",
            "8,10",
            "--dry-run",
            "--exclude-candidate-ids",
            f"8:{chapter_8_candidate}",
        ],
    )
    assert excluded.exit_code == 0, excluded.output
    excluded_run = Path(parse_json(excluded.output)["data"]["run_dir"])
    excluded_resume = runner.invoke(
        app,
        [
            "learn",
            "resume-approved-memory-validation",
            "--workspace",
            str(workspace),
            "--run",
            str(excluded_run),
            "--max-real-calls",
            "0",
            "--json",
        ],
    )
    assert excluded_resume.exit_code == 0, excluded_resume.output
    samples = json.loads((excluded_run / "selected_samples.json").read_text(encoding="utf-8"))[
        "samples"
    ]
    selected_chapter_8 = next(sample for sample in samples if sample["chapter_id"] == 8)
    assert selected_chapter_8["block_alignment_candidate_id"] != chapter_8_candidate
    assert (excluded_run / "chapter_8_window_ablation.json").exists()
    exclusions = json.loads((excluded_run / "excluded_validation_candidates.json").read_text(encoding="utf-8"))
    assert exclusions["used_exclusion_count"] >= 1


def test_diagnose_chapter_alignment_reports_chapter_10_join(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "learn",
            "diagnose-chapter-alignment",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--raw",
            str(RAW_PATH),
            "--translated",
            str(EPUB_PATH),
            "--chapters",
            "1-10",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert (run_dir / "chapter_alignment_diagnostics.json").exists()
    assert (run_dir / "chapter_alignment_diagnostics.md").exists()
    assert (run_dir / "raw_chapter_index.json").exists()
    assert (run_dir / "translated_chapter_index.json").exists()
    assert (run_dir / "chapter_10_alignment_candidates.json").exists()
    assert data["chapter_10_match"]["target_chapter_ids"] == [13, 14]
    assert data["chapter_10_match"]["split_decision"]["should_join"] is True


def test_chapter_matching_falls_back_when_title_differs() -> None:
    raw = [
        {
            "chapter_id": 1,
            "title": "第1章 无题",
            "text": "韩绝在玉清宗修炼灵根。\n\n" * 20,
        }
    ]
    target = [
        {
            "chapter_id": 1,
            "title": "Translator intro",
            "text": "Không liên quan.\n\n" * 20,
        },
        {
            "chapter_id": 2,
            "title": "Không có tiêu đề gốc",
            "text": "Hàn Tuyệt tu luyện linh căn tại Ngọc Thanh Tông.\n\n" * 20,
        },
    ]

    mapping, rows = _chapter_title_target_map(raw, target, match_window=2)

    assert mapping[1] == [2]
    assert rows[0]["status"] == "mapped_by_anchor_fallback"
    assert rows[0]["match_confidence"] >= 0.60


def test_chapter_matching_joins_adjacent_split_section() -> None:
    raw = [
        {
            "chapter_id": 10,
            "title": "第10章 悲惨的好友，大师兄的好感",
            "text": (
                "邢红璇遭遇追杀，好友好感变化。\n\n"
                + ("普通文字。" * 260)
                + "\n\n韩绝继续修炼。\n\n后来他在玉清宗达到炼气境和筑基境。"
            ),
        },
        {
            "chapter_id": 11,
            "title": "第11章 筑基境九层，树妖的机缘",
            "text": "树妖出现，韩绝闭关修炼。",
        },
    ]
    target = [
        {
            "chapter_id": 13,
            "title": "Chương 13: Bạn tốt bi thảm, thiện cảm của đại sư huynh",
            "text": "Hình Hồng Tuyền gặp nạn. Thiện cảm của đại sư huynh thay đổi.",
        },
        {
            "chapter_id": 14,
            "title": "Chương 14: Trúc Cơ cảnh tầng chín, cơ duyên của Thụ Yêu",
            "text": "Hàn Tuyệt tiếp tục tu luyện ở Ngọc Thanh Tông rồi đạt Trúc Cơ.",
        },
    ]

    mapping, rows = _chapter_title_target_map(raw, target, match_window=2)

    assert mapping[10] == [13, 14]
    assert rows[0]["status"] == "mapped_joined_adjacent_split"
    assert rows[0]["split_decision"]["should_join"] is True


def test_chapter_matching_rejects_low_confidence_match() -> None:
    raw = [
        {
            "chapter_id": 1,
            "title": "第1章 无题",
            "text": "完全没有可用锚点。\n\n" * 10,
        }
    ]
    target = [
        {
            "chapter_id": 1,
            "title": "Không liên quan",
            "text": "Một đoạn không có cùng tên riêng hoặc thuật ngữ.\n\n" * 10,
        }
    ]

    mapping, rows = _chapter_title_target_map(raw, target, match_window=1)

    assert mapping == {}
    assert rows[0]["status"] == "unmapped"
