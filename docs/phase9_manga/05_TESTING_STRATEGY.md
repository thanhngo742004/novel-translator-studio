# Phase 9 Testing Strategy

## Core Rule

Automated unit and integration tests must not call real providers, cloud OCR, cloud inpainting, or network services. Real provider calls are allowed only in final local canary and production rollout phases.

## Unit Tests

Unit tests use mocked adapters and tiny synthetic images.

Required coverage:

- Project creation and manifest ID stability.
- Folder, single image, CBZ/ZIP, and optional PDF import planning.
- Image hash and page ordering.
- Preprocessing variants with synthetic images.
- Detection adapter interface and mocked detector outputs.
- OCR adapter interface and mocked OCR outputs.
- OCR confidence parsing.
- OCR correction append-only behavior.
- Reading order algorithms for right-to-left, left-to-right, and webtoon flow.
- Translation prompt/context bundle creation with mock provider.
- No approved rules in prompts.
- No raw NLP cache in prompts.
- Dictionary and memory included only through approved bundles.
- Cleaning mask generation.
- Fill and OpenCV inpaint adapter boundaries with synthetic images.
- Typesetting line wrapping and overflow detection.
- Rendered image creation.
- Visual QA blocker generation.
- CBZ export ordering.
- PDF export adapter behavior when dependency is available.
- Redaction of API keys and provider secrets.
- Log-safety checks for full image/text dumps.

## Integration Tests

Integration tests may use local filesystem operations and image rendering, but still use mock provider and mock OCR unless a local OCR adapter has a small deterministic fixture mode.

Required coverage:

- Import creates artifact directories and `page_manifest.json`.
- Each stage writes JSON and Markdown summaries.
- Stage reruns do not destroy earlier review/correction records.
- Translation uses existing provider config loader with mock provider.
- Visual QA report links back to pages, boxes, and artifacts.
- Export manifest matches output files.
- GUI backend endpoints return redacted JSON.
- Job progress is artifact-backed and resumable.

## Browser Smoke Tests

Browser smoke tests validate the GUI manually or through Playwright after GUI subphases begin.

Required checks:

- Manga/Image tab loads.
- Wizard steps match the required Vietnamese labels.
- Buttons are wired or explicitly placeholder.
- Import flow creates a manifest.
- Detection/OCR progress appears.
- OCR correction can be saved.
- Technical details are hidden until `Xem chi tiết kỹ thuật` is opened.
- Translation canary shows provider/model route without API key exposure.
- Render preview is visible at desktop and mobile-like widths.
- QA blocker panel updates.
- Export outputs can be opened from the GUI.

## Real Provider Tests

Real provider tests are not CI tests.

They are required only for:

- `PHASE9L_END_TO_END_CANARY_GOAL.md`
- `PHASE9M_PRODUCTION_ROLLOUT_GOAL.md`

Real provider gate requirements:

- Use saved GUI/provider config.
- Run real provider preflight before translation.
- Verify provider type/name.
- Verify base URL normalization.
- Verify primary model.
- Verify fallback model.
- Redact API key.
- Test primary model route.
- Test fallback route if primary fails.
- Use a minimal real request if the provider supports it.
- Do not rely only on `/v1/models` because some OpenAI-compatible providers do not support it reliably.
- Save provider preflight artifacts under `provider/`.

If provider/auth/model is unavailable, the final canary or rollout reports `BLOCKED_PROVIDER_OR_ENVIRONMENT`, not PASS.

## Production Tests

Production tests are local runbooks with real artifacts:

- Canary on 1 to 3 pages.
- Batch on 10 pages.
- Larger batch only after canary and 10-page batch pass.
- Real provider preflight at start.
- OCR/detection pipeline evidence.
- Real translation calls in final canary and rollout.
- Per-call provider/model usage artifacts.
- Progress backed by artifacts.
- Rendered images created.
- CBZ/PDF exports created.
- QA blockers equal zero.
- Human review package created.
- No API key leak.
- No full copyrighted image/text dump in logs.

## Test Fixtures

Committed fixtures must be synthetic, tiny, and non-copyrighted.

Allowed fixture examples:

- White image with black rectangles and synthetic text.
- Tiny speech-bubble-like image drawn by the test.
- Tiny ZIP/CBZ built from synthetic images.
- Synthetic OCR JSON.
- Synthetic detection JSON.
- Synthetic rendered output baselines only if they are small and deterministic.

Forbidden fixtures:

- Copyrighted manga pages.
- Real scanlation pages.
- Full copyrighted text extracted from a manga or webtoon.
- Raw user images.
- API keys or provider config files containing secrets.

## Required Sanity Checks

Each implementation subphase should run:

```powershell
python -m pytest tests/<phase9_test_file>.py
git diff --check -- docs packages apps tests migrations
```

GUI subphases should also run the existing frontend/backend checks and a browser smoke script or documented manual browser checklist.

