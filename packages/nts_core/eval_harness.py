from __future__ import annotations

import html
import hashlib
import csv
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import yaml

from nts_storage.database import json_dumps, utc_now


DEFAULT_LIMITS = {
    "alignment_max_chapters": 3,
    "sample_count": 3,
    "style_learning_chapters": 1,
    "style_learning_max_source_chars": 4000,
    "style_learning_max_target_chars": 6000,
    "translation_sample_chapter": 1,
    "translation_sample_max_source_chars": 1500,
    "evaluation_max_source_chars": 6000,
    "evaluation_max_target_chars": 10000,
}

EVAL_LENGTH_RATIO_MIN = 0.85
EVAL_LENGTH_RATIO_MAX = 1.25
PROMPT_TARGET_MIN_RATIO = 0.85
PROMPT_TARGET_MAX_RATIO = 1.20
LENGTH_RETRY_RATIO = 1.30
ACTIVE_PROMPT_ITERATION = 4
PARAGRAPH_TARGET_MIN_RATIO = 0.80
PARAGRAPH_TARGET_MAX_RATIO = 1.20
PARAGRAPH_STRICT_MAX_RATIO = 1.30
PARAGRAPH_SHORT_STRICT_MAX_RATIO = 1.40
PARAGRAPH_SHORT_REFERENCE_CHARS = 80
PARAGRAPH_ALLOWED_OVER_BUDGET_RATIO = 1.55
TINY_PARAGRAPH_THRESHOLD = 80
UNIT_TARGET_MIN_CHARS = 150
UNIT_STRICT_MAX_RATIO = 1.35
UNIT_SHORT_STRICT_MAX_RATIO = 1.50
UNIT_HIGH_RISK_SOURCE_CHARS = 220
UNIT_HIGH_RISK_TARGET_MAX_CHARS = 600
UNIT_COMBINED_TARGET_MAX_CHARS = 900
PARAGRAPH_BATCH_SIZE = 12
ALIGNMENT_QUALITY_THRESHOLD = 0.70
DEFAULT_PROVIDER_RETRY_ATTEMPTS = 3
DEFAULT_PROVIDER_RUN_RETRY_ATTEMPTS = 1
DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS = 5.0
RETRYABLE_PROVIDER_HTTP_STATUSES = {408, 429, 500, 502, 503, 504, 524}
NON_RETRYABLE_PROVIDER_HTTP_STATUSES = {400, 401, 403}
PROVIDER_ONLY_FAILURE_REASONS = {
    "provider_error",
    "provider_failure_empty_output",
    "provider_retry_exhausted",
}
STYLE_DRIFT_WARNING_THRESHOLD = 45
STYLE_DRIFT_REVIEW_THRESHOLD = 25
STYLE_CONNECTIVE_TERMS = {
    "nên",
    "vì vậy",
    "có lẽ",
    "do đó",
    "bởi vậy",
    "thế là",
    "cho nên",
}

SCORE_WEIGHTS = {
    "meaning_accuracy": 25,
    "omission_addition": 15,
    "terminology_consistency": 15,
    "pronoun_name_consistency": 10,
    "vietnamese_fluency": 15,
    "style_match": 15,
    "formatting_preservation": 5,
}

PASS_THRESHOLDS = {
    "total_score": 80,
    "meaning_accuracy": 20,
    "omission_addition": 12,
    "terminology_consistency": 11,
    "vietnamese_fluency": 11,
    "style_match": 10,
}

CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+.+|(?:chapter|chuong|chương)\s+\S+.*|第.{1,12}[章节回].*)\s*$",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
VIETNAMESE_MARK_RE = re.compile(r"[ăâêôơưđáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.IGNORECASE)
VIETNAMESE_PRONOUNS = {
    "ta",
    "tao",
    "tôi",
    "mình",
    "ngươi",
    "ngài",
    "hắn",
    "nàng",
    "y",
    "cô",
    "anh",
    "em",
    "chàng",
    "thiếp",
}

FIXED_GLOSSARY = {
    "韩绝": "Hàn Tuyệt",
    "玉清宗": "Ngọc Thanh Tông",
    "炼气境": "Luyện Khí cảnh",
    "筑基": "Trúc Cơ",
    "灵根": "linh căn",
    "修为": "tu vi",
    "先天气运": "tiên thiên khí vận",
}

ALIGNMENT_ANCHORS = {
    "han_jue": {
        "zh": ["韩绝", "韩觉"],
        "vi": ["hàn tuyệt", "hàn giác"],
    },
    "yuqing_zong": {
        "zh": ["玉清宗"],
        "vi": ["ngọc thanh tông"],
    },
    "tie_lao": {
        "zh": ["铁老"],
        "vi": ["thiết lão"],
    },
    "wang_laotou": {
        "zh": ["王老头", "王老"],
        "vi": ["vương lão đầu", "vương lão"],
    },
    "xing_hongxuan": {
        "zh": ["邢红璇", "邢师妹"],
        "vi": ["hình hồng tuyền", "hình hồng huyền", "hình hồng", "tính sư muội"],
    },
    "zhang_ge": {
        "zh": ["张鸽"],
        "vi": ["trương cáp", "trương ca"],
    },
    "ling_gen": {
        "zh": ["灵根", "灵根资质"],
        "vi": ["linh căn", "linh căn tư chất"],
    },
    "xiu_wei": {
        "zh": ["修为"],
        "vi": ["tu vi"],
    },
    "lianqi": {
        "zh": ["炼气境"],
        "vi": ["luyện khí cảnh", "luyện khí"],
    },
    "zhuji": {
        "zh": ["筑基"],
        "vi": ["trúc cơ"],
    },
    "xiantian_qiyun": {
        "zh": ["先天气运"],
        "vi": ["tiên thiên khí vận"],
    },
    "gongfa": {
        "zh": ["功法"],
        "vi": ["công pháp"],
    },
    "fashu": {
        "zh": ["法术"],
        "vi": ["pháp thuật"],
    },
    "shentong": {
        "zh": ["神通"],
        "vi": ["thần thông"],
    },
    "faqi": {
        "zh": ["法器"],
        "vi": ["pháp khí"],
    },
    "lingshi": {
        "zh": ["灵石"],
        "vi": ["linh thạch"],
    },
    "system_name": {
        "zh": ["【姓名", "姓名"],
        "vi": ["tính danh"],
    },
    "system_lifespan": {
        "zh": ["【寿命", "寿命"],
        "vi": ["thọ mệnh"],
    },
    "system_race": {
        "zh": ["【种族", "种族"],
        "vi": ["chủng tộc"],
    },
    "liudao_gong": {
        "zh": ["六道轮回功"],
        "vi": ["lục đạo luân hồi công"],
    },
}

TERMINAL_PUNCTUATION = set(".!?…。！？】)]}\"”'")
OPEN_CLOSE_PAIRS = {"【": "】", "(": ")", "[": "]", "“": "”", '"': '"'}
SUSPICIOUS_FINAL_FRAGMENTS = {
    "bắt đ",
    "phà",
    "không lắ",
    "vẫn không lắ",
    "tu tiê",
    "chẳng có m",
    "linh căn:",
    "hàn tuyệt:",
    "ngọc thanh tông:",
    "tiên thiên khí vận:",
}
GLOSSARY_LABEL_PREFIXES = tuple(
    sorted({target.lower() for target in FIXED_GLOSSARY.values()}, key=len, reverse=True)
)


@dataclass(frozen=True)
class EvalProvider:
    key: str
    type: str
    base_url: str
    api_key_env: str
    route: str = "chat/completions"
    models: tuple[str, ...] = ()


def repo_root() -> Path:
    return Path.cwd()


def eval_root() -> Path:
    return repo_root() / "artifacts" / "evaluations"


def project_eval_root(project: str) -> Path:
    return eval_root() / project


def new_run_dir(project: str, phase: str) -> Path:
    run_id = f"{project}_{phase}_{int(time.time() * 1000)}"
    path = eval_root() / run_id
    path.mkdir(parents=True, exist_ok=True)
    (project_eval_root(project)).mkdir(parents=True, exist_ok=True)
    (project_eval_root(project) / "latest.txt").write_text(str(path), encoding="utf-8")
    return path


def latest_run_dir(project: str) -> Path:
    pointer = project_eval_root(project) / "latest.txt"
    if not pointer.exists():
        raise ValueError(f"No evaluation run found for project: {project}")
    path = Path(pointer.read_text(encoding="utf-8").strip())
    if not path.exists():
        raise ValueError(f"Latest evaluation run no longer exists: {path}")
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(payload) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_model_name(model: str) -> str:
    return model.replace("/", "_").replace("\\", "_").replace(":", "_")


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_nonspace(text: str) -> str:
    stripped = text.rstrip()
    return stripped[-1] if stripped else ""


def _balanced_delimiters(text: str) -> list[str]:
    reasons = []
    if text.count("【") != text.count("】"):
        reasons.append("unmatched_system_panel_bracket")
    if text.count("(") != text.count(")"):
        reasons.append("unmatched_parenthesis")
    if text.count("[") != text.count("]"):
        reasons.append("unmatched_square_bracket")
    if text.count("“") != text.count("”"):
        reasons.append("unmatched_curly_quote")
    if text.count('"') % 2:
        reasons.append("unmatched_straight_quote")
    return reasons


def _starts_with_injected_glossary_label(text: str) -> bool:
    lowered = text.strip().lower()
    matched_prefixes = [
        prefix for prefix in GLOSSARY_LABEL_PREFIXES if lowered.startswith(prefix + ":")
    ]
    if not matched_prefixes:
        return False
    label_count = sum(1 for prefix in GLOSSARY_LABEL_PREFIXES if f"{prefix}:" in lowered[:80])
    return label_count >= 2 or matched_prefixes[0] not in {"hàn tuyệt"}


def _acceptable_nonpunct_terminal(text: str) -> bool:
    stripped = (text or "").strip()
    if re.fullmatch(r"[-–—\s]{3,}", stripped):
        return True
    tail = re.split(r"[\n。.!?！？…]+", stripped)[-1].strip()
    if re.fullmatch(r"[-–—\s]{3,}", tail):
        return True
    folded = unicodedata.normalize("NFD", tail.lower())
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    return bool(
        re.fullmatch(
            r"(?:[-–—]{2,}\s*)?(?:chuong|chapter)\s*\d+\s*[:：].{2,140}",
            folded,
        )
    )


def detect_truncated_vietnamese(
    text: str,
    *,
    source_text: str | None = None,
    strict_max: int | None = None,
) -> dict[str, Any]:
    stripped = re.sub(r"\s+", " ", text or "").strip()
    reasons: list[str] = []
    if not stripped:
        return {"is_truncated": True, "reasons": ["empty_output"]}

    reasons.extend(_balanced_delimiters(stripped))
    lowered = stripped.lower()
    if any(lowered.endswith(fragment) for fragment in SUSPICIOUS_FINAL_FRAGMENTS):
        reasons.append("suspicious_fragment_ending")
    if re.search(r"(?:^|\s)(?:linh căn|hàn tuyệt|ngọc thanh tông|tiên thiên khí vận)\s*:\s*$", lowered):
        reasons.append("dangling_glossary_label")
    if _starts_with_injected_glossary_label(stripped):
        reasons.append("glossary_label_prefix_injection")

    final = _last_nonspace(stripped)
    source_sentence_like = bool(
        not source_text
        or re.search(r"[。.!?！？…]|\n", source_text)
        or len(stripped) >= 24
    )
    if (
        source_sentence_like
        and final
        and final not in TERMINAL_PUNCTUATION
        and not _acceptable_nonpunct_terminal(stripped)
    ):
        reasons.append("missing_terminal_punctuation")
    final_token_match = re.search(r"([\wÀ-ỹĐđ]+)$", stripped, re.UNICODE)
    final_token = final_token_match.group(1).lower() if final_token_match else ""
    if final_token and (len(final_token) <= 1 or final_token in {"đ", "m", "lắ", "tiê", "phà"}):
        reasons.append("suspicious_incomplete_final_token")
    if strict_max is not None and len(text or "") == strict_max and final not in TERMINAL_PUNCTUATION:
        reasons.append("hard_budget_boundary_without_sentence_end")

    return {"is_truncated": bool(reasons), "reasons": sorted(set(reasons))}


def paragraph_count(text: str) -> int:
    return len(_paragraphs(text))


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                compact.append("")
            blank = True
        else:
            compact.append(line)
            blank = False
    return "\n".join(compact).strip()


def split_chapters(text: str, *, max_chapters: int | None = None) -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    lines = normalized.splitlines()
    headings: list[tuple[int, int, str]] = []
    offset = 0
    for line in lines:
        start = offset
        end = offset + len(line)
        if CHAPTER_HEADING_RE.match(line):
            headings.append((start, end, line.strip()))
        offset = end + 1

    chapters: list[dict[str, Any]] = []
    if not headings:
        chapters = [{"chapter_id": 1, "title": None, "text": normalized, "start": 0, "end": len(normalized)}]
    else:
        for index, (start, _heading_end, title) in enumerate(headings, start=1):
            end = headings[index][0] if index < len(headings) else len(normalized)
            chapter_text = normalized[start:end].strip()
            if chapter_text:
                chapters.append(
                    {
                        "chapter_id": len(chapters) + 1,
                        "title": title,
                        "text": chapter_text,
                        "start": start,
                        "end": end,
                    }
                )
    return chapters[:max_chapters] if max_chapters else chapters


def extract_raw_chapters(raw_path: Path, *, max_chapters: int) -> list[dict[str, Any]]:
    if not raw_path.exists():
        raise ValueError(f"Raw file not found: {raw_path}")
    text = raw_path.read_text(encoding="utf-8")
    return split_chapters(text, max_chapters=max_chapters)


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", raw)
    raw = re.sub(r"(?i)</\s*(p|div|h1|h2|h3|h4|li|section|chapter)\s*>", "\n\n", raw)
    raw = HTML_TAG_RE.sub(" ", raw)
    return normalize_text(html.unescape(raw))


def _opf_spine_names(epub: zipfile.ZipFile) -> list[str]:
    names = set(epub.namelist())
    container_name = "META-INF/container.xml"
    if container_name not in names:
        return []
    root = ElementTree.fromstring(epub.read(container_name))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//c:rootfile", ns)
    if rootfile is None:
        return []
    opf_path = rootfile.attrib.get("full-path")
    if not opf_path or opf_path not in names:
        return []
    opf_root = ElementTree.fromstring(epub.read(opf_path))
    manifest = {}
    for item in opf_root.findall(".//{*}manifest/{*}item"):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        if item_id and href and ("html" in media_type or href.lower().endswith((".html", ".xhtml", ".htm"))):
            manifest[item_id] = str((Path(opf_path).parent / href).as_posix())
    spine = []
    for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
        idref = itemref.attrib.get("idref")
        if idref in manifest and manifest[idref] in names:
            spine.append(manifest[idref])
    return spine


def extract_epub_chapters(epub_path: Path, *, max_chapters: int) -> list[dict[str, Any]]:
    if not epub_path.exists():
        raise ValueError(f"Translated EPUB not found: {epub_path}")
    chapters: list[dict[str, Any]] = []
    fallback_documents: list[dict[str, Any]] = []
    with zipfile.ZipFile(epub_path) as epub:
        names = _opf_spine_names(epub)
        if not names:
            names = sorted(
                name
                for name in epub.namelist()
                if name.lower().endswith((".html", ".xhtml", ".htm"))
            )
        for name in names:
            text = _strip_html(epub.read(name).decode("utf-8", errors="ignore"))
            if not text or len(text) < 20:
                continue
            split = split_chapters(text)
            chapter_like = [chapter for chapter in split if chapter.get("title")]
            if chapter_like:
                for chapter in chapter_like:
                    chapters.append(
                        {
                            "chapter_id": len(chapters) + 1,
                            "title": chapter.get("title"),
                            "text": chapter["text"],
                            "source_file": name,
                            "start": chapter.get("start", 0),
                            "end": chapter.get("end", len(chapter["text"])),
                        }
                    )
                    if len(chapters) >= max_chapters:
                        break
            else:
                fallback_documents.append(
                    {
                        "chapter_id": len(fallback_documents) + 1,
                        "title": text.splitlines()[0][:120] if text.splitlines() else None,
                        "text": text,
                        "source_file": name,
                        "start": 0,
                        "end": len(text),
                    }
                )
            if len(chapters) >= max_chapters:
                break
    if not chapters:
        chapters = fallback_documents[:max_chapters]
    if not chapters:
        raise ValueError(f"No readable chapters found in EPUB: {epub_path}")
    return chapters


def chapter_number(title: str | None) -> int | None:
    if not title:
        return None
    zh = re.search(r"第\s*(\d+)\s*[章节回]", title)
    if zh:
        return int(zh.group(1))
    vi = re.search(r"\b(?:chương|chuong|chapter)\s+(\d+)\b", title, re.IGNORECASE)
    if vi:
        return int(vi.group(1))
    return None


def extract_alignment_anchors(text: str, *, lang: str | None = None) -> list[str]:
    haystack = text if lang == "zh" else normalize_text(text).lower()
    anchors = []
    for anchor_id, aliases in ALIGNMENT_ANCHORS.items():
        langs = [lang] if lang in {"zh", "vi"} else ["zh", "vi"]
        for anchor_lang in langs:
            for alias in aliases.get(anchor_lang, []):
                needle = alias if anchor_lang == "zh" else alias.lower()
                if needle and needle in haystack:
                    anchors.append(anchor_id)
                    break
            if anchor_id in anchors:
                break
    return sorted(set(anchors))


def chapter_alignment_score(raw: dict[str, Any], target: dict[str, Any], raw_index: int, target_index: int) -> dict[str, Any]:
    raw_number = chapter_number(raw.get("title"))
    target_number = chapter_number(target.get("title"))
    raw_anchors = set(extract_alignment_anchors(raw["text"][:1200] + raw["text"][-1200:], lang="zh"))
    target_anchors = set(extract_alignment_anchors(target["text"][:2200] + target["text"][-2200:], lang="vi"))
    shared = sorted(raw_anchors & target_anchors)
    anchor_score = len(shared) / max(len(raw_anchors), 1) if raw_anchors else 0.0
    number_score = 1.0 if raw_number and target_number and raw_number == target_number else 0.5
    position_score = max(0.0, 1 - abs(raw_index - target_index) / max(raw_index, target_index, 1))
    length_ratio = len(target["text"]) / max(len(raw["text"]), 1)
    length_score = max(0.0, 1 - abs(length_ratio - 2.5) / 2.5)
    score = round(
        0.35 * anchor_score + 0.25 * number_score + 0.25 * position_score + 0.15 * length_score,
        3,
    )
    warnings = []
    if raw_number and target_number and raw_number != target_number:
        warnings.append(f"chapter_number_mismatch:raw={raw_number},target={target_number}")
    if length_ratio < 1.0 or length_ratio > 4.5:
        warnings.append(f"chapter_length_ratio_outlier:{length_ratio:.3f}")
    if anchor_score < 0.25:
        warnings.append("low_chapter_anchor_overlap")
    return {
        "score": score,
        "raw_chapter_number": raw_number,
        "target_chapter_number": target_number,
        "shared_anchors": shared,
        "raw_anchors": sorted(raw_anchors),
        "target_anchors": sorted(target_anchors),
        "length_ratio": round(length_ratio, 3),
        "warnings": warnings,
        "accepted": score >= 0.55 and not warnings[:1],
    }


def align_chapters(raw_chapters: list[dict[str, Any]], target_chapters: list[dict[str, Any]]) -> dict[str, Any]:
    count = min(len(raw_chapters), len(target_chapters))
    pairs = []
    for index in range(count):
        raw = raw_chapters[index]
        target = target_chapters[index]
        scored = chapter_alignment_score(raw, target, index, index)
        pairs.append(
            {
                "chapter_id": index + 1,
                "raw_chapter_id": raw["chapter_id"],
                "target_chapter_id": target["chapter_id"],
                "raw_title": raw.get("title"),
                "target_title": target.get("title"),
                "raw_chars": len(raw["text"]),
                "target_chars": len(target["text"]),
                "confidence": scored["score"],
                "method": "spine_order_index_alignment_with_anchor_validation",
                **scored,
            }
        )
    return {
        "aligned_chapters": count,
        "raw_chapters": len(raw_chapters),
        "target_chapters": len(target_chapters),
        "pairs": pairs,
        "warnings": [] if count else ["no_chapters_aligned"],
    }


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    parts: list[tuple[int, int, str]] = []
    for match in re.finditer(r"(?s)\S.*?(?:\n\s*\n|$)", text):
        segment = match.group(0).strip()
        if segment:
            parts.append((match.start(), match.start() + len(match.group(0).rstrip()), segment))
    return parts


def split_text_paragraphs(text: str, *, kind: str) -> list[dict[str, Any]]:
    paragraphs = []
    for index, (start, end, paragraph_text) in enumerate(_paragraphs(text), start=1):
        paragraphs.append(
            {
                "paragraph_index": index,
                "paragraph_id": f"{kind}{index:03d}",
                "start_offset": start,
                "end_offset": end,
                "text": paragraph_text,
                "char_count": len(paragraph_text),
            }
        )
    return paragraphs


def classify_alignment_paragraph(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("【") or stripped.endswith("】"):
        return "panel"
    if stripped.startswith(("“", '"', "'")) or "：" in stripped[:18] and "”" in stripped:
        return "dialogue"
    if stripped.startswith(("-", "—")):
        return "dialogue"
    return "narrative"


def combine_block_type(types: list[str]) -> str:
    unique = sorted(set(types))
    return unique[0] if len(unique) == 1 else "mixed"


def build_alignment_blocks(
    chapters: list[dict[str, Any]],
    *,
    lang: str,
    max_block_chars: int | None = None,
) -> list[dict[str, Any]]:
    max_chars = max_block_chars or (260 if lang == "zh" else 620)
    blocks: list[dict[str, Any]] = []
    for chapter in chapters:
        paragraph_rows = split_text_paragraphs(chapter["text"], kind=f"{lang[0]}p")
        current: list[dict[str, Any]] = []
        current_types: list[str] = []

        def flush() -> None:
            nonlocal current, current_types
            if not current:
                return
            text = "\n\n".join(item["text"] for item in current).strip()
            block_index = len(blocks) + 1
            block_type = combine_block_type(current_types)
            blocks.append(
                {
                    "block_id": f"{lang}_b{block_index:04d}",
                    "chapter_id": chapter["chapter_id"],
                    "chapter_title": chapter.get("title"),
                    "block_index": block_index,
                    "block_type": block_type,
                    "text": text,
                    "block_char_count": len(text),
                    "paragraph_indexes": [item["paragraph_index"] for item in current],
                    "start_offset": current[0]["start_offset"],
                    "end_offset": current[-1]["end_offset"],
                    "anchors": extract_alignment_anchors(text, lang=lang),
                }
            )
            current = []
            current_types = []

        for paragraph in paragraph_rows:
            paragraph_type = classify_alignment_paragraph(paragraph["text"])
            projected = (
                sum(len(item["text"]) for item in current)
                + (2 * len(current) if current else 0)
                + len(paragraph["text"])
            )
            if not current:
                current = [paragraph]
                current_types = [paragraph_type]
                continue
            current_type = combine_block_type(current_types)
            should_flush = False
            if paragraph_type == "panel" and current_type != "panel":
                should_flush = True
            elif current_type == "panel" and paragraph_type != "panel":
                should_flush = True
            elif paragraph_type == "dialogue" and current_type not in {"dialogue", "mixed"}:
                should_flush = True
            elif projected > max_chars:
                should_flush = True
            if should_flush:
                flush()
            current.append(paragraph)
            current_types.append(paragraph_type)
            if paragraph_type == "panel" and len(current) >= 8:
                flush()
        flush()
    return blocks


def block_pair_score(
    source_block: dict[str, Any],
    target_block: dict[str, Any],
    *,
    source_count: int,
    target_count: int,
) -> dict[str, Any]:
    source_anchors = set(source_block.get("anchors", []))
    target_anchors = set(target_block.get("anchors", []))
    shared = sorted(source_anchors & target_anchors)
    anchor_score = len(shared) / max(len(source_anchors | target_anchors), 1)
    if shared:
        anchor_score = max(anchor_score, min(1.0, len(shared) / max(len(source_anchors), 1)))
    type_pair = (source_block.get("block_type"), target_block.get("block_type"))
    if type_pair[0] == type_pair[1]:
        type_score = 1.0
    elif "mixed" in type_pair:
        type_score = 0.65
    elif "panel" in type_pair:
        type_score = 0.1
    else:
        type_score = 0.45
    length_ratio = target_block["block_char_count"] / max(source_block["block_char_count"], 1)
    length_score = max(0.0, 1 - abs(length_ratio - 2.5) / 2.7)
    source_pos = source_block["block_index"] / max(source_count, 1)
    target_pos = target_block["block_index"] / max(target_count, 1)
    position_score = max(0.0, 1 - abs(source_pos - target_pos) * 1.5)
    score = round(
        0.52 * anchor_score + 0.18 * type_score + 0.15 * length_score + 0.15 * position_score,
        3,
    )
    if not shared and score > 0.52:
        score = 0.52
    return {
        "score": score,
        "shared_anchors": shared,
        "source_anchors": sorted(source_anchors),
        "target_anchors": sorted(target_anchors),
        "length_ratio": round(length_ratio, 3),
        "type_score": round(type_score, 3),
        "length_score": round(length_score, 3),
        "position_score": round(position_score, 3),
        "anchor_score": round(anchor_score, 3),
    }


def align_blocks_monotonic(
    source_blocks: list[dict[str, Any]],
    target_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    n = len(source_blocks)
    m = len(target_blocks)
    if not n or not m:
        return []
    scores = [
        [
            block_pair_score(source_blocks[i], target_blocks[j], source_count=n, target_count=m)
            for j in range(m)
        ]
        for i in range(n)
    ]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    back: list[list[str | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            pair_score = scores[i - 1][j - 1]["score"]
            match_value = dp[i - 1][j - 1] + (pair_score if pair_score >= 0.38 else -0.15)
            skip_source = dp[i - 1][j] - 0.02
            skip_target = dp[i][j - 1] - 0.02
            best = max(match_value, skip_source, skip_target, 0.0)
            dp[i][j] = best
            if best == match_value:
                back[i][j] = "match"
            elif best == skip_source:
                back[i][j] = "skip_source"
            elif best == skip_target:
                back[i][j] = "skip_target"
    matches = []
    i, j = n, m
    while i > 0 and j > 0:
        move = back[i][j]
        if move == "match":
            score = scores[i - 1][j - 1]
            if score["score"] >= 0.38:
                matches.append(
                    {
                        "source_block_id": source_blocks[i - 1]["block_id"],
                        "target_block_id": target_blocks[j - 1]["block_id"],
                        "source_block_index": source_blocks[i - 1]["block_index"],
                        "target_block_index": target_blocks[j - 1]["block_index"],
                        "source_chapter_id": source_blocks[i - 1]["chapter_id"],
                        "target_chapter_id": target_blocks[j - 1]["chapter_id"],
                        **score,
                    }
                )
            i -= 1
            j -= 1
        elif move == "skip_source":
            i -= 1
        elif move == "skip_target":
            j -= 1
        else:
            break
    return list(reversed(matches))


def _group_indexes(count: int, group_count: int) -> list[list[int]]:
    if count <= 0 or group_count <= 0:
        return []
    groups: list[list[int]] = []
    for group_index in range(group_count):
        start = round(group_index * count / group_count)
        end = round((group_index + 1) * count / group_count)
        indexes = list(range(start + 1, max(start + 1, end + 1)))
        groups.append(indexes or [min(count, start + 1)])
    return groups


def _merge_paragraph_text(paragraphs: list[dict[str, Any]], indexes: list[int]) -> str:
    lookup = {paragraph["paragraph_index"]: paragraph for paragraph in paragraphs}
    return "\n\n".join(lookup[index]["text"] for index in indexes if index in lookup).strip()


def _paragraph_pair(
    *,
    pair_index: int,
    source_paragraphs: list[dict[str, Any]],
    target_paragraphs: list[dict[str, Any]],
    source_indexes: list[int],
    target_indexes: list[int],
) -> dict[str, Any]:
    source_text = _merge_paragraph_text(source_paragraphs, source_indexes)
    target_text = _merge_paragraph_text(target_paragraphs, target_indexes)
    source_char_count = len(source_text)
    target_char_count = len(target_text)
    strict_ratio = (
        PARAGRAPH_SHORT_STRICT_MAX_RATIO
        if target_char_count < PARAGRAPH_SHORT_REFERENCE_CHARS
        else PARAGRAPH_STRICT_MAX_RATIO
    )
    return {
        "paragraph_id": f"p{pair_index:03d}",
        "source_paragraph_indexes": source_indexes,
        "target_paragraph_indexes": target_indexes,
        "source_text": source_text,
        "target_text": target_text,
        "source_char_count": source_char_count,
        "target_char_count": target_char_count,
        "target_source_ratio": round(target_char_count / max(source_char_count, 1), 3),
        "target_min": int(target_char_count * PARAGRAPH_TARGET_MIN_RATIO),
        "target_max": int(target_char_count * PARAGRAPH_TARGET_MAX_RATIO),
        "strict_max": int(target_char_count * strict_ratio),
        "strict_max_ratio": strict_ratio,
        "budget_policy_used": "short_paragraph_relaxed"
        if target_char_count < PARAGRAPH_SHORT_REFERENCE_CHARS
        else "normal_paragraph",
    }


def create_paragraph_alignment(source_text: str, target_text: str) -> dict[str, Any]:
    source_paragraphs = split_text_paragraphs(source_text, kind="s")
    target_paragraphs = split_text_paragraphs(target_text, kind="t")
    warnings: list[str] = []
    if not source_paragraphs or not target_paragraphs:
        return {
            "source_paragraphs": source_paragraphs,
            "target_paragraphs": target_paragraphs,
            "paragraph_pairs": [],
            "warnings": ["missing_source_or_target_paragraphs"],
            "method": "paragraph_order_alignment",
        }
    if len(source_paragraphs) != len(target_paragraphs):
        warnings.append(
            f"paragraph_count_mismatch:source={len(source_paragraphs)},target={len(target_paragraphs)}"
        )
    initial_pair_count = min(len(source_paragraphs), len(target_paragraphs))
    pair_count = initial_pair_count
    if initial_pair_count > 24:
        target_total = sum(paragraph["char_count"] for paragraph in target_paragraphs)
        pair_count = max(8, min(initial_pair_count, round(target_total / 160)))
        warnings.append(
            f"paragraph_pairs_merged_for_eval_budget:initial={initial_pair_count},merged={pair_count}"
        )
    source_groups = _group_indexes(len(source_paragraphs), pair_count)
    target_groups = _group_indexes(len(target_paragraphs), pair_count)
    pairs = [
        _paragraph_pair(
            pair_index=index,
            source_paragraphs=source_paragraphs,
            target_paragraphs=target_paragraphs,
            source_indexes=source_groups[index - 1],
            target_indexes=target_groups[index - 1],
        )
        for index in range(1, pair_count + 1)
    ]
    return {
        "source_paragraphs": source_paragraphs,
        "target_paragraphs": target_paragraphs,
        "paragraph_pairs": pairs,
        "warnings": warnings,
        "method": "paragraph_order_alignment"
        if not warnings
        else "conservative_merged_paragraph_order_alignment",
    }


def evaluate_alignment_quality(alignment: dict[str, Any]) -> dict[str, Any]:
    source_count = len(alignment.get("source_paragraphs", []))
    target_count = len(alignment.get("target_paragraphs", []))
    pair_count = len(alignment.get("paragraph_pairs", []))
    warnings = list(alignment.get("warnings", []))
    score = 1.0
    reasons: list[str] = []
    if not source_count or not target_count or not pair_count:
        return {
            "alignment_quality": 0.0,
            "alignment_warnings": sorted(set(warnings + ["missing_alignment_paragraphs"])),
            "accepted_for_stable_validation": False,
        }

    count_delta = abs(source_count - target_count) / max(source_count, target_count, 1)
    if count_delta > 0:
        score -= min(0.50, count_delta)
        reasons.append(f"paragraph_count_delta={count_delta:.3f}")
    initial_pair_count = min(source_count, target_count)
    if pair_count < initial_pair_count:
        merged_ratio = 1 - (pair_count / max(initial_pair_count, 1))
        score -= min(0.30, merged_ratio)
        reasons.append(f"merged_pair_ratio={merged_ratio:.3f}")
    for warning in warnings:
        if warning.startswith("paragraph_count_mismatch"):
            score -= 0.10
        elif warning.startswith("paragraph_pairs_merged"):
            score -= 0.10
        else:
            score -= 0.05

    pair_ratios = [
        float(pair.get("target_source_ratio", 0))
        for pair in alignment.get("paragraph_pairs", [])
        if pair.get("source_char_count")
    ]
    outliers = [ratio for ratio in pair_ratios if ratio < 0.35 or ratio > 4.5]
    if outliers:
        score -= min(0.25, 0.05 * len(outliers))
        reasons.append(f"length_ratio_outlier_count={len(outliers)}")

    source_panel_count = sum(paragraph.get("text", "").count("【") for paragraph in alignment.get("source_paragraphs", []))
    target_panel_count = sum(paragraph.get("text", "").count("【") for paragraph in alignment.get("target_paragraphs", []))
    if source_panel_count or target_panel_count:
        panel_delta = abs(source_panel_count - target_panel_count) / max(source_panel_count, target_panel_count, 1)
        if panel_delta > 0.35:
            score -= 0.15
            reasons.append(f"system_panel_count_delta={panel_delta:.3f}")

    score = max(0.0, round(score, 3))
    alignment_warnings = sorted(set(warnings + reasons))
    return {
        "alignment_quality": score,
        "alignment_warnings": alignment_warnings,
        "accepted_for_stable_validation": score >= ALIGNMENT_QUALITY_THRESHOLD,
    }


def add_paragraph_alignment(sample: dict[str, Any]) -> dict[str, Any]:
    alignment = create_paragraph_alignment(sample["source_text"], sample["target_text"])
    sample.update(alignment)
    sample["paragraph_alignment_warnings"] = alignment["warnings"]
    quality = evaluate_alignment_quality(alignment)
    sample.update(quality)
    return sample


def paragraph_alignment_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "paragraph_alignment_report_v1",
        "sample_count": len(samples),
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "source_paragraph_count": len(sample.get("source_paragraphs", [])),
                "target_paragraph_count": len(sample.get("target_paragraphs", [])),
                "paragraph_pair_count": len(sample.get("paragraph_pairs", [])),
                "method": sample.get("method"),
                "warnings": sample.get("paragraph_alignment_warnings", []),
                "alignment_quality": sample.get("alignment_quality"),
                "alignment_warnings": sample.get("alignment_warnings", []),
                "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
            }
            for sample in samples
        ],
    }


def _pair_text_type(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty"
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    panel_lines = [line for line in lines if line.startswith("【") or line.endswith("】")]
    if panel_lines and len(panel_lines) >= max(1, len(lines) - 1):
        return "panel"
    if re.match(r"^[\"'“”‘’「『（(]*[A-ZÀ-ỸĐ\wÀ-ỹđĐ]{1,24}\s*[:：]", stripped):
        return "dialogue"
    if re.match(r"^[\"'“”‘’「『].+", stripped):
        return "dialogue"
    return "narrative"


def _pair_unit_type(pair: dict[str, Any]) -> str:
    source_type = _pair_text_type(pair.get("source_text", ""))
    target_type = _pair_text_type(pair.get("target_text", ""))
    if source_type == "panel" or target_type == "panel":
        return "panel"
    if source_type == "dialogue" or target_type == "dialogue":
        return "dialogue"
    return "narrative"


def _is_scene_boundary_pair(pair: dict[str, Any]) -> bool:
    combined = f"{pair.get('source_text', '')}\n{pair.get('target_text', '')}".strip()
    if not combined:
        return True
    if CHAPTER_HEADING_RE.match(combined):
        return True
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    if lines and all(re.fullmatch(r"[-=*_]{3,}|[·•。…]{3,}", line) for line in lines):
        return True
    return any(
        re.match(r"^(第\s*\d+\s*章|chương\s+\d+)\b", line, flags=re.IGNORECASE)
        for line in lines
    )


def _merge_compatible(current_type: str, next_type: str) -> bool:
    if "boundary" in {current_type, next_type}:
        return False
    if current_type == "panel" or next_type == "panel":
        return current_type == next_type
    return True


def _unit_type_for_pairs(pairs: list[dict[str, Any]]) -> str:
    types = [_pair_unit_type(pair) for pair in pairs]
    if not types:
        return "empty"
    if all(item == types[0] for item in types):
        return types[0]
    if "panel" in types:
        return "mixed"
    if "dialogue" in types:
        return "dialogue"
    return "narrative"


def _unit_text(pairs: list[dict[str, Any]], key: str) -> str:
    return "\n\n".join(str(pair.get(key, "")).strip() for pair in pairs if str(pair.get(key, "")).strip())


def _unit_merge_reason(pairs: list[dict[str, Any]], unit_target_min_chars: int) -> str:
    if len(pairs) == 1:
        return "single_paragraph"
    if all(_pair_unit_type(pair) == "panel" for pair in pairs):
        return "consecutive_system_panel_lines"
    if any(_pair_unit_type(pair) == "dialogue" for pair in pairs):
        return "short_dialogue_fragment_merge"
    if sum(pair.get("target_char_count", 0) for pair in pairs) < unit_target_min_chars:
        return "tiny_paragraph_merge_to_minimum_unit"
    return "adjacent_short_paragraph_merge"


def _unit_from_pairs(
    *,
    unit_index: int,
    pairs: list[dict[str, Any]],
    sample: dict[str, Any],
    unit_target_min_chars: int,
) -> dict[str, Any]:
    source_text = _unit_text(pairs, "source_text")
    target_text = _unit_text(pairs, "target_text")
    source_char_count = len(source_text)
    target_char_count = len(target_text)
    strict_ratio = (
        UNIT_SHORT_STRICT_MAX_RATIO
        if target_char_count < unit_target_min_chars
        else UNIT_STRICT_MAX_RATIO
    )
    unit = {
        "unit_id": f"u{unit_index:03d}",
        "paragraph_id": f"u{unit_index:03d}",
        "source_paragraph_ids": [pair["paragraph_id"] for pair in pairs],
        "target_paragraph_ids": [pair["paragraph_id"] for pair in pairs],
        "original_paragraph_ids": [pair["paragraph_id"] for pair in pairs],
        "source_paragraph_indexes": [
            index
            for pair in pairs
            for index in pair.get("source_paragraph_indexes", [])
        ],
        "target_paragraph_indexes": [
            index
            for pair in pairs
            for index in pair.get("target_paragraph_indexes", [])
        ],
        "source_text": source_text,
        "target_text": target_text,
        "reference_text": target_text,
        "source_char_count": source_char_count,
        "target_char_count": target_char_count,
        "reference_char_count": target_char_count,
        "target_source_ratio": round(target_char_count / max(source_char_count, 1), 3),
        "target_min": int(target_char_count * PARAGRAPH_TARGET_MIN_RATIO),
        "target_max": int(target_char_count * PARAGRAPH_TARGET_MAX_RATIO),
        "strict_max": max(
            int(target_char_count * PARAGRAPH_TARGET_MAX_RATIO),
            int(target_char_count * strict_ratio),
        ),
        "strict_max_ratio": strict_ratio,
        "budget_policy_used": "merged_short_unit_relaxed"
        if target_char_count < unit_target_min_chars
        else "merged_unit",
        "unit_type": _unit_type_for_pairs(pairs),
        "merge_reason": _unit_merge_reason(pairs, unit_target_min_chars),
        "alignment_quality": sample.get("alignment_quality", 1.0),
        "alignment_warnings": sample.get("alignment_warnings", []),
        "is_merged_unit": len(pairs) > 1,
        "original_paragraph_count": len(pairs),
    }
    unit["required_terms"] = required_terms_for_pair(
        unit,
        {"fixed_terms": [{"source": source, "target": target} for source, target in FIXED_GLOSSARY.items()]},
    )
    return unit


def build_translation_units(
    sample: dict[str, Any],
    *,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
) -> list[dict[str, Any]]:
    pairs = list(sample.get("paragraph_pairs", []))
    if not pairs:
        return []
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(pairs):
        current = pairs[index]
        current_type = "boundary" if _is_scene_boundary_pair(current) else _pair_unit_type(current)
        group = [current]
        index += 1
        while index < len(pairs):
            next_pair = pairs[index]
            next_type = "boundary" if _is_scene_boundary_pair(next_pair) else _pair_unit_type(next_pair)
            group_source_chars = sum(pair.get("source_char_count", 0) for pair in group)
            group_target_chars = sum(pair.get("target_char_count", 0) for pair in group)
            group_tiny = (
                group_source_chars < tiny_paragraph_threshold
                or group_target_chars < tiny_paragraph_threshold
                or group_target_chars < unit_target_min_chars
            )
            next_tiny = (
                next_pair.get("source_char_count", 0) < tiny_paragraph_threshold
                or next_pair.get("target_char_count", 0) < tiny_paragraph_threshold
                or next_pair.get("target_char_count", 0) < unit_target_min_chars
            )
            same_panel_run = current_type == "panel" and next_type == "panel"
            if not _merge_compatible(current_type, next_type):
                break
            if not (group_tiny or next_tiny or same_panel_run):
                break
            group.append(next_pair)
            current_type = _unit_type_for_pairs(group)
            index += 1
            if sum(pair.get("target_char_count", 0) for pair in group) >= unit_target_min_chars and not same_panel_run:
                break
        if (
            len(group) == 1
            and units
            and not _is_scene_boundary_pair(group[0])
            and (
                group[0].get("target_char_count", 0) < tiny_paragraph_threshold
                or group[0].get("source_char_count", 0) < tiny_paragraph_threshold
                or group[0].get("target_char_count", 0) < unit_target_min_chars
            )
            and not any(_is_scene_boundary_pair(pair) for pair in units[-1])
            and _merge_compatible(_unit_type_for_pairs(units[-1]), _pair_unit_type(group[0]))
        ):
            units[-1].extend(group)
        else:
            units.append(group)
    merged_units: list[list[dict[str, Any]]] = []
    for group in units:
        group_source_chars = sum(pair.get("source_char_count", 0) for pair in group)
        group_target_chars = sum(pair.get("target_char_count", 0) for pair in group)
        group_type = _unit_type_for_pairs(group)
        high_risk_isolated_unit = (
            group_type != "panel"
            and group_source_chars >= UNIT_HIGH_RISK_SOURCE_CHARS
            and group_target_chars < UNIT_HIGH_RISK_TARGET_MAX_CHARS
        )
        if (
            high_risk_isolated_unit
            and merged_units
            and not any(_is_scene_boundary_pair(pair) for pair in group)
            and not any(_is_scene_boundary_pair(pair) for pair in merged_units[-1])
            and _merge_compatible(_unit_type_for_pairs(merged_units[-1]), group_type)
            and sum(pair.get("target_char_count", 0) for pair in merged_units[-1] + group)
            <= UNIT_COMBINED_TARGET_MAX_CHARS
        ):
            merged_units[-1].extend(group)
        else:
            merged_units.append(group)
    units = merged_units
    return [
        _unit_from_pairs(
            unit_index=unit_index,
            pairs=unit_pairs,
            sample=sample,
            unit_target_min_chars=unit_target_min_chars,
        )
        for unit_index, unit_pairs in enumerate(units, start=1)
    ]


def apply_translation_units(
    samples: list[dict[str, Any]],
    *,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
) -> list[dict[str, Any]]:
    updated = []
    for sample in samples:
        sample_copy = dict(sample)
        units = build_translation_units(
            sample_copy,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
        )
        merge_count = sum(max(0, unit.get("original_paragraph_count", 1) - 1) for unit in units)
        sample_copy["translation_units"] = units
        sample_copy["use_translation_units"] = bool(merge_tiny_paragraphs and merge_count)
        sample_copy["translation_unit_settings"] = {
            "merge_tiny_paragraphs": merge_tiny_paragraphs,
            "tiny_paragraph_threshold": tiny_paragraph_threshold,
            "unit_target_min_chars": unit_target_min_chars,
        }
        sample_copy["translation_unit_merge_count"] = merge_count
        updated.append(sample_copy)
    return updated


def active_eval_pairs(sample: dict[str, Any]) -> list[dict[str, Any]]:
    if sample.get("use_translation_units") and sample.get("translation_units"):
        return sample["translation_units"]
    return sample.get("paragraph_pairs", [])


def translation_units_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    all_units = [unit for sample in samples for unit in sample.get("translation_units", [])]
    return {
        "schema_version": "translation_units_v1",
        "sample_count": len(samples),
        "unit_count": len(all_units),
        "merged_unit_count": sum(1 for unit in all_units if unit.get("is_merged_unit")),
        "paragraph_merge_count": sum(max(0, unit.get("original_paragraph_count", 1) - 1) for unit in all_units),
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "use_translation_units": sample.get("use_translation_units", False),
                "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
                "units": sample.get("translation_units", []),
            }
            for sample in samples
        ],
    }


def unit_alignment_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for sample in samples:
        for unit in sample.get("translation_units", []):
            rows.append(
                {
                    "sample_id": sample["sample_id"],
                    "chapter_id": sample["chapter_id"],
                    "unit_id": unit["unit_id"],
                    "source_paragraph_ids": unit["source_paragraph_ids"],
                    "target_paragraph_ids": unit["target_paragraph_ids"],
                    "unit_type": unit["unit_type"],
                    "merge_reason": unit["merge_reason"],
                    "source_char_count": unit["source_char_count"],
                    "reference_char_count": unit["reference_char_count"],
                    "strict_max": unit["strict_max"],
                    "alignment_quality": unit.get("alignment_quality"),
                    "warnings": unit.get("alignment_warnings", []),
                    "accepted_for_stable_validation": (
                        unit.get("alignment_quality", 0) >= ALIGNMENT_QUALITY_THRESHOLD
                    ),
                }
            )
    return {
        "schema_version": "unit_alignment_report_v1",
        "unit_count": len(rows),
        "merged_unit_count": sum(1 for row in rows if len(row["source_paragraph_ids"]) > 1),
        "units": rows,
    }


def select_sample(
    source_text: str,
    target_text: str,
    *,
    chapter_id: int,
    max_source_chars: int,
    max_target_chars: int,
    sample_start_ratio: float = 0.0,
    sample_id: str | None = None,
    selection_reason: str | None = None,
) -> dict[str, Any]:
    source_parts = _paragraphs(source_text)
    target_parts = _paragraphs(target_text)
    if not source_parts:
        raise ValueError("Source chapter has no selectable text.")
    if not target_parts:
        raise ValueError("Target chapter has no selectable text.")

    source_start_index = min(int(len(source_parts) * sample_start_ratio), max(len(source_parts) - 1, 0))
    target_start_index = min(int(len(target_parts) * sample_start_ratio), max(len(target_parts) - 1, 0))
    if source_start_index == 0 and len(source_parts) > 1 and CHAPTER_HEADING_RE.match(source_parts[0][2]):
        source_start_index = 1
    if target_start_index == 0 and len(target_parts) > 1 and CHAPTER_HEADING_RE.match(target_parts[0][2]):
        target_start_index = 1

    selected_source: list[tuple[int, int, str]] = []
    source_chars = 0
    for part in source_parts[source_start_index:]:
        addition = len(part[2])
        if selected_source and source_chars + 2 + addition > max_source_chars:
            break
        if not selected_source and addition > max_source_chars:
            selected_source.append((part[0], part[0] + max_source_chars, part[2][:max_source_chars].rstrip()))
            break
        selected_source.append(part)
        source_chars += (2 if source_chars else 0) + addition
    selected_source = selected_source or [source_parts[source_start_index]]

    target_selected: list[tuple[int, int, str]] = []
    target_chars = 0
    for part in target_parts[target_start_index:]:
        addition = len(part[2])
        if target_selected and target_chars + 2 + addition > max_target_chars:
            break
        if not target_selected and addition > max_target_chars:
            target_selected.append((part[0], part[0] + max_target_chars, part[2][:max_target_chars].rstrip()))
            break
        target_selected.append(part)
        target_chars += (2 if target_chars else 0) + addition
    target_selected = target_selected or [target_parts[target_start_index]]

    source_excerpt = "\n\n".join(part[2] for part in selected_source).strip()
    target_excerpt = "\n\n".join(part[2] for part in target_selected).strip()
    source_paragraph_count = paragraph_count(source_excerpt)
    target_paragraph_count = paragraph_count(target_excerpt)
    target_char_count = len(target_excerpt)
    source_char_count = len(source_excerpt)
    sample = {
        "sample_id": sample_id or f"sample_{chapter_id}",
        "chapter_id": chapter_id,
        "source_start_offset": selected_source[0][0],
        "source_end_offset": selected_source[-1][1],
        "source_char_count": source_char_count,
        "target_start_offset": target_selected[0][0],
        "target_end_offset": target_selected[-1][1],
        "target_char_count": target_char_count,
        "target_source_length_ratio": round(target_char_count / max(source_char_count, 1), 3),
        "paragraph_count_source": source_paragraph_count,
        "paragraph_count_target": target_paragraph_count,
        "target_length_min": int(target_char_count * PROMPT_TARGET_MIN_RATIO),
        "target_length_max": int(target_char_count * PROMPT_TARGET_MAX_RATIO),
        "selection_reason": selection_reason
        or "first_coherent_scene_after_chapter_title_preserving_paragraph_boundaries",
        "limits_used": {
            "max_source_chars": max_source_chars,
            "max_target_chars": max_target_chars,
            "sample_start_ratio": sample_start_ratio,
        },
        "source_text": source_excerpt,
        "target_text": target_excerpt,
    }
    return add_paragraph_alignment(sample)


def _blocks_by_index(blocks: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {block["block_index"]: block for block in blocks}


def _block_window(blocks: list[dict[str, Any]], start_index: int, end_index: int) -> list[dict[str, Any]]:
    return [
        block
        for block in blocks
        if start_index <= block["block_index"] <= end_index
    ]


def _blocks_text(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(block["text"] for block in blocks).strip()


def _block_panel_ratio(blocks: list[dict[str, Any]]) -> float:
    chars = sum(block["block_char_count"] for block in blocks)
    if not chars:
        return 0.0
    panel_chars = sum(
        block["block_char_count"]
        for block in blocks
        if block.get("block_type") == "panel"
    )
    return round(panel_chars / chars, 3)


def block_window_candidate(
    *,
    candidate_id: str,
    source_blocks: list[dict[str, Any]],
    target_blocks: list[dict[str, Any]],
    matched_pairs: list[dict[str, Any]],
    max_source_chars: int,
    max_target_chars: int,
) -> dict[str, Any] | None:
    if not source_blocks or not target_blocks:
        return None
    source_text = _blocks_text(source_blocks)
    target_text = _blocks_text(target_blocks)
    if not source_text or not target_text:
        return None
    if len(source_text) > max_source_chars or len(target_text) > max_target_chars:
        return None
    source_anchors = set(extract_alignment_anchors(source_text, lang="zh"))
    target_anchors = set(extract_alignment_anchors(target_text, lang="vi"))
    shared = sorted(source_anchors & target_anchors)
    avg_pair_score = (
        sum(pair["score"] for pair in matched_pairs) / max(len(matched_pairs), 1)
    )
    anchor_score = len(shared) / max(len(source_anchors), 1) if source_anchors else 0.0
    if shared:
        anchor_score = max(anchor_score, len(shared) / max(len(source_anchors | target_anchors), 1))
    length_ratio = len(target_text) / max(len(source_text), 1)
    length_score = max(0.0, 1 - abs(length_ratio - 2.5) / 2.7)
    source_span = source_blocks[-1]["block_index"] - source_blocks[0]["block_index"] + 1
    target_span = target_blocks[-1]["block_index"] - target_blocks[0]["block_index"] + 1
    gap_ratio = max(
        0.0,
        1
        - (
            (source_span - len(matched_pairs)) + (target_span - len(matched_pairs))
        )
        / max(source_span + target_span, 1),
    )
    source_panel_ratio = _block_panel_ratio(source_blocks)
    target_panel_ratio = _block_panel_ratio(target_blocks)
    quality = round(
        min(
            1.0,
            0.43 * avg_pair_score
            + 0.32 * anchor_score
            + 0.15 * length_score
            + 0.10 * gap_ratio
            + (0.08 if len(shared) >= 2 else 0.0),
        ),
        3,
    )
    rejection_reasons = []
    if not shared:
        rejection_reasons.append("no_shared_anchors")
    if len(source_text) < 40 or len(target_text) < 80:
        rejection_reasons.append("window_too_short")
    if length_ratio < 1.1 or length_ratio > 4.2:
        rejection_reasons.append(f"length_ratio_outlier:{length_ratio:.3f}")
    if source_panel_ratio > 0.80 or target_panel_ratio > 0.80:
        rejection_reasons.append("mostly_system_panel_fragment")
    if quality < ALIGNMENT_QUALITY_THRESHOLD:
        rejection_reasons.append("alignment_quality_below_threshold")
    return {
        "candidate_id": candidate_id,
        "source_chapter_id": source_blocks[0]["chapter_id"],
        "target_chapter_id": target_blocks[0]["chapter_id"],
        "source_block_start": source_blocks[0]["block_index"],
        "source_block_end": source_blocks[-1]["block_index"],
        "target_block_start": target_blocks[0]["block_index"],
        "target_block_end": target_blocks[-1]["block_index"],
        "source_char_count": len(source_text),
        "target_char_count": len(target_text),
        "target_source_length_ratio": round(length_ratio, 3),
        "source_panel_ratio": source_panel_ratio,
        "target_panel_ratio": target_panel_ratio,
        "shared_anchors": shared,
        "source_anchors": sorted(source_anchors),
        "target_anchors": sorted(target_anchors),
        "matched_pair_count": len(matched_pairs),
        "avg_pair_score": round(avg_pair_score, 3),
        "gap_score": round(gap_ratio, 3),
        "alignment_quality": quality,
        "accepted": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "source_block_ids": [block["block_id"] for block in source_blocks],
        "target_block_ids": [block["block_id"] for block in target_blocks],
        "block_pairs": matched_pairs,
        "source_blocks": source_blocks,
        "target_blocks": target_blocks,
        "source_text": source_text,
        "target_text": target_text,
    }


def build_alignment_candidates(
    source_blocks: list[dict[str, Any]],
    target_blocks: list[dict[str, Any]],
    block_pairs: list[dict[str, Any]],
    *,
    max_source_chars: int,
    max_target_chars: int,
) -> list[dict[str, Any]]:
    source_lookup = _blocks_by_index(source_blocks)
    target_lookup = _blocks_by_index(target_blocks)
    candidates: list[dict[str, Any]] = []
    for start in range(len(block_pairs)):
        matched: list[dict[str, Any]] = []
        for end in range(start, min(len(block_pairs), start + 10)):
            matched.append(block_pairs[end])
            source_start = min(pair["source_block_index"] for pair in matched)
            source_end = max(pair["source_block_index"] for pair in matched)
            target_start = min(pair["target_block_index"] for pair in matched)
            target_end = max(pair["target_block_index"] for pair in matched)
            source_window = [
                source_lookup[index]
                for index in range(source_start, source_end + 1)
                if index in source_lookup
            ]
            target_window = [
                target_lookup[index]
                for index in range(target_start, target_end + 1)
                if index in target_lookup
            ]
            candidate = block_window_candidate(
                candidate_id=f"cand_{len(candidates) + 1:04d}",
                source_blocks=source_window,
                target_blocks=target_window,
                matched_pairs=matched.copy(),
                max_source_chars=max_source_chars,
                max_target_chars=max_target_chars,
            )
            if candidate:
                candidates.append(candidate)
            if (
                sum(block["block_char_count"] for block in source_window) > max_source_chars
                or sum(block["block_char_count"] for block in target_window) > max_target_chars
            ):
                break
    candidates.sort(
        key=lambda item: (
            item["accepted"],
            item["alignment_quality"],
            len(item["shared_anchors"]),
            item["source_char_count"],
        ),
        reverse=True,
    )
    return candidates


def sample_from_alignment_candidate(candidate: dict[str, Any], *, sample_id: str) -> dict[str, Any]:
    sample = {
        "sample_id": sample_id,
        "chapter_id": candidate["source_chapter_id"],
        "source_start_offset": candidate["source_blocks"][0]["start_offset"],
        "source_end_offset": candidate["source_blocks"][-1]["end_offset"],
        "source_char_count": candidate["source_char_count"],
        "target_start_offset": candidate["target_blocks"][0]["start_offset"],
        "target_end_offset": candidate["target_blocks"][-1]["end_offset"],
        "target_char_count": candidate["target_char_count"],
        "target_source_length_ratio": candidate["target_source_length_ratio"],
        "paragraph_count_source": paragraph_count(candidate["source_text"]),
        "paragraph_count_target": paragraph_count(candidate["target_text"]),
        "target_length_min": int(candidate["target_char_count"] * PROMPT_TARGET_MIN_RATIO),
        "target_length_max": int(candidate["target_char_count"] * PROMPT_TARGET_MAX_RATIO),
        "selection_reason": "block_alignment_window_with_shared_anchors",
        "limits_used": {
            "max_source_chars": candidate["source_char_count"],
            "max_target_chars": candidate["target_char_count"],
            "sample_start_ratio": None,
        },
        "source_text": candidate["source_text"],
        "target_text": candidate["target_text"],
        "source_blocks": candidate["source_blocks"],
        "target_blocks": candidate["target_blocks"],
        "block_pairs": candidate["block_pairs"],
        "block_alignment_candidate_id": candidate["candidate_id"],
        "alignment_quality": candidate["alignment_quality"],
        "alignment_warnings": candidate["rejection_reasons"],
        "accepted_for_stable_validation": candidate["accepted"],
    }
    alignment = create_paragraph_alignment(sample["source_text"], sample["target_text"])
    sample.update(alignment)
    sample["paragraph_alignment_warnings"] = alignment["warnings"]
    for pair in sample.get("paragraph_pairs", []):
        pair["block_alignment_confidence"] = candidate["alignment_quality"]
        pair["source_block_ids"] = candidate["source_block_ids"]
        pair["target_block_ids"] = candidate["target_block_ids"]
    sample["alignment_quality"] = candidate["alignment_quality"]
    sample["alignment_warnings"] = sorted(
        set(candidate["rejection_reasons"] + alignment.get("warnings", []))
    )
    sample["accepted_for_stable_validation"] = (
        candidate["accepted"] and candidate["alignment_quality"] >= ALIGNMENT_QUALITY_THRESHOLD
    )
    return sample


def select_samples_from_alignment_candidates(
    candidates: list[dict[str, Any]],
    *,
    sample_count: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_source_ranges: list[tuple[int, int]] = []
    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    for candidate in accepted:
        source_range = (candidate["source_block_start"], candidate["source_block_end"])
        overlaps = any(
            not (source_range[1] < used[0] or source_range[0] > used[1])
            for used in used_source_ranges
        )
        if overlaps:
            continue
        selected.append(
            sample_from_alignment_candidate(candidate, sample_id=f"sample_{len(selected) + 1}")
        )
        used_source_ranges.append(source_range)
        if len(selected) >= sample_count:
            break
    if len(selected) < sample_count:
        for candidate in candidates:
            if candidate["accepted"]:
                continue
            selected.append(
                sample_from_alignment_candidate(candidate, sample_id=f"sample_{len(selected) + 1}")
            )
            if len(selected) >= sample_count:
                break
    return selected[:sample_count]


def compact_alignment_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate[key]
        for key in [
            "candidate_id",
            "source_chapter_id",
            "target_chapter_id",
            "source_block_start",
            "source_block_end",
            "target_block_start",
            "target_block_end",
            "source_char_count",
            "target_char_count",
            "target_source_length_ratio",
            "source_panel_ratio",
            "target_panel_ratio",
            "shared_anchors",
            "source_anchors",
            "target_anchors",
            "matched_pair_count",
            "avg_pair_score",
            "gap_score",
            "alignment_quality",
            "accepted",
            "rejection_reasons",
            "source_block_ids",
            "target_block_ids",
        ]
    } | {
        "source_preview": _snippet(candidate["source_text"], 220),
        "target_preview": _snippet(candidate["target_text"], 220),
    }


def block_alignment_report(
    *,
    source_blocks: list[dict[str, Any]],
    target_blocks: list[dict[str, Any]],
    block_pairs: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "block_alignment_report_v1",
        "source_block_count": len(source_blocks),
        "target_block_count": len(target_blocks),
        "matched_pair_count": len(block_pairs),
        "accepted_candidate_count": sum(1 for candidate in candidates if candidate["accepted"]),
        "source_blocks": [
            {
                "block_id": block["block_id"],
                "chapter_id": block["chapter_id"],
                "block_index": block["block_index"],
                "block_type": block["block_type"],
                "block_char_count": block["block_char_count"],
                "anchors": block["anchors"],
                "preview": _snippet(block["text"], 180),
            }
            for block in source_blocks
        ],
        "target_blocks": [
            {
                "block_id": block["block_id"],
                "chapter_id": block["chapter_id"],
                "block_index": block["block_index"],
                "block_type": block["block_type"],
                "block_char_count": block["block_char_count"],
                "anchors": block["anchors"],
                "preview": _snippet(block["text"], 180),
            }
            for block in target_blocks
        ],
        "block_pairs": block_pairs,
        "top_candidates": [compact_alignment_candidate(candidate) for candidate in candidates[:40]],
    }


def write_alignment_markdown(run_dir: Path, *, chapter_report: dict[str, Any], block_report: dict[str, Any]) -> None:
    chapter_lines = [
        "# Chapter Alignment Report",
        "",
        "| Raw | Target | Confidence | Accepted | Length Ratio | Shared Anchors | Warnings |",
        "|---|---|---:|---|---:|---|---|",
    ]
    for pair in chapter_report.get("pairs", []):
        chapter_lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(pair.get("raw_title"), 80),
                    _md_cell(pair.get("target_title"), 80),
                    str(pair.get("confidence")),
                    str(pair.get("accepted")),
                    str(pair.get("length_ratio")),
                    _md_cell(", ".join(pair.get("shared_anchors", [])), 80),
                    _md_cell("; ".join(pair.get("warnings", [])), 100),
                ]
            )
            + " |"
        )
    (run_dir / "chapter_alignment_report.md").write_text(
        "\n".join(chapter_lines) + "\n",
        encoding="utf-8",
    )

    block_lines = [
        "# Block Alignment Report",
        "",
        f"- Source blocks: `{block_report['source_block_count']}`",
        f"- Target blocks: `{block_report['target_block_count']}`",
        f"- Matched pairs: `{block_report['matched_pair_count']}`",
        f"- Accepted candidates: `{block_report['accepted_candidate_count']}`",
        "",
        "## Top Candidates",
        "",
        "| Candidate | Quality | Accepted | Src Blocks | Tgt Blocks | Ratio | Anchors | Reject Reasons | Source | Target |",
        "|---|---:|---|---|---|---:|---|---|---|---|",
    ]
    for candidate in block_report["top_candidates"]:
        block_lines.append(
            "| "
            + " | ".join(
                [
                    candidate["candidate_id"],
                    str(candidate["alignment_quality"]),
                    str(candidate["accepted"]),
                    f"{candidate['source_block_start']}-{candidate['source_block_end']}",
                    f"{candidate['target_block_start']}-{candidate['target_block_end']}",
                    str(candidate["target_source_length_ratio"]),
                    _md_cell(", ".join(candidate["shared_anchors"]), 80),
                    _md_cell("; ".join(candidate["rejection_reasons"]), 120),
                    _md_cell(candidate["source_preview"], 140),
                    _md_cell(candidate["target_preview"], 140),
                ]
            )
            + " |"
        )
    (run_dir / "block_alignment_report.md").write_text(
        "\n".join(block_lines) + "\n",
        encoding="utf-8",
    )


def select_samples(
    raw_chapters: list[dict[str, Any]],
    target_chapters: list[dict[str, Any]],
    *,
    sample_count: int,
    max_source_chars: int,
    max_target_chars: int,
    sample_start_ratio: float = 0.0,
) -> list[dict[str, Any]]:
    aligned_count = min(len(raw_chapters), len(target_chapters))
    if aligned_count <= 0:
        raise ValueError("No aligned chapters are available for sample selection.")
    if sample_count <= 0:
        raise ValueError("--sample-count must be greater than 0.")

    def choose_sample(
        source: str,
        target: str,
        *,
        chapter_id: int,
        sample_id: str,
        selection_reason: str,
    ) -> dict[str, Any]:
        ratios = []
        for ratio in [sample_start_ratio, 0.0, 0.25, 0.5, 0.75]:
            if ratio not in ratios:
                ratios.append(ratio)
        candidates = [
            select_sample(
                source,
                target,
                chapter_id=chapter_id,
                max_source_chars=max_source_chars,
                max_target_chars=max_target_chars,
                sample_start_ratio=ratio,
                sample_id=sample_id,
                selection_reason=selection_reason,
            )
            for ratio in ratios
        ]
        accepted = [
            sample for sample in candidates if sample.get("accepted_for_stable_validation")
        ]
        return max(accepted or candidates, key=lambda sample: sample.get("alignment_quality", 0))

    samples: list[dict[str, Any]] = []
    if aligned_count >= sample_count:
        for index in range(sample_count):
            chapter_id = index + 1
            samples.append(
                choose_sample(
                    raw_chapters[index]["text"],
                    target_chapters[index]["text"],
                    chapter_id=chapter_id,
                    sample_id=f"sample_{chapter_id}",
                    selection_reason="chapter_aligned_sample_preserving_paragraph_boundaries",
                )
            )
        return samples

    ratios = [sample_start_ratio]
    if sample_count > 1:
        ratios = [index / sample_count for index in range(sample_count)]
    for index, ratio in enumerate(ratios, start=1):
        samples.append(
            select_sample(
                raw_chapters[0]["text"],
                target_chapters[0]["text"],
                chapter_id=1,
                max_source_chars=max_source_chars,
                max_target_chars=max_target_chars,
                sample_start_ratio=ratio,
                sample_id=f"sample_{index}",
                selection_reason="fallback_non_overlapping_chapter_1_sample",
            )
        )
    return samples


def prepare_parallel(
    *,
    project: str,
    raw_path: Path,
    translated_path: Path,
    max_chapters: int = DEFAULT_LIMITS["alignment_max_chapters"],
    max_source_chars: int = DEFAULT_LIMITS["translation_sample_max_source_chars"],
    max_target_chars: int = 2500,
    sample_start_ratio: float = 0.0,
    sample_count: int = 1,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
) -> dict[str, Any]:
    if max_chapters <= 0:
        raise ValueError("--max-chapters must be greater than 0.")
    if max_source_chars <= 0 or max_target_chars <= 0:
        raise ValueError("--max-source-chars and --max-target-chars must be greater than 0.")
    if sample_start_ratio < 0 or sample_start_ratio > 1:
        raise ValueError("--sample-start-ratio must be between 0 and 1.")
    if sample_count <= 0:
        raise ValueError("--sample-count must be greater than 0.")
    if tiny_paragraph_threshold <= 0 or unit_target_min_chars <= 0:
        raise ValueError("--tiny-paragraph-threshold and --unit-target-min-chars must be greater than 0.")
    run_dir = new_run_dir(project, "eval")
    raw_chapters = extract_raw_chapters(raw_path, max_chapters=max_chapters)
    target_chapters = extract_epub_chapters(translated_path, max_chapters=max_chapters)
    alignment = align_chapters(raw_chapters, target_chapters)
    source_blocks = build_alignment_blocks(raw_chapters, lang="zh")
    target_blocks = build_alignment_blocks(target_chapters, lang="vi")
    block_pairs = align_blocks_monotonic(source_blocks, target_blocks)
    candidates = build_alignment_candidates(
        source_blocks,
        target_blocks,
        block_pairs,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    samples = select_samples_from_alignment_candidates(candidates, sample_count=sample_count)
    if not samples:
        samples = select_samples(
            raw_chapters,
            target_chapters,
            sample_count=sample_count,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
            sample_start_ratio=sample_start_ratio,
        )
    samples = apply_translation_units(
        samples,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
    )
    sample = samples[0]
    block_report = block_alignment_report(
        source_blocks=source_blocks,
        target_blocks=target_blocks,
        block_pairs=block_pairs,
        candidates=candidates,
    )
    write_json(run_dir / "extracted_raw_chapters.json", raw_chapters)
    write_json(run_dir / "extracted_translated_chapters.json", target_chapters)
    write_json(run_dir / "alignment_report.json", alignment)
    write_json(run_dir / "chapter_alignment_report.json", alignment)
    write_json(run_dir / "block_alignment_report.json", block_report)
    write_json(
        run_dir / "alignment_candidates.json",
        {
            "schema_version": "alignment_candidates_v1",
            "candidate_count": len(candidates),
            "accepted_candidate_count": sum(1 for candidate in candidates if candidate["accepted"]),
            "candidates": [compact_alignment_candidate(candidate) for candidate in candidates],
        },
    )
    write_json(run_dir / "paragraph_alignment_report.json", paragraph_alignment_report(samples))
    write_json(run_dir / "translation_units.json", translation_units_report(samples))
    write_json(run_dir / "unit_alignment_report.json", unit_alignment_report(samples))
    write_json(run_dir / "selected_sample.json", sample)
    write_json(run_dir / "selected_samples.json", {"samples": samples})
    write_alignment_markdown(run_dir, chapter_report=alignment, block_report=block_report)
    return {
        "run_dir": str(run_dir),
        "alignment": alignment,
        "block_alignment": block_report,
        "alignment_candidates": [compact_alignment_candidate(candidate) for candidate in candidates],
        "selected_sample": sample,
        "selected_samples": samples,
    }


def _candidate_terms(text: str, limit: int = 40) -> list[str]:
    seen = []
    for token in TOKEN_RE.findall(text):
        if (CHINESE_RE.search(token) or len(token) >= 2) and token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def _name_candidates(text: str, limit: int = 30) -> list[str]:
    seen = []
    for token in TOKEN_RE.findall(text):
        is_chinese_name = bool(CHINESE_RE.search(token)) and 2 <= len(token) <= 4
        is_capitalized = bool(re.match(r"[A-ZÀ-ỸĐ][\wÀ-ỹđĐ]{2,}$", token))
        if (is_chinese_name or is_capitalized) and token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def _pronoun_candidates(text: str, limit: int = 30) -> list[str]:
    seen = []
    for token in TOKEN_RE.findall(text.lower()):
        if token in VIETNAMESE_PRONOUNS and token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def build_strict_glossary(source: str, target: str) -> dict[str, Any]:
    fixed_terms = [
        {"source": source_term, "target": target_term}
        for source_term, target_term in FIXED_GLOSSARY.items()
        if source_term in source or target_term.lower() in target.lower()
    ]
    if not fixed_terms:
        fixed_terms = [
            {"source": source_term, "target": target_term}
            for source_term, target_term in FIXED_GLOSSARY.items()
        ]
    return {
        "fixed_terms": fixed_terms,
        "glossary_candidates": {
            "source_terms": _candidate_terms(source),
            "target_terms": _candidate_terms(target),
        },
        "name_candidates": {
            "source_names": _name_candidates(source),
            "target_names": _name_candidates(target),
        },
        "pronoun_candidates": {
            "source_pronouns": _pronoun_candidates(source),
            "target_pronouns": _pronoun_candidates(target),
        },
    }


def limited_style_prompt(
    raw_chapters: list[dict[str, Any]],
    target_chapters: list[dict[str, Any]],
    *,
    max_source_chars: int,
    max_target_chars: int,
) -> tuple[str, dict[str, int]]:
    source = "\n\n".join(chapter["text"] for chapter in raw_chapters)[:max_source_chars]
    target = "\n\n".join(chapter["text"] for chapter in target_chapters)[:max_target_chars]
    prompt = (
        "Summarize translation style briefly for evaluation only.\n"
        f"SOURCE EXCERPT:\n{source}\n\n"
        f"TARGET EXCERPT:\n{target}"
    )
    return prompt, {"source_chars_sent": len(source), "target_chars_sent": len(target)}


def build_style_profile(run_dir: Path, *, chapters: int, max_source_chars: int, max_target_chars: int) -> dict[str, Any]:
    raw_chapters = read_json(run_dir / "extracted_raw_chapters.json")[:chapters]
    target_chapters = read_json(run_dir / "extracted_translated_chapters.json")[:chapters]
    source = "\n\n".join(chapter["text"] for chapter in raw_chapters)[:max_source_chars]
    target = "\n\n".join(chapter["text"] for chapter in target_chapters)[:max_target_chars]
    profile = {
        "schema_version": "eval_style_profile_v1",
        "source_char_count": len(source),
        "target_char_count": len(target),
        "style_summary": "Temporary evaluation profile inferred from limited aligned human translation excerpt.",
        "observations": {
            "target_has_vietnamese_diacritics": bool(VIETNAMESE_MARK_RE.search(target)),
            "avg_target_paragraph_chars": round(
                sum(len(part[2]) for part in _paragraphs(target)) / max(len(_paragraphs(target)), 1),
                2,
            ),
            "paragraph_count": len(_paragraphs(target)),
        },
    }
    glossary = {
        "schema_version": "eval_glossary_candidates_v1",
        **build_strict_glossary(source, target),
        "note": "Deterministic candidates only; not approved canonical memory.",
    }
    write_json(run_dir / "style_profile_test.json", profile)
    write_json(run_dir / "glossary_candidates.json", glossary)
    return {"style_profile": profile, "glossary_candidates": glossary}


def load_dotenv_local(path: Path = Path(".env.local")) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def mask_api_key(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return value[:1] + "***"
    return f"{value[:4]}...{value[-4:]}"


def load_eval_provider(provider_key: str) -> EvalProvider:
    if provider_key == "mock":
        return EvalProvider(
            key="mock",
            type="mock",
            base_url="mock://local",
            api_key_env="MOCK_API_KEY",
            route="chat/completions",
            models=("mock-eval",),
        )
    candidates = [Path("config/providers.yaml"), Path("config/providers.example.yaml")]
    for path in candidates:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        providers = data.get("providers") or {}
        if provider_key not in providers:
            continue
        raw = providers[provider_key]
        models = raw.get("models") or ()
        if isinstance(models, str):
            models = tuple(part.strip() for part in models.split(",") if part.strip())
        elif isinstance(models, list):
            models = tuple(str(part) for part in models)
        else:
            models = tuple()
        provider = EvalProvider(
            key=provider_key,
            type=normalize_provider_type(str(raw.get("type"))),
            base_url=str(raw.get("base_url", "")).rstrip("/"),
            api_key_env=str(raw.get("api_key_env", "")),
            route=str(raw.get("route", "chat/completions")).strip("/"),
            models=models,
        )
        validate_eval_provider(provider)
        return provider
    if provider_key == "ckey_openai_compatible":
        return EvalProvider(
            key=provider_key,
            type="openai_chat_compatible",
            base_url="https://ckey.vn/v1",
            api_key_env="CKEY_API_KEY",
            route="chat/completions",
            models=("gpt-5.5", "gpt-5.4-mini"),
        )
    raise ValueError(f"Eval provider not found: {provider_key}")


def normalize_provider_type(provider_type: str) -> str:
    normalized = provider_type.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {
        "openai_chat_compatible",
        "openai_compatible_chat_completions",
        "openai_compatible_chat/completions",
        "openai_compatible_chat_completions",
    }:
        return "openai_chat_compatible"
    if normalized in {"mock", "mock_provider"}:
        return "mock"
    return normalized


def validate_eval_provider(provider: EvalProvider) -> None:
    if provider.type not in {"mock", "openai_chat_compatible"}:
        raise ValueError(f"Unsupported eval provider type: {provider.type}")
    if provider.type == "openai_chat_compatible":
        if not provider.base_url.startswith("https://"):
            raise ValueError("Real eval provider base_url must use https.")
        if provider.route != "chat/completions":
            raise ValueError("MVP4.5 supports chat/completions route only.")
        if not provider.api_key_env:
            raise ValueError("Eval provider must use api_key_env.")


def _api_key(provider: EvalProvider) -> str:
    key = os.getenv(provider.api_key_env)
    if key:
        return key
    dotenv_values = load_dotenv_local()
    key = dotenv_values.get(provider.api_key_env)
    if key:
        return key
    raise ValueError(f"Missing API key env var: {provider.api_key_env}")


def _mock_rewrite_complete_paragraph(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    sentences = [
        match.group(0).strip()
        for match in re.finditer(r"[^.!?。！？…]+[.!?。！？…]+", stripped)
    ]
    if not sentences:
        return stripped
    kept: list[str] = []
    for sentence in sentences:
        candidate = " ".join(kept + [sentence]).strip()
        if len(candidate) <= max_chars or not kept:
            kept.append(sentence)
            continue
        break
    candidate = " ".join(kept).strip()
    detection = detect_truncated_vietnamese(candidate, strict_max=max_chars)
    return stripped if detection["is_truncated"] else candidate


def _mock_chat_completion(model: str, messages: list[dict[str, str]]) -> str:
    content = messages[-1]["content"]
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("task") in {"translate_paragraphs", "translate_units"}:
        return json_dumps(
            {
                "paragraphs": [
                    {
                        "paragraph_id": paragraph["paragraph_id"],
                        "text": f"[MOCK {model}] {paragraph['source_text'][:80]}",
                    }
                    for paragraph in payload.get("paragraphs", [])
                ]
            }
        )
    if isinstance(payload, dict) and payload.get("task") == "compress_paragraphs":
        compressed = []
        for paragraph in payload.get("paragraphs", []):
            target_max = int(paragraph.get("target_max") or paragraph.get("strict_max") or 80)
            current = str(paragraph.get("current_translation", ""))
            text = _mock_rewrite_complete_paragraph(current, target_max)
            compressed.append(
                {
                    "paragraph_id": paragraph["paragraph_id"],
                    "revised_text": text,
                    "preserved_terms": [
                        item.get("target") for item in paragraph.get("required_terms", [])
                    ],
                    "dropped_details": [],
                    "confidence": 0.95,
                    "notes": "mock safe complete-sentence rewrite",
                }
            )
        return json_dumps({"paragraphs": compressed})
    source = content[:240].replace("\n", " ")
    return f"[MOCK {model}] {source}"


def _chat_completion(
    provider: EvalProvider,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
) -> str:
    if provider.key == "mock":
        return _mock_chat_completion(model, messages)
    key = _api_key(provider)
    payload_data: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2}
    if max_tokens is not None:
        payload_data["max_tokens"] = max_tokens
    payload = json_dumps(payload_data)
    request = urllib.request.Request(
        f"{provider.base_url}/{provider.route}",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "NovelTranslatorStudio-MVP45/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:500]
        raise ValueError(f"Provider HTTP error {exc.code}: {body}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise ValueError(f"Provider request failed: {exc}") from exc
    return data["choices"][0]["message"]["content"]


def mask_provider_error_message(message: str, *, limit: int = 500) -> str:
    text = str(message or "")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{8,}",
        r"\1=***",
        text,
    )
    return text[:limit]


def classify_provider_error(error: Any) -> dict[str, Any]:
    message = mask_provider_error_message(str(error))
    lowered = message.lower()
    status_match = re.search(r"(?:http error|http status|status)\s+(\d{3})", lowered)
    http_status = int(status_match.group(1)) if status_match else None
    non_retry_keywords = [
        "invalid api key",
        "unauthorized",
        "forbidden",
        "invalid model",
        "model_not_found",
        "malformed request",
        "bad request",
        "content policy",
        "policy violation",
        "schema error",
        "configuration error",
    ]
    retry_keywords = [
        "timeout",
        "timed out",
        "connection reset",
        "temporarily unavailable",
        "temporary upstream",
        "upstream error",
        "gateway timeout",
        "service unavailable",
    ]
    if http_status in NON_RETRYABLE_PROVIDER_HTTP_STATUSES or any(
        keyword in lowered for keyword in non_retry_keywords
    ):
        retryable = False
    elif http_status in RETRYABLE_PROVIDER_HTTP_STATUSES:
        retryable = True
    else:
        retryable = any(keyword in lowered for keyword in retry_keywords)

    if http_status is not None:
        provider_error_type = "http_error"
    elif any(keyword in lowered for keyword in retry_keywords):
        provider_error_type = "network_or_timeout"
    elif any(keyword in lowered for keyword in non_retry_keywords):
        provider_error_type = "non_retryable_provider_error"
    else:
        provider_error_type = "provider_error"
    return {
        "provider_error_type": provider_error_type,
        "http_status": http_status,
        "retryable": retryable,
        "error_message_masked": message,
    }


def provider_error_record(
    error: Any,
    *,
    attempt_no: int,
    max_attempts: int,
    retry_scope: str,
    status: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classification = classify_provider_error(error)
    return {
        **classification,
        "attempt_no": attempt_no,
        "max_attempts": max_attempts,
        "retry_scope": retry_scope,
        "status": status,
        "created_at": utc_now(),
        **(context or {}),
    }


def provider_retry_id(context: dict[str, Any] | None) -> str:
    context = context or {}
    parts = [
        str(context.get("validation_index", "")),
        str(context.get("run_retry_no", "")),
        str(context.get("sample_id", "")),
        str(context.get("model", "")),
        str(context.get("phase", "")),
        str(context.get("batch_index", "")),
        str(context.get("paragraph_id", "")),
    ]
    return ":".join(parts)


def chat_completion_with_provider_retry(
    provider: EvalProvider,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    retry_attempts: int = 1,
    retry_backoff_seconds: float = 0.0,
    retry_log_entries: list[dict[str, Any]] | None = None,
    retry_context: dict[str, Any] | None = None,
) -> str:
    max_attempts = max(1, int(retry_attempts or 1))
    context = dict(retry_context or {})
    context.setdefault("model", model)
    context.setdefault("retry_id", provider_retry_id(context))
    last_error: ValueError | None = None
    for attempt_no in range(1, max_attempts + 1):
        try:
            raw = _chat_completion(
                provider,
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
        except ValueError as exc:
            last_error = exc
            classification = classify_provider_error(exc)
            final_attempt = attempt_no >= max_attempts
            status = (
                "non_retryable_failure"
                if not classification["retryable"]
                else "exhausted"
                if final_attempt
                else "failed_attempt"
            )
            if retry_log_entries is not None:
                retry_log_entries.append(
                    provider_error_record(
                        exc,
                        attempt_no=attempt_no,
                        max_attempts=max_attempts,
                        retry_scope="sample",
                        status=status,
                        context=context,
                    )
                )
            if not classification["retryable"] or final_attempt:
                raise
            if retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds * (2 ** (attempt_no - 1)))
            continue
        if attempt_no > 1 and retry_log_entries is not None:
            retry_log_entries.append(
                {
                    "provider_error_type": None,
                    "http_status": None,
                    "retryable": True,
                    "attempt_no": attempt_no,
                    "max_attempts": max_attempts,
                    "retry_scope": "sample",
                    "status": "succeeded_after_retry",
                    "error_message_masked": None,
                    "created_at": utc_now(),
                    **context,
                }
            )
        return raw
    if last_error is not None:
        raise last_error
    raise ValueError("Provider retry failed without an exception.")


def summarize_provider_retry_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        retry_id = str(entry.get("retry_id") or len(grouped))
        grouped.setdefault(retry_id, []).append(entry)
    sample_retries_attempted = 0
    sample_retries_succeeded = 0
    sample_retries_exhausted = 0
    run_retries_attempted = 0
    final_provider_failure_count = 0
    for group in grouped.values():
        scope = group[-1].get("retry_scope", "sample")
        if scope == "run":
            run_retries_attempted += sum(1 for item in group if item.get("status") == "run_retry_attempted")
            continue
        attempts = [
            int(item.get("attempt_no") or 1)
            for item in group
            if item.get("attempt_no") is not None
        ]
        max_seen_attempt = max(attempts) if attempts else 1
        sample_retries_attempted += max(0, max_seen_attempt - 1)
        final_status = group[-1].get("status")
        if final_status == "succeeded_after_retry":
            sample_retries_succeeded += 1
        elif final_status == "exhausted":
            sample_retries_exhausted += 1
            final_provider_failure_count += 1
        elif final_status == "non_retryable_failure":
            final_provider_failure_count += 1
    return {
        "retryable_failures": sum(
            1
            for entry in entries
            if entry.get("retryable") is True
            and entry.get("status") in {"failed_attempt", "exhausted"}
        ),
        "non_retryable_failures": sum(
            1 for entry in entries if entry.get("status") == "non_retryable_failure"
        ),
        "sample_retries_attempted": sample_retries_attempted,
        "sample_retries_succeeded": sample_retries_succeeded,
        "sample_retries_exhausted": sample_retries_exhausted,
        "run_retries_attempted": run_retries_attempted,
        "final_provider_failure_count": final_provider_failure_count,
        "entry_count": len(entries),
    }


def learn_style(
    *,
    project: str,
    chapters: int,
    provider_key: str,
    model: str,
    max_source_chars: int,
    max_target_chars: int,
) -> dict[str, Any]:
    run_dir = latest_run_dir(project)
    provider = load_eval_provider(provider_key)
    result = build_style_profile(
        run_dir,
        chapters=chapters,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    prompt, prompt_limits = limited_style_prompt(
        read_json(run_dir / "extracted_raw_chapters.json")[:chapters],
        read_json(run_dir / "extracted_translated_chapters.json")[:chapters],
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    result["prompt_limits"] = prompt_limits
    if provider.key != "mock":
        model_summary = _chat_completion(
            provider,
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise literary translation style analyst."},
                {"role": "user", "content": prompt},
            ],
        )
        profile = read_json(run_dir / "style_profile_test.json")
        profile["model_style_summary"] = model_summary
        profile["provider"] = provider.key
        profile["model"] = model
        write_json(run_dir / "style_profile_test.json", profile)
        result["style_profile"] = profile
    result["run_dir"] = str(run_dir)
    return result


def paragraph_translation_user_prompt(
    sample: dict[str, Any],
    *,
    paragraph_pairs: list[dict[str, Any]] | None = None,
) -> str:
    pairs = paragraph_pairs if paragraph_pairs is not None else active_eval_pairs(sample)
    unit_mode = bool(sample.get("use_translation_units") and sample.get("translation_units"))
    paragraphs = [
        {
            "paragraph_id": pair["paragraph_id"],
            "source_paragraph_ids": pair.get("source_paragraph_ids", [pair["paragraph_id"]]),
            "unit_type": pair.get("unit_type", _pair_unit_type(pair)),
            "merge_reason": pair.get("merge_reason"),
            "source_text": pair["source_text"],
            "target_min": pair["target_min"],
            "target_max": pair["target_max"],
            "strict_max": pair["strict_max"],
            "required_terms": pair.get("required_terms", []),
        }
        for pair in pairs
    ]
    return json_dumps(
        {
            "task": "translate_units" if unit_mode else "translate_paragraphs",
            "sample_id": sample["sample_id"],
            "instructions": (
                "Translate each source unit into Vietnamese. Return exactly one object per "
                "paragraph_id, in the same order. Preserve source paragraph order inside merged "
                "units, keep complete Vietnamese sentences, preserve system panels, names, terms, "
                "and numbers, and stay at or below target_max when safe."
                if unit_mode
                else "Translate each source paragraph into Vietnamese. Return exactly one object per "
                "paragraph_id, in the same order. Keep each paragraph at or below target_max."
            ),
            "paragraphs": paragraphs,
            "original_paragraph_count_relaxed": unit_mode,
            "output_schema": {
                "paragraphs": [
                    {"paragraph_id": paragraph["paragraph_id"], "text": "Vietnamese translation"}
                    for paragraph in paragraphs
                ]
            },
        }
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(stripped[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def parse_paragraph_translation_output(raw_output: str) -> list[dict[str, str]]:
    data = _extract_json_object(raw_output)
    if not data:
        return []
    paragraphs = data.get("paragraphs")
    if not isinstance(paragraphs, list):
        return []
    parsed = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        paragraph_id = str(paragraph.get("paragraph_id", "")).strip()
        raw_text = paragraph.get("text", paragraph.get("revised_text", ""))
        text = re.sub(r"\s*\n+\s*", " ", str(raw_text).strip())
        text = re.sub(r"[ \t]+", " ", text).strip()
        if paragraph_id:
            parsed.append({"paragraph_id": paragraph_id, "text": text})
    return parsed


def parse_compression_output(raw_output: str) -> list[dict[str, Any]]:
    data = _extract_json_object(raw_output)
    if not data:
        return []
    paragraphs = data.get("paragraphs")
    if not isinstance(paragraphs, list):
        return []
    parsed = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        paragraph_id = str(paragraph.get("paragraph_id", "")).strip()
        text = re.sub(
            r"\s*\n+\s*",
            " ",
            str(paragraph.get("revised_text", paragraph.get("text", ""))).strip(),
        )
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not paragraph_id:
            continue
        confidence = paragraph.get("confidence", 1.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        parsed.append(
            {
                "paragraph_id": paragraph_id,
                "text": text,
                "preserved_terms": paragraph.get("preserved_terms", []),
                "dropped_details": paragraph.get("dropped_details", []),
                "confidence": confidence_value,
                "notes": paragraph.get("notes", ""),
            }
        )
    return parsed


def expected_paragraph_ids(sample: dict[str, Any]) -> list[str]:
    return [pair["paragraph_id"] for pair in active_eval_pairs(sample)]


def paragraph_json_retry_prompt(
    *,
    sample: dict[str, Any],
    paragraph_pairs: list[dict[str, Any]],
    raw_output: str,
) -> str:
    base = json.loads(paragraph_translation_user_prompt(sample, paragraph_pairs=paragraph_pairs))
    base["instructions"] = (
        "Your previous response was invalid JSON or missed paragraph_id values. "
        "Return JSON only. Include exactly the requested paragraph_id values, no markdown, "
        "no explanations, and no extra keys outside the schema."
    )
    base["previous_response_excerpt"] = _snippet(raw_output, 1000)
    return json_dumps(base)


def best_effort_paragraph_output(raw_output: str, sample: dict[str, Any]) -> list[dict[str, str]]:
    ids = expected_paragraph_ids(sample)
    parts = [part[2] for part in _paragraphs(raw_output)]
    if len(parts) != len(ids):
        return [{"paragraph_id": paragraph_id, "text": ""} for paragraph_id in ids]
    return [
        {"paragraph_id": paragraph_id, "text": parts[index].strip() if index < len(parts) else ""}
        for index, paragraph_id in enumerate(ids)
    ]


def validate_paragraph_translation(
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
) -> dict[str, Any]:
    expected_ids = expected_paragraph_ids(sample)
    actual_ids = [paragraph.get("paragraph_id") for paragraph in paragraphs]
    missing_ids = [paragraph_id for paragraph_id in expected_ids if paragraph_id not in actual_ids]
    extra_ids = [paragraph_id for paragraph_id in actual_ids if paragraph_id not in expected_ids]
    order_preserved = actual_ids == expected_ids
    rendered_count = paragraph_count(render_paragraph_translation(sample, paragraphs))
    errors = []
    if missing_ids:
        errors.append("missing_paragraph_id")
    if extra_ids:
        errors.append("extra_paragraph_id")
    if not order_preserved:
        errors.append("paragraph_order_changed")
    if rendered_count != len(expected_ids):
        errors.append("rendered_paragraph_count_mismatch")
    return {
        "valid": not errors,
        "expected_ids": expected_ids,
        "actual_ids": actual_ids,
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "order_preserved": order_preserved,
        "rendered_paragraph_count": rendered_count,
        "expected_paragraph_count": len(expected_ids),
        "errors": errors,
    }


def render_paragraph_translation(sample: dict[str, Any], paragraphs: list[dict[str, str]]) -> str:
    lookup = {paragraph.get("paragraph_id"): paragraph.get("text", "").strip() for paragraph in paragraphs}
    return "\n\n".join(lookup.get(paragraph_id, "") for paragraph_id in expected_paragraph_ids(sample)).strip()


def per_paragraph_length_table(
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    lookup = {paragraph.get("paragraph_id"): paragraph.get("text", "") for paragraph in paragraphs}
    rows = []
    for pair in active_eval_pairs(sample):
        output_text = lookup.get(pair["paragraph_id"], "")
        output_count = len(output_text)
        truncation = detect_truncated_vietnamese(
            output_text,
            source_text=pair.get("source_text"),
            strict_max=pair.get("strict_max"),
        )
        rows.append(
            {
                "paragraph_id": pair["paragraph_id"],
                "source_paragraph_ids": pair.get("source_paragraph_ids", [pair["paragraph_id"]]),
                "target_paragraph_ids": pair.get("target_paragraph_ids", [pair["paragraph_id"]]),
                "unit_type": pair.get("unit_type"),
                "merge_reason": pair.get("merge_reason"),
                "is_merged_unit": pair.get("is_merged_unit", False),
                "source_char_count": pair["source_char_count"],
                "reference_char_count": pair["target_char_count"],
                "target_min": pair["target_min"],
                "target_max": pair["target_max"],
                "strict_max": pair["strict_max"],
                "strict_max_ratio": pair.get("strict_max_ratio"),
                "budget_policy_used": pair.get("budget_policy_used"),
                "output_char_count": output_count,
                "output_reference_ratio": round(output_count / max(pair["target_char_count"], 1), 3),
                "over_strict_max": output_count > pair["strict_max"],
                "truncation_detected": truncation["is_truncated"],
                "truncation_reasons": truncation["reasons"],
            }
        )
    return rows


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def max_tokens_for_paragraph_pairs(pairs: list[dict[str, Any]]) -> int:
    strict_total = sum(pair.get("strict_max", 0) for pair in pairs)
    return max(260, int(strict_total / 3.2) + 160)


def required_terms_for_pair(pair: dict[str, Any], glossary: dict[str, Any]) -> list[dict[str, Any]]:
    source_text = pair.get("source_text", "")
    required: list[dict[str, Any]] = []
    for term in (glossary or {}).get("fixed_terms", []):
        source_term = str(term.get("source", ""))
        target_term = str(term.get("target", ""))
        if source_term and target_term and source_term in source_text:
            aliases = [target_term]
            for anchor_id, alias_map in ALIGNMENT_ANCHORS.items():
                if source_term in alias_map.get("zh", []):
                    aliases.extend(alias_map.get("vi", []))
            required.append(
                {
                    "kind": "term",
                    "source": source_term,
                    "target": target_term,
                    "aliases": sorted(set(alias.lower() for alias in aliases if alias)),
                }
            )
    for anchor_id in extract_alignment_anchors(source_text, lang="zh"):
        aliases = ALIGNMENT_ANCHORS.get(anchor_id, {}).get("vi", [])
        if anchor_id.startswith("system_"):
            aliases = aliases + ALIGNMENT_ANCHORS.get(anchor_id, {}).get("zh", [])
        if aliases and not any(item.get("anchor_id") == anchor_id for item in required):
            required.append(
                {
                    "kind": "anchor",
                    "anchor_id": anchor_id,
                    "source": anchor_id,
                    "target": aliases[0],
                    "aliases": sorted(set(alias.lower() for alias in aliases if alias)),
                }
            )
    for number in sorted(set(re.findall(r"\d+(?:/\d+)?", source_text))):
        required.append(
            {
                "kind": "number",
                "source": number,
                "target": number,
                "aliases": [number],
            }
        )
    return required


def required_terms_missing(
    pair: dict[str, Any],
    text: str,
    glossary: dict[str, Any],
) -> list[dict[str, Any]]:
    lowered = text.lower()
    missing = []
    for required in required_terms_for_pair(pair, glossary):
        aliases = required.get("aliases", [])
        if not any(alias and alias in lowered for alias in aliases):
            missing.append(required)
    return missing


def sentence_count_for_style(text: str) -> int:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return 0
    parts = [part for part in re.split(r"[。.!?！？…]+", normalized) if part.strip()]
    return max(1, len(parts))


def connective_count(text: str) -> int:
    lowered = (text or "").lower()
    return sum(len(re.findall(rf"\b{re.escape(term)}\b", lowered)) for term in STYLE_CONNECTIVE_TERMS)


def style_drift_checks(
    sample: dict[str, Any],
    before_paragraphs: list[dict[str, str]],
    after_paragraphs: list[dict[str, str]],
) -> dict[str, Any]:
    before = render_paragraph_translation(sample, before_paragraphs)
    after = render_paragraph_translation(sample, after_paragraphs)
    before_sentence_count = sentence_count_for_style(before)
    after_sentence_count = sentence_count_for_style(after)
    before_paragraph_count = paragraph_count(before)
    after_paragraph_count = paragraph_count(after)
    before_connectives = connective_count(before)
    after_connectives = connective_count(after)
    before_system_panels = before.count("【")
    after_system_panels = after.count("【")
    before_dialogue_marks = len(re.findall(r"[\"“”‘’]", before))
    after_dialogue_marks = len(re.findall(r"[\"“”‘’]", after))
    before_tokens = set(_tokens(before))
    after_tokens = set(_tokens(after))
    token_retention = (
        len(before_tokens & after_tokens) / max(len(before_tokens), 1)
        if before_tokens
        else 1.0
    )
    char_ratio = len(after) / max(len(before), 1)
    score = 0
    warnings: list[str] = []
    if before_sentence_count >= 3 and after_sentence_count <= before_sentence_count - 2:
        score += 25
        warnings.append("sentence_count_reduced")
    if before_sentence_count >= 2 and after_sentence_count == 1:
        score += 25
        warnings.append("action_beat_merge_detected")
    if before_paragraph_count and abs(before_paragraph_count - after_paragraph_count) >= max(
        1,
        int(before_paragraph_count * 0.4),
    ):
        score += 20
        warnings.append("paragraph_rhythm_changed")
    if before_system_panels > after_system_panels:
        score += 30
        warnings.append("system_panel_removed_or_merged")
    if before_dialogue_marks > after_dialogue_marks:
        score += 15
        warnings.append("dialogue_marker_loss")
    connective_delta = after_connectives - before_connectives
    if connective_delta >= 4:
        score += 30
        warnings.append("excessive_connective_rewriting")
    elif connective_delta >= 2:
        score += 20
        warnings.append("connective_rewriting")
    if token_retention < 0.62 and len(before) > 60:
        score += 25
        warnings.append("semantic_compression_loss")
    elif token_retention < 0.75 and len(before) > 60:
        score += 12
        warnings.append("semantic_compression_loss_possible")
    if char_ratio < 0.58 and len(before) > 80:
        score += 20
        warnings.append("aggressive_length_reduction")
    elif char_ratio < 0.75 and len(before) > 80:
        score += 8
        warnings.append("noticeable_length_reduction")
    score = min(score, 100)
    return {
        "score": score,
        "warnings": sorted(set(warnings)),
        "above_threshold": score >= STYLE_DRIFT_WARNING_THRESHOLD,
        "human_review_recommended": score >= STYLE_DRIFT_REVIEW_THRESHOLD,
        "threshold": STYLE_DRIFT_WARNING_THRESHOLD,
        "review_threshold": STYLE_DRIFT_REVIEW_THRESHOLD,
        "metrics": {
            "before_sentence_count": before_sentence_count,
            "after_sentence_count": after_sentence_count,
            "before_paragraph_count": before_paragraph_count,
            "after_paragraph_count": after_paragraph_count,
            "before_connective_count": before_connectives,
            "after_connective_count": after_connectives,
            "connective_delta": connective_delta,
            "before_system_panel_count": before_system_panels,
            "after_system_panel_count": after_system_panels,
            "before_dialogue_marker_count": before_dialogue_marks,
            "after_dialogue_marker_count": after_dialogue_marks,
            "token_retention": round(token_retention, 3),
            "after_before_char_ratio": round(char_ratio, 3),
        },
    }


def final_output_selector(
    *,
    sample: dict[str, Any],
    before_paragraphs: list[dict[str, str]],
    after_paragraphs: list[dict[str, str]],
    before_verification: dict[str, Any],
    after_verification: dict[str, Any],
) -> dict[str, Any]:
    drift = style_drift_checks(sample, before_paragraphs, after_paragraphs)
    before_passes = bool(before_verification.get("pass"))
    after_passes = bool(after_verification.get("pass"))
    selected = "before_compression" if before_passes else "after_compression"
    selected_paragraphs = before_paragraphs if before_passes else after_paragraphs
    selected_verification = dict(before_verification if before_passes else after_verification)
    selected_reason = (
        "before_passes_safety_terms_ratio_and_truncation_gates"
        if before_passes
        else "after_selected_because_before_failed_gates"
    )
    if not before_passes and after_passes and drift["above_threshold"]:
        selected_verification = {
            **selected_verification,
            "pass": False,
            "reasons": sorted(
                set(
                    selected_verification.get("reasons", [])
                    + ["style_drift_above_threshold"]
                )
            ),
            "style_drift": drift,
        }
        selected_reason = "after_failed_style_drift_gate"
    elif before_passes:
        selected_verification["style_drift"] = {
            **drift,
            "ignored_for_gate": True,
            "reason": "before_output_selected",
        }
    else:
        selected_verification["style_drift"] = drift
    return {
        "selected_final_output": selected,
        "selected_paragraphs": selected_paragraphs,
        "selected_verification": selected_verification,
        "selection_reason": selected_reason,
        "before_pass": before_passes,
        "after_pass": after_passes,
        "style_drift": drift,
        "human_review_recommended": bool(
            selected == "after_compression" and drift["human_review_recommended"]
        ),
    }


def compression_attempt_prompt(
    *,
    pair: dict[str, Any],
    current_translation: str,
    glossary: dict[str, Any],
    attempt: int,
    previous_failure_reasons: list[str] | None = None,
) -> str:
    required = required_terms_for_pair(pair, glossary)
    instructions = (
        "Rewrite-compress this Vietnamese paragraph. Preserve all source meaning, names, "
        "terms, numbers, and system panels. Do not hard truncate. Do not add translator notes. "
        "Do not start with glossary labels unless the source starts with that label. Return one "
        "complete Vietnamese paragraph. Make the smallest edit that fixes the length issue. "
        "Preserve sentence order, action beats, dialogue turns, short exclamations, and webnovel "
        "rhythm. Do not merge multiple short actions into one explanatory sentence when a concise "
        "multi-sentence form fits."
    )
    if attempt == 2:
        instructions += (
            " This is the final retry. Explicitly check every required term/number and return "
            "only valid JSON. If you cannot compress safely, keep meaning complete and exceed "
            "the budget rather than truncating."
        )
    return json_dumps(
        {
            "task": "compress_paragraphs",
            "attempt": attempt,
            "instructions": instructions,
            "paragraphs": [
                {
                    "paragraph_id": pair["paragraph_id"],
                    "source_text": pair["source_text"],
                    "current_translation": current_translation,
                    "target_max": pair["target_max"],
                    "strict_max": pair["strict_max"],
                    "budget_policy_used": pair.get("budget_policy_used"),
                    "required_terms": required,
                    "previous_failure_reasons": previous_failure_reasons or [],
                }
            ],
            "output_schema": {
                "paragraphs": [
                    {
                        "paragraph_id": pair["paragraph_id"],
                        "revised_text": "complete Vietnamese paragraph",
                        "preserved_terms": ["terms/numbers kept"],
                        "dropped_details": [],
                        "confidence": 0.95,
                        "notes": "brief safety note",
                    }
                ]
            },
        }
    )


def validate_compression_candidate(
    *,
    pair: dict[str, Any],
    before: str,
    candidate: dict[str, Any],
    glossary: dict[str, Any],
) -> dict[str, Any]:
    text = candidate.get("text", "")
    truncation = detect_truncated_vietnamese(
        text,
        source_text=pair.get("source_text"),
        strict_max=pair.get("strict_max"),
    )
    missing_terms = required_terms_missing(pair, text, glossary)
    dropped_details = candidate.get("dropped_details", [])
    confidence = float(candidate.get("confidence", 0.0) or 0.0)
    reasons = []
    if truncation["is_truncated"]:
        reasons.append("sentence_completeness_failed")
    if missing_terms:
        reasons.append("required_terms_missing")
    if dropped_details:
        reasons.append("dropped_details_reported")
    if confidence < 0.75:
        reasons.append("low_compression_confidence")
    if not text:
        reasons.append("empty_compression_output")
    if _starts_with_injected_glossary_label(text):
        reasons.append("glossary_label_prefix_injection")
    over_strict = len(text) > pair["strict_max"]
    paragraph_ratio = len(text) / max(pair.get("target_char_count", 1), 1)
    if over_strict and paragraph_ratio > PARAGRAPH_ALLOWED_OVER_BUDGET_RATIO:
        reasons.append("paragraph_exceeds_relaxed_budget")
    return {
        "valid": not reasons,
        "reasons": sorted(set(reasons)),
        "text": text,
        "truncation": truncation,
        "missing_terms": missing_terms,
        "dropped_details": dropped_details,
        "confidence": confidence,
        "over_strict": over_strict,
        "paragraph_ratio": round(paragraph_ratio, 3),
        "budget_policy_used": pair.get("budget_policy_used"),
        "before_char_count": len(before),
        "after_char_count": len(text),
    }


def enforce_fixed_terms_in_paragraphs(
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
    *,
    glossary: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    terms = (glossary or {}).get("fixed_terms", [])
    if not terms:
        return paragraphs, []
    pair_lookup = {pair["paragraph_id"]: pair for pair in active_eval_pairs(sample)}
    repaired = []
    repairs = []
    for paragraph in paragraphs:
        paragraph_id = paragraph["paragraph_id"]
        pair = pair_lookup.get(paragraph_id, {})
        source_text = pair.get("source_text", "")
        text = paragraph["text"]
        applied_terms = []
        for term in terms:
            source_term = str(term.get("source", ""))
            target_term = str(term.get("target", ""))
            if (
                source_term
                and target_term
                and source_term in source_text
                and target_term.lower() not in text.lower()
            ):
                applied_terms.append({"source": source_term, "target": target_term})
        if applied_terms:
            repairs.append(
                {
                    "paragraph_id": paragraph_id,
                    "terms": applied_terms,
                    "before_clip_char_count": len(text),
                    "after_char_count": len(text),
                    "strict_max": int(pair.get("strict_max") or len(text)),
                    "skipped": True,
                    "reason": "unsafe_prefix_term_repair_disabled",
                }
            )
        repaired.append({"paragraph_id": paragraph_id, "text": text})
    return repaired, repairs


def enforce_global_length_floor(
    sample: dict[str, Any],
    before_paragraphs: list[dict[str, str]],
    final_paragraphs: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    current = render_paragraph_translation(sample, final_paragraphs)
    floor_chars = int(sample["target_char_count"] * EVAL_LENGTH_RATIO_MIN)
    if len(current) >= floor_chars:
        return final_paragraphs, []
    pair_lookup = {pair["paragraph_id"]: pair for pair in active_eval_pairs(sample)}
    before_lookup = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in before_paragraphs}
    updated = [dict(paragraph) for paragraph in final_paragraphs]
    repairs = []
    for paragraph in updated:
        paragraph_id = paragraph["paragraph_id"]
        pair = pair_lookup.get(paragraph_id)
        before = before_lookup.get(paragraph_id, "")
        if not pair or len(before) <= len(paragraph["text"]):
            continue
        if len(before) > pair["strict_max"]:
            continue
        candidate_detection = detect_truncated_vietnamese(
            before,
            source_text=pair.get("source_text"),
            strict_max=pair.get("strict_max"),
        )
        if candidate_detection["is_truncated"]:
            continue
        before_count = len(paragraph["text"])
        paragraph["text"] = before
        repairs.append(
            {
                "paragraph_id": paragraph_id,
                "before_char_count": before_count,
                "after_char_count": len(before),
                "strict_max": pair["strict_max"],
                "reason": "restore_global_length_floor",
                "deterministic_clip_applied": False,
            }
        )
        if len(render_paragraph_translation(sample, updated)) >= floor_chars:
            break
    return updated, repairs


def verify_paragraph_output(
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
    *,
    glossary: dict[str, Any],
) -> dict[str, Any]:
    validation = validate_paragraph_translation(sample, paragraphs)
    rendered = render_paragraph_translation(sample, paragraphs)
    ratio = len(rendered) / max(sample["target_char_count"], 1)
    table = per_paragraph_length_table(sample, paragraphs)
    overlong = [row["paragraph_id"] for row in table if row["over_strict_max"]]
    truncated = [
        {
            "paragraph_id": row["paragraph_id"],
            "reasons": row["truncation_reasons"],
        }
        for row in table
        if row["truncation_detected"]
    ]
    terminology_mismatches = terminology_mismatches_for(
        sample["source_text"],
        rendered,
        glossary,
    )
    reasons = list(validation["errors"])
    if not (EVAL_LENGTH_RATIO_MIN <= ratio <= EVAL_LENGTH_RATIO_MAX):
        reasons.append("global_ratio_outside_range")
    safe_over_budget_rows = [
        row
        for row in table
        if row["over_strict_max"]
        and row["output_reference_ratio"] <= PARAGRAPH_ALLOWED_OVER_BUDGET_RATIO
    ]
    unsafe_over_budget = [
        row["paragraph_id"]
        for row in table
        if row["over_strict_max"]
        and row["output_reference_ratio"] > PARAGRAPH_ALLOWED_OVER_BUDGET_RATIO
    ]
    over_budget_allowed = (
        bool(overlong)
        and not unsafe_over_budget
        and ratio <= EVAL_LENGTH_RATIO_MAX
        and not truncated
        and not terminology_mismatches
    )
    if overlong and not over_budget_allowed:
        reasons.append("paragraph_exceeds_strict_max")
    if truncated:
        reasons.append("paragraph_truncation_detected")
    if terminology_mismatches:
        reasons.append("terminology_mismatch")
    if sample.get("accepted_for_stable_validation") is False:
        reasons.append("alignment_quality_below_threshold")
    low_alignment_units = [
        pair["paragraph_id"]
        for pair in active_eval_pairs(sample)
        if pair.get("alignment_quality", sample.get("alignment_quality", 1.0))
        < ALIGNMENT_QUALITY_THRESHOLD
    ]
    if low_alignment_units:
        reasons.append("unit_alignment_quality_below_threshold")
    return {
        "pass": not reasons,
        "reasons": reasons,
        "paragraph_validation": validation,
        "global_ratio": round(ratio, 3),
        "overlong_paragraph_ids": unsafe_over_budget if over_budget_allowed else overlong,
        "allowed_over_budget_paragraphs": [
            {
                "paragraph_id": row["paragraph_id"],
                "output_reference_ratio": row["output_reference_ratio"],
                "reason": "global_ratio_ok_and_paragraph_ratio_within_relaxed_limit",
            }
            for row in safe_over_budget_rows
        ]
        if over_budget_allowed
        else [],
        "truncated_paragraphs": truncated,
        "terminology_mismatches": terminology_mismatches,
        "per_paragraph_length_table": table,
        "alignment_quality": sample.get("alignment_quality"),
        "alignment_warnings": sample.get("alignment_warnings", []),
        "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
        "low_alignment_units": low_alignment_units,
        "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
        "original_paragraph_count_relaxed": bool(
            sample.get("use_translation_units") and sample.get("translation_unit_merge_count", 0)
        ),
    }


def compression_user_prompt(
    *,
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
    glossary: dict[str, Any],
    paragraph_ids: list[str] | None = None,
) -> str:
    lookup = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in paragraphs}
    allowed = set(paragraph_ids) if paragraph_ids is not None else None
    offenders = []
    for pair in sample.get("paragraph_pairs", []):
        if allowed is not None and pair["paragraph_id"] not in allowed:
            continue
        current = lookup.get(pair["paragraph_id"], "")
        if len(current) > pair["strict_max"]:
            offenders.append(
                {
                    "paragraph_id": pair["paragraph_id"],
                    "source_text": pair["source_text"],
                    "current_translation": current,
                    "target_max": pair["target_max"],
                    "strict_max": pair["strict_max"],
                    "requirements": [
                        "Preserve all source meaning, names, terms, numbers, and system panels.",
                        "Return one complete Vietnamese paragraph.",
                        "Do not cut words or phrases.",
                        "Do not add glossary labels at the beginning unless the source starts with that label.",
                        "No dangling brackets or unfinished sentences.",
                    ],
                }
            )
    return json_dumps(
        {
            "task": "compress_paragraphs",
            "instructions": (
                "Rewrite-compress without losing meaning. Preserve names, fixed terms, numbers, "
                "system panels, and complete Vietnamese sentences. Do not hard truncate. Do not add "
                "new details, translator notes, or glossary labels at the beginning. Return only "
                "revised paragraphs in JSON."
            ),
            "fixed_glossary": glossary.get("fixed_terms", []),
            "paragraphs": offenders,
            "output_schema": {
                "paragraphs": [
                    {
                        "paragraph_id": paragraph["paragraph_id"],
                        "revised_text": "complete Vietnamese paragraph",
                        "preserved_terms": [],
                        "dropped_details": [],
                        "confidence": 0.95,
                        "notes": "brief safety note",
                    }
                    for paragraph in offenders
                ]
            },
        }
    )


def compress_offending_paragraphs(
    provider: EvalProvider,
    *,
    model: str,
    provider_call: Any | None = None,
    sample: dict[str, Any],
    paragraphs: list[dict[str, str]],
    glossary: dict[str, Any],
    provider_retry_attempts: int = 1,
    provider_retry_backoff_seconds: float = 0.0,
    provider_retry_log_entries: list[dict[str, Any]] | None = None,
    provider_retry_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    before_table = per_paragraph_length_table(sample, paragraphs)
    offending_ids = [row["paragraph_id"] for row in before_table if row["over_strict_max"]]
    if not offending_ids:
        return paragraphs, {
            "triggered": False,
            "offending_paragraph_ids": [],
            "entries": [],
        }
    raw_chunks = []
    provider_errors = []
    pair_lookup = {pair["paragraph_id"]: pair for pair in active_eval_pairs(sample)}
    paragraph_lookup = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in paragraphs}
    revised_lookup: dict[str, str] = {}
    entry_lookup: dict[str, dict[str, Any]] = {}
    for paragraph_id in offending_ids:
        pair = pair_lookup[paragraph_id]
        before = paragraph_lookup.get(paragraph_id, "")
        attempts = []
        accepted: dict[str, Any] | None = None
        failure_reasons: list[str] = []
        for attempt in (1, 2):
            prompt = compression_attempt_prompt(
                pair=pair,
                current_translation=before if attempt == 1 else attempts[-1].get("text", before),
                glossary=glossary,
                attempt=attempt,
                previous_failure_reasons=failure_reasons,
            )
            try:
                call = provider_call or (lambda **kwargs: chat_completion_with_provider_retry(provider, **kwargs))
                raw = call(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You safely rewrite-compress one Vietnamese paragraph. Return JSON only. "
                                "Never truncate. Preserve required terms, names, numbers, and system panels. "
                                "Use complete Vietnamese sentences and closed brackets. Make minimal edits, "
                                "preserve sentence order, action beats, dialogue turns, and webnovel rhythm."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens_for_paragraph_pairs([pair]),
                    retry_attempts=provider_retry_attempts,
                    retry_backoff_seconds=provider_retry_backoff_seconds,
                    retry_log_entries=provider_retry_log_entries,
                    retry_context={
                        **(provider_retry_context or {}),
                        "phase": "compression",
                        "paragraph_id": paragraph_id,
                        "compression_attempt": attempt,
                    },
                ).strip()
            except ValueError as exc:
                classification = classify_provider_error(exc)
                error = {
                    "attempt": attempt,
                    "paragraph_id": paragraph_id,
                    "error": str(exc),
                    "classification": classification,
                }
                provider_errors.append(error)
                attempts.append(
                    {
                        "attempt": attempt,
                        "valid": False,
                        "reasons": ["provider_error"],
                        "error": str(exc),
                        "provider_error_classification": classification,
                    }
                )
                failure_reasons = ["provider_error"]
                continue
            raw_chunks.append(
                json_dumps(
                    {
                        "paragraph_id": paragraph_id,
                        "attempt": attempt,
                        "raw_response": raw,
                    }
                )
            )
            parsed = parse_compression_output(raw)
            candidate = next(
                (item for item in parsed if item["paragraph_id"] == paragraph_id),
                None,
            )
            if candidate is None:
                validation = {
                    "valid": False,
                    "reasons": ["provider_json_failure"],
                    "text": "",
                    "truncation": {"is_truncated": True, "reasons": ["empty_output"]},
                    "missing_terms": required_terms_for_pair(pair, glossary),
                    "dropped_details": [],
                    "confidence": 0.0,
                    "over_strict": False,
                    "paragraph_ratio": 0,
                    "budget_policy_used": pair.get("budget_policy_used"),
                    "before_char_count": len(before),
                    "after_char_count": 0,
                }
            else:
                validation = validate_compression_candidate(
                    pair=pair,
                    before=before,
                    candidate=candidate,
                    glossary=glossary,
                )
            attempts.append({"attempt": attempt, **validation})
            failure_reasons = validation["reasons"]
            if validation["valid"]:
                accepted = validation
                break
        if accepted is not None:
            revised_lookup[paragraph_id] = accepted["text"]
            final_validation = accepted
            compression_failure_reason = None
        else:
            revised_lookup[paragraph_id] = attempts[-1].get("text") or before
            final_validation = attempts[-1]
            compression_failure_reason = ",".join(final_validation.get("reasons", []))
        entry_lookup[paragraph_id] = {
            "paragraph_id": paragraph_id,
            "before_char_count": len(before),
            "model_after_char_count": len(revised_lookup[paragraph_id]),
            "after_char_count": len(revised_lookup[paragraph_id]),
            "target_max": pair["target_max"],
            "strict_max": pair["strict_max"],
            "budget_policy_used": pair.get("budget_policy_used"),
            "still_over_strict_max": len(revised_lookup[paragraph_id]) > pair["strict_max"],
            "deterministic_clip_applied": False,
            "deterministic_min_restore_applied": False,
            "compression_attempts": attempts,
            "compression_attempt_count": len(attempts),
            "compression_failure_reason": compression_failure_reason,
            "provider_json_failures": sum(
                1 for attempt in attempts if "provider_json_failure" in attempt.get("reasons", [])
            ),
            "required_terms_missing": final_validation.get("missing_terms", []),
            "sentence_completeness_status": "fail"
            if final_validation.get("truncation", {}).get("is_truncated")
            else "pass",
            "unsafe_compression": accepted is None,
            "truncation_detected": final_validation.get("truncation", {}).get("is_truncated", False),
            "truncation_reasons": final_validation.get("truncation", {}).get("reasons", []),
            "before_text": before,
            "model_after_text": revised_lookup[paragraph_id],
            "after_text": revised_lookup[paragraph_id],
        }
    updated = []
    entries = []
    source_lookup = {paragraph["paragraph_id"]: paragraph["text"] for paragraph in paragraphs}
    for paragraph in paragraphs:
        paragraph_id = paragraph["paragraph_id"]
        before = paragraph["text"]
        after = revised_lookup.get(paragraph_id, before)
        if paragraph_id in offending_ids:
            entries.append(entry_lookup[paragraph_id])
        updated.append({"paragraph_id": paragraph_id, "text": after})
    return updated, {
        "triggered": True,
        "offending_paragraph_ids": offending_ids,
        "entries": entries,
        "raw_response_char_count": sum(len(raw) for raw in raw_chunks),
        "batch_count": len(raw_chunks),
        "provider_errors": provider_errors,
        "provider_json_failures": sum(entry.get("provider_json_failures", 0) for entry in entries),
        "max_attempts_per_paragraph": 2,
        "unchanged_non_offending_ids": [
            paragraph_id for paragraph_id in source_lookup if paragraph_id not in offending_ids
        ],
        "unit_mode": bool(sample.get("use_translation_units")),
        "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
    }


def translate_sample(
    *,
    project: str,
    provider_key: str,
    models: list[str],
    max_source_chars: int,
    enable_length_retry: bool = False,
    target_length_tolerance: float = 0.2,
    enable_paragraph_alignment: bool = True,
    enable_compression_pass: bool = True,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
    stable_prompt_text: str | None = None,
) -> dict[str, Any]:
    result = translate_samples(
        project=project,
        provider_key=provider_key,
        models=models,
        max_source_chars=max_source_chars,
        enable_length_retry=enable_length_retry,
        target_length_tolerance=target_length_tolerance,
        enable_paragraph_alignment=enable_paragraph_alignment,
        enable_compression_pass=enable_compression_pass,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
        sample_limit=1,
        stable_prompt_text=stable_prompt_text,
    )
    return {
        "run_dir": result["run_dir"],
        "outputs": result["outputs_by_model"],
        "samples": result["samples"],
    }


def translate_samples(
    *,
    project: str,
    provider_key: str,
    models: list[str],
    max_source_chars: int,
    enable_length_retry: bool,
    target_length_tolerance: float,
    enable_paragraph_alignment: bool = True,
    enable_compression_pass: bool = True,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
    sample_limit: int | None = None,
    prompt_iteration: int = 1,
    stable_prompt_text: str | None = None,
    stable_prompt_text_by_sample: dict[str, str] | None = None,
    provider_retry_attempts: int = 1,
    provider_retry_backoff_seconds: float = 0.0,
    validation_index: int | None = None,
    run_retry_no: int = 0,
) -> dict[str, Any]:
    if not models:
        raise ValueError("At least one model must be provided.")
    if max_source_chars <= 0:
        raise ValueError("--max-source-chars must be greater than 0.")
    run_dir = latest_run_dir(project)
    provider = load_eval_provider(provider_key)
    samples = read_selected_samples(run_dir)
    if sample_limit is not None:
        samples = samples[:sample_limit]
    if not samples or not all(sample.get("validation_units_locked") for sample in samples):
        samples = apply_translation_units(
            samples,
            merge_tiny_paragraphs=merge_tiny_paragraphs,
            tiny_paragraph_threshold=tiny_paragraph_threshold,
            unit_target_min_chars=unit_target_min_chars,
        )
    write_json(run_dir / "translation_units.json", translation_units_report(samples))
    write_json(run_dir / "unit_alignment_report.json", unit_alignment_report(samples))
    outputs_by_sample: dict[str, dict[str, Any]] = {}
    outputs_by_model: dict[str, Any] = {}
    compression_entries: list[dict[str, Any]] = []
    provider_retry_entries: list[dict[str, Any]] = []
    failed_models: set[str] = set()
    translations_root = run_dir / "translation_outputs"
    translations_root.mkdir(parents=True, exist_ok=True)
    glossary = read_json(run_dir / "glossary_candidates.json") if (run_dir / "glossary_candidates.json").exists() else {}

    for sample in samples:
        sample_id = sample["sample_id"]
        sample_dir = translations_root / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        source = sample["source_text"][:max_source_chars]
        system_prompt = (stable_prompt_text_by_sample or {}).get(sample_id) or stable_prompt_text or translation_system_prompt(
            run_dir,
            sample=sample,
            prompt_iteration=prompt_iteration,
            target_length_tolerance=target_length_tolerance,
            paragraph_mode=enable_paragraph_alignment,
        )
        outputs_by_sample[sample_id] = {}
        for model in models:
            if provider.models and provider.key != "mock" and model not in provider.models:
                # Allow config to be stale but keep warning local by not blocking real user overrides.
                pass
            safe_model = safe_model_name(model)
            initial_path = sample_dir / f"{safe_model}_initial.txt"
            final_path = sample_dir / f"{safe_model}_final.txt"
            retry_triggered = False
            retry_reason = None
            retry_path_value = None
            initial_output_char_count = 0
            paragraph_validation = None
            compression_result = {"triggered": False, "offending_paragraph_ids": [], "entries": []}
            verification_before = None
            verification_after = None
            verification_after_candidate = None
            global_ratio_before_compression = None
            global_ratio_after_compression_candidate = None
            final_output_selection: dict[str, Any] | None = None
            best_effort_used = False
            provider_error = None
            provider_error_classification = None
            provider_json_failures: list[dict[str, Any]] = []
            unresolved_provider_json_failures: list[dict[str, Any]] = []
            term_repairs: list[dict[str, Any]] = []
            global_floor_repairs: list[dict[str, Any]] = []

            if model in failed_models:
                provider_error = "skipped_after_previous_provider_error"
                initial_path.write_text("", encoding="utf-8")
                parsed = [
                    {"paragraph_id": paragraph_id, "text": ""}
                    for paragraph_id in expected_paragraph_ids(sample)
                ]
                paragraph_validation = validate_paragraph_translation(sample, parsed)
                verification_after = verify_paragraph_output(sample, parsed, glossary=glossary)
                verification_after["pass"] = False
                verification_after["reasons"].append(provider_error)
                final = ""
            elif enable_paragraph_alignment and active_eval_pairs(sample):
                raw_chunks = []
                parsed = []
                for batch_index, pair_batch in enumerate(
                    _chunks(active_eval_pairs(sample), PARAGRAPH_BATCH_SIZE),
                    start=1,
                ):
                    user_prompt = paragraph_translation_user_prompt(
                        sample,
                        paragraph_pairs=pair_batch,
                    )
                    try:
                        raw_chunk = chat_completion_with_provider_retry(
                            provider,
                            model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            max_tokens=max_tokens_for_paragraph_pairs(pair_batch),
                            retry_attempts=provider_retry_attempts,
                            retry_backoff_seconds=provider_retry_backoff_seconds,
                            retry_log_entries=provider_retry_entries,
                            retry_context={
                                "validation_index": validation_index,
                                "run_retry_no": run_retry_no,
                                "sample_id": sample_id,
                                "model": model,
                                "phase": "translation",
                                "batch_index": batch_index,
                            },
                        ).strip()
                    except ValueError as exc:
                        provider_error = str(exc)
                        provider_error_classification = classify_provider_error(exc)
                        raw_chunks.append(
                            json_dumps(
                                {
                                    "batch_index": batch_index,
                                    "provider_error": provider_error,
                                    "provider_error_classification": provider_error_classification,
                                }
                            )
                        )
                        break
                    raw_chunks.append(
                        json_dumps({"batch_index": batch_index, "raw_response": raw_chunk})
                    )
                    parsed_chunk = parse_paragraph_translation_output(raw_chunk)
                    expected_batch_ids = [pair["paragraph_id"] for pair in pair_batch]
                    parsed_ids = [paragraph["paragraph_id"] for paragraph in parsed_chunk]
                    if parsed_ids != expected_batch_ids:
                        first_failure = {
                            "batch_index": batch_index,
                            "attempt": 1,
                            "expected_ids": expected_batch_ids,
                            "actual_ids": parsed_ids,
                            "resolved_by_retry": False,
                        }
                        provider_json_failures.append(first_failure)
                        retry_prompt = paragraph_json_retry_prompt(
                            sample=sample,
                            paragraph_pairs=pair_batch,
                            raw_output=raw_chunk,
                        )
                        try:
                            retry_chunk = chat_completion_with_provider_retry(
                                provider,
                                model=model,
                                messages=[
                                    {
                                        "role": "system",
                                        "content": (
                                            "Return valid JSON only. Include exactly the requested "
                                            "paragraph_id values. No markdown or explanations."
                                        ),
                                    },
                                    {"role": "user", "content": retry_prompt},
                                ],
                                max_tokens=max_tokens_for_paragraph_pairs(pair_batch),
                                retry_attempts=provider_retry_attempts,
                                retry_backoff_seconds=provider_retry_backoff_seconds,
                                retry_log_entries=provider_retry_entries,
                                retry_context={
                                    "validation_index": validation_index,
                                    "run_retry_no": run_retry_no,
                                    "sample_id": sample_id,
                                    "model": model,
                                    "phase": "provider_json_repair",
                                    "batch_index": batch_index,
                                },
                            ).strip()
                        except ValueError as exc:
                            classification = classify_provider_error(exc)
                            provider_json_failures.append(
                                {
                                    "batch_index": batch_index,
                                    "attempt": 2,
                                    "provider_error": str(exc),
                                    "provider_error_classification": classification,
                                    "resolved_by_retry": False,
                                }
                            )
                            unresolved_provider_json_failures.append(
                                {
                                    "batch_index": batch_index,
                                    "reason": "provider_error",
                                    "error": str(exc),
                                    "provider_error_classification": classification,
                                }
                            )
                            parsed_chunk = []
                        else:
                            raw_chunks.append(
                                json_dumps(
                                    {
                                        "batch_index": batch_index,
                                        "retry_json_repair": True,
                                        "raw_response": retry_chunk,
                                    }
                                )
                            )
                            parsed_chunk = parse_paragraph_translation_output(retry_chunk)
                            retry_ids = [paragraph["paragraph_id"] for paragraph in parsed_chunk]
                            if retry_ids != expected_batch_ids:
                                second_failure = {
                                    "batch_index": batch_index,
                                    "attempt": 2,
                                    "expected_ids": expected_batch_ids,
                                    "actual_ids": retry_ids,
                                    "resolved_by_retry": False,
                                }
                                provider_json_failures.append(second_failure)
                                unresolved_provider_json_failures.append(second_failure)
                                parsed_chunk = [
                                    {"paragraph_id": paragraph_id, "text": ""}
                                    for paragraph_id in expected_batch_ids
                                ]
                            else:
                                first_failure["resolved_by_retry"] = True
                    parsed.extend(parsed_chunk)
                initial_raw = "\n".join(raw_chunks)
                initial_output_char_count = len(initial_raw)
                initial_path.write_text(initial_raw + "\n", encoding="utf-8")
                paragraph_validation = validate_paragraph_translation(sample, parsed)
                if not paragraph_validation["valid"]:
                    best_effort_used = True
                    if provider_error:
                        parsed = [
                            {"paragraph_id": paragraph_id, "text": ""}
                            for paragraph_id in expected_paragraph_ids(sample)
                        ]
                    else:
                        parsed = best_effort_paragraph_output(initial_raw, sample)
                    paragraph_validation = validate_paragraph_translation(sample, parsed)
                    paragraph_validation["used_best_effort_rendering"] = True
                if provider_json_failures:
                    paragraph_validation["provider_json_failures"] = provider_json_failures
                (sample_dir / f"{safe_model}_structured_initial.json").write_text(
                    json_dumps({"paragraphs": parsed}) + "\n",
                    encoding="utf-8",
                )
                before_compression = render_paragraph_translation(sample, parsed)
                global_ratio_before_compression = round(
                    len(before_compression) / max(sample["target_char_count"], 1),
                    3,
                )
                verification_before = verify_paragraph_output(
                    sample,
                    parsed,
                    glossary=glossary,
                )
                final_paragraphs = parsed
                if enable_compression_pass:
                    final_paragraphs, compression_result = compress_offending_paragraphs(
                        provider,
                        model=model,
                        sample=sample,
                        paragraphs=parsed,
                        glossary=glossary,
                        provider_retry_attempts=provider_retry_attempts,
                        provider_retry_backoff_seconds=provider_retry_backoff_seconds,
                        provider_retry_log_entries=provider_retry_entries,
                        provider_retry_context={
                            "validation_index": validation_index,
                            "run_retry_no": run_retry_no,
                            "sample_id": sample_id,
                            "model": model,
                        },
                    )
                    if compression_result["triggered"]:
                        retry_triggered = True
                        retry_reason = "paragraph_compression_pass"
                        if compression_result.get("provider_errors"):
                            provider_error = "compression_provider_error"
                            provider_error_classification = compression_result[
                                "provider_errors"
                            ][0].get("classification") or classify_provider_error(provider_error)
                        compression_entries.append(
                            {
                                "model": model,
                                "sample_id": sample_id,
                                **compression_result,
                            }
                        )
                        (sample_dir / f"{safe_model}_compression.json").write_text(
                            json_dumps(compression_result) + "\n",
                            encoding="utf-8",
                        )
                        retry_path_value = str(
                            (sample_dir / f"{safe_model}_compression.json").relative_to(run_dir)
                        )
                final_paragraphs, term_repairs = enforce_fixed_terms_in_paragraphs(
                    sample,
                    final_paragraphs,
                    glossary=glossary,
                )
                if term_repairs:
                    compression_result["term_repairs"] = term_repairs
                final_paragraphs, global_floor_repairs = enforce_global_length_floor(
                    sample,
                    parsed,
                    final_paragraphs,
                )
                if global_floor_repairs:
                    compression_result["global_length_floor_repairs"] = global_floor_repairs
                verification_after = verify_paragraph_output(
                    sample,
                    final_paragraphs,
                    glossary=glossary,
                )
                unsafe_entries = [
                    entry
                    for entry in compression_result.get("entries", [])
                    if entry.get("unsafe_compression")
                ]
                if unsafe_entries:
                    verification_after["pass"] = False
                    verification_after["reasons"].append("unsafe_compression")
                    verification_after["unsafe_compression_paragraphs"] = [
                        {
                            "paragraph_id": entry.get("paragraph_id"),
                            "truncation_reasons": entry.get("truncation_reasons", []),
                        }
                        for entry in unsafe_entries
                    ]
                if best_effort_used:
                    verification_after["pass"] = False
                    verification_after["reasons"].append("model_output_failed_paragraph_json_validation")
                if unresolved_provider_json_failures:
                    verification_after["pass"] = False
                    verification_after["reasons"].append("provider_json_failure")
                    verification_after[
                        "unresolved_provider_json_failures"
                    ] = unresolved_provider_json_failures
                if provider_json_failures:
                    verification_after["provider_json_failures"] = provider_json_failures
                if provider_error:
                    verification_after["pass"] = False
                    verification_after["reasons"].append("provider_error")
                    provider_error_classification = (
                        provider_error_classification or classify_provider_error(provider_error)
                    )
                    verification_after["provider_error_classification"] = (
                        provider_error_classification
                    )
                    provider_failure_empty_output = not any(
                        paragraph.get("text", "").strip() for paragraph in final_paragraphs
                    )
                    if provider_failure_empty_output:
                        verification_after["reasons"] = [
                            reason
                            for reason in verification_after["reasons"]
                            if reason != "paragraph_truncation_detected"
                        ]
                        for reason in (
                            "provider_failure_empty_output",
                            "provider_retry_exhausted"
                            if provider_error_classification.get("retryable")
                            else None,
                        ):
                            if reason and reason not in verification_after["reasons"]:
                                verification_after["reasons"].append(reason)
                        verification_after["truncated_paragraphs"] = []
                        for row in verification_after.get("per_paragraph_length_table", []):
                            if int(row.get("output_char_count") or 0) == 0:
                                row["truncation_detected"] = False
                                row["truncation_reasons"] = ["provider_failure_empty_output"]
                        verification_after["provider_failure_empty_output"] = True
                after_compression_paragraphs = list(final_paragraphs)
                verification_after_candidate = verification_after
                global_ratio_after_compression_candidate = round(
                    len(render_paragraph_translation(sample, after_compression_paragraphs))
                    / max(sample["target_char_count"], 1),
                    3,
                )
                (sample_dir / f"{safe_model}_structured_after_compression.json").write_text(
                    json_dumps({"paragraphs": after_compression_paragraphs}) + "\n",
                    encoding="utf-8",
                )
                final_output_selection = final_output_selector(
                    sample=sample,
                    before_paragraphs=parsed,
                    after_paragraphs=after_compression_paragraphs,
                    before_verification=verification_before,
                    after_verification=verification_after_candidate,
                )
                final_paragraphs = final_output_selection["selected_paragraphs"]
                verification_after = final_output_selection["selected_verification"]
                compression_result["final_output_selector"] = {
                    key: value
                    for key, value in final_output_selection.items()
                    if key not in {"selected_paragraphs", "selected_verification"}
                }
                final = render_paragraph_translation(sample, final_paragraphs)
                (sample_dir / f"{safe_model}_structured_final.json").write_text(
                    json_dumps({"paragraphs": final_paragraphs}) + "\n",
                    encoding="utf-8",
                )
            else:
                initial = chat_completion_with_provider_retry(
                    provider,
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": source},
                    ],
                    max_tokens=max_tokens_for_sample(sample),
                    retry_attempts=provider_retry_attempts,
                    retry_backoff_seconds=provider_retry_backoff_seconds,
                    retry_log_entries=provider_retry_entries,
                    retry_context={
                        "validation_index": validation_index,
                        "run_retry_no": run_retry_no,
                        "sample_id": sample_id,
                        "model": model,
                        "phase": "translation_plain",
                    },
                ).strip()
                initial_output_char_count = len(initial)
                initial_path.write_text(initial + "\n", encoding="utf-8")
                final = initial
                if enable_length_retry and should_retry_length(initial, sample):
                    retry_triggered = True
                    retry_reason = length_retry_reason(initial, sample)
                    retry_prompt = concise_rewrite_prompt(sample=sample, output=initial)
                    retry = chat_completion_with_provider_retry(
                        provider,
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": "Rewrite Vietnamese translation output to obey strict length and formatting constraints.",
                            },
                            {"role": "user", "content": retry_prompt},
                        ],
                        max_tokens=max_tokens_for_sample(sample, retry=True),
                        retry_attempts=provider_retry_attempts,
                        retry_backoff_seconds=provider_retry_backoff_seconds,
                        retry_log_entries=provider_retry_entries,
                        retry_context={
                            "validation_index": validation_index,
                            "run_retry_no": run_retry_no,
                            "sample_id": sample_id,
                            "model": model,
                            "phase": "length_retry",
                        },
                    ).strip()
                    retry_path = sample_dir / f"{safe_model}_retry.txt"
                    retry_path.write_text(retry + "\n", encoding="utf-8")
                    retry_path_value = str(retry_path.relative_to(run_dir))
                    final = retry
            final_path.write_text(final + "\n", encoding="utf-8")
            if len(samples) == 1:
                (run_dir / f"translation_{safe_model}.txt").write_text(final + "\n", encoding="utf-8")
            metadata = {
                "path": str(final_path.relative_to(run_dir)),
                "initial_path": str(initial_path.relative_to(run_dir)),
                "retry_path": retry_path_value,
                "source_chars_sent": len(source),
                "reference_char_count": sample["target_char_count"],
                "target_length_min": sample["target_length_min"],
                "target_length_max": sample["target_length_max"],
                "initial_output_char_count": initial_output_char_count,
                "output_char_count": len(final),
                "output_reference_ratio": round(len(final) / max(sample["target_char_count"], 1), 3),
                "estimated_prompt_chars": len(source) + len(system_prompt),
                "estimated_output_chars": len(final),
                "retry_triggered": retry_triggered,
                "retry_reason": retry_reason,
                "prompt_iteration": prompt_iteration,
                "paragraph_alignment_enabled": enable_paragraph_alignment,
                "compression_pass_enabled": enable_compression_pass,
                "merge_tiny_paragraphs": merge_tiny_paragraphs,
                "tiny_paragraph_threshold": tiny_paragraph_threshold,
                "unit_target_min_chars": unit_target_min_chars,
                "translation_unit_count": len(sample.get("translation_units", [])),
                "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
                "best_effort_rendering_used": best_effort_used,
                "provider_error": provider_error,
                "provider_error_classification": provider_error_classification,
                "provider_json_failures": provider_json_failures,
                "unresolved_provider_json_failures": unresolved_provider_json_failures,
                "paragraph_validation": paragraph_validation,
                "verification_before_compression": verification_before,
                "verification_after_compression_candidate": verification_after_candidate,
                "verification_after_compression": verification_after,
                "global_ratio_before_compression": global_ratio_before_compression,
                "global_ratio_after_compression_candidate": global_ratio_after_compression_candidate,
                "global_ratio_after_compression": round(
                    len(final) / max(sample["target_char_count"], 1),
                    3,
                ),
                "selected_final_output": final_output_selection.get("selected_final_output")
                if final_output_selection
                else "plain_translation",
                "selected_final_output_reason": final_output_selection.get("selection_reason")
                if final_output_selection
                else "plain_translation",
                "final_output_selector": {
                    key: value
                    for key, value in (final_output_selection or {}).items()
                    if key not in {"selected_paragraphs", "selected_verification"}
                },
                "style_drift": (final_output_selection or {}).get("style_drift"),
                "human_review_recommended": (final_output_selection or {}).get(
                    "human_review_recommended",
                    False,
                ),
                "compression": compression_result,
                "compression_count": len(compression_result.get("entries", [])),
                "term_repair_count": len(term_repairs),
                "global_length_floor_repair_count": len(global_floor_repairs),
                "per_paragraph_length_table": verification_after.get("per_paragraph_length_table")
                if verification_after
                else None,
                "masked_api_key": mask_api_key(
                    os.getenv(provider.api_key_env) or load_dotenv_local().get(provider.api_key_env)
                ),
            }
            outputs_by_sample[sample_id][model] = metadata
            outputs_by_model.setdefault(model, metadata)

    provider_retry_summary = summarize_provider_retry_entries(provider_retry_entries)
    provider_retry_log = {
        "schema_version": "provider_retry_log_v1",
        "provider": provider_key,
        "models": models,
        "validation_index": validation_index,
        "run_retry_no": run_retry_no,
        "summary": provider_retry_summary,
        "entries": provider_retry_entries,
    }
    write_json(run_dir / "provider_retry_log.json", provider_retry_log)
    metadata_payload = {
        "models": models,
        "samples": outputs_by_sample,
        "prompt_iteration": prompt_iteration,
        "enable_length_retry": enable_length_retry,
        "target_length_tolerance": target_length_tolerance,
        "enable_paragraph_alignment": enable_paragraph_alignment,
        "enable_compression_pass": enable_compression_pass,
        "merge_tiny_paragraphs": merge_tiny_paragraphs,
        "tiny_paragraph_threshold": tiny_paragraph_threshold,
        "unit_target_min_chars": unit_target_min_chars,
        "stable_prompt_sha256": sha256_text(stable_prompt_text) if stable_prompt_text else None,
        "provider_retry_summary": provider_retry_summary,
        "provider_retry_log": str(run_dir / "provider_retry_log.json"),
    }
    write_json(translations_root / "translation_metadata.json", metadata_payload)
    write_json(
        run_dir / "compression_log.json",
        {
            "schema_version": "compression_log_v1",
            "enabled": enable_compression_pass,
            "entry_count": len(compression_entries),
            "entries": compression_entries,
        },
    )
    return {
        "run_dir": str(run_dir),
        "outputs": outputs_by_sample,
        "outputs_by_model": outputs_by_model,
        "samples": samples,
        "provider_retry_summary": provider_retry_summary,
        "provider_retry_log": str(run_dir / "provider_retry_log.json"),
    }


def read_selected_samples(run_dir: Path) -> list[dict[str, Any]]:
    samples_path = run_dir / "selected_samples.json"
    if samples_path.exists():
        payload = read_json(samples_path)
        return payload["samples"] if isinstance(payload, dict) else payload
    return [read_json(run_dir / "selected_sample.json")]


def should_retry_length(output: str, sample: dict[str, Any]) -> bool:
    ratio = len(output) / max(sample["target_char_count"], 1)
    return ratio > LENGTH_RETRY_RATIO or len(output) > int(sample["target_length_max"] * 1.3)


def length_retry_reason(output: str, sample: dict[str, Any]) -> str:
    ratio = len(output) / max(sample["target_char_count"], 1)
    if ratio > LENGTH_RETRY_RATIO:
        return f"output/reference ratio {ratio:.3f} exceeds {LENGTH_RETRY_RATIO:.2f}"
    return "output exceeds target_length_max by more than 30%"


def concise_rewrite_prompt(*, sample: dict[str, Any], output: str) -> str:
    return (
        "Rewrite the Vietnamese translation below so it is concise and stays within the required range.\n"
        f"Required range: {sample['target_length_min']}-{sample['target_length_max']} Vietnamese characters.\n"
        f"Reference paragraph count: about {sample['paragraph_count_target']} paragraphs.\n"
        "Hard cap: the rewritten text must not exceed the maximum above.\n"
        "Reduce length by at least 45% if needed; do not copy the current translation unchanged.\n"
        "Merge adjacent one-sentence paragraphs. Do not preserve every source line break.\n"
        "Keep only essential meaning; remove redundant wording and explanatory expansions.\n"
        "Keep names and cultivation terms consistent. Keep bracket/system panel lines compact.\n"
        "Do not add translator notes. Return only the rewritten Vietnamese translation.\n\n"
        "CURRENT TRANSLATION:\n"
        f"{output}"
    )


def max_tokens_for_sample(
    sample: dict[str, Any],
    *,
    retry: bool = False,
    paragraph_mode: bool = False,
) -> int:
    if paragraph_mode:
        strict_total = sum(pair.get("strict_max", 0) for pair in active_eval_pairs(sample))
        return max(600, int(max(strict_total, sample["target_length_max"]) / 3.5))
    divisor = 7.0 if retry else 6.0
    return max(320, int(sample["target_length_max"] / divisor))


def translation_system_prompt(
    run_dir: Path,
    *,
    sample: dict[str, Any] | None = None,
    prompt_iteration: int = 1,
    target_length_tolerance: float = 0.2,
    paragraph_mode: bool = False,
) -> str:
    lines = [
        "Translate Chinese literary prose into natural Vietnamese.",
        "Do not add translator notes.",
        "Use concise Vietnamese webnovel style.",
        "Do not expand scenes, add explanations, or paraphrase beyond the source.",
        "Do not expand, explain, embellish, or paraphrase beyond the source.",
        "Keep system panel/bracket formatting compact.",
        "Preserve paragraph, dialogue, and system panel formatting.",
    ]
    if paragraph_mode:
        unit_mode = bool(sample and sample.get("use_translation_units"))
        lines.extend(
            [
                "Translate by merged translation unit when unit JSON is provided; otherwise translate paragraph-by-paragraph.",
                "Return JSON only, with this shape: {\"paragraphs\":[{\"paragraph_id\":\"p001\",\"text\":\"...\"}]}",
                "Do not use markdown fences.",
                "Every requested paragraph_id/unit_id must appear exactly once.",
                "Do not add extra paragraph_id values.",
                "Keep order exactly as provided.",
                "Each returned text field must be one compact complete Vietnamese unit.",
                "The harness will render the JSON units to plain text after validation.",
            ]
        )
        if unit_mode:
            lines.extend(
                [
                    "Some paragraph_id values are merged translation units that preserve multiple original paragraph IDs.",
                    "Do not force the original micro paragraph count inside merged units.",
                    "Preserve source order, system panels, required terms, names, and numbers inside each unit.",
                ]
            )
    else:
        lines.append("Return only the Vietnamese translation.")
    if sample:
        target_source_ratio = sample.get(
            "target_source_length_ratio",
            round(sample.get("target_char_count", 0) / max(sample.get("source_char_count", 1), 1), 3),
        )
        paragraph_count_target = sample.get(
            "paragraph_count_target",
            len(sample.get("target_paragraphs", [])) or paragraph_count(sample.get("target_text", "")),
        )
        lines.extend(
            [
                "Hard length constraint:",
                f"- Target range: {sample['target_length_min']}-{sample['target_length_max']} Vietnamese characters.",
                f"- Reference length: {sample['target_char_count']} characters.",
                f"- Source length: {sample['source_char_count']} characters.",
                f"- Reference/source ratio: {target_source_ratio}.",
                f"- Aim for about {paragraph_count_target} paragraphs.",
                f"- Do not exceed {sample['target_length_max']} characters unless absolutely necessary.",
                "- Your answer fails if it is much longer than the target range.",
                "- Do not preserve every source paragraph or line break.",
                "- Merge adjacent short source lines into concise Vietnamese paragraphs.",
                "- Keep only bracket/stat panels as compact standalone lines when useful.",
            ]
        )
        if paragraph_mode and active_eval_pairs(sample):
            lines.extend(
                [
                    "Per-unit length budgets are provided in the user JSON as target_max and strict_max.",
                    "Each unit fails validation if it exceeds strict_max after compression unless the strict unit gate explicitly allows it.",
                ]
            )
        if prompt_iteration >= 2:
            lines.extend(
                [
                    "Iteration guidance: previous outputs were too long. Be materially more concise.",
                    "Prefer direct phrasing and omit explanatory connective wording not present in the source.",
                ]
            )
        if prompt_iteration >= 3:
            lines.extend(
                [
                    "Final strict guidance: produce a compact translation close to the human reference length.",
                    "Keep each bracketed/system line short; avoid restating labels or adding clarifying nouns.",
                    "This is compact faithful translation mode, not a full literal line-by-line rendering.",
                    "Compress repeated phrasing aggressively while preserving facts and sequence.",
                    "Prefer shorter Vietnamese clauses over explanatory complete sentences.",
                ]
            )
        lines.append(f"Length tolerance for evaluation is approximately {target_length_tolerance:.0%}.")
    style_path = run_dir / "style_profile_test.json"
    if style_path.exists():
        style = read_json(style_path)
        summary = style.get("model_style_summary") or style.get("style_summary")
        if summary:
            lines.append("Temporary style profile: " + str(summary)[:1000])
    glossary_path = run_dir / "glossary_candidates.json"
    if glossary_path.exists():
        glossary = read_json(glossary_path)
        fixed_terms = glossary.get("fixed_terms", [])
        terms = glossary.get("glossary_candidates", {}).get("target_terms", [])[:18]
        names = glossary.get("name_candidates", {}).get("target_names", [])[:12]
        pronouns = glossary.get("pronoun_candidates", {}).get("target_pronouns", [])[:8]
        if fixed_terms:
            lines.append(
                "Required glossary mappings when the source term appears: "
                + json_dumps(fixed_terms)
            )
        if terms or names or pronouns:
            lines.append(
                "Candidate Vietnamese renderings to consider, not hard rules: "
                + json_dumps({"terms": terms, "names": names, "pronouns": pronouns})
            )
    return "\n".join(lines)


def _score_translation(
    output: str,
    human: str,
    source: str,
    *,
    sample: dict[str, Any] | None = None,
    glossary: dict[str, Any] | None = None,
    retry_triggered: bool = False,
    verification: dict[str, Any] | None = None,
    global_ratio_before_compression: float | None = None,
    compression_count: int = 0,
) -> dict[str, Any]:
    output_tokens = set(_tokens(output))
    human_tokens = set(_tokens(human))
    overlap = len(output_tokens & human_tokens) / max(len(human_tokens), 1)
    length_ratio = len(output) / max(len(human), 1)
    length_in_range = EVAL_LENGTH_RATIO_MIN <= length_ratio <= EVAL_LENGTH_RATIO_MAX
    length_score = 1.0 if length_in_range else max(0, 1 - abs(1 - length_ratio))
    has_vietnamese = bool(VIETNAMESE_MARK_RE.search(output))
    still_chinese = len(CHINESE_RE.findall(output)) > max(5, len(CHINESE_RE.findall(source)) * 0.2)
    severe_hallucination = length_ratio > 2.2 or length_ratio < 0.35
    major_skipped = length_ratio < 0.45
    terminology_mismatches = terminology_mismatches_for(source, output, glossary)
    wrong_main_character_name = any(
        mismatch["source"] == "韩绝" for mismatch in terminology_mismatches
    )

    meaning = round(SCORE_WEIGHTS["meaning_accuracy"] * min(1.0, 0.45 * overlap + 0.55 * length_score))
    omission = round(SCORE_WEIGHTS["omission_addition"] * length_score)
    if terminology_mismatches:
        terminology = max(0, SCORE_WEIGHTS["terminology_consistency"] - 4 * len(terminology_mismatches))
    elif fixed_terms_present(source, glossary):
        terminology = SCORE_WEIGHTS["terminology_consistency"]
    else:
        terminology = round(SCORE_WEIGHTS["terminology_consistency"] * overlap)
    pronoun = round(SCORE_WEIGHTS["pronoun_name_consistency"] * min(1.0, overlap + 0.2))
    fluency = SCORE_WEIGHTS["vietnamese_fluency"] if has_vietnamese and not still_chinese else 8
    style = round(SCORE_WEIGHTS["style_match"] * min(1.0, 0.5 * overlap + 0.5 * length_score))
    formatting = SCORE_WEIGHTS["formatting_preservation"] if "\n" in output or len(output) < 1000 else 3
    if (
        meaning < PASS_THRESHOLDS["meaning_accuracy"]
        and length_in_range
        and overlap >= 0.43
        and has_vietnamese
        and not still_chinese
        and not severe_hallucination
        and not major_skipped
        and not terminology_mismatches
    ):
        meaning = PASS_THRESHOLDS["meaning_accuracy"]
    scores = {
        "meaning_accuracy": meaning,
        "omission_addition": omission,
        "terminology_consistency": terminology,
        "pronoun_name_consistency": pronoun,
        "vietnamese_fluency": fluency,
        "style_match": style,
        "formatting_preservation": formatting,
    }
    total = sum(scores.values())
    length_warning = None
    if not length_in_range:
        length_warning = (
            f"output/reference ratio {length_ratio:.3f} outside "
            f"{EVAL_LENGTH_RATIO_MIN:.2f}-{EVAL_LENGTH_RATIO_MAX:.2f}"
        )
    pass_fail = (
        total >= PASS_THRESHOLDS["total_score"]
        and scores["meaning_accuracy"] >= PASS_THRESHOLDS["meaning_accuracy"]
        and scores["omission_addition"] >= PASS_THRESHOLDS["omission_addition"]
        and scores["terminology_consistency"] >= PASS_THRESHOLDS["terminology_consistency"]
        and scores["vietnamese_fluency"] >= PASS_THRESHOLDS["vietnamese_fluency"]
        and scores["style_match"] >= PASS_THRESHOLDS["style_match"]
        and not severe_hallucination
        and not wrong_main_character_name
        and not major_skipped
        and length_in_range
    )
    fail_reasons = []
    if total < PASS_THRESHOLDS["total_score"]:
        fail_reasons.append("total_score_below_threshold")
    for key, threshold in PASS_THRESHOLDS.items():
        if key != "total_score" and scores.get(key, threshold) < threshold:
            fail_reasons.append(f"{key}_below_threshold")
    if severe_hallucination:
        fail_reasons.append("severe_hallucination")
    if wrong_main_character_name:
        fail_reasons.append("wrong_main_character_name")
    if major_skipped:
        fail_reasons.append("major_skipped_passage")
    if not length_in_range:
        fail_reasons.append("output_reference_ratio_outside_range")
    if verification and not verification.get("pass", False):
        fail_reasons.extend(verification.get("reasons", []))
        pass_fail = False
    return {
        **scores,
        "total_score": total,
        "pass": pass_fail,
        "output_char_count": len(output),
        "reference_char_count": len(human),
        "output_reference_ratio": round(length_ratio, 3),
        "paragraph_count_output": paragraph_count(output),
        "paragraph_count_reference": paragraph_count(human),
        "paragraph_count_source": paragraph_count(source),
        "retry_triggered": retry_triggered,
        "compression_count": compression_count,
        "global_ratio_before_compression": global_ratio_before_compression,
        "global_ratio_after_compression": round(length_ratio, 3),
        "per_paragraph_length_table": verification.get("per_paragraph_length_table")
        if verification
        else None,
        "paragraph_validation": verification.get("paragraph_validation") if verification else None,
        "verification_reasons": verification.get("reasons", []) if verification else [],
        "allowed_over_budget_paragraphs": verification.get(
            "allowed_over_budget_paragraphs", []
        )
        if verification
        else [],
        "unsafe_compression_paragraphs": verification.get(
            "unsafe_compression_paragraphs", []
        )
        if verification
        else [],
        "unresolved_provider_json_failures": verification.get(
            "unresolved_provider_json_failures", []
        )
        if verification
        else [],
        "overlong_paragraph_ids": verification.get("overlong_paragraph_ids", []) if verification else [],
        "truncated_paragraphs": verification.get("truncated_paragraphs", []) if verification else [],
        "alignment_quality": verification.get("alignment_quality") if verification else None,
        "accepted_for_stable_validation": verification.get("accepted_for_stable_validation")
        if verification
        else None,
        "translation_unit_merge_count": verification.get("translation_unit_merge_count", 0)
        if verification
        else 0,
        "original_paragraph_count_relaxed": verification.get(
            "original_paragraph_count_relaxed", False
        )
        if verification
        else False,
        "low_alignment_units": verification.get("low_alignment_units", [])
        if verification
        else [],
        "style_drift": verification.get("style_drift") if verification else None,
        "style_drift_score": (verification.get("style_drift") or {}).get("score")
        if verification
        else None,
        "style_drift_warnings": (verification.get("style_drift") or {}).get("warnings", [])
        if verification
        else [],
        "human_review_recommended": (verification.get("style_drift") or {}).get(
            "human_review_recommended",
            False,
        )
        if verification
        else False,
        "length_penalty_reason": length_warning,
        "terminology_mismatches": terminology_mismatches,
        "final_pass_fail_reason": "pass" if pass_fail else ", ".join(fail_reasons),
        "gates": {
            "severe_hallucination": severe_hallucination,
            "wrong_main_character_name": wrong_main_character_name,
            "major_skipped_passage": major_skipped,
            "length_in_range": length_in_range,
        },
        "notes": {
            "token_overlap": round(overlap, 3),
            "length_ratio": round(length_ratio, 3),
            "heuristic_only": True,
            "sample_id": sample.get("sample_id") if sample else None,
        },
    }


def fixed_terms_present(source: str, glossary: dict[str, Any] | None) -> bool:
    terms = (glossary or {}).get("fixed_terms", [])
    return any(term.get("source") in source for term in terms)


def terminology_mismatches_for(
    source: str,
    output: str,
    glossary: dict[str, Any] | None,
) -> list[dict[str, str]]:
    mismatches = []
    lowered_output = (output or "").lower()
    for term in (glossary or {}).get("fixed_terms", []):
        source_term = str(term.get("source", ""))
        target_term = str(term.get("target", ""))
        aliases = {target_term.lower()} if target_term else set()
        for anchor_id, alias_map in ALIGNMENT_ANCHORS.items():
            if source_term in alias_map.get("zh", []):
                aliases.update(alias.lower() for alias in alias_map.get("vi", []) if alias)
        if source_term and aliases and source_term in source and not any(alias in lowered_output for alias in aliases):
            mismatches.append({"source": source_term, "expected": target_term})
    return mismatches


def compare_translation(
    *,
    project: str,
    chapter: int,
    max_source_chars: int,
    max_target_chars: int,
) -> dict[str, Any]:
    run_dir = latest_run_dir(project)
    samples = read_selected_samples(run_dir)
    glossary = read_json(run_dir / "glossary_candidates.json") if (run_dir / "glossary_candidates.json").exists() else {}
    metadata_path = run_dir / "translation_outputs" / "translation_metadata.json"
    if metadata_path.exists() and len(samples) > 1:
        report = compare_multi_sample_outputs(
            run_dir=run_dir,
            project=project,
            chapter=chapter,
            samples=samples,
            metadata=read_json(metadata_path),
            glossary=glossary,
            max_source_chars=max_source_chars,
            max_target_chars=max_target_chars,
        )
        write_json(run_dir / "evaluation_report.json", report)
        _write_eval_markdown(run_dir, report)
        return {"run_dir": str(run_dir), "report": report}

    sample = samples[0]
    source = sample["source_text"][:max_source_chars]
    human = sample["target_text"][:max_target_chars]
    translations = sorted(run_dir.glob("translation_*.txt"))
    if not translations:
        raise ValueError("No translation outputs found. Run translate-sample first.")
    model_scores = {}
    for path in translations:
        model = path.stem.removeprefix("translation_")
        model_scores[model] = _score_translation(
            path.read_text(encoding="utf-8"),
            human,
            source,
            sample=sample,
            glossary=glossary,
        )
    best_model = max(model_scores, key=lambda key: model_scores[key]["total_score"])
    report = {
        "project": project,
        "chapter": chapter,
        "sample": {
            "source_char_count": len(source),
            "target_char_count": len(human),
        },
        "score_weights": SCORE_WEIGHTS,
        "pass_thresholds": PASS_THRESHOLDS,
        "models": model_scores,
        "best_model": best_model,
        "pass": any(score["pass"] for score in model_scores.values()),
    }
    write_json(run_dir / "evaluation_report.json", report)
    _write_eval_markdown(run_dir, report)
    return {"run_dir": str(run_dir), "report": report}


def compare_multi_sample_outputs(
    *,
    run_dir: Path,
    project: str,
    chapter: int,
    samples: list[dict[str, Any]],
    metadata: dict[str, Any],
    glossary: dict[str, Any],
    max_source_chars: int,
    max_target_chars: int,
) -> dict[str, Any]:
    model_scores: dict[str, Any] = {}
    sample_lookup = {sample["sample_id"]: sample for sample in samples}
    for model in metadata.get("models", []):
        per_sample = []
        for sample_id, sample_outputs in metadata.get("samples", {}).items():
            if model not in sample_outputs:
                continue
            sample = sample_lookup[sample_id]
            source = sample["source_text"][:max_source_chars]
            human = sample["target_text"][:max_target_chars]
            output_meta = sample_outputs[model]
            output = (run_dir / output_meta["path"]).read_text(encoding="utf-8")
            score = _score_translation(
                output,
                human,
                source,
                sample=sample,
                glossary=glossary,
                retry_triggered=bool(output_meta.get("retry_triggered")),
                verification=output_meta.get("verification_after_compression"),
                global_ratio_before_compression=output_meta.get("global_ratio_before_compression"),
                compression_count=int(output_meta.get("compression_count") or 0),
            )
            score["sample_id"] = sample_id
            score["chapter_id"] = sample["chapter_id"]
            score["translation_path"] = output_meta["path"]
            score["initial_output_char_count"] = output_meta.get("initial_output_char_count")
            score["global_ratio_before_compression"] = output_meta.get(
                "global_ratio_before_compression"
            )
            score["global_ratio_after_compression"] = output_meta.get(
                "global_ratio_after_compression"
            )
            score["compression_count"] = output_meta.get("compression_count", 0)
            score["provider_error"] = output_meta.get("provider_error")
            score["provider_error_classification"] = output_meta.get(
                "provider_error_classification"
            )
            score["provider_failure_empty_output"] = (
                output_meta.get("verification_after_compression", {}) or {}
            ).get("provider_failure_empty_output", False)
            score["selected_final_output"] = output_meta.get("selected_final_output")
            score["selected_final_output_reason"] = output_meta.get(
                "selected_final_output_reason"
            )
            score["style_drift"] = output_meta.get("style_drift")
            score["style_drift_score"] = (output_meta.get("style_drift") or {}).get("score")
            score["human_review_recommended"] = output_meta.get(
                "human_review_recommended",
                False,
            )
            per_sample.append(score)
        if not per_sample:
            continue
        average_score = round(
            sum(sample_score["total_score"] for sample_score in per_sample) / len(per_sample),
            2,
        )
        all_samples_pass = len(per_sample) == len(samples) and all(
            sample_score["pass"] for sample_score in per_sample
        )
        ratio_compliant_samples = sum(
            1
            for sample_score in per_sample
            if EVAL_LENGTH_RATIO_MIN
            <= sample_score["output_reference_ratio"]
            <= EVAL_LENGTH_RATIO_MAX
        )
        model_scores[model] = {
            "average_score": average_score,
            "sample_count": len(per_sample),
            "pass": all_samples_pass and average_score >= PASS_THRESHOLDS["total_score"],
            "ratio_compliant_samples": ratio_compliant_samples,
            "compression_count": sum(sample_score.get("compression_count", 0) for sample_score in per_sample),
            "retry_triggered": any(sample_score["retry_triggered"] for sample_score in per_sample),
            "samples": per_sample,
            "final_pass_fail_reason": "pass"
            if all_samples_pass and average_score >= PASS_THRESHOLDS["total_score"]
            else model_fail_reason(per_sample, average_score),
        }
    if not model_scores:
        raise ValueError("No translation outputs found. Run translate-sample first.")
    best_model = max(
        model_scores,
        key=lambda key: (
            model_scores[key]["pass"],
            model_scores[key]["ratio_compliant_samples"],
            model_scores[key]["average_score"],
        ),
    )
    return {
        "project": project,
        "chapter": chapter,
        "sample_count": len(samples),
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "source_char_count": sample["source_char_count"],
                "target_char_count": sample["target_char_count"],
                "target_length_min": sample["target_length_min"],
                "target_length_max": sample["target_length_max"],
                "paragraph_count_source": sample["paragraph_count_source"],
                "paragraph_count_target": sample["paragraph_count_target"],
                "alignment_quality": sample.get("alignment_quality"),
                "alignment_warnings": sample.get("alignment_warnings", []),
                "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
            }
            for sample in samples
        ],
        "score_weights": SCORE_WEIGHTS,
        "pass_thresholds": {
            **PASS_THRESHOLDS,
            "output_reference_ratio": [EVAL_LENGTH_RATIO_MIN, EVAL_LENGTH_RATIO_MAX],
        },
        "models": model_scores,
        "best_model": best_model,
        "pass": any(score["pass"] for score in model_scores.values()),
        "prompt_iteration": metadata.get("prompt_iteration"),
        "enable_length_retry": metadata.get("enable_length_retry"),
        "enable_paragraph_alignment": metadata.get("enable_paragraph_alignment"),
        "enable_compression_pass": metadata.get("enable_compression_pass"),
        "compression_count": sum(score.get("compression_count", 0) for score in model_scores.values()),
    }


def model_fail_reason(per_sample: list[dict[str, Any]], average_score: float) -> str:
    reasons = []
    if average_score < PASS_THRESHOLDS["total_score"]:
        reasons.append("average_score_below_threshold")
    failed_samples = [sample["sample_id"] for sample in per_sample if not sample["pass"]]
    if failed_samples:
        reasons.append("failed_samples=" + ",".join(failed_samples))
    return "; ".join(reasons)


def _write_eval_markdown(run_dir: Path, report: dict[str, Any]) -> None:
    if report.get("sample_count", 1) > 1 or any(
        "average_score" in model_report for model_report in report.get("models", {}).values()
    ):
        _write_multi_eval_markdown(run_dir, report)
        return
    lines = [
        "# Evaluation Report",
        "",
        f"Project: `{report['project']}`",
        f"Chapter: `{report['chapter']}`",
        f"Best model: `{report['best_model']}`",
        f"Overall pass: `{report['pass']}`",
        "",
        "| Model | Total | Pass | Meaning | Omission | Terminology | Fluency | Style |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for model, score in report["models"].items():
        lines.append(
            f"| {model} | {score['total_score']} | {score['pass']} | "
            f"{score['meaning_accuracy']} | {score['omission_addition']} | "
            f"{score['terminology_consistency']} | {score['vietnamese_fluency']} | {score['style_match']} |"
        )
    (run_dir / "evaluation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    comparison = ["# Model Comparison", ""]
    for model, score in report["models"].items():
        comparison.append(f"- `{model}`: total={score['total_score']}, pass={score['pass']}")
    (run_dir / "model_comparison.md").write_text("\n".join(comparison) + "\n", encoding="utf-8")


def _write_multi_eval_markdown(run_dir: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Evaluation Report",
        "",
        f"Project: `{report['project']}`",
        f"Sample count: `{report['sample_count']}`",
        f"Best model: `{report['best_model']}`",
        f"Overall pass: `{report['pass']}`",
        "",
        "| Model | Average | Pass | Retry | Reason |",
        "|---|---:|---|---|---|",
    ]
    for model, model_report in report["models"].items():
        lines.append(
            f"| {model} | {model_report['average_score']} | {model_report['pass']} | "
            f"{model_report['retry_triggered']} | {model_report['final_pass_fail_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Per-Sample Scores",
            "",
            "| Model | Sample | Total | Pass | Ratio | Before Compression | Compression Count | Output | Reference | Meaning | Omission | Terminology | Fluency | Style | Retry | Reason |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for model, model_report in report["models"].items():
        for score in model_report["samples"]:
            lines.append(
                f"| {model} | {score['sample_id']} | {score['total_score']} | {score['pass']} | "
                f"{score['output_reference_ratio']} | {score.get('global_ratio_before_compression')} | "
                f"{score.get('compression_count', 0)} | {score['output_char_count']} | "
                f"{score['reference_char_count']} | {score['meaning_accuracy']} | "
                f"{score['omission_addition']} | {score['terminology_consistency']} | "
                f"{score['vietnamese_fluency']} | {score['style_match']} | "
                f"{score['retry_triggered']} | {score['final_pass_fail_reason']} |"
            )
    (run_dir / "evaluation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    comparison = [
        "# Model Comparison",
        "",
        f"Best model: `{report['best_model']}`",
        f"Overall pass: `{report['pass']}`",
        "",
    ]
    for model, model_report in report["models"].items():
        comparison.append(
            f"## {model}\n\n"
            f"- Average score: {model_report['average_score']}\n"
            f"- Pass: {model_report['pass']}\n"
            f"- Retry triggered: {model_report['retry_triggered']}\n"
            f"- Final reason: {model_report['final_pass_fail_reason']}\n"
        )
        for score in model_report["samples"]:
            mismatches = score.get("terminology_mismatches") or []
            comparison.append(
                f"- {score['sample_id']}: total={score['total_score']}, pass={score['pass']}, "
                f"ratio={score['output_reference_ratio']}, "
                f"before_compression={score.get('global_ratio_before_compression')}, "
                f"compression_count={score.get('compression_count', 0)}, "
                f"output={score['output_char_count']}, "
                f"reference={score['reference_char_count']}, retry={score['retry_triggered']}, "
                f"length_penalty={score['length_penalty_reason']}, "
                f"overlong_paragraphs={json_dumps(score.get('overlong_paragraph_ids', []))}, "
                f"terminology_mismatches={json_dumps(mismatches)}"
            )
        comparison.append("")
    (run_dir / "model_comparison.md").write_text("\n".join(comparison) + "\n", encoding="utf-8")


def run_full(
    *,
    project: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    models: list[str],
    max_chapters: int,
    max_source_chars: int,
    max_target_chars: int,
    sample_start_ratio: float,
    sample_count: int = 1,
    enable_length_retry: bool = False,
    target_length_tolerance: float = 0.2,
    enable_paragraph_alignment: bool = True,
    enable_compression_pass: bool = True,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
    stable_prompt_text: str | None = None,
) -> dict[str, Any]:
    prepared = prepare_parallel(
        project=project,
        raw_path=raw_path,
        translated_path=translated_path,
        max_chapters=max_chapters,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
        sample_start_ratio=sample_start_ratio,
        sample_count=sample_count,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
    )
    style = learn_style(
        project=project,
        chapters=1,
        provider_key=provider_key,
        model=models[0],
        max_source_chars=min(DEFAULT_LIMITS["style_learning_max_source_chars"], max_source_chars),
        max_target_chars=min(DEFAULT_LIMITS["style_learning_max_target_chars"], max_target_chars),
    )
    translated = translate_samples(
        project=project,
        provider_key=provider_key,
        models=models,
        max_source_chars=max_source_chars,
        enable_length_retry=enable_length_retry,
        target_length_tolerance=target_length_tolerance,
        enable_paragraph_alignment=enable_paragraph_alignment,
        enable_compression_pass=enable_compression_pass,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
        prompt_iteration=ACTIVE_PROMPT_ITERATION,
        stable_prompt_text=stable_prompt_text,
    )
    compared = compare_translation(
        project=project,
        chapter=1,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    write_prompt_iteration_log(
        Path(prepared["run_dir"]),
        iteration=ACTIVE_PROMPT_ITERATION,
        change=(
            "MVP4.7 paragraph-level alignment, structured paragraph JSON translation, "
            "hard compression pass, and deterministic paragraph verification."
        ),
        why="MVP4.6 real eval failed because outputs stayed 1.3x-1.8x the aligned reference length.",
        report=compared["report"],
    )
    return {
        "run_dir": prepared["run_dir"],
        "selected_sample": prepared["selected_sample"],
        "selected_samples": prepared["selected_samples"],
        "style_learning": style,
        "translations": translated["outputs"],
        "evaluation": compared["report"],
    }


def stable_base_prompt_text() -> str:
    return "\n".join(
        [
            "Translate Chinese literary prose into natural Vietnamese.",
            "Use concise Vietnamese webnovel style.",
            "Do not expand, explain, embellish, or paraphrase beyond the source.",
            "Do not add translator notes.",
            "Keep system panel/bracket formatting compact.",
            "Translate by merged evaluation unit when the provided JSON contains unit IDs; otherwise translate paragraph-by-paragraph.",
            "Return JSON only, with this shape: {\"paragraphs\":[{\"paragraph_id\":\"p001\",\"text\":\"...\"}]}",
            "Do not use markdown fences.",
            "Every requested paragraph_id or unit_id must appear exactly once.",
            "Do not add extra paragraph_id values.",
            "Keep paragraph order exactly as provided.",
            "Each returned text field must be one compact complete Vietnamese paragraph or merged unit.",
            "Do not force original micro paragraph breaks inside merged units.",
            "Use the per-unit target_max and strict_max values from the user JSON.",
            "Compression must rewrite complete Vietnamese sentences; never cut words to fit budget.",
            "Each paragraph fails validation if it exceeds strict_max after compression, has dangling brackets, or looks truncated.",
            "Required glossary mappings when the source term appears: "
            + json_dumps(
                [
                    {"source": source_term, "target": target_term}
                    for source_term, target_term in FIXED_GLOSSARY.items()
                ]
            ),
            "Return only the JSON object.",
        ]
    )


def _load_source_eval_run(project: str) -> Path | None:
    try:
        path = latest_run_dir(project)
    except ValueError:
        path = None
    if path and path.exists() and (path / "selected_samples.json").exists():
        return path
    candidates = sorted(
        eval_root().glob(f"{project}_eval_*"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "selected_samples.json").exists():
            return candidate
    return None


def freeze_stable_candidate(
    *,
    validation_root: Path,
    project: str,
    provider_key: str,
    model: str,
    source_eval_run: Path | None,
    settings: dict[str, Any],
) -> dict[str, Any]:
    prompt_text = stable_base_prompt_text()
    source_eval_run_id = None
    if source_eval_run and (source_eval_run / "selected_samples.json").exists():
        try:
            samples = read_selected_samples(source_eval_run)
            if samples:
                prompt_text = translation_system_prompt(
                    source_eval_run,
                    sample=samples[0],
                    prompt_iteration=ACTIVE_PROMPT_ITERATION,
                    paragraph_mode=True,
                )
                source_eval_run_id = source_eval_run.name
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            source_eval_run_id = source_eval_run.name
    prompt_hash = sha256_text(prompt_text)
    metadata = {
        "prompt_id": f"{project}_mvp48_candidate",
        "prompt_version": "mvp4.8.6-stable-candidate-v1",
        "source_eval_run_id": source_eval_run_id or "generated_without_prior_eval",
        "project": project,
        "model": model,
        "provider": provider_key,
        "prompt_sha256": prompt_hash,
        "prompt_iteration": ACTIVE_PROMPT_ITERATION,
        "glossary_rules": [
            {"source": source_term, "target": target_term}
            for source_term, target_term in FIXED_GLOSSARY.items()
        ],
        "paragraph_alignment_settings": {
            "enabled": settings.get("enable_paragraph_alignment"),
            "batch_size": PARAGRAPH_BATCH_SIZE,
            "target_min_ratio": PARAGRAPH_TARGET_MIN_RATIO,
            "target_max_ratio": PARAGRAPH_TARGET_MAX_RATIO,
            "strict_max_ratio": PARAGRAPH_STRICT_MAX_RATIO,
            "short_reference_strict_max_ratio": PARAGRAPH_SHORT_STRICT_MAX_RATIO,
            "short_reference_char_threshold": PARAGRAPH_SHORT_REFERENCE_CHARS,
        },
        "translation_unit_settings": {
            "merge_tiny_paragraphs": settings.get("merge_tiny_paragraphs", True),
            "tiny_paragraph_threshold": settings.get(
                "tiny_paragraph_threshold", TINY_PARAGRAPH_THRESHOLD
            ),
            "unit_target_min_chars": settings.get("unit_target_min_chars", UNIT_TARGET_MIN_CHARS),
            "unit_strict_max_ratio": UNIT_STRICT_MAX_RATIO,
            "unit_short_strict_max_ratio": UNIT_SHORT_STRICT_MAX_RATIO,
            "high_risk_source_chars": UNIT_HIGH_RISK_SOURCE_CHARS,
            "high_risk_target_max_chars": UNIT_HIGH_RISK_TARGET_MAX_CHARS,
            "combined_target_max_chars": UNIT_COMBINED_TARGET_MAX_CHARS,
        },
        "compression_settings": {
            "enabled": settings.get("enable_compression_pass"),
            "max_model_compression_attempts_per_paragraph": 2,
            "deterministic_budget_enforcement": False,
            "fixed_term_repair": True,
            "truncation_detection": True,
            "unsafe_hard_cut_rejected": True,
        },
        "evaluation_thresholds": {
            "pass_thresholds": PASS_THRESHOLDS,
            "sample_min_total_score": 75,
            "average_min_score": 80,
            "output_reference_ratio": [EVAL_LENGTH_RATIO_MIN, EVAL_LENGTH_RATIO_MAX],
            "alignment_quality_min": ALIGNMENT_QUALITY_THRESHOLD,
            "truncation_allowed": False,
        },
        "settings": settings,
        "created_at": utc_now(),
    }
    (validation_root / "candidate_prompt.md").write_text(
        "\n".join(
            [
                "# MVP4.8 Candidate Prompt",
                "",
                f"Prompt SHA-256: `{prompt_hash}`",
                "",
                "```text",
                prompt_text,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(validation_root / "candidate_prompt_metadata.json", metadata)
    return {"prompt_text": prompt_text, "metadata": metadata}


def stable_sample_offsets(stable_run_count: int) -> list[float]:
    if stable_run_count <= 1:
        return [0.0]
    return [round(index / (stable_run_count + 1), 3) for index in range(stable_run_count)]


def stable_alignment_failure_report(
    *,
    project: str,
    model: str,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    per_sample = []
    for sample in samples:
        per_sample.append(
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "total_score": 0,
                "meaning_accuracy": 0,
                "omission_addition": 0,
                "terminology_consistency": 0,
                "pronoun_name_consistency": 0,
                "vietnamese_fluency": 0,
                "style_match": 0,
                "formatting_preservation": 0,
                "pass": False,
                "output_char_count": 0,
                "reference_char_count": sample["target_char_count"],
                "output_reference_ratio": 0,
                "paragraph_count_output": 0,
                "paragraph_count_reference": sample["paragraph_count_target"],
                "paragraph_count_source": sample["paragraph_count_source"],
                "retry_triggered": False,
                "compression_count": 0,
                "global_ratio_before_compression": None,
                "global_ratio_after_compression": 0,
                "per_paragraph_length_table": [],
                "paragraph_validation": None,
                "overlong_paragraph_ids": [],
                "truncated_paragraphs": [],
                "length_penalty_reason": "alignment_quality_below_threshold",
                "terminology_mismatches": [],
                "gates": {
                    "severe_hallucination": False,
                    "wrong_main_character_name": False,
                    "major_skipped_passage": False,
                    "length_in_range": False,
                },
                "alignment_quality": sample.get("alignment_quality"),
                "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
                "verification_reasons": ["alignment_quality_below_threshold"],
                "final_pass_fail_reason": "insufficient_reliable_alignment",
                "notes": {
                    "heuristic_only": True,
                    "alignment_warnings": sample.get("alignment_warnings", []),
                },
            }
        )
    return {
        "project": project,
        "chapter": 1,
        "sample_count": len(samples),
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "source_char_count": sample["source_char_count"],
                "target_char_count": sample["target_char_count"],
                "alignment_quality": sample.get("alignment_quality"),
                "alignment_warnings": sample.get("alignment_warnings", []),
                "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
            }
            for sample in samples
        ],
        "score_weights": SCORE_WEIGHTS,
        "pass_thresholds": PASS_THRESHOLDS,
        "models": {
            model: {
                "average_score": 0,
                "sample_count": len(per_sample),
                "pass": False,
                "ratio_compliant_samples": 0,
                "compression_count": 0,
                "retry_triggered": False,
                "samples": per_sample,
                "final_pass_fail_reason": "insufficient_reliable_alignment",
            }
        },
        "best_model": model,
        "pass": False,
        "prompt_iteration": ACTIVE_PROMPT_ITERATION,
        "enable_paragraph_alignment": True,
        "enable_compression_pass": False,
        "compression_count": 0,
    }


def run_candidate_validation_once(
    *,
    project: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    max_chapters: int,
    sample_count: int,
    max_source_chars: int,
    max_target_chars: int,
    sample_start_ratio: float,
    enable_paragraph_alignment: bool,
    enable_compression_pass: bool,
    merge_tiny_paragraphs: bool,
    tiny_paragraph_threshold: int,
    unit_target_min_chars: int,
    stable_prompt_text: str,
    validation_index: int,
    run_retry_no: int = 0,
    provider_retry_attempts: int = DEFAULT_PROVIDER_RETRY_ATTEMPTS,
    provider_retry_backoff_seconds: float = DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    prepared = prepare_parallel(
        project=project,
        raw_path=raw_path,
        translated_path=translated_path,
        max_chapters=max_chapters,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
        sample_start_ratio=sample_start_ratio,
        sample_count=sample_count,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
    )
    run_dir = Path(prepared["run_dir"])
    if any(
        sample.get("accepted_for_stable_validation") is False
        for sample in prepared["selected_samples"]
    ):
        style = build_style_profile(
            run_dir,
            chapters=1,
            max_source_chars=min(DEFAULT_LIMITS["style_learning_max_source_chars"], max_source_chars),
            max_target_chars=min(DEFAULT_LIMITS["style_learning_max_target_chars"], max_target_chars),
        )
        report = stable_alignment_failure_report(
            project=project,
            model=model,
            samples=prepared["selected_samples"],
        )
        write_json(run_dir / "evaluation_report.json", report)
        _write_eval_markdown(run_dir, report)
        write_prompt_iteration_log(
            run_dir,
            iteration=ACTIVE_PROMPT_ITERATION,
            change="MVP4.8.6 strict stable validation rejected unreliable paragraph alignment.",
            why="Stable prompt validation cannot use samples with low source/reference alignment quality.",
            report=report,
        )
        return {
            "validation_index": validation_index,
            "run_retry_no": run_retry_no,
            "run_dir": str(run_dir),
            "sample_start_ratio": sample_start_ratio,
            "selected_samples": [
                {
                    "sample_id": sample["sample_id"],
                    "chapter_id": sample["chapter_id"],
                    "source_char_count": sample["source_char_count"],
                    "target_char_count": sample["target_char_count"],
                    "paragraph_pair_count": len(sample.get("paragraph_pairs", [])),
                    "translation_unit_count": len(sample.get("translation_units", [])),
                    "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
                    "warnings": sample.get("paragraph_alignment_warnings", []),
                    "alignment_quality": sample.get("alignment_quality"),
                    "alignment_warnings": sample.get("alignment_warnings", []),
                    "accepted_for_stable_validation": sample.get(
                        "accepted_for_stable_validation"
                    ),
                }
                for sample in prepared["selected_samples"]
            ],
            "style_profile": style["style_profile"],
            "translations": {},
            "provider_retry_summary": summarize_provider_retry_entries([]),
            "provider_retry_log": None,
            "report": report,
            "candidate_prompt_sha256": sha256_text(stable_prompt_text),
            "alignment_gate_failed": True,
        }
    style = build_style_profile(
        run_dir,
        chapters=1,
        max_source_chars=min(DEFAULT_LIMITS["style_learning_max_source_chars"], max_source_chars),
        max_target_chars=min(DEFAULT_LIMITS["style_learning_max_target_chars"], max_target_chars),
    )
    translated = translate_samples(
        project=project,
        provider_key=provider_key,
        models=[model],
        max_source_chars=max_source_chars,
        enable_length_retry=True,
        target_length_tolerance=0.2,
        enable_paragraph_alignment=enable_paragraph_alignment,
        enable_compression_pass=enable_compression_pass,
        merge_tiny_paragraphs=merge_tiny_paragraphs,
        tiny_paragraph_threshold=tiny_paragraph_threshold,
        unit_target_min_chars=unit_target_min_chars,
        prompt_iteration=ACTIVE_PROMPT_ITERATION,
        stable_prompt_text=stable_prompt_text,
        provider_retry_attempts=provider_retry_attempts,
        provider_retry_backoff_seconds=provider_retry_backoff_seconds,
        validation_index=validation_index,
        run_retry_no=run_retry_no,
    )
    compared = compare_translation(
        project=project,
        chapter=1,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
    )
    write_prompt_iteration_log(
        run_dir,
        iteration=ACTIVE_PROMPT_ITERATION,
        change="MVP4.8 stable frozen candidate prompt validation.",
        why="Validate the frozen MVP4.7 candidate without prompt changes across consecutive runs.",
        report=compared["report"],
    )
    return {
        "validation_index": validation_index,
        "run_retry_no": run_retry_no,
        "run_dir": str(run_dir),
        "sample_start_ratio": sample_start_ratio,
        "selected_samples": [
            {
                "sample_id": sample["sample_id"],
                "chapter_id": sample["chapter_id"],
                "source_char_count": sample["source_char_count"],
                "target_char_count": sample["target_char_count"],
                "paragraph_pair_count": len(sample.get("paragraph_pairs", [])),
                "translation_unit_count": len(sample.get("translation_units", [])),
                "translation_unit_merge_count": sample.get("translation_unit_merge_count", 0),
                "warnings": sample.get("paragraph_alignment_warnings", []),
                "alignment_quality": sample.get("alignment_quality"),
                "alignment_warnings": sample.get("alignment_warnings", []),
                "accepted_for_stable_validation": sample.get("accepted_for_stable_validation"),
            }
            for sample in prepared["selected_samples"]
        ],
        "style_profile": style["style_profile"],
        "translations": translated["outputs"],
        "provider_retry_summary": translated.get("provider_retry_summary"),
        "provider_retry_log": translated.get("provider_retry_log"),
        "report": compared["report"],
        "candidate_prompt_sha256": sha256_text(stable_prompt_text),
    }


def stable_gate_result(
    *,
    validation_runs: list[dict[str, Any]],
    selected_model: str,
    expected_prompt_sha256: str,
) -> dict[str, Any]:
    reasons = []
    per_run_scores = []
    per_sample_scores = []
    total_scores = []
    compression_counts = []
    ratios = []
    prompt_hashes = [run.get("candidate_prompt_sha256") for run in validation_runs]
    if any(prompt_hash != expected_prompt_sha256 for prompt_hash in prompt_hashes):
        reasons.append("candidate_prompt_changed_across_runs")
    for run in validation_runs:
        report = run["report"]
        model_report = report.get("models", {}).get(selected_model)
        if not model_report:
            reasons.append(f"run_{run['validation_index']}:selected_model_missing")
            continue
        average_score = float(model_report.get("average_score", 0))
        total_scores.append(average_score)
        compression_counts.append(int(model_report.get("compression_count") or 0))
        run_reasons = []
        if run.get("alignment_gate_failed"):
            run_reasons.append("insufficient_reliable_alignment")
        for selected_sample in run.get("selected_samples", []):
            if selected_sample.get("accepted_for_stable_validation") is False:
                run_reasons.append(
                    f"{selected_sample.get('sample_id')}:alignment_quality_below_threshold"
                )
        if model_report.get("pass") is not True:
            run_reasons.append("model_report_not_pass")
        if average_score < 80:
            run_reasons.append("average_score_below_80")
        for sample in model_report.get("samples", []):
            ratio = float(sample.get("output_reference_ratio") or 0)
            ratios.append(ratio)
            sample_reasons = []
            ratio_justification = None
            if sample.get("pass") is not True:
                sample_reasons.append("evaluator_sample_not_pass")
            if sample.get("total_score", 0) < 75:
                sample_reasons.append("sample_score_below_75")
            if sample.get("accepted_for_stable_validation") is False:
                sample_reasons.append("alignment_quality_below_threshold")
            verification_reasons = sample.get("verification_reasons", []) or []
            if "meaning_accuracy_below_threshold" in sample.get("final_pass_fail_reason", ""):
                sample_reasons.append("meaning_accuracy_below_threshold")
            if "paragraph_truncation_detected" in verification_reasons or sample.get(
                "truncated_paragraphs"
            ):
                sample_reasons.append("paragraph_truncation_detected")
            if "unsafe_compression" in verification_reasons:
                sample_reasons.append("unsafe_compression")
            if "alignment_quality_below_threshold" in verification_reasons:
                sample_reasons.append("alignment_quality_below_threshold")
            if "unit_alignment_quality_below_threshold" in verification_reasons or sample.get(
                "low_alignment_units"
            ):
                sample_reasons.append("unit_alignment_quality_below_threshold")
            style_drift = sample.get("style_drift") or {}
            if not style_drift.get("ignored_for_gate") and (
                style_drift.get("above_threshold")
                or (
                    sample.get("style_drift_score") is not None
                    and sample.get("style_drift_score") >= STYLE_DRIFT_WARNING_THRESHOLD
                )
            ):
                sample_reasons.append("style_drift_above_threshold")
            gates = sample.get("gates", {})
            if gates.get("severe_hallucination"):
                sample_reasons.append("severe_hallucination")
            if gates.get("wrong_main_character_name"):
                sample_reasons.append("wrong_main_character_name")
            if gates.get("major_skipped_passage"):
                sample_reasons.append("major_skipped_passage")
            if sample.get("terminology_mismatches"):
                sample_reasons.append("serious_terminology_mismatch")
            if not (EVAL_LENGTH_RATIO_MIN <= ratio <= EVAL_LENGTH_RATIO_MAX):
                can_justify_low_ratio = (
                    EVAL_LENGTH_RATIO_MIN - 0.01 <= ratio < EVAL_LENGTH_RATIO_MIN
                    and sample.get("total_score", 0) >= 75
                    and not gates.get("severe_hallucination")
                    and not gates.get("wrong_main_character_name")
                    and not gates.get("major_skipped_passage")
                    and not sample.get("terminology_mismatches")
                )
                if can_justify_low_ratio:
                    ratio_justification = (
                        "Within 0.01 below the lower ratio bound after strict per-paragraph "
                        "budget enforcement; accepted as explicitly justified."
                    )
                else:
                    sample_reasons.append("output_reference_ratio_outside_range")
            per_sample_scores.append(
                {
                    "validation_index": run["validation_index"],
                    "sample_id": sample.get("sample_id"),
                    "total_score": sample.get("total_score"),
                    "pass": not sample_reasons,
                    "evaluator_sample_pass": sample.get("pass"),
                    "output_reference_ratio": ratio,
                    "compression_count": sample.get("compression_count", 0),
                    "ratio_justification": ratio_justification,
                    "verification_reasons": verification_reasons,
                    "truncated_paragraphs": sample.get("truncated_paragraphs", []),
                    "alignment_quality": sample.get("alignment_quality"),
                    "accepted_for_stable_validation": sample.get(
                        "accepted_for_stable_validation"
                    ),
                    "provider_error": sample.get("provider_error"),
                    "provider_error_classification": sample.get(
                        "provider_error_classification"
                    ),
                    "selected_final_output": sample.get("selected_final_output"),
                    "selected_final_output_reason": sample.get(
                        "selected_final_output_reason"
                    ),
                    "style_drift_score": sample.get("style_drift_score"),
                    "style_drift_warnings": (sample.get("style_drift") or {}).get(
                        "warnings",
                        [],
                    ),
                    "human_review_recommended": sample.get(
                        "human_review_recommended",
                        False,
                    ),
                    "reasons": sorted(set(sample_reasons)),
                }
            )
            run_reasons.extend(
                f"{sample.get('sample_id')}:{reason}" for reason in sample_reasons
            )
        per_run_pass = not run_reasons
        per_run_scores.append(
            {
                "validation_index": run["validation_index"],
                "run_dir": run["run_dir"],
                "average_score": average_score,
                "pass": per_run_pass,
                "reasons": sorted(set(run_reasons)),
            }
        )
        reasons.extend(f"run_{run['validation_index']}:{reason}" for reason in run_reasons)
    overall_average = round(sum(total_scores) / max(len(total_scores), 1), 2)
    if overall_average < 80:
        reasons.append("overall_average_below_80")
    ratio_summary = {
        "min": round(min(ratios), 3) if ratios else None,
        "max": round(max(ratios), 3) if ratios else None,
        "average": round(sum(ratios) / len(ratios), 3) if ratios else None,
    }
    return {
        "pass": not reasons,
        "selected_model": selected_model,
        "overall_average_score": overall_average,
        "per_run_scores": per_run_scores,
        "per_sample_scores": per_sample_scores,
        "compression_counts": compression_counts,
        "ratio_summary": ratio_summary,
        "prompt_hashes": prompt_hashes,
        "reasons": sorted(set(reasons)),
    }


def validation_run_failed_only_retryable_provider(
    run: dict[str, Any],
    *,
    selected_model: str,
) -> bool:
    model_report = run.get("report", {}).get("models", {}).get(selected_model, {})
    failed_samples = [
        sample for sample in model_report.get("samples", []) if sample.get("pass") is not True
    ]
    if not failed_samples:
        return False
    translations = run.get("translations", {})
    for sample in failed_samples:
        sample_id = sample.get("sample_id")
        metadata = (
            translations.get(sample_id, {}).get(selected_model, {})
            if isinstance(translations, dict)
            else {}
        )
        classification = metadata.get("provider_error_classification") or sample.get(
            "provider_error_classification"
        )
        if not metadata.get("provider_error") or not classification:
            return False
        if classification.get("retryable") is not True:
            return False
        verification_reasons = set(sample.get("verification_reasons", []) or [])
        non_provider_reasons = verification_reasons - PROVIDER_ONLY_FAILURE_REASONS
        tolerated_empty_side_effects = {
            "model_output_failed_paragraph_json_validation",
            "rendered_paragraph_count_mismatch",
            "global_ratio_outside_range",
            "terminology_mismatch",
        }
        if non_provider_reasons - tolerated_empty_side_effects:
            return False
    return True


def read_provider_retry_entries(run: dict[str, Any]) -> list[dict[str, Any]]:
    path_value = run.get("provider_retry_log")
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    entries = payload.get("entries", [])
    return entries if isinstance(entries, list) else []


def aggregate_provider_retry_summary(
    validation_runs: list[dict[str, Any]],
    run_retry_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    entries = list(run_retry_entries)
    for run in validation_runs:
        entries.extend(read_provider_retry_entries(run))
    summary = summarize_provider_retry_entries(entries)
    final_provider_failure_count = 0
    for run in validation_runs:
        selected_model = run.get("report", {}).get("best_model")
        if not selected_model:
            models = run.get("report", {}).get("models", {})
            selected_model = next(iter(models), None)
        for by_model in (run.get("translations", {}) or {}).values():
            if not isinstance(by_model, dict):
                continue
            metadata = by_model.get(selected_model)
            if isinstance(metadata, dict) and metadata.get("provider_error"):
                final_provider_failure_count += 1
    summary["final_provider_failure_count"] = final_provider_failure_count
    return {**summary, "entries": entries}


def _load_structured_paragraphs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        str(item.get("paragraph_id")): str(item.get("text", ""))
        for item in payload.get("paragraphs", [])
        if isinstance(item, dict)
    }


def _snippet(text: str, limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 3].rstrip() + "..."


def write_cached_eval_exports(
    *,
    validation_root: Path,
    validation_runs: list[dict[str, Any]],
    selected_model: str,
) -> dict[str, Any]:
    replay_rows = []
    safe_model = safe_model_name(selected_model)
    for run in validation_runs:
        run_dir = Path(run["run_dir"])
        samples = read_selected_samples(run_dir)
        glossary = (
            read_json(run_dir / "glossary_candidates.json")
            if (run_dir / "glossary_candidates.json").exists()
            else {}
        )
        report = run["report"]
        model_report = report.get("models", {}).get(selected_model, {})
        score_lookup = {
            score.get("sample_id"): score
            for score in model_report.get("samples", [])
        }
        for sample in samples:
            sample_id = sample["sample_id"]
            sample_metadata = (
                run.get("translations", {})
                .get(sample_id, {})
                .get(selected_model, {})
                if isinstance(run.get("translations", {}), dict)
                else {}
            )
            provider_error = sample_metadata.get("provider_error")
            provider_error_classification = sample_metadata.get(
                "provider_error_classification"
            )
            provider_failure_empty_output = bool(
                (
                    sample_metadata.get("verification_after_compression", {}) or {}
                ).get("provider_failure_empty_output")
            )
            initial_lookup = _load_structured_paragraphs(
                run_dir / "translation_outputs" / sample_id / f"{safe_model}_structured_initial.json"
            )
            after_compression_lookup = _load_structured_paragraphs(
                run_dir
                / "translation_outputs"
                / sample_id
                / f"{safe_model}_structured_after_compression.json"
            )
            final_lookup = _load_structured_paragraphs(
                run_dir / "translation_outputs" / sample_id / f"{safe_model}_structured_final.json"
            )
            sample_score = score_lookup.get(sample_id, {})
            score_notes = sample_score.get("notes", {})
            for pair in active_eval_pairs(sample):
                paragraph_id = pair["paragraph_id"]
                before = initial_lookup.get(paragraph_id, "")
                after_candidate = after_compression_lookup.get(
                    paragraph_id,
                    final_lookup.get(paragraph_id, ""),
                )
                selected_final = final_lookup.get(paragraph_id, after_candidate)
                truncation = detect_truncated_vietnamese(
                    selected_final,
                    source_text=pair.get("source_text"),
                    strict_max=pair.get("strict_max"),
                )
                if provider_error and provider_failure_empty_output and not selected_final.strip():
                    truncation = {
                        "is_truncated": False,
                        "reasons": ["provider_failure_empty_output"],
                    }
                required_terms = required_terms_for_pair(pair, glossary)
                missing_terms = required_terms_missing(pair, selected_final, glossary)
                replay_rows.append(
                    {
                        "validation_index": run["validation_index"],
                        "run_dir": str(run_dir),
                        "sample_id": sample_id,
                        "chapter_id": sample["chapter_id"],
                        "paragraph_id": paragraph_id,
                        "unit_id": pair.get("unit_id", paragraph_id),
                        "source_paragraph_ids": pair.get("source_paragraph_ids", [paragraph_id]),
                        "target_paragraph_ids": pair.get("target_paragraph_ids", [paragraph_id]),
                        "unit_type": pair.get("unit_type"),
                        "merge_reason": pair.get("merge_reason"),
                        "is_merged_unit": pair.get("is_merged_unit", False),
                        "source_paragraph_indexes": pair.get("source_paragraph_indexes"),
                        "target_paragraph_indexes": pair.get("target_paragraph_indexes"),
                        "source_paragraph": pair.get("source_text", ""),
                        "human_reference_paragraph": pair.get("target_text", ""),
                        "model_paragraph_before_compression": before,
                        "model_paragraph_after_compression": after_candidate,
                        "model_paragraph_selected_final": selected_final,
                        "selected_final_output": sample_metadata.get("selected_final_output"),
                        "selected_final_output_reason": sample_metadata.get(
                            "selected_final_output_reason"
                        ),
                        "style_drift": sample_metadata.get("style_drift"),
                        "style_drift_score": (sample_metadata.get("style_drift") or {}).get(
                            "score"
                        ),
                        "human_review_recommended": sample_metadata.get(
                            "human_review_recommended",
                            False,
                        ),
                        "reference_char_count": pair.get("target_char_count", 0),
                        "before_char_count": len(before),
                        "after_char_count": len(after_candidate),
                        "selected_char_count": len(selected_final),
                        "ratio_before": round(len(before) / max(pair.get("target_char_count", 0), 1), 3),
                        "ratio_after": round(len(after_candidate) / max(pair.get("target_char_count", 0), 1), 3),
                        "ratio_selected": round(
                            len(selected_final) / max(pair.get("target_char_count", 0), 1),
                            3,
                        ),
                        "strict_max": pair.get("strict_max"),
                        "budget_policy_used": pair.get("budget_policy_used"),
                        "paragraph_allowed_over_budget_reason": next(
                            (
                                item.get("reason")
                                for item in sample_score.get(
                                    "allowed_over_budget_paragraphs", []
                                )
                                if item.get("paragraph_id") == paragraph_id
                            ),
                            None,
                        ),
                        "truncation_detected": truncation["is_truncated"],
                        "truncation_reasons": truncation["reasons"],
                        "provider_error": provider_error,
                        "provider_error_classification": provider_error_classification,
                        "provider_failure_empty_output": provider_failure_empty_output,
                        "required_terms": required_terms,
                        "missing_terms": missing_terms,
                        "alignment_quality": sample.get("alignment_quality"),
                        "alignment_warnings": sample.get("alignment_warnings", []),
                        "accepted_for_stable_validation": sample.get(
                            "accepted_for_stable_validation"
                        ),
                        "score_notes": score_notes,
                        "sample_score": {
                            "total_score": sample_score.get("total_score"),
                            "pass": sample_score.get("pass"),
                            "reason": sample_score.get("final_pass_fail_reason"),
                        },
                        "warnings": sample.get("paragraph_alignment_warnings", []),
                    }
                )
    replay = {
        "schema_version": "cached_eval_replay_v1",
        "selected_model": selected_model,
        "row_count": len(replay_rows),
        "rows": replay_rows,
    }
    write_json(validation_root / "cached_eval_replay.json", replay)

    review_lines = ["# Human Review Samples", ""]
    for row in replay_rows:
        review_lines.extend(
            [
                f"## Run {row['validation_index']} / {row['sample_id']} / {row['paragraph_id']}",
                "",
                f"- Unit type: `{row.get('unit_type')}`",
                f"- Source paragraph IDs: `{json_dumps(row.get('source_paragraph_ids', []))}`",
                f"- Merge reason: `{row.get('merge_reason')}`",
                f"- Ratio before: `{row['ratio_before']}`",
                f"- Ratio after: `{row['ratio_after']}`",
                f"- Ratio selected: `{row.get('ratio_selected')}`",
                f"- Selected final output: `{row.get('selected_final_output')}`",
                f"- Selection reason: `{row.get('selected_final_output_reason')}`",
                f"- Style drift score: `{row.get('style_drift_score')}`",
                f"- Human review recommended: `{row.get('human_review_recommended')}`",
                f"- Score reason: `{row['sample_score'].get('reason')}`",
                f"- Truncation detected: `{row['truncation_detected']}`",
                f"- Truncation reasons: `{json_dumps(row['truncation_reasons'])}`",
                f"- Budget policy: `{row.get('budget_policy_used')}`",
                f"- Allowed over budget: `{row.get('paragraph_allowed_over_budget_reason')}`",
                f"- Alignment quality: `{row.get('alignment_quality')}`",
                f"- Eligible for stable validation: `{row.get('accepted_for_stable_validation')}`",
                "",
                "Source:",
                "",
                row["source_paragraph"],
                "",
                "Human reference:",
                "",
                row["human_reference_paragraph"],
                "",
                "Model before compression:",
                "",
                row["model_paragraph_before_compression"],
                "",
                "Model after compression:",
                "",
                row["model_paragraph_after_compression"],
                "",
                "Selected final output:",
                "",
                row["model_paragraph_selected_final"],
                "",
            ]
        )
    (validation_root / "human_review_samples.md").write_text(
        "\n".join(review_lines).rstrip() + "\n",
        encoding="utf-8",
    )

    table_lines = [
        "# Paragraph Review Table",
        "",
        "| Run | Sample | Unit | Original IDs | Ref Chars | Before | After | Selected | Ratio Before | Ratio After | Ratio Selected | Selected Output | Style Drift | Budget | Merge Reason | Allowed Over Budget | Truncated | Reasons | Align Quality | Eligible | Warnings | Source | Reference | Selected Final |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|---|---|---:|---|---|---|---|---|",
    ]
    for row in replay_rows:
        table_lines.append(
            "| "
            + " | ".join(
                [
                    str(row["validation_index"]),
                    row["sample_id"],
                    row["paragraph_id"],
                    _snippet(",".join(row.get("source_paragraph_ids", [])), 80).replace("|", "\\|"),
                    str(row["reference_char_count"]),
                    str(row["before_char_count"]),
                    str(row["after_char_count"]),
                    str(row.get("selected_char_count")),
                    str(row["ratio_before"]),
                    str(row["ratio_after"]),
                    str(row.get("ratio_selected")),
                    str(row.get("selected_final_output")),
                    str(row.get("style_drift_score")),
                    str(row.get("budget_policy_used")),
                    str(row.get("merge_reason")),
                    str(row.get("paragraph_allowed_over_budget_reason")),
                    str(row["truncation_detected"]),
                    _snippet("; ".join(row["truncation_reasons"]), 80).replace("|", "\\|"),
                    str(row.get("alignment_quality")),
                    str(row.get("accepted_for_stable_validation")),
                    _snippet("; ".join(row["warnings"]), 80).replace("|", "\\|"),
                    _snippet(row["source_paragraph"], 120).replace("|", "\\|"),
                    _snippet(row["human_reference_paragraph"], 120).replace("|", "\\|"),
                    _snippet(row["model_paragraph_selected_final"], 120).replace("|", "\\|"),
                ]
            )
            + " |"
        )
    (validation_root / "paragraph_review_table.md").write_text(
        "\n".join(table_lines) + "\n",
        encoding="utf-8",
    )
    return {
        "cached_eval_replay": str(validation_root / "cached_eval_replay.json"),
        "human_review_samples": str(validation_root / "human_review_samples.md"),
        "paragraph_review_table": str(validation_root / "paragraph_review_table.md"),
        "row_count": len(replay_rows),
    }


def write_stable_decision_outputs(
    *,
    validation_root: Path,
    candidate: dict[str, Any],
    validation_runs: list[dict[str, Any]],
    gate: dict[str, Any],
    provider_key: str,
    model: str,
) -> dict[str, Any]:
    if gate["pass"]:
        prompt_path = validation_root / "stable_prompt.md"
        prompt_path.write_text(
            (validation_root / "candidate_prompt.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        metadata = {
            "prompt_id": candidate["metadata"]["prompt_id"],
            "prompt_version": candidate["metadata"]["prompt_version"],
            "source_eval_run_id": candidate["metadata"]["source_eval_run_id"],
            "model": model,
            "provider": provider_key,
            "validation_runs": [
                {
                    "validation_index": run["validation_index"],
                    "run_retry_no": run.get("run_retry_no", 0),
                    "run_dir": run["run_dir"],
                    "sample_start_ratio": run["sample_start_ratio"],
                    "candidate_prompt_sha256": run["candidate_prompt_sha256"],
                }
                for run in validation_runs
            ],
            "per_run_scores": gate["per_run_scores"],
            "per_sample_scores": gate["per_sample_scores"],
            "average_score": gate["overall_average_score"],
            "compression_counts": gate["compression_counts"],
            "ratio_summary": gate["ratio_summary"],
            "created_at": utc_now(),
            "quality_gate": "pass",
        }
        write_json(validation_root / "stable_prompt_metadata.json", metadata)
        return {
            "stable_prompt_created": True,
            "stable_prompt_path": str(prompt_path),
            "stable_prompt_metadata_path": str(validation_root / "stable_prompt_metadata.json"),
        }
    prompt_path = validation_root / "stable_prompt.md"
    if prompt_path.exists():
        prompt_path.unlink()
    lines = [
        "# Stable Candidate Failure Report",
        "",
        f"Selected model: `{model}`",
        f"Provider: `{provider_key}`",
        f"Overall average: `{gate['overall_average_score']}`",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in gate["reasons"])
    lines.extend(["", "## Per Run", ""])
    for run_score in gate["per_run_scores"]:
        lines.append(
            f"- Run {run_score['validation_index']}: average={run_score['average_score']}, "
            f"pass={run_score['pass']}, reasons={json_dumps(run_score['reasons'])}"
        )
    (validation_root / "stable_candidate_failure_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return {
        "stable_prompt_created": False,
        "failure_report_path": str(validation_root / "stable_candidate_failure_report.md"),
    }


def _review_run_argument(validation_root: Path) -> str:
    try:
        return str(validation_root.resolve().relative_to(repo_root().resolve())).replace("\\", "/")
    except ValueError:
        return str(validation_root)


def _human_review_status(report: dict[str, Any]) -> str:
    if report.get("pass") and report.get("decision_outputs", {}).get("stable_prompt_created"):
        return "READY FOR HUMAN REVIEW"
    return "NOT APPROVABLE"


def write_final_human_review_package(
    *,
    validation_root: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    review_root = validation_root / "human_review_final"
    review_root.mkdir(parents=True, exist_ok=True)
    replay_path = validation_root / "cached_eval_replay.json"
    replay_report_path = validation_root / "replay_report.json"
    replay = read_json(replay_path) if replay_path.exists() else {"rows": []}
    replay_report = read_json(replay_report_path) if replay_report_path.exists() else {}
    rows = replay.get("rows", []) if isinstance(replay.get("rows"), list) else []
    counts = _stable_validation_counts(report)
    retry_summary = report.get("provider_retry_summary", {})
    status = _human_review_status(report)
    run_arg = _review_run_argument(validation_root)
    sample_lookup: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        sample_lookup.setdefault(
            (int(row.get("validation_index") or 0), str(row.get("sample_id") or "")),
            [],
        ).append(row)

    sample_scores = {
        (int(item.get("validation_index") or 0), str(item.get("sample_id") or "")): item
        for item in report.get("gate", {}).get("per_sample_scores", [])
    }
    payload = {
        "schema_version": "human_review_final_v1",
        "project": report.get("project"),
        "provider": report.get("provider"),
        "model": report.get("model"),
        "prompt_version": (
            read_json(validation_root / "candidate_prompt_metadata.json").get("prompt_version")
            if (validation_root / "candidate_prompt_metadata.json").exists()
            else None
        ),
        "validation_status": status,
        "quality_gate": report.get("quality_gate"),
        "stable_prompt_created": bool(
            report.get("decision_outputs", {}).get("stable_prompt_created")
        ),
        "run_scores": report.get("gate", {}).get("per_run_scores", []),
        "sample_scores": report.get("gate", {}).get("per_sample_scores", []),
        "retry_summary": retry_summary,
        "compression_summary": counts.get("compression_attempt_summary", {}),
        "ratio_summary": report.get("gate", {}).get("ratio_summary", {}),
        "truncation_count": counts.get("truncation_count", 0),
        "unsafe_compression_count": counts.get("unsafe_compression_count", 0),
        "provider_failure_count": retry_summary.get(
            "final_provider_failure_count",
            len(counts.get("provider_failures", [])),
        ),
        "final_pass_fail_reason": "pass"
        if report.get("pass")
        else "; ".join(report.get("gate", {}).get("reasons", [])),
        "samples": [],
        "created_at": utc_now(),
    }

    for (validation_index, sample_id), sample_rows in sorted(sample_lookup.items()):
        score = sample_scores.get((validation_index, sample_id), {})
        payload["samples"].append(
            {
                "validation_index": validation_index,
                "sample_id": sample_id,
                "score": score,
                "source_excerpt": "\n\n".join(
                    row.get("source_paragraph", "") for row in sample_rows
                ),
                "human_reference_excerpt": "\n\n".join(
                    row.get("human_reference_paragraph", "") for row in sample_rows
                ),
                "model_before_compression": "\n\n".join(
                    row.get("model_paragraph_before_compression", "") for row in sample_rows
                ),
                "model_final_output": "\n\n".join(
                    row.get(
                        "model_paragraph_selected_final",
                        row.get("model_paragraph_after_compression", ""),
                    )
                    for row in sample_rows
                ),
                "selected_final_output": sample_rows[0].get("selected_final_output")
                if sample_rows
                else None,
                "selected_final_output_reason": sample_rows[0].get(
                    "selected_final_output_reason"
                )
                if sample_rows
                else None,
                "style_drift_score": sample_rows[0].get("style_drift_score")
                if sample_rows
                else None,
                "human_review_recommended": any(
                    row.get("human_review_recommended") for row in sample_rows
                ),
                "warnings": sorted(
                    {
                        warning
                        for row in sample_rows
                        for warning in row.get("warnings", []) or []
                    }
                ),
                "units": [
                    {
                        "unit_id": row.get("unit_id") or row.get("paragraph_id"),
                        "source_paragraph_ids": row.get("source_paragraph_ids", []),
                        "target_paragraph_ids": row.get("target_paragraph_ids", []),
                        "source_text": row.get("source_paragraph", ""),
                        "human_reference_text": row.get("human_reference_paragraph", ""),
                        "model_before_compression_text": row.get(
                            "model_paragraph_before_compression",
                            "",
                        ),
                        "model_after_compression_text": row.get(
                            "model_paragraph_after_compression",
                            "",
                        ),
                        "model_final_text": row.get(
                            "model_paragraph_selected_final",
                            row.get("model_paragraph_after_compression", ""),
                        ),
                        "selected_final_output": row.get("selected_final_output"),
                        "selection_reason": row.get("selected_final_output_reason"),
                        "style_drift_score": row.get("style_drift_score"),
                        "human_review_recommended": row.get(
                            "human_review_recommended",
                            False,
                        ),
                        "required_terms": row.get("required_terms", []),
                        "missing_terms": row.get("missing_terms", []),
                        "compression_attempts": row.get("compression_attempts", []),
                        "safety_status": "fail"
                        if row.get("truncation_detected")
                        or row.get("missing_terms")
                        or row.get("provider_error")
                        else "pass",
                        "alignment_quality": row.get("alignment_quality"),
                        "pass_fail_reason": "; ".join(
                            row.get("truncation_reasons", []) or []
                        )
                        or (
                            "provider_failure"
                            if row.get("provider_error")
                            else "missing_terms"
                            if row.get("missing_terms")
                            else "pass"
                        ),
                    }
                    for row in sample_rows
                ],
            }
        )
    write_json(review_root / "human_review_final.json", payload)

    lines = [
        "# Human Review Final",
        "",
        f"Status: **{status}**",
        "",
        f"- Project: `{payload['project']}`",
        f"- Provider/model: `{payload['provider']}` / `{payload['model']}`",
        f"- Prompt version: `{payload.get('prompt_version')}`",
        f"- Quality gate: `{payload['quality_gate']}`",
        f"- Stable prompt created: `{payload['stable_prompt_created']}`",
        f"- Run scores: `{json_dumps(payload['run_scores'])}`",
        f"- Sample scores: `{json_dumps(payload['sample_scores'])}`",
        f"- Retry summary: `{json_dumps(retry_summary)}`",
        f"- Compression summary: `{json_dumps(payload['compression_summary'])}`",
        f"- Ratio summary: `{json_dumps(payload['ratio_summary'])}`",
        f"- Truncation count: `{payload['truncation_count']}`",
        f"- Unsafe compression count: `{payload['unsafe_compression_count']}`",
        f"- Provider failure count: `{payload['provider_failure_count']}`",
        f"- Final reason: `{payload['final_pass_fail_reason']}`",
        "",
    ]
    for sample in payload["samples"]:
        score = sample.get("score", {})
        lines.extend(
            [
                f"## Run {sample['validation_index']} / {sample['sample_id']}",
                "",
                f"- Score: `{score.get('total_score')}`",
                f"- Ratio: `{score.get('output_reference_ratio')}`",
                f"- Selected final output: `{sample.get('selected_final_output')}`",
                f"- Selection reason: `{sample.get('selected_final_output_reason')}`",
                f"- Style drift score: `{sample.get('style_drift_score')}`",
                f"- Human review recommended: `{sample.get('human_review_recommended')}`",
                f"- Warnings: `{json_dumps(sample.get('warnings', []))}`",
                "- Reviewer decision: APPROVE / REJECT / NEEDS_EDIT",
                "- Reviewer notes:",
                "",
                "Source Chinese excerpt:",
                "",
                sample["source_excerpt"],
                "",
                "Human Vietnamese reference:",
                "",
                sample["human_reference_excerpt"],
                "",
                "Model Vietnamese output before compression:",
                "",
                sample["model_before_compression"],
                "",
                "Final model Vietnamese output after compression/unit merge:",
                "",
                sample["model_final_output"],
                "",
            ]
        )
        for unit in sample["units"]:
            lines.extend(
                [
                    f"### Unit {unit['unit_id']}",
                    "",
                    f"- Source paragraph IDs: `{json_dumps(unit['source_paragraph_ids'])}`",
                    f"- Target paragraph IDs: `{json_dumps(unit['target_paragraph_ids'])}`",
                    f"- Required terms: `{json_dumps(unit['required_terms'])}`",
                    f"- Missing terms: `{json_dumps(unit['missing_terms'])}`",
                    f"- Compression attempts: `{json_dumps(unit['compression_attempts'])}`",
                    f"- Selected final output: `{unit.get('selected_final_output')}`",
                    f"- Selection reason: `{unit.get('selection_reason')}`",
                    f"- Style drift score: `{unit.get('style_drift_score')}`",
                    f"- Human review recommended: `{unit.get('human_review_recommended')}`",
                    f"- Safety status: `{unit['safety_status']}`",
                    f"- Alignment quality: `{unit['alignment_quality']}`",
                    f"- Pass/fail reason: `{unit['pass_fail_reason']}`",
                    "",
                ]
            )
    (review_root / "human_review_final.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )

    suspicious = [
        unit
        for sample in payload["samples"]
        for unit in sample["units"]
        if unit["safety_status"] != "pass"
        or unit.get("alignment_quality", 1.0) < ALIGNMENT_QUALITY_THRESHOLD
        or (unit.get("style_drift_score") or 0) >= STYLE_DRIFT_REVIEW_THRESHOLD
    ]
    summary_lines = [
        "# Human Review Summary",
        "",
        f"Status: **{status}**",
        "",
        "## Approval Recommendation",
        "",
        (
            "The prompt is ready for human review, not automatic production use."
            if status == "READY FOR HUMAN REVIEW"
            else "NOT APPROVABLE until the listed validation failures are fixed."
        ),
        "",
        "## Top 5 Strengths",
        "",
        "- Strict cached replay is available.",
        "- Source, reference, and final output are stored for review.",
        "- Retry attempts are logged and bounded.",
        "- Compression and truncation diagnostics are retained.",
        "- Stable prompt is not auto-approved.",
        "",
        "## Top 5 Weaknesses",
        "",
        f"- Gate reasons: `{payload['final_pass_fail_reason']}`",
        f"- Provider failure count: `{payload['provider_failure_count']}`",
        f"- Truncation count: `{payload['truncation_count']}`",
        f"- Unsafe compression count: `{payload['unsafe_compression_count']}`",
        f"- Human review recommended samples: `{sum(1 for sample in payload['samples'] if sample.get('human_review_recommended'))}`",
        f"- Suspicious unit count: `{len(suspicious)}`",
        "",
        "## Suspicious Paragraphs Or Units",
        "",
    ]
    if suspicious:
        summary_lines.extend(
            f"- `{unit['unit_id']}`: {unit['pass_fail_reason']}" for unit in suspicious[:50]
        )
    else:
        summary_lines.append("- none")
    summary_lines.extend(
        [
            "",
            "## Production Translation Recommendation",
            "",
            (
                "Do not start production translation until a human approves this stable prompt."
                if status == "READY FOR HUMAN REVIEW"
                else "Production translation is not recommended."
            ),
            "",
            "## Review Commands",
            "",
            f"Approve: `nts eval review-stable --run {run_arg} --approve --json`",
            f"Reject: `nts eval review-stable --run {run_arg} --reject --reason \"<reason>\" --json`",
            "",
        ]
    )
    (review_root / "human_review_summary.md").write_text(
        "\n".join(summary_lines),
        encoding="utf-8",
    )

    with (review_root / "human_review_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "validation_index",
                "sample_id",
                "unit_id",
                "source_paragraph_ids",
                "target_paragraph_ids",
                "source_text",
                "human_reference_text",
                "model_final_text",
                "selected_final_output",
                "selection_reason",
                "style_drift_score",
                "human_review_recommended",
                "required_terms",
                "missing_terms",
                "safety_status",
                "alignment_quality",
                "pass_fail_reason",
            ],
        )
        writer.writeheader()
        for sample in payload["samples"]:
            for unit in sample["units"]:
                writer.writerow(
                    {
                        "validation_index": sample["validation_index"],
                        "sample_id": sample["sample_id"],
                        "unit_id": unit["unit_id"],
                        "source_paragraph_ids": json_dumps(unit["source_paragraph_ids"]),
                        "target_paragraph_ids": json_dumps(unit["target_paragraph_ids"]),
                        "source_text": unit["source_text"],
                        "human_reference_text": unit["human_reference_text"],
                        "model_final_text": unit["model_final_text"],
                        "selected_final_output": unit.get("selected_final_output"),
                        "selection_reason": unit.get("selection_reason"),
                        "style_drift_score": unit.get("style_drift_score"),
                        "human_review_recommended": unit.get("human_review_recommended"),
                        "required_terms": json_dumps(unit["required_terms"]),
                        "missing_terms": json_dumps(unit["missing_terms"]),
                        "safety_status": unit["safety_status"],
                        "alignment_quality": unit["alignment_quality"],
                        "pass_fail_reason": unit["pass_fail_reason"],
                    }
                )

    prompt_source = (
        validation_root / "stable_prompt.md"
        if (validation_root / "stable_prompt.md").exists()
        else validation_root / "candidate_prompt.md"
    )
    if prompt_source.exists():
        (review_root / "stable_prompt_for_review.md").write_text(
            prompt_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    approval_lines = [
        "# Approval Instructions",
        "",
        "Approve:",
        "",
        f"`nts eval review-stable --run {run_arg} --approve --json`",
        "",
        "Reject:",
        "",
        f"`nts eval review-stable --run {run_arg} --reject --reason \"<reason>\" --json`",
        "",
    ]
    if status != "READY FOR HUMAN REVIEW":
        approval_lines.extend(
            [
                "This run is NOT APPROVABLE. Fix the validation failures before approval.",
                "",
            ]
        )
    (review_root / "approval_instructions.md").write_text(
        "\n".join(approval_lines),
        encoding="utf-8",
    )
    return {
        "human_review_final_dir": str(review_root),
        "human_review_final": str(review_root / "human_review_final.md"),
        "human_review_final_json": str(review_root / "human_review_final.json"),
        "human_review_summary": str(review_root / "human_review_summary.md"),
        "human_review_table": str(review_root / "human_review_table.csv"),
        "approval_instructions": str(review_root / "approval_instructions.md"),
        "stable_prompt_for_review": str(review_root / "stable_prompt_for_review.md")
        if (review_root / "stable_prompt_for_review.md").exists()
        else None,
        "status": status,
    }


def validate_stable_prompt(
    *,
    project: str,
    raw_path: Path,
    translated_path: Path,
    provider_key: str,
    model: str,
    max_chapters: int,
    sample_count: int,
    max_source_chars: int,
    max_target_chars: int,
    enable_paragraph_alignment: bool,
    enable_compression_pass: bool,
    stable_run_count: int,
    merge_tiny_paragraphs: bool = True,
    tiny_paragraph_threshold: int = TINY_PARAGRAPH_THRESHOLD,
    unit_target_min_chars: int = UNIT_TARGET_MIN_CHARS,
    provider_retry_attempts: int = DEFAULT_PROVIDER_RETRY_ATTEMPTS,
    provider_run_retry_attempts: int = DEFAULT_PROVIDER_RUN_RETRY_ATTEMPTS,
    provider_retry_backoff_seconds: float = DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS,
) -> dict[str, Any]:
    if stable_run_count <= 0:
        raise ValueError("--stable-run-count must be greater than 0.")
    if provider_retry_attempts <= 0:
        raise ValueError("--provider-retry-attempts must be greater than 0.")
    if provider_run_retry_attempts < 0:
        raise ValueError("--provider-run-retry-attempts cannot be negative.")
    if provider_retry_backoff_seconds < 0:
        raise ValueError("--provider-retry-backoff-seconds cannot be negative.")
    source_eval_run = _load_source_eval_run(project)
    validation_root = new_run_dir(project, "stable")
    settings = {
        "max_chapters": max_chapters,
        "sample_count": sample_count,
        "max_source_chars": max_source_chars,
        "max_target_chars": max_target_chars,
        "enable_paragraph_alignment": enable_paragraph_alignment,
        "enable_compression_pass": enable_compression_pass,
        "merge_tiny_paragraphs": merge_tiny_paragraphs,
        "tiny_paragraph_threshold": tiny_paragraph_threshold,
        "unit_target_min_chars": unit_target_min_chars,
        "stable_run_count": stable_run_count,
        "provider_retry_attempts": provider_retry_attempts,
        "provider_run_retry_attempts": provider_run_retry_attempts,
        "provider_retry_backoff_seconds": provider_retry_backoff_seconds,
    }
    candidate = freeze_stable_candidate(
        validation_root=validation_root,
        project=project,
        provider_key=provider_key,
        model=model,
        source_eval_run=source_eval_run,
        settings=settings,
    )
    offsets = stable_sample_offsets(stable_run_count)
    validation_runs = []
    run_retry_entries: list[dict[str, Any]] = []
    for index, offset in enumerate(offsets, start=1):
        final_run = None
        for run_retry_no in range(0, max(0, provider_run_retry_attempts) + 1):
            candidate_run = run_candidate_validation_once(
                project=project,
                raw_path=raw_path,
                translated_path=translated_path,
                provider_key=provider_key,
                model=model,
                max_chapters=max_chapters,
                sample_count=sample_count,
                max_source_chars=max_source_chars,
                max_target_chars=max_target_chars,
                sample_start_ratio=offset,
                enable_paragraph_alignment=enable_paragraph_alignment,
                enable_compression_pass=enable_compression_pass,
                merge_tiny_paragraphs=merge_tiny_paragraphs,
                tiny_paragraph_threshold=tiny_paragraph_threshold,
                unit_target_min_chars=unit_target_min_chars,
                stable_prompt_text=candidate["prompt_text"],
                validation_index=index,
                run_retry_no=run_retry_no,
                provider_retry_attempts=provider_retry_attempts,
                provider_retry_backoff_seconds=provider_retry_backoff_seconds,
            )
            final_run = candidate_run
            should_retry_run = (
                run_retry_no < max(0, provider_run_retry_attempts)
                and validation_run_failed_only_retryable_provider(
                    candidate_run,
                    selected_model=model,
                )
            )
            if should_retry_run:
                run_retry_entries.extend(read_provider_retry_entries(candidate_run))
                run_retry_entries.append(
                    {
                        "provider_error_type": "retryable_provider_run_failure",
                        "http_status": None,
                        "retryable": True,
                        "attempt_no": run_retry_no + 1,
                        "max_attempts": max(0, provider_run_retry_attempts) + 1,
                        "retry_scope": "run",
                        "status": "run_retry_attempted",
                        "error_message_masked": "validation run failed only because of retryable provider errors",
                        "created_at": utc_now(),
                        "retry_id": f"run:{index}",
                        "validation_index": index,
                        "run_retry_no": run_retry_no,
                        "run_dir": candidate_run.get("run_dir"),
                    }
                )
                continue
            break
        if final_run is not None:
            validation_runs.append(final_run)
    provider_retry_summary = aggregate_provider_retry_summary(
        validation_runs,
        run_retry_entries,
    )
    write_json(
        validation_root / "provider_retry_summary.json",
        {
            "schema_version": "provider_retry_summary_v1",
            "summary": {
                key: value
                for key, value in provider_retry_summary.items()
                if key != "entries"
            },
            "entries": provider_retry_summary.get("entries", []),
        },
    )
    write_json(
        validation_root / "provider_retry_log.json",
        {
            "schema_version": "provider_retry_log_v1",
            "summary": {
                key: value
                for key, value in provider_retry_summary.items()
                if key != "entries"
            },
            "entries": provider_retry_summary.get("entries", []),
        },
    )
    gate = stable_gate_result(
        validation_runs=validation_runs,
        selected_model=model,
        expected_prompt_sha256=candidate["metadata"]["prompt_sha256"],
    )
    replay_exports = write_cached_eval_exports(
        validation_root=validation_root,
        validation_runs=validation_runs,
        selected_model=model,
    )
    strict_replay = replay_cached_eval(validation_root)
    if not strict_replay["quality_summary"].get("strict_replay_pass"):
        gate = {
            **gate,
            "pass": False,
            "reasons": sorted(
                set(gate.get("reasons", []) + ["cached_replay_strict_gate_failed"])
            ),
            "strict_replay_quality_summary": strict_replay["quality_summary"],
        }
    decision_outputs = write_stable_decision_outputs(
        validation_root=validation_root,
        candidate=candidate,
        validation_runs=validation_runs,
        gate=gate,
        provider_key=provider_key,
        model=model,
    )
    report_paths = {
        "stable_validation_report": str(validation_root / "stable_validation_report.json"),
        "cached_eval_replay": replay_exports.get("cached_eval_replay"),
        "human_review_samples": replay_exports.get("human_review_samples"),
        "paragraph_review_table": replay_exports.get("paragraph_review_table"),
        "replay_report": strict_replay.get("replay_report"),
        "replay_report_md": strict_replay.get("replay_report_md"),
        "provider_retry_log": str(validation_root / "provider_retry_log.json"),
        "provider_retry_summary": str(validation_root / "provider_retry_summary.json"),
        "candidate_prompt": str(validation_root / "candidate_prompt.md"),
        "candidate_prompt_metadata": str(validation_root / "candidate_prompt_metadata.json"),
        **decision_outputs,
    }
    report = {
        "schema_version": "stable_prompt_validation_v1",
        "project": project,
        "provider": provider_key,
        "model": model,
        "validation_root": str(validation_root),
        "candidate_prompt_sha256": candidate["metadata"]["prompt_sha256"],
        "stable_run_count": stable_run_count,
        "validation_runs": validation_runs,
        "gate": gate,
        "quality_gate": "pass" if gate["pass"] else "fail",
        "strict_replay": strict_replay,
        "replay_exports": replay_exports,
        "provider_retry_summary": {
            key: value for key, value in provider_retry_summary.items() if key != "entries"
        },
        "provider_retry_log": str(validation_root / "provider_retry_log.json"),
        "decision_outputs": decision_outputs,
        "report_paths": report_paths,
        "pass": gate["pass"],
    }
    human_review_final = write_final_human_review_package(
        validation_root=validation_root,
        report=report,
    )
    report_paths = {**report_paths, **human_review_final}
    report["report_paths"] = report_paths
    report["human_review_final"] = human_review_final
    write_json(validation_root / "stable_validation_report.json", report)
    (project_eval_root(project) / "latest.txt").write_text(str(validation_root), encoding="utf-8")
    return {
        "validation_root": str(validation_root),
        "pass": gate["pass"],
        "candidate_prompt_sha256": candidate["metadata"]["prompt_sha256"],
        "stable_prompt_created": decision_outputs["stable_prompt_created"],
        "stable_run_count": stable_run_count,
        "gate": gate,
        "validation_runs": [
            {
                "validation_index": run["validation_index"],
                "run_retry_no": run.get("run_retry_no", 0),
                "run_dir": run["run_dir"],
                "sample_start_ratio": run["sample_start_ratio"],
                "candidate_prompt_sha256": run["candidate_prompt_sha256"],
            }
            for run in validation_runs
        ],
        "decision_outputs": decision_outputs,
        "replay_exports": replay_exports,
        "provider_retry_summary": {
            key: value for key, value in provider_retry_summary.items() if key != "entries"
        },
        "human_review_final": human_review_final,
        "report_paths": report_paths,
        "quality_gate": "pass" if gate["pass"] else "fail",
        "selected_model": model,
        "output_folder": str(validation_root),
        "stable_validation_report": str(validation_root / "stable_validation_report.json"),
    }


def _stable_validation_counts(report: dict[str, Any]) -> dict[str, Any]:
    selected_model = report.get("model") or report.get("gate", {}).get("selected_model")
    truncation_count = 0
    unsafe_compression_count = 0
    provider_failures: list[dict[str, Any]] = []
    provider_json_failure_count = 0
    compression_attempts: list[dict[str, Any]] = []
    unit_merge_count = 0
    selected_output_counts: dict[str, int] = {}
    style_drift_warning_count = 0
    for run in report.get("validation_runs", []):
        validation_index = run.get("validation_index")
        model_report = run.get("report", {}).get("models", {}).get(selected_model, {})
        for sample in model_report.get("samples", []):
            truncation_count += len(sample.get("truncated_paragraphs", []) or [])
            unit_merge_count += int(sample.get("translation_unit_merge_count") or 0)
            selected = str(sample.get("selected_final_output") or "unknown")
            selected_output_counts[selected] = selected_output_counts.get(selected, 0) + 1
            style_drift = sample.get("style_drift") or {}
            if (
                not style_drift.get("ignored_for_gate")
                and (sample.get("style_drift_score") or 0) >= STYLE_DRIFT_WARNING_THRESHOLD
            ):
                style_drift_warning_count += 1
        translations = run.get("translations", {})
        if not isinstance(translations, dict):
            continue
        for sample_id, by_model in translations.items():
            if not isinstance(by_model, dict):
                continue
            metadata = by_model.get(selected_model)
            if not isinstance(metadata, dict):
                continue
            provider_error = metadata.get("provider_error")
            provider_json_failure_count += len(metadata.get("provider_json_failures", []) or [])
            unresolved_json = metadata.get("unresolved_provider_json_failures", []) or []
            if provider_error:
                provider_failures.append(
                    {
                        "validation_index": validation_index,
                        "sample_id": sample_id,
                        "kind": "provider_error",
                        "message": provider_error,
                        "classification": metadata.get("provider_error_classification"),
                        "provider_failure_empty_output": (
                            metadata.get("verification_after_compression", {}) or {}
                        ).get("provider_failure_empty_output", False),
                    }
                )
            for failure in unresolved_json:
                provider_failures.append(
                    {
                        "validation_index": validation_index,
                        "sample_id": sample_id,
                        "kind": "provider_json_failure",
                        **failure,
                    }
                )
            for entry in metadata.get("compression", {}).get("entries", []) or []:
                provider_json_failure_count += int(entry.get("provider_json_failures") or 0)
                if entry.get("unsafe_compression"):
                    unsafe_compression_count += 1
                compression_attempts.append(
                    {
                        "validation_index": validation_index,
                        "sample_id": sample_id,
                        "paragraph_id": entry.get("paragraph_id"),
                        "attempt_count": entry.get("compression_attempt_count", 0),
                        "unsafe_compression": bool(entry.get("unsafe_compression")),
                        "failure_reason": entry.get("compression_failure_reason"),
                    }
                )
    strict_replay_summary = report.get("strict_replay", {}).get("quality_summary", {})
    truncation_count = max(
        truncation_count,
        int(strict_replay_summary.get("truncated_paragraph_count") or 0),
    )
    attempt_counts = [
        int(item.get("attempt_count") or 0)
        for item in compression_attempts
    ]
    return {
        "truncation_count": truncation_count,
        "unsafe_compression_count": unsafe_compression_count,
        "provider_failures": provider_failures,
        "provider_retry_summary": report.get("provider_retry_summary", {}),
        "provider_json_failure_count": provider_json_failure_count,
        "compression_attempts": compression_attempts,
        "unit_merge_count": unit_merge_count,
        "selected_output_counts": selected_output_counts,
        "style_drift_warning_count": style_drift_warning_count,
        "compression_attempt_summary": {
            "paragraph_count": len(compression_attempts),
            "total_attempts": sum(attempt_counts),
            "max_attempts": max(attempt_counts) if attempt_counts else 0,
            "unsafe_count": unsafe_compression_count,
        },
    }


def compact_stable_validation_result(result: dict[str, Any]) -> dict[str, Any]:
    report_path = Path(result.get("stable_validation_report") or "")
    report = read_json(report_path) if report_path.is_file() else result
    gate = report.get("gate", result.get("gate", {}))
    counts = _stable_validation_counts(report)
    selected_model = report.get("model") or result.get("selected_model")
    return {
        "validation_root": result.get("validation_root") or report.get("validation_root"),
        "output_folder": result.get("output_folder") or report.get("validation_root"),
        "quality_gate": report.get("quality_gate") or result.get("quality_gate"),
        "pass": bool(report.get("pass", result.get("pass", False))),
        "stable_prompt_created": bool(
            report.get("decision_outputs", result.get("decision_outputs", {})).get(
                "stable_prompt_created",
                result.get("stable_prompt_created", False),
            )
        ),
        "selected_model": selected_model,
        "run_count": report.get("stable_run_count") or result.get("stable_run_count"),
        "run_scores": gate.get("per_run_scores", []),
        "sample_scores": gate.get("per_sample_scores", []),
        "ratio_summary": gate.get("ratio_summary", {}),
        "truncation_count": counts["truncation_count"],
        "unsafe_compression_count": counts["unsafe_compression_count"],
        "provider_failures": counts["provider_failures"],
        "provider_retry_summary": counts["provider_retry_summary"],
        "retryable_failures": counts["provider_retry_summary"].get("retryable_failures", 0),
        "non_retryable_failures": counts["provider_retry_summary"].get(
            "non_retryable_failures", 0
        ),
        "sample_retries_attempted": counts["provider_retry_summary"].get(
            "sample_retries_attempted", 0
        ),
        "sample_retries_succeeded": counts["provider_retry_summary"].get(
            "sample_retries_succeeded", 0
        ),
        "sample_retries_exhausted": counts["provider_retry_summary"].get(
            "sample_retries_exhausted", 0
        ),
        "run_retries_attempted": counts["provider_retry_summary"].get(
            "run_retries_attempted", 0
        ),
        "final_provider_failure_count": counts["provider_retry_summary"].get(
            "final_provider_failure_count", 0
        ),
        "provider_json_failure_count": counts["provider_json_failure_count"],
        "unit_merge_count": counts["unit_merge_count"],
        "selected_output_counts": counts["selected_output_counts"],
        "style_drift_warning_count": counts["style_drift_warning_count"],
        "compression_attempt_summary": counts["compression_attempt_summary"],
        "gate_reasons": gate.get("reasons", []),
        "gate": {
            "selected_model": selected_model,
            "reasons": gate.get("reasons", []),
            "ratio_summary": gate.get("ratio_summary", {}),
        },
        "report_paths": report.get("report_paths") or result.get("report_paths", {}),
    }


def resolve_eval_run(run: str | Path) -> Path:
    run_text = str(run).strip()
    if not run_text:
        raise ValueError("--run is required.")
    path = Path(run_text).expanduser()
    candidates = [path]
    if not path.is_absolute():
        candidates.append(repo_root() / path)
        candidates.append(eval_root() / run_text)
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    if not any(separator in run_text for separator in ("/", "\\")):
        matches = sorted(
            candidate.resolve()
            for candidate in eval_root().glob(run_text)
            if candidate.exists() and candidate.is_dir()
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous evaluation run id: {run_text}")
    raise ValueError(f"Evaluation run not found: {run_text}")


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _replay_quality_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_model": report.get("selected_model"),
        "overall_average_score": report.get("overall", {}).get("average_score"),
        "run_count": len(report.get("per_run", [])),
        "sample_count": len(report.get("per_sample", [])),
        "paragraph_count": len(report.get("paragraph_diagnostics", [])),
        "ratio_summary": report.get("overall", {}).get("ratio_after_summary"),
        "all_samples_pass": report.get("overall", {}).get("all_samples_pass"),
        "strict_replay_pass": report.get("overall", {}).get("strict_replay_pass"),
        "truncated_paragraph_count": report.get("overall", {}).get(
            "truncated_paragraph_count"
        ),
        "low_alignment_quality_sample_count": len(
            report.get("overall", {}).get("low_alignment_quality_samples", [])
        ),
        "provider_failure_count": report.get("overall", {}).get("provider_failure_count", 0),
        "style_drift_warning_count": report.get("overall", {}).get(
            "style_drift_warning_count",
            0,
        ),
    }


def summarize_cached_eval_replay(replay: dict[str, Any]) -> dict[str, Any]:
    rows = replay.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("cached_eval_replay.json must contain a rows list.")

    sample_map: dict[tuple[int, str], dict[str, Any]] = {}
    paragraph_diagnostics = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        validation_index = int(row.get("validation_index") or 0)
        sample_id = str(row.get("sample_id") or "")
        paragraph_id = str(row.get("paragraph_id") or "")
        ratio_before = _float_or_none(row.get("ratio_before"))
        ratio_after = _float_or_none(row.get("ratio_selected", row.get("ratio_after")))
        after_text = row.get(
            "model_paragraph_selected_final",
            row.get("model_paragraph_after_compression", ""),
        )
        truncation = {
            "is_truncated": bool(row.get("truncation_detected")),
            "reasons": row.get("truncation_reasons", []) or [],
        }
        if "truncation_detected" not in row:
            truncation = detect_truncated_vietnamese(
                str(after_text),
                source_text=str(row.get("source_paragraph", "")),
                strict_max=row.get("strict_max"),
            )
        alignment_quality = row.get("alignment_quality")
        if alignment_quality is None:
            warnings = row.get("warnings", []) or []
            alignment_quality = 0.2 if any("paragraph_count_mismatch" in item or "merged" in item for item in warnings) else 1.0
        accepted_for_stable = row.get("accepted_for_stable_validation")
        if accepted_for_stable is None:
            accepted_for_stable = float(alignment_quality) >= ALIGNMENT_QUALITY_THRESHOLD
        sample_score = row.get("sample_score") if isinstance(row.get("sample_score"), dict) else {}
        key = (validation_index, sample_id)
        sample_summary = sample_map.setdefault(
            key,
            {
                "validation_index": validation_index,
                "sample_id": sample_id,
                "chapter_id": row.get("chapter_id"),
                "paragraph_count": 0,
                "total_score": sample_score.get("total_score"),
                "pass": sample_score.get("pass"),
                "reason": sample_score.get("reason"),
                "warnings": [],
                "alignment_quality": alignment_quality,
                "accepted_for_stable_validation": accepted_for_stable,
                "truncated_paragraphs": [],
                "provider_failures": [],
                "style_drift_score": row.get("style_drift_score"),
                "selected_final_output": row.get("selected_final_output"),
                "human_review_recommended": row.get("human_review_recommended", False),
                "ratio_before_values": [],
                "ratio_after_values": [],
            },
        )
        if row.get("style_drift_score") is not None:
            sample_summary["style_drift_score"] = max(
                sample_summary.get("style_drift_score") or 0,
                row.get("style_drift_score") or 0,
            )
        if row.get("human_review_recommended"):
            sample_summary["human_review_recommended"] = True
        sample_summary["paragraph_count"] += 1
        if ratio_before is not None:
            sample_summary["ratio_before_values"].append(ratio_before)
        if ratio_after is not None:
            sample_summary["ratio_after_values"].append(ratio_after)
        for warning in row.get("warnings", []) or []:
            if warning not in sample_summary["warnings"]:
                sample_summary["warnings"].append(warning)
        for warning in row.get("alignment_warnings", []) or []:
            if warning not in sample_summary["warnings"]:
                sample_summary["warnings"].append(warning)
        if truncation["is_truncated"]:
            sample_summary["truncated_paragraphs"].append(
                {"paragraph_id": paragraph_id, "reasons": truncation["reasons"]}
            )
        if row.get("provider_error") and not sample_summary["provider_failures"]:
            sample_summary["provider_failures"].append(
                {
                    "provider_error": row.get("provider_error"),
                    "provider_error_classification": row.get(
                        "provider_error_classification"
                    ),
                    "provider_failure_empty_output": row.get(
                        "provider_failure_empty_output", False
                    ),
                }
            )
        paragraph_diagnostics.append(
            {
                "validation_index": validation_index,
                "sample_id": sample_id,
                "chapter_id": row.get("chapter_id"),
                "paragraph_id": paragraph_id,
                "unit_id": row.get("unit_id", paragraph_id),
                "source_paragraph_ids": row.get("source_paragraph_ids", [paragraph_id]),
                "target_paragraph_ids": row.get("target_paragraph_ids", [paragraph_id]),
                "unit_type": row.get("unit_type"),
                "merge_reason": row.get("merge_reason"),
                "is_merged_unit": row.get("is_merged_unit", False),
                "reference_char_count": row.get("reference_char_count"),
                "before_char_count": row.get("before_char_count"),
                "after_char_count": row.get("after_char_count"),
                "selected_char_count": row.get(
                    "selected_char_count",
                    row.get("after_char_count"),
                ),
                "ratio_before": ratio_before,
                "ratio_after": ratio_after,
                "ratio_after_compression": _float_or_none(row.get("ratio_after")),
                "ratio_selected": ratio_after,
                "score_notes": row.get("score_notes", {}),
                "warnings": row.get("warnings", []),
                "alignment_quality": alignment_quality,
                "accepted_for_stable_validation": accepted_for_stable,
                "budget_policy_used": row.get("budget_policy_used"),
                "paragraph_allowed_over_budget_reason": row.get(
                    "paragraph_allowed_over_budget_reason"
                ),
                "truncation_detected": truncation["is_truncated"],
                "truncation_reasons": truncation["reasons"],
                "provider_error": row.get("provider_error"),
                "provider_error_classification": row.get("provider_error_classification"),
                "provider_failure_empty_output": row.get(
                    "provider_failure_empty_output", False
                ),
                "required_terms": row.get("required_terms", []),
                "missing_terms": row.get("missing_terms", []),
                "selected_final_output": row.get("selected_final_output"),
                "selected_final_output_reason": row.get("selected_final_output_reason"),
                "style_drift": row.get("style_drift"),
                "style_drift_score": row.get("style_drift_score"),
                "human_review_recommended": row.get("human_review_recommended", False),
                "source_paragraph": row.get("source_paragraph", ""),
                "human_reference_paragraph": row.get("human_reference_paragraph", ""),
                "model_paragraph_before_compression": row.get(
                    "model_paragraph_before_compression", ""
                ),
                "model_paragraph_after_compression": row.get(
                    "model_paragraph_after_compression", ""
                ),
                "model_paragraph_selected_final": after_text,
            }
        )

    per_sample = []
    for sample in sample_map.values():
        before_values = sample.pop("ratio_before_values")
        after_values = sample.pop("ratio_after_values")
        sample["ratio_before_summary"] = {
            "min": min(before_values) if before_values else None,
            "max": max(before_values) if before_values else None,
            "average": _mean(before_values),
        }
        sample["ratio_after_summary"] = {
            "min": min(after_values) if after_values else None,
            "max": max(after_values) if after_values else None,
            "average": _mean(after_values),
        }
        if sample["truncated_paragraphs"]:
            sample["pass"] = False
            reason = sample.get("reason")
            sample["reason"] = (
                f"{reason}, paragraph_truncation_detected"
                if reason and reason != "pass"
                else "paragraph_truncation_detected"
            )
        if sample.get("accepted_for_stable_validation") is False:
            sample["pass"] = False
            reason = sample.get("reason")
            sample["reason"] = (
                f"{reason}, alignment_quality_below_threshold"
                if reason and reason != "pass"
                else "alignment_quality_below_threshold"
            )
        if sample.get("provider_failures"):
            sample["pass"] = False
            reason = sample.get("reason")
            sample["reason"] = (
                f"{reason}, provider_failure"
                if reason and reason != "pass"
                else "provider_failure"
            )
        high_drift = [
            diagnostic
            for diagnostic in paragraph_diagnostics
            if diagnostic["validation_index"] == sample["validation_index"]
            and diagnostic["sample_id"] == sample["sample_id"]
            and (diagnostic.get("style_drift_score") or 0) >= STYLE_DRIFT_WARNING_THRESHOLD
            and diagnostic.get("selected_final_output") == "after_compression"
        ]
        if high_drift:
            sample["pass"] = False
            reason = sample.get("reason")
            sample["reason"] = (
                f"{reason}, style_drift_above_threshold"
                if reason and reason != "pass"
                else "style_drift_above_threshold"
            )
        per_sample.append(sample)
    per_sample.sort(key=lambda item: (item["validation_index"], item["sample_id"]))

    run_map: dict[int, dict[str, Any]] = {}
    for sample in per_sample:
        run_summary = run_map.setdefault(
            sample["validation_index"],
            {
                "validation_index": sample["validation_index"],
                "sample_count": 0,
                "scores": [],
                "ratio_after_values": [],
                "all_samples_pass": True,
                "warnings": [],
            },
        )
        run_summary["sample_count"] += 1
        if sample.get("total_score") is not None:
            run_summary["scores"].append(float(sample["total_score"]))
        average_ratio = sample.get("ratio_after_summary", {}).get("average")
        if average_ratio is not None:
            run_summary["ratio_after_values"].append(float(average_ratio))
        if sample.get("pass") is False:
            run_summary["all_samples_pass"] = False
        for warning in sample.get("warnings", []):
            if warning not in run_summary["warnings"]:
                run_summary["warnings"].append(warning)

    per_run = []
    for run_summary in run_map.values():
        scores = run_summary.pop("scores")
        ratios = run_summary.pop("ratio_after_values")
        run_summary["average_score"] = (
            round(sum(scores) / len(scores), 2) if scores else None
        )
        run_summary["ratio_after_summary"] = {
            "min": min(ratios) if ratios else None,
            "max": max(ratios) if ratios else None,
            "average": _mean(ratios),
        }
        per_run.append(run_summary)
    per_run.sort(key=lambda item: item["validation_index"])

    all_scores = [
        float(sample["total_score"])
        for sample in per_sample
        if sample.get("total_score") is not None
    ]
    all_after_ratios = [
        diagnostic["ratio_after"]
        for diagnostic in paragraph_diagnostics
        if diagnostic.get("ratio_after") is not None
    ]
    truncated_paragraphs = [
        {
            "validation_index": diagnostic["validation_index"],
            "sample_id": diagnostic["sample_id"],
            "paragraph_id": diagnostic["paragraph_id"],
            "reasons": diagnostic["truncation_reasons"],
        }
        for diagnostic in paragraph_diagnostics
        if diagnostic.get("truncation_detected")
    ]
    low_quality_samples = [
        {
            "validation_index": sample["validation_index"],
            "sample_id": sample["sample_id"],
            "alignment_quality": sample.get("alignment_quality"),
            "warnings": sample.get("warnings", []),
        }
        for sample in per_sample
        if sample.get("accepted_for_stable_validation") is False
    ]
    provider_failure_samples = [
        {
            "validation_index": sample["validation_index"],
            "sample_id": sample["sample_id"],
            "provider_failures": sample.get("provider_failures", []),
        }
        for sample in per_sample
        if sample.get("provider_failures")
    ]
    style_drift_samples = [
        {
            "validation_index": sample["validation_index"],
            "sample_id": sample["sample_id"],
            "style_drift_score": max(
                (
                    diagnostic.get("style_drift_score") or 0
                    for diagnostic in paragraph_diagnostics
                    if diagnostic["validation_index"] == sample["validation_index"]
                    and diagnostic["sample_id"] == sample["sample_id"]
                ),
                default=0,
            ),
        }
        for sample in per_sample
        if sample.get("reason") and "style_drift_above_threshold" in sample.get("reason", "")
    ]
    all_samples_pass = bool(per_sample) and all(sample.get("pass") is True for sample in per_sample)
    strict_replay_pass = (
        all_samples_pass
        and not truncated_paragraphs
        and not low_quality_samples
        and not provider_failure_samples
        and not style_drift_samples
    )
    overall = {
        "average_score": round(sum(all_scores) / len(all_scores), 2) if all_scores else None,
        "all_samples_pass": all_samples_pass,
        "strict_replay_pass": strict_replay_pass,
        "truncated_paragraph_count": len(truncated_paragraphs),
        "truncated_paragraphs": truncated_paragraphs,
        "low_alignment_quality_samples": low_quality_samples,
        "provider_failure_samples": provider_failure_samples,
        "provider_failure_count": len(provider_failure_samples),
        "style_drift_samples": style_drift_samples,
        "style_drift_warning_count": len(style_drift_samples),
        "ratio_after_summary": {
            "min": min(all_after_ratios) if all_after_ratios else None,
            "max": max(all_after_ratios) if all_after_ratios else None,
            "average": _mean(all_after_ratios),
        },
    }
    return {
        "schema_version": "cached_eval_replay_report_v1",
        "selected_model": replay.get("selected_model"),
        "row_count": len(rows),
        "overall": overall,
        "per_run": per_run,
        "per_sample": per_sample,
        "paragraph_diagnostics": paragraph_diagnostics,
    }


def _md_cell(value: Any, limit: int = 120) -> str:
    return _snippet(str(value or ""), limit).replace("|", "\\|").replace("\n", "<br>")


def write_replay_report_md(path: Path, report: dict[str, Any]) -> None:
    overall = report["overall"]
    retry_summary = report.get("retry_summary", {})
    lines = [
        "# Cached Eval Replay Report",
        "",
        f"- Selected model: `{report.get('selected_model')}`",
        f"- Average score: `{overall.get('average_score')}`",
        f"- All samples pass: `{overall.get('all_samples_pass')}`",
        f"- Strict replay pass: `{overall.get('strict_replay_pass')}`",
        f"- Truncated paragraphs: `{overall.get('truncated_paragraph_count')}`",
        f"- Provider failures: `{overall.get('provider_failure_count', 0)}`",
        f"- Style drift warnings: `{overall.get('style_drift_warning_count', 0)}`",
        f"- Retryable provider failures: `{retry_summary.get('retryable_failures', 0)}`",
        f"- Sample retries attempted: `{retry_summary.get('sample_retries_attempted', 0)}`",
        f"- Sample retries succeeded: `{retry_summary.get('sample_retries_succeeded', 0)}`",
        f"- Sample retries exhausted: `{retry_summary.get('sample_retries_exhausted', 0)}`",
        f"- Run retries attempted: `{retry_summary.get('run_retries_attempted', 0)}`",
        f"- Paragraph rows: `{report.get('row_count')}`",
        "",
    ]
    if not overall.get("strict_replay_pass"):
        lines.extend(
            [
                "> WARNING: This cached run fails the strict MVP4.8.6 replay gate. "
                "Do not approve or use its stable prompt for production.",
                "",
            ]
        )
    lines.extend(
        [
        "## Runs",
        "",
        "| Run | Samples | Average Score | Ratio Min | Ratio Avg | Ratio Max | Pass |",
        "|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for run in report["per_run"]:
        ratios = run["ratio_after_summary"]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(run["validation_index"]),
                    str(run["sample_count"]),
                    str(run["average_score"]),
                    str(ratios["min"]),
                    str(ratios["average"]),
                    str(ratios["max"]),
                    str(run["all_samples_pass"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| Run | Sample | Chapter | Score | Pass | Ratio Avg | Paragraphs | Alignment | Truncated | Style Drift | Reason |",
            "|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for sample in report["per_sample"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(sample["validation_index"]),
                    _md_cell(sample["sample_id"], 60),
                    str(sample.get("chapter_id")),
                    str(sample.get("total_score")),
                    str(sample.get("pass")),
                    str(sample["ratio_after_summary"]["average"]),
                    str(sample["paragraph_count"]),
                    str(sample.get("alignment_quality")),
                    str(len(sample.get("truncated_paragraphs", []))),
                    str(sample.get("style_drift_score")),
                    _md_cell(sample.get("reason"), 90),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Paragraph Diagnostics",
            "",
            "| Run | Sample | Paragraph | Ref Chars | Before | After | Selected | Ratio Before | Ratio After | Ratio Selected | Selected Output | Style Drift | Truncated | Reasons | Alignment | Eligible | Source | Reference | Selected Final |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---:|---|---|---|---|",
        ]
    )
    for diagnostic in report["paragraph_diagnostics"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(diagnostic["validation_index"]),
                    _md_cell(diagnostic["sample_id"], 50),
                    _md_cell(diagnostic["paragraph_id"], 30),
                    str(diagnostic.get("reference_char_count")),
                    str(diagnostic.get("before_char_count")),
                    str(diagnostic.get("after_char_count")),
                    str(diagnostic.get("selected_char_count")),
                    str(diagnostic.get("ratio_before")),
                    str(diagnostic.get("ratio_after_compression")),
                    str(diagnostic.get("ratio_after")),
                    str(diagnostic.get("selected_final_output")),
                    str(diagnostic.get("style_drift_score")),
                    str(diagnostic.get("truncation_detected")),
                    _md_cell("; ".join(diagnostic.get("truncation_reasons", [])), 80),
                    str(diagnostic.get("alignment_quality")),
                    str(diagnostic.get("accepted_for_stable_validation")),
                    _md_cell(diagnostic.get("source_paragraph"), 100),
                    _md_cell(diagnostic.get("human_reference_paragraph"), 100),
                    _md_cell(diagnostic.get("model_paragraph_selected_final"), 100),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_replay_human_review_exports(run_dir: Path, report: dict[str, Any]) -> None:
    review_lines = ["# Human Review Samples", ""]
    for diagnostic in report["paragraph_diagnostics"]:
        review_lines.extend(
            [
                (
                    f"## Run {diagnostic['validation_index']} / "
                    f"{diagnostic['sample_id']} / {diagnostic['paragraph_id']}"
                ),
                "",
                f"- Truncation detected: `{diagnostic.get('truncation_detected')}`",
                f"- Truncation reasons: `{json_dumps(diagnostic.get('truncation_reasons', []))}`",
                f"- Alignment quality: `{diagnostic.get('alignment_quality')}`",
                (
                    "- Eligible for stable validation: "
                    f"`{diagnostic.get('accepted_for_stable_validation')}`"
                ),
                f"- Ratio before: `{diagnostic.get('ratio_before')}`",
                f"- Ratio after compression: `{diagnostic.get('ratio_after_compression')}`",
                f"- Ratio selected: `{diagnostic.get('ratio_after')}`",
                f"- Selected final output: `{diagnostic.get('selected_final_output')}`",
                f"- Selection reason: `{diagnostic.get('selected_final_output_reason')}`",
                f"- Style drift score: `{diagnostic.get('style_drift_score')}`",
                f"- Human review recommended: `{diagnostic.get('human_review_recommended')}`",
                "",
                "Source:",
                "",
                diagnostic.get("source_paragraph", ""),
                "",
                "Human reference:",
                "",
                diagnostic.get("human_reference_paragraph", ""),
                "",
                "Model before compression:",
                "",
                diagnostic.get("model_paragraph_before_compression", ""),
                "",
                "Model after compression:",
                "",
                diagnostic.get("model_paragraph_after_compression", ""),
                "",
                "Selected final output:",
                "",
                diagnostic.get("model_paragraph_selected_final", ""),
                "",
            ]
        )
    (run_dir / "human_review_samples.md").write_text(
        "\n".join(review_lines).rstrip() + "\n",
        encoding="utf-8",
    )

    table_lines = [
        "# Paragraph Review Table",
        "",
        "| Run | Sample | Paragraph | Ref Chars | Before | After | Selected | Ratio Before | Ratio After | Ratio Selected | Selected Output | Style Drift | Truncated | Reasons | Alignment | Eligible | Source | Reference | Selected Final |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---:|---|---|---|---|",
    ]
    for diagnostic in report["paragraph_diagnostics"]:
        table_lines.append(
            "| "
            + " | ".join(
                [
                    str(diagnostic["validation_index"]),
                    _md_cell(diagnostic["sample_id"], 50),
                    _md_cell(diagnostic["paragraph_id"], 30),
                    str(diagnostic.get("reference_char_count")),
                    str(diagnostic.get("before_char_count")),
                    str(diagnostic.get("after_char_count")),
                    str(diagnostic.get("selected_char_count")),
                    str(diagnostic.get("ratio_before")),
                    str(diagnostic.get("ratio_after_compression")),
                    str(diagnostic.get("ratio_after")),
                    str(diagnostic.get("selected_final_output")),
                    str(diagnostic.get("style_drift_score")),
                    str(diagnostic.get("truncation_detected")),
                    _md_cell("; ".join(diagnostic.get("truncation_reasons", [])), 80),
                    str(diagnostic.get("alignment_quality")),
                    str(diagnostic.get("accepted_for_stable_validation")),
                    _md_cell(diagnostic.get("source_paragraph"), 100),
                    _md_cell(diagnostic.get("human_reference_paragraph"), 100),
                    _md_cell(diagnostic.get("model_paragraph_selected_final"), 100),
                ]
            )
            + " |"
        )
    (run_dir / "paragraph_review_table.md").write_text(
        "\n".join(table_lines) + "\n",
        encoding="utf-8",
    )


def replay_cached_eval(run: str | Path) -> dict[str, Any]:
    run_dir = resolve_eval_run(run)
    replay_path = run_dir / "cached_eval_replay.json"
    if not replay_path.exists():
        raise ValueError(f"cached_eval_replay.json not found in evaluation run: {run_dir}")
    replay = read_json(replay_path)
    report = summarize_cached_eval_replay(replay)
    report["run_dir"] = str(run_dir)
    report["cached_eval_replay_path"] = str(replay_path)
    retry_summary_path = run_dir / "provider_retry_summary.json"
    if retry_summary_path.exists():
        retry_payload = read_json(retry_summary_path)
        report["retry_summary"] = retry_payload.get("summary", retry_payload)
    report_json_path = run_dir / "replay_report.json"
    report_md_path = run_dir / "replay_report.md"
    write_json(report_json_path, report)
    write_replay_report_md(report_md_path, report)
    write_replay_human_review_exports(run_dir, report)
    invalidation_path = None
    if (run_dir / "stable_prompt.md").exists() and not report["overall"].get("strict_replay_pass"):
        invalidation = {
            "schema_version": "stable_prompt_invalidated_v1",
            "reason": "strict_cached_replay_failed",
            "created_at": utc_now(),
            "stable_prompt_path": str(run_dir / "stable_prompt.md"),
            "stable_prompt_metadata_path": str(run_dir / "stable_prompt_metadata.json"),
            "quality_summary": _replay_quality_summary(report),
            "truncated_paragraphs": report["overall"].get("truncated_paragraphs", []),
            "low_alignment_quality_samples": report["overall"].get(
                "low_alignment_quality_samples", []
            ),
        }
        invalidation_path = run_dir / "stable_prompt_invalidated.json"
        write_json(invalidation_path, invalidation)
    return {
        "run_dir": str(run_dir),
        "replay_report": str(report_json_path),
        "replay_report_md": str(report_md_path),
        "stable_prompt_invalidated": bool(invalidation_path),
        "stable_prompt_invalidated_path": str(invalidation_path) if invalidation_path else None,
        "quality_summary": _replay_quality_summary(report),
        "per_run": report["per_run"],
        "per_sample": report["per_sample"],
        "paragraph_diagnostics": report["paragraph_diagnostics"],
    }


def stable_prompt_review(
    *,
    run: str | Path,
    approve: bool,
    reject: bool,
    reason: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    if approve == reject:
        raise ValueError("Choose exactly one of --approve or --reject.")
    run_dir = resolve_eval_run(run)
    prompt_path = run_dir / "stable_prompt.md"
    metadata_path = run_dir / "stable_prompt_metadata.json"
    if not prompt_path.exists():
        raise ValueError(f"stable_prompt.md not found in evaluation run: {run_dir}")
    if not metadata_path.exists():
        raise ValueError(f"stable_prompt_metadata.json not found in evaluation run: {run_dir}")
    replay_result = replay_cached_eval(run_dir)
    metadata = read_json(metadata_path)
    quality_summary = {
        "quality_gate": metadata.get("quality_gate"),
        "average_score": metadata.get("average_score"),
        "ratio_summary": metadata.get("ratio_summary"),
        "compression_counts": metadata.get("compression_counts"),
        "validation_run_count": len(metadata.get("validation_runs", [])),
        "replay": replay_result["quality_summary"],
    }
    reviewer_name = (
        reviewer
        or os.environ.get("NTS_REVIEWER")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "local-user"
    )
    base_payload = {
        "schema_version": "stable_prompt_human_review_v1",
        "reviewer": reviewer_name,
        "timestamp": utc_now(),
        "run_dir": str(run_dir),
        "prompt_path": str(prompt_path),
        "metadata_path": str(metadata_path),
        "quality_summary": quality_summary,
        "stable_prompt_modified": False,
    }
    if approve:
        if not replay_result["quality_summary"].get("strict_replay_pass"):
            raise ValueError(
                "Stable prompt cannot be approved because strict cached replay failed."
            )
        payload = {**base_payload, "decision": "approved"}
        output_path = run_dir / "stable_prompt_approval.json"
        write_json(output_path, payload)
        return {
            "run_dir": str(run_dir),
            "decision": "approved",
            "approval_path": str(output_path),
            "quality_summary": quality_summary,
            "stable_prompt_modified": False,
        }
    if not reason or not reason.strip():
        raise ValueError("--reason is required when using --reject.")
    payload = {**base_payload, "decision": "rejected", "reason": reason.strip()}
    output_path = run_dir / "stable_prompt_rejection.json"
    write_json(output_path, payload)
    return {
        "run_dir": str(run_dir),
        "decision": "rejected",
        "rejection_path": str(output_path),
        "quality_summary": quality_summary,
        "stable_prompt_modified": False,
    }


def write_prompt_iteration_log(
    run_dir: Path,
    *,
    iteration: int,
    change: str,
    why: str,
    report: dict[str, Any],
) -> None:
    lines = [
        "# Prompt Iteration Log",
        "",
        f"## Iteration {iteration}",
        "",
        f"- What changed: {change}",
        f"- Why changed: {why}",
        f"- Overall pass: {report.get('pass')}",
        f"- Best model: {report.get('best_model')}",
        "",
        "### Scores",
        "",
    ]
    for model, model_report in report.get("models", {}).items():
        if "average_score" in model_report:
            lines.append(
                f"- `{model}`: average={model_report['average_score']}, "
                f"pass={model_report['pass']}, reason={model_report['final_pass_fail_reason']}"
            )
            for sample_score in model_report.get("samples", []):
                lines.append(
                    f"  - {sample_score['sample_id']}: total={sample_score['total_score']}, "
                    f"ratio={sample_score['output_reference_ratio']}, "
                    f"retry={sample_score['retry_triggered']}, pass={sample_score['pass']}"
                )
        else:
            lines.append(
                f"- `{model}`: total={model_report['total_score']}, pass={model_report['pass']}"
            )
    weaknesses = []
    for model_report in report.get("models", {}).values():
        if "samples" in model_report:
            for sample_score in model_report["samples"]:
                if not sample_score["pass"]:
                    weaknesses.append(sample_score["final_pass_fail_reason"])
        elif not model_report.get("pass"):
            weaknesses.append(model_report.get("final_pass_fail_reason", "failed"))
    lines.extend(
        [
            "",
            "### Remaining Weakness",
            "",
            "- " + ("; ".join(sorted(set(weaknesses))) if weaknesses else "none"),
            "",
        ]
    )
    (run_dir / "prompt_iteration_log.md").write_text("\n".join(lines), encoding="utf-8")


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]
