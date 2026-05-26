from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def init_workspace(tmp_path: Path, monkeypatch) -> Path:
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
    return workspace


def create_memory(
    workspace: Path,
    *,
    memory_type: str,
    source_key: str,
    target_text: str,
    value: dict | None = None,
    rules: dict | None = None,
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
            "active",
            "--layer",
            "learning_candidate",
            "--project",
            "han-jue",
            "--source-key",
            source_key,
            "--target-text",
            target_text,
            "--value-json",
            json.dumps(value or {}, ensure_ascii=False),
            "--rules-json",
            json.dumps(rules or {}, ensure_ascii=False),
            "--confidence-score",
            "0.8",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]["item"]


def make_fake_validation_run(workspace: Path, approved_memory: list[dict]) -> Path:
    run_dir = workspace / "artifacts" / "approved_memory_validation" / "fake_mvp5d5_validation"
    run_dir.mkdir(parents=True, exist_ok=True)
    samples = {
        "samples": [
            {
                "sample_id": "sample_1",
                "chapter_id": 1,
                "source_text": "韩绝在玉清宗查看灵根资质，点击开始游戏人生，又继续摇骰子。",
                "target_text": "Hàn Tuyệt ở Ngọc Thanh Tông xem Linh căn tư chất, ấn bắt đầu du hí nhân sinh, rồi tiếp tục lắc xúc xắc.",
            },
            {
                "sample_id": "sample_2",
                "chapter_id": 2,
                "source_text": "青冥魔教来袭，韩绝回到玉幽峰，想到大燕和曦璇仙子。",
                "target_text": "Thanh Minh ma giáo kéo tới, Hàn Tuyệt trở về Ngọc U phong, nghĩ đến Đại Yến và Hi Tuyền tiên tử.",
            },
        ]
    }
    (run_dir / "selected_samples.json").write_text(
        json.dumps(samples, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    round_results = [
        {
            "round": 1,
            "baseline_score": 80,
            "memory_score": 82,
            "score_delta": 2,
            "terminology_error_delta": 1,
            "style_drift_delta": 0,
            "formatting_error_delta": 0,
            "sample_deltas": [
                {
                    "sample_id": "sample_1",
                    "chapter_id": 1,
                    "baseline_score": 78,
                    "memory_score": 81,
                    "delta": 3,
                    "baseline_ratio": 1.1,
                    "memory_ratio": 1.0,
                },
                {
                    "sample_id": "sample_2",
                    "chapter_id": 2,
                    "baseline_score": 82,
                    "memory_score": 83,
                    "delta": 1,
                    "baseline_ratio": 1.0,
                    "memory_ratio": 0.98,
                },
            ],
        },
        {
            "round": 2,
            "baseline_score": 81,
            "memory_score": 82,
            "score_delta": 1,
            "terminology_error_delta": 1,
            "style_drift_delta": 0,
            "formatting_error_delta": 0,
            "sample_deltas": [
                {
                    "sample_id": "sample_1",
                    "chapter_id": 1,
                    "baseline_score": 80,
                    "memory_score": 82,
                    "delta": 2,
                    "baseline_ratio": 1.05,
                    "memory_ratio": 1.0,
                },
                {
                    "sample_id": "sample_2",
                    "chapter_id": 2,
                    "baseline_score": 82,
                    "memory_score": 82,
                    "delta": 0,
                    "baseline_ratio": 1.0,
                    "memory_ratio": 1.0,
                },
            ],
        },
    ]
    summary = {
        "schema_version": "approved_memory_validation_summary_v1",
        "validation_run_id": run_dir.name,
        "project_slug": "han-jue",
        "chapters": [1, 2],
        "provider": "mock",
        "model": "mock",
        "approved_memory_ids": [item["id"] for item in approved_memory],
        "round_results": round_results,
        "final_decision": "FAIL",
        "reason": "minimum_improvement_not_reached",
    }
    (run_dir / "final_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (run_dir / "validation_job_state.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (run_dir / "approved_memory_used.json").write_text(
        json.dumps({"items": approved_memory}, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    for round_no in (1, 2):
        round_dir = run_dir / f"round_{round_no}"
        (round_dir / "baseline_outputs" / "sample_1").mkdir(parents=True, exist_ok=True)
        (round_dir / "memory_outputs" / "sample_1").mkdir(parents=True, exist_ok=True)
        (round_dir / "baseline_outputs" / "sample_2").mkdir(parents=True, exist_ok=True)
        (round_dir / "memory_outputs" / "sample_2").mkdir(parents=True, exist_ok=True)
        (round_dir / "baseline_outputs" / "sample_1" / "mock_final.txt").write_text(
            "Hàn Tuyệt xem Tư chất linh căn, bắt đầu nhân sinh game, rồi ném xúc xắc.",
            encoding="utf-8",
        )
        (round_dir / "memory_outputs" / "sample_1" / "mock_final.txt").write_text(
            "Hàn Tuyệt xem Linh căn tư chất, bắt đầu du hí nhân sinh, rồi lắc xúc xắc.",
            encoding="utf-8",
        )
        (round_dir / "baseline_outputs" / "sample_2" / "mock_final.txt").write_text(
            "Thanh Minh Ma Giáo tới, Hàn Tuyệt về đỉnh Ngọc U, nghĩ đến Đại Yên.",
            encoding="utf-8",
        )
        (round_dir / "memory_outputs" / "sample_2" / "mock_final.txt").write_text(
            "Thanh Minh ma giáo tới, Hàn Tuyệt về Ngọc U phong, nghĩ đến Đại Yến.",
            encoding="utf-8",
        )
        (round_dir / "score_delta.json").write_text(
            json.dumps(round_results[round_no - 1], ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    return run_dir


def make_fake_chapter_10_regression_run(workspace: Path, approved_memory: list[dict]) -> Path:
    run_dir = workspace / "artifacts" / "approved_memory_validation" / "fake_mvp5d7_regression"
    run_dir.mkdir(parents=True, exist_ok=True)
    sample = {
        "sample_id": "sample_10",
        "chapter_id": 10,
        "source_text": (
            "韩绝想到雷灵池。\n\n看来还是得利用这些灵池。\n\n"
            "玉幽峰忽然响起钟声。\n\n曦璇仙子要召见所有弟子。"
        ),
        "target_text": (
            "Hàn Tuyệt nghĩ đến Lôi Linh Trì.\n\n"
            "Xem ra hắn vẫn phải lợi dụng cái linh trì này.\n\n"
            "Trên Ngọc U phong bỗng vang chuông.\n\n"
            "Hi Tuyền tiên tử muốn triệu kiến toàn bộ đệ tử."
        ),
    }
    (run_dir / "selected_samples.json").write_text(
        json.dumps({"samples": [sample]}, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    delta = {
        "round": 2,
        "baseline_score": 90,
        "memory_score": 83,
        "score_delta": -7,
        "sample_deltas": [
            {
                "sample_id": "sample_10",
                "chapter_id": 10,
                "baseline_score": 90,
                "memory_score": 83,
                "delta": -7,
                "baseline_ratio": 1.05,
                "memory_ratio": 1.32,
            }
        ],
        "per_chapter_deltas": [
            {
                "sample_id": "sample_10",
                "chapter_id": 10,
                "baseline_score": 90,
                "memory_score": 83,
                "delta": -7,
                "baseline_ratio": 1.05,
                "memory_ratio": 1.32,
            }
        ],
        "regressions_over_3": [{"sample_id": "sample_10", "chapter_id": 10, "delta": -7}],
        "severe_flags": [],
    }
    summary = {
        "schema_version": "approved_memory_validation_summary_v1",
        "validation_run_id": run_dir.name,
        "project_slug": "han-jue",
        "chapters": [10],
        "provider": "mock",
        "model": "mock",
        "approved_memory_ids": [item["id"] for item in approved_memory],
        "round_results": [delta],
        "final_decision": "FAIL",
        "reason": "per_chapter_regression_over_3",
    }
    (run_dir / "final_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (run_dir / "validation_job_state.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (run_dir / "approved_memory_used.json").write_text(
        json.dumps({"items": approved_memory}, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    round_dir = run_dir / "round_2"
    (round_dir / "baseline_outputs" / "sample_10").mkdir(parents=True, exist_ok=True)
    (round_dir / "memory_outputs" / "sample_10").mkdir(parents=True, exist_ok=True)
    (round_dir / "baseline_outputs" / "sample_10" / "mock_final.txt").write_text(
        "Hàn Tuyệt nghĩ đến Lôi Linh Trì. Xem ra vẫn phải tận dụng những linh trì này. "
        "Trên Ngọc U phong vang chuông. Hi Tuyền tiên tử muốn triệu kiến đệ tử.",
        encoding="utf-8",
    )
    (round_dir / "memory_outputs" / "sample_10" / "mock_final.txt").write_text(
        "Hàn Tuyệt nghĩ đến Lôi Linh Trì. Xem ra vẫn phải tận dụng mấy linh trì này. "
        "Có linh trì hỗ trợ, tốc độ tu luyện của hắn mới có thể kéo lên. "
        "Trên Ngọc U phong vang chuông. Hi Tuyền tiên tử muốn triệu kiến đệ tử.",
        encoding="utf-8",
    )
    (round_dir / "score_delta.json").write_text(
        json.dumps(delta, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return run_dir


def test_ablate_approved_memory_creates_matrix_and_classifications(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    term = create_memory(
        workspace,
        memory_type="term",
        source_key="灵根资质",
        target_text="Linh căn tư chất",
        value={"candidate_type": "term_memory", "source_pattern": "灵根资质"},
        rules={"preferred_target": "Linh căn tư chất", "forbidden_variants": ["Tư chất linh căn"]},
    )
    name = create_memory(
        workspace,
        memory_type="name",
        source_key="玉清宗",
        target_text="Ngọc Thanh Tông",
        value={"candidate_type": "name_memory", "source_pattern": "玉清宗"},
        rules={"preferred_target": "Ngọc Thanh Tông", "forbidden_variants": ["Ngọc Thanh tông"]},
    )
    run_dir = make_fake_validation_run(workspace, [term, name])

    result = runner.invoke(
        app,
        [
            "learn",
            "ablate-approved-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    ablation_dir = Path(data["run_dir"])
    assert (ablation_dir / "ablation_matrix.json").exists()
    assert (ablation_dir / "candidate_impact_report.json").exists()
    matrix = json.loads((ablation_dir / "ablation_matrix.json").read_text(encoding="utf-8"))
    assert any(row["mode"].startswith("all_minus:") for row in matrix["rows"])
    impact = json.loads((ablation_dir / "candidate_impact_report.json").read_text(encoding="utf-8"))
    assert {row["classification"] for row in impact["candidates"]}


def test_mine_memory_candidates_dedups_conflicts_and_keeps_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    duplicate = create_memory(
        workspace,
        memory_type="name",
        source_key="韩绝",
        target_text="Hàn Tuyệt",
        value={"candidate_type": "name_memory", "source_pattern": "韩绝"},
    )
    conflict = create_memory(
        workspace,
        memory_type="name",
        source_key="大燕",
        target_text="Đại Yên",
        value={"candidate_type": "name_memory", "source_pattern": "大燕"},
    )
    run_dir = make_fake_validation_run(workspace, [duplicate, conflict])

    result = runner.invoke(
        app,
        [
            "learn",
            "mine-memory-candidates",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    mining_dir = Path(data["run_dir"])
    assert (mining_dir / "mined_memory_candidates.jsonl").exists()
    assert (mining_dir / "human_review" / "human_review_summary.md").exists()
    rows = [
        json.loads(line)
        for line in (mining_dir / "mined_memory_candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert all(row["status"] == "pending_review" for row in rows)
    assert any(row["source_pattern"] == "青冥魔教" for row in rows)
    dedup = json.loads((mining_dir / "candidate_dedup_report.json").read_text(encoding="utf-8"))
    conflicts = json.loads((mining_dir / "candidate_conflicts.json").read_text(encoding="utf-8"))
    assert dedup["duplicate_or_merged_count"] >= 1
    assert conflicts["conflict_count"] >= 1


def test_simulate_memory_bundle_does_not_activate_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    active = create_memory(
        workspace,
        memory_type="term",
        source_key="灵根资质",
        target_text="Linh căn tư chất",
        value={"candidate_type": "term_memory", "source_pattern": "灵根资质"},
    )
    run_dir = make_fake_validation_run(workspace, [active])
    mined = runner.invoke(
        app,
        [
            "learn",
            "mine-memory-candidates",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--json",
        ],
    )
    assert mined.exit_code == 0, mined.output
    mining_dir = Path(parse_json(mined.output)["data"]["run_dir"])

    before = runner.invoke(
        app,
        ["memory", "list", "--workspace", str(workspace), "--status", "active", "--json"],
    )
    assert before.exit_code == 0, before.output
    before_count = len(parse_json(before.output)["data"]["items"])

    result = runner.invoke(
        app,
        [
            "learn",
            "simulate-memory-bundle",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--candidate-run",
            str(mining_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    sim_dir = Path(data["run_dir"])
    assert data["memory_activation_performed"] is False
    assert (sim_dir / "simulated_bundle.json").exists()
    assert (sim_dir / "simulated_score_delta.json").exists()
    after = runner.invoke(
        app,
        ["memory", "list", "--workspace", str(workspace), "--status", "active", "--json"],
    )
    assert after.exit_code == 0, after.output
    assert len(parse_json(after.output)["data"]["items"]) == before_count


def test_approve_mined_memory_candidates_creates_active_memory_and_preserves_unselected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    active = create_memory(
        workspace,
        memory_type="term",
        source_key="灵根资质",
        target_text="Linh căn tư chất",
        value={"candidate_type": "term_memory", "source_pattern": "灵根资质"},
    )
    run_dir = make_fake_validation_run(workspace, [active])
    mined = runner.invoke(
        app,
        [
            "learn",
            "mine-memory-candidates",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--json",
        ],
    )
    assert mined.exit_code == 0, mined.output
    mining_dir = Path(parse_json(mined.output)["data"]["run_dir"])
    candidates = [
        json.loads(line)
        for line in (mining_dir / "mined_memory_candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_ids = [candidate["candidate_id"] for candidate in candidates[:2]]

    approved = runner.invoke(
        app,
        [
            "learn",
            "approve-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--run",
            str(mining_dir),
            "--candidate-ids",
            ",".join(selected_ids),
            "--json",
        ],
    )

    assert approved.exit_code == 0, approved.output
    data = parse_json(approved.output)["data"]
    assert data["updated_candidate_ids"] == selected_ids
    assert len(data["created_memory_item_ids"]) == 2
    assert (mining_dir / "mined_memory_approval.json").exists()
    assert (mining_dir / "mined_memory_approval.md").exists()
    updated = [
        json.loads(line)
        for line in (mining_dir / "mined_memory_candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected = [candidate for candidate in updated if candidate["candidate_id"] in selected_ids]
    unselected = [candidate for candidate in updated if candidate["candidate_id"] not in selected_ids]
    assert all(candidate["status"] == "active" for candidate in selected)
    assert all(candidate["review_status"] == "approved_by_human" for candidate in selected)
    assert all(candidate["memory_item_id"] for candidate in selected)
    assert all(candidate["status"] == "pending_review" for candidate in unselected)

    active_items = runner.invoke(
        app,
        ["memory", "list", "--workspace", str(workspace), "--status", "active", "--json"],
    )
    assert active_items.exit_code == 0, active_items.output
    items = parse_json(active_items.output)["data"]["items"]
    created_ids = set(data["created_memory_item_ids"])
    assert created_ids.issubset({item["id"] for item in items})


def test_memory_regression_diagnose_ablate_and_rollback_harmful_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    harmful = create_memory(
        workspace,
        memory_type="term",
        source_key="雷灵池",
        target_text="Lôi Linh Trì",
        value={
            "candidate_id": "candidate_c8e5a720bf1b24d0d2d2f69d",
            "candidate_type": "term_memory",
            "source_pattern": "雷灵池",
            "preferred_target": "Lôi Linh Trì",
            "mining_run_id": "fake_mining",
        },
        rules={"preferred_target": "Lôi Linh Trì", "forbidden_variants": ["Lôi linh trì"]},
    )
    safe = create_memory(
        workspace,
        memory_type="style",
        source_key="技能",
        target_text="skills",
        value={
            "candidate_id": "candidate_9ac6ad9ee889e2236a0cd82d",
            "candidate_type": "formatting_rule_memory",
            "source_pattern": "技能",
            "preferred_target": "skills",
            "mining_run_id": "fake_mining",
        },
        rules={"preferred_target": "skills", "forbidden_variants": ["kỹ năng"]},
    )
    run_dir = make_fake_chapter_10_regression_run(workspace, [harmful, safe])

    diagnosed = runner.invoke(
        app,
        [
            "learn",
            "diagnose-memory-regression",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--json",
        ],
    )
    assert diagnosed.exit_code == 0, diagnosed.output
    diagnostic = parse_json(diagnosed.output)["data"]
    assert diagnostic["harmful_candidate_ids"] == ["candidate_c8e5a720bf1b24d0d2d2f69d"]
    diagnostic_dir = Path(diagnostic["run_dir"])
    assert (diagnostic_dir / "memory_trigger_trace.json").exists()
    assert (diagnostic_dir / "prompt_context_diff.md").exists()

    ablated = runner.invoke(
        app,
        [
            "learn",
            "ablate-memory-regression",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--candidate-ids",
            "candidate_c8e5a720bf1b24d0d2d2f69d,candidate_9ac6ad9ee889e2236a0cd82d",
            "--json",
        ],
    )
    assert ablated.exit_code == 0, ablated.output
    ablation = parse_json(ablated.output)["data"]
    assert ablation["candidate_classifications"]["candidate_c8e5a720bf1b24d0d2d2f69d"] == "harmful"
    assert "candidate_c8e5a720bf1b24d0d2d2f69d" in ablation["harmful_candidate_ids"]
    ablation_dir = Path(ablation["run_dir"])
    assert (ablation_dir / "all_minus_one_report.json").exists()
    assert (ablation_dir / "safe_subset_recommendation.json").exists()

    rolled_back = runner.invoke(
        app,
        [
            "learn",
            "rollback-approved-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--candidate-ids",
            "candidate_c8e5a720bf1b24d0d2d2f69d",
            "--reason",
            "chapter 10 regression evidence",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--json",
        ],
    )
    assert rolled_back.exit_code == 0, rolled_back.output
    rollback = parse_json(rolled_back.output)["data"]
    assert rollback["updated_candidate_ids"] == ["candidate_c8e5a720bf1b24d0d2d2f69d"]
    rollback_dir = Path(rollback["run_dir"])
    assert (rollback_dir / "memory_rollback_audit.json").exists()
    assert (rollback_dir / "memory_rollback_audit.md").exists()
    assert (rollback_dir / "active_memory_after_rollback.json").exists()

    active_items = runner.invoke(
        app,
        ["memory", "list", "--workspace", str(workspace), "--status", "active", "--json"],
    )
    assert active_items.exit_code == 0, active_items.output
    active_ids = {item["id"] for item in parse_json(active_items.output)["data"]["items"]}
    assert harmful["id"] not in active_ids
    assert safe["id"] in active_ids


def test_active_memory_risk_review_recommends_risky_mined_candidate_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    direct_harmful = create_memory(
        workspace,
        memory_type="term",
        source_key="雷灵池",
        target_text="Lôi Linh Trì",
        value={
            "candidate_id": "candidate_c8e5a720bf1b24d0d2d2f69d",
            "candidate_type": "term_memory",
            "source_pattern": "雷灵池",
            "preferred_target": "Lôi Linh Trì",
            "mining_run_id": "fake_mining",
        },
    )
    combination_risk = create_memory(
        workspace,
        memory_type="term",
        source_key="玉幽峰",
        target_text="Ngọc U phong",
        value={
            "candidate_id": "candidate_a4d0439dc85a16a2589487f8",
            "candidate_type": "term_memory",
            "source_pattern": "玉幽峰",
            "preferred_target": "Ngọc U phong",
            "mining_run_id": "fake_mining",
        },
    )
    insufficient = create_memory(
        workspace,
        memory_type="style",
        source_key="技能",
        target_text="skills",
        value={
            "candidate_id": "candidate_9ac6ad9ee889e2236a0cd82d",
            "candidate_type": "formatting_rule_memory",
            "source_pattern": "技能",
            "preferred_target": "skills",
            "mining_run_id": "fake_mining",
        },
    )
    run_dir = make_fake_chapter_10_regression_run(
        workspace,
        [direct_harmful, combination_risk, insufficient],
    )
    ablated = runner.invoke(
        app,
        [
            "learn",
            "ablate-memory-regression",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--candidate-ids",
            ",".join(
                [
                    "candidate_c8e5a720bf1b24d0d2d2f69d",
                    "candidate_a4d0439dc85a16a2589487f8",
                    "candidate_9ac6ad9ee889e2236a0cd82d",
                ]
            ),
            "--json",
        ],
    )
    assert ablated.exit_code == 0, ablated.output

    reviewed = runner.invoke(
        app,
        [
            "learn",
            "review-active-memory-risk",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--json",
        ],
    )

    assert reviewed.exit_code == 0, reviewed.output
    review = parse_json(reviewed.output)["data"]
    review_dir = Path(review["run_dir"])
    assert (review_dir / "active_memory_risk_review.json").exists()
    assert (review_dir / "negative_evidence_report.md").exists()
    assert (review_dir / "remaining_mined_candidate_status.md").exists()
    recommended = set(review["rollback_recommended_candidate_ids"])
    assert "candidate_c8e5a720bf1b24d0d2d2f69d" in recommended
    assert "candidate_a4d0439dc85a16a2589487f8" in recommended
    assert "candidate_9ac6ad9ee889e2236a0cd82d" in recommended


def test_original_memory_regression_diagnose_ablate_and_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    term = create_memory(
        workspace,
        memory_type="term",
        source_key="雷灵池",
        target_text="Lôi Linh Trì",
        value={"learning_run_id": "mvp5c"},
    )
    unrelated = create_memory(
        workspace,
        memory_type="correction",
        source_key="游戏人生",
        target_text="du hí nhân sinh",
        value={"learning_run_id": "mvp5c"},
    )
    run_dir = make_fake_chapter_10_regression_run(workspace, [term, unrelated])

    diagnosed = runner.invoke(
        app,
        [
            "learn",
            "diagnose-original-memory-regression",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--json",
        ],
    )
    assert diagnosed.exit_code == 0, diagnosed.output
    diagnostic = parse_json(diagnosed.output)["data"]
    diagnostic_dir = Path(diagnostic["run_dir"])
    assert diagnostic["memory_classifications"][term["id"]] == "harmful"
    assert diagnostic["memory_classifications"][unrelated["id"]] == "context_too_broad"
    assert (diagnostic_dir / "original_memory_trigger_trace.json").exists()
    assert (diagnostic_dir / "original_memory_prompt_context_diff.md").exists()

    ablated = runner.invoke(
        app,
        [
            "learn",
            "ablate-original-memory-regression",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--memory-ids",
            f"{term['id']},{unrelated['id']}",
            "--json",
        ],
    )
    assert ablated.exit_code == 0, ablated.output
    ablation = parse_json(ablated.output)["data"]
    ablation_dir = Path(ablation["run_dir"])
    assert term["id"] in ablation["harmful_memory_ids"]
    assert unrelated["id"] in ablation["harmful_memory_ids"]
    assert (ablation_dir / "original_memory_all_minus_one_report.json").exists()
    assert (ablation_dir / "original_memory_safe_subset_recommendation.json").exists()

    scoped = runner.invoke(
        app,
        [
            "learn",
            "scope-approved-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--memory-ids",
            term["id"],
            "--reason",
            "chapter 10 original memory regression evidence",
            "--validation-run",
            str(run_dir),
            "--chapter",
            "10",
            "--json",
        ],
    )
    assert scoped.exit_code == 0, scoped.output
    scope_data = parse_json(scoped.output)["data"]
    scope_dir = Path(scope_data["run_dir"])
    assert (scope_dir / "original_memory_scope_audit.json").exists()
    assert (scope_dir / "active_memory_after_original_scope.json").exists()
    active_after = json.loads(
        (scope_dir / "active_memory_after_original_scope.json").read_text(encoding="utf-8")
    )
    scoped_row = next(row for row in active_after["active_memory"] if row["id"] == term["id"])
    assert scoped_row["deprecated_for_validation"] is True

    items = runner.invoke(
        app,
        ["memory", "show", "--workspace", str(workspace), term["id"], "--json"],
    )
    assert items.exit_code == 0, items.output
    shown = parse_json(items.output)["data"]["item"]
    assert shown["value_json"]["validation_status"] == "deprecated_for_validation"
