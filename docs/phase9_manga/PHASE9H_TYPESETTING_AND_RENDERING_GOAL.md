# PHASE9H Typesetting And Rendering Goal

## 1. Objective

Render Vietnamese translations into cleaned image regions with deterministic wrapping, fitting, font handling, and overflow detection.

## 2. Current Dependencies / Prerequisites

- Phase 9F translation output PASS.
- Phase 9G cleaning output PASS.
- Read `01_RESEARCH_SUMMARY.md` typesetting section.
- Confirm font and dependency policy.

## 3. What Must Be Implemented

- Typesetting service.
- Renderer adapter interface.
- Pillow-based MVP renderer.
- Font selection from system/user-configured fonts without committing third-party font files.
- Vietnamese text wrapping.
- Font-size stepping.
- Line-height and alignment controls.
- Optional stroke/shadow rendering settings.
- Horizontal rendering by default.
- Vertical rendering only as explicit advanced option if supported.
- Overflow detection and blocker reporting.
- Rendered page artifacts.

## 4. What Must Not Be Implemented

- Advanced stylized SFX redraw as PASS requirement.
- Paid font bundling.
- Committing font files without license approval.
- Silent overflow.
- Rewriting translations to fit without preserving review history.
- Cloud rendering.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `rendering/rendered_pages/`
- `rendering/typeset_decisions.json`
- `rendering/overflow_report.json`
- `rendering/rendering_summary.md`

Each typeset decision includes:

- `page_id`
- `box_id`
- `translated_text`
- `font_family`
- `font_size`
- `line_count`
- `alignment`
- `bbox`
- `overflow`
- `overflow_reason`
- `output_artifact`

## 6. Backend API Requirements

If backend is touched:

- Start rendering job.
- Read rendering progress.
- Read rendered page artifact.
- Read overflow report.
- Save render settings.
- Save manual text fit decision.

## 7. GUI Requirements

The GUI may activate the rendering part of:

`5. Chèn chữ vào ảnh`

User-facing behavior:

- Render current page.
- Show rendered preview.
- Show overflow warnings.
- Allow user to edit translation or fit settings for a box.
- Keep technical font metrics behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Render selected page.
- Render all pages.
- Export overflow report.
- Validate rendered outputs.

## 9. Tests Required

Add tests for:

- Vietnamese wrapping.
- Long word fit handling.
- Font-size stepping.
- Overflow blocker generation.
- Rendered output image creation.
- Original cleaned image unchanged.
- Missing font fallback.
- Stroke/shadow settings if implemented.
- Synthetic images only.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI rendering changes:

- Step 5 can render a synthetic page.
- Preview appears.
- Long Vietnamese text triggers overflow warning.
- User can adjust text or settings.
- No text overlaps controls.
- Technical metrics are hidden by default.

## 11. PASS Criteria

- Rendering tests pass.
- Rendered output exists.
- Overflow is detected and reported.
- Font handling does not require committed paid fonts.
- Source and cleaned artifacts are not overwritten.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- No usable local text rendering library is available.
- Font discovery cannot find any usable font and no fallback can be configured.
- Required cleaned image artifacts are missing.

Use `BLOCKED_RENDERER_LIBRARY`, `BLOCKED_FONT_CONFIG`, or `BLOCKED_CLEANED_ARTIFACTS`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if local rendering cannot be implemented without silently corrupting image outputs or violating font licensing constraints.

## 14. Security / Privacy Requirements

- Do not commit rendered copyrighted pages.
- Do not embed full user images in JSON.
- Do not commit font files unless license approved.
- Keep output local.

## 15. Production-Readiness Requirements

Rendering is production-foundation ready when outputs are deterministic and overflow is explicit. Final production PASS requires visual QA, export, and real provider rollout evidence.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Renderer used.
- Font policy.
- Overflow behavior.
- Artifact paths.

