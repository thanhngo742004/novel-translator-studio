# NTS Codebase Bootstrap

## Repo structure

- `apps/cli/nts_cli/main.py`: Typer CLI entrypoint with command groups for project, config, model, import, text, memory, translate, learn, export, manga, eval, nlp, dict, prompt, rule, and production.
- `packages/nts_core/`: application logic for config validation, workspace doctoring, text import, memory, learning, evaluation, NLP, dictionary, hybrid prompts, rules, translation, and production rollout.
- `packages/nts_storage/`: SQLite connection/migration helpers and local workspace initialization/discovery.
- `migrations/`: incremental SQLite schema files from MVP0 through MVP5G.
- `tests/`: CLI and feature tests from MVP0 through MVP5I.
- `artifacts/`: generated run outputs, evaluations, NLP cache, dictionaries, rollout audits, reports, and export bundles.
- `config/`: repo-level config examples and workspace configs.
- `docs/implementation/`: implementation progress and operating docs.
- `docs/research/`: research notes supporting later MVP phases.

## Main CLI commands

- `nts init`: initialize local workspace folders, default config files, and `nts.db`.
- `nts doctor`: inspect workspace health.
- `nts project create|list`: manage projects.
- `nts config validate`: validate provider and routing config without real provider calls.
- `nts model test --provider mock`: deterministic mock provider smoke path.
- `nts import text`, `nts text chapters list`, `nts text segments list`: import and inspect text.
- `nts memory create|list|show|bundle` and `nts memory evidence add`: manage structured LAMM-T memory and evidence.
- `nts translate text|batch`: stable translation pipeline with optional hybrid prompt supports.
- `nts learn ...`: learning loop, approved-memory validation, ablation, mining, and diagnostics.
- `nts nlp ...`: chapter analysis, cache build, status, quality checks, and human review package generation.
- `nts dict ...`: project dictionary prepare, build, review, approve, reject, export, inspect, and status.
- `nts prompt inspect`: inspect hybrid prompt support rendering.
- `nts rule ...`: extract, review, approve, reject, scope, diagnose, and export rules.
- `nts production rollout`: controlled production rollout with QA gating and prompt artifact review.

## Core modules and responsibilities

- `packages/nts_storage/database.py`: SQLite connection, migration application, run-row helpers, JSON helpers.
- `packages/nts_storage/workspace.py`: local-first workspace directory creation, default YAML creation, workspace discovery.
- `packages/nts_core/config.py`: provider/routing config parsing and validation.
- `packages/nts_core/projects.py`: project CRUD helpers.
- `packages/nts_core/text_import.py`: text import, chapter extraction, segment listing, and source artifact registration.
- `packages/nts_core/memory.py`: structured memory items, evidence, status transitions, retrieval bundle generation.
- `packages/nts_core/chinese_nlp.py`: optional NLP sidecar integration, fallback analysis, cache artifacts, and diagnostics.
- `packages/nts_core/dictionary.py`: dictionary candidate extraction/review/approval/export and approved dictionary retrieval.
- `packages/nts_core/rules.py`: rule candidate pipeline, approvals, scoping, verifier-only handling, and diagnostics.
- `packages/nts_core/hybrid_prompt.py`: dictionary + memory + optional rule support selection and rendering inspection.
- `packages/nts_core/production_translation.py`: chapter and batch translation with stable prompt and support caps.
- `packages/nts_core/production_rollout.py`: safe production profile, rollout config snapshot, QA blocking checks, and human review bundle.
- `packages/nts_core/approved_memory_validation.py` and `packages/nts_core/memory_impact.py`: approved-memory validation, replay, ablation, mining, rollback, and scoping.
- `packages/nts_core/manga.py`: early manga data-layer commands and manifest/box import-export support.

## Migration/schema summary

- `migrations/0001_mvp0_initial_tables.sql`: `projects`, `task_runs`, `model_runs`, `provider_configs`.
- `migrations/0002_mvp1_text_tables.sql`: `documents`, `chapters`, `segments`, `translations`.
- `migrations/0003_mvp1_memory_tables.sql`: `memory_items`, `memory_evidence`, `memory_audit_logs`, `memory_conflicts`.
- `migrations/0004_mvp3_export_bundles.sql`: `export_bundles`.
- `migrations/0005_mvp4a_manga_tables.sql`: manga page, artifact, box, OCR, translation, export, and visual evidence tables.
- `migrations/0006_mvp5e_nlp_analysis_runs.sql`: `nlp_analysis_runs` for cache metadata.
- `migrations/0007_mvp5f_project_dictionary.sql`: dictionary run, candidate, evidence, approved entry, and audit tables.
- `migrations/0008_mvp5g_rule_candidates.sql`: rule run, candidate, evidence, approved rule, audit, and conflict tables.

## Test suite summary

- MVP smoke and scaffolding tests exist in `tests/test_mvp0_cli.py` through `tests/test_mvp3.py`.
- Manga schema/data-layer tests exist in `tests/test_mvp4a.py`.
- Learning, resumable learning, approved-memory validation, memory impact, NLP, dictionary, rules, hybrid prompt, and production rollout tests exist in `tests/test_mvp5*.py`.
- The current suite includes explicit production rollout QA checks that block prompt-rendered rules and raw NLP cache leakage.

## Artifact folders and what they mean

- `artifacts/raw`, `artifacts/normalized`, `artifacts/translated`: source, normalized, and translated text artifacts.
- `artifacts/nlp`: chapter-level NLP cache, manifest, and analysis reports.
- `artifacts/dictionaries`: dictionary build manifests, candidate JSONL, review files, and exports.
- `artifacts/approved_memory_validation`: approved-memory validation runs and replay diagnostics.
- `artifacts/approved_memory_ablation`: cached impact analysis for approved memory.
- `artifacts/memory_candidate_mining`: mined candidate review packs and simulations.
- `artifacts/production_rollout`: rollout summaries, config snapshots, QA reports, and human review bundles.
- `artifacts/prod_batch`: shortened batch artifact root used to avoid Windows path-length issues.
- `artifacts/manga`: imported pages, boxes, manifests, and manga-derived artifacts.
- `artifacts/exports`: compiled bundle outputs.
- `artifacts/reports`: general reports.
- `logs/runs`, `reviews`, `cache`: local-first runtime support folders created by workspace init.

## Current Phase 5 status

- MVP5G rule candidate engine is implemented and passed.
- MVP5H full passed with dictionary + memory hybrid prompt.
- MVP5H.1 rule prompt rendering failed validation.
- MVP5H.1.1 keeps scoped or disabled rules out of production prompts; rules are verifier-only / QA-only for now.
- MVP5I rollout support exists in CLI and core code.
- Real MVP5I rollout is not yet safe for full production use because provider/model routing and chapter 2 production QA still need preflight and diagnosis work.

## Safe production config

Use this profile for production-style runs until MVP5I.1 is complete:

- `--use-stable-prompt`
- `--use-hybrid-prompt`
- `--use-approved-dictionary`
- `--dictionary-max-entries 8`
- `--memory-max-items 6`
- `--support-max-chars 1200`
- `--emit-prompt-artifacts`

Do not use:

- `--use-approved-rules`

## Known failed/blocked configs

- `--use-approved-rules` is intentionally rejected by `nts production rollout`; the code warns that MVP5H.1 rule prompt rendering failed validation.
- Any production prompt path that renders rules is unsafe until future validation proves positive quality impact.
- Real rollout remains blocked on provider/model route robustness and chapter 2 production QA diagnosis.
- If both primary and fallback provider/model routes fail, rollout should block before translation in MVP5I.1.

## LAMM-T policy

- Dictionary, Memory, and Rule are separate layers.
- Dictionary is the canonical source -> target glossary layer.
- Memory stores decisions, corrections, confidence, evidence, provenance, conflicts, and audit history.
- Rule stores context-bound behavioral guidance and guards.
- Do not mix these layers in storage, approval, retrieval, or prompt policy.
- Never overwrite conflicts in place; use challenger/conflict records and audit trails.
- No auto-approval.
- No raw NLP cache in production prompts.
- No pending, rejected, deprecated, harmful, or insufficient-evidence items in production prompts.
- Rules are verifier-only / QA-only until future validation explicitly proves prompt benefit.

## Next recommended phase

- MVP5I.1: provider/model preflight, fallback model handling, chapter 2 QA diagnostic, and canary rollout support.
