\# PHASE5\_VALIDATION\_DELTA\_GOAL



\## Objective



Finish Phase 5 by improving the safe production/hybrid configuration until validation reaches strong quality improvement.



Current gates already passed:



\* Chapters 1-2 canary passed.

\* Controlled 10-chapter rollout passed.

\* Rules rendered count stayed 0.

\* Rollout QA was clean.

\* Approved rules must remain verifier-only / QA-only.



Current remaining blocker:



\* Validation average delta is below target.

\* Latest known validation results:



&#x20; \* `han-jue\_amv\_1780126334837`: round deltas `+0.5`, `+0.6`, average `+0.55`

&#x20; \* `han-jue\_amv\_1780127378011`: round deltas `+0.1`, `+0.2`, average `+0.15`

\* Required target:



&#x20; \* Round 1 delta > 0

&#x20; \* Round 2 delta > 0

&#x20; \* average delta >= +1.0

&#x20; \* severe flags = 0

&#x20; \* unsafe compression = 0

&#x20; \* truncation = 0

&#x20; \* no chapter regression over 3

&#x20; \* rules rendered count = 0



\## Read first



Read:



\* `docs/implementation/NTS\_CURRENT\_STATE.md`

\* `docs/implementation/DECISIONS\_AND\_APPROVALS.md`

\* `docs/implementation/NTS\_CODEBASE\_BOOTSTRAP.md`

\* `docs/implementation/NTS\_NEXT\_ACTIONS.md`

\* `docs/implementation/NTS\_GOAL\_PROGRESS.md`

\* latest validation artifacts:



&#x20; \* `workspace\_mvp5c\_smoke\_20260525210758/artifacts/approved\_memory\_validation/han-jue\_amv\_1780126334837`

&#x20; \* `workspace\_mvp5c\_smoke\_20260525210758/artifacts/approved\_memory\_validation/han-jue\_amv\_1780127378011`

\* latest rollout artifact:



&#x20; \* `workspace\_mvp5c\_smoke\_20260525210758/artifacts/production\_rollout/han-jue\_p\_mps0bq9p`



Do not paste huge artifact contents into the chat. Read files locally and summarize only findings.



\## Hard constraints



Do not:



\* enable `--use-approved-rules`

\* render approved rules into prompts

\* weaken truncation detection

\* lower QA gates

\* fake PASS

\* auto-approve memory, dictionary, or rules

\* inject raw NLP cache into prompts

\* delete or hide failing artifacts

\* call PASS unless average validation delta >= +1.0



Rules must remain verifier-only / QA-only.



Safe config remains:



```text

\--use-stable-prompt

\--use-hybrid-prompt

\--use-approved-dictionary

\--dictionary-max-entries 8

\--memory-max-items 6

\--support-max-chars 1200

\--emit-prompt-artifacts

\--resumable

```



Do not use:



```text

\--use-approved-rules

```



\## Work plan



\### Phase 1 — Verify current repo state



1\. Run `git status --short`.

2\. Inspect current changed files.

3\. Do not overwrite user files.

4\. Confirm whether the latest changes were committed.

5\. Run focused tests first, then full suite if feasible.



Required tests after code changes:



```text

uv run --extra dev python -m pytest -q

```



If that fails due environment, use:



```text

python -m pytest -q

```



\### Phase 2 — Analyze why validation delta is below +1.0



Inspect validation artifacts for:



\* per-chapter deltas

\* chapter regressions

\* dropped dictionary hits

\* dropped memory items

\* prompt budget pruning

\* memory applicability misses

\* dictionary exact-hit misses

\* terminology mismatch warnings

\* style drift warnings

\* compression/selector differences

\* evaluator warnings

\* baseline unusually strong chapters

\* hybrid output regressions



Produce artifact:



```text

artifacts/phase5\_validation\_delta/<run\_id>/validation\_delta\_diagnostic.md

artifacts/phase5\_validation\_delta/<run\_id>/validation\_delta\_diagnostic.json

```



The diagnostic must identify concrete evidence-backed improvement opportunities.



\### Phase 3 — Apply only safe, evidence-backed improvements



Allowed improvements:



\* fix dictionary retrieval bugs

\* fix memory applicability bugs

\* fix prompt support ranking/pruning bugs

\* fix support bundle dedupe/conflict bugs

\* fix production unit construction if validation artifacts show it affects quality

\* fix terminology alias handling if evidence supports it

\* improve artifact reporting

\* create human-review candidates for new dictionary/memory if needed



Not allowed:



\* auto-approve new dictionary/memory/rules

\* enable rules in prompts

\* change stable prompt to chase score

\* weaken safety or QA gates

\* suppress evaluator warnings

\* hide regressions



\### Phase 4 — Rerun validation



After each evidence-backed fix:



1\. Run tests.

2\. Rerun 2-round validation using safe config.

3\. Continue until:



&#x20;  \* average delta >= +1.0 and all safety gates are clean, or

&#x20;  \* no evidence-backed safe improvement remains.



If max-real-calls checkpoint is hit, resume same run automatically.



\### Phase 5 — Finalize Phase 5 only if target is met



PASS only if:



\* full tests pass

\* canary remains passed or is not invalidated by changes

\* 10-chapter rollout remains passed or is not invalidated by changes

\* 2-round validation completes

\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta >= +1.0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* Phase 5 readiness report created



Create/update:



\* `docs/implementation/NTS\_GOAL\_PROGRESS.md`

\* `docs/implementation/NTS\_CURRENT\_STATE.md`

\* `docs/implementation/NTS\_NEXT\_ACTIONS.md`

\* `docs/implementation/PHASE5\_PRODUCTION\_READINESS\_REPORT.md`



\## FAIL criteria



Report FAIL if:



\* tests fail and cannot be repaired

\* validation average delta cannot reach +1.0 without unsafe changes

\* rules leak into prompt

\* output safety becomes blocking

\* prompt budget exceeded

\* the only way to pass would be weakening QA/safety

\* no evidence-backed safe improvement remains



If rollout/canary are still clean but average delta remains below +1.0, report FAIL honestly and preserve artifacts.



\## BLOCKED criteria



Report BLOCKED if:



\* provider/auth/model unavailable

\* both primary and fallback routes unavailable

\* environment blocks socket/network

\* workspace data missing



\## Progress log



Keep updating:



```text

docs/implementation/NTS\_GOAL\_PROGRESS.md

```



Each checkpoint must include:



\* timestamp

\* current phase

\* files changed

\* tests run

\* latest validation artifact

\* deltas

\* average delta

\* safety counters

\* rules rendered count

\* current hypothesis

\* next action



\## Final report must include



\* PASS/FAIL/BLOCKED

\* files changed

\* tests run

\* validation diagnostic artifact

\* validation artifact

\* Round 1 baseline/hybrid/delta

\* Round 2 baseline/hybrid/delta

\* average delta

\* safety counters

\* chapter regressions

\* rules rendered count

\* dictionary/memory support stats

\* prompt artifact paths

\* human review package paths

\* Phase 5 readiness report path if PASS

\* final recommendation



