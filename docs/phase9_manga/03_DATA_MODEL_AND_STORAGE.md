# Phase 9 Data Model And Storage

## Storage Principle

SQLite stores compact metadata, state, IDs, provenance, and artifact paths. Large binary artifacts stay on disk.

## Existing Manga Tables

The current repository already has a manga skeleton with tables for pages, page artifacts, boxes, box versions, OCR results, translations, exports, and visual evidence. Phase 9 should preserve those names where possible and add compatible migrations rather than replacing the schema.

Existing table concepts to preserve:

- `manga_pages`
- `manga_page_artifacts`
- `manga_boxes`
- `manga_box_versions`
- `manga_ocr_results`
- `manga_box_translations`
- `manga_exports`
- `manga_visual_evidence`

## Required New Or Extended Tables

Phase 9 implementation should add or extend tables for:

- `manga_projects`: project slug, title, source language, target language, reading direction, content type, created time, updated time.
- `manga_import_runs`: import source type, source path hash, run ID, page count, errors, warnings.
- `manga_preprocess_runs`: normalized image records, OCR variant records, size policy, orientation policy.
- `manga_detection_runs`: adapter ID, detection settings, text region count, bubble count, confidence summary.
- `manga_reading_orders`: page ID, ordered box IDs, algorithm version, user-edited flag.
- `manga_cleaning_jobs`: mask paths, cleaning adapter ID, inpaint settings, output image path.
- `manga_typeset_jobs`: font settings, fit decisions, overflow status, rendered image path.
- `manga_visual_qa_reports`: blocker count, warning count, review package path.
- `manga_provider_usage`: provider/model route per translation call for final canary and rollout.
- `manga_human_review_notes`: reviewer notes, decisions, timestamps, and artifact references.

## Stable IDs

Stable IDs are required for resumability and auditability.

- `project_id`: stable generated project identifier.
- `page_id`: stable page identifier generated from import order plus image hash.
- `box_id`: stable text region identifier scoped by page and preserved through OCR, translation, cleaning, rendering, QA, and export.
- `run_id`: unique run identifier for each pipeline run or stage job.

Never derive user-facing identity only from database row IDs. Row IDs may exist internally, but artifacts and JSON must use stable IDs.

## Page Manifest

`page_manifest.json` is the source of truth for imported page order and page artifacts.

Required fields:

- `schema_version`
- `project_id`
- `project_slug`
- `run_id`
- `source_type`
- `source_label`
- `created_at`
- `pages`
- `page_count`
- `hash_algorithm`
- `warnings`

Each page entry requires:

- `page_id`
- `page_index`
- `display_name`
- `source_relpath`
- `image_hash`
- `width`
- `height`
- `format`
- `artifact_relpath`
- `excluded`
- `exclude_reason`

## Box Records

Each text or bubble region requires:

- `box_id`
- `page_id`
- `region_type`
- `bbox`
- `polygon` when available
- `source`
- `confidence`
- `language_hint`
- `orientation`
- `translatable`
- `manual_review_state`
- `created_by_stage`

`region_type` values must include `dialogue`, `caption`, `narration`, `sfx`, `sign`, `note`, and `unknown`.

## OCR Records

Each OCR result requires:

- `box_id`
- `ocr_run_id`
- `adapter_id`
- `adapter_version`
- `text`
- `confidence`
- `language_detected`
- `orientation_detected`
- `raw_output_artifact`
- `review_state`
- `created_at`

Corrections must be appended as new review/correction records and must not overwrite original OCR output.

## Translation Records

Each translation requires:

- `box_id`
- `translation_run_id`
- `source_text`
- `translated_text`
- `provider_type`
- `provider_name`
- `model`
- `fallback_used`
- `prompt_context_bundle_artifact`
- `dictionary_entries_artifact`
- `memory_bundle_artifact`
- `usage_artifact`
- `qa_state`

Approved rules must not be inserted into the prompt bundle.

## Artifact Root

Use this runtime artifact structure:

```text
artifacts/manga/<project_slug>/<run_id>/
  page_manifest.json
  import/
  preprocessing/
  detection/
  ocr/
  reading_order/
  translation/
  cleaning/
  rendering/
  qa/
  export/
  provider/
  human_review/
```

The structure is further defined in `09_ARTIFACT_CONVENTIONS.md`.

## Git Ignore Requirements

Runtime images and outputs must be ignored by git:

- `artifacts/manga/`
- project-local source image copies
- generated previews
- OCR crops
- masks
- cleaned pages
- rendered pages
- CBZ/PDF exports
- provider preflight artifacts with local endpoint metadata

Tiny synthetic fixtures may be committed only under test fixture paths with explicit names and no copyrighted material.

## Migration Requirements

Each implementation subphase that changes schema must:

- Add a new migration file.
- Add migration tests.
- Preserve existing MVP4A manga imports and manifests.
- Avoid destructive schema changes.
- Provide a rollback note in the final report even if automated rollback is not implemented.

