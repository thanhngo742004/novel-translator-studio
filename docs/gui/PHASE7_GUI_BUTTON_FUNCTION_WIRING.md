# PHASE7_GUI_BUTTON_FUNCTION_WIRING

## Purpose

Every visible NTS Studio button/control must be wired to one of:

1. Existing backend/core action.
2. New backend wrapper around existing CLI/core function.
3. Frontend-only UI action.
4. Explicit placeholder / Coming Soon.

If exact function names differ, Codex must inspect `apps/cli/nts_cli/main.py`, `packages/nts_core`, and `packages/nts_storage`. Do not duplicate translation business logic in the frontend.

## Hard constraints

Never wire normal UI to:

```text
--use-approved-rules
```

Rules remain verifier-only / QA-only. Do not expose raw API keys. Do not inject raw NLP cache. Do not copy project-specific dictionary/memory across projects unless explicitly safe and scoped.

## Option display policy

Raw `--flags` are hidden by default. Use presets, checkboxes, and advanced chips.

### Presets

| UI preset | Meaning |
|---|---|
| Dịch thử 1 chương | small canary/trial |
| Dịch thử 3 chương | small validation/trial |
| Dịch thử 10 chương | larger canary |
| Dịch 20 chương | production batch |
| Dịch 50 chương | production scaling batch |
| Tiếp tục từ chỗ dừng | resumable mode |
| Kiểm tra chất lượng | validation/QA |
| Xuất file | export |

### Checkboxes

| Checkbox | CLI/core meaning |
|---|---|
| Dùng từ điển đã duyệt | `--use-approved-dictionary` |
| Dùng ghi nhớ đã duyệt | hybrid prompt with approved applicable memory |
| Tạo báo cáo chi tiết | `--emit-prompt-artifacts` |
| Cho phép tiếp tục nếu bị dừng | `--resumable` |
| Kiểm tra chất lượng sau khi dịch | run QA/validation |
| Giới hạn số mục từ điển | `--dictionary-max-entries` |
| Giới hạn số ghi nhớ | `--memory-max-items` |
| Giới hạn phần hỗ trợ | `--support-max-chars` |

### Advanced chips

Show raw flags only inside `Xem chi tiết kỹ thuật`.

Example:

```text
✓ Dùng từ điển đã duyệt → --use-approved-dictionary
✓ Tạo báo cáo chi tiết → --emit-prompt-artifacts
✓ Tiếp tục từ chỗ dừng → --resumable
```

If `--use-approved-rules` appears in advanced mode, it must be disabled with explanation:

```text
Approved rules are verifier-only in the current production profile.
```

## Safe runtime default

UI label:

```text
Hồ sơ an toàn đã kiểm chứng
```

Internal mapping:

```text
--use-stable-prompt
--use-hybrid-prompt
--use-approved-dictionary
--dictionary-max-entries 8
--memory-max-items 6
--support-max-chars 1200
--emit-prompt-artifacts
--resumable
```

## Recommended backend routes

| Route | Purpose |
|---|---|
| `GET /api/health` | backend health |
| `GET /api/system/status` | workspace/provider/LTP/readiness summary |
| `GET /api/ltp/status` | LTP status |
| `GET /api/projects` | list projects |
| `GET /api/projects/{project}` | project summary |
| `POST /api/projects/import` | import/create project |
| `POST /api/projects/{project}/scan-chapters` | chapter detection |
| `POST /api/projects/{project}/nlp/cache-build` | build NLP cache |
| `GET /api/projects/{project}/dictionary` | dictionary summary |
| `POST /api/projects/{project}/dictionary/build` | build dictionary candidates |
| `GET /api/projects/{project}/memory` | memory summary |
| `POST /api/projects/{project}/memory/mine` | mine memory candidates |
| `POST /api/projects/{project}/memory/auto-review` | audited auto-review bundle |
| `POST /api/projects/{project}/validate` | safe validation |
| `POST /api/projects/{project}/translate/trial` | trial/canary translation |
| `POST /api/projects/{project}/translate/batch` | 20/50 production batch |
| `POST /api/projects/{project}/translate/resume` | resume latest run |
| `GET /api/projects/{project}/runs` | list runs |
| `GET /api/runs/{run_id}` | run summary |
| `GET /api/runs/{run_id}/artifacts` | artifact listing |
| `GET /api/projects/{project}/review-queue` | review queue |
| `GET /api/review/{item_id}` | review item details |
| `POST /api/review/{item_id}/save` | save manual edit |
| `POST /api/review/{item_id}/learn` | learn from edit via scoped candidate/audit |
| `POST /api/review/{item_id}/mark-reviewed` | mark reviewed |
| `POST /api/projects/{project}/export` | export TXT/EPUB/review package |

## CLI/core command families to inspect

Likely existing command families:

- `nts nlp status`
- `nts nlp cache-build`
- `nts dict build/review/approve/reject/export/status`
- `nts learn validate-approved-memory`
- `nts learn mine-memory-candidates` or current equivalent
- `nts production rollout`

Codex must inspect the actual CLI before wiring.

## Page wiring

### Trang chủ

| Button | Wiring |
|---|---|
| Tạo dự án truyện mới | frontend navigation to create-project wizard |
| Tiếp tục dịch truyện | get projects, open latest active project |
| Xem bản dịch cần kiểm tra | open aggregated review queue |
| Kiểm tra lại LTP | `GET /api/ltp/status` |
| Mở cài đặt API | navigate to Settings |
| Recent project `Mở` | open selected project |

### Dự án truyện

| Button | Wiring |
|---|---|
| Mở dự án | route to project detail |
| Dịch tiếp | route to Translate page with project preselected |
| Kiểm tra bản dịch | route to Review page filtered by project |
| Xuất file | route to Export page filtered by project |
| Xem chi tiết kỹ thuật | open technical drawer |

### Create project wizard

| Button | Wiring |
|---|---|
| Chọn file | frontend file/path picker |
| Quét chương | `POST /api/projects/{project}/scan-chapters` |
| Nhận diện tự động | `POST /api/projects/{project}/nlp/cache-build` |
| Tiếp tục | frontend step transition |
| Tạo dự án | `POST /api/projects/import` |

### Dịch truyện

| Button | Wiring |
|---|---|
| Dịch thử 1 chương | trial/canary with 1 chapter |
| Dịch thử 3 chương | trial/canary with 3 chapters |
| Dịch thử 10 chương | trial/canary with 10 chapters |
| Dịch 20 chương | production batch 20 chapters |
| Dịch 50 chương | production batch 50 chapters |
| Tiếp tục từ chỗ dừng | resumable latest incomplete run |
| Bắt đầu dịch | `POST /api/projects/{project}/translate/batch` |
| Tạm dừng | pause if supported, otherwise disabled |
| Dừng sau chương hiện tại | graceful stop if supported |
| Xem chi tiết kỹ thuật | command preview + artifact links |

Production payload must force:

```json
{
  "safe_profile": true,
  "use_approved_dictionary": true,
  "use_approved_memory": true,
  "emit_prompt_artifacts": true,
  "resumable": true,
  "use_approved_rules": false
}
```

### Kiểm tra bản dịch

| Button | Wiring |
|---|---|
| Mở đoạn cần kiểm tra | `GET /api/review/{item_id}` |
| Lưu chỉnh sửa | `POST /api/review/{item_id}/save` |
| Lưu & cho hệ thống học | `POST /api/review/{item_id}/learn` |
| Không học, chỉ lưu bản dịch | same as save-only |
| Đánh dấu đã kiểm tra | `POST /api/review/{item_id}/mark-reviewed` |
| Bỏ qua | frontend next item |
| Xem bản gốc | frontend toggle |
| Xem chi tiết kỹ thuật | artifact/support drawer |

`Lưu & cho hệ thống học` creates scoped candidate/audit artifacts. It must not blindly mutate global memory/dictionary.

### Xuất file

| Button | Wiring |
|---|---|
| Xuất TXT | export format `txt` |
| Xuất EPUB | export format `epub` if supported; otherwise disabled Coming Soon |
| Xuất gói kiểm tra | export review package |
| Mở thư mục kết quả | OS action if supported, otherwise copy path |

### Cài đặt

| Button | Wiring |
|---|---|
| Kiểm tra API | provider status/preflight without raw key |
| Mở hướng dẫn cấu hình API | local docs/modal |
| Kiểm tra LTP | `GET /api/ltp/status` |
| Khởi động LTP | backend start if supported; otherwise copy command |
| Chọn workspace | path picker/config update if supported |

### Manga / Ảnh — Sắp ra mắt

Allowed buttons only:

- `Tìm hiểu kế hoạch`
- `Đóng`

Forbidden:

- no OCR action
- no image processing
- no upload image pipeline
- no production manga action

## Async button states

Every async action needs: idle, running, success, warning, blocked/error, retry available.

## Required tests

Codex must add/update tests proving:

- every visible button has a mapping
- `--use-approved-rules` is never sent
- API keys are not exposed
- raw NLP cache is not injected
- friendly checkboxes map to safe flags
- manga tab is placeholder-only
- review save does not learn unless requested
- learn-from-edit creates scoped candidate/audit artifact
- production batch payload uses safe defaults
- unsupported export states are explicit
