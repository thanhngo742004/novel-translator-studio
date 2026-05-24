# 06 — CLI Spec

## CLI name

`nts`

## MVP0 commands

```bash
nts init
nts doctor
nts project create
nts project list
nts config validate
nts model test --provider mock
```

## JSON mode

All commands that produce meaningful output should support:

```bash
--json
```

In JSON mode, stdout should contain only machine-readable JSON.

## Exit codes

- `0` success
- `2` partial success
- `3` review required
- `4` validation error
- `5` provider error retryable
- `6` not found
- `7` config error
- `8` budget exceeded
- `9` non-retryable task failure

## Future command tree

```text
nts import text
nts import translation
nts import manga
nts learn style
nts learn correction
nts translate text
nts translate manga
nts memory list/show/review/resolve/export/bundle
nts manga detect-boxes/ocr/preview/export
nts task list/show/retry/resume/cancel
nts export vbook-profile
```
