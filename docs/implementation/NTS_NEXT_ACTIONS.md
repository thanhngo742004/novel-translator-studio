# NTS Next Actions

## Phase 7.5 Browser Reality Fix

1. Run focused GUI tests: `uv run --extra dev python -m pytest tests/test_phase7_gui.py -q`.
2. Run full suite: `uv run --extra dev python -m pytest -q`.
3. Start backend: `uv run --extra dev python -m nts_gui_backend.server --workspace <workspace> --host 127.0.0.1 --port 8765`.
4. Open frontend: `http://127.0.0.1:8765/`.
5. Complete `docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md` before claiming PASS.
6. Confirm Settings shows `GUI build: phase7.5` and backend `phase7.5-browser-reality-fix`.
7. Click `Làm mới trạng thái` and confirm the debug panel updates button/action/endpoint/redacted payload/response/timestamp.
8. Stop LTP completely, click `Kiểm tra LTP`, and confirm the GUI shows `LTP chưa chạy` / `unavailable` / `healthy: false`.
9. Start LTP, click `Kiểm tra LTP`, and confirm healthy appears only when the real analyze endpoint returns valid tokens.
10. Click `Mở workspace` and confirm Windows File Explorer opens the end-user output root via backend `explorer.exe`.
11. Click `Mở dự án` and confirm Windows File Explorer opens the latest project result folder or export folder, not technical details.
12. In `Cài đặt`, save provider/base URL/primary model/fallback model/API key, then click `Kiểm tra API` and confirm a real preflight result.
13. Start translation with `Dịch thử 1 chương` and confirm `/api/jobs/{job_id}` progress appears and is artifact-backed.
14. Confirm provider failures, QA blockers, and missing prerequisites show blocked/error states instead of fake success.
15. Hand `docs/implementation/PHASE7_5_CLAUDE_REVIEW_PLAN.md` to Claude Code for an independent analyze/test/review pass.

## Provider Config Precedence

- GUI-triggered runs use `<workspace>/config/gui_provider.local.json` first.
- GUI-saved config overrides environment/provider YAML only for GUI-triggered runs.
- CLI-triggered runs keep the existing provider YAML and environment-variable behavior.
- If the GUI local file is missing, NTS Studio uses safe mock defaults until the user saves settings.
- `gui_provider.local.json` is ignored by git and should stay private.

## Current Placeholders

- `Tạm dừng`: shows `Sắp hỗ trợ`.
- `Dừng sau chương hiện tại`: shows `Sắp hỗ trợ` until core supports graceful stop.
- `Xuất EPUB`: disabled with `Sắp hỗ trợ`.
- `Khởi động LTP`: shows current command/limitation guidance when direct backend start is unavailable.
- `Chọn workspace`: validates the selected path and explains backend restart requirements.
- `Manga / Ảnh`: Coming Soon only.

## Guardrails

- Do not add manga OCR/image logic during Phase 7.
- Do not expose raw API keys.
- Do not enable approved rules in production prompts.
- Do not inject raw NLP cache into prompt context.
- Do not weaken Phase 6 QA/safety gates.
- Do not break existing CLI/core behavior.
