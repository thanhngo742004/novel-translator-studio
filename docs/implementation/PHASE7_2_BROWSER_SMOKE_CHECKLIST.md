# Phase 7.2 Browser Smoke Checklist

## Backend

- Start command: `nts-gui-backend --workspace <workspace> --host 127.0.0.1 --port 8765`
- Frontend URL: `http://127.0.0.1:8765/`

## Checklist

| Check | Expected result | Evidence status |
|---|---|---|
| Sidebar: Trang chủ | Home page visible | Covered by event delegation and page ids |
| Sidebar: Dự án truyện | Projects page visible | Covered by event delegation and page ids |
| Sidebar: Dịch truyện | Translation page visible | Covered by event delegation and page ids |
| Sidebar: Kiểm tra bản dịch | Review page visible | Covered by event delegation and page ids |
| Sidebar: Xuất file | Export page visible | Covered by event delegation and page ids |
| Sidebar: Cài đặt | Settings page visible | Covered by event delegation and page ids |
| Sidebar: Manga / Ảnh | Coming Soon placeholder visible | `tests/test_phase7_gui.py` verifies only placeholder actions |
| Tạo dự án truyện mới | Opens project wizard/page | `home.create_project` navigates to Projects |
| Mở dự án | Calls `/api/projects/{project}/open-folder`, copies path, does not open technical drawer | `tests/test_phase7_gui.py` verifies endpoint and frontend handler distinction |
| Dịch tiếp | Selects project and opens translation page | Frontend handler updates selected project before navigation |
| Kiểm tra bản dịch | Selects project and loads project review queue | Frontend handler calls queue loader before navigation |
| Xuất file | Selects project and opens export page | Frontend handler navigates to export page |
| Xem chi tiết kỹ thuật | Opens technical drawer only | Separate `projects.technical` handler |
| Dịch thử 1/3/10 | Updates visible chapter range | `selectPreset` updates `chapter-start`, `chapter-end`, and range message |
| Dịch 20/50 | Updates visible chapter range | `selectPreset` updates `chapter-start`, `chapter-end`, and range message |
| Tiếp tục từ chỗ dừng | Enables resumable resume mode | `selectPreset("resume")` checks resumable option |
| Bắt đầu dịch | Calls backend wrapper and displays run id/artifact path | Backend returns `Đã tạo tác vụ dịch an toàn` with `run_id` |
| Kiểm tra LTP | Calls `/api/ltp/status` and updates status | Frontend `loadLtpStatus` handler |
| Khởi động LTP | Calls `/api/ltp/start`; unsupported state gives copyable command | Backend returns explicit `unsupported` with `copyable_command` |
| Mở workspace | Calls `/api/workspace/open-folder`, copies path | Frontend `openWorkspaceFolder` handler |
| Chọn workspace | Validates input through `/api/workspace` | Frontend `validateWorkspacePath` handler |
| Lưu cấu hình | Saves provider settings to local private config | Tests verify save/load/redaction |
| Sửa cấu hình | Keeps fields editable after save | Frontend `enableProviderEditing` handler |
| Xóa API key | Clears saved key | Tests verify key can be cleared |
| Hủy thay đổi | Reloads saved settings | Frontend `loadProviderSettings(true)` handler |
| Kiểm tra API | Calls `/api/settings/provider/test` | Tests verify redacted actionable result |

## Manual Notes

This checklist is artifacted for browser click-through. Automated tests and backend smoke verify the route/action contracts, but a human should still click through the running browser UI before release packaging.
