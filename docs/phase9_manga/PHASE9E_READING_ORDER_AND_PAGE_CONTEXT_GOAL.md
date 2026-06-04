# PHASE9E Reading Order And Page Context Goal

## 1. Objective

Add explicit, reviewable reading order for manga, manhua, manhwa, webtoon, and generic image projects, then build page context bundles that later translation can use safely.

## 2. Current Dependencies / Prerequisites

- Phase 9A manifest PASS.
- Phase 9C boxes PASS.
- Phase 9D OCR review foundation PASS.
- Read `01_RESEARCH_SUMMARY.md` reading order section.
- Preserve user corrections and manual box order edits.

## 3. What Must Be Implemented

- Reading direction project setting: right-to-left, left-to-right, top-to-bottom webtoon, manual.
- Deterministic initial reading order algorithm.
- User-adjustable reading order records.
- Duplicate/missing box validation.
- Page context bundle with ordered OCR text, page metadata, neighboring page hints when configured, and region types.
- Reading order summary artifacts.
- Manual override audit history.

## 4. What Must Not Be Implemented

- Translation.
- Automatic speaker detection.
- Full panel detection as a hard dependency.
- Multimodal model calls.
- Cloud layout analysis as a requirement.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `reading_order/reading_order.json`
- `reading_order/reading_order_graph.json`
- `reading_order/page_context_bundle.json`
- `reading_order/reading_order_summary.md`

Each reading order record includes:

- `page_id`
- `ordered_box_ids`
- `direction_preset`
- `algorithm_version`
- `user_edited`
- `warnings`
- `validation_status`

## 6. Backend API Requirements

If backend is touched:

- Read page reading order.
- Save reading order.
- Validate reading order.
- Read page context bundle.
- Update project reading direction.

## 7. GUI Requirements

If GUI is touched, the OCR review screen may include reading order controls:

- Numbered boxes on preview.
- Drag or move controls for order.
- Direction preset selector.
- Save order.
- Validate order.

Technical graph details stay behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Generate reading order.
- Export reading order.
- Import edited reading order.
- Validate reading order.

## 9. Tests Required

Add tests for:

- Right-to-left manga ordering.
- Left-to-right manhua/manhwa ordering.
- Top-to-bottom webtoon ordering.
- Manual order import.
- Missing box ID validation.
- Duplicate box ID validation.
- Ignored/not-translatable boxes behavior.
- Page context bundle creation.
- User edit audit record.

Tests must use synthetic box coordinates and mock OCR text.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI reading-order controls are touched:

- Order numbers appear over boxes.
- Direction preset changes initial order.
- User can move a box earlier/later.
- Save order persists.
- Duplicate/missing validation appears.
- Technical details are hidden by default.

## 11. PASS Criteria

- Reading order tests pass.
- Reading order artifact exists.
- Page context bundle exists.
- Duplicate and missing box IDs are blocked.
- User-edited order is preserved on rerun.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Stable box IDs are not available.
- Existing schema cannot preserve user-edited order.
- Required GUI reorder behavior cannot be implemented without an available preview surface.

Use `BLOCKED_BOX_IDS`, `BLOCKED_ORDER_SCHEMA`, or `BLOCKED_GUI_SURFACE`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if reading order cannot be represented or validated without breaking box ID stability.

## 14. Security / Privacy Requirements

- Context bundles are local artifacts.
- Do not include raw full copyrighted page text in console logs.
- Do not upload layout data.
- Do not include provider secrets.

## 15. Production-Readiness Requirements

This phase is production-foundation ready when reading order is deterministic, reviewable, and validated. Production translation readiness requires Phase 9F and final provider gates.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Direction presets supported.
- Artifact paths.
- Known reading order limitations.
- Follow-up tasks for translation context.

