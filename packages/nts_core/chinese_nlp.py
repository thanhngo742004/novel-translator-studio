from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Protocol
import urllib.error
import urllib.request

from nts_core.config import NlpSettings, load_nlp_config
from nts_core.projects import get_project_by_slug
from nts_core.text_import import get_chapter, list_chapters, list_segments, normalize_text
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


HEURISTICS_VERSION = "mvp5e-v1"
LTP_PROVIDER_VERSION = "ltp-server-local"
FALLBACK_PROVIDER_VERSION = "fallback-simple-v1"
CHINESE_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
CHINESE_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
SYSTEM_PANEL_RE = re.compile(r"【[^】]+】")
CHINESE_RUN_RE = re.compile(r"[\u3400-\u9fff]{2,8}")
DOMAIN_SUFFIX_RE = re.compile(r"[\u3400-\u9fff]{1,8}(?:宗|门|峰|池|谷|派|宫|殿|仙子|真人|老祖)")
NAME_LIKE_RE = re.compile(r"[\u3400-\u9fff]{1,4}(?:老|头|绝|璇|鸽|清|宗)")


POS_MAP = {
    "nh": "name",
    "nr": "name",
    "nz": "noun",
    "ns": "noun",
    "ni": "noun",
    "nt": "noun",
    "nl": "noun",
    "n": "noun",
    "v": "verb",
    "vd": "verb",
    "vn": "verb",
    "a": "adj",
    "ad": "adj",
    "an": "adj",
    "r": "pron",
    "m": "num",
    "q": "measure",
    "u": "particle",
    "p": "particle",
    "c": "particle",
    "e": "particle",
    "y": "particle",
    "o": "particle",
    "wp": "punctuation",
}

NER_TYPE_MAP = {
    "nh": "person",
    "nr": "person",
    "per": "person",
    "person": "person",
    "ns": "place",
    "loc": "place",
    "location": "place",
    "ni": "organization",
    "org": "organization",
    "organization": "organization",
}


class AnalyzerProvider(Protocol):
    provider_kind: str
    provider_version: str

    def analyze_sentences(self, sentences: list[str]) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class SidecarStatus:
    healthy: bool
    base_url: str
    provider: str
    start_attempted: bool = False
    started_by_nts: bool = False
    pid: int | None = None
    degraded: bool = False
    error: str | None = None
    warnings: tuple[str, ...] = ()
    command: str | None = None
    executable: str | None = None
    working_dir: str | None = None
    startup_elapsed_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "base_url": self.base_url,
            "provider": self.provider,
            "start_attempted": self.start_attempted,
            "started_by_nts": self.started_by_nts,
            "pid": self.pid,
            "degraded": self.degraded,
            "error": self.error,
            "warnings": list(self.warnings),
            "command": self.command,
            "executable": self.executable,
            "working_dir": self.working_dir,
            "startup_elapsed_seconds": self.startup_elapsed_seconds,
        }


_SIDECAR_SESSION_CACHE: dict[str, SidecarStatus] = {}


def _sidecar_cache_key(ltp: Any) -> str:
    return "|".join(
        [
            str(ltp.base_url).rstrip("/"),
            str(ltp.working_dir or ""),
            str(ltp.executable or ""),
            str(ltp.start_command or ""),
            str(ltp.request_timeout_seconds),
            str(ltp.max_sentences_per_request),
        ]
    )


def clear_sidecar_session_cache(base_url: str | None = None) -> None:
    if base_url is None:
        _SIDECAR_SESSION_CACHE.clear()
        return
    normalized = base_url.rstrip("/")
    for key in list(_SIDECAR_SESSION_CACHE):
        if key == normalized or key.startswith(normalized + "|"):
            _SIDECAR_SESSION_CACHE.pop(key, None)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _markdown_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _safe_id(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "unknown"


def normalize_pos(provider_pos: str | None) -> str:
    if not provider_pos:
        return "other"
    tag = provider_pos.lower()
    if tag in POS_MAP:
        return POS_MAP[tag]
    if tag.startswith("n"):
        return "noun"
    if tag.startswith("v"):
        return "verb"
    if tag.startswith("a"):
        return "adj"
    return "other"


def _split_entity_tag(tag: str) -> tuple[str, str | None]:
    if not tag or tag == "O":
        return "O", None
    if "-" in tag:
        prefix, entity_type = tag.split("-", 1)
    else:
        prefix, entity_type = tag[:1], tag[1:] or None
    return prefix.upper(), entity_type.lower() if entity_type else None


def convert_ner_tags(tokens: list[dict[str, Any]], tags: list[str]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    active_start: int | None = None
    active_type: str | None = None
    active_tokens: list[dict[str, Any]] = []

    def close_span() -> None:
        nonlocal active_start, active_type, active_tokens
        if active_start is None or not active_tokens:
            return
        start_token = active_tokens[0]
        end_token = active_tokens[-1]
        text = "".join(token["text"] for token in active_tokens)
        spans.append(
            {
                "text": text,
                "start": start_token["start"],
                "end": end_token["end"],
                "entity_type": NER_TYPE_MAP.get(active_type or "", "other"),
                "token_start": active_start,
                "token_end": active_start + len(active_tokens),
            }
        )
        active_start = None
        active_type = None
        active_tokens = []

    for index, token in enumerate(tokens):
        tag = tags[index] if index < len(tags) else "O"
        prefix, entity_type = _split_entity_tag(tag)
        if prefix == "O" or entity_type is None:
            close_span()
            continue
        if prefix == "S":
            close_span()
            spans.append(
                {
                    "text": token["text"],
                    "start": token["start"],
                    "end": token["end"],
                    "entity_type": NER_TYPE_MAP.get(entity_type, "other"),
                    "token_start": index,
                    "token_end": index + 1,
                }
            )
            continue
        if prefix == "B" or active_start is None or active_type != entity_type:
            close_span()
            active_start = index
            active_type = entity_type
            active_tokens = [token]
            if prefix == "E":
                close_span()
            continue
        active_tokens.append(token)
        if prefix == "E":
            close_span()
    close_span()
    return spans


def split_text_segments(text: str) -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    paragraphs = [part for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    if not paragraphs and normalized.strip():
        paragraphs = [normalized.strip()]
    segments: list[dict[str, Any]] = []
    cursor = 0
    for index, paragraph in enumerate(paragraphs, start=1):
        start = normalized.find(paragraph, cursor)
        if start < 0:
            start = cursor
        end = start + len(paragraph)
        segments.append(
            {
                "segment_id": f"seg_{index:04d}",
                "text": paragraph,
                "start": start,
                "end": end,
            }
        )
        cursor = end
    return segments


def split_chinese_sentences(segment_text: str, segment_start: int) -> list[dict[str, Any]]:
    sentences: list[dict[str, Any]] = []
    for match in CHINESE_SENTENCE_RE.finditer(segment_text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        local_start = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        start = segment_start + local_start
        end = start + len(sentence)
        sentences.append({"text": sentence, "start": start, "end": end})
    if not sentences and segment_text.strip():
        stripped = segment_text.strip()
        start = segment_start + segment_text.find(stripped)
        sentences.append({"text": stripped, "start": start, "end": start + len(stripped)})
    return sentences


def _positions_for_tokens(sentence: str, sentence_start: int, words: list[str]) -> list[dict[str, int]]:
    positions: list[dict[str, int]] = []
    cursor = 0
    for word in words:
        local = sentence.find(word, cursor)
        if local < 0:
            local = cursor
        start = sentence_start + local
        end = start + len(word)
        positions.append({"start": start, "end": end})
        cursor = max(local + len(word), cursor)
    return positions


def _tokens_from_provider(
    sentence: str,
    sentence_start: int,
    words: list[str],
    pos_tags: list[str],
) -> list[dict[str, Any]]:
    positions = _positions_for_tokens(sentence, sentence_start, words)
    tokens: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        provider_pos = pos_tags[index] if index < len(pos_tags) else None
        tokens.append(
            {
                "text": word,
                "start": positions[index]["start"],
                "end": positions[index]["end"],
                "provider_pos": provider_pos,
                "norm_pos": normalize_pos(provider_pos),
            }
        )
    return tokens


def _fallback_words(sentence: str) -> list[str]:
    words: list[str] = []
    cursor = 0
    while cursor < len(sentence):
        char = sentence[cursor]
        if char.isspace():
            cursor += 1
            continue
        if char in "【】（）()[]，。！？!?；;：:、,.\"“”'":
            words.append(char)
            cursor += 1
            continue
        if CHINESE_CHAR_RE.match(char):
            panel = SYSTEM_PANEL_RE.match(sentence, cursor)
            if panel:
                words.append(panel.group(0))
                cursor = panel.end()
                continue
            words.append(char)
            cursor += 1
            continue
        match = re.match(r"[A-Za-z0-9_+-]+", sentence[cursor:])
        if match:
            words.append(match.group(0))
            cursor += len(match.group(0))
        else:
            words.append(char)
            cursor += 1
    return words


def _fallback_pos(word: str) -> str:
    if word in "，。！？!?；;：:、,.\"“”'【】（）()[]":
        return "wp"
    if re.fullmatch(r"[0-9一二三四五六七八九十百千万亿零]+", word):
        return "m"
    if DOMAIN_SUFFIX_RE.fullmatch(word) or NAME_LIKE_RE.fullmatch(word):
        return "nh"
    if CHINESE_CHAR_RE.search(word):
        return "n"
    return "x"


def _candidate_id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def _candidate(text: str, candidate_type: str, confidence: float, source: str) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(candidate_type, text),
        "text": text,
        "candidate_type": candidate_type,
        "confidence": round(confidence, 3),
        "source": source,
    }


def _derive_sentence_candidates(sentence: str, entity_spans: list[dict[str, Any]]) -> tuple[list, list, list]:
    entity_candidates = [
        _candidate(span["text"], span.get("entity_type", "entity"), 0.72, "ner_or_heuristic")
        for span in entity_spans
        if len(span.get("text", "")) >= 2
    ]
    term_candidates: list[dict[str, Any]] = []
    for match in DOMAIN_SUFFIX_RE.finditer(sentence):
        term = match.group(0)
        term_candidates.append(_candidate(term, "domain_suffix", 0.62, "suffix_heuristic"))
        if len(term) > 4:
            suffix_tail = term[-3:]
            if CHINESE_CHAR_RE.search(suffix_tail):
                term_candidates.append(
                    _candidate(suffix_tail, "domain_suffix", 0.66, "suffix_tail_heuristic")
                )
    phrase_candidates = [
        _candidate(match.group(0), "system_panel", 0.8, "bracket_span")
        for match in SYSTEM_PANEL_RE.finditer(sentence)
    ]
    return entity_candidates, term_candidates, phrase_candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_text: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (candidate.get("text", ""), candidate.get("candidate_type", ""))
        existing = by_text.get(key)
        if existing is None or candidate.get("confidence", 0) > existing.get("confidence", 0):
            by_text[key] = candidate
    return sorted(by_text.values(), key=lambda item: (-float(item.get("confidence", 0)), item["text"]))


def _chapter_repeated_candidates(text: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for run in CHINESE_RUN_RE.findall(text):
        for size in (2, 3, 4):
            if len(run) < size:
                continue
            for index in range(0, len(run) - size + 1):
                gram = run[index : index + size]
                if SYSTEM_PANEL_RE.search(gram):
                    continue
                counts[gram] = counts.get(gram, 0) + 1
    candidates = []
    for gram, count in counts.items():
        if count >= 2:
            confidence = min(0.7, 0.45 + count * 0.05)
            candidate = _candidate(gram, "repeated_ngram", confidence, "frequency_heuristic")
            candidate["count"] = count
            candidates.append(candidate)
    return _dedupe_candidates(candidates)[:50]


class LtpServerAnalyzer:
    provider_kind = "ltp_server"
    provider_version = LTP_PROVIDER_VERSION

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: int = 15,
        max_sentences_per_request: int = 512,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.max_sentences_per_request = max_sentences_per_request

    def _post_analyze(self, text: str) -> Any:
        payload = text.encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/analyze",
            data=payload,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)

    def health_check(self) -> tuple[bool, str | None]:
        try:
            data = self._post_analyze("他叫汤姆。")
        except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            return False, str(exc)
        rows = _ltp_result_objects(data, expected_count=1)
        if not rows:
            return False, "ltp-server response missing cws/pos/ner"
        row = rows[0]
        if _valid_ltp_result_object(row):
            return True, None
        return False, "ltp-server response has malformed cws/pos/ner arrays"

    def analyze_sentences(self, sentences: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index in range(0, len(sentences), self.max_sentences_per_request):
            batch = sentences[index : index + self.max_sentences_per_request]
            data = self._post_analyze("\n".join(batch))
            rows = _ltp_result_objects(data, expected_count=len(batch))
            if not rows:
                raise ValueError("ltp-server response missing cws/pos/ner")
            for batch_index, sentence in enumerate(batch):
                row = rows[min(batch_index, len(rows) - 1)]
                cws = _as_sentence_lists(row.get("cws"), len(batch))
                pos = _as_sentence_lists(row.get("pos"), len(batch))
                ner = _as_sentence_lists(row.get("ner"), len(batch))
                sentence_row_index = 0 if len(rows) == len(batch) else batch_index
                results.append(
                    {
                        "text": sentence,
                        "words": cws[sentence_row_index] if sentence_row_index < len(cws) else [],
                        "pos": pos[sentence_row_index] if sentence_row_index < len(pos) else [],
                        "ner": ner[sentence_row_index] if sentence_row_index < len(ner) else [],
                        "warnings": [],
                    }
                )
        return results


def _valid_ltp_result_object(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not all(key in value for key in ("cws", "pos", "ner")):
        return False
    return all(isinstance(value.get(key), list) and len(value.get(key) or []) > 0 for key in ("cws", "pos", "ner"))


def _ltp_result_objects(data: Any, expected_count: int) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data] if _valid_ltp_result_object(data) else []
    if isinstance(data, list):
        rows = [item for item in data if isinstance(item, dict) and _valid_ltp_result_object(item)]
        if len(rows) == 1 and expected_count > 1:
            return rows
        return rows
    return []


def _as_sentence_lists(value: Any, expected_count: int) -> list[list[str]]:
    if value is None:
        return [[] for _ in range(expected_count)]
    if isinstance(value, list) and (not value or all(isinstance(item, str) for item in value)):
        return [list(value)] + [[] for _ in range(max(0, expected_count - 1))]
    if isinstance(value, list):
        rows: list[list[str]] = []
        for item in value:
            if isinstance(item, list):
                rows.append([str(part) for part in item])
            else:
                rows.append([str(item)])
        while len(rows) < expected_count:
            rows.append([])
        return rows
    return [[] for _ in range(expected_count)]


class FallbackSimpleAnalyzer:
    provider_kind = "fallback_simple"
    provider_version = FALLBACK_PROVIDER_VERSION

    def analyze_sentences(self, sentences: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for sentence in sentences:
            words = _fallback_words(sentence)
            results.append(
                {
                    "text": sentence,
                    "words": words,
                    "pos": [_fallback_pos(word) for word in words],
                    "ner": [_fallback_ner_tag(word) for word in words],
                    "warnings": ["fallback_simple_degraded_analysis"],
                }
            )
        return results


def _fallback_ner_tag(word: str) -> str:
    if DOMAIN_SUFFIX_RE.fullmatch(word):
        if word.endswith(("宗", "门", "派", "宫", "殿")):
            return "S-Ni"
        if word.endswith(("峰", "池", "谷")):
            return "S-Ns"
        return "S-Nh"
    if NAME_LIKE_RE.fullmatch(word) and len(word) >= 2:
        return "S-Nh"
    return "O"


class NlpSidecarManager:
    def __init__(self, config: NlpSettings) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None

    def ensure_ltp_server(self, *, auto_start: bool | None = None) -> SidecarStatus:
        ltp = self.config.ltp_server
        cache_key = _sidecar_cache_key(ltp)
        cached = _SIDECAR_SESSION_CACHE.get(cache_key)
        if cached and cached.healthy:
            return cached
        analyzer = LtpServerAnalyzer(
            base_url=ltp.base_url,
            request_timeout_seconds=ltp.request_timeout_seconds,
            max_sentences_per_request=ltp.max_sentences_per_request,
        )
        healthy, error = analyzer.health_check()
        if healthy:
            status = SidecarStatus(healthy=True, base_url=ltp.base_url, provider="ltp_server")
            _SIDECAR_SESSION_CACHE[cache_key] = status
            return status

        should_start = self.config.auto_start if auto_start is None else auto_start
        if not should_start:
            return SidecarStatus(
                healthy=False,
                base_url=ltp.base_url,
                provider="ltp_server",
                degraded=self.config.fallback.enabled,
                error=error,
                warnings=("ltp_server_unavailable",),
            )

        working_dir = Path(ltp.working_dir).expanduser() if ltp.working_dir else None
        if working_dir is None or not working_dir.exists():
            return SidecarStatus(
                healthy=False,
                base_url=ltp.base_url,
                provider="ltp_server",
                start_attempted=True,
                degraded=self.config.fallback.enabled,
                error=f"ltp-server working_dir not found: {working_dir}",
                warnings=("ltp_server_start_failed",),
            )
        executable = Path(ltp.executable).expanduser() if ltp.executable else None
        use_executable = executable is not None and executable.exists()
        command: str | list[str]
        shell = not use_executable
        if use_executable:
            command = [str(executable)]
            command_text = str(executable)
        else:
            command = ltp.start_command
            command_text = ltp.start_command

        start_time = time.monotonic()
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
            self.process = subprocess.Popen(
                command,
                cwd=str(working_dir),
                shell=shell,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            return SidecarStatus(
                healthy=False,
                base_url=ltp.base_url,
                provider="ltp_server",
                start_attempted=True,
                degraded=self.config.fallback.enabled,
                error=str(exc),
                warnings=("ltp_server_start_failed",),
                command=command_text,
                executable=str(executable) if executable else None,
                working_dir=str(working_dir),
                startup_elapsed_seconds=round(time.monotonic() - start_time, 3),
            )

        deadline = time.monotonic() + ltp.startup_timeout_seconds
        last_error = error
        while time.monotonic() < deadline:
            healthy, last_error = analyzer.health_check()
            if healthy:
                status = SidecarStatus(
                    healthy=True,
                    base_url=ltp.base_url,
                    provider="ltp_server",
                    start_attempted=True,
                    started_by_nts=True,
                    pid=self.process.pid if self.process else None,
                    command=command_text,
                    executable=str(executable) if executable else None,
                    working_dir=str(working_dir),
                    startup_elapsed_seconds=round(time.monotonic() - start_time, 3),
                )
                _SIDECAR_SESSION_CACHE[cache_key] = status
                return status
            time.sleep(1)

        return SidecarStatus(
            healthy=False,
            base_url=ltp.base_url,
            provider="ltp_server",
            start_attempted=True,
            started_by_nts=True,
            pid=self.process.pid if self.process else None,
            degraded=self.config.fallback.enabled,
            error=last_error or "ltp-server startup timeout",
            warnings=("ltp_server_start_timeout",),
            command=command_text,
            executable=str(executable) if executable else None,
            working_dir=str(working_dir),
            startup_elapsed_seconds=round(time.monotonic() - start_time, 3),
        )


def build_normalized_analysis(
    *,
    text: str,
    project_slug: str | None,
    chapter_id: str | None,
    provider: AnalyzerProvider,
    degraded: bool,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    source_sha = _sha256_text(text)
    segments_raw = split_text_segments(text)
    sentence_specs: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for segment in segments_raw:
        sentence_ids: list[str] = []
        segment_sentences = split_chinese_sentences(segment["text"], segment["start"])
        for sentence_index, sentence in enumerate(segment_sentences, start=1):
            sentence_id = f"{segment['segment_id']}_sent_{sentence_index:04d}"
            sentence["sentence_id"] = sentence_id
            sentence["segment_id"] = segment["segment_id"]
            sentence_specs.append(sentence)
            sentence_ids.append(sentence_id)
        segments.append({"segment_id": segment["segment_id"], "sentence_ids": sentence_ids})

    provider_rows = provider.analyze_sentences([sentence["text"] for sentence in sentence_specs])
    normalized_sentences: list[dict[str, Any]] = []
    chapter_entity_candidates: list[dict[str, Any]] = []
    chapter_term_candidates: list[dict[str, Any]] = []
    chapter_phrase_candidates: list[dict[str, Any]] = []

    for index, spec in enumerate(sentence_specs):
        provider_row = provider_rows[index] if index < len(provider_rows) else {}
        words = [str(word) for word in provider_row.get("words") or _fallback_words(spec["text"])]
        pos_tags = [str(tag) for tag in provider_row.get("pos") or []]
        ner_tags = [str(tag) for tag in provider_row.get("ner") or []]
        tokens = _tokens_from_provider(spec["text"], spec["start"], words, pos_tags)
        entity_spans = convert_ner_tags(tokens, ner_tags)
        entity_candidates, term_candidates, phrase_candidates = _derive_sentence_candidates(
            spec["text"], entity_spans
        )
        chapter_entity_candidates.extend(entity_candidates)
        chapter_term_candidates.extend(term_candidates)
        chapter_phrase_candidates.extend(phrase_candidates)
        normalized_sentences.append(
            {
                "sentence_id": spec["sentence_id"],
                "segment_id": spec["segment_id"],
                "text": spec["text"],
                "start": spec["start"],
                "end": spec["end"],
                "tokens": tokens,
                "ner_tags": ner_tags or ["O" for _ in tokens],
                "entity_spans": entity_spans,
                "phrase_candidates": phrase_candidates,
                "term_candidates": term_candidates,
                "warnings": list(provider_row.get("warnings") or []),
            }
        )

    chapter_term_candidates.extend(_chapter_repeated_candidates(text))
    analysis_warnings = list(warnings or [])
    if degraded:
        analysis_warnings.append("degraded_analysis")
    return {
        "meta": {
            "project_slug": project_slug,
            "chapter_id": chapter_id,
            "source_sha256": source_sha,
            "provider": provider.provider_kind,
            "provider_version": provider.provider_version,
            "heuristics_version": HEURISTICS_VERSION,
            "degraded": degraded,
            "created_at": utc_now(),
            "warnings": analysis_warnings,
        },
        "segments": segments,
        "sentences": normalized_sentences,
        "chapter_candidates": {
            "entity_candidates": _dedupe_candidates(chapter_entity_candidates)[:50],
            "term_candidates": _dedupe_candidates(chapter_term_candidates)[:80],
            "phrase_candidates": _dedupe_candidates(chapter_phrase_candidates)[:50],
        },
    }


def _resolve_provider(
    config: NlpSettings,
    provider_kind: str | None,
    auto_start: bool | None,
    *,
    allow_fallback: bool = True,
) -> tuple[AnalyzerProvider, SidecarStatus | None, bool, list[str]]:
    kind = provider_kind or config.provider
    warnings: list[str] = []
    if kind == "fallback_simple":
        return FallbackSimpleAnalyzer(), None, True, ["fallback_simple_requested"]
    if kind != "ltp_server":
        raise ValueError(f"Unsupported NLP provider: {kind}")

    manager = NlpSidecarManager(config)
    status = manager.ensure_ltp_server(auto_start=auto_start)
    if status.healthy:
        return (
            LtpServerAnalyzer(
                base_url=config.ltp_server.base_url,
                request_timeout_seconds=config.ltp_server.request_timeout_seconds,
                max_sentences_per_request=config.ltp_server.max_sentences_per_request,
            ),
            status,
            False,
            [],
        )
    if allow_fallback and config.fallback.enabled:
        warnings.extend(status.warnings)
        if status.error:
            warnings.append(f"ltp_server_error:{status.error}")
        return FallbackSimpleAnalyzer(), status, True, warnings
    raise ValueError(status.error or "ltp-server is unavailable and fallback is disabled")


def analyze_text(
    workspace: Workspace | None,
    *,
    text: str,
    project_slug: str | None = None,
    chapter_id: str | None = None,
    provider_kind: str | None = None,
    auto_start: bool | None = None,
) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("Text is empty.")
    config = load_nlp_config(workspace=workspace)
    provider, sidecar_status, degraded, warnings = _resolve_provider(
        config, provider_kind, auto_start, allow_fallback=True
    )
    try:
        analysis = build_normalized_analysis(
            text=text,
            project_slug=project_slug,
            chapter_id=chapter_id,
            provider=provider,
            degraded=degraded,
            warnings=warnings,
        )
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        if (provider_kind or config.provider) != "ltp_server":
            raise
        clear_sidecar_session_cache(config.ltp_server.base_url)
        retry_provider, retry_status, retry_degraded, retry_warnings = _resolve_provider(
            config,
            "ltp_server",
            auto_start,
            allow_fallback=True,
        )
        retry_warnings.append(f"ltp_server_analyze_retry_after_failure:{exc}")
        analysis = build_normalized_analysis(
            text=text,
            project_slug=project_slug,
            chapter_id=chapter_id,
            provider=retry_provider,
            degraded=retry_degraded,
            warnings=retry_warnings,
        )
        sidecar_status = retry_status
    if sidecar_status:
        analysis["sidecar_status"] = sidecar_status.to_dict()
    return analysis


def _chapter_text(workspace: Workspace, chapter_id: str) -> tuple[dict[str, Any], str]:
    chapter = get_chapter(workspace, chapter_id)
    segments = list_segments(workspace, chapter_id=chapter_id)
    text = "\n\n".join(segment["normalized_text"] for segment in segments)
    return chapter, text


def resolve_chapter_id(workspace: Workspace, *, project_slug: str, chapter: str) -> str:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        if chapter.startswith("chapter_"):
            row = conn.execute(
                "SELECT id FROM chapters WHERE id = ? AND project_id = ?",
                (chapter, project["id"]),
            ).fetchone()
        else:
            try:
                chapter_no = int(chapter)
            except ValueError:
                chapter_no = None
            if chapter_no is not None:
                row = conn.execute(
                    "SELECT id FROM chapters WHERE chapter_no = ? AND project_id = ? ORDER BY id LIMIT 1",
                    (chapter_no, project["id"]),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM chapters WHERE id = ? AND project_id = ?",
                    (chapter, project["id"]),
                ).fetchone()
    if row is None:
        raise ValueError(f"Chapter not found for project {project_slug}: {chapter}")
    return str(row["id"])


def _chapter_cache_path(workspace: Workspace, project_slug: str, chapter_key: str) -> Path:
    return workspace.path / "artifacts" / "nlp" / project_slug / f"{_safe_id(chapter_key)}.ltp.json"


def _manifest_path(workspace: Workspace, project_slug: str) -> Path:
    return workspace.path / "artifacts" / "nlp" / project_slug / "nlp_cache_manifest.json"


def _report_path(workspace: Workspace, project_slug: str) -> Path:
    return workspace.path / "artifacts" / "nlp" / project_slug / "nlp_analysis_report.md"


def _analysis_cache_valid(path: Path, source_sha: str, provider_kind: str) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    meta = data.get("meta", {})
    return (
        meta.get("source_sha256") == source_sha
        and meta.get("heuristics_version") == HEURISTICS_VERSION
        and meta.get("provider") == provider_kind
    )


def _record_nlp_run(
    workspace: Workspace,
    *,
    project_slug: str,
    chapter: dict[str, Any],
    analysis: dict[str, Any],
    artifact_path: Path,
    manifest_path: Path,
    status: str,
) -> None:
    token_count = sum(len(sentence.get("tokens", [])) for sentence in analysis.get("sentences", []))
    sentence_count = len(analysis.get("sentences", []))
    meta = analysis.get("meta", {})
    project_id = chapter.get("project_id")
    now = utc_now()
    with connection(workspace.db_path) as conn:
        conn.execute(
            """
            INSERT INTO nlp_analysis_runs (
                id, project_id, project_slug, chapter_id, provider_kind, provider_version,
                heuristics_version, source_sha256, artifact_path, manifest_path, status,
                degraded, sentence_count, token_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("nlp"),
                project_id,
                project_slug,
                chapter["id"],
                meta.get("provider"),
                meta.get("provider_version"),
                meta.get("heuristics_version"),
                meta.get("source_sha256"),
                artifact_path.relative_to(workspace.path).as_posix(),
                manifest_path.relative_to(workspace.path).as_posix(),
                status,
                1 if meta.get("degraded") else 0,
                sentence_count,
                token_count,
                now,
                now,
            ),
        )
        conn.commit()


def _update_manifest(
    workspace: Workspace,
    *,
    project_slug: str,
    entries: list[dict[str, Any]],
    sidecar_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_path = _manifest_path(workspace, project_slug)
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    by_chapter = {entry["chapter_id"]: entry for entry in existing.get("chapters", [])}
    for entry in entries:
        by_chapter[entry["chapter_id"]] = entry
    chapters = sorted(by_chapter.values(), key=lambda item: str(item.get("chapter_no", "")))
    manifest = {
        "schema_version": "nlp_cache_manifest_v1",
        "project_slug": project_slug,
        "heuristics_version": HEURISTICS_VERSION,
        "created_at": existing.get("created_at") or utc_now(),
        "updated_at": utc_now(),
        "coverage_count": len(chapters),
        "degraded_chapter_count": sum(1 for entry in chapters if entry.get("degraded")),
        "sentence_count": sum(int(entry.get("sentence_count", 0)) for entry in chapters),
        "token_count": sum(int(entry.get("token_count", 0)) for entry in chapters),
        "chapters": chapters,
        "sidecar_status": sidecar_status,
    }
    _json_write(manifest_path, manifest)
    _write_cache_report(workspace, project_slug, manifest)
    return manifest


def _write_cache_report(workspace: Workspace, project_slug: str, manifest: dict[str, Any]) -> None:
    lines = [
        f"# NLP Cache Report: {project_slug}",
        "",
        f"- Coverage: {manifest.get('coverage_count', 0)} chapter(s)",
        f"- Degraded chapters: {manifest.get('degraded_chapter_count', 0)}",
        f"- Sentences: {manifest.get('sentence_count', 0)}",
        f"- Tokens: {manifest.get('token_count', 0)}",
        "",
        "| Chapter | Provider | Degraded | Sentences | Tokens | Artifact |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for entry in manifest.get("chapters", []):
        lines.append(
            "| {chapter_no} | {provider} | {degraded} | {sentences} | {tokens} | {artifact} |".format(
                chapter_no=entry.get("chapter_no"),
                provider=entry.get("provider"),
                degraded=entry.get("degraded"),
                sentences=entry.get("sentence_count"),
                tokens=entry.get("token_count"),
                artifact=entry.get("artifact_path"),
            )
        )
    _markdown_write(_report_path(workspace, project_slug), lines)


def analyze_chapter(
    workspace: Workspace,
    *,
    project_slug: str,
    chapter_ref: str,
    provider_kind: str | None = None,
    auto_start: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    chapter_id = resolve_chapter_id(workspace, project_slug=project_slug, chapter=chapter_ref)
    chapter, text = _chapter_text(workspace, chapter_id)
    config = load_nlp_config(workspace=workspace)
    requested_kind = provider_kind or config.provider
    source_sha = _sha256_text(text)
    cache_path = _chapter_cache_path(workspace, project_slug, str(chapter.get("chapter_no") or chapter_id))
    if not force and _analysis_cache_valid(cache_path, source_sha, requested_kind):
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return {
            "status": "cache_hit",
            "chapter_id": chapter_id,
            "chapter_no": chapter.get("chapter_no"),
            "artifact_path": str(cache_path),
            "analysis": cached,
        }

    analysis = analyze_text(
        workspace,
        text=text,
        project_slug=project_slug,
        chapter_id=chapter_id,
        provider_kind=provider_kind,
        auto_start=auto_start,
    )
    _json_write(cache_path, analysis)
    sentence_count = len(analysis.get("sentences", []))
    token_count = sum(len(sentence.get("tokens", [])) for sentence in analysis.get("sentences", []))
    entry = {
        "chapter_id": chapter_id,
        "chapter_no": chapter.get("chapter_no"),
        "title": chapter.get("title"),
        "source_sha256": analysis["meta"]["source_sha256"],
        "provider": analysis["meta"]["provider"],
        "provider_version": analysis["meta"]["provider_version"],
        "heuristics_version": HEURISTICS_VERSION,
        "degraded": analysis["meta"]["degraded"],
        "sentence_count": sentence_count,
        "token_count": token_count,
        "artifact_path": str(cache_path),
        "updated_at": utc_now(),
    }
    manifest = _update_manifest(
        workspace,
        project_slug=project_slug,
        entries=[entry],
        sidecar_status=analysis.get("sidecar_status"),
    )
    _record_nlp_run(
        workspace,
        project_slug=project_slug,
        chapter=chapter,
        analysis=analysis,
        artifact_path=cache_path,
        manifest_path=_manifest_path(workspace, project_slug),
        status="success",
    )
    return {
        "status": "analyzed",
        "chapter_id": chapter_id,
        "chapter_no": chapter.get("chapter_no"),
        "artifact_path": str(cache_path),
        "manifest_path": str(_manifest_path(workspace, project_slug)),
        "sentence_count": sentence_count,
        "token_count": token_count,
        "degraded": analysis["meta"]["degraded"],
        "provider": analysis["meta"]["provider"],
        "manifest": {
            "coverage_count": manifest["coverage_count"],
            "degraded_chapter_count": manifest["degraded_chapter_count"],
        },
    }


def parse_chapter_range(value: str) -> list[str]:
    chapters: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            step = 1 if end >= start else -1
            chapters.extend(str(number) for number in range(start, end + step, step))
        else:
            chapters.append(part)
    return chapters


def cache_build(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str,
    missing_only: bool = False,
    force: bool = False,
    provider_kind: str | None = None,
    auto_start: bool | None = None,
) -> dict[str, Any]:
    initialize_database(workspace.db_path)
    project = get_project_by_slug(workspace, project_slug)
    config = load_nlp_config(workspace=workspace)
    requested_kind = provider_kind or config.provider
    effective_provider_kind = requested_kind
    sidecar_status: dict[str, Any] | None = None
    if requested_kind == "ltp_server":
        status = NlpSidecarManager(config).ensure_ltp_server(auto_start=auto_start)
        sidecar_status = status.to_dict()
        if not status.healthy:
            if not config.fallback.enabled:
                raise ValueError(status.error or "ltp-server is unavailable and fallback is disabled")
            effective_provider_kind = "fallback_simple"
    task_id: str | None = None
    with connection(workspace.db_path) as conn:
        task_id = insert_task_run(
            conn,
            task_type="nlp.cache_build",
            status="success",
            stage="completed",
            project_id=project["id"],
            input_data={
                "project": project_slug,
                "chapters": chapters,
                "provider": provider_kind,
                "effective_provider": effective_provider_kind,
                "missing_only": missing_only,
                "force": force,
            },
        )
        conn.commit()

    results: list[dict[str, Any]] = []
    for chapter_ref in parse_chapter_range(chapters):
        try:
            result = analyze_chapter(
                workspace,
                project_slug=project_slug,
                chapter_ref=chapter_ref,
                provider_kind=effective_provider_kind,
                auto_start=False if effective_provider_kind == "fallback_simple" else auto_start,
                force=force and not missing_only,
            )
            if missing_only and result["status"] == "cache_hit":
                result = {key: value for key, value in result.items() if key != "analysis"}
            results.append(result)
        except ValueError as exc:
            results.append({"status": "error", "chapter": chapter_ref, "error": str(exc)})
    if all(result.get("status") == "error" for result in results):
        raise ValueError("No chapters could be analyzed.")
    manifest_path = _manifest_path(workspace, project_slug)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if sidecar_status:
        manifest["sidecar_status"] = sidecar_status
        _json_write(manifest_path, manifest)
    return {
        "task_run_id": task_id,
        "project": project_slug,
        "results": results,
        "requested_provider": requested_kind,
        "effective_provider": effective_provider_kind,
        "sidecar_status": sidecar_status,
        "manifest_path": str(manifest_path),
        "report_path": str(_report_path(workspace, project_slug)),
        "coverage_count": manifest.get("coverage_count", 0),
        "degraded_chapter_count": manifest.get("degraded_chapter_count", 0),
    }


def nlp_status(
    workspace: Workspace | None,
    *,
    project_slug: str | None = None,
    provider_kind: str | None = None,
    auto_start: bool | None = None,
) -> dict[str, Any]:
    config = load_nlp_config(workspace=workspace)
    kind = provider_kind or config.provider
    sidecar_status: SidecarStatus | None = None
    if kind == "ltp_server":
        sidecar_status = NlpSidecarManager(config).ensure_ltp_server(auto_start=auto_start)
    elif kind != "fallback_simple":
        raise ValueError(f"Unsupported NLP provider: {kind}")

    cache: dict[str, Any] | None = None
    if workspace and project_slug:
        manifest_path = _manifest_path(workspace, project_slug)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache = {
                "manifest_path": str(manifest_path),
                "coverage_count": manifest.get("coverage_count", 0),
                "degraded_chapter_count": manifest.get("degraded_chapter_count", 0),
                "sentence_count": manifest.get("sentence_count", 0),
                "token_count": manifest.get("token_count", 0),
            }
        else:
            cache = {"manifest_path": str(manifest_path), "coverage_count": 0}
        status_dir = workspace.path / "artifacts" / "nlp" / project_slug
        status_json = status_dir / "sidecar_status.json"
        payload = sidecar_status.to_dict() if sidecar_status else {
            "healthy": True,
            "provider": "fallback_simple",
            "degraded": True,
        }
        _json_write(status_json, payload)
        _markdown_write(
            status_dir / "sidecar_status.md",
            [
                f"# NLP Sidecar Status: {project_slug}",
                "",
                f"- Provider: {payload.get('provider')}",
                f"- Healthy: {payload.get('healthy')}",
                f"- Degraded: {payload.get('degraded')}",
                f"- PID: {payload.get('pid')}",
                f"- Error: {payload.get('error')}",
            ],
        )

    return {
        "config": {
            "enabled": config.enabled,
            "provider": kind,
            "auto_start": config.auto_start if auto_start is None else auto_start,
            "fallback_enabled": config.fallback.enabled,
            "base_url": config.ltp_server.base_url,
            "working_dir": config.ltp_server.working_dir,
        },
        "sidecar": sidecar_status.to_dict() if sidecar_status else None,
        "healthy": sidecar_status.healthy if sidecar_status else True,
        "degraded": sidecar_status.degraded if sidecar_status else kind == "fallback_simple",
        "cache": cache,
    }


def show_cache(
    workspace: Workspace,
    *,
    project_slug: str,
    chapter_ref: str | None = None,
) -> dict[str, Any]:
    manifest_path = _manifest_path(workspace, project_slug)
    if not manifest_path.exists():
        raise ValueError(f"NLP cache manifest not found for project: {project_slug}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if chapter_ref is None:
        return {"manifest": manifest}
    chapter_id = resolve_chapter_id(workspace, project_slug=project_slug, chapter=chapter_ref)
    for entry in manifest.get("chapters", []):
        if entry.get("chapter_id") == chapter_id or str(entry.get("chapter_no")) == str(chapter_ref):
            artifact = Path(entry["artifact_path"])
            if artifact.exists():
                analysis = json.loads(artifact.read_text(encoding="utf-8"))
            else:
                analysis = None
            return {
                "chapter": entry,
                "summary": {
                    "sentence_count": entry.get("sentence_count", 0),
                    "token_count": entry.get("token_count", 0),
                    "entity_candidates": len(
                        (analysis or {}).get("chapter_candidates", {}).get("entity_candidates", [])
                    ),
                    "term_candidates": len(
                        (analysis or {}).get("chapter_candidates", {}).get("term_candidates", [])
                    ),
                    "phrase_candidates": len(
                        (analysis or {}).get("chapter_candidates", {}).get("phrase_candidates", [])
                    ),
                },
                "analysis": analysis,
            }
    raise ValueError(f"NLP cache not found for chapter: {chapter_ref}")


MOJIBAKE_RE = re.compile(r"[äåæÃ]")


def _quality_paths(workspace: Workspace, project_slug: str) -> dict[str, Path]:
    root = workspace.path / "artifacts" / "nlp" / project_slug / "quality"
    return {
        "root": root,
        "json": root / "nlp_quality_report.json",
        "markdown": root / "nlp_quality_report.md",
        "csv": root / "nlp_quality_table.csv",
    }


def _chapter_entry_by_ref(manifest: dict[str, Any], chapter_ref: str) -> dict[str, Any] | None:
    for entry in manifest.get("chapters", []):
        if str(entry.get("chapter_no")) == str(chapter_ref) or entry.get("chapter_id") == chapter_ref:
            return entry
    return None


def quality_check(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str,
) -> dict[str, Any]:
    manifest_path = _manifest_path(workspace, project_slug)
    if not manifest_path.exists():
        raise ValueError(f"NLP cache manifest not found for project: {project_slug}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requested = parse_chapter_range(chapters)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total_tokens = 0
    total_sentences = 0
    total_provider_pos = 0
    total_ner_tags = 0

    for chapter_ref in requested:
        entry = _chapter_entry_by_ref(manifest, chapter_ref)
        reasons: list[str] = []
        artifact_data: dict[str, Any] | None = None
        if entry is None:
            reasons.append("missing_cache_artifact")
        else:
            artifact = Path(entry.get("artifact_path") or "")
            if not artifact.exists():
                reasons.append("artifact_path_missing")
            else:
                try:
                    artifact_data = json.loads(artifact.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    reasons.append("artifact_malformed_json")

        if artifact_data is not None:
            meta = artifact_data.get("meta", {})
            sentences = artifact_data.get("sentences", [])
            candidates = artifact_data.get("chapter_candidates", {})
            if meta.get("provider") != "ltp_server":
                reasons.append("provider_not_ltp_server")
            if meta.get("degraded"):
                reasons.append("chapter_degraded")
            if not sentences:
                reasons.append("sentences_missing")
            token_count = 0
            provider_pos_count = 0
            ner_count = 0
            mojibake_count = 0
            for sentence in sentences:
                tokens = sentence.get("tokens", [])
                if not tokens:
                    reasons.append("sentence_without_tokens")
                for token in tokens:
                    token_count += 1
                    if token.get("provider_pos") is not None:
                        provider_pos_count += 1
                    if not token.get("norm_pos"):
                        reasons.append("token_missing_norm_pos")
                    if MOJIBAKE_RE.search(str(token.get("text") or "")):
                        mojibake_count += 1
                ner_tags = sentence.get("ner_tags", [])
                ner_count += len(ner_tags)
            if token_count == 0:
                reasons.append("zero_tokens")
            if len(sentences) and token_count / max(len(sentences), 1) < 1.2:
                reasons.append("suspiciously_tiny_token_count")
            if provider_pos_count < max(1, token_count // 2):
                reasons.append("provider_pos_sparse")
            if ner_count == 0:
                reasons.append("ner_tags_missing")
            for key in ("entity_candidates", "term_candidates", "phrase_candidates"):
                if not isinstance(candidates.get(key), list):
                    reasons.append(f"{key}_not_list")
            if mojibake_count:
                reasons.append("mojibake_detected")
            total_tokens += token_count
            total_sentences += len(sentences)
            total_provider_pos += provider_pos_count
            total_ner_tags += ner_count
            row = {
                "chapter": chapter_ref,
                "chapter_id": entry.get("chapter_id") if entry else None,
                "provider": meta.get("provider"),
                "degraded": bool(meta.get("degraded")),
                "sentence_count": len(sentences),
                "token_count": token_count,
                "provider_pos_count": provider_pos_count,
                "ner_tag_count": ner_count,
                "entity_candidate_count": len(candidates.get("entity_candidates") or []),
                "term_candidate_count": len(candidates.get("term_candidates") or []),
                "phrase_candidate_count": len(candidates.get("phrase_candidates") or []),
                "pass": not reasons,
                "reasons": reasons,
            }
        else:
            row = {
                "chapter": chapter_ref,
                "chapter_id": entry.get("chapter_id") if entry else None,
                "provider": None,
                "degraded": True,
                "sentence_count": 0,
                "token_count": 0,
                "provider_pos_count": 0,
                "ner_tag_count": 0,
                "entity_candidate_count": 0,
                "term_candidate_count": 0,
                "phrase_candidate_count": 0,
                "pass": False,
                "reasons": reasons,
            }
        rows.append(row)
        if not row["pass"]:
            failures.append(row)

    sidecar_status = manifest.get("sidecar_status") or {}
    manifest_reasons: list[str] = []
    if len(requested) != len(rows):
        manifest_reasons.append("requested_chapter_count_mismatch")
    if int(manifest.get("coverage_count") or 0) < len(requested):
        manifest_reasons.append("coverage_below_requested")
    if int(manifest.get("degraded_chapter_count") or 0) != 0:
        manifest_reasons.append("manifest_has_degraded_chapters")
    providers = {row.get("provider") for row in rows}
    if providers != {"ltp_server"}:
        manifest_reasons.append("not_all_chapters_use_ltp_server")
    if not sidecar_status.get("healthy"):
        manifest_reasons.append("sidecar_not_healthy")
    if sidecar_status.get("degraded"):
        manifest_reasons.append("sidecar_degraded")
    error_text = str(sidecar_status.get("error") or "")
    if "non-object JSON" in error_text:
        manifest_reasons.append("legacy_non_object_json_error_present")
    pass_gate = not failures and not manifest_reasons

    paths = _quality_paths(workspace, project_slug)
    report = {
        "schema_version": "nlp_quality_report_v1",
        "project_slug": project_slug,
        "chapters": requested,
        "pass": pass_gate,
        "failure_count": len(failures),
        "manifest_reasons": manifest_reasons,
        "coverage_count": manifest.get("coverage_count", 0),
        "degraded_chapter_count": manifest.get("degraded_chapter_count", 0),
        "provider": "ltp_server" if providers == {"ltp_server"} else sorted(str(p) for p in providers),
        "sentence_count": total_sentences,
        "token_count": total_tokens,
        "provider_pos_count": total_provider_pos,
        "ner_tag_count": total_ner_tags,
        "sidecar_status": sidecar_status,
        "rows": rows,
        "report_paths": {
            "json": str(paths["json"]),
            "markdown": str(paths["markdown"]),
            "csv": str(paths["csv"]),
        },
        "created_at": utc_now(),
    }
    _json_write(paths["json"], report)
    paths["root"].mkdir(parents=True, exist_ok=True)
    with paths["csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "chapter",
                "chapter_id",
                "provider",
                "degraded",
                "sentence_count",
                "token_count",
                "provider_pos_count",
                "ner_tag_count",
                "entity_candidate_count",
                "term_candidate_count",
                "phrase_candidate_count",
                "pass",
                "reasons",
            ],
        )
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["reasons"] = "; ".join(row.get("reasons") or [])
            writer.writerow(out)
    lines = [
        f"# NLP Quality Report: {project_slug}",
        "",
        f"- Pass: `{pass_gate}`",
        f"- Coverage: `{report['coverage_count']}`",
        f"- Degraded chapters: `{report['degraded_chapter_count']}`",
        f"- Sentences: `{total_sentences}`",
        f"- Tokens: `{total_tokens}`",
        f"- Manifest reasons: `{', '.join(manifest_reasons) or 'none'}`",
        "",
        "| Chapter | Provider | Degraded | Sentences | Tokens | Pass | Reasons |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['chapter']} | {row.get('provider')} | {row.get('degraded')} | "
            f"{row.get('sentence_count')} | {row.get('token_count')} | {row.get('pass')} | "
            f"{'; '.join(row.get('reasons') or []) or 'none'} |"
        )
    _markdown_write(paths["markdown"], lines)
    return report


def _resolve_validation_run_path(workspace: Workspace, validation_run: str) -> Path:
    run_path = Path(validation_run)
    if run_path.exists():
        return run_path
    candidate = workspace.path / "artifacts" / "approved_memory_validation" / validation_run
    if candidate.exists():
        return candidate
    raise ValueError(f"Approved-memory validation run not found: {validation_run}")


def _translation_quality_gate(summary: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    rounds = summary.get("round_results") or []
    reasons: list[str] = []
    warnings: list[str] = []
    if summary.get("final_decision") != "PASS":
        warnings.append(f"legacy_validation_final_decision:{summary.get('final_decision')}")
    if len(rounds) < 2:
        reasons.append("fewer_than_two_validation_rounds")
    severe_count = 0
    unsafe_count = 0
    truncation_count = 0
    regression_count = 0
    round_rows: list[dict[str, Any]] = []
    for row in rounds:
        severe_flags = row.get("severe_flags") or []
        regressions = row.get("regressions_over_3") or []
        severe_count += len(severe_flags)
        regression_count += len(regressions)
        unsafe_count += sum(1 for flag in severe_flags if flag.get("reason") == "unsafe_compression")
        truncation_count += sum(1 for flag in severe_flags if flag.get("reason") == "truncation")
        if float(row.get("score_delta") or 0) <= 0:
            reasons.append(f"round_{row.get('round')}_delta_not_positive")
        round_rows.append(
            {
                "round": row.get("round"),
                "baseline_score": row.get("baseline_score"),
                "memory_score": row.get("memory_score"),
                "score_delta": row.get("score_delta"),
                "severe_flag_count": len(severe_flags),
                "regressions_over_3": regressions,
            }
        )
    if severe_count:
        reasons.append("severe_flags_present")
    if unsafe_count:
        reasons.append("unsafe_compression_present")
    if truncation_count:
        reasons.append("truncation_present")
    if regression_count:
        reasons.append("chapter_regression_over_3_present")
    details = {
        "rounds": round_rows,
        "legacy_final_decision": summary.get("final_decision"),
        "legacy_reason": summary.get("reason"),
        "warnings": warnings,
        "severe_flag_count": severe_count,
        "unsafe_compression_count": unsafe_count,
        "truncation_count": truncation_count,
        "regression_count": regression_count,
        "validation_artifact_path": str(summary.get("validation_artifact_path") or ""),
    }
    return not reasons, reasons, details


def _candidate_rows_for_review(
    workspace: Workspace,
    *,
    project_slug: str,
    chapters: str,
    candidate_key: str,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for chapter_ref in parse_chapter_range(chapters):
        try:
            analysis = load_nlp_cache(workspace, project_slug, chapter_ref)
        except ValueError:
            continue
        chapter_id = analysis.get("meta", {}).get("chapter_id") or chapter_ref
        for candidate in analysis.get("chapter_candidates", {}).get(candidate_key, []) or []:
            text = str(candidate.get("text") or "").strip()
            if not text:
                continue
            row = rows.setdefault(
                text,
                {
                    "text": text,
                    "candidate_type": candidate_key.removesuffix("_candidates"),
                    "confidence": candidate.get("confidence"),
                    "count": 0,
                    "chapters": set(),
                    "source": "nlp_cache_read_only",
                },
            )
            row["count"] = int(row.get("count") or 0) + int(candidate.get("count") or 1)
            row["chapters"].add(str(chapter_id))
    normalized = []
    for row in rows.values():
        out = dict(row)
        out["chapters"] = ", ".join(sorted(out["chapters"]))
        normalized.append(out)
    return sorted(normalized, key=lambda item: (-int(item.get("count") or 0), item.get("text") or ""))


def _write_candidate_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["text", "candidate_type", "confidence", "count", "chapters", "source"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def create_final_human_review_package(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    chapters: str,
) -> dict[str, Any]:
    quality_path = _quality_paths(workspace, project_slug)["json"]
    if not quality_path.exists():
        raise ValueError("NLP quality report is missing. Run nlp quality-check first.")
    quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
    if not quality_report.get("pass"):
        raise ValueError("NLP quality gate has not passed; final human review package is blocked.")

    run_path = _resolve_validation_run_path(workspace, validation_run)
    summary_path = run_path / "final_validation_summary.json"
    if not summary_path.exists():
        raise ValueError(f"Translation validation summary missing: {summary_path}")
    translation_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    translation_summary["validation_artifact_path"] = str(run_path)
    translation_pass, translation_reasons, translation_gate = _translation_quality_gate(translation_summary)
    if not translation_pass:
        raise ValueError(
            "Translation quality gate has not passed; final human review package is blocked: "
            + ", ".join(translation_reasons)
        )

    root = workspace.path / "artifacts" / "nlp" / project_slug / "human_review_final"
    root.mkdir(parents=True, exist_ok=True)
    entity_rows = _candidate_rows_for_review(
        workspace, project_slug=project_slug, chapters=chapters, candidate_key="entity_candidates"
    )
    term_rows = _candidate_rows_for_review(
        workspace, project_slug=project_slug, chapters=chapters, candidate_key="term_candidates"
    )
    phrase_rows = _candidate_rows_for_review(
        workspace, project_slug=project_slug, chapters=chapters, candidate_key="phrase_candidates"
    )
    _write_candidate_review_csv(root / "entity_candidates_review.csv", entity_rows)
    _write_candidate_review_csv(root / "term_candidates_review.csv", term_rows)
    _write_candidate_review_csv(root / "phrase_candidates_review.csv", phrase_rows)

    _json_write(root / "sidecar_status_final.json", quality_report.get("sidecar_status") or {})
    _json_write(root / "nlp_quality_gate_final.json", quality_report)
    _json_write(root / "translation_quality_gate_final.json", translation_gate | {"pass": True})

    round_lines = [
        "# Validation Rounds Summary",
        "",
        "| Round | Baseline | Memory | Delta | Severe flags | Regressions >3 |",
        "| ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in translation_gate["rounds"]:
        round_lines.append(
            f"| {row.get('round')} | {row.get('baseline_score')} | {row.get('memory_score')} | "
            f"{row.get('score_delta')} | {row.get('severe_flag_count')} | "
            f"{json.dumps(row.get('regressions_over_3') or [], ensure_ascii=False)} |"
        )
    _markdown_write(root / "validation_rounds_summary.md", round_lines)

    _markdown_write(
        root / "nlp_cache_overview.md",
        [
            "# NLP Cache Overview",
            "",
            f"- Provider: `{quality_report.get('provider')}`",
            f"- Degraded chapters: `{quality_report.get('degraded_chapter_count')}`",
            f"- Chapters: `{', '.join(quality_report.get('chapters') or [])}`",
            f"- Sentences: `{quality_report.get('sentence_count')}`",
            f"- Tokens: `{quality_report.get('token_count')}`",
            f"- Quality report: `{quality_path}`",
        ],
    )
    _markdown_write(
        root / "translation_quality_summary.md",
        [
            "# Translation Quality Summary",
            "",
            f"- Validation artifact: `{run_path}`",
            f"- Final decision: `{translation_summary.get('final_decision')}`",
            f"- Severe flags: `{translation_gate['severe_flag_count']}`",
            f"- Unsafe compression: `{translation_gate['unsafe_compression_count']}`",
            f"- Truncation: `{translation_gate['truncation_count']}`",
            f"- Chapter regressions >3: `{translation_gate['regression_count']}`",
        ],
    )
    _markdown_write(
        root / "prompt_memory_filter_summary.md",
        [
            "# Prompt Memory Filter Summary",
            "",
            "Production memory filtering remains gate-based: status, exact trigger, context, negative evidence, and prompt budget.",
            "This MVP5E.1 package is read-only and does not create memory or dictionary entries from NLP candidates.",
        ],
    )
    sample_lines = ["# Chapter Samples", ""]
    for chapter_ref in parse_chapter_range(chapters)[:3]:
        try:
            cache = load_nlp_cache(workspace, project_slug, chapter_ref)
        except ValueError:
            continue
        sample_lines.extend(
            [
                f"## Chapter {chapter_ref}",
                "",
                f"- Sentences: `{len(cache.get('sentences') or [])}`",
                f"- Entity candidates: `{len(cache.get('chapter_candidates', {}).get('entity_candidates') or [])}`",
                f"- Term candidates: `{len(cache.get('chapter_candidates', {}).get('term_candidates') or [])}`",
                "",
                (cache.get("sentences") or [{}])[0].get("text", ""),
                "",
            ]
        )
    _markdown_write(root / "chapter_samples.md", sample_lines)

    summary_lines = [
        "# MVP5E.1 Human Review Summary",
        "",
        f"- Project: `{project_slug}`",
        f"- Provider used: `{quality_report.get('provider')}`",
        f"- Degraded chapter count: `{quality_report.get('degraded_chapter_count')}`",
        f"- Total chapters: `{len(quality_report.get('chapters') or [])}`",
        f"- Total sentences: `{quality_report.get('sentence_count')}`",
        f"- Total tokens: `{quality_report.get('token_count')}`",
        f"- Translation validation artifact: `{run_path}`",
        f"- Severe flag count: `{translation_gate['severe_flag_count']}`",
        f"- Unsafe compression count: `{translation_gate['unsafe_compression_count']}`",
        f"- Truncation count: `{translation_gate['truncation_count']}`",
        f"- Memory/filter changes during repair: `none in final packaging step`",
        f"- Recommendation: `Proceed to MVP5F Project Dictionary Builder using this cache as read-only analysis input.`",
        "",
        "## Top Entity Candidates",
        "",
    ]
    summary_lines.extend(f"- {row['text']} ({row['count']})" for row in entity_rows[:10])
    summary_lines.extend(["", "## Top Term Candidates", ""])
    summary_lines.extend(f"- {row['text']} ({row['count']})" for row in term_rows[:10])
    summary_lines.extend(["", "## Top Phrase Candidates", ""])
    summary_lines.extend(f"- {row['text']} ({row['count']})" for row in phrase_rows[:10])
    _markdown_write(root / "human_review_summary.md", summary_lines)

    return {
        "status": "created",
        "project_slug": project_slug,
        "chapters": parse_chapter_range(chapters),
        "output_dir": str(root),
        "required_files": sorted(path.name for path in root.iterdir() if path.is_file()),
        "translation_validation_run": str(run_path),
    }


def load_nlp_cache(workspace: Workspace, project_slug: str, chapter_ref: str) -> dict[str, Any]:
    return show_cache(workspace, project_slug=project_slug, chapter_ref=chapter_ref)["analysis"]


def get_entity_candidates(workspace: Workspace, project_slug: str, chapter_ref: str) -> list[dict[str, Any]]:
    cache = load_nlp_cache(workspace, project_slug, chapter_ref)
    return cache.get("chapter_candidates", {}).get("entity_candidates", [])


def get_term_candidates(workspace: Workspace, project_slug: str, chapter_ref: str) -> list[dict[str, Any]]:
    cache = load_nlp_cache(workspace, project_slug, chapter_ref)
    return cache.get("chapter_candidates", {}).get("term_candidates", [])


def get_phrase_candidates(workspace: Workspace, project_slug: str, chapter_ref: str) -> list[dict[str, Any]]:
    cache = load_nlp_cache(workspace, project_slug, chapter_ref)
    return cache.get("chapter_candidates", {}).get("phrase_candidates", [])


def get_exact_source_anchors(workspace: Workspace, project_slug: str, chapter_ref: str) -> list[str]:
    cache = load_nlp_cache(workspace, project_slug, chapter_ref)
    anchors = []
    for group in ("entity_candidates", "term_candidates", "phrase_candidates"):
        anchors.extend(item["text"] for item in cache.get("chapter_candidates", {}).get(group, []))
    return sorted(set(anchors))


def chapter_count_for_project(workspace: Workspace, project_slug: str) -> int:
    return len(list_chapters(workspace, project_slug=project_slug))
