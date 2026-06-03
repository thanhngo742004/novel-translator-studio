# Phase 7.3 Browser Smoke Checklist

Use this checklist before claiming Phase 7.3 PASS. The GUI must be running at `http://127.0.0.1:8765/` with backend command:

```powershell
nts-gui-backend --workspace <workspace> --host 127.0.0.1 --port 8765
```

## Required Result

Phase 7.3 is **not PASS** until these checks are manually confirmed in the browser. Automated tests verify backend/frontend wiring, but this checklist confirms real click behavior.

## Phase 7.4 Blocker Fix Checks

- [ ] Stop LTP completely.
- [ ] Click `Kiểm tra LTP`.
- [ ] Expected while stopped: GUI shows `LTP chưa chạy`, `unavailable`, `healthy: false`, not `đang hoạt động`.
- [ ] Start LTP.
- [ ] Click `Kiểm tra LTP` again.
- [ ] Expected after start: GUI shows `healthy` only if real `/analyze` succeeds with valid tokens.
- [ ] Click `Mở workspace`.
- [ ] Expected: Windows File Explorer opens through backend `explorer.exe`; fallback path appears only if Explorer launch fails.
- [ ] Click `Mở dự án`.
- [ ] Expected: Windows File Explorer opens the project/output/artifact folder; technical drawer does not open.
- [ ] Click `Kiểm tra API`.
- [ ] Expected: real provider preflight result using saved GUI provider/base URL/model/API key; no raw key is displayed.

## Provider Settings

- [ ] Open `Cài đặt`.
- [ ] Click `Sửa cấu hình`; provider, base URL, primary model, fallback model, timeout, retries, and API key fields are editable.
- [ ] Save provider/base URL/model/API key with `Lưu cấu hình`.
- [ ] Reload settings; API key is shown only as a redacted saved placeholder, never raw text.
- [ ] Click `Kiểm tra API`; the backend calls the real provider preflight endpoint using saved GUI settings.
- [ ] Failure messages are actionable: invalid key, unreachable base URL, missing model, or fallback failure.
- [ ] Replace the model/base URL/API key and save again.
- [ ] Click `Xóa API key`; saved settings reload with no configured key.

## Project Open and Navigation

- [ ] Click every sidebar item; page changes visibly.
- [ ] Click `Mở dự án`; Windows File Explorer opens the project folder, or the GUI shows/copies a safe fallback path.
- [ ] Confirm `Mở dự án` does **not** open `Xem chi tiết kỹ thuật`.
- [ ] Click `Dịch tiếp`; the translation page opens with that project selected.
- [ ] Click `Kiểm tra bản dịch`; the review page opens filtered by that project.
- [ ] Click `Xuất file`; the export page opens for that project.
- [ ] Click `Xem chi tiết kỹ thuật`; only this button opens the technical drawer.

## Translation and Progress

- [ ] Select a project in `Dịch truyện`.
- [ ] Change `Chương bắt đầu` and `Chương kết thúc`; `Sẽ dịch chương X-Y` updates.
- [ ] Click `Dịch thử 1 chương`; chapter range payload becomes one chapter.
- [ ] Click `Dịch thử 3 chương`; chapter range payload becomes three chapters.
- [ ] Click `Dịch thử 10 chương`; chapter range payload becomes ten chapters.
- [ ] Click `Dịch 20 chương`; chapter range payload becomes twenty chapters.
- [ ] Click `Dịch 50 chương`; chapter range payload becomes fifty chapters.
- [ ] Click `Tiếp tục từ chỗ dừng`; resumable/resume mode is visible.
- [ ] Click `Bắt đầu dịch`; a real backend production rollout job starts, not a fake task record.
- [ ] A progress panel appears with project name, chapter range, status, run id, latest message, elapsed time, and artifact path when available.
- [ ] Progress is indeterminate while totals are unknown and switches to determinate once chapter/chunk totals are available.
- [ ] `GET /api/jobs/{job_id}` returns percent, chapter, chunk, and status fields.
- [ ] The frontend polls job status every 2-5 seconds and stops after completed, blocked, error, or cancelled.
- [ ] Progress never reaches 100 percent before the job is completed/PASS.
- [ ] Blocked/provider/QA failures render a blocked/error state instead of green success.
- [ ] `Xem chi tiết` opens job details.
- [ ] `Mở thư mục kết quả` opens File Explorer or shows/copies the artifact path fallback.
- [ ] `Dừng sau chương hiện tại` shows an explicit unsupported placeholder unless core graceful stop support exists.

## LTP and Workspace

- [ ] Click `Kiểm tra LTP`; status updates to healthy/degraded/unavailable based on a fresh real analyze check, not cached config.
- [ ] Click `Khởi động LTP`; the GUI either starts it through backend support or shows a copyable PowerShell command.
- [ ] Click `Mở workspace`; File Explorer opens the workspace or a safe fallback path is copied.
- [ ] Enter a workspace path and click `Chọn workspace`; backend validates it and explains that live switching requires backend restart if unsupported.

## Review and Export

- [ ] Click `Lưu chỉnh sửa`; save endpoint runs and gives visible feedback.
- [ ] Click `Lưu & cho hệ thống học`; learn-from-edit endpoint creates a scoped candidate artifact.
- [ ] Click `Không học, chỉ lưu bản dịch`; save-only endpoint runs.
- [ ] Click `Đánh dấu đã kiểm tra`; reviewed endpoint runs.
- [ ] Click `Bỏ qua`; UI moves to the next review item.
- [ ] Click `Xuất TXT`; export endpoint runs if supported.
- [ ] Click `Xuất EPUB`; it is disabled or shows `Sắp hỗ trợ`.
- [ ] Click `Xuất gói kiểm tra`; review package export runs if supported.

## Safety Checks

- [ ] No response or UI panel shows a raw API key.
- [ ] No GUI command/payload contains `--use-approved-rules`.
- [ ] Raw NLP cache is not injected into prompts.
- [ ] Manga / Ảnh remains Coming Soon only.

## Smoke Result

- Result: pending manual browser confirmation.
- Tester:
- Date:
- Notes:
