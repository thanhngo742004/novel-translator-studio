# Phase 9 Dependency Evaluation

## Rule

Do not add dependencies blindly. Each implementation phase must re-check the current license, maintenance status, Windows compatibility, and model-download requirements before adding a package.

| Area | Candidate | License | Offline? | Pros | Cons | Recommendation |
| ---- | --------- | ------- | -------- | ---- | ---- | -------------- |
| Japanese OCR | Manga OCR | Apache-2.0 per repository | Yes after model install | Manga-specific, supports vertical/horizontal Japanese, furigana, multi-line bubbles | Japanese-focused, model download, may need GPU/CPU performance checks | Preferred first Japanese OCR adapter. Source: https://github.com/kha-white/manga-ocr |
| CJK/mixed OCR | PaddleOCR | Apache-2.0 per docs | Yes after model install | Multilingual OCR, active ecosystem, detection and recognition models | Multiple docs versions, heavier dependencies, model selection complexity | Preferred Chinese/Korean/English/mixed CJK OCR adapter. Source: https://www.paddleocr.ai/main/en/index/index.html |
| OCR fallback | EasyOCR | Apache-2.0 per repository | Yes after model install | Simple Python API, many languages | Less manga-specific, performance varies | Optional fallback adapter. Source: https://github.com/JaidedAI/EasyOCR |
| OCR fallback | Tesseract | Apache-2.0 per project | Yes | Mature, widely packaged, many language data files | Weak fit for manga vertical text unless configured well | Fallback only, not primary CJK manga OCR. Source: https://github.com/tesseract-ocr/tesseract |
| Cloud OCR | Google Cloud Vision OCR | Proprietary service terms | No | Strong OCR, dense text endpoint | Uploads user images, cost, credentials, privacy review | Optional opt-in adapter only. Source: https://cloud.google.com/vision/docs/ocr |
| Cloud OCR | Azure AI Vision Read | Proprietary service terms | No | OCR service with broad image support | Uploads user images, cost, credentials | Optional opt-in adapter only. Source: https://learn.microsoft.com/en-us/azure/ai-services/computer-vision/overview-ocr |
| Cloud OCR | AWS Textract DetectDocumentText | Proprietary service terms | No | Structured line/word output, PDF/image support | Uploads user images, document-oriented, cost | Optional opt-in adapter, not manga default. Source: https://docs.aws.amazon.com/textract/latest/APIReference/API_DetectDocumentText.html |
| Text detection | CRAFT | Verify implementation license | Yes | Handles irregular/curved text regions, established paper | Generic scene text, model management | Reference or optional detector adapter. Sources: https://arxiv.org/abs/1904.01941 and https://github.com/clovaai/CRAFT-pytorch |
| Text detection | DBNet / DB-style detectors | Verify implementation license | Yes | Fast arbitrary-shape scene text detection | Generic scene text, integration work | Reference or optional detector adapter. Sources: https://arxiv.org/abs/1911.08947 and https://github.com/MhLiao/DB |
| Comic text detection | comic-text-detector | Verify current repository and model license | Yes | Comic-specific text detection reference | License/model provenance needs review | Do not vendor into core until license approved. Source: https://github.com/dmMaze/comic-text-detector |
| End-to-end reference | manga-image-translator | GPL-3.0 reported by project indexes; verify repository | Partial | Mature pipeline reference with OCR, translation, inpainting, rendering | Copyleft concerns, broad architecture not aligned with NTS data model | Reference only or optional external adapter after license approval. Source: https://github.com/zyddnys/manga-image-translator |
| End-to-end reference | Koharu | GPL-3.0 reported by package metadata | Yes | Local-first workflow, desktop UX, editable export concepts | Rust/Tauri stack separate from NTS, copyleft concerns | Reference only. Source: https://github.com/mayocream/koharu |
| Image preprocessing | Pillow | HPND-style permissive Pillow license; verify before pin | Yes | Already common Python image library, text measurement/drawing | Advanced shaping limited without extra libraries | Preferred core image IO and text measurement dependency if not already present. Source: https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html |
| Image preprocessing | OpenCV | Apache-2.0 per OpenCV | Yes | Thresholding, morphology, masks, inpainting, geometry | Large dependency, wheels can be heavy | Optional extra for preprocessing and inpainting. Source: https://opencv.org/license/ |
| Cleaning | Simple fill | NTS code | Yes | Deterministic, fast, testable | Poor on complex backgrounds and SFX | Required MVP cleaning mode |
| Inpainting | OpenCV inpaint | Apache-2.0 via OpenCV | Yes | Local Telea/Navier-Stokes inpainting | Limited quality on complex art | First optional local inpaint adapter. Source: https://docs.opencv.org/4.x/df/d3d/tutorial_py_inpainting.html |
| Inpainting | LaMa | Verify current license and model license | Yes after model install | Better object/text removal on complex backgrounds | Heavy model dependency, GPU variability | Optional later adapter only. Source: https://github.com/advimman/lama |
| Typesetting | Pillow ImageDraw | Pillow license | Yes | Local drawing, text bbox measurement, deterministic tests | Complex CJK shaping and advanced lettering limited | Required MVP renderer. Source: https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html |
| Typesetting | HarfBuzz/Pango/Raqm stack | Mixed licenses, verify | Yes | Better shaping and font fallback | Windows packaging complexity | Later optional text engine if Pillow is insufficient |
| CBZ export | Python zipfile | Python standard library | Yes | No new dependency, deterministic ZIP/CBZ packaging | Need careful ordering and metadata | Required CBZ implementation. Source: https://docs.python.org/3/library/zipfile.html |
| PDF export | img2pdf | LGPLv3 per PyPI | Yes | Lossless image-to-PDF behavior for common formats | License review, optional dependency | Preferred optional PDF export adapter after license review. Source: https://pypi.org/project/img2pdf/ |
| PDF import | pdf2image + Poppler | Wrapper license plus Poppler license; verify | Yes after Poppler install | Practical PDF rasterization | External Poppler install, Windows setup | Optional PDF scan import adapter. Source: https://pdf2image.readthedocs.io/ |
| PDF import/export | PyMuPDF | AGPL/commercial | Yes | Powerful PDF rendering and manipulation | AGPL obligations unless commercial license | Avoid core dependency; optional only after explicit approval. Source: https://pymupdf.io/ |
| GUI | Existing NTS GUI stack | Existing repo license | Local | Consistent UX, provider config reuse, artifact viewer reuse | Manga review UI adds complexity | Reuse existing GUI stack; do not add a new desktop framework in Phase 9 |

## Dependency Decision Summary

- Core Phase 9 should require only dependencies already accepted by the repo plus small permissive libraries.
- OCR, detection, OpenCV, PDF, and inpainting dependencies should be extras or optional adapters.
- GPL/AGPL projects are useful references but must not be vendored into core without approval.
- Cloud adapters must be explicit and disabled by default.

