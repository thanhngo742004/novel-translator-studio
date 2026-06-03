# PHASE7_GUI_CODEX_SKILL_USAGE

## Purpose

This file tells Codex exactly how to use installed skills for Phase 7 GUI work.

Use with:

```text
docs/gui/PHASE7_GUI_USER_FRIENDLY_UI_SPEC.md
docs/gui/PHASE7_GUI_BUTTON_FUNCTION_WIRING.md
docs/goals/PHASE7_GUI_IMPLEMENTATION_GOAL.md
```

## Skills to use

```text
lamm-t-architect
design-taste-frontend
minimalist-ui
web-design-guidelines
full-output-enforcement
```

## lamm-t-architect

Use for architecture and safety:

- preserve Dictionary / Memory / Rule separation
- keep rules verifier-only / QA-only
- never use `--use-approved-rules`
- never inject raw NLP cache
- keep project-scoped support isolated
- map UI actions to existing CLI/core behavior
- avoid frontend business-logic duplication
- ensure review edits create scoped candidates/audits, not blind global mutations

## design-taste-frontend

Use for visual polish:

- dark slate UI
- friendly local studio feel
- polished cards
- good spacing
- no generic purple AI SaaS look
- no dense developer dashboard
- meaningful status colors

## minimalist-ui

Use for end-user simplicity:

- fewer sidebar items
- fewer tables on home screen
- 3 primary action cards
- guided wizard
- short labels
- technical details hidden by default
- friendly empty states

## web-design-guidelines

Use for UX/accessibility:

- contrast
- keyboard focus
- loading states
- disabled states
- error states
- empty states
- responsive behavior
- form labels
- no key exposure
- sensible button text

## full-output-enforcement

Use when generating code:

- no TODO-only stubs for required shell features
- no incomplete files
- no visible unwired buttons
- no placeholder where the goal requires wiring
- intentional placeholders must be labeled Coming Soon / unsupported

## Skills not to use by default

Do not use unless explicitly requested:

```text
industrial-brutalism
brandkit
gpt-taste
high-end-visual-design
imagegen-frontend-web
imagegen-frontend-mobile
```

Phase 7 is a practical end-user GUI, not a premium agency landing page or visual experiment.

## Acceptance checklist

Before PASS, verify:

- UI feels like NTS Studio, not NTS Control Room
- home screen is not dense
- basic users know what to click
- technical details are hidden behind `Xem chi tiết kỹ thuật`
- every visible button is wired or explicitly placeholder
- Manga tab is Coming Soon only
- no API key appears in UI/logs
- no approved rules are enabled
