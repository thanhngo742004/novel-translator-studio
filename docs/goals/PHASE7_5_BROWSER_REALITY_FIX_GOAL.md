\# PHASE7\_5\_BROWSER\_REALITY\_FIX\_GOAL



\## Objective



Fix Phase 7 GUI based on real browser behavior, not mocked tests.



Current user-tested result:



\* The GUI still behaves like Phase 7.3.

\* LTP status still reports active incorrectly.

\* Workspace/project open still only shows a path instead of opening File Explorer.

\* API preflight still fails even with correct provider/base URL/model/API key.

\* Translation cannot be tested because API preflight blocks it.

\* Previous automated tests passed, but browser behavior did not change.



Therefore, automated unit tests alone are not enough.

Do not report PASS based only on pytest/node checks.



Allowed terminal outcomes:



1\. PASS only after real browser smoke behavior is confirmed.

2\. BLOCKED\_PROVIDER\_OR\_ENVIRONMENT only if provider/auth/model/network/environment genuinely blocks execution.



Do not report PASS if the browser still behaves like Phase 7.3.



\---



\## First priority: eliminate stale-server / stale-frontend problems



Before changing logic, verify that the browser is actually loading the latest backend and frontend.



Implement:



1\. Backend version endpoint:



```text

GET /api/gui/version

```



It must return:



\* git commit if available

\* server start timestamp

\* frontend asset version/hash

\* app.js modified time/hash

\* backend file modified time/hash

\* phase label: `phase7.5-browser-reality-fix`



2\. Frontend must display a small debug/version label in Settings or footer:



```text

GUI build: phase7.5 / <timestamp or hash>

Backend: <timestamp or hash>

```



3\. Serve frontend with no-cache headers during local dev:



\* `Cache-Control: no-store`

\* `Pragma: no-cache`

\* `Expires: 0`



4\. Add cache-busting for app.js/styles.css if static serving supports it.



5\. Add a visible “Làm mới trạng thái” button that refetches health, LTP, provider, and workspace status.



PASS cannot be claimed unless the user can see the new Phase 7.5 version label in browser.



\---



\## Required debug action log



Add a visible debug/status panel, at least in Settings or Advanced Details:



```text

Lần bấm gần nhất:

\- button id

\- action name

\- endpoint called

\- request payload summary, with API key redacted

\- response status

\- response message

\- timestamp

```



Every important button must update this panel.



This is required so the user can see whether a click actually called backend.



No button may silently do nothing.



\---



\## Fix 1 — LTP status must not false-positive



Current user result:



\* LTP is off.

\* GUI still says LTP is working.



Required behavior:



\* If LTP is off, GUI must show `LTP chưa chạy`.

\* Healthy is true only when real LTP analyze succeeds.

\* Port reachability alone is not enough.

\* Stale cached healthy status is not allowed.

\* The button `Kiểm tra LTP` must force a fresh check every time.



Backend endpoint:



```text

GET /api/ltp/status?fresh=1

```



Required real check:



1\. Try existing NTS LTP/chinese\_nlp health checker if available.

2\. Or call real ltp\_server analyze endpoint with tiny Chinese text:



```text

我爱北京天安门。

```



3\. Validate that returned data contains real segmentation/tokens.

4\. If analyze fails, return healthy=false.



Expected statuses:



```text

healthy

unavailable

reachable\_but\_unhealthy

error

```



If LTP off:



```json

{

&#x20; "ok": true,

&#x20; "data": {

&#x20;   "status": "unavailable",

&#x20;   "healthy": false,

&#x20;   "message": "LTP chưa chạy"

&#x20; }

}

```



If port reachable but invalid response:



```json

{

&#x20; "ok": true,

&#x20; "data": {

&#x20;   "status": "reachable\_but\_unhealthy",

&#x20;   "healthy": false,

&#x20;   "message": "Có tiến trình ở cổng LTP nhưng không trả kết quả LTP hợp lệ"

&#x20; }

}

```



Tests must include:



\* LTP off returns healthy false.

\* Invalid response returns healthy false.

\* Valid analyze response returns healthy true.

\* Fresh check does not reuse stale healthy cache.

\* UI displays `LTP chưa chạy` when healthy false.



\---



\## Fix 2 — Workspace/project folder must open File Explorer



User confirmed this command works:



```powershell

Start-Process explorer.exe "<workspace\_path>"

```



Backend must use equivalent behavior.



Required behavior:



\* `Mở workspace` tries to open File Explorer.

\* `Mở dự án` tries to open File Explorer.

\* Showing/copying path is fallback only if OS open fails.

\* `Mở dự án` must not open technical details.



Backend endpoints:



```text

POST /api/system/open-path

POST /api/projects/{project}/open-folder

```



Windows implementation:



\* prefer `subprocess.Popen(\["explorer.exe", normalized\_path])`

\* or `os.startfile(normalized\_path)`



Security:



\* normalize path

\* reject path traversal

\* only allow workspace/project/output/artifact paths

\* do not open arbitrary user-provided paths outside allowed roots



Response if opened:



```json

{

&#x20; "ok": true,

&#x20; "data": {

&#x20;   "opened": true,

&#x20;   "method": "explorer.exe",

&#x20;   "path": "..."

&#x20; }

}

```



Response if fallback:



```json

{

&#x20; "ok": true,

&#x20; "data": {

&#x20;   "opened": false,

&#x20;   "fallback": "copy\_path",

&#x20;   "path": "...",

&#x20;   "message": "Không mở được File Explorer, hãy sao chép đường dẫn."

&#x20; }

}

```



Tests must mock and verify:



\* explorer.exe/os.startfile is called for workspace.

\* explorer.exe/os.startfile is called for project.

\* unsafe outside path rejected.

\* fallback only after OS open failure.



\---



\## Fix 3 — API preflight must be real and compatible with OpenAI-compatible providers



Current user result:



\* User enters correct provider/base URL/model/API key.

\* GUI says it is wrong.

\* This blocks all translation tests.



Required behavior:



\* Use GUI-saved provider config exactly.

\* GUI config overrides env for GUI-triggered actions.

\* API key is sent only in local POST/test call.

\* API key is never returned by GET/status/test response.

\* Do not rely only on `/v1/models`.



Provider test endpoint:



```text

POST /api/settings/provider/test

```



Preflight strategy:



1\. Load saved GUI config:



&#x20;  \* provider

&#x20;  \* base\_url

&#x20;  \* primary\_model

&#x20;  \* fallback\_model

&#x20;  \* api\_key



2\. Normalize base URL:



&#x20;  \* `https://host/v1` must not become `/v1/v1`

&#x20;  \* if user gives `https://host`, support adding `/v1` only if required

&#x20;  \* record sanitized attempted URL



3\. Try existing NTS provider preflight if available.



4\. If not enough, try minimal OpenAI-compatible chat completion:



```json

{

&#x20; "model": "<primary\_model>",

&#x20; "messages": \[{"role": "user", "content": "ping"}],

&#x20; "max\_tokens": 1

}

```



5\. If primary fails and fallback exists, try fallback.



6\. `/models` may be used only as optional diagnostic, not as the only pass/fail gate.



Response must include:



\* ok

\* provider

\* base\_url\_sanitized

\* primary\_model

\* fallback\_model

\* attempted\_primary

\* attempted\_fallback

\* chosen\_model

\* route\_status

\* latency\_ms

\* error\_category

\* error\_summary

\* no raw API key



Error categories:



```text

auth\_error

base\_url\_unreachable

model\_not\_found

provider\_response\_error

timeout

invalid\_config

unknown\_error

```



Tests must include:



\* saved GUI provider config is used.

\* no duplicate `/v1/v1`.

\* primary model success passes.

\* primary fail + fallback success passes.

\* `/v1/models` failure alone does not fail if chat completion succeeds.

\* wrong key returns auth\_error without leaking key.

\* raw key absent from all response fields.



\---



\## Fix 4 — Translation must start real Phase 6 job only after API preflight works



After API preflight passes, `Bắt đầu dịch` must call real backend execution.



Endpoint:



```text

POST /api/projects/{project}/translate/batch

```



Must start actual Phase 6 rollout/translation through:



\* direct core function, or

\* CLI subprocess wrapper, or

\* existing production rollout runner



Not acceptable:



\* fake safe task record only

\* completed job without real run

\* progress timer not backed by job/artifacts



Payload must include:



\* project

\* chapter\_start

\* chapter\_end

\* preset

\* safe\_profile true

\* resumable true/false

\* GUI provider config reference

\* `use\_approved\_rules=false`



Job endpoint:



```text

GET /api/jobs/{job\_id}

```



Must return:



\* status

\* stage

\* current chapter if known

\* current chunk if known

\* chapters completed/total

\* chunks completed/total if known

\* percent

\* latest message

\* artifact path

\* error if any



Progress bar:



\* must poll real job endpoint

\* must not fake percent

\* must not show 100% before completion

\* must stop polling at terminal state



Tests must include:



\* translate endpoint invokes real runner or mocked real runner.

\* provider config passed to run.

\* selected chapter range passed.

\* `use\_approved\_rules=false`.

\* job percent from artifacts/status.

\* completed job returns 100%.

\* blocked/error job shows blocked/error.



\---



\## Fix 5 — Button audit must be browser-reality oriented



Every visible button must:



1\. call endpoint,

2\. update UI state,

3\. navigate,

4\. or show explicit placeholder.



Add a generated/updated button audit artifact:



```text

docs/implementation/PHASE7\_5\_BUTTON\_REALITY\_AUDIT.md

```



For each button:



\* page

\* button text

\* button id/data-action

\* expected endpoint/action

\* frontend handler name

\* backend endpoint if any

\* current status:



&#x20; \* real

&#x20; \* frontend-state

&#x20; \* placeholder

&#x20; \* disabled

\* tested by:



&#x20; \* unit

&#x20; \* HTTP smoke

&#x20; \* browser manual pending/done



Buttons that remain placeholders must be listed clearly.



\---



\## Required browser smoke checklist



Create/update:



```text

docs/implementation/PHASE7\_5\_BROWSER\_SMOKE\_CHECKLIST.md

```



Checklist must be precise:



1\. Confirm visible version label says Phase 7.5.

2\. With LTP off:



&#x20;  \* click `Kiểm tra LTP`

&#x20;  \* expected: `LTP chưa chạy`

3\. Start LTP:



&#x20;  \* click `Kiểm tra LTP`

&#x20;  \* expected: healthy only after real analyze succeeds

4\. Click `Mở workspace`



&#x20;  \* expected: Windows File Explorer opens

5\. Click `Mở dự án`



&#x20;  \* expected: Windows File Explorer opens project/output folder

6\. Save provider config:



&#x20;  \* provider

&#x20;  \* base URL

&#x20;  \* primary model

&#x20;  \* fallback model

&#x20;  \* API key

7\. Click `Kiểm tra API`



&#x20;  \* expected: real preflight, not lightweight placeholder

8\. If API passes:



&#x20;  \* go to Dịch truyện

&#x20;  \* choose project

&#x20;  \* choose `Dịch thử 1 chương`

&#x20;  \* click `Bắt đầu dịch`

&#x20;  \* expected: real job starts

&#x20;  \* progress panel shows job id/status/percent/artifacts



\---



\## Do not claim PASS unless all are true



PASS criteria:



\* full tests pass

\* frontend version label confirms latest Phase 7.5 loaded

\* LTP off shows off, not active

\* LTP on shows healthy only after real analyze

\* workspace opens File Explorer

\* project opens File Explorer

\* API preflight works with saved GUI provider config

\* API key remains redacted

\* provider settings remain editable

\* translation starts real job after API pass

\* progress bar uses real job status/artifacts

\* no approved rules used

\* raw NLP cache not injected

\* manga remains Coming Soon only

\* browser smoke checklist is manually confirmed



If automated checks pass but browser still fails, continue fixing. Do not report PASS.



\---



\## BLOCKED criteria



Report BLOCKED\_PROVIDER\_OR\_ENVIRONMENT only if:



\* provider/auth/model genuinely unavailable after correct preflight

\* both primary and fallback fail for real reasons

\* LTP required but unavailable

\* OS blocks folder opening despite correct backend command

\* workspace data missing

\* filesystem/network/socket access blocked



\---



\## Tests to run



Run:



```text

node --check apps/gui/frontend/app.js

uv run --extra dev python -m pytest tests/test\_phase7\_gui.py -q

uv run --extra dev python -m pytest -q

```



Also run backend HTTP smoke with the actual server:



```text

GET /api/gui/version

GET /api/ltp/status?fresh=1

POST /api/system/open-path

POST /api/settings/provider/test

```



\---



\## Final report



Final report must include:



\* PASS / BLOCKED\_PROVIDER\_OR\_ENVIRONMENT

\* files changed

\* tests run

\* backend start command

\* frontend URL

\* version label/hash

\* LTP off/on behavior

\* folder opening behavior

\* provider preflight behavior

\* translation job behavior

\* progress polling behavior

\* button reality audit path

\* browser smoke checklist path

\* remaining placeholders

\* security evidence

\* final recommendation



