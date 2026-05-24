# AGENTS.md — Novel Translator Studio

## Mission

Implement Novel Translator Studio incrementally from the Phase 1 LAMM-T memory method and Phase 2 architecture.

The project is a local-first CLI + future desktop app for translating text novels and manga/comics into Vietnamese, with adaptive memory learning and compact plugin export.

## Mandatory reading order

Before planning implementation, read:

1. `docs/specs/00_PROJECT_OVERVIEW.md`
2. `docs/specs/01_ARCHITECTURE_DECISIONS.md`
3. `docs/specs/10_MVP_IMPLEMENTATION_PLAN.md`
4. `docs/codex-prompts/MVP0_IMPLEMENTATION_PROMPT.md`

Use raw research files only as reference:

- `docs/raw/phase1-memory-method.md`
- `docs/raw/phase2-architecture.md`

## Non-negotiable architecture decisions

- CLI first, GUI later.
- Python backend/core first.
- Local-first workspace.
- SQLite for MVP.
- Large artifacts live on disk, not inside the database.
- LAMM-T memory is structured-first.
- Vector retrieval is optional support, not the source of truth.
- Plugin export is compact and read-only.
- Plugin does not self-learn.
- Manga automation is deferred; MVP manga is semi-manual only.
- Model routing must be configurable by provider, base URL, API key env var, endpoint type, and model/capability class.

## Scope rules

Do not implement the full application unless explicitly asked.

For current MVP0, implement only:

- package/repo skeleton
- CLI entrypoint
- workspace initialization
- config loading/validation
- SQLite connection + migrations placeholder
- `task_runs` / `model_runs` minimal tables
- provider config validation
- mock provider
- smoke tests

Do not implement in MVP0:

- real translation pipeline
- real style learning
- real LLM provider calls
- manga OCR/detection/inpainting/typeset
- desktop GUI
- VBook plugin export
- OpenClaw skill packaging beyond docs

## Memory rules

Never redesign LAMM-T from scratch. Implement it incrementally.

LAMM-T requires:

- scope-aware structured memory
- confidence
- evidence
- provenance
- conflict handling
- audit logs
- retrieval bundles
- compact export
- human-in-the-loop review

Never overwrite conflicting memory in place. Create challenger/conflict records.

## Model routing rules

Do not hard-code provider/model assumptions into business logic.

Provider config must support:

- OpenAI Responses
- OpenAI-compatible endpoints
- Anthropic Messages
- local compatible endpoints such as Ollama/LM Studio later
- optional gateways like LiteLLM/OpenRouter/9Router later

API keys must be referenced by environment variable names. Never commit raw API keys.

## Testing rules

Add tests with every implementation step.

MVP0 minimum tests:

- CLI smoke test
- workspace init creates expected folders/db
- config loader parses example config
- DB migration initializes core tables
- mock provider returns deterministic response

## Delivery format

When you make changes, report:

1. Files changed
2. Commands/tests run
3. What works
4. What is intentionally not implemented
5. Risks/follow-up tasks

## When uncertain

Prefer the smallest implementation that preserves:

- local-first architecture
- CLI automation compatibility
- structured memory
- auditability
- future GUI compatibility

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

