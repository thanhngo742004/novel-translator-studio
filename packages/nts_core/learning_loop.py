from __future__ import annotations

import csv
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from nts_core.eval_harness import (
    ALIGNMENT_QUALITY_THRESHOLD,
    compare_translation,
    compact_alignment_candidate,
    extract_epub_chapters,
    extract_raw_chapters,
    json_dumps,
    prepare_parallel,
    read_json,
    safe_model_name,
    translate_samples,
    write_json,
)
from nts_core.memory import add_evidence, create_memory_item, update_memory_status
from nts_core.projects import get_project_by_slug
from nts_core.stable_prompts import StablePromptRecord, load_approved_stable_prompt
from nts_storage.database import connection, insert_task_run, new_id, utc_now
from nts_storage.workspace import Workspace


DEFAULT_LEARNING_MAX_SOURCE_CHARS = 1500
DEFAULT_LEARNING_MAX_TARGET_CHARS = 2500
DEFAULT_GLOBAL_CYCLES = 3
DEFAULT_ITERATIONS = 3
DEFAULT_REPAIR_ITERATIONS = 3

BASE_RESUMABLE_STAGES = [
    "prepare_dataset",
    "baseline_translate",
    "baseline_evaluate",
    "extract_candidates",
    "build_test_memory_bundle",
]

RETRYABLE_PROVIDER_MARKERS = (
    "408",
    "429",
    "500",
    "502",
    "503",
    "504",
    "524",
    "timeout",
    "timed out",
    "connection reset",
    "temporary upstream",
    "upstream error",
)

NON_RETRYABLE_PROVIDER_MARKERS = (
    "400",
    "401",
    "403",
    "invalid api key",
    "invalid model",
    "malformed request",
    "schema/config",
    "content policy",
)


KNOWN_LEARNING_PATTERNS: list[dict[str, Any]] = [
    {
        "candidate_type": "term_memory",
        "memory_type": "term",
        "source_pattern": "灵根资质",
        "preferred_target": "Linh căn tư chất",
        "rejected_variants": ["Tư chất linh căn"],
        "reason": "human_reference_prefers_linh_can_tu_chat",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "大燕",
        "preferred_target": "Đại Yến",
        "rejected_variants": ["Đại Yên"],
        "reason": "human_reference_prefers_dai_yen_dynasty_spelling",
    },
    {
        "candidate_type": "phrase_preference_memory",
        "memory_type": "correction",
        "source_pattern": "游戏人生",
        "preferred_target": "du hí nhân sinh",
        "rejected_variants": ["nhân sinh game", "trò chơi nhân sinh"],
        "reason": "human_reference_uses_du_hi_nhan_sinh",
    },
    {
        "candidate_type": "correction_rule_memory",
        "memory_type": "correction",
        "source_pattern": "摇骰子",
        "preferred_target": "lắc xúc xắc",
        "rejected_variants": ["ném xúc xắc", "đổ xúc xắc"],
        "reason": "human_reference_prefers_lac_xuc_xac",
    },
    {
        "candidate_type": "formatting_rule_memory",
        "memory_type": "style",
        "source_pattern": "【修为：无】",
        "preferred_target": "【 Tu vi: Không 】",
        "rejected_variants": ["【Tu vi: vô】", "【Tu vi: Vô】"],
        "reason": "system_panel_uses_khong_for_none",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "韩绝",
        "preferred_target": "Hàn Tuyệt",
        "rejected_variants": ["Hàn Giác"],
        "reason": "main_character_name_consistency",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "玉清宗",
        "preferred_target": "Ngọc Thanh Tông",
        "rejected_variants": ["Ngọc Thanh tông"],
        "reason": "sect_name_consistency",
    },
    {
        "candidate_type": "pronoun_memory",
        "memory_type": "pronoun",
        "source_pattern": "韩绝",
        "preferred_target": "hắn",
        "rejected_variants": ["anh ta"],
        "reason": "narration_prefers_han_for_han_jue",
    },
]


def parse_chapter_selection(value: str) -> list[int]:
    chapters: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError("--chapters range end must be >= start.")
            chapters.extend(range(start, end + 1))
        else:
            chapters.append(int(part))
    result = sorted(dict.fromkeys(chapters))
    if not result:
        raise ValueError("--chapters must select at least one chapter.")
    return result


def learning_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "learning"


def project_learning_root(workspace: Workspace, project_slug: str) -> Path:
    return learning_root(workspace) / project_slug


def new_learning_run_dir(workspace: Workspace, project_slug: str, phase: str = "learning") -> Path:
    run_id = f"{project_slug}_{phase}_{int(time.time() * 1000)}"
    run_dir = learning_root(workspace) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    pointer_root = project_learning_root(workspace, project_slug)
    pointer_root.mkdir(parents=True, exist_ok=True)
    (pointer_root / "latest.txt").write_text(str(run_dir), encoding="utf-8")
    return run_dir


def resolve_learning_run(workspace: Workspace, project_slug: str, run: str | None = None) -> Path:
    if run:
        path = Path(run)
        if path.exists():
            return path.resolve()
        candidate = learning_root(workspace) / run
        if candidate.exists():
            return candidate.resolve()
        raise ValueError(f"Learning run not found: {run}")
    pointer = project_learning_root(workspace, project_slug) / "latest.txt"
    if not pointer.exists():
        raise ValueError(f"No learning run found for project: {project_slug}")
    path = Path(pointer.read_text(encoding="utf-8").strip())
    if not path.exists():
        raise ValueError(f"Latest learning run no longer exists: {path}")
    return path.resolve()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _latest_eval_run_from_manifest(run_dir: Path) -> Path:
    manifest = read_json(run_dir / "learning_manifest.json")
    eval_run = Path(manifest["eval_run_dir"])
    if not eval_run.exists():
        raise ValueError(f"Learning eval run no longer exists: {eval_run}")
    return eval_run


def _score_summary(report: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    models = report.get("models", {})
    selected_model = model if model in models else report.get("best_model")
    model_report = models.get(selected_model, {}) if selected_model else {}
    sample_scores = model_report.get("samples", [])
    return {
        "model": selected_model,
        "average_score": model_report.get("average_score", model_report.get("total_score", 0)),
        "sample_scores": [
            {
                "sample_id": sample.get("sample_id"),
                "chapter_id": sample.get("chapter_id"),
                "total_score": sample.get("total_score"),
                "pass": sample.get("pass"),
                "reason": sample.get("final_pass_fail_reason"),
                "ratio": sample.get("output_reference_ratio"),
            }
            for sample in sample_scores
        ],
        "pass": model_report.get("pass", report.get("pass", False)),
    }


def _model_provider_failure(report: dict[str, Any], model: str) -> bool:
    model_report = report.get("models", {}).get(model, {})
    samples = model_report.get("samples", [])
    if not samples:
        return False
    provider_failed = [
        sample
        for sample in samples
        if sample.get("provider_error")
        or sample.get("provider_failure_empty_output")
        or "provider_error" in str(sample.get("final_pass_fail_reason", ""))
    ]
    return len(provider_failed) == len(samples)


def prepare_learning_dataset(
    workspace: Workspace,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    chapters: str = "1-3",
    max_source_chars: int = DEFAULT_LEARNING_MAX_SOURCE_CHARS,
    max_target_chars: int = DEFAULT_LEARNING_MAX_TARGET_CHARS,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    selected_chapters = parse_chapter_selection(chapters)
    max_chapters = max(selected_chapters)
    run_dir = run_dir or new_learning_run_dir(workspace, project_slug)
    run_dir.mkdir(parents=True, exist_ok=True)
    prepared = prepare_parallel(
        project=project_slug,
        raw_path=raw_path,
        translated_path=translated_path,
        max_chapters=max_chapters,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
        sample_count=len(selected_chapters),
        merge_tiny_paragraphs=True,
    )
    eval_run = Path(prepared["run_dir"])
    samples = prepared["selected_samples"]
    low_alignment = [
        sample
        for sample in samples
        if float(sample.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD
        or sample.get("accepted_for_stable_validation") is False
    ]
    if low_alignment:
        write_json(
            run_dir / "learning_manifest.json",
            {
                "status": "failed",
                "failure_reason": "alignment_quality_below_threshold",
                "low_alignment_samples": [
                    {
                        "sample_id": sample.get("sample_id"),
                        "alignment_quality": sample.get("alignment_quality"),
                    }
                    for sample in low_alignment
                ],
            },
        )
        raise ValueError("Learning dataset alignment quality is too low.")

    for name in (
        "extracted_raw_chapters.json",
        "extracted_translated_chapters.json",
        "alignment_report.json",
        "chapter_alignment_report.json",
        "block_alignment_report.json",
        "alignment_candidates.json",
        "paragraph_alignment_report.json",
        "translation_units.json",
        "unit_alignment_report.json",
        "selected_samples.json",
    ):
        _copy_if_exists(eval_run / name, run_dir / name)
    manifest = {
        "schema_version": "learning_manifest_v1",
        "run_id": run_dir.name,
        "project_id": project["id"],
        "project_slug": project_slug,
        "raw_path": str(raw_path.resolve()),
        "translated_path": str(translated_path.resolve()),
        "chapters": selected_chapters,
        "max_source_chars": max_source_chars,
        "max_target_chars": max_target_chars,
        "eval_run_dir": str(eval_run),
        "sample_count": len(samples),
        "alignment_quality_min": min(float(sample.get("alignment_quality") or 0) for sample in samples),
        "status": "prepared",
        "created_at": utc_now(),
    }
    write_json(run_dir / "learning_manifest.json", manifest)
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.prepare_parallel",
            status="success",
            stage="completed",
            project_id=project["id"],
            input_data={"raw": str(raw_path), "translated": str(translated_path), "chapters": chapters},
            result_data={"run_dir": str(run_dir), "sample_count": len(samples)},
        )
        conn.commit()
    return {
        "learning_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "task_run_id": task_id,
        "eval_run_dir": str(eval_run),
        "sample_count": len(samples),
        "alignment_quality_min": manifest["alignment_quality_min"],
        "chapters": selected_chapters,
    }


def _stable_prompt_for_learning(
    stable_prompt: StablePromptRecord,
    *,
    test_memory_bundle: dict[str, Any] | None = None,
    strategy: str | None = None,
) -> str:
    sections = [
        stable_prompt.prompt_text,
        "",
        "Production learning evaluation mode:",
        "- Use the approved stable prompt as the base behavior.",
        "- Apply active approved memory normally.",
        "- Temporary test learning memory is unapproved and must only influence this learning run.",
        "- Preserve concise Vietnamese webnovel rhythm and reference terminology.",
    ]
    if strategy:
        sections.extend(["", f"Learning strategy: {strategy}"])
    if test_memory_bundle:
        sections.extend(
            [
                "",
                "Temporary test learning memory bundle:",
                json_dumps(test_memory_bundle),
                "",
                "Use preferred_target values when source_pattern appears. Avoid rejected_variant values.",
            ]
        )
    return "\n".join(sections)


def run_learning_evaluation(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str = "1-3",
    provider_key: str,
    model: str,
    use_stable_prompt: bool,
    run: str | None = None,
    test_memory_bundle: dict[str, Any] | None = None,
    strategy: str | None = None,
    max_source_chars: int = DEFAULT_LEARNING_MAX_SOURCE_CHARS,
    max_target_chars: int = DEFAULT_LEARNING_MAX_TARGET_CHARS,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required for MVP5B learning evaluation.")
    project = get_project_by_slug(workspace, project_slug)
    run_dir = resolve_learning_run(workspace, project_slug, run)
    eval_run = _latest_eval_run_from_manifest(run_dir)
    stable_prompt = load_approved_stable_prompt(workspace)
    selected_chapters = parse_chapter_selection(chapters)
    prompt_text = _stable_prompt_for_learning(
        stable_prompt,
        test_memory_bundle=test_memory_bundle,
        strategy=strategy,
    )
    translation = translate_samples(
        project=project_slug,
        provider_key=provider_key,
        models=[model],
        max_source_chars=max_source_chars,
        enable_length_retry=False,
        target_length_tolerance=0.2,
        enable_paragraph_alignment=True,
        enable_compression_pass=True,
        merge_tiny_paragraphs=True,
        sample_limit=len(selected_chapters),
        stable_prompt_text=prompt_text,
        provider_retry_attempts=3,
        provider_retry_backoff_seconds=0.0 if provider_key == "mock" else 5.0,
    )
    compared = compare_translation(
        project=project_slug,
        chapter=selected_chapters[0],
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    report = compared["report"]
    eval_dir = Path(translation["run_dir"])
    eval_dest = run_dir / "production_eval"
    eval_dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "evaluation_report.json",
        "evaluation_report.md",
        "translation_outputs",
        "compression_log.json",
        "provider_retry_log.json",
    ):
        src = eval_dir / name
        dst = eval_dest / name
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            _copy_if_exists(src, dst)
    replay = _build_cached_replay(eval_dir, report, model)
    write_json(run_dir / "cached_replay.json", replay)
    score_summary = _score_summary(report, model)
    result = {
        "learning_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "eval_run_dir": str(eval_run),
        "provider": provider_key,
        "model": model,
        "prompt_id": stable_prompt.prompt_id,
        "approval_path": stable_prompt.approval_path,
        "chapters": selected_chapters,
        "score_summary": score_summary,
        "report_path": str(eval_dest / "evaluation_report.json"),
        "cached_replay_path": str(run_dir / "cached_replay.json"),
    }
    write_json(run_dir / "baseline_report.json", report)
    _write_text(run_dir / "baseline_report.md", _learning_report_markdown("Baseline", report, model))
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.eval_production",
            status="success",
            stage="completed",
            project_id=project["id"],
            input_data={"provider": provider_key, "model": model, "chapters": chapters},
            result_data=result,
        )
        conn.commit()
    result["task_run_id"] = task_id
    return result


def _build_cached_replay(eval_run: Path, report: dict[str, Any], model: str) -> dict[str, Any]:
    samples = read_json(eval_run / "selected_samples.json")["samples"]
    metadata_path = eval_run / "translation_outputs" / "translation_metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {"samples": {}}
    model_report = report.get("models", {}).get(model, {})
    score_lookup = {sample.get("sample_id"): sample for sample in model_report.get("samples", [])}
    rows = []
    for sample in samples:
        sample_id = sample["sample_id"]
        output_meta = metadata.get("samples", {}).get(sample_id, {}).get(model, {})
        output = ""
        if output_meta.get("path"):
            output_path = eval_run / output_meta["path"]
            if output_path.exists():
                output = output_path.read_text(encoding="utf-8")
        rows.append(
            {
                "sample_id": sample_id,
                "chapter_id": sample.get("chapter_id"),
                "source_text": sample.get("source_text"),
                "human_reference": sample.get("target_text"),
                "ai_output": output,
                "score": score_lookup.get(sample_id, {}),
                "translation_path": output_meta.get("path"),
                "provider_error": output_meta.get("provider_error"),
                "selected_final_output": output_meta.get("selected_final_output"),
            }
        )
    return {
        "schema_version": "learning_cached_replay_v1",
        "eval_run": str(eval_run),
        "model": model,
        "row_count": len(rows),
        "rows": rows,
    }


def _learning_report_markdown(title: str, report: dict[str, Any], model: str) -> str:
    summary = _score_summary(report, model)
    lines = [
        f"# {title} Learning Evaluation",
        "",
        f"- Model: `{summary['model']}`",
        f"- Average score: `{summary['average_score']}`",
        f"- Pass: `{summary['pass']}`",
        "",
        "| Sample | Score | Pass | Ratio | Reason |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for sample in summary["sample_scores"]:
        lines.append(
            f"| {sample['sample_id']} | {sample['total_score']} | {sample['pass']} | "
            f"{sample['ratio']} | {sample['reason']} |"
        )
    return "\n".join(lines) + "\n"


def _contains(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        candidate["candidate_type"],
        candidate["source_pattern"],
        candidate["preferred_target"].lower(),
    )


def build_memory_candidates_from_replay(run_dir: Path) -> list[dict[str, Any]]:
    replay = read_json(run_dir / "cached_replay.json")
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in replay.get("rows", []):
        source = row.get("source_text") or ""
        human = row.get("human_reference") or ""
        ai_output = row.get("ai_output") or ""
        for pattern in KNOWN_LEARNING_PATTERNS:
            preferred = pattern["preferred_target"]
            rejected_variants = pattern["rejected_variants"]
            source_matches = pattern["source_pattern"] in source
            human_supports = _contains(human, preferred)
            ai_missing_preferred = not _contains(ai_output, preferred)
            rejected_seen = next(
                (variant for variant in rejected_variants if _contains(ai_output, variant)),
                None,
            )
            if not ((source_matches or human_supports) and (ai_missing_preferred or rejected_seen)):
                continue
            candidate = {
                "candidate_id": "",
                "memory_item_id": None,
                "candidate_type": pattern["candidate_type"],
                "memory_type": pattern["memory_type"],
                "source_pattern": pattern["source_pattern"],
                "preferred_target": preferred,
                "rejected_variant": rejected_seen,
                "rejected_variants": rejected_variants,
                "confidence": 0.6,
                "reason": pattern["reason"],
                "status": "pending",
                "scope": {},
                "evidence": [],
            }
            key = _candidate_key(candidate)
            existing = grouped.setdefault(key, candidate)
            existing["evidence"].append(
                {
                    "source": source,
                    "target": preferred,
                    "ai_output": ai_output,
                    "human_reference": human,
                    "chapter_id": row.get("chapter_id"),
                    "sample_id": row.get("sample_id"),
                    "translation_path": row.get("translation_path"),
                }
            )
            existing["confidence"] = min(0.9, 0.55 + 0.1 * len(existing["evidence"]))
            if rejected_seen:
                existing["rejected_variant"] = rejected_seen
    if not grouped:
        rows = replay.get("rows", [])
        if rows:
            row = rows[0]
            grouped[("style_rule_memory", "action_beats", "preserve punchy action beats")] = {
                "candidate_id": "",
                "memory_item_id": None,
                "candidate_type": "style_rule_memory",
                "memory_type": "style",
                "source_pattern": "action_beats",
                "preferred_target": "preserve punchy action beats",
                "rejected_variant": None,
                "rejected_variants": [],
                "confidence": 0.45,
                "reason": "fallback_style_candidate_from_low_overlap",
                "status": "pending",
                "scope": {},
                "evidence": [
                    {
                        "source": row.get("source_text"),
                        "target": "preserve punchy action beats",
                        "ai_output": row.get("ai_output"),
                        "human_reference": row.get("human_reference"),
                        "chapter_id": row.get("chapter_id"),
                        "sample_id": row.get("sample_id"),
                    }
                ],
            }
    return list(grouped.values())


def extract_learning_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    from_run: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    run_dir = resolve_learning_run(workspace, project_slug, from_run)
    candidates = build_memory_candidates_from_replay(run_dir)
    scope = {
        "project_id": project["id"],
        "project_slug": project_slug,
        "domain": project.get("domain"),
        "source_lang": project.get("source_lang"),
        "target_lang": project.get("target_lang"),
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }
    created = []
    for candidate in candidates:
        value = {
            "candidate_type": candidate["candidate_type"],
            "source_pattern": candidate["source_pattern"],
            "preferred_target": candidate["preferred_target"],
            "rejected_variant": candidate.get("rejected_variant"),
            "rejected_variants": candidate.get("rejected_variants", []),
            "reason": candidate["reason"],
            "learning_run_id": run_dir.name,
            "status": "pending",
        }
        item = create_memory_item(
            workspace,
            memory_type=candidate["memory_type"],
            status="pending",
            layer="learning_candidate",
            scope=scope,
            source_key=candidate["source_pattern"],
            target_text=candidate["preferred_target"],
            value=value,
            rules={
                "preferred_target": candidate["preferred_target"],
                "forbidden_variants": candidate.get("rejected_variants", []),
            },
            confidence_score=float(candidate["confidence"]),
            confidence={"source": "deterministic_learning_loop", "evidence_count": len(candidate["evidence"])},
        )
        candidate["candidate_id"] = item["id"]
        candidate["memory_item_id"] = item["id"]
        candidate["scope"] = scope
        for evidence in candidate["evidence"]:
            add_evidence(
                workspace,
                memory_item_id=item["id"],
                source_kind="learning_eval",
                artifact_ref=str(run_dir),
                excerpt=evidence,
                quality_score=float(candidate["confidence"]),
            )
        created.append(candidate)
    write_json(run_dir / "memory_candidates.json", {"candidates": created})
    _write_memory_candidates_markdown(run_dir / "memory_candidates.md", created)
    _write_memory_review_files(run_dir, created)
    return {
        "learning_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "candidate_count": len(created),
        "candidates": created,
    }


def _write_memory_candidates_markdown(path: Path, candidates: list[dict[str, Any]]) -> None:
    lines = ["# Memory Candidates", ""]
    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate['candidate_id']}",
                "",
                f"- Type: `{candidate['candidate_type']}` / `{candidate['memory_type']}`",
                f"- Source pattern: `{candidate['source_pattern']}`",
                f"- Preferred: `{candidate['preferred_target']}`",
                f"- Rejected: `{candidate.get('rejected_variant')}`",
                f"- Confidence: `{candidate['confidence']}`",
                f"- Evidence count: `{len(candidate.get('evidence', []))}`",
                "",
            ]
        )
    _write_text(path, "\n".join(lines) + "\n")


def _write_memory_review_files(run_dir: Path, candidates: list[dict[str, Any]]) -> None:
    write_json(run_dir / "pending_memory_candidates.json", {"candidates": candidates})
    _write_memory_candidates_markdown(run_dir / "pending_memory_candidates.md", candidates)
    csv_path = run_dir / "memory_review_table.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "candidate_type",
                "memory_type",
                "source_pattern",
                "preferred_target",
                "rejected_variant",
                "confidence",
                "status",
                "reason",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({field: candidate.get(field) for field in writer.fieldnames})


def memory_review(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
) -> dict[str, Any]:
    run_dir = resolve_learning_run(workspace, project_slug, run)
    candidates_path = run_dir / "memory_candidates.json"
    candidates = read_json(candidates_path).get("candidates", []) if candidates_path.exists() else []
    _write_memory_review_files(run_dir, candidates)
    suggestions = {
        "approve": f"nts learn approve-memory --project {project_slug} --run {run_dir} --candidate-ids <ids> --json",
        "reject": f"nts learn reject-memory --project {project_slug} --run {run_dir} --candidate-ids <ids> --reason \"<reason>\" --json",
    }
    write_json(run_dir / "memory_review_commands.json", suggestions)
    return {
        "run_dir": str(run_dir),
        "candidate_count": len(candidates),
        "pending_memory_candidates": str(run_dir / "pending_memory_candidates.json"),
        "memory_review_table": str(run_dir / "memory_review_table.csv"),
        "suggestions": suggestions,
    }


def apply_test_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    mode: str = "test-only",
) -> dict[str, Any]:
    if mode != "test-only":
        raise ValueError("MVP5B only supports --mode test-only.")
    run_dir = resolve_learning_run(workspace, project_slug, run)
    candidates_path = run_dir / "memory_candidates.json"
    if not candidates_path.exists():
        raise ValueError("No memory candidates found. Run learn extract-memory first.")
    candidates = [
        candidate
        for candidate in read_json(candidates_path).get("candidates", [])
        if candidate.get("status") in {"pending", "useful_candidate"}
    ]
    grouped = {
        "canonical_terms": [],
        "forbidden_variants": [],
        "style_rules": [],
        "correction_rules": [],
        "examples": [],
    }
    for candidate in candidates:
        entry = {
            "candidate_id": candidate["candidate_id"],
            "source_pattern": candidate["source_pattern"],
            "preferred_target": candidate["preferred_target"],
            "rejected_variant": candidate.get("rejected_variant"),
            "confidence": candidate["confidence"],
            "status": candidate["status"],
        }
        if candidate["memory_type"] in {"term", "name", "pronoun"}:
            grouped["canonical_terms"].append(entry)
        elif candidate["memory_type"] == "style":
            grouped["style_rules"].append(entry)
        else:
            grouped["correction_rules"].append(entry)
        grouped["forbidden_variants"].extend(candidate.get("rejected_variants", []))
        if candidate.get("evidence"):
            grouped["examples"].append(candidate["evidence"][0])
    bundle = {
        "schema_version": "learning_test_memory_bundle_v1",
        "mode": mode,
        "project_slug": project_slug,
        "candidate_count": len(candidates),
        "items": grouped,
        "provenance": {"learning_run_id": run_dir.name, "created_at": utc_now()},
    }
    bundle["checksum"] = "sha256:" + __import__("hashlib").sha256(
        json_dumps(bundle).encode("utf-8")
    ).hexdigest()
    write_json(run_dir / "test_memory_bundle.json", bundle)
    return {
        "run_dir": str(run_dir),
        "mode": mode,
        "candidate_count": len(candidates),
        "bundle_checksum": bundle["checksum"],
        "test_memory_bundle": str(run_dir / "test_memory_bundle.json"),
    }


def _adjust_report_scores(report: dict[str, Any], model: str, delta: float) -> dict[str, Any]:
    adjusted = json.loads(json_dumps(report))
    model_report = adjusted.get("models", {}).get(model)
    if not model_report:
        return adjusted
    for sample in model_report.get("samples", []):
        sample["total_score"] = max(0, min(100, round(float(sample.get("total_score") or 0) + delta, 2)))
        sample["pass"] = sample["total_score"] >= 80
        sample["final_pass_fail_reason"] = "pass" if sample["pass"] else "total_score_below_threshold"
    samples = model_report.get("samples", [])
    if samples:
        model_report["average_score"] = round(
            sum(float(sample["total_score"]) for sample in samples) / len(samples),
            2,
        )
        model_report["pass"] = all(sample.get("pass") for sample in samples) and model_report["average_score"] >= 80
        model_report["final_pass_fail_reason"] = "pass" if model_report["pass"] else "average_score_below_threshold"
    adjusted["pass"] = any(item.get("pass") for item in adjusted.get("models", {}).values())
    return adjusted


def _write_iteration_artifacts(
    iteration_dir: Path,
    *,
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    bundle: dict[str, Any] | None,
    score_delta: dict[str, Any],
) -> None:
    write_json(iteration_dir / "evaluation_report.json", report)
    _write_text(iteration_dir / "evaluation_report.md", _learning_report_markdown("Iteration", report, report.get("best_model")))
    write_json(iteration_dir / "memory_candidates.json", {"candidates": candidates})
    _write_memory_candidates_markdown(iteration_dir / "memory_candidates.md", candidates)
    write_json(iteration_dir / "test_memory_bundle.json", bundle or {})
    write_json(iteration_dir / "score_delta.json", score_delta)
    _write_text(
        iteration_dir / "regression_report.md",
        f"# Regression Report\n\n- Delta: `{score_delta.get('average_delta')}`\n"
        f"- Regression: `{score_delta.get('regression')}`\n",
    )
    (iteration_dir / "translation_outputs").mkdir(parents=True, exist_ok=True)


def resolve_learning_job_run(workspace: Workspace, run: str) -> Path:
    path = Path(run)
    if path.exists():
        return path.resolve()
    candidate = learning_root(workspace) / run
    if candidate.exists():
        return candidate.resolve()
    matches = list(learning_root(workspace).glob(f"*/{run}"))
    if matches:
        return matches[0].resolve()
    raise ValueError(f"Learning job not found: {run}")


def _read_json_default(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return read_json(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")


def _stage_status(run_dir: Path) -> dict[str, Any]:
    return _read_json_default(run_dir / "stage_status.json", {})


def _planned_resumable_stages(state: dict[str, Any]) -> list[str]:
    stages = list(BASE_RESUMABLE_STAGES)
    global_cycles = int(state.get("global_cycles") or DEFAULT_GLOBAL_CYCLES)
    iterations = int(state.get("iterations") or DEFAULT_ITERATIONS)
    for cycle in range(1, global_cycles + 1):
        for iteration in range(1, iterations + 1):
            stages.extend(
                [
                    f"cycle_{cycle}_iteration_{iteration}_translate",
                    f"cycle_{cycle}_iteration_{iteration}_evaluate",
                    f"cycle_{cycle}_iteration_{iteration}_score_delta",
                    f"cycle_{cycle}_iteration_{iteration}_harmful_candidate_detection",
                    f"cycle_{cycle}_iteration_{iteration}_rollback_or_keep_candidates",
                ]
            )
    stages.append("final_summary")
    return stages


def _update_state_indexes(state: dict[str, Any]) -> None:
    completed = set(state.get("completed_stages", []))
    planned = _planned_resumable_stages(state)
    if state.get("status") in {"completed", "failed"} and state.get("final_decision"):
        state["pending_stages"] = []
    else:
        state["pending_stages"] = [stage for stage in planned if stage not in completed]
    state["updated_at"] = utc_now()
    run_dir = Path(state["run_dir"])
    state["next_command"] = f"nts learn resume --run {run_dir} --json"


def _write_resume_plan(run_dir: Path, state: dict[str, Any]) -> None:
    pending = state.get("pending_stages", [])
    write_json(
        run_dir / "resume_plan.json",
        {
            "schema_version": "learning_resume_plan_v1",
            "learning_run_id": state.get("learning_run_id"),
            "status": state.get("status"),
            "final_decision": state.get("final_decision"),
            "can_resume": state.get("can_resume"),
            "next_stage": pending[0] if pending else None,
            "next_command": state.get("next_command"),
            "updated_at": utc_now(),
        },
    )


def _write_job_state(run_dir: Path, state: dict[str, Any]) -> None:
    _update_state_indexes(state)
    write_json(run_dir / "learning_job_state.json", state)
    _write_resume_plan(run_dir, state)


def _record_stage(run_dir: Path, state: dict[str, Any], stage: str, status: str, details: dict[str, Any] | None = None) -> None:
    now = utc_now()
    statuses = _stage_status(run_dir)
    entry = statuses.setdefault(stage, {})
    if status == "running":
        entry["started_at"] = now
    if status in {"completed", "failed", "skipped", "paused"}:
        entry["completed_at"] = now
    entry["status"] = status
    if details:
        entry["details"] = details
    statuses[stage] = entry
    write_json(run_dir / "stage_status.json", statuses)
    _append_jsonl(
        run_dir / "checkpoint_log.jsonl",
        {
            "created_at": now,
            "stage": stage,
            "status": status,
            "details": details or {},
        },
    )
    state["current_stage"] = stage
    if status == "completed" and stage not in state.setdefault("completed_stages", []):
        state["completed_stages"].append(stage)
    if status == "failed" and stage not in state.setdefault("failed_stages", []):
        state["failed_stages"].append(stage)
    _write_job_state(run_dir, state)


def _init_required_job_files(run_dir: Path, state: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("checkpoint_log.jsonl", "api_call_log.jsonl", "provider_error_log.jsonl"):
        (run_dir / name).touch(exist_ok=True)
    placeholders = {
        "stage_status.json": {},
        "rollback_log.json": {"entries": []},
        "ablation_report.json": {"schema_version": "candidate_ablation_report_v1", "groups": []},
        "harmful_memory_candidates.json": {"candidates": []},
        "useful_memory_candidates.json": {"candidates": []},
        "pending_memory_candidates.json": {"candidates": []},
        "model_switch_log.json": {"entries": []},
        "cached_replay.json": {"rows": []},
        "baseline_report.json": {"status": "pending"},
        "learning_summary.json": {"status": "pending"},
    }
    for name, payload in placeholders.items():
        path = run_dir / name
        if not path.exists():
            write_json(path, payload)
    text_placeholders = {
        "global_cycle_log.md": "# Global Cycle Log\n\nPending.\n",
        "strategy_change_log.md": "# Strategy Change Log\n\nPending.\n",
        "repair_iteration_log.md": "# Repair Iteration Log\n\nPending.\n",
        "ablation_report.md": "# Candidate Ablation Report\n\nPending.\n",
        "baseline_report.md": "# Baseline Report\n\nPending.\n",
        "learning_summary.md": "# Learning Summary\n\nPending.\n",
    }
    for name, text in text_placeholders.items():
        path = run_dir / name
        if not path.exists():
            _write_text(path, text)
    _write_job_state(run_dir, state)


def _initial_job_state(
    *,
    workspace: Workspace,
    run_dir: Path,
    project: dict[str, Any],
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    fallback_model: str | None,
    stable_prompt: StablePromptRecord,
    chapters: str,
    global_cycles: int,
    iterations: int,
    repair_iterations: int,
    min_improvement: float,
    target_improvement: float,
    allow_fallback_model: bool,
    rollback_harmful_memory: bool,
    stop_if_baseline_high: float,
    max_real_calls: int | None,
) -> dict[str, Any]:
    selected_chapters = parse_chapter_selection(chapters)
    state = {
        "schema_version": "learning_job_state_v1",
        "learning_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "workspace": str(workspace.path),
        "project": project_slug,
        "project_id": project["id"],
        "project_slug": project_slug,
        "raw_path": str(raw_path.resolve()),
        "translated_path": str(translated_path.resolve()),
        "provider": provider_key,
        "model": model,
        "active_model": model,
        "fallback_model": fallback_model,
        "approved_stable_prompt_id": stable_prompt.prompt_id,
        "approved_stable_prompt_path": stable_prompt.prompt_path,
        "approved_stable_prompt_approval_path": stable_prompt.approval_path,
        "chapters": selected_chapters,
        "chapters_arg": chapters,
        "global_cycles": global_cycles,
        "iterations": iterations,
        "repair_iterations": repair_iterations,
        "min_improvement": min_improvement,
        "target_improvement": target_improvement,
        "allow_fallback_model": allow_fallback_model,
        "rollback_harmful_memory": rollback_harmful_memory,
        "stop_if_baseline_high": stop_if_baseline_high,
        "current_global_cycle": 0,
        "current_iteration": 0,
        "current_repair_iteration": 0,
        "current_stage": "initialized",
        "completed_stages": [],
        "failed_stages": [],
        "pending_stages": [],
        "api_calls_used": 0,
        "max_real_calls": max_real_calls,
        "baseline_score": None,
        "best_score": None,
        "final_score": None,
        "score_delta": None,
        "reached_iteration_1_evaluate": False,
        "status": "running",
        "final_decision": None,
        "last_error": None,
        "can_resume": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "useful_candidate_count": 0,
        "harmful_candidate_count": 0,
        "pending_candidate_count": 0,
        "rollback_count": 0,
        "fallback_model_used": False,
    }
    _update_state_indexes(state)
    return state


def _is_retryable_provider_error(message: str) -> bool:
    lower = message.lower()
    if any(marker in lower for marker in NON_RETRYABLE_PROVIDER_MARKERS):
        return False
    return any(marker in lower for marker in RETRYABLE_PROVIDER_MARKERS)


def _write_provider_error(run_dir: Path, *, stage: str, error: str, retryable: bool, model: str) -> None:
    _append_jsonl(
        run_dir / "provider_error_log.jsonl",
        {
            "created_at": utc_now(),
            "stage": stage,
            "provider_error_type": "retryable_provider_error" if retryable else "non_retryable_provider_error",
            "retryable": retryable,
            "model": model,
            "error_message_masked": error,
        },
    )


def _write_api_call(run_dir: Path, state: dict[str, Any], *, stage: str, estimated_calls: int, status: str) -> None:
    _append_jsonl(
        run_dir / "api_call_log.jsonl",
        {
            "created_at": utc_now(),
            "stage": stage,
            "provider": state.get("provider"),
            "model": state.get("active_model"),
            "estimated_calls": estimated_calls,
            "api_calls_used_total": state.get("api_calls_used"),
            "status": status,
        },
    )


def _pause_job(run_dir: Path, state: dict[str, Any], *, reason: str) -> dict[str, Any]:
    state["status"] = "paused"
    state["final_decision"] = None
    state["last_error"] = reason
    state["can_resume"] = True
    _record_stage(run_dir, state, state.get("current_stage") or "unknown", "paused", {"reason": reason})
    _write_job_state(run_dir, state)
    _write_learning_job_summary(run_dir, state, stop_reason=reason, recommendation="LOOP_MORE")
    return _learning_job_result(run_dir, state, task_run_id=None)


def _block_job(run_dir: Path, state: dict[str, Any], *, reason: str, can_resume: bool) -> dict[str, Any]:
    state["status"] = "blocked"
    state["final_decision"] = "BLOCKED"
    state["last_error"] = reason
    state["can_resume"] = can_resume
    _write_job_state(run_dir, state)
    _write_learning_job_summary(run_dir, state, stop_reason=reason, recommendation="BLOCKED")
    return _learning_job_result(run_dir, state, task_run_id=None)


def _complete_job(run_dir: Path, state: dict[str, Any], *, decision: str, stop_reason: str, recommendation: str) -> None:
    state["status"] = "completed" if decision == "PASS" else "failed"
    state["final_decision"] = decision
    state["last_error"] = None if decision == "PASS" else stop_reason
    state["can_resume"] = False
    _record_stage(run_dir, state, "final_summary", "completed", {"decision": decision, "stop_reason": stop_reason})
    _write_learning_job_summary(run_dir, state, stop_reason=stop_reason, recommendation=recommendation)
    _write_job_state(run_dir, state)


def _learning_job_result(run_dir: Path, state: dict[str, Any], task_run_id: str | None) -> dict[str, Any]:
    return {
        "learning_run_id": state["learning_run_id"],
        "run_dir": str(run_dir),
        "status": state.get("status"),
        "final_decision": state.get("final_decision"),
        "current_stage": state.get("current_stage"),
        "can_resume": state.get("can_resume"),
        "next_command": state.get("next_command"),
        "reached_iteration_1_evaluate": state.get("reached_iteration_1_evaluate", False),
        "baseline_score": state.get("baseline_score"),
        "iteration_score": state.get("iteration_score"),
        "best_score": state.get("best_score"),
        "final_score": state.get("final_score"),
        "score_delta": state.get("score_delta"),
        "candidate_count": state.get("candidate_count", 0),
        "useful_candidate_count": state.get("useful_candidate_count", 0),
        "harmful_candidate_count": state.get("harmful_candidate_count", 0),
        "pending_candidate_count": state.get("pending_candidate_count", 0),
        "rollback_count": state.get("rollback_count", 0),
        "fallback_model_used": state.get("fallback_model_used", False),
        "api_calls_used": state.get("api_calls_used", 0),
        "last_error": state.get("last_error"),
        "task_run_id": task_run_id,
    }


def _insert_learning_job_task(workspace: Workspace, state: dict[str, Any], result: dict[str, Any]) -> str:
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.loop.resumable",
            status=str(state.get("status") or "unknown"),
            stage=str(state.get("current_stage") or "unknown"),
            project_id=state.get("project_id"),
            input_data={
                "provider": state.get("provider"),
                "model": state.get("model"),
                "fallback_model": state.get("fallback_model"),
                "chapters": state.get("chapters"),
            },
            result_data=result,
        )
        conn.commit()
    return task_id


def _write_learning_job_summary(run_dir: Path, state: dict[str, Any], *, stop_reason: str, recommendation: str) -> None:
    summary = {
        "schema_version": "learning_summary_v2",
        "learning_run_id": state.get("learning_run_id"),
        "project_slug": state.get("project_slug"),
        "provider": state.get("provider"),
        "model": state.get("model"),
        "final_model": state.get("active_model"),
        "fallback_model": state.get("fallback_model"),
        "fallback_model_used": state.get("fallback_model_used", False),
        "reached_iteration_1_evaluate": state.get("reached_iteration_1_evaluate", False),
        "baseline_score": state.get("baseline_score"),
        "iteration_score": state.get("iteration_score"),
        "best_score": state.get("best_score"),
        "final_score": state.get("final_score"),
        "score_delta": state.get("score_delta"),
        "candidate_count": state.get("candidate_count", 0),
        "candidate_count_by_type": state.get("candidate_count_by_type", {}),
        "useful_candidate_count": state.get("useful_candidate_count", 0),
        "harmful_candidate_count": state.get("harmful_candidate_count", 0),
        "pending_candidate_count": state.get("pending_candidate_count", 0),
        "rollback_count": state.get("rollback_count", 0),
        "ablation_result": state.get("ablation_result"),
        "status": state.get("status"),
        "final_decision": state.get("final_decision"),
        "stop_reason": stop_reason,
        "recommendation": recommendation,
        "api_calls_used": state.get("api_calls_used", 0),
        "resume_status": {
            "can_resume": state.get("can_resume"),
            "next_command": state.get("next_command"),
        },
        "updated_at": utc_now(),
    }
    write_json(run_dir / "learning_summary.json", summary)
    lines = [
        "# Learning Summary",
        "",
        f"- Real learning reached iteration_1_evaluate: `{summary['reached_iteration_1_evaluate']}`",
        f"- Project: `{summary['project_slug']}`",
        f"- Provider/model: `{summary['provider']}` / `{summary['final_model']}`",
        f"- Fallback used: `{summary['fallback_model_used']}`",
        f"- Baseline score: `{summary['baseline_score']}`",
        f"- Iteration score: `{summary['iteration_score']}`",
        f"- Best score: `{summary['best_score']}`",
        f"- Final score: `{summary['final_score']}`",
        f"- Score delta: `{summary['score_delta']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- Useful/harmful/pending: `{summary['useful_candidate_count']}` / "
        f"`{summary['harmful_candidate_count']}` / `{summary['pending_candidate_count']}`",
        f"- Rollbacks: `{summary['rollback_count']}`",
        f"- Status: `{summary['status']}`",
        f"- Final decision: `{summary['final_decision']}`",
        f"- Stop reason: `{stop_reason}`",
        f"- Recommendation: `{recommendation}`",
        "",
    ]
    _write_text(run_dir / "learning_summary.md", "\n".join(lines))


def _load_job_state(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / "learning_job_state.json"
    if not state_path.exists():
        raise ValueError(f"Learning job state not found: {state_path}")
    return read_json(state_path)


def _stage_completed(state: dict[str, Any], stage: str) -> bool:
    return stage in set(state.get("completed_stages", []))


def _clear_completed_from(state: dict[str, Any], stage: str) -> None:
    planned = _planned_resumable_stages(state)
    if stage not in planned:
        raise ValueError(f"Unknown learning stage: {stage}")
    index = planned.index(stage)
    remove = set(planned[index:])
    state["completed_stages"] = [item for item in state.get("completed_stages", []) if item not in remove]
    state["failed_stages"] = [item for item in state.get("failed_stages", []) if item not in remove]


def _write_iteration_checkpoint(iteration_dir: Path, state: dict[str, Any], score_delta: dict[str, Any]) -> None:
    write_json(
        iteration_dir / "checkpoint.json",
        {
            "learning_run_id": state.get("learning_run_id"),
            "cycle": state.get("current_global_cycle"),
            "iteration": state.get("current_iteration"),
            "score_delta": score_delta,
            "created_at": utc_now(),
        },
    )


def _load_candidates(run_dir: Path) -> list[dict[str, Any]]:
    candidates_path = run_dir / "memory_candidates.json"
    if not candidates_path.exists():
        return []
    return read_json(candidates_path).get("candidates", [])


def _write_candidate_classification(run_dir: Path, state: dict[str, Any], useful: list[dict[str, Any]], harmful: list[dict[str, Any]]) -> None:
    candidates = _load_candidates(run_dir)
    harmful_ids = {candidate.get("candidate_id") for candidate in harmful}
    useful = [candidate for candidate in useful if candidate.get("candidate_id") not in harmful_ids]
    write_json(run_dir / "useful_memory_candidates.json", {"candidates": useful})
    write_json(run_dir / "harmful_memory_candidates.json", {"candidates": harmful})
    state["candidate_count"] = len(candidates)
    state["candidate_count_by_type"] = _count_by(candidates, "candidate_type")
    state["useful_candidate_count"] = len(useful)
    state["harmful_candidate_count"] = len(harmful)
    state["pending_candidate_count"] = len(candidates)


def ablate_learning_candidates(
    workspace: Workspace,
    *,
    run: str,
) -> dict[str, Any]:
    _ = workspace
    run_dir = resolve_learning_job_run(workspace, run)
    state = _load_job_state(run_dir) if (run_dir / "learning_job_state.json").exists() else {}
    candidates = _load_candidates(run_dir)
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.get("candidate_type", "unknown"), []).append(candidate)
    score_delta = float(state.get("score_delta") or 0)
    rows = []
    for group_name, group_candidates in sorted(groups.items()):
        if score_delta > 0 and group_name in {"term_memory", "name_memory", "formatting_rule_memory"}:
            impact = "useful"
            estimated_delta = min(score_delta, 1.0)
        elif score_delta < 0:
            impact = "harmful"
            estimated_delta = score_delta
        else:
            impact = "neutral"
            estimated_delta = 0.0
        rows.append(
            {
                "group": group_name,
                "candidate_count": len(group_candidates),
                "estimated_delta": round(estimated_delta, 2),
                "impact": impact,
                "candidate_ids": [candidate.get("candidate_id") for candidate in group_candidates],
            }
        )
    report = {
        "schema_version": "candidate_ablation_report_v1",
        "learning_run_id": run_dir.name,
        "method": "deterministic_artifact_ablation",
        "groups": rows,
        "created_at": utc_now(),
    }
    write_json(run_dir / "ablation_report.json", report)
    lines = ["# Candidate Ablation Report", "", "| Group | Candidates | Impact | Estimated delta |", "| --- | ---: | --- | ---: |"]
    for row in rows:
        lines.append(f"| {row['group']} | {row['candidate_count']} | {row['impact']} | {row['estimated_delta']} |")
    _write_text(run_dir / "ablation_report.md", "\n".join(lines) + "\n")
    if state:
        state["ablation_result"] = report
        _write_job_state(run_dir, state)
    return {"run_dir": str(run_dir), "group_count": len(rows), "groups": rows, "ablation_report": str(run_dir / "ablation_report.json")}


def _evaluation_call_allowed(
    run_dir: Path,
    state: dict[str, Any],
    *,
    stage: str,
    max_real_calls: int | None,
    invocation_calls: int,
) -> tuple[bool, int, int]:
    estimated = len(state.get("chapters", [])) or 1
    if max_real_calls is not None and invocation_calls + estimated > max_real_calls:
        _write_api_call(run_dir, state, stage=stage, estimated_calls=estimated, status="paused_before_call")
        return False, invocation_calls, estimated
    state["api_calls_used"] = int(state.get("api_calls_used") or 0) + estimated
    _write_api_call(run_dir, state, stage=stage, estimated_calls=estimated, status="started")
    return True, invocation_calls + estimated, estimated


def _run_learning_eval_with_fallback(
    workspace: Workspace,
    *,
    run_dir: Path,
    state: dict[str, Any],
    stage: str,
    bundle: dict[str, Any] | None,
    strategy: str,
) -> dict[str, Any]:
    consecutive_failures = int(state.get("consecutive_provider_failures") or 0)
    last_error = ""
    for attempt in range(1, 4):
        model = str(state.get("active_model") or state.get("model"))
        try:
            if state.get("provider") == "mock" and model.startswith("mock-fail"):
                raise ValueError("mock simulated provider/model failure 524")
            result = run_learning_evaluation(
                workspace,
                project_slug=str(state["project_slug"]),
                chapters=",".join(str(chapter) for chapter in state.get("chapters", [])),
                provider_key=str(state["provider"]),
                model=model,
                use_stable_prompt=True,
                run=str(run_dir),
                test_memory_bundle=bundle,
                strategy=strategy,
            )
            report = read_json(run_dir / "baseline_report.json")
            if _model_provider_failure(report, model):
                raise ValueError("provider/model failure blocked learning evaluation")
            state["consecutive_provider_failures"] = 0
            _write_api_call(run_dir, state, stage=stage, estimated_calls=0, status="completed")
            return result
        except ValueError as exc:
            last_error = str(exc)
            retryable = _is_retryable_provider_error(last_error)
            _write_provider_error(run_dir, stage=stage, error=last_error, retryable=retryable, model=model)
            consecutive_failures += 1
            state["consecutive_provider_failures"] = consecutive_failures
            fallback = state.get("fallback_model")
            if state.get("allow_fallback_model", True) and fallback and consecutive_failures >= 2 and fallback != model:
                switch_entry = {
                    "from_model": model,
                    "to_model": fallback,
                    "reason": "consecutive_provider_or_model_failures",
                    "stage": stage,
                    "created_at": utc_now(),
                }
                log = _read_json_default(run_dir / "model_switch_log.json", {"entries": []})
                log.setdefault("entries", []).append(switch_entry)
                write_json(run_dir / "model_switch_log.json", log)
                state["active_model"] = fallback
                state["fallback_model_used"] = True
                state["consecutive_provider_failures"] = 0
                consecutive_failures = 0
                _write_job_state(run_dir, state)
                continue
            if not retryable:
                raise
            if attempt >= 3:
                raise
    raise ValueError(last_error or "provider/model failure blocked learning evaluation")


def _persist_iteration_report_from_eval(run_dir: Path, baseline_report: dict[str, Any], model: str) -> dict[str, Any]:
    iteration_report = read_json(run_dir / "baseline_report.json")
    write_json(run_dir / "baseline_report.json", baseline_report)
    _write_text(run_dir / "baseline_report.md", _learning_report_markdown("Baseline", baseline_report, model))
    return iteration_report


def _score_delta_payload(baseline_score: float, previous_score: float, current_score: float) -> dict[str, Any]:
    return {
        "baseline_score": baseline_score,
        "previous_score": previous_score,
        "current_score": current_score,
        "average_delta": round(current_score - previous_score, 2),
        "baseline_delta": round(current_score - baseline_score, 2),
        "regression": current_score < previous_score,
    }


def run_resumable_learning_loop(
    workspace: Workspace,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    fallback_model: str | None,
    chapters: str = "1-3",
    global_cycles: int = DEFAULT_GLOBAL_CYCLES,
    iterations: int = DEFAULT_ITERATIONS,
    repair_iterations: int = DEFAULT_REPAIR_ITERATIONS,
    min_improvement: float = 1.0,
    target_improvement: float = 3.0,
    allow_fallback_model: bool = True,
    rollback_harmful_memory: bool = False,
    stop_if_baseline_high: float = 94.0,
    max_real_calls: int | None = None,
    use_stable_prompt: bool = True,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required for MVP5C resumable learning loop.")
    project = get_project_by_slug(workspace, project_slug)
    stable_prompt = load_approved_stable_prompt(workspace)
    run_dir = new_learning_run_dir(workspace, project_slug, phase="learning_job")
    state = _initial_job_state(
        workspace=workspace,
        run_dir=run_dir,
        project=project,
        project_slug=project_slug,
        raw_path=raw_path,
        translated_path=translated_path,
        provider_key=provider_key,
        model=model,
        fallback_model=fallback_model,
        stable_prompt=stable_prompt,
        chapters=chapters,
        global_cycles=global_cycles,
        iterations=iterations,
        repair_iterations=repair_iterations,
        min_improvement=min_improvement,
        target_improvement=target_improvement,
        allow_fallback_model=allow_fallback_model,
        rollback_harmful_memory=rollback_harmful_memory,
        stop_if_baseline_high=stop_if_baseline_high,
        max_real_calls=max_real_calls,
    )
    _init_required_job_files(run_dir, state)
    return resume_learning_job(
        workspace,
        run=str(run_dir),
        max_real_calls=max_real_calls,
    )


def resume_learning_job(
    workspace: Workspace,
    *,
    run: str,
    max_real_calls: int | None = None,
    force_stage: str | None = None,
    from_stage: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir = resolve_learning_job_run(workspace, run)
    state = _load_job_state(run_dir)
    if state.get("status") in {"completed", "failed"} and not force_stage and not from_stage:
        result = _learning_job_result(run_dir, state, task_run_id=None)
        task_id = _insert_learning_job_task(workspace, state, result)
        result["task_run_id"] = task_id
        return result
    if force_stage:
        _clear_completed_from(state, force_stage)
    if from_stage:
        _clear_completed_from(state, from_stage)
    state["status"] = "running"
    state["can_resume"] = True
    if max_real_calls is not None:
        state["max_real_calls"] = max_real_calls
    _write_job_state(run_dir, state)
    if dry_run:
        result = _learning_job_result(run_dir, state, task_run_id=None)
        result["dry_run"] = True
        result["next_stage"] = state.get("pending_stages", [None])[0]
        return result

    invocation_calls = 0
    selected_chapters = state.get("chapters", [])
    chapters_arg = ",".join(str(chapter) for chapter in selected_chapters)
    try:
        if not _stage_completed(state, "prepare_dataset"):
            _record_stage(run_dir, state, "prepare_dataset", "running")
            prepare_learning_dataset(
                workspace,
                project_slug=str(state["project_slug"]),
                raw_path=Path(state["raw_path"]),
                translated_path=Path(state["translated_path"]),
                chapters=chapters_arg,
                run_dir=run_dir,
            )
            _record_stage(run_dir, state, "prepare_dataset", "completed")

        if not (_stage_completed(state, "baseline_translate") and _stage_completed(state, "baseline_evaluate")):
            allowed, invocation_calls, _estimated = _evaluation_call_allowed(
                run_dir,
                state,
                stage="baseline_translate",
                max_real_calls=max_real_calls,
                invocation_calls=invocation_calls,
            )
            if not allowed:
                return _finalize_resume_result(workspace, run_dir, _pause_job(run_dir, state, reason="max_real_calls_reached_before_baseline_translate"))
            _record_stage(run_dir, state, "baseline_translate", "running")
            _record_stage(run_dir, state, "baseline_evaluate", "running")
            baseline_result = _run_learning_eval_with_fallback(
                workspace,
                run_dir=run_dir,
                state=state,
                stage="baseline_translate",
                bundle=None,
                strategy="baseline",
            )
            baseline_report = read_json(run_dir / "baseline_report.json")
            baseline_score = float(baseline_result["score_summary"].get("average_score") or 0)
            state["baseline_score"] = baseline_score
            state["best_score"] = baseline_score
            state["final_score"] = baseline_score
            state["score_delta"] = 0.0
            _record_stage(run_dir, state, "baseline_translate", "completed", {"score": baseline_score})
            _record_stage(run_dir, state, "baseline_evaluate", "completed", {"score": baseline_score})
            _write_text(run_dir / "baseline_report.md", _learning_report_markdown("Baseline", baseline_report, state["active_model"]))

        if not _stage_completed(state, "extract_candidates"):
            _record_stage(run_dir, state, "extract_candidates", "running")
            extraction = extract_learning_memory(workspace, project_slug=str(state["project_slug"]), from_run=str(run_dir))
            state["candidate_count"] = extraction["candidate_count"]
            state["candidate_count_by_type"] = _count_by(extraction["candidates"], "candidate_type")
            state["pending_candidate_count"] = extraction["candidate_count"]
            _record_stage(run_dir, state, "extract_candidates", "completed", {"candidate_count": extraction["candidate_count"]})

        if not _stage_completed(state, "build_test_memory_bundle"):
            _record_stage(run_dir, state, "build_test_memory_bundle", "running")
            bundle_result = apply_test_memory(workspace, project_slug=str(state["project_slug"]), run=str(run_dir))
            _record_stage(
                run_dir,
                state,
                "build_test_memory_bundle",
                "completed",
                {"bundle_checksum": bundle_result["bundle_checksum"], "candidate_count": bundle_result["candidate_count"]},
            )

        baseline_report = read_json(run_dir / "baseline_report.json")
        baseline_score = float(state.get("baseline_score") or _score_summary(baseline_report, state.get("active_model"))["average_score"] or 0)
        test_bundle = read_json(run_dir / "test_memory_bundle.json")
        candidates = _load_candidates(run_dir)
        useful: list[dict[str, Any]] = read_json(run_dir / "useful_memory_candidates.json").get("candidates", [])
        harmful: list[dict[str, Any]] = read_json(run_dir / "harmful_memory_candidates.json").get("candidates", [])
        rollback_log = read_json(run_dir / "rollback_log.json")
        stop_reason = "max_cycles_exhausted"
        recommendation = "NEEDS_HUMAN_REVIEW"

        for cycle in range(1, int(state.get("global_cycles") or DEFAULT_GLOBAL_CYCLES) + 1):
            state["current_global_cycle"] = cycle
            strategy = _strategy_for_cycle(cycle)
            with (run_dir / "global_cycle_log.md").open("a", encoding="utf-8") as handle:
                handle.write(f"\n## Global cycle {cycle}\n\nStrategy: {strategy}\n")
            with (run_dir / "strategy_change_log.md").open("a", encoding="utf-8") as handle:
                handle.write(f"- Cycle {cycle}: {strategy}\n")
            for iteration in range(1, int(state.get("iterations") or DEFAULT_ITERATIONS) + 1):
                state["current_iteration"] = iteration
                translate_stage = f"cycle_{cycle}_iteration_{iteration}_translate"
                evaluate_stage = f"cycle_{cycle}_iteration_{iteration}_evaluate"
                score_stage = f"cycle_{cycle}_iteration_{iteration}_score_delta"
                impact_stage = f"cycle_{cycle}_iteration_{iteration}_harmful_candidate_detection"
                rollback_stage = f"cycle_{cycle}_iteration_{iteration}_rollback_or_keep_candidates"
                if _stage_completed(state, score_stage):
                    continue
                allowed, invocation_calls, _estimated = _evaluation_call_allowed(
                    run_dir,
                    state,
                    stage=translate_stage,
                    max_real_calls=max_real_calls,
                    invocation_calls=invocation_calls,
                )
                if not allowed:
                    return _finalize_resume_result(workspace, run_dir, _pause_job(run_dir, state, reason=f"max_real_calls_reached_before_{translate_stage}"))

                iteration_dir = run_dir / f"cycle_{cycle}" / f"iteration_{iteration}"
                iteration_dir.mkdir(parents=True, exist_ok=True)
                _record_stage(run_dir, state, translate_stage, "running")
                _record_stage(run_dir, state, evaluate_stage, "running")
                previous_score = float(state.get("final_score") or baseline_score)
                if state.get("provider") == "mock":
                    active_model = str(state.get("active_model") or state.get("model"))
                    if active_model.startswith("mock-regress"):
                        delta = -2.0
                    else:
                        delta = 3.0 if cycle == 1 and iteration == 1 else 1.0
                    iteration_report = _adjust_report_scores(baseline_report, active_model, delta)
                    result_summary = _score_summary(iteration_report, active_model)
                    current_score = float(result_summary.get("average_score") or 0)
                else:
                    result = _run_learning_eval_with_fallback(
                        workspace,
                        run_dir=run_dir,
                        state=state,
                        stage=translate_stage,
                        bundle=test_bundle,
                        strategy=strategy,
                    )
                    iteration_report = _persist_iteration_report_from_eval(run_dir, baseline_report, str(state.get("active_model")))
                    current_score = float(result["score_summary"].get("average_score") or 0)
                score_delta = _score_delta_payload(baseline_score, previous_score, current_score)
                _write_iteration_artifacts(
                    iteration_dir,
                    report=iteration_report,
                    candidates=candidates,
                    bundle=test_bundle,
                    score_delta=score_delta,
                )
                _write_iteration_checkpoint(iteration_dir, state, score_delta)
                _record_stage(run_dir, state, translate_stage, "completed", {"score": current_score})
                _record_stage(run_dir, state, evaluate_stage, "completed", {"score": current_score})
                _record_stage(run_dir, state, score_stage, "completed", score_delta)
                if cycle == 1 and iteration == 1:
                    state["reached_iteration_1_evaluate"] = True
                    state["iteration_score"] = current_score
                state["final_score"] = current_score
                state["best_score"] = max(float(state.get("best_score") or baseline_score), current_score)
                state["score_delta"] = round(current_score - baseline_score, 2)
                _record_stage(run_dir, state, impact_stage, "running")
                if score_delta["regression"]:
                    harmful.extend(candidates)
                    rollback_entry = {
                        "cycle": cycle,
                        "iteration": iteration,
                        "reason": "score_regression",
                        "candidates": [candidate.get("candidate_id") for candidate in candidates],
                    }
                    rollback_log.setdefault("entries", []).append(rollback_entry)
                    if state.get("rollback_harmful_memory"):
                        for candidate in candidates:
                            candidate["status"] = "rejected_in_test"
                    for repair in range(1, int(state.get("repair_iterations") or DEFAULT_REPAIR_ITERATIONS) + 1):
                        state["current_repair_iteration"] = repair
                        with (run_dir / "repair_iteration_log.md").open("a", encoding="utf-8") as handle:
                            handle.write(f"- Cycle {cycle} iteration {iteration} repair {repair}: rollback_harmful_memory\n")
                    _record_stage(run_dir, state, impact_stage, "completed", {"impact": "harmful"})
                    _record_stage(run_dir, state, rollback_stage, "completed", {"rollback": True})
                    write_json(run_dir / "rollback_log.json", rollback_log)
                    state["rollback_count"] = len(rollback_log.get("entries", []))
                    _write_candidate_classification(run_dir, state, useful, harmful)
                    ablate_learning_candidates(workspace, run=str(run_dir))
                    continue
                if current_score - baseline_score >= float(state.get("min_improvement") or 1.0):
                    useful = candidates
                    _record_stage(run_dir, state, impact_stage, "completed", {"impact": "useful"})
                    _record_stage(run_dir, state, rollback_stage, "completed", {"rollback": False})
                    _write_candidate_classification(run_dir, state, useful, harmful)
                    stop_reason = (
                        "target_improvement_reached"
                        if current_score - baseline_score >= float(state.get("target_improvement") or 3.0)
                        else "minimum_improvement_reached"
                    )
                    recommendation = "APPROVE_CANDIDATES" if stop_reason == "target_improvement_reached" else "NEEDS_HUMAN_REVIEW"
                    _complete_job(run_dir, state, decision="PASS", stop_reason=stop_reason, recommendation=recommendation)
                    return _finalize_resume_result(workspace, run_dir, _learning_job_result(run_dir, state, task_run_id=None))
                if baseline_score >= float(state.get("stop_if_baseline_high") or 94.0) and current_score >= baseline_score - 1.0:
                    useful = candidates
                    _record_stage(run_dir, state, impact_stage, "completed", {"impact": "useful_high_baseline"})
                    _record_stage(run_dir, state, rollback_stage, "completed", {"rollback": False})
                    _write_candidate_classification(run_dir, state, useful, harmful)
                    _complete_job(run_dir, state, decision="PASS", stop_reason="baseline_high_no_regression", recommendation="NEEDS_HUMAN_REVIEW")
                    return _finalize_resume_result(workspace, run_dir, _learning_job_result(run_dir, state, task_run_id=None))
                _record_stage(run_dir, state, impact_stage, "completed", {"impact": "neutral"})
                _record_stage(run_dir, state, rollback_stage, "completed", {"rollback": False})
                ablate_learning_candidates(workspace, run=str(run_dir))

        _write_candidate_classification(run_dir, state, useful, harmful)
        write_json(run_dir / "rollback_log.json", rollback_log)
        state["rollback_count"] = len(rollback_log.get("entries", []))
        if not state.get("reached_iteration_1_evaluate"):
            _complete_job(run_dir, state, decision="FAIL", stop_reason="baseline_only_no_learning_iteration", recommendation="STOP_NO_GAIN")
        else:
            _complete_job(run_dir, state, decision="FAIL", stop_reason=stop_reason, recommendation="STOP_NO_GAIN")
        return _finalize_resume_result(workspace, run_dir, _learning_job_result(run_dir, state, task_run_id=None))
    except StablePromptBlocker:
        raise
    except ValueError as exc:
        message = str(exc)
        retryable = _is_retryable_provider_error(message)
        if retryable:
            result = _block_job(run_dir, state, reason=f"blocked_retryable_provider_error: {message}", can_resume=True)
        else:
            result = _block_job(run_dir, state, reason=message, can_resume=False)
        return _finalize_resume_result(workspace, run_dir, result)


def _finalize_resume_result(workspace: Workspace, run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    state = _load_job_state(run_dir)
    task_id = _insert_learning_job_task(workspace, state, result)
    result["task_run_id"] = task_id
    return result


def learning_job_status(workspace: Workspace, *, run: str) -> dict[str, Any]:
    run_dir = resolve_learning_job_run(workspace, run)
    state = _load_job_state(run_dir)
    return {
        "learning_run_id": state.get("learning_run_id"),
        "run_dir": str(run_dir),
        "status": state.get("status"),
        "final_decision": state.get("final_decision"),
        "current_stage": state.get("current_stage"),
        "completed_stages": state.get("completed_stages", []),
        "pending_stages": state.get("pending_stages", []),
        "last_error": state.get("last_error"),
        "baseline_score": state.get("baseline_score"),
        "best_score": state.get("best_score"),
        "final_score": state.get("final_score"),
        "score_delta": state.get("score_delta"),
        "reached_iteration_1_evaluate": state.get("reached_iteration_1_evaluate", False),
        "can_resume": state.get("can_resume"),
        "next_command": state.get("next_command"),
        "artifacts": {
            "learning_job_state": str(run_dir / "learning_job_state.json"),
            "learning_summary": str(run_dir / "learning_summary.json"),
            "checkpoint_log": str(run_dir / "checkpoint_log.jsonl"),
        },
    }


def list_learning_jobs(workspace: Workspace, *, project_slug: str) -> dict[str, Any]:
    root = learning_root(workspace)
    jobs = []
    for state_path in sorted(root.glob("*/learning_job_state.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            state = read_json(state_path)
        except (OSError, ValueError):
            continue
        if state.get("project_slug") != project_slug:
            continue
        jobs.append(
            {
                "learning_run_id": state.get("learning_run_id"),
                "run_dir": str(state_path.parent),
                "status": state.get("status"),
                "final_decision": state.get("final_decision"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "best_score": state.get("best_score"),
                "score_delta": state.get("score_delta"),
                "can_resume": state.get("can_resume"),
            }
        )
    return {"project_slug": project_slug, "jobs": jobs}


def learning_loop(
    workspace: Workspace,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    fallback_model: str | None,
    chapters: str = "1-3",
    global_cycles: int = DEFAULT_GLOBAL_CYCLES,
    iterations: int = DEFAULT_ITERATIONS,
    repair_iterations: int = DEFAULT_REPAIR_ITERATIONS,
    min_improvement: float = 1.0,
    target_improvement: float = 3.0,
    allow_fallback_model: bool = True,
    rollback_harmful_memory: bool = False,
    stop_if_baseline_high: float = 94.0,
    max_real_calls: int | None = None,
    use_stable_prompt: bool = True,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required for MVP5B learning loop.")
    project = get_project_by_slug(workspace, project_slug)
    prepared = prepare_learning_dataset(
        workspace,
        project_slug=project_slug,
        raw_path=raw_path,
        translated_path=translated_path,
        chapters=chapters,
    )
    run_dir = Path(prepared["run_dir"])
    selected_chapters = parse_chapter_selection(chapters)
    model_switch_log: list[dict[str, Any]] = []
    provider_failure_count = 0
    active_model = model
    real_call_count = 0

    def evaluate_current(*, bundle: dict[str, Any] | None = None, strategy: str | None = None) -> dict[str, Any]:
        nonlocal provider_failure_count, active_model, real_call_count
        if provider_key != "mock":
            estimated = len(selected_chapters)
            if max_real_calls is not None and real_call_count + estimated > max_real_calls:
                raise ValueError("--max-real-calls safety cap reached.")
        if provider_key == "mock" and active_model.startswith("mock-fail"):
            provider_failure_count += 1
            if allow_fallback_model and fallback_model and provider_failure_count >= 2:
                model_switch_log.append(
                    {
                        "from_model": active_model,
                        "to_model": fallback_model,
                        "reason": "mock_simulated_consecutive_provider_failures",
                        "created_at": utc_now(),
                    }
                )
                active_model = fallback_model
                provider_failure_count = 0
            raise ValueError("mock simulated provider/model failure")
        result = run_learning_evaluation(
            workspace,
            project_slug=project_slug,
            chapters=chapters,
            provider_key=provider_key,
            model=active_model,
            use_stable_prompt=True,
            run=str(run_dir),
            test_memory_bundle=bundle,
            strategy=strategy,
        )
        report = read_json(run_dir / "baseline_report.json")
        if provider_key != "mock" and _model_provider_failure(report, active_model):
            provider_failure_count += 1
            if allow_fallback_model and fallback_model and provider_failure_count >= 2:
                model_switch_log.append(
                    {
                        "from_model": active_model,
                        "to_model": fallback_model,
                        "reason": "consecutive_provider_or_model_failures",
                        "created_at": utc_now(),
                    }
                )
                active_model = fallback_model
                provider_failure_count = 0
            raise ValueError("provider/model failure blocked learning evaluation")
        if provider_key != "mock":
            real_call_count += len(selected_chapters)
        return result

    baseline_result = None
    for attempt in range(1, 4):
        try:
            baseline_result = evaluate_current(strategy="baseline")
            break
        except ValueError:
            if not (allow_fallback_model and fallback_model and provider_failure_count >= 2):
                if attempt >= 3:
                    raise
                continue
    if baseline_result is None:
        baseline_result = evaluate_current(strategy="baseline")
    baseline_report = read_json(run_dir / "baseline_report.json")
    baseline_score = float(baseline_result["score_summary"].get("average_score") or 0)
    extraction = extract_learning_memory(workspace, project_slug=project_slug, from_run=str(run_dir))
    review = memory_review(workspace, project_slug=project_slug, run=str(run_dir))
    test_bundle_result = apply_test_memory(workspace, project_slug=project_slug, run=str(run_dir))
    test_bundle = read_json(Path(test_bundle_result["test_memory_bundle"]))

    useful: list[dict[str, Any]] = []
    harmful: list[dict[str, Any]] = []
    rollback_entries: list[dict[str, Any]] = []
    cycle_log: list[str] = []
    strategy_log: list[dict[str, Any]] = []
    repair_log: list[dict[str, Any]] = []
    best_score = baseline_score
    final_score = baseline_score
    stop_reason = "max_cycles_exhausted"
    recommendation = "NEEDS_HUMAN_REVIEW"

    if baseline_score >= stop_if_baseline_high:
        stop_reason = "baseline_already_high"
        recommendation = "NEEDS_HUMAN_REVIEW"
    else:
        for cycle in range(1, global_cycles + 1):
            strategy = _strategy_for_cycle(cycle)
            strategy_log.append({"cycle": cycle, "strategy": strategy})
            cycle_log.append(f"## Global cycle {cycle}\n\nStrategy: {strategy}\n")
            previous_score = final_score
            for iteration in range(1, iterations + 1):
                iteration_dir = run_dir / f"cycle_{cycle}" / f"iteration_{iteration}"
                iteration_dir.mkdir(parents=True, exist_ok=True)
                try:
                    if provider_key == "mock":
                        if active_model.startswith("mock-regress"):
                            delta = -2.0
                        else:
                            delta = 3.0 if cycle == 1 else 1.5 if cycle == 2 else 0.5
                        report = _adjust_report_scores(baseline_report, active_model, delta)
                        score = float(_score_summary(report, active_model)["average_score"] or 0)
                    else:
                        result = evaluate_current(bundle=test_bundle, strategy=strategy)
                        report = read_json(run_dir / "baseline_report.json")
                        score = float(result["score_summary"].get("average_score") or 0)
                    score_delta = {
                        "baseline_score": baseline_score,
                        "previous_score": previous_score,
                        "current_score": score,
                        "average_delta": round(score - previous_score, 2),
                        "baseline_delta": round(score - baseline_score, 2),
                        "regression": score < previous_score,
                    }
                    _write_iteration_artifacts(
                        iteration_dir,
                        report=report,
                        candidates=extraction["candidates"],
                        bundle=test_bundle,
                        score_delta=score_delta,
                    )
                    if score_delta["regression"]:
                        harmful.extend(extraction["candidates"])
                        rollback_entries.append(
                            {
                                "cycle": cycle,
                                "iteration": iteration,
                                "reason": "score_regression",
                                "candidates": [candidate["candidate_id"] for candidate in extraction["candidates"]],
                            }
                        )
                        if rollback_harmful_memory:
                            for candidate in extraction["candidates"]:
                                candidate["status"] = "rejected_in_test"
                        for repair in range(1, repair_iterations + 1):
                            repair_log.append(
                                {
                                    "cycle": cycle,
                                    "iteration": iteration,
                                    "repair_iteration": repair,
                                    "reason": "rollback_harmful_memory",
                                }
                            )
                        break
                    final_score = score
                    best_score = max(best_score, score)
                    if score - baseline_score >= min_improvement:
                        useful = extraction["candidates"]
                    if score - baseline_score >= target_improvement:
                        stop_reason = "target_improvement_reached"
                        recommendation = "APPROVE_CANDIDATES"
                        break
                    previous_score = score
                except ValueError as exc:
                    repair_log.append(
                        {
                            "cycle": cycle,
                            "iteration": iteration,
                            "reason": "evaluation_error",
                            "error": str(exc),
                        }
                    )
                    if iteration >= iterations:
                        break
            if stop_reason == "target_improvement_reached":
                break

    if not useful and extraction["candidates"]:
        useful = extraction["candidates"] if final_score >= baseline_score else []
    harmful_ids = {candidate.get("candidate_id") for candidate in harmful}
    useful = [candidate for candidate in useful if candidate.get("candidate_id") not in harmful_ids]
    write_json(run_dir / "useful_memory_candidates.json", {"candidates": useful})
    write_json(run_dir / "harmful_memory_candidates.json", {"candidates": harmful})
    write_json(run_dir / "rollback_log.json", {"entries": rollback_entries})
    write_json(run_dir / "model_switch_log.json", {"entries": model_switch_log})
    _write_text(run_dir / "global_cycle_log.md", "\n".join(cycle_log) or "# Global Cycle Log\n\nNo cycles run.\n")
    write_json(run_dir / "strategy_change_log.json", {"entries": strategy_log})
    _write_text(
        run_dir / "strategy_change_log.md",
        "\n".join(f"- Cycle {entry['cycle']}: {entry['strategy']}" for entry in strategy_log) + "\n",
    )
    write_json(run_dir / "repair_iteration_log.json", {"entries": repair_log})
    _write_text(
        run_dir / "repair_iteration_log.md",
        "\n".join(f"- {entry}" for entry in repair_log) + "\n",
    )
    summary = {
        "learning_run_id": run_dir.name,
        "project_id": project["id"],
        "project_slug": project_slug,
        "provider": provider_key,
        "model": model,
        "final_model": active_model,
        "fallback_model": fallback_model,
        "fallback_model_used": active_model != model,
        "chapters": selected_chapters,
        "global_cycles_requested": global_cycles,
        "iterations_requested": iterations,
        "repair_iterations_requested": repair_iterations,
        "baseline_score": baseline_score,
        "final_score": final_score,
        "best_score": best_score,
        "score_delta": round(final_score - baseline_score, 2),
        "candidate_count": len(extraction["candidates"]),
        "candidate_count_by_type": _count_by(extraction["candidates"], "candidate_type"),
        "useful_candidate_count": len(useful),
        "harmful_candidate_count": len(harmful),
        "pending_candidate_count": len(extraction["candidates"]),
        "rollback_count": len(rollback_entries),
        "model_switch_log": model_switch_log,
        "stop_reason": stop_reason,
        "recommendation": recommendation,
        "real_call_count": real_call_count,
        "status": "success" if final_score >= baseline_score else "no_gain",
    }
    write_json(run_dir / "learning_summary.json", summary)
    _write_learning_summary_markdown(run_dir / "learning_summary.md", summary)
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="learn.loop",
            status="success",
            stage="completed",
            project_id=project["id"],
            input_data={
                "provider": provider_key,
                "model": model,
                "fallback_model": fallback_model,
                "chapters": chapters,
            },
            result_data=summary,
        )
        conn.commit()
    return {**summary, "run_dir": str(run_dir), "task_run_id": task_id}


def _strategy_for_cycle(cycle: int) -> str:
    if cycle == 1:
        return "all_evidence_backed_candidates"
    if cycle == 2:
        return "terms_names_formatting_first"
    return "high_confidence_ablation_by_type"


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _write_learning_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Learning Summary",
        "",
        f"- Project: `{summary['project_slug']}`",
        f"- Provider/model: `{summary['provider']}` / `{summary['final_model']}`",
        f"- Fallback used: `{summary['fallback_model_used']}`",
        f"- Baseline score: `{summary['baseline_score']}`",
        f"- Final score: `{summary['final_score']}`",
        f"- Best score: `{summary['best_score']}`",
        f"- Score delta: `{summary['score_delta']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- Useful/harmful/pending: `{summary['useful_candidate_count']}` / "
        f"`{summary['harmful_candidate_count']}` / `{summary['pending_candidate_count']}`",
        f"- Rollbacks: `{summary['rollback_count']}`",
        f"- Stop reason: `{summary['stop_reason']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
    ]
    _write_text(path, "\n".join(lines))


def approve_learning_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    candidate_ids: str | None,
    approve_all: bool = False,
) -> dict[str, Any]:
    return _review_memory_status(
        workspace,
        project_slug=project_slug,
        run=run,
        candidate_ids=candidate_ids,
        all_candidates=approve_all,
        status="active",
        reason="approved_by_human",
    )


def reject_learning_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    candidate_ids: str | None,
    reason: str | None,
    reject_all: bool = False,
) -> dict[str, Any]:
    if not reason or not reason.strip():
        raise ValueError("--reason is required when rejecting memory.")
    return _review_memory_status(
        workspace,
        project_slug=project_slug,
        run=run,
        candidate_ids=candidate_ids,
        all_candidates=reject_all,
        status="rejected",
        reason=reason.strip(),
    )


def _review_memory_status(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    candidate_ids: str | None,
    all_candidates: bool,
    status: str,
    reason: str,
) -> dict[str, Any]:
    run_dir = resolve_learning_run(workspace, project_slug, run)
    candidates_path = run_dir / "memory_candidates.json"
    if not candidates_path.exists():
        raise ValueError("No memory candidates found for this learning run.")
    payload = read_json(candidates_path)
    candidates = payload.get("candidates", [])
    if all_candidates:
        ids = [candidate["candidate_id"] for candidate in candidates]
    else:
        if not candidate_ids:
            raise ValueError("Provide --candidate-ids or explicit --all.")
        ids = [item.strip() for item in candidate_ids.split(",") if item.strip()]
    candidate_lookup = {candidate["candidate_id"]: candidate for candidate in candidates}
    missing = [candidate_id for candidate_id in ids if candidate_id not in candidate_lookup]
    if missing:
        raise ValueError(f"Candidate id(s) not found: {', '.join(missing)}")
    updated = []
    for candidate_id in ids:
        update_memory_status(workspace, memory_item_id=candidate_id, status=status)
        candidate_lookup[candidate_id]["status"] = status
        candidate_lookup[candidate_id]["review_reason"] = reason
        updated.append(candidate_lookup[candidate_id])
    write_json(candidates_path, {"candidates": candidates})
    _write_memory_review_files(run_dir, candidates)
    result = {
        "run_dir": str(run_dir),
        "status": status,
        "updated_candidate_ids": ids,
        "reason": reason,
    }
    write_json(run_dir / f"memory_{status}_review.json", result)
    return result
