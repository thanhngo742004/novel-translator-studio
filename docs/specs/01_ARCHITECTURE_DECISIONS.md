# 01 — Architecture Decisions

## Decision 1: Backend-first, local-first, service-first

The app should have one core backend/service layer. CLI and GUI both call this service layer.

## Decision 2: CLI first

The CLI is the stable contract for automation and future OpenClaw integration. GUI must not duplicate business logic.

## Decision 3: Python-first MVP

Recommended MVP stack:

- Python 3.11+ or 3.12+
- Typer for CLI
- Pydantic for schemas/config
- SQLAlchemy for DB access
- Alembic or a simple migration runner for early MVP
- pytest for tests
- Ruff for lint/format
- PyYAML for YAML config

## Decision 4: SQLite for MVP

Use SQLite local workspace for MVP.

Reasons:

- local-first single-user workflow
- one DB file easy to backup
- WAL support for concurrent read/write
- FTS5/BM25 support
- JSON functions available

Large files should be stored in artifact folders, not DB blobs.

## Decision 5: LAMM-T structured memory first

Memory truth should be structured records with scope, evidence, confidence, provenance, conflict and audit data.

Vector search is optional support for examples, not canonical truth.

## Decision 6: Plugin export is compiler output

VBook/plugin export should compile active approved memory into a compact bundle. It is not full sync. The plugin is read-only and does not learn.

## Decision 7: Manga is deferred to semi-manual MVP later

Manga is important but should not block core architecture.

First manga MVP should support page/box data and manual correction before full OCR/inpainting/typeset automation.
