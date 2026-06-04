# PHASE9J Export CBZ PDF Goal

## 1. Objective

Export reviewed rendered pages as an image folder, deterministic CBZ, and PDF when the PDF export adapter is available and approved.

## 2. Current Dependencies / Prerequisites

- Phase 9H rendered pages PASS.
- Phase 9I visual QA PASS or accepted warnings with no blockers.
- Read `08_DEPENDENCY_EVALUATION.md` export rows.
- Read `09_ARTIFACT_CONVENTIONS.md`.

## 3. What Must Be Implemented

- Export service.
- Image folder export.
- CBZ export using Python `zipfile`.
- Deterministic page filenames and order.
- Export manifest.
- Export summary.
- PDF export through optional adapter if dependency is approved.
- PDF unavailable status if no approved adapter is present.
- Export validation for page count and order.

## 4. What Must Not Be Implemented

- CBR/RAR export.
- Cloud export.
- Polished print-layout PDF beyond image-page PDF.
- Bypassing QA blockers.
- Committing exported copyrighted outputs.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `export/export_manifest.json`
- `export/export_summary.md`
- `export/images/`
- `export/cbz/<project_slug>.cbz`
- `export/pdf/<project_slug>.pdf` when enabled.

Export manifest includes:

- `project_id`
- `run_id`
- `page_count`
- `source_rendered_pages`
- `image_export_paths`
- `cbz_path`
- `pdf_path`
- `pdf_status`
- `qa_report_ref`
- `warnings`

## 6. Backend API Requirements

If backend is touched:

- Start export job.
- Read export progress.
- Read export manifest.
- Open export folder.
- Download or serve local artifact references if existing GUI supports it.

## 7. GUI Requirements

The GUI may activate:

`7. Xuất CBZ/PDF`

Required behavior:

- Export buttons disabled while QA blockers exist.
- Image folder export button.
- CBZ export button.
- PDF export button only enabled when adapter is available.
- Open output folder.
- Show export manifest summary.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Export image folder.
- Export CBZ.
- Export PDF.
- Validate export manifest.

## 9. Tests Required

Add tests for:

- Image folder export.
- CBZ file creation.
- CBZ page order.
- Export manifest fields.
- QA blocker prevents export unless explicit unsafe override exists and is not default.
- PDF adapter unavailable behavior.
- PDF export if adapter is available in test environment.
- Synthetic images only.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI export changes:

- Step 7 opens only after QA.
- Export disabled with blockers.
- Image folder export works.
- CBZ export works.
- PDF button correctly reflects availability.
- Open output folder opens export location.
- No secrets visible.

## 11. PASS Criteria

- Export tests pass.
- Image folder export exists.
- CBZ export exists.
- Export manifest exists.
- PDF export is created or explicitly reported unavailable.
- Export page count and order validate.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Rendered pages are missing.
- QA blockers exist and the active goal requires clean export.
- Filesystem prevents writing export artifacts.

Use `BLOCKED_RENDERED_PAGES`, `BLOCKED_QA_BLOCKERS`, or `BLOCKED_FILESYSTEM`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if deterministic local export cannot be implemented without committing user artifacts or bypassing QA blockers.

## 14. Security / Privacy Requirements

- Do not commit exports.
- Keep outputs local.
- Do not upload exported files.
- Do not write API keys to export manifests.
- Do not include full copyrighted text dumps in summaries.

## 15. Production-Readiness Requirements

Export is production-foundation ready when image and CBZ export are deterministic and PDF export has a documented availability status. Production PASS waits for Phase 9M rollout gates.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Export formats supported.
- Export artifact paths.
- PDF availability.
- QA status.

