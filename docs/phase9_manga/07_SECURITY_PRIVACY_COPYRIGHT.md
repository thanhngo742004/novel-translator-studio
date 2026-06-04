# Phase 9 Security, Privacy, And Copyright

## Local Default

User images, OCR text, translated text, masks, rendered pages, and exports stay local by default.

Cloud OCR, cloud inpainting, cloud image editing, or hosted vision services must require explicit configuration and visible user confirmation.

## Copyright Rules

Do not commit:

- Copyrighted manga pages.
- Copyrighted manhua pages.
- Copyrighted manhwa pages.
- Webtoon screenshots.
- Scanlation pages.
- User-supplied source images.
- Full copyrighted OCR text dumps.
- Full translated copyrighted text dumps.

Allowed committed fixtures:

- Tiny synthetic images generated for tests.
- Synthetic OCR/detection JSON.
- Explicitly licensed sample images only if license and attribution are committed next to the fixture.

## Artifact Handling

Runtime artifacts live in the user's workspace and must be ignored by git.

Artifacts may include copyrighted user data, so they must not be copied into docs or repository fixtures. Human review packages must use bounded local previews and local paths, not public links.

## Secrets

API keys must be referenced by environment variable name. Raw API key values must not appear in:

- Markdown docs.
- JSON artifacts.
- SQLite rows.
- Logs.
- GUI backend responses.
- Frontend state.
- Browser screenshots.
- Final reports.

Provider config snapshots must redact secrets with a stable marker such as `***REDACTED***`.

## Cloud Adapter Requirements

Cloud adapters must record:

- Adapter ID.
- Provider name.
- Endpoint label.
- Whether images were uploaded.
- Redacted credential reference.
- User confirmation flag.
- Timestamp.
- Page IDs processed.

Cloud adapters must not be enabled implicitly by dependency installation.

## Log Safety

Logs and artifacts should summarize copyrighted content, not dump it.

Allowed:

- Box IDs.
- Confidence numbers.
- Character counts.
- Short synthetic snippets in tests.
- Redacted provider routes.
- Hashes and relative artifact paths.

Forbidden:

- Full raw page images embedded in logs.
- Full OCR text for user projects in console output.
- Full translated text for user projects in public docs.
- Base64 image dumps.
- API key fragments.

## Public Repo Safety

Implementation phases must check:

- `.gitignore` covers runtime manga artifacts.
- Tests use synthetic fixtures.
- No provider config with real secrets is staged.
- No generated manga output is staged.
- No user image path is committed in docs unless it is a neutral placeholder path.

## Review Policy

Any phase adding a new dependency with GPL, AGPL, commercial, or unclear license must document the risk in its final report and avoid vendoring that dependency into the NTS core until explicitly approved.

