# Novel Translator Studio — Phase 3 Manga Update

Bộ này là **patch docs/spec** cho workspace Codex đã tạo ở Phase 2.

## Cách đặt vào workspace

Giải nén file zip này **ngay trong thư mục root của workspace** — tức thư mục có:

```text
AGENTS.md
docs/
config/
.agents/
```

Ví dụ workspace của bạn là:

```text
C:\Users\Admin\Desktop\NovelTranslatorStudio_Codex_Phase2_Setup
```

thì giải nén các thư mục `docs/` và `scripts/` của zip này vào đúng folder đó.

## Cần làm sau khi giải nén

1. Đảm bảo có file:

```text
docs/raw/phase3-manga-research.md
docs/specs/11_MANGA_ARCHITECTURE_PHASE3.md
docs/specs/12_MANGA_DATA_SCHEMA.md
docs/specs/13_MANGA_CLI_SPEC.md
docs/specs/14_MANGA_MVP_PLAN.md
```

2. Mở `AGENTS_PHASE3_APPEND.md`, copy phần nội dung trong đó và dán xuống cuối file `AGENTS.md` hiện có.

3. Hiện tại **không yêu cầu Codex implement manga ngay**. Scope đang ưu tiên MVP0/MVP1. Phase 3 chỉ được đưa vào workspace để giữ kiến trúc manga đúng khi tới MVP4.

## Thứ tự triển khai vẫn giữ

```text
MVP0: Skeleton + CLI + SQLite + config + mock provider
MVP1: Text import + memory core tối giản
MVP2: Correction learning
MVP3: Compact plugin export
MVP4A: Manga data foundation + manifest
MVP4B: OCR import/edit + translate by box ID
MVP4C: Simple clean/typeset
MVP5: GUI desktop/canvas
```
