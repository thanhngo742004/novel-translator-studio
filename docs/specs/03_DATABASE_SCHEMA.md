# 03 — Database Schema

## MVP0 tables

MVP0 only needs minimal tables:

### `workspaces`

Optional. Can be skipped if workspace metadata is stored in config.

### `projects`

Fields:

- `id`
- `slug`
- `name`
- `source_lang`
- `target_lang`
- `domain`
- `genre`
- `status`
- `created_at`
- `updated_at`

### `task_runs`

Fields:

- `id`
- `task_type`
- `project_id`
- `status`
- `stage`
- `input_json`
- `state_json`
- `result_json`
- `error_json`
- `started_at`
- `finished_at`
- `created_at`

### `model_runs`

Fields:

- `id`
- `task_run_id`
- `provider_key`
- `adapter_type`
- `base_url`
- `model_name`
- `prompt_hash`
- `input_tokens`
- `output_tokens`
- `cost_estimate`
- `status`
- `started_at`
- `finished_at`

### `provider_configs`

Fields:

- `id`
- `provider_key`
- `provider_type`
- `base_url`
- `api_key_env`
- `options_json`
- `last_validated_at`
- `status`

## MVP1+ tables

Add later:

- `documents`
- `chapters`
- `segments`
- `translations`
- `memory_items`
- `memory_evidence`
- `memory_conflicts`
- `memory_audit_logs`
- `style_profiles`
- `glossary_terms`
- `character_entities`
- `export_bundles`

## MVP4+ manga tables

Add later:

- `manga_pages`
- `manga_boxes`

## Storage rules

- Store large artifacts on disk.
- Store paths/checksums/metadata in DB.
- Use SQLite for MVP.
- Use WAL mode if appropriate.
- Add FTS5 later for memory/text search.
