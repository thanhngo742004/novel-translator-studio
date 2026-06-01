\# PHASE5\_MULTI\_NOVEL\_COMPLETION\_LONG\_GOAL



\## Objective



Complete or nearly complete Phase 5 for multi-novel text translation.



This is a long-running repair-and-validation goal.

Do not stop at the first alignment failure, negative delta, missing dictionary, or low-quality validation result. Treat those as repair targets unless they are truly unrecoverable.



The tool is meant to translate many novels, not overfit one novel.



Target novels:



1\. Han Jue

2\. Tien Nghich



&#x20;  \* raw file: `raw tien nghich`

&#x20;  \* translated file: `translated tien nghich.epub`

&#x20;  \* files are large, around 20MB

&#x20;  \* handle with streaming/chapter indexing/chunking

&#x20;  \* never paste/load full text into chat/prompt/log



\## Current known state



Previous multi-novel goal stopped too early.



Known results:



\* Full tests passed: `212 passed`

\* LTP server was healthy at `http://127.0.0.1:3003`

\* Tien Nghich project was created in the workspace

\* Tien Nghich raw file was discovered:

&#x20; `test\_data/translation\_eval/han\_jue/raw tien nghich.txt`

\* Tien Nghich translated EPUB was discovered:

&#x20; `test\_data/translation\_eval/han\_jue/translated tien nghich.epub`

\* Large-file extraction fixes were started:



&#x20; \* raw chapter extraction avoids reading the full 20MB raw novel for bounded windows

&#x20; \* EPUB natural sort fixed C2 before C10

&#x20; \* EPUB extraction avoids title-only splits when real bodies exist

\* Tien Nghich validation failed at alignment sample selection:

&#x20; `No reliable title-matched alignment sample found for requested chapter 2`

\* Han Jue latest validation still failed:



&#x20; \* Round 1 delta: `-0.2`

&#x20; \* Round 2 delta: `+0.6`

&#x20; \* average delta: `+0.2`

\* Rules were not used in prompts.

\* No auto-approval was performed.



This is not final FAIL yet. The next goal must continue repairing:



\* Tien Nghich alignment/chapter sample selection

\* multi-novel validation

\* safe prompt/pipeline generalization

\* evidence-backed dictionary/memory candidate workflow



\## Final PASS target



PASS only if all are true:



1\. Full test suite passes.

2\. Tien Nghich large-file ingestion is safe.

3\. Tien Nghich first 10 raw chapters and translated EPUB chapters can be extracted and aligned.

4\. LTP/NLP cache for Tien Nghich chapters 1-10 is built with `ltp\_server` or a clearly accepted degraded status.

5\. Han Jue 2-round validation completes.

6\. Tien Nghich 2-round validation completes.

7\. For each novel:



&#x20;  \* Round 1 delta > 0

&#x20;  \* Round 2 delta > 0

&#x20;  \* average delta > 0

8\. Across both novels:



&#x20;  \* severe flags = 0

&#x20;  \* unsafe compression = 0

&#x20;  \* truncation = 0

&#x20;  \* no chapter regression over 3

&#x20;  \* rules rendered count = 0

&#x20;  \* raw NLP cache not injected into prompts

&#x20;  \* pending/rejected/deprecated/harmful/insufficient-evidence items excluded

9\. A multi-novel readiness report is created.



This goal intentionally does not require average delta >= +1.0.

The target is generalization across novels with positive lift and clean safety.



\## Hard constraints



Do not:



\* enable `--use-approved-rules`

\* render approved rules into prompts

\* weaken truncation detection

\* lower QA/evaluator gates

\* fake PASS

\* auto-approve memory, dictionary, or rules

\* inject raw NLP cache into prompts

\* copy Han Jue-specific dictionary/memory into Tien Nghich

\* make story-specific hacks that only work for one novel

\* load full 20MB files into prompt/chat/log

\* delete or hide failing artifacts



Rules remain verifier-only / QA-only.



\## Safe runtime profile



Use:



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



\## Progress log



Maintain:



```text

docs/implementation/NTS\_GOAL\_PROGRESS.md

```



After each meaningful checkpoint, append:



\* timestamp

\* current phase

\* files changed

\* tests run

\* latest artifact paths

\* LTP status

\* Han Jue validation status

\* Tien Nghich validation status

\* alignment status

\* round deltas

\* safety counters

\* rules rendered count

\* current hypothesis

\* next repair action



\## Phase A — Resume from current repo and artifacts



1\. Read:



&#x20;  \* `docs/implementation/NTS\_CURRENT\_STATE.md`

&#x20;  \* `docs/implementation/DECISIONS\_AND\_APPROVALS.md`

&#x20;  \* `docs/implementation/NTS\_CODEBASE\_BOOTSTRAP.md`

&#x20;  \* `docs/implementation/NTS\_NEXT\_ACTIONS.md`

&#x20;  \* `docs/implementation/NTS\_GOAL\_PROGRESS.md`

&#x20;  \* previous multi-novel artifacts under:

&#x20;    `artifacts/multi\_novel/20260530T200108Z/`

2\. Inspect `git status --short`.

3\. Do not overwrite user changes.

4\. Treat prior uncommitted goal artifacts as intentional unless they conflict with code.



\## Phase B — Fix Tien Nghich chapter alignment and sample selection



The previous goal failed too early at:



```text

No reliable title-matched alignment sample found for requested chapter 2

```



This must be treated as a repair target, not immediate FAIL.



Implement robust alignment for long raw + EPUB pairs:



1\. Improve raw chapter indexing:



&#x20;  \* support Chinese chapter headings

&#x20;  \* support numeric headings

&#x20;  \* support irregular heading spacing

&#x20;  \* support bounded extraction for chapters 1-10

&#x20;  \* do not read full file into prompt/log



2\. Improve EPUB chapter mapping:



&#x20;  \* natural sort

&#x20;  \* spine order if available

&#x20;  \* skip title-only pages

&#x20;  \* join adjacent sections when body continuation is detected

&#x20;  \* preserve chapter body text boundaries



3\. Improve alignment scoring:



&#x20;  \* title tokens

&#x20;  \* head/tail anchors

&#x20;  \* repeated named entities

&#x20;  \* rare phrase anchors

&#x20;  \* monotonic position

&#x20;  \* length ratio

&#x20;  \* fallback fuzzy matching

&#x20;  \* adjacent-section joining



4\. Add fallback sample selection:

&#x20;  If title-matched chapter sample is unavailable:



&#x20;  \* search nearby EPUB sections

&#x20;  \* try adjacent joins

&#x20;  \* try anchor-heavy windows

&#x20;  \* try non-title body-aligned samples

&#x20;  \* select a safe high-confidence sample

&#x20;  \* only fail after all fallback strategies are exhausted



5\. Required artifacts:



&#x20;  \* `tien\_nghich\_alignment\_diagnostic.json`

&#x20;  \* `tien\_nghich\_alignment\_diagnostic.md`

&#x20;  \* `tien\_nghich\_chapter\_mapping.json`

&#x20;  \* `tien\_nghich\_chapter\_mapping.md`

&#x20;  \* `tien\_nghich\_sample\_selection\_report.json`

&#x20;  \* `tien\_nghich\_sample\_selection\_report.md`

&#x20;  \* `tien\_nghich\_alignment\_failure\_report.md` only if truly unrecoverable



6\. Do not report final FAIL for Tien Nghich alignment until at least these strategies are attempted and artifacted:



&#x20;  \* natural sort / spine order

&#x20;  \* title token matching

&#x20;  \* head/tail anchor matching

&#x20;  \* adjacent section joining

&#x20;  \* body-only fallback sample selection

&#x20;  \* monotonic position fallback



\## Phase C — Tien Nghich NLP and dictionary candidate workflow



After chapters 1-10 can be extracted:



1\. Check LTP:



&#x20;  \* URL: `http://127.0.0.1:3003`

&#x20;  \* provider: `ltp\_server`



2\. Build NLP cache for Tien Nghich chapters 1-10.



3\. If LTP is unavailable:



&#x20;  \* if provider/auth/socket issue, report BLOCKED

&#x20;  \* if fallback mode is allowed by command, record degraded status explicitly

&#x20;  \* do not silently pretend LTP worked



4\. Build Tien Nghich dictionary candidates if useful.



&#x20;  \* Do not auto-approve.

&#x20;  \* Create human review package.

&#x20;  \* Pending candidates must not be injected into prompts.



5\. Required artifacts:



&#x20;  \* NLP cache manifest

&#x20;  \* NLP analysis report

&#x20;  \* dictionary candidate review package if candidates are built



\## Phase D — Multi-novel safe validation



Run safe 2-round validation for both novels:



1\. Han Jue chapters 1-10.

2\. Tien Nghich chapters 1-10.



Compare:



\* baseline stable behavior

&#x20; vs

\* safe hybrid profile:

&#x20; stable prompt + hybrid prompt + approved dictionary + approved applicable memory



Do not use approved rules.



For Tien Nghich, if approved dictionary/memory count is zero, still validate safely:



\* no pending candidates injected

\* no Han Jue-specific support injected

\* prompt support may be empty or project-appropriate



Required per novel:



\* Round 1 delta > 0

\* Round 2 delta > 0

\* average delta > 0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0



If a novel fails validation:



\* do not stop immediately

\* inspect artifacts

\* diagnose exact cause

\* fix evidence-backed general pipeline issue

\* rerun validation



\## Phase E — General optimization loop



Continue repairing until PASS, BLOCKED, or exhausted FAIL.



Allowed improvements:



\* chapter alignment bug fixes

\* sample selection bug fixes

\* large-file handling fixes

\* dictionary retrieval bug fixes

\* memory applicability bug fixes

\* project-scope isolation fixes

\* prompt support ranking/pruning improvements

\* unit construction fixes

\* terminology alias checks

\* safe artifact/reporting improvements

\* human-review candidate generation



Not allowed:



\* auto-approval

\* story-specific hacks

\* using reference translation in production prompt

\* weakening evaluator/safety gates

\* hiding failures

\* enabling rules in prompts



If both novels do not improve:



\* diagnose whether safe hybrid support is too sparse

\* mine candidates for human review

\* do not activate without explicit user approval

\* if approval is needed, pause with human review package paths instead of faking progress



\## Phase F — When to stop



\### PASS



Report PASS only when all final PASS targets are met.



\### FAIL



Report FAIL only after evidence-backed repair paths are exhausted.



Do not report FAIL merely because:



\* first Tien Nghich alignment attempt failed

\* one validation round was negative

\* dictionary/memory for Tien Nghich is empty

\* first sample selection attempt failed

\* a repair loop needs another targeted iteration



Report FAIL if:



\* tests fail and cannot be repaired

\* Tien Nghich alignment remains impossible after all fallback strategies are attempted and artifacted

\* either novel cannot achieve both rounds delta > 0 safely after evidence-backed repairs

\* either novel has blocking safety issues that cannot be repaired

\* general fixes overfit one novel and regress the other

\* rules leak into prompts

\* prompt budget exceeded

\* only way to pass is weakening QA/evaluator/safety gates

\* large-file ingestion remains unsafe or unstable



\### BLOCKED



Report BLOCKED if:



\* required Tien Nghich files are missing

\* provider/auth/model unavailable

\* LTP required but unavailable

\* workspace data missing

\* environment blocks filesystem/network/socket access



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



\* large-file safe ingestion

\* raw chapter indexing

\* EPUB chapter extraction

\* natural sort C2 before C10

\* title-only EPUB split avoidance

\* adjacent EPUB section joining

\* alignment fallback sample selection

\* multi-novel project isolation

\* dictionary/memory scope isolation

\* validation for a new project with no approved dictionary

\* rules rendered count remains 0

\* raw NLP cache not injected

\* prompt budget respected

\* artifact generation



\## Final report must include



\* PASS/FAIL/BLOCKED

\* files changed

\* tests run

\* LTP status

\* Tien Nghich data paths

\* Han Jue validation artifact

\* Tien Nghich validation artifact

\* Han Jue round deltas and average

\* Tien Nghich round deltas and average

\* safety counters

\* rules rendered count

\* dictionary/memory support stats per novel

\* large-file handling artifacts

\* alignment diagnostic artifacts

\* human review package paths

\* readiness report path if PASS

\* final recommendation



