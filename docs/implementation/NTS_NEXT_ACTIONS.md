# NTS Next Actions

## Phase 5 validation delta follow-up

1. Review the diagnostic artifact: `artifacts/phase5_validation_delta/20260530T160816/validation_delta_diagnostic.md`.
2. Human-review the mined candidates in `workspace_mvp5c_smoke_20260525210758/artifacts/memory_candidate_mining/han-jue_mining_1780147607357`.
3. If reviewers approve additional evidence-backed memory/dictionary entries, apply approvals through the existing review/approval commands; do not auto-approve.
4. Rerun 2-round validation with the safe config only:
   - `--use-stable-prompt`
   - `--use-hybrid-prompt`
   - `--use-approved-dictionary`
   - `--dictionary-max-entries 8`
   - `--memory-max-items 6`
   - `--support-max-chars 1200`
   - `--emit-prompt-artifacts`
   - `--resumable`
5. Do not use `--use-approved-rules`.
6. PASS remains unavailable unless both rounds are positive and average delta is at least `+1.0` with clean safety counters.

## Guardrails

- Rules rendered count must remain 0.
- Raw NLP cache must not be injected into prompts.
- Do not weaken truncation, compression, or QA gates.
- Do not hide or delete failing artifacts.
