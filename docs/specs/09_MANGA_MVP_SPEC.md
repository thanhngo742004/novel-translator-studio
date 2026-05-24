# 09 — Manga MVP Spec

## Phase 2 position

Manga is important but should not block core CLI/storage/model/memory MVP.

## MVP4 scope

Manga semi-manual MVP should eventually support:

- import image folder/CBZ
- register manga pages
- manually create/edit/delete/resize boxes
- import or edit OCR text
- define reading order
- translate box text by box ID
- export box translation manifest

## Not in MVP4 unless explicitly requested

- full automatic bubble detection
- image inpainting
- advanced OCR benchmark
- speaker attribution automation
- polished typesetting engine

## Data model direction

Tables later:

- `manga_pages`
- `manga_boxes`

Box fields later:

- `box_id`
- `page_id`
- `bbox_json`
- `polygon_json`
- `origin`
- `reading_order`
- `ocr_text`
- `ocr_corrected_text`
- `translation_text`
- `status`
