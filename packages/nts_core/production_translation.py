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
from nts_core.stable_prompts import StablePromptBlocker, StablePromptRecord, load_approved_stable_prompt
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


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_run_id(project_slug: str, prefix: str) -> str:
    return f"{project_slug}_{prefix}_{int(time.time() * 1000)}"


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
        if (
            truncation_reasons == ["missing_terminal_punctuation"]
            and CHAPTER_HEADING_RE.match(str(pair.get("source_text", "")))
        ):
            warnings.append("heading_without_terminal_punctuation_allowed")
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
            and all(row.get("output_reference_ratio", 99) <= 1.8 for row in overlong_rows)
            and not truncated
            and not adjusted.get("terminology_mismatches")
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
        from nts_core.eval_harness import apply_translation_units

        return apply_translation_units([sample], merge_tiny_paragraphs=True)[0]
    return sample


def build_production_prompt(
    *,
    stable_prompt: StablePromptRecord,
    sample: dict[str, Any],
    memory_bundle: dict[str, Any],
    glossary: dict[str, Any],
    dictionary_block: str | None = None,
    support_block: str | None = None,
) -> tuple[str, str]:
    system_sections = [stable_prompt.prompt_text, ""]
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
        if text and not re.search(r"[.!?。！？…】）)\"]$", text):
            text = f"{text}."
        paragraphs.append({"paragraph_id": pair["paragraph_id"], "text": f"[MOCK {model}] {text}"})
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
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
) -> dict[str, Any]:
    if not use_stable_prompt:
        from nts_core.translation import translate_chapter_mock

        return translate_chapter_mock(workspace, chapter_id=chapter_id, provider_key=provider_key)
    stable_prompt = load_approved_stable_prompt(workspace, prompt_id=prompt_id)
    provider = load_production_provider(workspace, provider_key)
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
    hybrid_context = (
        build_hybrid_prompt_support(
            workspace,
            project["slug"],
            source_text,
            mode="production",
            max_dictionary_entries=dictionary_max_entries,
            max_memory_items=memory_max_items,
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
                "prompt_id": stable_prompt.prompt_id,
                "dry_run": dry_run,
                "use_approved_dictionary": use_approved_dictionary,
                "use_hybrid_prompt": use_hybrid_prompt,
                "dictionary_max_entries": dictionary_max_entries,
                "memory_max_items": memory_max_items,
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
        else:
            raw_response = chat_completion_with_provider_retry(
                provider,
                model=model,
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
            raw_response = chat_completion_with_provider_retry(
                provider,
                model=model,
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
        "hybrid_prompt_block_rendered": bool((hybrid_context or {}).get("block_rendered")),
        "hybrid_selected_item_count": len((hybrid_context or {}).get("selected_items") or []),
        "hybrid_conflict_count": int((hybrid_context or {}).get("conflict_count") or 0),
        "dictionary_prompt_block_rendered": bool((dictionary_context or {}).get("block_rendered")),
        "dictionary_selected_hit_count": len((dictionary_context or {}).get("selected_hits") or []),
    }
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
        "hybrid_selected_item_count": len((hybrid_context or {}).get("selected_items") or []),
        "hybrid_conflict_count": int((hybrid_context or {}).get("conflict_count") or 0),
        "dictionary_selected_hit_count": len((dictionary_context or {}).get("selected_hits") or []),
    }


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
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
) -> dict[str, Any]:
    if not use_stable_prompt:
        raise ValueError("--use-stable-prompt is required for MVP5A batch translation.")
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
                    output_dir=batch_dir / "chunk_outputs" / chapter_id / f"chunk_{index:03d}",
                    force=True,
                    artifact_run_id=f"{batch_run_id}_{chapter_id}_chunk_{index:03d}",
                    source_override=chunk,
                    save_translation_row=False,
                    use_approved_dictionary=use_approved_dictionary,
                    use_hybrid_prompt=use_hybrid_prompt,
                    dictionary_max_entries=dictionary_max_entries,
                    memory_max_items=memory_max_items,
                    support_max_chars=support_max_chars,
                    emit_prompt_artifacts=emit_prompt_artifacts,
                )
                actual_api_calls += 1
                chunk_text = Path(chunk_result["output_path"]).read_text(encoding="utf-8")
                chunk_outputs.append(chunk_text.strip())
                chunk_task_ids.append(chunk_result["task_run_id"])
                chunk_model_run_ids.append(chunk_result["model_run_id"])
                (batch_dir / "chunk_prompts" / chapter_id).mkdir(parents=True, exist_ok=True)
                (batch_dir / "chunk_quality" / chapter_id).mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    Path(chunk_result["artifact_dir"]) / "prompt_used.md",
                    batch_dir / "chunk_prompts" / chapter_id / f"chunk_{index:03d}_prompt_used.md",
                )
                shutil.copy2(
                    Path(chunk_result["quality_report"]),
                    batch_dir / "chunk_quality" / chapter_id / f"chunk_{index:03d}_quality_report.json",
                )
            final_text = "\n\n".join(part for part in chunk_outputs if part)
            output_path.write_text(final_text + "\n", encoding="utf-8")
            prompt_copy = batch_dir / "prompts" / f"{chapter_label}_prompt_used.md"
            quality_copy = batch_dir / "quality" / f"{chapter_label}_quality_report.json"
            first_chunk_dir = batch_dir / "chunk_outputs" / chapter_id / "chunk_001"
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
        "support_max_chars": support_max_chars,
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
