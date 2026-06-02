from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "raw.txt"
EPUB_PATH = REPO_ROOT / "test_data" / "translation_eval" / "han_jue" / "viettranslated.epub"


def parse_json(output: str) -> dict:
    return json.loads(output)


def test_learning_prompt_strips_cross_project_stable_glossary() -> None:
    from nts_core.learning_loop import _stable_prompt_for_learning
    from nts_core.stable_prompts import StablePromptRecord

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
            "Temporary style profile: Han profile\n"
            "Required glossary mappings when the source term appears: [{\"source\": \"韩绝\", \"target\": \"Hàn Tuyệt\"}]\n"
            "Return JSON only"
        ),
        prompt_path="stable.md",
        metadata_path="stable.json",
        created_at=None,
    )

    prompt = _stable_prompt_for_learning(record, project_slug="tien-nghich")

    assert "Stable body" in prompt
    assert "Production learning evaluation mode:" in prompt
    assert "韩绝" not in prompt
    assert "Temporary style profile" not in prompt


def init_learning_workspace(tmp_path: Path, monkeypatch, *, approved: bool = True) -> Path:
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
    run_dir = workspace / "artifacts" / "evaluations" / "stable_run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stable_prompt.md").write_text(
        "# Stable Prompt\n\n```text\nTranslate Chinese into concise Vietnamese webnovel prose.\n```\n",
        encoding="utf-8",
    )
    (run_dir / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "prompt_id": "stable_test",
                "prompt_version": "mvp5b-test",
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
    if approved:
        (run_dir / "stable_prompt_approval.json").write_text(
            json.dumps({"decision": "approved", "reviewer": "pytest"}, sort_keys=True),
            encoding="utf-8",
        )
    return workspace


def prepare_dataset(workspace: Path) -> dict:
    prepared = runner.invoke(
        app,
        [
            "learn",
            "prepare-parallel",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--raw",
            str(RAW_PATH),
            "--translated",
            str(EPUB_PATH),
            "--chapters",
            "1-3",
            "--json",
        ],
    )
    assert prepared.exit_code == 0, prepared.output
    return parse_json(prepared.output)["data"]


def eval_and_extract(workspace: Path) -> tuple[dict, dict]:
    prepared = prepare_dataset(workspace)
    evaluated = runner.invoke(
        app,
        [
            "learn",
            "eval-production",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--chapters",
            "1-3",
            "--provider",
            "mock",
            "--model",
            "mock-eval",
            "--use-stable-prompt",
            "--json",
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    extracted = runner.invoke(
        app,
        [
            "learn",
            "extract-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--from-run",
            prepared["run_dir"],
            "--json",
        ],
    )
    assert extracted.exit_code == 0, extracted.output
    return parse_json(evaluated.output)["data"], parse_json(extracted.output)["data"]


def test_prepare_parallel_learning_dataset_creates_artifacts(tmp_path: Path, monkeypatch) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)

    data = prepare_dataset(workspace)
    run_dir = Path(data["run_dir"])

    assert data["sample_count"] == 3
    assert data["alignment_quality_min"] >= 0.7
    assert (run_dir / "learning_manifest.json").exists()
    assert (run_dir / "selected_samples.json").exists()
    assert (run_dir / "block_alignment_report.json").exists()


def test_eval_production_uses_approved_stable_prompt_and_blocks_unapproved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch, approved=False)
    prepare_dataset(workspace)

    blocked = runner.invoke(
        app,
        [
            "learn",
            "eval-production",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--provider",
            "mock",
            "--model",
            "mock-eval",
            "--use-stable-prompt",
            "--json",
        ],
    )

    assert blocked.exit_code == 4
    assert parse_json(blocked.output)["error"]["code"] == "STABLE_PROMPT_BLOCKED"


def test_eval_production_and_extract_memory_create_pending_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)

    evaluated, extracted = eval_and_extract(workspace)
    run_dir = Path(extracted["run_dir"])

    assert evaluated["score_summary"]["average_score"] is not None
    assert extracted["candidate_count"] > 0
    assert (run_dir / "cached_replay.json").exists()
    assert (run_dir / "memory_candidates.json").exists()
    assert (run_dir / "memory_review_table.csv").exists()
    assert all(candidate["status"] == "pending" for candidate in extracted["candidates"])
    assert all(candidate["evidence"] for candidate in extracted["candidates"])

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE status = 'active'"
        ).fetchone()[0]
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM memory_items WHERE status = 'pending'"
        ).fetchone()[0]
        evidence_count = conn.execute("SELECT COUNT(*) FROM memory_evidence").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM memory_audit_logs").fetchone()[0]
    assert active_count == 0
    assert pending_count == extracted["candidate_count"]
    assert evidence_count >= extracted["candidate_count"]
    assert audit_count >= extracted["candidate_count"]


def test_memory_review_apply_approve_and_reject(tmp_path: Path, monkeypatch) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)
    _evaluated, extracted = eval_and_extract(workspace)
    run_dir = extracted["run_dir"]
    candidate_ids = [candidate["candidate_id"] for candidate in extracted["candidates"]]

    review = runner.invoke(
        app,
        [
            "learn",
            "memory-review",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--json",
        ],
    )
    assert review.exit_code == 0, review.output

    bundle = runner.invoke(
        app,
        [
            "learn",
            "apply-test-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--mode",
            "test-only",
            "--json",
        ],
    )
    assert bundle.exit_code == 0, bundle.output
    assert Path(parse_json(bundle.output)["data"]["test_memory_bundle"]).exists()

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
            run_dir,
            "--candidate-ids",
            candidate_ids[0],
            "--json",
        ],
    )
    assert approved.exit_code == 0, approved.output

    rejected = runner.invoke(
        app,
        [
            "learn",
            "reject-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--run",
            run_dir,
            "--candidate-ids",
            candidate_ids[-1],
            "--reason",
            "not preferred",
            "--json",
        ],
    )
    assert rejected.exit_code == 0, rejected.output
    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        statuses = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT id, status FROM memory_items WHERE id IN (?, ?)",
                (candidate_ids[0], candidate_ids[-1]),
            )
        }
    assert statuses[candidate_ids[0]] == "active"
    assert statuses[candidate_ids[-1]] == "rejected"


def test_mock_learning_loop_creates_artifacts_and_improves(tmp_path: Path, monkeypatch) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)

    loop = runner.invoke(
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
            "3",
            "--iterations",
            "3",
            "--repair-iterations",
            "2",
            "--use-stable-prompt",
            "--rollback-harmful-memory",
            "--json",
        ],
    )

    assert loop.exit_code == 0, loop.output
    data = parse_json(loop.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["best_score"] >= data["baseline_score"]
    assert data["score_delta"] >= 0
    assert data["candidate_count"] > 0
    assert (run_dir / "learning_summary.json").exists()
    assert (run_dir / "global_cycle_log.md").exists()
    assert (run_dir / "rollback_log.json").exists()
    assert (run_dir / "cached_replay.json").exists()
    assert any(run_dir.glob("cycle_*/iteration_*/score_delta.json"))


def test_learning_loop_model_fallback_after_repeated_mock_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)

    loop = runner.invoke(
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
            "--json",
        ],
    )

    assert loop.exit_code == 0, loop.output
    data = parse_json(loop.output)["data"]
    assert data["fallback_model_used"] is True
    switch_log = json.loads((Path(data["run_dir"]) / "model_switch_log.json").read_text(encoding="utf-8"))
    assert switch_log["entries"]


def test_learning_loop_detects_regression_and_rolls_back_harmful_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)

    loop = runner.invoke(
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
            "--json",
        ],
    )

    assert loop.exit_code == 0, loop.output
    data = parse_json(loop.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["harmful_candidate_count"] > 0
    assert data["rollback_count"] > 0
    assert data["score_delta"] == 0
    rollback = json.loads((run_dir / "rollback_log.json").read_text(encoding="utf-8"))
    repairs = json.loads((run_dir / "repair_iteration_log.json").read_text(encoding="utf-8"))
    assert rollback["entries"]
    assert len(repairs["entries"]) == 2


def test_reject_requires_reason_and_explicit_selection(tmp_path: Path, monkeypatch) -> None:
    workspace = init_learning_workspace(tmp_path, monkeypatch)
    _evaluated, extracted = eval_and_extract(workspace)

    rejected = runner.invoke(
        app,
        [
            "learn",
            "reject-memory",
            "--workspace",
            str(workspace),
            "--project",
            "han-jue",
            "--run",
            extracted["run_dir"],
            "--all",
            "--json",
        ],
    )

    assert rejected.exit_code == 4
    assert "reason" in parse_json(rejected.output)["error"]["message"]
