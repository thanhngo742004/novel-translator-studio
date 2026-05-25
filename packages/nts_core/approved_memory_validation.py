from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from nts_core.eval_harness import (
    ALIGNMENT_QUALITY_THRESHOLD,
    apply_translation_units,
    align_blocks_monotonic,
    build_alignment_blocks,
    build_alignment_candidates,
    compare_translation,
    extract_epub_chapters,
    extract_raw_chapters,
    json_dumps,
    prepare_parallel,
    read_json,
    sample_from_alignment_candidate,
    translate_samples,
    write_json,
)
from nts_core.learning_loop import (
    DEFAULT_LEARNING_MAX_SOURCE_CHARS,
    DEFAULT_LEARNING_MAX_TARGET_CHARS,
    _adjust_report_scores,
    _is_retryable_provider_error,
    _score_summary,
    parse_chapter_selection,
)
from nts_core.projects import get_project_by_slug
from nts_core.stable_prompts import StablePromptRecord, load_approved_stable_prompt
from nts_storage.database import connection, insert_task_run, row_to_dict, utc_now
from nts_storage.workspace import Workspace


DEFAULT_APPROVED_MEMORY_VALIDATION_CHAPTERS = "1-10"
DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS = 2
DEFAULT_APPROVED_MEMORY_MAX_CHAPTERS = 10


def approved_memory_validation_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "approved_memory_validation"


def new_validation_run_dir(workspace: Workspace, project_slug: str) -> Path:
    run_id = f"{project_slug}_amv_{int(time.time() * 1000)}"
    run_dir = approved_memory_validation_root(workspace) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pointer_root = approved_memory_validation_root(workspace) / project_slug
    pointer_root.mkdir(parents=True, exist_ok=True)
    (pointer_root / "latest.txt").write_text(str(run_dir), encoding="utf-8")
    return run_dir


def resolve_validation_run(workspace: Workspace, run: str) -> Path:
    path = Path(run)
    if path.exists():
        return path.resolve()
    candidate = approved_memory_validation_root(workspace) / run
    if candidate.exists():
        return candidate.resolve()
    raise ValueError(f"Approved-memory validation run not found: {run}")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")


def _copy_file_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _copy_dir_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        warnings = []
        for path in src.rglob("*"):
            relative = path.relative_to(src)
            target = dst / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(path, target)
                except OSError as exc:
                    warnings.append(
                        {
                            "source": str(path),
                            "target": str(target),
                            "error": str(exc),
                        }
                    )
        if warnings:
            write_json(dst / "copy_warnings.json", {"warnings": warnings})


def _active_memory_rows(workspace: Workspace, project_id: str, project_slug: str) -> list[dict[str, Any]]:
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            WHERE status = 'active'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    items = [row_to_dict(row, json_fields=("scope_json", "value_json", "rules_json", "confidence_json")) for row in rows]
    scoped = []
    for item in items:
        scope = item.get("scope_json") or {}
        if scope.get("project_id") not in (None, project_id):
            continue
        if scope.get("project_slug") not in (None, project_slug):
            continue
        scoped.append(item)
    return scoped


def _approved_learning_memory(workspace: Workspace, project: dict[str, Any]) -> list[dict[str, Any]]:
    items = _active_memory_rows(workspace, project["id"], project["slug"])
    return [
        item
        for item in items
        if item.get("layer") == "learning_candidate"
        or (item.get("value_json") or {}).get("learning_run_id")
    ]


def _memory_prompt_section(memory_items: list[dict[str, Any]], *, title: str) -> str:
    if not memory_items:
        return f"{title}\n- None supplied.\n"
    lines = [title]
    for item in memory_items:
        rules = item.get("rules_json") or {}
        forbidden = rules.get("forbidden_variants") or []
        lines.append(
            "- "
            f"id={item['id']}; type={item['memory_type']}; "
            f"source={item.get('source_key')}; preferred={item.get('target_text')}; "
            f"forbidden={json_dumps(forbidden)}; confidence={item.get('confidence_score')}"
        )
    return "\n".join(lines) + "\n"


def _validation_prompt(
    stable_prompt: StablePromptRecord,
    *,
    included_memory: list[dict[str, Any]],
    excluded_memory: list[dict[str, Any]],
    phase: str,
) -> str:
    sections = [
        stable_prompt.prompt_text,
        "",
        "Approved-memory validation mode:",
        "- Translate with the approved stable prompt.",
        "- Preserve concise Vietnamese webnovel style.",
        "- Return only requested Vietnamese translation JSON/plain text as instructed.",
        f"- Validation phase: {phase}.",
        "",
        _memory_prompt_section(included_memory, title="Active approved memory supplied to this phase:"),
    ]
    if excluded_memory:
        sections.extend(
            [
                "",
                "Memory intentionally excluded from this baseline phase:",
                "\n".join(f"- {item['id']} {item.get('source_key')} -> {item.get('target_text')}" for item in excluded_memory),
                "Do not treat the excluded list as active injected memory for this phase.",
            ]
        )
    return "\n".join(sections)


def _planned_stages(rounds: int) -> list[str]:
    stages = ["prepare_dataset"]
    for index in range(1, rounds + 1):
        stages.extend(
            [
                f"round_{index}_baseline_translate",
                f"round_{index}_baseline_evaluate",
                f"round_{index}_memory_translate",
                f"round_{index}_memory_evaluate",
                f"round_{index}_score_delta",
            ]
        )
    stages.append("final_decision")
    return stages


def _update_state(run_dir: Path, state: dict[str, Any]) -> None:
    planned = _planned_stages(int(state.get("rounds") or DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS))
    completed = set(state.get("completed_stages", []))
    if state.get("final_decision"):
        state["pending_stages"] = []
    else:
        state["pending_stages"] = [stage for stage in planned if stage not in completed]
    state["updated_at"] = utc_now()
    state["next_command"] = f"nts learn resume-approved-memory-validation --run {run_dir} --json"
    write_json(run_dir / "validation_job_state.json", state)
    write_json(
        run_dir / "resume_plan.json",
        {
            "schema_version": "approved_memory_validation_resume_plan_v1",
            "validation_run_id": state["validation_run_id"],
            "status": state["status"],
            "final_decision": state.get("final_decision"),
            "can_resume": state.get("can_resume"),
            "next_stage": state["pending_stages"][0] if state.get("pending_stages") else None,
            "next_command": state["next_command"],
            "updated_at": state["updated_at"],
        },
    )


def _mark_stage(run_dir: Path, state: dict[str, Any], stage: str, status: str, details: dict[str, Any] | None = None) -> None:
    stage_status = read_json(run_dir / "stage_status.json") if (run_dir / "stage_status.json").exists() else {}
    entry = stage_status.setdefault(stage, {})
    entry["status"] = status
    if status == "running":
        entry["started_at"] = utc_now()
    if status in {"completed", "failed", "paused", "blocked"}:
        entry["completed_at"] = utc_now()
    if details:
        entry["details"] = details
    stage_status[stage] = entry
    write_json(run_dir / "stage_status.json", stage_status)
    _append_jsonl(
        run_dir / "checkpoint_log.jsonl",
        {"created_at": utc_now(), "stage": stage, "status": status, "details": details or {}},
    )
    state["current_stage"] = stage
    if status == "completed" and stage not in state.setdefault("completed_stages", []):
        state["completed_stages"].append(stage)
    if status in {"failed", "blocked"} and stage not in state.setdefault("failed_stages", []):
        state["failed_stages"].append(stage)
    _update_state(run_dir, state)


def _init_validation_files(run_dir: Path, state: dict[str, Any]) -> None:
    for name in ("checkpoint_log.jsonl", "api_call_log.jsonl", "provider_error_log.jsonl"):
        (run_dir / name).touch(exist_ok=True)
    defaults = {
        "stage_status.json": {},
        "alignment_report.json": {"status": "pending"},
        "approved_memory_used.json": {"items": []},
        "baseline_memory_exclusion.json": {"excluded_memory_ids": []},
        "final_validation_summary.json": {"status": "pending"},
    }
    for name, payload in defaults.items():
        path = run_dir / name
        if not path.exists():
            write_json(path, payload)
    text_defaults = {
        "alignment_report.md": "# Alignment Report\n\nPending.\n",
        "final_validation_summary.md": "# Final Validation Summary\n\nPending.\n",
        "memory_effect_report.md": "# Memory Effect Report\n\nPending.\n",
        "regression_report.md": "# Regression Report\n\nPending.\n",
    }
    for name, content in text_defaults.items():
        path = run_dir / name
        if not path.exists():
            _write_text(path, content)
    _update_state(run_dir, state)


def _initial_state(
    *,
    workspace: Workspace,
    run_dir: Path,
    project: dict[str, Any],
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    fallback_model: str | None,
    stable_prompt: StablePromptRecord,
    chapters: str,
    rounds: int,
    min_improvement: float,
    target_improvement: float,
    max_chapters: int,
    max_real_calls: int | None,
    require_consecutive_improvement: bool,
    rollback_on_regression: bool,
) -> dict[str, Any]:
    selected_chapters = parse_chapter_selection(chapters)
    return {
        "schema_version": "approved_memory_validation_job_v1",
        "validation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "workspace": str(workspace.path),
        "project_slug": project["slug"],
        "project_id": project["id"],
        "raw_path": str(raw_path.resolve()),
        "translated_path": str(translated_path.resolve()),
        "provider": provider_key,
        "model": model,
        "active_model": model,
        "fallback_model": fallback_model,
        "fallback_model_used": False,
        "stable_prompt_id": stable_prompt.prompt_id,
        "stable_prompt_path": stable_prompt.prompt_path,
        "chapters": selected_chapters,
        "chapters_arg": chapters,
        "rounds": rounds,
        "min_improvement": min_improvement,
        "target_improvement": target_improvement,
        "max_chapters": max_chapters,
        "max_real_calls": max_real_calls,
        "api_calls_used": 0,
        "require_consecutive_improvement": require_consecutive_improvement,
        "rollback_on_regression": rollback_on_regression,
        "current_stage": "initialized",
        "current_round": 0,
        "completed_stages": [],
        "failed_stages": [],
        "pending_stages": [],
        "round_results": [],
        "status": "running",
        "final_decision": None,
        "last_error": None,
        "can_resume": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def _prepare_dataset(run_dir: Path, state: dict[str, Any]) -> None:
    prepared = prepare_parallel(
        project=str(state["project_slug"]),
        raw_path=Path(state["raw_path"]),
        translated_path=Path(state["translated_path"]),
        max_chapters=max(state["chapters"]),
        max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
        max_target_chars=DEFAULT_LEARNING_MAX_TARGET_CHARS,
        sample_count=len(state["chapters"]),
        merge_tiny_paragraphs=True,
    )
    eval_run = Path(prepared["run_dir"])
    samples = _select_requested_chapter_samples(
        eval_run,
        state["chapters"],
        raw_path=Path(state["raw_path"]),
        translated_path=Path(state["translated_path"]),
    )
    low_alignment = [
        sample
        for sample in samples
        if float(sample.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD
        or sample.get("accepted_for_stable_validation") is False
    ]
    if low_alignment:
        raise ValueError("Low-alignment sample was selected; approved-memory validation is blocked.")
    for name in (
        "alignment_report.json",
        "chapter_alignment_report.json",
        "block_alignment_report.json",
        "alignment_candidates.json",
        "selected_samples.json",
        "approved_memory_validation_sample_selection.json",
        "translation_units.json",
        "unit_alignment_report.json",
    ):
        _copy_file_if_exists(eval_run / name, run_dir / name)
    alignment_payload = read_json(eval_run / "alignment_report.json") if (eval_run / "alignment_report.json").exists() else {}
    write_json(run_dir / "alignment_report.json", alignment_payload)
    _write_text(
        run_dir / "alignment_report.md",
        "# Alignment Report\n\n"
        f"- Eval run: `{eval_run}`\n"
        f"- Sample count: `{len(samples)}`\n"
        f"- Minimum alignment quality: `{min(float(sample.get('alignment_quality') or 0) for sample in samples)}`\n",
    )
    manifest = {
        "schema_version": "approved_memory_validation_manifest_v1",
        "validation_run_id": state["validation_run_id"],
        "project_slug": state["project_slug"],
        "chapters": state["chapters"],
        "rounds": state["rounds"],
        "eval_run_dir": str(eval_run),
        "sample_count": len(samples),
        "created_at": state["created_at"],
    }
    write_json(run_dir / "validation_manifest.json", manifest)
    state["eval_run_dir"] = str(eval_run)


def _select_requested_chapter_samples(
    eval_run: Path,
    chapters: list[int],
    *,
    raw_path: Path,
    translated_path: Path,
) -> list[dict[str, Any]]:
    raw_chapters = extract_raw_chapters(raw_path, max_chapters=max(chapters))
    target_chapters = extract_epub_chapters(translated_path, max_chapters=max(chapters))
    source_blocks = build_alignment_blocks(raw_chapters, lang="zh")
    target_blocks = build_alignment_blocks(target_chapters, lang="vi")
    block_pairs = align_blocks_monotonic(source_blocks, target_blocks)
    candidates = build_alignment_candidates(
        source_blocks,
        target_blocks,
        block_pairs,
        max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
        max_target_chars=DEFAULT_LEARNING_MAX_TARGET_CHARS,
    )
    selected = []
    for chapter in chapters:
        accepted = [
            candidate
            for candidate in candidates
            if candidate.get("accepted")
            and int(candidate.get("source_chapter_id") or 0) == int(chapter)
            and float(candidate.get("alignment_quality") or 0) >= ALIGNMENT_QUALITY_THRESHOLD
        ]
        if not accepted:
            raise ValueError(f"No reliable alignment sample found for requested chapter {chapter}.")
        accepted.sort(
            key=lambda item: (
                float(item.get("alignment_quality") or 0),
                len(item.get("shared_anchors") or []),
                int(item.get("source_char_count") or 0),
            ),
            reverse=True,
        )
        selected.append(
            sample_from_alignment_candidate(
                accepted[0],
                sample_id=f"sample_{len(selected) + 1}",
            )
        )
    selected = apply_translation_units(
        selected,
        merge_tiny_paragraphs=True,
    )
    write_json(eval_run / "selected_samples.json", {"samples": selected})
    write_json(
        eval_run / "approved_memory_validation_sample_selection.json",
        {
            "schema_version": "approved_memory_validation_sample_selection_v1",
            "requested_chapters": chapters,
            "selected_chapters": [sample["chapter_id"] for sample in selected],
            "selection_policy": "one_reliable_alignment_window_per_requested_chapter",
            "created_at": utc_now(),
        },
    )
    return selected


def _copy_phase_artifacts(eval_run: Path, round_dir: Path, *, phase: str, report: dict[str, Any]) -> None:
    outputs_dir = round_dir / f"{phase}_outputs"
    _copy_dir_if_exists(eval_run / "translation_outputs", outputs_dir)
    write_json(round_dir / f"{phase}_evaluation.json", report)
    _copy_file_if_exists(eval_run / "evaluation_report.md", round_dir / f"{phase}_evaluation.md")
    _copy_file_if_exists(eval_run / "provider_retry_log.json", round_dir / f"{phase}_provider_retry_log.json")
    _copy_file_if_exists(eval_run / "compression_log.json", round_dir / f"{phase}_compression_log.json")


def _model_report(report: dict[str, Any], model: str) -> dict[str, Any]:
    models = report.get("models", {})
    if model in models:
        return models[model]
    best = report.get("best_model")
    return models.get(best, {})


def _round_delta(
    *,
    baseline_report: dict[str, Any],
    memory_report: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    baseline = _model_report(baseline_report, model)
    memory = _model_report(memory_report, model)
    baseline_score = float(baseline.get("average_score") or baseline.get("total_score") or 0)
    memory_score = float(memory.get("average_score") or memory.get("total_score") or 0)
    baseline_samples = {sample.get("sample_id"): sample for sample in baseline.get("samples", [])}
    memory_samples = {sample.get("sample_id"): sample for sample in memory.get("samples", [])}
    sample_deltas = []
    severe_flags = []
    for sample_id, memory_sample in memory_samples.items():
        baseline_sample = baseline_samples.get(sample_id, {})
        delta = round(float(memory_sample.get("total_score") or 0) - float(baseline_sample.get("total_score") or 0), 2)
        flags = memory_sample.get("gates", {}) or {}
        reasons = list(memory_sample.get("verification_reasons", []) or [])
        if flags.get("severe_hallucination"):
            severe_flags.append({"sample_id": sample_id, "reason": "severe_hallucination"})
        if flags.get("wrong_main_character_name"):
            severe_flags.append({"sample_id": sample_id, "reason": "wrong_main_character_name"})
        if memory_sample.get("truncated_paragraphs"):
            severe_flags.append({"sample_id": sample_id, "reason": "truncation"})
        if memory_sample.get("unsafe_compression_paragraphs") or "unsafe_compression" in reasons:
            severe_flags.append({"sample_id": sample_id, "reason": "unsafe_compression"})
        if memory_sample.get("alignment_quality") is not None and float(memory_sample.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD:
            severe_flags.append({"sample_id": sample_id, "reason": "low_alignment"})
        sample_deltas.append(
            {
                "sample_id": sample_id,
                "chapter_id": memory_sample.get("chapter_id"),
                "baseline_score": baseline_sample.get("total_score"),
                "memory_score": memory_sample.get("total_score"),
                "delta": delta,
                "baseline_ratio": baseline_sample.get("output_reference_ratio"),
                "memory_ratio": memory_sample.get("output_reference_ratio"),
                "terminology_delta": (
                    len(baseline_sample.get("terminology_mismatches", []) or [])
                    - len(memory_sample.get("terminology_mismatches", []) or [])
                ),
                "style_drift_delta": (
                    float(baseline_sample.get("style_drift_score") or 0)
                    - float(memory_sample.get("style_drift_score") or 0)
                ),
            }
        )
    regressions = [row for row in sample_deltas if row["delta"] < -3]
    terminology_error_delta = sum(row["terminology_delta"] for row in sample_deltas)
    return {
        "baseline_score": baseline_score,
        "memory_score": memory_score,
        "score_delta": round(memory_score - baseline_score, 2),
        "sample_deltas": sample_deltas,
        "per_chapter_deltas": sample_deltas,
        "terminology_error_delta": terminology_error_delta,
        "name_pronoun_error_delta": terminology_error_delta,
        "style_drift_delta": round(sum(row["style_drift_delta"] for row in sample_deltas), 2),
        "omission_addition_delta": round(
            sum(
                float(memory_samples.get(row["sample_id"], {}).get("omission_addition") or 0)
                - float(baseline_samples.get(row["sample_id"], {}).get("omission_addition") or 0)
                for row in sample_deltas
            ),
            2,
        ),
        "formatting_error_delta": round(
            sum(
                float(memory_samples.get(row["sample_id"], {}).get("formatting_preservation") or 0)
                - float(baseline_samples.get(row["sample_id"], {}).get("formatting_preservation") or 0)
                for row in sample_deltas
            ),
            2,
        ),
        "ratio_delta": round(
            sum(
                float(row.get("memory_ratio") or 0) - float(row.get("baseline_ratio") or 0)
                for row in sample_deltas
            ),
            3,
        ),
        "regressions_over_3": regressions,
        "severe_flags": severe_flags,
    }


def _write_round_comparison(round_dir: Path, round_index: int, delta: dict[str, Any]) -> None:
    write_json(round_dir / "score_delta.json", delta)
    lines = [
        f"# Round {round_index} Comparison",
        "",
        f"- Baseline score: `{delta['baseline_score']}`",
        f"- Approved-memory score: `{delta['memory_score']}`",
        f"- Delta: `{delta['score_delta']}`",
        f"- Regressions > 3: `{len(delta['regressions_over_3'])}`",
        f"- Severe flags: `{len(delta['severe_flags'])}`",
        "",
        "| Sample | Chapter | Baseline | Memory | Delta |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in delta["sample_deltas"]:
        lines.append(
            f"| {row['sample_id']} | {row.get('chapter_id')} | {row.get('baseline_score')} | "
            f"{row.get('memory_score')} | {row['delta']} |"
        )
    _write_text(round_dir / "comparison_report.md", "\n".join(lines) + "\n")


def _mock_memory_report(report: dict[str, Any], model: str, round_index: int) -> dict[str, Any]:
    if model.startswith("mock-regress-approved"):
        adjusted = _adjust_report_scores(report, model, -4.0)
    elif model.startswith("mock-one-round-fails") and round_index >= 2:
        adjusted = _adjust_report_scores(report, model, 0.0)
    else:
        adjusted = _adjust_report_scores(report, model, 2.0)
    model_report = adjusted.get("models", {}).get(model, {})
    for sample in model_report.get("samples", []):
        gates = sample.setdefault("gates", {})
        gates["severe_hallucination"] = False
        gates["wrong_main_character_name"] = False
        gates["major_skipped_passage"] = False
        sample["truncated_paragraphs"] = []
        sample["unsafe_compression_paragraphs"] = []
        sample["verification_reasons"] = []
        if float(sample.get("total_score") or 0) >= 80:
            sample["pass"] = True
            sample["final_pass_fail_reason"] = "pass"
    if model_report.get("samples"):
        model_report["pass"] = all(sample.get("pass") for sample in model_report["samples"])
        model_report["final_pass_fail_reason"] = "pass" if model_report["pass"] else "mock_quality_gate_failed"
        adjusted["pass"] = any(item.get("pass") for item in adjusted.get("models", {}).values())
    return adjusted


def _provider_failures_from_translation(translation: dict[str, Any], model: str) -> list[dict[str, Any]]:
    failures = []
    for sample_id, outputs in (translation.get("outputs") or {}).items():
        if not isinstance(outputs, dict):
            continue
        metadata = outputs.get(model)
        if metadata is None and outputs:
            metadata = next(iter(outputs.values()))
        if not isinstance(metadata, dict):
            continue
        verification = metadata.get("verification_after_compression") or {}
        reasons = set(verification.get("reasons") or [])
        provider_error = metadata.get("provider_error")
        provider_failure_empty_output = bool(
            verification.get("provider_failure_empty_output")
            or "provider_failure_empty_output" in reasons
            or "provider_retry_exhausted" in reasons
        )
        output_empty = int(metadata.get("output_char_count") or 0) == 0
        if provider_error or provider_failure_empty_output:
            failures.append(
                {
                    "sample_id": sample_id,
                    "provider_error": provider_error,
                    "classification": metadata.get("provider_error_classification") or {},
                    "provider_failure_empty_output": provider_failure_empty_output,
                    "output_empty": output_empty,
                    "reasons": sorted(reasons),
                }
            )
    return failures


def _raise_provider_failures_if_any(run_dir: Path, translation: dict[str, Any], model: str) -> None:
    failures = _provider_failures_from_translation(translation, model)
    if not failures:
        return
    retryable = any((failure.get("classification") or {}).get("retryable") for failure in failures)
    first = failures[0]
    message = first.get("provider_error") or "provider_failure_empty_output"
    write_json(
        run_dir / "latest_provider_failures.json",
        {
            "schema_version": "approved_memory_validation_provider_failures_v1",
            "model": model,
            "retryable": retryable,
            "failure_count": len(failures),
            "failures": failures,
            "created_at": utc_now(),
        },
    )
    retry_text = "retryable" if retryable else "non_retryable"
    raise ValueError(
        f"{retry_text}_provider_failure during approved-memory validation: {message}"
    )


def _run_phase(
    *,
    run_dir: Path,
    state: dict[str, Any],
    phase: str,
    prompt_text: str,
    model: str,
    round_index: int,
) -> tuple[dict[str, Any], Path]:
    translation = translate_samples(
        project=str(state["project_slug"]),
        provider_key=str(state["provider"]),
        models=[model],
        max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
        enable_length_retry=False,
        target_length_tolerance=0.2,
        enable_paragraph_alignment=True,
        enable_compression_pass=True,
        merge_tiny_paragraphs=True,
        sample_limit=len(state["chapters"]),
        stable_prompt_text=prompt_text,
        provider_retry_attempts=3,
        provider_retry_backoff_seconds=0.0 if state["provider"] == "mock" else 5.0,
        validation_index=round_index,
    )
    _raise_provider_failures_if_any(run_dir, translation, model)
    compared = compare_translation(
        project=str(state["project_slug"]),
        chapter=int(state["chapters"][0]),
        max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
        max_target_chars=DEFAULT_LEARNING_MAX_TARGET_CHARS,
    )
    report = compared["report"]
    if state["provider"] == "mock" and phase == "memory":
        report = _mock_memory_report(report, model, round_index)
    return report, Path(translation["run_dir"])


def _call_allowed(run_dir: Path, state: dict[str, Any], *, stage: str, max_real_calls: int | None, invocation_calls: int) -> tuple[bool, int]:
    estimated = len(state.get("chapters", [])) or 1
    if max_real_calls is not None and invocation_calls + estimated > max_real_calls:
        _append_jsonl(
            run_dir / "api_call_log.jsonl",
            {
                "created_at": utc_now(),
                "stage": stage,
                "estimated_calls": estimated,
                "api_calls_used_total": state.get("api_calls_used", 0),
                "status": "paused_before_call",
            },
        )
        return False, invocation_calls
    state["api_calls_used"] = int(state.get("api_calls_used") or 0) + estimated
    _append_jsonl(
        run_dir / "api_call_log.jsonl",
        {
            "created_at": utc_now(),
            "stage": stage,
            "estimated_calls": estimated,
            "api_calls_used_total": state["api_calls_used"],
            "status": "started",
        },
    )
    return True, invocation_calls + estimated


def _validation_result(run_dir: Path, state: dict[str, Any], task_run_id: str | None = None) -> dict[str, Any]:
    rounds = state.get("round_results", [])
    return {
        "validation_run_id": state["validation_run_id"],
        "run_dir": str(run_dir),
        "status": state.get("status"),
        "final_decision": state.get("final_decision"),
        "current_stage": state.get("current_stage"),
        "can_resume": state.get("can_resume"),
        "next_command": state.get("next_command"),
        "rounds_completed": len(rounds),
        "round_results": rounds,
        "api_calls_used": state.get("api_calls_used", 0),
        "fallback_model_used": state.get("fallback_model_used", False),
        "approved_memory_ids": state.get("approved_memory_ids", []),
        "last_error": state.get("last_error"),
        "task_run_id": task_run_id,
    }


def _insert_task(workspace: Workspace, state: dict[str, Any], result: dict[str, Any]) -> str:
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.validate_approved_memory",
            status=str(state.get("status")),
            stage=str(state.get("current_stage")),
            project_id=state.get("project_id"),
            input_data={
                "provider": state.get("provider"),
                "model": state.get("model"),
                "chapters": state.get("chapters"),
                "rounds": state.get("rounds"),
            },
            result_data=result,
        )
        conn.commit()
    return task_id


def _finalize_result(workspace: Workspace, run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    _update_state(run_dir, state)
    result = _validation_result(run_dir, state)
    task_id = _insert_task(workspace, state, result)
    result["task_run_id"] = task_id
    return result


def _pause(run_dir: Path, state: dict[str, Any], reason: str) -> None:
    state["status"] = "paused"
    state["last_error"] = reason
    state["can_resume"] = True
    _mark_stage(run_dir, state, state.get("current_stage") or "unknown", "paused", {"reason": reason})


def _block(run_dir: Path, state: dict[str, Any], reason: str, *, can_resume: bool) -> None:
    state["status"] = "blocked"
    state["final_decision"] = "BLOCKED"
    state["last_error"] = reason
    state["can_resume"] = can_resume
    _mark_stage(run_dir, state, state.get("current_stage") or "unknown", "blocked", {"reason": reason})
    _write_final_summary(run_dir, state, reason=reason)


def _write_final_summary(run_dir: Path, state: dict[str, Any], *, reason: str) -> None:
    rounds = state.get("round_results", [])
    final_summary = {
        "schema_version": "approved_memory_validation_summary_v1",
        "validation_run_id": state["validation_run_id"],
        "project_slug": state["project_slug"],
        "chapters": state["chapters"],
        "provider": state["provider"],
        "model": state["model"],
        "final_model": state.get("active_model"),
        "fallback_model_used": state.get("fallback_model_used", False),
        "stable_prompt_path": state.get("stable_prompt_path"),
        "approved_memory_ids": state.get("approved_memory_ids", []),
        "round_results": rounds,
        "final_decision": state.get("final_decision"),
        "reason": reason,
        "created_at": utc_now(),
    }
    write_json(run_dir / "final_validation_summary.json", final_summary)
    lines = [
        "# Final Validation Summary",
        "",
        f"- Chapters tested: `{state['chapters']}`",
        f"- Provider/model: `{state['provider']}` / `{state.get('active_model')}`",
        f"- Fallback used: `{state.get('fallback_model_used', False)}`",
        f"- Stable prompt path: `{state.get('stable_prompt_path')}`",
        f"- Approved memory IDs: `{', '.join(state.get('approved_memory_ids', []))}`",
        f"- Final decision: `{state.get('final_decision')}`",
        f"- Reason: `{reason}`",
        "",
        "| Round | Baseline | Memory | Delta |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in rounds:
        lines.append(
            f"| {row['round']} | {row['baseline_score']} | {row['memory_score']} | {row['score_delta']} |"
        )
    _write_text(run_dir / "final_validation_summary.md", "\n".join(lines) + "\n")
    _write_text(
        run_dir / "memory_effect_report.md",
        "# Memory Effect Report\n\n"
        + "\n".join(
            f"- Round {row['round']}: delta={row['score_delta']}, terminology_delta={row.get('terminology_error_delta')}"
            for row in rounds
        )
        + "\n",
    )
    regressions = [
        regression
        for row in rounds
        for regression in row.get("regressions_over_3", [])
    ]
    _write_text(
        run_dir / "regression_report.md",
        "# Regression Report\n\n"
        + (json_dumps(regressions) if regressions else "No per-chapter regression over 3 points.\n"),
    )


def _final_decision(state: dict[str, Any], *, require_consecutive_improvement: bool, min_improvement: float) -> tuple[str, str]:
    rounds = state.get("round_results", [])
    if len(rounds) < int(state.get("rounds") or DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS):
        return "BLOCKED", "validation_incomplete"
    if any(row.get("severe_flags") for row in rounds):
        return "FAIL", "severe_validation_flag_detected"
    if any(row.get("regressions_over_3") for row in rounds):
        return "FAIL", "per_chapter_regression_over_3"
    positive = [float(row.get("score_delta") or 0) > 0 for row in rounds]
    if require_consecutive_improvement and not all(positive):
        return "FAIL", "not_all_rounds_improved"
    if not any(float(row.get("score_delta") or 0) >= min_improvement for row in rounds):
        high_baseline_error_delta = all(
            float(row.get("baseline_score") or 0) >= 92
            and float(row.get("score_delta") or 0) > 0
            and int(row.get("terminology_error_delta") or 0) > 0
            for row in rounds
        )
        if not high_baseline_error_delta:
            return "FAIL", "minimum_improvement_not_reached"
    return "PASS", "consecutive_rounds_improved"


def start_approved_memory_validation(
    workspace: Workspace,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    fallback_model: str | None,
    chapters: str = DEFAULT_APPROVED_MEMORY_VALIDATION_CHAPTERS,
    rounds: int = DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS,
    require_consecutive_improvement: bool = True,
    min_improvement: float = 1.0,
    target_improvement: float = 3.0,
    max_chapters: int = DEFAULT_APPROVED_MEMORY_MAX_CHAPTERS,
    max_real_calls: int | None = None,
    use_stable_prompt: bool = True,
    resumable: bool = True,
    rollback_on_regression: bool = False,
    dry_run: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required.")
    if not resumable:
        raise ValueError("MVP5D approved-memory validation must be resumable.")
    selected_chapters = parse_chapter_selection(chapters)
    if len(selected_chapters) > max_chapters or max(selected_chapters) > max_chapters:
        raise ValueError("MVP5D refuses to process more than 10 chapters.")
    if rounds < 2:
        raise ValueError("MVP5D requires at least two validation rounds.")
    project = get_project_by_slug(workspace, project_slug)
    stable_prompt = load_approved_stable_prompt(workspace)
    approved_memory = _approved_learning_memory(workspace, project)
    run_dir = (output_dir.resolve() if output_dir else new_validation_run_dir(workspace, project_slug))
    run_dir.mkdir(parents=True, exist_ok=True)
    state = _initial_state(
        workspace=workspace,
        run_dir=run_dir,
        project=project,
        raw_path=raw_path,
        translated_path=translated_path,
        provider_key=provider_key,
        model=model,
        fallback_model=fallback_model,
        stable_prompt=stable_prompt,
        chapters=chapters,
        rounds=rounds,
        min_improvement=min_improvement,
        target_improvement=target_improvement,
        max_chapters=max_chapters,
        max_real_calls=max_real_calls,
        require_consecutive_improvement=require_consecutive_improvement,
        rollback_on_regression=rollback_on_regression,
    )
    state["approved_memory_ids"] = [item["id"] for item in approved_memory]
    state["stable_prompt_path"] = stable_prompt.prompt_path
    _init_validation_files(run_dir, state)
    write_json(run_dir / "approved_memory_used.json", {"items": approved_memory})
    write_json(
        run_dir / "baseline_memory_exclusion.json",
        {"excluded_memory_ids": state["approved_memory_ids"], "excluded_items": approved_memory},
    )
    if not approved_memory:
        _block(run_dir, state, "approved_learning_memory_missing", can_resume=False)
        return _finalize_result(workspace, run_dir, state)
    if dry_run:
        state["status"] = "dry_run"
        state["can_resume"] = True
        state["last_error"] = None
        _update_state(run_dir, state)
        result = _validation_result(run_dir, state)
        result["estimated_api_calls_per_round"] = len(selected_chapters) * 2
        result["estimated_total_api_calls"] = len(selected_chapters) * 2 * rounds
        result["dry_run"] = True
        task_id = _insert_task(workspace, state, result)
        result["task_run_id"] = task_id
        return result
    return resume_approved_memory_validation(
        workspace,
        run=str(run_dir),
        max_real_calls=max_real_calls,
    )


def resume_approved_memory_validation(
    workspace: Workspace,
    *,
    run: str,
    max_real_calls: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir = resolve_validation_run(workspace, run)
    state = read_json(run_dir / "validation_job_state.json")
    if state.get("final_decision"):
        return _finalize_result(workspace, run_dir, state)
    if max_real_calls is not None:
        state["max_real_calls"] = max_real_calls
    if dry_run:
        result = _validation_result(run_dir, state)
        result["dry_run"] = True
        return result
    invocation_calls = 0
    stable_prompt = load_approved_stable_prompt(workspace)
    approved_memory = read_json(run_dir / "approved_memory_used.json").get("items", [])
    baseline_excluded = read_json(run_dir / "baseline_memory_exclusion.json").get("excluded_items", [])
    all_active_memory = _active_memory_rows(
        workspace,
        str(state["project_id"]),
        str(state["project_slug"]),
    )
    baseline_memory = [item for item in all_active_memory if item["id"] not in set(state.get("approved_memory_ids", []))]
    try:
        if "prepare_dataset" not in state.get("completed_stages", []):
            _mark_stage(run_dir, state, "prepare_dataset", "running")
            _prepare_dataset(run_dir, state)
            _mark_stage(run_dir, state, "prepare_dataset", "completed")
        eval_run = Path(read_json(run_dir / "validation_manifest.json")["eval_run_dir"])
        model = str(state.get("active_model") or state.get("model"))
        round_results = list(state.get("round_results", []))
        for round_index in range(1, int(state["rounds"]) + 1):
            state["current_round"] = round_index
            round_dir = run_dir / f"round_{round_index}"
            round_dir.mkdir(parents=True, exist_ok=True)
            baseline_eval_path = round_dir / "baseline_evaluation.json"
            memory_eval_path = round_dir / "memory_evaluation.json"
            if not baseline_eval_path.exists():
                stage = f"round_{round_index}_baseline_translate"
                allowed, invocation_calls = _call_allowed(
                    run_dir,
                    state,
                    stage=stage,
                    max_real_calls=max_real_calls,
                    invocation_calls=invocation_calls,
                )
                if not allowed:
                    _pause(run_dir, state, f"max_real_calls_reached_before_{stage}")
                    return _finalize_result(workspace, run_dir, state)
                _mark_stage(run_dir, state, stage, "running")
                _mark_stage(run_dir, state, f"round_{round_index}_baseline_evaluate", "running")
                prompt = _validation_prompt(
                    stable_prompt,
                    included_memory=baseline_memory,
                    excluded_memory=baseline_excluded,
                    phase=f"round_{round_index}_baseline",
                )
                baseline_report, eval_run = _run_phase(
                    run_dir=run_dir,
                    state=state,
                    phase="baseline",
                    prompt_text=prompt,
                    model=model,
                    round_index=round_index,
                )
                _copy_phase_artifacts(eval_run, round_dir, phase="baseline", report=baseline_report)
                _mark_stage(run_dir, state, stage, "completed")
                _mark_stage(
                    run_dir,
                    state,
                    f"round_{round_index}_baseline_evaluate",
                    "completed",
                    {"average_score": _score_summary(baseline_report, model).get("average_score")},
                )
            else:
                baseline_report = read_json(baseline_eval_path)

            if not memory_eval_path.exists():
                stage = f"round_{round_index}_memory_translate"
                allowed, invocation_calls = _call_allowed(
                    run_dir,
                    state,
                    stage=stage,
                    max_real_calls=max_real_calls,
                    invocation_calls=invocation_calls,
                )
                if not allowed:
                    _pause(run_dir, state, f"max_real_calls_reached_before_{stage}")
                    return _finalize_result(workspace, run_dir, state)
                _mark_stage(run_dir, state, stage, "running")
                _mark_stage(run_dir, state, f"round_{round_index}_memory_evaluate", "running")
                prompt = _validation_prompt(
                    stable_prompt,
                    included_memory=all_active_memory,
                    excluded_memory=[],
                    phase=f"round_{round_index}_approved_memory",
                )
                memory_report, eval_run = _run_phase(
                    run_dir=run_dir,
                    state=state,
                    phase="memory",
                    prompt_text=prompt,
                    model=model,
                    round_index=round_index,
                )
                _copy_phase_artifacts(eval_run, round_dir, phase="memory", report=memory_report)
                _mark_stage(run_dir, state, stage, "completed")
                _mark_stage(
                    run_dir,
                    state,
                    f"round_{round_index}_memory_evaluate",
                    "completed",
                    {"average_score": _score_summary(memory_report, model).get("average_score")},
                )
            else:
                memory_report = read_json(memory_eval_path)

            score_stage = f"round_{round_index}_score_delta"
            if score_stage not in state.get("completed_stages", []):
                delta = _round_delta(
                    baseline_report=baseline_report,
                    memory_report=memory_report,
                    model=model,
                )
                delta["round"] = round_index
                _write_round_comparison(round_dir, round_index, delta)
                if not any(row.get("round") == round_index for row in round_results):
                    round_results.append(delta)
                state["round_results"] = round_results
                _mark_stage(run_dir, state, score_stage, "completed", delta)

        decision, reason = _final_decision(
            state,
            require_consecutive_improvement=bool(state.get("require_consecutive_improvement")),
            min_improvement=float(state.get("min_improvement") or 1.0),
        )
        state["final_decision"] = decision
        state["status"] = "completed" if decision == "PASS" else "failed"
        state["can_resume"] = False
        state["last_error"] = None if decision == "PASS" else reason
        _mark_stage(run_dir, state, "final_decision", "completed", {"decision": decision, "reason": reason})
        _write_final_summary(run_dir, state, reason=reason)
        return _finalize_result(workspace, run_dir, state)
    except ValueError as exc:
        message = str(exc)
        retryable = _is_retryable_provider_error(message)
        _append_jsonl(
            run_dir / "provider_error_log.jsonl",
            {
                "created_at": utc_now(),
                "stage": state.get("current_stage"),
                "error_message_masked": message,
                "retryable": retryable,
            },
        )
        _block(run_dir, state, f"provider_or_validation_error: {message}", can_resume=retryable)
        return _finalize_result(workspace, run_dir, state)


def approved_memory_validation_status(workspace: Workspace, *, run: str) -> dict[str, Any]:
    run_dir = resolve_validation_run(workspace, run)
    state = read_json(run_dir / "validation_job_state.json")
    return {
        "validation_run_id": state.get("validation_run_id"),
        "run_dir": str(run_dir),
        "status": state.get("status"),
        "final_decision": state.get("final_decision"),
        "current_stage": state.get("current_stage"),
        "completed_stages": state.get("completed_stages", []),
        "pending_stages": state.get("pending_stages", []),
        "round_results": state.get("round_results", []),
        "last_error": state.get("last_error"),
        "can_resume": state.get("can_resume"),
        "next_command": state.get("next_command"),
    }
