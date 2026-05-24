---
name: nts-mvp0-scaffold
description: Use only when implementing MVP0 scaffold for Novel Translator Studio. It limits work to CLI, config, SQLite setup, task/model run tracking, mock provider, and tests.
---

# NTS MVP0 Scaffold Skill

## Scope

Implement only MVP0.

Allowed:

- Python package skeleton
- CLI entrypoint `nts`
- workspace init
- config validation
- SQLite DB setup
- minimal migrations
- projects/task_runs/model_runs/provider_configs tables
- mock provider adapter
- smoke tests

Forbidden:

- real translation
- real LLM calls
- memory learning
- manga pipeline
- GUI
- plugin export
- cloud/server

## Acceptance criteria

See `docs/specs/10_MVP_IMPLEMENTATION_PLAN.md`.
