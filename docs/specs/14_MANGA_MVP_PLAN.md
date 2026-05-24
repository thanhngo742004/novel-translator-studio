# 14 — Manga MVP Plan

## Principle

Do not build full-auto manga translation first.

Build a stable manifest pipeline first:

```text
page import
→ stable box IDs
→ OCR/import/edit
→ translate by ID
→ QA
→ clean/typeset
→ export
```

## Manga MVP4A — Data foundation

Scope:

```text
page import
page artifact registry
CBZ import
box data model
manual box JSON import/export
manifest export
```

Acceptance criteria:

```text
- Import a folder of images.
- Import a CBZ archive.
- Register pages and artifacts in SQLite.
- Compute SHA-256 for imported pages.
- Create/import/export box JSON.
- Store box versions.
- Export a manifest without OCR/translation.
- Re-import exact same pages without duplicating canonical pages unexpectedly.
```

Do not implement:

```text
OCR engines
translation
cleaning
typesetting
GUI canvas
PDF/CBR unless optional backend is explicitly requested
```

## Manga MVP4B — OCR import/edit + translate by box ID

Scope:

```text
OCR adapter interface
manual OCR import/edit
optional manga-ocr adapter
optional PaddleOCR adapter
translate boxes by stable box IDs
validate ID preservation
export text/JSON manifest
```

Acceptance criteria:

```text
- Every source box gets exactly one translation output.
- Missing/extra box IDs fail validation.
- OCR can be rerun without losing manual box corrections.
- User-corrected OCR is stored as a new OCR result, not an overwrite.
```

## Manga MVP4C — Simple clean/typeset

Scope:

```text
white/color fill
OpenCV inpaint optional
Pillow/OpenCV typesetting
overflow warnings
preview artifact export
CBZ export
```

Acceptance criteria:

```text
- Simple speech bubbles can be cleaned.
- Vietnamese text can be fitted into boxes.
- Overflow is detected and reported.
- Original images are never modified in place.
```

## Later MVPs

MVP5+:

```text
GUI canvas
advanced box detection
advanced OCR correction
speaker hints
reading-order learning
LaMa/inpainting
PDF polished export
SFX handling
vision QA
```

## Codex implementation order for manga

When manga work begins, use narrow tasks:

1. Implement manga import/artifact registry.
2. Implement box manifest schema.
3. Implement box versioning.
4. Implement JSON import/export.
5. Implement manifest validator.
6. Implement OCR adapter interface.
7. Implement translation-by-box-ID validator.
8. Implement local QA checks.
9. Implement simple cleaning.
10. Implement local typesetting.

## Do not do early

```text
full-auto detection
full-auto speaker attribution
AI redraw for every page
stylized SFX redraw
GUI-heavy implementation before CLI/data foundation
vendor GPL/AGPL repos into core
```
