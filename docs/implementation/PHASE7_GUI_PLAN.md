# Phase 7 GUI Plan

## Goal

Build **NTS Studio** as a simplified local end-user GUI for text-novel translation, backed by existing NTS core/CLI behavior and safe LAMM-T constraints.

## Source Documents

- `docs/goals/PHASE7_GUI_IMPLEMENTATION_GOAL.md`
- `docs/gui/PHASE7_GUI_USER_FRIENDLY_UI_SPEC.md`
- `docs/gui/PHASE7_GUI_BUTTON_FUNCTION_WIRING.md`
- `docs/gui/PHASE7_GUI_CODEX_SKILL_USAGE.md`

## Architecture

- Backend: `apps/gui/backend/nts_gui_backend/`, a dependency-light local Python API wrapper.
- Frontend: `apps/gui/frontend/`, a static dark-slate guided interface served by the backend.
- Core boundary: frontend never duplicates translation business logic; backend records or wraps project/core actions.
- Safety boundary: approved rules remain verifier-only; raw NLP cache is never injected; provider status never returns raw key values.

## Pages

1. Trang chủ
2. Dự án truyện
3. Dịch truyện
4. Kiểm tra bản dịch
5. Xuất file
6. Cài đặt
7. Manga / Ảnh — Sắp ra mắt

## Button Wiring Policy

Every visible button is mapped to one of:

- frontend-only navigation/state
- local API route
- existing core-backed action record
- explicit placeholder with disabled or Coming Soon copy

The canonical backend registry is `VISIBLE_BUTTON_WIRING` in `apps/gui/backend/nts_gui_backend/service.py`.

## Safety Defaults

Production translation payloads force:

```json
{
  "safe_profile": true,
  "use_approved_dictionary": true,
  "use_approved_memory": true,
  "emit_prompt_artifacts": true,
  "resumable": true,
  "use_approved_rules": false,
  "inject_raw_nlp_cache": false
}
```

## Tests

Phase 7 focused tests live in `tests/test_phase7_gui.py` and cover health/status, project listing, LTP status, API key redaction, safe payloads, button wiring, review save-vs-learn behavior, bounded artifact previews, unsupported export states, and manga placeholder-only behavior.
