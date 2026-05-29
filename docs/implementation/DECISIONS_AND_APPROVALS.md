# Decisions And Approvals

## Approved project decisions

- 14 Han Jue dictionary entries approved.
- 7 Han Jue rules were approved, but rule prompt rendering failed validation.
- Approved rules are now verifier-only or disabled for prompt.
- Do not enable rules in production prompt until a future validation proves positive quality.

## LAMM-T policy

- Dictionary = canonical source -> target glossary.
- Memory = decisions/corrections/evidence/provenance/audit.
- Rule = context-bound behavior/guard.
- Do not mix these layers.
- No auto-approval.
- No raw NLP cache in prompts.
- No pending/rejected/deprecated/harmful/insufficient-evidence item in production prompts.

## Operating approvals

- Production-safe prompt profile uses stable prompt + hybrid prompt + approved dictionary support only.
- Approved memory remains a separate support layer from dictionary and must stay evidence-backed and auditable.
- Rules remain verifier-only / QA-only until dedicated validation shows positive impact and safe rendering.
- Production rollout QA is the enforcement layer for prompt safety, rule non-rendering, and support-budget limits.
