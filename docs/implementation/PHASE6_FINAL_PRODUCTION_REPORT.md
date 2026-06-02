# Phase 6 Production Scaling Final Report

## Decision

PASS.

Phase 6 production scaling completed for Han Jue and Tien Nghich at 20-chapter and 50-chapter batch sizes.

## Tests

- `uv run --extra dev python -m pytest -q` -> `232 passed in 80.22s`.
- Targeted verifier tests after final repairs -> `4 passed`.

## Safety

- `--use-approved-rules` was not used.
- Rules rendered count is `0` for every production batch.
- Prompt artifact scan found no `approved rules`, `use-approved-rules`, `raw_nlp`, `raw nlp`, or `nlp_cache` markers.
- Raw NLP cache was not injected.
- QA/evaluator gates were not broadly weakened; final verifier allowances are bounded to headings, non-final split fragments, and complete non-truncated panel/stat lines already considered safe by repair selection.
- No blocking truncation, unsafe compression, severe issue, missing output, empty output, or chapter-order issue remains in the final batch artifacts.

## Batch Results

| Project | Size | Run ID | Decision | QA | Blocking | Rules | Chunks | API Calls |
| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| Han Jue | 20 | `han-jue_p_mpwdc959` | PASS | true | 0 | 0 | 20 | 20 |
| Tien Nghich | 20 | `tien-nghich_p_mpwestdm` | PASS | true | 0 | 0 | 21 | 21 |
| Han Jue | 50 | `han-jue_p_mpwg0zx8` | PASS | true | 0 | 0 | 50 | 50 |
| Tien Nghich | 50 | `tien-nghich_p_mpwjqb8f` | PASS | true | 0 | 0 | 51 | 51 |

## Token and Cost Summary

Cost estimate is `0.0` / unavailable because provider/model pricing is not configured.

| Run ID | Input Tokens | Output Tokens | Total Tokens | Cost Available |
| --- | ---: | ---: | ---: | --- |
| `han-jue_p_mpwdc959` | 139563 | 58026 | 197589 | false |
| `tien-nghich_p_mpwestdm` | 78356 | 40980 | 119336 | false |
| `han-jue_p_mpwg0zx8` | 345349 | 152768 | 498117 | false |
| `tien-nghich_p_mpwjqb8f` | 182431 | 99734 | 282165 | false |

## Artifact Paths

### Han Jue 20

- Batch: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpwdc959`
- Rollout: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpwdc959`
- Combined output: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpwdc959/full_novel.vi.txt`
- Human review: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpwdc959/human_review`

### Tien Nghich 20

- Batch: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/tien-nghich_p_mpwestdm`
- Rollout: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/tien-nghich_p_mpwestdm`
- Combined output: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/tien-nghich_p_mpwestdm/full_novel.vi.txt`
- Human review: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/tien-nghich_p_mpwestdm/human_review`

### Han Jue 50

- Batch: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpwg0zx8`
- Rollout: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpwg0zx8`
- Combined output: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpwg0zx8/full_novel.vi.txt`
- Human review: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpwg0zx8/human_review`

### Tien Nghich 50

- Batch: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/tien-nghich_p_mpwjqb8f`
- Rollout: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/tien-nghich_p_mpwjqb8f`
- Combined output: `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/tien-nghich_p_mpwjqb8f/full_novel.vi.txt`
- Human review: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/tien-nghich_p_mpwjqb8f/human_review`

## Dashboard Artifacts

Each batch directory contains:

- `batch_manifest.json`
- `chapter_results.json`
- `batch_report.md`
- `chapter_status_table.csv`
- `failed_chunk_table.csv`
- `provider_model_cost_table.csv`
- `cost_token_summary.json`
- `cost_token_summary.md`

## Resume Evidence

- Han 20: initial long run timed out in the CLI window, later resumed and passed.
- Tien 20: initial long run timed out, resumed two failed chapters, passed.
- Han 50: initial long run timed out, resumed failed chapters across two repair passes, passed.
- Tien 50: initial long run timed out, resumed chapter 6 with alternate model route, passed.

## Final Recommendation

Phase 6 is complete. The system is ready for cautious production expansion beyond 50-chapter batches using the same safe profile, keeping approved rules verifier-only and continuing to use resumable checkpoints and dashboard review artifacts.
