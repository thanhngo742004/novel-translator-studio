# PHASE9M Production Rollout Goal

## 1. Objective

Run a production-capable 10-page manga/image rollout with real provider preflight, per-call provider/model usage, resumable progress, rendered outputs, exports, QA blockers equal zero, and a final production report.

## 2. Current Dependencies / Prerequisites

- Phase 9L end-to-end canary PASS.
- Same provider config or explicitly updated saved GUI/provider config.
- User-approved source project with at least 10 pages.
- Read `06_PRODUCTION_ROLLOUT_STRATEGY.md`.
- Read `10_REAL_PROVIDER_GATE.md`.

## 3. What Must Be Implemented

- Production rollout runner for 10 pages.
- Real provider preflight at rollout start.
- Provider/model policy snapshot.
- Per-call provider/model usage artifact.
- Fallback usage recording.
- Resumable job progress.
- Artifact-backed stage status.
- Batch QA.
- Human review package.
- Image folder export.
- CBZ export.
- PDF export when enabled.
- Final production report.

## 4. What Must Not Be Implemented

- Larger batch before 10-page batch PASS.
- Fake PASS with mock provider.
- Bypassing QA blockers.
- Cloud image upload unless explicitly configured.
- Text-novel production behavior changes.
- Approved rules in prompts.
- Raw NLP cache in prompts.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `provider/provider_preflight.json`
- `provider/provider_preflight.md`
- `provider/model_policy_snapshot.json`
- `provider/model_usage.jsonl`
- all stage artifacts for processed pages.
- `qa/visual_qa_report.json`
- `qa/visual_qa_report.md`
- `human_review/review_index.md`
- `export/export_manifest.json`
- `export/export_summary.md`
- `final_rollout_report.json`
- `final_rollout_report.md`

Progress must be backed by artifacts so the job can resume after interruption.

## 6. Backend API Requirements

If backend is touched:

- Start production rollout.
- Pause/cancel if existing job system supports it.
- Resume rollout by `run_id`.
- Read rollout progress.
- Read provider preflight.
- Read QA report.
- Read export manifest.
- Read final report.

## 7. GUI Requirements

The GUI should show:

- 10-page rollout status.
- Current stage.
- Progress bar.
- Provider preflight PASS/BLOCKED state.
- Model route summary with secrets redacted.
- QA blocker count.
- Export status.
- Human review package link.
- Final report link.

Technical details stay behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden command for:

- Run production rollout for 10 pages.
- Resume rollout by `run_id`.
- Print final artifact paths.

CLI output must redact secrets and avoid full copyrighted text dumps.

## 9. Tests Required

Automated tests:

- Rollout planner uses mock provider.
- Resume reads artifacts and skips completed stages.
- Provider usage artifact format validation with redacted fixture.
- QA blockers prevent PASS.
- Export manifest required for PASS.
- No approved rules in prompts.
- No raw NLP cache in prompts.
- No API key exposure.

Local production run:

- Real provider preflight.
- Real translation calls.
- 10 pages processed.
- Rendered outputs created.
- Visual QA blockers equal zero.
- Image folder and CBZ exports created.
- PDF export created when enabled or reported unavailable.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI rollout status is touched:

- Production rollout can start only after canary PASS.
- Progress bar updates.
- Provider preflight status is redacted.
- QA blockers appear and block PASS.
- Export status appears.
- Human review package opens.
- Final report opens.
- No API key appears in DOM, network response, screenshots, or logs.

## 11. PASS Criteria

PASS requires:

- Real provider preflight PASS at rollout start.
- Provider/model policy snapshot saved.
- Per-call model usage artifact saved.
- Fallback usage recorded if fallback is used.
- Production job progress backed by artifacts.
- 10 pages processed.
- Rendered output images created.
- Image folder export created.
- CBZ export created.
- PDF export created when enabled or explicitly reported unavailable.
- QA blockers equal zero.
- Human review package created.
- Final production report created.
- No raw key leak.
- No fake provider PASS.

## 12. BLOCKED Criteria

Report BLOCKED when:

- Provider/auth/model unavailable.
- Phase 9L canary is not PASS.
- User project has fewer than 10 usable pages.
- Required local adapters are unavailable.
- QA blockers remain after allowed fix attempts.
- Filesystem prevents artifact writes.

Use `BLOCKED_PROVIDER_OR_ENVIRONMENT`, `BLOCKED_CANARY_NOT_PASS`, `BLOCKED_INSUFFICIENT_PAGES`, `BLOCKED_LOCAL_ADAPTER`, `BLOCKED_QA_BLOCKERS`, or `BLOCKED_FILESYSTEM`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if a production rollout cannot be implemented without violating provider-gate, redaction, local artifact, or Phase 5/6/7 safety constraints.

Provider unavailability, unresolved QA blockers, and missing user inputs are BLOCKED, not FAIL.

## 14. Security / Privacy Requirements

- Redact API keys everywhere.
- Keep images local unless explicit cloud adapter configuration exists.
- Do not commit rollout artifacts.
- Do not log full copyrighted OCR/translation text.
- Save provider usage without secrets.
- Preserve rules verifier-only policy.

## 15. Production-Readiness Requirements

This phase is production-ready only when PASS criteria are met. A 10-page PASS authorizes planning for larger batches but does not automatically run them.

## 16. Final Report Requirements

Report:

- PASS/BLOCKED/FAIL.
- Files changed.
- Commands/tests run.
- Provider preflight artifact path.
- Model policy snapshot path.
- Model usage artifact path.
- Pages processed.
- QA blocker count.
- Export artifact paths.
- Human review package path.
- Evidence of redaction.
- Recommended next larger-batch gate.

