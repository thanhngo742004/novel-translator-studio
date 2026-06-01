from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from nts_core.dictionary import TYPE_PRIORITY, load_project_dictionary
from nts_core.projects import get_project_by_slug
from nts_core.rules import load_approved_rules
from nts_storage.database import connection, row_to_dict, utc_now
from nts_storage.workspace import Workspace


HYBRID_PROMPT_SCHEMA = "hybrid_prompt_context_bundle_v1"
HYBRID_HEURISTICS_VERSION = "mvp5h-v1"
MEMORY_TYPE_PRIORITY = {
    "name": 0,
    "term": 1,
    "correction": 2,
    "pronoun": 3,
    "style": 4,
}
BLOCKED_MEMORY_MARKERS = {
    "deprecated_for_validation",
    "rejected_after_validation",
    "pending_needs_scoped_review",
    "harmful",
    "harmful_only_in_combination",
    "insufficient_evidence",
    "pending_review",
}
RULE_TYPE_PRIORITY = {
    "dictionary_priority_guard": 0,
    "format_preservation": 1,
    "forbidden_variant": 2,
    "expansion_guard": 3,
    "entity_atomicity_guard": 4,
    "context_lexical_preference": 5,
    "style_rhythm_preservation": 6,
}


@dataclass
class SupportItem:
    item_id: str
    source_type: str
    source_anchor: str
    target_value: str
    instruction_text: str
    entry_type: str | None = None
    memory_type: str | None = None
    rule_id: str | None = None
    rule_type: str | None = None
    trigger_pattern: dict[str, Any] = field(default_factory=dict)
    forbidden_variants: list[str] = field(default_factory=list)
    priority: int | None = None
    authority_rank: int = 50
    specificity_rank: int = 0
    confidence: float = 0.0
    scope: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    mode_allowed: str = "production"
    source_ref: str | None = None
    conflict_group: str | None = None
    char_cost: int = 0
    exact_source_match: bool = True
    render_group: str = "memory"
    drop_reason: str | None = None
    merged_provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")


def _source_present(source_text: str, anchor: str | None) -> bool:
    if not anchor:
        return False
    if _has_chinese(anchor):
        return anchor in source_text
    return _normalize_text(anchor) in _normalize_text(source_text)


def _project_scope_context(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "domain": project.get("domain"),
        "source_lang": project.get("source_lang"),
        "target_lang": project.get("target_lang"),
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
    }


def _scope_matches(scope: dict[str, Any], context: dict[str, Any]) -> bool:
    for key in (
        "project_id",
        "project_slug",
        "domain",
        "source_lang",
        "target_lang",
        "language_pair",
    ):
        if key in scope and scope[key] not in (None, context.get(key)):
            return False
    return True


def _compact_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"summary": str(value)[:160]} if value else {}
    keys = (
        "source_run_id",
        "dict_run_id",
        "candidate_id",
        "memory_id",
        "target_provenance",
        "source_kinds",
        "learning_run_id",
        "mining_run_id",
    )
    compact = {key: value.get(key) for key in keys if value.get(key)}
    if not compact and value:
        compact = {key: value.get(key) for key in sorted(value)[:4]}
    return compact


def _memory_source_anchor(item: dict[str, Any]) -> str | None:
    value = item.get("value_json") or {}
    return item.get("source_key") or value.get("source_pattern")


def _memory_target_value(item: dict[str, Any]) -> str | None:
    value = item.get("value_json") or {}
    rules = item.get("rules_json") or {}
    return item.get("target_text") or value.get("preferred_target") or rules.get("preferred_target")


def _memory_context_gate(item: dict[str, Any], source_text: str) -> tuple[bool, str | None]:
    anchor = _memory_source_anchor(item)
    value = item.get("value_json") or {}
    rules = item.get("rules_json") or {}
    context_required = str(value.get("context_required") or rules.get("context_required") or "")
    if anchor == "技能" and not re.search(r"【[^】]*技能[^】]*】", source_text or ""):
        return False, "context_gate_failed:skills_requires_system_panel"
    if context_required in {"system_panel", "game_ui"} and not re.search(r"【[^】]+】", source_text or ""):
        return False, f"context_gate_failed:{context_required}"
    if context_required == "name_only" and item.get("memory_type") != "name":
        return False, "context_gate_failed:name_only"
    if context_required == "exact_phrase_only" and not _source_present(source_text, anchor):
        return False, "context_gate_failed:exact_phrase_only"
    return True, None


def _memory_negative_gate(item: dict[str, Any], *, production: bool = True) -> tuple[bool, str | None]:
    value = item.get("value_json") or {}
    confidence = item.get("confidence_json") or {}
    if item.get("status") != "active":
        return False, f"status_gate:{item.get('status')}"
    if production and str(item.get("layer") or "").startswith("temporary"):
        return False, "mode_gate:temporary_learning_memory"
    if value.get("deprecated_for_validation") is True:
        return False, "negative_evidence_gate:deprecated_for_validation"
    for field_name, source in (("value", value), ("confidence", confidence)):
        for key in ("status", "review_status", "validation_status", "impact_classification"):
            marker = str(source.get(key) or "")
            if marker in BLOCKED_MEMORY_MARKERS:
                return False, f"negative_evidence_gate:{field_name}.{key}={marker}"
    return True, None


def _chapter_scope_gate(item: dict[str, Any], chapters: set[int] | None) -> tuple[bool, str | None]:
    if not chapters:
        return True, None
    value = item.get("value_json") or {}
    rules = item.get("rules_json") or {}
    excluded = {
        int(chapter)
        for chapter in (value.get("exclude_chapters") or rules.get("exclude_chapters") or [])
        if str(chapter).strip().lstrip("-").isdigit()
    }
    overlap = excluded & chapters
    if overlap:
        return False, "scope_gate:excluded_chapter=" + ",".join(str(chapter) for chapter in sorted(overlap))
    return True, None


def _rule_chapter_scope_gate(scope: dict[str, Any], chapters: set[int] | None) -> tuple[bool, str | None]:
    if not chapters:
        return True, None
    scoped_chapters = {
        int(chapter)
        for chapter in (scope.get("chapters") or [])
        if str(chapter).strip().lstrip("-").isdigit()
    }
    if scoped_chapters and not (scoped_chapters & chapters):
        return False, "scope_gate:chapter_not_in_rule_scope"
    excluded = {
        int(chapter)
        for chapter in (scope.get("exclude_chapters") or [])
        if str(chapter).strip().lstrip("-").isdigit()
    }
    overlap = excluded & chapters
    if overlap:
        return False, "scope_gate:rule_excluded_chapter=" + ",".join(str(chapter) for chapter in sorted(overlap))
    return True, None


def _load_active_memory_rows(workspace: Workspace, project: dict[str, Any]) -> list[dict[str, Any]]:
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
    context = _project_scope_context(project)
    scoped: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(
            row,
            json_fields=("scope_json", "value_json", "rules_json", "confidence_json"),
        )
        if _scope_matches(item.get("scope_json") or {}, context):
            scoped.append(item)
    return scoped


def _load_ineligible_rule_rows(workspace: Workspace, project_slug: str, source_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with connection(workspace.db_path) as conn:
        candidates = conn.execute(
            """
            SELECT id, rule_type, trigger_pattern_json, instruction, status, review_status,
                   confidence_score
            FROM rule_candidates
            WHERE project_slug = ? AND status NOT IN ('approved_by_human')
            ORDER BY created_at ASC, id ASC
            """,
            (project_slug,),
        ).fetchall()
        inactive_approved = conn.execute(
            """
            SELECT id, rule_type, trigger_pattern_json, instruction, status, confidence_score
            FROM approved_rules
            WHERE project_slug = ? AND status != 'active'
            ORDER BY created_at ASC, id ASC
            """,
            (project_slug,),
        ).fetchall()
    for row in candidates:
        item = row_to_dict(row, json_fields=("trigger_pattern_json",))
        trigger = item.get("trigger_pattern_json") or {}
        text = str(trigger.get("text") or "")
        if text and _source_present(source_text, text):
            item["record_kind"] = "rule_candidate"
            item["reasons"] = [f"status_gate:{item.get('status')}"]
            rows.append(item)
    for row in inactive_approved:
        item = row_to_dict(row, json_fields=("trigger_pattern_json",))
        trigger = item.get("trigger_pattern_json") or {}
        text = str(trigger.get("text") or "")
        if text and _source_present(source_text, text):
            item["record_kind"] = "approved_rule"
            status = str(item.get("status") or "")
            reason_map = {
                "active_verifier_only": "verifier_only",
                "disabled_for_prompt": "disabled_after_validation",
                "rejected_after_validation": "rejected_after_validation",
                "scoped_prompt_only": "scoped_prompt_only",
            }
            item["reasons"] = [reason_map.get(status, f"status_gate:{status}")]
            rows.append(item)
    return rows[:100]


def _load_inactive_memory_matches(workspace: Workspace, project: dict[str, Any], source_text: str) -> list[dict[str, Any]]:
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            WHERE status != 'active'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    context = _project_scope_context(project)
    matches: list[dict[str, Any]] = []
    for row in rows:
        item = row_to_dict(
            row,
            json_fields=("scope_json", "value_json", "rules_json", "confidence_json"),
        )
        if not _scope_matches(item.get("scope_json") or {}, context):
            continue
        anchor = _memory_source_anchor(item)
        if anchor and _source_present(source_text, anchor):
            matches.append(
                {
                    "memory_id": item.get("id"),
                    "memory_type": item.get("memory_type"),
                    "status": item.get("status"),
                    "source_anchor": anchor,
                    "target_value": _memory_target_value(item),
                    "negative_evidence": item.get("value_json") or {},
                }
            )
    return matches[:50]


def _inactive_dictionary_matches(workspace: Workspace, project_slug: str, source_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with connection(workspace.db_path) as conn:
        candidate_rows = conn.execute(
            """
            SELECT id, entry_type, source_text, target_text, status, confidence_score
            FROM dictionary_candidates
            WHERE project_slug = ? AND status NOT IN ('approved_by_human')
            """,
            (project_slug,),
        ).fetchall()
        entry_rows = conn.execute(
            """
            SELECT id, entry_type, source_text, target_text, status, confidence_score
            FROM project_dictionary_entries
            WHERE project_slug = ? AND status != 'active'
            """,
            (project_slug,),
        ).fetchall()
    for row in candidate_rows:
        item = row_to_dict(row)
        if item.get("source_text") and str(item["source_text"]) in source_text:
            item["record_kind"] = "dictionary_candidate"
            rows.append(item)
    for row in entry_rows:
        item = row_to_dict(row)
        if item.get("source_text") and str(item["source_text"]) in source_text:
            item["record_kind"] = "project_dictionary_entry"
            rows.append(item)
    return rows[:50]


def _dictionary_scope_gate(entry: dict[str, Any], chapters: set[int] | None) -> tuple[bool, str | None]:
    if not chapters:
        return True, None
    scope = entry.get("scope_json") or {}
    scoped = {
        int(chapter)
        for chapter in (scope.get("chapters") or [])
        if str(chapter).strip().lstrip("-").isdigit()
    }
    if scoped and not (scoped & chapters):
        return False, "scope_gate:chapter_not_in_dictionary_scope"
    excluded = {
        int(chapter)
        for chapter in (scope.get("exclude_chapters") or [])
        if str(chapter).strip().lstrip("-").isdigit()
    }
    overlap = excluded & chapters
    if overlap:
        return False, "scope_gate:dictionary_excluded_chapter=" + ",".join(str(chapter) for chapter in sorted(overlap))
    return True, None


def _dictionary_items(
    workspace: Workspace,
    project_slug: str,
    source_text: str,
    *,
    max_scan_entries: int = 1000,
    chapters: set[int] | None = None,
) -> list[SupportItem]:
    entries = []
    for entry in load_project_dictionary(workspace, project_slug):
        if not entry.get("source_text") or str(entry["source_text"]) not in source_text:
            continue
        ok, _reason = _dictionary_scope_gate(entry, chapters)
        if ok:
            entries.append(entry)
    entries.sort(
        key=lambda entry: (
            -len(str(entry.get("source_text") or "")),
            TYPE_PRIORITY.get(str(entry.get("entry_type")), 99),
            -float(entry.get("confidence_score") or 0),
            str(entry.get("id")),
        )
    )
    items: list[SupportItem] = []
    for entry in entries[:max_scan_entries]:
        source = str(entry["source_text"])
        target = str(entry.get("target_text") or "")
        instruction = f"{source} => {target}"
        items.append(
            SupportItem(
                item_id=str(entry.get("id")),
                source_type="dictionary",
                source_anchor=source,
                target_value=target,
                instruction_text=instruction,
                entry_type=str(entry.get("entry_type") or "fixed_phrase"),
                authority_rank=100,
                specificity_rank=len(source),
                confidence=float(entry.get("confidence_score") or 0),
                scope=entry.get("scope_json") or {},
                provenance=_compact_provenance(entry.get("provenance_json") or {}),
                status=str(entry.get("status") or "active"),
                mode_allowed="production",
                source_ref=f"project_dictionary_entries:{entry.get('id')}",
                char_cost=len(instruction),
                render_group="dictionary",
            )
        )
    return items


def _memory_items(
    workspace: Workspace,
    project: dict[str, Any],
    source_text: str,
    *,
    mode: str,
    chapters: set[int] | None = None,
) -> tuple[list[SupportItem], list[dict[str, Any]], int]:
    production = mode == "production"
    rows = _load_active_memory_rows(workspace, project)
    items: list[SupportItem] = []
    excluded: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        anchor = _memory_source_anchor(row)
        target = _memory_target_value(row)
        if not anchor:
            reasons.append("exact_source_trigger_missing")
        elif not _source_present(source_text, anchor):
            reasons.append("exact_source_trigger_absent")
        ok, reason = _memory_negative_gate(row, production=production)
        if not ok:
            reasons.append(reason or "negative_evidence_gate")
        if not reasons:
            ok, reason = _chapter_scope_gate(row, chapters)
            if not ok:
                reasons.append(reason or "scope_gate")
        if not reasons:
            ok, reason = _memory_context_gate(row, source_text)
            if not ok:
                reasons.append(reason or "context_gate")
        if reasons:
            excluded.append(
                {
                    "memory_id": row.get("id"),
                    "memory_type": row.get("memory_type"),
                    "source_anchor": anchor,
                    "target_value": target,
                    "status": row.get("status"),
                    "reasons": reasons,
                }
            )
            continue
        if not anchor or not target:
            excluded.append(
                {
                    "memory_id": row.get("id"),
                    "memory_type": row.get("memory_type"),
                    "source_anchor": anchor,
                    "target_value": target,
                    "status": row.get("status"),
                    "reasons": ["missing_source_or_target"],
                }
            )
            continue
        instruction = f"{anchor} => {target}"
        value = row.get("value_json") or {}
        rules = row.get("rules_json") or {}
        items.append(
            SupportItem(
                item_id=str(row.get("id")),
                source_type="memory",
                source_anchor=str(anchor),
                target_value=str(target),
                instruction_text=instruction,
                memory_type=str(row.get("memory_type") or ""),
                authority_rank=70 if anchor else 50,
                specificity_rank=len(str(anchor)),
                confidence=float(row.get("confidence_score") or 0),
                scope=row.get("scope_json") or {},
                provenance={
                    "value": _compact_provenance(value),
                    "confidence": _compact_provenance(row.get("confidence_json") or {}),
                    "rules": _compact_provenance(rules),
                },
                status=str(row.get("status") or "active"),
                mode_allowed="both" if not production else "production",
                source_ref=f"memory_items:{row.get('id')}",
                char_cost=len(instruction),
                render_group="memory",
            )
        )
    return items, excluded[:100], len(rows)


def _safe_rule_trigger_match(rule: dict[str, Any], source_text: str) -> tuple[bool, list[str]]:
    trigger = rule.get("trigger_pattern_json") or {}
    applies = rule.get("applies_when_json") or {}
    kind = str(trigger.get("kind") or "")
    text = str(trigger.get("text") or "")
    reasons: list[str] = []
    matched = False
    if kind in {"exact_text", "exact_ngram", "dictionary_hit"}:
        matched = bool(text and _source_present(source_text, text))
        reasons.append("exact_trigger_match" if matched else "exact_trigger_absent")
    elif kind == "segment_type":
        if text == "system_panel":
            matched = bool(re.search(r"【[^】]+】", source_text or ""))
            reasons.append("system_panel_bracket_match" if matched else "system_panel_absent")
        else:
            reasons.append(f"unsupported_segment_type:{text}")
    elif kind == "anchored_regex":
        try:
            matched = bool(text and re.search(text, source_text or ""))
            reasons.append("regex_match" if matched else "regex_absent")
        except re.error:
            matched = False
            reasons.append("unsafe_regex_rejected")
    else:
        reasons.append(f"unsupported_trigger_kind:{kind or 'missing'}")
    if matched and applies.get("source_has_brackets") and not re.search(r"【[^】]+】", source_text or ""):
        matched = False
        reasons.append("applies_when_source_has_brackets_failed")
    context_required = str(applies.get("context_required") or "")
    if matched and context_required in {"system_panel", "system_panel_or_game_ui", "game_ui"} and not re.search(r"【[^】]+】", source_text or ""):
        matched = False
        reasons.append(f"context_required_failed:{context_required}")
    return matched, reasons


def _rule_is_covered_by_dictionary(rule: dict[str, Any], dictionary_items: list[SupportItem]) -> bool:
    if rule.get("rule_type") != "dictionary_priority_guard":
        return False
    trigger = rule.get("trigger_pattern_json") or {}
    text = str(trigger.get("text") or "")
    return bool(text and any(item.source_anchor == text for item in dictionary_items))


def _longer_dictionary_hit_present(rule: dict[str, Any], dictionary_items: list[SupportItem]) -> bool:
    if rule.get("rule_type") != "expansion_guard":
        return False
    trigger = rule.get("trigger_pattern_json") or {}
    text = str(trigger.get("text") or "")
    return bool(text and any(item.source_anchor != text and text in item.source_anchor for item in dictionary_items))


def _rule_has_positive_canon(rule: dict[str, Any], dictionary_items: list[SupportItem], memory_items: list[SupportItem]) -> bool:
    trigger = rule.get("trigger_pattern_json") or {}
    text = str(trigger.get("text") or "")
    if not text:
        return False
    return any(item.source_anchor == text and item.target_value for item in [*dictionary_items, *memory_items])


def _rule_needs_panel_context(rule: dict[str, Any]) -> bool:
    if rule.get("rule_type") != "expansion_guard":
        return False
    forbidden = " ".join(str(item) for item in (rule.get("forbidden_variants_json") or []))
    instruction = str(rule.get("instruction") or "")
    return "【" in forbidden or "】" in forbidden or "【" in instruction or "】" in instruction


def _rule_has_concrete_negative_evidence(rule: dict[str, Any]) -> bool:
    if rule.get("forbidden_variants_json"):
        return True
    for example in rule.get("examples_json") or []:
        if isinstance(example, dict) and (example.get("rejected") or example.get("forbidden")):
            return True
    provenance = rule.get("provenance_json") or {}
    return str(provenance.get("conflict_type") or "") in {
        "overlapping_dictionary_hit",
        "related_inactive_or_negative_memory",
    }


def _rule_items(
    workspace: Workspace,
    project: dict[str, Any],
    source_text: str,
    *,
    mode: str,
    chapters: set[int] | None,
    dictionary_items: list[SupportItem],
    memory_items: list[SupportItem],
) -> tuple[list[SupportItem], list[dict[str, Any]], int]:
    context = _project_scope_context(project)
    rules = load_approved_rules(workspace, project["slug"])
    items: list[SupportItem] = []
    excluded: list[dict[str, Any]] = []
    for rule in rules:
        reasons: list[str] = []
        if rule.get("status") != "active":
            reasons.append(f"status_gate:{rule.get('status')}")
        if not rule.get("approved_by"):
            reasons.append("status_gate:missing_approved_by")
        scope = rule.get("scope_json") or {}
        if not _scope_matches(scope, context):
            reasons.append("scope_gate:project_scope_mismatch")
        ok, reason = _rule_chapter_scope_gate(scope, chapters)
        if not ok:
            reasons.append(reason or "scope_gate:chapter")
        matched, match_reasons = _safe_rule_trigger_match(rule, source_text)
        if not matched:
            reasons.extend(match_reasons)
        if _longer_dictionary_hit_present(rule, dictionary_items):
            reasons.append("longer_exact_dictionary_hit_present")
        if rule.get("rule_type") == "forbidden_variant":
            if not rule.get("forbidden_variants_json"):
                reasons.append("no_concrete_forbidden_variant")
            if not _rule_has_positive_canon(rule, dictionary_items, memory_items):
                reasons.append("forbidden_variant_without_positive_canon")
        if rule.get("rule_type") == "expansion_guard" and not _rule_has_concrete_negative_evidence(rule):
            reasons.append("no_concrete_forbidden_variant")
        if _rule_needs_panel_context(rule) and not re.search(r"【[^】]+】", source_text or ""):
            reasons.append("panel_expansion_guard_requires_bracket_context")
        if reasons:
            excluded.append(
                {
                    "rule_id": rule.get("id"),
                    "rule_type": rule.get("rule_type"),
                    "trigger_pattern": rule.get("trigger_pattern_json") or {},
                    "instruction": rule.get("instruction"),
                    "status": rule.get("status"),
                    "reasons": reasons,
                }
            )
            continue
        trigger = rule.get("trigger_pattern_json") or {}
        source_anchor = str(trigger.get("text") or trigger.get("kind") or "")
        instruction = str(rule.get("instruction") or "").strip()
        if not instruction:
            excluded.append(
                {
                    "rule_id": rule.get("id"),
                    "rule_type": rule.get("rule_type"),
                    "trigger_pattern": trigger,
                    "status": rule.get("status"),
                    "reasons": ["missing_instruction"],
                }
            )
            continue
        rule_type = str(rule.get("rule_type") or "")
        authority = 78 if rule_type in {"dictionary_priority_guard", "format_preservation"} else 74
        if _rule_is_covered_by_dictionary(rule, dictionary_items):
            authority = 64
        items.append(
            SupportItem(
                item_id=str(rule.get("id")),
                source_type="rule",
                source_anchor=source_anchor,
                target_value="",
                instruction_text=instruction,
                rule_id=str(rule.get("id")),
                rule_type=rule_type,
                trigger_pattern=trigger,
                forbidden_variants=[str(item) for item in (rule.get("forbidden_variants_json") or [])],
                priority=RULE_TYPE_PRIORITY.get(rule_type, 99),
                authority_rank=authority,
                specificity_rank=len(source_anchor),
                confidence=float(rule.get("confidence_score") or 0),
                scope=scope,
                provenance=_compact_provenance(rule.get("provenance_json") or {}),
                status=str(rule.get("status") or "active"),
                mode_allowed=mode,
                source_ref=f"approved_rules:{rule.get('id')}",
                char_cost=len(instruction),
                render_group="rule",
            )
        )
    return items, excluded[:100], len(rules)


def _dictionary_occurs_independently(source_text: str, shorter: str, longer_items: list[SupportItem]) -> bool:
    positions: list[tuple[int, int]] = []
    for item in longer_items:
        long_anchor = item.source_anchor
        start = source_text.find(long_anchor)
        while start >= 0:
            positions.append((start, start + len(long_anchor)))
            start = source_text.find(long_anchor, start + 1)
    short_start = source_text.find(shorter)
    while short_start >= 0:
        short_end = short_start + len(shorter)
        if not any(start <= short_start and short_end <= end for start, end in positions):
            return True
        short_start = source_text.find(shorter, short_start + 1)
    return False


def _rank_key(item: SupportItem) -> tuple[Any, ...]:
    if item.source_type == "dictionary":
        type_priority = TYPE_PRIORITY.get(str(item.entry_type), 99)
    elif item.source_type == "rule":
        type_priority = RULE_TYPE_PRIORITY.get(str(item.rule_type), 99)
    else:
        type_priority = MEMORY_TYPE_PRIORITY.get(str(item.memory_type), 99)
    return (
        -item.authority_rank,
        -int(item.exact_source_match),
        -item.specificity_rank,
        type_priority,
        -item.confidence,
        str(item.item_id),
    )


def _dedupe_and_resolve_conflicts(
    *,
    items: list[SupportItem],
    source_text: str,
    inactive_memory_matches: list[dict[str, Any]],
) -> tuple[list[SupportItem], list[SupportItem], dict[str, Any]]:
    selected: list[SupportItem] = []
    dropped: list[SupportItem] = []
    conflicts: list[dict[str, Any]] = []

    dictionary_items = [item for item in items if item.source_type == "dictionary"]
    overlap_drop_ids: set[str] = set()
    for item in dictionary_items:
        longer = [
            candidate
            for candidate in dictionary_items
            if candidate.item_id != item.item_id
            and item.source_anchor in candidate.source_anchor
            and len(candidate.source_anchor) > len(item.source_anchor)
        ]
        if longer and not _dictionary_occurs_independently(source_text, item.source_anchor, longer):
            overlap_drop_ids.add(item.item_id)
            conflicts.append(
                {
                    "conflict_type": "overlapping_dictionary_hit",
                    "source_anchor": item.source_anchor,
                    "dropped_item_id": item.item_id,
                    "kept_item_ids": [candidate.item_id for candidate in longer],
                    "policy": "longer_exact_hit_wins",
                }
            )

    remaining: list[SupportItem] = []
    for item in items:
        if item.item_id in overlap_drop_ids:
            item.drop_reason = "overlapping_longer_dictionary_hit"
            dropped.append(item)
        else:
            remaining.append(item)

    groups: dict[str, list[SupportItem]] = {}
    for item in remaining:
        key = _normalize_text(item.source_anchor)
        groups.setdefault(key, []).append(item)

    for source_key, group in groups.items():
        dictionary_for_anchor = [item for item in group if item.source_type == "dictionary"]
        if dictionary_for_anchor:
            for rule in [item for item in group if item.source_type == "rule" and item.rule_type == "dictionary_priority_guard"]:
                conflicts.append(
                    {
                        "conflict_type": "dictionary_rule_duplicate",
                        "source_anchor": rule.source_anchor,
                        "dictionary_item_ids": [item.item_id for item in dictionary_for_anchor],
                        "rule_item_id": rule.item_id,
                        "policy": "dictionary_canon_kept_rule_budgeted_if_behavioral",
                    }
                )
        target_groups: dict[str, list[SupportItem]] = {}
        for item in group:
            if item.source_type == "rule":
                if item.rule_type == "dictionary_priority_guard" and dictionary_for_anchor:
                    item.drop_reason = "covered_by_dictionary"
                    dropped.append(item)
                    continue
                selected.append(item)
                continue
            target_groups.setdefault(_normalize_text(item.target_value), []).append(item)
        if len(target_groups) > 1:
            conflict_group = f"conflict_{hashlib.sha1(source_key.encode('utf-8')).hexdigest()[:10]}"
            for item in group:
                item.conflict_group = conflict_group
            winners = sorted(group, key=_rank_key)
            winner = winners[0]
            winner_target_key = _normalize_text(winner.target_value)
            selected.append(winner)
            conflicts.append(
                {
                    "conflict_type": "same_source_different_target",
                    "source_anchor": winner.source_anchor,
                    "winner_item_id": winner.item_id,
                    "winner_source_type": winner.source_type,
                    "policy": "approved_dictionary_exact_hit_wins"
                    if winner.source_type == "dictionary"
                    else "highest_authority_confidence_wins",
                    "conflicting_items": [item.to_dict() for item in group if item.item_id != winner.item_id],
                }
            )
            for item in group:
                if item.item_id == winner.item_id:
                    continue
                if _normalize_text(item.target_value) == winner_target_key:
                    winner.merged_provenance.append(item.to_dict())
                    item.drop_reason = "duplicate_support_item"
                else:
                    item.drop_reason = "conflict_lower_priority"
                dropped.append(item)
            continue
        for _target_key, duplicates in target_groups.items():
            sorted_duplicates = sorted(duplicates, key=_rank_key)
            winner = sorted_duplicates[0]
            for duplicate in sorted_duplicates[1:]:
                winner.merged_provenance.append(duplicate.to_dict())
                duplicate.drop_reason = "duplicate_support_item"
                dropped.append(duplicate)
                if duplicate.source_type != winner.source_type:
                    conflicts.append(
                        {
                            "conflict_type": "dictionary_memory_duplicate",
                            "source_anchor": winner.source_anchor,
                            "target_value": winner.target_value,
                            "winner_item_id": winner.item_id,
                            "dropped_item_id": duplicate.item_id,
                            "policy": "dictionary_canonical_when_available",
                        }
                    )
            selected.append(winner)

    for inactive in inactive_memory_matches:
        conflicts.append(
            {
                "conflict_type": "related_inactive_or_negative_memory",
                "source_anchor": inactive.get("source_anchor"),
                "related_memory_id": inactive.get("memory_id"),
                "related_status": inactive.get("status"),
                "policy": "inactive_deprecated_harmful_memory_not_rendered",
            }
        )

    selected.sort(key=_rank_key)
    return selected, dropped, {
        "schema_version": "hybrid_prompt_conflict_report_v1",
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }


def _render_block(items: list[SupportItem]) -> str:
    dictionary_lines = [f"- {item.source_anchor} => {item.target_value}" for item in items if item.render_group == "dictionary"]
    memory_lines = [f"- {item.source_anchor} => {item.target_value}" for item in items if item.render_group == "memory"]
    rule_lines = [f"- {item.instruction_text}" for item in items if item.render_group == "rule"]
    if not dictionary_lines and not memory_lines and not rule_lines:
        return ""
    lines = ["Project support for this source:"]
    if dictionary_lines:
        lines.extend(["Dictionary:", *dictionary_lines])
    if memory_lines:
        lines.extend(["Memory:", *memory_lines])
    if rule_lines:
        lines.extend(["Rules:", *rule_lines])
    lines.extend(
        [
            "- Use entries only when the exact Chinese source appears in this chunk.",
            "- Do not apply unrelated entries.",
        ]
    )
    return "\n".join(lines)


def _budget_prune(
    items: list[SupportItem],
    *,
    max_dictionary_entries: int,
    max_memory_items: int,
    max_rule_hints: int,
    max_support_chars: int,
    max_support_lines: int,
) -> tuple[list[SupportItem], list[SupportItem], dict[str, Any], str]:
    selected: list[SupportItem] = []
    dropped: list[SupportItem] = []
    dictionary_count = 0
    memory_count = 0
    rule_count = 0
    for item in items:
        if item.source_type == "dictionary":
            if dictionary_count >= max_dictionary_entries:
                item.drop_reason = "max_dictionary_entries"
                dropped.append(item)
                continue
            dictionary_count += 1
        elif item.source_type == "memory":
            if memory_count >= max_memory_items:
                item.drop_reason = "max_memory_items"
                dropped.append(item)
                continue
            memory_count += 1
        elif item.source_type == "rule":
            if rule_count >= max_rule_hints:
                item.drop_reason = (
                    "covered_by_dictionary"
                    if item.rule_type == "dictionary_priority_guard"
                    and any(selected_item.source_type == "dictionary" and selected_item.source_anchor == item.source_anchor for selected_item in selected)
                    else "max_rule_hints"
                )
                dropped.append(item)
                continue
            rule_count += 1
        selected.append(item)

    def over_budget(current: list[SupportItem]) -> tuple[bool, str, str]:
        block = _render_block(current)
        line_count = len(block.splitlines()) if block else 0
        if len(block) > max_support_chars:
            return True, "max_support_chars", block
        if line_count > max_support_lines:
            return True, "max_support_lines", block
        return False, "", block

    while selected:
        is_over, reason, block_text = over_budget(selected)
        if not is_over:
            break
        prune_order = sorted(
            selected,
            key=lambda item: (
                0 if item.source_type == "memory" and item.memory_type not in {"name"} else
                1 if item.source_type == "rule" else
                2 if item.source_type == "memory" else
                3,
                item.confidence,
                item.authority_rank,
                item.specificity_rank,
            ),
        )
        victim = prune_order[0]
        selected = [item for item in selected if item.item_id != victim.item_id]
        victim.drop_reason = reason
        dropped.append(victim)
    block_text = _render_block(selected)
    budget_report = {
        "schema_version": "hybrid_prompt_budget_report_v1",
        "max_dictionary_entries": max_dictionary_entries,
        "max_memory_items": max_memory_items,
        "max_rule_hints": max_rule_hints,
        "max_support_chars": max_support_chars,
        "max_support_lines": max_support_lines,
        "selected_item_count": len(selected),
        "selected_dictionary_count": sum(1 for item in selected if item.source_type == "dictionary"),
        "selected_memory_count": sum(1 for item in selected if item.source_type == "memory"),
        "selected_rule_count": sum(1 for item in selected if item.source_type == "rule"),
        "dropped_item_count": len(dropped),
        "dropped_rule_count": sum(1 for item in dropped if item.source_type == "rule"),
        "support_chars": len(block_text),
        "support_lines": len(block_text.splitlines()) if block_text else 0,
        "block_rendered": bool(block_text),
        "selected_items": [item.to_dict() for item in selected],
        "dropped_items": [item.to_dict() for item in dropped],
        "config": {
            "max_dictionary_entries": max_dictionary_entries,
            "max_memory_items": max_memory_items,
            "max_rule_hints": max_rule_hints,
            "max_support_chars": max_support_chars,
            "max_support_lines": max_support_lines,
        },
    }
    return selected, dropped, budget_report, block_text


def build_hybrid_prompt_support(
    workspace: Workspace,
    project_slug: str,
    source_text: str,
    *,
    mode: str = "production",
    max_dictionary_entries: int = 8,
    max_memory_items: int = 6,
    use_approved_rules: bool = False,
    max_rule_hints: int = 4,
    max_support_chars: int = 1200,
    max_support_lines: int = 18,
    chapters: set[int] | None = None,
) -> dict[str, Any]:
    if mode not in {"production", "learning"}:
        raise ValueError("mode must be production or learning.")
    if max_dictionary_entries < 0 or max_memory_items < 0 or max_rule_hints < 0:
        raise ValueError("max entry counts cannot be negative.")
    if max_support_chars <= 0 or max_support_lines <= 0:
        raise ValueError("support budgets must be positive.")

    project = get_project_by_slug(workspace, project_slug)
    dictionary_items = _dictionary_items(workspace, project_slug, source_text, chapters=chapters)
    memory_items, excluded_memory, active_memory_count = _memory_items(
        workspace,
        project,
        source_text,
        mode=mode,
        chapters=chapters,
    )
    rule_items: list[SupportItem] = []
    excluded_rules: list[dict[str, Any]] = []
    approved_rule_count = 0
    ineligible_rule_matches: list[dict[str, Any]] = []
    if use_approved_rules:
        rule_items, excluded_rules, approved_rule_count = _rule_items(
            workspace,
            project,
            source_text,
            mode=mode,
            chapters=chapters,
            dictionary_items=dictionary_items,
            memory_items=memory_items,
        )
        ineligible_rule_matches = _load_ineligible_rule_rows(workspace, project_slug, source_text)
    inactive_dictionary = _inactive_dictionary_matches(workspace, project_slug, source_text)
    inactive_memory = _load_inactive_memory_matches(workspace, project, source_text)
    candidates = dictionary_items + memory_items + rule_items
    deduped, dedupe_dropped, conflict_report = _dedupe_and_resolve_conflicts(
        items=candidates,
        source_text=source_text,
        inactive_memory_matches=inactive_memory,
    )
    selected, budget_dropped, budget_report, block_text = _budget_prune(
        deduped,
        max_dictionary_entries=max_dictionary_entries,
        max_memory_items=max_memory_items,
        max_rule_hints=max_rule_hints,
        max_support_chars=max_support_chars,
        max_support_lines=max_support_lines,
    )
    dropped = dedupe_dropped + budget_dropped
    budget_report["dropped_item_count"] = len(dropped)
    budget_report["dropped_rule_count"] = sum(1 for item in dropped if item.source_type == "rule")
    budget_report["dropped_items"] = [item.to_dict() for item in dropped]
    retrieval_report = {
        "schema_version": "hybrid_prompt_retrieval_report_v1",
        "project_slug": project_slug,
        "source_sha256": _sha256_text(source_text),
        "mode": mode,
        "heuristics_version": HYBRID_HEURISTICS_VERSION,
        "dictionary_exact_source_match_required": True,
        "memory_applicability_gates": [
            "status",
            "exact_source_trigger",
            "context",
            "negative_evidence",
            "scope",
        ],
        "rule_applicability_gates": [
            "status",
            "approved_by",
            "project_scope",
            "chapter_scope",
            "trigger",
            "applies_when",
        ],
        "rules_enabled": use_approved_rules,
        "active_dictionary_candidate_count": len(dictionary_items),
        "active_memory_count_considered": active_memory_count,
        "approved_rule_count_considered": approved_rule_count,
        "eligible_memory_count": len(memory_items),
        "eligible_rule_count": len(rule_items),
        "selected_items": [item.to_dict() for item in selected],
        "dropped_items": [item.to_dict() for item in dropped],
        "excluded_dictionary_matches": inactive_dictionary,
        "excluded_memory_rows": excluded_memory,
        "excluded_rule_rows": excluded_rules,
        "pending_rejected_or_inactive_rule_matches": ineligible_rule_matches,
        "inactive_or_negative_memory_matches": inactive_memory,
    }
    support_items = {
        "schema_version": "hybrid_prompt_support_items_v1",
        "created_at": utc_now(),
        "candidate_items": [item.to_dict() for item in candidates],
        "selected_items": [item.to_dict() for item in selected],
        "deduped_items": [item.to_dict() for item in deduped],
        "dropped_items": [item.to_dict() for item in dropped],
    }
    return {
        "schema_version": HYBRID_PROMPT_SCHEMA,
        "project_slug": project_slug,
        "source_sha256": retrieval_report["source_sha256"],
        "mode": mode,
        "heuristics_version": HYBRID_HEURISTICS_VERSION,
        "block_text": block_text,
        "block_rendered": bool(block_text),
        "selected_items": [item.to_dict() for item in selected],
        "selected_dictionary_items": [item.to_dict() for item in selected if item.source_type == "dictionary"],
        "selected_memory_items": [item.to_dict() for item in selected if item.source_type == "memory"],
        "selected_rule_items": [item.to_dict() for item in selected if item.source_type == "rule"],
        "dropped_items": [item.to_dict() for item in dropped],
        "dropped_rule_items": [item.to_dict() for item in dropped if item.source_type == "rule"],
        "deduped_items": [item.to_dict() for item in deduped],
        "conflicts": conflict_report.get("conflicts", []),
        "conflict_count": conflict_report.get("conflict_count", 0),
        "budget_report": budget_report,
        "retrieval_report": retrieval_report,
        "conflict_report": conflict_report,
        "support_items": support_items,
        "active_memory_count_considered": active_memory_count,
        "approved_rule_count_considered": approved_rule_count,
        "active_dictionary_hit_count": len(dictionary_items),
    }


def inspect_hybrid_prompt(
    workspace: Workspace,
    *,
    project_slug: str,
    source_text: str,
    mode: str = "production",
    max_dictionary_entries: int = 8,
    max_memory_items: int = 6,
    use_approved_rules: bool = False,
    max_rule_hints: int = 4,
    max_support_chars: int = 1200,
) -> dict[str, Any]:
    bundle = build_hybrid_prompt_support(
        workspace,
        project_slug,
        source_text,
        mode=mode,
        max_dictionary_entries=max_dictionary_entries,
        max_memory_items=max_memory_items,
        use_approved_rules=use_approved_rules,
        max_rule_hints=max_rule_hints,
        max_support_chars=max_support_chars,
    )
    return {
        "project_slug": project_slug,
        "mode": mode,
        "block_rendered": bundle["block_rendered"],
        "block_text": bundle["block_text"],
        "selected_item_count": len(bundle["selected_items"]),
        "selected_dictionary_count": len(bundle["selected_dictionary_items"]),
        "selected_memory_count": len(bundle["selected_memory_items"]),
        "selected_rule_count": len(bundle["selected_rule_items"]),
        "approved_rule_count_considered": bundle["approved_rule_count_considered"],
        "conflict_count": bundle["conflict_count"],
        "dropped_item_count": len(bundle["dropped_items"]),
        "budget_report": bundle["budget_report"],
        "retrieval_report": bundle["retrieval_report"],
        "conflict_report": bundle["conflict_report"],
        "support_items": bundle["support_items"],
    }
