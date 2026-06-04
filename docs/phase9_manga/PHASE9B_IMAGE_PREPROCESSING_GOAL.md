# PHASE9B Image Preprocessing Goal

## 1. Objective

Create a local, deterministic preprocessing stage that prepares imported manga/image pages for detection and OCR without modifying the original imported artifacts.

## 2. Current Dependencies / Prerequisites

- `PHASE9A_IMPORT_AND_PROJECT_MODEL_GOAL.md` must be PASS or accepted as equivalent.
- Page manifest must exist.
- Artifact root must exist.
- Read `01_RESEARCH_SUMMARY.md` sections on OCR, preprocessing, and local/offline tradeoffs.

## 3. What Must Be Implemented

- Preprocessing service that reads `page_manifest.json`.
- Normalized working image copies.
- Format normalization policy, preferably PNG for working images unless the repo chooses a documented alternative.
- EXIF orientation handling where applicable.
- Size policy with no destructive overwrite of originals.
- Grayscale variants for OCR.
- Threshold or contrast variants for OCR where useful.
- Preview-safe thumbnails for GUI/human review.
- Stage summary artifacts.
- Rerun behavior that preserves previous stage artifacts unless `--force` is requested.

## 4. What Must Not Be Implemented

- Text detection.
- OCR.
- Translation.
- Cleaning/inpainting of source text.
- Typesetting.
- Cloud image processing.
- Destructive edits to imported source images.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `preprocessing/preprocess_manifest.json`
- `preprocessing/preprocess_summary.md`
- `preprocessing/pages/`
- `preprocessing/ocr_variants/`
- `preprocessing/previews/`

Each preprocessed page record must include:

- `page_id`
- `source_artifact`
- `normalized_artifact`
- `ocr_variant_artifacts`
- `preview_artifact`
- `width`
- `height`
- `format`
- `orientation_applied`
- `warnings`

## 6. Backend API Requirements

If backend endpoints are touched:

- Start preprocessing job.
- Read preprocessing progress.
- Read preprocessing summary.
- Read preview artifact reference.

Responses must not include image bytes inline.

## 7. GUI Requirements

The GUI may show preprocessing as internal progress inside `2. Nhận diện chữ`.

The user-facing surface should show:

- Pages queued.
- Pages processed.
- Warnings count.
- Preview thumbnails after completion.

Technical details stay behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden CLI command:

- `nts manga preprocess <project/run>` or equivalent existing command pattern.

CLI must print artifact paths and counts, not full image data.

## 9. Tests Required

Add tests for:

- Preprocess manifest creation.
- Normalized copy creation.
- Original image unchanged.
- Grayscale variant creation.
- Threshold/contrast variant creation if implemented.
- Preview thumbnail creation.
- EXIF orientation behavior if fixture support exists.
- Rerun without destructive overwrite.
- Synthetic images only.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required only if GUI changes:

- Step 2 can start preprocessing or includes preprocessing before detection.
- Progress updates.
- Previews render.
- `Xem chi tiết kỹ thuật` hides technical artifact paths by default.
- No layout overlap at desktop and mobile-like widths.

## 11. PASS Criteria

- Preprocessing tests pass.
- Preprocess artifacts exist for every imported page.
- Original imported artifacts remain unchanged.
- Stage summary exists.
- Existing Phase 5/6/7 and MVP4A behavior remains unchanged.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Required image library is unavailable and no standard-library fallback can meet the phase objective.
- Existing manifest is missing required page image references.
- Filesystem permissions prevent writing preprocess artifacts.

Use `BLOCKED_IMAGE_LIBRARY`, `BLOCKED_MANIFEST_INCOMPLETE`, or `BLOCKED_FILESYSTEM`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only when deterministic local preprocessing cannot be implemented without violating local-first storage or damaging source artifacts.

## 14. Security / Privacy Requirements

- Keep all images local.
- Do not upload images.
- Do not embed image bytes in JSON.
- Ignore generated previews and variants in git.
- Do not log full local paths in public-ready summaries.

## 15. Production-Readiness Requirements

This phase is production-foundation ready when preprocessing is deterministic, resumable, and artifact-backed. It is not a complete production pipeline.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Preprocessing variants created.
- Artifact paths.
- Known limitations.
- Follow-up tasks for detection/OCR.

