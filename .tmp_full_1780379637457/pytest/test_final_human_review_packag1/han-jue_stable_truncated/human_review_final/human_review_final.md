# Human Review Final

Status: **NOT APPROVABLE**

- Project: `han-jue`
- Provider/model: `mock` / `gpt-5.4-mini`
- Prompt version: `None`
- Quality gate: `fail`
- Stable prompt created: `False`
- Run scores: `[{"average_score": 90, "pass": false, "validation_index": 1}]`
- Sample scores: `[{"output_reference_ratio": 1.0, "pass": false, "sample_id": "sample_1", "total_score": 90, "validation_index": 1}]`
- Retry summary: `{"final_provider_failure_count": 0, "non_retryable_failures": 0, "retryable_failures": 0, "run_retries_attempted": 0, "sample_retries_attempted": 0, "sample_retries_exhausted": 0, "sample_retries_succeeded": 0}`
- Compression summary: `{"max_attempts": 0, "paragraph_count": 0, "total_attempts": 0, "unsafe_count": 0}`
- Ratio summary: `{"average": 1.0, "max": 1.0, "min": 1.0}`
- Truncation count: `1`
- Unsafe compression count: `0`
- Provider failure count: `0`
- Final reason: `cached_replay_strict_gate_failed`

## Run 1 / sample_1

- Score: `90`
- Ratio: `1.0`
- Selected final output: `None`
- Selection reason: `None`
- Style drift score: `None`
- Human review recommended: `False`
- Warnings: `[]`
- Reviewer decision: APPROVE / REJECT / NEEDS_EDIT
- Reviewer notes:

Source Chinese excerpt:

韩绝继续修炼。

Human Vietnamese reference:

Hàn Tuyệt tiếp tục tu luyện.

Model Vietnamese output before compression:

Hàn Tuyệt tiếp tục tu luyện.

Final model Vietnamese output after compression/unit merge:

Hàn Tuyệt tiếp tục tu luyệ

### Unit p001

- Source paragraph IDs: `["p001"]`
- Target paragraph IDs: `["p001"]`
- Required terms: `[{"aliases": ["hàn giác", "hàn tuyệt"], "anchor_id": "han_jue", "kind": "anchor", "source": "han_jue", "target": "hàn tuyệt"}]`
- Missing terms: `[]`
- Compression attempts: `[]`
- Selected final output: `None`
- Selection reason: `None`
- Style drift score: `None`
- Human review recommended: `False`
- Safety status: `fail`
- Alignment quality: `1.0`
- Pass/fail reason: `missing_terminal_punctuation`
