\# PHASE5\_MULTI\_NOVEL\_GENERALIZATION\_GOAL



\## Objective



Generalize Phase 5 production translation beyond one novel.



The goal is no longer to over-optimize Han Jue to average delta >= +1.0.

The new goal is to prove the tool can handle multiple novels safely and improve quality on both.



Target novels:



1\. Existing Han Jue project / data.

2\. New Tien Nghich data:



&#x20;  \* raw file: `raw tien nghich.txt`

&#x20;  \* translated file: `translated tien nghich.epub`



The Tien Nghich files are large, around 20MB. The tool must process them safely with streaming/chapter indexing/chunking. Do not load full files into prompts, logs, memory, or ChatGPT-visible output.



\## Final PASS target



PASS only if all are true:



1\. Full test suite passes.

2\. LTP/NLP processing for both novels works or degrades safely with explicit artifacts.

3\. The first 10 chapters of both novels can be indexed/aligned/validated.

4\. Safe production profile is used:



&#x20;  \* stable prompt

&#x20;  \* hybrid prompt

&#x20;  \* approved dictionary where available

&#x20;  \* approved memory where applicable

&#x20;  \* no approved rules in prompts

5\. For each novel, 2 validation rounds complete.

6\. For each novel:



&#x20;  \* Round 1 delta > 0

&#x20;  \* Round 2 delta > 0

&#x20;  \* average delta > 0

7\. Across both novels:



&#x20;  \* severe flags = 0

&#x20;  \* unsafe compression = 0

&#x20;  \* truncation = 0

&#x20;  \* no chapter regression over 3

&#x20;  \* rules rendered count = 0

&#x20;  \* raw NLP cache is not injected into prompts

&#x20;  \* pending/rejected/deprecated/harmful/insufficient-evidence items are excluded

8\. A multi-novel readiness report is created.



This goal intentionally relaxes the previous average delta >= +1.0 requirement.

The tool is meant to translate many novels, not overfit one novel.



\## Read first



Read:



\* `docs/implementation/NTS\_CURRENT\_STATE.md`

\* `docs/implementation/DECISIONS\_AND\_APPROVALS.md`

\* `docs/implementation/NTS\_CODEBASE\_BOOTSTRAP.md`

\* `docs/implementation/NTS\_NEXT\_ACTIONS.md`

\* `docs/implementation/NTS\_GOAL\_PROGRESS.md`

\* `docs/implementation/CONTINUOUS\_MVP\_PROGRESS.md` if present

\* latest Han Jue validation and rollout artifacts

\* latest Han Jue memory candidate mining artifact:

&#x20; `workspace\_mvp5c\_smoke\_20260525210758/artifacts/memory\_candidate\_mining/han-jue\_mining\_1780147607357`



Do not paste large artifact contents into chat. Read locally and summarize.



\## Hard constraints



Do not:



\* enable `--use-approved-rules`

\* render approved rules into prompts

\* weaken truncation detection

\* lower QA gates

\* fake PASS

\* auto-approve memory, dictionary, or rules

\* inject raw NLP cache into prompts

\* personalize global prompt to Han Jue only

\* copy Han Jue-specific dictionary or memory into Tien Nghich unless the scope is explicitly project-agnostic and safe

\* load entire 20MB files into memory/prompt/logs

\* dump full novel text into artifacts except controlled internal chunk files

\* delete or hide failing artifacts



Rules remain verifier-only / QA-only.



\## Safe production profile



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



\## Phase A — Repo and data discovery



1\. Inspect current git status.

2\. Locate both Tien Nghich files without reading them fully into chat:



&#x20;  \* `raw tien nghich`

&#x20;  \* `translated tien nghich.epub`

3\. If needed, create a project slug:



&#x20;  \* `tien-nghich`

4\. Create data manifest artifacts:



&#x20;  \* file path

&#x20;  \* file size

&#x20;  \* type

&#x20;  \* chapter count estimate

&#x20;  \* first 10 chapter availability

&#x20;  \* extraction method

5\. If filenames or paths are ambiguous, search locally and report candidates.



Required artifacts:



```text

artifacts/multi\_novel/<run\_id>/data\_discovery\_report.json

artifacts/multi\_novel/<run\_id>/data\_discovery\_report.md

```



\## Phase B — Large-file safe ingestion



Implement or fix large-file handling if needed.



Requirements:



\* stream or chunk files instead of reading whole 20MB file into prompt/log

\* for EPUB, extract spine/chapters safely

\* for raw text, build chapter index safely

\* create chapter manifests

\* extract only chapters 1-10 for validation

\* avoid printing full chapter text to chat

\* write only bounded snippets in review artifacts



Required artifacts:



```text

artifacts/multi\_novel/<run\_id>/tien\_nghich\_chapter\_manifest.json

artifacts/multi\_novel/<run\_id>/tien\_nghich\_chapter\_manifest.md

```



\## Phase C — LTP/NLP cache for new novel



Ensure LTP server is checked.



Use provider:



```text

ltp\_server

```



Expected URL:



```text

http://127.0.0.1:3003

```



For Tien Nghich chapters 1-10:



\* run or build NLP cache

\* require degraded\_chapter\_count = 0 if LTP is available

\* if LTP unavailable, report BLOCKED or degraded explicitly depending on command mode

\* do not inject raw NLP cache into prompts



Required artifacts:



```text

artifacts/nlp/tien-nghich/nlp\_cache\_manifest.json

artifacts/nlp/tien-nghich/nlp\_analysis\_report.md

```



\## Phase D — Project dictionary/memory safety



For Tien Nghich:



\* build dictionary candidates if needed

\* do not auto-approve candidates

\* produce human review package

\* approved dictionary count may be 0 for first validation

\* pending dictionary candidates must not be injected into prompts



For Han Jue:



\* do not auto-approve pending memory candidates

\* you may inspect current pending candidates and report whether they should be human-reviewed

\* do not activate them without explicit human approval



Important:



\* Dictionary is project-scoped.

\* Memory is project-scoped unless explicitly global and safe.

\* Rules remain verifier-only.

\* The goal is multi-novel generalization, not Han Jue overfitting.



\## Phase E — Multi-novel validation profile



Run safe 2-round validation on:



1\. Han Jue chapters 1-10.

2\. Tien Nghich chapters 1-10.



Validation for each novel must compare:



\* baseline stable production behavior

&#x20; vs

\* safe hybrid production profile:

&#x20; stable prompt + hybrid prompt + approved dictionary + approved applicable memory



Do not use approved rules.



If Tien Nghich has no approved dictionary/memory yet, validation should still run with safe hybrid infrastructure but must not inject pending candidates.



Required per-novel PASS:



```text

Round 1 delta > 0

Round 2 delta > 0

average delta > 0

severe flags = 0

unsafe compression = 0

truncation = 0

no chapter regression over 3

rules rendered count = 0

```



If either novel has one negative round, do not PASS. Diagnose and repair evidence-backed general issues only.



\## Phase F — General prompt/pipeline optimization



Allowed improvements:



\* general stable/hybrid prompt support wording that improves flow across novels

\* dictionary retrieval bug fixes

\* memory applicability bug fixes

\* prompt support ranking/pruning improvements

\* production unit construction fixes

\* large-file ingestion fixes

\* chapter alignment improvements

\* terminology alias checks if evidence-backed

\* safer artifact reporting

\* generic formatting/flow constraints that are not story-specific



Not allowed:



\* story-specific hacks for only Han Jue

\* story-specific hacks for only Tien Nghich

\* auto-approval of candidates

\* using translated reference text in production prompt

\* weakening evaluation/safety gates

\* hiding failures



If a fix only helps one novel and hurts the other, do not treat it as general improvement. Diagnose and scope it.



\## Phase G — Progress log



Maintain:



```text

docs/implementation/NTS\_GOAL\_PROGRESS.md

```



Every checkpoint must include:



\* timestamp

\* current phase

\* files changed

\* tests run

\* LTP status

\* data discovery status

\* latest Han Jue validation artifact

\* latest Tien Nghich validation artifact

\* round deltas for each novel

\* safety counters

\* rules rendered count

\* current hypothesis

\* next action



\## Phase H — Final report



If PASS, create:



```text

docs/implementation/PHASE5\_MULTI\_NOVEL\_READINESS\_REPORT.md

```



The report must include:



\* Han Jue validation result

\* Tien Nghich validation result

\* deltas per round

\* average deltas

\* safety counters

\* rules rendered count

\* LTP/NLP status

\* large-file handling summary

\* dictionary/memory status for each novel

\* known limitations

\* whether Phase 5 is ready for broader text-novel production

\* next recommended phase:



&#x20; \* broader production scaling, or

&#x20; \* Phase 6 manga pipeline



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



\* large file safe ingestion

\* EPUB chapter extraction

\* raw chapter indexing

\* multi-novel project isolation

\* dictionary/memory scope isolation

\* validation for a new project without approved dictionary

\* rules rendered count remains 0

\* raw NLP cache not injected

\* prompt budget respected

\* artifact generation



\## PASS criteria



PASS only if:



\* full tests pass

\* Tien Nghich data discovery succeeds

\* Tien Nghich chapters 1-10 are safely extracted/aligned

\* LTP/NLP cache succeeds or degrades with explicit accepted status

\* Han Jue 2-round validation passes with both rounds delta > 0

\* Tien Nghich 2-round validation passes with both rounds delta > 0

\* both novels have average delta > 0

\* severe flags = 0

\* unsafe compression = 0

\* truncation = 0

\* no chapter regression over 3

\* rules rendered count = 0

\* raw NLP cache not injected

\* no story-specific unsafe hacks

\* multi-novel readiness report created



\## FAIL criteria



Report FAIL if:



\* tests fail and cannot be repaired

\* either novel cannot achieve both rounds delta > 0 safely

\* either novel has blocking safety issues

\* general fixes overfit one novel and regress the other

\* rules leak into prompts

\* prompt budget exceeded

\* the only way to pass is weakening QA/evaluator/safety gates

\* large-file ingestion remains unsafe or unstable



\## BLOCKED criteria



Report BLOCKED if:



\* required Tien Nghich files are missing

\* provider/auth/model unavailable

\* LTP required but unavailable

\* workspace data missing

\* environment blocks filesystem/network/socket access



\## Final response



Final report must include:



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

\* human review package paths

\* readiness report path if PASS

\* final recommendation



