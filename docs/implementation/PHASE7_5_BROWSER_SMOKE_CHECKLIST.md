# Phase 7.5 Browser Smoke Checklist

Phase 7.5 PASS is allowed only after this checklist is manually confirmed in the browser. Automated tests and HTTP smoke are supporting evidence only.

## Start Fresh

- [ ] Stop any old backend bound to `127.0.0.1:8765`.
- [ ] Start backend: `uv run --extra dev python -m nts_gui_backend.server --workspace <workspace> --host 127.0.0.1 --port 8765`.
- [ ] Open `http://127.0.0.1:8765/`.
- [ ] Hard-refresh the browser once.

## Version / Cache Reality

- [ ] Open `Cài đặt`.
- [ ] Confirm visible label includes `GUI build: phase7.5`.
- [ ] Confirm backend label includes `phase7.5-browser-reality-fix`.
- [ ] Click `Làm mới trạng thái`.
- [ ] Confirm the debug panel records button id, action, endpoint, redacted payload, response, and timestamp.

## LTP Reality

- [ ] Stop LTP completely.
- [ ] Click `Kiểm tra LTP`.
- [ ] Expected: `LTP chưa chạy`, `unavailable`, `healthy: false`; it must not say active/working.
- [ ] Start LTP.
- [ ] Click `Kiểm tra LTP`.
- [ ] Expected: healthy only after real `/analyze` returns valid tokens.
- [ ] If a process is reachable but invalid, expected: `reachable_but_unhealthy`, not healthy.

## Folder Opening Reality

- [ ] Click `Mở workspace`.
- [ ] Expected: Windows File Explorer opens the end-user output root, typically `<workspace>/artifacts/exports`, via backend `explorer.exe`.
- [ ] Click `Mở dự án`.
- [ ] Expected: Windows File Explorer opens the novel's dedicated TXT chapter output folder: `<workspace>/artifacts/exports/<project>/txt_chapters`.
- [ ] Expected: all TXT chapter files for the same project are in that one `txt_chapters` folder; the action must not open the internal rollout/artifact tree.
- [ ] Confirm `Mở dự án` does not open the technical drawer.
- [ ] If opening fails, expected: copy-path fallback with explicit message.

## Provider Reality

- [ ] Save provider config in GUI: provider, base URL, primary model, fallback model, API key.
- [ ] Confirm API key remains password-style and is not shown after save.
- [ ] Click `Kiểm tra API`.
- [ ] Expected: real saved-config provider preflight, not placeholder text.
- [ ] Expected: no duplicate `/v1/v1` route and no raw API key in debug panel or response.
- [ ] Expected: primary model passes, or fallback model is tested with actionable failure if both fail.

## Translation Reality

- [ ] If API preflight passes, open `Dịch truyện`.
- [ ] Select a project.
- [ ] Click `Dịch thử 1 chương`.
- [ ] Click `Bắt đầu dịch`.
- [ ] Expected: GUI starts a real Phase 6 rollout job, not a fake task record.
- [ ] Expected: progress panel shows job id, status, percent, chapter/chunk fields, elapsed time, latest message, and artifacts when available.
- [ ] Expected: frontend polls `/api/jobs/{job_id}` until terminal state.
- [ ] Expected: progress reaches 100% only after completed/PASS.

## Automated Evidence

- Source evidence after hardening: `app_js.hash = 341d9116dab6`; `backend_service.hash = 9bbf052829cf`.
- Focused GUI tests: `uv run --extra dev python -m pytest tests/test_phase7_gui.py -q` -> `43 passed in 9.69s`.
- Full suite: `uv run --extra dev python -m pytest -q` -> `275 passed in 89.68s`.
- HTTP smoke: `/api/gui/version` returned `phase7.5-browser-reality-fix`; `/api/ltp/status?fresh=1` with LTP off returned `unavailable` and `healthy: false`; `/api/settings/provider/test` with saved `.env.local` provider returned `primary_ok`, `chosen_model: gpt-5.5`, no raw key leak.
- Independent Claude audit provider preflight: `ok=true`, `route_status=primary_ok`, `chosen_model=gpt-5.5`, `message="Kiểm tra API thành công."`.
- Independent Claude HTTP job: `job_fbe1719bda084eb7a408b188f42a4b54`; independent Claude browser job: `job_204625ff2b284135a548d51f5f7b2895`; browser job result: `Production rollout PASS`.
- Real direct GUI translation smoke: `job_361bf0e9d1054643b04372d920e91cb3` ran Phase 6 `run_controlled_production_rollout`, completed `PASS`, `percent: 100`, `chapters_completed: 1/1`, `chunks_completed: 1/1`, `qa_pass: true`, `rules_rendered_count: 0`.
- Real job artifact path: `tmp/phase7_5_gui_real_smoke_utf8/artifacts/gui_jobs/job_361bf0e9d1054643b04372d920e91cb3/production_rollout`.
- Chromium browser smoke via Chrome DevTools MCP: visible Phase 7.5 labels, LTP off as unavailable, provider test success with redacted API key, and `Browser Smoke` translation job completed `Production rollout PASS` at `100%`.
- Claude audit browser engine: Chromium engine through `chrome-devtools-mcp`; Edge was not directly automatable in that environment.
- Screenshot/evidence artifacts: `tmp/phase7_5_provider_test.png`, `tmp/phase7_5_translation_job.png`, `tmp/phase7_5_browser_smoke_evidence.json`.
- Open-folder browser smoke: `Mở workspace` returned `opened: true`, `method: explorer.exe`, `target: output_root`, `path: <workspace>/artifacts/exports`; `Mở dự án` uses `target: preferred_output_path`, now pointing at `<workspace>/artifacts/exports/<project>/txt_chapters`.
- Open-folder evidence artifact: `tmp/phase7_5_open_folder_browser_evidence.json`.

## Safety Reality

- [ ] Debug panel and provider responses never show raw API key.
- [ ] GUI-triggered translation does not use `--use-approved-rules`.
- [ ] Raw NLP cache is not injected.
- [ ] Manga / Ảnh remains Coming Soon only.
- [ ] Path traversal test `POST /api/system/open-path` with `C:\Windows\System32` returns `403 unsafe_path`.

## Result

- Result: pending manual browser confirmation.
- Tester:
- Date:
- Notes:
