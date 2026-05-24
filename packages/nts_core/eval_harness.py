from __future__ import annotations

import html
import json
import os
import re
import time
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
ACTIVE_PROMPT_ITERATION = 3

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


def align_chapters(raw_chapters: list[dict[str, Any]], target_chapters: list[dict[str, Any]]) -> dict[str, Any]:
    count = min(len(raw_chapters), len(target_chapters))
    pairs = []
    for index in range(count):
        raw = raw_chapters[index]
        target = target_chapters[index]
        pairs.append(
            {
                "chapter_id": index + 1,
                "raw_chapter_id": raw["chapter_id"],
                "target_chapter_id": target["chapter_id"],
                "raw_title": raw.get("title"),
                "target_title": target.get("title"),
                "raw_chars": len(raw["text"]),
                "target_chars": len(target["text"]),
                "confidence": 0.75,
                "method": "spine_order_index_alignment",
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
    return {
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

    samples: list[dict[str, Any]] = []
    if aligned_count >= sample_count:
        for index in range(sample_count):
            chapter_id = index + 1
            samples.append(
                select_sample(
                    raw_chapters[index]["text"],
                    target_chapters[index]["text"],
                    chapter_id=chapter_id,
                    max_source_chars=max_source_chars,
                    max_target_chars=max_target_chars,
                    sample_start_ratio=sample_start_ratio,
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
) -> dict[str, Any]:
    if max_chapters <= 0:
        raise ValueError("--max-chapters must be greater than 0.")
    if max_source_chars <= 0 or max_target_chars <= 0:
        raise ValueError("--max-source-chars and --max-target-chars must be greater than 0.")
    if sample_start_ratio < 0 or sample_start_ratio > 1:
        raise ValueError("--sample-start-ratio must be between 0 and 1.")
    if sample_count <= 0:
        raise ValueError("--sample-count must be greater than 0.")
    run_dir = new_run_dir(project, "eval")
    raw_chapters = extract_raw_chapters(raw_path, max_chapters=max_chapters)
    target_chapters = extract_epub_chapters(translated_path, max_chapters=max_chapters)
    alignment = align_chapters(raw_chapters, target_chapters)
    samples = select_samples(
        raw_chapters,
        target_chapters,
        sample_count=sample_count,
        max_source_chars=max_source_chars,
        max_target_chars=max_target_chars,
        sample_start_ratio=sample_start_ratio,
    )
    sample = samples[0]
    write_json(run_dir / "extracted_raw_chapters.json", raw_chapters)
    write_json(run_dir / "extracted_translated_chapters.json", target_chapters)
    write_json(run_dir / "alignment_report.json", alignment)
    write_json(run_dir / "selected_sample.json", sample)
    write_json(run_dir / "selected_samples.json", {"samples": samples})
    return {
        "run_dir": str(run_dir),
        "alignment": alignment,
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


def _chat_completion(
    provider: EvalProvider,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
) -> str:
    if provider.key == "mock":
        source = messages[-1]["content"][:240].replace("\n", " ")
        return f"[MOCK {model}] {source}"
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
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:500]
        raise ValueError(f"Provider HTTP error {exc.code}: {body}") from exc
    return data["choices"][0]["message"]["content"]


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


def translate_sample(
    *,
    project: str,
    provider_key: str,
    models: list[str],
    max_source_chars: int,
    enable_length_retry: bool = False,
    target_length_tolerance: float = 0.2,
) -> dict[str, Any]:
    result = translate_samples(
        project=project,
        provider_key=provider_key,
        models=models,
        max_source_chars=max_source_chars,
        enable_length_retry=enable_length_retry,
        target_length_tolerance=target_length_tolerance,
        sample_limit=1,
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
    sample_limit: int | None = None,
    prompt_iteration: int = 1,
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
    outputs_by_sample: dict[str, dict[str, Any]] = {}
    outputs_by_model: dict[str, Any] = {}
    translations_root = run_dir / "translation_outputs"
    translations_root.mkdir(parents=True, exist_ok=True)

    for sample in samples:
        sample_id = sample["sample_id"]
        sample_dir = translations_root / sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        source = sample["source_text"][:max_source_chars]
        system_prompt = translation_system_prompt(
            run_dir,
            sample=sample,
            prompt_iteration=prompt_iteration,
            target_length_tolerance=target_length_tolerance,
        )
        outputs_by_sample[sample_id] = {}
        for model in models:
            if provider.models and provider.key != "mock" and model not in provider.models:
                # Allow config to be stale but keep warning local by not blocking real user overrides.
                pass
            initial = _chat_completion(
                provider,
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": source},
                ],
                max_tokens=max_tokens_for_sample(sample),
            ).strip()
            safe_model = safe_model_name(model)
            initial_path = sample_dir / f"{safe_model}_initial.txt"
            final_path = sample_dir / f"{safe_model}_final.txt"
            initial_path.write_text(initial + "\n", encoding="utf-8")
            final = initial
            retry_triggered = False
            retry_reason = None
            if enable_length_retry and should_retry_length(initial, sample):
                retry_triggered = True
                retry_reason = length_retry_reason(initial, sample)
                retry_prompt = concise_rewrite_prompt(sample=sample, output=initial)
                retry = _chat_completion(
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
                ).strip()
                (sample_dir / f"{safe_model}_retry.txt").write_text(retry + "\n", encoding="utf-8")
                final = retry
            final_path.write_text(final + "\n", encoding="utf-8")
            if len(samples) == 1:
                (run_dir / f"translation_{safe_model}.txt").write_text(final + "\n", encoding="utf-8")
            metadata = {
                "path": str(final_path.relative_to(run_dir)),
                "initial_path": str(initial_path.relative_to(run_dir)),
                "retry_path": str((sample_dir / f"{safe_model}_retry.txt").relative_to(run_dir))
                if retry_triggered
                else None,
                "source_chars_sent": len(source),
                "reference_char_count": sample["target_char_count"],
                "target_length_min": sample["target_length_min"],
                "target_length_max": sample["target_length_max"],
                "initial_output_char_count": len(initial),
                "output_char_count": len(final),
                "output_reference_ratio": round(len(final) / max(sample["target_char_count"], 1), 3),
                "estimated_prompt_chars": len(source) + len(system_prompt),
                "estimated_output_chars": len(final),
                "retry_triggered": retry_triggered,
                "retry_reason": retry_reason,
                "prompt_iteration": prompt_iteration,
                "masked_api_key": mask_api_key(
                    os.getenv(provider.api_key_env) or load_dotenv_local().get(provider.api_key_env)
                ),
            }
            outputs_by_sample[sample_id][model] = metadata
            outputs_by_model.setdefault(model, metadata)

    metadata_payload = {
        "models": models,
        "samples": outputs_by_sample,
        "prompt_iteration": prompt_iteration,
        "enable_length_retry": enable_length_retry,
        "target_length_tolerance": target_length_tolerance,
    }
    write_json(translations_root / "translation_metadata.json", metadata_payload)
    return {
        "run_dir": str(run_dir),
        "outputs": outputs_by_sample,
        "outputs_by_model": outputs_by_model,
        "samples": samples,
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


def max_tokens_for_sample(sample: dict[str, Any], *, retry: bool = False) -> int:
    divisor = 7.0 if retry else 6.0
    return max(320, int(sample["target_length_max"] / divisor))


def translation_system_prompt(
    run_dir: Path,
    *,
    sample: dict[str, Any] | None = None,
    prompt_iteration: int = 1,
    target_length_tolerance: float = 0.2,
) -> str:
    lines = [
        "Translate Chinese literary prose into natural Vietnamese.",
        "Return only the Vietnamese translation.",
        "Do not add translator notes.",
        "Use concise Vietnamese webnovel style.",
        "Do not expand scenes, add explanations, or paraphrase beyond the source.",
        "Do not expand, explain, embellish, or paraphrase beyond the source.",
        "Keep system panel/bracket formatting compact.",
        "Preserve paragraph, dialogue, and system panel formatting.",
    ]
    if sample:
        lines.extend(
            [
                "Hard length constraint:",
                f"- Target range: {sample['target_length_min']}-{sample['target_length_max']} Vietnamese characters.",
                f"- Reference length: {sample['target_char_count']} characters.",
                f"- Source length: {sample['source_char_count']} characters.",
                f"- Reference/source ratio: {sample['target_source_length_ratio']}.",
                f"- Aim for about {sample['paragraph_count_target']} paragraphs.",
                f"- Do not exceed {sample['target_length_max']} characters unless absolutely necessary.",
                "- Your answer fails if it is much longer than the target range.",
                "- Do not preserve every source paragraph or line break.",
                "- Merge adjacent short source lines into concise Vietnamese paragraphs.",
                "- Keep only bracket/stat panels as compact standalone lines when useful.",
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
    for term in (glossary or {}).get("fixed_terms", []):
        source_term = str(term.get("source", ""))
        target_term = str(term.get("target", ""))
        if source_term and target_term and source_term in source and target_term.lower() not in output.lower():
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
            )
            score["sample_id"] = sample_id
            score["chapter_id"] = sample["chapter_id"]
            score["translation_path"] = output_meta["path"]
            score["initial_output_char_count"] = output_meta.get("initial_output_char_count")
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
        model_scores[model] = {
            "average_score": average_score,
            "sample_count": len(per_sample),
            "pass": all_samples_pass and average_score >= PASS_THRESHOLDS["total_score"],
            "retry_triggered": any(sample_score["retry_triggered"] for sample_score in per_sample),
            "samples": per_sample,
            "final_pass_fail_reason": "pass"
            if all_samples_pass and average_score >= PASS_THRESHOLDS["total_score"]
            else model_fail_reason(per_sample, average_score),
        }
    if not model_scores:
        raise ValueError("No translation outputs found. Run translate-sample first.")
    best_model = max(model_scores, key=lambda key: model_scores[key]["average_score"])
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
            "| Model | Sample | Total | Pass | Ratio | Output | Reference | Meaning | Omission | Terminology | Fluency | Style | Retry | Reason |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for model, model_report in report["models"].items():
        for score in model_report["samples"]:
            lines.append(
                f"| {model} | {score['sample_id']} | {score['total_score']} | {score['pass']} | "
                f"{score['output_reference_ratio']} | {score['output_char_count']} | "
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
                f"ratio={score['output_reference_ratio']}, output={score['output_char_count']}, "
                f"reference={score['reference_char_count']}, retry={score['retry_triggered']}, "
                f"length_penalty={score['length_penalty_reason']}, "
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
        prompt_iteration=ACTIVE_PROMPT_ITERATION,
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
            "MVP4.6 strict compact prompt with fixed glossary, paragraph merging, "
            "lower output-token ceiling, and one concise retry."
        ),
        why="Earlier MVP4.6 iterations still produced outputs 1.5x-1.8x the aligned reference length.",
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
