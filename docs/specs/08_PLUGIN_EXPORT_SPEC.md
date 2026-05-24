# 08 — Plugin Export Spec

## Principle

Plugin export is a compiler output, not a sync of full memory.

Plugin receives compact read-only memory. It does not learn.

## Compact bundle shape

```json
{
  "bundle_id": "bundle_nhom_dich_x_zh_vi",
  "profile_id": "nhom_dich_x",
  "project_id": "truyen_a",
  "language_pair": "zh-vi",
  "domain": "novel",
  "genre": "tien_hiep",
  "style_summary": "...",
  "force_terms": [],
  "force_names": [],
  "pronoun_rules": [],
  "phrase_patterns": [],
  "correction_rules": [],
  "do": [],
  "dont": [],
  "warnings": [],
  "schema_version": "lamm_t_compact_v1",
  "checksum": "sha256:..."
}
```

## Future compatibility assets

When implementing VBook export later, output:

```text
exports/<bundle_id>/
├── bundle.json
├── manifest.json
├── compat/
│   ├── Pronouns.txt
│   ├── LuatNhan.txt
│   ├── ChinesePhienAmWords.txt
│   ├── ChinesePhienAmEnglishWords.txt
│   └── StyleSummary.txt
└── checksums.txt
```

## MVP0

Do not implement plugin export in MVP0.
