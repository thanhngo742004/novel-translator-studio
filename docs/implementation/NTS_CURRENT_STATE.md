# NTS Current State

## Latest safe state

- MVP5H full passed with dictionary + memory hybrid prompt.
- MVP5G rule candidate engine passed.
- MVP5H.1 rule prompt rendering failed.
- MVP5H.1.1 scoped/disabled rules for prompt; rules are verifier-only.
- MVP5I rollout support exists but real rollout failed due provider/model route and chapter 2 QA.

## Safe production config

- `--use-stable-prompt`
- `--use-hybrid-prompt`
- `--use-approved-dictionary`
- `--dictionary-max-entries 8`
- `--memory-max-items 6`
- `--support-max-chars 1200`
- `--emit-prompt-artifacts`

## Do NOT use

- `--use-approved-rules`

## Current operational interpretation

- Stable prompt remains the production baseline.
- Hybrid prompt support is safe only with approved dictionary + memory support under the configured caps.
- Rules are not part of the production prompt profile.
- Production QA must continue to enforce zero rules rendered into prompts and zero raw NLP cache leakage.

## Recommended next phase

- MVP5I.1 provider/model preflight, fallback model handling, chapter 2 QA diagnostic, canary rollout.
