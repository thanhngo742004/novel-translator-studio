# NTS Current State

## Latest safe state

- MVP5G rule candidate engine passed.
- MVP5H full passed with dictionary + memory hybrid prompt.
- MVP5H.1 rule prompt rendering failed; rules remain verifier-only / QA-only.
- MVP5I chapters 1-2 canary passed at `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzd2o7`.
- MVP5I controlled 10-chapter rollout passed at `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mps0bq9p` with clean QA and `rules_rendered_count = 0`.
- Phase 5 strong validation delta is not passed. Latest rerun `workspace_mvp5c_smoke_20260525210758/artifacts/approved_memory_validation/han-jue_amv_1780146926259` failed: round 1 delta `-0.2`, round 2 delta `+0.6`, average delta `+0.2`, required average `+1.0`.
- Validation gating now records `average_delta` and `required_average_delta` and fails sub-target average deltas instead of labeling any positive hybrid rounds as PASS.

## Safe production / validation config

- `--use-stable-prompt`
- `--use-hybrid-prompt`
- `--use-approved-dictionary`
- `--dictionary-max-entries 8`
- `--memory-max-items 6`
- `--support-max-chars 1200`
- `--emit-prompt-artifacts`
- `--resumable`

## Do NOT use

- `--use-approved-rules`

## Current operational interpretation

- Stable prompt remains the production baseline.
- Hybrid prompt support is safe only with approved dictionary + memory support under configured caps.
- Rules are not part of the production prompt profile.
- Production QA must continue to enforce zero rules rendered into prompts and zero raw NLP cache leakage.
- Current approved dictionary/memory support is safe but insufficient to prove the required validation quality lift.
- New memory/dictionary candidates must stay human-review-only until explicitly approved; no auto-approval.

## Latest diagnostic and review artifacts

- Validation delta diagnostic: `artifacts/phase5_validation_delta/20260530T160816/validation_delta_diagnostic.md` and `.json`.
- Latest failed validation: `workspace_mvp5c_smoke_20260525210758/artifacts/approved_memory_validation/han-jue_amv_1780146926259`.
- Human-review candidates: `workspace_mvp5c_smoke_20260525210758/artifacts/memory_candidate_mining/han-jue_mining_1780147607357`.

## Recommended next phase

- Human review of mined memory/dictionary candidates for low/flat chapters, followed by an explicit approval step if warranted.
- Rerun the same safe 2-round validation profile after approvals.
- Keep approved rules verifier-only / QA-only until a separate safe validation proves prompt benefit.
