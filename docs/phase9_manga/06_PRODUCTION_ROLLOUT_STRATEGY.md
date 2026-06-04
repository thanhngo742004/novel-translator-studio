# Phase 9 Production Rollout Strategy

## Principle

Production readiness requires real local evidence. Passing unit tests with mock providers is necessary, but it is not enough for Phase 9 canary or rollout PASS.

## Rollout Stages

### Stage 1: Local Synthetic Validation

- Synthetic images only.
- Mock OCR.
- Mock provider.
- No real network.
- Validate schema, artifacts, rendering, QA, and export.

### Stage 2: User Canary

- 1 to 3 user-provided pages.
- Local OCR/detection unless user explicitly configures cloud adapters.
- Real provider preflight required.
- Real translation call required.
- Rendered output required.
- Visual QA artifacts required.
- Human review package required.

### Stage 3: 10-Page Batch

- 10 pages from the same user project.
- Real provider preflight at start.
- Provider/model policy snapshot saved.
- Per-call model usage saved.
- Resumable job progress.
- Output images and CBZ/PDF exports.
- QA blockers must be zero.

### Stage 4: Larger Batch

- Larger batch only after the 10-page batch passes.
- Same gates as Stage 3.
- Add throughput, cost, and recovery reporting.

## Real Provider Gate

The final canary and rollout must use existing NTS provider preflight infrastructure where possible.

The gate must verify:

- Provider type/name.
- Normalized base URL.
- Primary model.
- Fallback model.
- API key redaction.
- Primary model route.
- Fallback route if primary fails.
- Minimal real request if the provider supports it.

The gate must not rely only on `/v1/models`.

If provider/auth/model is unavailable, report `BLOCKED_PROVIDER_OR_ENVIRONMENT`, not PASS.

## Rollout Artifacts

Required rollout artifacts:

- `provider/provider_preflight.json`
- `provider/provider_preflight.md`
- `provider/model_policy_snapshot.json`
- `provider/model_usage.jsonl`
- `qa/visual_qa_report.json`
- `qa/visual_qa_report.md`
- `human_review/review_index.html` or `human_review/review_index.md`
- `export/export_manifest.json`
- `export/export_summary.md`
- `final_rollout_report.json`
- `final_rollout_report.md`

## QA Blockers

Production rollout PASS requires QA blockers equal zero.

Blockers include:

- Missing page in manifest.
- Missing required box ID.
- Missing OCR for a translatable box.
- Missing translation for a translatable box.
- Provider preflight failure.
- Translation call through unverified provider route.
- Unredacted API key in artifact or UI response.
- Render overflow not explicitly accepted by reviewer.
- Rendered text outside its assigned region.
- Missing rendered output page.
- Export page count mismatch.
- Export page order mismatch.

## Resumability

Production jobs must be resumable by `run_id`.

On resume:

- Read existing manifest.
- Read completed stage summaries.
- Skip completed pages unless `--force` is requested.
- Preserve manual OCR corrections.
- Preserve user-edited reading order.
- Preserve user-approved render edits.
- Append new provider usage records rather than overwriting previous usage.

## Final PASS Criteria

Phase 9M production rollout is PASS only when:

- Real provider preflight PASS at rollout start.
- Provider/model policy snapshot saved.
- Per-call provider/model usage saved.
- Fallback usage recorded if fallback is used.
- Job progress backed by artifacts.
- Output images exported.
- CBZ export created.
- PDF export created when enabled.
- QA blockers equal zero.
- Human review package created.
- Final rollout report created.
- No raw API key leak.
- No full copyrighted image/text dump in logs or artifacts.

