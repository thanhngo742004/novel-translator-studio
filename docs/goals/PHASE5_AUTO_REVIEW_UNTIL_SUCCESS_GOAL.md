\# PHASE5\_AUTO\_REVIEW\_UNTIL\_SUCCESS\_GOAL



\## Objective



Finish Phase 5 for multi-novel text translation using an automatic candidate review / approval / rollback loop.



This goal removes manual human approval as a terminal stop.



Codex may automatically review, approve, test, rollback, re-score, and re-mine dictionary/memory candidates as long as every activation is evidence-backed, project-scoped, auditable, reversible, and validated by tests + real validation.



Allowed terminal outcomes:



1\. PASS / SUCCESS

2\. BLOCKED\_PROVIDER\_OR\_ENVIRONMENT



Do not report terminal FAIL.

Do not stop with NEEDS\_HUMAN\_APPROVAL.

If a validation/translation run fails and provider/environment still works, diagnose, repair, rollback if needed, and continue.



Target novels:



1\. Han Jue

2\. Tien Nghich



&#x20;  \* raw: `raw tien nghich`

&#x20;  \* translated EPUB: `translated tien nghich.epub`

&#x20;  \* large files around 20MB

&#x20;  \* use bounded reading/chapter indexing/chunking

&#x20;  \* never paste full novel text into chat, prompt, logs, or review docs



\---



\## Hard safety constraints



Do not:



\* weaken truncation detection

\* weaken QA/evaluator/safety gates

\* fake PASS

\* hide failing artifacts

\* delete failing artifacts

\* inject raw NLP cache into prompts

\* copy Han Jue-specific dictionary/memory into Tien Nghich

\* make story-specific hacks that only work for one novel

\* dump full 20MB files into chat/logs/prompts

\* globally enable approved rules in production prompts



Rules remain verifier-only / QA-only unless a future separate rule-validation phase proves otherwise.



For this goal:



```text

\--use-approved-rules is forbidden

rules rendered count must remain 0

```



Safe runtime profile:



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



\---



\## Current known state



Previous run reached:



\* Full tests: 212 passed

\* LTP previously healthy at `http://127.0.0.1:3003`

\* Han Jue validation:



&#x20; \* Round 1 delta: -0.2

&#x20; \* Round 2 delta: +0.6

&#x20; \* Average: +0.2

\* Tien Nghich validation:



&#x20; \* Round 1 delta: -1.0

&#x20; \* Round 2 delta: +1.6

&#x20; \* Average: +0.3

&#x20; \* Unsafe compression on sample\_3 and sample\_7

&#x20; \* Regressions over 3 in chapters 4 and 8

\* Tien Nghich alignment now reaches 2-round validation.

\* Tien Nghich has no approved support yet.

\* Candidate packages exist for Han Jue and Tien Nghich.

\* Previous goal stopped with NEEDS\_HUMAN\_APPROVAL.

\* This new goal must not stop there.



\---



\## Core loop



Repeat until PASS or BLOCKED\_PROVIDER\_OR\_ENVIRONMENT:



1\. Inspect artifacts.

2\. Identify weak chapters/samples/regressions.

3\. Mine dictionary/memory candidates if needed.

4\. Auto-review candidates using evidence gates.

5\. Activate only safe candidates into a reversible candidate bundle.

6\. Rerun tests.

7\. Rerun validation.

8\. If score improves and safety remains clean, keep the bundle.

9\. If score decreases, rollback the bundle, classify the harmful candidates, and mine/recalculate again.

10\. Continue.



Do not stop because one bundle failed.



\---



\## Auto-review policy



Manual human approval is not required in this goal.



Codex may auto-approve dictionary/memory candidates only if all conditions below are met.



\### Candidate must have



\* project scope:



&#x20; \* `han-jue`, or

&#x20; \* `tien-nghich`, or

&#x20; \* explicitly safe global scope

\* evidence from aligned source/reference/output artifacts

\* no conflict with active dictionary/memory

\* no harmful/deprecated/rejected status

\* no unsupported hallucinated expansion

\* clear trigger condition

\* clear target/correction

\* provenance artifact path



\### Candidate types allowed



Allowed for auto-review:



\* name mapping

\* sect/org/place mapping

\* cultivation realm/term mapping

\* repeated terminology correction

\* style preference with direct evidence

\* compression/anti-expansion preference if safety-backed

\* prompt-support applicability correction

\* memory applicability scoping fix



Not allowed for auto-review:



\* broad style rules without evidence

\* rules that would be rendered in production prompt

\* candidates requiring interpretation without source/reference evidence

\* candidates that are only useful for one weird sample and harm others

\* candidates that conflict with project dictionary

\* any candidate that weakens safety



\---



\## Auto-approval workflow



When candidates exist:



1\. Create candidate bundle:



```text

artifacts/auto\_review/<run\_id>/candidate\_bundle.json

artifacts/auto\_review/<run\_id>/candidate\_bundle.md

```



2\. Classify every candidate:



```text

safe\_positive

safe\_project\_scoped

needs\_scope

neutral

harmful

conflict

insufficient\_evidence

```



3\. Activate only candidates classified:



```text

safe\_positive

safe\_project\_scoped

needs\_scope with explicit scope applied

```



4\. Write audit:



```text

artifacts/auto\_review/<run\_id>/auto\_approval\_audit.json

artifacts/auto\_review/<run\_id>/auto\_approval\_audit.md

```



5\. Run validation.



6\. If validation improves:



Keep candidates.



7\. If validation decreases:



Rollback automatically.

Classify harmful candidates.

Write:



```text

artifacts/auto\_review/<run\_id>/rollback\_audit.json

artifacts/auto\_review/<run\_id>/harmful\_candidate\_report.md

```



8\. Mine or recalculate again.



\---



\## Validation targets



Run safe 2-round validation for both:



1\. Han Jue chapters 1-10

2\. Tien Nghich chapters 1-10



Target for PASS:



For each novel:



\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta > 0



Across both novels:



\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* prompt budget respected



If a validation run has a negative round:



\* do not stop

\* inspect sample/chapter artifacts

\* classify cause

\* repair pipeline or candidate bundle

\* rollback harmful support

\* rerun



\---



\## Safety regression policy



If any candidate bundle causes:



\* lower average delta

\* new negative round

\* unsafe compression

\* truncation

\* severe flag

\* chapter regression over 3

\* terminology regression

\* prompt budget overflow

\* rules rendered count > 0



then:



1\. rollback the bundle

2\. mark candidate(s) harmful or needs\_scope

3\. write audit

4\. re-mine/re-score

5\. rerun tests and validation



Do not keep harmful candidates.



\---



\## Alignment and extraction work



If validation cannot run because of alignment/sample selection:



Repair and continue.



Allowed work:



\* raw chapter indexing

\* EPUB spine/natural ordering

\* title-only section skipping

\* adjacent section joining

\* bounded body-window selection

\* low-ratio subwindow selection

\* anchor scoring

\* chapter mapping artifacts

\* sample selection artifacts



Do not weaken ratio gates globally.

Select safer subwindows instead.



\---



\## Translation trial



After both validations pass:



Run supported controlled production/translation trial for first 10 chapters where available.



Required:



\* no truncation

\* no unsafe compression

\* no missing output

\* no empty output

\* no overlong blocking output

\* no raw NLP leakage

\* rules rendered count = 0

\* prompt budget respected

\* human-review-style package created



This human review package is for final verification only, not for blocking approval.



\---



\## Progress log



Maintain:



```text

docs/implementation/NTS\_GOAL\_PROGRESS.md

```



Every checkpoint must include:



\* timestamp

\* current phase

\* files changed

\* tests run

\* candidate bundle id

\* activated candidates

\* rolled back candidates

\* Han Jue validation result

\* Tien Nghich validation result

\* safety counters

\* rules rendered count

\* current blocker

\* next action



\---



\## Tests



After code changes, run:



```text

uv run --extra dev python -m pytest -q

```



If uv unavailable:



```text

python -m pytest -q

```



Add/update tests for:



\* auto-review candidate classification

\* auto-approval audit

\* rollback audit

\* harmful candidate rollback

\* project-scoped activation

\* dictionary/memory scope isolation

\* validation decreases trigger rollback

\* rules rendered count remains 0

\* raw NLP cache not injected

\* prompt budget respected

\* alignment/sample selection repair if touched

\* existing tests still pass



\---



\## PASS / SUCCESS criteria



Report PASS only if:



\* full tests pass

\* Han Jue validation:



&#x20; \* Round 1 delta > 0

&#x20; \* Round 2 delta > 0

&#x20; \* average delta > 0

\* Tien Nghich validation:



&#x20; \* Round 1 delta > 0

&#x20; \* Round 2 delta > 0

&#x20; \* average delta > 0

\* safety counters clean:



&#x20; \* severe flags = 0

&#x20; \* unsafe compression = 0

&#x20; \* truncation = 0

&#x20; \* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* prompt budget respected

\* harmful candidates rolled back

\* final active candidates have audit evidence

\* supported translation trial passes

\* final verification package created

\* Phase 5 readiness report created



Create:



```text

docs/implementation/PHASE5\_FINAL\_READINESS\_REPORT.md

```



\---



\## BLOCKED\_PROVIDER\_OR\_ENVIRONMENT criteria



Report BLOCKED only if:



\* provider/auth/model unavailable

\* both primary and fallback routes unavailable

\* LTP required but unavailable

\* required files missing

\* workspace data missing

\* environment blocks filesystem/network/socket access



\---



\## No terminal FAIL



Do not report terminal FAIL.



If a run fails but environment/provider works:



\* diagnose

\* repair

\* rollback

\* recalculate

\* rerun



Only stop at PASS or BLOCKED\_PROVIDER\_OR\_ENVIRONMENT.



\---



\## Final response



Final response must include:



\* PASS or BLOCKED\_PROVIDER\_OR\_ENVIRONMENT

\* files changed

\* tests run

\* active candidate bundle

\* rolled back candidate bundle if any

\* Han Jue validation artifact

\* Tien Nghich validation artifact

\* Han Jue deltas and average

\* Tien Nghich deltas and average

\* safety counters

\* rules rendered count

\* dictionary/memory support stats per novel

\* translation/trial artifact paths

\* final verification package paths

\* Phase 5 readiness report path

\* final recommendation



