# PHASE7_GUI_IMPLEMENTATION_GOAL

## Objective

Implement the Phase 7 GUI shell for Novel Translator Studio as **NTS Studio**, a simplified end-user app for translating long novels.

Use these docs as source of truth:

```text
docs/gui/PHASE7_GUI_USER_FRIENDLY_UI_SPEC.md
docs/gui/PHASE7_GUI_BUTTON_FUNCTION_WIRING.md
docs/gui/PHASE7_GUI_CODEX_SKILL_USAGE.md
```

## Required skills

Before designing or implementing, Codex must intentionally apply:

- `lamm-t-architect`
- `design-taste-frontend`
- `minimalist-ui`
- `web-design-guidelines`
- `full-output-enforcement`

Do not use `industrial-brutalism`, `brandkit`, `gpt-taste`, or high-end agency visual skills unless explicitly requested later.

## Current state

Phase 5 and Phase 6 are complete:

- multi-novel validation passed
- 20/50-chapter production scaling passed for Han Jue and Tien Nghich
- full tests reached 232 passed
- rules rendered count remained 0
- `--use-approved-rules` was not used
- raw NLP cache was not injected

Phase 7 must wrap the working text-novel pipeline in a GUI.

## Hard constraints

Do not:

- implement manga OCR/image processing
- expose API keys
- render approved rules into production prompts
- use `--use-approved-rules`
- weaken QA/evaluator/safety gates
- break existing CLI/core behavior
- duplicate core translation business logic in frontend
- load full 20MB novel files into frontend memory
- commit workspace data, copyrighted novels, `.env`, API keys, or heavy artifacts
- leave visible buttons unwired

Rules remain verifier-only / QA-only. Manga tab is placeholder only.

## Required navigation

```text
Trang chủ
Dự án truyện
Dịch truyện
Kiểm tra bản dịch
Xuất file
Cài đặt
Manga / Ảnh — Sắp ra mắt
```

Technical details must be hidden behind `Xem chi tiết kỹ thuật`.

## Required pages

Implement a functional GUI shell for:

1. Trang chủ
2. Dự án truyện
3. Dịch truyện
4. Kiểm tra bản dịch
5. Xuất file
6. Cài đặt
7. Manga / Ảnh — Sắp ra mắt

## Required workflows

- Home: system readiness, create project, continue project, open review queue.
- Projects: list/open projects, show simple progress/status.
- Translation: choose project/range/preset, run trial/batch, resume, view progress.
- Review: edit translation, save without learning, save and learn, mark reviewed.
- Export: export TXT/EPUB/review package, open/copy output path.
- Settings: show provider status without keys, LTP status, workspace path.
- Manga: Coming Soon only.

## Backend/API preference

Prefer:

```text
apps/gui/backend/
apps/gui/frontend/
```

Backend should be a lightweight local Python API wrapper around existing core/CLI functions. Do not duplicate translation business logic in frontend.

Required endpoint families:

- health/status
- project listing/import/scan
- LTP status
- validation/translation run start
- run status/artifact listing
- review queue
- save edit
- learn from edit
- export

## Button wiring

Use `docs/gui/PHASE7_GUI_BUTTON_FUNCTION_WIRING.md`.

Every visible button must have one of:

1. backend route
2. existing CLI/core action
3. frontend-only state transition
4. explicit placeholder/Coming Soon state

If Codex cannot wire a button, it must remove it or mark it disabled with explanation.

## Option UI

Do not show raw CLI flags to normal users.

Use:

- presets
- checkboxes
- advanced chips

`--use-approved-rules` must not be available as a normal option. If shown in technical details, it must be disabled and explained.

## Tests

No real provider/API calls in tests.

Run after changes:

```text
uv run --extra dev python -m pytest -q
```

If unavailable:

```text
python -m pytest -q
```

Add/update tests for:

- backend health/status
- project listing
- LTP status endpoint
- no API key exposure
- translation payload uses safe defaults
- `--use-approved-rules` is never sent
- GUI button wiring coverage
- raw NLP cache not injected
- review save vs learn behavior
- manga tab placeholder only
- artifact listing bounded preview behavior
- frontend build if frontend toolchain is introduced
- existing core/CLI tests still pass

## Documentation

Create/update:

```text
docs/implementation/PHASE7_GUI_PLAN.md
docs/implementation/PHASE7_GUI_STATUS.md
docs/implementation/NTS_NEXT_ACTIONS.md
```

## PASS criteria

PASS only if:

- full tests pass
- GUI backend starts or passes startup tests
- GUI frontend builds or passes available checks
- simplified end-user pages exist
- every visible button is wired or explicitly placeholder
- safe defaults are used
- API keys are not exposed
- approved rules are not used in prompts
- raw NLP cache not injected
- manga tab is placeholder only
- docs created/updated

## BLOCKED criteria

Report BLOCKED only if Python/Node toolchain, dependency install, server startup, workspace data, or filesystem/network/socket access is unavailable.

## Final report

Include PASS/BLOCKED, files changed, tests run, backend command, frontend command, implemented pages, button wiring coverage, security checks, manga placeholder confirmation, limitations, and next recommended phase.
