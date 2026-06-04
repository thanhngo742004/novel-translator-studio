# PHASE9K GUI Manga Tab Goal

## 1. Objective

Activate the Manga/Image tab as a guided, end-user friendly workflow that integrates the completed Phase 9A-J backend stages without exposing technical complexity or secrets.

## 2. Current Dependencies / Prerequisites

- Phase 9A-J backend stages PASS or explicitly accepted as available.
- Existing Phase 7 GUI hardening patterns inspected.
- Read `04_GUI_INTEGRATION_PLAN.md`.
- Read `web-design-guidelines`, `minimalist-ui`, and `design-taste-frontend` directions if GUI code is being modified.

## 3. What Must Be Implemented

- Active Manga/Image tab.
- Wizard flow with exact labels:
  1. `Chọn ảnh/CBZ/PDF`
  2. `Nhận diện chữ`
  3. `Kiểm tra OCR`
  4. `Dịch thử`
  5. `Chèn chữ vào ảnh`
  6. `Kiểm tra hình`
  7. `Xuất CBZ/PDF`
- Progress panel backed by job artifacts.
- Page preview and artifact viewer.
- OCR correction screen.
- Reading order controls if implemented in backend.
- Translation preview.
- Cleaning/rendering preview.
- QA blocker panel.
- Export panel.
- Provider/settings integration with redaction.
- `Xem chi tiết kỹ thuật` disclosure for JSON, paths, adapter diagnostics, and provider diagnostics.
- Every visible button wired or explicitly placeholder.

## 4. What Must Not Be Implemented

- New unrelated GUI redesign.
- Decorative landing page.
- Industrial/brutalist/brand-heavy visual direction.
- Secret display.
- Cloud upload without explicit configuration and user confirmation.
- Text-novel workflow changes.

## 5. Data Model / Artifact Requirements

GUI must read from existing artifacts rather than inventing parallel state:

- `page_manifest.json`
- preprocessing summary.
- detection boxes.
- OCR results and corrections.
- reading order.
- translations.
- cleaned/rendered outputs.
- QA report.
- export manifest.
- provider preflight artifacts when available.

Frontend state must not store raw API keys.

## 6. Backend API Requirements

Complete or verify endpoints for:

- Manga project import/status.
- Stage job start and progress polling.
- Page and artifact retrieval.
- Box read/update.
- OCR correction save.
- Reading order save.
- Translation preview start/status.
- Cleaning/rendering start/status.
- QA start/status.
- Export start/status.
- Open output folder.
- Redacted provider status.

## 7. GUI Requirements

The UI must be:

- Quiet and utilitarian.
- Keyboard reachable.
- Clear on stage prerequisites.
- Readable at desktop and mobile-like widths.
- Stable in layout while progress updates.
- Clear about blocked stages.
- Simple for end users, with technical detail hidden by default.

Do not use visible instructional paragraphs that explain the whole app. Use labels, statuses, and focused controls.

## 8. CLI Requirements If Applicable

No new CLI behavior is required unless backend gaps are discovered. Do not change CLI output format unnecessarily.

## 9. Tests Required

Add or update tests for:

- Backend GUI endpoints.
- Button wiring registry or route coverage where available.
- Redacted provider response.
- Manga tab status response.
- Stage prerequisite logic.
- No API key exposure.
- Artifact references returned safely.

Frontend tests should cover the wizard labels and disabled/enabled state if the repo has frontend test infrastructure.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required:

- Manga/Image tab loads.
- All seven wizard labels are present.
- Step 1 import can be started.
- Progress panel updates.
- OCR review can save a correction.
- Translation preview can run with configured mock or local test mode.
- Cleaning/rendering preview appears for synthetic fixture.
- QA blocker panel appears.
- Export panel shows artifact paths.
- `Xem chi tiết kỹ thuật` toggles technical details.
- Every visible button is wired or labeled placeholder.
- No API key appears in DOM, network response, screenshots, or logs.
- Layout has no overlapping controls on desktop and mobile-like viewport.

## 11. PASS Criteria

- GUI endpoint tests pass.
- Manual/browser smoke checklist passes.
- Manga/Image tab is active and guided.
- Visible buttons are wired or explicit placeholders.
- Provider secrets are redacted.
- Existing text-novel GUI behavior is unchanged.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Current GUI source is unavailable in the workspace.
- Existing backend cannot expose required artifact-backed status.
- Browser smoke cannot run due environment failure after the implementation is otherwise complete.

Use `BLOCKED_GUI_SOURCE_UNAVAILABLE`, `BLOCKED_BACKEND_STATUS`, or `BLOCKED_BROWSER_ENVIRONMENT`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if the Manga/Image tab cannot be made usable without a major unrelated GUI rewrite or breaking Phase 7 text-novel behavior.

## 14. Security / Privacy Requirements

- Redact provider secrets.
- Do not embed full user images in frontend JSON.
- Use local artifact URLs or safe backend routes.
- Cloud adapter controls must show explicit opt-in state.
- Technical details must not expose secrets.

## 15. Production-Readiness Requirements

The GUI is production-ready for Phase 9 only after Phase 9L and 9M real-provider gates pass. This phase makes the GUI workflow usable and smoke-tested, but not production-validated.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Browser smoke result.
- Buttons wired/placeholders.
- Redaction evidence.
- Known GUI limitations.

