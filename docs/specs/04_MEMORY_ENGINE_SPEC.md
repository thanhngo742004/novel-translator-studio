# 04 — Memory Engine Spec

## LAMM-T implementation rule

LAMM-T should be implemented as one canonical memory store with typed records, not as separate databases per layer.

Memory item envelope should eventually support:

- `id`
- `memory_type`
- `layer`
- `status`
- `scope_json`
- `source_key`
- `concept_key`
- `entity_key`
- `value_json`
- `rules_json`
- `confidence_score`
- `confidence_json`
- `conflict_cluster_id`
- `current_version`
- `created_at`
- `updated_at`

## MVP1 memory types

Start with:

- Term Memory
- Name Memory
- Pronoun Memory
- Style Memory
- Correction Memory

Evidence/provenance should be metadata attached to memory, not necessarily separate memory types.

## Services

### Memory Writer

Writes memory candidates, evidence refs and audit logs.

### Memory Retriever

Builds compact memory bundle by scope and token budget.

### Memory Curator

Merges duplicates, deprecates stale machine-generated items, and summarizes style rules.

### Conflict Resolver

Creates conflict clusters and handles winner/challenger logic.

### Confidence Scorer

Deterministic scoring with configurable weights.

### Evidence Manager

Stores evidence excerpts and artifact refs.

### Export Compiler

Compiles active approved memory into compact plugin bundles.

## MVP0 note

Do not implement Memory Engine in MVP0 except placeholder interfaces/docs.
