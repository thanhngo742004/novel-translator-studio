from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "raw.txt"
EPUB_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "viettranslated.epub"


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
                "prompt_version": "mvp5c-test",
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
    return workspace


def run_loop(workspace: Path, *extra: str) -> dict:
    result = runner.invoke(
        app,
        [
            "learn",
            "loop",
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
            "mock-eval",
            "--fallback-model",
            "mock-eval",
            "--chapters",
            "1-3",
            "--global-cycles",
            "2",
            "--iterations",
            "2",
            "--repair-iterations",
            "2",
            "--use-stable-prompt",
            "--rollback-harmful-memory",
            "--resumable",
            *extra,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    return parse_json(result.output)["data"]


def test_resumable_learning_job_state_and_checkpoints(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    data = run_loop(workspace)
    run_dir = Path(data["run_dir"])
    state = json.loads((run_dir / "learning_job_state.json").read_text(encoding="utf-8"))

    assert data["final_decision"] == "PASS"
    assert state["reached_iteration_1_evaluate"] is True
    assert state["score_delta"] >= 1.0
    assert (run_dir / "checkpoint_log.jsonl").read_text(encoding="utf-8").strip()
    assert (run_dir / "stage_status.json").exists()
    assert (run_dir / "resume_plan.json").exists()
    assert (run_dir / "api_call_log.jsonl").exists()
    assert (run_dir / "provider_error_log.jsonl").exists()
    assert (run_dir / "cycle_1" / "iteration_1" / "score_delta.json").exists()
    assert "prepare_dataset" in state["completed_stages"]
    assert "cycle_1_iteration_1_evaluate" in state["completed_stages"]


def test_max_real_calls_pauses_and_resume_reaches_iteration_evaluate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    paused = run_loop(workspace, "--max-real-calls", "3")
    run_dir = Path(paused["run_dir"])
    assert paused["status"] == "paused"
    assert paused["reached_iteration_1_evaluate"] is False

    status = runner.invoke(
        app,
        ["learn", "status", "--workspace", str(workspace), "--run", str(run_dir), "--json"],
    )
    assert status.exit_code == 0, status.output
    status_data = parse_json(status.output)["data"]
    assert status_data["can_resume"] is True
    assert "nts learn resume" in status_data["next_command"]

    resumed = runner.invoke(
        app,
        [
            "learn",
            "resume",
            "--workspace",
            str(workspace),
            "--run",
            str(run_dir),
            "--max-real-calls",
            "6",
            "--json",
        ],
    )
    assert resumed.exit_code == 0, resumed.output
    resumed_data = parse_json(resumed.output)["data"]
    assert resumed_data["reached_iteration_1_evaluate"] is True
    assert resumed_data["score_delta"] is not None


def test_jobs_list_and_ablation_report(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)
    data = run_loop(workspace)
    run_dir = Path(data["run_dir"])

    jobs = runner.invoke(
        app,
        ["learn", "jobs", "--workspace", str(workspace), "--project", "han-jue", "--json"],
    )
    assert jobs.exit_code == 0, jobs.output
    assert parse_json(jobs.output)["data"]["jobs"]

    ablation = runner.invoke(
        app,
        ["learn", "ablate-candidates", "--workspace", str(workspace), "--run", str(run_dir), "--json"],
    )
    assert ablation.exit_code == 0, ablation.output
    assert (run_dir / "ablation_report.json").exists()
    assert parse_json(ablation.output)["data"]["group_count"] > 0


def test_resumable_loop_fallback_after_provider_failures(tmp_path: Path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "learn",
            "loop",
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
            "mock-fail-primary",
            "--fallback-model",
            "mock-eval",
            "--chapters",
            "1-3",
            "--global-cycles",
            "1",
            "--iterations",
            "1",
            "--repair-iterations",
            "1",
            "--use-stable-prompt",
            "--resumable",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["fallback_model_used"] is True
    assert data["final_decision"] == "PASS"
    switch_log = json.loads((Path(data["run_dir"]) / "model_switch_log.json").read_text(encoding="utf-8"))
    assert switch_log["entries"]


def test_resumable_regression_rolls_back_and_does_not_activate_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "learn",
            "loop",
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
            "mock-regress",
            "--fallback-model",
            "mock-regress",
            "--chapters",
            "1-3",
            "--global-cycles",
            "1",
            "--iterations",
            "1",
            "--repair-iterations",
            "2",
            "--use-stable-prompt",
            "--rollback-harmful-memory",
            "--resumable",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["final_decision"] == "FAIL"
    assert data["reached_iteration_1_evaluate"] is True
    assert data["rollback_count"] > 0
    assert json.loads((run_dir / "rollback_log.json").read_text(encoding="utf-8"))["entries"]
    assert json.loads((run_dir / "ablation_report.json").read_text(encoding="utf-8"))["groups"]

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE status = 'active'"
        ).fetchone()[0]
    assert active_count == 0
