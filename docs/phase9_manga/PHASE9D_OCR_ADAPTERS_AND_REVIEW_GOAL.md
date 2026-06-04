# PHASE9D OCR Adapters And Review Goal

## 1. Objective

Add OCR adapter infrastructure and an OCR review/correction workflow so every translatable box can have auditable source text before reading order and translation.

## 2. Current Dependencies / Prerequisites

- Phase 9A manifest PASS.
- Phase 9B preprocessing PASS.
- Phase 9C boxes PASS.
- Read `01_RESEARCH_SUMMARY.md` OCR section.
- Review Manga OCR, PaddleOCR, EasyOCR, Tesseract, and cloud OCR tradeoffs.

## 3. What Must Be Implemented

- OCR adapter interface.
- Mock OCR adapter for tests.
- Local adapter registration model.
- Japanese Manga OCR adapter plan or implementation if dependency is approved.
- PaddleOCR adapter plan or implementation if dependency is approved.
- Optional cloud OCR adapter boundary with explicit opt-in, even if no cloud adapter is implemented yet.
- OCR result records by `box_id`.
- OCR confidence report.
- OCR correction records appended without overwriting original OCR.
- Review state: pending, approved, corrected, ignored, not_translatable.
- Correction-to-dictionary/memory candidate artifact, without auto-promotion.

## 4. What Must Not Be Implemented

- Translation.
- Reading order automation beyond preserving box order fields.
- Cleaning/inpainting.
- Typesetting.
- Mandatory cloud OCR.
- Auto-promotion of OCR corrections into dictionary or memory.
- Full raw copyrighted OCR text dumps in logs.

## 5. Data Model / Artifact Requirements

Required artifacts:

- `ocr/ocr_results.json`
- `ocr/page_<index>_ocr.json`
- `ocr/ocr_confidence_report.json`
- `ocr/ocr_confidence_report.md`
- `ocr/ocr_corrections.jsonl`
- `ocr/ocr_review_summary.md`
- `ocr/memory_dictionary_candidates.jsonl`

Each OCR result references:

- `page_id`
- `box_id`
- `adapter_id`
- `adapter_version`
- `text`
- `confidence`
- `language_detected`
- `orientation_detected`
- `raw_output_artifact`
- `review_state`

## 6. Backend API Requirements

If backend is touched:

- Start OCR job.
- Read OCR progress.
- Read OCR results for page.
- Save OCR correction.
- Mark OCR as approved.
- Mark box as not translatable.
- Read OCR confidence summary.

Backend responses must bound text payloads for list views and must not expose full user OCR dumps unnecessarily.

## 7. GUI Requirements

The GUI may activate:

`3. Kiểm tra OCR`

Required UI behavior:

- Page preview with boxes.
- Box list with confidence.
- OCR text editor for selected box.
- Save correction.
- Approve OCR.
- Mark not translatable.
- Display low-confidence warnings.
- Hide raw adapter output behind `Xem chi tiết kỹ thuật`.

## 8. CLI Requirements If Applicable

Add or harden commands for:

- Run OCR.
- Export OCR JSON.
- Import OCR corrections.
- Approve OCR corrections from a review file.

CLI must avoid printing full copyrighted OCR content by default.

## 9. Tests Required

Add tests for:

- Mock OCR adapter.
- OCR results linked to box IDs.
- Confidence report.
- Correction append-only behavior.
- Review state transitions.
- Not-translatable boxes skipped by later translation planning.
- Candidate artifact generation without auto-promotion.
- API key redaction for optional cloud adapter config.
- No network calls.

## 10. Manual Browser Smoke Checklist If GUI Is Involved

Required if OCR GUI changes:

- Step 3 opens.
- OCR text appears for selected synthetic box.
- Correction can be saved.
- Correction history is visible.
- Low-confidence badges appear.
- Mark not translatable works.
- Technical raw output is hidden by default.
- No API key or secret appears.

## 11. PASS Criteria

- OCR adapter tests pass.
- OCR artifacts exist.
- Corrections append instead of overwrite.
- OCR review state is persisted.
- No real provider calls.
- No cloud upload unless explicitly configured outside unit tests.

## 12. BLOCKED Criteria

Report BLOCKED only when:

- Existing box schema cannot link OCR results to stable `box_id`.
- OCR dependency approval is required but unresolved for a required adapter.
- GUI OCR review is required by the active goal but no current GUI surface can host it safely.

Use `BLOCKED_OCR_SCHEMA`, `BLOCKED_DEPENDENCY_APPROVAL`, or `BLOCKED_GUI_SURFACE`.

## 13. FAIL Criteria Only For True Exhausted Implementation Impossibility, Not First Failure

FAIL is appropriate only if OCR review cannot be implemented without overwriting source OCR or violating local-first privacy constraints.

## 14. Security / Privacy Requirements

- Local OCR default.
- Cloud OCR explicit opt-in.
- Redact cloud credentials.
- Do not log full OCR text for user projects.
- Store raw adapter outputs only in local ignored artifacts.
- Do not commit OCR outputs from copyrighted pages.

## 15. Production-Readiness Requirements

OCR is production-foundation ready when every translatable box can have reviewed source text, confidence, provenance, and correction history. Full production PASS waits for later real provider canary/rollout.

## 16. Final Report Requirements

Report:

- Files changed.
- Commands/tests run.
- OCR adapters implemented or planned.
- Review workflow status.
- Artifact paths.
- Known OCR limitations by language/orientation.

