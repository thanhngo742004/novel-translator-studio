# PHASE9F Translation Integration Goal

## 1. Objective

Translate reviewed OCR text by stable `box_id` using the existing NTS provider routing, safe prompt stack, approved dictionary, and approved memory support, while keeping approved rules verifier-only and excluding raw NLP cache.

## 2. Current Dependencies / Prerequisites

- Phase 9A manifest PASS.
- Phase 9D OCR review PASS.
- Phase 9E reading order PASS.
- Existing provider config loader and mock provider are available.
- Read `10_REAL_PROVIDER_GATE.md`, but do not run real provider calls in this phase's automated tests.

## 3. What Must Be Implemented

- Manga translation service that consumes reviewed OCR and reading order.
- Translation request records by `box_id`.
- Prompt/context bundle artifact per page or batch.
- Existing approved dictionary support.
- Existing approved memory support.
- Existing stable/hybrid prompt posture.
- Mock provider translation path for tests.
- Translation result records with provider/model metadata.
- Translation QA for missing boxes and untranslated text.
- No approved rules in prompts.
- No raw NLP cache in prompts.

## 4. What Must Not Be Implemented

- Real provider calls in unit tests.
- Final canary provider gate.
- Production rollout.
- Cleaning/inpainting.
- Typesetting/rendering.
- GUI production completion beyond a translation preview if touched.
- Auto-promotion to memory.
- Rules-in-prompt behavior.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `translation/prompt_context_bundle.json`
- `translation/box_translation_requests.jsonl`
- `translation/translation_results.json`
- `translation/translation_summary.md`
- `translation/translation_qa.json`

Each translation result includes:

- `page_id`
- `box_id`
- `source_text`
- `translated_text`
- `provider_type`
- `provider_name`
- `model`
- `route`
- `fallback_used`
- `dictionary_bundle_artifact`
- `memory_bundle_artifact`
- `prompt_context_bundle_artifact`

In mock-provider tests, provider/model metadata must clearly indicate mock mode.

## 6. Backend API Requirements

If backend is touched:

- Start translation job.
- Start single-page or selected-box translation preview.
- Read translation progress.
- Read translation result by page.
- Save manual translation correction.
- Read redacted provider readiness state.

## 7. GUI Requirements

The GUI may activate:

`4. Dịch thử`

Required behavior if touched:

- Show source OCR and Vietnamese translation by box.
- Show dictionary/memory matches at a user-friendly level.
- Hide prompt/context JSON behind `Xem chi tiết kỹ thuật`.
- Show provider/model route with API key redacted.
- Do not show raw prompt if it contains copyrighted OCR text unless inside local technical details.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Translate selected pages.
- Translate selected boxes.
- Export translation results.
- Validate missing translations.

CLI defaults must use mock provider in tests and saved provider config only when explicitly requested by local user commands.

## 9. Tests Required

Add tests for:

- Mock provider translation by box ID.
- Reading order context included.
- Approved dictionary bundle included.
- Approved memory bundle included.
- Approved rules excluded from prompt.
- Raw NLP cache excluded from prompt.
- Missing translation QA.
- Manual translation correction append/persist.
- Provider/model metadata recorded in mock mode.
- No real network calls.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if translation GUI changes:

- Step 4 opens only after OCR/review prerequisites.
- User can run translation preview with mock/local test mode.
- Translation appears by box.
- Provider route is redacted.
- `Xem chi tiết kỹ thuật` hides context bundle by default.
- No API key appears in DOM or network response.

## 11. PASS Criteria

- Translation integration tests pass with mock provider.
- Translation artifacts exist.
- Dictionary and memory separation is preserved.
- Approved rules are not in prompts.
- Raw NLP cache is not in prompts.
- Existing text-novel production behavior is unchanged.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Existing provider abstraction cannot be reused without changing text-novel behavior.
- Translation cannot be linked to stable `box_id`.
- Required dictionary/memory bundles are unavailable and cannot be safely mocked for this phase.

Use `BLOCKED_PROVIDER_ABSTRACTION`, `BLOCKED_BOX_TRANSLATION_LINK`, or `BLOCKED_MEMORY_DICTIONARY_BUNDLE`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if manga translation cannot use the existing NTS provider/dictionary/memory stack without violating the rule verifier-only policy or breaking text-novel production.

## 14. Security / Privacy Requirements

- Redact API keys.
- Do not print full copyrighted OCR text by default.
- Keep context bundles local and git-ignored.
- Do not upload images.
- Do not include raw rules or raw NLP cache in prompts.

## 15. Production-Readiness Requirements

This phase is not final production-ready because it uses mock-provider automated tests. Real provider readiness is deferred to Phase 9L and 9M.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- Translation artifact paths.
- Provider mode used.
- Evidence that rules/raw NLP cache are excluded.
- Follow-up tasks for cleaning/rendering.

