# Continuous MVP Progress

## 2026-05-24T19:46:29+07:00

- Branch: `master`
- Baseline: MVP0 scaffold with CLI, workspace init, SQLite file-based migrations, config validation, project create/list, mock model test, deterministic SQLite connection closing.
- Baseline tests: `python -m pytest` -> 11 passed.
- Current target: MVP1 only.
- Planned MVP1 phases:
  - MVP1A text import: documents, chapters, segments, translations, local artifact copy, simple segmentation.
  - MVP1B memory core: structured LAMM-T memory tables, CRUD/status/evidence, audit logs.
  - MVP1C deterministic retrieval bundle: active-only filtering, simple scope/source matching, confidence ordering, checksum.
  - MVP1D mock translation pipeline: bundle + deterministic mock provider + translation rows + output artifact.
- Deferred:
  - Real provider calls, style learning, correction learning, plugin export, manga, GUI, vector/BM25/graph/cloud/multi-user.

## 2026-05-24T20:00:00+07:00

- Completed: MVP1 only.
- MVP1A text import:
  - Added `documents`, `chapters`, `segments`, `translations` migrations.
  - Implemented UTF-8 `.txt` import, SHA-256 checksum, raw artifact copy to `artifacts/raw/`, simple heading-based chapters, paragraph segments, and `task_runs` logging.
  - Added CLI: `nts import text`, `nts text chapters list`, `nts text segments list`.
- MVP1B memory core:
  - Added `memory_items`, `memory_evidence`, `memory_audit_logs`, `memory_conflicts` migrations.
  - Implemented create/list/show/evidence/status services with memory type/status validation.
  - Every memory create and status update writes an audit log.
  - Added CLI: `nts memory create`, `nts memory list`, `nts memory show`, `nts memory evidence add`, `nts memory status set`.
- MVP1C retrieval bundle:
  - Implemented deterministic `MemoryRetriever.build_bundle()`.
  - Supports active-only retrieval, simple scope matching, exact `source_key` text matching, confidence ordering, top-k, grouped bundle output, and deterministic checksum.
  - Added CLI: `nts memory bundle --project ... --text ...` and `nts memory bundle --chapter ...`.
- MVP1D mock translation:
  - Implemented `nts translate text --chapter ... --provider mock`.
  - Uses retrieval bundle, deterministic mock model logging, `translations` rows, output `.vi.txt` artifact, `task_runs`, `model_runs`, bundle checksum, and `quality_json` skeleton.
- Commands run:
  - `python -m pytest`
  - `python -m nts_cli.main --help`
  - `python -m nts_cli.main memory --help`
  - `python -m nts_cli.main import --help`
  - `python -m nts_cli.main translate --help`
- Test result:
  - `python -m pytest` -> 18 passed.
- Known limitations:
  - Chapter detection is a simple heading heuristic with one-chapter fallback.
  - Segmentation is paragraph-based only.
  - Retrieval uses deterministic structured filtering only; no vector DB, BM25/FTS, LLM summarization, or advanced conflict resolution.
  - Translation output is intentionally mock text and must not be treated as real translation.
- Next recommended phase:
  - MVP2 correction learning foundation: import raw/AI/human corrections, create pending correction memory candidates, attach evidence, audit writes, and emit correction reports.
