# Cached Eval Replay Report

- Selected model: `gpt-5.4-mini`
- Average score: `88.0`
- All samples pass: `True`
- Strict replay pass: `True`
- Truncated paragraphs: `0`
- Provider failures: `0`
- Style drift warnings: `0`
- Retryable provider failures: `0`
- Sample retries attempted: `0`
- Sample retries succeeded: `0`
- Sample retries exhausted: `0`
- Run retries attempted: `0`
- Paragraph rows: `2`

## Runs

| Run | Samples | Average Score | Ratio Min | Ratio Avg | Ratio Max | Pass |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 88.0 | 1.0 | 1.0 | 1.0 | True |

## Samples

| Run | Sample | Chapter | Score | Pass | Ratio Avg | Paragraphs | Alignment | Truncated | Style Drift | Reason |
|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|
| 1 | sample_1 | 1 | 88 | True | 1.0 | 2 | 0.95 | 0 | None | pass |

## Paragraph Diagnostics

| Run | Sample | Paragraph | Ref Chars | Before | After | Selected | Ratio Before | Ratio After | Ratio Selected | Selected Output | Style Drift | Truncated | Reasons | Alignment | Eligible | Source | Reference | Selected Final |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---:|---|---|---|---|
| 1 | sample_1 | p001 | 39 | 48 | 39 | 39 | 1.231 | 1.0 | 1.0 | None | None | False |  | 0.95 | True | 韩绝看向玉清宗。 | Hàn Tuyệt nhìn về phía Ngọc Thanh Tông. | Hàn Tuyệt nhìn về phía Ngọc Thanh Tông. |
| 1 | sample_1 | p002 | 22 | 31 | 22 | 22 | 1.409 | 1.0 | 1.0 | None | None | False |  | 0.95 | True | 他继续修炼。 | Hắn tiếp tục tu luyện. | Hắn tiếp tục tu luyện. |
