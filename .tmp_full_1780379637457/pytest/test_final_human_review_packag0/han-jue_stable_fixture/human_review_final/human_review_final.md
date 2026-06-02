# Human Review Final

Status: **READY FOR HUMAN REVIEW**

- Project: `han-jue`
- Provider/model: `ckey_openai_compatible` / `gpt-5.4-mini`
- Prompt version: `None`
- Quality gate: `pass`
- Stable prompt created: `True`
- Run scores: `[{"average_score": 88, "pass": true, "validation_index": 1}]`
- Sample scores: `[{"output_reference_ratio": 1.0, "pass": true, "sample_id": "sample_1", "total_score": 88, "validation_index": 1}]`
- Retry summary: `{"final_provider_failure_count": 0, "non_retryable_failures": 0, "retryable_failures": 0, "run_retries_attempted": 0, "sample_retries_attempted": 0, "sample_retries_exhausted": 0, "sample_retries_succeeded": 0}`
- Compression summary: `{"max_attempts": 0, "paragraph_count": 0, "total_attempts": 0, "unsafe_count": 0}`
- Ratio summary: `{"average": 1.0, "max": 1.0, "min": 1.0}`
- Truncation count: `0`
- Unsafe compression count: `0`
- Provider failure count: `0`
- Final reason: `pass`

## Run 1 / sample_1

- Score: `88`
- Ratio: `1.0`
- Selected final output: `None`
- Selection reason: `None`
- Style drift score: `None`
- Human review recommended: `False`
- Warnings: `[]`
- Reviewer decision: APPROVE / REJECT / NEEDS_EDIT
- Reviewer notes:

Source Chinese excerpt:

韩绝看向玉清宗。

他继续修炼。

Human Vietnamese reference:

Hàn Tuyệt nhìn về phía Ngọc Thanh Tông.

Hắn tiếp tục tu luyện.

Model Vietnamese output before compression:

Hàn Tuyệt nhìn về phía Ngọc Thanh Tông rộng lớn.

Hắn tiếp tục tu luyện chăm chỉ.

Final model Vietnamese output after compression/unit merge:

Hàn Tuyệt nhìn về phía Ngọc Thanh Tông.

Hắn tiếp tục tu luyện.

### Unit p001

- Source paragraph IDs: `["p001"]`
- Target paragraph IDs: `["p001"]`
- Required terms: `[{"aliases": ["hàn giác", "hàn tuyệt"], "anchor_id": "han_jue", "kind": "anchor", "source": "han_jue", "target": "hàn tuyệt"}, {"aliases": ["ngọc thanh tông"], "anchor_id": "yuqing_zong", "kind": "anchor", "source": "yuqing_zong", "target": "ngọc thanh tông"}]`
- Missing terms: `[]`
- Compression attempts: `[]`
- Selected final output: `None`
- Selection reason: `None`
- Style drift score: `None`
- Human review recommended: `False`
- Safety status: `pass`
- Alignment quality: `0.95`
- Pass/fail reason: `pass`

### Unit p002

- Source paragraph IDs: `["p002"]`
- Target paragraph IDs: `["p002"]`
- Required terms: `[]`
- Missing terms: `[]`
- Compression attempts: `[]`
- Selected final output: `None`
- Selection reason: `None`
- Style drift score: `None`
- Human review recommended: `False`
- Safety status: `pass`
- Alignment quality: `0.95`
- Pass/fail reason: `pass`
