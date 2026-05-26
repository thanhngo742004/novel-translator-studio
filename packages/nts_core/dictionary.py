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


ENTRY_TYPES = {
    "name",
    "sect_org",
    "realm",
    "system_label",
    "item_artifact",
    "fixed_phrase",
    "forbidden_variant",
}
JSON_FIELDS = (
    "scope_json",
    "confidence_json",
    "provenance_json",
    "artifact_ref_json",
)
ENTRY_JSON_FIELDS = ("forbidden_variants_json", "scope_json", "provenance_json")
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
PUNCT_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)
CHAPTER_MARKER_RE = re.compile(r"^(?:第?[一二三四五六七八九十百千万\d]+[章节回]?|章|节|回)$")
SEPARATOR_RE = re.compile(r"^-{3,}$")
BRACKET_RE = re.compile(r"^【(.+)】$")
DOMAIN_SUFFIXES = ("宗", "门", "峰", "池", "谷", "洞", "山", "城", "宫", "殿")
TITLE_SUFFIXES = ("仙子", "长老", "师兄", "师妹", "前辈", "真人", "老祖")
REALM_TERMS = ("炼气", "练气", "筑基", "金丹", "元婴", "化神", "渡劫")
ITEM_TERMS = ("法器", "法宝", "神剑", "丹谱", "丹药", "灵石", "功法", "法术", "神通", "秘籍")
DOMAIN_TERMS = ("灵根", "修为", "气运", "寿命", "资质", "剑道")
GENERIC_NOISE = {
    "没有",
    "开始",
    "弟子",
    "修炼",
    "仙子",
    "长老",
    "资质",
    "清宗",
    "玉清",
    "雷灵",
    "幽峰",
    "常月",
    "月儿",
}
CANON_TARGETS = {
    "韩绝": "Hàn Tuyệt",
    "玉清宗": "Ngọc Thanh Tông",
    "铁老": "Thiết lão",
    "邢红璇": "Hình Hồng Tuyền",
    "曦璇仙子": "Hi Tuyền tiên tử",
    "玉幽峰": "Ngọc U phong",
    "玉幽殿": "Ngọc U điện",
    "莫复仇": "Mạc Phục Cừu",
    "灵根": "linh căn",
    "灵根资质": "Linh căn tư chất",
    "修为": "tu vi",
    "先天气运": "tiên thiên khí vận",
    "气运": "khí vận",
    "寿命": "thọ mệnh",
    "炼气": "Luyện Khí",
    "炼气境": "Luyện Khí cảnh",
    "筑基": "Trúc Cơ",
    "金丹": "Kim Đan",
    "元婴": "Nguyên Anh",
    "法器": "pháp khí",
    "神通": "thần thông",
    "功法": "công pháp",
    "法术": "pháp thuật",
    "种族": "chủng tộc",
    "姓名": "Tính danh",
    "无": "Không",
    "六道轮回功": "Lục Đạo Luân Hồi Công",
    "雷灵根": "lôi linh căn",
    "绝指神剑": "Tuyệt Chỉ Thần Kiếm",
}
TYPE_PRIORITY = {
    "name": 0,
    "sect_org": 1,
    "realm": 2,
    "system_label": 3,
    "item_artifact": 4,
    "fixed_phrase": 5,
    "forbidden_variant": 6,
}


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
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _safe_id(text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return safe or hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def normalize_source(text: str) -> str:
    return re.sub(r"\s+", "", text.strip().replace("：", ":"))


def normalize_target(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def _dict_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "dictionaries"


def _resolve_run_path(workspace: Workspace, run: str) -> Path:
    candidate = Path(run)
    if candidate.exists():
        return candidate
    root_candidate = _dict_root(workspace) / run
    if root_candidate.exists():
        return root_candidate
    raise ValueError(f"Dictionary run not found: {run}")


def _run_id_from_path(path: Path) -> str:
    return path.name


def _manifest_path(run_dir: Path) -> Path:
    return run_dir / "dict_build_manifest.json"


def _candidate_path(run_dir: Path) -> Path:
    return run_dir / "candidates.jsonl"


def _load_manifest(workspace: Workspace, run: str) -> tuple[Path, dict[str, Any]]:
    run_dir = _resolve_run_path(workspace, run)
    path = _manifest_path(run_dir)
    if not path.exists():
        raise ValueError(f"Dictionary manifest not found: {path}")
    return run_dir, json.loads(path.read_text(encoding="utf-8"))


def _nlp_manifest_path(workspace: Workspace, project_slug: str, source_nlp_cache: Path | None = None) -> Path:
    if source_nlp_cache is not None:
        path = source_nlp_cache
        if path.is_dir():
            path = path / "nlp_cache_manifest.json"
        return path
    return workspace.path / "artifacts" / "nlp" / project_slug / "nlp_cache_manifest.json"


def _chapter_entry(manifest: dict[str, Any], chapter_ref: str) -> dict[str, Any] | None:
    for entry in manifest.get("chapters", []):
        if str(entry.get("chapter_no")) == str(chapter_ref) or entry.get("chapter_id") == chapter_ref:
            return entry
    return None


def _load_active_memory_targets(workspace: Workspace, project_id: str, project_slug: str) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = defaultdict(list)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT source_key, target_text, value_json, rules_json, scope_json, status
            FROM memory_items
            WHERE status = 'active'
            """
        ).fetchall()
    for row in rows:
        item = row_to_dict(row, json_fields=("value_json", "rules_json", "scope_json"))
        scope = item.get("scope_json") or {}
        if scope.get("project_id") not in (None, project_id):
            continue
        if scope.get("project_slug") not in (None, project_slug):
            continue
        source = item.get("source_key") or (item.get("value_json") or {}).get("source_pattern")
        target = (
            item.get("target_text")
            or (item.get("value_json") or {}).get("preferred_target")
            or (item.get("rules_json") or {}).get("preferred_target")
        )
        if source and target and CHINESE_RE.search(str(source)):
            targets[normalize_source(str(source))].append(str(target))
    return targets


def _infer_entry_type(source: str, source_kind: str, candidate: dict[str, Any]) -> str:
    if source_kind == "phrase" and BRACKET_RE.match(source):
        return "system_label" if "：" in source or ":" in source else "fixed_phrase"
    if any(term in source for term in REALM_TERMS):
        return "realm"
    if any(term in source for term in ITEM_TERMS):
        return "item_artifact"
    if any(source.endswith(suffix) for suffix in DOMAIN_SUFFIXES):
        return "sect_org"
    if any(source.endswith(suffix) for suffix in TITLE_SUFFIXES):
        return "name"
    if source_kind == "entity" or candidate.get("entity_type") in {"person", "organization", "place"}:
        entity_type = candidate.get("entity_type")
        if entity_type == "person":
            return "name"
        if entity_type == "organization" or any(source.endswith(suffix) for suffix in DOMAIN_SUFFIXES):
            return "sect_org"
        return "name"
    if any(term in source for term in DOMAIN_TERMS):
        return "realm" if "灵根" in source or "修为" in source else "fixed_phrase"
    return "fixed_phrase"


def _system_panel_target(source: str) -> str | None:
    match = BRACKET_RE.match(source.strip())
    if not match:
        return None
    body = match.group(1)
    if "：" not in body and ":" not in body:
        return None
    separator = "：" if "：" in body else ":"
    label, value = body.split(separator, 1)
    label_target = CANON_TARGETS.get(label.strip())
    if not label_target:
        return None
    value = value.strip()
    value_target = CANON_TARGETS.get(value)
    if value_target is None and value == "无":
        value_target = "Không"
    if value_target:
        return f"【 {label_target}: {value_target} 】"
    return f"【 {label_target}: {value} 】"


def _infer_target(source: str, entry_type: str, memory_targets: dict[str, list[str]]) -> tuple[str, list[str]]:
    normalized = normalize_source(source)
    provenance = []
    if normalized in memory_targets:
        provenance.append("active_memory_target")
        if len(set(memory_targets[normalized])) > 1:
            provenance.append("active_memory_target_conflict")
        return memory_targets[normalized][0], provenance
    panel = _system_panel_target(source)
    if panel:
        provenance.append("system_panel_label_map")
        return panel, provenance
    if source in CANON_TARGETS:
        provenance.append("canonical_seed_map")
        return CANON_TARGETS[source], provenance
    if entry_type == "system_label":
        inner = source.strip("【】")
        label = inner.split("：", 1)[0].split(":", 1)[0].strip()
        if label in CANON_TARGETS:
            provenance.append("system_panel_label_map")
            return CANON_TARGETS[label], provenance
    return "", provenance


def _noise_reasons(source: str, entry_type: str) -> list[str]:
    reasons = []
    stripped = source.strip()
    if not stripped or not CHINESE_RE.search(stripped):
        reasons.append("no_chinese_characters")
    if SEPARATOR_RE.fullmatch(stripped) or PUNCT_ONLY_RE.fullmatch(stripped):
        reasons.append("separator_or_punctuation")
    if CHAPTER_MARKER_RE.fullmatch(stripped):
        reasons.append("chapter_marker")
    if len(stripped) == 1 and stripped not in {"无"}:
        reasons.append("single_character_non_domain")
    if stripped in GENERIC_NOISE and entry_type not in {"system_label", "realm"}:
        reasons.append("generic_common_word")
    if stripped.startswith("【") and not stripped.endswith("】"):
        reasons.append("broken_left_bracket_fragment")
    if stripped.endswith("】") and not stripped.startswith("【"):
        reasons.append("broken_right_bracket_fragment")
    if re.search(r"韩绝[不松】]", stripped):
        reasons.append("fragmented_name_context")
    if "/" in stripped:
        reasons.append("reader_or_page_marker")
    return reasons


def _apply_partial_name_penalty(rows: list[dict[str, Any]]) -> None:
    sources = {row["source_text"] for row in rows}
    full_names = {source for source in sources if len(source) >= 3}
    for row in rows:
        source = row["source_text"]
        if len(source) >= 3:
            continue
        for full in full_names:
            if source != full and source in full and row["entry_type"] in {"name", "fixed_phrase"}:
                row.setdefault("noise_reasons", []).append(f"partial_name_fragment_of:{full}")
                break


def _confidence(
    *,
    occurrence_count: int,
    chapter_spread: int,
    source_kind_counts: Counter[str],
    entry_type: str,
    target_text: str,
    target_provenance: list[str],
    noise_reasons: list[str],
    conflict: bool,
) -> tuple[float, dict[str, Any]]:
    score = 0.35
    score += min(occurrence_count, 12) / 12 * 0.16
    score += min(chapter_spread, 4) / 4 * 0.12
    if source_kind_counts.get("entity"):
        score += 0.08
    if source_kind_counts.get("term"):
        score += 0.06
    if source_kind_counts.get("phrase"):
        score += 0.08
    if entry_type == "system_label":
        score += 0.1
    target_contains_chinese = bool(CHINESE_RE.search(target_text or ""))
    if target_text and not target_contains_chinese:
        score += 0.14
    if target_contains_chinese:
        score -= 0.08
    if "active_memory_target" in target_provenance:
        score += 0.12
    if noise_reasons:
        score -= min(0.45, 0.14 * len(set(noise_reasons)))
    if conflict:
        score -= 0.22
    score = max(0.0, min(1.0, round(score, 3)))
    return score, {
        "occurrence_count": occurrence_count,
        "chapter_spread": chapter_spread,
        "source_kind_counts": dict(source_kind_counts),
        "target_provenance": target_provenance,
        "target_warnings": ["target_contains_untranslated_chinese"] if target_contains_chinese else [],
        "noise_reasons": sorted(set(noise_reasons)),
        "conflict": conflict,
        "group": (
            "high_confidence"
            if score >= 0.8 and target_text and not target_contains_chinese and not noise_reasons and not conflict
            else "needs_review"
            if score >= 0.5 and not noise_reasons
            else "likely_reject"
        ),
    }


def _status_from_confidence(score: float, confidence: dict[str, Any], conflict: bool) -> str:
    if conflict:
        return "needs_human_review"
    if confidence.get("noise_reasons"):
        return "likely_reject"
    return "pending_review" if score >= 0.5 else "likely_reject"


def _candidate_group(row: dict[str, Any]) -> str:
    confidence = row.get("confidence_json") or {}
    if row.get("status") == "needs_human_review":
        return "conflicts"
    if confidence.get("group") == "high_confidence":
        return "high_confidence"
    if confidence.get("group") == "likely_reject" or row.get("status") == "likely_reject":
        return "likely_reject"
    return "needs_review"


def prepare_dictionary_run(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str,
    source_nlp_cache: Path | None = None,
    raw_path: Path | None = None,
    translated_path: Path | None = None,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    nlp_manifest_path = _nlp_manifest_path(workspace, project_slug, source_nlp_cache)
    if not nlp_manifest_path.exists():
        raise ValueError(f"NLP cache manifest not found: {nlp_manifest_path}")
    nlp_manifest = json.loads(nlp_manifest_path.read_text(encoding="utf-8"))
    requested = parse_chapter_range(chapters)
    chunk_rows = []
    for index, chapter_ref in enumerate(requested, start=1):
        entry = _chapter_entry(nlp_manifest, chapter_ref)
        if entry is None:
            raise ValueError(f"NLP cache missing requested chapter: {chapter_ref}")
        if entry.get("degraded") or entry.get("provider") != "ltp_server":
            raise ValueError(f"NLP cache for chapter {chapter_ref} is not a real LTP cache.")
        artifact = Path(entry.get("artifact_path") or "")
        if not artifact.exists():
            raise ValueError(f"NLP chapter artifact missing: {artifact}")
        chunk_rows.append(
            {
                "chunk_index": index,
                "chapter_ref": chapter_ref,
                "chapter_id": entry.get("chapter_id"),
                "chapter_no": entry.get("chapter_no"),
                "artifact_path": str(artifact),
                "source_sha256": entry.get("source_sha256"),
                "sentence_count": entry.get("sentence_count", 0),
                "token_count": entry.get("token_count", 0),
                "status": "pending",
            }
        )

    run_id = f"{project_slug}_dict_{int(time.time() * 1000)}"
    run_dir = _dict_root(workspace) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    source_snapshot = {
        "chapters": requested,
        "nlp_manifest_path": str(nlp_manifest_path),
        "nlp_provider": "ltp_server",
        "raw_path": str(raw_path) if raw_path else None,
        "translated_path": str(translated_path) if translated_path else None,
        "coverage_count": len(chunk_rows),
        "source_hashes": {str(row["chapter_no"]): row.get("source_sha256") for row in chunk_rows},
    }
    scope = {
        "project_id": project["id"],
        "project_slug": project_slug,
        "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
        "chapters": requested,
    }
    manifest = {
        "schema_version": "dictionary_build_manifest_v1",
        "dict_run_id": run_id,
        "project_id": project["id"],
        "project_slug": project_slug,
        "scope": scope,
        "source_snapshot": source_snapshot,
        "artifact_dir": str(run_dir),
        "status": "prepared",
        "created_at": now,
        "updated_at": now,
    }
    chunk_plan = {
        "schema_version": "dictionary_chunk_plan_v1",
        "dict_run_id": run_id,
        "chunk_count": len(chunk_rows),
        "chunks": chunk_rows,
    }
    _json_write(_manifest_path(run_dir), manifest)
    _json_write(run_dir / "chunk_plan.json", chunk_plan)
    _jsonl_write(_candidate_path(run_dir), [])
    _json_write(run_dir / "candidate_conflicts.json", {"schema_version": "candidate_conflicts_v1", "conflicts": []})
    _json_write(run_dir / "candidate_dedup_report.json", {"schema_version": "candidate_dedup_report_v1", "merged_duplicates": []})
    _json_write(run_dir / "approved_entries.json", {"schema_version": "approved_dictionary_entries_v1", "entries": []})
    _json_write(run_dir / "rejected_entries.json", {"schema_version": "rejected_dictionary_entries_v1", "entries": []})
    _jsonl_write(run_dir / "dictionary_audit_log.jsonl", [])
    _text_write(run_dir / "dictionary_build_report.md", f"# Dictionary Build Report\n\nRun `{run_id}` prepared.\n")
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="dict.prepare",
            status="success",
            stage="prepared",
            project_id=project["id"],
            input_data={"project": project_slug, "chapters": chapters},
            result_data={"dict_run_id": run_id, "artifact_dir": str(run_dir)},
        )
        conn.execute(
            """
            INSERT INTO dictionary_runs (
                id, project_id, project_slug, scope_json, source_snapshot_json, artifact_dir,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                project["id"],
                project_slug,
                json_dumps(scope),
                json_dumps(source_snapshot),
                str(run_dir),
                "prepared",
                now,
                now,
            ),
        )
        conn.commit()
    return {
        "task_run_id": task_id,
        "dict_run_id": run_id,
        "run_dir": str(run_dir),
        "manifest_path": str(_manifest_path(run_dir)),
        "chunk_plan_path": str(run_dir / "chunk_plan.json"),
        "chunk_count": len(chunk_rows),
        "chapters": requested,
    }


def _extract_raw_candidates(
    analysis: dict[str, Any],
    *,
    chapter_no: int | None,
    artifact_path: str,
    memory_targets: dict[str, list[str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    chapter_id = analysis.get("meta", {}).get("chapter_id")
    for source_kind, key in (
        ("entity", "entity_candidates"),
        ("term", "term_candidates"),
        ("phrase", "phrase_candidates"),
    ):
        for item in analysis.get("chapter_candidates", {}).get(key, []) or []:
            source = str(item.get("text") or "").strip()
            if not source:
                continue
            entry_type = _infer_entry_type(source, source_kind, item)
            target, target_provenance = _infer_target(source, entry_type, memory_targets)
            rows.append(
                {
                    "source_text": source,
                    "entry_type": entry_type,
                    "target_text": target,
                    "target_provenance": target_provenance,
                    "source_kind": source_kind,
                    "occurrence_count": int(item.get("count") or 1),
                    "chapter_no": chapter_no,
                    "chapter_id": chapter_id,
                    "confidence_hint": item.get("confidence"),
                    "source_excerpt": source,
                    "target_excerpt": target,
                    "artifact_path": artifact_path,
                    "evidence_kind": f"nlp_{source_kind}_candidate",
                    "entity_type": item.get("entity_type"),
                }
            )
    return rows


def _merge_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    project_id: str,
    dict_run_id: str,
    raw_rows: list[dict[str, Any]],
    scope: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    duplicate_rows = []
    for raw in raw_rows:
        normalized_source = normalize_source(raw["source_text"])
        normalized_target = normalize_target(raw.get("target_text"))
        key = (raw["entry_type"], normalized_source, normalized_target)
        candidate = by_key.get(key)
        if candidate is None:
            candidate = {
                "id": _stable_id("dictcand", dict_run_id, *key),
                "dict_run_id": dict_run_id,
                "project_id": project_id,
                "project_slug": project_slug,
                "entry_type": raw["entry_type"],
                "source_text": raw["source_text"],
                "target_text": raw.get("target_text") or "",
                "normalized_source": normalized_source,
                "normalized_target": normalized_target,
                "scope_json": scope,
                "evidence": [],
                "source_kind_counts": Counter(),
                "target_provenance": set(raw.get("target_provenance") or []),
                "noise_reasons": _noise_reasons(raw["source_text"], raw["entry_type"]),
                "conflict_group": None,
            }
            by_key[key] = candidate
        else:
            duplicate_rows.append({"merged_into": candidate["id"], "source_text": raw["source_text"]})
            candidate["target_provenance"].update(raw.get("target_provenance") or [])
        candidate["source_kind_counts"][raw["source_kind"]] += 1
        for _ in range(max(1, int(raw.get("occurrence_count") or 1))):
            pass
        candidate["evidence"].append(
            {
                "chapter_id": raw.get("chapter_id"),
                "chapter_no": raw.get("chapter_no"),
                "source_excerpt": raw.get("source_excerpt") or raw["source_text"],
                "target_excerpt": raw.get("target_excerpt") or "",
                "evidence_kind": raw.get("evidence_kind"),
                "artifact_ref": {"path": raw.get("artifact_path")},
                "occurrence_count": raw.get("occurrence_count") or 1,
            }
        )

    candidates = list(by_key.values())
    _apply_partial_name_penalty(candidates)

    conflict_groups: dict[str, list[dict[str, Any]]] = {}
    source_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for candidate in candidates:
        source_targets[(candidate["entry_type"], candidate["normalized_source"])].add(
            candidate["normalized_target"]
        )
    for candidate in candidates:
        targets = source_targets[(candidate["entry_type"], candidate["normalized_source"])]
        if len(targets) > 1 or "active_memory_target_conflict" in candidate["target_provenance"]:
            group = _stable_id("dictconflict", candidate["entry_type"], candidate["normalized_source"])
            candidate["conflict_group"] = group
            conflict_groups.setdefault(group, []).append(candidate)

    now = utc_now()
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence = candidate["evidence"]
        occurrence_count = sum(int(item.get("occurrence_count") or 1) for item in evidence)
        chapter_spread = len({item.get("chapter_no") or item.get("chapter_id") for item in evidence if item.get("chapter_no") or item.get("chapter_id")})
        target_provenance = sorted(candidate["target_provenance"])
        confidence_score, confidence_json = _confidence(
            occurrence_count=occurrence_count,
            chapter_spread=chapter_spread,
            source_kind_counts=candidate["source_kind_counts"],
            entry_type=candidate["entry_type"],
            target_text=candidate["target_text"],
            target_provenance=target_provenance,
            noise_reasons=sorted(set(candidate["noise_reasons"])),
            conflict=bool(candidate["conflict_group"]),
        )
        provenance_json = {
            "sources": sorted(candidate["source_kind_counts"].keys()),
            "target_provenance": target_provenance,
            "builder": "mvp5f-deterministic",
        }
        artifact_ref_json = {
            "evidence_count": len(evidence),
            "evidence_kinds": sorted({item.get("evidence_kind") for item in evidence if item.get("evidence_kind")}),
        }
        row = {
            "id": candidate["id"],
            "dict_run_id": dict_run_id,
            "project_id": project_id,
            "project_slug": project_slug,
            "entry_type": candidate["entry_type"],
            "source_text": candidate["source_text"],
            "target_text": candidate["target_text"],
            "normalized_source": candidate["normalized_source"],
            "normalized_target": candidate["normalized_target"],
            "scope_json": scope,
            "confidence_score": confidence_score,
            "confidence_json": confidence_json,
            "status": _status_from_confidence(confidence_score, confidence_json, bool(candidate["conflict_group"])),
            "evidence_count": len(evidence),
            "chapter_spread": chapter_spread,
            "provenance_json": provenance_json,
            "artifact_ref_json": artifact_ref_json,
            "conflict_group": candidate["conflict_group"],
            "evidence": evidence,
            "created_at": now,
            "updated_at": now,
            "reviewed_at": None,
        }
        rows.append(row)

    rows.sort(
        key=lambda row: (
            _candidate_group(row) != "high_confidence",
            TYPE_PRIORITY.get(row["entry_type"], 99),
            -float(row["confidence_score"]),
            row["source_text"],
        )
    )
    conflicts = {
        "schema_version": "candidate_conflicts_v1",
        "conflict_count": len(conflict_groups),
        "conflicts": [
            {
                "conflict_group": group,
                "reason": "same_source_different_target",
                "candidate_ids": [item["id"] for item in items],
                "source_texts": sorted({item["source_text"] for item in items}),
                "target_texts": sorted({item["target_text"] for item in items}),
            }
            for group, items in conflict_groups.items()
        ],
        "created_at": now,
    }
    dedup = {
        "schema_version": "candidate_dedup_report_v1",
        "input_rows": len(raw_rows),
        "candidate_count": len(rows),
        "merged_duplicate_count": len(duplicate_rows),
        "merged_duplicates": duplicate_rows[:200],
        "created_at": now,
    }
    return rows, conflicts, dedup


def _candidate_db_row(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["id"],
        row["dict_run_id"],
        row["project_id"],
        row["project_slug"],
        row["entry_type"],
        row["source_text"],
        row["target_text"],
        row["normalized_source"],
        row["normalized_target"],
        json_dumps(row["scope_json"]),
        row["confidence_score"],
        json_dumps(row["confidence_json"]),
        row["status"],
        row["evidence_count"],
        row["chapter_spread"],
        json_dumps(row["provenance_json"]),
        json_dumps(row["artifact_ref_json"]),
        row["conflict_group"],
        row["created_at"],
        row["updated_at"],
        row["reviewed_at"],
    )


def _write_review_artifacts(run_dir: Path, rows: list[dict[str, Any]], *, project_slug: str) -> None:
    review_path = run_dir / "dictionary_review.csv"
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "entry_type",
                "source_text",
                "target_text",
                "confidence_score",
                "group",
                "status",
                "evidence_count",
                "chapter_spread",
                "noise_reasons",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "candidate_id": row["id"],
                    "entry_type": row["entry_type"],
                    "source_text": row["source_text"],
                    "target_text": row["target_text"],
                    "confidence_score": row["confidence_score"],
                    "group": _candidate_group(row),
                    "status": row["status"],
                    "evidence_count": row["evidence_count"],
                    "chapter_spread": row["chapter_spread"],
                    "noise_reasons": ";".join((row.get("confidence_json") or {}).get("noise_reasons") or []),
                    "conflict_group": row.get("conflict_group") or "",
                }
            )
    lines = [
        f"# Dictionary Review: {project_slug}",
        "",
        f"- Candidate count: `{len(rows)}`",
        f"- High confidence: `{sum(1 for row in rows if _candidate_group(row) == 'high_confidence')}`",
        f"- Needs review: `{sum(1 for row in rows if _candidate_group(row) == 'needs_review')}`",
        f"- Likely reject: `{sum(1 for row in rows if _candidate_group(row) == 'likely_reject')}`",
        f"- Conflicts: `{sum(1 for row in rows if _candidate_group(row) == 'conflicts')}`",
        "",
        "| Candidate | Type | Source | Target | Confidence | Group | Evidence |",
        "| --- | --- | --- | --- | ---: | --- | ---: |",
    ]
    for row in rows[:120]:
        lines.append(
            f"| {row['id']} | {row['entry_type']} | {row['source_text']} | {row['target_text']} | "
            f"{row['confidence_score']} | {_candidate_group(row)} | {row['evidence_count']} |"
        )
    _text_write(run_dir / "dictionary_review.md", "\n".join(lines))
    evidence_lines = ["# Dictionary Evidence Pack", ""]
    for row in rows[:80]:
        evidence_lines.extend([f"## {row['id']} - {row['source_text']}", ""])
        for evidence in row.get("evidence", [])[:5]:
            evidence_lines.append(
                f"- Chapter `{evidence.get('chapter_no')}` `{evidence.get('evidence_kind')}`: "
                f"{evidence.get('source_excerpt')} -> {evidence.get('target_excerpt') or '(no target)'}"
            )
        evidence_lines.append("")
    _text_write(run_dir / "evidence_pack.md", "\n".join(evidence_lines))


def _write_human_review_package(run_dir: Path, rows: list[dict[str, Any]], conflicts: dict[str, Any]) -> Path:
    root = run_dir / "human_review"
    root.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for row in rows:
        grouped[_candidate_group(row)].append(row)
    with (root / "candidate_review_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "entry_type",
                "source_text",
                "target_text",
                "confidence_score",
                "group",
                "status",
                "evidence_count",
                "chapter_spread",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "candidate_id": row["id"],
                    "entry_type": row["entry_type"],
                    "source_text": row["source_text"],
                    "target_text": row["target_text"],
                    "confidence_score": row["confidence_score"],
                    "group": _candidate_group(row),
                    "status": row["status"],
                    "evidence_count": row["evidence_count"],
                    "chapter_spread": row["chapter_spread"],
                }
            )
    for filename, group_name in (
        ("high_confidence_candidates.md", "high_confidence"),
        ("needs_review_candidates.md", "needs_review"),
        ("likely_reject_candidates.md", "likely_reject"),
    ):
        lines = [f"# {group_name.replace('_', ' ').title()}", ""]
        for row in grouped[group_name][:100]:
            lines.append(
                f"- `{row['id']}` {row['entry_type']} `{row['source_text']}` -> `{row['target_text']}` "
                f"(confidence {row['confidence_score']})"
            )
        _text_write(root / filename, "\n".join(lines))
    conflict_lines = ["# Conflicts", ""]
    for conflict in conflicts.get("conflicts", []):
        conflict_lines.append(f"- `{conflict['conflict_group']}`: {', '.join(conflict['candidate_ids'])}")
    _text_write(root / "conflicts.md", "\n".join(conflict_lines))
    high_ids = ",".join(row["id"] for row in grouped["high_confidence"])
    _text_write(
        root / "approve_commands.md",
        "\n".join(
            [
                "# Approve Commands",
                "",
                "Approve selected candidates:",
                f"`nts dict approve --project <project> --run {run_dir} --candidate-ids <ids> --json`",
                "",
                "Approve all high-confidence candidates from this run:",
                f"`nts dict approve --project <project> --run {run_dir} --all-high-confidence --json`",
                "",
                f"High-confidence IDs: `{high_ids}`",
            ]
        ),
    )
    _text_write(
        root / "reject_commands.md",
        "\n".join(
            [
                "# Reject Commands",
                "",
                f"`nts dict reject --project <project> --run {run_dir} --candidate-ids <ids> --reason \"<reason>\" --json`",
            ]
        ),
    )
    _text_write(root / "evidence_pack.md", (run_dir / "evidence_pack.md").read_text(encoding="utf-8"))
    _text_write(
        root / "human_review_summary.md",
        "\n".join(
            [
                "# Dictionary Human Review Summary",
                "",
                f"- Candidate count: `{len(rows)}`",
                f"- High confidence: `{len(grouped['high_confidence'])}`",
                f"- Needs review: `{len(grouped['needs_review'])}`",
                f"- Likely reject: `{len(grouped['likely_reject'])}`",
                f"- Conflicts: `{conflicts.get('conflict_count', 0)}`",
                "- No candidates are approved automatically.",
            ]
        ),
    )
    return root


def build_dictionary_run(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    from_chunk: int | None = None,
    to_chunk: int | None = None,
    resume: bool = False,
    skip_existing: bool = False,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    run_dir, manifest = _load_manifest(workspace, run)
    if manifest["project_slug"] != project_slug:
        raise ValueError("Dictionary run project does not match --project.")
    if skip_existing and _candidate_path(run_dir).exists() and _jsonl_read(_candidate_path(run_dir)):
        rows = _jsonl_read(_candidate_path(run_dir))
        return {
            "dict_run_id": manifest["dict_run_id"],
            "run_dir": str(run_dir),
            "status": "skipped_existing",
            "candidate_count": len(rows),
            "candidate_counts_by_type": dict(Counter(row["entry_type"] for row in rows)),
            "human_review_path": str(run_dir / "human_review"),
        }
    project = get_project_by_slug(workspace, project_slug)
    chunk_plan = json.loads((run_dir / "chunk_plan.json").read_text(encoding="utf-8"))
    chunks = chunk_plan["chunks"]
    if from_chunk is not None:
        chunks = [chunk for chunk in chunks if int(chunk["chunk_index"]) >= from_chunk]
    if to_chunk is not None:
        chunks = [chunk for chunk in chunks if int(chunk["chunk_index"]) <= to_chunk]
    memory_targets = _load_active_memory_targets(workspace, project["id"], project_slug)
    raw_rows = []
    for chunk in chunks:
        artifact_path = Path(chunk["artifact_path"])
        analysis = json.loads(artifact_path.read_text(encoding="utf-8"))
        raw_rows.extend(
            _extract_raw_candidates(
                analysis,
                chapter_no=chunk.get("chapter_no"),
                artifact_path=str(artifact_path),
                memory_targets=memory_targets,
            )
        )
        chunk["status"] = "built"
    scope = manifest.get("scope") or {}
    rows, conflicts, dedup = _merge_candidates(
        workspace,
        project_slug=project_slug,
        project_id=project["id"],
        dict_run_id=manifest["dict_run_id"],
        raw_rows=raw_rows,
        scope=scope,
    )
    if max_candidates is not None:
        rows = rows[:max_candidates]
    _jsonl_write(_candidate_path(run_dir), rows)
    _json_write(run_dir / "candidate_conflicts.json", conflicts)
    _json_write(run_dir / "candidate_dedup_report.json", dedup)
    _write_review_artifacts(run_dir, rows, project_slug=project_slug)
    human_review_path = _write_human_review_package(run_dir, rows, conflicts)
    manifest["status"] = "built"
    manifest["updated_at"] = utc_now()
    manifest["candidate_count"] = len(rows)
    _json_write(_manifest_path(run_dir), manifest)
    chunk_plan["chunks"] = chunk_plan["chunks"]
    _json_write(run_dir / "chunk_plan.json", chunk_plan)
    with connection(workspace.db_path) as conn:
        valid_chapter_ids = {
            row["id"]
            for row in conn.execute("SELECT id FROM chapters WHERE project_id = ?", (project["id"],)).fetchall()
        }
        conn.execute("DELETE FROM dictionary_candidate_evidence WHERE candidate_id IN (SELECT id FROM dictionary_candidates WHERE dict_run_id = ?)", (manifest["dict_run_id"],))
        conn.execute("DELETE FROM dictionary_candidates WHERE dict_run_id = ?", (manifest["dict_run_id"],))
        for row in rows:
            conn.execute(
                """
                INSERT INTO dictionary_candidates (
                    id, dict_run_id, project_id, project_slug, entry_type, source_text, target_text,
                    normalized_source, normalized_target, scope_json, confidence_score, confidence_json,
                    status, evidence_count, chapter_spread, provenance_json, artifact_ref_json,
                    conflict_group, created_at, updated_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _candidate_db_row(row),
            )
            for evidence in row.get("evidence", []):
                conn.execute(
                    """
                    INSERT INTO dictionary_candidate_evidence (
                        id, candidate_id, chapter_id, chapter_no, segment_id, source_excerpt,
                        target_excerpt, evidence_kind, artifact_ref_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            new_id("dictev"),
                            row["id"],
                            evidence.get("chapter_id") if evidence.get("chapter_id") in valid_chapter_ids else None,
                            evidence.get("chapter_no"),
                        evidence.get("segment_id"),
                        evidence.get("source_excerpt"),
                        evidence.get("target_excerpt"),
                        evidence.get("evidence_kind"),
                        json_dumps(evidence.get("artifact_ref") or {}),
                        utc_now(),
                    ),
                )
        conn.execute(
            "UPDATE dictionary_runs SET status = ?, updated_at = ? WHERE id = ?",
            ("built", utc_now(), manifest["dict_run_id"]),
        )
        task_id = insert_task_run(
            conn,
            task_type="dict.build",
            status="success",
            stage="built",
            project_id=project["id"],
            input_data={"project": project_slug, "run": str(run_dir), "resume": resume},
            result_data={"candidate_count": len(rows), "human_review_path": str(human_review_path)},
        )
        conn.commit()
    counts = Counter(row["entry_type"] for row in rows)
    groups = Counter(_candidate_group(row) for row in rows)
    return {
        "task_run_id": task_id,
        "dict_run_id": manifest["dict_run_id"],
        "run_dir": str(run_dir),
        "candidate_count": len(rows),
        "candidate_counts_by_type": dict(counts),
        "high_confidence_count": groups.get("high_confidence", 0),
        "needs_review_count": groups.get("needs_review", 0),
        "likely_reject_count": groups.get("likely_reject", 0),
        "conflict_count": conflicts.get("conflict_count", 0),
        "candidates_path": str(_candidate_path(run_dir)),
        "dictionary_review_path": str(run_dir / "dictionary_review.md"),
        "human_review_path": str(human_review_path),
        "top_candidates": [
            {
                "candidate_id": row["id"],
                "entry_type": row["entry_type"],
                "source_text": row["source_text"],
                "target_text": row["target_text"],
                "confidence_score": row["confidence_score"],
                "group": _candidate_group(row),
            }
            for row in rows[:10]
        ],
    }


def _load_candidates_for_run(workspace: Workspace, run: str) -> tuple[Path, dict[str, dict[str, Any]]]:
    run_dir, _manifest = _load_manifest(workspace, run)
    rows = _jsonl_read(_candidate_path(run_dir))
    return run_dir, {row["id"]: row for row in rows}


def _update_candidate_artifacts(run_dir: Path, rows_by_id: dict[str, dict[str, Any]]) -> None:
    rows = list(rows_by_id.values())
    rows.sort(key=lambda row: (TYPE_PRIORITY.get(row["entry_type"], 99), -float(row["confidence_score"]), row["source_text"]))
    _jsonl_write(_candidate_path(run_dir), rows)


def _append_audit(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / "dictionary_audit_log.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def approve_dictionary_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    candidate_ids: str | None = None,
    all_high_confidence: bool = False,
    reviewer: str = "human",
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    if candidate_ids:
        selected_ids = [item.strip() for item in candidate_ids.split(",") if item.strip()]
    elif all_high_confidence:
        selected_ids = [
            row["id"]
            for row in rows_by_id.values()
            if _candidate_group(row) == "high_confidence" and row.get("status") != "needs_human_review"
        ]
    else:
        raise ValueError("Use --candidate-ids or --all-high-confidence.")
    missing = [candidate_id for candidate_id in selected_ids if candidate_id not in rows_by_id]
    if missing:
        raise ValueError(f"Dictionary candidate(s) not found: {', '.join(missing)}")
    now = utc_now()
    approved_entries = []
    with connection(workspace.db_path) as conn:
        for candidate_id in selected_ids:
            row = rows_by_id[candidate_id]
            if not row.get("target_text"):
                raise ValueError(f"Candidate has no target_text and cannot be approved: {candidate_id}")
            entry_id = _stable_id("dictentry", project_slug, row["entry_type"], row["normalized_source"], row["normalized_target"])
            forbidden = (row.get("confidence_json") or {}).get("forbidden_variants") or []
            conn.execute(
                """
                INSERT OR REPLACE INTO project_dictionary_entries (
                    id, project_id, project_slug, entry_type, source_text, target_text,
                    normalized_source, normalized_target, forbidden_variants_json, scope_json,
                    confidence_score, provenance_json, status, approved_by, approved_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    project["id"],
                    project_slug,
                    row["entry_type"],
                    row["source_text"],
                    row["target_text"],
                    row["normalized_source"],
                    row["normalized_target"],
                    json_dumps(forbidden),
                    json_dumps(row.get("scope_json") or {}),
                    row["confidence_score"],
                    json_dumps(row.get("provenance_json") or {}),
                    "active",
                    reviewer,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE dictionary_candidates SET status = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                ("approved_by_human", now, now, candidate_id),
            )
            audit_id = new_id("dictaudit")
            audit_payload = {"candidate_id": candidate_id, "entry_id": entry_id, "reviewer": reviewer}
            conn.execute(
                """
                INSERT INTO dictionary_audit_logs (
                    id, dictionary_entry_id, candidate_id, action, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (audit_id, entry_id, candidate_id, "approve", json_dumps(audit_payload), now),
            )
            row["status"] = "approved_by_human"
            row["reviewed_at"] = now
            approved_entries.append(
                {
                    "entry_id": entry_id,
                    "candidate_id": candidate_id,
                    "entry_type": row["entry_type"],
                    "source_text": row["source_text"],
                    "target_text": row["target_text"],
                    "confidence_score": row["confidence_score"],
                    "status": "active",
                }
            )
            _append_audit(run_dir, {"action": "approve", "created_at": now, **audit_payload})
        task_id = insert_task_run(
            conn,
            task_type="dict.approve",
            status="success",
            stage="approved",
            project_id=project["id"],
            input_data={"project": project_slug, "run": str(run_dir), "candidate_ids": selected_ids},
            result_data={"approved_candidate_ids": selected_ids},
        )
        conn.commit()
    _update_candidate_artifacts(run_dir, rows_by_id)
    _json_write(
        run_dir / "approved_entries.json",
        {"schema_version": "approved_dictionary_entries_v1", "entries": approved_entries, "updated_at": now},
    )
    return {
        "task_run_id": task_id,
        "dict_run_id": _run_id_from_path(run_dir),
        "run_dir": str(run_dir),
        "updated_candidate_ids": selected_ids,
        "approved_entries": approved_entries,
        "approved_entries_path": str(run_dir / "approved_entries.json"),
    }


def reject_dictionary_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    candidate_ids: str,
    reason: str,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    selected_ids = [item.strip() for item in candidate_ids.split(",") if item.strip()]
    missing = [candidate_id for candidate_id in selected_ids if candidate_id not in rows_by_id]
    if missing:
        raise ValueError(f"Dictionary candidate(s) not found: {', '.join(missing)}")
    now = utc_now()
    rejected = []
    with connection(workspace.db_path) as conn:
        for candidate_id in selected_ids:
            row = rows_by_id[candidate_id]
            conn.execute(
                "UPDATE dictionary_candidates SET status = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
                ("rejected", now, now, candidate_id),
            )
            payload = {"candidate_id": candidate_id, "reason": reason}
            conn.execute(
                """
                INSERT INTO dictionary_audit_logs (
                    id, dictionary_entry_id, candidate_id, action, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_id("dictaudit"), None, candidate_id, "reject", json_dumps(payload), now),
            )
            row["status"] = "rejected"
            row["reviewed_at"] = now
            rejected.append({"candidate_id": candidate_id, "source_text": row["source_text"], "reason": reason})
            _append_audit(run_dir, {"action": "reject", "created_at": now, **payload})
        task_id = insert_task_run(
            conn,
            task_type="dict.reject",
            status="success",
            stage="rejected",
            project_id=project["id"],
            input_data={"project": project_slug, "run": str(run_dir), "candidate_ids": selected_ids},
            result_data={"rejected_candidate_ids": selected_ids},
        )
        conn.commit()
    _update_candidate_artifacts(run_dir, rows_by_id)
    _json_write(
        run_dir / "rejected_entries.json",
        {"schema_version": "rejected_dictionary_entries_v1", "entries": rejected, "updated_at": now},
    )
    return {
        "task_run_id": task_id,
        "dict_run_id": _run_id_from_path(run_dir),
        "run_dir": str(run_dir),
        "updated_candidate_ids": selected_ids,
        "rejected_entries_path": str(run_dir / "rejected_entries.json"),
    }


def _entry_row_to_dict(row: Any) -> dict[str, Any]:
    return row_to_dict(row, json_fields=ENTRY_JSON_FIELDS)


def load_project_dictionary(workspace: Workspace, project_slug: str) -> list[dict[str, Any]]:
    initialize_database(workspace.db_path)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, project_id, project_slug, entry_type, source_text, target_text,
                   normalized_source, normalized_target, forbidden_variants_json, scope_json,
                   confidence_score, provenance_json, status, approved_by, approved_at,
                   created_at, updated_at
            FROM project_dictionary_entries
            WHERE project_slug = ? AND status = 'active'
            ORDER BY entry_type ASC, confidence_score DESC, source_text ASC
            """,
            (project_slug,),
        ).fetchall()
    return [_entry_row_to_dict(row) for row in rows]


def retrieve_dictionary_hits(
    workspace: Workspace,
    project_slug: str,
    source_text: str,
    *,
    max_entries: int = 8,
) -> list[dict[str, Any]]:
    entries = load_project_dictionary(workspace, project_slug)
    hits = [
        entry
        for entry in entries
        if entry.get("source_text") and str(entry["source_text"]) in source_text
    ]
    hits.sort(
        key=lambda entry: (
            -len(str(entry.get("source_text") or "")),
            TYPE_PRIORITY.get(str(entry.get("entry_type")), 99),
            -float(entry.get("confidence_score") or 0),
        )
    )
    return hits[:max_entries]


def get_forbidden_variants(workspace: Workspace, project_slug: str, source_text: str) -> list[str]:
    variants = []
    for entry in retrieve_dictionary_hits(workspace, project_slug, source_text, max_entries=50):
        variants.extend(entry.get("forbidden_variants_json") or [])
    return sorted(set(str(item) for item in variants if item))


def summarize_dictionary_for_prompt(workspace: Workspace, project_slug: str, source_text: str) -> dict[str, Any]:
    hits = retrieve_dictionary_hits(workspace, project_slug, source_text, max_entries=8)
    return {
        "entries": [
            {
                "source_text": hit["source_text"],
                "target_text": hit["target_text"],
                "entry_type": hit["entry_type"],
            }
            for hit in hits
        ],
        "forbidden_variants": get_forbidden_variants(workspace, project_slug, source_text),
        "read_only": True,
    }


def export_project_dictionary(
    workspace: Workspace,
    *,
    project_slug: str,
    out: Path | None = None,
) -> dict[str, Any]:
    entries = load_project_dictionary(workspace, project_slug)
    payload = {
        "schema_version": "project_dictionary_export_v1",
        "project_slug": project_slug,
        "exported_at": utc_now(),
        "entry_count": len(entries),
        "entries": [
            {
                "entry_id": entry["id"],
                "entry_type": entry["entry_type"],
                "source_text": entry["source_text"],
                "target_text": entry["target_text"],
                "forbidden_variants": entry.get("forbidden_variants_json") or [],
                "scope": entry.get("scope_json") or {},
                "confidence": entry.get("confidence_score"),
                "provenance": entry.get("provenance_json") or {},
                "status": entry.get("status"),
            }
            for entry in entries
        ],
    }
    output_path = out or (workspace.path / "artifacts" / "dictionaries" / f"{project_slug}_approved_dictionary.json")
    _json_write(output_path, payload)
    return {"project_slug": project_slug, "entry_count": len(entries), "output_path": str(output_path), "entries": payload["entries"]}


def review_dictionary_run(
    workspace: Workspace,
    *,
    project_slug: str,
    run: str,
    min_confidence: float | None = None,
    entry_type: str | None = None,
) -> dict[str, Any]:
    run_dir, rows_by_id = _load_candidates_for_run(workspace, run)
    rows = [
        row
        for row in rows_by_id.values()
        if (min_confidence is None or float(row.get("confidence_score") or 0) >= min_confidence)
        and (entry_type is None or row.get("entry_type") == entry_type)
    ]
    conflicts = json.loads((run_dir / "candidate_conflicts.json").read_text(encoding="utf-8")) if (run_dir / "candidate_conflicts.json").exists() else {"conflict_count": 0, "conflicts": []}
    _write_review_artifacts(run_dir, rows, project_slug=project_slug)
    human_review_path = _write_human_review_package(run_dir, rows, conflicts)
    groups = Counter(_candidate_group(row) for row in rows)
    return {
        "dict_run_id": _run_id_from_path(run_dir),
        "run_dir": str(run_dir),
        "candidate_count": len(rows),
        "high_confidence_count": groups.get("high_confidence", 0),
        "needs_review_count": groups.get("needs_review", 0),
        "likely_reject_count": groups.get("likely_reject", 0),
        "conflict_count": conflicts.get("conflict_count", 0),
        "review_path": str(run_dir / "dictionary_review.md"),
        "review_csv_path": str(run_dir / "dictionary_review.csv"),
        "human_review_path": str(human_review_path),
    }


def dictionary_status(workspace: Workspace, *, project_slug: str) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    with connection(workspace.db_path) as conn:
        approved = conn.execute(
            "SELECT COUNT(*) FROM project_dictionary_entries WHERE project_slug = ? AND status = 'active'",
            (project_slug,),
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM dictionary_candidates WHERE project_slug = ? AND status IN ('pending_review', 'needs_human_review', 'likely_reject')",
            (project_slug,),
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM dictionary_candidates WHERE project_slug = ? AND status = 'rejected'",
            (project_slug,),
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT id, artifact_dir, status, created_at FROM dictionary_runs WHERE project_slug = ? ORDER BY created_at DESC LIMIT 1",
            (project_slug,),
        ).fetchone()
        conflicts = conn.execute(
            "SELECT COUNT(DISTINCT conflict_group) FROM dictionary_candidates WHERE project_slug = ? AND conflict_group IS NOT NULL",
            (project_slug,),
        ).fetchone()[0]
    return {
        "project_slug": project_slug,
        "approved_entry_count": int(approved),
        "pending_candidate_count": int(pending),
        "rejected_candidate_count": int(rejected),
        "conflict_count": int(conflicts),
        "last_run": row_to_dict(last_run) if last_run else None,
    }


def inspect_dictionary_hits(
    workspace: Workspace,
    *,
    project_slug: str,
    source_text: str,
    chapter: str | None = None,
) -> dict[str, Any]:
    hits = retrieve_dictionary_hits(workspace, project_slug, source_text)
    return {
        "project_slug": project_slug,
        "chapter": chapter,
        "source_text": source_text,
        "hit_count": len(hits),
        "hits": hits,
        "forbidden_variants": get_forbidden_variants(workspace, project_slug, source_text),
    }
