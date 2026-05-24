# MVP0 Implementation Prompt for Codex

Use this prompt after Codex has read `AGENTS.md` and produced a plan.

```text
Implement MVP0 only.

Read:
- AGENTS.md
- docs/specs/10_MVP_IMPLEMENTATION_PLAN.md
- docs/specs/03_DATABASE_SCHEMA.md
- docs/specs/05_MODEL_ROUTING_SPEC.md
- docs/specs/06_CLI_SPEC.md

Scope:
- Create Python project skeleton.
- Add CLI `nts`.
- Add workspace init.
- Add SQLite DB setup and minimal migrations.
- Add config YAML loader and validator.
- Add tables: projects, task_runs, model_runs, provider_configs.
- Add mock provider adapter.
- Add commands:
  - nts init
  - nts doctor
  - nts project create
  - nts project list
  - nts config validate
  - nts model test --provider mock
- Add JSON output mode where practical.
- Add tests for the acceptance criteria.

Do not implement:
- real LLM provider calls
- translation pipeline
- memory learning
- manga pipeline
- GUI
- plugin export

Before editing, show a concise file plan. After editing, run tests and report files changed.
```
