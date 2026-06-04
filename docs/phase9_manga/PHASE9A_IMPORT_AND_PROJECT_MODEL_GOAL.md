# PHASE9A Import And Project Model Goal

## 1. Objective

Harden the manga/image project foundation so every later Phase 9 stage has stable project IDs, page IDs, page order, image hashes, artifact directories, and a durable page manifest.

This phase may extend the existing MVP4A manga import skeleton, but it must not implement OCR, detection, translation, cleaning, rendering, export production, or GUI review flows.

## 2. Current Dependencies / Prerequisites

- Read `docs/phase9_manga/00_PHASE9_OVERVIEW.md`.
- Read `docs/phase9_manga/03_DATA_MODEL_AND_STORAGE.md`.
- Read `docs/phase9_manga/09_ARTIFACT_CONVENTIONS.md`.
- Inspect existing `packages/nts_core/manga.py`, manga migrations, and `tests/test_mvp4a.py`.
- Preserve existing folder and CBZ import behavior unless a test proves it is incorrect.

## 3. What Must Be Implemented

- Manga/image project creation with stable `project_id` and `project_slug`.
- Import from image folder.
- Import from single image for canary use.
- Import from CBZ/ZIP with deterministic page order.
- PDF scan import only if a safe optional local adapter is already available; otherwise document `BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED` and keep the rest PASS-capable.
- Page manifest generation with page order, dimensions, format, hashes, source labels, artifact paths, warnings, and schema version.
- Artifact root creation under `artifacts/manga/<project_slug>/<run_id>/`.
- Image hashing with a documented algorithm.
- Duplicate page detection as warning, not automatic deletion.
- Non-copyrighted synthetic fixtures for tests.

## 4. What Must Not Be Implemented

- Text detection.
- Bubble detection.
- OCR.
- Translation.
- Cleaning or inpainting.
- Typesetting or rendering.
- GUI review screens beyond a first-step placeholder if the existing GUI requires one.
- Real provider calls.
- Cloud upload.
- Copyrighted fixtures.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `page_manifest.json`
- `import/import_summary.md`
- `import/import_warnings.json`

Required manifest fields are defined in `03_DATA_MODEL_AND_STORAGE.md`.

If schema changes are required, add a migration without destructively changing existing MVP4A tables.

## 6. Backend API Requirements

If the GUI backend is touched, expose or prepare endpoints for:

- Create manga project.
- Import source.
- Read project import status.
- Read page manifest.
- List imported pages.

All responses must use relative artifact references and must not expose user secrets.

## 7. GUI Requirements

The Manga/Image tab may show only the first wizard step:

`1. Chọn ảnh/CBZ/PDF`

The remaining wizard steps must stay disabled or explicitly labeled placeholder until their subphase is implemented.

Visible first-step controls:

- Select image folder.
- Select CBZ/ZIP.
- Select single image.
- Select PDF only if PDF import is actually available.
- Create/import project.

## 8. CLI Requirements If Applicable

Add or harden CLI commands for:

- Import image folder.
- Import single image.
- Import CBZ/ZIP.
- Export or print page manifest path.

CLI output must be concise and must not print full user image contents or raw provider config.

## 9. Tests Required

Add tests for:

- Image folder import.
- Single image import.
- CBZ/ZIP import.
- Deterministic page order.
- Manifest schema and required fields.
- Stable page IDs across repeat import of unchanged inputs where design supports it.
- Hash algorithm recorded.
- Duplicate warning.
- Empty source failure.
- Unsupported extension warning.
- Artifact directory creation.
- No copyrighted fixtures.

Tests must use tiny synthetic images.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required only if GUI code changes in this phase:

- Manga/Image tab loads.
- Step 1 label is `Chọn ảnh/CBZ/PDF`.
- Later steps are disabled or explicitly placeholder.
- Import button is wired.
- Import progress/status appears.
- Manifest page count appears.
- No API key or provider secret is visible.

## 11. PASS Criteria

- All required import tests pass.
- Manifest exists and validates.
- Artifact directories match `09_ARTIFACT_CONVENTIONS.md`.
- Existing MVP4A import tests still pass.
- No production text-novel behavior changes.
- No copyrighted image is committed.
- PDF import is either implemented through an approved local optional adapter or explicitly reported as not enabled.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Existing manga schema is incompatible with stable manifest creation and migration cannot be safely designed in the current turn.
- Filesystem permissions prevent writing workspace artifacts.
- PDF import was required by the active goal but no acceptable local PDF rasterization dependency is available.

Use a concrete code such as `BLOCKED_SCHEMA_MIGRATION`, `BLOCKED_FILESYSTEM`, or `BLOCKED_PDF_IMPORT_ADAPTER_NOT_CONFIGURED`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only after repeated attempts show the import/project model cannot be implemented without violating Phase 9 constraints or breaking existing Phase 5/6/7 behavior.

Do not mark FAIL for ordinary test failures before fixing attempts.

## 14. Security / Privacy Requirements

- Do not copy source images outside the workspace artifact area.
- Do not log full image bytes.
- Do not commit user images.
- Do not include API keys or provider config.
- Treat local source paths as user-sensitive in public summaries.

## 15. Production-Readiness Requirements

This phase is not production-ready for manga translation. It is production-foundation ready only when manifests are stable, imports are reproducible, and artifacts are local and resumable.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Import sources supported.
- Manifest path and schema version.
- What remains intentionally unimplemented.
- Risks and follow-up tasks.

