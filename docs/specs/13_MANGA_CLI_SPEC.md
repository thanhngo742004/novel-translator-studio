# 13 — Manga CLI Spec

## Command philosophy

Manga CLI must be automation-friendly, resumable, and JSON-capable.

Every long-running command should create or reference a `task_run_id`.

## Command tree

```bash
nts manga import <path> --project <project> [--chapter <chapter>] [--json]
nts manga pages list --chapter <chapter> [--json]
nts manga preprocess --chapter <chapter> [--profile default] [--json]

nts manga boxes detect --chapter <chapter> [--mode manual_only|ocr_proposal|dl_proposal] [--json]
nts manga boxes list --chapter <chapter> [--page 1] [--json]
nts manga boxes export --chapter <chapter> --out boxes.json
nts manga boxes import boxes.json --chapter <chapter> [--replace|--merge] [--json]
nts manga boxes revise --chapter <chapter> --page 1 --ops ops.json [--json]

nts manga ocr run --chapter <chapter> [--engine auto|manga-ocr|paddleocr|easyocr|tesseract] [--json]
nts manga ocr import ocr.json --chapter <chapter> [--json]
nts manga ocr export --chapter <chapter> --out ocr.json
nts manga ocr review --chapter <chapter> [--errors-only] [--json]

nts manga order auto --chapter <chapter> [--profile rtl_tb] [--json]
nts manga order import order.json --chapter <chapter> [--json]

nts manga translate --chapter <chapter> --profile <profile> [--provider <p>] [--model <m>] [--json]
nts manga translate review --chapter <chapter> [--json]
nts manga manifest export --chapter <chapter> --out manifest.json

nts manga clean --chapter <chapter> [--level 1|2|3] [--method fill|telea|ns|lama] [--json]
nts manga typeset --chapter <chapter> [--preset default_vi] [--json]
nts manga preview --chapter <chapter> [--page 1] [--mode original|overlay|clean|typeset|diff]

nts manga export --chapter <chapter> --format images|cbz|pdf|manifest|qa [--json]
nts manga qa run --chapter <chapter> [--json]
nts manga memory update --chapter <chapter> [--json]
```

## MVP4A commands

Implement first:

```bash
nts manga import
nts manga pages list
nts manga boxes import
nts manga boxes export
nts manga boxes list
nts manga manifest export
```

## JSON result envelope

```json
{
  "ok": true,
  "run_id": "run_...",
  "chapter_id": "chap_001",
  "task": "manga_import",
  "status": "completed",
  "warnings": [],
  "artifacts": [],
  "db_writes": {},
  "resume_token": null
}
```

## Error codes

```text
10 input error
20 import/decode error
30 dependency missing
40 validation error
50 OCR/translation runtime error
60 review required
70 export failed
```

## Implementation rule

All commands must be usable without GUI. GUI should call the same service layer or CLI-equivalent application services.
