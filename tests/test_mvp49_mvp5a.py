from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.production_translation import split_text_chunks
from nts_core.stable_prompts import load_approved_stable_prompt


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def create_workspace_with_project(tmp_path: Path, monkeypatch, text: str) -> tuple[Path, str]:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    raw_path = tmp_path / "raw.txt"
    raw_path.write_text(text, encoding="utf-8")
    assert runner.invoke(app, ["init", "--workspace", str(workspace), "--json"]).exit_code == 0
    create = runner.invoke(
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
    assert create.exit_code == 0, create.output
    imported = runner.invoke(
        app,
        [
            "import",
            "text",
            str(raw_path),
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--json",
        ],
    )
    assert imported.exit_code == 0, imported.output
    chapters = runner.invoke(
        app,
        ["text", "chapters", "list", "--workspace", str(workspace), "--project", "demo", "--json"],
    )
    assert chapters.exit_code == 0, chapters.output
    chapter_id = parse_json(chapters.output)["data"]["chapters"][0]["id"]
    return workspace, chapter_id


def write_stable_prompt(
    workspace: Path,
    *,
    approved: bool,
    prompt_id: str = "prompt_test",
) -> Path:
    run_dir = workspace / "artifacts" / "evaluations" / "run_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "stable_prompt.md").write_text(
        "# Stable Prompt\n\n```text\nTranslate faithfully and concisely.\n```\n",
        encoding="utf-8",
    )
    (run_dir / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "prompt_id": prompt_id,
                "prompt_version": "mvp4.8-test",
                "source_eval_run_id": "run_test",
                "language_pair": "zh-vi",
                "domain": "novel",
                "quality_gate": "pass",
                "average_score": 91.0,
                "per_run_scores": [91, 92, 90],
                "per_sample_scores": [91, 92, 90],
                "created_at": "2026-05-25T00:00:00Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if approved:
        (run_dir / "stable_prompt_approval.json").write_text(
            json.dumps(
                {
                    "schema_version": "stable_prompt_human_review_v1",
                    "decision": "approved",
                    "reviewer": "pytest",
                    "timestamp": "2026-05-25T00:00:01Z",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return run_dir


def test_missing_approved_stable_prompt_blocks_production_translate(tmp_path: Path, monkeypatch) -> None:
    workspace, chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 第一\n\nDao enters the room.",
    )

    result = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--json",
        ],
    )

    assert result.exit_code == 4
    payload = parse_json(result.output)
    assert payload["error"]["code"] == "STABLE_PROMPT_BLOCKED"


def test_unapproved_stable_prompt_blocks_production_translate(tmp_path: Path, monkeypatch) -> None:
    workspace, chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 第一\n\nDao enters the room.",
    )
    write_stable_prompt(workspace, approved=False)

    result = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--json",
        ],
    )

    assert result.exit_code == 4
    assert parse_json(result.output)["error"]["code"] == "STABLE_PROMPT_BLOCKED"
    assert "not approved" in parse_json(result.output)["error"]["message"]


def test_stable_prompt_registry_loads_approved_prompt(tmp_path: Path, monkeypatch) -> None:
    workspace, _chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 第一\n\nDao enters the room.",
    )
    run_dir = write_stable_prompt(workspace, approved=True, prompt_id="prompt_registry")

    record = load_approved_stable_prompt(type("WS", (), {"path": workspace})())

    assert record.prompt_id == "prompt_registry"
    assert record.approval_status == "approved"
    assert record.approval_path == str(run_dir / "stable_prompt_approval.json")
    assert "Translate faithfully" in record.prompt_text


def test_translate_text_stable_mock_creates_artifacts_and_logs(tmp_path: Path, monkeypatch) -> None:
    workspace, chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 第一\n\nDao enters the room.\n\nDao finds a manual.",
    )
    write_stable_prompt(workspace, approved=True)
    memory = runner.invoke(
        app,
        [
            "memory",
            "create",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--type",
            "term",
            "--status",
            "active",
            "--source-key",
            "Dao",
            "--target-text",
            "Đạo",
            "--confidence-score",
            "0.9",
            "--json",
        ],
    )
    assert memory.exit_code == 0, memory.output

    result = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--enable-compression-pass",
            "--merge-tiny-paragraphs",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    artifact_dir = Path(data["artifact_dir"])
    assert (artifact_dir / "source.txt").exists()
    assert (artifact_dir / "memory_bundle.json").exists()
    assert (artifact_dir / "prompt_used.md").exists()
    assert (artifact_dir / "model_response_raw.json").exists()
    assert (artifact_dir / "translation.vi.txt").exists()
    assert (artifact_dir / "quality_report.json").exists()
    assert (artifact_dir / "run_manifest.json").exists()
    assert "Đạo" in (artifact_dir / "prompt_used.md").read_text(encoding="utf-8")
    quality = json.loads((artifact_dir / "quality_report.json").read_text(encoding="utf-8"))
    assert quality["final_output_selector"]["selected_final_output"] in {
        "before_compression",
        "after_compression",
    }

    with closing(sqlite3.connect(workspace / "nts.db")) as conn:
        model_runs = conn.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        task_runs = conn.execute(
            "SELECT COUNT(*) FROM task_runs WHERE task_type = 'translate.text.stable'"
        ).fetchone()[0]
        translations = conn.execute(
            "SELECT COUNT(*) FROM translations WHERE chapter_id = ? AND is_current = 1",
            (chapter_id,),
        ).fetchone()[0]
    assert model_runs >= 1
    assert task_runs == 1
    assert translations == 1


def test_batch_dry_run_and_limit_guard(tmp_path: Path, monkeypatch) -> None:
    workspace, _chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 一\n\nDao one.\n\n第2章 二\n\nDao two.\n\n第3章 三\n\nDao three.",
    )
    write_stable_prompt(workspace, approved=True)

    dry_run = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1-2",
            "--max-chapters",
            "2",
            "--dry-run",
            "--json",
        ],
    )
    assert dry_run.exit_code == 0, dry_run.output
    dry_data = parse_json(dry_run.output)["data"]
    assert dry_data["dry_run"] is True
    assert dry_data["estimated_api_calls"] == 2
    assert Path(dry_data["batch_manifest"]).exists()

    too_many = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1-3",
            "--max-chapters",
            "2",
            "--json",
        ],
    )
    assert too_many.exit_code == 4
    assert "exceeds --max-chapters" in parse_json(too_many.output)["error"]["message"]


def test_batch_translates_chunks_and_exports_combined(tmp_path: Path, monkeypatch) -> None:
    text = (
        "第1章 一\n\n"
        + "\n\n".join(f"Dao paragraph {index} keeps moving." for index in range(1, 8))
        + "\n\n第2章 二\n\nDao second chapter."
    )
    workspace, _chapter_id = create_workspace_with_project(tmp_path, monkeypatch, text)
    write_stable_prompt(workspace, approved=True)

    result = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1-2",
            "--max-chapters",
            "2",
            "--max-source-chars-per-chapter",
            "1000",
            "--chunk-size-chars",
            "80",
            "--export-combined",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    batch_dir = Path(data["batch_dir"])
    assert (batch_dir / "batch_manifest.json").exists()
    assert (batch_dir / "chapter_results.json").exists()
    assert (batch_dir / "outputs" / "1.vi.txt").exists()
    assert (batch_dir / "outputs" / "2.vi.txt").exists()
    assert (batch_dir / "full_novel.vi.txt").exists()
    assert any((batch_dir / "chunk_outputs").glob("**/translation.vi.txt"))
    results = json.loads((batch_dir / "chapter_results.json").read_text(encoding="utf-8"))
    assert all(chapter["status"] == "success" for chapter in results["chapters"])


def test_batch_skip_existing_and_force_new_attempt(tmp_path: Path, monkeypatch) -> None:
    workspace, chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 一\n\nDao first chapter.",
    )
    write_stable_prompt(workspace, approved=True)
    first = runner.invoke(
        app,
        [
            "translate",
            "text",
            "--workspace",
            str(workspace),
            "--chapter",
            chapter_id,
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output

    skipped = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1",
            "--max-chapters",
            "1",
            "--json",
        ],
    )
    assert skipped.exit_code == 0, skipped.output
    assert parse_json(skipped.output)["data"]["chapters"][0]["status"] == "skipped_existing"

    forced = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "mock",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1",
            "--max-chapters",
            "1",
            "--force",
            "--json",
        ],
    )
    assert forced.exit_code == 0, forced.output
    assert parse_json(forced.output)["data"]["chapters"][0]["status"] == "success"


def test_batch_failed_chapter_is_recorded_and_resume_reuses_batch_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, _chapter_id = create_workspace_with_project(
        tmp_path,
        monkeypatch,
        "第1章 一\n\nDao first chapter.",
    )
    write_stable_prompt(workspace, approved=True)

    failed = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "missing_provider",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1",
            "--max-chapters",
            "1",
            "--json",
        ],
    )
    assert failed.exit_code == 0, failed.output
    failed_data = parse_json(failed.output)["data"]
    assert failed_data["status"] == "partial_failure"
    assert failed_data["chapters"][0]["status"] == "failed"
    assert "Provider not found" in failed_data["chapters"][0]["error"]

    resumed = runner.invoke(
        app,
        [
            "translate",
            "batch",
            "--workspace",
            str(workspace),
            "--project",
            "demo",
            "--provider",
            "missing_provider",
            "--model",
            "mock-production",
            "--use-stable-prompt",
            "--chapters",
            "1",
            "--max-chapters",
            "1",
            "--resume",
            "--json",
        ],
    )
    assert resumed.exit_code == 0, resumed.output
    assert parse_json(resumed.output)["data"]["batch_dir"] == failed_data["batch_dir"]


def test_split_text_chunks_preserves_paragraph_boundaries() -> None:
    chunks = split_text_chunks(
        "one paragraph\n\nsecond paragraph is longer\n\nthird paragraph",
        chunk_size_chars=30,
    )
    assert chunks == ["one paragraph", "second paragraph is longer", "third paragraph"]
