# Phase 9 Research Summary

## Scope

This summary covers the technical research needed to design a manga, manhwa, manhua, webtoon, and general image translation pipeline for Novel Translator Studio. It favors local/offline defaults, adapter-based cloud options, stable page and box IDs, human review, and artifact-backed production evidence.

## End-To-End Manga/Image Translation Pipelines

Open-source manga translators generally use the same high-level pipeline: detect text regions, OCR text, translate text, clean or inpaint source text, and render translated text back into the image. The `manga-image-translator` project is a broad reference for this shape of pipeline and supports multiple modes and OCR/translation/inpainting choices, but its GPL-3.0 status means it must be treated as a reference or optional external adapter unless a license strategy is explicitly approved: https://github.com/zyddnys/manga-image-translator.

Koharu is a newer local-first manga translation project that combines detection, OCR, inpainting, LLM translation, and export from a desktop application. It is useful as an architecture reference for local-first UX and editable outputs, but its GPL-3.0 package metadata also makes it unsuitable for vendoring into the NTS core without approval: https://github.com/mayocream/koharu and https://packages.ecosyste.ms/registries/crates.io/packages/koharu.

The AAAI 2021 paper "Towards Fully Automated Manga Translation" frames manga translation as multimodal and context-aware rather than plain text translation. It emphasizes that speech bubbles can depend on nearby bubbles, speaker clues, and visual context, which supports Phase 9's plan to translate by page context and neighboring boxes rather than isolated OCR strings: https://ojs.aaai.org/index.php/AAAI/article/view/17537.

## OCR For CJK And Vertical Text

Manga OCR focuses on Japanese manga text and is explicitly designed for vertical text, horizontal text, furigana, overlaid text, varied fonts, low-quality images, and multi-line text bubbles. It is Apache-2.0 and should be the preferred first Japanese local OCR adapter: https://github.com/kha-white/manga-ocr.

PaddleOCR is an Apache-2.0 OCR toolkit with multilingual recognition models and production deployment options. It is a strong candidate for Chinese, Korean, English, mixed CJK, and general image OCR adapters, while exact model/version choices should be locked during implementation because PaddleOCR has multiple active documentation versions: https://www.paddleocr.ai/main/en/index/index.html and https://github.com/PaddlePaddle/PaddleOCR.

EasyOCR is Apache-2.0 and supports many languages, making it a pragmatic fallback adapter for quick experiments or unsupported local environments. It should not be the only OCR path for manga-specific vertical Japanese because Manga OCR is more specialized for that case: https://github.com/JaidedAI/EasyOCR.

Tesseract is Apache-2.0 and mature, but its manga vertical-text quality is uncertain and should be treated as a fallback, not the default for CJK manga OCR: https://github.com/tesseract-ocr/tesseract and https://tesseract-ocr.github.io/tessdoc/.

Cloud OCR options such as Google Cloud Vision, Azure AI Vision Read, and AWS Textract can be useful for users who explicitly opt in to uploading images. They must be off by default because user images may be copyrighted or private: https://cloud.google.com/vision/docs/ocr, https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/overview-ocr, and https://docs.aws.amazon.com/textract/latest/APIReference/API_DetectDocumentText.html.

## Text Detection, Bubble Detection, And Region Detection

Generic scene text detection techniques such as CRAFT and DBNet are relevant because manga text can be rotated, curved, vertical, or heavily stylized. CRAFT detects character regions and affinities, which is useful for irregular text regions: https://arxiv.org/abs/1904.01941 and https://github.com/clovaai/CRAFT-pytorch. DBNet and DBNet-style detectors are relevant for real-time arbitrary-shape text detection: https://arxiv.org/abs/1911.08947 and https://github.com/MhLiao/DB.

Comic-specific text detectors such as `comic-text-detector` are useful references for manga text/bubble detection, but license and model provenance must be verified before direct integration: https://github.com/dmMaze/comic-text-detector.

Manga109 annotations include text, frame, face, and body regions with unique IDs and rectangular areas. It is valuable as a research reference for page, box, and region modeling, but NTS must not commit dataset images or annotations unless license and redistribution rights are explicitly approved: https://manga109.github.io/manga109-project-website/en/annotations.html and https://github.com/manga109/manga109api.

Manga109Dialog adds speaker-to-dialogue annotations for comic speaker detection research. It is relevant later for speaker hints, but Phase 9 should not depend on automatic speaker detection for production readiness: https://github.com/liyingxuan1012/Manga109Dialog and https://arxiv.org/abs/2306.17469.

## Reading Order

Reading order differs by content type:

- Japanese manga commonly uses right-to-left page and bubble conventions.
- Chinese manhua and Korean manhwa may use left-to-right or top-to-bottom conventions depending on format.
- Webtoons often use vertical scroll order.

Research on manga bubble reading order shows that bubble position and image data can be used to infer order, but the result is inherently uncertain and should stay user-adjustable: https://research.aalto.fi/en/publications/a-layered-method-for-determining-manga-text-bubble-reading-order/.

The AAAI manga translation paper also supports page-level and context-aware translation, which means Phase 9 should store an explicit reading order list or graph before translation: https://ojs.aaai.org/index.php/AAAI/article/view/17537.

## Cleaning, Inpainting, And Text Removal

OpenCV provides local inpainting via Telea and Navier-Stokes algorithms and is a reasonable first local inpainting option after simple white/color fill. It is not enough for every manga background, but it is lightweight and testable: https://docs.opencv.org/4.x/df/d3d/tutorial_py_inpainting.html.

LaMa-style neural inpainting is relevant for complex backgrounds, but it should be optional because it adds model-management, GPU, dependency, and license complexity: https://github.com/advimman/lama.

Cloud image editing or inpainting adapters can be added later only if they are explicit opt-in, redact credentials, and warn that user images leave the local machine.

## Typesetting And Rendering

Pillow's `ImageDraw` APIs provide local text drawing and measurement primitives such as multiline text bounding boxes, making it a practical MVP renderer for Vietnamese text fitting, overflow detection, and image output: https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html.

OpenCV can support mask operations, compositing, and some image transformations, while Pillow is better suited to font-aware text drawing in Python. Advanced typesetting for Vietnamese should plan font fallback and line-height control without committing third-party font files.

## Export Formats

CBZ is a ZIP archive of ordered image pages. Python's standard `zipfile` module is enough for deterministic CBZ export and keeps the core dependency small: https://docs.python.org/3/library/zipfile.html.

`img2pdf` can embed common image formats into PDF without unnecessary re-encoding and is a good optional PDF export dependency. Its PyPI metadata lists LGPLv3, so license impact should be reviewed before adding it to core dependencies: https://pypi.org/project/img2pdf/.

PyMuPDF is useful for PDF rendering/import workflows but has AGPL/commercial licensing. It must remain optional or be avoided unless the project accepts the license terms: https://pymupdf.io/ and https://pypi.org/project/PyMuPDF/.

`pdf2image` wraps Poppler for PDF-to-image conversion. It may be acceptable as an optional adapter, but Poppler installation and license implications must be documented for Windows users: https://pdf2image.readthedocs.io/.

## Visual QA

Visual QA should combine structured checks and human-review artifacts:

- Missing OCR for required boxes.
- Missing translations for boxes marked translatable.
- Low OCR confidence.
- Low detection confidence.
- Reading order gaps or duplicates.
- Rendered text overflow.
- Text smaller than configured readability threshold.
- Rendered text outside the destination region.
- Uncleaned source text residue where a region was marked cleaned.
- Export page order mismatch.

Automated checks cannot prove subjective quality. The final canary and rollout must include a human review package with bounded previews, JSON summaries, and Markdown summaries.

## Local/Offline Versus Cloud Tradeoffs

Local OCR, detection, cleaning, and rendering protect privacy and copyright-sensitive user data. The cost is larger local dependencies, model downloads, CPU/GPU variability, and lower quality on some scans.

Cloud adapters can improve OCR or image cleanup in some cases, but they upload user images and may incur cost. They must be explicit, configurable, and off by default. Artifacts must record whether a cloud adapter was used and must never store raw API keys.

## Research-Backed Phase 9 Decisions

- Start semi-manual and reviewable rather than fully automatic.
- Keep stable `page_id` and `box_id` as canonical identifiers.
- Treat detection, OCR, cleaning, and inpainting as adapters.
- Use Manga OCR first for Japanese manga OCR.
- Use PaddleOCR first for Chinese, Korean, English, and mixed CJK OCR.
- Use simple fill and OpenCV inpaint before neural inpainting.
- Use Pillow/OpenCV for MVP typesetting and rendering.
- Use `zipfile` for CBZ and optional `img2pdf` for PDF export.
- Keep final production readiness tied to real provider preflight and real translation calls, not mocked tests.

