@echo off
setlocal

echo Setting up Novel Translator Studio Codex workspace...

if not exist workspace mkdir workspace
if not exist workspace\config mkdir workspace\config
if not exist workspace\artifacts mkdir workspace\artifacts
if not exist workspace\logs mkdir workspace\logs
if not exist workspace\cache mkdir workspace\cache

if not exist workspace\config\providers.yaml copy config\providers.example.yaml workspace\config\providers.yaml
if not exist workspace\config\task-routing.yaml copy config\task-routing.example.yaml workspace\config\task-routing.yaml
if not exist workspace\config\budget-limits.yaml copy config\budget-limits.example.yaml workspace\config\budget-limits.yaml

echo Done. Open Codex in this folder and start with docs/codex-prompts/INITIAL_CODEX_CHECK_PROMPT.md
endlocal
