#!/usr/bin/env bash
set -euo pipefail

echo "Setting up Novel Translator Studio Codex workspace..."
mkdir -p workspace/config workspace/artifacts workspace/logs workspace/cache

[ -f workspace/config/providers.yaml ] || cp config/providers.example.yaml workspace/config/providers.yaml
[ -f workspace/config/task-routing.yaml ] || cp config/task-routing.example.yaml workspace/config/task-routing.yaml
[ -f workspace/config/budget-limits.yaml ] || cp config/budget-limits.example.yaml workspace/config/budget-limits.yaml

echo "Done. Open Codex in this folder and start with docs/codex-prompts/INITIAL_CODEX_CHECK_PROMPT.md"
