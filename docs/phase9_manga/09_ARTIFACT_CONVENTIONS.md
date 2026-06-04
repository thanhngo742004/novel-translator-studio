# Phase 9 Artifact Conventions

## Root Structure

Each manga/image run writes artifacts under:

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

## Naming Rules

- Use stable `project_slug`, `run_id`, `page_id`, and `box_id`.
- Use zero-padded page indexes in filenames for human readability.
- Use JSON for machine-readable artifacts.
- Use Markdown for human summaries.
- Use images only for local previews and outputs.
- Do not store base64 images inside JSON.
- Store relative artifact paths where possible.

## Required Artifact Categories

### 1. Page Manifest

- `page_manifest.json`
- `import/import_summary.md`
- `import/import_warnings.json`

### 2. Region And Bubble Boxes

- `detection/regions.json`
- `detection/bubbles.json`
- `detection/boxes_merged.json`
- `detection/detection_summary.md`

### 3. OCR Output

- `ocr/ocr_results.json`
- `ocr/page_<index>_ocr.json`
- `ocr/ocr_summary.md`

### 4. OCR Confidence Report

- `ocr/ocr_confidence_report.json`
- `ocr/ocr_confidence_report.md`

### 5. OCR Review And Corrections

- `ocr/ocr_corrections.jsonl`
- `ocr/ocr_review_summary.md`

### 6. Reading Order

- `reading_order/reading_order.json`
- `reading_order/reading_order_graph.json`
- `reading_order/reading_order_summary.md`

### 7. Translation Context Bundle

- `translation/prompt_context_bundle.json`
- `translation/box_translation_requests.jsonl`
- `translation/translation_results.json`
- `translation/translation_summary.md`

The context bundle must not include approved rules in prompts or raw NLP cache.

### 8. Provider And Model Usage

- `provider/provider_preflight.json`
- `provider/provider_preflight.md`
- `provider/model_policy_snapshot.json`
- `provider/model_usage.jsonl`

Secrets must be redacted.

### 9. Cleaning And Inpainting Masks

- `cleaning/masks/`
- `cleaning/cleaned_pages/`
- `cleaning/cleaning_jobs.json`
- `cleaning/cleaning_summary.md`

### 10. Rendered Page Outputs

- `rendering/rendered_pages/`
- `rendering/typeset_decisions.json`
- `rendering/overflow_report.json`
- `rendering/rendering_summary.md`

### 11. Visual QA

- `qa/visual_qa_report.json`
- `qa/visual_qa_report.md`
- `qa/page_review_index.json`
- `qa/blockers.json`

### 12. Export Manifest

- `export/export_manifest.json`
- `export/export_summary.md`
- `export/images/`
- `export/cbz/`
- `export/pdf/`

### 13. Human Review Package

- `human_review/review_index.md`
- `human_review/review_index.html` when supported.
- `human_review/previews/`
- `human_review/review_notes.jsonl`

### 14. Final Rollout Report

- `final_rollout_report.json`
- `final_rollout_report.md`

## Redaction Rules

Artifacts must redact:

- API keys.
- Authorization headers.
- Provider secret query parameters.
- Local user home paths in public summaries where feasible.

Artifacts may include local relative paths under the workspace.

## Copyright Safety

Runtime artifacts may contain user images and OCR text. They must stay in ignored workspace directories and must not be copied into docs, issue templates, or committed fixtures.

