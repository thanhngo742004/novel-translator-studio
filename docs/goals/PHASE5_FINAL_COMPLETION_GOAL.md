\# PHASE5\_FINAL\_COMPLETION\_GOAL



\## Objective



Finish Phase 5 completely enough for text-novel production.



This is one final long-running goal. Do not stop at the first bug, alignment failure, negative validation round, missing dictionary, or repairable QA issue. Fix evidence-backed issues, rerun tests, rerun validation/translation trials, and continue until PASS, BLOCKED, or truly exhausted FAIL.



Target novels:



1\. Han Jue

2\. Tien Nghich



&#x20;  \* raw: `raw tien nghich`

&#x20;  \* translated EPUB: `translated tien nghich.epub`

&#x20;  \* files are large, around 20MB

&#x20;  \* handle with bounded reading, streaming/chapter indexing/chunking

&#x20;  \* never paste full novel text into chat, prompt, logs, or review docs



\## Hard rules



Do not:



\* use `--use-approved-rules`

\* render approved rules into prompts

\* weaken truncation detection

\* weaken QA/evaluator/safety gates

\* fake PASS

\* auto-approve memory, dictionary, or rules

\* inject raw NLP cache into prompts

\* copy Han Jue-specific dictionary/memory into Tien Nghich

\* make story-specific hacks that only work for one novel

\* dump full large files into logs or prompts



Rules are verifier-only / QA-only.



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



Do not use:



```text

\--use-approved-rules

```



\## Current known state



\* Full tests previously reached 212 passed.

\* Han Jue production/canary work has passed before, but latest validation still had one negative round.

\* Tien Nghich files were discovered and project was created.

\* LTP was previously healthy at `http://127.0.0.1:3003`.

\* Tien Nghich currently has an alignment/sample-selection blocker around chapter 2.

\* Previous failure reason: candidate samples existed, but accepted safe candidates were 0 due high reference/source ratio.

\* Next required repair is general bounded body-window alignment/sample selection for long raw + EPUB pairs.



\## Work loop



Repeat this loop until PASS, BLOCKED, or exhausted FAIL:



1\. Inspect current repo state and artifacts.

2\. Fix the smallest evidence-backed issue.

3\. Add or update tests for the fix.

4\. Run full tests.

5\. Rerun the relevant validation/translation trial.

6\. Update progress docs.

7\. Continue if the result is still repairable.



Do not stop early just because one run fails.



\## Required implementation/fix areas



Fix whatever is still blocking Phase 5, especially:



\### 1. Large-file ingestion



\* Handle 20MB raw/EPUB files safely.

\* Build chapter manifests.

\* Extract only bounded chapter windows needed for validation/trial runs.

\* Do not read full text into prompt/log/chat.



\### 2. Tien Nghich alignment



Implement robust bounded body-window alignment/sample selection:



\* raw chapter indexing

\* EPUB spine/natural ordering

\* skip title-only sections

\* adjacent section joining

\* body-only fallback windows

\* head/mid/tail windows

\* paragraph/window offsets

\* anchor scoring

\* length-ratio-safe sample selection



Do not weaken ratio gates globally. Select smaller safe subwindows instead.



\### 3. NLP/LTP



\* Check LTP at `http://127.0.0.1:3003`.

\* Build NLP cache for Tien Nghich chapters 1-10 if needed.

\* If LTP unavailable due environment, report BLOCKED.

\* Do not inject raw NLP cache into prompts.



\### 4. Dictionary/memory



\* Keep dictionary and memory project-scoped.

\* Do not auto-approve candidates.

\* If new candidates are needed, generate human review package and leave them pending.

\* Pending/rejected/deprecated/harmful/insufficient-evidence items must not enter prompts.



\### 5. Prompt/pipeline generalization



Improve only general, evidence-backed behavior:



\* alignment

\* sample selection

\* unit construction

\* prompt support ranking

\* dictionary retrieval

\* memory applicability

\* terminology alias checks

\* artifact reporting

\* QA/reporting



Do not overfit to one novel.



\## Required trials



When fixes are ready, run:



\### A. Tests



Run:



```text

uv run --extra dev python -m pytest -q

```



If uv is unavailable:



```text

python -m pytest -q

```



PASS requires full tests to pass.



\### B. Han Jue validation



Run 2-round safe validation on Han Jue chapters 1-10.



Required:



\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta > 0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0



\### C. Tien Nghich validation



Run 2-round safe validation on Tien Nghich chapters 1-10.



Required:



\* chapter/sample alignment succeeds

\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta > 0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0



\### D. Translation trial



Run a controlled translation/production trial for the first 10 chapters where supported.



Required:



\* no truncation

\* no unsafe compression

\* no missing output

\* no empty output

\* no overlong blocking output

\* no raw NLP leakage

\* rules rendered count = 0

\* prompt budget respected

\* human review package created



If 10-chapter production trial is not yet supported for both novels, run the strongest supported equivalent and document the limitation.



\## Human review



Create final human review packages for:



\* Han Jue validation/trial

\* Tien Nghich validation/trial

\* dictionary/memory candidates if generated

\* final Phase 5 readiness



Human review must include:



\* sample source/output/reference snippets

\* validation scores

\* QA warnings

\* dictionary/memory support used

\* candidate review tables

\* prompt artifacts

\* known limitations



Do not auto-approve anything.



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

\* artifacts created

\* Han Jue status

\* Tien Nghich status

\* LTP status

\* validation deltas

\* safety counters

\* rules rendered count

\* current blocker

\* next action



\## PASS criteria



Report PASS only if all are true:



\* full tests pass

\* Han Jue chapters 1-10 validation passes both rounds with positive deltas

\* Tien Nghich chapters 1-10 validation passes both rounds with positive deltas

\* both novels have average delta > 0

\* safety counters are clean:



&#x20; \* severe flags = 0

&#x20; \* unsafe compression = 0

&#x20; \* truncation = 0

&#x20; \* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* prompt budget respected

\* controlled translation/trial artifacts created

\* human review packages created

\* Phase 5 final readiness report created



Create:



```text

docs/implementation/PHASE5\_FINAL\_READINESS\_REPORT.md

```



\## FAIL criteria



Report FAIL only if repair paths are exhausted.



Do not report FAIL just because:



\* first alignment attempt fails

\* first validation has a negative round

\* Tien Nghich has no approved dictionary/memory

\* new candidates require human review

\* one repair loop needs another iteration



Report FAIL only if:



\* tests cannot be repaired

\* Tien Nghich alignment remains impossible after bounded body-window strategies are attempted and artifacted

\* either novel cannot achieve both validation rounds positive without unsafe changes

\* safety issues cannot be repaired

\* rules leak into prompts

\* prompt budget exceeded

\* the only way to pass is weakening QA/evaluator/safety gates

\* large-file handling remains unsafe



\## BLOCKED criteria



Report BLOCKED if:



\* required files are missing

\* provider/auth/model unavailable

\* LTP required but unavailable

\* workspace data missing

\* environment blocks filesystem/network/socket access



\## Final report



Final report must include:



\* PASS/FAIL/BLOCKED

\* files changed

\* tests run

\* Han Jue validation artifact

\* Tien Nghich validation artifact

\* Han Jue round deltas and average

\* Tien Nghich round deltas and average

\* translation/trial artifacts

\* safety counters

\* rules rendered count

\* LTP status

\* dictionary/memory support stats

\* human review package paths

\* Phase 5 readiness report path

\* final recommendation



