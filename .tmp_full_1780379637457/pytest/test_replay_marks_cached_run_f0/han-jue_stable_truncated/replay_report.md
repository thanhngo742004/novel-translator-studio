# Cached Eval Replay Report

- Selected model: `gpt-5.4-mini`
- Average score: `90.0`
- All samples pass: `False`
- Strict replay pass: `False`
- Truncated paragraphs: `1`
- Provider failures: `0`
- Style drift warnings: `0`
- Retryable provider failures: `0`
- Sample retries attempted: `0`
- Sample retries succeeded: `0`
- Sample retries exhausted: `0`
- Run retries attempted: `0`
- Paragraph rows: `1`

> WARNING: This cached run fails the strict MVP4.8.6 replay gate. Do not approve or use its stable prompt for production.

## Runs

| Run | Samples | Average Score | Ratio Min | Ratio Avg | Ratio Max | Pass |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | 90.0 | 0.929 | 0.929 | 0.929 | False |

## Samples

| Run | Sample | Chapter | Score | Pass | Ratio Avg | Paragraphs | Alignment | Truncated | Style Drift | Reason |
|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|
| 1 | sample_1 | 1 | 90 | False | 0.929 | 1 | 1.0 | 1 | None | paragraph_truncation_detected |

## Paragraph Diagnostics

| Run | Sample | Paragraph | Ref Chars | Before | After | Selected | Ratio Before | Ratio After | Ratio Selected | Selected Output | Style Drift | Truncated | Reasons | Alignment | Eligible | Source | Reference | Selected Final |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|---:|---|---|---|---|
| 1 | sample_1 | p001 | 28 | 28 | 26 | 26 | 1.0 | 0.929 | 0.929 | None | None | True | missing_terminal_punctuation | 1.0 | True | 韩绝继续修炼。 | Hàn Tuyệt tiếp tục tu luyện. | Hàn Tuyệt tiếp tục tu luyệ |
