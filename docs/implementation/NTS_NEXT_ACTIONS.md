# NTS Next Actions

## MVP5I.1 only

- provider/model preflight before rollout
- classify `gpt-5.4` HTTP 404 as `model_route_not_found`
- test fallback model route
- BLOCKED before translation if primary and fallback fail
- diagnose chapter 2 production QA failure from artifact `han-jue_p_mpp4cdaw`
- add canary mode for 1-2 chunks/chapters
- rerun canary before full 10-chapter rollout
- rules rendered count must remain 0
