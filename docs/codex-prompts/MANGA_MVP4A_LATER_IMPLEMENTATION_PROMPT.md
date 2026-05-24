# Codex Prompt — Manga MVP4A Later

Use this only when you explicitly decide to start manga MVP4A. Do not use during MVP0/MVP1/MVP2/MVP3.

```text
Implement Manga MVP4A only.

Read:
- AGENTS.md
- AGENTS_PHASE3_APPEND.md
- docs/specs/11_MANGA_ARCHITECTURE_PHASE3.md
- docs/specs/12_MANGA_DATA_SCHEMA.md
- docs/specs/13_MANGA_CLI_SPEC.md
- docs/specs/14_MANGA_MVP_PLAN.md

Scope:
- Add manga page/artifact data foundation.
- Add image folder import.
- Add CBZ import using Python standard zipfile.
- Add page SHA-256 checksum.
- Add manga_pages and manga_page_artifacts tables/migrations.
- Add manga_boxes and manga_box_versions tables/migrations.
- Add box JSON import/export.
- Add manifest export.
- Add CLI commands:
  - nts manga import
  - nts manga pages list
  - nts manga boxes import
  - nts manga boxes export
  - nts manga boxes list
  - nts manga manifest export
- Add tests.

Do not implement:
- OCR engines
- translation
- cleaning
- typesetting
- GUI canvas
- PDF/CBR import
- inpainting
- speaker detection
- GPL/AGPL vendor dependencies
```
