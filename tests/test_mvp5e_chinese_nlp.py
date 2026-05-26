from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nts_cli.main import app
from nts_core.chinese_nlp import (
    FallbackSimpleAnalyzer,
    LtpServerAnalyzer,
    NlpSidecarManager,
    analyze_chapter,
    analyze_text,
    build_normalized_analysis,
    cache_build,
    convert_ner_tags,
    get_exact_source_anchors,
    get_term_candidates,
    nlp_status,
    normalize_pos,
    show_cache,
)
from nts_core.config import LtpServerConfig, NlpFallbackConfig, NlpSettings, load_nlp_config
from nts_core.projects import create_project
from nts_core.text_import import import_text_file
from nts_storage.workspace import init_workspace


runner = CliRunner()


def _workspace_with_chapters(tmp_path: Path):
    workspace = init_workspace(tmp_path / "workspace")
    create_project(
        workspace,
        slug="han-jue",
        name="Han Jue",
        source_lang="zh",
        target_lang="vi",
        domain="novel",
        genre=None,
    )
    raw = tmp_path / "raw.txt"
    raw.write_text(
        "第1章 测试\n\n韩绝进入玉清宗。【修为：无】\n\n第2章 后续\n\n他叫汤姆去拿外衣。我爱北京天安门。",
        encoding="utf-8",
    )
    import_text_file(workspace, path=raw, project_slug="han-jue", language="zh")
    return workspace


def test_nlp_config_parses_workspace_section(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "workspace")
    config_path = workspace.config_dir / "nlp.yaml"
    config_path.write_text(
        """
nlp:
  enabled: true
  provider: fallback_simple
  auto_start: false
  ltp_server:
    base_url: "http://127.0.0.1:3999"
    working_dir: "C:/tmp/ltp"
    start_command: "cargo run --release"
    startup_timeout_seconds: 2
    request_timeout_seconds: 1
    max_sentences_per_request: 4
  fallback:
    enabled: true
""",
        encoding="utf-8",
    )

    loaded = load_nlp_config(workspace=workspace)

    assert loaded.provider == "fallback_simple"
    assert loaded.auto_start is False
    assert loaded.ltp_server.base_url == "http://127.0.0.1:3999"
    assert loaded.ltp_server.max_sentences_per_request == 4


def test_sidecar_manager_does_not_start_duplicate_when_healthy(monkeypatch) -> None:
    monkeypatch.setattr(LtpServerAnalyzer, "health_check", lambda self: (True, None))
    config = NlpSettings(ltp_server=LtpServerConfig(working_dir="C:/missing"))

    status = NlpSidecarManager(config).ensure_ltp_server(auto_start=True)

    assert status.healthy is True
    assert status.start_attempted is False
    assert status.pid is None


def test_sidecar_manager_handles_start_failure_with_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(LtpServerAnalyzer, "health_check", lambda self: (False, "connection refused"))
    config = NlpSettings(
        ltp_server=LtpServerConfig(working_dir=str(tmp_path / "missing")),
        fallback=NlpFallbackConfig(enabled=True),
    )

    status = NlpSidecarManager(config).ensure_ltp_server(auto_start=True)

    assert status.healthy is False
    assert status.start_attempted is True
    assert status.degraded is True
    assert "working_dir not found" in (status.error or "")


def test_ltp_server_analyzer_parses_mocked_cws_pos_ner(monkeypatch) -> None:
    analyzer = LtpServerAnalyzer(base_url="http://127.0.0.1:3003")

    def fake_post(text: str):
        assert "汤姆" in text
        return {"cws": [["他", "叫", "汤姆"]], "pos": [["r", "v", "nh"]], "ner": [["O", "O", "S-Nh"]]}

    monkeypatch.setattr(analyzer, "_post_analyze", fake_post)

    rows = analyzer.analyze_sentences(["他叫汤姆"])

    assert rows[0]["words"] == ["他", "叫", "汤姆"]
    assert rows[0]["pos"] == ["r", "v", "nh"]
    assert rows[0]["ner"] == ["O", "O", "S-Nh"]


def test_utf8_chinese_text_normalizes_without_mojibake(monkeypatch) -> None:
    analyzer = LtpServerAnalyzer(base_url="http://127.0.0.1:3003")
    monkeypatch.setattr(
        analyzer,
        "_post_analyze",
        lambda text: {"cws": [["他", "叫", "汤姆"]], "pos": [["r", "v", "nh"]], "ner": [["O", "O", "S-Nh"]]},
    )

    analysis = build_normalized_analysis(
        text="他叫汤姆。",
        project_slug=None,
        chapter_id=None,
        provider=analyzer,
        degraded=False,
    )

    assert analysis["sentences"][0]["tokens"][2]["text"] == "汤姆"
    assert "æ" not in json.dumps(analysis, ensure_ascii=False)


def test_fallback_analyzer_returns_degraded_schema() -> None:
    analysis = build_normalized_analysis(
        text="韩绝进入玉清宗。【修为：无】",
        project_slug="han-jue",
        chapter_id="chapter_1",
        provider=FallbackSimpleAnalyzer(),
        degraded=True,
    )

    assert analysis["meta"]["degraded"] is True
    assert analysis["meta"]["provider"] == "fallback_simple"
    assert analysis["sentences"]
    assert analysis["chapter_candidates"]["phrase_candidates"]


def test_pos_normalization_and_ner_span_conversion() -> None:
    assert normalize_pos("nh") == "name"
    assert normalize_pos("v") == "verb"
    tokens = [
        {"text": "玉", "start": 0, "end": 1},
        {"text": "清", "start": 1, "end": 2},
        {"text": "宗", "start": 2, "end": 3},
    ]

    spans = convert_ner_tags(tokens, ["B-Ni", "I-Ni", "E-Ni"])

    assert spans == [
        {
            "text": "玉清宗",
            "start": 0,
            "end": 3,
            "entity_type": "organization",
            "token_start": 0,
            "token_end": 3,
        }
    ]


def test_candidate_derivation_is_conservative() -> None:
    analysis = build_normalized_analysis(
        text="韩绝进入玉清宗。韩绝留在玉清宗。【修为：无】",
        project_slug="han-jue",
        chapter_id="chapter_1",
        provider=FallbackSimpleAnalyzer(),
        degraded=True,
    )

    terms = {candidate["text"] for candidate in analysis["chapter_candidates"]["term_candidates"]}
    phrases = {candidate["text"] for candidate in analysis["chapter_candidates"]["phrase_candidates"]}
    assert "玉清宗" in terms
    assert "【修为：无】" in phrases


def test_analyze_command_returns_normalized_schema() -> None:
    result = runner.invoke(
        app,
        ["nlp", "analyze", "--text", "韩绝进入玉清宗。", "--provider", "fallback_simple", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["meta"]["provider"] == "fallback_simple"
    assert payload["data"]["meta"]["degraded"] is True


def test_analyze_chapter_writes_artifact(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)

    result = analyze_chapter(
        workspace,
        project_slug="han-jue",
        chapter_ref="1",
        provider_kind="fallback_simple",
    )

    artifact = Path(result["artifact_path"])
    assert artifact.exists()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["meta"]["project_slug"] == "han-jue"
    assert result["degraded"] is True


def test_cache_build_writes_manifest_and_skips_valid_cache(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)

    first = cache_build(
        workspace,
        project_slug="han-jue",
        chapters="1-2",
        provider_kind="fallback_simple",
    )
    second = cache_build(
        workspace,
        project_slug="han-jue",
        chapters="1-2",
        provider_kind="fallback_simple",
        missing_only=True,
    )

    manifest = Path(first["manifest_path"])
    assert manifest.exists()
    assert first["coverage_count"] == 2
    assert all(result["status"] == "cache_hit" for result in second["results"])


def test_force_invalidates_cache(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)
    first = analyze_chapter(
        workspace,
        project_slug="han-jue",
        chapter_ref="1",
        provider_kind="fallback_simple",
    )
    artifact = Path(first["artifact_path"])
    data = json.loads(artifact.read_text(encoding="utf-8"))
    data["meta"]["heuristics_version"] = "old"
    artifact.write_text(json.dumps(data), encoding="utf-8")

    rebuilt = analyze_chapter(
        workspace,
        project_slug="han-jue",
        chapter_ref="1",
        provider_kind="fallback_simple",
        force=True,
    )
    new_data = json.loads(Path(rebuilt["artifact_path"]).read_text(encoding="utf-8"))

    assert new_data["meta"]["heuristics_version"] == "mvp5e-v1"


def test_nlp_status_works_with_fallback_provider(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)

    status = nlp_status(workspace, project_slug="han-jue", provider_kind="fallback_simple")

    assert status["healthy"] is True
    assert status["degraded"] is True
    assert status["cache"]["coverage_count"] == 0


def test_show_cache_and_read_only_helpers(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)
    cache_build(workspace, project_slug="han-jue", chapters="1", provider_kind="fallback_simple")

    shown = show_cache(workspace, project_slug="han-jue", chapter_ref="1")
    terms = get_term_candidates(workspace, "han-jue", "1")
    anchors = get_exact_source_anchors(workspace, "han-jue", "1")

    assert shown["summary"]["sentence_count"] >= 1
    assert terms
    assert "玉清宗" in anchors


def test_cli_analyze_chapter_and_cache_build_write_artifacts(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)
    analyze = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "nlp",
            "analyze-chapter",
            "--project",
            "han-jue",
            "--chapter",
            "1",
            "--provider",
            "fallback_simple",
            "--json",
        ],
    )
    build = runner.invoke(
        app,
        [
            "--workspace",
            str(workspace.path),
            "nlp",
            "cache-build",
            "--project",
            "han-jue",
            "--chapters",
            "1-2",
            "--provider",
            "fallback_simple",
            "--missing-only",
            "--json",
        ],
    )

    assert analyze.exit_code == 0, analyze.output
    assert build.exit_code == 0, build.output
    payload = json.loads(build.output)
    assert Path(payload["data"]["manifest_path"]).exists()
    assert Path(payload["data"]["report_path"]).exists()


def test_no_memory_or_dictionary_is_created_by_nlp_cache(tmp_path: Path) -> None:
    workspace = _workspace_with_chapters(tmp_path)
    cache_build(workspace, project_slug="han-jue", chapters="1", provider_kind="fallback_simple")

    import sqlite3

    with sqlite3.connect(workspace.db_path) as conn:
        memory_count = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%dictionary%'"
            )
        }

    assert memory_count == 0
    assert tables == set()
