from __future__ import annotations

import csv
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

from nts_core.dictionary import build_dictionary_prompt_support, load_project_dictionary
from nts_core.hybrid_prompt import build_hybrid_prompt_support
from nts_core.eval_harness import (
    ALIGNMENT_QUALITY_THRESHOLD,
    apply_translation_units,
    align_blocks_monotonic,
    build_alignment_blocks,
    build_alignment_candidates,
    chapter_number,
    compare_translation,
    detect_truncated_vietnamese,
    extract_alignment_anchors,
    extract_epub_chapters,
    extract_raw_chapters,
    json_dumps,
    prepare_parallel,
    read_json,
    sample_from_alignment_candidate,
    sha256_text,
    translate_samples,
    translation_units_report,
    unit_alignment_report,
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
from nts_core.rules import load_all_project_rules, load_approved_rules
from nts_core.stable_prompts import StablePromptRecord, load_approved_stable_prompt
from nts_storage.database import connection, insert_task_run, row_to_dict, utc_now
from nts_storage.workspace import Workspace


DEFAULT_APPROVED_MEMORY_VALIDATION_CHAPTERS = "1-10"
DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS = 2
DEFAULT_APPROVED_MEMORY_MAX_CHAPTERS = 10
MAX_SOURCE_CHARS_PER_VALIDATION_UNIT = 700
MAX_REFERENCE_CHARS_PER_VALIDATION_UNIT = 900
MIN_REFERENCE_SOURCE_RATIO = 1.85
MAX_REFERENCE_SOURCE_RATIO = 4.6
MIN_REFERENCE_CHARS_FOR_MEDIUM_SOURCE = 180
MAX_BLOCKS_PER_SAFE_CANDIDATE = 6
DATASET_DIAGNOSTIC_FILES = (
    "alignment_report.json",
    "chapter_alignment_report.json",
    "block_alignment_report.json",
    "alignment_candidates.json",
    "selected_samples.json",
    "approved_memory_validation_sample_selection.json",
    "translation_units.json",
    "unit_alignment_report.json",
    "unit_candidate_ranking.json",
    "unit_candidate_ranking.md",
    "selected_validation_units.json",
    "selected_validation_units.md",
    "excluded_validation_candidates.json",
    "chapter_8_window_ablation.json",
    "chapter_8_window_ablation.md",
    "chapter_10_window_ablation.json",
    "chapter_10_window_ablation.md",
    "chapter_10_rebuilt_alignment.json",
    "chapter_10_rebuilt_alignment.md",
    "chapter_10_rebuilt_unit_candidate_ranking.json",
    "chapter_10_rebuilt_unit_candidate_ranking.md",
    "chapter_10_selected_safe_unit.json",
)
DEFAULT_CHAPTER_MATCH_WINDOW = 3
STRONG_CHAPTER_MATCH_THRESHOLD = 0.75
TENTATIVE_CHAPTER_MATCH_THRESHOLD = 0.60
MVP5D6_ORIGINAL_APPROVED_MEMORY_IDS = {
    "memory_5190e5ee3320419992bc8833ffd45fcc",
    "memory_ee0e5afb1b8f4180b9d7b1907de1385c",
    "memory_9ae91c19082341ae85626f5f74e2cf3f",
    "memory_bc32c4066a624090918af5e0f89ddda7",
    "memory_160c0cae68964045bdc25b691f469bc4",
}
MVP5D6_APPROVED_MINED_CANDIDATE_IDS = {
    "candidate_a4d0439dc85a16a2589487f8",
    "candidate_f46deb2e55950a845fcbe4f8",
    "candidate_c8e5a720bf1b24d0d2d2f69d",
    "candidate_9ac6ad9ee889e2236a0cd82d",
}


def approved_memory_validation_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "approved_memory_validation"


def alignment_diagnostics_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "alignment_diagnostics"


def new_alignment_diagnostics_run_dir(workspace: Workspace, project_slug: str) -> Path:
    run_id = f"{project_slug}_align_{int(time.time() * 1000)}"
    run_dir = alignment_diagnostics_root(workspace) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def validation_candidate_exclusions_path(workspace: Workspace | Path) -> Path:
    workspace_path = workspace.path if isinstance(workspace, Workspace) else Path(workspace)
    return workspace_path / "artifacts" / "approved_memory_validation" / "validation_candidate_exclusions.json"


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


def _copy_dataset_diagnostics(eval_run: Path, run_dir: Path) -> None:
    for name in DATASET_DIAGNOSTIC_FILES:
        _copy_file_if_exists(eval_run / name, run_dir / name)


TITLE_TOKEN_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "qi_yun": {"zh": ("气运",), "vi": ("khi van", "khí vận")},
    "liu_dao_ling_ti": {"zh": ("六道灵体",), "vi": ("sau dao linh the", "sáu đạo linh thể")},
    "dingji_linggen": {"zh": ("顶级灵根",), "vi": ("dinh cap linh can", "đỉnh cấp linh căn")},
    "lianqi": {"zh": ("炼气",), "vi": ("luyen khi", "luyện khí")},
    "meili": {"zh": ("魅力",), "vi": ("mi luc", "mị lực")},
    "mo_xiu": {"zh": ("魔修",), "vi": ("ma tu",)},
    "jianfa": {"zh": ("剑法",), "vi": ("kiem phap", "kiếm pháp")},
    "juezhi_shenjian": {"zh": ("绝指神剑",), "vi": ("tuyet chi than kiem", "tuyệt chỉ thần kiếm")},
    "zhuji": {"zh": ("筑基",), "vi": ("truc co", "trúc cơ")},
    "yuqing_zong": {"zh": ("玉清宗",), "vi": ("ngoc thanh tong", "ngọc thanh tông")},
    "huo_linggen": {"zh": ("火灵根",), "vi": ("hoa linh can", "hỏa linh căn", "hac hoa linh can", "hắc hỏa linh căn")},
    "xi_xuan": {"zh": ("曦璇",), "vi": ("hi tuyen",)},
    "hao_gan": {"zh": ("好感", "好感度"), "vi": ("hao cam", "hảo cảm", "thien cam", "thiện cảm")},
    "yuan_ying": {"zh": ("元婴",), "vi": ("nguyen anh", "nguyên anh")},
    "lei_lingqi": {"zh": ("雷灵气",), "vi": ("loi linh khi", "lôi linh khí")},
    "bei_can": {"zh": ("悲惨",), "vi": ("bi tham", "bi thảm")},
    "hao_you": {"zh": ("好友",), "vi": ("ban tot", "bạn tốt")},
    "da_shixiong": {"zh": ("大师兄",), "vi": ("dai su huynh", "đại sư huynh")},
    "mo_fuchou": {"zh": ("莫复仇",), "vi": ("mac phuc cuu", "mạc phục cừu")},
    "shu_yao": {"zh": ("树妖",), "vi": ("thu yeu", "thụ yêu")},
    "ji_yuan": {"zh": ("机缘",), "vi": ("co duyen", "cơ duyên")},
}


def _fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    without_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", without_marks.lower()).strip()


def _title_tokens(title: str | None, *, lang: str) -> set[str]:
    if not title:
        return set()
    haystack = title if lang == "zh" else _fold_text(title)
    tokens: set[str] = set()
    for token, aliases in TITLE_TOKEN_ALIASES.items():
        for alias in aliases.get(lang, ()):
            needle = alias if lang == "zh" else _fold_text(alias)
            if needle and needle in haystack:
                tokens.add(token)
                break
    return tokens


def _target_title_groups(target_chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for chapter in target_chapters:
        title = str(chapter.get("title") or "")
        folded = _fold_text(title)
        base = re.sub(r"^chuong\s+\d+\s*:\s*", "", folded)
        base = re.sub(r"\(\s*\d+\s*\)\s*$", "", base).strip()
        if groups and groups[-1]["base_title"] == base:
            groups[-1]["chapter_ids"].append(int(chapter["chapter_id"]))
            groups[-1]["titles"].append(title)
            groups[-1]["tokens"].update(_title_tokens(title, lang="vi"))
            continue
        groups.append(
            {
                "group_index": len(groups),
                "base_title": base,
                "chapter_ids": [int(chapter["chapter_id"])],
                "titles": [title],
                "tokens": _title_tokens(title, lang="vi"),
            }
        )
    return groups


def _chapter_numbers(text: str) -> list[str]:
    return sorted(set(re.findall(r"\d+", text or "")))


def _chapter_index_row(chapter: dict[str, Any], *, lang: str) -> dict[str, Any]:
    text = str(chapter.get("text") or "")
    title = str(chapter.get("title") or "")
    return {
        "chapter_id": int(chapter.get("chapter_id") or 0),
        "chapter_number": chapter_number(title),
        "title": title,
        "normalized_title": _fold_text(title),
        "title_tokens": sorted(_title_tokens(title, lang=lang)),
        "source_length": len(text),
        "first_300_chars": text[:300],
        "last_300_chars": text[-300:],
        "anchors": extract_alignment_anchors(text[:1200] + text[-1200:], lang=lang),
        "head_anchors": extract_alignment_anchors(text[:1200], lang=lang),
        "tail_anchors": extract_alignment_anchors(text[-1200:], lang=lang),
        "numbers": _chapter_numbers(title + "\n" + text[:1200] + "\n" + text[-1200:]),
        "panel_marker_count": text.count("【"),
        "confidence_signals": {
            "has_title_number": chapter_number(title) is not None,
            "anchor_count": len(extract_alignment_anchors(text[:1200] + text[-1200:], lang=lang)),
            "title_token_count": len(_title_tokens(title, lang=lang)),
        },
    }


def _target_group_text(group: dict[str, Any], target_by_id: dict[int, dict[str, Any]]) -> str:
    return "\n\n".join(
        str(target_by_id[chapter_id]["text"])
        for chapter_id in group.get("chapter_ids", [])
        if chapter_id in target_by_id
    )


def _target_group_title(group: dict[str, Any], target_by_id: dict[int, dict[str, Any]]) -> str:
    titles = [
        str(target_by_id[chapter_id].get("title") or "")
        for chapter_id in group.get("chapter_ids", [])
        if chapter_id in target_by_id
    ]
    return " / ".join(title for title in titles if title)


def _target_group_summary(
    group: dict[str, Any],
    target_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    text = _target_group_text(group, target_by_id)
    title = _target_group_title(group, target_by_id)
    return {
        "group_index": int(group.get("group_index") or 0),
        "chapter_ids": list(group.get("chapter_ids") or []),
        "title": title,
        "normalized_title": _fold_text(title),
        "title_tokens": sorted(set(group.get("tokens") or [])),
        "source_length": len(text),
        "first_300_chars": text[:300],
        "last_300_chars": text[-300:],
        "anchors": extract_alignment_anchors(text[:2200] + text[-2200:], lang="vi"),
        "head_anchors": extract_alignment_anchors(text[:2200], lang="vi"),
        "tail_anchors": extract_alignment_anchors(text[-2200:], lang="vi"),
        "numbers": _chapter_numbers(title + "\n" + text[:2200] + "\n" + text[-2200:]),
        "panel_marker_count": text.count("【"),
    }


def _overlap_ratio(source: set[str], target: set[str]) -> float:
    if not source:
        return 0.0
    return len(source & target) / max(len(source), 1)


def _chapter_match_score(
    raw_summary: dict[str, Any],
    target_summary: dict[str, Any],
    *,
    raw_index: int,
    expected_group_index: int,
) -> dict[str, Any]:
    source_title_tokens = set(raw_summary.get("title_tokens") or [])
    target_title_tokens = set(target_summary.get("title_tokens") or [])
    source_anchors = set(raw_summary.get("anchors") or [])
    target_anchors = set(target_summary.get("anchors") or [])
    source_head = set(raw_summary.get("head_anchors") or [])
    target_head = set(target_summary.get("head_anchors") or [])
    source_tail = set(raw_summary.get("tail_anchors") or [])
    target_tail = set(target_summary.get("tail_anchors") or [])
    title_score = _overlap_ratio(source_title_tokens, target_title_tokens)
    anchor_score = _overlap_ratio(source_anchors, target_anchors)
    head_score = _overlap_ratio(source_head, target_head)
    tail_score = _overlap_ratio(source_tail, target_tail)
    target_len = int(target_summary.get("source_length") or 0)
    raw_len = int(raw_summary.get("source_length") or 0)
    length_ratio = target_len / max(raw_len, 1)
    length_score = max(0.0, 1 - abs(length_ratio - 2.6) / 3.2)
    position_score = max(
        0.0,
        1 - abs(int(target_summary.get("group_index") or 0) - expected_group_index) / max(raw_index + 3, 1),
    )
    raw_number = raw_summary.get("chapter_number")
    target_numbers = [
        int(number)
        for number in target_summary.get("numbers", [])
        if str(number).isdigit()
    ]
    number_score = 1.0 if raw_number and raw_number in target_numbers else 0.0
    score = round(
        min(
            1.0,
            0.25 * title_score
            + 0.28 * anchor_score
            + 0.13 * head_score
            + 0.14 * tail_score
            + 0.10 * length_score
            + 0.07 * position_score
            + 0.03 * number_score,
        ),
        3,
    )
    warnings: list[str] = []
    if title_score == 0 and source_title_tokens:
        warnings.append("title_token_mismatch")
    if anchor_score < 0.35:
        warnings.append("low_anchor_overlap")
    if length_ratio < 1.0 or length_ratio > 6.5:
        warnings.append(f"length_ratio_outlier:{length_ratio:.3f}")
    return {
        "confidence": score,
        "title_similarity": round(title_score, 3),
        "anchor_overlap": round(anchor_score, 3),
        "first_anchor_overlap": round(head_score, 3),
        "last_anchor_overlap": round(tail_score, 3),
        "length_ratio": round(length_ratio, 3),
        "position_score": round(position_score, 3),
        "number_score": round(number_score, 3),
        "shared_title_tokens": sorted(source_title_tokens & target_title_tokens),
        "shared_anchors": sorted(source_anchors & target_anchors),
        "warnings": warnings,
    }


def _adjacent_split_decision(
    *,
    raw_chapters: list[dict[str, Any]],
    raw_index: int,
    target_group: dict[str, Any],
    target_groups: list[dict[str, Any]],
    target_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    group_index = int(target_group.get("group_index") or 0)
    next_group = target_groups[group_index + 1] if group_index + 1 < len(target_groups) else None
    if not next_group:
        return None
    raw_text = str(raw_chapters[raw_index].get("text") or "")
    raw_tail = set(extract_alignment_anchors(raw_text[-1200:], lang="zh"))
    current_tail_text = "\n\n".join(
        str(target_by_id[chapter_id]["text"])
        for chapter_id in target_group.get("chapter_ids", [])
        if chapter_id in target_by_id
    )[-2200:]
    next_chapter_id = int((next_group.get("chapter_ids") or [0])[0])
    next_text = str(target_by_id.get(next_chapter_id, {}).get("text") or "")
    current_tail = set(extract_alignment_anchors(current_tail_text, lang="vi"))
    next_head = set(extract_alignment_anchors(next_text[:2200], lang="vi"))
    next_raw_head: set[str] = set()
    if raw_index + 1 < len(raw_chapters):
        next_raw_head = set(
            extract_alignment_anchors(str(raw_chapters[raw_index + 1].get("text") or "")[:1200], lang="zh")
        )
    current_overlap = len(raw_tail & current_tail)
    next_overlap = len(raw_tail & next_head)
    next_raw_overlap = len(next_raw_head & next_head)
    should_join = (
        next_overlap >= 3
        and next_overlap >= current_overlap + 2
        and next_overlap > next_raw_overlap + 1
    )
    return {
        "should_join": should_join,
        "next_chapter_id": next_chapter_id,
        "current_tail_overlap": current_overlap,
        "next_head_overlap": next_overlap,
        "next_raw_head_overlap": next_raw_overlap,
        "shared_tail_next_head_anchors": sorted(raw_tail & next_head),
        "reason": "raw_tail_anchors_continue_in_next_translated_section" if should_join else "no_adjacent_split_join_needed",
    }


def _fallback_target_group(
    raw_summary: dict[str, Any],
    groups: list[dict[str, Any]],
    target_by_id: dict[int, dict[str, Any]],
    *,
    raw_index: int,
    expected_group_index: int,
    match_window: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    start = max(0, expected_group_index - match_window)
    end = min(len(groups), expected_group_index + match_window + 1)
    scored: list[dict[str, Any]] = []
    for group in groups[start:end]:
        summary = _target_group_summary(group, target_by_id)
        score = _chapter_match_score(
            raw_summary,
            summary,
            raw_index=raw_index,
            expected_group_index=expected_group_index,
        )
        scored.append(
            {
                "target_chapter_ids": list(group.get("chapter_ids") or []),
                "target_titles": list(group.get("titles") or []),
                "group_index": group.get("group_index"),
                **score,
            }
        )
    scored.sort(key=lambda row: (row["confidence"], row["anchor_overlap"], row["title_similarity"]), reverse=True)
    if not scored:
        return None, None, []
    best = scored[0]
    best_group = next(group for group in groups if group.get("group_index") == best.get("group_index"))
    target_titles = "\n".join(str(title) for title in best_group.get("titles") or [])
    has_numbered_target_title = bool(re.search(r"\b(?:chương|chuong|chapter)\s+\d+\b", target_titles, re.IGNORECASE))
    expected_single = (
        has_numbered_target_title
        and len(best_group.get("chapter_ids") or []) == 1
        and int((best_group.get("chapter_ids") or [0])[0]) == raw_index + 1
        and 0.25 <= float(best.get("length_ratio") or 0) <= 5.0
    )
    if expected_single:
        best = dict(best)
        best["confidence"] = max(float(best.get("confidence") or 0), TENTATIVE_CHAPTER_MATCH_THRESHOLD)
        best["match_strength"] = "tentative_numbered_spine_fallback"
    if float(best["confidence"]) < TENTATIVE_CHAPTER_MATCH_THRESHOLD and not expected_single:
        return None, best, scored
    return best_group, best, scored


def _chapter_title_target_map(
    raw_chapters: list[dict[str, Any]],
    target_chapters: list[dict[str, Any]],
    *,
    match_window: int = DEFAULT_CHAPTER_MATCH_WINDOW,
) -> tuple[dict[int, list[int]], list[dict[str, Any]]]:
    groups = _target_title_groups(target_chapters)
    target_by_id = {int(chapter["chapter_id"]): chapter for chapter in target_chapters}
    mapping: dict[int, list[int]] = {}
    report_rows: list[dict[str, Any]] = []
    min_group_index = 0
    for raw_index, raw_chapter in enumerate(raw_chapters):
        chapter_id = int(raw_chapter["chapter_id"])
        raw_summary = _chapter_index_row(raw_chapter, lang="zh")
        source_tokens = _title_tokens(str(raw_chapter.get("title") or ""), lang="zh")
        scored: list[tuple[int, int, dict[str, Any], set[str]]] = []
        for group in groups[min_group_index:]:
            overlap = source_tokens & set(group["tokens"])
            if not overlap:
                continue
            # Prefer richer title overlap, then monotonic nearness.
            scored.append(
                (
                    len(overlap),
                    -abs(group["group_index"] - min_group_index),
                    group,
                    overlap,
                )
            )
        if scored:
            scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
            overlap_count, _distance, group, overlap = scored[0]
            match_summary = _target_group_summary(group, target_by_id)
            match_score = _chapter_match_score(
                raw_summary,
                match_summary,
                raw_index=raw_index,
                expected_group_index=min_group_index,
            )
            target_ids = list(group["chapter_ids"])
            split_decision = _adjacent_split_decision(
                raw_chapters=raw_chapters,
                raw_index=raw_index,
                target_group=group,
                target_groups=groups,
                target_by_id=target_by_id,
            )
            status = "mapped"
            if split_decision and split_decision["should_join"]:
                next_id = int(split_decision["next_chapter_id"])
                if next_id not in target_ids:
                    target_ids.append(next_id)
                status = "mapped_joined_adjacent_split"
            mapping[chapter_id] = target_ids
            min_group_index = int(group["group_index"]) + 1
            report_rows.append(
                {
                    "source_chapter_id": chapter_id,
                    "source_title": raw_chapter.get("title"),
                    "source_chapter_number": raw_summary.get("chapter_number"),
                    "source_title_tokens": sorted(source_tokens),
                    "target_chapter_ids": target_ids,
                    "target_titles": group["titles"],
                    "shared_title_tokens": sorted(overlap),
                    "overlap_count": overlap_count,
                    "match_confidence": match_score["confidence"],
                    "title_similarity": match_score["title_similarity"],
                    "anchor_overlap": match_score["anchor_overlap"],
                    "first_anchor_overlap": match_score["first_anchor_overlap"],
                    "last_anchor_overlap": match_score["last_anchor_overlap"],
                    "length_ratio": match_score["length_ratio"],
                    "split_decision": split_decision,
                    "status": status,
                }
            )
        else:
            fallback_group, fallback_score, fallback_candidates = _fallback_target_group(
                raw_summary,
                groups,
                target_by_id,
                raw_index=raw_index,
                expected_group_index=min_group_index,
                match_window=match_window,
            )
            if fallback_group and fallback_score:
                target_ids = list(fallback_group["chapter_ids"])
                split_decision = _adjacent_split_decision(
                    raw_chapters=raw_chapters,
                    raw_index=raw_index,
                    target_group=fallback_group,
                    target_groups=groups,
                    target_by_id=target_by_id,
                )
                status = "mapped_by_anchor_fallback"
                if split_decision and split_decision["should_join"]:
                    next_id = int(split_decision["next_chapter_id"])
                    if next_id not in target_ids:
                        target_ids.append(next_id)
                    status = "mapped_by_anchor_fallback_joined_adjacent_split"
                mapping[chapter_id] = target_ids
                min_group_index = int(fallback_group["group_index"]) + 1
                report_rows.append(
                    {
                        "source_chapter_id": chapter_id,
                        "source_title": raw_chapter.get("title"),
                        "source_chapter_number": raw_summary.get("chapter_number"),
                        "source_title_tokens": sorted(source_tokens),
                        "target_chapter_ids": target_ids,
                        "target_titles": fallback_group["titles"],
                        "shared_title_tokens": fallback_score.get("shared_title_tokens", []),
                        "overlap_count": len(fallback_score.get("shared_title_tokens", [])),
                        "match_confidence": fallback_score["confidence"],
                        "title_similarity": fallback_score["title_similarity"],
                        "anchor_overlap": fallback_score["anchor_overlap"],
                        "first_anchor_overlap": fallback_score["first_anchor_overlap"],
                        "last_anchor_overlap": fallback_score["last_anchor_overlap"],
                        "length_ratio": fallback_score["length_ratio"],
                        "split_decision": split_decision,
                        "fallback_candidates": fallback_candidates[:8],
                        "status": status,
                    }
                )
                continue
            report_rows.append(
                {
                    "source_chapter_id": chapter_id,
                    "source_title": raw_chapter.get("title"),
                    "source_chapter_number": raw_summary.get("chapter_number"),
                    "source_title_tokens": sorted(source_tokens),
                    "target_chapter_ids": [],
                    "target_titles": [],
                    "shared_title_tokens": [],
                    "overlap_count": 0,
                    "match_confidence": float((fallback_score or {}).get("confidence") or 0),
                    "fallback_candidates": fallback_candidates[:8],
                    "status": "unmapped",
                }
            )
    return mapping, report_rows


def _chapter_diagnostic_candidates(
    raw_chapter: dict[str, Any],
    raw_chapters: list[dict[str, Any]],
    target_chapters: list[dict[str, Any]],
    *,
    match_window: int,
) -> list[dict[str, Any]]:
    groups = _target_title_groups(target_chapters)
    target_by_id = {int(chapter["chapter_id"]): chapter for chapter in target_chapters}
    raw_summary = _chapter_index_row(raw_chapter, lang="zh")
    source_tokens = set(raw_summary.get("title_tokens") or [])
    title_group_index = None
    for group in groups:
        if source_tokens and source_tokens & set(group.get("tokens") or []):
            title_group_index = int(group["group_index"])
            break
    expected_index = title_group_index if title_group_index is not None else max(0, int(raw_chapter["chapter_id"]) - 1)
    start = max(0, expected_index - match_window)
    end = min(len(groups), expected_index + match_window + 1)
    rows: list[dict[str, Any]] = []
    raw_index = max(0, int(raw_chapter["chapter_id"]) - 1)
    for group_index in range(start, end):
        group = groups[group_index]
        candidate_groups = [group]
        if group_index + 1 < len(groups):
            joined = dict(group)
            joined["chapter_ids"] = [
                *list(group.get("chapter_ids") or []),
                int((groups[group_index + 1].get("chapter_ids") or [0])[0]),
            ]
            joined["titles"] = [
                *list(group.get("titles") or []),
                str((groups[group_index + 1].get("titles") or [""])[0]),
            ]
            joined["tokens"] = set(group.get("tokens") or []) | set(groups[group_index + 1].get("tokens") or [])
            candidate_groups.append(joined)
        for candidate_group in candidate_groups:
            summary = _target_group_summary(candidate_group, target_by_id)
            score = _chapter_match_score(
                raw_summary,
                summary,
                raw_index=raw_index,
                expected_group_index=expected_index,
            )
            split_decision = _adjacent_split_decision(
                raw_chapters=raw_chapters,
                raw_index=raw_index,
                target_group=group,
                target_groups=groups,
                target_by_id=target_by_id,
            )
            confidence = float(score["confidence"])
            rows.append(
                {
                    "target_chapter_ids": list(candidate_group.get("chapter_ids") or []),
                    "target_titles": list(candidate_group.get("titles") or []),
                    "group_index": group.get("group_index"),
                    "candidate_kind": "joined_adjacent" if candidate_group is not group else "single_or_title_group",
                    "accepted": confidence >= TENTATIVE_CHAPTER_MATCH_THRESHOLD,
                    "match_strength": "strong" if confidence >= STRONG_CHAPTER_MATCH_THRESHOLD else "tentative" if confidence >= TENTATIVE_CHAPTER_MATCH_THRESHOLD else "reject",
                    "split_decision": split_decision if candidate_group is group else None,
                    **score,
                }
            )
    rows.sort(
        key=lambda row: (
            row["accepted"],
            row["confidence"],
            row["anchor_overlap"],
            row["last_anchor_overlap"],
        ),
        reverse=True,
    )
    return rows


def _write_chapter_alignment_diagnostics(
    run_dir: Path,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    chapters: list[int],
    raw_chapters: list[dict[str, Any]],
    target_chapters: list[dict[str, Any]],
    chapter_map_rows: list[dict[str, Any]],
    match_window: int,
) -> None:
    raw_index = [_chapter_index_row(chapter, lang="zh") for chapter in raw_chapters]
    target_index = [_chapter_index_row(chapter, lang="vi") for chapter in target_chapters]
    chapter_10 = next((chapter for chapter in raw_chapters if int(chapter["chapter_id"]) == 10), None)
    chapter_10_candidates = (
        _chapter_diagnostic_candidates(
            chapter_10,
            raw_chapters,
            target_chapters,
            match_window=match_window,
        )
        if chapter_10
        else []
    )
    payload = {
        "schema_version": "chapter_alignment_diagnostics_v1",
        "project": project_slug,
        "raw_path": str(raw_path),
        "translated_path": str(translated_path),
        "requested_chapters": chapters,
        "match_window": match_window,
        "raw_chapter_count": len(raw_chapters),
        "translated_chapter_count": len(target_chapters),
        "chapter_match_rows": chapter_map_rows,
        "created_at": utc_now(),
    }
    write_json(run_dir / "chapter_alignment_diagnostics.json", payload)
    write_json(run_dir / "raw_chapter_index.json", {"chapters": raw_index, "created_at": utc_now()})
    write_json(run_dir / "translated_chapter_index.json", {"chapters": target_index, "created_at": utc_now()})
    write_json(
        run_dir / "chapter_10_alignment_candidates.json",
        {
            "schema_version": "chapter_10_alignment_candidates_v1",
            "candidate_count": len(chapter_10_candidates),
            "candidates": chapter_10_candidates,
            "created_at": utc_now(),
        },
    )
    lines = [
        "# Chapter Alignment Diagnostics",
        "",
        f"- Project: `{project_slug}`",
        f"- Requested chapters: `{chapters}`",
        f"- Raw chapters: `{len(raw_chapters)}`",
        f"- Translated sections: `{len(target_chapters)}`",
        "",
        "| Raw | Status | Target IDs | Confidence | Title | Anchor | Tail | Split decision |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in chapter_map_rows:
        split = row.get("split_decision") or {}
        lines.append(
            f"| {row.get('source_chapter_id')} | {row.get('status')} | "
            f"{','.join(str(item) for item in row.get('target_chapter_ids', []))} | "
            f"{row.get('match_confidence')} | {row.get('title_similarity')} | "
            f"{row.get('anchor_overlap')} | {row.get('last_anchor_overlap')} | "
            f"{split.get('reason', '')} |"
        )
    _write_text(run_dir / "chapter_alignment_diagnostics.md", "\n".join(lines) + "\n")

    c10_lines = [
        "# Chapter 10 Alignment Candidates",
        "",
        "| Target IDs | Kind | Strength | Confidence | Title | Anchor | Head | Tail | Length | Warnings |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in chapter_10_candidates:
        c10_lines.append(
            f"| {','.join(str(item) for item in row.get('target_chapter_ids', []))} | "
            f"{row.get('candidate_kind')} | {row.get('match_strength')} | {row.get('confidence')} | "
            f"{row.get('title_similarity')} | {row.get('anchor_overlap')} | "
            f"{row.get('first_anchor_overlap')} | {row.get('last_anchor_overlap')} | "
            f"{row.get('length_ratio')} | {', '.join(row.get('warnings', []))} |"
        )
    _write_text(run_dir / "chapter_10_alignment_candidates.md", "\n".join(c10_lines) + "\n")


def diagnose_chapter_alignment(
    workspace: Workspace,
    *,
    project_slug: str,
    raw_path: Path,
    translated_path: Path,
    chapters: str,
    match_window: int = DEFAULT_CHAPTER_MATCH_WINDOW,
) -> dict[str, Any]:
    get_project_by_slug(workspace, project_slug)
    selected_chapters = parse_chapter_selection(chapters)
    raw_chapters = extract_raw_chapters(raw_path, max_chapters=max(selected_chapters))
    target_chapters = extract_epub_chapters(
        translated_path,
        max_chapters=max(selected_chapters) * 3 + match_window + 3,
    )
    mapping, report_rows = _chapter_title_target_map(
        raw_chapters,
        target_chapters,
        match_window=match_window,
    )
    run_dir = new_alignment_diagnostics_run_dir(workspace, project_slug)
    _write_chapter_alignment_diagnostics(
        run_dir,
        project_slug=project_slug,
        raw_path=raw_path,
        translated_path=translated_path,
        chapters=selected_chapters,
        raw_chapters=raw_chapters,
        target_chapters=target_chapters,
        chapter_map_rows=report_rows,
        match_window=match_window,
    )
    chapter_10_row = next((row for row in report_rows if int(row.get("source_chapter_id") or 0) == 10), None)
    return {
        "run_dir": str(run_dir),
        "requested_chapters": selected_chapters,
        "chapter_10_match": chapter_10_row,
        "matched_chapter_count": len(mapping),
        "report_paths": {
            "diagnostics_json": str(run_dir / "chapter_alignment_diagnostics.json"),
            "diagnostics_md": str(run_dir / "chapter_alignment_diagnostics.md"),
            "raw_index": str(run_dir / "raw_chapter_index.json"),
            "translated_index": str(run_dir / "translated_chapter_index.json"),
            "chapter_10_candidates_json": str(run_dir / "chapter_10_alignment_candidates.json"),
            "chapter_10_candidates_md": str(run_dir / "chapter_10_alignment_candidates.md"),
        },
    }


def _validation_unit_safety(sample: dict[str, Any]) -> dict[str, Any]:
    units = sample.get("translation_units") or []
    rows: list[dict[str, Any]] = []
    risk_score = 0
    hard_rejections: list[str] = []
    warnings: list[str] = []
    for unit in units:
        source_chars = int(unit.get("source_char_count") or 0)
        reference_chars = int(unit.get("reference_char_count") or unit.get("target_char_count") or 0)
        ratio = reference_chars / max(source_chars, 1)
        unit_type = str(unit.get("unit_type") or "")
        unit_reasons: list[str] = []
        if source_chars > MAX_SOURCE_CHARS_PER_VALIDATION_UNIT:
            unit_reasons.append("source_unit_too_wide")
            hard_rejections.append(f"{unit.get('unit_id')}:source_unit_too_wide")
            risk_score += 5
        if reference_chars > MAX_REFERENCE_CHARS_PER_VALIDATION_UNIT:
            unit_reasons.append("reference_unit_too_wide")
            hard_rejections.append(f"{unit.get('unit_id')}:reference_unit_too_wide")
            risk_score += 5
        if unit_type == "mixed":
            unit_reasons.append("mixed_panel_narrative_unit")
            hard_rejections.append(f"{unit.get('unit_id')}:mixed_panel_narrative_unit")
            risk_score += 4
        if source_chars > 70 and ratio < MIN_REFERENCE_SOURCE_RATIO:
            unit_reasons.append("reference_too_short_for_source")
            risk_score += 4
        if source_chars > 60 and reference_chars < MIN_REFERENCE_CHARS_FOR_MEDIUM_SOURCE:
            unit_reasons.append("small_reference_budget_for_medium_source")
            risk_score += 3
        if reference_chars >= 180 and ratio > MAX_REFERENCE_SOURCE_RATIO:
            unit_reasons.append("reference_source_ratio_outlier")
            risk_score += 3
        rows.append(
            {
                "unit_id": unit.get("unit_id"),
                "unit_type": unit_type,
                "source_char_count": source_chars,
                "reference_char_count": reference_chars,
                "reference_source_ratio": round(ratio, 3),
                "source_paragraph_ids": unit.get("source_paragraph_ids", []),
                "target_paragraph_ids": unit.get("target_paragraph_ids", []),
                "merge_reason": unit.get("merge_reason"),
                "reasons": unit_reasons,
                "accepted": not unit_reasons,
            }
        )
    source_span = len(sample.get("source_blocks") or [])
    target_span = len(sample.get("target_blocks") or [])
    source_types = {block.get("block_type") for block in sample.get("source_blocks", [])}
    target_types = {block.get("block_type") for block in sample.get("target_blocks", [])}
    sample_ratio = int(sample.get("target_char_count") or 0) / max(int(sample.get("source_char_count") or 0), 1)
    if source_span > MAX_BLOCKS_PER_SAFE_CANDIDATE or target_span > MAX_BLOCKS_PER_SAFE_CANDIDATE:
        warnings.append("large_block_span")
        risk_score += 1
    if ("panel" in source_types) != ("panel" in target_types):
        warnings.append("panel_type_mismatch")
        risk_score += 4
    if sample_ratio < 1.7:
        warnings.append("sample_reference_too_short_for_source")
        risk_score += 2
    if sample_ratio > 3.5:
        warnings.append("sample_reference_source_ratio_outlier")
        risk_score += 2
    compression_risk = "low" if risk_score == 0 else "medium" if risk_score <= 3 else "high"
    return {
        "accepted": risk_score == 0 and not hard_rejections,
        "risk_score": risk_score,
        "compression_risk": compression_risk,
        "hard_rejections": hard_rejections,
        "boundary_warnings": warnings,
        "unit_rows": rows,
        "unit_count": len(units),
        "block_types": {
            "source": sorted(str(item) for item in source_types if item),
            "target": sorted(str(item) for item in target_types if item),
        },
        "source_block_count": source_span,
        "target_block_count": target_span,
        "sample_reference_source_ratio": round(sample_ratio, 3),
    }


def _locked_validation_sample_from_candidate(
    candidate: dict[str, Any],
    *,
    sample_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = sample_from_alignment_candidate(candidate, sample_id=sample_id)
    sample = apply_translation_units([sample], merge_tiny_paragraphs=True)[0]
    safety = _validation_unit_safety(sample)
    sample["validation_units_locked"] = True
    sample["validation_unit_safety"] = safety
    sample["use_translation_units"] = True
    for unit in sample.get("translation_units", []):
        unit["validation_unit_locked"] = True
        unit["validation_unit_safety"] = next(
            (
                row
                for row in safety["unit_rows"]
                if row.get("unit_id") == unit.get("unit_id")
            ),
            {},
        )
    return sample, safety




def _candidate_ranking_row(
    *,
    chapter: int,
    candidate: dict[str, Any],
    safety: dict[str, Any],
    selected: bool = False,
) -> dict[str, Any]:
    accepted = bool(candidate.get("accepted")) and safety["accepted"]
    rejected_reasons = list(candidate.get("rejection_reasons") or [])
    if not safety["accepted"]:
        rejected_reasons.extend(safety.get("hard_rejections") or [])
        rejected_reasons.extend(safety.get("boundary_warnings") or [])
        rejected_reasons.extend(
            f"{row['unit_id']}:{','.join(row['reasons'])}"
            for row in safety.get("unit_rows", [])
            if row.get("reasons")
        )
    return {
        "chapter": chapter,
        "candidate_id": candidate.get("candidate_id"),
        "alignment_quality": candidate.get("alignment_quality"),
        "source_chars": candidate.get("source_char_count"),
        "reference_chars": candidate.get("target_char_count"),
        "ratio": candidate.get("target_source_length_ratio"),
        "source_block_start": candidate.get("source_block_start"),
        "source_block_end": candidate.get("source_block_end"),
        "target_block_start": candidate.get("target_block_start"),
        "target_block_end": candidate.get("target_block_end"),
        "block_types": safety.get("block_types"),
        "boundary_warnings": safety.get("boundary_warnings", []),
        "compression_risk": safety.get("compression_risk"),
        "risk_score": safety.get("risk_score"),
        "unit_count": safety.get("unit_count"),
        "unit_rows": safety.get("unit_rows", []),
        "accepted": accepted,
        "selected": selected,
        "rejected_reasons": sorted(set(str(reason) for reason in rejected_reasons if reason)),
        "source_preview": str(candidate.get("source_text", ""))[:240],
        "target_preview": str(candidate.get("target_text", ""))[:240],
    }


def _write_unit_candidate_ranking(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    write_json(
        run_dir / "unit_candidate_ranking.json",
        {
            "schema_version": "approved_memory_validation_unit_candidate_ranking_v1",
            "candidate_count": len(rows),
            "accepted_candidate_count": sum(1 for row in rows if row.get("accepted")),
            "selected_candidate_count": sum(1 for row in rows if row.get("selected")),
            "candidates": rows,
            "created_at": utc_now(),
        },
    )
    lines = [
        "# Unit Candidate Ranking",
        "",
        "| Chapter | Candidate | Accepted | Selected | Risk | Align | Source | Ref | Ratio | Reasons |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['chapter']} | {row['candidate_id']} | {row['accepted']} | {row['selected']} | "
            f"{row['risk_score']} | {row['alignment_quality']} | {row['source_chars']} | "
            f"{row['reference_chars']} | {row['ratio']} | {', '.join(row['rejected_reasons'][:4])} |"
        )
    _write_text(run_dir / "unit_candidate_ranking.md", "\n".join(lines) + "\n")


def _write_selected_validation_units(run_dir: Path, samples: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": "approved_memory_validation_selected_units_v1",
        "sample_count": len(samples),
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "alignment_quality": sample.get("alignment_quality"),
                "source_char_count": sample.get("source_char_count"),
                "reference_char_count": sample.get("target_char_count"),
                "validation_unit_safety": sample.get("validation_unit_safety"),
                "units": sample.get("translation_units", []),
            }
            for sample in samples
        ],
        "created_at": utc_now(),
    }
    write_json(run_dir / "selected_validation_units.json", payload)
    lines = ["# Selected Validation Units", ""]
    for sample in samples:
        lines.extend(
            [
                f"## {sample['sample_id']} Chapter {sample['chapter_id']}",
                "",
                f"- Alignment quality: `{sample.get('alignment_quality')}`",
                f"- Compression risk: `{(sample.get('validation_unit_safety') or {}).get('compression_risk')}`",
                "",
                "| Unit | Type | Source chars | Reference chars | Ratio | Reasons |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for unit in sample.get("translation_units", []):
            safety = unit.get("validation_unit_safety") or {}
            lines.append(
                f"| {unit.get('unit_id')} | {unit.get('unit_type')} | {unit.get('source_char_count')} | "
                f"{unit.get('reference_char_count')} | {unit.get('target_source_ratio')} | "
                f"{', '.join(safety.get('reasons', []))} |"
            )
        lines.append("")
    _write_text(run_dir / "selected_validation_units.md", "\n".join(lines) + "\n")


def _parse_excluded_candidate_ids(raw: str | None) -> list[dict[str, Any]]:
    exclusions: list[dict[str, Any]] = []
    if not raw:
        return exclusions
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            chapter, candidate_id = token.split(":", 1)
            try:
                chapter_value: int | None = int(chapter)
            except ValueError:
                chapter_value = None
            exclusions.append(
                {
                    "chapter": chapter_value,
                    "candidate_id": candidate_id.strip(),
                    "exclusion_reason": "cli_excluded_candidate",
                    "source": "cli",
                }
            )
        else:
            exclusions.append(
                {
                    "chapter": None,
                    "candidate_id": token,
                    "exclusion_reason": "cli_excluded_candidate",
                    "source": "cli",
                }
            )
    return exclusions


def _load_validation_candidate_exclusions(
    workspace_path: Path,
    *,
    project_slug: str,
) -> list[dict[str, Any]]:
    path = validation_candidate_exclusions_path(workspace_path)
    if not path.exists():
        return []
    payload = read_json(path)
    return [
        exclusion
        for exclusion in payload.get("exclusions", [])
        if exclusion.get("project") == project_slug
        and exclusion.get("validation_purpose") == "approved_memory_validation"
    ]


def _write_validation_candidate_exclusions(
    workspace_path: Path,
    exclusions: list[dict[str, Any]],
) -> None:
    path = validation_candidate_exclusions_path(workspace_path)
    existing = read_json(path) if path.exists() else {"schema_version": "validation_candidate_exclusions_v1", "exclusions": []}
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in existing.get("exclusions", []):
        key = (
            item.get("project"),
            item.get("validation_purpose"),
            item.get("chapter"),
            item.get("candidate_id"),
            item.get("source_hash"),
            item.get("reference_hash"),
        )
        merged[key] = item
    for item in exclusions:
        key = (
            item.get("project"),
            item.get("validation_purpose"),
            item.get("chapter"),
            item.get("candidate_id"),
            item.get("source_hash"),
            item.get("reference_hash"),
        )
        merged[key] = item
    write_json(
        path,
        {
            "schema_version": "validation_candidate_exclusions_v1",
            "exclusions": sorted(
                merged.values(),
                key=lambda item: (
                    str(item.get("project")),
                    int(item.get("chapter") or 0),
                    str(item.get("candidate_id")),
                    str(item.get("created_at")),
                ),
            ),
            "updated_at": utc_now(),
        },
    )


def _candidate_is_excluded(
    *,
    project_slug: str,
    chapter: int,
    candidate: dict[str, Any],
    exclusions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidate_id = str(candidate.get("candidate_id"))
    source_hash = sha256_text(str(candidate.get("source_text", "")))
    reference_hash = sha256_text(str(candidate.get("target_text", "")))
    for exclusion in exclusions:
        if exclusion.get("project") not in (None, project_slug):
            continue
        if exclusion.get("chapter") not in (None, chapter):
            continue
        if str(exclusion.get("candidate_id")) != candidate_id:
            continue
        exclusion_source_hash = exclusion.get("source_hash")
        exclusion_reference_hash = exclusion.get("reference_hash")
        if exclusion_source_hash and exclusion_source_hash != source_hash:
            continue
        if exclusion_reference_hash and exclusion_reference_hash != reference_hash:
            continue
        return exclusion
    return None


def _write_chapter_ablation_report(
    run_dir: Path,
    *,
    chapter: int,
    rows: list[dict[str, Any]],
    selected_candidate_id: str | None,
    previous_exclusions: list[dict[str, Any]],
    top_n: int,
) -> None:
    considered = rows[:top_n]
    payload = {
        "schema_version": "approved_memory_validation_window_ablation_v1",
        "chapter": chapter,
        "top_n": top_n,
        "selected_candidate_id": selected_candidate_id,
        "previously_excluded_candidate_ids": [
            item.get("candidate_id") for item in previous_exclusions if item.get("chapter") in (chapter, None)
        ],
        "candidates": considered,
        "created_at": utc_now(),
    }
    write_json(run_dir / f"chapter_{chapter}_window_ablation.json", payload)
    lines = [
        f"# Chapter {chapter} Window Ablation",
        "",
        f"- Selected candidate: `{selected_candidate_id}`",
        f"- Top N: `{top_n}`",
        "",
        "| Candidate | Selected | Accepted | Risk | Align | Source | Ref | Ratio | Reasons |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in considered:
        lines.append(
            f"| {row['candidate_id']} | {row['selected']} | {row['accepted']} | {row['risk_score']} | "
            f"{row['alignment_quality']} | {row['source_chars']} | {row['reference_chars']} | "
            f"{row['ratio']} | {', '.join(row['rejected_reasons'][:4])} |"
        )
    _write_text(run_dir / f"chapter_{chapter}_window_ablation.md", "\n".join(lines) + "\n")


def _write_chapter_10_rebuild_report(
    run_dir: Path,
    *,
    source_chapter: dict[str, Any] | None,
    target_subset: list[dict[str, Any]],
    match_row: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    selected_candidate_id: str | None,
    selected_sample: dict[str, Any] | None,
) -> None:
    payload = {
        "schema_version": "approved_memory_validation_chapter_10_rebuilt_alignment_v1",
        "source_chapter": _chapter_index_row(source_chapter, lang="zh") if source_chapter else None,
        "target_chapters": [_chapter_index_row(chapter, lang="vi") for chapter in target_subset],
        "match_decision": match_row or {},
        "selected_candidate_id": selected_candidate_id,
        "safe_candidate_count": sum(1 for row in rows if row.get("accepted")),
        "candidate_count": len(rows),
        "created_at": utc_now(),
    }
    write_json(run_dir / "chapter_10_rebuilt_alignment.json", payload)
    write_json(
        run_dir / "chapter_10_rebuilt_unit_candidate_ranking.json",
        {
            "schema_version": "approved_memory_validation_chapter_10_rebuilt_unit_candidate_ranking_v1",
            "candidate_count": len(rows),
            "accepted_candidate_count": sum(1 for row in rows if row.get("accepted")),
            "selected_candidate_id": selected_candidate_id,
            "candidates": rows,
            "created_at": utc_now(),
        },
    )
    if selected_sample:
        write_json(
            run_dir / "chapter_10_selected_safe_unit.json",
            {
                "schema_version": "approved_memory_validation_chapter_10_selected_safe_unit_v1",
                "selected_candidate_id": selected_candidate_id,
                "sample": selected_sample,
                "created_at": utc_now(),
            },
        )
    lines = [
        "# Chapter 10 Rebuilt Alignment",
        "",
        f"- Source title: `{source_chapter.get('title') if source_chapter else None}`",
        f"- Target sections: `{', '.join(str(chapter.get('chapter_id')) for chapter in target_subset)}`",
        f"- Match status: `{(match_row or {}).get('status')}`",
        f"- Selected candidate: `{selected_candidate_id}`",
        f"- Safe candidates: `{sum(1 for row in rows if row.get('accepted'))}`",
        "",
        "| Candidate | Accepted | Selected | Risk | Align | Source | Ref | Ratio | Reasons |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows[:20]:
        lines.append(
            f"| {row.get('candidate_id')} | {row.get('accepted')} | {row.get('selected')} | "
            f"{row.get('risk_score')} | {row.get('alignment_quality')} | "
            f"{row.get('source_chars')} | {row.get('reference_chars')} | "
            f"{row.get('ratio')} | {', '.join(row.get('rejected_reasons', [])[:4])} |"
        )
    _write_text(run_dir / "chapter_10_rebuilt_alignment.md", "\n".join(lines) + "\n")
    _write_text(run_dir / "chapter_10_rebuilt_unit_candidate_ranking.md", "\n".join(lines) + "\n")


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


def _is_mined_approved_memory(item: dict[str, Any]) -> bool:
    value = item.get("value_json") or {}
    confidence = item.get("confidence_json") or {}
    return bool(
        value.get("mining_run_id")
        or value.get("candidate_id")
        or confidence.get("source") == "mined_memory_candidate"
    )


def _memory_source_pattern(item: dict[str, Any]) -> str | None:
    value = item.get("value_json") or {}
    return item.get("source_key") or value.get("source_pattern")


def _memory_preferred_target(item: dict[str, Any]) -> str | None:
    value = item.get("value_json") or {}
    rules = item.get("rules_json") or {}
    return item.get("target_text") or value.get("preferred_target") or rules.get("preferred_target")


def _memory_origin(item: dict[str, Any]) -> str:
    value = item.get("value_json") or {}
    if value.get("mining_run_id") or value.get("candidate_id"):
        return "MVP5D.5 mining"
    if value.get("learning_run_id"):
        return "MVP5C learning"
    return "active_memory"


def _memory_provenance(item: dict[str, Any]) -> str | None:
    value = item.get("value_json") or {}
    confidence = item.get("confidence_json") or {}
    return value.get("mining_run_id") or value.get("learning_run_id") or confidence.get("source")


def _snapshot_memory_row(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("value_json") or {}
    return {
        "id": item.get("id"),
        "candidate_id": value.get("candidate_id"),
        "memory_type": item.get("memory_type"),
        "source_pattern": _memory_source_pattern(item),
        "preferred_target": _memory_preferred_target(item),
        "status": item.get("status"),
        "provenance": _memory_provenance(item),
        "origin": _memory_origin(item),
        "confidence_score": item.get("confidence_score"),
    }


def _write_active_memory_snapshot(
    run_dir: Path,
    *,
    active_memory: list[dict[str, Any]],
    baseline_memory: list[dict[str, Any]],
    memory_pass: list[dict[str, Any]],
    baseline_excluded: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = [_snapshot_memory_row(item) for item in active_memory]
    baseline_ids = {item["id"] for item in baseline_memory}
    memory_pass_ids = {item["id"] for item in memory_pass}
    excluded_ids = {item["id"] for item in baseline_excluded}
    for row in rows:
        row["included_in_baseline_pass"] = row["id"] in baseline_ids
        row["included_in_memory_pass"] = row["id"] in memory_pass_ids
        row["excluded_from_baseline"] = row["id"] in excluded_ids
    payload = {
        "schema_version": "approved_memory_active_snapshot_v1",
        "created_at": utc_now(),
        "active_memory_count": len(rows),
        "baseline_memory_count": len(baseline_memory),
        "memory_pass_count": len(memory_pass),
        "baseline_excluded_count": len(baseline_excluded),
        "active_memory": rows,
    }
    write_json(run_dir / "active_memory_snapshot.json", payload)
    lines = [
        "# Active Memory Snapshot",
        "",
        f"- Active memory count: `{len(rows)}`",
        f"- Baseline memory count: `{len(baseline_memory)}`",
        f"- Memory pass count: `{len(memory_pass)}`",
        f"- Baseline excluded count: `{len(baseline_excluded)}`",
        "",
        "| ID | Candidate | Type | Source | Preferred | Status | Origin | Baseline | Memory pass |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('id')} | {row.get('candidate_id') or ''} | {row.get('memory_type')} | "
            f"{row.get('source_pattern')} | {row.get('preferred_target')} | {row.get('status')} | "
            f"{row.get('origin')} | {row.get('included_in_baseline_pass')} | {row.get('included_in_memory_pass')} |"
        )
    _write_text(run_dir / "active_memory_snapshot.md", "\n".join(lines) + "\n")
    return payload


def _missing_expected_mvp5d6_memory(active_memory: list[dict[str, Any]]) -> dict[str, list[str]]:
    memory_ids = {str(item.get("id")) for item in active_memory}
    candidate_ids = {
        str((item.get("value_json") or {}).get("candidate_id"))
        for item in active_memory
        if (item.get("value_json") or {}).get("candidate_id")
    }
    has_any_expected = bool(
        memory_ids & MVP5D6_ORIGINAL_APPROVED_MEMORY_IDS
        or candidate_ids & MVP5D6_APPROVED_MINED_CANDIDATE_IDS
    )
    if not has_any_expected:
        return {"missing_original_memory_ids": [], "missing_mined_candidate_ids": []}
    return {
        "missing_original_memory_ids": sorted(MVP5D6_ORIGINAL_APPROVED_MEMORY_IDS - memory_ids),
        "missing_mined_candidate_ids": sorted(MVP5D6_APPROVED_MINED_CANDIDATE_IDS - candidate_ids),
    }


def _rolled_back_expected_mined_candidate_ids(workspace: Workspace, project: dict[str, Any]) -> set[str]:
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, status, scope_json, value_json, confidence_json
            FROM memory_items
            WHERE status IN ('deprecated', 'rejected')
            """
        ).fetchall()
    rolled_back: set[str] = set()
    for row in rows:
        item = row_to_dict(row, json_fields=("scope_json", "value_json", "confidence_json"))
        scope = item.get("scope_json") or {}
        if scope.get("project_id") not in (None, project["id"]):
            continue
        if scope.get("project_slug") not in (None, project["slug"]):
            continue
        value = item.get("value_json") or {}
        candidate_id = value.get("candidate_id")
        if candidate_id in MVP5D6_APPROVED_MINED_CANDIDATE_IDS and (
            value.get("review_status") == "rejected_after_validation"
            or value.get("status") == "rejected_after_validation"
            or (item.get("confidence_json") or {}).get("source") == "mined_memory_candidate"
        ):
            rolled_back.add(str(candidate_id))
    return rolled_back


def _write_memory_delta_context(
    run_dir: Path,
    *,
    approved_memory: list[dict[str, Any]],
    baseline_memory: list[dict[str, Any]],
    mined_memory: list[dict[str, Any]],
    baseline_excluded: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "schema_version": "approved_memory_delta_context_v1",
        "created_at": utc_now(),
        "comparison_mode": (
            "newly_mined_memory_delta"
            if mined_memory
            else "legacy_all_approved_memory_delta"
        ),
        "approved_memory_ids": [item["id"] for item in approved_memory],
        "baseline_memory_ids": [item["id"] for item in baseline_memory],
        "newly_approved_mined_memory_ids": [item["id"] for item in mined_memory],
        "newly_approved_mined_candidate_ids": [
            (item.get("value_json") or {}).get("candidate_id")
            for item in mined_memory
            if (item.get("value_json") or {}).get("candidate_id")
        ],
        "baseline_excluded_memory_ids": [item["id"] for item in baseline_excluded],
        "baseline_excluded_candidate_ids": [
            (item.get("value_json") or {}).get("candidate_id")
            for item in baseline_excluded
            if (item.get("value_json") or {}).get("candidate_id")
        ],
    }
    write_json(run_dir / "memory_delta_context.json", payload)
    return payload


def _memory_prompt_section(memory_items: list[dict[str, Any]], *, title: str) -> str:
    if not memory_items:
        return f"{title}\n- None supplied.\n"
    lines = [
        title,
        "- Apply a memory item only when its source text appears in the current source unit JSON.",
        "- Ignore listed memory items whose source trigger is absent from the current sample/unit.",
    ]
    for item in memory_items:
        rules = item.get("rules_json") or {}
        value = item.get("value_json") or {}
        forbidden = rules.get("forbidden_variants") or []
        lines.append(
            "- "
            f"id={item['id']}; type={item['memory_type']}; "
            f"source={item.get('source_key')}; preferred={item.get('target_text')}; "
            f"forbidden={json_dumps(forbidden)}; confidence={item.get('confidence_score')}; "
            f"context_required={value.get('context_required') or rules.get('context_required') or ''}; "
            f"exclude_chapters={json_dumps(value.get('exclude_chapters') or rules.get('exclude_chapters') or [])}"
        )
    return "\n".join(lines) + "\n"


def _validation_prompt(
    stable_prompt: StablePromptRecord,
    *,
    included_memory: list[dict[str, Any]],
    excluded_memory: list[dict[str, Any]],
    phase: str,
    dictionary_block: str | None = None,
    support_block: str | None = None,
) -> str:
    sections = [
        stable_prompt.prompt_text,
        "",
    ]
    rendered_support_block = support_block or dictionary_block
    if rendered_support_block:
        sections.extend([rendered_support_block, ""])
    sections.extend(
        [
        "Approved-memory validation mode:",
        "- Translate with the approved stable prompt.",
        "- Preserve concise Vietnamese webnovel style.",
        "- Return only requested Vietnamese translation JSON/plain text as instructed.",
        "",
        _memory_prompt_section(included_memory, title="Active approved memory supplied to this phase:"),
        ]
    )
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


def _dictionary_prompt_empty_context(project_slug: str, source_text: str) -> dict[str, Any]:
    source_hash = sha256_text(source_text)
    return {
        "schema_version": "dictionary_prompt_context_bundle_v1",
        "project_slug": project_slug,
        "source_sha256": source_hash,
        "block_text": "",
        "block_rendered": False,
        "selected_hits": [],
        "dropped_hits": [],
        "excluded_pending_rejected_entries": [],
        "budget_report": {
            "schema_version": "dictionary_prompt_budget_report_v1",
            "max_dictionary_entries": 0,
            "max_dictionary_chars": 0,
            "eligible_hit_count": 0,
            "selected_hit_count": 0,
            "dropped_hit_count": 0,
            "support_chars": 0,
            "block_rendered": False,
        },
        "retrieval_report": {
            "schema_version": "dictionary_prompt_retrieval_report_v1",
            "project_slug": project_slug,
            "exact_source_match_required": True,
            "active_approved_only": True,
            "selected_hits": [],
            "dropped_hits": [],
            "excluded_pending_rejected_entries": [],
        },
    }


def _hybrid_prompt_empty_context(project_slug: str, source_text: str) -> dict[str, Any]:
    source_hash = sha256_text(source_text)
    return {
        "schema_version": "hybrid_prompt_context_bundle_v1",
        "project_slug": project_slug,
        "source_sha256": source_hash,
        "mode": "production",
        "block_text": "",
        "block_rendered": False,
        "selected_items": [],
        "selected_dictionary_items": [],
        "selected_memory_items": [],
        "selected_rule_items": [],
        "dropped_items": [],
        "dropped_rule_items": [],
        "deduped_items": [],
        "conflicts": [],
        "conflict_count": 0,
        "budget_report": {
            "schema_version": "hybrid_prompt_budget_report_v1",
            "selected_item_count": 0,
            "selected_dictionary_count": 0,
            "selected_memory_count": 0,
            "selected_rule_count": 0,
            "dropped_item_count": 0,
            "dropped_rule_count": 0,
            "support_chars": 0,
            "support_lines": 0,
            "block_rendered": False,
        },
        "retrieval_report": {
            "schema_version": "hybrid_prompt_retrieval_report_v1",
            "project_slug": project_slug,
            "selected_items": [],
            "dropped_items": [],
            "excluded_dictionary_matches": [],
            "excluded_memory_rows": [],
            "excluded_rule_rows": [],
            "pending_rejected_or_inactive_rule_matches": [],
        },
        "conflict_report": {
            "schema_version": "hybrid_prompt_conflict_report_v1",
            "conflict_count": 0,
            "conflicts": [],
        },
        "support_items": {
            "schema_version": "hybrid_prompt_support_items_v1",
            "candidate_items": [],
            "selected_items": [],
            "deduped_items": [],
            "dropped_items": [],
        },
    }


def _read_prompt_artifact(path: Path, schema_version: str) -> dict[str, Any]:
    if path.exists():
        return read_json(path)
    return {"schema_version": schema_version, "created_at": utc_now(), "phases": {}}


def _record_dictionary_prompt_artifacts(
    run_dir: Path,
    *,
    phase: str,
    sample: dict[str, Any],
    context: dict[str, Any],
    prompt_text: str,
) -> None:
    sample_id = str(sample.get("sample_id") or "")
    chapter_id = sample.get("chapter_id")
    prompt_sha = sha256_text(prompt_text)
    is_hybrid = str(context.get("schema_version") or "").startswith("hybrid_prompt")
    context_row = {
        "source_chunk_id": sample_id,
        "chapter_id": chapter_id,
        "source_sha256": context.get("source_sha256"),
        "block_rendered": context.get("block_rendered", False),
        "dictionary_hits_selected": context.get("selected_hits", []) if not is_hybrid else context.get("selected_dictionary_items", []),
        "dictionary_hits_dropped_due_budget": context.get("dropped_hits", []) if not is_hybrid else [
            item for item in context.get("dropped_items", []) if item.get("source_type") == "dictionary"
        ],
        "pending_rejected_entries_excluded": context.get("excluded_pending_rejected_entries", [])
        if not is_hybrid
        else (context.get("retrieval_report") or {}).get("excluded_dictionary_matches", []),
        "support_items_selected": context.get("selected_items", []),
        "memory_items_selected": context.get("selected_memory_items", []),
        "rule_items_selected": context.get("selected_rule_items", []),
        "support_items_dropped": context.get("dropped_items", []),
        "rule_items_dropped": context.get("dropped_rule_items", []),
        "conflicts": context.get("conflicts", []),
        "conflict_count": int(context.get("conflict_count") or 0),
        "prompt_sha256": prompt_sha,
    }
    context_path = run_dir / "prompt_context_bundle.json"
    budget_path = run_dir / "prompt_budget_report.json"
    retrieval_path = run_dir / "prompt_retrieval_report.json"
    conflict_path = run_dir / "prompt_conflict_report.json"
    support_items_path = run_dir / "prompt_support_items.json"
    context_payload = _read_prompt_artifact(context_path, "dictionary_prompt_context_bundle_collection_v1")
    budget_payload = _read_prompt_artifact(budget_path, "dictionary_prompt_budget_report_collection_v1")
    retrieval_payload = _read_prompt_artifact(retrieval_path, "dictionary_prompt_retrieval_report_collection_v1")
    conflict_payload = _read_prompt_artifact(conflict_path, "hybrid_prompt_conflict_report_collection_v1")
    support_items_payload = _read_prompt_artifact(support_items_path, "hybrid_prompt_support_items_collection_v1")
    context_payload.setdefault("phases", {}).setdefault(phase, {})[sample_id] = context_row
    budget_row = dict(context.get("budget_report") or {})
    budget_row.update({"source_chunk_id": sample_id, "chapter_id": chapter_id, "prompt_sha256": prompt_sha})
    budget_payload.setdefault("phases", {}).setdefault(phase, {})[sample_id] = budget_row
    retrieval_row = dict(context.get("retrieval_report") or {})
    retrieval_row.update({"source_chunk_id": sample_id, "chapter_id": chapter_id, "prompt_sha256": prompt_sha})
    retrieval_payload.setdefault("phases", {}).setdefault(phase, {})[sample_id] = retrieval_row
    conflict_row = dict(context.get("conflict_report") or {})
    conflict_row.update({"source_chunk_id": sample_id, "chapter_id": chapter_id, "prompt_sha256": prompt_sha})
    conflict_payload.setdefault("phases", {}).setdefault(phase, {})[sample_id] = conflict_row
    support_items_row = dict(context.get("support_items") or {})
    if not is_hybrid:
        support_items_row.setdefault("selected_items", context.get("selected_hits", []))
        support_items_row.setdefault("dropped_items", context.get("dropped_hits", []))
        support_items_row["dictionary_hits_selected"] = context.get("selected_hits", [])
    support_items_row.update({"source_chunk_id": sample_id, "chapter_id": chapter_id, "prompt_sha256": prompt_sha})
    support_items_payload.setdefault("phases", {}).setdefault(phase, {})[sample_id] = support_items_row
    write_json(context_path, context_payload)
    write_json(budget_path, budget_payload)
    write_json(retrieval_path, retrieval_payload)
    if is_hybrid or context_row.get("dictionary_hits_selected"):
        write_json(conflict_path, conflict_payload)
        write_json(support_items_path, support_items_payload)
    prompt_path = run_dir / "prompt_used.md"
    existing = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "# Prompt Samples\n\n"
    marker = f"## {phase} / {sample_id}\n"
    section = (
        marker
        + "\n"
        + f"- Chapter: `{chapter_id}`\n"
        + f"- Dictionary block rendered: `{context.get('block_rendered', False)}`\n"
        + f"- Prompt sha256: `{prompt_sha}`\n\n"
        + "```text\n"
        + prompt_text.replace("```", "'''")
        + "\n```\n\n"
    )
    if marker not in existing:
        prompt_path.write_text(existing + section, encoding="utf-8")


def _selected_validation_source_text(run_dir: Path) -> str:
    for name in ("selected_samples.json", "selected_validation_units.json"):
        path = run_dir / name
        if not path.exists():
            continue
        payload = read_json(path)
        return "\n\n".join(
            str(sample.get("source_text") or "")
            for sample in payload.get("samples", [])
            if isinstance(sample, dict)
        )
    return ""


def _selected_validation_chapters(run_dir: Path) -> set[int]:
    chapters: set[int] = set()
    for name in ("selected_samples.json", "selected_validation_units.json"):
        path = run_dir / name
        if not path.exists():
            continue
        payload = read_json(path)
        for sample in payload.get("samples", []):
            if not isinstance(sample, dict):
                continue
            chapter = sample.get("chapter_id")
            try:
                if chapter is not None:
                    chapters.add(int(chapter))
            except (TypeError, ValueError):
                continue
    return chapters


def _is_source_pattern_present(source_text: str, source_pattern: str | None) -> bool:
    if not source_pattern:
        return False
    if any("\u4e00" <= char <= "\u9fff" for char in source_pattern):
        return source_pattern in source_text
    return _fold_text(source_pattern) in _fold_text(source_text)


def _memory_context_gate(item: dict[str, Any], source_text: str) -> tuple[bool, str | None]:
    source_pattern = _memory_source_pattern(item)
    value = item.get("value_json") or {}
    rules = item.get("rules_json") or {}
    context_required = str(value.get("context_required") or rules.get("context_required") or "")
    if source_pattern == "技能":
        panel_match = re.search(r"【[^】]*技能[^】]*】", source_text or "")
        if not panel_match:
            return False, "context_gate_failed:skills_requires_system_panel"
    if context_required in {"system_panel", "game_ui"} and not re.search(r"【[^】]+】", source_text or ""):
        return False, f"context_gate_failed:{context_required}"
    if context_required == "name_only" and item.get("memory_type") != "name":
        return False, "context_gate_failed:name_only"
    return True, None


def _memory_negative_gate(item: dict[str, Any]) -> tuple[bool, str | None]:
    value = item.get("value_json") or {}
    confidence = item.get("confidence_json") or {}
    if value.get("deprecated_for_validation") is True:
        return False, "negative_evidence_gate:deprecated_for_validation"
    blocked_statuses = {
        "rejected_after_validation",
        "pending_needs_scoped_review",
        "harmful",
        "harmful_only_in_combination",
        "insufficient_evidence",
        "pending_review",
        "deprecated_for_validation",
    }
    for field in ("status", "review_status", "validation_status", "impact_classification"):
        if str(value.get(field) or "") in blocked_statuses:
            return False, f"negative_evidence_gate:{field}={value.get(field)}"
    if str(confidence.get("impact_classification") or "") in blocked_statuses:
        return False, f"negative_evidence_gate:confidence={confidence.get('impact_classification')}"
    return True, None


def _memory_applicability_rows(
    *,
    memory_items: list[dict[str, Any]],
    source_text: str,
    phase: str,
    chapters: set[int] | None = None,
    cap: int = 24,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    included: list[dict[str, Any]] = []
    for item in memory_items:
        reasons: list[str] = []
        include = True
        if item.get("status") != "active":
            include = False
            reasons.append(f"status_gate:{item.get('status')}")
        source_pattern = _memory_source_pattern(item)
        if not _is_source_pattern_present(source_text, source_pattern):
            include = False
            reasons.append("exact_source_trigger_absent")
        value = item.get("value_json") or {}
        rules = item.get("rules_json") or {}
        excluded_chapters = {
            int(chapter)
            for chapter in (value.get("exclude_chapters") or rules.get("exclude_chapters") or [])
            if str(chapter).strip().lstrip("-").isdigit()
        }
        if chapters and excluded_chapters and chapters & excluded_chapters:
            include = False
            reasons.append(
                "scope_gate:excluded_chapter="
                + ",".join(str(chapter) for chapter in sorted(chapters & excluded_chapters))
            )
        context_ok, context_reason = _memory_context_gate(item, source_text)
        if not context_ok:
            include = False
            reasons.append(context_reason or "context_gate_failed")
        negative_ok, negative_reason = _memory_negative_gate(item)
        if not negative_ok:
            include = False
            reasons.append(negative_reason or "negative_evidence_gate")
        if include and len(included) >= cap:
            include = False
            reasons.append("prompt_budget_cap")
        if include:
            included.append(item)
            reasons.append("included")
        rows.append(
            {
                "phase": phase,
                "memory_id": item.get("id"),
                "candidate_id": (item.get("value_json") or {}).get("candidate_id"),
                "memory_type": item.get("memory_type"),
                "source_pattern": source_pattern,
                "preferred_target": _memory_preferred_target(item),
                "status": item.get("status"),
                "included": include,
                "reasons": reasons,
            }
        )
    return included, rows


def _filter_prompt_memory_for_context(
    run_dir: Path,
    *,
    memory_items: list[dict[str, Any]],
    phase: str,
    source_text: str,
    chapters: set[int],
) -> list[dict[str, Any]]:
    included, rows = _memory_applicability_rows(
        memory_items=memory_items,
        source_text=source_text,
        phase=phase,
        chapters=chapters,
    )
    existing_applicability: dict[str, Any] = (
        read_json(run_dir / "memory_applicability_report.json")
        if (run_dir / "memory_applicability_report.json").exists()
        else {
            "schema_version": "memory_applicability_report_v1",
            "created_at": utc_now(),
            "phases": {},
        }
    )
    existing_applicability.setdefault("phases", {})[phase] = {
        "source_char_count": len(source_text),
        "chapters": sorted(chapters),
        "input_memory_count": len(memory_items),
        "included_memory_count": len(included),
        "excluded_memory_count": len(memory_items) - len(included),
        "rows": rows,
    }
    write_json(run_dir / "memory_applicability_report.json", existing_applicability)
    filter_report: dict[str, Any] = (
        read_json(run_dir / "prompt_memory_filter_report.json")
        if (run_dir / "prompt_memory_filter_report.json").exists()
        else {
            "schema_version": "prompt_memory_filter_report_v1",
            "created_at": utc_now(),
            "phases": {},
        }
    )
    filter_report.setdefault("phases", {})[phase] = {
        "chapters": sorted(chapters),
        "included_memory_ids": [item["id"] for item in included],
        "excluded_memory_ids": [row["memory_id"] for row in rows if not row["included"]],
        "rows": rows,
    }
    write_json(run_dir / "prompt_memory_filter_report.json", filter_report)
    lines = ["# Memory Applicability Report", ""]
    for phase_name, payload in existing_applicability.get("phases", {}).items():
        lines.extend(
            [
                f"## {phase_name}",
                "",
                f"- Input memory: `{payload.get('input_memory_count')}`",
                f"- Included: `{payload.get('included_memory_count')}`",
                f"- Excluded: `{payload.get('excluded_memory_count')}`",
                "",
                "| Memory | Candidate | Source | Included | Reasons |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in payload.get("rows", []):
            lines.append(
                f"| {row.get('memory_id')} | {row.get('candidate_id') or ''} | "
                f"{row.get('source_pattern')} | {row.get('included')} | {', '.join(row.get('reasons', []))} |"
            )
        lines.append("")
    _write_text(run_dir / "memory_applicability_report.md", "\n".join(lines) + "\n")
    _write_text(run_dir / "prompt_memory_filter_report.md", "\n".join(lines).replace("Memory Applicability", "Prompt Memory Filter") + "\n")
    return included


def _filter_prompt_memory(
    run_dir: Path,
    *,
    memory_items: list[dict[str, Any]],
    phase: str,
) -> list[dict[str, Any]]:
    return _filter_prompt_memory_for_context(
        run_dir,
        memory_items=memory_items,
        phase=phase,
        source_text=_selected_validation_source_text(run_dir),
        chapters=_selected_validation_chapters(run_dir),
    )


def _validation_prompts_by_sample(
    run_dir: Path,
    *,
    workspace: Workspace | None = None,
    stable_prompt: StablePromptRecord,
    memory_items: list[dict[str, Any]],
    excluded_memory: list[dict[str, Any]],
    phase: str,
    dictionary_enabled: bool = False,
    hybrid_enabled: bool = False,
    dictionary_max_entries: int = 8,
    dictionary_max_chars: int = 500,
    memory_max_items: int = 6,
    use_approved_rules: bool = False,
    rule_max_hints: int = 4,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
) -> dict[str, str]:
    samples_path = run_dir / "selected_samples.json"
    if not samples_path.exists():
        return {}
    prompts: dict[str, str] = {}
    for sample in read_json(samples_path).get("samples", []):
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or "")
        if not sample_id:
            continue
        chapter = sample.get("chapter_id")
        chapters: set[int] = set()
        try:
            if chapter is not None:
                chapters.add(int(chapter))
        except (TypeError, ValueError):
            chapters = set()
        included = _filter_prompt_memory_for_context(
            run_dir,
            memory_items=memory_items,
            phase=f"{phase}:{sample_id}",
            source_text=str(sample.get("source_text") or ""),
            chapters=chapters,
        )
        source_text = str(sample.get("source_text") or "")
        project_slug = str(read_json(run_dir / "validation_job_state.json").get("project_slug") or "")
        if hybrid_enabled and workspace is not None:
            hybrid_context = build_hybrid_prompt_support(
                workspace,
                project_slug,
                source_text,
                mode="production",
                max_dictionary_entries=dictionary_max_entries,
                max_memory_items=memory_max_items,
                use_approved_rules=use_approved_rules,
                max_rule_hints=rule_max_hints,
                max_support_chars=support_max_chars,
                chapters=chapters,
            )
            dictionary_context = _dictionary_prompt_empty_context(project_slug, source_text)
            if not hybrid_context.get("selected_items"):
                hybrid_context = _hybrid_prompt_empty_context(project_slug, source_text)
        else:
            hybrid_context = _hybrid_prompt_empty_context(project_slug, source_text)
            dictionary_context = (
                build_dictionary_prompt_support(
                    workspace,
                    project_slug,
                    source_text,
                    max_entries=dictionary_max_entries,
                    max_chars=dictionary_max_chars,
                )
                if dictionary_enabled and workspace is not None
                else _dictionary_prompt_empty_context(project_slug, source_text)
            )
        prompt = _validation_prompt(
            stable_prompt,
            included_memory=included,
            excluded_memory=excluded_memory,
            phase=f"{phase}:{sample_id}",
            dictionary_block=dictionary_context.get("block_text") if dictionary_enabled else None,
            support_block=hybrid_context.get("block_text") if hybrid_enabled else None,
        )
        if hybrid_enabled:
            _record_dictionary_prompt_artifacts(
                run_dir,
                phase=f"{phase}:{sample_id}",
                sample=sample,
                context=hybrid_context,
                prompt_text=prompt,
            )
        elif dictionary_enabled or emit_prompt_artifacts:
            _record_dictionary_prompt_artifacts(
                run_dir,
                phase=f"{phase}:{sample_id}",
                sample=sample,
                context=dictionary_context,
                prompt_text=prompt,
            )
        prompts[sample_id] = prompt
    return prompts



def _phase_effectively_has_no_support(run_dir: Path, phase: str) -> bool:
    support_payload = read_json(run_dir / "prompt_support_items.json") if (run_dir / "prompt_support_items.json").exists() else {"phases": {}}
    filter_payload = read_json(run_dir / "prompt_memory_filter_report.json") if (run_dir / "prompt_memory_filter_report.json").exists() else {"phases": {}}
    phase_support = support_payload.get("phases", {}).get(phase, {})
    phase_filter = filter_payload.get("phases", {})
    sample_phase_prefix = f"{phase}:"
    for phase_name, rows in (support_payload.get("phases", {}) or {}).items():
        if phase_name != phase and not phase_name.startswith(sample_phase_prefix):
            continue
        for row in (rows or {}).values():
            row = row or {}
            if row.get("selected_items") or row.get("dictionary_hits_selected"):
                return False
    for phase_name, payload in phase_filter.items():
        if phase_name == phase and (payload or {}).get("included_memory_ids"):
            return False
        if phase_name.startswith(sample_phase_prefix) and (payload or {}).get("included_memory_ids"):
            return False
    return True


def _sample_ids_without_effective_support(run_dir: Path, phase: str) -> list[str]:
    samples_path = run_dir / "selected_samples.json"
    if not samples_path.exists():
        return []
    sample_ids = [
        str(sample.get("sample_id") or "")
        for sample in (read_json(samples_path).get("samples") or [])
        if isinstance(sample, dict) and sample.get("sample_id")
    ]
    if not sample_ids:
        return []

    supported_sample_ids: set[str] = set()
    support_payload = read_json(run_dir / "prompt_support_items.json") if (run_dir / "prompt_support_items.json").exists() else {"phases": {}}
    for sample_id in sample_ids:
        sample_phase = f"{phase}:{sample_id}"
        for row in ((support_payload.get("phases") or {}).get(sample_phase) or {}).values():
            row = row or {}
            if row.get("selected_items") or row.get("dictionary_hits_selected"):
                supported_sample_ids.add(sample_id)
                break

    filter_payload = read_json(run_dir / "prompt_memory_filter_report.json") if (run_dir / "prompt_memory_filter_report.json").exists() else {"phases": {}}
    for sample_id in sample_ids:
        sample_phase = f"{phase}:{sample_id}"
        if ((filter_payload.get("phases") or {}).get(sample_phase) or {}).get("included_memory_ids"):
            supported_sample_ids.add(sample_id)

    return [sample_id for sample_id in sample_ids if sample_id not in supported_sample_ids]


def _reuse_baseline_as_memory_phase(round_dir: Path, *, baseline_report: dict[str, Any]) -> dict[str, Any]:
    write_json(round_dir / "memory_evaluation.json", baseline_report)
    _copy_file_if_exists(round_dir / "baseline_evaluation.md", round_dir / "memory_evaluation.md")
    _copy_file_if_exists(round_dir / "baseline_provider_retry_log.json", round_dir / "memory_provider_retry_log.json")
    _copy_file_if_exists(round_dir / "baseline_compression_log.json", round_dir / "memory_compression_log.json")
    _copy_dir_if_exists(round_dir / "baseline_outputs", round_dir / "memory_outputs")
    write_json(
        round_dir / "memory_phase_reused_from_baseline.json",
        {
            "schema_version": "approved_memory_validation_reused_phase_v1",
            "reason": "no_effective_prompt_support",
            "reused_from_phase": "baseline",
            "created_at": utc_now(),
        },
    )
    return baseline_report

def _reuse_baseline_for_unsupported_samples(
    *,
    round_dir: Path,
    eval_run: Path,
    sample_ids: list[str],
) -> list[str]:
    baseline_outputs = round_dir / "baseline_outputs"
    memory_outputs = eval_run / "translation_outputs"
    reused_sample_ids: list[str] = []
    for sample_id in sample_ids:
        source_dir = baseline_outputs / sample_id
        if not source_dir.exists():
            continue
        _copy_dir_if_exists(source_dir, memory_outputs / sample_id)
        reused_sample_ids.append(sample_id)

    if not reused_sample_ids:
        return []

    baseline_metadata_path = baseline_outputs / "translation_metadata.json"
    memory_metadata_path = memory_outputs / "translation_metadata.json"
    if baseline_metadata_path.exists() and memory_metadata_path.exists():
        baseline_metadata = read_json(baseline_metadata_path)
        memory_metadata = read_json(memory_metadata_path)
        memory_metadata.setdefault("samples", {})
        for sample_id in reused_sample_ids:
            baseline_sample = (baseline_metadata.get("samples") or {}).get(sample_id)
            if baseline_sample is not None:
                memory_metadata["samples"][sample_id] = baseline_sample
        write_json(memory_metadata_path, memory_metadata)

    write_json(
        round_dir / "memory_sample_reuse_from_baseline.json",
        {
            "schema_version": "approved_memory_validation_reused_samples_v1",
            "reason": "no_effective_prompt_support_for_sample",
            "reused_from_phase": "baseline",
            "sample_ids": reused_sample_ids,
            "created_at": utc_now(),
        },
    )
    return reused_sample_ids


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
    exclude_candidate_ids: str | None,
    candidate_ablation_top_n: int,
    prefer_no_compression_window: bool,
    allow_skip_unsafe_chapter_sample: bool,
    use_approved_dictionary: bool,
    use_hybrid_prompt: bool,
    dictionary_max_entries: int,
    dictionary_max_chars: int,
    memory_max_items: int,
    use_approved_rules: bool,
    rule_max_hints: int,
    support_max_chars: int,
    emit_prompt_artifacts: bool,
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
        "exclude_candidate_ids": exclude_candidate_ids,
        "candidate_ablation_top_n": candidate_ablation_top_n,
        "prefer_no_compression_window": prefer_no_compression_window,
        "allow_skip_unsafe_chapter_sample": allow_skip_unsafe_chapter_sample,
        "use_approved_dictionary": use_approved_dictionary,
        "use_hybrid_prompt": use_hybrid_prompt,
        "use_approved_rules": use_approved_rules,
        "dictionary_max_entries": dictionary_max_entries,
        "dictionary_max_chars": dictionary_max_chars,
        "memory_max_items": memory_max_items,
        "rule_max_hints": rule_max_hints,
        "support_max_chars": support_max_chars,
        "emit_prompt_artifacts": emit_prompt_artifacts,
        "comparison_mode": "hybrid_prompt_rules_support" if use_approved_rules else "hybrid_prompt_support" if use_hybrid_prompt else "approved_dictionary_prompt_support" if use_approved_dictionary else "approved_memory",
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
    try:
        samples = _select_requested_chapter_samples(
            eval_run,
            state["chapters"],
            raw_path=Path(state["raw_path"]),
            translated_path=Path(state["translated_path"]),
            project_slug=str(state["project_slug"]),
            workspace_path=Path(state["workspace"]),
            explicit_exclude_candidate_ids=state.get("exclude_candidate_ids"),
            candidate_ablation_top_n=int(state.get("candidate_ablation_top_n") or 5),
            allow_skip_unsafe_chapter_sample=bool(state.get("allow_skip_unsafe_chapter_sample")),
        )
    except ValueError:
        _copy_dataset_diagnostics(eval_run, run_dir)
        raise
    low_alignment = [
        sample
        for sample in samples
        if float(sample.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD
        or sample.get("accepted_for_stable_validation") is False
    ]
    if low_alignment:
        raise ValueError("Low-alignment sample was selected; approved-memory validation is blocked.")
    _copy_dataset_diagnostics(eval_run, run_dir)
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
    project_slug: str = "",
    workspace_path: Path | None = None,
    explicit_exclude_candidate_ids: str | None = None,
    candidate_ablation_top_n: int = 5,
    allow_skip_unsafe_chapter_sample: bool = False,
) -> list[dict[str, Any]]:
    raw_chapters = extract_raw_chapters(raw_path, max_chapters=max(chapters))
    target_chapters = extract_epub_chapters(
        translated_path,
        max_chapters=max(chapters) * 3,
    )
    title_target_map, title_map_rows = _chapter_title_target_map(raw_chapters, target_chapters)
    title_map_by_chapter = {
        int(row.get("source_chapter_id") or 0): row
        for row in title_map_rows
    }
    raw_by_id = {int(chapter["chapter_id"]): chapter for chapter in raw_chapters}
    target_by_id = {int(chapter["chapter_id"]): chapter for chapter in target_chapters}
    for chapter in chapters:
        chapter_id = int(chapter)
        if chapter_id not in title_target_map and chapter_id in target_by_id:
            target_chapter = target_by_id[chapter_id]
            ratio = len(str(target_chapter.get("text") or "")) / max(1, len(str(raw_by_id.get(chapter_id, {}).get("text") or "")))
            if 0.25 <= ratio <= 5.0 and chapter_number(str(target_chapter.get("title") or "")) == chapter_id:
                title_target_map[chapter_id] = [chapter_id]
                title_map_by_chapter[chapter_id] = {
                    "source_chapter_id": chapter_id,
                    "source_title": (raw_by_id.get(chapter_id) or {}).get("title"),
                    "target_chapter_ids": [chapter_id],
                    "target_titles": [target_chapter.get("title")],
                    "match_confidence": 0.5,
                    "length_ratio": round(ratio, 3),
                    "status": "mapped_by_numbered_spine_order_fallback",
                }
                title_map_rows.append(
                    {
                        "source_chapter_id": chapter_id,
                        "source_title": (raw_by_id.get(chapter_id) or {}).get("title"),
                        "target_chapter_ids": [chapter_id],
                        "target_titles": [target_chapter.get("title")],
                        "match_confidence": 0.5,
                        "length_ratio": round(ratio, 3),
                        "status": "mapped_by_numbered_spine_order_fallback",
                    }
                )
    all_candidates: list[dict[str, Any]] = []
    selected_candidates: list[dict[str, Any]] = []
    ranking_rows: list[dict[str, Any]] = []
    selected = []
    file_exclusions = (
        _load_validation_candidate_exclusions(workspace_path, project_slug=project_slug)
        if workspace_path and project_slug
        else []
    )
    cli_exclusions = _parse_excluded_candidate_ids(explicit_exclude_candidate_ids)
    all_exclusions = [*file_exclusions, *cli_exclusions]
    global_source_blocks = build_alignment_blocks(raw_chapters, lang="zh")
    global_target_blocks = build_alignment_blocks(target_chapters, lang="vi")
    global_block_pairs = align_blocks_monotonic(global_source_blocks, global_target_blocks)
    global_candidates = build_alignment_candidates(
        global_source_blocks,
        global_target_blocks,
        global_block_pairs,
        max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
        max_target_chars=DEFAULT_LEARNING_MAX_TARGET_CHARS,
    )
    used_exclusions: list[dict[str, Any]] = []
    for chapter in chapters:
        expected_targets = set(title_target_map.get(int(chapter), []))
        source_chapter = raw_by_id.get(int(chapter))
        target_subset = [
            target_by_id[target_id]
            for target_id in sorted(expected_targets)
            if target_id in target_by_id
        ]
        if not source_chapter or not target_subset:
            raise ValueError(
                f"No title-mapped target chapter found for requested chapter {chapter}."
            )
        source_blocks = build_alignment_blocks([source_chapter], lang="zh")
        target_blocks = build_alignment_blocks(target_subset, lang="vi")
        block_pairs = align_blocks_monotonic(source_blocks, target_blocks)
        candidates = build_alignment_candidates(
            source_blocks,
            target_blocks,
            block_pairs,
            max_source_chars=DEFAULT_LEARNING_MAX_SOURCE_CHARS,
            max_target_chars=DEFAULT_LEARNING_MAX_TARGET_CHARS,
        )
        for candidate in candidates:
            candidate["title_mapped_target_chapter_ids"] = sorted(expected_targets)
            candidate["title_map_status"] = "matched"
        all_candidates.extend(candidates)
        candidate_samples: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            if int(candidate.get("source_chapter_id") or 0) != int(chapter):
                continue
            if not candidate.get("accepted") or float(candidate.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD:
                ranking_rows.append(
                    {
                        "chapter": int(chapter),
                        "candidate_id": candidate.get("candidate_id"),
                        "alignment_quality": candidate.get("alignment_quality"),
                        "source_chars": candidate.get("source_char_count"),
                        "reference_chars": candidate.get("target_char_count"),
                        "ratio": candidate.get("target_source_length_ratio"),
                        "risk_score": 999,
                        "compression_risk": "alignment_rejected",
                        "accepted": False,
                        "selected": False,
                        "rejected_reasons": sorted(set(str(reason) for reason in candidate.get("rejection_reasons", []))),
                    }
                )
                continue
            sample, safety = _locked_validation_sample_from_candidate(
                candidate,
                sample_id=f"sample_{len(selected) + 1}",
            )
            exclusion = _candidate_is_excluded(
                project_slug=project_slug,
                chapter=int(chapter),
                candidate=candidate,
                exclusions=all_exclusions,
            )
            if exclusion:
                safety = dict(safety)
                safety["accepted"] = False
                safety["risk_score"] = int(safety.get("risk_score") or 0) + 100
                safety["compression_risk"] = "excluded"
                safety["hard_rejections"] = [
                    *list(safety.get("hard_rejections") or []),
                    "excluded_previous_severe_failure",
                ]
                safety["boundary_warnings"] = [
                    *list(safety.get("boundary_warnings") or []),
                    "excluded_previous_severe_failure",
                ]
                sample["validation_unit_safety"] = safety
                used_exclusions.append(exclusion)
            candidate_samples.append((candidate, sample, safety))
            ranking_rows.append(
                _candidate_ranking_row(
                    chapter=int(chapter),
                    candidate=candidate,
                    safety=safety,
                )
            )
        safe_candidates = [
            (candidate, sample, safety)
            for candidate, sample, safety in candidate_samples
            if safety["accepted"]
        ]
        if not safe_candidates:
            for candidate in global_candidates:
                if (
                    not candidate.get("accepted")
                    or int(candidate.get("source_chapter_id") or 0) != int(chapter)
                    or int(candidate.get("target_chapter_id") or 0) not in expected_targets
                    or float(candidate.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD
                ):
                    continue
                sample, safety = _locked_validation_sample_from_candidate(
                    candidate,
                    sample_id=f"sample_{len(selected) + 1}",
                )
                candidate["title_mapped_target_chapter_ids"] = sorted(expected_targets)
                candidate["title_map_status"] = "global_body_window_fallback"
                candidate_samples.append((candidate, sample, safety))
                ranking_rows.append(
                    _candidate_ranking_row(
                        chapter=int(chapter),
                        candidate=candidate,
                        safety=safety,
                    )
                )
            safe_candidates = [
                (candidate, sample, safety)
                for candidate, sample, safety in candidate_samples
                if safety["accepted"]
            ]
        if not safe_candidates:
            chapter_rows = [row for row in ranking_rows if row["chapter"] == int(chapter)]
            chapter_rows.sort(
                key=lambda row: (
                    int(row.get("risk_score") or 0),
                    -float(row.get("alignment_quality") or 0),
                    -int(row.get("source_chars") or 0),
                )
            )
            if int(chapter) in {8, 10}:
                _write_chapter_ablation_report(
                    eval_run,
                    chapter=int(chapter),
                    rows=chapter_rows,
                    selected_candidate_id=None,
                    previous_exclusions=all_exclusions,
                    top_n=candidate_ablation_top_n,
                )
            if int(chapter) == 10:
                _write_chapter_10_rebuild_report(
                    eval_run,
                    source_chapter=source_chapter,
                    target_subset=target_subset,
                    match_row=title_map_by_chapter.get(10),
                    rows=chapter_rows,
                    selected_candidate_id=None,
                    selected_sample=None,
                )
            _write_unit_candidate_ranking(eval_run, ranking_rows)
            _write_selected_validation_units(eval_run, selected)
            write_json(
                eval_run / "excluded_validation_candidates.json",
                {
                    "schema_version": "approved_memory_validation_excluded_candidates_v1",
                    "project": project_slug,
                    "used_exclusion_count": len(used_exclusions),
                    "available_exclusion_count": len(all_exclusions),
                    "used_exclusions": used_exclusions,
                    "created_at": utc_now(),
                },
            )
            if allow_skip_unsafe_chapter_sample:
                continue
            expected_text = (
                ", ".join(str(item) for item in sorted(expected_targets))
                if expected_targets
                else "unmapped"
            )
            raise ValueError(
                f"No reliable title-matched alignment sample found for requested chapter {chapter} "
                f"(expected target chapters: {expected_text})."
            )
        safe_candidates.sort(
            key=lambda item: (
                int(item[2].get("risk_score") or 0),
                -float(item[0].get("alignment_quality") or 0),
                -len(item[0].get("shared_anchors") or []),
                -int(item[0].get("source_char_count") or 0),
            ),
        )
        selected_candidate, selected_sample, selected_safety = safe_candidates[0]
        selected_candidates.append(selected_candidate)
        selected.append(selected_sample)
        for row in ranking_rows:
            if row["chapter"] == int(chapter) and row["candidate_id"] == selected_candidate.get("candidate_id"):
                row["selected"] = True
                row["accepted"] = True
                row["rejected_reasons"] = []
        if int(chapter) in {8, 10}:
            chapter_rows = [row for row in ranking_rows if row["chapter"] == int(chapter)]
            chapter_rows.sort(
                key=lambda row: (
                    int(row.get("risk_score") or 0),
                    -float(row.get("alignment_quality") or 0),
                    -int(row.get("source_chars") or 0),
                )
            )
            _write_chapter_ablation_report(
                eval_run,
                chapter=int(chapter),
                rows=chapter_rows,
                selected_candidate_id=str(selected_candidate.get("candidate_id")),
                previous_exclusions=all_exclusions,
                top_n=candidate_ablation_top_n,
            )
            if int(chapter) == 10:
                _write_chapter_10_rebuild_report(
                    eval_run,
                    source_chapter=source_chapter,
                    target_subset=target_subset,
                    match_row=title_map_by_chapter.get(10),
                    rows=chapter_rows,
                    selected_candidate_id=str(selected_candidate.get("candidate_id")),
                    selected_sample=selected_sample,
                )
    write_json(eval_run / "selected_samples.json", {"samples": selected})
    write_json(eval_run / "translation_units.json", translation_units_report(selected))
    write_json(eval_run / "unit_alignment_report.json", unit_alignment_report(selected))
    _write_unit_candidate_ranking(eval_run, ranking_rows)
    _write_selected_validation_units(eval_run, selected)
    write_json(
        eval_run / "excluded_validation_candidates.json",
        {
            "schema_version": "approved_memory_validation_excluded_candidates_v1",
            "project": project_slug,
            "used_exclusion_count": len(used_exclusions),
            "available_exclusion_count": len(all_exclusions),
            "used_exclusions": used_exclusions,
            "created_at": utc_now(),
        },
    )
    write_json(
        eval_run / "alignment_candidates.json",
        {
            "schema_version": "approved_memory_validation_alignment_candidates_v2",
            "candidate_count": len(all_candidates),
            "accepted_candidate_count": sum(1 for item in all_candidates if item.get("accepted")),
            "candidates": all_candidates,
            "chapter_title_map": title_map_rows,
        },
    )
    write_json(
        eval_run / "chapter_alignment_report.json",
        {
            "schema_version": "approved_memory_validation_chapter_alignment_v2",
            "source_chapter_count": len(raw_chapters),
            "target_chapter_count": len(target_chapters),
            "chapter_title_map": title_map_rows,
            "selected_samples": [
                {
                    "sample_id": sample["sample_id"],
                    "source_chapter_id": sample["chapter_id"],
                    "target_chapter_id": candidate.get("target_chapter_id"),
                    "alignment_quality": sample.get("alignment_quality"),
                    "source_preview": candidate.get("source_text", "")[:240],
                    "target_preview": candidate.get("target_text", "")[:240],
                }
                for sample, candidate in zip(selected, selected_candidates)
            ],
            "created_at": utc_now(),
        },
    )
    write_json(
        eval_run / "approved_memory_validation_sample_selection.json",
        {
            "schema_version": "approved_memory_validation_sample_selection_v1",
            "requested_chapters": chapters,
            "selected_chapters": [sample["chapter_id"] for sample in selected],
            "selected_target_chapters": [
                int(candidate.get("target_chapter_id") or 0)
                for candidate in selected_candidates
            ],
            "chapter_title_map": title_map_rows,
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


def _phase_model(report: dict[str, Any]) -> str | None:
    best = report.get("best_model")
    if best:
        return str(best)
    models = report.get("models") or {}
    return str(next(iter(models), "")) or None


def _phase_metadata(round_dir: Path, phase: str) -> dict[str, Any]:
    path = round_dir / f"{phase}_outputs" / "translation_metadata.json"
    return read_json(path) if path.exists() else {"samples": {}}


def _phase_output_text(round_dir: Path, phase: str, relative: str | None) -> str:
    if not relative:
        return ""
    rel = Path(relative)
    parts = rel.parts
    if parts and parts[0] == "translation_outputs":
        rel = Path(*parts[1:])
    path = round_dir / f"{phase}_outputs" / rel
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _structured_text(round_dir: Path, phase: str, sample_id: str, model: str, suffix: str) -> str:
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    path = round_dir / f"{phase}_outputs" / sample_id / f"{safe_model}_{suffix}.json"
    if not path.exists():
        return ""
    payload = read_json(path)
    return "\n\n".join(
        str(item.get("text", ""))
        for item in payload.get("paragraphs", [])
        if isinstance(item, dict)
    ).strip()


def _sample_lookup(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "selected_samples.json"
    if not path.exists():
        return {}
    return {
        str(sample.get("sample_id")): sample
        for sample in read_json(path).get("samples", [])
        if isinstance(sample, dict)
    }


def _looks_like_heading_or_separator(text: str) -> bool:
    stripped = (text or "").strip()
    if re.fullmatch(r"[-–—\s]{3,}", stripped):
        return True
    tail = re.split(r"[\n。.!?！？…]+", stripped)[-1].strip()
    if re.fullmatch(r"[-–—\s]{3,}", tail):
        return True
    folded = _fold_text(tail)
    return bool(re.fullmatch(r"(?:[-–—]{2,}\s*)?(?:chuong|chapter)\s*\d+\s*[:：].{2,140}", folded))


def _root_cause_for_failure(
    *,
    sample: dict[str, Any],
    score_sample: dict[str, Any],
    metadata: dict[str, Any],
    final_text: str,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    verification = metadata.get("verification_after_compression") or {}
    if metadata.get("provider_error") or verification.get("provider_failure_empty_output"):
        return "model_output_empty_or_provider_failure", ["provider_failure_or_empty_output"]

    truncation = detect_truncated_vietnamese(final_text, source_text=sample.get("source_text", ""))
    truncated_paragraphs = score_sample.get("truncated_paragraphs") or []
    if truncation["is_truncated"] or truncated_paragraphs:
        reasons.extend(truncation.get("reasons") or [])
        for item in truncated_paragraphs:
            reasons.extend(item.get("reasons") or [])
        if reasons and set(reasons) <= {"missing_terminal_punctuation"} and _looks_like_heading_or_separator(final_text):
            return "evaluator_false_positive", sorted(set(reasons))
        if any("bracket" in reason or "parenthesis" in reason or "quote" in reason for reason in reasons):
            return "formatting/bracket safety issue", sorted(set(reasons))
        return "real_truncation", sorted(set(reasons)) or ["truncation_detected"]

    selector = metadata.get("final_output_selector") or {}
    before_verification = metadata.get("verification_before_compression") or {}
    if (
        selector.get("selected_final_output") == "after_compression"
        and before_verification.get("pass") is True
    ):
        return "wrong_final_candidate_selected", ["before_compression_passed_but_after_selected"]

    compression_entries = (metadata.get("compression") or {}).get("entries") or []
    unsafe_entries = [entry for entry in compression_entries if entry.get("unsafe_compression")]
    if unsafe_entries:
        failure_reasons = ",".join(str(entry.get("compression_failure_reason") or "") for entry in unsafe_entries)
        if "paragraph_exceeds_relaxed_budget" in failure_reasons:
            unit_ratios = [
                float(entry.get("paragraph_ratio") or 0)
                for entry in unsafe_entries
                if entry.get("paragraph_ratio") is not None
            ]
            if sample.get("alignment_warnings") or any(ratio >= 1.85 for ratio in unit_ratios):
                return "unit_merge_boundary_problem", [
                    "unsafe_compression_due_over_budget_unit",
                    *sorted({reason for entry in unsafe_entries for reason in str(entry.get("compression_failure_reason") or "").split(",") if reason}),
                ]
            return "over_strict_micro_unit_budget", ["paragraph_exceeds_relaxed_budget"]
        return "unsafe_compression_rewrite", [
            reason
            for entry in unsafe_entries
            for reason in str(entry.get("compression_failure_reason") or "unsafe_compression").split(",")
            if reason
        ]

    if score_sample.get("unsafe_compression_paragraphs") or "unsafe_compression" in (score_sample.get("verification_reasons") or []):
        return "missing_diagnostics", ["unsafe_flag_without_compression_entry"]

    return "missing_diagnostics", ["severe_flag_without_cached_details"]


def replay_approved_memory_validation(workspace: Workspace, *, run: str) -> dict[str, Any]:
    run_dir = resolve_validation_run(workspace, run)
    if not (run_dir / "validation_job_state.json").exists():
        raise ValueError(f"Not an approved-memory validation run: {run_dir}")
    state = read_json(run_dir / "validation_job_state.json")
    samples = _sample_lookup(run_dir)
    rows: list[dict[str, Any]] = []
    for round_index in range(1, int(state.get("rounds") or DEFAULT_APPROVED_MEMORY_VALIDATION_ROUNDS) + 1):
        round_dir = run_dir / f"round_{round_index}"
        for phase in ("baseline", "memory"):
            report_path = round_dir / f"{phase}_evaluation.json"
            if not report_path.exists():
                continue
            report = read_json(report_path)
            model = _phase_model(report)
            if not model:
                continue
            model_report = (report.get("models") or {}).get(model, {})
            metadata_root = _phase_metadata(round_dir, phase)
            for score_sample in model_report.get("samples", []) or []:
                sample_id = str(score_sample.get("sample_id"))
                has_truncation = bool(score_sample.get("truncated_paragraphs"))
                has_unsafe = bool(score_sample.get("unsafe_compression_paragraphs")) or (
                    "unsafe_compression" in (score_sample.get("verification_reasons") or [])
                )
                gates = score_sample.get("gates") or {}
                if not (
                    has_truncation
                    or has_unsafe
                    or gates.get("severe_hallucination")
                    or gates.get("wrong_main_character_name")
                    or gates.get("major_skipped_passage")
                ):
                    continue
                metadata = (
                    (metadata_root.get("samples") or {})
                    .get(sample_id, {})
                    .get(model, {})
                )
                sample = samples.get(sample_id, {})
                before = _structured_text(round_dir, phase, sample_id, model, "structured_initial")
                after = _structured_text(round_dir, phase, sample_id, model, "structured_after_compression")
                final = _structured_text(round_dir, phase, sample_id, model, "structured_final")
                if not final:
                    final = _phase_output_text(round_dir, phase, metadata.get("path"))
                root_cause, root_reasons = _root_cause_for_failure(
                    sample=sample,
                    score_sample=score_sample,
                    metadata=metadata,
                    final_text=final,
                )
                compression_entries = (metadata.get("compression") or {}).get("entries") or []
                rows.append(
                    {
                        "round": round_index,
                        "phase": phase,
                        "model": model,
                        "sample_id": sample_id,
                        "chapter_number": score_sample.get("chapter_id") or sample.get("chapter_id"),
                        "candidate_id": sample.get("block_alignment_candidate_id"),
                        "source_hash": sha256_text(str(sample.get("source_text", ""))),
                        "reference_hash": sha256_text(str(sample.get("target_text", ""))),
                        "source_text": sample.get("source_text", ""),
                        "human_reference": sample.get("target_text", ""),
                        "model_output_before_compression": before,
                        "model_output_after_compression": after,
                        "selected_final_output": final,
                        "selected_final_output_mode": metadata.get("selected_final_output"),
                        "final_output_selector_decision": metadata.get("selected_final_output_reason"),
                        "truncation_reasons": sorted(
                            set(
                                reason
                                for item in score_sample.get("truncated_paragraphs", []) or []
                                for reason in item.get("reasons", [])
                            )
                        ),
                        "unsafe_compression_reasons": sorted(
                            set(
                                str(entry.get("compression_failure_reason") or "unsafe_compression")
                                for entry in compression_entries
                                if entry.get("unsafe_compression")
                            )
                        ),
                        "output_reference_ratio": score_sample.get("output_reference_ratio"),
                        "global_ratio_before_compression": metadata.get("global_ratio_before_compression"),
                        "global_ratio_after_compression": metadata.get("global_ratio_after_compression"),
                        "compression_attempts": [
                            {
                                "paragraph_id": entry.get("paragraph_id"),
                                "attempt_count": entry.get("compression_attempt_count"),
                                "unsafe_compression": entry.get("unsafe_compression"),
                                "failure_reason": entry.get("compression_failure_reason"),
                                "paragraph_ratio": entry.get("paragraph_ratio"),
                            }
                            for entry in compression_entries
                        ],
                        "root_cause": root_cause,
                        "root_cause_reasons": root_reasons,
                    }
                )

    replay_payload = {
        "schema_version": "approved_memory_validation_failing_samples_replay_v1",
        "validation_run_id": run_dir.name,
        "failure_count": len(rows),
        "root_cause_counts": {
            cause: sum(1 for row in rows if row["root_cause"] == cause)
            for cause in sorted({row["root_cause"] for row in rows})
        },
        "failures": rows,
        "created_at": utc_now(),
    }
    write_json(run_dir / "failing_samples_report.json", replay_payload)
    write_json(run_dir / "latest_safety_replay.json", replay_payload)
    with (run_dir / "safety_failure_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "round",
                "phase",
                "sample_id",
                "candidate_id",
                "chapter_number",
                "root_cause",
                "output_reference_ratio",
                "selected_final_output_mode",
                "final_output_selector_decision",
                "truncation_reasons",
                "unsafe_compression_reasons",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "round": row["round"],
                    "phase": row["phase"],
                    "sample_id": row["sample_id"],
                    "candidate_id": row.get("candidate_id"),
                    "chapter_number": row["chapter_number"],
                    "root_cause": row["root_cause"],
                    "output_reference_ratio": row["output_reference_ratio"],
                    "selected_final_output_mode": row["selected_final_output_mode"],
                    "final_output_selector_decision": row["final_output_selector_decision"],
                    "truncation_reasons": ";".join(row["truncation_reasons"]),
                    "unsafe_compression_reasons": ";".join(row["unsafe_compression_reasons"]),
                }
            )
    lines = [
        "# Failing Samples Replay",
        "",
        f"- Validation run: `{run_dir.name}`",
        f"- Failure count: `{len(rows)}`",
        "",
        "| Round | Phase | Sample | Chapter | Root cause | Ratio |",
        "| ---: | --- | --- | ---: | --- | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['round']} | {row['phase']} | {row['sample_id']} | "
            f"{row['chapter_number']} | {row['root_cause']} | {row.get('output_reference_ratio')} |"
        )
    lines.append("")
    for row in rows:
        lines.extend(
            [
                f"## Round {row['round']} {row['phase']} {row['sample_id']}",
                "",
                f"- Root cause: `{row['root_cause']}`",
                f"- Reasons: `{json_dumps(row['root_cause_reasons'])}`",
                f"- Selector: `{row.get('selected_final_output_mode')}` / `{row.get('final_output_selector_decision')}`",
                "",
                "Source:",
                "",
                row.get("source_text", "")[:1800],
                "",
                "Human reference:",
                "",
                row.get("human_reference", "")[:1800],
                "",
                "Model before compression:",
                "",
                row.get("model_output_before_compression", "")[:1800],
                "",
                "Model after compression:",
                "",
                row.get("model_output_after_compression", "")[:1800],
                "",
                "Selected final output:",
                "",
                row.get("selected_final_output", "")[:1800],
                "",
            ]
        )
    replay_markdown = "\n".join(lines) + "\n"
    _write_text(run_dir / "failing_samples_report.md", replay_markdown)
    _write_text(run_dir / "latest_safety_replay.md", replay_markdown)
    targeted = [
        row
        for row in rows
        if int(row.get("chapter_number") or 0) in {8, 10}
    ]
    write_json(
        run_dir / "targeted_failure_report.json",
        {
            "schema_version": "approved_memory_validation_targeted_failure_report_v1",
            "chapters": [8, 10],
            "failure_count": len(targeted),
            "failures": targeted,
            "created_at": utc_now(),
        },
    )
    targeted_lines = [
        "# Targeted Chapter 8/10 Failure Report",
        "",
        f"- Validation run: `{run_dir.name}`",
        f"- Failure count: `{len(targeted)}`",
        "",
        "| Chapter | Round | Phase | Sample | Candidate | Root cause | Ratio |",
        "| ---: | ---: | --- | --- | --- | --- | ---: |",
    ]
    for row in targeted:
        targeted_lines.append(
            f"| {row['chapter_number']} | {row['round']} | {row['phase']} | {row['sample_id']} | "
            f"{row.get('candidate_id')} | {row['root_cause']} | {row.get('output_reference_ratio')} |"
        )
    for row in targeted:
        targeted_lines.extend(
            [
                "",
                f"## Chapter {row['chapter_number']} {row['phase']} {row['sample_id']}",
                "",
                f"- Candidate: `{row.get('candidate_id')}`",
                f"- Root cause: `{row['root_cause']}`",
                f"- Compression reasons: `{json_dumps(row.get('unsafe_compression_reasons', []))}`",
                "",
                "Source:",
                "",
                row.get("source_text", "")[:1600],
                "",
                "Reference:",
                "",
                row.get("human_reference", "")[:1600],
                "",
                "Before compression:",
                "",
                row.get("model_output_before_compression", "")[:1600],
                "",
                "After compression:",
                "",
                row.get("model_output_after_compression", "")[:1600],
                "",
                "Selected final:",
                "",
                row.get("selected_final_output", "")[:1600],
            ]
        )
    _write_text(run_dir / "targeted_failure_report.md", "\n".join(targeted_lines) + "\n")

    state = read_json(run_dir / "validation_job_state.json")
    exclusions: list[dict[str, Any]] = []
    for row in rows:
        if row.get("root_cause") not in {
            "unit_merge_boundary_problem",
            "over_strict_micro_unit_budget",
            "unsafe_compression_rewrite",
            "real_truncation",
            "formatting/bracket safety issue",
        }:
            continue
        candidate_id = row.get("candidate_id")
        if not candidate_id:
            continue
        exclusions.append(
            {
                "project": state.get("project_slug"),
                "validation_purpose": "approved_memory_validation",
                "chapter": int(row.get("chapter_number") or 0),
                "candidate_id": candidate_id,
                "exclusion_reason": row.get("root_cause"),
                "evidence_run_id": run_dir.name,
                "source_hash": row.get("source_hash"),
                "reference_hash": row.get("reference_hash"),
                "created_at": utc_now(),
            }
        )
    if exclusions:
        _write_validation_candidate_exclusions(workspace.path, exclusions)
    write_json(
        run_dir / "validation_candidate_exclusions.json",
        {
            "schema_version": "validation_candidate_exclusions_evidence_v1",
            "exclusion_count": len(exclusions),
            "exclusions": exclusions,
            "created_at": utc_now(),
        },
    )
    return {
        "validation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "failure_count": len(rows),
        "targeted_failure_count": len(targeted),
        "exclusion_count": len(exclusions),
        "root_cause_counts": {
            cause: sum(1 for row in rows if row["root_cause"] == cause)
            for cause in sorted({row["root_cause"] for row in rows})
        },
        "report_paths": {
            "json": str(run_dir / "failing_samples_report.json"),
            "markdown": str(run_dir / "failing_samples_report.md"),
            "csv": str(run_dir / "safety_failure_table.csv"),
            "targeted_json": str(run_dir / "targeted_failure_report.json"),
            "targeted_markdown": str(run_dir / "targeted_failure_report.md"),
            "latest_safety_json": str(run_dir / "latest_safety_replay.json"),
            "latest_safety_markdown": str(run_dir / "latest_safety_replay.md"),
            "exclusions": str(run_dir / "validation_candidate_exclusions.json"),
        },
    }


def _write_round_comparison(round_dir: Path, round_index: int, delta: dict[str, Any]) -> None:
    write_json(round_dir / "score_delta.json", delta)
    comparison_label = (
        "Hybrid"
        if delta.get("comparison_mode") == "hybrid_prompt_support"
        else "Dictionary"
        if delta.get("comparison_mode") == "approved_dictionary_prompt_support"
        else "Memory"
    )
    lines = [
        f"# Round {round_index} Comparison",
        "",
        f"- Baseline score: `{delta['baseline_score']}`",
        f"- {comparison_label} score: `{delta['memory_score']}`",
        f"- Delta: `{delta['score_delta']}`",
        f"- Regressions > 3: `{len(delta['regressions_over_3'])}`",
        f"- Severe flags: `{len(delta['severe_flags'])}`",
        "",
        f"| Sample | Chapter | Baseline | {comparison_label} | Delta |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in delta["sample_deltas"]:
        lines.append(
            f"| {row['sample_id']} | {row.get('chapter_id')} | {row.get('baseline_score')} | "
            f"{row.get('memory_score')} | {row['delta']} |"
        )
    _write_text(round_dir / "comparison_report.md", "\n".join(lines) + "\n")


def _flatten_prompt_phase_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phase, samples in (payload.get("phases") or {}).items():
        if not isinstance(samples, dict):
            continue
        for sample_id, row in samples.items():
            if isinstance(row, dict):
                rows.append({"phase": phase, "sample_id": sample_id, **row})
    return rows


def _write_dictionary_prompt_review_package(run_dir: Path, state: dict[str, Any]) -> str | None:
    if not state.get("use_approved_dictionary"):
        return None
    review_dir = (
        Path(state["workspace"])
        / "artifacts"
        / "dictionaries"
        / str(state["validation_run_id"])
        / "dictionary_prompt_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    context_payload = read_json(run_dir / "prompt_context_bundle.json") if (run_dir / "prompt_context_bundle.json").exists() else {"phases": {}}
    budget_payload = read_json(run_dir / "prompt_budget_report.json") if (run_dir / "prompt_budget_report.json").exists() else {"phases": {}}
    retrieval_payload = read_json(run_dir / "prompt_retrieval_report.json") if (run_dir / "prompt_retrieval_report.json").exists() else {"phases": {}}
    dictionary_used = read_json(run_dir / "approved_dictionary_used.json") if (run_dir / "approved_dictionary_used.json").exists() else {"entry_count": 0, "entries": []}
    context_rows = _flatten_prompt_phase_rows(context_payload)
    budget_rows = _flatten_prompt_phase_rows(budget_payload)
    retrieval_rows = _flatten_prompt_phase_rows(retrieval_payload)
    selected_rows: list[dict[str, Any]] = []
    dropped_rows: list[dict[str, Any]] = []
    for row in context_rows:
        for hit in row.get("dictionary_hits_selected", []) or []:
            selected_rows.append(
                {
                    "phase": row.get("phase"),
                    "sample_id": row.get("sample_id"),
                    "chapter_id": row.get("chapter_id"),
                    **hit,
                }
            )
        for hit in row.get("dictionary_hits_dropped_due_budget", []) or []:
            dropped_rows.append(
                {
                    "phase": row.get("phase"),
                    "sample_id": row.get("sample_id"),
                    "chapter_id": row.get("chapter_id"),
                    **hit,
                }
            )
    unique_used = {
        str(row.get("entry_id")): row
        for row in selected_rows
        if row.get("entry_id")
    }
    rounds = state.get("round_results", [])
    severe_count = sum(len(row.get("severe_flags", []) or []) for row in rounds)
    regressions = [
        regression
        for row in rounds
        for regression in row.get("regressions_over_3", []) or []
    ]
    unsafe_count = sum(
        1
        for row in rounds
        for flag in row.get("severe_flags", []) or []
        if flag.get("reason") == "unsafe_compression"
    )
    truncation_count = sum(
        1
        for row in rounds
        for flag in row.get("severe_flags", []) or []
        if flag.get("reason") == "truncation"
    )
    with (review_dir / "selected_dictionary_hits.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "entry_id",
                "source_text",
                "target_text",
                "entry_type",
                "confidence",
            ],
        )
        writer.writeheader()
        for row in selected_rows:
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    with (review_dir / "dropped_dictionary_hits.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "entry_id",
                "source_text",
                "target_text",
                "entry_type",
                "confidence",
                "drop_reason",
            ],
        )
        writer.writeheader()
        for row in dropped_rows:
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    round_lines = [
        "# Validation Rounds Summary",
        "",
        "| Round | Baseline | Dictionary | Delta |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in rounds:
        round_lines.append(
            f"| {row.get('round')} | {row.get('baseline_score')} | {row.get('memory_score')} | {row.get('score_delta')} |"
        )
    _write_text(review_dir / "validation_rounds_summary.md", "\n".join(round_lines) + "\n")
    _write_text(
        review_dir / "dictionary_effect_summary.md",
        "# Dictionary Effect Summary\n\n"
        f"- Approved dictionary entries: `{dictionary_used.get('entry_count', 0)}`\n"
        f"- Entries used in validation: `{len(unique_used)}`\n"
        f"- Selected hit rows: `{len(selected_rows)}`\n"
        f"- Dropped hit rows: `{len(dropped_rows)}`\n"
        f"- Final decision: `{state.get('final_decision')}`\n"
        f"- Last error: `{state.get('last_error')}`\n",
    )
    budget_lines = ["# Prompt Budget Summary", ""]
    for row in budget_rows[:80]:
        budget_lines.append(
            "- "
            f"{row.get('phase')} / {row.get('sample_id')}: "
            f"selected={row.get('selected_hit_count', 0)}, "
            f"dropped={row.get('dropped_hit_count', 0)}, "
            f"chars={row.get('support_chars', 0)}"
        )
    _write_text(review_dir / "prompt_budget_summary.md", "\n".join(budget_lines) + "\n")
    retrieval_lines = ["# Prompt Retrieval Examples", ""]
    for row in retrieval_rows[:12]:
        retrieval_lines.append(f"## {row.get('phase')} / {row.get('sample_id')}")
        for hit in row.get("selected_hits", []) or []:
            retrieval_lines.append(f"- {hit.get('source_text')} => {hit.get('target_text')}")
        if not row.get("selected_hits"):
            retrieval_lines.append("- No dictionary hit rendered.")
        retrieval_lines.append("")
    _write_text(review_dir / "prompt_retrieval_examples.md", "\n".join(retrieval_lines) + "\n")
    prompt_samples = (run_dir / "prompt_used.md").read_text(encoding="utf-8") if (run_dir / "prompt_used.md").exists() else "# Prompt Samples\n\nNo prompt artifacts were emitted.\n"
    _write_text(review_dir / "prompt_samples.md", prompt_samples)
    human_lines = [
        "# Dictionary Prompt Human Review Summary",
        "",
        f"- Approved dictionary entry count: `{dictionary_used.get('entry_count', 0)}`",
        f"- Dictionary entries used in validation: `{len(unique_used)}`",
        f"- Top exact hits: `{', '.join(row.get('source_text', '') for row in list(unique_used.values())[:12])}`",
    ]
    for row in rounds:
        human_lines.append(
            f"- Round {row.get('round')}: baseline `{row.get('baseline_score')}`, dictionary `{row.get('memory_score')}`, delta `{row.get('score_delta')}`"
        )
    human_lines.extend(
        [
            f"- Severe flag count: `{severe_count}`",
            f"- Unsafe compression count: `{unsafe_count}`",
            f"- Truncation count: `{truncation_count}`",
            f"- Chapter regressions over 3: `{len(regressions)}`",
            f"- Recommendation: `{'PROCEED_TO_FULL_MVP5H' if state.get('final_decision') == 'PASS' else 'REVIEW_DICTIONARY_PROMPT_EFFECT'}`",
            "",
            "No pending or rejected dictionary candidates are rendered by the prompt support block.",
            "Raw NLP cache is not injected into prompts.",
        ]
    )
    _write_text(review_dir / "human_review_summary.md", "\n".join(human_lines) + "\n")
    state["dictionary_prompt_review_path"] = str(review_dir)
    return str(review_dir)


def _write_hybrid_prompt_review_package(run_dir: Path, state: dict[str, Any]) -> str | None:
    if not state.get("use_hybrid_prompt"):
        return None
    review_root = "hybrid_prompt_rules" if state.get("use_approved_rules") else "hybrid_prompt"
    review_dir = (
        Path(state["workspace"])
        / "artifacts"
        / review_root
        / str(state["validation_run_id"])
        / "human_review"
    )
    review_dir.mkdir(parents=True, exist_ok=True)
    context_payload = read_json(run_dir / "prompt_context_bundle.json") if (run_dir / "prompt_context_bundle.json").exists() else {"phases": {}}
    budget_payload = read_json(run_dir / "prompt_budget_report.json") if (run_dir / "prompt_budget_report.json").exists() else {"phases": {}}
    retrieval_payload = read_json(run_dir / "prompt_retrieval_report.json") if (run_dir / "prompt_retrieval_report.json").exists() else {"phases": {}}
    conflict_payload = read_json(run_dir / "prompt_conflict_report.json") if (run_dir / "prompt_conflict_report.json").exists() else {"phases": {}}
    support_payload = read_json(run_dir / "prompt_support_items.json") if (run_dir / "prompt_support_items.json").exists() else {"phases": {}}
    dictionary_used = read_json(run_dir / "approved_dictionary_used.json") if (run_dir / "approved_dictionary_used.json").exists() else {"entry_count": 0, "entries": []}
    rules_used = read_json(run_dir / "approved_rules_used.json") if (run_dir / "approved_rules_used.json").exists() else {"rule_count": 0, "rules": []}
    context_rows = _flatten_prompt_phase_rows(context_payload)
    budget_rows = _flatten_prompt_phase_rows(budget_payload)
    retrieval_rows = _flatten_prompt_phase_rows(retrieval_payload)
    conflict_rows = _flatten_prompt_phase_rows(conflict_payload)
    support_rows = _flatten_prompt_phase_rows(support_payload)
    selected_rows: list[dict[str, Any]] = []
    dropped_rows: list[dict[str, Any]] = []
    for row in context_rows:
        for item in row.get("support_items_selected", []) or []:
            selected_rows.append(
                {
                    "phase": row.get("phase"),
                    "sample_id": row.get("sample_id"),
                    "chapter_id": row.get("chapter_id"),
                    **item,
                }
            )
        for item in row.get("support_items_dropped", []) or []:
            dropped_rows.append(
                {
                    "phase": row.get("phase"),
                    "sample_id": row.get("sample_id"),
                    "chapter_id": row.get("chapter_id"),
                    **item,
                }
            )
    unique_items = {
        str(row.get("item_id")): row
        for row in selected_rows
        if row.get("item_id")
    }
    unique_memory = {
        str(row.get("item_id")): row
        for row in selected_rows
        if row.get("source_type") == "memory" and row.get("item_id")
    }
    unique_rules = {
        str(row.get("item_id")): row
        for row in selected_rows
        if row.get("source_type") == "rule" and row.get("item_id")
    }
    conflicts = [
        conflict
        for row in conflict_rows
        for conflict in row.get("conflicts", []) or []
    ]
    rounds = state.get("round_results", [])
    severe_count = sum(len(row.get("severe_flags", []) or []) for row in rounds)
    regressions = [
        regression
        for row in rounds
        for regression in row.get("regressions_over_3", []) or []
    ]
    unsafe_count = sum(
        1
        for row in rounds
        for flag in row.get("severe_flags", []) or []
        if flag.get("reason") == "unsafe_compression"
    )
    truncation_count = sum(
        1
        for row in rounds
        for flag in row.get("severe_flags", []) or []
        if flag.get("reason") == "truncation"
    )
    with (review_dir / "selected_support_items.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "item_id",
                "source_type",
                "source_anchor",
                "target_value",
                "entry_type",
                "memory_type",
                "confidence",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in selected_rows:
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    with (review_dir / "dropped_support_items.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "item_id",
                "source_type",
                "source_anchor",
                "target_value",
                "entry_type",
                "memory_type",
                "confidence",
                "drop_reason",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in dropped_rows:
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    with (review_dir / "selected_rules.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "item_id",
                "rule_type",
                "source_anchor",
                "instruction_text",
                "confidence",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in selected_rows:
            if row.get("source_type") == "rule":
                writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    with (review_dir / "dropped_rules.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "phase",
                "sample_id",
                "chapter_id",
                "item_id",
                "rule_type",
                "source_anchor",
                "instruction_text",
                "confidence",
                "drop_reason",
                "conflict_group",
            ],
        )
        writer.writeheader()
        for row in dropped_rows:
            if row.get("source_type") == "rule":
                writer.writerow({field: row.get(field, "") for field in writer.fieldnames})
    if state.get("use_approved_rules"):
        all_rules = rules_used.get("all_rules") or rules_used.get("rules") or []
        with (review_dir / "disabled_or_verifier_only_rules.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "rule_id",
                    "rule_type",
                    "status",
                    "trigger_text",
                    "instruction",
                    "reason",
                ],
            )
            writer.writeheader()
            for rule in all_rules:
                status = str(rule.get("status") or "")
                if status == "active":
                    continue
                trigger = rule.get("trigger_pattern_json") or {}
                writer.writerow(
                    {
                        "rule_id": rule.get("id"),
                        "rule_type": rule.get("rule_type"),
                        "status": status,
                        "trigger_text": trigger.get("text") or trigger.get("kind") or "",
                        "instruction": rule.get("instruction"),
                        "reason": (
                            "verifier_only"
                            if status == "active_verifier_only"
                            else "disabled_after_validation"
                            if status == "disabled_for_prompt"
                            else status
                        ),
                    }
                )
        scoped_lines = [
            "# Scoped Rules Summary",
            "",
            f"- Approved rules before prompt filtering: `{rules_used.get('all_rule_count', rules_used.get('rule_count', 0))}`",
            f"- Active prompt-renderable rules: `{rules_used.get('rule_count', 0)}`",
            f"- Non-prompt/verifier-only/disabled rules: `{rules_used.get('non_prompt_rule_count', 0)}`",
        ]
        for rule in all_rules:
            status = str(rule.get("status") or "")
            if status != "active":
                scoped_lines.append(f"- `{rule.get('id')}` ({rule.get('rule_type')}): `{status}`")
        _write_text(review_dir / "scoped_rules_summary.md", "\n".join(scoped_lines) + "\n")
        _write_text(
            review_dir / "rule_ablation_summary.md",
            "# Rule Ablation Summary\n\n"
            "Prompt-rule ablation is generated by `nts rule ablate-prompt-impact`. "
            "This validation package records the post-scope prompt behavior and selected/dropped rule rows.\n",
        )
    round_lines = [
        "# Validation Rounds Summary",
        "",
        "| Round | Baseline | Hybrid | Delta |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in rounds:
        round_lines.append(
            f"| {row.get('round')} | {row.get('baseline_score')} | {row.get('memory_score')} | {row.get('score_delta')} |"
        )
    _write_text(review_dir / "validation_rounds_summary.md", "\n".join(round_lines) + "\n")
    _write_text(
        review_dir / "hybrid_effect_summary.md",
        "# Hybrid Effect Summary\n\n"
        f"- Approved dictionary entries: `{dictionary_used.get('entry_count', 0)}`\n"
        f"- Active memory count considered: `{max((row.get('active_memory_count_considered') or 0) for row in retrieval_rows) if retrieval_rows else 0}`\n"
        f"- Approved rules considered: `{rules_used.get('rule_count', 0)}`\n"
        f"- Unique support items used: `{len(unique_items)}`\n"
        f"- Unique memory support items used: `{len(unique_memory)}`\n"
        f"- Unique rule support items used: `{len(unique_rules)}`\n"
        f"- Selected support rows: `{len(selected_rows)}`\n"
        f"- Dropped support rows: `{len(dropped_rows)}`\n"
        f"- Conflicts detected: `{len(conflicts)}`\n"
        f"- Final decision: `{state.get('final_decision')}`\n"
        f"- Last error: `{state.get('last_error')}`\n",
    )
    if state.get("use_approved_rules"):
        _copy_file_if_exists(review_dir / "hybrid_effect_summary.md", review_dir / "hybrid_rules_effect_summary.md")
    budget_lines = ["# Prompt Budget Summary", ""]
    for row in budget_rows[:80]:
        budget_lines.append(
            "- "
            f"{row.get('phase')} / {row.get('sample_id')}: "
            f"selected={row.get('selected_item_count', 0)}, "
            f"dict={row.get('selected_dictionary_count', 0)}, "
            f"memory={row.get('selected_memory_count', 0)}, "
            f"rules={row.get('selected_rule_count', 0)}, "
            f"dropped={row.get('dropped_item_count', 0)}, "
            f"chars={row.get('support_chars', 0)}, "
            f"lines={row.get('support_lines', 0)}"
        )
    _write_text(review_dir / "prompt_budget_summary.md", "\n".join(budget_lines) + "\n")
    conflict_lines = ["# Prompt Conflict Summary", ""]
    if not conflicts:
        conflict_lines.append("No prompt support conflicts detected.")
    for conflict in conflicts[:80]:
        conflict_lines.append(
            "- "
            f"{conflict.get('conflict_type')}: "
            f"{conflict.get('source_anchor')} "
            f"policy={conflict.get('policy')}"
        )
    _write_text(review_dir / "prompt_conflict_summary.md", "\n".join(conflict_lines) + "\n")
    retrieval_lines = ["# Prompt Retrieval Examples", ""]
    for row in retrieval_rows[:12]:
        retrieval_lines.append(f"## {row.get('phase')} / {row.get('sample_id')}")
        for item in row.get("selected_items", []) or []:
            if item.get("source_type") == "rule":
                retrieval_lines.append(f"- rule: {item.get('rule_type')} / {item.get('instruction_text')}")
            else:
                retrieval_lines.append(
                    f"- {item.get('source_type')}: {item.get('source_anchor')} => {item.get('target_value')}"
                )
        if not row.get("selected_items"):
            retrieval_lines.append("- No hybrid support item rendered.")
        retrieval_lines.append("")
    _write_text(review_dir / "prompt_retrieval_examples.md", "\n".join(retrieval_lines) + "\n")
    prompt_samples = (run_dir / "prompt_used.md").read_text(encoding="utf-8") if (run_dir / "prompt_used.md").exists() else "# Prompt Samples\n\nNo prompt artifacts were emitted.\n"
    _write_text(review_dir / "prompt_samples.md", prompt_samples)
    recommendation = "proceed to controlled production" if state.get("final_decision") == "PASS" else "review harmful memory/dictionary/rule entries"
    title = "Hybrid Prompt Rules Human Review Summary" if state.get("use_approved_rules") else "Hybrid Prompt Human Review Summary"
    human_lines = [
        f"# {title}",
        "",
        f"- Approved dictionary count: `{dictionary_used.get('entry_count', 0)}`",
        f"- Active memory count considered: `{max((row.get('active_memory_count_considered') or 0) for row in retrieval_rows) if retrieval_rows else 0}`",
        f"- Approved rule count: `{rules_used.get('rule_count', 0)}`",
        f"- Rules selected: `{sum(1 for row in selected_rows if row.get('source_type') == 'rule')}`",
        f"- Rules dropped: `{sum(1 for row in dropped_rows if row.get('source_type') == 'rule')}`",
        f"- Support items selected: `{len(selected_rows)}`",
        f"- Support items dropped: `{len(dropped_rows)}`",
        f"- Conflicts detected: `{len(conflicts)}`",
    ]
    for row in rounds:
        human_lines.append(
            f"- Round {row.get('round')}: baseline `{row.get('baseline_score')}`, hybrid `{row.get('memory_score')}`, delta `{row.get('score_delta')}`"
        )
    human_lines.extend(
        [
            f"- Severe flag count: `{severe_count}`",
            f"- Unsafe compression count: `{unsafe_count}`",
            f"- Truncation count: `{truncation_count}`",
            f"- Chapter regressions over 3: `{len(regressions)}`",
            f"- Recommendation: `{recommendation}`",
            "",
            "Pending, rejected, deprecated, harmful, and insufficient-evidence memory/dictionary/rule entries are excluded from the rendered support block.",
            "Raw NLP cache is not injected into prompts.",
        ]
    )
    _write_text(review_dir / "human_review_summary.md", "\n".join(human_lines) + "\n")
    state["hybrid_prompt_review_path"] = str(review_dir)
    state["dictionary_prompt_review_path"] = str(review_dir)
    return str(review_dir)


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
        sample["alignment_quality"] = max(float(sample.get("alignment_quality") or 0), ALIGNMENT_QUALITY_THRESHOLD)
        # Mock validation isolates prompt-support mechanics; real providers keep full QA gates.
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
    prompt_text_by_sample: dict[str, str] | None = None,
    model: str,
    round_index: int,
    unsupported_sample_ids: list[str] | None = None,
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
        stable_prompt_text_by_sample=prompt_text_by_sample,
        provider_retry_attempts=3,
        provider_retry_backoff_seconds=0.0 if state["provider"] == "mock" else 5.0,
        validation_index=round_index,
    )
    _raise_provider_failures_if_any(run_dir, translation, model)
    if phase == "memory" and unsupported_sample_ids:
        _reuse_baseline_for_unsupported_samples(
            round_dir=run_dir / f"round_{round_index}",
            eval_run=Path(translation["run_dir"]),
            sample_ids=unsupported_sample_ids,
        )
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
        "comparison_mode": state.get("comparison_mode"),
        "use_approved_dictionary": bool(state.get("use_approved_dictionary")),
        "use_hybrid_prompt": bool(state.get("use_hybrid_prompt")),
        "use_approved_rules": bool(state.get("use_approved_rules")),
        "dictionary_prompt_review_path": state.get("dictionary_prompt_review_path"),
        "hybrid_prompt_review_path": state.get("hybrid_prompt_review_path"),
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


def _fail_validation(run_dir: Path, state: dict[str, Any], reason: str) -> None:
    state["status"] = "failed"
    state["final_decision"] = "FAIL"
    state["last_error"] = reason
    state["can_resume"] = False
    _mark_stage(run_dir, state, state.get("current_stage") or "unknown", "failed", {"reason": reason})
    _write_final_summary(run_dir, state, reason=reason)


def _write_final_summary(run_dir: Path, state: dict[str, Any], *, reason: str) -> None:
    rounds = state.get("round_results", [])
    comparison_mode = state.get("comparison_mode", "approved_memory")
    comparison_label = (
        "Hybrid Rules"
        if comparison_mode == "hybrid_prompt_rules_support"
        else "Hybrid"
        if comparison_mode == "hybrid_prompt_support"
        else "Dictionary"
        if comparison_mode == "approved_dictionary_prompt_support"
        else "Memory"
    )
    review_path = (
        _write_hybrid_prompt_review_package(run_dir, state)
        if comparison_mode in {"hybrid_prompt_support", "hybrid_prompt_rules_support"}
        else _write_dictionary_prompt_review_package(run_dir, state)
    )
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
        "comparison_mode": comparison_mode,
        "use_approved_dictionary": bool(state.get("use_approved_dictionary")),
        "use_hybrid_prompt": bool(state.get("use_hybrid_prompt")),
        "use_approved_rules": bool(state.get("use_approved_rules")),
        "dictionary_prompt_review_path": review_path,
        "hybrid_prompt_review_path": review_path if comparison_mode in {"hybrid_prompt_support", "hybrid_prompt_rules_support"} else None,
        "round_results": rounds,
        "average_delta": _average_round_delta(rounds),
        "required_average_delta": _state_min_improvement(state),
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
        f"- Comparison mode: `{comparison_mode}`",
        f"- Prompt review: `{review_path}`",
        f"- Average delta: `{_average_round_delta(rounds)}`",
        f"- Required average delta: `{_state_min_improvement(state)}`",
        f"- Final decision: `{state.get('final_decision')}`",
        f"- Reason: `{reason}`",
        "",
        f"| Round | Baseline | {comparison_label} | Delta |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in rounds:
        lines.append(
            f"| {row['round']} | {row['baseline_score']} | {row['memory_score']} | {row['score_delta']} |"
        )
    _write_text(run_dir / "final_validation_summary.md", "\n".join(lines) + "\n")
    effect_report_name = "hybrid_rules_effect_report.md" if comparison_label == "Hybrid Rules" else "hybrid_effect_report.md" if comparison_label == "Hybrid" else "dictionary_effect_report.md" if comparison_label == "Dictionary" else "memory_effect_report.md"
    _write_text(
        run_dir / effect_report_name,
        f"# {comparison_label} Effect Report\n\n"
        + "\n".join(
            f"- Round {row['round']}: delta={row['score_delta']}, terminology_delta={row.get('terminology_error_delta')}"
            for row in rounds
        )
        + "\n",
    )
    if comparison_label in {"Dictionary", "Hybrid", "Hybrid Rules"}:
        _write_text(
            run_dir / "memory_effect_report.md",
            f"# Memory Effect Report\n\n{comparison_label} prompt support mode used; see {effect_report_name}.\n",
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


def _average_round_delta(rounds: list[dict[str, Any]]) -> float:
    if not rounds:
        return 0.0
    return round(sum(float(row.get("score_delta") or 0) for row in rounds) / len(rounds), 3)


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
    average_delta = _average_round_delta(rounds)
    if average_delta < float(min_improvement):
        high_baseline_error_delta = all(
            float(row.get("baseline_score") or 0) >= 92
            and float(row.get("score_delta") or 0) > 0
            and int(row.get("terminology_error_delta") or 0) > 0
            for row in rounds
        )
        if not high_baseline_error_delta:
            if float(min_improvement) <= 0:
                return "FAIL", "minimum_improvement_not_reached"
            return "FAIL", "average_improvement_below_target"
    if float(min_improvement) <= 0:
        return "PASS", "consecutive_rounds_improved"
    return "PASS", "consecutive_rounds_average_target_met"


def _state_min_improvement(state: dict[str, Any]) -> float:
    value = state.get("min_improvement")
    return 1.0 if value is None else float(value)


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
    exclude_candidate_ids: str | None = None,
    candidate_ablation_top_n: int = 5,
    prefer_no_compression_window: bool = True,
    allow_skip_unsafe_chapter_sample: bool = False,
    use_approved_dictionary: bool = False,
    use_hybrid_prompt: bool = False,
    dictionary_max_entries: int = 8,
    dictionary_max_chars: int = 500,
    memory_max_items: int = 6,
    use_approved_rules: bool = False,
    rule_max_hints: int = 4,
    support_max_chars: int = 1200,
    emit_prompt_artifacts: bool = False,
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
    if use_approved_rules:
        use_hybrid_prompt = True
        use_approved_dictionary = True
    project = get_project_by_slug(workspace, project_slug)
    stable_prompt = load_approved_stable_prompt(workspace)
    approved_memory = _approved_learning_memory(workspace, project)
    all_active_memory = _active_memory_rows(workspace, project["id"], project["slug"])
    if use_hybrid_prompt:
        approved_memory = list(all_active_memory)
    approved_dictionary_entries = load_project_dictionary(workspace, project_slug) if (use_approved_dictionary or use_hybrid_prompt) else []
    approved_rule_entries = load_approved_rules(workspace, project_slug) if use_approved_rules else []
    all_project_rule_entries = load_all_project_rules(workspace, project_slug) if use_approved_rules else []
    mined_approved_memory = [item for item in approved_memory if _is_mined_approved_memory(item)]
    if use_approved_dictionary or use_hybrid_prompt:
        baseline_excluded_memory = list(all_active_memory)
        baseline_memory = []
    else:
        baseline_excluded_memory = mined_approved_memory if mined_approved_memory else approved_memory
        baseline_memory = [
            item
            for item in all_active_memory
            if item["id"] not in {excluded["id"] for excluded in baseline_excluded_memory}
        ]
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
        exclude_candidate_ids=exclude_candidate_ids,
        candidate_ablation_top_n=candidate_ablation_top_n,
        prefer_no_compression_window=prefer_no_compression_window,
        allow_skip_unsafe_chapter_sample=allow_skip_unsafe_chapter_sample,
        use_approved_dictionary=use_approved_dictionary,
        use_hybrid_prompt=use_hybrid_prompt,
        use_approved_rules=use_approved_rules,
        dictionary_max_entries=dictionary_max_entries,
        dictionary_max_chars=dictionary_max_chars,
        memory_max_items=memory_max_items,
        rule_max_hints=rule_max_hints,
        support_max_chars=support_max_chars,
        emit_prompt_artifacts=emit_prompt_artifacts,
    )
    state["approved_memory_ids"] = [item["id"] for item in approved_memory]
    state["baseline_excluded_memory_ids"] = [item["id"] for item in baseline_excluded_memory]
    state["newly_approved_mined_memory_ids"] = [item["id"] for item in mined_approved_memory]
    state["newly_approved_mined_candidate_ids"] = [
        (item.get("value_json") or {}).get("candidate_id")
        for item in mined_approved_memory
        if (item.get("value_json") or {}).get("candidate_id")
    ]
    state["stable_prompt_path"] = stable_prompt.prompt_path
    _init_validation_files(run_dir, state)
    write_json(run_dir / "approved_memory_used.json", {"items": approved_memory})
    if use_approved_dictionary or use_hybrid_prompt:
        write_json(
            run_dir / "approved_dictionary_used.json",
            {
                "schema_version": "approved_dictionary_used_v1",
                "project_slug": project_slug,
                "entry_count": len(approved_dictionary_entries),
                "entries": approved_dictionary_entries,
                "max_dictionary_entries": dictionary_max_entries,
                "max_dictionary_chars": dictionary_max_chars,
                "use_hybrid_prompt": use_hybrid_prompt,
                "memory_max_items": memory_max_items,
                "use_approved_rules": use_approved_rules,
                "rule_max_hints": rule_max_hints,
                "support_max_chars": support_max_chars,
                "created_at": utc_now(),
            },
        )
    if use_approved_rules:
        write_json(
            run_dir / "approved_rules_used.json",
            {
                "schema_version": "approved_rules_used_v1",
                "project_slug": project_slug,
                "rule_count": len(approved_rule_entries),
                "all_rule_count": len(all_project_rule_entries),
                "non_prompt_rule_count": len(all_project_rule_entries) - len(approved_rule_entries),
                "rules": approved_rule_entries,
                "all_rules": all_project_rule_entries,
                "max_rule_hints": rule_max_hints,
                "created_at": utc_now(),
            },
        )
    write_json(
        run_dir / "baseline_memory_exclusion.json",
        {
            "baseline_memory_ids": [item["id"] for item in baseline_memory],
            "excluded_memory_ids": state["baseline_excluded_memory_ids"],
            "excluded_candidate_ids": state["newly_approved_mined_candidate_ids"],
            "excluded_items": baseline_excluded_memory,
            "comparison_mode": (
                "hybrid_prompt_support"
                if use_hybrid_prompt
                else
                "approved_dictionary_prompt_support"
                if use_approved_dictionary
                else
                "newly_mined_memory_delta"
                if mined_approved_memory
                else "legacy_all_approved_memory_delta"
            ),
        },
    )
    _write_memory_delta_context(
        run_dir,
        approved_memory=approved_memory,
        baseline_memory=baseline_memory,
        mined_memory=mined_approved_memory,
        baseline_excluded=baseline_excluded_memory,
    )
    _write_active_memory_snapshot(
        run_dir,
        active_memory=all_active_memory,
        baseline_memory=baseline_memory,
        memory_pass=all_active_memory,
        baseline_excluded=baseline_excluded_memory,
    )
    if use_approved_dictionary and not use_hybrid_prompt and not approved_dictionary_entries:
        _block(run_dir, state, "approved_project_dictionary_missing", can_resume=False)
        return _finalize_result(workspace, run_dir, state)
    if use_approved_rules and not all_project_rule_entries:
        _block(run_dir, state, "approved_rules_missing", can_resume=False)
        return _finalize_result(workspace, run_dir, state)
    if not use_hybrid_prompt and not use_approved_dictionary and not approved_memory:
        _block(run_dir, state, "approved_learning_memory_missing", can_resume=False)
        return _finalize_result(workspace, run_dir, state)
    missing_expected = _missing_expected_mvp5d6_memory(approved_memory)
    rolled_back_mined = _rolled_back_expected_mined_candidate_ids(workspace, project)
    missing_expected["missing_mined_candidate_ids"] = [
        candidate_id
        for candidate_id in missing_expected["missing_mined_candidate_ids"]
        if candidate_id not in rolled_back_mined
    ]
    if missing_expected["missing_original_memory_ids"] or missing_expected["missing_mined_candidate_ids"]:
        write_json(
            run_dir / "active_memory_snapshot_missing_expected.json",
            {
                "schema_version": "mvp5d6_expected_memory_check_v1",
                **missing_expected,
                "rolled_back_mined_candidate_ids": sorted(rolled_back_mined),
                "created_at": utc_now(),
            },
        )
        _block(
            run_dir,
            state,
            "expected_approved_memory_missing",
            can_resume=False,
        )
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
        if (
            (state.get("use_approved_dictionary") or state.get("use_hybrid_prompt"))
            and state.get("final_decision") == "FAIL"
            and state.get("last_error") == "minimum_improvement_not_reached"
        ):
            decision, reason = _final_decision(
                state,
                require_consecutive_improvement=bool(state.get("require_consecutive_improvement")),
                min_improvement=_state_min_improvement(state),
            )
            if decision != state.get("final_decision"):
                state["final_decision"] = decision
                state["status"] = "completed" if decision == "PASS" else "failed"
                state["can_resume"] = False
                state["last_error"] = None if decision == "PASS" else reason
                _write_final_summary(run_dir, state, reason=reason)
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
    baseline_excluded_ids = set(
        state.get("baseline_excluded_memory_ids")
        or [item.get("id") for item in baseline_excluded]
        or state.get("approved_memory_ids", [])
    )
    if state.get("use_approved_dictionary") or state.get("use_hybrid_prompt"):
        baseline_memory = []
        baseline_excluded = list(all_active_memory)
    else:
        baseline_memory = [item for item in all_active_memory if item["id"] not in baseline_excluded_ids]
    memory_pass_memory = list(all_active_memory)
    try:
        if "prepare_dataset" not in state.get("completed_stages", []):
            _mark_stage(run_dir, state, "prepare_dataset", "running")
            _prepare_dataset(run_dir, state)
            _mark_stage(run_dir, state, "prepare_dataset", "completed")
        baseline_memory = _filter_prompt_memory(
            run_dir,
            memory_items=baseline_memory,
            phase="baseline",
        )
        memory_pass_memory = _filter_prompt_memory(
            run_dir,
            memory_items=memory_pass_memory,
            phase="approved_memory",
        )
        _write_active_memory_snapshot(
            run_dir,
            active_memory=all_active_memory,
            baseline_memory=baseline_memory,
            memory_pass=memory_pass_memory,
            baseline_excluded=baseline_excluded,
        )
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
                prompt_by_sample = _validation_prompts_by_sample(
                    run_dir,
                    workspace=workspace,
                    stable_prompt=stable_prompt,
                    memory_items=baseline_memory,
                    excluded_memory=baseline_excluded,
                    phase=f"round_{round_index}_baseline",
                    dictionary_enabled=False,
                    hybrid_enabled=False,
                    dictionary_max_entries=int(state.get("dictionary_max_entries") or 8),
                    dictionary_max_chars=int(state.get("dictionary_max_chars") or 500),
                    memory_max_items=int(state.get("memory_max_items") or 6),
                    use_approved_rules=bool(state.get("use_approved_rules")),
                    rule_max_hints=int(state.get("rule_max_hints") or 4),
                    support_max_chars=int(state.get("support_max_chars") or 1200),
                    emit_prompt_artifacts=bool(state.get("emit_prompt_artifacts")),
                )
                baseline_report, eval_run = _run_phase(
                    run_dir=run_dir,
                    state=state,
                    phase="baseline",
                    prompt_text=prompt,
                    prompt_text_by_sample=prompt_by_sample,
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
                memory_phase_name = f"round_{round_index}_approved_memory"
                prompt = _validation_prompt(
                    stable_prompt,
                    included_memory=memory_pass_memory,
                    excluded_memory=[],
                    phase=memory_phase_name,
                )
                prompt_by_sample = _validation_prompts_by_sample(
                    run_dir,
                    workspace=workspace,
                    stable_prompt=stable_prompt,
                    memory_items=memory_pass_memory,
                    excluded_memory=[],
                    phase=memory_phase_name,
                    dictionary_enabled=bool(state.get("use_approved_dictionary")) and not bool(state.get("use_hybrid_prompt")),
                    hybrid_enabled=bool(state.get("use_hybrid_prompt")),
                    dictionary_max_entries=int(state.get("dictionary_max_entries") or 8),
                    dictionary_max_chars=int(state.get("dictionary_max_chars") or 500),
                    memory_max_items=int(state.get("memory_max_items") or 6),
                    use_approved_rules=bool(state.get("use_approved_rules")),
                    rule_max_hints=int(state.get("rule_max_hints") or 4),
                    support_max_chars=int(state.get("support_max_chars") or 1200),
                    emit_prompt_artifacts=bool(state.get("emit_prompt_artifacts")),
                )
                no_effective_support = (
                    state.get("comparison_mode") in {"hybrid_prompt_support", "approved_dictionary_prompt_support", "hybrid_prompt_rules_support"}
                    and _phase_effectively_has_no_support(run_dir, memory_phase_name)
                )
                unsupported_sample_ids = (
                    _sample_ids_without_effective_support(run_dir, memory_phase_name)
                    if state.get("comparison_mode") in {"hybrid_prompt_support", "approved_dictionary_prompt_support", "hybrid_prompt_rules_support"}
                    else []
                )
                if no_effective_support:
                    _mark_stage(run_dir, state, stage, "running")
                    _mark_stage(run_dir, state, f"round_{round_index}_memory_evaluate", "running")
                    memory_report = _reuse_baseline_as_memory_phase(round_dir, baseline_report=baseline_report)
                    _mark_stage(
                        run_dir,
                        state,
                        stage,
                        "completed",
                        {"reused_baseline_due_to_no_support": True},
                    )
                    _mark_stage(
                        run_dir,
                        state,
                        f"round_{round_index}_memory_evaluate",
                        "completed",
                        {
                            "average_score": _score_summary(memory_report, model).get("average_score"),
                            "reused_baseline_due_to_no_support": True,
                        },
                    )
                else:
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
                    memory_report, eval_run = _run_phase(
                        run_dir=run_dir,
                        state=state,
                        phase="memory",
                        prompt_text=prompt,
                        prompt_text_by_sample=prompt_by_sample,
                        model=model,
                        round_index=round_index,
                        unsupported_sample_ids=unsupported_sample_ids,
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
                delta["comparison_mode"] = state.get("comparison_mode", "approved_memory")
                _write_round_comparison(round_dir, round_index, delta)
                if not any(row.get("round") == round_index for row in round_results):
                    round_results.append(delta)
                state["round_results"] = round_results
                _mark_stage(run_dir, state, score_stage, "completed", delta)

        decision, reason = _final_decision(
            state,
            require_consecutive_improvement=bool(state.get("require_consecutive_improvement")),
            min_improvement=_state_min_improvement(state),
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
        if message.startswith("No reliable title-matched alignment sample found"):
            _fail_validation(run_dir, state, f"validation_candidate_selection_failed: {message}")
        else:
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
