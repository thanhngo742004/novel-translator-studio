# 11 — Manga Architecture Phase 3

## Purpose

This document converts Phase 3 manga research into implementation guidance for Novel Translator Studio.

## Core decision

The canonical object of the manga module is not the rendered translated image.

The canonical object is:

```text
page + stable box IDs + manifest + audit trail
```

Generated artifacts include OCR crops, cleaned images, typeset images, CBZ/PDF exports, masks, preview renders, and QA reports.

## Service boundaries

The manga module should be implemented as service-layer modules inside the same app architecture:

```text
MangaImportService
PageArtifactService
PagePreprocessService
BoxDetectionService
BoxRevisionService
OcrService
ReadingOrderService
SpeakerHintService
MangaTranslationService
MangaQaService
CleaningService
TypesetService
PreviewService
ExportService
MangaMemoryService
```

CLI and future GUI must call these services rather than duplicating logic.

## Recommended pipeline

```text
source asset
→ normalized page asset
→ detector/manual box proposals
→ canonical boxes + versions
→ OCR results
→ translation manifest
→ cleaned page artifact
→ typeset page artifact
→ exports + QA report
→ memory updates
```

## MVP philosophy

Start semi-manual:

```text
import pages
→ import/draw boxes
→ edit OCR text
→ translate by box ID
→ validate ID preservation
→ export manifest
```

Do not start with full automation.

## Tool recommendations

```text
Japanese OCR: manga-ocr
Chinese/Korean/English/mixed OCR: PaddleOCR
Fallback OCR: EasyOCR/Tesseract
Cleaning MVP: white/color fill
Cleaning V2: OpenCV inpaint
Cleaning later: LaMa/inpainting
Typesetting MVP: Pillow/OpenCV
CBZ import/export: Python zipfile
CBR/PDF: optional/experimental
```

## License caution

Repos such as manga-image-translator, BallonsTranslator, comic-text-detector, Koharu, and PyMuPDF may have GPL/AGPL/commercial constraints. Treat them as references or optional external adapters unless license strategy is explicitly approved.

## What to implement first when manga begins

Manga MVP4A:

```text
import images/CBZ
register pages/artifacts
create/import/export boxes JSON
box versioning
manifest export
no OCR dependency yet
```
