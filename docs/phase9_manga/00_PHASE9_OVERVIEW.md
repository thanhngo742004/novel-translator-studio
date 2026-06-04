# Phase 9 Manga/Image Translation Pipeline Overview

## Status

Phase 9 is planning-only until a future implementation goal starts from one of the `PHASE9*.md` goal files in this folder.

Current repository state relevant to Phase 9:

- Phase 5 text-novel validation is complete.
- Phase 6 production scaling is complete.
- Phase 7 GUI is nearly complete and remains in browser-smoke hardening.
- Manga/Image production is intentionally not implemented yet.
- The current manga surface is a limited MVP4A-style data layer and CLI skeleton for page import, stable box IDs, box import/export, and manifest export.

## Phase 9 End State

When all Phase 9 subphases pass, Novel Translator Studio supports a production-capable local-first manga/image translation pipeline:

1. Import image folder, CBZ/ZIP, a single image canary, and PDF scans where feasible.
2. Create a page manifest with stable page IDs, page order, hashes, dimensions, and artifact paths.
3. Preprocess page images into normalized working copies and OCR-friendly variants.
4. Detect text regions and speech bubble regions through adapter-backed services.
5. Run OCR through local-first adapters, with optional explicit cloud adapters.
6. Let users review and correct OCR before translation.
7. Determine reading order for manga, manhua, manhwa, and webtoon layouts.
8. Translate by stable box ID through the existing NTS provider, dictionary, and memory stack.
9. Clean or inpaint original text with adapter-backed local-first behavior.
10. Render Vietnamese text back into bubbles or text regions.
11. Run visual QA for missing text, overflow, low confidence, residue, page order, and export integrity.
12. Produce a human review package.
13. Export translated image folders, CBZ files, and PDF files.
14. Integrate with the GUI through a guided Manga/Image tab.
15. Support canary and production rollout jobs with artifact-backed status.
16. Require a real provider preflight and real translation calls in the final canary and rollout phases.

## Non-Negotiable Constraints

- Do not modify production text-novel translation behavior.
- Do not enable approved rules in prompts.
- Do not inject raw NLP cache into prompts.
- Keep Dictionary, Memory, and Rules separated.
- Keep rules verifier-only unless a later validation phase proves a different policy.
- Keep project-scoped dictionary and project-scoped memory.
- Keep user data local by default.
- Make cloud OCR, cloud inpainting, and cloud image-processing adapters explicit opt-in configuration.
- Never commit copyrighted manga, manhua, manhwa, webtoon, or scan images.
- Commit only tiny synthetic or explicitly licensed test fixtures.
- Redact API keys in docs, logs, artifacts, backend responses, and frontend state.
- Do not claim production readiness until the relevant canary or rollout has real provider evidence.

## GUI End State

The Manga/Image tab must use this wizard-style flow:

1. Chọn ảnh/CBZ/PDF
2. Nhận diện chữ
3. Kiểm tra OCR
4. Dịch thử
5. Chèn chữ vào ảnh
6. Kiểm tra hình
7. Xuất CBZ/PDF

The GUI must remain end-user friendly. Technical logs, adapter settings, file paths, JSON details, and provider diagnostics belong behind `Xem chi tiết kỹ thuật`.

Every visible button must be wired to a real action or explicitly labeled as a placeholder until the relevant subphase activates it.

## Architecture Direction

Phase 9 extends the existing NTS architecture:

- CLI first, GUI backed by service APIs.
- Python backend/core first.
- Local-first workspace storage.
- SQLite records for manifests, boxes, runs, review state, and lightweight metadata.
- Large images, masks, previews, exports, and QA files stored on disk.
- Artifact-backed progress and status.
- Stable prompt and hybrid prompt stack for translation.
- Approved dictionary and approved memory support.
- Rules remain verifier-only.
- Existing provider preflight and production rollout patterns reused for final manga canary and rollout.

## Required Subphase Order

1. `PHASE9A_IMPORT_AND_PROJECT_MODEL_GOAL.md`
2. `PHASE9B_IMAGE_PREPROCESSING_GOAL.md`
3. `PHASE9C_TEXT_AND_BUBBLE_DETECTION_GOAL.md`
4. `PHASE9D_OCR_ADAPTERS_AND_REVIEW_GOAL.md`
5. `PHASE9E_READING_ORDER_AND_PAGE_CONTEXT_GOAL.md`
6. `PHASE9F_TRANSLATION_INTEGRATION_GOAL.md`
7. `PHASE9G_TEXT_CLEANING_AND_INPAINTING_GOAL.md`
8. `PHASE9H_TYPESETTING_AND_RENDERING_GOAL.md`
9. `PHASE9I_VISUAL_QA_GOAL.md`
10. `PHASE9J_EXPORT_CBZ_PDF_GOAL.md`
11. `PHASE9K_GUI_MANGA_TAB_GOAL.md`
12. `PHASE9L_END_TO_END_CANARY_GOAL.md`
13. `PHASE9M_PRODUCTION_ROLLOUT_GOAL.md`

Each subphase file is designed to be usable as a future Codex `/goal` instruction.

## Phase 9 PASS Definition

Phase 9 as a whole is PASS only when:

- All subphase PASS criteria are met.
- The GUI Manga/Image tab completes the guided workflow without dead buttons.
- Unit and integration tests pass without real provider calls.
- The final canary runs a real provider preflight and real translation call.
- The production rollout starts with a real provider preflight and records per-call provider/model usage.
- QA blockers are zero for the production rollout.
- Exported image folder, CBZ, and PDF artifacts exist.
- Human review artifacts exist.
- No raw API keys, full copyrighted images, or full copyrighted text dumps leak into logs, artifacts, or UI responses.

