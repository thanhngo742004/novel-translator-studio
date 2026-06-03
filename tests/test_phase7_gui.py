from __future__ import annotations

import json
import re
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from urllib.error import URLError

import nts_gui_backend.service as gui_service_module
from nts_core.projects import create_project
from nts_gui_backend.service import (
    GUI_PROVIDER_CONFIG_RELATIVE_PATH,
    JOB_LOCK,
    JOB_REGISTRY,
    SAFE_TRANSLATION_DEFAULTS,
    VISIBLE_BUTTON_WIRING,
    GuiService,
)
from nts_storage.database import connection, json_dumps, new_id, utc_now
from nts_storage.workspace import init_workspace


FRONTEND_DIR = Path("apps/gui/frontend")
BACKEND_SERVICE_PATH = Path("apps/gui/backend/nts_gui_backend/service.py")


def _service(tmp_path: Path) -> tuple[GuiService, Path]:
    workspace = init_workspace(tmp_path / "workspace")
    return GuiService(workspace.path), workspace.path


def _project(workspace_path: Path, slug: str = "demo") -> dict[str, str]:
    return create_project(
        init_workspace(workspace_path),
        slug=slug,
        name="Demo Novel",
        source_lang="zh",
        target_lang="vi",
        domain="novel",
        genre=None,
    )


def _review_fixture(workspace_path: Path, project: dict[str, str]) -> str:
    document_id = new_id("doc")
    chapter_id = new_id("chapter")
    segment_id = new_id("segment")
    translation_id = new_id("translation")
    now = utc_now()
    with connection(workspace_path / "nts.db") as conn:
        conn.execute(
            """
            INSERT INTO documents(id, project_id, doc_kind, source_path, artifact_path,
                                  checksum_sha256, language, metadata_json, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, project["id"], "text", "source.txt", "artifacts/raw/source.txt", "sha", "zh", "{}", now),
        )
        conn.execute(
            """
            INSERT INTO chapters(id, project_id, document_id, chapter_no, title, boundary_start,
                                 boundary_end, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chapter_id, project["id"], document_id, 1, "Chương 1", 0, 120, 1.0, now),
        )
        conn.execute(
            """
            INSERT INTO segments(id, project_id, chapter_id, segment_no, source_text,
                                 normalized_text, paragraph_no, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (segment_id, project["id"], chapter_id, 1, "原文", "原文", 1, "{}", now),
        )
        conn.execute(
            """
            INSERT INTO translations(id, segment_id, chapter_id, translation_kind, text, status,
                                     quality_json, is_current, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (translation_id, segment_id, chapter_id, "machine", "Bản dịch", "needs_review", json_dumps({"warning": True}), 1, now),
        )
        conn.commit()
    return translation_id


def _short_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()[:12]


def test_backend_health_project_listing_and_ltp_status(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    health = service.handle("GET", "/api/health")
    projects = service.handle("GET", "/api/projects")
    ltp = service.handle("GET", "/api/ltp/status")

    assert health["app"] == "NTS Studio"
    assert health["workspace_ready"] is True
    assert projects["projects"][0]["slug"] == "demo"
    assert ltp["status"] in {"healthy", "unavailable", "reachable_but_unhealthy", "disabled", "degraded", "error"}
    if ltp["status"] != "healthy":
        assert ltp["healthy"] is False


def test_gui_version_endpoint_exposes_phase_and_asset_hashes(tmp_path: Path) -> None:
    service, _workspace_path = _service(tmp_path)

    version = service.handle("GET", "/api/gui/version")

    assert version["phase_label"] == "phase7.5-browser-reality-fix"
    assert version["server_start_time"]
    assert version["cache_policy"] == "no-store"
    assert version["app_js"]["hash"]
    assert version["styles_css"]["hash"]
    assert version["backend_service"]["hash"]


def test_api_key_values_are_not_exposed(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    monkeypatch.setenv("MOCK_API_KEY", "test-secret-value-that-must-not-appear")

    status = service.handle("GET", "/api/system/status")
    raw = json.dumps(status, ensure_ascii=False)

    assert "test-secret-value-that-must-not-appear" not in raw
    assert any(provider.get("api_key_env") == "MOCK_API_KEY" for provider in status["providers"])
    assert all(provider.get("api_key_value") is None for provider in status["providers"])
    assert Path(status["workspace"]["path"]) == workspace_path


def test_provider_settings_save_load_redacts_secret(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)

    saved = service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gpt-test-primary",
            "fallback_model": "gpt-test-fallback",
            "api_key": "secret-provider-key",
            "timeout_seconds": 45,
            "max_retries": 3,
        },
    )
    loaded = service.handle("GET", "/api/settings/provider")
    raw_loaded = json.dumps(loaded, ensure_ascii=False)
    config_path = workspace_path / GUI_PROVIDER_CONFIG_RELATIVE_PATH

    assert config_path.exists()
    assert "secret-provider-key" in config_path.read_text(encoding="utf-8")
    assert "secret-provider-key" not in json.dumps(saved, ensure_ascii=False)
    assert "secret-provider-key" not in raw_loaded
    assert loaded["settings"]["api_key"] == "********"
    assert loaded["settings"]["api_key_configured"] is True
    assert loaded["settings"]["provider_name"] == "gui-openai"
    assert loaded["settings"]["primary_model"] == "gpt-test-primary"


def test_provider_test_returns_actionable_redacted_result(tmp_path: Path, monkeypatch) -> None:
    service, _workspace_path = _service(tmp_path)
    captured: dict[str, object] = {}

    def fake_preflight(settings: dict[str, object]) -> tuple[str, str]:
        captured.update(settings)
        return "gpt-test-primary", "primary_ok"

    monkeypatch.setattr(GuiService, "_real_provider_preflight", staticmethod(fake_preflight))

    result = service.handle(
        "POST",
        "/api/settings/provider/test",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gpt-test-primary",
            "api_key": "secret-provider-key",
        },
    )
    raw = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["route_status"] == "primary_ok"
    assert result["chosen_model"] == "gpt-test-primary"
    assert result["provider"] == "gui-openai"
    assert result["base_url"] == "https://api.example.test/v1"
    assert result["primary_model"] == "gpt-test-primary"
    assert captured["api_key"] == "secret-provider-key"
    assert "secret-provider-key" not in raw
    assert result["settings"]["api_key"] == "********"


def test_provider_test_failure_is_actionable_and_redacted(tmp_path: Path, monkeypatch) -> None:
    service, _workspace_path = _service(tmp_path)

    def fake_preflight(_settings: dict[str, object]) -> tuple[str, str]:
        raise ValueError("Model không tồn tại")

    monkeypatch.setattr(GuiService, "_real_provider_preflight", staticmethod(fake_preflight))

    result = service.handle(
        "POST",
        "/api/settings/provider/test",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "missing-model",
            "fallback_model": "missing-fallback",
            "api_key": "secret-provider-key",
        },
    )
    raw = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["route_status"] == "model_not_found"
    assert result["message"] == "Model không tồn tại"
    assert "secret-provider-key" not in raw


def test_provider_test_redacts_under_real_http_error(tmp_path: Path, monkeypatch) -> None:
    service, _workspace_path = _service(tmp_path)

    def fake_preflight(settings: dict[str, object]) -> tuple[str, str]:
        raise ValueError(f"401 auth failed for {settings['api_key']}")

    monkeypatch.setattr(GuiService, "_real_provider_preflight", staticmethod(fake_preflight))

    result = service.handle(
        "POST",
        "/api/settings/provider/test",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gpt-test-primary",
            "api_key": "secret-provider-key",
        },
    )
    raw = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["route_status"] == "auth_failed"
    assert "secret-provider-key" not in raw
    assert "********" in raw


def test_redacted_text_handles_empty_secret(tmp_path: Path) -> None:
    service, _workspace_path = _service(tmp_path)

    assert service._redact_text("") == ""
    assert service._redact_text("plain error") == "plain error"


def test_provider_preflight_normalizes_base_url_and_uses_core_preflight(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    captured: dict[str, object] = {}

    def fake_core_preflight(workspace, *, run_dir, provider_key, primary_model, fallback_model=None):
        captured.update(
            {
                "workspace": workspace.path,
                "provider_key": provider_key,
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "run_dir": run_dir,
            }
        )
        return {
            "pass": True,
            "chosen_model": primary_model,
            "fallback_model_used": False,
            "primary_status": {"ok": True, "status": "ok"},
            "fallback_status": {"ok": False, "status": "not_configured"},
        }

    monkeypatch.setattr(gui_service_module, "write_provider_preflight", fake_core_preflight)

    result = service.handle(
        "POST",
        "/api/settings/provider/test",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.test/v1/v1",
            "primary_model": "gpt-test-primary",
            "api_key": "secret-provider-key",
        },
    )

    assert result["ok"] is True
    assert result["chosen_model"] == "gpt-test-primary"
    assert captured["workspace"] == workspace_path
    assert captured["provider_key"] == "gui-openai"
    assert captured["primary_model"] == "gpt-test-primary"
    saved = json.loads((workspace_path / GUI_PROVIDER_CONFIG_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://api.example.test/v1"
    assert "secret-provider-key" not in json.dumps(result, ensure_ascii=False)


def test_provider_key_can_be_replaced_and_cleared(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)

    service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.one.test/v1",
            "primary_model": "model-one",
            "api_key": "first-secret",
        },
    )
    replaced = service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.two.test/v1",
            "primary_model": "model-two",
            "api_key": "second-secret",
        },
    )
    cleared = service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-openai",
            "provider_type": "openai_responses",
            "base_url": "https://api.two.test/v1",
            "primary_model": "model-two",
            "clear_api_key": True,
        },
    )
    config_text = (workspace_path / GUI_PROVIDER_CONFIG_RELATIVE_PATH).read_text(encoding="utf-8")

    assert replaced["settings"]["base_url"] == "https://api.two.test/v1"
    assert replaced["settings"]["primary_model"] == "model-two"
    assert replaced["settings"]["api_key"] == "********"
    assert "first-secret" not in config_text
    assert "second-secret" not in json.dumps(replaced, ensure_ascii=False)
    assert cleared["settings"]["api_key_configured"] is False
    assert cleared["settings"]["api_key"] == ""


def test_translation_payload_uses_safe_defaults_and_no_raw_nlp_cache(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)
    monkeypatch.setattr(GuiService, "_run_translation_job", lambda self, job_id: None)
    service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-provider",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gui-primary",
            "fallback_model": "gui-fallback",
            "api_key": "secret-provider-key",
        },
    )

    result = service.handle(
        "POST",
        "/api/projects/demo/translate/batch",
        {"chapter_start": 4, "chapter_end": 23, "chapter_count": 20},
    )
    payload = result["payload"]
    raw_payload = json.dumps(payload, ensure_ascii=False)

    assert payload["safe_profile"] is True
    assert payload["use_approved_dictionary"] is True
    assert payload["use_approved_memory"] is True
    assert payload["emit_prompt_artifacts"] is True
    assert payload["resumable"] is True
    assert payload["use_approved_rules"] is False
    assert payload["inject_raw_nlp_cache"] is False
    assert payload["chapter_start"] == 4
    assert payload["chapter_end"] == 23
    assert payload["chapter_range"] == "4-23"
    assert result["message"] == "Đã bắt đầu tác vụ dịch an toàn"
    assert result["job_id"].startswith("job_")
    assert result["artifact_path"].replace("\\", "/").endswith(f"artifacts/gui_jobs/{result['job_id']}")
    assert payload["provider"]["source"] == "gui_local"
    assert payload["provider"]["provider_name"] == "gui-provider"
    assert payload["provider"]["primary_model"] == "gui-primary"
    assert payload["provider"]["fallback_model"] == "gui-fallback"
    assert payload["provider"]["api_key_configured"] is True
    assert payload["provider"]["api_key_value"] is None
    assert "secret-provider-key" not in raw_payload
    assert "--use-approved-rules" not in raw_payload


def test_translation_range_validation_rejects_invalid_range(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    try:
        service.handle("POST", "/api/projects/demo/translate/batch", {"chapter_start": 5, "chapter_end": 3})
    except Exception as exc:
        assert getattr(exc, "code", "") == "invalid_chapter_range"
    else:
        raise AssertionError("Expected invalid_chapter_range")


def test_translation_job_invokes_phase6_rollout_with_gui_provider(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)
    captured: dict[str, object] = {}

    def fake_rollout(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        run_dir = Path(kwargs["output_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "final_decision": "PASS",
            "run_id": "fake_phase6_run",
            "run_dir": str(run_dir),
            "chapters_processed": 3,
            "chunks_processed": 9,
            "warnings": [],
        }
        (run_dir / "production_rollout_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return summary

    monkeypatch.setattr(gui_service_module, "run_controlled_production_rollout", fake_rollout)
    service.handle(
        "POST",
        "/api/settings/provider",
        {
            "provider_name": "gui-provider",
            "provider_type": "openai_responses",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gui-primary",
            "fallback_model": "gui-fallback",
            "api_key": "secret-provider-key",
        },
    )

    started = service.handle("POST", "/api/projects/demo/translate/batch", {"chapter_start": 1, "chapter_end": 3})
    deadline = time.monotonic() + 3
    status = service.handle("GET", f"/api/jobs/{started['job_id']}")
    while status["status"] not in {"completed", "blocked", "error"} and time.monotonic() < deadline:
        time.sleep(0.02)
        status = service.handle("GET", f"/api/jobs/{started['job_id']}")

    assert status["status"] == "completed"
    assert status["percent"] == 100
    assert captured["project_slug"] == "demo"
    assert captured["provider_key"] == "gui-provider"
    assert captured["model"] == "gui-primary"
    assert captured["fallback_model"] == "gui-fallback"
    assert captured["chapters"] == "1-3"
    assert captured["max_chapters"] == 3
    assert captured["dictionary_max_entries"] == 8
    assert captured["memory_max_items"] == 6
    assert captured["support_max_chars"] == 1200
    assert captured["emit_prompt_artifacts"] is True
    assert captured["resumable"] is True
    assert captured["dry_run"] is False
    assert "secret-provider-key" not in json.dumps(status, ensure_ascii=False)


def test_translation_job_readiness_uses_saved_provider_not_frontend_payload(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    def fake_rollout(*args, **kwargs):
        run_dir = Path(kwargs["output_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)
        return {
            "final_decision": "PASS",
            "run_id": "saved_provider_run",
            "run_dir": str(run_dir),
            "chapters_processed": 1,
            "chunks_processed": 1,
            "warnings": [],
        }

    monkeypatch.setattr(gui_service_module, "run_controlled_production_rollout", fake_rollout)
    service.save_provider_settings(
        {
            "provider_name": "gui-provider",
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.test/v1",
            "primary_model": "gui-primary",
            "api_key": "secret-provider-key",
        }
    )
    payload = service.translation_payload({"chapter_start": 1, "chapter_end": 1})
    payload["provider"] = service.get_provider_settings()

    started = service.start_translation_job("demo", "trial", payload)

    assert started["status"] == "queued"


def test_job_status_progress_is_artifact_backed_and_capped_before_completion(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    job_id = new_id("job")
    artifact_root = workspace_path / "artifacts" / "gui_jobs" / job_id
    batch_dir = artifact_root / "production_rollout" / "batch_run"
    batch_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "job_id": job_id,
        "project": "demo",
        "project_name": "Demo Novel",
        "status": "running",
        "stage": "translation",
        "chapter_start": 1,
        "chapter_end": 4,
        "current_chapter": None,
        "current_chunk": None,
        "chapters_completed": 0,
        "chapters_total": 4,
        "chunks_completed": 0,
        "chunks_total": None,
        "percent": 0,
        "created_at_monotonic": time.monotonic(),
        "latest_message": "Đang chuẩn bị...",
        "warnings": [],
        "artifact_path": str(artifact_root),
        "payload": {},
        "error": None,
    }
    with JOB_LOCK:
        JOB_REGISTRY[job_id] = job
    chapter_results = batch_dir / "chapter_results.json"
    chapter_results.write_text(
        json.dumps(
            {
                "chapters": [
                    {"chapter_no": 1, "status": "success", "chunks_total": 3, "chunks_completed": 3},
                    {"chapter_no": 2, "status": "running", "chunks_total": 3, "chunks_completed": 1},
                    {"chapter_no": 3, "status": "queued", "chunks_total": 3, "chunks_completed": 0},
                    {"chapter_no": 4, "status": "queued", "chunks_total": 3, "chunks_completed": 0},
                ]
            }
        ),
        encoding="utf-8",
    )

    first = service.handle("GET", f"/api/jobs/{job_id}")
    chapter_results.write_text(
        json.dumps(
            {
                "chapters": [
                    {"chapter_no": 1, "status": "success", "chunks_total": 3, "chunks_completed": 3},
                    {"chapter_no": 2, "status": "success", "chunks_total": 3, "chunks_completed": 3},
                    {"chapter_no": 3, "status": "success", "chunks_total": 3, "chunks_completed": 3},
                    {"chapter_no": 4, "status": "success", "chunks_total": 3, "chunks_completed": 3},
                ]
            }
        ),
        encoding="utf-8",
    )
    second = service.handle("GET", f"/api/jobs/{job_id}")
    summary = {
        "final_decision": "PASS",
        "run_id": "completed_run",
        "run_dir": str(artifact_root / "production_rollout"),
        "chapters_processed": 4,
        "chunks_processed": 12,
        "warnings": [],
    }
    (artifact_root / "production_rollout" / "production_rollout_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    completed = service.handle("GET", f"/api/jobs/{job_id}")

    assert first["percent"] == 25
    assert first["current_chapter"] == 2
    assert first["chunks_completed"] == 4
    assert second["status"] == "running"
    assert second["percent"] == 99
    assert completed["status"] == "completed"
    assert completed["percent"] == 100


def test_job_status_uses_chunk_plan_chunk_count_when_totals_known(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    job_id = new_id("job")
    rollout_dir = workspace_path / "artifacts" / "gui_jobs" / job_id / "production_rollout"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    with JOB_LOCK:
        JOB_REGISTRY[job_id] = {
            "job_id": job_id,
            "project": "demo",
            "project_name": "Demo Novel",
            "status": "running",
            "stage": "production_rollout",
            "chapter_start": 1,
            "chapter_end": 2,
            "current_chapter": None,
            "current_chunk": None,
            "chapters_completed": 0,
            "chapters_total": 2,
            "chunks_completed": 0,
            "chunks_total": None,
            "percent": 0,
            "created_at_monotonic": time.monotonic(),
            "latest_message": "Đang chuẩn bị...",
            "warnings": [],
            "artifact_path": str(rollout_dir.parent),
            "payload": {},
            "error": None,
        }
    (rollout_dir / "chunk_plan.json").write_text(
        json.dumps({"chapters": [{"chapter_no": 1, "chunk_count": 2}, {"chapter_no": 2, "chunk_count": 3}]}),
        encoding="utf-8",
    )

    status = service.handle("GET", f"/api/jobs/{job_id}")

    assert status["status"] == "running"
    assert status["chunks_total"] == 5
    assert status["percent"] == 0


def test_job_status_reports_blocked_and_error_terminal_states(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    for decision, expected in [("BLOCKED", "blocked"), ("FAIL", "error")]:
        job_id = new_id("job")
        rollout_dir = workspace_path / "artifacts" / "gui_jobs" / job_id / "production_rollout"
        rollout_dir.mkdir(parents=True, exist_ok=True)
        with JOB_LOCK:
            JOB_REGISTRY[job_id] = {
                "job_id": job_id,
                "project": "demo",
                "project_name": "Demo Novel",
                "status": "running",
                "stage": "translation",
                "chapter_start": 1,
                "chapter_end": 2,
                "current_chapter": None,
                "current_chunk": None,
                "chapters_completed": 0,
                "chapters_total": 2,
                "chunks_completed": 0,
                "chunks_total": None,
                "percent": 0,
                "created_at_monotonic": time.monotonic(),
                "latest_message": "Đang chuẩn bị...",
                "warnings": [],
                "artifact_path": str(rollout_dir.parent),
                "payload": {},
                "error": None,
            }
        (rollout_dir / "production_rollout_summary.json").write_text(
            json.dumps({"final_decision": decision, "run_id": f"run_{decision}", "chapters_processed": 1, "chunks_processed": 2}),
            encoding="utf-8",
        )

        status = service.handle("GET", f"/api/jobs/{job_id}")

        assert status["status"] == expected
        assert status["percent"] < 100


def test_friendly_checkboxes_map_to_safe_flags() -> None:
    assert SAFE_TRANSLATION_DEFAULTS == {
        "safe_profile": True,
        "use_stable_prompt": True,
        "use_hybrid_prompt": True,
        "use_approved_dictionary": True,
        "use_approved_memory": True,
        "emit_prompt_artifacts": True,
        "resumable": True,
        "dictionary_max_entries": 8,
        "memory_max_items": 6,
        "support_max_chars": 1200,
        "use_approved_rules": False,
        "inject_raw_nlp_cache": False,
    }


def test_review_save_does_not_learn_but_learn_creates_scoped_candidate(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    project = _project(workspace_path)
    translation_id = _review_fixture(workspace_path, project)

    aggregate_queue = service.handle("GET", "/api/review-queue")
    project_queue = service.handle("GET", "/api/projects/demo/review-queue")

    saved = service.handle("POST", f"/api/review/{translation_id}/save", {"reviewed_text": "Bản sửa"})
    learned = service.handle("POST", f"/api/review/{translation_id}/learn", {"reviewed_text": "Bản sửa học"})

    assert aggregate_queue["items"][0]["id"] == translation_id
    assert project_queue["items"][0]["id"] == translation_id
    assert saved["status"] == "saved_only"
    assert saved["learn"] is False
    assert saved["mutated_global_memory"] is False
    assert learned["status"] == "learn_candidate_created"
    assert learned["learn"] is True
    assert learned["mutated_global_memory"] is False
    artifact = workspace_path / learned["candidate_artifact"]
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["scope"] == "project"
    assert payload["mutated_global_memory"] is False


def test_artifact_listing_uses_bounded_previews(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    artifact = workspace_path / "artifacts" / "reports" / "long_report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("A" * 1000, encoding="utf-8")

    artifacts = service.artifacts("unmatched", limit=5, preview_chars=120)

    assert artifacts
    assert len(artifacts[0]["preview"]) == 120
    assert artifacts[0]["preview_truncated"] is True


def test_unsupported_export_state_is_explicit(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    result = service.handle("POST", "/api/projects/demo/export", {"format": "epub"})

    assert result == {"format": "epub", "status": "unsupported", "label": "Sắp hỗ trợ"}


def test_txt_export_writes_project_chapters_to_single_txt_folder(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    project = _project(workspace_path)
    _review_fixture(workspace_path, project)

    exported = service.handle("POST", "/api/projects/demo/export", {"format": "txt"})
    txt_output_path = Path(exported["txt_output_path"])

    assert exported["status"] == "exported"
    assert txt_output_path == workspace_path / "artifacts" / "exports" / "demo" / "txt_chapters"
    assert exported["file_count"] == 1
    assert exported["files"] == [str(txt_output_path / "chapter_001.vi.txt")]
    assert (txt_output_path / "chapter_001.vi.txt").read_text(encoding="utf-8").strip() == "Chương 1\n\nBản dịch"
    assert not (txt_output_path / "full_novel.vi.txt").exists()


def test_open_project_folder_endpoint_returns_path_not_technical(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    paths = service.handle("GET", "/api/projects/demo/paths")
    opened = service.handle("POST", "/api/projects/demo/open-folder", {"open": False})

    assert paths["project"] == "demo"
    assert Path(paths["artifact_path"]).exists()
    assert Path(paths["output_path"]).exists()
    assert opened["status"] == "path_only"
    assert opened["opened"] is False
    assert opened["target"] == "preferred_output_path"
    assert opened["path"] == paths["preferred_output_path"] == paths["txt_output_path"]
    assert opened["path"].endswith(str(Path("artifacts") / "exports" / "demo" / "txt_chapters"))
    assert "technical" not in json.dumps(opened).lower()
    assert str(workspace_path) in opened["path"]


def test_open_project_folder_prefers_txt_chapter_output_not_rollout_artifacts(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)
    run_dir = workspace_path / "artifacts" / "gui_jobs" / "job_demo" / "production_rollout"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "production_rollout_summary.json").write_text(
        json.dumps({"project_slug": "demo", "run_dir": str(run_dir), "final_decision": "PASS"}),
        encoding="utf-8",
    )

    opened = service.handle("POST", "/api/projects/demo/open-folder", {"open": False})

    assert opened["target"] == "preferred_output_path"
    assert opened["path"] == str(workspace_path / "artifacts" / "exports" / "demo" / "txt_chapters")
    assert opened["path"] != str(run_dir)
    assert opened["latest_job_output_path"] == str(run_dir)


def test_open_project_folder_rejects_unsafe_target_key(tmp_path: Path) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)

    try:
        service.handle("POST", "/api/projects/demo/open-folder", {"target": "../../../Windows", "open": False})
    except Exception as exc:
        assert getattr(exc, "code", "") == "unsafe_target"
        assert getattr(exc, "status", None) == 403
    else:
        raise AssertionError("Expected unsafe_target")


def test_workspace_open_uses_windows_explorer_when_requested(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(gui_service_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(gui_service_module.subprocess, "Popen", lambda args: calls.append(list(args)))

    opened = service.handle("POST", "/api/workspace/open-folder", {"open": True})

    assert opened["opened"] is True
    assert opened["method"] == "explorer.exe"
    assert opened["workspace_path"] == str(workspace_path)
    assert opened["target"] == "output_root"
    assert calls == [["explorer.exe", str(workspace_path / "artifacts" / "exports")]]


def test_project_open_uses_windows_explorer_when_requested(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    _project(workspace_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(gui_service_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(gui_service_module.subprocess, "Popen", lambda args: calls.append(list(args)))

    opened = service.handle("POST", "/api/projects/demo/open-folder", {"open": True})

    assert opened["opened"] is True
    assert opened["method"] == "explorer.exe"
    assert calls and calls[0][0] == "explorer.exe"
    assert calls[0][1].endswith(str(Path("artifacts") / "exports" / "demo" / "txt_chapters"))


def test_open_path_falls_back_only_when_explorer_launch_fails(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)

    def fail_open(_args):
        raise OSError("explorer failed")

    monkeypatch.setattr(gui_service_module.os, "name", "nt", raising=False)
    monkeypatch.setattr(gui_service_module.subprocess, "Popen", fail_open)

    opened = service.handle("POST", "/api/system/open-path", {"path": str(workspace_path), "open": True})

    assert opened["opened"] is False
    assert opened["fallback"] == "copy_path"
    assert opened["method"] == "explorer.exe"
    assert opened["message"] == "Không mở được File Explorer, hãy sao chép đường dẫn."


def test_system_open_path_rejects_unsafe_outside_workspace(tmp_path: Path) -> None:
    service, _workspace_path = _service(tmp_path)
    outside = tmp_path / "outside" / "secret"

    try:
        service.handle("POST", "/api/system/open-path", {"path": str(outside), "open": False})
    except Exception as exc:
        assert getattr(exc, "code", "") == "unsafe_path"
    else:
        raise AssertionError("Expected unsafe_path")


def test_workspace_and_ltp_endpoints_return_actionable_results(tmp_path: Path, monkeypatch) -> None:
    service, workspace_path = _service(tmp_path)
    monkeypatch.setattr(gui_service_module.LtpServerAnalyzer, "analyze_sentences", lambda self, sentences: (_ for _ in ()).throw(URLError("refused")))

    workspace = service.handle("GET", "/api/workspace")
    validated = service.handle("POST", "/api/workspace", {"workspace_path": str(tmp_path / "alternate_ws")})
    opened = service.handle("POST", "/api/workspace/open-folder", {"open": False})
    ltp_status = service.handle("GET", "/api/ltp/status")
    ltp_start = service.handle("POST", "/api/ltp/start")

    assert Path(workspace["path"]) == workspace_path
    assert validated["status"] == "validated"
    assert Path(validated["path"]).exists()
    assert opened["status"] == "path_only"
    assert ltp_status["status"] == "unavailable"
    assert ltp_status["healthy"] is False
    assert ltp_status["reachable"] is False
    assert ltp_status["message"] == "LTP chưa chạy"
    assert "start_command" in ltp_status
    assert ltp_start["status"] == "unsupported"
    assert "copyable_command" in ltp_start


def test_ltp_status_reachable_but_invalid_is_not_healthy(tmp_path: Path, monkeypatch) -> None:
    service, _workspace_path = _service(tmp_path)
    monkeypatch.setattr(gui_service_module.LtpServerAnalyzer, "analyze_sentences", lambda self, sentences: (_ for _ in ()).throw(ValueError("bad analyze")))

    status = service.handle("GET", "/api/ltp/status")

    assert status["status"] == "reachable_but_unhealthy"
    assert status["healthy"] is False
    assert status["reachable"] is True
    assert status["message"] == "Có tiến trình ở cổng LTP nhưng LTP không trả kết quả phân tích hợp lệ"


def test_ltp_status_healthy_requires_valid_analyze_response(tmp_path: Path, monkeypatch) -> None:
    service, _workspace_path = _service(tmp_path)
    monkeypatch.setattr(gui_service_module.LtpServerAnalyzer, "analyze_sentences", lambda self, sentences: [{"words": ["我", "爱", "北京"]}])

    status = service.handle("GET", "/api/ltp/status")

    assert status["status"] == "healthy"
    assert status["healthy"] is True
    assert status["reachable"] is True
    assert status["degraded"] is False
    assert status["message"] == "LTP đang hoạt động"


def test_every_visible_button_has_wiring() -> None:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    html_actions = set(re.findall(r'data-action="([^"]+)"', html))
    dynamic_project_actions = set(re.findall(r'data-project-action="([^"$]+)"', app_js))

    assert html_actions
    assert html_actions <= set(VISIBLE_BUTTON_WIRING)
    assert dynamic_project_actions <= set(VISIBLE_BUTTON_WIRING)
    for action in html_actions | dynamic_project_actions:
        assert f'"{action}":' in app_js
    assert "document.addEventListener(\"click\"" in app_js
    for required in [
        "settings.edit_provider",
        "settings.clear_api_key",
        "settings.cancel_provider",
        "settings.open_workspace",
        "settings.refresh_status",
    ]:
        assert required in html_actions
        assert required in VISIBLE_BUTTON_WIRING


def test_phase75_version_label_cache_busting_and_button_audit_exist() -> None:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    audit = Path("docs/implementation/PHASE7_5_BUTTON_REALITY_AUDIT.md").read_text(encoding="utf-8")
    checklist = Path("docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "styles.css?v=phase7.5-browser-reality-fix" in html
    assert "app.js?v=phase7.5-browser-reality-fix" in html
    assert "gui-version-label" in html
    assert "backend-version-label" in html
    assert "debug-action-log" in html
    assert "settings.refresh_status" in html
    assert "Phase 7.5 Button Reality Audit" in audit
    assert "browser manual pending" in audit
    assert "Phase 7.5 Browser Smoke Checklist" in checklist


def test_phase75_status_doc_hashes_match_disk() -> None:
    status_doc = Path("docs/implementation/PHASE7_GUI_STATUS.md").read_text(encoding="utf-8")
    smoke_doc = Path("docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md").read_text(encoding="utf-8")
    expected = {
        "app_js.hash": _short_hash(FRONTEND_DIR / "app.js"),
        "backend_service.hash": _short_hash(BACKEND_SERVICE_PATH),
    }

    for label, value in expected.items():
        assert f"{label} = {value}" in status_doc
        assert f"{label} = {value}" in smoke_doc


def test_phase75_release_assets_are_not_git_ignored() -> None:
    paths = [
        "apps/gui/backend/nts_gui_backend/service.py",
        "apps/gui/frontend/app.js",
        "apps/gui/frontend/index.html",
        "tests/test_phase7_gui.py",
        "docs/implementation/PHASE7_GUI_STATUS.md",
        "docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md",
        "docs/gui/PHASE7_GUI_USER_GUIDE.md",
        "docs/goals/PHASE7_5_BROWSER_REALITY_FIX_GOAL.md",
    ]

    result = subprocess.run(["git", "check-ignore", *paths], check=False, capture_output=True, text=True)

    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_dead_legacy_preflight_is_unreachable() -> None:
    service_source = BACKEND_SERVICE_PATH.read_text(encoding="utf-8")

    assert not hasattr(GuiService, "_legacy_http_provider_preflight")
    assert not hasattr(GuiService, "_minimal_chat_preflight")
    assert "_legacy_http_provider_preflight" not in service_source
    assert "_minimal_chat_preflight" not in service_source
    assert "_real_provider_preflight" in service_source


def test_async_button_states_are_supported() -> None:
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "styles.css").read_text(encoding="utf-8")

    for state in ["idle", "running", "success", "warning", "blocked", "error"]:
        assert state in app_js or f'data-state="{state}"' in css
    assert "retryAvailable" in app_js
    assert 'data-retry-available="true"' in css


def test_frontend_contains_real_interaction_handlers() -> None:
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert '"projects.open": () => openProjectFolder()' in app_js
    assert '"projects.technical": () => openProjectDetails()' in app_js
    assert "/api/projects/${encodeURIComponent(project)}/open-folder" in app_js
    assert "function currentChapterRange" in app_js
    assert "function updateRangeMessage" in app_js
    assert "function clearProviderApiKey" in app_js
    assert "function validateWorkspacePath" in app_js
    assert "function startLtp" in app_js
    assert '"settings.check_ltp": () => loadLtpStatus(true)' in app_js
    assert '"home.check_ltp": async () => loadLtpStatus(true)' in app_js
    assert 'api("/api/ltp/status?fresh=1")' in app_js
    assert "function ltpDisplayText" in app_js
    assert "LTP chưa chạy" not in app_js or "data.message" in app_js
    assert "function openJobArtifacts" in app_js
    assert 'api("/api/system/open-path"' in app_js
    assert "function loadGuiVersion" in app_js
    assert "function refreshAllStatus" in app_js
    assert "function renderActionLog" in app_js
    assert "summarizeRequestBody" in app_js


def test_frontend_progress_polling_starts_and_stops_at_terminal_state() -> None:
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "startJobPolling(state.activeJobId)" in app_js
    assert "window.setInterval(() => pollJobStatus(jobId), 3000)" in app_js
    assert '["completed", "blocked", "error", "cancelled"].includes(job.status)' in app_js
    assert "window.clearInterval(state.jobPollTimer)" in app_js
    assert "progress.removeAttribute(\"value\")" in app_js
    assert "progress.value = job.percent" in app_js


def test_manga_tab_is_placeholder_only() -> None:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    manga_section = re.search(r'<section class="page" id="page-manga".*?</section>', html, flags=re.S)

    assert manga_section is not None
    section = manga_section.group(0)
    assert set(re.findall(r'data-action="([^"]+)"', section)) == {"manga.plan", "manga.close"}
    assert "Sắp ra mắt" in section
    assert "upload" not in section.lower()
    assert "production manga" in section


def test_frontend_does_not_expose_forbidden_prompt_options() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in [FRONTEND_DIR / "index.html", FRONTEND_DIR / "app.js"]
    )

    assert "--use-approved-rules" not in combined
    assert "inject_raw_nlp_cache: false" in combined
    assert "sk-" not in combined


def test_unsupported_buttons_show_placeholder_state() -> None:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")

    assert "Xuất EPUB — Sắp hỗ trợ" in html
    assert "Tạm dừng trực tiếp: Sắp hỗ trợ" in html
    assert "showToast(\"Xuất EPUB: Sắp hỗ trợ.\")" in app_js
    assert "showToast(\"Tạm dừng: Sắp hỗ trợ.\")" in app_js
    assert "Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop." in app_js
