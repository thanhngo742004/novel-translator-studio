from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import time
from typing import Any

from nts_core.dictionary import load_project_dictionary
from nts_core.production_translation import (
    build_rollout_model_policy,
    load_production_provider,
    DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    DEFAULT_CHUNK_SIZE_CHARS,
    DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
    _chapter_source_text,
    _select_batch_chapters,
    split_text_chunks,
    translate_batch_stable,
)
from nts_core.rules import load_all_project_rules
from nts_core.eval_harness import chat_completion_with_provider_retry, classify_provider_error
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




def _route_status_from_error(exc: Exception) -> dict[str, Any]:
    info = classify_provider_error(exc)
    if info.get("http_status") == 404:
        info["provider_error_type"] = "model_route_not_found"
        info["blocker_reason"] = "model_route_not_found"
        info["retryable"] = False
    return info


def _preflight_one_model(provider: Any, model: str) -> dict[str, Any]:
    if not model:
        return {"model": model, "status": "not_configured", "ok": False, "reason": "not_configured"}
    try:
        if provider.key == "mock":
            lowered = model.lower()
            if any(token in lowered for token in ("404", "missing", "not-found", "route-missing")):
                raise ValueError(f"Provider HTTP error 404: mock model route not found for {model}")
            if any(token in lowered for token in ("fail", "blocked")):
                raise ValueError(f"Provider HTTP error 503: mock provider unavailable for {model}")
            raw = "ok"
        else:
            raw = chat_completion_with_provider_retry(
                provider,
                model=model,
                messages=[
                    {"role": "system", "content": "Return exactly OK."},
                    {"role": "user", "content": "NTS provider preflight. Return OK."},
                ],
                max_tokens=8,
                retry_attempts=1,
                retry_context={"phase": "production_preflight", "model": model},
            )
        return {"model": model, "status": "ok", "ok": True, "response_chars": len(raw or "")}
    except Exception as exc:
        info = _route_status_from_error(exc)
        return {"model": model, "status": info.get("provider_error_type") or "failed", "ok": False, **info}


def write_provider_preflight(
    workspace: Workspace,
    *,
    run_dir: Path,
    provider_key: str,
    primary_model: str,
    fallback_model: str | None = None,
) -> dict[str, Any]:
    provider = load_production_provider(workspace, provider_key)
    primary = _preflight_one_model(provider, primary_model)
    fallback = {"model": fallback_model, "status": "not_configured", "ok": False, "reason": "not_configured"}
    if fallback_model:
        fallback = _preflight_one_model(provider, fallback_model)
    chosen = primary_model if primary.get("ok") else None
    fallback_used = False
    blocker = None
    if not primary.get("ok") and fallback.get("ok"):
        chosen = fallback_model
        fallback_used = True
    if not chosen:
        blocker = "primary_and_fallback_unavailable" if fallback_model else (primary.get("blocker_reason") or primary.get("status") or "primary_unavailable")
    report = {
        "schema_version": "mvp5i_provider_preflight_v1",
        "created_at": utc_now(),
        "provider": provider_key,
        "primary_model": primary_model,
        "fallback_model": fallback_model,
        "primary_status": primary,
        "fallback_status": fallback,
        "chosen_model": chosen,
        "blocker_reason": blocker,
        "fallback_model_used": fallback_used,
        "pass": bool(chosen),
    }
    _write_json(run_dir / "provider_preflight.json", report)
    _write_text(
        run_dir / "provider_preflight.md",
        "# Provider Preflight\n\n"
        f"- Provider: `{provider_key}`\n"
        f"- Primary model: `{primary_model}`\n"
        f"- Fallback model: `{fallback_model}`\n"
        f"- Primary status: `{primary.get('status')}`\n"
        f"- Fallback status: `{fallback.get('status')}`\n"
        f"- Chosen model: `{chosen}`\n"
        f"- Blocker reason: `{blocker}`\n"
        f"- Fallback model used: `{fallback_used}`\n",
    )
    return report


def _resolve_rollout_path(workspace: Workspace, rollout_run_path: str | Path) -> Path:
    path = Path(rollout_run_path)
    if path.exists():
        return path
    candidate = workspace.path / "artifacts" / "production_rollout" / str(rollout_run_path)
    if candidate.exists():
        return candidate
    raise ValueError(f"Rollout run not found: {rollout_run_path}")


def diagnose_production_qa(workspace: Workspace, *, rollout_run_path: str | Path, chapter: int) -> dict[str, Any]:
    run_dir = _resolve_rollout_path(workspace, rollout_run_path)
    summary = _read_json(run_dir / "production_rollout_summary.json")
    batch_dir = Path(summary.get("batch_dir") or _read_json(run_dir / "production_qa_report.json").get("batch_dir") or "")
    if not batch_dir.exists():
        raise ValueError("Production batch directory missing for QA diagnostic.")
    chapter_results = _read_json(batch_dir / "chapter_results.json").get("chapters") or []
    target = next((row for row in chapter_results if int(row.get("chapter_no") or -1) == int(chapter)), None)
    chunk_dir = None
    if target:
        chapter_id = target.get("chapter_id")
        root = batch_dir / "chunk_outputs" / str(chapter_id)
        if root.exists():
            dirs = sorted(p for p in root.glob("chunk_*") if p.is_dir())
            chunk_dir = dirs[0] if dirs else None
    quality = _read_json(chunk_dir / "quality_report.json") if chunk_dir else {}
    source_text = ""
    if chunk_dir:
        for source_name in ("source.zh.txt", "source.txt"):
            source_path = chunk_dir / source_name
            if source_path.exists():
                source_text = source_path.read_text(encoding="utf-8")
                break
    output_text = (chunk_dir / "translation.vi.txt").read_text(encoding="utf-8") if chunk_dir and (chunk_dir / "translation.vi.txt").exists() else ""
    verification = quality.get("verification") or {}
    strict_rows = verification.get("over_budget_paragraphs") or verification.get("paragraph_budget_failures") or []
    trunc_rows = verification.get("truncated_paragraphs") or []
    by_pid: dict[str, dict[str, Any]] = {}
    for row in strict_rows:
        by_pid.setdefault(str(row.get("paragraph_id") or row.get("id") or "unknown"), {}).update({"strict_max": row})
    for row in trunc_rows:
        by_pid.setdefault(str(row.get("paragraph_id") or row.get("id") or "unknown"), {}).update({"truncation": row})
    if not by_pid and verification.get("reasons"):
        by_pid["unknown"] = {"reasons": verification.get("reasons")}
    source_paras = [p.strip() for p in re.split(r"\n\s*\n+", source_text) if p.strip()]
    output_paras = [p.strip() for p in re.split(r"\n\s*\n+", output_text) if p.strip()]
    table = []
    for i, (sp, op) in enumerate(zip(source_paras or [source_text], output_paras or [output_text]), start=1):
        table.append({"paragraph_index": i, "source_length": len(sp), "output_length": len(op), "source_output_ratio": round(len(op)/max(len(sp),1),3)})
    per_call_usage = []
    usage_path = chunk_dir / "per_call_model_usage.jsonl" if chunk_dir else None
    if usage_path and usage_path.exists():
        per_call_usage = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    issue_rows = []
    quality_table = ((quality.get("verification") or {}).get("per_paragraph_length_table") or [])
    source_lookup = {row.get("paragraph_id"): row for row in quality_table}
    output_lookup = {}
    for idx, para in enumerate(output_paras, start=1):
        output_lookup[idx] = para
    for pid, info in by_pid.items():
        row = source_lookup.get(pid, {})
        para_index = None
        try:
            para_index = int(str(pid).lstrip("u"))
        except Exception:
            para_index = None
        issue_rows.append({
            "unit_id": pid,
            "source_text": row.get("source_text") or "",
            "output_text": output_lookup.get(para_index, ""),
            "source_length": int(row.get("source_char_count") or 0),
            "output_length": int(row.get("output_char_count") or 0),
            "source_output_ratio": round(int(row.get("output_char_count") or 0) / max(int(row.get("source_char_count") or 1), 1), 3),
            "detected_issue": ",".join((info.get("truncation") or {}).get("reasons") or (["strict_max"] if info.get("strict_max") else [])),
            "repair_or_compression_attempted": any(call.get("unit_id") == pid and call.get("call_type") in {"compression","repair","retry"} for call in per_call_usage),
            "repair_or_compression_models": sorted({str(call.get("chosen_model")) for call in per_call_usage if call.get("unit_id") == pid and call.get("call_type") in {"compression","repair","retry"}}),
        })
    diagnostic = {
        "schema_version": "mvp5i_chapter_qa_diagnostic_v1",
        "created_at": utc_now(),
        "rollout_run_path": str(run_dir),
        "chapter": chapter,
        "chapter_result": target,
        "chunk_dir": str(chunk_dir) if chunk_dir else None,
        "triggered_paragraphs": by_pid,
        "paragraph_truncation_detected": bool(trunc_rows or "paragraph_truncation_detected" in verification.get("reasons", [])),
        "paragraph_exceeds_strict_max": bool(strict_rows or "paragraph_exceeds_strict_max" in verification.get("reasons", [])),
        "source_paragraph_count": len(source_paras),
        "output_paragraph_count": len(output_paras),
        "paragraph_boundary_wrong": len(source_paras) != len(output_paras),
        "output_actually_truncated": bool(trunc_rows or "paragraph_truncation_detected" in verification.get("reasons", [])),
        "production_path_differs_from_validated_path": not bool((quality.get("final_output_selector") or {}).get("selected_candidate")),
        "skipped_compression_output_selector_safety_repair": not bool(quality.get("compression") is not None and quality.get("final_output_selector") is not None),
        "paragraph_safety_table": table,
    }
    _write_json(run_dir / f"chapter_{chapter}_qa_diagnostic.json", diagnostic)
    md_lines = ["# Chapter QA Diagnostic", ""]
    md_lines.extend(f"- {k}: `{v}`" for k, v in diagnostic.items() if k not in {"paragraph_safety_table", "issue_rows", "per_call_model_usage"})
    md_lines.extend(["", "## Unit Mapping", ""])
    for row in issue_rows:
        md_lines.extend([
            f"### {row['unit_id']}",
            f"- Source length: `{row['source_length']}`",
            f"- Output length: `{row['output_length']}`",
            f"- Source/output ratio: `{row['source_output_ratio']}`",
            f"- Detected issue: `{row['detected_issue']}`",
            f"- Repair/compression attempted: `{row['repair_or_compression_attempted']}`",
            f"- Repair/compression models: `{', '.join(row['repair_or_compression_models']) if row['repair_or_compression_models'] else 'none'}`",
            "- Source text:",
            row["source_text"] or "",
            "- Output text:",
            row["output_text"] or "",
            "",
        ])
    _write_text(run_dir / f"chapter_{chapter}_qa_diagnostic.md", "\n".join(md_lines))
    _write_text(run_dir / f"chapter_{chapter}_source_output_comparison.md", f"# Chapter {chapter} Source/Output Comparison\n\n## Source\n\n{source_text[:8000]}\n\n## Output\n\n{output_text[:8000]}")
    csv = "paragraph_index,source_length,output_length,source_output_ratio\n" + "\n".join(f"{r['paragraph_index']},{r['source_length']},{r['output_length']},{r['source_output_ratio']}" for r in table)
    _write_text(run_dir / f"chapter_{chapter}_paragraph_safety_table.csv", csv)
    repair = {
        "schema_version": "mvp5i_production_safety_repair_v1",
        "created_at": utc_now(),
        "chapter": chapter,
        "validated_selector_present": not diagnostic["production_path_differs_from_validated_path"],
        "action": "reuse_validated_selector_path_required" if diagnostic["production_path_differs_from_validated_path"] else "production_selector_path_present",
        "do_not_weaken_truncation_detection": True,
    }
    _write_json(run_dir / "production_safety_repair_report.json", repair)
    _write_text(run_dir / "production_safety_repair_report.md", "# Production Safety Repair Report\n\n" + "\n".join(f"- {k}: `{v}`" for k,v in repair.items()))
    return {**diagnostic, "diagnostic_path": str(run_dir / f"chapter_{chapter}_qa_diagnostic.json"), "safety_repair_report_path": str(run_dir / "production_safety_repair_report.json")}



def diagnose_unit_safety(workspace: Workspace, *, rollout_run_path: str | Path, chapter: int) -> dict[str, Any]:
    base = diagnose_production_qa(workspace, rollout_run_path=rollout_run_path, chapter=chapter)
    run_dir = _resolve_rollout_path(workspace, rollout_run_path)
    chunk_dir = Path(base.get("chunk_dir") or "")
    quality = _read_json(chunk_dir / "quality_report.json") if chunk_dir.exists() else {}
    output_text = (chunk_dir / "translation.vi.txt").read_text(encoding="utf-8") if (chunk_dir / "translation.vi.txt").exists() else ""
    output_paras = [p.strip() for p in re.split(r"\n\s*\n+", output_text) if p.strip()]
    verification = quality.get("verification") or {}
    rows = []
    unsafe = set()
    issue_by_id: dict[str, list[str]] = {}
    for row in verification.get("truncated_paragraphs", []) or []:
        pid = str(row.get("paragraph_id"))
        unsafe.add(pid)
        issue_by_id.setdefault(pid, []).extend(row.get("reasons", []))
    for row in verification.get("per_paragraph_length_table", []) or []:
        if row.get("over_strict_max"):
            pid = str(row.get("paragraph_id"))
            unsafe.add(pid)
            issue_by_id.setdefault(pid, []).append("paragraph_exceeds_strict_max")
    usage = []
    usage_path = chunk_dir / "per_call_model_usage.jsonl"
    if usage_path.exists():
        usage = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selection = _read_json(chunk_dir / "candidate_selection_report.json") if (chunk_dir / "candidate_selection_report.json").exists() else {}
    selected_by_id = {row.get("unit_id"): row for row in selection.get("selected", []) or []}
    for item in verification.get("per_paragraph_length_table", []) or []:
        pid = str(item.get("paragraph_id"))
        if pid not in unsafe:
            continue
        idx = None
        try:
            idx = int(pid.lstrip("u"))
        except ValueError:
            pass
        output = output_paras[idx - 1] if idx and idx <= len(output_paras) else ""
        sel = selected_by_id.get(pid, {})
        rows.append({
            "unit_id": pid,
            "source_text": item.get("source_text") or "",
            "output_text": output,
            "source_length": item.get("source_char_count", 0),
            "output_length": item.get("output_char_count", len(output)),
            "ratio": round((item.get("output_char_count", len(output)) or 0) / max(int(item.get("source_char_count") or 1), 1), 3),
            "issue_type": sorted(set(issue_by_id.get(pid, []))),
            "terminal_punctuation_status": _terminal_ok(output),
            "dangling_glossary_label_status": bool(re.search(r"[:：]\s*$", output)),
            "repair_attempted": any(call.get("call_type") == "repair" and call.get("unit_id") == pid for call in usage),
            "compression_attempted": any(call.get("call_type") == "compression" and call.get("unit_id") == pid for call in usage),
            "selected_output_candidate_id": sel.get("selected_output_candidate_id"),
            "rejected_output_candidate_ids": sel.get("rejected_output_candidate_ids", []),
            "selection_reason": sel.get("selection_reason"),
        })
    payload = {"schema_version": "mvp5i_unit_safety_diagnostic_v1", "chapter": chapter, "unsafe_unit_count": len(rows), "unsafe_units": rows}
    _write_json(run_dir / f"chapter_{chapter}_unit_safety_diagnostic.json", payload)
    md = ["# Unit Safety Diagnostic", ""]
    for row in rows:
        md.extend([f"## {row['unit_id']}", f"- Issues: `{', '.join(row['issue_type'])}`", f"- Source/output: `{row['source_length']}` / `{row['output_length']}` ratio `{row['ratio']}`", f"- Terminal punctuation ok: `{row['terminal_punctuation_status']}`", f"- Dangling label: `{row['dangling_glossary_label_status']}`", f"- Repair/compression attempted: `{row['repair_attempted']}` / `{row['compression_attempted']}`", f"- Selected candidate: `{row['selected_output_candidate_id']}`", f"- Rejected candidates: `{row['rejected_output_candidate_ids']}`", "### Source", row['source_text'], "### Output", row['output_text'], ""])
    _write_text(run_dir / f"chapter_{chapter}_unit_safety_diagnostic.md", "\n".join(md))
    csv = "unit_id,source_length,output_length,ratio,issue_type,terminal_punctuation_ok,dangling_label,repair_attempted,compression_attempted,selected_candidate\n" + "\n".join(f"{r['unit_id']},{r['source_length']},{r['output_length']},{r['ratio']},{'|'.join(r['issue_type'])},{r['terminal_punctuation_status']},{r['dangling_glossary_label_status']},{r['repair_attempted']},{r['compression_attempted']},{r.get('selected_output_candidate_id') or ''}" for r in rows)
    _write_text(run_dir / f"chapter_{chapter}_unit_safety_table.csv", csv)
    _write_text(run_dir / f"chapter_{chapter}_source_output_alignment.md", "# Source Output Alignment\n\n" + "\n\n".join(f"## {r['unit_id']}\n\nSource:\n{r['source_text']}\n\nOutput:\n{r['output_text']}" for r in rows))
    return {**payload, "diagnostic_path": str(run_dir / f"chapter_{chapter}_unit_safety_diagnostic.json"), "table_path": str(run_dir / f"chapter_{chapter}_unit_safety_table.csv")}


def _terminal_ok(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…\]）】\)\"']\s*$", (text or "").strip()))

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
    fallback_model: str | None = None,
    chapters: str = "1-10",
    max_chapters: int = 10,
    max_real_calls: int = 24,
    dictionary_max_entries: int = 8,
    memory_max_items: int = 6,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = True,
    max_unit_repair_attempts: int = 2,
    resumable: bool = True,
    dry_run: bool = False,
    canary: bool = False,
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

    preflight = write_provider_preflight(
        workspace,
        run_dir=run_dir,
        provider_key=provider_key,
        primary_model=model,
        fallback_model=fallback_model,
    )
    chosen_model = preflight.get("chosen_model")
    model_policy = build_rollout_model_policy(
        provider_key=provider_key,
        primary_model=model,
        fallback_model=fallback_model,
        chosen_model=chosen_model,
        fallback_model_used=bool(preflight.get("fallback_model_used")),
        primary_status=preflight.get("primary_status"),
        fallback_status=preflight.get("fallback_status"),
    )
    _write_json(run_dir / "model_policy_snapshot.json", model_policy)
    if not preflight.get("pass"):
        summary = {
            "schema_version": PRODUCTION_ROLLOUT_SCHEMA,
            "project_slug": project_slug,
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "final_decision": "BLOCKED",
            "blocker_reason": preflight.get("blocker_reason"),
            "provider_preflight_path": str(run_dir / "provider_preflight.json"),
            "primary_model_status": preflight.get("primary_status"),
            "fallback_model_status": preflight.get("fallback_status"),
            "chosen_model": None,
            "fallback_model_used": False,
            "model_policy_snapshot_path": str(run_dir / "model_policy_snapshot.json"),
            "chapters_processed": 0,
            "chapters_failed": 0,
            "chapters_skipped": 0,
            "chunks_processed": 0,
            "api_calls_used": 0,
            "qa_pass": False,
            "rules_rendered_count": 0,
            "created_at": utc_now(),
        }
        _write_json(run_dir / "production_rollout_summary.json", summary)
        _write_text(
            run_dir / "production_rollout_summary.md",
            f"# Production Rollout Summary\n\n- Final decision: `BLOCKED`\n- Blocker: `{summary['blocker_reason']}`\n",
        )
        return summary

    batch_output_dir = _batch_artifact_dir(workspace, run_dir)
    batch_result = translate_batch_stable(
        workspace,
        project_slug=project_slug,
        provider_key=provider_key,
        model=str(chosen_model),
        rollout_model_policy=model_policy,
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
        max_unit_repair_attempts=max_unit_repair_attempts,
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
        "provider_preflight_path": str(run_dir / "provider_preflight.json"),
        "primary_model_status": preflight.get("primary_status"),
        "fallback_model_status": preflight.get("fallback_status"),
        "chosen_model": chosen_model,
        "fallback_model_used": bool(preflight.get("fallback_model_used")),
        "model_policy_snapshot_path": str(run_dir / "model_policy_snapshot.json"),
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
    if canary:
        canary_report = {
            "schema_version": "mvp5i_canary_report_v1",
            "created_at": utc_now(),
            "pass": final_decision == "PASS" and summary["chapters_processed"] <= 2,
            "chapters_processed": summary["chapters_processed"],
            "chunks_processed": summary["chunks_processed"],
            "qa_pass": summary["qa_pass"],
            "rules_rendered_count": summary["rules_rendered_count"],
            "raw_nlp_cache_injected": any(row.get("kind") == "raw_nlp_cache_in_prompt" for row in qa.get("blocking_issues", [])),
            "stable_hybrid_dictionary_memory_used": True,
            "prompt_budget_respected": not any(row.get("kind") == "prompt_budget_exceeded" for row in qa.get("blocking_issues", [])),
            "human_review_path": summary["human_review_path"],
        }
        summary["canary_report_path"] = str(run_dir / "canary_report.json")
        _write_json(run_dir / "canary_report.json", canary_report)
        _write_text(run_dir / "canary_report.md", "# Canary Report\n\n" + "\n".join(f"- {k}: `{v}`" for k,v in canary_report.items()))
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
