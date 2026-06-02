from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import yaml

from nts_core.dictionary import build_dictionary_prompt_support
from nts_core.hybrid_prompt import build_hybrid_prompt_support
from nts_core.eval_harness import (
    EvalProvider,
    classify_provider_error,
    active_eval_pairs,
    chat_completion_with_provider_retry,
    compress_offending_paragraphs,
    final_output_selector,
    max_tokens_for_paragraph_pairs,
    parse_paragraph_translation_output,
    render_paragraph_translation,
    safe_model_name,
    validate_eval_provider,
    verify_paragraph_output,
)
from nts_core.memory import build_bundle
from nts_core.projects import get_project_by_id, get_project_by_slug
from nts_core.stable_prompts import StablePromptBlocker, StablePromptRecord, load_approved_stable_prompt, prompt_text_for_project
from nts_core.text_import import get_chapter, list_chapters, list_segments
from nts_storage.database import (
    connection,
    insert_task_run,
    json_dumps,
    new_id,
    row_to_dict,
    update_task_run,
    utc_now,
)
from nts_storage.workspace import Workspace


DEFAULT_BATCH_MAX_CHAPTERS = 3
DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER = 5000
DEFAULT_CHUNK_SIZE_CHARS = 3000
DEFAULT_CHUNK_OVERLAP_PARAGRAPHS = 0



def build_rollout_model_policy(
    *,
    provider_key: str,
    primary_model: str,
    fallback_model: str | None,
    chosen_model: str | None,
    fallback_model_used: bool,
    primary_status: dict[str, Any] | None,
    fallback_status: dict[str, Any] | None,
) -> dict[str, Any]:
    route_status = str((primary_status or {}).get("status") or "unknown")
    warning = None
    if fallback_model_used and fallback_model:
        route_status = "fallback_selected"
        warning = f"Primary model {primary_model} failed preflight; fallback {fallback_model} selected."
    return {
        "schema_version": "mvp5i_rollout_model_policy_v1",
        "provider": provider_key,
        "primary_model": primary_model,
        "fallback_model": fallback_model,
        "chosen_model": chosen_model or primary_model,
        "fallback_model_used": bool(fallback_model_used),
        "primary_status": primary_status or {},
        "fallback_status": fallback_status or {},
        "model_route_status": route_status,
        "warning": warning,
    }


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json_dumps(payload) + "\n")


def _record_model_usage(artifact_dir: Path | None, payload: dict[str, Any]) -> None:
    if artifact_dir is None:
        return
    _append_jsonl(artifact_dir / "per_call_model_usage.jsonl", payload)


def _policy_call(
    provider: EvalProvider,
    *,
    policy: dict[str, Any],
    artifact_dir: Path | None,
    call_type: str,
    chapter: str,
    unit_id: str | None,
    messages: list[dict[str, str]],
    max_tokens: int | None,
    retry_attempts: int,
    retry_backoff_seconds: float,
    retry_context: dict[str, Any],
) -> str:
    requested_model = str(policy.get("chosen_model") or policy.get("primary_model") or "")
    fallback_model = policy.get("fallback_model")
    models_to_try = [requested_model]
    if fallback_model and fallback_model not in models_to_try:
        models_to_try.append(str(fallback_model))
    last_exc: Exception | None = None
    for index, model_name in enumerate(models_to_try):
        try:
            raw = chat_completion_with_provider_retry(
                provider,
                model=model_name,
                messages=messages,
                max_tokens=max_tokens,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                retry_context=retry_context,
            )
            payload = {
                "call_type": call_type,
                "chapter": chapter,
                "unit_id": unit_id,
                "provider": policy.get("provider") or provider.key,
                "requested_model": requested_model,
                "chosen_model": model_name,
                "fallback_model_used": index > 0 or bool(policy.get("fallback_model_used")),
                "route_status": "fallback_runtime_success" if index > 0 else str(policy.get("model_route_status") or "ok"),
                "error_class": None,
            }
            _record_model_usage(artifact_dir, payload)
            return raw
        except Exception as exc:
            last_exc = exc
            error_class = classify_provider_error(exc)
            if error_class.get("http_status") == 404:
                error_class["provider_error_type"] = "model_route_not_found"
            payload = {
                "call_type": call_type,
                "chapter": chapter,
                "unit_id": unit_id,
                "provider": policy.get("provider") or provider.key,
                "requested_model": requested_model,
                "chosen_model": model_name,
                "fallback_model_used": index > 0 or bool(policy.get("fallback_model_used")),
                "route_status": error_class.get("provider_error_type") or "provider_error",
                "error_class": error_class,
            }
            _record_model_usage(artifact_dir, payload)
            if error_class.get("http_status") == 404 and index + 1 < len(models_to_try):
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise ValueError("Rollout model policy call failed without exception.")


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short_run_slug(value: str, *, max_chars: int = 16) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_") or "run"
    if len(safe) <= max_chars:
        return safe
    digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:8]
    return f"{safe[: max_chars - 9]}_{digest}"


def _new_run_id(project_slug: str, prefix: str) -> str:
    return f"{_short_run_slug(project_slug)}_{_short_run_slug(prefix, max_chars=12)}_{int(time.time() * 1000)}"


def _read_workspace_provider(workspace: Workspace, provider_key: str) -> EvalProvider | None:
    for path in (workspace.config_dir / "providers.yaml", Path("config/providers.yaml"), Path("config/providers.example.yaml")):
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = data.get("providers") or {}
        if provider_key not in providers:
            continue
        raw = providers[provider_key] or {}
        models = raw.get("models") or ()
        if isinstance(models, str):
            models = tuple(part.strip() for part in models.split(",") if part.strip())
        elif isinstance(models, list):
            models = tuple(str(part) for part in models)
        else:
            models = tuple()
        provider = EvalProvider(
            key=provider_key,
            type=str(raw.get("type", "mock")).strip().lower().replace("-", "_"),
            base_url=str(raw.get("base_url", "")).rstrip("/"),
            api_key_env=str(raw.get("api_key_env", "")),
            route=str(raw.get("route", "chat/completions")).strip("/"),
            models=models,
        )
        if provider.type in {"openai_compatible_chat_completions", "openai_chat_compatible"}:
            provider = EvalProvider(
                key=provider.key,
                type="openai_chat_compatible",
                base_url=provider.base_url,
                api_key_env=provider.api_key_env,
                route=provider.route,
                models=provider.models,
            )
        validate_eval_provider(provider)
        return provider
    if provider_key == "ckey_openai_compatible":
        provider = EvalProvider(
            key=provider_key,
            type="openai_chat_compatible",
            base_url="https://ckey.vn/v1",
            api_key_env="CKEY_API_KEY",
            route="chat/completions",
            models=("gpt-5.5", "gpt-5.4-mini"),
        )
        validate_eval_provider(provider)
        return provider
    return None


def load_production_provider(workspace: Workspace, provider_key: str) -> EvalProvider:
    if provider_key == "mock":
        return EvalProvider(
            key="mock",
            type="mock",
            base_url="mock://local",
            api_key_env="MOCK_API_KEY",
            route="chat/completions",
            models=("mock-eval", "mock-stable", "mock-production"),
        )
    provider = _read_workspace_provider(workspace, provider_key)
    if provider is None:
        raise ValueError(f"Provider not found: {provider_key}")
    return provider


def _chapter_source_text(workspace: Workspace, chapter_id: str, max_source_chars: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    chapter = get_chapter(workspace, chapter_id)
    segments = list_segments(workspace, chapter_id=chapter_id)
    if not segments:
        raise ValueError(f"Chapter has no segments: {chapter_id}")
    source_text = "\n\n".join(segment["normalized_text"] for segment in segments)
    if max_source_chars is not None and max_source_chars > 0:
        source_text = _limit_source_text(source_text, max_source_chars)
    if not source_text.strip():
        raise ValueError(f"Chapter has no source text after limits: {chapter_id}")
    return chapter, segments, source_text


def _limit_source_text(source_text: str, max_source_chars: int) -> str:
    if len(source_text) <= max_source_chars:
        return source_text.strip()
    candidate = source_text[:max_source_chars].rstrip()
    minimum_useful = max(200, int(max_source_chars * 0.55))
    paragraph_cut = max(candidate.rfind("\n\n"), candidate.rfind("\n"))
    if paragraph_cut >= minimum_useful:
        return candidate[:paragraph_cut].rstrip()
    sentence_positions = [
        match.end()
        for match in re.finditer(r"[。.!?！？…]", candidate)
        if match.end() >= minimum_useful
    ]
    if sentence_positions:
        return candidate[: sentence_positions[-1]].rstrip()
    return candidate


def _bundle_glossary(bundle: dict[str, Any]) -> dict[str, Any]:
    fixed_terms = []
    for group in ("terms", "names"):
        for item in bundle.get("items", {}).get(group, []) or []:
            source = item.get("source_key")
            target = item.get("target_text")
            if source and target:
                fixed_terms.append({"source": source, "target": target})
    return {
        "fixed_terms": fixed_terms,
        "pronoun_rules": bundle.get("items", {}).get("pronouns", []),
        "style_rules": bundle.get("items", {}).get("style_rules", []),
        "correction_rules": bundle.get("items", {}).get("corrections", []),
    }


def _estimated_target_chars(source: str) -> int:
    return max(16, int(len(source) * 1.7) + 12)


CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+.+|第.{1,12}[章节回].*|(?:chapter|chuong|chương)\s+\S+.*)",
    re.IGNORECASE,
)


def _production_verification(
    verification: dict[str, Any],
    *,
    sample: dict[str, Any],
) -> dict[str, Any]:
    """Production has no human reference, so only over-expansion is a hard ratio failure."""
    adjusted = dict(verification)
    reasons = list(adjusted.get("reasons", []))
    warnings = list(adjusted.get("warnings", []))
    if "global_ratio_outside_range" in reasons and adjusted.get("global_ratio", 0) < 1.75:
        reasons.remove("global_ratio_outside_range")
        warnings.append("estimated_global_ratio_outside_range_reference_unavailable")
    pair_lookup = {pair["paragraph_id"]: pair for pair in active_eval_pairs(sample)}
    truncated = []
    for entry in adjusted.get("truncated_paragraphs", []):
        pair = pair_lookup.get(entry.get("paragraph_id"), {})
        truncation_reasons = entry.get("reasons", [])
        source_for_pair = str(pair.get("source_text", ""))
        if (
            truncation_reasons == ["missing_terminal_punctuation"]
            and CHAPTER_HEADING_RE.match(source_for_pair)
        ):
            warnings.append("heading_without_terminal_punctuation_allowed")
            continue
        if (
            truncation_reasons == ["missing_terminal_punctuation"]
            and str(pair.get("production_unit_class") or "") in {"pre_panel_label", "risky_short_unit", "system_panel", "stat_line"}
            and not any(reason in truncation_reasons for reason in ("dangling_glossary_label", "suspicious_fragment_ending"))
        ):
            warnings.append("panel_or_separator_terminal_allowed")
            continue
        truncated.append(entry)
    adjusted["truncated_paragraphs"] = truncated
    if not truncated and "paragraph_truncation_detected" in reasons:
        reasons.remove("paragraph_truncation_detected")
    if "paragraph_exceeds_strict_max" in reasons:
        overlong_rows = [
            row
            for row in adjusted.get("per_paragraph_length_table", [])
            if row.get("over_strict_max")
        ]
        if (
            overlong_rows
            and all(row.get("output_reference_ratio", 99) <= 1.35 for row in overlong_rows)
            and not truncated
            and not adjusted.get("terminology_mismatches")
            and all(str(row.get("unit_type") or "") in {"panel", "system_panel", "stat_line"} for row in overlong_rows)
        ):
            reasons.remove("paragraph_exceeds_strict_max")
            warnings.append("estimated_paragraph_budget_exceeded_reference_unavailable")
    adjusted["reasons"] = reasons
    adjusted["warnings"] = warnings
    adjusted["pass"] = not reasons
    return adjusted


def _production_sample(
    *,
    chapter: dict[str, Any],
    source_text: str,
    merge_tiny_paragraphs: bool,
) -> dict[str, Any]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", source_text) if part.strip()]
    pairs = []
    for index, paragraph in enumerate(paragraphs or [source_text], start=1):
        target_count = _estimated_target_chars(paragraph)
        pairs.append(
            {
                "paragraph_id": f"p{index:03d}",
                "source_text": paragraph,
                "target_text": "x" * target_count,
                "source_char_count": len(paragraph),
                "target_char_count": target_count,
                "target_min": max(1, int(target_count * 0.70)),
                "target_max": int(target_count * 1.30),
                "strict_max": int(target_count * 1.55),
                "strict_max_ratio": 1.55,
                "budget_policy_used": "production_estimated_no_reference",
                "alignment_quality": 1.0,
            }
        )
    target_char_count = sum(pair["target_char_count"] for pair in pairs)
    sample = {
        "sample_id": str(chapter["id"]),
        "chapter_id": chapter["id"],
        "source_text": source_text,
        "target_text": "",
        "source_char_count": len(source_text),
        "target_char_count": target_char_count,
        "target_length_min": max(1, int(target_char_count * 0.70)),
        "target_length_max": int(target_char_count * 1.30),
        "paragraph_count_source": len(pairs),
        "paragraph_count_target": len(pairs),
        "accepted_for_stable_validation": True,
        "alignment_quality": 1.0,
        "paragraph_pairs": pairs,
    }
    if merge_tiny_paragraphs:
        return _apply_production_unit_plan(sample)
    return sample


def build_production_prompt(
    *,
    stable_prompt: StablePromptRecord,
    project_slug: str | None = None,
    sample: dict[str, Any],
    memory_bundle: dict[str, Any],
    glossary: dict[str, Any],
    dictionary_block: str | None = None,
    support_block: str | None = None,
) -> tuple[str, str]:
    system_sections = [prompt_text_for_project(stable_prompt, project_slug), ""]
    rendered_support_block = support_block or dictionary_block
    if rendered_support_block:
        system_sections.extend([rendered_support_block, ""])
    system_sections.extend(
        [
            "Production translation mode:",
            "- Translate Chinese source into Vietnamese.",
            "- Use concise Vietnamese webnovel style.",
            "- Do not add translator notes.",
            "- Preserve required names, terms, numbers, dialogue, and system panels.",
            "- Return JSON only with {\"paragraphs\":[{\"paragraph_id\":\"...\",\"text\":\"...\"}]}",
            "- There is no human reference; use conservative length budgets and avoid over-expansion.",
            "- Treat target_max and strict_max as hard character budgets for each paragraph_id.",
            "- For pre-panel labels ending with a colon, translate as a complete Vietnamese setup sentence ending with a period, not a dangling colon.",
            "- For dialogue/narration, keep the Vietnamese compact; do not add explanatory phrases.",
        ]
    )
    system_prompt = "\n".join(system_sections)
    pairs = [
        {
            "paragraph_id": pair["paragraph_id"],
            "source_paragraph_ids": pair.get("source_paragraph_ids", [pair["paragraph_id"]]),
            "source_text": pair["source_text"],
            "target_min": pair["target_min"],
            "target_max": pair["target_max"],
            "strict_max": pair["strict_max"],
        }
        for pair in active_eval_pairs(sample)
    ]
    user_prompt = json_dumps(
        {
            "task": "translate_units" if sample.get("use_translation_units") else "translate_paragraphs",
            "language_pair": stable_prompt.language_pair or "zh-vi",
            "target_language": "Vietnamese",
            "chapter_id": sample["chapter_id"],
            "source_text": sample["source_text"],
            "memory_bundle": memory_bundle,
            "glossary": glossary.get("fixed_terms", []),
            "pronoun_rules": glossary.get("pronoun_rules", []),
            "style_rules": glossary.get("style_rules", []),
            "correction_rules": glossary.get("correction_rules", []),
            "instructions": [
                "Translate every paragraph_id exactly once.",
                "Keep output concise and faithful.",
                "No translator notes.",
                "Preserve paragraph/unit order.",
            ],
            "paragraphs": pairs,
        }
    )
    return system_prompt, user_prompt


def _production_json_retry_prompt(
    *,
    sample: dict[str, Any],
    original_user_prompt: str,
    raw_output: str,
) -> str:
    return json_dumps(
        {
            "task": "repair_translation_json",
            "instructions": [
                "Your previous response missed paragraph_id values or changed their order.",
                "Return JSON only.",
                "Include exactly the requested paragraph_id values once each.",
                "Do not add markdown, explanations, or translator notes.",
                "Preserve the original translation task constraints.",
            ],
            "required_paragraph_ids": [pair["paragraph_id"] for pair in active_eval_pairs(sample)],
            "original_request": json.loads(original_user_prompt),
            "previous_response_excerpt": raw_output[:2000],
            "output_schema": {
                "paragraphs": [
                    {"paragraph_id": pair["paragraph_id"], "text": "Vietnamese translation"}
                    for pair in active_eval_pairs(sample)
                ]
            },
        }
    )


def _production_max_tokens(sample: dict[str, Any], source_text: str) -> int:
    pair_budget = max_tokens_for_paragraph_pairs(active_eval_pairs(sample))
    source_budget = int(len(source_text) * 3.5) + 800
    return max(pair_budget * 2, source_budget, 1600)


def _structure_failure(verification: dict[str, Any]) -> bool:
    errors = set(verification.get("paragraph_validation", {}).get("errors", []))
    return bool(errors & {"missing_paragraph_id", "extra_paragraph_id", "paragraph_order_changed"})


def _mock_paragraph_response(model: str, sample: dict[str, Any], glossary: dict[str, Any]) -> str:
    fixed_terms = glossary.get("fixed_terms", [])
    paragraphs = []
    for pair in active_eval_pairs(sample):
        text = pair["source_text"]
        for term in fixed_terms:
            source = str(term.get("source", ""))
            target = str(term.get("target", ""))
            if source and target:
                text = text.replace(source, target)
        stripped = text.strip()
        if stripped.endswith((":", "：")):
            text = stripped[:-1].rstrip() + "."
        elif text and not re.search(r'[.!?。！？…】）)"\']$', text):
            text = f"{text}."
        paragraphs.append({"paragraph_id": pair["paragraph_id"], "text": text})
    return json_dumps({"paragraphs": paragraphs})

def _insert_model_run(
    conn: sqlite3.Connection,
    *,
    task_run_id: str,
    provider: EvalProvider,
    model: str,
    prompt: str,
    response: str,
    status: str = "success",
) -> str:
    model_run_id = new_id("modelrun")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO model_runs (
            id, task_run_id, provider_key, adapter_type, base_url, model_name,
            prompt_hash, input_tokens, output_tokens, cost_estimate, status,
            started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_run_id,
            task_run_id,
            provider.key,
            provider.type,
            provider.base_url,
            model,
            _sha256_hex(prompt),
            max(1, len(prompt) // 4),
            max(1, len(response) // 4),
            0.0,
            status,
            now,
            now,
        ),
    )
    return model_run_id


def _existing_current_translation(conn: sqlite3.Connection, chapter_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, chapter_id, text, status, model_run_id, bundle_checksum, quality_json, created_at
        FROM translations
        WHERE chapter_id = ? AND is_current = 1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()
    return row_to_dict(row, json_fields=("quality_json",)) if row else None






def _is_separator_line(text: str) -> bool:
    return bool(re.fullmatch(r"[-–—=_*~.\s]{3,}", (text or "").strip()))

def _split_dialogue_and_separator(text: str) -> list[str] | None:
    stripped = (text or "").strip()
    match = re.search(r"(.+?)(\s+[-–—=_*~.]{3,})$", stripped)
    if not match:
        return None
    leading = match.group(1).strip()
    trailing = match.group(2).strip()
    if not leading or not _is_separator_line(trailing):
        return None
    return [leading, trailing]

def _sentence_split_parts(text: str, *, max_part_chars: int = 56, max_parts: int = 2) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?…])", stripped) if part.strip()]
    if len(parts) <= 1:
        return [stripped]
    groups: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{part}" if current else part
        if current and len(candidate) > max_part_chars and len(groups) + 1 < max_parts:
            groups.append(current.strip())
            current = part
        else:
            current = candidate
    if current.strip():
        groups.append(current.strip())
    return groups or [stripped]

def _classify_production_pair(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if CHAPTER_HEADING_RE.match(stripped):
        return "system_panel"
    if _is_separator_line(stripped):
        return "system_panel"
    if stripped.startswith("【") and stripped.endswith("】") and len(stripped) > 60:
        stat_markers = ("修为", "法器", "神通", "法术", "灵根", "资质", "姓名", "寿命", "种族")
        descriptive_punctuation = stripped.count("，") + stripped.count("。") + stripped.count("；") + stripped.count("：")
        if descriptive_punctuation >= 3 and not any(marker in stripped[:12] for marker in stat_markers):
            return "mixed_panel_narration"
    if stripped.startswith("【") and stripped.endswith("】"):
        if any(token in stripped for token in ("修为", "法器", "神通", "法术", "灵根", "资质", "姓名", "寿命", "种族")):
            return "stat_line"
        return "system_panel"
    if stripped.endswith(("：", ":")):
        return "pre_panel_label"
    if "【" in stripped and "】" in stripped and len(stripped) > 180:
        return "mixed_panel_narration"
    if re.search(r"^[0-9０-９]+[、,.:：)]", stripped) or re.search(r"[：:][^\n]{1,40}$", stripped):
        return "glossary_label"
    if len(stripped) <= 24:
        return "short_action"
    if len(stripped) <= 50:
        return "risky_short_unit"
    if any(mark in stripped for mark in ('“', '”', '"')):
        return "dialogue"
    if len(stripped) > 180:
        return "oversized_unit"
    return "narration"

def _split_source_text(text: str, *, production_unit_class: str | None = None) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    dialogue_split = _split_dialogue_and_separator(stripped)
    if dialogue_split:
        return dialogue_split
    if production_unit_class == "mixed_panel_narration" and len(stripped) > 40:
        inner = stripped[1:-1].strip() if stripped.startswith("【") and stripped.endswith("】") else stripped
        comma_parts = [part.strip() for part in re.split(r"(?<=，)", inner) if part.strip()]
        if len(comma_parts) > 3:
            groups: list[str] = []
            current = ""
            max_group_chars = 48
            for part in comma_parts:
                candidate = f"{current}{part}" if current else part
                if current and len(candidate) > max_group_chars and len(groups) < 2:
                    groups.append(current.strip())
                    current = part
                else:
                    current = candidate
            if current.strip():
                groups.append(current.strip())
            if len(groups) > 1:
                groups[0] = f"【{groups[0].lstrip('【')}"
                groups[-1] = f"{groups[-1].rstrip('】')}】"
                return groups[:3]
        if any(mark in inner for mark in ("。", "！", "？", "!", "?", "…")):
            sentence_parts = [part.strip() for part in re.split(r"(?<=[。！？!?…])", inner) if part.strip()]
            sentence_parts = [part for part in sentence_parts if re.search(r"[\w一-鿿]", part)]
            if len(sentence_parts) > 1:
                sentence_parts[0] = f"【{sentence_parts[0].lstrip('【')}"
                sentence_parts[-1] = f"{sentence_parts[-1].rstrip('】')}】"
                return sentence_parts[:3]
        parts = _sentence_split_parts(inner, max_part_chars=72, max_parts=3)
        if len(parts) > 1:
            parts[0] = f"【{parts[0].lstrip('【')}"
            parts[-1] = f"{parts[-1].rstrip('】')}】"
            return parts
    if production_unit_class == "narration" and len(stripped) > 60 and any(mark in stripped for mark in ("，", "。", "；")):
        comma_parts = [part.strip() for part in re.split(r"(?<=，)", stripped) if part.strip()]
        if len(comma_parts) > 2:
            first = "".join(comma_parts[: max(2, len(comma_parts) // 2)]).strip()
            second = "".join(comma_parts[max(2, len(comma_parts) // 2):]).strip()
            return [first, second]
        parts = _sentence_split_parts(stripped, max_part_chars=80, max_parts=3)
        if len(parts) > 1:
            return parts
    if len(stripped) <= 220:
        return [stripped]
    return _sentence_split_parts(stripped, max_part_chars=180, max_parts=3)

def _compact_pair_budget(pair: dict[str, Any]) -> dict[str, Any]:
    pair = dict(pair)
    cls = _classify_production_pair(str(pair.get("source_text") or ""))
    source_len = int(pair.get("source_char_count") or len(str(pair.get("source_text") or "")))
    if cls == "system_panel":
        target_count = max(20, int(source_len * 1.4) + 10)
        strict_ratio = 2.05
    elif cls == "stat_line":
        target_count = max(20, int(source_len * 1.4) + 10)
        strict_ratio = 2.05
    elif cls in {"pre_panel_label", "glossary_label"}:
        target_count = max(20, int(source_len * 1.55) + 8)
        strict_ratio = 2.0
    elif cls == "short_action":
        target_count = max(24, int(source_len * 2.05) + 10)
        strict_ratio = 2.15
    elif cls == "risky_short_unit":
        target_count = max(30, int(source_len * 2.15) + 12)
        strict_ratio = 2.2
    elif cls == "mixed_panel_narration":
        target_count = max(72, int(source_len * 1.55) + 16)
        strict_ratio = 1.9
    elif cls == "oversized_unit":
        target_count = max(96, int(source_len * 1.45) + 16)
        strict_ratio = 1.8
    elif cls == "dialogue":
        target_count = max(42, int(source_len * 1.65) + 14)
        strict_ratio = 1.95
    else:
        target_count = max(36, int(source_len * 1.55) + 12)
        strict_ratio = 1.9
    pair.update({
        "production_unit_class": cls,
        "target_char_count": target_count,
        "reference_char_count": target_count,
        "target_text": "x" * target_count,
        "reference_text": "x" * target_count,
        "target_min": max(1, int(target_count * 0.70)),
        "target_max": max(1, int(target_count * 1.20)),
        "strict_max": max(int(target_count * strict_ratio), int(source_len * strict_ratio)),
        "strict_max_ratio": strict_ratio,
        "budget_policy_used": f"production_{cls}_compact" if cls in {"system_panel", "stat_line", "pre_panel_label", "glossary_label", "short_action", "risky_short_unit", "mixed_panel_narration", "oversized_unit"} else pair.get("budget_policy_used", "production_estimated_no_reference"),
    })
    return pair

def _apply_production_unit_plan(sample: dict[str, Any]) -> dict[str, Any]:
    original_pairs = [_compact_pair_budget(pair) for pair in sample.get("paragraph_pairs", [])]
    units = []
    split_rows = []
    for idx, pair in enumerate(original_pairs, start=1):
        unit_id = f"u{idx:03d}"
        source_parts = _split_source_text(str(pair.get("source_text") or ""), production_unit_class=str(pair.get("production_unit_class") or ""))
        split_required = len(source_parts) > 1 and (
            pair.get("production_unit_class") in {"oversized_unit", "dialogue", "narration", "mixed_panel_narration"}
            or any(_is_separator_line(part) for part in source_parts)
        )
        child_ids: list[str] = []
        if split_required:
            for child_index, part in enumerate(source_parts, start=1):
                child_id = f"{unit_id}_{chr(96 + child_index)}"
                child_ids.append(child_id)
                child = _compact_pair_budget({**pair, "source_text": part, "source_char_count": len(part)})
                if str(pair.get("production_unit_class") or "") == "mixed_panel_narration":
                    child["production_unit_class"] = "mixed_panel_narration"
                child.update({
                    "unit_id": child_id,
                    "paragraph_id": child_id,
                    "source_paragraph_ids": [pair["paragraph_id"]],
                    "target_paragraph_ids": [pair["paragraph_id"]],
                    "original_paragraph_ids": [pair["paragraph_id"]],
                    "parent_unit_id": unit_id,
                    "unit_type": child.get("production_unit_class", "narration"),
                    "merge_reason": "production_pretranslate_split",
                    "is_merged_unit": False,
                    "original_paragraph_count": 1,
                    "split_child": True,
                })
                units.append(child)
        else:
            unit = dict(pair)
            unit.update({
                "unit_id": unit_id,
                "paragraph_id": unit_id,
                "source_paragraph_ids": [pair["paragraph_id"]],
                "target_paragraph_ids": [pair["paragraph_id"]],
                "original_paragraph_ids": [pair["paragraph_id"]],
                "unit_type": "panel" if pair.get("production_unit_class") in {"system_panel", "stat_line", "pre_panel_label", "glossary_label"} else pair.get("production_unit_class", "narration"),
                "merge_reason": "production_one_input_one_output",
                "is_merged_unit": False,
                "original_paragraph_count": 1,
                "split_child": False,
            })
            units.append(unit)
        split_rows.append({
            "unit_id": unit_id,
            "source_length": len(str(pair.get("source_text") or "")),
            "split_required": split_required,
            "child_ids": child_ids,
            "reason": "oversized_source_unit" if split_required else "within_production_unit_budget",
        })
    updated = dict(sample)
    updated["paragraph_pairs"] = original_pairs
    updated["translation_units"] = units
    updated["use_translation_units"] = True
    updated["translation_unit_merge_count"] = 0
    updated["production_unit_plan_enabled"] = True
    updated["production_unit_split_rows"] = split_rows
    return updated

def _write_production_unit_reports(artifact_dir: Path, sample: dict[str, Any]) -> None:
    rows = []
    for pair in active_eval_pairs(sample):
        rows.append({
            "unit_id": pair["paragraph_id"],
            "source_paragraph_ids": pair.get("source_paragraph_ids", []),
            "classification": pair.get("production_unit_class") or pair.get("unit_type"),
            "source_length": pair.get("source_char_count", 0),
            "target_max": pair.get("target_max"),
            "strict_max": pair.get("strict_max"),
            "budget_policy_used": pair.get("budget_policy_used"),
            "mode": "compact_panel_short_line" if pair.get("production_unit_class") in {"system_panel", "stat_line", "pre_panel_label", "risky_short_unit", "mixed_panel_narration"} else "standard",
        })
    payload = {"schema_version": "mvp5i_production_unit_plan_v1", "unit_count": len(rows), "units": rows}
    (artifact_dir / "production_unit_plan.json").write_text(json_dumps(payload) + "\n", encoding="utf-8")
    (artifact_dir / "unit_classification_report.json").write_text(json_dumps(payload) + "\n", encoding="utf-8")
    lines = ["# Production Unit Plan", ""] + [f"- `{r['unit_id']}` {r['classification']} mode={r['mode']} strict_max={r['strict_max']}" for r in rows]
    (artifact_dir / "production_unit_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (artifact_dir / "unit_classification_report.md").write_text("\n".join(["# Unit Classification Report", ""] + lines[2:]) + "\n", encoding="utf-8")
    csv = "unit_id,classification,source_length,target_max,strict_max,budget_policy_used,mode\n" + "\n".join(f"{r['unit_id']},{r['classification']},{r['source_length']},{r['target_max']},{r['strict_max']},{r['budget_policy_used']},{r['mode']}" for r in rows)
    (artifact_dir / "unit_classification_table.csv").write_text(csv + "\n", encoding="utf-8")

def _terminal_ok(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…\]）】\)\"']\s*$", (text or "").strip()))


def _write_unit_split_plan(artifact_dir: Path, sample: dict[str, Any]) -> None:
    rows = list(sample.get("production_unit_split_rows") or [])
    if not rows:
        for pair in active_eval_pairs(sample):
            rows.append({
                "unit_id": pair["paragraph_id"],
                "source_length": len(str(pair.get("source_text") or "")),
                "split_required": False,
                "child_ids": [],
                "reason": "within_production_unit_budget",
            })
    payload = {"schema_version": "mvp5i_unit_split_plan_v1", "units": rows}
    (artifact_dir / "unit_split_plan.json").write_text(json_dumps(payload) + "\n", encoding="utf-8")
    lines = ["# Unit Split Plan", ""]
    for row in rows:
        lines.append(f"- `{row['unit_id']}` split={row['split_required']} source_len={row['source_length']} reason={row['reason']}")
    (artifact_dir / "unit_split_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _repair_prompt(pair: dict[str, Any], current_text: str, reasons: list[str], glossary: dict[str, Any]) -> str:
    return json_dumps({
        "task": "repair_one_translation_unit",
        "unit_id": pair["paragraph_id"],
        "source_text": pair.get("source_text", ""),
        "current_translation": current_text,
        "detected_reasons": reasons,
        "strict_max": pair.get("strict_max"),
        "instructions": [
            "Repair only this Vietnamese unit.",
            "Preserve source meaning, names, terms, numbers, dialogue, and panels.",
            "Return a complete safe Vietnamese unit; do not truncate.",
            "Do not leave dangling labels or glossary fragments.",
            "Prefer safe completion over shorter incomplete text.",
            "If the unit is over strict_max, materially compress sentence structure and remove explanatory connective wording not explicit in the source.",
            "For mixed_panel_narration units, keep the bracketed prophecy compact and image-driven; avoid repeating subjects or adding interpretive phrases.",
        ],
        "glossary": glossary.get("fixed_terms", []),
        "output_schema": {"paragraphs": [{"paragraph_id": pair["paragraph_id"], "text": "safe complete Vietnamese text"}]},
    })


def _repair_unsafe_units(
    *,
    provider: EvalProvider,
    policy: dict[str, Any],
    artifact_dir: Path,
    chapter_label: str,
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
    verification: dict[str, Any],
    glossary: dict[str, Any],
    max_attempts: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    pair_lookup = {pair["paragraph_id"]: pair for pair in active_eval_pairs(sample)}
    para_lookup = {p["paragraph_id"]: p["text"] for p in paragraphs}
    unsafe_ids = []
    reasons_by_id: dict[str, list[str]] = {}
    for row in verification.get("truncated_paragraphs", []) or []:
        pid = row.get("paragraph_id")
        if pid:
            unsafe_ids.append(pid)
            reasons_by_id.setdefault(pid, []).extend(row.get("reasons", []))
    for row in verification.get("per_paragraph_length_table", []) or []:
        if row.get("over_strict_max"):
            pid = row.get("paragraph_id")
            unsafe_ids.append(pid)
            reasons_by_id.setdefault(pid, []).append("paragraph_exceeds_strict_max")
    unsafe_ids = list(dict.fromkeys(unsafe_ids))
    attempts_path = artifact_dir / "unit_repair_attempts.jsonl"
    candidates = []
    selected = []
    repaired = dict(para_lookup)
    for pid in unsafe_ids:
        pair = pair_lookup.get(pid)
        if not pair:
            continue
        original = repaired.get(pid, "")
        best = original
        best_status = "original"
        rejected = []
        for attempt in range(1, max(0, max_attempts) + 1):
            if provider.key == "mock":
                candidate = original if _terminal_ok(original) else original.rstrip() + "."
                _record_model_usage(artifact_dir, {"call_type": "repair", "chapter": chapter_label, "unit_id": pid, "provider": policy.get("provider") or provider.key, "requested_model": str(policy.get("primary_model")), "chosen_model": str(policy.get("chosen_model")), "fallback_model_used": bool(policy.get("fallback_model_used")), "route_status": str(policy.get("model_route_status") or "ok"), "error_class": None})
            else:
                raw = _policy_call(
                    provider,
                    policy=policy,
                    artifact_dir=artifact_dir,
                    call_type="repair",
                    chapter=chapter_label,
                    unit_id=pid,
                    messages=[{"role": "system", "content": "Repair one Vietnamese translation unit. Return JSON only."}, {"role": "user", "content": _repair_prompt(pair, original, reasons_by_id.get(pid, []), glossary)}],
                    max_tokens=max_tokens_for_paragraph_pairs([pair]),
                    retry_attempts=1,
                    retry_backoff_seconds=5 if provider.key != "mock" else 0,
                    retry_context={"phase": "production_unit_repair", "paragraph_id": pid},
                )
                parsed = parse_paragraph_translation_output(raw)
                candidate = next((item.get("text", "") for item in parsed if item.get("paragraph_id") == pid), "")
            test_paras = [{"paragraph_id": p["paragraph_id"], "text": candidate if p["paragraph_id"] == pid else repaired.get(p["paragraph_id"], p["text"])} for p in paragraphs]
            test_ver = _production_verification(verify_paragraph_output(sample, test_paras, glossary=glossary), sample=sample)
            unit_truncated = any(r.get("paragraph_id") == pid for r in test_ver.get("truncated_paragraphs", []) or [])
            unit_over_strict = any(r.get("paragraph_id") == pid and r.get("over_strict_max") for r in test_ver.get("per_paragraph_length_table", []) or [])
            terminal_ok = _terminal_ok(candidate)
            dangling = bool(re.search(r"[:：]\s*$", candidate or ""))
            safe_reason = bool(pair.get("unit_type") == "panel" and terminal_ok and not unit_truncated and not dangling)
            unit_bad = unit_truncated or dangling or (unit_over_strict and not safe_reason)
            row = {"unit_id": pid, "attempt": attempt, "candidate_id": f"{pid}_repair_{attempt}", "candidate_text": candidate, "pass": not unit_bad, "reasons": test_ver.get("reasons", []), "terminal_ok": terminal_ok, "dangling_glossary_label": dangling, "safe_over_strict_reason": "panel_complete" if safe_reason else None, "model": policy.get("chosen_model")}
            _append_jsonl(attempts_path, row)
            candidates.append(row)
            if candidate.strip() and terminal_ok and not unit_bad:
                best = candidate
                best_status = row["candidate_id"]
                break
            rejected.append(row["candidate_id"])
        repaired[pid] = best
        selected.append({"unit_id": pid, "selected_output_candidate_id": best_status, "rejected_output_candidate_ids": rejected, "selection_reason": "safe_repair_selected" if best_status != "original" else "repair_failed_original_retained"})
    updated = [{"paragraph_id": p["paragraph_id"], "text": repaired.get(p["paragraph_id"], p["text"])} for p in paragraphs]
    report = {"schema_version": "mvp5i_candidate_selection_report_v1", "unsafe_unit_ids": unsafe_ids, "selected": selected, "candidates": candidates, "max_unit_repair_attempts": max_attempts}
    (artifact_dir / "candidate_selection_report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
    lines = ["# Candidate Selection Report", ""] + [f"- `{r['unit_id']}` selected `{r['selected_output_candidate_id']}` rejected {r['rejected_output_candidate_ids']} reason={r['selection_reason']}" for r in selected]
    (artifact_dir / "candidate_selection_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (artifact_dir / "unit_repair_summary.md").write_text("# Unit Repair Summary\n\n" + f"- Unsafe units: `{len(unsafe_ids)}`\n- Repair attempts: `{len(candidates)}`\n", encoding="utf-8")
    (artifact_dir / "production_selector_alignment_report.json").write_text(json_dumps({"schema_version": "mvp5i_selector_alignment_v1", "uses_validation_selector": True, "selector": "final_output_selector", "divergent_logic": False}) + "\n", encoding="utf-8")
    (artifact_dir / "production_selector_alignment_report.md").write_text("# Production Selector Alignment\n\n- Uses validation selector: `True`\n- Divergent logic: `False`\n", encoding="utf-8")
    return updated, report

def translate_chapter_stable(
    workspace: Workspace,
    *,
    chapter_id: str,
    provider_key: str,
    model: str,
    use_stable_prompt: bool,
    prompt_id: str | None = None,
    max_source_chars: int | None = None,
    enable_paragraph_alignment: bool = True,
    enable_compression_pass: bool = True,
    merge_tiny_paragraphs: bool = True,
    evaluate_after: bool = False,
    dry_run: bool = False,
    output_dir: Path | None = None,
    force: bool = False,
    artifact_run_id: str | None = None,
    source_override: str | None = None,
    save_translation_row: bool = True,
    use_approved_dictionary: bool = False,
    use_hybrid_prompt: bool = False,
    dictionary_max_entries: int = 8,
    dictionary_max_chars: int = 500,
    memory_max_items: int = 6,
    use_approved_rules: bool = False,
    rule_max_hints: int = 4,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
    rollout_model_policy: dict[str, Any] | None = None,
    max_unit_repair_attempts: int = 2,
) -> dict[str, Any]:
    if not use_stable_prompt:
        from nts_core.translation import translate_chapter_mock

        return translate_chapter_mock(workspace, chapter_id=chapter_id, provider_key=provider_key)
    if use_approved_rules:
        use_hybrid_prompt = True
        use_approved_dictionary = True
    stable_prompt = load_approved_stable_prompt(workspace, prompt_id=prompt_id)
    provider = load_production_provider(workspace, provider_key)
    policy = rollout_model_policy or build_rollout_model_policy(provider_key=provider_key, primary_model=model, fallback_model=None, chosen_model=model, fallback_model_used=False, primary_status={"status": "direct"}, fallback_status={})
    model = str(policy.get("chosen_model") or model)
    chapter, _segments, source_text = _chapter_source_text(workspace, chapter_id, max_source_chars)
    if source_override is not None:
        source_text = source_override
    project = get_project_by_id(workspace, chapter["project_id"])
    run_id = artifact_run_id or _new_run_id(project["slug"], "translation")
    artifact_dir = output_dir or (workspace.path / "artifacts" / "translations" / run_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    warnings = []
    if not dry_run:
        warnings.append("Real provider call may incur API cost." if provider.key != "mock" else "mock provider used")

    bundle = build_bundle(workspace, project_id=project["id"], text=source_text, top_k=30)
    glossary = _bundle_glossary(bundle)
    sample = _production_sample(chapter=chapter, source_text=source_text, merge_tiny_paragraphs=merge_tiny_paragraphs)
    _write_production_unit_reports(artifact_dir, sample)
    _write_unit_split_plan(artifact_dir, sample)
    hybrid_context = (
        build_hybrid_prompt_support(
            workspace,
            project["slug"],
            source_text,
            mode="production",
            max_dictionary_entries=dictionary_max_entries,
            max_memory_items=memory_max_items,
            use_approved_rules=use_approved_rules,
            max_rule_hints=rule_max_hints,
            max_support_chars=support_max_chars,
            chapters={int(chapter["chapter_no"])} if chapter.get("chapter_no") is not None else None,
        )
        if use_hybrid_prompt
        else None
    )
    dictionary_context = (
        build_dictionary_prompt_support(
            workspace,
            project["slug"],
            source_text,
            max_entries=dictionary_max_entries,
            max_chars=dictionary_max_chars,
        )
        if use_approved_dictionary and not use_hybrid_prompt
        else None
    )
    system_prompt, user_prompt = build_production_prompt(
        stable_prompt=stable_prompt,
        project_slug=project["slug"],
        sample=sample,
        memory_bundle=bundle,
        glossary=glossary,
        dictionary_block=(dictionary_context or {}).get("block_text"),
        support_block=(hybrid_context or {}).get("block_text"),
    )
    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
    prompt_path = artifact_dir / "prompt_used.md"
    source_path = artifact_dir / "source.txt"
    bundle_path = artifact_dir / "memory_bundle.json"
    response_path = artifact_dir / "model_response_raw.json"
    output_path = artifact_dir / "translation.vi.txt"
    quality_path = artifact_dir / "quality_report.json"
    manifest_path = artifact_dir / "run_manifest.json"
    source_path.write_text(source_text + "\n", encoding="utf-8")
    bundle_path.write_text(json_dumps(bundle) + "\n", encoding="utf-8")
    prompt_path.write_text(full_prompt + "\n", encoding="utf-8")
    if use_hybrid_prompt:
        context_payload = dict(hybrid_context or {})
        context_payload["source_chunk_id"] = chapter_id
        context_payload["prompt_sha256"] = _sha256_hex(full_prompt)
        (artifact_dir / "prompt_context_bundle.json").write_text(
            json_dumps(context_payload) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_budget_report.json").write_text(
            json_dumps(context_payload.get("budget_report") or {}) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_retrieval_report.json").write_text(
            json_dumps(context_payload.get("retrieval_report") or {}) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_conflict_report.json").write_text(
            json_dumps(context_payload.get("conflict_report") or {}) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_support_items.json").write_text(
            json_dumps(context_payload.get("support_items") or {}) + "\n",
            encoding="utf-8",
        )
    elif use_approved_dictionary or emit_prompt_artifacts:
        context_payload = dictionary_context or {
            "schema_version": "dictionary_prompt_context_bundle_v1",
            "project_slug": project["slug"],
            "source_sha256": _sha256_hex(source_text),
            "block_text": "",
            "block_rendered": False,
            "selected_hits": [],
            "dropped_hits": [],
            "budget_report": {
                "schema_version": "dictionary_prompt_budget_report_v1",
                "max_dictionary_entries": dictionary_max_entries,
                "max_dictionary_chars": dictionary_max_chars,
                "eligible_hit_count": 0,
                "selected_hit_count": 0,
                "dropped_hit_count": 0,
                "support_chars": 0,
                "block_rendered": False,
            },
            "retrieval_report": {
                "schema_version": "dictionary_prompt_retrieval_report_v1",
                "project_slug": project["slug"],
                "exact_source_match_required": True,
                "active_approved_only": True,
                "selected_hits": [],
                "dropped_hits": [],
            },
        }
        context_payload["source_chunk_id"] = chapter_id
        context_payload["prompt_sha256"] = _sha256_hex(full_prompt)
        (artifact_dir / "prompt_context_bundle.json").write_text(
            json_dumps(context_payload) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_budget_report.json").write_text(
            json_dumps(context_payload.get("budget_report") or {}) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "prompt_retrieval_report.json").write_text(
            json_dumps(context_payload.get("retrieval_report") or {}) + "\n",
            encoding="utf-8",
        )

    with connection(workspace.db_path) as conn:
        if not force and save_translation_row and _existing_current_translation(conn, chapter_id):
            raise ValueError("Current translation already exists for chapter; use --force to create a new attempt.")
        task_id = insert_task_run(
            conn,
            task_type="translate.text.stable",
            status="running" if not dry_run else "success",
            stage="dry_run" if dry_run else "provider_call",
            project_id=project["id"],
            input_data={
                "chapter_id": chapter_id,
                "provider": provider_key,
                "model": model,
                "rollout_model_policy": policy,
                "prompt_id": stable_prompt.prompt_id,
                "dry_run": dry_run,
                "use_approved_dictionary": use_approved_dictionary,
                "use_hybrid_prompt": use_hybrid_prompt,
                "dictionary_max_entries": dictionary_max_entries,
                "memory_max_items": memory_max_items,
                "use_approved_rules": use_approved_rules,
                "rule_max_hints": rule_max_hints,
                "support_max_chars": support_max_chars,
            },
            result_data={},
        )
        conn.commit()

    model_run_id = None
    translation_id = None
    raw_response = ""
    raw_attempts: list[dict[str, Any]] = []
    final_text = ""
    quality: dict[str, Any] = {
        "dry_run": dry_run,
        "warnings": warnings,
        "prompt_id": stable_prompt.prompt_id,
        "prompt_version": stable_prompt.prompt_version,
        "bundle_checksum": bundle["checksum"],
    }
    status = "dry_run" if dry_run else "success"
    if dry_run:
        raw_response = json_dumps({"dry_run": True, "paragraphs": []})
        final_text = ""
    else:
        if provider.key == "mock":
            raw_response = _mock_paragraph_response(model, sample, glossary)
            _record_model_usage(artifact_dir, {
                "call_type": "translation",
                "chapter": str(chapter.get("chapter_no") or chapter_id),
                "unit_id": None,
                "provider": policy.get("provider") or provider.key,
                "requested_model": str(policy.get("primary_model") or model),
                "chosen_model": str(policy.get("chosen_model") or model),
                "fallback_model_used": bool(policy.get("fallback_model_used")),
                "route_status": str(policy.get("model_route_status") or "ok"),
                "error_class": None,
            })
        else:
            raw_response = _policy_call(
                provider,
                policy=policy,
                artifact_dir=artifact_dir,
                call_type="translation",
                chapter=str(chapter.get("chapter_no") or chapter_id),
                unit_id=None,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=_production_max_tokens(sample, source_text),
                retry_attempts=3,
                retry_backoff_seconds=5,
                retry_context={"phase": "production_translate", "chapter_id": chapter_id},
            )
        raw_attempts.append({"attempt_no": 1, "raw_response": raw_response})
        parsed = parse_paragraph_translation_output(raw_response)
        before_verification = _production_verification(
            verify_paragraph_output(sample, parsed, glossary=glossary),
            sample=sample,
        )
        if provider.key != "mock" and _structure_failure(before_verification):
            retry_prompt = _production_json_retry_prompt(
                sample=sample,
                original_user_prompt=user_prompt,
                raw_output=raw_response,
            )
            raw_response = _policy_call(
                provider,
                policy=policy,
                artifact_dir=artifact_dir,
                call_type="retry",
                chapter=str(chapter.get("chapter_no") or chapter_id),
                unit_id=None,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": retry_prompt},
                ],
                max_tokens=_production_max_tokens(sample, source_text),
                retry_attempts=2,
                retry_backoff_seconds=5,
                retry_context={"phase": "production_json_retry", "chapter_id": chapter_id},
            )
            raw_attempts.append({"attempt_no": 2, "raw_response": raw_response})
            parsed = parse_paragraph_translation_output(raw_response)
            before_verification = _production_verification(
                verify_paragraph_output(sample, parsed, glossary=glossary),
                sample=sample,
            )
        final_paragraphs = parsed
        compression_result = {"triggered": False, "entries": [], "offending_paragraph_ids": []}
        if enable_compression_pass:
            final_paragraphs, compression_result = compress_offending_paragraphs(
                provider,
                model=model,
                provider_call=lambda **kwargs: _policy_call(
                    provider,
                    policy=policy,
                    artifact_dir=artifact_dir,
                    call_type="compression",
                    chapter=str(chapter.get("chapter_no") or chapter_id),
                    unit_id=str((kwargs.get("retry_context") or {}).get("paragraph_id") or "" ) or None,
                    messages=kwargs["messages"],
                    max_tokens=kwargs.get("max_tokens"),
                    retry_attempts=kwargs.get("retry_attempts", 1),
                    retry_backoff_seconds=kwargs.get("retry_backoff_seconds", 0.0),
                    retry_context=kwargs.get("retry_context") or {},
                ),
                sample=sample,
                paragraphs=parsed,
                glossary=glossary,
                provider_retry_attempts=3,
                provider_retry_backoff_seconds=5 if provider.key != "mock" else 0,
                provider_retry_context={"phase": "production_compression", "chapter_id": chapter_id},
            )
        after_verification = _production_verification(
            verify_paragraph_output(sample, final_paragraphs, glossary=glossary),
            sample=sample,
        )
        if not after_verification.get("pass") and max_unit_repair_attempts > 0:
            final_paragraphs, repair_report = _repair_unsafe_units(
                provider=provider,
                policy=policy,
                artifact_dir=artifact_dir,
                chapter_label=str(chapter.get("chapter_no") or chapter_id),
                sample=sample,
                paragraphs=final_paragraphs,
                verification=after_verification,
                glossary=glossary,
                max_attempts=max_unit_repair_attempts,
            )
            after_verification = _production_verification(
                verify_paragraph_output(sample, final_paragraphs, glossary=glossary),
                sample=sample,
            )
        else:
            repair_report = {"unsafe_unit_ids": [], "selected": [], "candidates": []}
            (artifact_dir / "candidate_selection_report.json").write_text(
                json_dumps({"schema_version": "mvp5i_candidate_selection_report_v1", **repair_report}) + "\n",
                encoding="utf-8",
            )
            (artifact_dir / "candidate_selection_report.md").write_text(
                "# Candidate Selection Report\n\nNo unsafe units required repair.\n",
                encoding="utf-8",
            )
            (artifact_dir / "unit_repair_summary.md").write_text(
                "# Unit Repair Summary\n\n- Unsafe units: `0`\n- Repair attempts: `0`\n",
                encoding="utf-8",
            )
            (artifact_dir / "production_selector_alignment_report.json").write_text(
                json_dumps({"schema_version": "mvp5i_selector_alignment_v1", "uses_validation_selector": True, "selector": "final_output_selector", "divergent_logic": False}) + "\n",
                encoding="utf-8",
            )
            (artifact_dir / "production_selector_alignment_report.md").write_text(
                "# Production Selector Alignment\n\n- Uses validation selector: `True`\n- Divergent logic: `False`\n",
                encoding="utf-8",
            )
        _record_model_usage(artifact_dir, {
            "call_type": "selector",
            "chapter": str(chapter.get("chapter_no") or chapter_id),
            "unit_id": None,
            "provider": policy.get("provider") or provider.key,
            "requested_model": str(policy.get("primary_model") or model),
            "chosen_model": str(policy.get("chosen_model") or model),
            "fallback_model_used": bool(policy.get("fallback_model_used")),
            "route_status": str(policy.get("model_route_status") or "ok"),
            "error_class": None,
        })
        selector = final_output_selector(
            sample=sample,
            before_paragraphs=parsed,
            after_paragraphs=final_paragraphs,
            before_verification=before_verification,
            after_verification=after_verification,
        )
        selected_paragraphs = selector["selected_paragraphs"]
        selected_verification = selector["selected_verification"]
        final_text = render_paragraph_translation(sample, selected_paragraphs)
        quality = {
            **quality,
            "status": "pass" if selected_verification.get("pass") else "fail",
            "verification": selected_verification,
            "before_verification": before_verification,
            "after_verification": after_verification,
            "final_output_selector": {
                key: value
                for key, value in selector.items()
                if key not in {"selected_paragraphs", "selected_verification"}
            },
            "compression": compression_result,
            "repair": repair_report,
            "structured_output_retry_count": max(0, len(raw_attempts) - 1),
            "output_char_count": len(final_text),
            "source_char_count": len(source_text),
            "output_source_ratio": round(len(final_text) / max(len(source_text), 1), 3),
            "evaluate_after_requested": evaluate_after,
        }
        if not selected_verification.get("pass"):
            status = "quality_failed"

    response_path.write_text(
        json_dumps({"raw_response": raw_response, "attempts": raw_attempts}) + "\n",
        encoding="utf-8",
    )
    output_path.write_text(final_text + ("\n" if final_text else ""), encoding="utf-8")
    quality_path.write_text(json_dumps(quality) + "\n", encoding="utf-8")
    completed_at = utc_now()

    with connection(workspace.db_path) as conn:
        if not dry_run:
            model_run_id = _insert_model_run(
                conn,
                task_run_id=task_id,
                provider=provider,
                model=model,
                prompt=full_prompt,
                response=json_dumps({"attempts": raw_attempts}) if raw_attempts else raw_response,
                status="success" if status != "provider_error" else "error",
            )
            if save_translation_row and status in {"success", "quality_failed"}:
                conn.execute(
                    "UPDATE translations SET is_current = 0 WHERE chapter_id = ?",
                    (chapter_id,),
                )
                translation_id = new_id("translation")
                conn.execute(
                    """
                    INSERT INTO translations (
                        id, segment_id, chapter_id, translation_kind, text, status,
                        model_run_id, bundle_checksum, quality_json, is_current, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        translation_id,
                        None,
                        chapter_id,
                        "stable_prompt",
                        final_text,
                        "current" if status == "success" else "needs_review",
                        model_run_id,
                        bundle["checksum"],
                        json_dumps(quality),
                        1,
                        completed_at,
                    ),
                )
        result_data = {
            "run_id": run_id,
            "chapter_id": chapter_id,
            "translation_id": translation_id,
            "model_run_id": model_run_id,
            "output_path": str(output_path),
            "status": status,
        }
        update_task_run(
            conn,
            task_id=task_id,
            status="success" if status in {"success", "dry_run"} else "error",
            stage="completed",
            result_data=result_data,
            error_data={} if status in {"success", "dry_run"} else {"quality": quality},
        )
        conn.commit()

    usage_rows = []
    usage_path = artifact_dir / "per_call_model_usage.jsonl"
    if usage_path.exists():
        usage_rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    (artifact_dir / "compression_model_usage_report.json").write_text(json_dumps({"rows": [row for row in usage_rows if row.get("call_type") == "compression"]}) + "\n", encoding="utf-8")
    (artifact_dir / "repair_model_usage_report.json").write_text(json_dumps({"rows": [row for row in usage_rows if row.get("call_type") in {"repair", "retry", "selector"}]}) + "\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "project_id": project["id"],
        "chapter_id": chapter_id,
        "provider": provider_key,
        "model": model,
        "prompt_id": stable_prompt.prompt_id,
        "prompt_version": stable_prompt.prompt_version,
        "prompt_path": stable_prompt.prompt_path,
        "approval_path": stable_prompt.approval_path,
        "bundle_checksum": bundle["checksum"],
        "output_path": str(output_path),
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "warnings": warnings,
        "task_run_id": task_id,
        "model_run_id": model_run_id,
        "translation_id": translation_id,
        "use_approved_dictionary": use_approved_dictionary,
        "use_hybrid_prompt": use_hybrid_prompt,
        "use_approved_rules": use_approved_rules,
        "hybrid_prompt_block_rendered": bool((hybrid_context or {}).get("block_rendered")),
        "hybrid_selected_item_count": len((hybrid_context or {}).get("selected_items") or []),
        "hybrid_selected_rule_count": len((hybrid_context or {}).get("selected_rule_items") or []),
        "hybrid_conflict_count": int((hybrid_context or {}).get("conflict_count") or 0),
        "dictionary_prompt_block_rendered": bool((dictionary_context or {}).get("block_rendered")),
        "dictionary_selected_hit_count": len((dictionary_context or {}).get("selected_hits") or []),
        "rollout_model_policy": policy,
        "model_policy_snapshot_path": str(artifact_dir / "model_policy_snapshot.json"),
        "per_call_model_usage_path": str(artifact_dir / "per_call_model_usage.jsonl"),
    }
    (artifact_dir / "model_policy_snapshot.json").write_text(json_dumps(policy) + "\n", encoding="utf-8")
    manifest_path.write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    if status == "quality_failed":
        raise ValueError("Production translation failed deterministic quality checks.")
    return {
        "run_id": run_id,
        "artifact_dir": str(artifact_dir),
        "task_run_id": task_id,
        "model_run_id": model_run_id,
        "translation_id": translation_id,
        "chapter_id": chapter_id,
        "project_id": project["id"],
        "prompt_id": stable_prompt.prompt_id,
        "prompt_version": stable_prompt.prompt_version,
        "prompt_path": stable_prompt.prompt_path,
        "approval_path": stable_prompt.approval_path,
        "bundle_checksum": bundle["checksum"],
        "output_path": str(output_path),
        "quality_report": str(quality_path),
        "run_manifest": str(manifest_path),
        "status": status,
        "dry_run": dry_run,
        "warnings": warnings,
        "use_approved_dictionary": use_approved_dictionary,
        "use_hybrid_prompt": use_hybrid_prompt,
        "use_approved_rules": use_approved_rules,
        "hybrid_selected_item_count": len((hybrid_context or {}).get("selected_items") or []),
        "hybrid_selected_rule_count": len((hybrid_context or {}).get("selected_rule_items") or []),
        "hybrid_conflict_count": int((hybrid_context or {}).get("conflict_count") or 0),
        "dictionary_selected_hit_count": len((dictionary_context or {}).get("selected_hits") or []),
        "rollout_model_policy": policy,
        "model_policy_snapshot_path": str(artifact_dir / "model_policy_snapshot.json"),
        "per_call_model_usage_path": str(artifact_dir / "per_call_model_usage.jsonl"),
    }
    (artifact_dir / "model_policy_snapshot.json").write_text(json_dumps(policy) + "\n", encoding="utf-8")


def parse_chapter_range(value: str) -> list[int]:
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
    return sorted(dict.fromkeys(chapters))


def _select_batch_chapters(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str | None,
    chapter_ids: str | None,
    max_chapters: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    project = get_project_by_slug(workspace, project_slug)
    all_chapters = list_chapters(workspace, project_slug=project_slug)
    warnings = []
    selected: list[dict[str, Any]]
    if chapter_ids:
        ids = [item.strip() for item in chapter_ids.split(",") if item.strip()]
        by_id = {chapter["id"]: chapter for chapter in all_chapters}
        missing = [chapter_id for chapter_id in ids if chapter_id not in by_id]
        if missing:
            raise ValueError(f"Chapter id(s) not found: {', '.join(missing)}")
        selected = [by_id[chapter_id] for chapter_id in ids]
    elif chapters:
        numbers = parse_chapter_range(chapters)
        by_no = {int(chapter["chapter_no"]): chapter for chapter in all_chapters if chapter.get("chapter_no") is not None}
        missing_numbers = [number for number in numbers if number not in by_no]
        if missing_numbers:
            raise ValueError(f"Chapter number(s) not found: {missing_numbers}")
        selected = [by_no[number] for number in numbers]
    else:
        selected = all_chapters[:max_chapters]
        if len(all_chapters) > max_chapters:
            warnings.append(f"Default safety limit selected first {max_chapters} chapters only.")
    if len(selected) > max_chapters:
        raise ValueError(
            f"Requested {len(selected)} chapters exceeds --max-chapters {max_chapters}."
        )
    return project, selected, warnings


def split_text_chunks(text: str, *, chunk_size_chars: int, overlap_paragraphs: int = 0) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs:
        return [text]
    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        addition = len(paragraph) + (2 if current else 0)
        if current and current_len + addition > chunk_size_chars:
            chunks.append(current)
            overlap = current[-overlap_paragraphs:] if overlap_paragraphs > 0 else []
            current = list(overlap)
            current_len = sum(len(part) for part in current) + max(0, len(current) - 1) * 2
        current.append(paragraph)
        current_len += addition
    if current:
        chunks.append(current)
    return ["\n\n".join(chunk) for chunk in chunks]


def latest_incomplete_batch(workspace: Workspace, project_slug: str) -> Path | None:
    root = workspace.path / "artifacts" / "batches"
    if not root.exists():
        return None
    candidates = sorted(root.glob(f"{project_slug}_batch_*"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        manifest = candidate / "batch_manifest.json"
        if not manifest.exists():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("status") not in {"success", "dry_run"}:
            return candidate
    return None


def translate_batch_stable(
    workspace: Workspace,
    *,
    project_slug: str,
    provider_key: str,
    model: str,
    use_stable_prompt: bool,
    chapters: str | None = None,
    chapter_ids: str | None = None,
    max_chapters: int = DEFAULT_BATCH_MAX_CHAPTERS,
    max_source_chars_per_chapter: int = DEFAULT_MAX_SOURCE_CHARS_PER_CHAPTER,
    chunk_size_chars: int = DEFAULT_CHUNK_SIZE_CHARS,
    chunk_overlap_paragraphs: int = DEFAULT_CHUNK_OVERLAP_PARAGRAPHS,
    resume: bool = False,
    skip_existing: bool = True,
    force: bool = False,
    enable_paragraph_alignment: bool = True,
    enable_compression_pass: bool = True,
    merge_tiny_paragraphs: bool = True,
    evaluate_after: bool = False,
    dry_run: bool = False,
    output_dir: Path | None = None,
    export_combined: bool = False,
    stop_on_error: bool = False,
    prompt_id: str | None = None,
    use_approved_dictionary: bool = False,
    use_hybrid_prompt: bool = False,
    dictionary_max_entries: int = 8,
    memory_max_items: int = 6,
    use_approved_rules: bool = False,
    rule_max_hints: int = 4,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
    rollout_model_policy: dict[str, Any] | None = None,
    max_unit_repair_attempts: int = 2,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required for MVP5A batch translation.")
    if use_approved_rules:
        use_hybrid_prompt = True
        use_approved_dictionary = True
    if max_chapters <= 0:
        raise ValueError("--max-chapters must be greater than 0.")
    if chunk_size_chars <= 0:
        raise ValueError("--chunk-size-chars must be greater than 0.")
    stable_prompt = load_approved_stable_prompt(workspace, prompt_id=prompt_id) if use_stable_prompt else None
    project, selected_chapters, warnings = _select_batch_chapters(
        workspace,
        project_slug=project_slug,
        chapters=chapters,
        chapter_ids=chapter_ids,
        max_chapters=max_chapters,
    )
    resumed_from = latest_incomplete_batch(workspace, project_slug) if resume else None
    batch_run_id = _new_run_id(project_slug, "batch")
    batch_dir = output_dir or resumed_from or (workspace.path / "artifacts" / "batches" / batch_run_id)
    batch_dir.mkdir(parents=True, exist_ok=True)
    for rel in ("outputs", "prompts", "quality", "chunk_outputs", "chunk_prompts", "chunk_quality"):
        (batch_dir / rel).mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    estimated_api_calls = 0
    actual_api_calls = 0
    chapter_results = []
    processed = []
    skipped = []
    failed = []
    combined_parts = []

    with connection(workspace.db_path) as conn:
        batch_task_id = insert_task_run(
            conn,
            task_type="translate.batch.stable",
            status="running" if not dry_run else "success",
            stage="dry_run" if dry_run else "started",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "chapters": chapters,
                "chapter_ids": chapter_ids,
                "max_chapters": max_chapters,
                "dry_run": dry_run,
            },
            result_data={},
        )
        conn.commit()

    for chapter in selected_chapters:
        chapter_id = chapter["id"]
        chapter_label = str(chapter.get("chapter_no") or chapter_id)
        try:
            _, _segments, source_text = _chapter_source_text(
                workspace,
                chapter_id,
                max_source_chars_per_chapter,
            )
            chunks = split_text_chunks(
                source_text,
                chunk_size_chars=chunk_size_chars,
                overlap_paragraphs=chunk_overlap_paragraphs,
            )
            estimated_api_calls += len(chunks)
            output_path = batch_dir / "outputs" / f"{chapter_label}.vi.txt"
            with connection(workspace.db_path) as conn:
                existing_translation = _existing_current_translation(conn, chapter_id)
            if skip_existing and not force and (output_path.exists() or existing_translation):
                skipped.append(chapter_id)
                chapter_results.append(
                    {
                        "chapter_id": chapter_id,
                        "chapter_no": chapter.get("chapter_no"),
                        "status": "skipped_existing",
                        "output_path": str(output_path),
                        "task_run_id": None,
                        "model_run_id": None,
                        "quality_summary": {},
                        "warnings": ["existing current translation or output skipped"],
                        "error": None,
                        "chunk_count": len(chunks),
                    }
                )
                continue
            if dry_run:
                chapter_results.append(
                    {
                        "chapter_id": chapter_id,
                        "chapter_no": chapter.get("chapter_no"),
                        "status": "dry_run",
                        "output_path": str(output_path),
                        "task_run_id": None,
                        "model_run_id": None,
                        "quality_summary": {
                            "source_char_count": len(source_text),
                            "chunk_count": len(chunks),
                        },
                        "warnings": ["dry_run_no_provider_call"],
                        "error": None,
                        "chunk_count": len(chunks),
                    }
                )
                processed.append(chapter_id)
                continue
            chapter_artifact_id = f"chapter_{chapter_label}"
            chunk_outputs = []
            chunk_task_ids = []
            chunk_model_run_ids = []
            for index, chunk in enumerate(chunks, start=1):
                chunk_result = translate_chapter_stable(
                    workspace,
                    chapter_id=chapter_id,
                    provider_key=provider_key,
                    model=model,
                    use_stable_prompt=use_stable_prompt,
                    prompt_id=prompt_id,
                    max_source_chars=None,
                    enable_paragraph_alignment=enable_paragraph_alignment,
                    enable_compression_pass=enable_compression_pass,
                    merge_tiny_paragraphs=merge_tiny_paragraphs,
                    evaluate_after=evaluate_after,
                    dry_run=False,
                    output_dir=batch_dir / "chunk_outputs" / chapter_artifact_id / f"chunk_{index:03d}",
                    force=True,
                    artifact_run_id=f"{batch_run_id}_ch{chapter_label}_chunk_{index:03d}",
                    source_override=chunk,
                    save_translation_row=False,
                    use_approved_dictionary=use_approved_dictionary,
                    use_hybrid_prompt=use_hybrid_prompt,
                    dictionary_max_entries=dictionary_max_entries,
                    memory_max_items=memory_max_items,
                    use_approved_rules=use_approved_rules,
                    rule_max_hints=rule_max_hints,
                    support_max_chars=support_max_chars,
                    emit_prompt_artifacts=emit_prompt_artifacts,
                    rollout_model_policy=rollout_model_policy,
                    max_unit_repair_attempts=max_unit_repair_attempts,
                )
                actual_api_calls += 1
                chunk_text = Path(chunk_result["output_path"]).read_text(encoding="utf-8")
                chunk_outputs.append(chunk_text.strip())
                chunk_task_ids.append(chunk_result["task_run_id"])
                chunk_model_run_ids.append(chunk_result["model_run_id"])
                (batch_dir / "chunk_prompts" / chapter_artifact_id).mkdir(parents=True, exist_ok=True)
                (batch_dir / "chunk_quality" / chapter_artifact_id).mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    Path(chunk_result["artifact_dir"]) / "prompt_used.md",
                    batch_dir / "chunk_prompts" / chapter_artifact_id / f"chunk_{index:03d}_prompt_used.md",
                )
                shutil.copy2(
                    Path(chunk_result["quality_report"]),
                    batch_dir / "chunk_quality" / chapter_artifact_id / f"chunk_{index:03d}_quality_report.json",
                )
            final_text = "\n\n".join(part for part in chunk_outputs if part)
            output_path.write_text(final_text + "\n", encoding="utf-8")
            prompt_copy = batch_dir / "prompts" / f"{chapter_label}_prompt_used.md"
            quality_copy = batch_dir / "quality" / f"{chapter_label}_quality_report.json"
            first_chunk_dir = batch_dir / "chunk_outputs" / chapter_artifact_id / "chunk_001"
            if (first_chunk_dir / "prompt_used.md").exists():
                shutil.copy2(first_chunk_dir / "prompt_used.md", prompt_copy)
            quality_summary = {
                "chunk_count": len(chunks),
                "source_char_count": len(source_text),
                "output_char_count": len(final_text),
                "model_run_ids": chunk_model_run_ids,
            }
            quality_copy.write_text(json_dumps(quality_summary) + "\n", encoding="utf-8")
            with connection(workspace.db_path) as conn:
                conn.execute("UPDATE translations SET is_current = 0 WHERE chapter_id = ?", (chapter_id,))
                translation_id = new_id("translation")
                now = utc_now()
                conn.execute(
                    """
                    INSERT INTO translations (
                        id, segment_id, chapter_id, translation_kind, text, status,
                        model_run_id, bundle_checksum, quality_json, is_current, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        translation_id,
                        None,
                        chapter_id,
                        "stable_prompt_batch",
                        final_text,
                        "current",
                        chunk_model_run_ids[-1] if chunk_model_run_ids else None,
                        None,
                        json_dumps(quality_summary),
                        1,
                        now,
                    ),
                )
                conn.commit()
            processed.append(chapter_id)
            combined_parts.append(final_text)
            chapter_results.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_no": chapter.get("chapter_no"),
                    "status": "success",
                    "output_path": str(output_path),
                    "task_run_id": chunk_task_ids[-1] if chunk_task_ids else None,
                    "model_run_id": chunk_model_run_ids[-1] if chunk_model_run_ids else None,
                    "quality_summary": quality_summary,
                    "warnings": [],
                    "error": None,
                    "chunk_count": len(chunks),
                }
            )
        except Exception as exc:  # keep batch resumable; caller controls stop_on_error
            failed.append(chapter_id)
            provider_failure = classify_provider_error(exc)
            if provider_failure.get("http_status") == 404:
                provider_failure["provider_error_type"] = "model_route_not_found"
                provider_failure["retryable"] = False
            report_path = batch_dir / "provider_failure_reports" / f"{chapter_label}_provider_failure_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_payload = {
                "schema_version": "mvp5i_provider_failure_report_v1",
                "created_at": utc_now(),
                "chapter_id": chapter_id,
                "chapter_no": chapter.get("chapter_no"),
                "provider": provider_key,
                "model": model,
                "rollout_model_policy": rollout_model_policy,
                "no_translation_output_produced": True,
                **provider_failure,
            }
            report_path.write_text(json_dumps(report_payload) + "\n", encoding="utf-8")
            chapter_results.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_no": chapter.get("chapter_no"),
                    "status": "failed",
                    "output_path": None,
                    "task_run_id": None,
                    "model_run_id": None,
                    "quality_summary": {},
                    "warnings": [],
                    "error": str(exc),
                    "provider_failure_report": str(report_path),
                    "provider_failure_type": provider_failure.get("provider_error_type"),
                    "chunk_count": 0,
                }
            )
            if stop_on_error:
                break

    if export_combined and combined_parts:
        (batch_dir / "full_novel.vi.txt").write_text("\n\n".join(combined_parts) + "\n", encoding="utf-8")
    completed_at = utc_now()
    status = "dry_run" if dry_run else "partial_failure" if failed else "success"
    manifest = {
        "batch_run_id": batch_run_id,
        "project_id": project["id"],
        "chapters_requested": [chapter["id"] for chapter in selected_chapters],
        "chapters_processed": processed,
        "chapters_skipped": skipped,
        "chapters_failed": failed,
        "provider": provider_key,
        "model": model,
        "prompt_id": stable_prompt.prompt_id if stable_prompt else None,
        "prompt_version": stable_prompt.prompt_version if stable_prompt else None,
        "approval_path": stable_prompt.approval_path if stable_prompt else None,
        "use_approved_dictionary": use_approved_dictionary,
        "use_hybrid_prompt": use_hybrid_prompt,
        "dictionary_max_entries": dictionary_max_entries,
        "memory_max_items": memory_max_items,
        "use_approved_rules": use_approved_rules,
        "rule_max_hints": rule_max_hints,
        "support_max_chars": support_max_chars,
        "rollout_model_policy": rollout_model_policy,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "warnings": warnings + ([] if dry_run else ["Real provider calls may incur API cost."]),
        "estimated_api_calls": estimated_api_calls,
        "actual_api_calls": 0 if dry_run else actual_api_calls,
        "resumed_from": str(resumed_from) if resumed_from else None,
    }
    (batch_dir / "batch_manifest.json").write_text(json_dumps(manifest) + "\n", encoding="utf-8")
    (batch_dir / "chapter_results.json").write_text(json_dumps({"chapters": chapter_results}) + "\n", encoding="utf-8")
    report_lines = [
        "# Batch Translation Report",
        "",
        f"- Status: `{status}`",
        f"- Project: `{project_slug}`",
        f"- Provider/model: `{provider_key}` / `{model}`",
        f"- Chapters processed: `{len(processed)}`",
        f"- Chapters skipped: `{len(skipped)}`",
        f"- Chapters failed: `{len(failed)}`",
        f"- Estimated API calls: `{estimated_api_calls}`",
        "",
    ]
    for result in chapter_results:
        report_lines.append(
            f"- Chapter {result.get('chapter_no') or result['chapter_id']}: {result['status']}"
        )
    (batch_dir / "batch_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    with connection(workspace.db_path) as conn:
        update_task_run(
            conn,
            task_id=batch_task_id,
            status="success" if status in {"success", "dry_run"} else "error",
            stage="completed",
            result_data=manifest,
            error_data={"failed": failed} if failed else None,
        )
        conn.commit()
    return {
        "batch_run_id": batch_run_id,
        "batch_dir": str(batch_dir),
        "batch_manifest": str(batch_dir / "batch_manifest.json"),
        "chapter_results": str(batch_dir / "chapter_results.json"),
        "batch_report": str(batch_dir / "batch_report.md"),
        "task_run_id": batch_task_id,
        "status": status,
        "dry_run": dry_run,
        "chapters": chapter_results,
        "warnings": manifest["warnings"],
        "estimated_api_calls": estimated_api_calls,
        "actual_api_calls": manifest["actual_api_calls"],
    }
