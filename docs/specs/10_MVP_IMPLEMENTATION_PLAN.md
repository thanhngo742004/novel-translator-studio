# 10 — MVP Implementation Plan

## MVP0 — Skeleton

Goal: create a runnable, tested project skeleton.

### Must implement

- `pyproject.toml`
- Python package structure
- CLI entrypoint `nts`
- `nts init`
- `nts doctor`
- `nts project create`
- `nts project list`
- `nts config validate`
- `nts model test --provider mock`
- workspace folder creation
- SQLite DB creation
- minimal migrations
- `projects`, `task_runs`, `model_runs`, `provider_configs` tables
- config loader for YAML
- mock provider adapter
- JSON result envelope
- tests

### Must not implement

- real translation
- real LLM calls
- memory learning
- manga pipeline
- GUI
- plugin export

### Acceptance criteria

- `nts init --workspace ./workspace` creates workspace directories and DB.
- `nts doctor --workspace ./workspace --json` returns valid JSON.
- `nts project create --slug demo --name Demo --source-lang zh --target-lang vi --json` creates a project.
- `nts project list --json` lists created project.
- `nts config validate --json` validates example configs.
- `nts model test --provider mock --json` returns deterministic mock output and logs a model run.
- `pytest` passes.

## MVP1 — Text + memory core

Later.

## MVP2 — Correction learning

Later.

## MVP3 — Plugin export

Later.

## MVP4 — Manga semi-manual

Later.

## MVP5 — GUI

Later.
