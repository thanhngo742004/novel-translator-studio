# Phase 9 Real Provider Gate

## Purpose

Mock provider tests validate implementation logic. They do not prove Phase 9 production readiness.

The final Phase 9 canary and production rollout require a real provider gate using saved GUI/provider config and existing NTS provider preflight patterns where possible.

## Separation Of Test Types

- Unit tests: mock provider only, no network.
- Integration tests: mock provider only, no real translation provider.
- Browser smoke: may display saved provider readiness but must not expose secrets.
- Final canary: real provider preflight and real translation call required.
- Production rollout: real provider preflight and real translation calls required.

## Required Preflight Checks

The provider preflight must verify:

1. Provider type/name.
2. Base URL normalization.
3. Primary model.
4. Fallback model.
5. API key redaction.
6. Primary model route.
7. Fallback route if primary fails.
8. Minimal real request if provider supports it.
9. Artifact creation under `provider/`.
10. No raw API key in logs, artifacts, frontend responses, screenshots, or docs.

Do not rely only on `/v1/models`; some OpenAI-compatible providers do not support it reliably.

## Required Artifacts

```text
provider/provider_preflight.json
provider/provider_preflight.md
provider/model_policy_snapshot.json
provider/model_usage.jsonl
```

`provider_preflight.json` must include:

- `status`
- `provider_type`
- `provider_name`
- `base_url_normalized`
- `primary_model`
- `fallback_model`
- `api_key_env_var`
- `api_key_redacted`
- `primary_route_status`
- `fallback_route_status`
- `minimal_request_status`
- `warnings`
- `errors`

`model_usage.jsonl` must include one record per translation call:

- `timestamp`
- `project_id`
- `run_id`
- `page_id`
- `box_id`
- `provider_type`
- `provider_name`
- `model`
- `route`
- `fallback_used`
- `request_id` when available.
- `input_char_count`
- `output_char_count`
- token counts when available.

## PASS And BLOCKED Rules

Final canary PASS requires:

- Real provider preflight PASS.
- OCR/detection pipeline PASS on canary pages.
- Translation call uses verified provider/model.
- Rendered output created.
- Visual QA artifacts created.
- No raw key leak.

Production rollout PASS requires:

- Real provider preflight PASS at rollout start.
- Provider/model policy snapshot saved.
- Per-call model usage artifact saved.
- Fallback usage recorded if fallback is used.
- Production job progress backed by artifacts.
- Output images, CBZ, and PDF exported as configured.
- QA blockers equal zero.
- No raw key leak.

If provider/auth/model is unavailable, final phases must report `BLOCKED_PROVIDER_OR_ENVIRONMENT`, not PASS.

## Implementation Guidance

Prefer reusing existing NTS provider preflight infrastructure from the Phase 7.5/production rollout code path. Do not create a separate manga-only provider config system unless the existing provider config cannot represent the required provider route.

