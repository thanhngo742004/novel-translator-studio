param(
  [string]$Workspace = "."
)

Write-Host "Applying Phase 3 docs to workspace: $Workspace"
$root = Resolve-Path $Workspace
$docs = Join-Path $root "docs"
$specs = Join-Path $docs "specs"
$raw = Join-Path $docs "raw"
$prompts = Join-Path $docs "codex-prompts"
New-Item -ItemType Directory -Force -Path $specs | Out-Null
New-Item -ItemType Directory -Force -Path $raw | Out-Null
New-Item -ItemType Directory -Force -Path $prompts | Out-Null
Copy-Item -Force "docs\raw\phase3-manga-research.md" (Join-Path $raw "phase3-manga-research.md")
Copy-Item -Force "docs\specs\11_MANGA_ARCHITECTURE_PHASE3.md" (Join-Path $specs "11_MANGA_ARCHITECTURE_PHASE3.md")
Copy-Item -Force "docs\specs\12_MANGA_DATA_SCHEMA.md" (Join-Path $specs "12_MANGA_DATA_SCHEMA.md")
Copy-Item -Force "docs\specs\13_MANGA_CLI_SPEC.md" (Join-Path $specs "13_MANGA_CLI_SPEC.md")
Copy-Item -Force "docs\specs\14_MANGA_MVP_PLAN.md" (Join-Path $specs "14_MANGA_MVP_PLAN.md")
Copy-Item -Force "docs\codex-prompts\PHASE3_REVIEW_PROMPT.md" (Join-Path $prompts "PHASE3_REVIEW_PROMPT.md")
Copy-Item -Force "docs\codex-prompts\MANGA_MVP4A_LATER_IMPLEMENTATION_PROMPT.md" (Join-Path $prompts "MANGA_MVP4A_LATER_IMPLEMENTATION_PROMPT.md")
Copy-Item -Force "AGENTS_PHASE3_APPEND.md" (Join-Path $root "AGENTS_PHASE3_APPEND.md")
Write-Host "Done. Next: append AGENTS_PHASE3_APPEND.md to AGENTS.md manually."
