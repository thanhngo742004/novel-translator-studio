# Phase 3 Manga Rules

Phase 3 adds manga/comic/manhwa/manhua architecture. These rules extend the existing AGENTS.md.

## Manga implementation timing

- Do not implement manga before MVP4 unless the user explicitly asks.
- Continue current roadmap: MVP0 → MVP1 → MVP2 → MVP3 → MVP4.
- Phase 3 documents are planning/spec references until manga MVP begins.

## Manga architecture principles

- Manga module belongs inside Novel Translator Studio, not a separate app.
- The canonical object is not the final translated image.
- The canonical object is: page + stable box IDs + manifest + audit trail.
- Cleaned images, typeset images, CBZ/PDF exports, masks, previews, and QA reports are artifacts generated from the manifest.
- Use stable `box_id` for every OCR/translation/typeset operation.
- Translation must preserve exact box IDs. Missing/extra box IDs are validation failures.

## Manga MVP rules

Start with semi-manual, not full-auto:

1. Import pages / CBZ.
2. Register page artifacts.
3. Import/export boxes JSON.
4. Version box edits.
5. Import/edit OCR text.
6. Translate by box ID.
7. Export manifest.
8. Add simple clean/typeset later.

Do not implement early:
- full-auto speaker detection
- full-auto inpainting for every page
- stylized SFX redraw
- AI image editing as default
- polished PDF export
- complete GUI canvas before core CLI/data layer

## Dependency rules

- Keep manga dependencies optional.
- Do not add OCR/CV/inpainting dependencies to the core package by default.
- Prefer extras such as `[manga-basic]`, `[manga-ocr]`, `[manga-cv]` later.
- Do not vendor GPL/AGPL projects into the core without explicit approval.
- Treat GPL/AGPL repositories as references or optional external adapters unless license strategy is approved.

## Preferred manga technical path

- OCR Japanese: `manga-ocr` first.
- OCR Chinese/Korean/English/mixed: `PaddleOCR` first.
- Fallback OCR: EasyOCR/Tesseract if needed.
- Detection MVP: manual/imported boxes first.
- Detection V2: OCR proposals.
- Detection V3: deep detector / external adapter.
- Cleaning MVP: white/color fill.
- Cleaning V2: OpenCV inpaint.
- Cleaning later: LaMa/inpainting optional.
- Typesetting MVP: Pillow/OpenCV local text fitting.

## Required docs before manga implementation

Before implementing manga MVP4, read:

- `docs/raw/phase3-manga-research.md`
- `docs/specs/11_MANGA_ARCHITECTURE_PHASE3.md`
- `docs/specs/12_MANGA_DATA_SCHEMA.md`
- `docs/specs/13_MANGA_CLI_SPEC.md`
- `docs/specs/14_MANGA_MVP_PLAN.md`
