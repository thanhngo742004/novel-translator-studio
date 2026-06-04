# PHASE9L End To End Canary Goal

## 1. Objective

Run a 1 to 3 page end-to-end manga/image canary with real provider preflight and real translation call evidence, producing rendered output, visual QA artifacts, and a human review package.

## 2. Current Dependencies / Prerequisites

- Phase 9A-K PASS.
- Saved GUI/provider config exists or can be created by the user.
- Local OCR/detection adapters are configured, or the canary explicitly uses mocked OCR/detection fixtures while still requiring real translation.
- Read `10_REAL_PROVIDER_GATE.md`.
- Read `06_PRODUCTION_ROLLOUT_STRATEGY.md`.

## 3. What Must Be Implemented

- Canary runner for 1 to 3 pages.
- Real provider preflight using saved GUI/provider config.
- Provider type/name verification.
- Base URL normalization verification.
- Primary model verification.
- Fallback model verification.
- API key redaction verification.
- Primary route minimal real request when supported.
- Fallback route test if primary fails.
- End-to-end pipeline execution through import, preprocess, detection/OCR, review state, reading order, translation, cleaning, rendering, QA, and review package.
- Real translation call through verified provider/model.
- Canary final report.

## 4. What Must Not Be Implemented

- Production 10-page rollout.
- Larger batch.
- Fake PASS from mock provider.
- New provider config system.
- Uploading images to cloud unless explicitly configured.
- Bypassing human review package.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `provider/provider_preflight.json`
- `provider/provider_preflight.md`
- `provider/model_policy_snapshot.json`
- `provider/model_usage.jsonl`
- all stage artifacts from import through QA.
- `human_review/review_index.md`
- `final_canary_report.json`
- `final_canary_report.md`

Provider artifacts must redact secrets.

## 6. Backend API Requirements

If backend is touched:

- Start canary job.
- Read canary progress.
- Read provider preflight status.
- Read canary final report.
- Open human review package.

## 7. GUI Requirements

The GUI should expose canary status in the Manga/Image tab or a production/testing panel:

- Canary page count.
- Provider preflight status.
- Current stage.
- QA blocker count.
- Human review package link.
- Final canary report link.

Technical provider details stay behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden command for:

- Run end-to-end canary on 1 to 3 pages.

CLI must require an explicit flag or config for real provider use and must redact secrets in output.

## 9. Tests Required

Automated tests:

- Canary runner planning with mock provider.
- Provider preflight artifact parser with redacted fixture.
- Failure maps to `BLOCKED_PROVIDER_OR_ENVIRONMENT`.
- No API key exposure checks.

Manual/local canary:

- Real provider preflight.
- Real translation request.
- Render output exists.
- Visual QA artifacts exist.
- Human review package exists.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI canary status is touched:

- Canary start/status is visible.
- Provider preflight status is visible and redacted.
- Progress updates.
- QA blocker count appears.
- Human review package opens.
- Final canary report opens.
- No API key appears.

## 11. PASS Criteria

PASS requires:

- Real provider preflight PASS.
- OCR/detection pipeline PASS on canary pages.
- Translation call uses verified provider/model.
- Rendered output created.
- Visual QA artifacts created.
- Human review package created.
- Final canary report created.
- No raw API key leak.
- No fake provider PASS from mocked tests.

## 12. BLOCKED Criteria

Report BLOCKED when:

- Provider/auth/model unavailable.
- Saved GUI/provider config missing and cannot be supplied.
- Local OCR/detection adapter required by canary scope is unavailable.
- User canary images are unavailable.
- Filesystem prevents artifact writes.

Use `BLOCKED_PROVIDER_OR_ENVIRONMENT`, `BLOCKED_PROVIDER_CONFIG`, `BLOCKED_OCR_DETECTION_ADAPTER`, `BLOCKED_CANARY_INPUT`, or `BLOCKED_FILESYSTEM`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if the canary runner cannot execute the end-to-end pipeline without violating provider-gate, privacy, or artifact requirements.

Provider unavailability is BLOCKED, not FAIL.

## 14. Security / Privacy Requirements

- Redact API keys.
- Keep user images local unless cloud adapters explicitly configured.
- Do not dump full OCR/translation text in console logs.
- Do not commit canary artifacts.
- Save provider/model usage without secrets.

## 15. Production-Readiness Requirements

This is the first real production-readiness gate. It proves the full pipeline on 1 to 3 pages only. It does not authorize larger production rollout until PASS.

## 16. Final Report Requirements

Report:

- PASS/BLOCKED/FAIL.
- Files changed.
- Commands/tests run.
- Provider preflight artifact path.
- Provider/model route used.
- Canary pages processed.
- Rendered output paths.
- QA blocker count.
- Human review package path.
- Evidence of API key redaction.

