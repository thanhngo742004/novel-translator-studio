# Novel Translator Studio — Codex Phase 2 Setup

Bộ này dùng để chuẩn bị workspace Codex sau khi đã có:

- Phase 1: LAMM-T memory method
- Phase 2: kiến trúc app desktop + CLI

Mục tiêu hiện tại **không phải code toàn bộ app**, mà là để Codex scaffold **MVP0** an toàn:

1. repo/package skeleton
2. CLI `nts`
3. workspace init
4. SQLite migration setup
5. task/model run tracking
6. config loader
7. provider validation + mock provider
8. test harness tối thiểu

## Cách dùng nhanh

Giải nén zip, mở terminal tại thư mục này:

```bash
codex --cd . --model gpt-5.5
```

Prompt đầu tiên nên dùng:

```text
Read AGENTS.md and docs/specs/10_MVP_IMPLEMENTATION_PLAN.md. Also read docs/raw/phase1-memory-method.md and docs/raw/phase2-architecture.md if needed. Do not implement the full app. Create a short plan for MVP0 only and list the files you propose to create or modify. Wait for my approval before editing.
```

Sau khi Codex đưa plan MVP0, prompt tiếp theo mới cho phép code:

```text
Implement MVP0 only, exactly following docs/codex-prompts/MVP0_IMPLEMENTATION_PROMPT.md. Do not implement translation, manga, GUI, plugin export, or real provider calls.
```

## Thứ tự làm việc khuyến nghị

1. Chạy Codex ở chế độ Read-only hoặc Auto, không Full Access.
2. Bắt Codex đọc `AGENTS.md`.
3. Bắt Codex chỉ plan MVP0.
4. Duyệt plan.
5. Mới cho scaffold MVP0.
6. Chạy tests.
7. Sau khi MVP0 ổn, mới chuyển sang MVP1.

## Không làm ở giai đoạn này

- Không làm GUI desktop.
- Không làm manga OCR/inpainting/typeset tự động.
- Không làm cloud/server/multi-user.
- Không fine-tune model.
- Không để plugin VBook tự học.
- Không hard-code API key/model/provider.
