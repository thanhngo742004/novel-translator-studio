# PHASE9C Text And Bubble Detection Goal

## 1. Objective

Add adapter-backed detection for text regions and, where available, speech bubble regions, producing stable box records that downstream OCR, reading order, translation, cleaning, rendering, and QA can reference.

## 2. Current Dependencies / Prerequisites

- Phase 9A manifest exists.
- Phase 9B preprocessing exists.
- Read `01_RESEARCH_SUMMARY.md` sections on CRAFT, DBNet, comic text detectors, Manga109 annotations, and detection tradeoffs.
- Read `02_ARCHITECTURE.md` adapter interface requirements.

## 3. What Must Be Implemented

- Detection adapter interface.
- Mock detection adapter for tests.
- Manual/imported boxes path preserved.
- Text region boxes with `box_id`.
- Speech bubble boxes if the selected adapter can provide them.
- Region type values: `dialogue`, `caption`, `narration`, `sfx`, `sign`, `note`, `unknown`.
- Confidence scores.
- Orientation hints when available.
- Source attribution: manual, imported, local_adapter, cloud_adapter.
- Detection artifacts and Markdown summary.
- Merge policy for manual boxes and adapter boxes without overwriting manual edits.

## 4. What Must Not Be Implemented

- OCR.
- Translation.
- Cleaning/inpainting.
- Typesetting.
- Automatic speaker detection.
- Mandatory cloud detection.
- Vendoring GPL/AGPL detector projects into core.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `detection/regions.json`
- `detection/bubbles.json` when bubble detection is available.
- `detection/boxes_merged.json`
- `detection/detection_summary.md`

Each detected region requires:

- `page_id`
- `box_id`
- `region_type`
- `bbox`
- `polygon` when available.
- `confidence`
- `orientation`
- `source`
- `adapter_id`
- `review_state`

## 6. Backend API Requirements

If backend is touched:

- Start detection job.
- Read detection progress.
- Read boxes for a page.
- Update a box.
- Create a manual box.
- Delete or mark a box ignored without hard-deleting audit history.

## 7. GUI Requirements

The GUI may activate the detection portion of:

`2. Nhận diện chữ`

If review UI is touched, users must be able to:

- See detected boxes over a page preview.
- Mark a box as wrong.
- Add a missing box.
- Change region type.
- Save edits.

Technical adapter output remains behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Run detection.
- Import boxes JSON.
- Export boxes JSON.
- List boxes by page.

CLI must preserve stable box IDs and report invalid missing/extra IDs.

## 9. Tests Required

Add tests for:

- Mock detector output converted to boxes.
- Stable box IDs generated.
- Manual box import preserved.
- Adapter boxes do not overwrite manual edits.
- SFX/outside-bubble region types.
- Confidence summary.
- Detection artifacts created.
- Invalid box coordinates rejected.
- No cloud/network calls in tests.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI detection/review is touched:

- Step 2 can run detection.
- Page preview displays boxes.
- User can add a box.
- User can change region type.
- User can save edits.
- Low-confidence boxes are visible.
- Technical details are hidden by default.

## 11. PASS Criteria

- Mock detection tests pass.
- Detection artifacts exist.
- Boxes have stable IDs.
- Manual boxes are preserved.
- Detection can be rerun without destroying review history.
- No required cloud dependency.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Existing box schema cannot represent required stable region metadata.
- No safe path exists to preserve manual edits.
- GUI-required box editing cannot be implemented because the current GUI surface is absent and the active goal requires GUI completion.

Use `BLOCKED_BOX_SCHEMA`, `BLOCKED_MANUAL_EDIT_PRESERVATION`, or `BLOCKED_GUI_SURFACE`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if stable detection boxes cannot be integrated without breaking existing manga import/manifest behavior.

## 14. Security / Privacy Requirements

- Detection runs local by default.
- Cloud detector adapters require explicit opt-in.
- Do not log image bytes.
- Do not commit detector outputs from copyrighted pages.
- Redact adapter credentials.

## 15. Production-Readiness Requirements

Detection is production-foundation ready when outputs are reviewable, confidence-scored, and stable by box ID. It is not production PASS until later OCR, translation, rendering, QA, and real provider gates pass.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Detection adapters available.
- Artifacts created.
- Box schema changes.
- Known detection limitations.

