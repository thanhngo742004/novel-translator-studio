# PHASE9I Visual QA Goal

## 1. Objective

Add automated and reviewable visual QA for the manga/image pipeline, producing blocker reports and human review artifacts before export.

## 2. Current Dependencies / Prerequisites

- Phase 9D OCR artifacts.
- Phase 9F translation artifacts.
- Phase 9H rendered page artifacts.
- Read `05_TESTING_STRATEGY.md` and `09_ARTIFACT_CONVENTIONS.md`.

## 3. What Must Be Implemented

- Visual QA service.
- Missing OCR checks.
- Missing translation checks.
- Low OCR confidence checks.
- Detection low-confidence checks.
- Overflow checks from rendering.
- Unreadable small text checks when font metrics exist.
- Rendered text outside box checks.
- Page order checks.
- Export readiness checks.
- Raw text residue check as a best-effort local heuristic if feasible.
- Human review package with bounded previews.
- QA blocker and warning classification.

## 4. What Must Not Be Implemented

- Subjective artistic quality scoring as a hard PASS gate.
- Cloud vision QA by default.
- Production rollout.
- Export packaging.
- Real provider calls.
- Committing review images.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `qa/visual_qa_report.json`
- `qa/visual_qa_report.md`
- `qa/page_review_index.json`
- `qa/blockers.json`
- `human_review/review_index.md`
- `human_review/previews/`
- `human_review/review_notes.jsonl`

Each QA issue includes:

- `issue_id`
- `severity`
- `page_id`
- `box_id` when applicable.
- `stage`
- `message`
- `artifact_ref`
- `recommended_action`
- `blocks_export`

## 6. Backend API Requirements

If backend is touched:

- Start visual QA job.
- Read QA progress.
- Read QA report.
- Read blocker list.
- Save human review note.
- Mark issue accepted or resolved.

## 7. GUI Requirements

The GUI may activate:

`6. Kiểm tra hình`

Required behavior:

- Show blocker count.
- Show warning count.
- Show page preview.
- Show before/after comparison when available.
- Let user navigate to the relevant page/box.
- Let user add review notes.
- Hide raw JSON behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Run visual QA.
- Export QA report.
- Open or print human review package path.
- Validate export readiness.

## 9. Tests Required

Add tests for:

- Missing OCR blocker.
- Missing translation blocker.
- Low-confidence warning/blocker threshold.
- Overflow blocker.
- Rendered text outside box blocker.
- Page order mismatch blocker.
- Human review package generation.
- QA report Markdown and JSON creation.
- No copyrighted fixtures.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if GUI QA changes:

- Step 6 opens.
- QA run starts.
- Progress appears.
- Blockers appear.
- Clicking blocker navigates to page/box.
- Human review note saves.
- Technical JSON hidden by default.

## 11. PASS Criteria

- Visual QA tests pass.
- QA artifacts exist.
- Blockers are machine-readable.
- Human review package exists.
- Export readiness is false when blockers exist.
- No real provider calls.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Rendered artifacts required for QA are missing.
- Existing artifact model cannot link QA issues to page/box IDs.
- GUI-required review package cannot be safely displayed.

Use `BLOCKED_RENDERED_ARTIFACTS`, `BLOCKED_QA_LINKAGE`, or `BLOCKED_GUI_REVIEW`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if QA blockers cannot be represented or linked to artifacts without breaking the stable ID model.

## 14. Security / Privacy Requirements

- Keep human review package local.
- Use bounded previews.
- Do not copy review images into repo docs.
- Do not dump full OCR/translation text in logs.
- Redact provider details if QA links provider artifacts.

## 15. Production-Readiness Requirements

Visual QA is production-foundation ready when blockers are explicit and export readiness is enforced. Production rollout PASS requires QA blockers equal zero.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- QA checks implemented.
- Blocker categories.
- Artifact paths.
- Remaining QA limitations.

