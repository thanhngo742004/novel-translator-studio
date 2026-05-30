/goal Complete Phase 5 production readiness for Novel Translator Studio with strong quality improvement: fix remaining production safety issues, pass canary, pass controlled 10-chapter rollout, and achieve average validation delta >= +1.0 with clean safety.



Read first:



\* docs/implementation/NTS\_CURRENT\_STATE.md

\* docs/implementation/DECISIONS\_AND\_APPROVALS.md

\* docs/implementation/NTS\_CODEBASE\_BOOTSTRAP.md

\* docs/implementation/NTS\_NEXT\_ACTIONS.md

\* docs/implementation/CONTINUOUS\_MVP\_PROGRESS.md if present

\* latest MVP5I/MVP5I.1/MVP5I.2 canary and diagnostic artifacts under workspace\_mvp5c\_smoke\_20260525210758/artifacts/production\_rollout/



Current known state:



\* MVP5H full passed with dictionary + memory Hybrid Prompt Builder.

\* MVP5G Rule Candidate Engine passed.

\* MVP5H.1 rule prompt rendering failed quality validation.

\* MVP5H.1.1 scoped/disabled approved rules for prompt.

\* Rules are verifier-only / QA-only for now.

\* MVP5I rollout support exists.

\* MVP5I.1 provider preflight and fallback/model policy propagation exist.

\* MVP5I.2 added unit safety diagnostics and repair loop.

\* Latest canary still failed chapter 2 QA.

\* Provider/model route is no longer the main bottleneck.

\* Current blocker is production unit construction/safety for chapter-2-style content.

\* Remaining unsafe units after MVP5I.2 include strict-max and truncation/terminal issues.

\* Post-translation repair alone is not enough; fix production unit construction before translation.



Main goal:

Make Phase 5 text production pipeline nearly or fully complete by proving the safe production configuration can:



1\. pass chapters 1-2 canary cleanly,

2\. pass controlled 10-chapter production rollout cleanly,

3\. pass 2-round validation with average delta >= +1.0,

4\. keep all safety counters clean,

5\. keep rules out of production prompts.



Safe production config:

\--use-stable-prompt

\--use-hybrid-prompt

\--use-approved-dictionary

\--dictionary-max-entries 8

\--memory-max-items 6

\--support-max-chars 1200

\--emit-prompt-artifacts

\--resumable



Do NOT use:

\--use-approved-rules



Hard constraints:



\* Do not enable approved rules in prompt.

\* Rules must remain verifier-only / QA-only.

\* Rules rendered count must remain 0.

\* Do not weaken truncation detection.

\* Do not lower QA gates.

\* Do not fake PASS.

\* Do not auto-approve memory, dictionary, or rules.

\* Do not inject raw NLP cache into prompts.

\* Do not delete or hide failing artifacts.

\* Do not run full 10-chapter rollout until chapters 1-2 canary passes.

\* Do not call a run PASS if it only passes because thresholds were weakened.

\* Do not treat provider/model failure as translation quality failure; classify it as BLOCKED if both primary and fallback are unavailable.



Quota/runtime:



\* I am intentionally allowing a long-running goal.

\* Do not stop just because a short checkpoint cap is reached.

\* If a command uses max-real-calls as a checkpoint cap, automatically resume the same run until PASS / FAIL / BLOCKED.

\* Prefer resumable/checkpointed execution over one huge fragile command.

\* It is acceptable to spend API calls to reach the quality target, but still keep artifacts and progress logs.



Progress log:

Maintain and update:

docs/implementation/NTS\_GOAL\_PROGRESS.md



After every meaningful checkpoint, record:



\* timestamp

\* current phase

\* files changed

\* tests run

\* latest artifact paths

\* canary/rollout/validation result

\* unsafe units remaining

\* deltas if available

\* next action

\* whether rules rendered count remains 0



Phase A — Fix production unit construction before translation:



1\. Diagnose current chapter 2 unsafe units from latest canary artifacts.

2\. Implement or improve pre-translation unit classifier:



&#x20;  \* narration

&#x20;  \* dialogue

&#x20;  \* short\_action

&#x20;  \* system\_panel

&#x20;  \* pre\_panel\_label

&#x20;  \* glossary\_label

&#x20;  \* stat\_line

&#x20;  \* mixed\_panel\_narration

&#x20;  \* risky\_short\_unit

&#x20;  \* oversized\_unit

3\. Implement or improve production unit plan:



&#x20;  \* production\_unit\_plan.json

&#x20;  \* production\_unit\_plan.md

&#x20;  \* unit\_classification\_report.json

&#x20;  \* unit\_classification\_report.md

&#x20;  \* unit\_classification\_table.csv

4\. For system/panel/stat/pre-panel/risky short units:



&#x20;  \* use compact/panel/short-line mode before first provider call

&#x20;  \* prevent model over-expansion

&#x20;  \* preserve one-output-per-input unless split plan exists

&#x20;  \* preserve terminal punctuation

&#x20;  \* prevent dangling glossary labels

&#x20;  \* preserve field/value structure

5\. For strict-max offenders:



&#x20;  \* split, merge, reclassify, or compact before translation

&#x20;  \* do not rely only on post-translation compression

6\. Keep dictionary + memory support.

7\. Keep rules rendered count = 0.



Phase B — Canary gate:

Run only chapters 1-2 canary after each substantial repair.



Canary must pass:



\* tests pass

\* chapters 1-2 canary completes

\* no paragraph\_truncation\_detected

\* no paragraph\_exceeds\_strict\_max blocking issue

\* no unsafe compression

\* no missing output

\* no empty output

\* no dangling glossary label

\* no suspicious fragment ending

\* no raw NLP leakage

\* rules rendered count = 0

\* pending/rejected/deprecated/harmful items excluded

\* prompt budget respected

\* human review package created



If canary fails:



\* inspect artifacts

\* repair the smallest specific cause

\* rerun canary

\* continue until canary passes or a real FAIL/BLOCKED condition is reached.



Phase C — Controlled 10-chapter production rollout:

Only after canary passes, run controlled 10-chapter rollout.



Use safe config:

\--use-stable-prompt

\--use-hybrid-prompt

\--use-approved-dictionary

\--dictionary-max-entries 8

\--memory-max-items 6

\--support-max-chars 1200

\--emit-prompt-artifacts

\--resumable



Do not use --use-approved-rules.



10-chapter rollout must pass:



\* provider preflight passes with primary or fallback

\* rollout completes

\* no truncation

\* no unsafe compression

\* no missing output

\* no empty output

\* no overlong blocking output

\* no raw NLP leakage

\* rules rendered count = 0

\* pending/rejected/deprecated/harmful items excluded

\* prompt budget respected

\* dictionary/memory support stats recorded

\* human review package created



If rollout fails:



\* inspect the exact failing chapter/unit

\* repair production unit construction / selector / budgeting / repair path

\* rerun canary if the fix affects chapter 1-2 behavior

\* then rerun controlled rollout

\* do not hide failures



Phase D — Strong quality validation:

After canary and 10-chapter rollout pass, run 2-round validation comparing baseline vs safe production/hybrid config.



Validation target:



\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta >= +1.0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* human review package created



If average delta is between 0 and +1.0:



\* do not call PASS.

\* inspect artifacts for remaining fixable issues:



&#x20; \* missing dictionary hits

&#x20; \* memory applicability errors

&#x20; \* prompt support budget pruning too aggressive

&#x20; \* output selector issues

&#x20; \* production unit construction issues

&#x20; \* chapter-specific regressions

\* fix only evidence-backed issues.

\* You may approve no new memory/dictionary/rule unless the command requires explicit human approval; instead create review candidates/artifacts.

\* Rerun validation after targeted repairs.

\* Continue until average delta >= +1.0 or report FAIL with evidence that the threshold cannot be reached safely.



Phase E — Phase 5 finalization:

If all gates pass:



1\. Update docs:



&#x20;  \* docs/implementation/NTS\_CURRENT\_STATE.md

&#x20;  \* docs/implementation/DECISIONS\_AND\_APPROVALS.md

&#x20;  \* docs/implementation/NTS\_NEXT\_ACTIONS.md

&#x20;  \* docs/implementation/CONTINUOUS\_MVP\_PROGRESS.md if present

2\. Add a final Phase 5 readiness report:

&#x20;  docs/implementation/PHASE5\_PRODUCTION\_READINESS\_REPORT.md

3\. The report must include:



&#x20;  \* canary result

&#x20;  \* 10-chapter rollout result

&#x20;  \* validation deltas

&#x20;  \* average delta

&#x20;  \* safety counters

&#x20;  \* rules rendered count

&#x20;  \* safe production config

&#x20;  \* known limitations

&#x20;  \* whether Phase 5 is production-ready for text novels

&#x20;  \* next recommended phase: Phase 6 manga pipeline or broader production scaling



Testing:

After code changes, run:

uv run --extra dev python -m pytest -q



If uv is unavailable:

python -m pytest -q



Add or update tests for every fix:



\* production unit classifier

\* production unit plan

\* compact/panel/short-line mode

\* strict-max prevention before translation

\* selector rejects truncated output

\* selector rejects dangling glossary label

\* repair uses rollout model policy

\* rules rendered count remains 0

\* raw NLP cache is not injected

\* prompt budget respected

\* rollout/canary artifact generation

\* validation/reporting of average delta >= +1.0



PASS only if all are true:



\* full tests pass

\* chapters 1-2 canary passes clean QA

\* controlled 10-chapter rollout passes clean QA

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

\* human review packages created

\* Phase 5 readiness report created



FAIL if:



\* tests fail and cannot be repaired

\* canary cannot be made safe

\* 10-chapter rollout cannot be made safe

\* average validation delta cannot reach +1.0 without unsafe changes

\* rules leak into prompt

\* output safety remains blocking

\* prompt budget exceeded

\* the only way to pass would be weakening QA/safety



BLOCKED if:



\* provider/auth/model unavailable

\* both primary and fallback routes unavailable

\* environment blocks socket/network

\* workspace data missing



Do not report PASS WITH LIMITATIONS.



Final report must include:



\* PASS/FAIL/BLOCKED

\* files changed

\* tests run

\* canary artifact

\* 10-chapter rollout artifact

\* validation artifact

\* Round 1 baseline/hybrid/delta

\* Round 2 baseline/hybrid/delta

\* average delta

\* safety counters

\* unsafe units before/after

\* chapters/chunks processed

\* successful/failed/skipped chunks

\* API calls used

\* QA result

\* rules rendered count

\* dictionary/memory support stats

\* prompt artifact paths

\* human review package paths

\* Phase 5 readiness report path

\* final recommendation



