# PHASE9G Text Cleaning And Inpainting Goal

## 1. Objective

Add local-first cleaning and optional inpainting so original source text can be removed or covered before Vietnamese text rendering, while preserving masks, provenance, and reviewability.

## 2. Current Dependencies / Prerequisites

- Phase 9C detection boxes PASS.
- Phase 9F translation output available for fit planning.
- Read `01_RESEARCH_SUMMARY.md` cleaning and inpainting section.
- Confirm dependency policy from `08_DEPENDENCY_EVALUATION.md`.

## 3. What Must Be Implemented

- Cleaning adapter interface.
- Deterministic white/color fill cleaning mode.
- Mask generation from boxes/polygons.
- Optional OpenCV inpaint adapter if OpenCV is already approved or added as optional extra.
- Inpainting adapter boundary for later LaMa/cloud adapters.
- SFX handling policy: leave unchanged, translate as note, or manually mark for cleaning.
- Cleaning artifacts and summary.
- Rerun behavior that preserves manual overrides.

## 4. What Must Not Be Implemented

- Stylized SFX redraw as a PASS requirement.
- Mandatory neural inpainting.
- Mandatory cloud inpainting.
- Translation changes.
- Rendering/typesetting final text.
- Uploading user images by default.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `cleaning/masks/`
- `cleaning/cleaned_pages/`
- `cleaning/cleaning_jobs.json`
- `cleaning/cleaning_summary.md`

Each cleaning job includes:

- `page_id`
- `box_ids`
- `adapter_id`
- `mode`
- `mask_artifact`
- `input_image_artifact`
- `output_image_artifact`
- `warnings`
- `cloud_used`

## 6. Backend API Requirements

If backend is touched:

- Start cleaning job.
- Read cleaning progress.
- Read cleaned page artifact.
- Save cleaning mode per region.
- Mark SFX handling decision.

## 7. GUI Requirements

The GUI may activate the cleaning part of:

`5. Chèn chữ vào ảnh`

User-facing controls:

- Choose cleaning mode.
- Run cleaning for current page.
- Show before/after preview.
- Warn when SFX is unchanged or manually required.

Technical mask paths and adapter diagnostics stay behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Generate masks.
- Run fill cleaning.
- Run optional OpenCV inpaint.
- Export cleaning summary.

## 9. Tests Required

Add tests for:

- Mask creation from rectangular boxes.
- Mask creation from polygons if polygons are supported.
- Fill cleaning output image.
- Original image unchanged.
- OpenCV adapter skipped cleanly when dependency unavailable.
- SFX decision records.
- Cleaning artifacts created.
- No cloud calls in tests.

Tests must use synthetic images.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI cleaning controls are touched:

- Step 5 shows cleaning mode.
- Cleaning can run on a synthetic page.
- Before/after preview appears.
- SFX warning appears when relevant.
- Technical details are hidden by default.
- No image upload occurs unless explicitly configured.

## 11. PASS Criteria

- Cleaning tests pass.
- Fill cleaning works locally.
- Masks are saved.
- Cleaned page artifacts are saved.
- Original artifacts are not overwritten.
- Optional inpaint adapter is either working or cleanly reported unavailable.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Box geometry is unavailable.
- Required image library is unavailable and fill cleaning cannot be implemented.
- Filesystem prevents writing mask/cleaned artifacts.

Use `BLOCKED_BOX_GEOMETRY`, `BLOCKED_IMAGE_LIBRARY`, or `BLOCKED_FILESYSTEM`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if local deterministic cleaning cannot be implemented without destructive source image edits.

## 14. Security / Privacy Requirements

- Keep cleaning local by default.
- Cloud inpainting requires explicit opt-in.
- Do not log image bytes.
- Do not commit masks or cleaned pages from copyrighted sources.
- Redact cloud adapter credentials.

## 15. Production-Readiness Requirements

Cleaning is production-foundation ready when it produces auditable local outputs and clear warnings for unresolved SFX or complex backgrounds. Final production PASS requires visual QA and rollout gates.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Cleaning modes supported.
- Artifact paths.
- SFX limitations.
- Follow-up tasks for rendering.

