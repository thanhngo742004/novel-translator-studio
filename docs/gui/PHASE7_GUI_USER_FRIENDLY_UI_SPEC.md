# PHASE7_GUI_USER_FRIENDLY_UI_SPEC

## Goal

Build **NTS Studio** as a simple guided app for basic end users, not a developer control-room dashboard.

The user should understand:

1. What novel am I working on?
2. What should I do next?
3. Is the system ready?

## Product name

Use `NTS Studio`. Avoid `NTS Control Room`.

## Target user

Basic computer user: can choose files, click buttons, review text, and export outputs. They should not need to understand NLP, prompt artifacts, validation deltas, memory impact, or rollout internals.

## Visual direction

Keep the dark slate/charcoal theme from the approved mockup, but reduce density.

Color meanings:

| State | Color |
|---|---|
| Running | Blue |
| Pass / completed | Emerald green |
| Needs attention | Amber |
| Blocked / error | Red |
| Needs review | Purple |
| Neutral | Slate/zinc |

Avoid generic purple AI SaaS visuals, dense technical charts, or a DevOps control room feel.

## Required Codex skill direction

Design must apply:

- `design-taste-frontend` for polish
- `minimalist-ui` for simple end-user flow
- `web-design-guidelines` for UX/accessibility
- `lamm-t-architect` for NTS architecture correctness
- `full-output-enforcement` for complete implementation files

## Main navigation

Sidebar must contain only:

```text
Trang chủ
Dự án truyện
Dịch truyện
Kiểm tra bản dịch
Xuất file
Cài đặt
Manga / Ảnh — Sắp ra mắt
```

Do not expose these as top-level tabs for basic users:

```text
NLP / LTP
Dictionary
Memory
Validation / QA
Production Rollout
Artifacts / Human Review
```

Those must be hidden behind `Xem chi tiết kỹ thuật`.

## Label translation

| Internal label | End-user label |
|---|---|
| Validation / QA | Kiểm tra chất lượng |
| Production Rollout | Dịch hàng loạt |
| Dictionary | Từ điển tên riêng & thuật ngữ |
| Memory | Ghi nhớ cách dịch |
| Artifacts | Báo cáo & file kết quả |
| NLP / LTP | Nhận diện tên riêng & thuật ngữ |
| Prompt artifacts | Chi tiết kỹ thuật |
| Delta | Mức cải thiện |
| Severe flag | Lỗi nghiêm trọng |
| Unsafe compression | Dịch bị rút gọn nguy hiểm |
| Truncation | Dịch bị cụt |
| Regression | Chất lượng giảm |

## Home screen

Header:

```text
Bạn muốn làm gì hôm nay?
```

Three large action cards:

1. `Tạo dự án truyện mới` — chọn file truyện gốc và thiết lập dự án.
2. `Tiếp tục dịch truyện` — mở dự án đang làm dở.
3. `Xem bản dịch cần kiểm tra` — duyệt các đoạn cần xem lại.

Simple status strip:

```text
Hệ thống sẵn sàng
LTP đang chạy
API kết nối ổn
Lần dịch gần nhất: Thành công
```

Recent projects: show only project name, progress, status, and next action. Do not show raw artifacts, API calls, prompt bundles, memory internals, or validation tables on the home page.

## Project page

Show project cards with:

- tên truyện
- language pair
- chapter count
- translated count
- status
- last updated
- next action

Actions: `Mở dự án`, `Dịch tiếp`, `Kiểm tra bản dịch`, `Xuất file`, `Xem chi tiết kỹ thuật`.

## Create project wizard

Use a step-by-step wizard:

1. Chọn file truyện
2. Kiểm tra chương
3. Nhận diện tên riêng & thuật ngữ
4. Dịch thử
5. Kiểm tra chất lượng
6. Dịch hàng loạt
7. Xuất file

Supported user inputs: `.txt`, `.epub`, `.zip` if backend supports it, and folders if backend supports them.

## Translate page

Show simple presets:

- `Dịch thử 1 chương`
- `Dịch thử 3 chương`
- `Dịch thử 10 chương`
- `Dịch 20 chương`
- `Dịch 50 chương`
- `Tiếp tục từ chỗ dừng`

Advanced command/flag preview must be hidden under `Xem chi tiết kỹ thuật`.

## Review page

Required for manual review and learning from edits.

Show:

- review queue
- source text pane
- current translation pane
- editable reviewed output pane
- warning badges

Buttons:

- `Lưu chỉnh sửa`
- `Lưu & cho hệ thống học`
- `Không học, chỉ lưu bản dịch`
- `Đánh dấu đã kiểm tra`
- `Bỏ qua`
- `Xem chi tiết kỹ thuật`

Learning from edits must create project-scoped dictionary/memory candidates or audited auto-review bundles. It must not mutate global behavior blindly.

## Export page

Actions:

- `Xuất TXT`
- `Xuất EPUB` if supported, otherwise disabled with `Sắp hỗ trợ`
- `Xuất gói kiểm tra`
- `Mở thư mục kết quả` or `Sao chép đường dẫn`

## Settings page

Sections:

- Workspace path
- API/provider status, no raw key display
- LTP status and start/check instructions
- Advanced safe defaults

## Manga / Image tab

Placeholder only:

```text
Manga / Ảnh
Sắp ra mắt

Tính năng dự kiến:
- OCR
- nhận diện khung thoại
- thứ tự đọc
- dịch nội dung ảnh
- chèn chữ tiếng Việt
- xuất CBZ/PDF
```

No manga OCR/image logic in Phase 7.

## Empty and error states

Examples:

- No projects: `Bạn chưa có dự án nào. Hãy tạo dự án truyện đầu tiên.`
- No review items: `Không có bản dịch nào cần kiểm tra.`
- LTP missing: `LTP chưa chạy. Một số bước nhận diện tên riêng có thể chưa hoạt động.`
- Provider error: `Không kết nối được API dịch. Kiểm tra lại cấu hình API trong Cài đặt.`

## Visual density rule

Home and project pages must be low-density. Detailed tables are allowed only inside `Xem chi tiết kỹ thuật`, artifact viewer, or advanced mode.
