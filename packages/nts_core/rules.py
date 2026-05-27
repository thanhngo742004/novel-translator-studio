from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from nts_core.chinese_nlp import parse_chapter_range
from nts_core.dictionary import load_project_dictionary
from nts_core.projects import get_project_by_slug
from nts_storage.database import (
    connection,
    initialize_database,
    insert_task_run,
    json_dumps,
    new_id,
    row_to_dict,
    utc_now,
)
from nts_storage.workspace import Workspace


RULE_TYPES = {
    "format_preservation",
    "forbidden_variant",
    "dictionary_priority_guard",
    "entity_atomicity_guard",
    "style_rhythm_preservation",
    "context_lexical_preference",
    "expansion_guard",
}
RULE_JSON_FIELDS = (
    "trigger_pattern_json",
    "applies_when_json",
    "examples_json",
    "forbidden_variants_json",
    "scope_json",
    "confidence_json",
    "provenance_json",
)
APPROVED_RULE_JSON_FIELDS = (
    "trigger_pattern_json",
    "applies_when_json",
    "examples_json",
    "forbidden_variants_json",
    "scope_json",
    "provenance_json",
)
RULE_TYPE_PRIORITY = {
    "dictionary_priority_guard": 10,
    "forbidden_variant": 20,
    "expansion_guard": 30,
    "entity_atomicity_guard": 40,
    "format_preservation": 50,
    "context_lexical_preference": 60,
    "style_rhythm_preservation": 70,
}
BRACKET_RE = re.compile(r"【[^】]+】")


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _jsonl_read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").strip()).casefold()


def _rules_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "rules"


def _resolve_rule_run(workspace: Workspace, run: str) -> Path:
    candidate = Path(run)
    if candidate.exists():
        return candidate
    root_candidate = _rules_root(workspace) / run
    if root_candidate.exists():
        return root_candidate
    raise ValueError(f"Rule run not found: {run}")


def _candidate_path(run_dir: Path) -> Path:
    return run_dir / "rule_candidates.jsonl"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_scope(project: dict[str, Any], chapters: list[int] | None = None) -> dict[str, Any]:
    scope = {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }
    if chapters:
        scope["chapters"] = chapters
    return scope


def _row_to_rule_candidate(row: Any) -> dict[str, Any]:
    return row_to_dict(row, json_fields=RULE_JSON_FIELDS)


def _row_to_approved_rule(row: Any) -> dict[str, Any]:
    return row_to_dict(row, json_fields=APPROVED_RULE_JSON_FIELDS)


def _read_memory_rows(workspace: Workspace, project: dict[str, Any], statuses: set[str] | None = None) -> list[dict[str, Any]]:
    query_status = ""
    params: list[Any] = []
    if statuses:
        query_status = "WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
        params.extend(sorted(statuses))
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            {query_status}
            ORDER BY created_at ASC, id ASC
            """,
            params,
        ).fetchall()
    scoped = []
    for row in rows:
        item = row_to_dict(row, json_fields=("scope_json", "value_json", "rules_json", "confidence_json"))
        scope = item.get("scope_json") or {}
        if scope.get("project_id") not in (None, project["id"]):
            continue
        if scope.get("project_slug") not in (None, project["slug"]):
            continue
        scoped.append(item)
    return scoped


def _memory_by_id(workspace: Workspace, memory_id: str) -> dict[str, Any] | None:
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items WHERE id = ?
            """,
            (memory_id,),
        ).fetchone()
    return row_to_dict(row, json_fields=("scope_json", "value_json", "rules_json", "confidence_json")) if row else None


def _dictionary_entry_by_id(workspace: Workspace, entry_id: str) -> dict[str, Any] | None:
    with connection(workspace.db_path) as conn:
        row = conn.execute(
            """
            SELECT id, project_id, project_slug, entry_type, source_text, target_text,
                   normalized_source, normalized_target, forbidden_variants_json, scope_json,
                   confidence_score, provenance_json, status, approved_by, approved_at,
                   created_at, updated_at
            FROM project_dictionary_entries WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
    return row_to_dict(row, json_fields=("forbidden_variants_json", "scope_json", "provenance_json")) if row else None


def _rule_raw(
    *,
    rule_type: str,
    trigger: dict[str, Any],
    applies_when: dict[str, Any],
    instruction: str,
    scope: dict[str, Any],
    evidence: dict[str, Any],
    examples: list[dict[str, Any]] | None = None,
    forbidden_variants: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    priority: int | None = None,
    confidence_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_type": rule_type,
        "trigger_pattern": trigger,
        "applies_when": applies_when,
        "instruction": instruction.strip(),
        "examples": examples or [],
        "forbidden_variants": sorted({str(item) for item in (forbidden_variants or []) if item}),
        "scope": scope,
        "evidence": [evidence],
        "provenance": provenance or {},
        "priority": priority if priority is not None else RULE_TYPE_PRIORITY.get(rule_type, 99),
        "confidence_hints": confidence_hints or {},
    }


def _flatten_hybrid_conflicts(validation_run: Path | None) -> list[dict[str, Any]]:
    if validation_run is None:
        return []
    path = validation_run / "prompt_conflict_report.json"
    if not path.exists():
        return []
    payload = _load_json(path)
    rows: list[dict[str, Any]] = []
    for phase, samples in (payload.get("phases") or {}).items():
        if not isinstance(samples, dict):
            continue
        for sample_id, report in samples.items():
            if not isinstance(report, dict):
                continue
            for conflict in report.get("conflicts", []) or []:
                rows.append(
                    {
                        "phase": phase,
                        "sample_id": sample_id,
                        "chapter_id": report.get("chapter_id"),
                        "artifact_ref": {"path": str(path), "phase": phase, "sample_id": sample_id},
                        **conflict,
                    }
                )
    return rows


def _support_lookup(validation_run: Path | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if validation_run is None:
        return lookup
    path = validation_run / "prompt_support_items.json"
    if not path.exists():
        return lookup
    payload = _load_json(path)
    for samples in (payload.get("phases") or {}).values():
        if not isinstance(samples, dict):
            continue
        for row in samples.values():
            if not isinstance(row, dict):
                continue
            for key in ("candidate_items", "selected_items", "deduped_items", "dropped_items"):
                for item in row.get(key, []) or []:
                    if item.get("item_id"):
                        lookup[str(item["item_id"])] = item
    return lookup


def _extract_from_hybrid_conflicts(
    workspace: Workspace,
    project: dict[str, Any],
    *,
    validation_run: Path | None,
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    support = _support_lookup(validation_run)
    for conflict in _flatten_hybrid_conflicts(validation_run):
        ctype = str(conflict.get("conflict_type") or "")
        source = str(conflict.get("source_anchor") or conflict.get("source_key") or "")
        evidence = {
            "chapter_id": str(conflict.get("chapter_id") or "") or None,
            "chapter_no": int(conflict["chapter_id"]) if str(conflict.get("chapter_id") or "").isdigit() else None,
            "segment_id": None,
            "source_excerpt": source,
            "target_excerpt": str(conflict.get("target_value") or ""),
            "model_output_excerpt": "",
            "evidence_kind": f"hybrid_conflict:{ctype}",
            "artifact_ref": conflict.get("artifact_ref") or {},
        }
        if ctype == "dictionary_memory_duplicate" and source:
            target = str(conflict.get("target_value") or "")
            raw.append(
                _rule_raw(
                    rule_type="dictionary_priority_guard",
                    trigger={"kind": "dictionary_hit", "text": source},
                    applies_when={"exact_source_required": True, "dictionary_exact_hit": True},
                    instruction=f"When exact source {source} appears, use the approved dictionary target {target}.",
                    examples=[{"source": source, "preferred": target}],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "hybrid_prompt_conflict", "conflict_type": ctype},
                    confidence_hints={"dictionary_support": True, "conflict_frequency": 1},
                )
            )
        elif ctype == "overlapping_dictionary_hit" and source:
            kept_items = [support.get(str(item_id)) for item_id in conflict.get("kept_item_ids", []) or []]
            kept_sources = [str(item.get("source_anchor")) for item in kept_items if item and item.get("source_anchor")]
            kept_targets = [str(item.get("target_value")) for item in kept_items if item and item.get("target_value")]
            longer_text = kept_sources[0] if kept_sources else "the longer approved dictionary hit"
            forbidden = kept_targets[:1]
            raw.append(
                _rule_raw(
                    rule_type="expansion_guard",
                    trigger={"kind": "exact_ngram", "text": source},
                    applies_when={"exact_source_required": True, "longer_hit_must_be_exact": True},
                    instruction=f"Do not expand {source} into {longer_text} unless the exact longer Chinese source appears.",
                    examples=[{"source": source, "preferred": source, "rejected": forbidden[0] if forbidden else ""}],
                    forbidden_variants=forbidden,
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "hybrid_prompt_conflict", "conflict_type": ctype, "kept_item_ids": conflict.get("kept_item_ids", [])},
                    confidence_hints={"dictionary_support": True, "negative_evidence_strength": 0.7},
                )
            )
        elif ctype == "related_inactive_or_negative_memory" and source:
            memory = _memory_by_id(workspace, str(conflict.get("related_memory_id") or ""))
            target = str((memory or {}).get("target_text") or conflict.get("target_value") or "")
            raw.append(
                _rule_raw(
                    rule_type="forbidden_variant",
                    trigger={"kind": "exact_text", "text": source},
                    applies_when={"exact_source_required": True, "negative_memory_status": conflict.get("related_status")},
                    instruction=f"Do not use deprecated, pending, or harmful memory variants for {source} unless they are re-approved with scope.",
                    examples=[{"source": source, "rejected": target}] if target else [{"source": source}],
                    forbidden_variants=[target] if target else [],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "hybrid_prompt_conflict", "conflict_type": ctype, "related_memory_id": conflict.get("related_memory_id")},
                    confidence_hints={"memory_support": True, "negative_evidence_strength": 0.8 if conflict.get("related_status") in {"deprecated", "rejected"} else 0.5},
                )
            )
    return raw


def _extract_from_dictionary(workspace: Workspace, project: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for entry in load_project_dictionary(workspace, project["slug"]):
        source = str(entry.get("source_text") or "")
        target = str(entry.get("target_text") or "")
        if not source or not target:
            continue
        evidence = {
            "chapter_id": None,
            "chapter_no": None,
            "segment_id": None,
            "source_excerpt": source,
            "target_excerpt": target,
            "model_output_excerpt": "",
            "evidence_kind": "approved_dictionary_entry",
            "artifact_ref": {"entry_id": entry.get("id")},
        }
        raw.append(
            _rule_raw(
                rule_type="dictionary_priority_guard",
                trigger={"kind": "dictionary_hit", "text": source},
                applies_when={"exact_source_required": True, "dictionary_exact_hit": True},
                instruction=f"When exact source {source} appears, prefer approved dictionary target {target}.",
                examples=[{"source": source, "preferred": target}],
                scope=scope,
                evidence=evidence,
                provenance={"source": "approved_dictionary", "entry_id": entry.get("id")},
                confidence_hints={"dictionary_support": True},
            )
        )
        if entry.get("entry_type") in {"name", "sect_org"}:
            raw.append(
                _rule_raw(
                    rule_type="entity_atomicity_guard",
                    trigger={"kind": "dictionary_hit", "text": source},
                    applies_when={"exact_source_required": True, "entity_atomic": True},
                    instruction=f"Treat approved entity {source} as atomic and render it consistently as {target}.",
                    examples=[{"source": source, "preferred": target}],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "approved_dictionary", "entry_id": entry.get("id")},
                    confidence_hints={"dictionary_support": True, "specific_entity": True},
                )
            )
        for forbidden in entry.get("forbidden_variants_json") or []:
            raw.append(
                _rule_raw(
                    rule_type="forbidden_variant",
                    trigger={"kind": "dictionary_hit", "text": source},
                    applies_when={"exact_source_required": True, "dictionary_exact_hit": True},
                    instruction=f"When exact source {source} appears, do not use rejected variant {forbidden}.",
                    examples=[{"source": source, "preferred": target, "rejected": forbidden}],
                    forbidden_variants=[str(forbidden)],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "approved_dictionary_forbidden_variant", "entry_id": entry.get("id")},
                    confidence_hints={"dictionary_support": True, "negative_evidence_strength": 0.7},
                )
            )
    return raw


def _extract_from_memory(workspace: Workspace, project: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for item in _read_memory_rows(workspace, project, statuses={"active", "deprecated", "rejected", "pending"}):
        value = item.get("value_json") or {}
        rules = item.get("rules_json") or {}
        source = str(item.get("source_key") or value.get("source_pattern") or "")
        target = str(item.get("target_text") or value.get("preferred_target") or rules.get("preferred_target") or "")
        if not source:
            continue
        evidence = {
            "chapter_id": None,
            "chapter_no": None,
            "segment_id": None,
            "source_excerpt": source,
            "target_excerpt": target,
            "model_output_excerpt": "",
            "evidence_kind": f"memory_item:{item.get('status')}",
            "artifact_ref": {"memory_id": item.get("id")},
        }
        markers = {
            str(value.get("status") or ""),
            str(value.get("review_status") or ""),
            str(value.get("validation_status") or ""),
            str(value.get("impact_classification") or ""),
            str((item.get("confidence_json") or {}).get("impact_classification") or ""),
        }
        is_negative = item.get("status") in {"deprecated", "rejected"} or bool(
            markers & {"rejected_after_validation", "deprecated_for_validation", "harmful", "harmful_only_in_combination", "insufficient_evidence"}
        ) or value.get("deprecated_for_validation") is True
        if is_negative:
            raw.append(
                _rule_raw(
                    rule_type="forbidden_variant",
                    trigger={"kind": "exact_text", "text": source},
                    applies_when={"exact_source_required": True, "negative_memory": True},
                    instruction=f"Do not use memory-derived variant {target or source} for {source} unless it is re-approved with explicit scope.",
                    examples=[{"source": source, "rejected": target}],
                    forbidden_variants=[target] if target else [],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "memory_negative_evidence", "memory_id": item.get("id")},
                    confidence_hints={"memory_support": True, "negative_evidence_strength": 0.9},
                )
            )
        context_required = str(value.get("context_required") or rules.get("context_required") or "")
        if context_required:
            raw.append(
                _rule_raw(
                    rule_type="context_lexical_preference",
                    trigger={"kind": "exact_text", "text": source},
                    applies_when={"exact_source_required": True, "context_required": context_required},
                    instruction=f"Apply {source} => {target} only in {context_required} context.",
                    examples=[{"source": source, "preferred": target}],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "memory_context_scope", "memory_id": item.get("id")},
                    confidence_hints={"memory_support": True, "context_specific": True},
                )
            )
        if source == "技能" and target:
            raw.append(
                _rule_raw(
                    rule_type="context_lexical_preference",
                    trigger={"kind": "exact_text", "text": source},
                    applies_when={"exact_source_required": True, "context_required": "system_panel_or_game_ui"},
                    instruction="Use the approved 技能 lexical mapping only in system panel/game UI context, not broad narration.",
                    examples=[{"source": source, "preferred": target}],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "memory_context_heuristic", "memory_id": item.get("id")},
                    confidence_hints={"memory_support": True, "context_specific": True},
                )
            )
    return raw


def _validation_source_texts(validation_run: Path | None) -> list[dict[str, Any]]:
    if validation_run is None:
        return []
    rows: list[dict[str, Any]] = []
    for name in ("selected_validation_units.json", "selected_samples.json"):
        path = validation_run / name
        if not path.exists():
            continue
        payload = _load_json(path)
        for sample in payload.get("samples", []) or []:
            if isinstance(sample, dict):
                rows.append(sample)
        if rows:
            break
    return rows


def _extract_from_validation(validation_run: Path | None, scope: dict[str, Any]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for sample in _validation_source_texts(validation_run):
        source = str(sample.get("source_text") or "")
        if BRACKET_RE.search(source):
            evidence = {
                "chapter_id": str(sample.get("chapter_id") or "") or None,
                "chapter_no": int(sample["chapter_id"]) if str(sample.get("chapter_id") or "").isdigit() else None,
                "segment_id": None,
                "source_excerpt": BRACKET_RE.search(source).group(0),
                "target_excerpt": "",
                "model_output_excerpt": "",
                "evidence_kind": "validation_system_panel_span",
                "artifact_ref": {"sample_id": sample.get("sample_id"), "validation_run": str(validation_run) if validation_run else None},
            }
            raw.append(
                _rule_raw(
                    rule_type="format_preservation",
                    trigger={"kind": "segment_type", "text": "system_panel"},
                    applies_when={"source_has_brackets": True},
                    instruction="Preserve system panel bracket format 【...】 and translate only field labels/values.",
                    examples=[{"source": evidence["source_excerpt"]}],
                    scope=scope,
                    evidence=evidence,
                    provenance={"source": "validation_sample_system_panel"},
                    confidence_hints={"validation_support": True, "format_specific": True},
                )
            )
    return raw


def _extract_from_nlp_cache(workspace: Workspace, project_slug: str, chapters: list[int], scope: dict[str, Any]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    root = workspace.path / "artifacts" / "nlp" / project_slug
    manifest_path = root / "nlp_cache_manifest.json"
    if not manifest_path.exists():
        return raw
    manifest = _load_json(manifest_path)
    chapter_set = {int(chapter) for chapter in chapters}
    for entry in manifest.get("chapters", []) or []:
        try:
            chapter_no = int(entry.get("chapter_no"))
        except (TypeError, ValueError):
            continue
        if chapter_no not in chapter_set:
            continue
        artifact = Path(entry.get("artifact_path") or "")
        if not artifact.exists():
            continue
        analysis = _load_json(artifact)
        for phrase in (analysis.get("chapter_candidates") or {}).get("phrase_candidates", []) or []:
            text = str(phrase.get("text") or "")
            if BRACKET_RE.match(text):
                evidence = {
                    "chapter_id": analysis.get("meta", {}).get("chapter_id"),
                    "chapter_no": chapter_no,
                    "segment_id": None,
                    "source_excerpt": text,
                    "target_excerpt": "",
                    "model_output_excerpt": "",
                    "evidence_kind": "nlp_system_panel_phrase",
                    "artifact_ref": {"path": str(artifact)},
                }
                raw.append(
                    _rule_raw(
                        rule_type="format_preservation",
                        trigger={"kind": "segment_type", "text": "system_panel"},
                        applies_when={"source_has_brackets": True},
                        instruction="Preserve system panel bracket format 【...】 and translate only field labels/values.",
                        examples=[{"source": text}],
                        scope=scope,
                        evidence=evidence,
                        provenance={"source": "ltp_cache_read_only"},
                        confidence_hints={"nlp_support": True, "format_specific": True},
                    )
                )
    return raw


def _evidence_key(evidence: dict[str, Any]) -> tuple[Any, ...]:
    return (
        evidence.get("evidence_kind"),
        evidence.get("chapter_id"),
        evidence.get("chapter_no"),
        evidence.get("source_excerpt"),
        json.dumps(evidence.get("artifact_ref") or {}, sort_keys=True),
    )


def _confidence_for(candidate: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    evidence = candidate["evidence"]
    evidence_count = len(evidence)
    chapter_spread = len({item.get("chapter_no") or item.get("chapter_id") for item in evidence if item.get("chapter_no") or item.get("chapter_id")})
    trigger = candidate["trigger_pattern"]
    trigger_text = str(trigger.get("text") or "")
    hints = candidate.get("confidence_hints") or {}
    score = 0.34
    score += min(0.24, evidence_count * 0.04)
    score += min(0.12, chapter_spread * 0.04)
    if trigger.get("kind") in {"dictionary_hit", "exact_text", "exact_ngram"} and trigger_text:
        score += 0.12
    if len(trigger_text) >= 3:
        score += 0.06
    if hints.get("dictionary_support"):
        score += 0.18
    if hints.get("memory_support"):
        score += 0.10
    if hints.get("validation_support"):
        score += 0.08
    if hints.get("negative_evidence_strength"):
        score += min(0.14, float(hints.get("negative_evidence_strength") or 0) * 0.14)
    if hints.get("nlp_support") and not (hints.get("dictionary_support") or hints.get("memory_support") or hints.get("validation_support")):
        score = min(score, 0.62)
    if candidate["rule_type"] in {"style_rhythm_preservation"} and not trigger_text:
        score -= 0.15
    if not trigger_text and trigger.get("kind") != "segment_type":
        score -= 0.25
    score = max(0.0, min(0.99, round(score, 3)))
    if score >= 0.80:
        group = "high_confidence"
    elif score >= 0.50:
        group = "needs_review"
    else:
        group = "likely_reject"
    return score, {
        "group": group,
        "evidence_count": evidence_count,
        "chapter_spread": chapter_spread,
        "hints": hints,
        "scoring_version": "mvp5g-v1",
    }


def _merge_rule_candidates(
    *,
    rule_run_id: str,
    project: dict[str, Any],
    raw_rows: list[dict[str, Any]],
    max_candidates: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for raw in raw_rows:
        if raw["rule_type"] not in RULE_TYPES:
            continue
        trigger_norm = _normalize_text(json.dumps(raw["trigger_pattern"], ensure_ascii=False, sort_keys=True))
        instruction_norm = _normalize_text(raw["instruction"])
        scope_norm = _normalize_text(json.dumps(raw["scope"], ensure_ascii=False, sort_keys=True))
        key = (raw["rule_type"], trigger_norm, instruction_norm, scope_norm)
        candidate = by_key.get(key)
        if candidate is None:
            candidate = {
                "id": _stable_id("rulecand", rule_run_id, *key),
                "rule_run_id": rule_run_id,
                "project_id": project["id"],
                "project_slug": project["slug"],
                "rule_type": raw["rule_type"],
                "trigger_pattern": raw["trigger_pattern"],
                "applies_when": raw["applies_when"],
                "instruction": raw["instruction"],
                "examples": list(raw.get("examples") or []),
                "forbidden_variants": list(raw.get("forbidden_variants") or []),
                "scope": raw["scope"],
                "evidence": [],
                "provenance_sources": Counter(),
                "provenance": raw.get("provenance") or {},
                "priority": raw.get("priority") or RULE_TYPE_PRIORITY.get(raw["rule_type"], 99),
                "confidence_hints": dict(raw.get("confidence_hints") or {}),
                "conflict_group": None,
            }
            by_key[key] = candidate
        else:
            duplicates.append({"merged_into": candidate["id"], "rule_type": raw["rule_type"], "instruction": raw["instruction"]})
            candidate["examples"].extend(raw.get("examples") or [])
            candidate["forbidden_variants"].extend(raw.get("forbidden_variants") or [])
            candidate["confidence_hints"].update(raw.get("confidence_hints") or {})
        candidate["provenance_sources"][str((raw.get("provenance") or {}).get("source") or "unknown")] += 1
        existing_evidence = {_evidence_key(item) for item in candidate["evidence"]}
        for evidence in raw.get("evidence") or []:
            if _evidence_key(evidence) not in existing_evidence:
                candidate["evidence"].append(evidence)
                existing_evidence.add(_evidence_key(evidence))

    candidates = list(by_key.values())
    conflicts = _detect_rule_conflicts(candidates)
    conflict_candidate_ids = {
        candidate_id
        for conflict in conflicts
        for candidate_id in conflict["candidate_ids"]
    }
    now = utc_now()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        score, confidence = _confidence_for(candidate)
        candidate_id = candidate["id"]
        status = "needs_human_review" if candidate_id in conflict_candidate_ids else "pending_review"
        provenance = {
            **(candidate.get("provenance") or {}),
            "builder": "mvp5g-deterministic",
            "source_counts": dict(candidate["provenance_sources"]),
        }
        rows.append(
            {
                "id": candidate_id,
                "rule_run_id": rule_run_id,
                "project_id": project["id"],
                "project_slug": project["slug"],
                "rule_type": candidate["rule_type"],
                "trigger_pattern_json": candidate["trigger_pattern"],
                "applies_when_json": candidate["applies_when"],
                "instruction": candidate["instruction"],
                "examples_json": _dedup_examples(candidate["examples"])[:8],
                "forbidden_variants_json": sorted(set(str(item) for item in candidate["forbidden_variants"] if item)),
                "scope_json": candidate["scope"],
                "confidence_score": score,
                "confidence_json": confidence,
                "evidence_count": len(candidate["evidence"]),
                "provenance_json": provenance,
                "status": status,
                "priority": int(candidate["priority"]),
                "conflict_group": next((conflict["conflict_group"] for conflict in conflicts if candidate_id in conflict["candidate_ids"]), None),
                "review_status": "unreviewed",
                "created_at": now,
                "updated_at": now,
                "reviewed_at": None,
                "evidence": candidate["evidence"],
            }
        )
    rows.sort(key=lambda row: (row["priority"], -float(row["confidence_score"]), row["rule_type"], row["instruction"]))
    if max_candidates:
        rows = rows[:max_candidates]
    dedup = {
        "schema_version": "rule_dedup_report_v1",
        "input_rows": len(raw_rows),
        "candidate_count": len(rows),
        "merged_duplicate_count": len(duplicates),
        "merged_duplicates": duplicates[:200],
        "created_at": now,
    }
    conflict_payload = {
        "schema_version": "rule_conflicts_v1",
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "created_at": now,
    }
    return rows, conflict_payload, dedup


def _dedup_examples(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    rows = []
    for example in examples:
        key = json.dumps(example, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        rows.append(example)
    return rows


def _detect_rule_conflicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_trigger: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        trigger = _normalize_text(json.dumps(candidate["trigger_pattern"], ensure_ascii=False, sort_keys=True))
        by_trigger[trigger].append(candidate)
    conflicts: list[dict[str, Any]] = []
    for trigger, group in by_trigger.items():
        instructions = {_normalize_text(item["instruction"]) for item in group}
        forbidden_sets = {
            tuple(sorted(str(variant) for variant in item.get("forbidden_variants", []) or []))
            for item in group
        }
        rule_types = {item["rule_type"] for item in group}
        if len(group) > 1 and (len(instructions) > 1 or len(forbidden_sets) > 1):
            conflict_type = "same_trigger_different_instruction"
            if "dictionary_priority_guard" in rule_types and "context_lexical_preference" in rule_types:
                conflict_type = "dictionary_guard_context_rule_overlap"
            conflict_group = _stable_id("ruleconflict", trigger, conflict_type)
            conflicts.append(
                {
                    "conflict_group": conflict_group,
                    "conflict_type": conflict_type,
                    "source_key": (group[0].get("trigger_pattern") or {}).get("text"),
                    "candidate_ids": [item["id"] for item in group],
                    "policy": "requires_human_review",
                    "status": "open",
                    "payload": {
                        "rule_types": sorted(rule_types),
                        "instructions": [item["instruction"] for item in group],
                    },
                    "created_at": utc_now(),
                }
            )
    return conflicts


def _candidate_db_tuple(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["id"],
        row["rule_run_id"],
        row["project_id"],
        row["project_slug"],
        row["rule_type"],
        json_dumps(row["trigger_pattern_json"]),
        json_dumps(row["applies_when_json"]),
        row["instruction"],
        json_dumps(row["examples_json"]),
        json_dumps(row["forbidden_variants_json"]),
        json_dumps(row["scope_json"]),
        row["confidence_score"],
        json_dumps(row["confidence_json"]),
        row["evidence_count"],
        json_dumps(row["provenance_json"]),
        row["status"],
        row["priority"],
        row["conflict_group"],
        row["review_status"],
        row["created_at"],
        row["updated_at"],
        row["reviewed_at"],
    )


def _existing_ids(conn: Any, table_name: str, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(f"SELECT id FROM {table_name} WHERE id IN ({placeholders})", tuple(sorted(ids))).fetchall()
    return {str(row["id"] if hasattr(row, "keys") else row[0]) for row in rows}


def _artifact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "evidence"}


def _group(row: dict[str, Any]) -> str:
    return (row.get("confidence_json") or {}).get("group", "needs_review")


def _review_group(row: dict[str, Any]) -> str:
    if row.get("status") == "needs_human_review" or row.get("conflict_group"):
        return "needs_review"
    return _group(row)


def _write_rule_artifacts(run_dir: Path, rows: list[dict[str, Any]], conflicts: dict[str, Any], dedup: dict[str, Any]) -> None:
    artifact_rows = [_artifact_candidate(row) for row in rows]
    _jsonl_write(_candidate_path(run_dir), artifact_rows)
    _write_review_files(run_dir, artifact_rows)
    _json_write(run_dir / "rule_conflicts.json", conflicts)
    _json_write(run_dir / "rule_dedup_report.json", dedup)
    _text_write(run_dir / "rule_evidence_pack.md", _evidence_markdown(rows))
    _json_write(run_dir / "approved_rules.json", {"schema_version": "approved_rules_v1", "rules": []})
    _json_write(run_dir / "rejected_rules.json", {"schema_version": "rejected_rules_v1", "rules": []})
    if not (run_dir / "rule_audit_log.jsonl").exists():
        _jsonl_write(run_dir / "rule_audit_log.jsonl", [])
    _text_write(
        run_dir / "rule_build_report.md",
        "# Rule Build Report\n\n"
        f"- Candidate count: `{len(rows)}`\n"
        f"- Conflict count: `{conflicts.get('conflict_count', 0)}`\n"
        f"- High confidence: `{sum(1 for row in artifact_rows if _review_group(row) == 'high_confidence')}`\n"
        f"- Needs review: `{sum(1 for row in artifact_rows if _review_group(row) == 'needs_review')}`\n"
        f"- Likely reject: `{sum(1 for row in artifact_rows if _review_group(row) == 'likely_reject')}`\n",
    )


def _write_review_files(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    with (run_dir / "rule_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "rule_type",
                "trigger",
                "instruction",
                "confidence_score",
                "group",
                "status",
                "evidence_count",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row["id"],
                    "rule_type": row["rule_type"],
                    "trigger": (row.get("trigger_pattern_json") or {}).get("text"),
                    "instruction": row["instruction"],
                    "confidence_score": row["confidence_score"],
                    "group": _group(row),
                    "status": row["status"],
                    "evidence_count": row["evidence_count"],
                    "conflict_group": row.get("conflict_group") or "",
                }
            )
    lines = [
        "# Rule Review",
        "",
        "| ID | Type | Trigger | Confidence | Group | Status | Instruction |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in rows[:200]:
        trigger = (row.get("trigger_pattern_json") or {}).get("text") or (row.get("trigger_pattern_json") or {}).get("kind")
        lines.append(
            f"| {row['id']} | {row['rule_type']} | {trigger} | {row['confidence_score']} | {_group(row)} | {row['status']} | {row['instruction']} |"
        )
    _text_write(run_dir / "rule_review.md", "\n".join(lines))


def _evidence_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Rule Evidence Pack", ""]
    for row in rows[:120]:
        lines.extend([f"## {row['id']}", "", f"- Type: `{row['rule_type']}`", f"- Instruction: {row['instruction']}"])
        for evidence in row.get("evidence", [])[:6]:
            lines.append(
                f"- Evidence `{evidence.get('evidence_kind')}` chapter `{evidence.get('chapter_no')}`: {evidence.get('source_excerpt')}"
            )
        lines.append("")
    return "\n".join(lines)


def extract_rule_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    from_hybrid_run: str | None = None,
    from_dictionary_run: str | None = None,
    from_learning_run: str | None = None,
    from_validation_run: str | None = None,
    from_nlp_cache: bool = False,
    chapters: str = "1-10",
    max_candidates: int | None = None,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    requested_chapters = parse_chapter_range(chapters)
    scope = _project_scope(project, requested_chapters)
    validation_run = Path(from_validation_run) if from_validation_run else None
    if validation_run is not None and not validation_run.exists():
        raise ValueError(f"Validation run not found: {validation_run}")
    if from_hybrid_run and not Path(from_hybrid_run).exists():
        raise ValueError(f"Hybrid review/run path not found: {from_hybrid_run}")
    now = utc_now()
    rule_run_id = f"{project_slug}_rules_{int(time.time() * 1000)}"
    run_dir = _rules_root(workspace) / rule_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    raw: list[dict[str, Any]] = []
    raw.extend(_extract_from_hybrid_conflicts(workspace, project, validation_run=validation_run, scope=scope))
    raw.extend(_extract_from_dictionary(workspace, project, scope))
    raw.extend(_extract_from_memory(workspace, project, scope))
    raw.extend(_extract_from_validation(validation_run, scope))
    if from_nlp_cache:
        raw.extend(_extract_from_nlp_cache(workspace, project_slug, requested_chapters, scope))
    rows, conflicts, dedup = _merge_rule_candidates(
        rule_run_id=rule_run_id,
        project=project,
        raw_rows=raw,
        max_candidates=max_candidates,
    )
    source_refs = {
        "from_hybrid_run": str(Path(from_hybrid_run)) if from_hybrid_run else None,
        "from_dictionary_run": str(Path(from_dictionary_run)) if from_dictionary_run else None,
        "from_learning_run": str(Path(from_learning_run)) if from_learning_run else None,
        "from_validation_run": str(validation_run) if validation_run else None,
        "from_nlp_cache": from_nlp_cache,
        "chapters": requested_chapters,
    }
    manifest = {
        "schema_version": "rule_build_manifest_v1",
        "rule_run_id": rule_run_id,
        "project_id": project["id"],
        "project_slug": project_slug,
        "source_run_refs": source_refs,
        "artifact_dir": str(run_dir),
        "candidate_count": len(rows),
        "candidate_counts_by_type": dict(Counter(row["rule_type"] for row in rows)),
        "status": "built",
        "created_at": now,
        "updated_at": now,
    }
    _json_write(run_dir / "rule_build_manifest.json", manifest)
    _write_rule_artifacts(run_dir, rows, conflicts, dedup)
    _write_human_review(run_dir, rows, conflicts)
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="rule.extract",
            status="success",
            stage="built",
            project_id=project["id"],
            input_data=source_refs,
            result_data={"rule_run_id": rule_run_id, "candidate_count": len(rows)},
        )
        conn.execute(
            """
            INSERT INTO rule_runs (
                id, project_id, project_slug, source_run_refs_json, artifact_dir,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rule_run_id, project["id"], project_slug, json_dumps(source_refs), str(run_dir), "built", now, now),
        )
        chapter_ids = {
            str(evidence.get("chapter_id"))
            for row in rows
            for evidence in row.get("evidence", [])
            if evidence.get("chapter_id")
        }
        segment_ids = {
            str(evidence.get("segment_id"))
            for row in rows
            for evidence in row.get("evidence", [])
            if evidence.get("segment_id")
        }
        known_chapter_ids = _existing_ids(conn, "chapters", chapter_ids)
        known_segment_ids = _existing_ids(conn, "segments", segment_ids)
        for row in rows:
            conn.execute(
                """
                INSERT INTO rule_candidates (
                    id, rule_run_id, project_id, project_slug, rule_type,
                    trigger_pattern_json, applies_when_json, instruction, examples_json,
                    forbidden_variants_json, scope_json, confidence_score, confidence_json,
                    evidence_count, provenance_json, status, priority, conflict_group,
                    review_status, created_at, updated_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _candidate_db_tuple(row),
            )
            for evidence in row.get("evidence", []):
                chapter_id = str(evidence.get("chapter_id")) if evidence.get("chapter_id") else None
                segment_id = str(evidence.get("segment_id")) if evidence.get("segment_id") else None
                conn.execute(
                    """
                    INSERT INTO rule_candidate_evidence (
                        id, rule_candidate_id, chapter_id, chapter_no, segment_id,
                        source_excerpt, target_excerpt, model_output_excerpt,
                        evidence_kind, artifact_ref_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("ruleev"),
                        row["id"],
                        chapter_id if chapter_id in known_chapter_ids else None,
                        evidence.get("chapter_no"),
                        segment_id if segment_id in known_segment_ids else None,
                        evidence.get("source_excerpt"),
                        evidence.get("target_excerpt"),
                        evidence.get("model_output_excerpt"),
                        evidence.get("evidence_kind"),
                        json_dumps(evidence.get("artifact_ref") or {}),
                        now,
                    ),
                )
        for conflict in conflicts.get("conflicts", []):
            conn.execute(
                """
                INSERT INTO rule_conflicts (
                    id, rule_run_id, conflict_type, source_key, candidate_ids_json,
                    policy, status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _stable_id("ruleconf", rule_run_id, conflict.get("conflict_type"), conflict.get("source_key"), ",".join(conflict.get("candidate_ids", []))),
                    rule_run_id,
                    conflict.get("conflict_type"),
                    conflict.get("source_key"),
                    json_dumps(conflict.get("candidate_ids", [])),
                    conflict.get("policy") or "requires_human_review",
                    conflict.get("status") or "open",
                    json_dumps(conflict.get("payload") or {}),
                    now,
                ),
            )
        conn.commit()
    return {
        "task_run_id": task_id,
        "rule_run_id": rule_run_id,
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "rule_build_manifest.json"),
        "candidates_path": str(run_dir / "rule_candidates.jsonl"),
        "human_review_path": str(run_dir / "human_review"),
        "candidate_count": len(rows),
        "candidate_counts_by_type": manifest["candidate_counts_by_type"],
        "high_confidence_count": sum(1 for row in rows if _review_group(row) == "high_confidence"),
        "needs_review_count": sum(1 for row in rows if _review_group(row) == "needs_review"),
        "likely_reject_count": sum(1 for row in rows if _review_group(row) == "likely_reject"),
        "conflict_count": conflicts.get("conflict_count", 0),
        "top_rules": [
            {
                "id": row["id"],
                "rule_type": row["rule_type"],
                "trigger": row["trigger_pattern_json"].get("text") or row["trigger_pattern_json"].get("kind"),
                "instruction": row["instruction"],
                "confidence_score": row["confidence_score"],
            }
            for row in rows[:8]
        ],
    }


def _load_candidates_for_run(workspace: Workspace, run: str) -> tuple[Path, dict[str, dict[str, Any]]]:
    run_dir = _resolve_rule_run(workspace, run)
    rows = _jsonl_read(_candidate_path(run_dir))
    if not rows:
        raise ValueError("No rule candidates found for this rule run.")
    return run_dir, {str(row["id"]): row for row in rows}


def _write_human_review(run_dir: Path, rows: list[dict[str, Any]], conflicts: dict[str, Any]) -> str:
    review_dir = run_dir / "human_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    high = [row for row in rows if _review_group(row) == "high_confidence"]
    needs = [row for row in rows if _review_group(row) == "needs_review"]
    reject = [row for row in rows if _review_group(row) == "likely_reject"]
    with (review_dir / "rule_review_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "rule_type", "trigger", "instruction", "confidence_score", "group", "status", "evidence_count"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row["id"],
                    "rule_type": row["rule_type"],
                    "trigger": row["trigger_pattern_json"].get("text") or row["trigger_pattern_json"].get("kind"),
                    "instruction": row["instruction"],
                    "confidence_score": row["confidence_score"],
                    "group": _group(row),
                    "status": row["status"],
                    "evidence_count": row["evidence_count"],
                }
            )
    def section(title: str, section_rows: list[dict[str, Any]]) -> str:
        lines = [f"# {title}", ""]
        for row in section_rows[:80]:
            lines.append(f"- `{row['id']}` {row['rule_type']}: {row['instruction']}")
        if not section_rows:
            lines.append("No candidates.")
        return "\n".join(lines) + "\n"
    _text_write(review_dir / "high_confidence_rules.md", section("High Confidence Rules", high))
    _text_write(review_dir / "needs_review_rules.md", section("Needs Review Rules", needs))
    _text_write(review_dir / "likely_reject_rules.md", section("Likely Reject Rules", reject))
    conflict_lines = ["# Conflicts", ""]
    for conflict in conflicts.get("conflicts", []) or []:
        conflict_lines.append(f"- `{conflict.get('conflict_type')}` trigger `{conflict.get('source_key')}` candidates `{', '.join(conflict.get('candidate_ids', []))}`")
    if not conflicts.get("conflicts"):
        conflict_lines.append("No conflicts detected.")
    _text_write(review_dir / "conflicts.md", "\n".join(conflict_lines))
    approve_lines = ["# Approve Commands", ""]
    if high:
        approve_lines.append(f"python -m nts_cli.main rule approve --project PROJECT --run {run_dir} --rule-ids " + ",".join(row["id"] for row in high[:20]) + " --json")
    else:
        approve_lines.append("No high-confidence approve command suggested.")
    _text_write(review_dir / "approve_commands.md", "\n".join(approve_lines))
    reject_lines = ["# Reject Commands", "", f"python -m nts_cli.main rule reject --project PROJECT --run {run_dir} --rule-ids <ids> --reason \"human rejected\" --json"]
    _text_write(review_dir / "reject_commands.md", "\n".join(reject_lines))
    _text_write(review_dir / "evidence_pack.md", (run_dir / "rule_evidence_pack.md").read_text(encoding="utf-8") if (run_dir / "rule_evidence_pack.md").exists() else "")
    _text_write(
        review_dir / "human_review_summary.md",
        "# Rule Candidate Human Review Summary\n\n"
        f"- Candidate count: `{len(rows)}`\n"
        f"- High confidence: `{len(high)}`\n"
        f"- Needs review: `{len(needs)}`\n"
        f"- Likely reject: `{len(reject)}`\n"
        f"- Conflict count: `{conflicts.get('conflict_count', 0)}`\n"
        "\nNo rules are approved automatically. Approved rules are not injected into production prompts in MVP5G.\n",
    )
    return str(review_dir)


def review_rule_run(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    min_confidence: float | None = None,
    rule_type: str | None = None,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    rows = list(rows_by_id.values())
    if min_confidence is not None:
        rows = [row for row in rows if float(row.get("confidence_score") or 0) >= min_confidence]
    if rule_type:
        rows = [row for row in rows if row.get("rule_type") == rule_type]
    conflicts = _load_json(run_dir / "rule_conflicts.json") if (run_dir / "rule_conflicts.json").exists() else {"conflicts": [], "conflict_count": 0}
    review_path = _write_human_review(run_dir, rows, conflicts)
    return {
        "rule_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "candidate_count": len(rows),
        "high_confidence_count": sum(1 for row in rows if _review_group(row) == "high_confidence"),
        "needs_review_count": sum(1 for row in rows if _review_group(row) == "needs_review"),
        "likely_reject_count": sum(1 for row in rows if _review_group(row) == "likely_reject"),
        "conflict_count": conflicts.get("conflict_count", 0),
        "human_review_path": review_path,
    }


def _update_candidate_artifacts(run_dir: Path, rows_by_id: dict[str, dict[str, Any]]) -> None:
    rows = list(rows_by_id.values())
    rows.sort(key=lambda row: (row["priority"], -float(row["confidence_score"]), row["rule_type"], row["instruction"]))
    _jsonl_write(_candidate_path(run_dir), rows)
    _write_review_files(run_dir, rows)


def approve_rule_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    rule_ids: str | None = None,
    all_high_confidence: bool = False,
    reviewer: str = "human",
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    if rule_ids:
        selected_ids = [item.strip() for item in rule_ids.split(",") if item.strip()]
    elif all_high_confidence:
        selected_ids = [
            row["id"]
            for row in rows_by_id.values()
            if _group(row) == "high_confidence" and row.get("status") != "needs_human_review"
        ]
    else:
        raise ValueError("Use --rule-ids or --all-high-confidence.")
    missing = [rule_id for rule_id in selected_ids if rule_id not in rows_by_id]
    if missing:
        raise ValueError(f"Rule candidate(s) not found: {', '.join(missing)}")
    now = utc_now()
    approved = []
    with connection(workspace.db_path) as conn:
        for rule_id in selected_ids:
            row = rows_by_id[rule_id]
            approved_rule_id = _stable_id("rule", project_slug, row["rule_type"], json.dumps(row["trigger_pattern_json"], ensure_ascii=False, sort_keys=True), row["instruction"])
            conn.execute(
                """
                INSERT OR REPLACE INTO approved_rules (
                    id, project_id, project_slug, rule_type, trigger_pattern_json,
                    applies_when_json, instruction, examples_json, forbidden_variants_json,
                    scope_json, confidence_score, provenance_json, status,
                    approved_by, approved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approved_rule_id,
                    project["id"],
                    project_slug,
                    row["rule_type"],
                    json_dumps(row["trigger_pattern_json"]),
                    json_dumps(row["applies_when_json"]),
                    row["instruction"],
                    json_dumps(row["examples_json"]),
                    json_dumps(row["forbidden_variants_json"]),
                    json_dumps(row["scope_json"]),
                    row["confidence_score"],
                    json_dumps(row["provenance_json"]),
                    "active",
                    reviewer,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE rule_candidates SET status = ?, review_status = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                ("approved_by_human", "approved", now, now, rule_id),
            )
            payload = {"rule_candidate_id": rule_id, "approved_rule_id": approved_rule_id, "reviewer": reviewer}
            conn.execute(
                """
                INSERT INTO rule_audit_logs (
                    id, rule_candidate_id, approved_rule_id, action, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_id("ruleaudit"), rule_id, approved_rule_id, "approve", json_dumps(payload), now),
            )
            row["status"] = "approved_by_human"
            row["review_status"] = "approved"
            row["reviewed_at"] = now
            approved.append({"rule_id": approved_rule_id, "candidate_id": rule_id, "rule_type": row["rule_type"], "instruction": row["instruction"]})
            _append_jsonl(run_dir / "rule_audit_log.jsonl", {"action": "approve", "created_at": now, **payload})
        task_id = insert_task_run(
            conn,
            task_type="rule.approve",
            status="success",
            stage="approved",
            project_id=project["id"],
            input_data={"project": project_slug, "run": str(run_dir), "rule_ids": selected_ids},
            result_data={"approved_rule_candidate_ids": selected_ids},
        )
        conn.commit()
    _update_candidate_artifacts(run_dir, rows_by_id)
    _json_write(run_dir / "approved_rules.json", {"schema_version": "approved_rules_v1", "rules": approved, "updated_at": now})
    return {
        "task_run_id": task_id,
        "rule_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "updated_rule_ids": selected_ids,
        "approved_rules": approved,
        "approved_rules_path": str(run_dir / "approved_rules.json"),
    }


def reject_rule_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    rule_ids: str,
    reason: str,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    selected_ids = [item.strip() for item in rule_ids.split(",") if item.strip()]
    missing = [rule_id for rule_id in selected_ids if rule_id not in rows_by_id]
    if missing:
        raise ValueError(f"Rule candidate(s) not found: {', '.join(missing)}")
    now = utc_now()
    rejected = []
    with connection(workspace.db_path) as conn:
        for rule_id in selected_ids:
            row = rows_by_id[rule_id]
            conn.execute(
                "UPDATE rule_candidates SET status = ?, review_status = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                ("rejected", "rejected", now, now, rule_id),
            )
            payload = {"rule_candidate_id": rule_id, "reason": reason}
            conn.execute(
                """
                INSERT INTO rule_audit_logs (
                    id, rule_candidate_id, approved_rule_id, action, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_id("ruleaudit"), rule_id, None, "reject", json_dumps(payload), now),
            )
            row["status"] = "rejected"
            row["review_status"] = "rejected"
            row["reviewed_at"] = now
            rejected.append({"candidate_id": rule_id, "reason": reason, "instruction": row["instruction"]})
            _append_jsonl(run_dir / "rule_audit_log.jsonl", {"action": "reject", "created_at": now, **payload})
        task_id = insert_task_run(
            conn,
            task_type="rule.reject",
            status="success",
            stage="rejected",
            project_id=project["id"],
            input_data={"project": project_slug, "run": str(run_dir), "rule_ids": selected_ids},
            result_data={"rejected_rule_candidate_ids": selected_ids},
        )
        conn.commit()
    _update_candidate_artifacts(run_dir, rows_by_id)
    _json_write(run_dir / "rejected_rules.json", {"schema_version": "rejected_rules_v1", "rules": rejected, "updated_at": now})
    return {
        "task_run_id": task_id,
        "rule_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "updated_rule_ids": selected_ids,
        "rejected_rules_path": str(run_dir / "rejected_rules.json"),
    }


def load_approved_rules(workspace: Workspace, project_slug: str) -> list[dict[str, Any]]:
    initialize_database(workspace.db_path)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, project_slug, rule_type, trigger_pattern_json,
                   applies_when_json, instruction, examples_json, forbidden_variants_json,
                   scope_json, confidence_score, provenance_json, status,
                   approved_by, approved_at, created_at, updated_at
            FROM approved_rules
            WHERE project_slug = ? AND status = 'active'
            ORDER BY rule_type ASC, confidence_score DESC, instruction ASC
            """,
            (project_slug,),
        ).fetchall()
    return [_row_to_approved_rule(row) for row in rows]


def export_project_rules(workspace: Workspace, *, project_slug: str, out: Path | None = None) -> dict[str, Any]:
    rules = load_approved_rules(workspace, project_slug)
    payload = {
        "schema_version": "approved_project_rules_export_v1",
        "project_slug": project_slug,
        "exported_at": utc_now(),
        "rule_count": len(rules),
        "rules": rules,
    }
    default_out = workspace.path / "artifacts" / "rules" / project_slug / "approved_rules_export.json"
    output_path = out or default_out
    _json_write(output_path, payload)
    return {"project_slug": project_slug, "rule_count": len(rules), "output_path": str(output_path), "rules": rules}


def rule_status(workspace: Workspace, *, project_slug: str) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    with connection(workspace.db_path) as conn:
        approved = conn.execute(
            "SELECT COUNT(*) FROM approved_rules WHERE project_slug = ? AND status = 'active'",
            (project_slug,),
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM rule_candidates WHERE project_slug = ? AND status IN ('pending_review', 'needs_human_review', 'likely_reject')",
            (project_slug,),
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM rule_candidates WHERE project_slug = ? AND status = 'rejected'",
            (project_slug,),
        ).fetchone()[0]
        conflicts = conn.execute(
            "SELECT COUNT(*) FROM rule_conflicts WHERE rule_run_id IN (SELECT id FROM rule_runs WHERE project_slug = ?)",
            (project_slug,),
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT id, artifact_dir, status, created_at FROM rule_runs WHERE project_slug = ? ORDER BY created_at DESC LIMIT 1",
            (project_slug,),
        ).fetchone()
    return {
        "project_slug": project_slug,
        "approved_rule_count": int(approved),
        "pending_rule_candidate_count": int(pending),
        "rejected_rule_count": int(rejected),
        "conflict_count": int(conflicts),
        "last_run": row_to_dict(last_run) if last_run else None,
    }


def _rule_matches(rule: dict[str, Any], source_text: str, mode: str) -> tuple[bool, list[str]]:
    trigger = rule.get("trigger_pattern_json") or {}
    applies = rule.get("applies_when_json") or {}
    kind = trigger.get("kind")
    text = str(trigger.get("text") or "")
    reasons: list[str] = []
    matched = False
    if kind in {"exact_text", "exact_ngram", "dictionary_hit"}:
        matched = bool(text and text in source_text)
        reasons.append("exact_trigger_match" if matched else "exact_trigger_absent")
    elif kind == "segment_type" and text == "system_panel":
        matched = bool(BRACKET_RE.search(source_text))
        reasons.append("system_panel_bracket_match" if matched else "system_panel_absent")
    elif kind == "anchored_regex":
        matched = bool(text and re.search(text, source_text))
        reasons.append("regex_match" if matched else "regex_absent")
    if matched and applies.get("source_has_brackets") and not BRACKET_RE.search(source_text):
        matched = False
        reasons.append("applies_when_source_has_brackets_failed")
    if matched and applies.get("context_required") in {"system_panel", "system_panel_or_game_ui"} and not BRACKET_RE.search(source_text):
        matched = False
        reasons.append("context_required_failed")
    return matched, reasons


def test_project_rules(
    workspace: Workspace,
    *,
    project_slug: str,
    source_text: str,
    mode: str = "production",
) -> dict[str, Any]:
    rules = load_approved_rules(workspace, project_slug)
    matches = []
    misses = []
    for rule in rules:
        matched, reasons = _rule_matches(rule, source_text, mode)
        row = {
            "rule_id": rule["id"],
            "rule_type": rule["rule_type"],
            "trigger_pattern": rule["trigger_pattern_json"],
            "instruction": rule["instruction"],
            "reasons": reasons,
        }
        if matched:
            matches.append(row)
        else:
            misses.append(row)
    return {
        "project_slug": project_slug,
        "mode": mode,
        "source_text": source_text,
        "match_count": len(matches),
        "matches": matches,
        "non_matching_rule_count": len(misses),
        "read_only": True,
        "prompt_integration": "not_enabled_in_mvp5g",
    }


test_project_rules.__test__ = False
