from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from nts_core.chinese_nlp import LtpServerAnalyzer
from nts_core.config import load_nlp_config, load_providers
from nts_core.production_rollout import run_controlled_production_rollout, write_provider_preflight
from nts_core.projects import create_project, get_project_by_slug, list_projects
from nts_storage.database import connection, insert_task_run, json_loads, new_id, row_to_dict, utc_now
from nts_storage.workspace import Workspace, WorkspaceError, discover_workspace, init_workspace


SAFE_TRANSLATION_DEFAULTS: dict[str, Any] = {
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

GUI_PROVIDER_CONFIG_RELATIVE_PATH = Path("config") / "gui_provider.local.json"
DEFAULT_GUI_PROVIDER_SETTINGS: dict[str, Any] = {
    "provider_name": "mock",
    "provider_type": "mock",
    "base_url": "mock://local",
    "primary_model": "mock-translation",
    "fallback_model": "",
    "timeout_seconds": 30,
    "max_retries": 2,
}
REDACTED_SECRET = "********"
GUI_PROVIDER_ENV_VAR = "NTS_GUI_PROVIDER_API_KEY"
JOB_REGISTRY: dict[str, dict[str, Any]] = {}
JOB_LOCK = threading.RLock()
PHASE_LABEL = "phase7.5-browser-reality-fix"
SERVER_START_TIME = datetime.now(timezone.utc).isoformat()

VISIBLE_BUTTON_WIRING: dict[str, dict[str, str]] = {
    "home.create_project": {"label": "Tạo dự án truyện mới", "kind": "frontend", "target": "wizard.open"},
    "home.continue_project": {"label": "Tiếp tục dịch truyện", "kind": "api", "target": "GET /api/projects"},
    "home.review_queue": {"label": "Xem bản dịch cần kiểm tra", "kind": "api", "target": "GET /api/review-queue"},
    "home.check_ltp": {"label": "Kiểm tra lại LTP", "kind": "api", "target": "GET /api/ltp/status"},
    "home.open_settings": {"label": "Mở cài đặt API", "kind": "frontend", "target": "settings.open"},
    "projects.open": {"label": "Mở dự án", "kind": "frontend", "target": "project.detail"},
    "projects.translate": {"label": "Dịch tiếp", "kind": "frontend", "target": "translate.with_project"},
    "projects.review": {"label": "Kiểm tra bản dịch", "kind": "frontend", "target": "review.with_project"},
    "projects.export": {"label": "Xuất file", "kind": "frontend", "target": "export.with_project"},
    "projects.technical": {"label": "Xem chi tiết kỹ thuật", "kind": "frontend", "target": "technical.drawer"},
    "wizard.choose_file": {"label": "Chọn file", "kind": "frontend", "target": "file.path_input"},
    "wizard.scan_chapters": {"label": "Quét chương", "kind": "api", "target": "POST /api/projects/{project}/scan-chapters"},
    "wizard.nlp_detect": {"label": "Nhận diện tự động", "kind": "api", "target": "POST /api/projects/{project}/nlp/cache-build"},
    "wizard.next": {"label": "Tiếp tục", "kind": "frontend", "target": "wizard.next_step"},
    "wizard.create": {"label": "Tạo dự án", "kind": "api", "target": "POST /api/projects/import"},
    "translate.trial_1": {"label": "Dịch thử 1 chương", "kind": "api", "target": "POST /api/projects/{project}/translate/trial"},
    "translate.trial_3": {"label": "Dịch thử 3 chương", "kind": "api", "target": "POST /api/projects/{project}/translate/trial"},
    "translate.trial_10": {"label": "Dịch thử 10 chương", "kind": "api", "target": "POST /api/projects/{project}/translate/trial"},
    "translate.batch_20": {"label": "Dịch 20 chương", "kind": "api", "target": "POST /api/projects/{project}/translate/batch"},
    "translate.batch_50": {"label": "Dịch 50 chương", "kind": "api", "target": "POST /api/projects/{project}/translate/batch"},
    "translate.resume": {"label": "Tiếp tục từ chỗ dừng", "kind": "api", "target": "POST /api/projects/{project}/translate/resume"},
    "translate.start": {"label": "Bắt đầu dịch", "kind": "api", "target": "POST /api/projects/{project}/translate/batch"},
    "translate.pause": {"label": "Tạm dừng", "kind": "placeholder", "target": "Sắp hỗ trợ: tạm dừng tiến trình đang chạy"},
    "translate.stop_after_current": {"label": "Dừng sau chương hiện tại", "kind": "placeholder", "target": "Sắp hỗ trợ khi core hỗ trợ graceful stop"},
    "translate.technical": {"label": "Xem chi tiết kỹ thuật", "kind": "frontend", "target": "technical.drawer"},
    "job.details": {"label": "Xem chi tiết", "kind": "api", "target": "GET /api/jobs/{job_id}"},
    "job.open_artifacts": {"label": "Mở thư mục kết quả", "kind": "api", "target": "POST /api/system/open-path"},
    "job.resume": {"label": "Tiếp tục từ chỗ dừng", "kind": "api", "target": "POST /api/projects/{project}/translate/resume"},
    "job.stop_after_current": {"label": "Dừng sau chương hiện tại", "kind": "placeholder", "target": "Sắp hỗ trợ: graceful stop nếu core hỗ trợ"},
    "review.open_item": {"label": "Mở đoạn cần kiểm tra", "kind": "api", "target": "GET /api/review/{item_id}"},
    "review.save": {"label": "Lưu chỉnh sửa", "kind": "api", "target": "POST /api/review/{item_id}/save"},
    "review.learn": {"label": "Lưu & cho hệ thống học", "kind": "api", "target": "POST /api/review/{item_id}/learn"},
    "review.save_only": {"label": "Không học, chỉ lưu bản dịch", "kind": "api", "target": "POST /api/review/{item_id}/save"},
    "review.mark_reviewed": {"label": "Đánh dấu đã kiểm tra", "kind": "api", "target": "POST /api/review/{item_id}/mark-reviewed"},
    "review.skip": {"label": "Bỏ qua", "kind": "frontend", "target": "review.next_item"},
    "review.toggle_source": {"label": "Xem bản gốc", "kind": "frontend", "target": "review.toggle_source"},
    "review.technical": {"label": "Xem chi tiết kỹ thuật", "kind": "frontend", "target": "technical.drawer"},
    "export.txt": {"label": "Xuất TXT", "kind": "api", "target": "POST /api/projects/{project}/export"},
    "export.epub": {"label": "Xuất EPUB", "kind": "placeholder", "target": "Sắp hỗ trợ: EPUB chưa bật trong Phase 7"},
    "export.review_package": {"label": "Xuất gói kiểm tra", "kind": "api", "target": "POST /api/projects/{project}/export"},
    "export.copy_path": {"label": "Sao chép đường dẫn", "kind": "frontend", "target": "clipboard.writeText"},
    "settings.check_api": {"label": "Kiểm tra API", "kind": "api", "target": "POST /api/settings/provider/test"},
    "settings.edit_provider": {"label": "Sửa cấu hình", "kind": "frontend", "target": "provider.form.enable"},
    "settings.save_provider": {"label": "Lưu cấu hình", "kind": "api", "target": "POST /api/settings/provider"},
    "settings.clear_api_key": {"label": "Xóa API key", "kind": "api", "target": "POST /api/settings/provider"},
    "settings.cancel_provider": {"label": "Hủy thay đổi", "kind": "api", "target": "GET /api/settings/provider"},
    "settings.api_help": {"label": "Mở hướng dẫn cấu hình API", "kind": "frontend", "target": "help.modal"},
    "settings.check_ltp": {"label": "Kiểm tra LTP", "kind": "api", "target": "GET /api/ltp/status"},
    "settings.start_ltp": {"label": "Khởi động LTP", "kind": "placeholder", "target": "Sao chép lệnh khởi động từ cấu hình"},
    "settings.open_workspace": {"label": "Mở workspace", "kind": "api", "target": "POST /api/workspace/open-folder"},
    "settings.choose_workspace": {"label": "Chọn workspace", "kind": "frontend", "target": "workspace.path_input"},
    "settings.refresh_status": {"label": "Làm mới trạng thái", "kind": "api", "target": "GET /api/gui/version + status endpoints"},
    "manga.plan": {"label": "Tìm hiểu kế hoạch", "kind": "frontend", "target": "manga.plan_modal"},
    "manga.close": {"label": "Đóng", "kind": "frontend", "target": "manga.close_modal"},
}


class GuiServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class GuiService:
    workspace_path: Path | None = None

    def workspace(self, *, create_if_missing: bool = False) -> Workspace:
        if create_if_missing:
            return init_workspace(self.workspace_path or Path("workspace"))
        try:
            return discover_workspace(self.workspace_path)
        except WorkspaceError as exc:
            raise GuiServiceError("workspace_missing", str(exc), 503) from exc

    def handle(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        method = method.upper()
        clean_path = path.split("?", 1)[0].rstrip("/") or "/"
        parts = [part for part in clean_path.split("/") if part]
        if method == "GET" and clean_path == "/api/health":
            return self.health()
        if method == "GET" and clean_path == "/api/gui/version":
            return self.gui_version()
        if method == "GET" and clean_path == "/api/system/status":
            return self.system_status()
        if method == "GET" and clean_path == "/api/ltp/status":
            return self.ltp_status()
        if method == "POST" and clean_path == "/api/ltp/start":
            return self.ltp_start()
        if method == "GET" and clean_path == "/api/workspace":
            return self.workspace_status()
        if method == "POST" and clean_path == "/api/workspace":
            return self.set_workspace_path(body)
        if method == "POST" and clean_path == "/api/workspace/open-folder":
            return self.open_workspace_folder(body)
        if method == "POST" and clean_path == "/api/system/open-path":
            return self.open_system_path(body)
        if method == "GET" and clean_path == "/api/gui/button-wiring":
            return {"buttons": VISIBLE_BUTTON_WIRING}
        if method == "GET" and clean_path == "/api/settings/provider":
            return self.get_provider_settings()
        if method == "POST" and clean_path == "/api/settings/provider":
            return self.save_provider_settings(body)
        if method == "POST" and clean_path == "/api/settings/provider/test":
            return self.test_provider_settings(body)
        if method == "GET" and clean_path == "/api/review-queue":
            return {"items": self.review_queue(None)}
        if method == "GET" and clean_path == "/api/projects":
            return {"projects": self.projects()}
        if method == "POST" and clean_path == "/api/projects/import":
            return self.import_project(body)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "jobs":
            job_id = parts[2]
            if method == "GET" and len(parts) == 3:
                return self.job_status(job_id)
            if method == "GET" and len(parts) == 4 and parts[3] == "artifacts":
                return {"artifacts": self.job_artifacts(job_id)}
            if method == "POST" and len(parts) == 4 and parts[3] == "cancel":
                return self.cancel_job(job_id)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "projects":
            project = parts[2]
            if method == "GET" and len(parts) == 3:
                return {"project": self.project_summary(project)}
            if method == "GET" and len(parts) == 4 and parts[3] == "paths":
                return self.project_paths(project)
            if method == "POST" and len(parts) == 4 and parts[3] == "open-folder":
                return self.open_project_folder(project, body)
            if method == "POST" and len(parts) == 4 and parts[3] == "scan-chapters":
                return self.record_project_action(project, "gui.scan_chapters", body)
            if method == "POST" and len(parts) == 5 and parts[3] == "nlp" and parts[4] == "cache-build":
                return self.record_project_action(project, "gui.nlp_cache_build", body, raw_nlp_cache=False)
            if method == "POST" and len(parts) == 4 and parts[3] == "validate":
                return self.record_project_action(project, "gui.validate", self.translation_payload(body))
            if method == "POST" and len(parts) == 5 and parts[3] == "translate" and parts[4] == "trial":
                return self.start_translation(project, "trial", body)
            if method == "POST" and len(parts) == 5 and parts[3] == "translate" and parts[4] == "batch":
                return self.start_translation(project, "batch", body)
            if method == "POST" and len(parts) == 5 and parts[3] == "translate" and parts[4] == "resume":
                return self.start_translation(project, "resume", body)
            if method == "GET" and len(parts) == 4 and parts[3] == "runs":
                return {"runs": self.project_runs(project)}
            if method == "GET" and len(parts) == 4 and parts[3] == "review-queue":
                return {"items": self.review_queue(project)}
            if method == "POST" and len(parts) == 4 and parts[3] == "export":
                return self.export_project(project, body)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "runs":
            run_id = parts[2]
            if method == "GET" and len(parts) == 3:
                return {"run": self.run_summary(run_id)}
            if method == "GET" and len(parts) == 4 and parts[3] == "artifacts":
                return {"artifacts": self.artifacts(run_id)}
            if method == "POST" and len(parts) == 4 and parts[3] == "stop-after-current":
                return self.run_control(run_id, "stop_after_current")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "review":
            item_id = parts[2]
            if method == "GET" and len(parts) == 3:
                return {"item": self.review_item(item_id)}
            if method == "POST" and len(parts) == 4 and parts[3] == "save":
                return self.save_review(item_id, body, learn=False)
            if method == "POST" and len(parts) == 4 and parts[3] == "learn":
                return self.save_review(item_id, body, learn=True)
            if method == "POST" and len(parts) == 4 and parts[3] == "mark-reviewed":
                return self.mark_reviewed(item_id)
        raise GuiServiceError("route_not_found", f"No route for {method} {path}", 404)

    def health(self) -> dict[str, Any]:
        try:
            workspace = self.workspace()
            workspace_ready = True
            workspace_path = str(workspace.path)
        except GuiServiceError:
            workspace_ready = False
            workspace_path = str(self.workspace_path or Path("workspace"))
        return {
            "app": "NTS Studio",
            "status": "ok" if workspace_ready else "needs_workspace",
            "workspace_ready": workspace_ready,
            "workspace_path": workspace_path,
            "safe_defaults": SAFE_TRANSLATION_DEFAULTS.copy(),
            "phase_label": PHASE_LABEL,
        }

    def gui_version(self) -> dict[str, Any]:
        frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
        app_js = frontend_dir / "app.js"
        styles_css = frontend_dir / "styles.css"
        backend_file = Path(__file__).resolve()
        return {
            "phase_label": PHASE_LABEL,
            "server_start_time": SERVER_START_TIME,
            "git_commit": self._git_commit(),
            "frontend_asset_version": self._short_file_hash(app_js),
            "app_js": self._file_version(app_js),
            "styles_css": self._file_version(styles_css),
            "backend_service": self._file_version(backend_file),
            "cache_policy": "no-store",
        }

    def provider_config_path(self) -> Path:
        workspace = self.workspace(create_if_missing=True)
        return workspace.path / GUI_PROVIDER_CONFIG_RELATIVE_PATH

    def get_provider_settings(self) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        settings = self._load_provider_settings(workspace)
        return {
            "settings": self._redact_provider_settings(settings),
            "config_path": str(workspace.path / GUI_PROVIDER_CONFIG_RELATIVE_PATH),
            "precedence": "GUI saved config overrides env only for GUI-triggered runs; CLI keeps env/config fallback.",
        }

    def save_provider_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        current = self._load_provider_settings(workspace, include_secret=True)
        api_key = "" if body.get("clear_api_key") else body.get("api_key")
        if not body.get("clear_api_key") and api_key in {None, "", REDACTED_SECRET}:
            api_key = current.get("api_key", "")
        settings = {
            "provider_name": str(body.get("provider_name") or body.get("name") or current.get("provider_name") or "mock").strip(),
            "provider_type": str(body.get("provider_type") or body.get("type") or current.get("provider_type") or "mock").strip(),
            "base_url": str(body.get("base_url") or current.get("base_url") or "").strip(),
            "primary_model": str(body.get("primary_model") or body.get("model") or current.get("primary_model") or "").strip(),
            "fallback_model": str(body.get("fallback_model") or current.get("fallback_model") or "").strip(),
            "timeout_seconds": int(body.get("timeout_seconds") or current.get("timeout_seconds") or 30),
            "max_retries": int(body.get("max_retries") or current.get("max_retries") or 2),
            "api_key": str(api_key or ""),
            "updated_at": utc_now(),
        }
        if not settings["provider_name"]:
            raise GuiServiceError("provider_name_required", "Provider name is required.")
        if not settings["provider_type"]:
            raise GuiServiceError("provider_type_required", "Provider type is required.")
        if not settings["primary_model"]:
            raise GuiServiceError("primary_model_required", "Primary model is required.")
        path = workspace.path / GUI_PROVIDER_CONFIG_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return {
            "settings": self._redact_provider_settings(settings),
            "config_path": str(path),
            "status": "saved",
        }

    def test_provider_settings(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        settings = self._load_provider_settings(workspace, include_secret=True)
        if body:
            preview = self.save_provider_settings(body)["settings"]
            settings = self._load_provider_settings(workspace, include_secret=True)
        else:
            preview = self._redact_provider_settings(settings)
        provider_type = str(settings.get("provider_type") or "mock")
        started = time.perf_counter()
        if provider_type == "mock":
            return {
                "ok": True,
                "provider": settings.get("provider_name"),
                "base_url": settings.get("base_url"),
                "primary_model": settings.get("primary_model"),
                "fallback_model": settings.get("fallback_model"),
                "chosen_model": settings.get("primary_model"),
                "route_status": "mock_pass",
                "latency_ms": 0,
                "message": "Mock provider is ready.",
                "settings": preview,
            }
        if not settings.get("api_key"):
            return {
                "ok": False,
                "provider": settings.get("provider_name"),
                "base_url": settings.get("base_url"),
                "primary_model": settings.get("primary_model"),
                "fallback_model": settings.get("fallback_model"),
                "chosen_model": None,
                "route_status": "api_key_missing",
                "latency_ms": 0,
                "error_summary": "API key không hợp lệ hoặc chưa được nhập.",
                "message": "API key không hợp lệ",
                "settings": preview,
            }
        try:
            chosen_model, route_status = self._real_provider_preflight(settings)
            return {
                "ok": True,
                "provider": settings.get("provider_name"),
                "base_url": settings.get("base_url"),
                "primary_model": settings.get("primary_model"),
                "fallback_model": settings.get("fallback_model"),
                "chosen_model": chosen_model,
                "route_status": route_status,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "message": "Kiểm tra API thành công.",
                "settings": preview,
            }
        except Exception as exc:
            classified = self._classify_provider_preflight_error(exc)
            return {
                "ok": False,
                "provider": settings.get("provider_name"),
                "base_url": settings.get("base_url"),
                "primary_model": settings.get("primary_model"),
                "fallback_model": settings.get("fallback_model"),
                "chosen_model": None,
                "route_status": classified["route_status"],
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error_summary": classified["message"],
                "message": classified["message"],
                "settings": preview,
            }

    def system_status(self) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        providers = []
        gui_provider = self._load_provider_settings(workspace)
        providers.append(
            {
                "key": gui_provider["provider_name"],
                "type": gui_provider["provider_type"],
                "enabled": True,
                "base_url": gui_provider.get("base_url"),
                "primary_model": gui_provider.get("primary_model"),
                "fallback_model": gui_provider.get("fallback_model"),
                "source": "gui_local",
                "api_key_configured": bool(gui_provider.get("api_key_configured")),
                "api_key_value": None,
            }
        )
        try:
            providers_file = load_providers(workspace.config_dir / "providers.yaml")
            for key, provider in providers_file.providers.items():
                env_name = provider.api_key_env
                providers.append(
                    {
                        "key": key,
                        "type": provider.type,
                        "enabled": provider.enabled,
                        "api_key_env": env_name,
                        "api_key_configured": bool(env_name and os.getenv(env_name)),
                        "api_key_value": None,
                        "source": "env_fallback",
                    }
                )
        except Exception as exc:
            providers.append({"key": "config", "status": "warning", "message": str(exc)})
        ltp = self.ltp_status()
        projects = self.projects()
        return {
            "workspace": {"path": str(workspace.path), "ready": True},
            "providers": providers,
            "ltp": ltp,
            "readiness": {
                "status": "ready" if providers else "warning",
                "label": "Hệ thống sẵn sàng" if providers else "Cần kiểm tra cấu hình API",
            },
            "project_count": len(projects),
        }

    def ltp_status(self) -> dict[str, Any]:
        try:
            workspace = self.workspace(create_if_missing=True)
            nlp = load_nlp_config(workspace=workspace)
            start_command = nlp.ltp_server.start_command
            base_url = nlp.ltp_server.base_url.rstrip("/")
            if not nlp.enabled:
                return {
                    "status": "disabled",
                    "healthy": False,
                    "reachable": False,
                    "degraded": True,
                    "enabled": False,
                    "provider": nlp.provider,
                    "url": base_url,
                    "base_url": base_url,
                    "message": "LTP chưa bật trong cấu hình",
                    "start_command": start_command,
                    "start_command_available": bool(start_command),
                }
            if nlp.provider != "ltp_server":
                return {
                    "status": "degraded",
                    "healthy": False,
                    "reachable": False,
                    "degraded": True,
                    "enabled": nlp.enabled,
                    "provider": nlp.provider,
                    "url": base_url,
                    "base_url": base_url,
                    "message": "GUI chỉ kiểm tra trực tiếp LTP server; provider NLP hiện tại không phải ltp_server",
                    "start_command": start_command,
                    "start_command_available": bool(start_command),
                }
            analyzer = LtpServerAnalyzer(
                base_url=base_url,
                request_timeout_seconds=nlp.ltp_server.request_timeout_seconds,
                max_sentences_per_request=nlp.ltp_server.max_sentences_per_request,
            )
            try:
                rows = analyzer.analyze_sentences(["我爱北京天安门。"])
            except HTTPError as exc:
                return self._ltp_status_payload(
                    status="reachable_but_unhealthy",
                    healthy=False,
                    reachable=True,
                    degraded=True,
                    nlp=nlp,
                    base_url=base_url,
                    message="Có tiến trình ở cổng LTP nhưng LTP không trả kết quả phân tích hợp lệ",
                    error=f"HTTP {exc.code}",
                )
            except URLError as exc:
                return self._ltp_status_payload(
                    status="unavailable",
                    healthy=False,
                    reachable=False,
                    degraded=True,
                    nlp=nlp,
                    base_url=base_url,
                    message="LTP chưa chạy",
                    error=str(exc),
                )
            except (OSError, TimeoutError) as exc:
                return self._ltp_status_payload(
                    status="unavailable",
                    healthy=False,
                    reachable=False,
                    degraded=True,
                    nlp=nlp,
                    base_url=base_url,
                    message="LTP chưa chạy",
                    error=str(exc),
                )
            except (ValueError, json.JSONDecodeError) as exc:
                return self._ltp_status_payload(
                    status="reachable_but_unhealthy",
                    healthy=False,
                    reachable=True,
                    degraded=True,
                    nlp=nlp,
                    base_url=base_url,
                    message="Có tiến trình ở cổng LTP nhưng LTP không trả kết quả phân tích hợp lệ",
                    error=str(exc),
                )
            if rows and rows[0].get("words"):
                return self._ltp_status_payload(
                    status="healthy",
                    healthy=True,
                    reachable=True,
                    degraded=False,
                    nlp=nlp,
                    base_url=base_url,
                    message="LTP đang hoạt động",
                )
            return {
                **self._ltp_status_payload(
                    status="reachable_but_unhealthy",
                    healthy=False,
                    reachable=True,
                    degraded=True,
                    nlp=nlp,
                    base_url=base_url,
                    message="Có tiến trình ở cổng LTP nhưng LTP không trả kết quả phân tích hợp lệ",
                    error="empty token result",
                )
            }
        except Exception as exc:
            return {"enabled": False, "status": "error", "healthy": False, "reachable": False, "message": str(exc)}

    def _ltp_status_payload(
        self,
        *,
        status: str,
        healthy: bool,
        reachable: bool,
        degraded: bool,
        nlp: Any,
        base_url: str,
        message: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": status,
            "healthy": healthy,
            "reachable": reachable,
            "degraded": degraded,
            "enabled": nlp.enabled,
            "provider": nlp.provider,
            "auto_start": nlp.auto_start,
            "url": base_url,
            "base_url": base_url,
            "message": message,
            "start_command": nlp.ltp_server.start_command,
            "start_command_available": bool(nlp.ltp_server.start_command),
        }
        if error:
            payload["error"] = self._redact_text(error)
        return payload

    def ltp_start(self) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        nlp = load_nlp_config(workspace=workspace)
        command = nlp.ltp_server.start_command
        return {
            "status": "unsupported",
            "message": "Khởi động LTP tự động chưa bật trong GUI. Sao chép lệnh này và chạy trong PowerShell.",
            "copyable_command": command,
            "working_dir": nlp.ltp_server.working_dir,
            "base_url": nlp.ltp_server.base_url,
        }

    def workspace_status(self) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        return {
            "path": str(workspace.path),
            "exists": workspace.path.exists(),
            "db_exists": workspace.db_path.exists(),
            "selection_mode": "backend-start --workspace or NTS_WORKSPACE; GUI validates alternate paths.",
        }

    def set_workspace_path(self, body: dict[str, Any]) -> dict[str, Any]:
        requested = Path(str(body.get("workspace_path") or "")).expanduser()
        if not str(requested).strip():
            raise GuiServiceError("workspace_path_required", "Workspace path is required.")
        workspace = init_workspace(requested)
        return {
            "status": "validated",
            "path": str(workspace.path),
            "active_path": str(self.workspace(create_if_missing=True).path),
            "message": "Workspace path is valid. Restart backend with --workspace to make it active in this browser session.",
        }

    def open_workspace_folder(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        target = self._preferred_workspace_output_path(workspace)
        result = self._open_or_return_path(target, bool((body or {}).get("open")))
        return {**result, "workspace_path": str(workspace.path), "target": "output_root"}

    def open_system_path(self, body: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        requested = Path(str(body.get("path") or "")).resolve()
        if not self._is_safe_workspace_path(workspace, requested):
            raise GuiServiceError("unsafe_path", "Path must be inside the active workspace/artifact/output directories.", 403)
        return self._open_or_return_path(requested, bool(body.get("open", True)))

    def projects(self) -> list[dict[str, Any]]:
        workspace = self.workspace()
        return [self._augment_project(workspace, project) for project in list_projects(workspace)]

    def project_summary(self, project_slug: str) -> dict[str, Any]:
        workspace = self.workspace()
        return self._augment_project(workspace, get_project_by_slug(workspace, project_slug))

    def project_paths(self, project_slug: str) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        project = get_project_by_slug(workspace, project_slug)
        project_artifacts = workspace.path / "artifacts" / "projects" / project_slug
        output_dir = workspace.path / "artifacts" / "exports" / project_slug
        txt_chapter_dir = self._project_txt_chapter_output_dir(workspace, project_slug)
        gui_jobs_dir = workspace.path / "artifacts" / "gui_jobs"
        latest_job_output = self._latest_project_job_output(workspace, project_slug)
        review_dir = workspace.path / "reviews" / project_slug
        for path in (project_artifacts, output_dir, txt_chapter_dir, review_dir):
            path.mkdir(parents=True, exist_ok=True)
        return {
            "project": project_slug,
            "project_id": project["id"],
            "workspace_path": str(workspace.path),
            "project_path": str(project_artifacts),
            "artifact_path": str(project_artifacts),
            "output_path": str(output_dir),
            "txt_output_path": str(txt_chapter_dir),
            "txt_chapter_output_path": str(txt_chapter_dir),
            "output_root_path": str(self._preferred_workspace_output_path(workspace)),
            "latest_job_output_path": str(latest_job_output) if latest_job_output else None,
            "preferred_output_path": str(txt_chapter_dir),
            "gui_jobs_path": str(gui_jobs_dir),
            "review_path": str(review_dir),
        }

    def open_project_folder(self, project_slug: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        paths = self.project_paths(project_slug)
        target_key = str((body or {}).get("target") or "preferred_output_path")
        allowed_targets = {"preferred_output_path", "txt_output_path", "txt_chapter_output_path", "output_path", "latest_job_output_path", "review_path"}
        if target_key not in allowed_targets or not paths.get(target_key):
            raise GuiServiceError("unsafe_target", "Project open target is not an approved output/review folder.", 403)
        target = Path(paths[target_key])
        if not self._is_safe_workspace_path(workspace, target):
            raise GuiServiceError("unsafe_path", "Project path must be inside the active workspace/artifact/output directories.", 403)
        result = self._open_or_return_path(target, bool((body or {}).get("open", True)))
        return {**paths, **result, "target": target_key}

    def import_project(self, body: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=bool(body.get("create_workspace")))
        slug = str(body.get("slug") or "").strip()
        name = str(body.get("name") or slug).strip()
        if not slug:
            raise GuiServiceError("project_slug_required", "Project slug is required.")
        project = create_project(
            workspace,
            slug=slug,
            name=name,
            source_lang=str(body.get("source_lang") or "zh"),
            target_lang=str(body.get("target_lang") or "vi"),
            domain="novel",
            genre=body.get("genre"),
        )
        return {"project": self._augment_project(workspace, project), "status": "created"}

    def translation_payload(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        workspace = self.workspace()
        payload = SAFE_TRANSLATION_DEFAULTS.copy()
        chapter_start = int(body.get("chapter_start") or 1)
        chapter_end = int(body.get("chapter_end") or body.get("chapter_count") or chapter_start)
        if chapter_start <= 0 or chapter_end <= 0 or chapter_end < chapter_start:
            raise GuiServiceError("invalid_chapter_range", "Chapter range must be positive and end must be >= start.")
        if "chapter_count" in body:
            payload["chapter_count"] = int(body["chapter_count"])
        if "preset" in body:
            payload["preset"] = str(body["preset"])
        payload["chapter_start"] = chapter_start
        payload["chapter_end"] = chapter_end
        payload["chapter_range"] = f"{chapter_start}-{chapter_end}"
        payload["chapter_count"] = chapter_end - chapter_start + 1
        payload["mode"] = str(body.get("mode") or body.get("preset") or "batch")
        payload["resumable"] = bool(body.get("resumable", payload["resumable"]))
        payload["provider"] = self._provider_runtime_summary(workspace)
        payload["use_approved_rules"] = False
        payload["inject_raw_nlp_cache"] = False
        return payload

    def start_translation(self, project_slug: str, mode: str, body: dict[str, Any]) -> dict[str, Any]:
        if mode == "resume":
            payload = self.translation_payload({**body, "chapter_start": body.get("chapter_start") or 1, "chapter_end": body.get("chapter_end") or 1, "preset": mode, "mode": "resume", "resumable": True})
        else:
            chapter_count = int(body.get("chapter_count") or {"trial": 1, "batch": 20}.get(mode, 1))
            chapter_start = int(body.get("chapter_start") or 1)
            chapter_end = int(body.get("chapter_end") or (chapter_start + chapter_count - 1))
            payload = self.translation_payload({**body, "chapter_start": chapter_start, "chapter_end": chapter_end, "preset": mode, "mode": mode})
        return self.start_translation_job(project_slug, mode, payload)

    def start_translation_job(self, project_slug: str, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspace(create_if_missing=True)
        project = get_project_by_slug(workspace, project_slug)
        provider = self._provider_runtime_summary(workspace)
        payload["provider"] = provider
        if provider.get("provider_type") != "mock" and not provider.get("api_key_configured"):
            raise GuiServiceError("provider_not_ready", "Chưa kiểm tra được API. Hãy vào Cài đặt và bấm Kiểm tra API.")
        self._write_gui_provider_to_workspace(workspace)
        job_id = new_id("job")
        artifact_path = workspace.path / "artifacts" / "gui_jobs" / job_id
        artifact_path.mkdir(parents=True, exist_ok=True)
        job = {
            "job_id": job_id,
            "project": project_slug,
            "project_name": project.get("name"),
            "status": "queued",
            "stage": "queued",
            "chapter_start": payload["chapter_start"],
            "chapter_end": payload["chapter_end"],
            "current_chapter": None,
            "current_chunk": None,
            "chapters_completed": 0,
            "chapters_total": payload["chapter_count"],
            "chunks_completed": 0,
            "chunks_total": None,
            "percent": 0,
            "elapsed_seconds": 0,
            "eta_seconds": None,
            "latest_message": "Đã xếp hàng tác vụ dịch an toàn.",
            "warnings": [],
            "artifact_path": str(artifact_path),
            "payload": payload,
            "created_at_monotonic": time.monotonic(),
            "summary": None,
            "error": None,
        }
        with JOB_LOCK:
            JOB_REGISTRY[job_id] = job
        thread = threading.Thread(target=self._run_translation_job, args=(job_id,), daemon=True)
        thread.start()
        return {
            "job_id": job_id,
            "run_id": job_id,
            "status": "queued",
            "message": "Đã bắt đầu tác vụ dịch an toàn",
            "artifact_path": str(artifact_path),
            "payload": self._public_payload(payload),
        }

    def job_status(self, job_id: str) -> dict[str, Any]:
        with JOB_LOCK:
            job = JOB_REGISTRY.get(job_id)
            if job is None:
                raise GuiServiceError("job_not_found", f"Job not found: {job_id}", 404)
            self._refresh_job_progress_from_artifacts(job)
            public = self._public_job(job)
        return public

    def job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job = self.job_status(job_id)
        artifact_path = Path(str(job.get("artifact_path") or ""))
        if not artifact_path.exists():
            return []
        items = []
        for path in artifact_path.rglob("*"):
            if path.is_file():
                items.append({"path": str(path), "size_bytes": path.stat().st_size})
        return items[:50]

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with JOB_LOCK:
            job = JOB_REGISTRY.get(job_id)
            if job is None:
                raise GuiServiceError("job_not_found", f"Job not found: {job_id}", 404)
            if job["status"] in {"completed", "blocked", "error", "cancelled"}:
                return self._public_job(job)
            job["status"] = "cancelled"
            job["stage"] = "cancelled"
            job["latest_message"] = "Đã yêu cầu hủy. Tác vụ lõi có thể kết thúc sau chương hiện tại nếu hỗ trợ."
            return self._public_job(job)

    def record_project_action(self, project_slug: str, task_type: str, payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
        workspace = self.workspace()
        project = get_project_by_slug(workspace, project_slug)
        input_data = {**payload, **extra}
        input_data["use_approved_rules"] = False
        input_data["inject_raw_nlp_cache"] = False
        with connection(workspace.db_path) as conn:
            task_id = insert_task_run(
                conn,
                task_type=task_type,
                status="success",
                stage="gui_recorded",
                project_id=project["id"],
                input_data=input_data,
                result_data={"message": "Recorded by NTS Studio GUI wrapper."},
            )
            conn.commit()
        return {"run_id": task_id, "status": "recorded", "payload": input_data}

    def project_runs(self, project_slug: str) -> list[dict[str, Any]]:
        workspace = self.workspace()
        project = get_project_by_slug(workspace, project_slug)
        with connection(workspace.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, task_type, status, stage, input_json, result_json, created_at, finished_at
                FROM task_runs
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (project["id"],),
            ).fetchall()
        return [row_to_dict(row, json_fields=("input_json", "result_json")) for row in rows]

    def run_summary(self, run_id: str) -> dict[str, Any]:
        workspace = self.workspace()
        with connection(workspace.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, task_type, project_id, status, stage, input_json, result_json, error_json,
                       created_at, finished_at
                FROM task_runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise GuiServiceError("run_not_found", f"Run not found: {run_id}", 404)
        return row_to_dict(row, json_fields=("input_json", "result_json", "error_json"))

    def artifacts(self, run_id: str, *, limit: int = 25, preview_chars: int = 240) -> list[dict[str, Any]]:
        workspace = self.workspace()
        roots = [workspace.path / "artifacts", workspace.path / "reviews"]
        items: list[dict[str, Any]] = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                name = path.name.lower()
                if run_id not in str(path) and not name.endswith((".json", ".md", ".txt", ".csv")):
                    continue
                preview = ""
                if path.suffix.lower() in {".json", ".md", ".txt", ".csv"}:
                    preview = path.read_text(encoding="utf-8", errors="ignore")[:preview_chars]
                items.append(
                    {
                        "path": str(path.relative_to(workspace.path)),
                        "size_bytes": path.stat().st_size,
                        "preview": preview,
                        "preview_truncated": path.stat().st_size > len(preview.encode("utf-8")),
                    }
                )
                if len(items) >= limit:
                    return items
        return items

    def review_queue(self, project_slug: str | None = None) -> list[dict[str, Any]]:
        workspace = self.workspace()
        with connection(workspace.db_path) as conn:
            if project_slug:
                project = get_project_by_slug(workspace, project_slug)
                rows = conn.execute(
                    """
                    SELECT t.id, c.project_id, c.id AS chapter_id, c.title, t.text, t.status, t.created_at
                    FROM translations t
                    JOIN chapters c ON c.id = t.chapter_id
                    WHERE t.is_current = 1 AND t.status != 'reviewed' AND c.project_id = ?
                    ORDER BY t.created_at ASC
                    LIMIT 50
                    """,
                    (project["id"],),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT t.id, c.project_id, c.id AS chapter_id, c.title, t.text, t.status, t.created_at
                    FROM translations t
                    JOIN chapters c ON c.id = t.chapter_id
                    WHERE t.is_current = 1 AND t.status != 'reviewed'
                    ORDER BY t.created_at ASC
                    LIMIT 50
                    """
                ).fetchall()
        return [row_to_dict(row) for row in rows]

    def review_item(self, item_id: str) -> dict[str, Any]:
        workspace = self.workspace()
        with connection(workspace.db_path) as conn:
            row = conn.execute(
                """
                SELECT t.id, t.text, t.status, t.quality_json, c.title, c.project_id, s.source_text
                FROM translations t
                LEFT JOIN chapters c ON c.id = t.chapter_id
                LEFT JOIN segments s ON s.id = t.segment_id
                WHERE t.id = ?
                """,
                (item_id,),
            ).fetchone()
        if row is None:
            raise GuiServiceError("review_item_not_found", f"Review item not found: {item_id}", 404)
        data = row_to_dict(row)
        data["quality"] = json_loads(data.pop("quality_json")) if data.get("quality_json") else {}
        return data

    def save_review(self, item_id: str, body: dict[str, Any], *, learn: bool) -> dict[str, Any]:
        workspace = self.workspace()
        reviewed_text = str(body.get("reviewed_text") or body.get("text") or "").strip()
        if not reviewed_text:
            raise GuiServiceError("review_text_required", "Reviewed text is required.")
        with connection(workspace.db_path) as conn:
            row = conn.execute("SELECT id, chapter_id, segment_id, text FROM translations WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise GuiServiceError("review_item_not_found", f"Review item not found: {item_id}", 404)
            conn.execute(
                "UPDATE translations SET text = ?, status = ? WHERE id = ?",
                (reviewed_text, "reviewed" if not learn else "reviewed_learn_candidate", item_id),
            )
            task_id = insert_task_run(
                conn,
                task_type="gui.review.learn" if learn else "gui.review.save",
                status="success",
                stage="scoped_candidate_created" if learn else "saved_only",
                input_data={"translation_id": item_id, "learn": learn},
                result_data={"mutated_global_memory": False},
            )
            conn.commit()
        artifact_path = None
        if learn:
            artifact_path = self._write_learning_candidate(workspace, item_id, reviewed_text, task_id)
        return {
            "translation_id": item_id,
            "status": "learn_candidate_created" if learn else "saved_only",
            "learn": learn,
            "mutated_global_memory": False,
            "candidate_artifact": artifact_path,
            "task_run_id": task_id,
        }

    def mark_reviewed(self, item_id: str) -> dict[str, Any]:
        workspace = self.workspace()
        with connection(workspace.db_path) as conn:
            conn.execute("UPDATE translations SET status = ? WHERE id = ?", ("reviewed", item_id))
            conn.commit()
        return {"translation_id": item_id, "status": "reviewed"}

    def export_project(self, project_slug: str, body: dict[str, Any]) -> dict[str, Any]:
        fmt = str(body.get("format") or "txt").lower()
        if fmt == "epub":
            return {"format": "epub", "status": "unsupported", "label": "Sắp hỗ trợ"}
        if fmt == "txt":
            workspace = self.workspace(create_if_missing=True)
            exported = self._write_project_txt_chapter_exports(workspace, project_slug)
            return {
                "format": "txt",
                "status": "exported",
                "label": "TXT chapters",
                "artifact_path": exported["txt_output_path"],
                "txt_output_path": exported["txt_output_path"],
                "files": exported["files"],
                "file_count": exported["file_count"],
                "message": "Đã xuất TXT từng chương vào một thư mục riêng.",
            }
        if fmt not in {"txt", "review_package"}:
            raise GuiServiceError("export_format_unsupported", f"Unsupported export format: {fmt}")
        return self.record_project_action(project_slug, "gui.export", {"format": fmt})

    def run_control(self, run_id: str, action: str) -> dict[str, Any]:
        return {"run_id": run_id, "action": action, "status": "requested"}

    def _augment_project(self, workspace: Workspace, project: dict[str, Any]) -> dict[str, Any]:
        with connection(workspace.db_path) as conn:
            counts = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM chapters WHERE project_id = ?) AS chapters,
                  (SELECT COUNT(*) FROM translations t JOIN chapters c ON c.id = t.chapter_id
                   WHERE c.project_id = ? AND t.is_current = 1) AS translated
                """,
                (project["id"], project["id"]),
            ).fetchone()
        chapter_count = int(counts["chapters"] or 0)
        translated_count = int(counts["translated"] or 0)
        progress = 0 if chapter_count == 0 else round((translated_count / chapter_count) * 100)
        return {
            **project,
            "chapter_count": chapter_count,
            "translated_count": translated_count,
            "progress_percent": progress,
            "next_action": "Dịch thử" if translated_count == 0 else "Kiểm tra bản dịch",
        }

    def _load_provider_settings(
        self, workspace: Workspace, *, include_secret: bool = False
    ) -> dict[str, Any]:
        path = workspace.path / GUI_PROVIDER_CONFIG_RELATIVE_PATH
        settings = DEFAULT_GUI_PROVIDER_SETTINGS.copy()
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise GuiServiceError("provider_config_invalid", "GUI provider config must be a JSON object.")
            settings.update(loaded)
        api_key = str(settings.get("api_key") or "")
        settings["api_key_configured"] = bool(api_key)
        if not include_secret:
            settings.pop("api_key", None)
        return settings

    def _redact_provider_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        redacted = {key: value for key, value in settings.items() if key != "api_key"}
        redacted["api_key_configured"] = bool(settings.get("api_key") or settings.get("api_key_configured"))
        redacted["api_key"] = REDACTED_SECRET if redacted["api_key_configured"] else ""
        return redacted

    def _file_version(self, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {"path": str(path), "exists": False, "mtime": None, "hash": None}
        return {
            "path": str(path),
            "exists": True,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "hash": self._short_file_hash(path),
        }

    def _short_file_hash(self, path: Path) -> str | None:
        try:
            return sha256(path.read_bytes()).hexdigest()[:12]
        except FileNotFoundError:
            return None

    def _git_commit(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parents[4],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
        except Exception:
            return None
        return result.stdout.strip() or None

    def _provider_runtime_summary(self, workspace: Workspace) -> dict[str, Any]:
        settings = self._load_provider_settings(workspace, include_secret=True)
        return {
            "source": "gui_local",
            "provider_name": settings.get("provider_name"),
            "provider_type": settings.get("provider_type"),
            "base_url": settings.get("base_url"),
            "primary_model": settings.get("primary_model"),
            "fallback_model": settings.get("fallback_model"),
            "timeout_seconds": settings.get("timeout_seconds"),
            "max_retries": settings.get("max_retries"),
            "api_key_configured": bool(settings.get("api_key")),
            "api_key_value": None,
            "precedence": "GUI saved config overrides env for GUI-triggered runs.",
        }

    def _real_provider_preflight(self, settings: dict[str, Any]) -> tuple[str, str]:
        workspace = self.workspace(create_if_missing=True)
        normalized_base_url = self._normalize_provider_base_url(str(settings.get("base_url") or ""))
        if not normalized_base_url.startswith(("http://", "https://")):
            raise ValueError("Không kết nối được base URL")
        if settings.get("base_url") != normalized_base_url:
            saved = dict(settings)
            saved["base_url"] = normalized_base_url
            self.save_provider_settings(saved)
            settings = self._load_provider_settings(workspace, include_secret=True)
        provider_settings = self._write_gui_provider_to_workspace(workspace)
        run_dir = workspace.path / "artifacts" / "gui_provider_preflight" / new_id("preflight")
        run_dir.mkdir(parents=True, exist_ok=True)
        report = write_provider_preflight(
            workspace,
            run_dir=run_dir,
            provider_key=str(provider_settings.get("provider_name") or settings.get("provider_name") or "mock"),
            primary_model=str(settings.get("primary_model") or ""),
            fallback_model=str(settings.get("fallback_model") or "") or None,
        )
        if report.get("pass") and report.get("chosen_model"):
            route_status = "fallback_selected" if report.get("fallback_model_used") else "primary_ok"
            return str(report["chosen_model"]), route_status
        primary_status = report.get("primary_status") or {}
        fallback_status = report.get("fallback_status") or {}
        failure = fallback_status if settings.get("fallback_model") and not fallback_status.get("ok") else primary_status
        reason = str(failure.get("blocker_reason") or failure.get("status") or report.get("blocker_reason") or "provider_test_failed")
        if "auth" in reason or "401" in reason or "403" in reason:
            raise ValueError("API key không hợp lệ")
        if "not_found" in reason or "404" in reason:
            raise ValueError("Model không tồn tại")
        if settings.get("fallback_model") and not fallback_status.get("ok"):
            raise ValueError("Fallback model cũng không dùng được")
        raise ValueError(reason)

    def _normalize_provider_base_url(self, base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if normalized.endswith("/chat/completions"):
            normalized = normalized[: -len("/chat/completions")].rstrip("/")
        if normalized.endswith("/models"):
            normalized = normalized[: -len("/models")].rstrip("/")
        while normalized.endswith("/v1/v1"):
            normalized = normalized[: -len("/v1")]
        return normalized

    def _classify_provider_preflight_error(self, exc: Exception) -> dict[str, str]:
        if isinstance(exc, HTTPError):
            if exc.code in {401, 403}:
                return {"route_status": "auth_failed", "message": "API key không hợp lệ"}
            if exc.code == 404:
                return {"route_status": "model_or_route_not_found", "message": "Model không tồn tại"}
            return {"route_status": f"http_{exc.code}", "message": f"Không kết nối được base URL: HTTP {exc.code}"}
        if isinstance(exc, URLError):
            return {"route_status": "base_url_unreachable", "message": "Không kết nối được base URL"}
        message = str(exc)
        lowered = message.lower()
        if "API key" in message or "401" in message or "403" in message or "auth" in lowered:
            return {"route_status": "auth_failed", "message": "API key không hợp lệ"}
        if "Model không tồn tại" in message:
            return {"route_status": "model_not_found", "message": "Model không tồn tại"}
        if "Fallback model" in message:
            return {"route_status": "fallback_model_failed", "message": "Fallback model cũng không dùng được"}
        if "base URL" in message:
            return {"route_status": "base_url_invalid", "message": "Không kết nối được base URL"}
        return {"route_status": "provider_test_failed", "message": message or "Fallback model cũng không dùng được"}

    def _write_gui_provider_to_workspace(self, workspace: Workspace) -> dict[str, Any]:
        settings = self._load_provider_settings(workspace, include_secret=True)
        provider_name = str(settings.get("provider_name") or "mock")
        if provider_name == "mock" or settings.get("provider_type") == "mock":
            return settings
        path = workspace.config_dir / "providers.yaml"
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(data, dict):
                data = {}
            providers = data.setdefault("providers", {})
            providers[provider_name] = {
                "type": "openai_chat_compatible",
                "base_url": str(settings.get("base_url") or "").rstrip("/"),
                "api_key_env": GUI_PROVIDER_ENV_VAR,
                "models": [value for value in [settings.get("primary_model"), settings.get("fallback_model")] if value],
                "route": "chat/completions",
            }
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            raise GuiServiceError("provider_workspace_write_failed", f"Cannot write GUI provider config: {exc}") from exc
        if settings.get("api_key"):
            os.environ[GUI_PROVIDER_ENV_VAR] = str(settings["api_key"])
        return settings

    def _run_translation_job(self, job_id: str) -> None:
        with JOB_LOCK:
            job = JOB_REGISTRY[job_id]
            job["status"] = "running"
            job["stage"] = "production_rollout"
            job["latest_message"] = "Đang chạy Phase 6 production rollout an toàn..."
        try:
            workspace = self.workspace(create_if_missing=True)
            payload = job["payload"]
            provider = payload["provider"]
            settings = self._write_gui_provider_to_workspace(workspace)
            chapters = payload["chapter_range"]
            output_dir = Path(job["artifact_path"]) / "production_rollout"
            summary = run_controlled_production_rollout(
                workspace,
                project_slug=job["project"],
                provider_key=str(provider.get("provider_name") or settings.get("provider_name") or "mock"),
                model=str(provider.get("primary_model") or settings.get("primary_model") or "mock-production"),
                fallback_model=str(provider.get("fallback_model") or settings.get("fallback_model") or "") or None,
                chapters=chapters,
                max_chapters=int(payload["chapter_count"]),
                dictionary_max_entries=8,
                memory_max_items=6,
                support_max_chars=1200,
                emit_prompt_artifacts=True,
                resumable=bool(payload.get("resumable", True)),
                dry_run=False,
                canary=payload.get("mode") == "trial",
                output_dir=output_dir,
            )
            txt_export = self._write_project_txt_chapter_exports(
                workspace,
                job["project"],
                source_run_dir=Path(str(summary.get("run_dir") or output_dir)),
            )
            status = "completed" if summary.get("final_decision") == "PASS" else "blocked" if summary.get("final_decision") == "BLOCKED" else "error"
            chapters_done = int(summary.get("chapters_processed") or 0) + int(summary.get("chapters_skipped") or 0)
            with JOB_LOCK:
                job.update(
                    {
                        "status": status,
                        "stage": "completed" if status == "completed" else "qa_blocked",
                        "chapters_completed": chapters_done,
                        "chunks_completed": int(summary.get("chunks_processed") or 0),
                        "chunks_total": int(summary.get("chunks_processed") or 0) or None,
                        "percent": 100 if status == "completed" else min(99, int(chapters_done / max(1, job["chapters_total"]) * 100)),
                        "latest_message": f"Production rollout {summary.get('final_decision')}: {summary.get('run_id')}",
                        "warnings": summary.get("warnings") or [],
                        "artifact_path": str(summary.get("run_dir") or output_dir),
                        "txt_output_path": txt_export["txt_output_path"],
                        "txt_files": txt_export["files"],
                        "summary": summary,
                    }
                )
        except Exception as exc:
            redacted_error = self._redact_text(str(exc))
            with JOB_LOCK:
                job.update({"status": "error", "stage": "error", "percent": 0, "latest_message": redacted_error, "error": redacted_error})

    def _refresh_job_progress_from_artifacts(self, job: dict[str, Any]) -> None:
        if job.get("status") in {"completed", "blocked", "error", "cancelled"}:
            return
        artifact_root = Path(str(job.get("artifact_path") or ""))
        rollout_dir = artifact_root / "production_rollout"
        if not rollout_dir.exists() and artifact_root.name == "production_rollout":
            rollout_dir = artifact_root
        if not rollout_dir.exists():
            job["latest_message"] = "Đang chuẩn bị..."
            job["percent"] = 0
            return
        chunk_plan = self._read_json_if_exists(rollout_dir / "chunk_plan.json")
        if chunk_plan:
            chapters = chunk_plan.get("chapters") or []
            if chapters and not job.get("chunks_total"):
                chunk_total = 0
                for chapter in chapters:
                    chunks = chapter.get("chunks") or chapter.get("chunk_plan") or []
                    chunk_total += len(chunks) if isinstance(chunks, list) and chunks else int(chapter.get("chunk_count") or 0)
                job["chunks_total"] = chunk_total or None
                job["latest_message"] = "Đã tạo kế hoạch chunk, đang chạy rollout..."
        summary = self._read_json_if_exists(rollout_dir / "production_rollout_summary.json")
        if summary:
            txt_export = self._write_project_txt_chapter_exports(
                self.workspace(create_if_missing=True),
                str(job.get("project") or ""),
                source_run_dir=Path(str(summary.get("run_dir") or rollout_dir)),
            )
            decision = str(summary.get("final_decision") or "")
            status = "completed" if decision == "PASS" else "blocked" if decision == "BLOCKED" else "error"
            chapters_done = int(summary.get("chapters_processed") or 0) + int(summary.get("chapters_skipped") or 0)
            job.update(
                {
                    "status": status,
                    "stage": "completed" if status == "completed" else "qa_blocked",
                    "chapters_completed": chapters_done,
                    "chunks_completed": int(summary.get("chunks_processed") or 0),
                    "chunks_total": int(summary.get("chunks_processed") or 0) or job.get("chunks_total"),
                    "percent": 100 if status == "completed" else min(99, int(chapters_done / max(1, job["chapters_total"]) * 100)),
                    "latest_message": f"Production rollout {decision}: {summary.get('run_id')}",
                    "warnings": summary.get("warnings") or [],
                    "artifact_path": str(summary.get("run_dir") or rollout_dir),
                    "txt_output_path": txt_export["txt_output_path"],
                    "txt_files": txt_export["files"],
                    "summary": summary,
                }
            )
            return
        batch_dir = self._discover_batch_dir(rollout_dir)
        chapter_results = self._read_json_if_exists(batch_dir / "chapter_results.json") if batch_dir else {}
        rows = chapter_results.get("chapters") if isinstance(chapter_results, dict) else []
        if rows:
            completed_rows = [row for row in rows if str(row.get("status") or "") in {"success", "failed", "skipped", "skipped_existing", "skipped_completed"}]
            success_rows = [row for row in rows if str(row.get("status") or "") == "success"]
            chunks_completed = 0
            chunks_total = 0
            current_chapter = None
            for row in rows:
                row_chunks_total = int(row.get("chunks_total") or row.get("chunk_count") or len(row.get("chunks") or []))
                row_chunks_done = int(row.get("chunks_completed") or row.get("chunks_processed") or (row_chunks_total if row in completed_rows else 0))
                chunks_total += row_chunks_total
                chunks_completed += row_chunks_done
                if current_chapter is None and str(row.get("status") or "") not in {"success", "failed", "skipped", "skipped_existing", "skipped_completed"}:
                    current_chapter = row.get("chapter_no") or row.get("chapter")
            total = int(job.get("chapters_total") or len(rows) or 1)
            percent = int(len(completed_rows) / max(1, total) * 100)
            job.update(
                {
                    "stage": "translation",
                    "current_chapter": current_chapter,
                    "chapters_completed": len(success_rows),
                    "chunks_completed": chunks_completed,
                    "chunks_total": chunks_total or job.get("chunks_total"),
                    "percent": min(99, percent),
                    "latest_message": f"Đang dịch: {len(completed_rows)} / {total} chương có kết quả.",
                }
            )

    def _read_json_if_exists(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _discover_batch_dir(self, rollout_dir: Path) -> Path | None:
        qa = self._read_json_if_exists(rollout_dir / "production_qa_report.json")
        if qa.get("batch_dir") and Path(str(qa["batch_dir"])).exists():
            return Path(str(qa["batch_dir"]))
        for candidate in rollout_dir.rglob("chapter_results.json"):
            return candidate.parent
        return None

    def _project_txt_chapter_output_dir(self, workspace: Workspace, project_slug: str) -> Path:
        return workspace.path / "artifacts" / "exports" / project_slug / "txt_chapters"

    def _write_project_txt_chapter_exports(
        self,
        workspace: Workspace,
        project_slug: str,
        *,
        source_run_dir: Path | None = None,
    ) -> dict[str, Any]:
        output_dir = self._project_txt_chapter_output_dir(workspace, project_slug)
        output_dir.mkdir(parents=True, exist_ok=True)
        copied_files = self._copy_txt_chapters_from_rollout(output_dir, source_run_dir or self._latest_project_job_output(workspace, project_slug))
        if not copied_files:
            copied_files = self._write_txt_chapters_from_current_translations(workspace, project_slug, output_dir)
        return {
            "txt_output_path": str(output_dir),
            "files": [str(path) for path in copied_files],
            "file_count": len(copied_files),
        }

    def _copy_txt_chapters_from_rollout(self, output_dir: Path, rollout_dir: Path | None) -> list[Path]:
        if not rollout_dir or not rollout_dir.exists():
            return []
        resolved_rollout = rollout_dir / "production_rollout" if (rollout_dir / "production_rollout").exists() else rollout_dir
        batch_dir = self._discover_batch_dir(resolved_rollout)
        if not batch_dir:
            return []
        chapter_results = self._read_json_if_exists(batch_dir / "chapter_results.json")
        rows = chapter_results.get("chapters") if isinstance(chapter_results, dict) else []
        written: list[Path] = []
        for index, row in enumerate(rows or [], start=1):
            if not isinstance(row, dict):
                continue
            source = Path(str(row.get("output_path") or ""))
            if not source.is_file() or source.name == "full_novel.vi.txt":
                continue
            chapter_no = int(row.get("chapter_no") or row.get("chapter") or index)
            target = output_dir / f"chapter_{chapter_no:03d}.vi.txt"
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(target)
        return written

    def _write_txt_chapters_from_current_translations(self, workspace: Workspace, project_slug: str, output_dir: Path) -> list[Path]:
        try:
            project = get_project_by_slug(workspace, project_slug)
        except Exception:
            return []
        with connection(workspace.db_path) as conn:
            rows = conn.execute(
                """
                SELECT c.chapter_no, c.title, s.segment_no, t.text
                FROM chapters c
                JOIN segments s ON s.chapter_id = c.id
                JOIN translations t ON t.segment_id = s.id AND t.chapter_id = c.id
                WHERE c.project_id = ? AND t.is_current = 1
                ORDER BY c.chapter_no, s.segment_no
                """,
                (project["id"],),
            ).fetchall()
        chapters: dict[int, dict[str, Any]] = {}
        for row in rows:
            chapter_no = int(row["chapter_no"] or 0)
            chapter = chapters.setdefault(chapter_no, {"title": row["title"], "segments": []})
            chapter["segments"].append(str(row["text"] or ""))
        written: list[Path] = []
        for chapter_no, chapter in sorted(chapters.items()):
            if chapter_no <= 0:
                continue
            title = str(chapter.get("title") or f"Chương {chapter_no}")
            target = output_dir / f"chapter_{chapter_no:03d}.vi.txt"
            body = "\n\n".join(segment for segment in chapter["segments"] if segment.strip())
            target.write_text(f"{title}\n\n{body}".strip() + "\n", encoding="utf-8")
            written.append(target)
        return written

    def _preferred_workspace_output_path(self, workspace: Workspace) -> Path:
        output_root = workspace.path / "artifacts" / "exports"
        output_root.mkdir(parents=True, exist_ok=True)
        return output_root

    def _latest_project_job_output(self, workspace: Workspace, project_slug: str) -> Path | None:
        jobs_root = workspace.path / "artifacts" / "gui_jobs"
        if not jobs_root.exists():
            return None
        candidates: list[Path] = []
        for summary_path in jobs_root.rglob("production_rollout_summary.json"):
            summary = self._read_json_if_exists(summary_path)
            if summary.get("project_slug") != project_slug:
                continue
            run_dir = Path(str(summary.get("run_dir") or summary_path.parent))
            if run_dir.exists():
                candidates.append(run_dir)
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _public_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned = json.loads(json.dumps(payload, ensure_ascii=False))
        if isinstance(cleaned.get("provider"), dict):
            cleaned["provider"]["api_key_value"] = None
        return cleaned

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        elapsed = int(time.monotonic() - float(job.get("created_at_monotonic") or time.monotonic()))
        percent = int(job.get("percent") or 0)
        eta = None
        if job.get("status") == "running" and percent > 0:
            eta = int(max(0, elapsed * (100 - percent) / percent))
        return {
            "job_id": job["job_id"],
            "project": job["project"],
            "project_name": job.get("project_name"),
            "status": job["status"],
            "stage": job["stage"],
            "chapter_start": job["chapter_start"],
            "chapter_end": job["chapter_end"],
            "current_chapter": job.get("current_chapter"),
            "current_chunk": job.get("current_chunk"),
            "chapters_completed": job.get("chapters_completed") or 0,
            "chapters_total": job.get("chapters_total") or 0,
            "chunks_completed": job.get("chunks_completed") or 0,
            "chunks_total": job.get("chunks_total"),
            "percent": percent,
            "elapsed_seconds": elapsed,
            "eta_seconds": eta,
            "latest_message": self._redact_text(str(job.get("latest_message") or "")),
            "warnings": job.get("warnings") or [],
            "artifact_path": job.get("artifact_path"),
            "txt_output_path": job.get("txt_output_path"),
            "error": job.get("error"),
        }

    def _redact_text(self, value: str) -> str:
        settings = None
        try:
            settings = self._load_provider_settings(self.workspace(create_if_missing=True), include_secret=True)
        except Exception:
            settings = {}
        secret = str((settings or {}).get("api_key") or "")
        return value.replace(secret, REDACTED_SECRET) if secret else value

    def _open_or_return_path(self, path: Path, should_open: bool) -> dict[str, Any]:
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        if should_open and os.name == "nt":
            try:
                subprocess.Popen(["explorer.exe", str(path)])
                return {
                    "status": "opened",
                    "path": str(path),
                    "opened": True,
                    "open_supported": True,
                    "method": "explorer.exe",
                    "message": "Đã mở thư mục trong File Explorer.",
                }
            except OSError as exc:
                return {
                    "status": "path_only",
                    "path": str(path),
                    "opened": False,
                    "open_supported": False,
                    "fallback": "copy_path",
                    "method": "explorer.exe",
                    "message": "Không mở được File Explorer, hãy sao chép đường dẫn.",
                    "error": self._redact_text(str(exc)),
                }
        if should_open and hasattr(os, "startfile"):
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
                return {
                    "status": "opened",
                    "path": str(path),
                    "opened": True,
                    "open_supported": True,
                    "method": "os.startfile",
                    "message": "Đã mở thư mục trong File Explorer.",
                }
            except OSError as exc:
                return {
                    "status": "path_only",
                    "path": str(path),
                    "opened": False,
                    "open_supported": False,
                    "fallback": "copy_path",
                    "method": "os.startfile",
                    "message": "Không mở được File Explorer, hãy sao chép đường dẫn.",
                    "error": self._redact_text(str(exc)),
                }
        return {
            "status": "path_only",
            "path": str(path),
            "opened": False,
            "open_supported": False,
            "fallback": "copy_path",
            "message": "Không mở được File Explorer, hãy sao chép đường dẫn.",
        }

    def _is_safe_workspace_path(self, workspace: Workspace, path: Path) -> bool:
        try:
            resolved = path.resolve()
            workspace_root = workspace.path.resolve()
            resolved.relative_to(workspace_root)
            return True
        except ValueError:
            return False

    def _write_learning_candidate(self, workspace: Workspace, item_id: str, reviewed_text: str, task_id: str) -> str:
        review_dir = workspace.path / "reviews" / "gui_learning_candidates"
        review_dir.mkdir(parents=True, exist_ok=True)
        path = review_dir / f"{new_id('candidate')}.json"
        payload = {
            "schema_version": "phase7_gui_learning_candidate_v1",
            "translation_id": item_id,
            "reviewed_text": reviewed_text,
            "task_run_id": task_id,
            "scope": "project",
            "mutated_global_memory": False,
            "created_at": utc_now(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return str(path.relative_to(workspace.path))
