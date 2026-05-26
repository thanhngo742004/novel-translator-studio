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
