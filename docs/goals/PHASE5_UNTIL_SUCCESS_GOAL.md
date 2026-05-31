\# PHASE5\_UNTIL\_SUCCESS\_GOAL



\## Objective



Finish Phase 5 for multi-novel text translation.



This is an until-success long-running goal.



Do not stop with FAIL after one or a few attempts. If a run fails but the environment still works, treat it as a repair target, not a terminal result.



Allowed terminal outcomes:



1\. PASS / SUCCESS

2\. BLOCKED\_PROVIDER\_OR\_ENVIRONMENT

3\. NEEDS\_HUMAN\_APPROVAL



Do not report FAIL as a final terminal status unless the user explicitly asks to stop the goal. If a validation/rollout/alignment run fails, diagnose it, fix it, test it, and rerun.



The tool is meant to translate many novels, not overfit one novel.



Target novels:



1\. Han Jue

2\. Tien Nghich

&#x20;  - raw: `raw tien nghich`

&#x20;  - translated EPUB: `translated tien nghich.epub`

&#x20;  - files are large, around 20MB

&#x20;  - use bounded reading, streaming, indexing, chunking

&#x20;  - never paste full novel text into chat, prompt, logs, or review docs



\---



\## Non-negotiable safety rules



Do not:



\- weaken truncation detection

\- weaken QA/evaluator/safety gates

\- fake PASS

\- hide failing artifacts

\- delete failing artifacts

\- inject raw NLP cache into prompts

\- render approved rules into prompts unless a later explicit human-approved phase says otherwise

\- use `--use-approved-rules`

\- copy Han Jue-specific dictionary/memory into Tien Nghich

\- make story-specific hacks that only work for one novel

\- dump full 20MB files into chat/logs/prompts

\- silently auto-approve risky candidates without review artifacts



Rules remain verifier-only / QA-only.



Rules rendered count must remain 0.



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



Do not use:



\--use-approved-rules

Current known state



Previous runs showed:



Full tests reached 212 passed.

LTP was healthy at http://127.0.0.1:3003.

Han Jue canary and 10-chapter rollout passed previously.

Han Jue validation still had unstable/low lift.

Tien Nghich files were discovered:

raw: test\_data/translation\_eval/han\_jue/raw tien nghich.txt

EPUB: test\_data/translation\_eval/han\_jue/translated tien nghich.epub

Tien Nghich project exists.

Tien Nghich validation currently blocks around chapter 2 alignment/sample selection.

Existing diagnostics show candidates existed but accepted safe candidates were 0 due high reference/source ratio.

Previous attempts stopped too early with FAIL.

This goal must not stop at the same early failure.

Core philosophy



If something fails:



Read the artifact.

Identify the exact blocker.

Fix the smallest evidence-backed general issue.

Add or update tests.

Run tests.

Rerun the relevant trial/validation.

Update progress log.

Continue.



Do not stop just because:



first alignment rerun fails

first validation has negative delta

Tien Nghich has no approved dictionary/memory

chapter 2 sample selection fails

a repair loop needs another iteration

average delta is low

a candidate needs human review



Treat those as repair targets.



Progress log



Maintain:



docs/implementation/NTS\_GOAL\_PROGRESS.md



Every checkpoint must include:



timestamp

current phase

files changed

tests run

artifact paths

current blocker

fix attempted

result

next action

whether rules rendered count remains 0

Phase A — Stabilize repo and current state

Inspect git status --short.

Read:

docs/implementation/NTS\_CURRENT\_STATE.md

docs/implementation/DECISIONS\_AND\_APPROVALS.md

docs/implementation/NTS\_CODEBASE\_BOOTSTRAP.md

docs/implementation/NTS\_NEXT\_ACTIONS.md

docs/implementation/NTS\_GOAL\_PROGRESS.md

Inspect existing artifacts:

Han Jue latest validation/rollout artifacts

Tien Nghich multi-novel artifacts

Tien Nghich alignment failure reports

Do not overwrite user changes.

If worktree has intentional untracked goal/progress files, keep them.

Phase B — Fix Tien Nghich alignment/sample selection until it works



Main blocker:



Tien Nghich chapter 2: candidates existed, but accepted safe candidates = 0 due high reference/source ratio.



Do not treat this as final failure.



Implement a broader alignment/sample-selection redesign:



B1. Raw chapter indexing



Support:



Chinese chapter headings

numeric headings

irregular heading spacing

bounded chapter extraction

first 10 chapter extraction

streaming/offset-based reading

no full-file prompt/log dumps

B2. EPUB chapter extraction



Support:



spine order

natural sort

skip title-only pages

detect title vs body pages

join adjacent body sections when needed

preserve section/paragraph offsets

avoid selecting C10 before C2

B3. Bounded body-window selection



Implement body-window candidates:



head windows

mid windows

tail windows

sliding windows

paragraph windows

adjacent joined windows

window sizes around 300–800 Chinese chars when appropriate

smaller subwindows when whole-chapter ratio is unsafe

B4. Candidate scoring



Score with:



anchor overlap

rare phrase anchors

repeated entity anchors

head/tail anchors

monotonic chapter position

length ratio

paragraph density

punctuation/quote shape

body-only support if title is unreliable



Do not globally weaken ratio gates. Select smaller safer windows.



B5. Required artifacts



Create/update:



artifacts/multi\_novel/<run\_id>/bounded\_window\_alignment\_report.json

artifacts/multi\_novel/<run\_id>/bounded\_window\_alignment\_report.md

artifacts/multi\_novel/<run\_id>/bounded\_window\_candidates\_chapter\_2.csv

artifacts/multi\_novel/<run\_id>/accepted\_alignment\_samples.json

artifacts/multi\_novel/<run\_id>/rejected\_alignment\_samples.json

artifacts/multi\_novel/<run\_id>/alignment\_selection\_debug.md



Every rejected sample must have a reason.



Every accepted sample must show:



source offsets

reference offsets

source length

reference length

ratio

matched anchors

final score



Continue until Tien Nghich chapters 1-10 have accepted safe samples or a true environment/provider blocker occurs.



Phase C — LTP/NLP and project isolation



For Tien Nghich:



Check LTP at http://127.0.0.1:3003.

Build NLP cache for chapters 1-10 if needed.

If LTP unavailable due environment, report BLOCKED\_PROVIDER\_OR\_ENVIRONMENT.

Do not inject raw NLP cache into prompts.

Keep Tien Nghich dictionary/memory project-scoped.

Do not copy Han Jue-specific dictionary/memory into Tien Nghich.

If dictionary/memory candidates are needed, create human-review package and continue with approved-only support.



If human approval is required to improve quality:



create review package

report NEEDS\_HUMAN\_APPROVAL

do not call it FAIL

Phase D — Validation loop for both novels



Run safe 2-round validation for:



Han Jue chapters 1-10

Tien Nghich chapters 1-10



Use safe config only.



Do not use approved rules.



Required target for each novel:



Round 1 delta > 0

Round 2 delta > 0

average delta > 0

severe flags = 0

unsafe compression = 0

truncation = 0

no chapter regression over 3

rules rendered count = 0

raw NLP cache not injected



If validation fails:



Inspect artifacts.

Identify whether failure is:

alignment/sample selection

prompt support sparse

dictionary retrieval

memory applicability

unit construction

terminology alias

evaluator false positive

provider instability

Fix the general evidence-backed issue.

Add tests.

Rerun.



Do not stop at the first negative round.



Phase E — Supported translation trial



Run the strongest supported production/translation trial for the first 10 chapters.



If both novels support 10-chapter production trial, run both.



If Tien Nghich production trial is not fully supported yet, implement the missing general support or document why it is blocked.



Trial must satisfy:



no truncation

no unsafe compression

no missing output

no empty output

no overlong blocking output

no raw NLP leakage

rules rendered count = 0

prompt budget respected

human review package created



If trial fails, repair and rerun.



Phase F — Human verification package



Create final human review packages for:



Han Jue validation/trial

Tien Nghich validation/trial

dictionary/memory candidates if generated

Phase 5 final readiness



Human review must include:



bounded snippets only

source/output/reference snippets

validation scores

QA warnings

support items used

candidate review tables

prompt artifacts

known limitations



Do not auto-approve candidates without explicit user approval.



If approval is needed:



stop with NEEDS\_HUMAN\_APPROVAL

include exact candidate review package path

include approve/reject commands

do not call FAIL

Phase G — Final readiness report



If all gates pass, create:



docs/implementation/PHASE5\_FINAL\_READINESS\_REPORT.md



Must include:



Han Jue validation result

Tien Nghich validation result

round deltas and averages

production/trial results

safety counters

rules rendered count

LTP/NLP status

large-file handling summary

dictionary/memory status per novel

human review paths

known limitations

whether Phase 5 is ready for broader text-novel production

next recommended phase

Tests



After code changes, run:



uv run --extra dev python -m pytest -q



If uv unavailable:



python -m pytest -q



Add/update tests for:



raw chapter indexing

EPUB extraction

large-file bounded reading

body-window sample selection

title-only section skipping

adjacent section joining

ratio-safe sample acceptance

rejected sample reasons

multi-novel project isolation

dictionary/memory scope isolation

rules rendered count = 0

raw NLP cache not injected

prompt budget respected

artifact generation

validation workflow for project with no approved dictionary/memory

Terminal conditions

PASS / SUCCESS



Report PASS only when:



full tests pass

Han Jue validation passes both rounds with positive deltas

Tien Nghich validation passes both rounds with positive deltas

both average deltas > 0

safety counters clean

rules rendered count = 0

raw NLP cache not injected

supported production/translation trial passes

human review packages created

Phase 5 readiness report created

BLOCKED\_PROVIDER\_OR\_ENVIRONMENT



Report BLOCKED only if:



provider/auth/model unavailable

both primary and fallback routes unavailable

LTP required but unavailable

required files missing

workspace data missing

environment blocks filesystem/network/socket access

NEEDS\_HUMAN\_APPROVAL



Report NEEDS\_HUMAN\_APPROVAL only if:



new dictionary/memory candidates are necessary for further progress

candidates were generated safely

review package is created

explicit approval is required before activation



Do not report FAIL as a terminal state.



If a run fails but is not provider/environment blocked and does not require human approval, continue repairing.



Final response requirements



Final response must include:



PASS / BLOCKED\_PROVIDER\_OR\_ENVIRONMENT / NEEDS\_HUMAN\_APPROVAL

files changed

tests run

LTP status

Han Jue validation artifact

Tien Nghich validation artifact

Han Jue round deltas and average

Tien Nghich round deltas and average

safety counters

rules rendered count

dictionary/memory support stats

translation/trial artifact paths

human review package paths

Phase 5 readiness report path if PASS

final recommendation

