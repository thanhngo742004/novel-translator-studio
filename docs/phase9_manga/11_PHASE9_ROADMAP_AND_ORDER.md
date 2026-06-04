# Phase 9 Roadmap And Order

## Implementation Order

Run future goals in this order:

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

## Why This Order

- Import and manifest come first because every later stage needs stable `page_id` and artifact paths.
- Preprocessing comes before detection/OCR because OCR variants and normalized pages improve downstream consistency.
- Detection comes before OCR because OCR must attach text to stable regions.
- OCR review comes before reading order and translation so the user can fix source text early.
- Reading order comes before translation because page context depends on ordered boxes.
- Translation comes before cleaning/rendering because translated text length drives fit decisions.
- Cleaning comes before rendering because original text must be removed or masked first.
- Rendering comes before visual QA because QA must inspect final images.
- Export comes after QA so exports represent reviewed output.
- GUI activation follows core capabilities so every visible button can be wired.
- Canary and rollout come last because they require real provider evidence and end-to-end artifacts.

## Future Goal Commands

Use one subphase file at a time:

```text
/goal Follow docs/phase9_manga/PHASE9A_IMPORT_AND_PROJECT_MODEL_GOAL.md exactly. Do not expand scope.
```

Then continue with:

```text
/goal Follow docs/phase9_manga/PHASE9B_IMAGE_PREPROCESSING_GOAL.md exactly. Preserve Phase 5/6/7 behavior.
```

Continue through Phase 9M only after each prior phase reaches PASS or an accepted BLOCKED status.

## Highest-Risk Areas

- Detection quality on stylized manga and vertical text.
- OCR quality for low-resolution scans and mixed CJK.
- Reading order ambiguity.
- Vietnamese text overflow in small bubbles.
- Cleaning/inpainting quality on textured backgrounds.
- License risk from GPL/AGPL manga translation projects.
- Windows packaging for OCR, OpenCV, and PDF rasterization.
- Privacy risk when users enable cloud adapters.
- Provider configuration drift between GUI and backend.

## Recommended First Implementation Goal

Start with `PHASE9A_IMPORT_AND_PROJECT_MODEL_GOAL.md`.

That phase hardens the existing manga import/project model into the stable foundation required by every later stage.

