---
name: lamm-t-architect
description: Use when planning, reviewing, or implementing Novel Translator Studio architecture based on LAMM-T memory. Do not use for unrelated projects.
---

# LAMM-T Architect Skill

## Purpose

Keep Codex aligned with LAMM-T while planning or coding Novel Translator Studio.

## Required reading

- `AGENTS.md`
- `docs/specs/00_PROJECT_OVERVIEW.md`
- `docs/specs/01_ARCHITECTURE_DECISIONS.md`
- `docs/specs/10_MVP_IMPLEMENTATION_PLAN.md`

## Rules

- Do not redesign LAMM-T from scratch.
- Do not replace structured memory with vector-only memory.
- Do not treat translation memory as only source-target segment pairs.
- Preserve scope, confidence, evidence, provenance, conflict handling, and audit trail.
- Keep plugin export compact and read-only.
- Keep the app local-first.
- Prefer CLI-first MVP before GUI.
- Do not implement full manga automation in MVP0.

## Expected planning output

1. Goal
2. Relevant docs read
3. Proposed files/modules
4. Risks
5. Acceptance criteria
6. Next smallest implementation step
