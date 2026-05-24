# 12 — Manga Data Schema

## Minimal tables/entities

For manga MVPs, preserve rerunnable pipelines and manual corrections with stable entities and version tables.

## Required entities

```text
manga_pages
manga_page_artifacts
manga_boxes
manga_box_versions
manga_ocr_results
manga_box_translations
manga_cleaning_jobs
manga_typeset_jobs
manga_exports
manga_visual_evidence
```

## MVP4A minimal subset

For the first manga implementation, only require:

```text
manga_pages
manga_page_artifacts
manga_boxes
manga_box_versions
manga_visual_evidence
```

OCR/translation/clean/typeset tables can be added later.

## Entity notes

### manga_pages

Stores imported page records.

Key fields:

```text
page_id
project_id
chapter_id
page_index
source_kind
original_path
checksum_sha256
visual_phash
width
height
status
created_at
updated_at
```

### manga_page_artifacts

Stores artifacts generated from a page.

Artifact kinds:

```text
original
normalized
ocr_preprocess
detection_preprocess
preview
crop
mask
clean
typeset
export
```

### manga_boxes

Stable logical box object.

Key fields:

```text
box_id
page_id
stable_key
current_version_id
canonical_type
deleted
created_at
updated_at
```

### manga_box_versions

Versioned geometry and metadata for a box.

Key fields:

```text
version_id
box_id
revision_no
bbox_json
polygon_json
mask_artifact_id
box_type
text_direction
reading_order
speaker_id
origin
detector_name
detector_version
detector_confidence
previous_version_id
change_reason
changed_by
created_at
```

### manga_ocr_results

Stores repeatable OCR runs and user-corrected OCR.

Key fields:

```text
ocr_result_id
box_id
box_version_id
engine_name
engine_version
input_artifact_id
raw_text
normalized_text
language_hint
script_guess
confidence
warnings_json
origin
created_at
```

### manga_box_translations

Stores translations mapped to stable box IDs.

Key fields:

```text
translation_id
box_id
source_ocr_result_id
context_window_json
memory_bundle_ref
model_name
provider_name
prompt_hash
raw_output_json
translated_text
reviewer_status
qa_flags_json
approved
created_at
```

## Design rules

- Never overwrite manual box edits silently.
- Every OCR run can be repeated.
- Every translation run can be repeated.
- Cleaning/typesetting outputs are artifacts, not canonical truth.
- User corrections must be auditable and promotable into LAMM-T memory.
