# Human Review Summary

Status: **NOT APPROVABLE**

## Approval Recommendation

NOT APPROVABLE until the listed validation failures are fixed.

## Top 5 Strengths

- Strict cached replay is available.
- Source, reference, and final output are stored for review.
- Retry attempts are logged and bounded.
- Compression and truncation diagnostics are retained.
- Stable prompt is not auto-approved.

## Top 5 Weaknesses

- Gate reasons: `cached_replay_strict_gate_failed`
- Provider failure count: `0`
- Truncation count: `1`
- Unsafe compression count: `0`
- Human review recommended samples: `0`
- Suspicious unit count: `1`

## Suspicious Paragraphs Or Units

- `p001`: missing_terminal_punctuation

## Production Translation Recommendation

Production translation is not recommended.

## Review Commands

Approve: `nts eval review-stable --run .tmp_full_1780379637457/pytest/test_final_human_review_packag1/han-jue_stable_truncated --approve --json`
Reject: `nts eval review-stable --run .tmp_full_1780379637457/pytest/test_final_human_review_packag1/han-jue_stable_truncated --reject --reason "<reason>" --json`
