from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.eval_harness import (
    EvalProvider,
    FIXED_GLOSSARY,
    add_paragraph_alignment,
    compress_offending_paragraphs,
    create_paragraph_alignment,
    detect_truncated_vietnamese,
    evaluate_alignment_quality,
    freeze_stable_candidate,
    limited_style_prompt,
    load_eval_provider,
    mask_api_key,
    normalize_provider_type,
    replay_cached_eval,
    render_paragraph_translation,
    split_text_paragraphs,
    stable_gate_result,
    stable_prompt_review,
    translation_system_prompt,
    validate_paragraph_translation,
    validate_eval_provider,
    verify_paragraph_output,
    write_cached_eval_exports,
    write_stable_decision_outputs,
)


runner = CliRunner()


def parse_json(output: str) -> dict:
    return json.loads(output)


def write_eval_inputs(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "raw.txt"
    raw.write_text(
        "\n\n".join(
            [
                "第1章 初遇",
                "韩觉站在窗前，看着雨水落在旧街上。他想起昨夜的梦，也想起那个没有说完的名字。",
                "门外传来轻轻的脚步声，他收起信纸，低声说道：别让他们知道。",
                "第2章 回声",
                "城南的钟声响起时，韩觉已经离开客栈。他没有回头，只把伞留在门边。",
            ]
        ),
        encoding="utf-8",
    )
    epub = tmp_path / "viettranslated.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/ch1.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Chương 1</h1>
<p>Hàn Giác đứng bên cửa sổ, nhìn mưa rơi xuống con phố cũ. Hắn nhớ đến giấc mơ đêm qua, cũng nhớ cái tên còn chưa kịp nói hết.</p>
<p>Ngoài cửa vang lên tiếng bước chân rất khẽ, hắn gấp lá thư lại và thấp giọng nói: đừng để bọn họ biết.</p>
</body></html>""",
        )
        archive.writestr(
            "OEBPS/ch2.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Chương 2</h1>
<p>Khi tiếng chuông phía nam thành vang lên, Hàn Giác đã rời khỏi quán trọ. Hắn không ngoảnh lại, chỉ để chiếc ô bên cửa.</p>
</body></html>""",
        )
    return raw, epub


def test_prepare_parallel_extracts_aligns_limits_and_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw, epub = write_eval_inputs(tmp_path)

    result = runner.invoke(
        app,
        [
            "eval",
            "prepare-parallel",
            "--raw",
            str(raw),
            "--translated",
            str(epub),
            "--project",
            "han-jue-test",
            "--max-chapters",
            "2",
            "--max-source-chars",
            "45",
            "--max-target-chars",
            "90",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    run_dir = Path(data["run_dir"])
    assert data["alignment"]["aligned_chapters"] == 2
    sample = data["selected_sample"]
    assert sample["source_char_count"] <= 45
    assert sample["target_char_count"] <= 90
    assert "第1章" not in sample["source_text"]
    assert {
        "chapter_id",
        "source_start_offset",
        "source_end_offset",
        "source_char_count",
        "target_start_offset",
        "target_end_offset",
        "target_char_count",
        "selection_reason",
        "limits_used",
    }.issubset(sample)
    assert (run_dir / "extracted_raw_chapters.json").exists()
    assert (run_dir / "extracted_translated_chapters.json").exists()
    assert (run_dir / "alignment_report.json").exists()
    assert (run_dir / "paragraph_alignment_report.json").exists()
    assert (run_dir / "selected_sample.json").exists()
    assert (run_dir / "selected_samples.json").exists()
    stored_sample = json.loads((run_dir / "selected_sample.json").read_text(encoding="utf-8"))
    assert stored_sample["source_paragraphs"]
    assert stored_sample["target_paragraphs"]
    assert stored_sample["paragraph_pairs"]
    first_pair = stored_sample["paragraph_pairs"][0]
    assert {"paragraph_id", "target_max", "strict_max", "target_source_ratio"}.issubset(first_pair)


def test_paragraph_alignment_pairs_and_mismatch_warning() -> None:
    source = "甲。\n\n乙。\n\n丙。"
    target = "Một.\n\nHai."

    alignment = create_paragraph_alignment(source, target)

    assert [item["char_count"] for item in split_text_paragraphs(source, kind="s")] == [2, 2, 2]
    assert len(alignment["paragraph_pairs"]) == 2
    assert alignment["paragraph_pairs"][0]["source_paragraph_indexes"] == [1, 2]
    assert alignment["paragraph_pairs"][1]["source_paragraph_indexes"] == [3]
    assert alignment["warnings"] == ["paragraph_count_mismatch:source=3,target=2"]


def test_truncation_detector_catches_broken_endings_and_brackets() -> None:
    examples = [
        "【Linh căn:",
        "Click bắt đ",
        "sao có thể phà",
        "vẫn không lắ",
        "hắn lại có thể tu tiê",
        "chẳng có m",
        "Một câu (chưa đóng.",
    ]

    for text in examples:
        result = detect_truncated_vietnamese(text)
        assert result["is_truncated"] is True, text


def test_truncation_detector_warns_on_glossary_prefix_injection() -> None:
    result = detect_truncated_vietnamese("linh căn: Ngọc Thanh Tông: Hắn tiếp tục tu luyện.")

    assert result["is_truncated"] is True
    assert "glossary_label_prefix_injection" in result["reasons"]


def test_alignment_quality_below_threshold_excludes_sample() -> None:
    alignment = create_paragraph_alignment(
        "甲。\n\n乙。\n\n丙。\n\n丁。\n\n戊。",
        "Một đoạn tham chiếu duy nhất rất ngắn.",
    )

    quality = evaluate_alignment_quality(alignment)

    assert quality["alignment_quality"] < 0.70
    assert quality["accepted_for_stable_validation"] is False


def test_paragraph_validation_and_rendering_preserves_ids_and_count() -> None:
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "甲。\n\n乙。",
            "target_text": "Một.\n\nHai.",
            "target_char_count": len("Một.\n\nHai."),
        }
    )
    valid = [
        {"paragraph_id": "p001", "text": "Một."},
        {"paragraph_id": "p002", "text": "Hai."},
    ]

    assert validate_paragraph_translation(sample, valid)["valid"] is True
    assert render_paragraph_translation(sample, valid) == "Một.\n\nHai."
    assert validate_paragraph_translation(sample, valid[:1])["valid"] is False
    assert validate_paragraph_translation(sample, list(reversed(valid)))["valid"] is False
    assert (
        "extra_paragraph_id"
        in validate_paragraph_translation(
            sample,
            valid + [{"paragraph_id": "p999", "text": "Extra."}],
        )["errors"]
    )


def test_compression_only_rewrites_offending_paragraph_once() -> None:
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "甲。\n\n乙。",
            "target_text": "Một câu hoàn chỉnh và vừa đủ dài.\n\nHai.",
            "target_char_count": len("Một câu hoàn chỉnh và vừa đủ dài.\n\nHai."),
        }
    )
    pair = sample["paragraph_pairs"][0]
    overlong = "Một câu hoàn chỉnh. " * 8
    paragraphs = [
        {"paragraph_id": "p001", "text": overlong},
        {"paragraph_id": "p002", "text": "Hai."},
    ]
    provider = EvalProvider(
        key="mock",
        type="mock",
        base_url="mock://local",
        api_key_env="MOCK_API_KEY",
    )

    compressed, log = compress_offending_paragraphs(
        provider,
        model="mock-compress",
        sample=sample,
        paragraphs=paragraphs,
        glossary={"fixed_terms": []},
    )

    assert log["triggered"] is True
    assert log["offending_paragraph_ids"] == ["p001"]
    assert len(log["entries"]) == 1
    assert compressed[0]["paragraph_id"] == "p001"
    assert len(compressed[0]["text"]) <= pair["target_max"]
    assert compressed[0]["text"].endswith(".")
    assert log["entries"][0]["deterministic_clip_applied"] is False
    assert compressed[1] == paragraphs[1]


def test_unsafe_compression_does_not_force_pass() -> None:
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "甲。\n\n乙。",
            "target_text": "Một câu hoàn chỉnh và vừa đủ dài.\n\nHai.",
            "target_char_count": len("Một câu hoàn chỉnh và vừa đủ dài.\n\nHai."),
        }
    )
    pair = sample["paragraph_pairs"][0]
    paragraphs = [
        {"paragraph_id": "p001", "text": "bắt đ"},
        {"paragraph_id": "p002", "text": "Hai."},
    ]

    verification = validate_paragraph_translation(sample, paragraphs)
    assert verification["valid"] is True
    output = verify_paragraph_output(sample, paragraphs, glossary={"fixed_terms": []})

    assert output["pass"] is False
    assert "paragraph_truncation_detected" in output["reasons"]
    assert output["truncated_paragraphs"][0]["paragraph_id"] == pair["paragraph_id"]


def test_learn_style_translate_compare_mock_outputs_and_score_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw, epub = write_eval_inputs(tmp_path)
    prepare = runner.invoke(
        app,
        [
            "eval",
            "prepare-parallel",
            "--raw",
            str(raw),
            "--translated",
            str(epub),
            "--project",
            "han-jue-test",
            "--max-chapters",
            "2",
            "--max-source-chars",
            "80",
            "--max-target-chars",
            "140",
            "--json",
        ],
    )
    assert prepare.exit_code == 0, prepare.output
    run_dir = Path(parse_json(prepare.output)["data"]["run_dir"])

    style = runner.invoke(
        app,
        [
            "eval",
            "learn-style",
            "--project",
            "han-jue-test",
            "--provider",
            "mock",
            "--model",
            "mock-style",
            "--chapters",
            "1",
            "--max-source-chars",
            "35",
            "--max-target-chars",
            "60",
            "--json",
        ],
    )
    assert style.exit_code == 0, style.output
    style_data = parse_json(style.output)["data"]
    assert style_data["prompt_limits"]["source_chars_sent"] <= 35
    assert style_data["prompt_limits"]["target_chars_sent"] <= 60
    glossary = json.loads((run_dir / "glossary_candidates.json").read_text(encoding="utf-8"))
    assert {"glossary_candidates", "name_candidates", "pronoun_candidates"}.issubset(glossary)
    prompt = translation_system_prompt(run_dir)
    assert "Temporary style profile" in prompt
    assert "Candidate Vietnamese renderings" in prompt
    assert "Return only the Vietnamese translation" in prompt

    translated = runner.invoke(
        app,
        [
            "eval",
            "translate-sample",
            "--project",
            "han-jue-test",
            "--provider",
            "mock",
            "--models",
            "mock-a,mock-b",
            "--max-source-chars",
            "40",
            "--json",
        ],
    )
    assert translated.exit_code == 0, translated.output
    outputs = parse_json(translated.output)["data"]["outputs"]
    assert outputs["mock-a"]["source_chars_sent"] <= 40
    assert outputs["mock-b"]["source_chars_sent"] <= 40
    assert (run_dir / "translation_mock-a.txt").exists()
    assert (run_dir / "translation_mock-b.txt").exists()

    compared = runner.invoke(
        app,
        [
            "eval",
            "compare-translation",
            "--project",
            "han-jue-test",
            "--chapter",
            "1",
            "--max-source-chars",
            "40",
            "--max-target-chars",
            "80",
            "--json",
        ],
    )
    assert compared.exit_code == 0, compared.output
    report = parse_json(compared.output)["data"]["report"]
    expected_score_keys = {
        "meaning_accuracy",
        "omission_addition",
        "terminology_consistency",
        "pronoun_name_consistency",
        "vietnamese_fluency",
        "style_match",
        "formatting_preservation",
        "total_score",
        "pass",
        "gates",
        "notes",
    }
    assert expected_score_keys.issubset(report["models"]["mock-a"])
    assert (run_dir / "evaluation_report.json").exists()
    assert (run_dir / "evaluation_report.md").exists()
    assert (run_dir / "model_comparison.md").exists()


def test_run_full_mock_creates_required_eval_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw, epub = write_eval_inputs(tmp_path)

    result = runner.invoke(
        app,
        [
            "eval",
            "run-full",
            "--project",
            "han-jue-test",
            "--raw",
            str(raw),
            "--translated",
            str(epub),
            "--provider",
            "mock",
            "--models",
            "mock-a,mock-b",
            "--max-chapters",
            "2",
            "--max-source-chars",
            "70",
            "--max-target-chars",
            "120",
            "--sample-count",
            "2",
            "--enable-length-retry",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = Path(parse_json(result.output)["data"]["run_dir"])
    for filename in [
        "extracted_raw_chapters.json",
        "extracted_translated_chapters.json",
        "alignment_report.json",
        "paragraph_alignment_report.json",
        "selected_sample.json",
        "selected_samples.json",
        "style_profile_test.json",
        "glossary_candidates.json",
        "evaluation_report.json",
        "evaluation_report.md",
        "model_comparison.md",
        "prompt_iteration_log.md",
        "compression_log.json",
    ]:
        assert (run_dir / filename).exists(), filename
    samples = json.loads((run_dir / "selected_samples.json").read_text(encoding="utf-8"))["samples"]
    assert len(samples) == 2
    assert samples[0]["target_length_min"] == int(samples[0]["target_char_count"] * 0.85)
    assert samples[0]["target_length_max"] == int(samples[0]["target_char_count"] * 1.2)
    assert {sample["chapter_id"] for sample in samples} == {1, 2}
    assert (run_dir / "translation_outputs" / "translation_metadata.json").exists()
    report = json.loads((run_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["sample_count"] == 2
    assert "samples" in report["models"]["mock-a"]
    first_score = report["models"]["mock-a"]["samples"][0]
    assert "per_paragraph_length_table" in first_score
    assert "global_ratio_before_compression" in first_score
    assert "compression_count" in first_score


def test_provider_config_validation_and_api_key_masking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "providers.yaml").write_text(
        """
providers:
  ckey_openai_compatible:
    type: OpenAI-compatible chat/completions
    base_url: https://ckey.vn/v1
    api_key_env: CKEY_API_KEY
    route: chat/completions
    models:
      - gpt-5.5
      - gpt-5.4-mini
""",
        encoding="utf-8",
    )

    provider = load_eval_provider("ckey_openai_compatible")
    assert provider.type == "openai_chat_compatible"
    assert provider.models == ("gpt-5.5", "gpt-5.4-mini")
    assert normalize_provider_type("OpenAI-compatible chat/completions") == "openai_chat_compatible"
    validate_eval_provider(provider)
    with pytest.raises(ValueError, match="https"):
        validate_eval_provider(
            EvalProvider(
                key="bad",
                type="openai_chat_compatible",
                base_url="http://example.test/v1",
                api_key_env="CKEY_API_KEY",
            )
        )

    raw_key = "ckey_test_secret_1234567890"
    masked = mask_api_key(raw_key)
    assert raw_key not in masked
    assert masked.startswith("ckey")
    assert masked.endswith("7890")
    assert mask_api_key(None) == "<missing>"


def test_limited_style_prompt_respects_configured_excerpt_limits() -> None:
    prompt, limits = limited_style_prompt(
        [{"text": "源" * 200}],
        [{"text": "đích " * 200}],
        max_source_chars=25,
        max_target_chars=55,
    )

    assert limits == {"source_chars_sent": 25, "target_chars_sent": 55}
    assert "源" * 26 not in prompt
    assert prompt.count("SOURCE EXCERPT") == 1
    assert prompt.count("TARGET EXCERPT") == 1


def test_translation_prompt_includes_length_and_fixed_glossary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw, epub = write_eval_inputs(tmp_path)
    result = runner.invoke(
        app,
        [
            "eval",
            "prepare-parallel",
            "--raw",
            str(raw),
            "--translated",
            str(epub),
            "--project",
            "han-jue-test",
            "--max-chapters",
            "2",
            "--sample-count",
            "2",
            "--max-source-chars",
            "80",
            "--max-target-chars",
            "140",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = Path(parse_json(result.output)["data"]["run_dir"])
    style = runner.invoke(
        app,
        [
            "eval",
            "learn-style",
            "--project",
            "han-jue-test",
            "--provider",
            "mock",
            "--model",
            "mock-style",
            "--json",
        ],
    )
    assert style.exit_code == 0, style.output
    sample = json.loads((run_dir / "selected_samples.json").read_text(encoding="utf-8"))[
        "samples"
    ][0]
    prompt = translation_system_prompt(run_dir, sample=sample, prompt_iteration=2)
    paragraph_prompt = translation_system_prompt(
        run_dir,
        sample=sample,
        prompt_iteration=4,
        paragraph_mode=True,
    )

    assert f"{sample['target_length_min']}-{sample['target_length_max']}" in prompt
    assert "Do not expand, explain, embellish" in prompt
    assert "Keep system panel/bracket formatting compact" in prompt
    assert FIXED_GLOSSARY["韩绝"] in prompt
    assert "Return JSON only" in paragraph_prompt
    assert "Per-paragraph length budgets" in paragraph_prompt
    assert "target_max" in paragraph_prompt


def stable_sample_score(sample_id: str, total_score: int = 86, ratio: float = 1.0) -> dict:
    return {
        "sample_id": sample_id,
        "total_score": total_score,
        "pass": True,
        "output_reference_ratio": ratio,
        "compression_count": 1,
        "terminology_mismatches": [],
        "gates": {
            "severe_hallucination": False,
            "wrong_main_character_name": False,
            "major_skipped_passage": False,
            "length_in_range": True,
        },
        "notes": {"heuristic_only": True},
        "final_pass_fail_reason": "pass",
    }


def stable_validation_run(index: int, prompt_hash: str, *, include_failing_gpt55: bool = False) -> dict:
    models = {
        "gpt-5.4-mini": {
            "average_score": 86,
            "pass": True,
            "compression_count": 3,
            "samples": [
                stable_sample_score("sample_1", 87, 1.0),
                stable_sample_score("sample_2", 86, 1.1),
                stable_sample_score("sample_3", 85, 0.95),
            ],
        }
    }
    if include_failing_gpt55:
        models["gpt-5.5"] = {
            "average_score": 10,
            "pass": False,
            "compression_count": 0,
            "samples": [
                {
                    **stable_sample_score("sample_1", 10, 0.1),
                    "pass": False,
                    "gates": {
                        "severe_hallucination": True,
                        "wrong_main_character_name": False,
                        "major_skipped_passage": True,
                        "length_in_range": False,
                    },
                }
            ],
        }
    return {
        "validation_index": index,
        "run_dir": f"run_{index}",
        "sample_start_ratio": 0.0,
        "candidate_prompt_sha256": prompt_hash,
        "report": {"models": models},
    }


def test_stable_candidate_freeze_and_prompt_hash(tmp_path: Path) -> None:
    root = tmp_path / "stable"
    root.mkdir()
    candidate = freeze_stable_candidate(
        validation_root=root,
        project="han-jue",
        provider_key="mock",
        model="gpt-5.4-mini",
        source_eval_run=None,
        settings={
            "enable_paragraph_alignment": True,
            "enable_compression_pass": True,
            "stable_run_count": 3,
        },
    )

    assert (root / "candidate_prompt.md").exists()
    assert (root / "candidate_prompt_metadata.json").exists()
    metadata = json.loads((root / "candidate_prompt_metadata.json").read_text(encoding="utf-8"))
    assert metadata["prompt_sha256"] == candidate["metadata"]["prompt_sha256"]
    assert metadata["model"] == "gpt-5.4-mini"
    assert "Return JSON only" in candidate["prompt_text"]


def test_stable_gate_requires_unchanged_prompt_and_ignores_unselected_model() -> None:
    prompt_hash = "sha256:" + "a" * 64
    runs = [
        stable_validation_run(1, prompt_hash, include_failing_gpt55=True),
        stable_validation_run(2, prompt_hash, include_failing_gpt55=True),
        stable_validation_run(3, prompt_hash, include_failing_gpt55=True),
    ]

    gate = stable_gate_result(
        validation_runs=runs,
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=prompt_hash,
    )
    assert gate["pass"] is True

    changed = [*runs]
    changed[2] = {**changed[2], "candidate_prompt_sha256": "sha256:" + "b" * 64}
    changed_gate = stable_gate_result(
        validation_runs=changed,
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=prompt_hash,
    )
    assert changed_gate["pass"] is False
    assert "candidate_prompt_changed_across_runs" in changed_gate["reasons"]


def test_stable_gate_rejects_false_sample_pass_and_truncation() -> None:
    prompt_hash = "sha256:" + "a" * 64
    false_pass_run = stable_validation_run(1, prompt_hash)
    false_pass_run["report"]["models"]["gpt-5.4-mini"]["pass"] = False
    false_pass_run["report"]["models"]["gpt-5.4-mini"]["samples"][0]["pass"] = False
    false_pass_run["report"]["models"]["gpt-5.4-mini"]["samples"][0][
        "final_pass_fail_reason"
    ] = "meaning_accuracy_below_threshold"

    gate = stable_gate_result(
        validation_runs=[false_pass_run],
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=prompt_hash,
    )

    assert gate["pass"] is False
    assert any("model_report_not_pass" in reason for reason in gate["reasons"])
    assert any(
        score["sample_id"] == "sample_1" and "evaluator_sample_not_pass" in score["reasons"]
        for score in gate["per_sample_scores"]
    )

    trunc_run = stable_validation_run(1, prompt_hash)
    trunc_run["report"]["models"]["gpt-5.4-mini"]["samples"][0][
        "verification_reasons"
    ] = ["paragraph_truncation_detected"]
    trunc_run["report"]["models"]["gpt-5.4-mini"]["samples"][0][
        "truncated_paragraphs"
    ] = [{"paragraph_id": "p001", "reasons": ["missing_terminal_punctuation"]}]
    trunc_gate = stable_gate_result(
        validation_runs=[trunc_run],
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=prompt_hash,
    )

    assert trunc_gate["pass"] is False
    assert any(
        "paragraph_truncation_detected" in score["reasons"]
        for score in trunc_gate["per_sample_scores"]
    )


def test_stable_decision_outputs_success_and_failure(tmp_path: Path) -> None:
    root = tmp_path / "stable-pass"
    root.mkdir()
    candidate = freeze_stable_candidate(
        validation_root=root,
        project="han-jue",
        provider_key="mock",
        model="gpt-5.4-mini",
        source_eval_run=None,
        settings={"enable_paragraph_alignment": True, "enable_compression_pass": True},
    )
    prompt_hash = candidate["metadata"]["prompt_sha256"]
    runs = [stable_validation_run(1, prompt_hash)]
    gate = stable_gate_result(
        validation_runs=runs,
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=prompt_hash,
    )
    result = write_stable_decision_outputs(
        validation_root=root,
        candidate=candidate,
        validation_runs=runs,
        gate=gate,
        provider_key="mock",
        model="gpt-5.4-mini",
    )
    assert result["stable_prompt_created"] is True
    assert (root / "stable_prompt.md").exists()
    assert (root / "stable_prompt_metadata.json").exists()

    fail_root = tmp_path / "stable-fail"
    fail_root.mkdir()
    fail_candidate = freeze_stable_candidate(
        validation_root=fail_root,
        project="han-jue",
        provider_key="mock",
        model="gpt-5.4-mini",
        source_eval_run=None,
        settings={"enable_paragraph_alignment": True, "enable_compression_pass": True},
    )
    fail_run = stable_validation_run(1, fail_candidate["metadata"]["prompt_sha256"])
    fail_run["report"]["models"]["gpt-5.4-mini"]["samples"][0]["total_score"] = 70
    fail_gate = stable_gate_result(
        validation_runs=[fail_run],
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=fail_candidate["metadata"]["prompt_sha256"],
    )
    fail_result = write_stable_decision_outputs(
        validation_root=fail_root,
        candidate=fail_candidate,
        validation_runs=[fail_run],
        gate=fail_gate,
        provider_key="mock",
        model="gpt-5.4-mini",
    )
    assert fail_result["stable_prompt_created"] is False
    assert not (fail_root / "stable_prompt.md").exists()
    assert (fail_root / "stable_candidate_failure_report.md").exists()

    trunc_root = tmp_path / "stable-trunc-fail"
    trunc_root.mkdir()
    trunc_candidate = freeze_stable_candidate(
        validation_root=trunc_root,
        project="han-jue",
        provider_key="mock",
        model="gpt-5.4-mini",
        source_eval_run=None,
        settings={"enable_paragraph_alignment": True, "enable_compression_pass": True},
    )
    trunc_run = stable_validation_run(1, trunc_candidate["metadata"]["prompt_sha256"])
    trunc_run["report"]["models"]["gpt-5.4-mini"]["samples"][0][
        "verification_reasons"
    ] = ["paragraph_truncation_detected"]
    trunc_gate = stable_gate_result(
        validation_runs=[trunc_run],
        selected_model="gpt-5.4-mini",
        expected_prompt_sha256=trunc_candidate["metadata"]["prompt_sha256"],
    )
    trunc_result = write_stable_decision_outputs(
        validation_root=trunc_root,
        candidate=trunc_candidate,
        validation_runs=[trunc_run],
        gate=trunc_gate,
        provider_key="mock",
        model="gpt-5.4-mini",
    )
    assert trunc_result["stable_prompt_created"] is False
    assert not (trunc_root / "stable_prompt.md").exists()


def test_cached_replay_and_human_review_exports_created(tmp_path: Path) -> None:
    validation_root = tmp_path / "stable"
    validation_root.mkdir()
    run_dir = tmp_path / "eval_run"
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "甲。\n\n乙。",
            "target_text": "Một.\n\nHai.",
            "target_char_count": len("Một.\n\nHai."),
        }
    )
    (run_dir / "translation_outputs" / "sample_1").mkdir(parents=True)
    (run_dir / "selected_samples.json").write_text(
        json.dumps({"samples": [sample]}, ensure_ascii=False),
        encoding="utf-8",
    )
    structured = {
        "paragraphs": [
            {"paragraph_id": "p001", "text": "Một."},
            {"paragraph_id": "p002", "text": "Hai."},
        ]
    }
    for suffix in ["structured_initial", "structured_final"]:
        (run_dir / "translation_outputs" / "sample_1" / f"gpt-5.4-mini_{suffix}.json").write_text(
            json.dumps(structured, ensure_ascii=False),
            encoding="utf-8",
        )
    validation_runs = [
        {
            "validation_index": 1,
            "run_dir": str(run_dir),
            "report": {
                "models": {
                    "gpt-5.4-mini": {
                        "samples": [stable_sample_score("sample_1")],
                    }
                }
            },
        }
    ]

    exports = write_cached_eval_exports(
        validation_root=validation_root,
        validation_runs=validation_runs,
        selected_model="gpt-5.4-mini",
    )

    assert exports["row_count"] == 2
    assert (validation_root / "cached_eval_replay.json").exists()
    assert (validation_root / "human_review_samples.md").exists()
    assert (validation_root / "paragraph_review_table.md").exists()


def write_stable_review_fixture(tmp_path: Path) -> Path:
    validation_root = tmp_path / "han-jue_stable_fixture"
    validation_root.mkdir(parents=True)
    run_dir = tmp_path / "eval_run_for_review"
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "韩绝看向玉清宗。\n\n他继续修炼。",
            "target_text": "Hàn Tuyệt nhìn về phía Ngọc Thanh Tông.\n\nHắn tiếp tục tu luyện.",
            "target_char_count": len(
                "Hàn Tuyệt nhìn về phía Ngọc Thanh Tông.\n\nHắn tiếp tục tu luyện."
            ),
        }
    )
    (run_dir / "translation_outputs" / "sample_1").mkdir(parents=True)
    (run_dir / "selected_samples.json").write_text(
        json.dumps({"samples": [sample]}, ensure_ascii=False),
        encoding="utf-8",
    )
    initial = {
        "paragraphs": [
            {
                "paragraph_id": "p001",
                "text": "Hàn Tuyệt nhìn về phía Ngọc Thanh Tông rộng lớn.",
            },
            {"paragraph_id": "p002", "text": "Hắn tiếp tục tu luyện chăm chỉ."},
        ]
    }
    final = {
        "paragraphs": [
            {"paragraph_id": "p001", "text": "Hàn Tuyệt nhìn về phía Ngọc Thanh Tông."},
            {"paragraph_id": "p002", "text": "Hắn tiếp tục tu luyện."},
        ]
    }
    sample_dir = run_dir / "translation_outputs" / "sample_1"
    (sample_dir / "gpt-5.4-mini_structured_initial.json").write_text(
        json.dumps(initial, ensure_ascii=False),
        encoding="utf-8",
    )
    (sample_dir / "gpt-5.4-mini_structured_final.json").write_text(
        json.dumps(final, ensure_ascii=False),
        encoding="utf-8",
    )
    validation_runs = [
        {
            "validation_index": 1,
            "run_dir": str(run_dir),
            "report": {
                "models": {
                    "gpt-5.4-mini": {
                        "samples": [stable_sample_score("sample_1", total_score=88, ratio=1.0)],
                    }
                }
            },
        }
    ]
    write_cached_eval_exports(
        validation_root=validation_root,
        validation_runs=validation_runs,
        selected_model="gpt-5.4-mini",
    )
    (validation_root / "stable_prompt.md").write_text("Frozen prompt\n", encoding="utf-8")
    (validation_root / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "prompt_id": "stable-test",
                "prompt_version": "mvp4.8-stable-candidate-v1",
                "source_eval_run_id": "eval_run_for_review",
                "model": "gpt-5.4-mini",
                "provider": "mock",
                "validation_runs": [{"validation_index": 1}],
                "per_run_scores": [{"validation_index": 1, "average_score": 88}],
                "per_sample_scores": [{"sample_id": "sample_1", "total_score": 88}],
                "average_score": 88,
                "compression_counts": [1],
                "ratio_summary": {"min": 0.9, "max": 1.1, "average": 1.0},
                "created_at": "2026-05-25T00:00:00+00:00",
                "quality_gate": "pass",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return validation_root


def write_truncated_stable_fixture(tmp_path: Path) -> Path:
    validation_root = tmp_path / "han-jue_stable_truncated"
    validation_root.mkdir(parents=True)
    run_dir = tmp_path / "eval_run_truncated"
    sample = add_paragraph_alignment(
        {
            "sample_id": "sample_1",
            "chapter_id": 1,
            "source_text": "韩绝继续修炼。",
            "target_text": "Hàn Tuyệt tiếp tục tu luyện.",
            "target_char_count": len("Hàn Tuyệt tiếp tục tu luyện."),
        }
    )
    (run_dir / "translation_outputs" / "sample_1").mkdir(parents=True)
    (run_dir / "selected_samples.json").write_text(
        json.dumps({"samples": [sample]}, ensure_ascii=False),
        encoding="utf-8",
    )
    initial = {"paragraphs": [{"paragraph_id": "p001", "text": "Hàn Tuyệt tiếp tục tu luyện."}]}
    final = {"paragraphs": [{"paragraph_id": "p001", "text": "Hàn Tuyệt tiếp tục tu luyệ"}]}
    sample_dir = run_dir / "translation_outputs" / "sample_1"
    (sample_dir / "gpt-5.4-mini_structured_initial.json").write_text(
        json.dumps(initial, ensure_ascii=False),
        encoding="utf-8",
    )
    (sample_dir / "gpt-5.4-mini_structured_final.json").write_text(
        json.dumps(final, ensure_ascii=False),
        encoding="utf-8",
    )
    write_cached_eval_exports(
        validation_root=validation_root,
        validation_runs=[
            {
                "validation_index": 1,
                "run_dir": str(run_dir),
                "report": {
                    "models": {
                        "gpt-5.4-mini": {
                            "samples": [stable_sample_score("sample_1", total_score=90)],
                        }
                    }
                },
            }
        ],
        selected_model="gpt-5.4-mini",
    )
    (validation_root / "stable_prompt.md").write_text("Unsafe prompt\n", encoding="utf-8")
    (validation_root / "stable_prompt_metadata.json").write_text(
        json.dumps(
            {
                "model": "gpt-5.4-mini",
                "provider": "mock",
                "validation_runs": [{"validation_index": 1}],
                "average_score": 90,
                "compression_counts": [1],
                "ratio_summary": {"min": 1.0, "max": 1.0, "average": 1.0},
                "quality_gate": "pass",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return validation_root


def test_eval_replay_regenerates_reports_without_api_calls(tmp_path: Path) -> None:
    validation_root = write_stable_review_fixture(tmp_path)

    result = replay_cached_eval(validation_root)

    assert Path(result["replay_report"]).exists()
    assert Path(result["replay_report_md"]).exists()
    assert result["quality_summary"]["overall_average_score"] == 88.0
    assert result["quality_summary"]["paragraph_count"] == 2
    assert result["per_sample"][0]["paragraph_count"] == 2


def test_eval_replay_command_outputs_machine_readable_json(tmp_path: Path) -> None:
    validation_root = write_stable_review_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["eval", "replay", "--run", str(validation_root), "--json"],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert data["quality_summary"]["selected_model"] == "gpt-5.4-mini"
    assert Path(data["replay_report"]).exists()


def test_replay_marks_cached_run_fail_when_truncation_exists(tmp_path: Path) -> None:
    validation_root = write_truncated_stable_fixture(tmp_path)

    result = replay_cached_eval(validation_root)

    assert result["quality_summary"]["strict_replay_pass"] is False
    assert result["quality_summary"]["truncated_paragraph_count"] == 1
    assert result["stable_prompt_invalidated"] is True
    invalidation = json.loads(
        Path(result["stable_prompt_invalidated_path"]).read_text(encoding="utf-8")
    )
    assert invalidation["reason"] == "strict_cached_replay_failed"
    assert "WARNING" in Path(result["replay_report_md"]).read_text(encoding="utf-8")


def test_eval_replay_accepts_run_id_from_eval_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validation_root = write_stable_review_fixture(tmp_path / "artifacts" / "evaluations")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["eval", "replay", "--run", validation_root.name, "--json"],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    assert Path(data["run_dir"]) == validation_root.resolve()


def test_review_stable_approve_creates_approval_without_modifying_prompt(tmp_path: Path) -> None:
    validation_root = write_stable_review_fixture(tmp_path)
    before = (validation_root / "stable_prompt.md").read_text(encoding="utf-8")

    result = stable_prompt_review(
        run=validation_root,
        approve=True,
        reject=False,
        reviewer="unit-test",
    )

    approval_path = Path(result["approval_path"])
    assert approval_path.exists()
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["decision"] == "approved"
    assert approval["reviewer"] == "unit-test"
    assert approval["quality_summary"]["average_score"] == 88
    assert (validation_root / "stable_prompt.md").read_text(encoding="utf-8") == before


def test_review_stable_cannot_approve_strict_replay_failure(tmp_path: Path) -> None:
    validation_root = write_truncated_stable_fixture(tmp_path)

    result = runner.invoke(
        app,
        ["eval", "review-stable", "--run", str(validation_root), "--approve", "--json"],
    )

    assert result.exit_code == 4
    payload = parse_json(result.output)
    assert payload["status"] == "error"
    assert "strict cached replay failed" in payload["error"]["message"]


def test_review_stable_reject_creates_rejection_with_reason(tmp_path: Path) -> None:
    validation_root = write_stable_review_fixture(tmp_path)

    result = runner.invoke(
        app,
        [
            "eval",
            "review-stable",
            "--run",
            str(validation_root),
            "--reject",
            "--reason",
            "Needs human terminology review.",
            "--reviewer",
            "unit-test",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    rejection = json.loads(Path(data["rejection_path"]).read_text(encoding="utf-8"))
    assert rejection["decision"] == "rejected"
    assert rejection["reason"] == "Needs human terminology review."
    assert rejection["stable_prompt_modified"] is False


def test_replay_missing_cached_file_fails_cleanly(tmp_path: Path) -> None:
    validation_root = tmp_path / "missing-cache"
    validation_root.mkdir()

    result = runner.invoke(
        app,
        ["eval", "replay", "--run", str(validation_root), "--json"],
    )

    assert result.exit_code == 4
    payload = parse_json(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "cached_eval_replay.json not found" in payload["error"]["message"]


def test_validate_stable_prompt_mock_command_creates_replay_and_failure_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    raw, epub = write_eval_inputs(tmp_path)

    result = runner.invoke(
        app,
        [
            "eval",
            "validate-stable-prompt",
            "--project",
            "han-jue-stable-test",
            "--raw",
            str(raw),
            "--translated",
            str(epub),
            "--provider",
            "mock",
            "--model",
            "mock-stable",
            "--max-chapters",
            "1",
            "--sample-count",
            "1",
            "--max-source-chars",
            "80",
            "--max-target-chars",
            "140",
            "--stable-run-count",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = parse_json(result.output)["data"]
    root = Path(data["validation_root"])
    assert (root / "candidate_prompt.md").exists()
    assert (root / "candidate_prompt_metadata.json").exists()
    assert (root / "cached_eval_replay.json").exists()
    assert (root / "human_review_samples.md").exists()
    assert (root / "paragraph_review_table.md").exists()
    if data["pass"]:
        assert (root / "stable_prompt.md").exists()
    else:
        assert not (root / "stable_prompt.md").exists()
        assert (root / "stable_candidate_failure_report.md").exists()
