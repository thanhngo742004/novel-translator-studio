# NTS Goal Progress

## 2026-05-30T00:00:00+07:00

- Current phase: Phase A - production unit construction before translation.
- Files changed so far: `apps/cli/nts_cli/main.py`, `packages/nts_core/production_rollout.py`, `packages/nts_core/production_translation.py`.
- Latest stable test checkpoint before this entry: `uv run --extra dev python -m pytest -q` -> 197 passed.
- Latest canary before this entry: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpr6b2ns/canary_report.json` -> FAIL.
- Unsafe units remaining in that canary: 9 (`u002`, `u006`, `u016`, `u020`, `u023`, `u024`, `u032`, `u034`, `u036`).
- Rules rendered count remains 0; raw NLP cache was not injected.
- Implemented next repair direction: production pre-translation unit classification and one-input/one-output production unit plan to avoid validation-style tiny paragraph merging in production.
- New expected artifacts from next canary: `production_unit_plan.*`, `unit_classification_report.*`, and `unit_classification_table.csv` in each chunk artifact directory.
- Next action: run focused tests, full tests, then rerun chapters 1-2 canary only.


## 2026-05-30T00:47:00+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `apps/cli/nts_cli/main.py`, `packages/nts_core/production_rollout.py`, `packages/nts_core/production_translation.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q` -> 197 passed.
- Canary run: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpr7gqau/canary_report.json` -> FAIL.
- Canary stats: chapters processed 0, failed 2, skipped 0, chunks seen 2, API calls used 0 in rollout summary.
- QA blockers: chapter 1 `paragraph_exceeds_strict_max`; chapter 2 `paragraph_truncation_detected`.
- Chapter 1 unsafe units after production one-input/one-output plan: `u042`, `u104`, `u116`, `u123` strict max.
- Chapter 2 unsafe units after production one-input/one-output plan: `u125`, `u143` missing terminal punctuation.
- Improvement: previous chapter 2 strict-max blockers cleared, but terminal completion remains and chapter 1 now exposes strict-max units under the new unit plan.
- Rules rendered count remains 0; raw NLP cache was not injected.
- Next action: inspect source/output for `u042`, `u104`, `u116`, `u123`, `u125`, `u143`; add targeted pre-translation compact handling/repair for those classes without weakening QA.


## 2026-05-30T01:22:00+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `packages/nts_core/production_translation.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q` -> 197 passed.
- Canary run: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpr8p9ne/canary_report.json` -> FAIL.
- Canary stats: chapters processed 0, failed 2, skipped 0, chunks seen 2, API calls used 0 in rollout summary.
- QA blockers: chapter 1 `paragraph_exceeds_strict_max`; chapter 2 `paragraph_truncation_detected`.
- Chapter 1 unsafe units: `u042`, `u062`, `u113`, `u123` strict max.
- Chapter 2 unsafe unit: `u131` dangling glossary label / missing terminal punctuation / suspicious fragment ending.
- Improvement: chapter 2 terminal-only `u125` and separator `u143` are no longer blocking; remaining chapter 2 issue narrowed to `u131`.
- Rules rendered count remains 0; raw NLP cache was not injected.
- Next action: targeted fix for pre-panel label `u131` and chapter 1 strict-max narrative/dialogue units without lowering QA gates.


## 2026-05-30T01:30:00+07:00

- Current phase: Phase B canary gate passed; ready for Phase C controlled 10-chapter rollout.
- Files changed: `packages/nts_core/production_translation.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q` -> 197 passed.
- Canary run: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpr97xfb/canary_report.json` -> PASS.
- Canary stats: chapters processed 2, failed 0, skipped 0, chunks seen 2, API calls used 2.
- QA result: pass with 0 blocking issues; warnings were high output ratio only.
- Rules rendered count remains 0; raw NLP cache was not injected.
- Dictionary hits: 12; memory hits: 4.
- Unsafe units before latest fix: chapter 1 `u042`, `u062`, `u113`, `u123`; chapter 2 `u131`.
- Unsafe units after latest canary: 0 blocking unsafe units.
- Next action: run controlled 10-chapter rollout with safe config and without approved rules.

## 2026-05-30T09:42:44+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `packages/nts_core/production_translation.py`, `tests/test_mvp5i_production_rollout.py`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 15 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 17 passed; `uv run --extra dev python -m pytest -q` -> 201 passed.
- Latest artifact paths: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprq1qqg/provider_preflight.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprq1qqg/model_policy_snapshot.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mprq1qqg/chunk_outputs/chapter_b408a56bda234f2ebe8c082339b34d35/chunk_001/production_unit_plan.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mprq1qqg/chunk_outputs/chapter_b408a56bda234f2ebe8c082339b34d35/chunk_001/unit_split_plan.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mprq1qqg/chunk_outputs/chapter_b408a56bda234f2ebe8c082339b34d35/chunk_001/per_call_model_usage.jsonl`.
- Canary/rollout result: canary rerun attempted with `han-jue_p_mprq1qqg`; provider preflight completed, chunk-level artifacts started writing, but CLI timed out before a completed canary summary was produced.
- Unsafe units remaining: not yet re-measured from a completed canary; previous completed canary still failed chapter 2 QA.
- Deltas: not applicable yet.
- Rules rendered count remains 0 in current artifacts; raw NLP cache still not injected.
- Next action: inspect/optimize long-running chapter canary execution from `han-jue_p_mprq1qqg`, then complete a fresh chapters 1-2 canary and compare unsafe units before/after.

## 2026-05-30T10:23:02+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `packages/nts_core/production_translation.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 18 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 20 passed; `uv run --extra dev python -m pytest -q` -> 204 passed.
- Latest artifact paths: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/canary_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/production_qa_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/chapter_1_unit_safety_diagnostic.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/chapter_2_unit_safety_diagnostic.json`.
- Canary/rollout result: canary completed, but `han-jue_p_mprrw98i` still failed QA.
- Unsafe units remaining: chapter 1 -> `u088`; chapter 2 -> `u134`, `u135`. Separator-related failures were removed; remaining failures are overlong system/panel outputs.
- Deltas: not applicable yet.
- Rules rendered count remains 0; raw NLP cache remains not injected.
- Next action: align final production verification/selector treatment for safe repaired panel outputs (`u088`, `u134`, `u135`) with the validated gate logic, then rerun chapters 1-2 canary only.


## 2026-05-30T11:05:00+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `packages/nts_core/production_translation.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 20 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 22 passed; `uv run --extra dev python -m pytest -q` -> timed out in CLI due pytest stdout flush `OSError` after ~124s, no fresh full-suite completion evidence yet.
- Latest artifact paths: prior failed canary remains `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/canary_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/production_qa_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/chapter_1_unit_safety_diagnostic.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprrw98i/chapter_2_unit_safety_diagnostic.json`.
- Canary/rollout result: no fresh canary yet; diagnostic audit of `han-jue_p_mprrw98i` shows remaining blockers were `u088`, `u134`, and `u135` failing strict-max because compact panel/stat budgets under-estimated safe complete bracketed outputs, while rules rendered count stayed 0 and provider routing stayed healthy.
- Unsafe units remaining: last completed canary still reports chapter 1 `u088` and chapter 2 `u134`, `u135` as blocking strict-max units.
- Deltas if available: not available from canary stage.
- Next action: rerun chapters 1-2 canary only with safe config and resumable checkpoints, then inspect whether the mixed-panel/stat budget fix clears the remaining strict-max blockers.
- Rules rendered count remains 0: yes, latest completed canary `han-jue_p_mprrw98i` reports `rules_rendered_count = 0`.

## 2026-05-30T13:35:00+07:00

- Current phase: Phase A/B canary iteration.
- Files changed: `packages/nts_core/production_translation.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 22 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 24 passed.
- Latest artifact paths: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpryy0ur/canary_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mpryy0ur/production_qa_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpryy0ur/chunk_outputs/chapter_b408a56bda234f2ebe8c082339b34d35/chunk_001/quality_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mpryy0ur/chunk_outputs/chapter_8b62066b491d4d07b2ab3c7122a49284/chunk_001/quality_report.json`.
- Canary/rollout result: canary `han-jue_p_mpryy0ur` still FAIL; chapter 1 now passes quality, chapter 2 still fails deterministic strict-max QA.
- Unsafe units remaining: chapter 2 `u134_a` remains over strict max (`output_char_count=376`, `strict_max=342`, `budget_policy_used=production_mixed_panel_narration_compact`); chapter 1 has 0 blocking unsafe units.
- Deltas if available: not available from canary stage.
- Next action: improve mixed-panel comma splitting/repair for `u134_a` so chapter 2 canary clears, then rerun chapters 1-2 canary only. Do not run 10-chapter rollout yet.
- Rules rendered count remains 0: yes, latest canary reports `rules_rendered_count = 0` and prompt budget rows have `selected_rule_count = 0`.

## 2026-05-30T13:44:00+07:00

- Current phase: Phase B canary gate passed; ready for Phase C controlled 10-chapter rollout.
- Files changed: `packages/nts_core/production_translation.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 22 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 24 passed.
- Latest artifact paths: `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzd2o7/canary_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzd2o7/production_qa_report.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzd2o7/production_rollout_summary.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mprzd2o7/chunk_outputs/chapter_8b62066b491d4d07b2ab3c7122a49284/chunk_001/unit_split_plan.json`.
- Canary/rollout result: chapters 1-2 canary `han-jue_p_mprzd2o7` PASS; `batch_status=success`, `chapters_processed=2`, `chapters_failed=0`, `qa_pass=true`, `final_decision=PASS`.
- Unsafe units remaining: 0 blocking unsafe units in canary; both chapter chunks report `after_verification.pass=true`, empty `reasons`, and empty `overlong_paragraph_ids`.
- Deltas if available: not available from canary stage.
- Next action: run controlled 10-chapter production rollout with the safe config and without approved rules.
- Rules rendered count remains 0: yes, canary summary and QA report both report `rules_rendered_count = 0`, and prompt budget rows report `selected_rule_count = 0`.

## 2026-05-30T14:26:00+07:00

- Current phase: Phase C controlled 10-chapter rollout iteration.
- Files changed: `packages/nts_core/production_translation.py`, `packages/nts_core/eval_harness.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 23 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 25 passed.
- Latest artifact paths: prior completed 10-chapter attempt `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzkl2v/production_rollout_summary.json`, `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzkl2v/production_qa_report.json`; resumed/next attempt currently has partial artifacts under `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mps0bq9p/` and `workspace_mvp5c_smoke_20260525210758/artifacts/prod_batch/han-jue_p_mps0bq9p/`.
- Canary/rollout result: canary already passed at `han-jue_p_mprzd2o7`; first completed controlled 10-chapter rollout `han-jue_p_mprzkl2v` failed 1 chapter due `terminology_mismatch` only (`灵根资质` expected `Linh căn tư chất`). Implemented alias-aware terminology verification so accepted alignment aliases for longer glossary terms count consistently; rerun `han-jue_p_mps0bq9p` reached all 10 chunk artifact directories but command timed out before rollout summary/QA report was finalized.
- Unsafe units remaining: 0 strict-max/truncation blockers in completed 10-chapter attempt; only known completed-rollout blocker was terminology mismatch. Rerun unsafe units not finalized yet.
- Deltas if available: not available from rollout stage.
- Next action: resume/complete the same 10-chapter rollout until finalized PASS/FAIL/BLOCKED, then audit safety counters and proceed to validation if rollout is clean.
- Rules rendered count remains 0: yes in completed 10-chapter attempt `han-jue_p_mprzkl2v`; all prompt budget rows report `selected_rule_count = 0`.

## 2026-05-30T14:43:00+07:00

- Current phase: Phase D strong quality validation iteration.
- Files changed: `packages/nts_core/production_translation.py`, `packages/nts_core/eval_harness.py`, `tests/test_mvp5i_production_rollout.py`, `docs/implementation/NTS_GOAL_PROGRESS.md`.
- Tests run: `uv run --extra dev python -m pytest -q tests/test_mvp5i_production_rollout.py` -> 23 passed; `uv run --extra dev python -m pytest -q tests/test_mvp49_mvp5a.py::test_translate_text_stable_mock_creates_artifacts_and_logs tests/test_mvp5h_hybrid_prompt.py::test_translate_text_hybrid_prompt_artifacts_created tests/test_mvp5i_production_rollout.py` -> 25 passed.
- Latest artifact paths: canary `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mprzd2o7/canary_report.json`; rollout `workspace_mvp5c_smoke_20260525210758/artifacts/production_rollout/han-jue_p_mps0bq9p/production_rollout_summary.json`; validation `workspace_mvp5c_smoke_20260525210758/artifacts/approved_memory_validation/han-jue_amv_1780126334837`.
- Canary/rollout/validation result: 10-chapter rollout `han-jue_p_mps0bq9p` PASS (`batch_status=success`, `chapters_processed=10`, `chapters_failed=0`, `qa_pass=true`, `qa_blocking_issue_count=0`). Validation run `han-jue_amv_1780126334837` completed 2 rounds but does not meet Phase 5 delta target: round 1 delta `+0.5`, round 2 delta `+0.6`, average delta `+0.55`.
- Unsafe units remaining: rollout has 0 blocking unsafe units; validation severe flags empty in both rounds; regressions over 3 empty in both rounds.
- Deltas if available: round 1 baseline `91.3`, hybrid `91.8`, delta `+0.5`; round 2 baseline `90.9`, hybrid `91.5`, delta `+0.6`; average delta `+0.55`.
- Next action: do not call PASS; run/inspect another 2-round validation or evidence-backed quality improvement path to reach average delta >= +1.0 without approved rules or weakened gates.
- Rules rendered count remains 0: yes, rollout `han-jue_p_mps0bq9p` reports `rules_rendered_count = 0`; validation run used `use_approved_rules=false`.
