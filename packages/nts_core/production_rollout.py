from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import time
from typing import Any

from nts_core.dictionary import load_project_dictionary
from nts_core.production_translation import (
    DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    DEFAULT_CHUNK_SIZE_CHARS,
    DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
    _chapter_source_text,
    _select_batch_chapters,
    split_text_chunks,
    translate_batch_stable,
)
from nts_core.rules import load_all_project_rules
from nts_storage.database import json_dumps, utc_now
from nts_storage.workspace import Workspace


PRODUCTION_ROLLOUT_SCHEMA = "mvp5i_production_rollout_v1"
BAD_ITEM_STATUSES = {
    "pending",
    "pending_review",
    "needs_human_review",
    "likely_reject",
    "rejected",
    "deprecated",
    "harmful",
    "insufficient_evidence",
    "disabled_for_prompt",
    "rejected_after_validation",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _new_rollout_dir(workspace: Workspace, project_slug: str) -> Path:
    timestamp = int(time.time() * 1000)
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    value = timestamp
    while value:
        value, remainder = divmod(value, 36)
        encoded = alphabet[remainder] + encoded
    return workspace.path / "artifacts" / "production_rollout" / f"{project_slug}_p_{encoded or '0'}"


def _batch_artifact_dir(workspace: Workspace, run_dir: Path) -> Path:
    # Keep deeply nested chunk artifacts under a shorter root to avoid Windows
    # MAX_PATH failures while preserving the rollout audit folder.
    return workspace.path / "artifacts" / "prod_batch" / run_dir.name


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def _safe_config_snapshot(
    *,
    project_slug: str,
    chapters: str,
    provider_key: str,
    model: str,
    dictionary_max_entries: int,
    memory_max_items: int,
    support_max_chars: int,
    emit_prompt_artifacts: bool,
    resumable: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "mvp5i_production_config_snapshot_v1",
        "project_slug": project_slug,
        "created_at": utc_now(),
        "safe_flags": [
            "--use-stable-prompt",
            "--use-hybrid-prompt",
            "--use-approved-dictionary",
            f"--dictionary-max-entries {dictionary_max_entries}",
            f"--memory-max-items {memory_max_items}",
            f"--support-max-chars {support_max_chars}",
            "--emit-prompt-artifacts" if emit_prompt_artifacts else "--no-emit-prompt-artifacts",
            "--resumable" if resumable else "--no-resumable",
        ],
        "provider": provider_key,
        "model": model,
        "chapters": chapters,
        "use_stable_prompt": True,
        "use_hybrid_prompt": True,
        "use_approved_dictionary": True,
        "use_approved_memory": True,
        "use_approved_rules": False,
        "dictionary_max_entries": dictionary_max_entries,
        "memory_max_items": memory_max_items,
        "support_max_chars": support_max_chars,
        "emit_prompt_artifacts": emit_prompt_artifacts,
        "resumable": resumable,
        "rule_policy": {
            "approved_rules_verifier_only": True,
            "render_rules_in_prompt": False,
            "use_approved_rules_flag": "opt_in_only",
            "warning": "MVP5H.1 rule prompt rendering failed validation; do not enable --use-approved-rules for production rollout.",
        },
        "forbidden": [
            "--use-approved-rules",
            "pending/rejected/deprecated/harmful prompt items",
            "raw NLP cache in prompt",
            "stable prompt text changes",
        ],
    }


def _write_config_snapshot(run_dir: Path, snapshot: dict[str, Any]) -> None:
    _write_json(run_dir / "production_config_snapshot.json", snapshot)
    lines = [
        "# Production Config Snapshot",
        "",
        f"- Project: `{snapshot['project_slug']}`",
        f"- Provider/model: `{snapshot['provider']}` / `{snapshot['model']}`",
        f"- Chapters: `{snapshot['chapters']}`",
        f"- Stable prompt: `{snapshot['use_stable_prompt']}`",
        f"- Hybrid prompt: `{snapshot['use_hybrid_prompt']}`",
        f"- Approved dictionary: `{snapshot['use_approved_dictionary']}`",
        f"- Approved memory: `{snapshot['use_approved_memory']}`",
        f"- Approved rules rendered: `{snapshot['use_approved_rules']}`",
        "",
        "## Safe Flags",
        "",
    ]
    lines.extend(f"- `{flag}`" for flag in snapshot["safe_flags"])
    lines.extend(
        [
            "",
            "Rules are verifier-only / QA-only for this rollout. `--use-approved-rules` remains opt-in and is not part of the safe profile.",
        ]
    )
    _write_text(run_dir / "production_config_snapshot.md", "\n".join(lines))


def _estimate_batch_calls(
    workspace: Workspace,
    *,
    selected_chapters: list[dict[str, Any]],
    max_source_chars_per_chapter: int,
    chunk_size_chars: int,
    chunk_overlap_paragraphs: int,
) -> tuple[int, list[dict[str, Any]]]:
    estimates: list[dict[str, Any]] = []
    total = 0
    for chapter in selected_chapters:
        _chapter, _segments, source_text = _chapter_source_text(
            workspace,
            str(chapter["id"]),
            max_source_chars_per_chapter,
        )
        chunks = split_text_chunks(source_text, chunk_size_chars=chunk_size_chars, overlap_paragraphs=chunk_overlap_paragraphs)
        total += len(chunks)
        estimates.append(
            {
                "chapter_id": chapter["id"],
                "chapter_no": chapter.get("chapter_no"),
                "source_chars": len(source_text),
                "chunk_count": len(chunks),
            }
        )
    return total, estimates


def _iter_chunk_dirs(batch_dir: Path) -> list[Path]:
    root = batch_dir / "chunk_outputs"
    if not root.exists():
        return []
    return sorted(path for path in root.glob("*\\chunk_*") if path.is_dir()) or sorted(path for path in root.glob("*/chunk_*") if path.is_dir())


def run_production_qa(
    *,
    rollout_dir: Path,
    batch_dir: Path,
    max_support_chars: int,
) -> dict[str, Any]:
    batch_manifest = _read_json(batch_dir / "batch_manifest.json")
    chapter_results = _read_json(batch_dir / "chapter_results.json").get("chapters") or []
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    stats = Counter()
    dictionary_hits = Counter()
    memory_hits = Counter()
    rule_rows: list[dict[str, Any]] = []
    prompt_samples: list[str] = []
    translated_samples: list[str] = []
    prompt_budget_rows: list[dict[str, Any]] = []

    failed = [row for row in chapter_results if row.get("status") == "failed"]
    skipped = [row for row in chapter_results if str(row.get("status") or "").startswith("skipped")]
    for row in failed:
        blocking.append({"kind": "failed_chapter", "chapter_id": row.get("chapter_id"), "error": row.get("error")})
    for row in skipped:
        warnings.append({"kind": "skipped_chapter", "chapter_id": row.get("chapter_id"), "status": row.get("status")})

    for row in chapter_results:
        output_path = Path(row.get("output_path") or "")
        if row.get("status") != "success":
            continue
        if not output_path.exists():
            blocking.append({"kind": "missing_output", "chapter_id": row.get("chapter_id")})
            continue
        output_text = output_path.read_text(encoding="utf-8")
        if not output_text.strip():
            blocking.append({"kind": "empty_output", "chapter_id": row.get("chapter_id")})
        source_chars = int((row.get("quality_summary") or {}).get("source_char_count") or 0)
        ratio = len(output_text) / max(source_chars, 1)
        if ratio > 4.0:
            blocking.append({"kind": "overlong_output", "chapter_id": row.get("chapter_id"), "ratio": round(ratio, 3)})
        elif ratio > 2.8:
            warnings.append({"kind": "high_output_ratio", "chapter_id": row.get("chapter_id"), "ratio": round(ratio, 3)})
        if _contains_chinese(output_text):
            warnings.append({"kind": "chinese_residue", "chapter_id": row.get("chapter_id")})
        if len(translated_samples) < 5:
            translated_samples.append(f"## Chapter {row.get('chapter_no') or row.get('chapter_id')}\n\n{output_text[:1200]}")

    for chunk_dir in _iter_chunk_dirs(batch_dir):
        stats["chunks_seen"] += 1
        prompt_text = (chunk_dir / "prompt_used.md").read_text(encoding="utf-8") if (chunk_dir / "prompt_used.md").exists() else ""
        if prompt_text and len(prompt_samples) < 5:
            prompt_samples.append(f"## {chunk_dir.parent.name}/{chunk_dir.name}\n\n```text\n{prompt_text[:1600]}\n```")
        if "Rules:" in prompt_text:
            blocking.append({"kind": "rules_rendered_in_prompt", "chunk": str(chunk_dir)})
        if "chapter_candidates" in prompt_text or '"tokens"' in prompt_text or '"sentences"' in prompt_text:
            blocking.append({"kind": "raw_nlp_cache_in_prompt", "chunk": str(chunk_dir)})
        if "sk-" in prompt_text:
            blocking.append({"kind": "possible_api_key_leak", "chunk": str(chunk_dir)})

        quality = _read_json(chunk_dir / "quality_report.json")
        verification = quality.get("verification") or {}
        reasons = set(verification.get("reasons") or [])
        if "truncation" in reasons or verification.get("truncation"):
            blocking.append({"kind": "truncation", "chunk": str(chunk_dir)})
        if "unsafe_compression" in reasons or verification.get("unsafe_compression"):
            blocking.append({"kind": "unsafe_compression", "chunk": str(chunk_dir)})
        if quality.get("status") == "fail":
            blocking.append({"kind": "quality_failed", "chunk": str(chunk_dir), "reasons": sorted(reasons)})

        context = _read_json(chunk_dir / "prompt_context_bundle.json")
        selected_rules = context.get("selected_rule_items") or []
        if selected_rules:
            blocking.append({"kind": "rule_items_selected", "chunk": str(chunk_dir), "rule_count": len(selected_rules)})
        for item in context.get("selected_items") or []:
            status = str(item.get("status") or "")
            source_type = str(item.get("source_type") or "")
            if status in BAD_ITEM_STATUSES:
                blocking.append({"kind": "bad_status_item_selected", "chunk": str(chunk_dir), "item_id": item.get("item_id"), "status": status})
            if source_type == "dictionary":
                dictionary_hits[str(item.get("source_anchor") or item.get("item_id"))] += 1
                target = str(item.get("target_value") or "")
                output = (chunk_dir / "translation.vi.txt").read_text(encoding="utf-8") if (chunk_dir / "translation.vi.txt").exists() else ""
                if target and target not in output:
                    warnings.append({"kind": "dictionary_target_not_observed", "chunk": str(chunk_dir), "source": item.get("source_anchor"), "target": target})
            elif source_type == "memory":
                memory_hits[str(item.get("source_anchor") or item.get("item_id"))] += 1
            elif source_type == "rule":
                rule_rows.append({"chunk": str(chunk_dir), "item_id": item.get("item_id")})

        budget = _read_json(chunk_dir / "prompt_budget_report.json")
        if budget:
            prompt_budget_rows.append(
                {
                    "chunk": str(chunk_dir),
                    "support_chars": budget.get("support_chars", 0),
                    "support_lines": budget.get("support_lines", 0),
                    "selected_dictionary_count": budget.get("selected_dictionary_count", 0),
                    "selected_memory_count": budget.get("selected_memory_count", 0),
                    "selected_rule_count": budget.get("selected_rule_count", 0),
                }
            )
            if int(budget.get("support_chars") or 0) > max_support_chars:
                blocking.append({"kind": "prompt_budget_exceeded", "chunk": str(chunk_dir), "support_chars": budget.get("support_chars")})
            if int(budget.get("selected_rule_count") or 0) > 0:
                blocking.append({"kind": "rule_budget_selected_rules", "chunk": str(chunk_dir), "selected_rule_count": budget.get("selected_rule_count")})

    qa = {
        "schema_version": "mvp5i_production_qa_v1",
        "created_at": utc_now(),
        "batch_dir": str(batch_dir),
        "batch_status": batch_manifest.get("status"),
        "pass": not blocking,
        "blocking_issue_count": len(blocking),
        "warning_count": len(warnings),
        "blocking_issues": blocking,
        "warnings": warnings,
        "chunks_seen": int(stats["chunks_seen"]),
        "chapters_processed": len(batch_manifest.get("chapters_processed") or []),
        "chapters_failed": len(batch_manifest.get("chapters_failed") or []),
        "chapters_skipped": len(batch_manifest.get("chapters_skipped") or []),
        "api_calls_used": batch_manifest.get("actual_api_calls", 0),
        "rules_rendered_count": len(rule_rows),
        "dictionary_hit_count": sum(dictionary_hits.values()),
        "memory_hit_count": sum(memory_hits.values()),
        "dictionary_hits": dict(dictionary_hits),
        "memory_hits": dict(memory_hits),
        "prompt_budget_rows": prompt_budget_rows,
    }
    _write_json(rollout_dir / "production_qa_report.json", qa)
    _write_text(
        rollout_dir / "production_qa_report.md",
        "# Production QA Report\n\n"
        f"- Pass: `{qa['pass']}`\n"
        f"- Blocking issues: `{qa['blocking_issue_count']}`\n"
        f"- Warnings: `{qa['warning_count']}`\n"
        f"- Rules rendered: `{qa['rules_rendered_count']}`\n"
        f"- Dictionary hits: `{qa['dictionary_hit_count']}`\n"
        f"- Memory hits: `{qa['memory_hit_count']}`\n",
    )
    return {**qa, "prompt_samples": prompt_samples, "translated_samples": translated_samples}


def _write_human_review(
    *,
    rollout_dir: Path,
    config: dict[str, Any],
    batch_result: dict[str, Any],
    qa: dict[str, Any],
    all_rules: list[dict[str, Any]],
    final_decision: str,
) -> Path:
    review_dir = rollout_dir / "human_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    active_rules = [rule for rule in all_rules if rule.get("status") == "active"]
    verifier_only = [rule for rule in all_rules if rule.get("status") == "active_verifier_only"]
    disabled = [rule for rule in all_rules if rule.get("status") in {"disabled_for_prompt", "rejected_after_validation"}]
    chapters = batch_result.get("chapters") or []
    success_count = sum(1 for row in chapters if row.get("status") == "success")
    failed_count = sum(1 for row in chapters if row.get("status") == "failed")
    skipped_count = sum(1 for row in chapters if str(row.get("status") or "").startswith("skipped"))

    _write_text(
        review_dir / "production_batch_summary.md",
        "# Production Batch Summary\n\n"
        f"- Batch status: `{batch_result.get('status')}`\n"
        f"- Chapters processed: `{success_count}`\n"
        f"- Failed chapters: `{failed_count}`\n"
        f"- Skipped chapters: `{skipped_count}`\n"
        f"- API calls used: `{batch_result.get('actual_api_calls', 0)}`\n"
        f"- Batch dir: `{batch_result.get('batch_dir')}`\n",
    )
    _write_text(
        review_dir / "qa_summary.md",
        "# QA Summary\n\n"
        f"- QA pass: `{qa['pass']}`\n"
        f"- Blocking issues: `{qa['blocking_issue_count']}`\n"
        f"- Warnings: `{qa['warning_count']}`\n"
        f"- Rules rendered: `{qa['rules_rendered_count']}`\n",
    )
    _write_text(
        review_dir / "prompt_budget_summary.md",
        "# Prompt Budget Summary\n\n"
        + "\n".join(
            f"- {row['chunk']}: chars={row['support_chars']}, lines={row['support_lines']}, "
            f"dict={row['selected_dictionary_count']}, memory={row['selected_memory_count']}, rules={row['selected_rule_count']}"
            for row in qa.get("prompt_budget_rows", [])[:80]
        ),
    )
    _write_text(
        review_dir / "dictionary_hit_summary.md",
        "# Dictionary Hit Summary\n\n"
        + ("\n".join(f"- `{key}`: {value}" for key, value in sorted((qa.get("dictionary_hits") or {}).items())) or "No dictionary hits recorded."),
    )
    _write_text(
        review_dir / "memory_usage_summary.md",
        "# Memory Usage Summary\n\n"
        + ("\n".join(f"- `{key}`: {value}" for key, value in sorted((qa.get("memory_hits") or {}).items())) or "No memory hits recorded."),
    )
    _write_text(
        review_dir / "rule_verifier_summary.md",
        "# Rule Verifier Summary\n\n"
        f"- Active prompt rules: `{len(active_rules)}`\n"
        f"- Verifier-only rules: `{len(verifier_only)}`\n"
        f"- Disabled/rejected-after-validation rules: `{len(disabled)}`\n"
        f"- Rules rendered in prompts: `{qa['rules_rendered_count']}`\n",
    )
    _write_text(review_dir / "prompt_samples.md", "\n\n".join(qa.get("prompt_samples") or ["# Prompt Samples\n\nNo prompt samples recorded."]))
    _write_text(review_dir / "translated_samples.md", "\n\n".join(qa.get("translated_samples") or ["# Translated Samples\n\nNo translated samples recorded."]))
    _write_text(
        review_dir / "warnings.md",
        "# Warnings\n\n" + ("\n".join(f"- {row}" for row in qa.get("warnings", [])) or "No warnings."),
    )
    recommendation = "proceed to larger batch" if final_decision == "PASS" else "fix production issue before scaling"
    _write_text(
        review_dir / "human_review_summary.md",
        "# MVP5I Production Rollout Review\n\n"
        f"- Final decision: `{final_decision}`\n"
        f"- Chapters processed: `{success_count}`\n"
        f"- Chunks processed: `{qa.get('chunks_seen', 0)}`\n"
        f"- Successful chunks: `{qa.get('chunks_seen', 0) if qa['pass'] else 'see QA'}`\n"
        f"- Failed/skipped chapters: `{failed_count}` / `{skipped_count}`\n"
        f"- API calls used: `{batch_result.get('actual_api_calls', 0)}`\n"
        f"- Model/fallback usage: `{config['model']}` / fallback not configured for batch rollout\n"
        f"- Dictionary hits used: `{qa['dictionary_hit_count']}`\n"
        f"- Memory items used: `{qa['memory_hit_count']}`\n"
        f"- Rules rendered: `{qa['rules_rendered_count']}`\n"
        f"- QA warnings: `{qa['warning_count']}`\n"
        f"- Recommendation: `{recommendation}`\n",
    )
    return review_dir


def run_controlled_production_rollout(
    workspace: Workspace,
    *,
    project_slug: str,
    provider_key: str,
    model: str,
    chapters: str = "1-10",
    max_chapters: int = 10,
    max_real_calls: int = 24,
    dictionary_max_entries: int = 8,
    memory_max_items: int = 6,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = True,
    resumable: bool = True,
    dry_run: bool = False,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    run_dir = output_dir or _new_rollout_dir(workspace, project_slug)
    run_dir.mkdir(parents=True, exist_ok=True)
    project, selected_chapters, selection_warnings = _select_batch_chapters(
        workspace,
        project_slug=project_slug,
        chapters=chapters,
        chapter_ids=None,
        max_chapters=max_chapters,
    )
    estimated_calls, chunk_plan = _estimate_batch_calls(
        workspace,
        selected_chapters=selected_chapters,
        max_source_chars_per_chapter=DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
        chunk_size_chars=DEFAULT_CHUNK_SIZE_CHARS,
        chunk_overlap_paragraphs=DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    )
    if estimated_calls > max_real_calls:
        raise ValueError(f"Estimated API calls {estimated_calls} exceed --max-real-calls {max_real_calls}.")
    config = _safe_config_snapshot(
        project_slug=project_slug,
        chapters=chapters,
        provider_key=provider_key,
        model=model,
        dictionary_max_entries=dictionary_max_entries,
        memory_max_items=memory_max_items,
        support_max_chars=support_max_chars,
        emit_prompt_artifacts=emit_prompt_artifacts,
        resumable=resumable,
    )
    config["project_id"] = project["id"]
    config["chunk_plan"] = chunk_plan
    config["estimated_api_calls"] = estimated_calls
    config["selection_warnings"] = selection_warnings
    config["approved_dictionary_count"] = len(load_project_dictionary(workspace, project_slug))
    all_rules = load_all_project_rules(workspace, project_slug)
    config["approved_rule_count_total"] = len(all_rules)
    config["active_prompt_rule_count"] = sum(1 for rule in all_rules if rule.get("status") == "active")
    config["verifier_only_rule_count"] = sum(1 for rule in all_rules if rule.get("status") == "active_verifier_only")
    _write_config_snapshot(run_dir, config)
    _write_json(run_dir / "chunk_plan.json", {"schema_version": "mvp5i_chunk_plan_v1", "estimated_api_calls": estimated_calls, "chapters": chunk_plan})

    batch_output_dir = _batch_artifact_dir(workspace, run_dir)
    batch_result = translate_batch_stable(
        workspace,
        project_slug=project_slug,
        provider_key=provider_key,
        model=model,
        use_stable_prompt=True,
        chapters=chapters,
        max_chapters=max_chapters,
        resume=resumable,
        skip_existing=False,
        force=True,
        dry_run=dry_run,
        output_dir=batch_output_dir,
        export_combined=True,
        stop_on_error=False,
        use_approved_dictionary=True,
        use_hybrid_prompt=True,
        dictionary_max_entries=dictionary_max_entries,
        memory_max_items=memory_max_items,
        use_approved_rules=False,
        support_max_chars=support_max_chars,
        emit_prompt_artifacts=emit_prompt_artifacts,
    )
    qa = run_production_qa(rollout_dir=run_dir, batch_dir=Path(batch_result["batch_dir"]), max_support_chars=support_max_chars)
    provider_blocked = (
        batch_result.get("status") == "partial_failure"
        and batch_result.get("chapters")
        and all(str(row.get("error") or "").lower().find("provider") >= 0 for row in batch_result["chapters"] if row.get("status") == "failed")
    )
    final_decision = "BLOCKED" if provider_blocked else "PASS" if batch_result.get("status") in {"success", "dry_run"} and qa["pass"] else "FAIL"
    review_dir = _write_human_review(
        rollout_dir=run_dir,
        config=config,
        batch_result=batch_result,
        qa=qa,
        all_rules=all_rules,
        final_decision=final_decision,
    )
    summary = {
        "schema_version": PRODUCTION_ROLLOUT_SCHEMA,
        "project_slug": project_slug,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "final_decision": final_decision,
        "batch_dir": batch_result["batch_dir"],
        "batch_status": batch_result.get("status"),
        "chapters_processed": len([row for row in batch_result.get("chapters", []) if row.get("status") == "success"]),
        "chapters_failed": len([row for row in batch_result.get("chapters", []) if row.get("status") == "failed"]),
        "chapters_skipped": len([row for row in batch_result.get("chapters", []) if str(row.get("status") or "").startswith("skipped")]),
        "chunks_processed": qa.get("chunks_seen", 0),
        "api_calls_used": batch_result.get("actual_api_calls", 0),
        "qa_pass": qa["pass"],
        "qa_blocking_issue_count": qa["blocking_issue_count"],
        "qa_warning_count": qa["warning_count"],
        "rules_rendered_count": qa["rules_rendered_count"],
        "dictionary_hit_count": qa["dictionary_hit_count"],
        "memory_hit_count": qa["memory_hit_count"],
        "human_review_path": str(review_dir),
        "config_snapshot_path": str(run_dir / "production_config_snapshot.json"),
        "qa_report_path": str(run_dir / "production_qa_report.json"),
        "prompt_artifact_root": str(Path(batch_result["batch_dir"]) / "chunk_outputs"),
        "warnings": qa["warnings"],
        "created_at": utc_now(),
    }
    _write_json(run_dir / "production_rollout_summary.json", summary)
    _write_text(
        run_dir / "production_rollout_summary.md",
        "# Production Rollout Summary\n\n"
        f"- Final decision: `{final_decision}`\n"
        f"- Chapters processed: `{summary['chapters_processed']}`\n"
        f"- Chunks processed: `{summary['chunks_processed']}`\n"
        f"- API calls used: `{summary['api_calls_used']}`\n"
        f"- QA pass: `{summary['qa_pass']}`\n"
        f"- Rules rendered: `{summary['rules_rendered_count']}`\n",
    )
    return summary
