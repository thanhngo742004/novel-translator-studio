# 02 — Repo Structure

Recommended structure after MVP0 scaffold:

```text
NovelTranslatorStudio/
├── apps/
│   ├── cli/
│   └── desktop/                  # deferred
├── packages/
│   ├── nts_core/
│   ├── nts_storage/
│   ├── nts_model_router/
│   ├── nts_memory/
│   ├── nts_learning/             # deferred until MVP1+
│   ├── nts_translation/          # deferred until MVP1+
│   ├── nts_alignment/            # deferred until MVP1+
│   ├── nts_quality/              # deferred until MVP1+
│   ├── nts_manga/                # deferred until MVP4+
│   └── nts_shared/
├── config/
├── docs/
├── examples/
├── migrations/
├── tests/
├── workspace-template/
└── pyproject.toml
```

## MVP0 packages

MVP0 should only create these packages if needed:

- `nts_shared`
- `nts_core`
- `nts_storage`
- `nts_model_router`
- `apps/cli`

Do not create deep empty packages unless they help the scaffold remain understandable.
