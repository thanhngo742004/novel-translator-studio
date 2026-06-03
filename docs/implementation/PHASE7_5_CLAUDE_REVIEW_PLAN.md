# Phase 7.5 Claude Code Review Plan

## Goal

Review Phase 7.5 as a browser-reality fix, not as a unit-test-only pass. The reviewer should verify that NTS Studio behaves as a real local end-user translation app:

- stale frontend/backend detection works,
- LTP off is not reported as healthy,
- workspace/project opening targets end-user output/result folders,
- provider settings are editable and API preflight is real,
- translation starts a real Phase 6 production rollout after API pass,
- progress is backed by real job status/artifacts,
- no API key, approved rules, or raw NLP cache leaks into visible UI or prompts,
- manga remains Coming Soon only.

Do not mark PASS from automated tests alone. Browser behavior is authoritative.

## Primary Files to Review

- `apps/gui/backend/nts_gui_backend/server.py`
  - no-cache API/static responses,
  - static frontend serving,
  - backend entrypoint.
- `apps/gui/backend/nts_gui_backend/service.py`
  - `GET /api/gui/version`,
  - `GET /api/ltp/status?fresh=1`,
  - provider settings and preflight,
  - workspace/project/system open endpoints,
  - translation job start/status/artifacts,
  - safe profile enforcement.
- `apps/gui/frontend/index.html`
  - visible version labels,
  - settings/provider form,
  - debug action log,
  - translation progress panel,
  - button `data-action` coverage.
- `apps/gui/frontend/app.js`
  - event delegation,
  - API/debug logging,
  - provider status locking,
  - translation preset payload construction,
  - progress polling,
  - output-folder open actions.
- `tests/test_phase7_gui.py`
  - provider redaction and editability,
  - real runner invocation through mocks,
  - LTP health states,
  - Windows Explorer open behavior,
  - progress from artifacts,
  - button wiring audit.
- `docs/implementation/PHASE7_5_BROWSER_SMOKE_CHECKLIST.md`
- `docs/implementation/PHASE7_GUI_STATUS.md`
- `docs/implementation/NTS_NEXT_ACTIONS.md`

## Non-Negotiable Safety Checks

Confirm all of these are still true:

- `--use-approved-rules` is never sent for GUI-triggered translation.
- `use_approved_rules` is forced false in the GUI payload/backend runner.
- raw NLP cache is not injected.
- API key is never shown in:
  - `GET /api/settings/provider`,
  - `POST /api/settings/provider/test`,
  - debug action log,
  - job status,
  - screenshot-visible UI,
  - server responses.
- Provider config is stored only in `<workspace>/config/gui_provider.local.json`.
- `.gitignore` excludes `gui_provider.local.json`.
- Manga / Ảnh remains Coming Soon only.

## Automated Test Commands

Run with workspace-local temp/cache paths on Windows to avoid locked `AppData\Local\Temp` pytest folders:

```powershell
$root = (Resolve-Path .).Path
$env:UV_CACHE_DIR = "$root\.uv-cache"
$env:TMP = "$root\tmp\pytest_tmp"
$env:TEMP = "$root\tmp\pytest_tmp"
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

node --check apps/gui/frontend/app.js
uv run --extra dev python -m pytest tests/test_phase7_gui.py -q --basetemp "$root\tmp\pytest_basetemp" -o cache_dir="$root\tmp\pytest_cache"
uv run --extra dev python -m pytest -q --basetemp "$root\tmp\pytest_full_basetemp" -o cache_dir="$root\tmp\pytest_cache"
```

Expected current evidence:

- `tests/test_phase7_gui.py` -> `36 passed`.
- Full suite -> `267 passed`.

## Backend Start Command

Use this command for manual browser review:

```powershell
uv run --extra dev python -m nts_gui_backend.server --workspace <workspace> --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

## Manual Browser Smoke

### 1. Version and Cache Reality

1. Open the GUI.
2. Go to `Cài đặt`.
3. Confirm visible labels:
   - `GUI build: phase7.5 / <hash>`,
   - `Backend: phase7.5-browser-reality-fix / <hash> / started <timestamp>`.
4. Click `Làm mới trạng thái`.
5. Confirm debug panel updates:
   - button id,
   - action,
   - endpoint,
   - redacted payload,
   - response,
   - timestamp.

### 2. LTP Reality

1. Stop LTP completely.
2. Click `Kiểm tra LTP`.
3. Expected:
   - `LTP chưa chạy`,
   - `unavailable`,
   - `healthy: false`,
   - never `đang hoạt động`.
4. Start LTP.
5. Click `Kiểm tra LTP`.
6. Expected healthy only if a real analyze call for `我爱北京天安门。` returns valid tokens.

### 3. Folder Opening Reality

NTS Studio targets end users, so folder opening should point to output/result locations, not developer internals.

1. Click `Mở workspace`.
2. Expected:
   - File Explorer opens the end-user output root, usually `<workspace>/artifacts/exports`.
3. Select/open a project and click `Mở dự án`.
4. Expected:
   - File Explorer opens `<workspace>/artifacts/exports/<project>/txt_chapters`,
   - all TXT chapters for that project are in this one folder,
   - it does not open the internal rollout/artifact tree,
   - does not open the technical drawer.
5. If OS open fails, UI must show a copyable path fallback.

### 4. Provider/API Reality

1. In `Cài đặt`, click `Sửa cấu hình`.
2. Enter:
   - provider name,
   - provider type,
   - base URL,
   - primary model,
   - fallback model,
   - API key,
   - timeout/retries.
3. Click `Lưu cấu hình`.
4. Confirm API key field shows password placeholder, not raw key.
5. Click `Sửa cấu hình` again.
6. Confirm fields are editable after save.
7. Click `Kiểm tra API`.
8. Expected:
   - real provider preflight,
   - visible `Kiểm tra API thành công.` or actionable failure,
   - debug log response is not placeholder,
   - payload shows `api_key: "********"`,
   - raw key is absent from page text and response.

### 5. Translation Reality

1. Open `Dịch truyện`.
2. Select a project with at least one imported chapter and approved stable prompt prerequisites.
3. Click `Dịch thử 1 chương`.
4. Confirm chapter range changes visibly to `1–1`.
5. Click `Bắt đầu dịch`.
6. Expected:
   - if provider was not preflighted, GUI first calls `/api/settings/provider/test`,
   - if API passes, GUI starts a real Phase 6 rollout job,
   - progress panel appears,
   - `/api/jobs/{job_id}` polling continues until terminal state,
   - progress reaches `100%` only after completed/PASS,
   - artifact path appears.
7. Inspect rollout summary:
   - `final_decision: PASS` or a real blocked/error reason,
   - `qa_pass: true` for PASS,
   - `rules_rendered_count: 0`,
   - `api_calls_used > 0` for a real new run.

### 6. Remaining Placeholder Reality

Confirm every unsupported action is explicit:

- `Tạm dừng`: disabled or `Sắp hỗ trợ`.
- `Dừng sau chương hiện tại`: placeholder unless core supports it.
- `Xuất EPUB`: disabled/placeholder.
- `Khởi động LTP`: shows copyable command/guidance if direct start unsupported.
- `Manga / Ảnh`: Coming Soon only.

## Browser Evidence Artifacts to Inspect

Recent real Edge smoke generated:

- `tmp/phase7_5_initial.png`
- `tmp/phase7_5_settings_ltp_workspace.png`
- `tmp/phase7_5_provider_test.png`
- `tmp/phase7_5_translation_job.png`
- `tmp/phase7_5_browser_smoke_evidence.json`

The expected evidence from the latest smoke:

- visible GUI label includes `phase7.5`,
- backend label includes `phase7.5-browser-reality-fix`,
- LTP off displays `ltp_server: LTP chưa chạy (unavailable)`,
- provider test displays `Kiểm tra API thành công.`,
- API key is redacted in debug panel,
- translation progress panel shows `completed`, `1 / 1`, `100%`,
- latest message includes `Production rollout PASS: production_rollout`.

## Review Questions for Claude Code

1. Are any visible buttons missing from `actionMap` or `VISIBLE_BUTTON_WIRING`?
2. Does any button silently do nothing in browser?
3. Can stale frontend/backend code still be mistaken for current Phase 7.5?
4. Does `Mở workspace` open an end-user output root instead of repo/config internals?
5. Does `Mở dự án` open `<workspace>/artifacts/exports/<project>/txt_chapters` instead of technical details or the internal rollout tree?
6. Does LTP-off ever display as active/healthy?
7. Does provider preflight use saved GUI config and return actionable failures?
8. Does translation start a real Phase 6 rollout only after API pass?
9. Is progress based on job status/artifacts rather than a fake timer?
10. Is any raw API key visible in DOM, debug panel, responses, logs, or artifacts?
11. Are approved rules still verifier-only and absent from prompts?
12. Is raw NLP cache still absent from prompt context?
13. Is manga still placeholder only?

## PASS / BLOCKED Decision

PASS is valid only if:

- full tests pass,
- real browser smoke passes,
- folder opening targets end-user output/result locations,
- provider preflight succeeds or fails with a real actionable reason,
- translation starts a real Phase 6 job after API pass,
- security/safety constraints are verified.

BLOCKED_PROVIDER_OR_ENVIRONMENT is valid only if:

- provider/auth/model/network blocks real preflight or translation,
- LTP is required by the selected flow and cannot be started,
- Windows/browser/File Explorer access is blocked by environment,
- the blocker is reproduced and documented with exact commands/errors.
