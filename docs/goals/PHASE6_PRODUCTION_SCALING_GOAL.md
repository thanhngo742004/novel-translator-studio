\# PHASE6\_PRODUCTION\_SCALING\_GOAL



\## Objective



Scale Novel Translator Studio from 10-chapter validation to real long-form production translation.



Phase 5 is complete:



\* Han Jue validation passed.

\* Tien Nghich validation passed.

\* Safety counters clean.

\* Rules rendered count = 0.

\* Raw NLP cache not injected.

\* Final verification package and readiness report exist.



Phase 6 goal:

Translate longer novel batches safely, resumably, and reviewably.



\## Targets



Use two projects:



1\. Han Jue

2\. Tien Nghich



Start with 20 chapters per novel.

If stable, expand to 50 chapters.

Do not attempt full-novel translation until 20/50-chapter batches pass.



\## Hard rules



Do not:



\* use `--use-approved-rules`

\* render approved rules into prompts

\* weaken QA/evaluator/safety gates

\* fake PASS

\* inject raw NLP cache into prompts

\* copy project-specific memory/dictionary across projects

\* dump full copyrighted text into logs or review docs

\* delete failing artifacts



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



\## Required features



Implement or improve:



1\. Long batch production runner



&#x20;  \* 20-chapter batch

&#x20;  \* 50-chapter batch

&#x20;  \* resume from checkpoint

&#x20;  \* skip completed chunks

&#x20;  \* retry failed chunks safely



2\. Cost/token tracking



&#x20;  \* per chapter

&#x20;  \* per chunk

&#x20;  \* per provider/model

&#x20;  \* total batch cost estimate if available

&#x20;  \* API call count



3\. Batch QA dashboard artifacts



&#x20;  \* summary JSON

&#x20;  \* summary Markdown

&#x20;  \* chapter status table CSV

&#x20;  \* failed chunk table CSV

&#x20;  \* safety counters



4\. Export



&#x20;  \* chapter TXT outputs

&#x20;  \* combined TXT

&#x20;  \* optional EPUB export if already supported or easy to add safely

&#x20;  \* no broken chapter order



5\. Human review package



&#x20;  \* bounded snippets only

&#x20;  \* chapter QA table

&#x20;  \* dictionary/memory support stats

&#x20;  \* prompt artifact links

&#x20;  \* known warnings

&#x20;  \* final selected outputs



\## Required trials



Run:



1\. Han Jue 20-chapter production batch

2\. Tien Nghich 20-chapter production batch



If both pass, run:



3\. Han Jue 50-chapter production batch

4\. Tien Nghich 50-chapter production batch



\## PASS criteria



PASS only if:



\* full tests pass

\* 20-chapter batch passes for both novels

\* 50-chapter batch passes for both novels, or clearly documented BLOCKED reason if provider/env stops it

\* no truncation

\* no unsafe compression

\* no severe flags

\* no missing/empty outputs

\* no chapter order errors

\* rules rendered count = 0

\* raw NLP cache not injected

\* prompt budget respected

\* resume works

\* batch artifacts created

\* human review package created

\* export files created



\## BLOCKED criteria



Report BLOCKED only if:



\* provider/auth/model unavailable

\* both primary and fallback unavailable

\* LTP required but unavailable

\* workspace data missing

\* environment blocks filesystem/network/socket access



\## Work loop



If a batch fails:



1\. inspect artifacts

2\. identify exact chapter/chunk

3\. fix evidence-backed issue

4\. add/update tests

5\. rerun failed batch from checkpoint

6\. continue until PASS or BLOCKED



Do not stop with terminal FAIL.



\## Tests



After code changes, run:



```text

uv run --extra dev python -m pytest -q

```



If uv unavailable:



```text

python -m pytest -q

```



\## Final report



Include:



\* PASS / BLOCKED

\* files changed

\* tests run

\* Han Jue 20/50 batch artifacts

\* Tien Nghich 20/50 batch artifacts

\* chapters processed

\* chunks succeeded/failed/skipped

\* API calls used

\* token/cost summary

\* QA safety counters

\* rules rendered count

\* export paths

\* human review package paths

\* final recommendation



