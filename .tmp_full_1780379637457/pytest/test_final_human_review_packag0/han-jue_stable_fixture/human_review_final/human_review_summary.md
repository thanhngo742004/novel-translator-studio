# Human Review Summary

Status: **READY FOR HUMAN REVIEW**

## Approval Recommendation

The prompt is ready for human review, not automatic production use.

## Top 5 Strengths

- Strict cached replay is available.
- Source, reference, and final output are stored for review.
- Retry attempts are logged and bounded.
- Compression and truncation diagnostics are retained.
- Stable prompt is not auto-approved.

## Top 5 Weaknesses

- Gate reasons: `pass`
- Provider failure count: `0`
- Truncation count: `0`
- Unsafe compression count: `0`
- Human review recommended samples: `0`
- Suspicious unit count: `0`

## Suspicious Paragraphs Or Units

- none

## Production Translation Recommendation

Do not start production translation until a human approves this stable prompt.

## Review Commands

Approve: `nts eval review-stable --run .tmp_full_1780379637457/pytest/test_final_human_review_packag0/han-jue_stable_fixture --approve --json`
Reject: `nts eval review-stable --run .tmp_full_1780379637457/pytest/test_final_human_review_packag0/han-jue_stable_fixture --reject --reason "<reason>" --json`
