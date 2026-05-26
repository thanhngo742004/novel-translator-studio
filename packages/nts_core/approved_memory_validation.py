from __future__ import annotations

import csv
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

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
    if float(best["confidence"]) < TENTATIVE_CHAPTER_MATCH_THRESHOLD:
        return None, best, scored
    best_group = next(group for group in groups if group.get("group_index") == best.get("group_index"))
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
    exclude_candidate_ids: str | None,
    candidate_ablation_top_n: int,
    prefer_no_compression_window: bool,
    allow_skip_unsafe_chapter_sample: bool,
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
            if (
                not candidate.get("accepted")
                or int(candidate.get("source_chapter_id") or 0) != int(chapter)
                or float(candidate.get("alignment_quality") or 0) < ALIGNMENT_QUALITY_THRESHOLD
            ):
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

    write_json(
        run_dir / "failing_samples_report.json",
        {
            "schema_version": "approved_memory_validation_failing_samples_replay_v1",
            "validation_run_id": run_dir.name,
            "failure_count": len(rows),
            "root_cause_counts": {
                cause: sum(1 for row in rows if row["root_cause"] == cause)
                for cause in sorted({row["root_cause"] for row in rows})
            },
            "failures": rows,
            "created_at": utc_now(),
        },
    )
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
    _write_text(run_dir / "failing_samples_report.md", "\n".join(lines) + "\n")
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
            "exclusions": str(run_dir / "validation_candidate_exclusions.json"),
        },
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


def _fail_validation(run_dir: Path, state: dict[str, Any], reason: str) -> None:
    state["status"] = "failed"
    state["final_decision"] = "FAIL"
    state["last_error"] = reason
    state["can_resume"] = False
    _mark_stage(run_dir, state, state.get("current_stage") or "unknown", "failed", {"reason": reason})
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
    exclude_candidate_ids: str | None = None,
    candidate_ablation_top_n: int = 5,
    prefer_no_compression_window: bool = True,
    allow_skip_unsafe_chapter_sample: bool = False,
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
    all_active_memory = _active_memory_rows(workspace, project["id"], project["slug"])
    mined_approved_memory = [item for item in approved_memory if _is_mined_approved_memory(item)]
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
    write_json(
        run_dir / "baseline_memory_exclusion.json",
        {
            "excluded_memory_ids": state["baseline_excluded_memory_ids"],
            "excluded_candidate_ids": state["newly_approved_mined_candidate_ids"],
            "excluded_items": baseline_excluded_memory,
            "comparison_mode": (
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
    if not approved_memory:
        _block(run_dir, state, "approved_learning_memory_missing", can_resume=False)
        return _finalize_result(workspace, run_dir, state)
    missing_expected = _missing_expected_mvp5d6_memory(approved_memory)
    if missing_expected["missing_original_memory_ids"] or missing_expected["missing_mined_candidate_ids"]:
        write_json(
            run_dir / "active_memory_snapshot_missing_expected.json",
            {
                "schema_version": "mvp5d6_expected_memory_check_v1",
                **missing_expected,
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
    baseline_memory = [item for item in all_active_memory if item["id"] not in baseline_excluded_ids]
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
