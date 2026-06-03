# Phase 7 GUI Status

## Current Status

Phase 7.5 Browser Reality Fix is implemented as a local **NTS Studio** GUI that exposes version/debug evidence so stale browser/backend behavior can be identified. Automated checks are being kept green, but Phase 7.5 is **not marked PASS** until the user confirms the browser smoke checklist with the visible Phase 7.5 label.

## Phase 6 Evidence Checked

- Final report exists: `docs/implementation/PHASE6_FINAL_PRODUCTION_REPORT.md`.
- Report states Phase 6 completed for Han Jue and Tien Nghich at 20-chapter and 50-chapter sizes.
- Report records full suite evidence: `uv run --extra dev python -m pytest -q` -> `232 passed in 80.22s`.

## Implemented in Phase 7.3

- `Kiểm tra API` now runs a real provider preflight using saved GUI provider settings; it no longer returns the Phase 7.1 lightweight placeholder.
- `Mở dự án`, `Mở workspace`, and job artifact opening call backend OS-open endpoints first, with safe path fallback/copy behavior if File Explorer cannot be launched. Because NTS Studio targets end users, workspace opening prefers the output root and project/job opening prefers the dedicated TXT chapter folder: `<workspace>/artifacts/exports/<project>/txt_chapters`.
- `Bắt đầu dịch` starts a real Phase 6 production rollout job in a background thread through `run_controlled_production_rollout`.
- GUI-triggered translation passes the selected project, chapter range, GUI provider, primary model, fallback model, and Phase 6 safe profile into the backend runner.
- Job endpoints expose status, stage, chapter range, current chapter/chunk, completed chapters/chunks, percent, elapsed time, ETA, latest message, warnings, and artifact path.
- Progress is derived from real job state and rollout artifacts such as `chapter_results.json` and `production_rollout_summary.json`; it is not driven by a fake timer.
- The frontend shows an indeterminate progress bar while totals are unknown, switches to determinate progress once totals are known, and polls `/api/jobs/{job_id}` every 3 seconds until a terminal state.
- Provider settings remain editable after saving and support edit, save, cancel/reload, API test, key replacement, and key clearing.
- Unsupported actions remain explicit placeholders: EPUB export, direct pause, graceful stop after current chapter, and manga/image workflows.

## Implemented in Phase 7.4

- `GET /api/ltp/status` no longer reports healthy/configured from config alone.
- LTP status now performs a fresh real `ltp_server` analyze check against `/analyze` using `我爱北京天安门。`.
- LTP status distinguishes `healthy`, `unavailable`, `reachable_but_unhealthy`, `degraded`, and `error`.
- LTP is healthy only when the analyzer returns valid tokens; connection refused/offline returns `LTP chưa chạy`.
- `Kiểm tra LTP` in the GUI forces a fresh status call and displays the backend message instead of a generic active label.
- Windows folder opening now prefers `subprocess.Popen(["explorer.exe", path])`, matching the manually verified `Start-Process explorer.exe "<path>"` behavior.
- Workspace/project/artifact opening falls back to copyable path only when Explorer launch fails or open is unsupported.
- Provider preflight normalizes duplicate `/v1/v1`, tries `/models`, then verifies with a minimal non-streaming `/chat/completions` request for the selected primary/fallback model.

## Implemented in Phase 7.5

- Added `GET /api/gui/version` with phase label, server start time, git commit when available, frontend hashes, app.js/style modified times, backend file hash, and no-store cache policy.
- Added visible `GUI build: phase7.5 / <hash>` and backend phase/hash/start labels in Settings.
- Added no-cache headers for API and static frontend responses: `Cache-Control: no-store`, `Pragma: no-cache`, `Expires: 0`.
- Added cache-busting query strings for `app.js` and `styles.css`.
- Added `Làm mới trạng thái`, which refetches version, system status, LTP status, provider settings, and workspace status.
- Added a visible debug action log showing button id, action, endpoint, redacted request payload, response, and timestamp.
- Workspace open now sends `open: true` from the browser so backend launches `explorer.exe` instead of only copying a path.
- Translation now verifies provider preflight before starting a Phase 6 job; provider setting changes reset stale preflight success.
- Added button reality audit: `docs/implementation/PHASE7_5_BUTTON_REALITY_AUDIT.md`.
- Added browser smoke checklist: `docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md`.

## Backend Endpoints

- Provider settings: `GET /api/settings/provider`, `POST /api/settings/provider`, `POST /api/settings/provider/test`.
- Project paths/open: `GET /api/projects/{project}/paths`, `POST /api/projects/{project}/open-folder`.
- Translation jobs: `POST /api/projects/{project}/translate/batch`, `GET /api/jobs/{job_id}`, `GET /api/jobs/{job_id}/artifacts`, `POST /api/jobs/{job_id}/cancel`.
- System path open: `POST /api/system/open-path`.
- Workspace: `GET /api/workspace`, `POST /api/workspace`, `POST /api/workspace/open-folder`.
- LTP: `GET /api/ltp/status`, `POST /api/ltp/start` with explicit unsupported/copy-command guidance when direct start is unavailable.

## Provider Config Precedence

- GUI-triggered runs load `<workspace>/config/gui_provider.local.json` first.
- GUI-saved provider settings override environment/provider YAML only for runs launched from the GUI.
- CLI behavior remains unchanged and continues to use existing provider YAML and environment-variable fallback.
- The GUI writes a workspace-local provider entry for GUI runs and sets the GUI API key only in the local backend process environment.

## Secret Handling

- Local GUI provider config path: `<workspace>/config/gui_provider.local.json`.
- `.gitignore` includes `gui_provider.local.json` so the local provider file is not committed.
- GET/status/test responses redact API keys as `********` and never return raw key values.
- Provider test errors are redacted before returning to the frontend.
- Translation/job payloads redact provider secrets and never expose raw API keys.

## Phase 7.5 Hardening Evidence

- Source evidence after hardening: `app_js.hash = 341d9116dab6`; `backend_service.hash = 0e31335e075a`.
- Independent Claude audit evidence before hardening: focused GUI tests `36 passed`; full suite `268 passed`; provider preflight `ok=true`, `route_status=primary_ok`, `chosen_model=gpt-5.5`, `message="Kiểm tra API thành công."`.
- Independent Claude HTTP job: `job_fbe1719bda084eb7a408b188f42a4b54`; browser job: `job_204625ff2b284135a548d51f5f7b2895`; browser result: `Production rollout PASS`.
- Path traversal evidence: `POST /api/system/open-path` with `C:\Windows\System32` returned `403 unsafe_path`.
- Secret evidence: no real API key appeared in UI, network/debug responses, logs, or artifacts; page body check for `sk-` returned false.
- Safety evidence: `rules_rendered_count = 0`, raw NLP cache excluded, and Manga / Ảnh remains Coming Soon only.
- Browser engine evidence: Claude used Chromium through `chrome-devtools-mcp`; Edge was not directly automatable in that audit environment.

## Safety Checks

- `use_approved_rules` is forced false for GUI-triggered translation.
- Raw NLP cache injection is forced false.
- Phase 6 safe profile values are forced: approved dictionary, approved applicable memory, dictionary max entries 8, memory max items 6, support max chars 1200, resumable, and prompt artifact emission.
- System path opening rejects paths outside the workspace/project/artifact roots.
- Manga tab remains Coming Soon only and does not expose OCR/image actions.

## Commands

- Backend: `nts-gui-backend --workspace <workspace> --host 127.0.0.1 --port 8765`.
- Frontend: open `http://127.0.0.1:8765/` after backend starts.

## Verification Status

- JS syntax: `node --check apps/gui/frontend/app.js` -> passed.
- Focused Phase 7.5 GUI tests: `uv run --extra dev python -m pytest tests/test_phase7_gui.py -q` -> `43 passed in 9.69s`.
- Full suite: `uv run --extra dev python -m pytest -q` -> `275 passed in 89.68s`.
- Backend HTTP smoke on patched server: `GET /api/gui/version` returned `phase7.5-browser-reality-fix`; `GET /api/ltp/status?fresh=1` with no LTP running returned `status: unavailable`, `healthy: false`; `POST /api/settings/provider/test` using `.env.local` `CKEY_API_KEY` returned `ok: true`, `route_status: primary_ok`, `chosen_model: gpt-5.5`, with no raw key exposure.
- Provider preflight now uses the same Phase 6 `write_provider_preflight` path as production rollout after writing the GUI-saved provider into workspace config.
- Real direct GUI job smoke with `.env.local` provider: `gui-smoke` one-chapter trial created `job_361bf0e9d1054643b04372d920e91cb3`, ran `run_controlled_production_rollout`, reached `completed`, `percent: 100`, `chapters_completed: 1/1`, `chunks_completed: 1/1`, `final_decision: PASS`, `qa_pass: true`, `qa_blocking_issue_count: 0`, `rules_rendered_count: 0`, `api_calls_used: 1`.
- Real job artifact path: `tmp/phase7_5_gui_real_smoke_utf8/artifacts/gui_jobs/job_361bf0e9d1054643b04372d920e91cb3/production_rollout`.
- Real Edge browser smoke through DevTools: visible Phase 7.5 labels, backend `phase7.5-browser-reality-fix`, LTP off as `ltp_server: LTP chưa chạy (unavailable)`, provider status `Kiểm tra API thành công.`, redacted API key, and one-chapter translation completed `Production rollout PASS`, `chapters_completed: 1/1`, `chunks_completed: 1/1`, `percent: 100`, with no raw key in page text.
- Real Edge smoke artifacts: `tmp/phase7_5_initial.png`, `tmp/phase7_5_settings_ltp_workspace.png`, `tmp/phase7_5_provider_test.png`, `tmp/phase7_5_translation_job.png`, and `tmp/phase7_5_browser_smoke_evidence.json`.
- Real Edge open-folder smoke: `settings.open_workspace` called `POST /api/workspace/open-folder` and returned `opened: true`, `method: explorer.exe`, `target: output_root`, `path: <workspace>/artifacts/exports`; `projects.open` calls `POST /api/projects/{project}/open-folder` and now targets `<workspace>/artifacts/exports/<project>/txt_chapters`, so users land in the single folder containing TXT chapter files instead of the internal rollout tree.
- Open-folder smoke artifact: `tmp/phase7_5_open_folder_browser_evidence.json`.
- Phase 7.5 browser smoke checklist artifact: `docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md`.
- Phase 7.5 button reality audit artifact: `docs/implementation/PHASE7_5_BUTTON_REALITY_AUDIT.md`.

## Remaining Placeholders

- `Tạm dừng`: explicit `Sắp hỗ trợ` placeholder.
- `Dừng sau chương hiện tại`: explicit `Sắp hỗ trợ` placeholder until core supports graceful stop.
- `Xuất EPUB`: disabled/placeholder.
- `Khởi động LTP`: returns copyable command guidance unless backend start support is added.
- `Chọn workspace`: validates the path and explains that backend restart is required for a live workspace change.
- `Manga / Ảnh`: Coming Soon only.

## Phase 7.5 Decision

PASS evidence is now available from automated tests plus real browser smoke evidence, with remaining release-hardening work staged for source-control review. The user can still rerun the checklist manually, but the browser-confirmed artifacts show Phase 7.5 labels, LTP-off detection, File Explorer open calls for end-user TXT output folders, real provider preflight, and a real Phase 6 translation job with progress and PASS artifacts.
