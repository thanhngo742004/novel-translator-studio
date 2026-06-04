# Phase 9 GUI Integration Plan

## GUI Principle

The Manga/Image tab must feel like a guided end-user workflow, not a technical console. Technical information remains available behind `Xem chi tiết kỹ thuật`.

The GUI should follow the existing Phase 7 pattern: server-side config, artifact-backed jobs, progress polling, redacted provider status, and browser-smoke verification.

## Wizard Flow

The Manga/Image tab must expose these seven steps:

1. Chọn ảnh/CBZ/PDF
2. Nhận diện chữ
3. Kiểm tra OCR
4. Dịch thử
5. Chèn chữ vào ảnh
6. Kiểm tra hình
7. Xuất CBZ/PDF

## Screen Requirements

### 1. Chọn ảnh/CBZ/PDF

Controls:

- Select source folder.
- Select CBZ/ZIP.
- Select PDF if PDF import is enabled.
- Select single image canary.
- Choose content type: manga, manhua, manhwa, webtoon, generic image.
- Choose reading direction preset.
- Create project/import button.

Visible outputs:

- Page count.
- First/last page preview.
- Import warnings.
- Next step button only after manifest exists.

### 2. Nhận diện chữ

Controls:

- Select detection adapter.
- Select OCR adapter.
- Run detection/OCR.
- Stop/cancel if job cancellation exists.

Visible outputs:

- Progress bar.
- Pages processed.
- Detected region count.
- Low-confidence count.
- Review button.

### 3. Kiểm tra OCR

Controls:

- Page selector.
- Region list.
- Image preview with boxes.
- OCR correction text area.
- Mark as approved.
- Mark as not translatable.
- Adjust reading order.

Visible outputs:

- Confidence badges.
- Correction history.
- Region type.
- `Xem chi tiết kỹ thuật` with adapter raw output path and redacted diagnostics.

### 4. Dịch thử

Controls:

- Run translation canary for current page or selected boxes.
- Use saved provider config button.
- Show provider preflight status when available.

Visible outputs:

- Source OCR text.
- Vietnamese translation.
- Dictionary/memory matches.
- Provider route with API key redacted.
- Translation warnings.

### 5. Chèn chữ vào ảnh

Controls:

- Choose cleaning mode: fill, OpenCV inpaint, optional configured adapter.
- Choose render profile.
- Render selected page.

Visible outputs:

- Cleaned preview.
- Rendered preview.
- Overflow warnings.
- Unrendered box count.

### 6. Kiểm tra hình

Controls:

- Run visual QA.
- Open human review package.
- Approve page.
- Send page back to OCR, translation, cleaning, or rendering stage.

Visible outputs:

- QA blocker count.
- Warning count.
- Page comparison preview.
- Export readiness.

### 7. Xuất CBZ/PDF

Controls:

- Export image folder.
- Export CBZ.
- Export PDF when enabled.
- Open output folder.

Visible outputs:

- Export manifest.
- Output paths.
- Page count.
- File size.
- Final report link.

## Backend Endpoint Requirements

The GUI backend should expose endpoints for:

- Load manga tab status.
- Create/import project.
- List projects.
- List pages.
- Read page manifest.
- Start preprocessing.
- Start detection/OCR.
- Read stage progress.
- Read and update OCR corrections.
- Read and update boxes.
- Read and update reading order.
- Start translation canary.
- Start cleaning/rendering.
- Start visual QA.
- Read QA report.
- Start exports.
- Read export manifest.
- Open artifact folder.

All endpoints returning provider data must redact secrets.

## Accessibility And Usability Requirements

Use the web interface guidance as a review checklist: visible focus states, keyboard-reachable controls, readable text, clear labels, sufficient contrast, status messages for long jobs, and no hidden critical errors.

Design direction:

- Quiet, utilitarian layout.
- Clear wizard progress.
- Dense but readable region review table.
- Stable preview dimensions.
- No decorative landing page.
- No brand-heavy or industrial visual treatment.
- Avoid explaining every feature in visible prose.

## Browser Smoke Requirements

Subphases that touch the GUI must include manual browser smoke:

- Manga tab loads.
- Disabled steps are clearly disabled until prerequisites exist.
- Every visible button is wired or explicitly marked placeholder.
- Import creates a project and manifest.
- Progress panel updates from artifacts.
- OCR review screen can save a correction.
- Translation canary shows redacted provider status.
- Render preview appears and does not overlap controls.
- Export button creates an output artifact when its phase is active.
- API key is never visible in frontend JSON, DOM text, screenshots, or logs.

