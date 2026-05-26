from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any

from nts_core.approved_memory_validation import resolve_validation_run
from nts_core.eval_harness import json_dumps, read_json, sha256_text, write_json
from nts_core.learning_loop import KNOWN_LEARNING_PATTERNS
from nts_core.projects import get_project_by_slug
from nts_storage.database import connection, json_loads, row_to_dict, utc_now
from nts_storage.workspace import Workspace


APPROVED_MEMORY_IDS_MVP5C = [
    "memory_5190e5ee3320419992bc8833ffd45fcc",
    "memory_ee0e5afb1b8f4180b9d7b1907de1385c",
    "memory_9ae91c19082341ae85626f5f74e2cf3f",
    "memory_bc32c4066a624090918af5e0f89ddda7",
    "memory_160c0cae68964045bdc25b691f469bc4",
]

JSON_FIELDS = ("scope_json", "value_json", "rules_json", "confidence_json")

ADDITIONAL_MINING_PATTERNS: list[dict[str, Any]] = [
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "青冥魔教",
        "preferred_target": "Thanh Minh ma giáo",
        "rejected_variants": ["Thanh Minh Ma Giáo", "Thanh Minh Ma giáo"],
        "reason": "canon_org_name_from_human_reference",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "玉幽峰",
        "preferred_target": "Ngọc U phong",
        "rejected_variants": ["Ngọc U Phong", "đỉnh Ngọc U"],
        "reason": "canon_peak_name_from_human_reference",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "曦璇仙子",
        "preferred_target": "Hi Tuyền tiên tử",
        "rejected_variants": ["Hi Tuyền Tiên Tử", "Tiên tử Hi Tuyền"],
        "reason": "canon_addressing_for_xi_xuan",
    },
    {
        "candidate_type": "term_memory",
        "memory_type": "term",
        "source_pattern": "雷灵池",
        "preferred_target": "Lôi Linh Trì",
        "rejected_variants": ["Lôi linh trì", "ao linh lôi"],
        "reason": "canon_place_term_from_reference",
    },
    {
        "candidate_type": "name_memory",
        "memory_type": "name",
        "source_pattern": "莫复仇",
        "preferred_target": "Mạc Phục Cừu",
        "rejected_variants": ["Mạc Phục Thù"],
        "reason": "canon_character_name_from_reference",
    },
    {
        "candidate_type": "formatting_rule_memory",
        "memory_type": "style",
        "source_pattern": "技能",
        "preferred_target": "skills",
        "rejected_variants": ["kỹ năng", "skill"],
        "reason": "human_reference_keeps_system_panel_skills_label",
    },
    {
        "candidate_type": "style_rule_memory",
        "memory_type": "style",
        "source_pattern": "短促动作节奏",
        "preferred_target": "Giữ các nhịp hành động ngắn thành câu ngắn, không gộp thành câu giải thích dài.",
        "rejected_variants": ["gộp nhiều nhịp hành động thành một câu giải thích"],
        "reason": "human_reference_preserves_punchy_webnovel_action_beats",
    },
]

MINING_PATTERNS: list[dict[str, Any]] = [*KNOWN_LEARNING_PATTERNS, *ADDITIONAL_MINING_PATTERNS]


def approved_memory_ablation_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "approved_memory_ablation"


def memory_candidate_mining_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "memory_candidate_mining"


def _new_run_dir(root: Path, project_slug: str, suffix: str) -> Path:
    run_dir = root / f"{project_slug}_{suffix}_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json_dumps(row) + "\n")


def _normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _contains(text: str | None, needle: str | None) -> bool:
    if not needle:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in needle):
        return needle in (text or "")
    return _normalize_text(needle) in _normalize_text(text)


def _safe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json_loads(value)
        except Exception:
            return {}
    return value if value is not None else {}


def _memory_item_to_dict(row) -> dict[str, Any]:
    return row_to_dict(row, json_fields=JSON_FIELDS)


def _memory_rows(workspace: Workspace, project_slug: str, *, statuses: set[str] | None = None) -> list[dict[str, Any]]:
    project = get_project_by_slug(workspace, project_slug)
    with connection(workspace.db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                   value_json, rules_json, confidence_score, confidence_json,
                   conflict_cluster_id, created_at, updated_at
            FROM memory_items
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _memory_item_to_dict(row)
        scope = item.get("scope_json") or {}
        if scope.get("project_slug") != project_slug and scope.get("project_id") != project["id"]:
            continue
        if statuses and item.get("status") not in statuses:
            continue
        items.append(item)
    return items


def _validation_summary(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "final_validation_summary.json"
    if path.exists():
        return read_json(path)
    state_path = run_dir / "validation_job_state.json"
    if state_path.exists():
        state = read_json(state_path)
        return {
            "validation_run_id": state.get("validation_run_id", run_dir.name),
            "project_slug": state.get("project_slug"),
            "chapters": state.get("chapters", []),
            "provider": state.get("provider"),
            "model": state.get("model"),
            "round_results": state.get("round_results", []),
            "approved_memory_ids": state.get("approved_memory_ids", []),
        }
    raise ValueError(f"Validation summary not found: {run_dir}")


def _approved_memory_from_run(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "approved_memory_used.json"
    if not path.exists():
        return []
    payload = read_json(path)
    return [item for item in payload.get("items", []) if isinstance(item, dict)]


def _round_results(run_dir: Path) -> list[dict[str, Any]]:
    summary = _validation_summary(run_dir)
    rows = [row for row in summary.get("round_results", []) if isinstance(row, dict)]
    if rows:
        return rows
    inferred: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("round_*/score_delta.json")):
        row = read_json(path)
        if "round" not in row:
            match = re.search(r"round_(\d+)", str(path.parent.name))
            row["round"] = int(match.group(1)) if match else len(inferred) + 1
        inferred.append(row)
    return inferred


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _sample_lookup(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "selected_samples.json"
    if not path.exists():
        path = run_dir / "selected_validation_units.json"
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        str(sample.get("sample_id")): sample
        for sample in payload.get("samples", [])
        if isinstance(sample, dict) and sample.get("sample_id")
    }


def _read_first_text(paths: list[Path]) -> str:
    for path in paths:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def _phase_output_text(run_dir: Path, round_no: int, phase: str, sample_id: str) -> str:
    root = run_dir / f"round_{round_no}" / f"{phase}_outputs" / sample_id
    if root.exists():
        text = _read_first_text(sorted(root.glob("*_final.txt")))
        if text:
            return text
        for suffix in ("*_structured_final.json", "*_structured_after_compression.json"):
            for path in sorted(root.glob(suffix)):
                payload = read_json(path)
                paragraphs = payload.get("paragraphs") if isinstance(payload, dict) else None
                if isinstance(paragraphs, list):
                    return "\n\n".join(
                        str(item.get("text") or "")
                        for item in paragraphs
                        if isinstance(item, dict)
                    ).strip()
    metadata_path = run_dir / f"round_{round_no}" / f"{phase}_outputs" / "translation_metadata.json"
    if metadata_path.exists():
        metadata = read_json(metadata_path)
        sample_models = (metadata.get("samples") or {}).get(sample_id, {})
        for model_payload in sample_models.values():
            if not isinstance(model_payload, dict):
                continue
            relative = model_payload.get("path")
            if relative:
                path = run_dir / f"round_{round_no}" / f"{phase}_outputs" / Path(relative).name
                if path.exists():
                    return path.read_text(encoding="utf-8")
    return ""


def _validation_evidence_rows(run_dir: Path) -> list[dict[str, Any]]:
    samples = _sample_lookup(run_dir)
    rows: list[dict[str, Any]] = []
    for round_row in _round_results(run_dir):
        round_no = int(round_row.get("round") or len(rows) + 1)
        deltas = round_row.get("sample_deltas") or round_row.get("per_chapter_deltas") or []
        for delta in deltas:
            if not isinstance(delta, dict):
                continue
            sample_id = str(delta.get("sample_id") or f"sample_{delta.get('chapter_id')}")
            sample = samples.get(sample_id, {})
            rows.append(
                {
                    "round": round_no,
                    "sample_id": sample_id,
                    "chapter_id": delta.get("chapter_id") or sample.get("chapter_id"),
                    "score_delta": float(delta.get("delta") or 0),
                    "baseline_score": delta.get("baseline_score"),
                    "memory_score": delta.get("memory_score"),
                    "baseline_ratio": delta.get("baseline_ratio"),
                    "memory_ratio": delta.get("memory_ratio"),
                    "source_text": sample.get("source_text", ""),
                    "human_reference": sample.get("target_text", ""),
                    "baseline_output": _phase_output_text(run_dir, round_no, "baseline", sample_id),
                    "memory_output": _phase_output_text(run_dir, round_no, "memory", sample_id),
                }
            )
    if rows:
        return rows
    for sample_id, sample in samples.items():
        rows.append(
            {
                "round": 1,
                "sample_id": sample_id,
                "chapter_id": sample.get("chapter_id"),
                "score_delta": 0.0,
                "source_text": sample.get("source_text", ""),
                "human_reference": sample.get("target_text", ""),
                "baseline_output": "",
                "memory_output": "",
            }
        )
    return rows


def _memory_source(memory: dict[str, Any]) -> str:
    value = memory.get("value_json") or {}
    return str(memory.get("source_key") or value.get("source_pattern") or "")


def _memory_target(memory: dict[str, Any]) -> str:
    value = memory.get("value_json") or {}
    rules = memory.get("rules_json") or {}
    return str(memory.get("target_text") or value.get("preferred_target") or rules.get("preferred_target") or "")


def _memory_forbidden(memory: dict[str, Any]) -> list[str]:
    value = memory.get("value_json") or {}
    rules = memory.get("rules_json") or {}
    variants = []
    for raw in (value.get("rejected_variant"), *(value.get("rejected_variants") or []), *(rules.get("forbidden_variants") or [])):
        if raw and str(raw) not in variants:
            variants.append(str(raw))
    return variants


def _candidate_type(memory: dict[str, Any]) -> str:
    value = memory.get("value_json") or {}
    candidate_type = value.get("candidate_type")
    if candidate_type:
        return str(candidate_type)
    memory_type = memory.get("memory_type")
    if memory_type == "term":
        return "term_memory"
    if memory_type == "name":
        return "name_memory"
    if memory_type == "pronoun":
        return "pronoun_memory"
    if memory_type == "style":
        return "formatting_rule_memory"
    return "correction_rule_memory"


def _candidate_group(memory: dict[str, Any]) -> str:
    candidate_type = _candidate_type(memory)
    memory_type = str(memory.get("memory_type") or "")
    if memory_type == "term":
        return "terms"
    if memory_type == "name":
        return "names"
    if "phrase_preference" in candidate_type:
        return "phrase_preferences"
    if "formatting" in candidate_type or memory_type == "style":
        return "formatting_system_panel"
    return "corrections"


def _memory_evidence(memory: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    source = _memory_source(memory)
    target = _memory_target(memory)
    forbidden = _memory_forbidden(memory)
    evidence: list[dict[str, Any]] = []
    exact_hits = 0
    forbidden_hits = 0
    delta_sum = 0.0
    chapter_ids: set[int] = set()
    for row in rows:
        source_hit = _contains(row.get("source_text"), source)
        reference_hit = _contains(row.get("human_reference"), target)
        output_hit = _contains(row.get("memory_output"), target)
        rejected_hit = any(
            _contains(row.get("baseline_output"), variant) or _contains(row.get("memory_output"), variant)
            for variant in forbidden
        )
        if not (source_hit or reference_hit or output_hit or rejected_hit):
            continue
        if reference_hit or output_hit:
            exact_hits += 1
        if rejected_hit:
            forbidden_hits += 1
        delta_sum += float(row.get("score_delta") or 0)
        if row.get("chapter_id") is not None:
            chapter_ids.add(int(row["chapter_id"]))
        evidence.append(
            {
                "round": row.get("round"),
                "sample_id": row.get("sample_id"),
                "chapter_id": row.get("chapter_id"),
                "score_delta": row.get("score_delta"),
                "source_hit": source_hit,
                "reference_hit": reference_hit,
                "memory_output_hit": output_hit,
                "forbidden_variant_hit": rejected_hit,
                "source_excerpt": str(row.get("source_text", ""))[:400],
                "ai_output_excerpt": str(row.get("memory_output") or row.get("baseline_output") or "")[:400],
                "human_reference_excerpt": str(row.get("human_reference", ""))[:400],
            }
        )
    count = len(evidence)
    return {
        "evidence_count": count,
        "chapter_spread": len(chapter_ids),
        "exact_hits": exact_hits,
        "forbidden_variant_count": forbidden_hits,
        "exact_canon_hit_rate": round(exact_hits / count, 3) if count else 0.0,
        "mean_observed_delta": round(delta_sum / count, 3) if count else 0.0,
        "evidence": evidence[:12],
    }


def _classify_impact(estimated_delta: float, evidence_count: int) -> str:
    if evidence_count <= 0:
        return "insufficient_evidence"
    if estimated_delta >= 1.0:
        return "strong_positive"
    if estimated_delta > 0.05:
        return "weak_positive"
    if estimated_delta < -0.05:
        return "harmful"
    return "neutral"


def _ablation_modes(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"mode": "all_current_approved_candidates", "items": memories},
    ]
    for memory in memories:
        rows.append({"mode": f"candidate:{memory['id']}", "items": [memory]})
    groups = {
        "terms_only": [item for item in memories if _candidate_group(item) == "terms"],
        "names_only": [item for item in memories if _candidate_group(item) == "names"],
        "phrase_preferences_only": [item for item in memories if _candidate_group(item) == "phrase_preferences"],
        "formatting_system_panel_only": [item for item in memories if _candidate_group(item) == "formatting_system_panel"],
    }
    for mode, items in groups.items():
        rows.append({"mode": mode, "items": items})
    for memory in memories:
        rows.append(
            {
                "mode": f"all_minus:{memory['id']}",
                "items": [item for item in memories if item["id"] != memory["id"]],
            }
        )
    return rows


def ablate_approved_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    validation_dir = resolve_validation_run(workspace, validation_run)
    if not (validation_dir / "final_validation_summary.json").exists():
        raise ValueError(f"Validation summary not found: {validation_dir}")

    run_dir = _new_run_dir(approved_memory_ablation_root(workspace), project_slug, "ablation")
    round_rows = _round_results(validation_dir)
    baseline_score = _average([float(row.get("baseline_score") or 0) for row in round_rows])
    memory_score = _average([float(row.get("memory_score") or 0) for row in round_rows])
    observed_delta = round(memory_score - baseline_score, 3)
    evidence_rows = _validation_evidence_rows(validation_dir)
    approved_memories = _approved_memory_from_run(validation_dir)
    if not approved_memories:
        approved_memories = [
            item
            for item in _memory_rows(workspace, project_slug, statuses={"active"})
            if item.get("layer") == "learning_candidate"
        ]

    impact_rows: list[dict[str, Any]] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    raw_weights: dict[str, float] = {}
    for memory in approved_memories:
        evidence = _memory_evidence(memory, evidence_rows)
        evidence_by_id[memory["id"]] = evidence
        weight = max(0.0, evidence["mean_observed_delta"]) * max(1, evidence["evidence_count"])
        if evidence["evidence_count"] and weight == 0:
            weight = 0.1
        raw_weights[memory["id"]] = weight
    total_weight = sum(raw_weights.values())
    for memory in approved_memories:
        evidence = evidence_by_id[memory["id"]]
        if total_weight > 0 and evidence["evidence_count"] > 0:
            estimated_delta = round(observed_delta * raw_weights[memory["id"]] / total_weight, 3)
        else:
            estimated_delta = 0.0
        impact_rows.append(
            {
                "memory_id": memory["id"],
                "memory_type": memory.get("memory_type"),
                "candidate_type": _candidate_type(memory),
                "group": _candidate_group(memory),
                "source_pattern": _memory_source(memory),
                "preferred_target": _memory_target(memory),
                "forbidden_variants": _memory_forbidden(memory),
                "estimated_delta": estimated_delta,
                "classification": _classify_impact(estimated_delta, evidence["evidence_count"]),
                **{key: value for key, value in evidence.items() if key != "evidence"},
                "evidence": evidence["evidence"],
            }
        )

    contribution_by_id = {row["memory_id"]: float(row["estimated_delta"]) for row in impact_rows}
    ablation_matrix: list[dict[str, Any]] = []
    for mode in _ablation_modes(approved_memories):
        items = mode["items"]
        ids = [item["id"] for item in items]
        if mode["mode"] == "all_current_approved_candidates":
            estimated_delta = observed_delta
        else:
            estimated_delta = round(sum(contribution_by_id.get(memory_id, 0.0) for memory_id in ids), 3)
        sample_deltas = [
            row.get("delta", 0)
            for round_row in round_rows
            for row in (round_row.get("sample_deltas") or round_row.get("per_chapter_deltas") or [])
            if isinstance(row, dict)
        ]
        ablation_matrix.append(
            {
                "mode": mode["mode"],
                "memory_ids": ids,
                "memory_count": len(ids),
                "baseline_score": baseline_score,
                "estimated_average_score": round(baseline_score + estimated_delta, 3),
                "estimated_score_delta": estimated_delta,
                "observed_full_bundle_delta": observed_delta,
                "regression_count": sum(1 for value in sample_deltas if float(value or 0) < 0),
                "terminology_error_delta": sum(int(row.get("terminology_error_delta") or 0) for row in round_rows),
                "style_formatting_error_delta": round(
                    sum(float(row.get("style_drift_delta") or 0) + float(row.get("formatting_error_delta") or 0) for row in round_rows),
                    3,
                ),
                "forbidden_variant_count": sum(evidence_by_id.get(memory_id, {}).get("forbidden_variant_count", 0) for memory_id in ids),
                "exact_canon_hit_rate": _average(
                    [
                        float(evidence_by_id.get(memory_id, {}).get("exact_canon_hit_rate") or 0)
                        for memory_id in ids
                    ]
                ),
                "analysis_mode": "cached_no_api",
            }
        )

    per_chapter_rows: list[dict[str, Any]] = []
    for round_row in round_rows:
        for sample in round_row.get("sample_deltas") or round_row.get("per_chapter_deltas") or []:
            if isinstance(sample, dict):
                per_chapter_rows.append(
                    {
                        "round": round_row.get("round"),
                        "chapter_id": sample.get("chapter_id"),
                        "sample_id": sample.get("sample_id"),
                        "baseline_score": sample.get("baseline_score"),
                        "memory_score": sample.get("memory_score"),
                        "delta": sample.get("delta"),
                        "baseline_ratio": sample.get("baseline_ratio"),
                        "memory_ratio": sample.get("memory_ratio"),
                    }
                )

    manifest = {
        "schema_version": "approved_memory_ablation_manifest_v1",
        "ablation_run_id": run_dir.name,
        "project_id": project["id"],
        "project_slug": project_slug,
        "validation_run_dir": str(validation_dir),
        "approved_memory_ids": [item["id"] for item in approved_memories],
        "analysis_mode": "cached_no_api",
        "created_at": utc_now(),
    }
    write_json(run_dir / "ablation_manifest.json", manifest)
    write_json(
        run_dir / "ablation_matrix.json",
        {
            "schema_version": "approved_memory_ablation_matrix_v1",
            "baseline_score": baseline_score,
            "memory_score": memory_score,
            "observed_delta": observed_delta,
            "rows": ablation_matrix,
        },
    )
    write_json(
        run_dir / "candidate_impact_report.json",
        {
            "schema_version": "approved_memory_candidate_impact_v1",
            "candidates": impact_rows,
            "classification_counts": {
                label: sum(1 for row in impact_rows if row["classification"] == label)
                for label in ["strong_positive", "weak_positive", "neutral", "harmful", "insufficient_evidence"]
            },
        },
    )
    with (run_dir / "per_chapter_impact.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "round",
                "chapter_id",
                "sample_id",
                "baseline_score",
                "memory_score",
                "delta",
                "baseline_ratio",
                "memory_ratio",
            ],
        )
        writer.writeheader()
        writer.writerows(per_chapter_rows)
    _write_text(
        run_dir / "ablation_matrix.md",
        "# Approved Memory Ablation Matrix\n\n"
        + f"- Validation run: `{validation_dir.name}`\n"
        + f"- Analysis mode: `cached_no_api`\n"
        + f"- Observed full-bundle delta: `{observed_delta}`\n\n"
        + "| Mode | Memory count | Estimated delta | Estimated score |\n"
        + "| --- | ---: | ---: | ---: |\n"
        + "\n".join(
            f"| {row['mode']} | {row['memory_count']} | {row['estimated_score_delta']} | {row['estimated_average_score']} |"
            for row in ablation_matrix
        )
        + "\n",
    )
    _write_text(
        run_dir / "candidate_impact_report.md",
        "# Candidate Impact Report\n\n"
        + "| Memory | Type | Source | Target | Estimated delta | Classification | Evidence |\n"
        + "| --- | --- | --- | --- | ---: | --- | ---: |\n"
        + "\n".join(
            f"| {row['memory_id']} | {row['candidate_type']} | {row['source_pattern']} | {row['preferred_target']} | "
            f"{row['estimated_delta']} | {row['classification']} | {row['evidence_count']} |"
            for row in impact_rows
        )
        + "\n",
    )
    recommendations = []
    for row in impact_rows:
        if row["classification"] in {"strong_positive", "weak_positive"}:
            action = "KEEP_FOR_NOW"
        elif row["classification"] == "harmful":
            action = "REVIEW_FOR_DEACTIVATION"
        else:
            action = "NEEDS_MORE_EVIDENCE"
        recommendations.append(f"- `{row['memory_id']}`: `{action}` ({row['classification']}).")
    _write_text(
        run_dir / "recommended_keep_drop_review.md",
        "# Recommended Keep/Drop Review\n\n"
        + "\n".join(recommendations)
        + "\n\nNo active memory was changed by this command.\n",
    )
    return {
        "ablation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "analysis_mode": "cached_no_api",
        "approved_memory_count": len(approved_memories),
        "observed_delta": observed_delta,
        "classification_counts": {
            label: sum(1 for row in impact_rows if row["classification"] == label)
            for label in ["strong_positive", "weak_positive", "neutral", "harmful", "insufficient_evidence"]
        },
        "report_paths": {
            "ablation_matrix": str(run_dir / "ablation_matrix.json"),
            "candidate_impact": str(run_dir / "candidate_impact_report.json"),
            "review": str(run_dir / "recommended_keep_drop_review.md"),
        },
    }


def _candidate_id(pattern: dict[str, Any], evidence: dict[str, Any]) -> str:
    basis = f"{pattern['memory_type']}|{pattern['source_pattern']}|{pattern['preferred_target']}|{evidence['evidence_count']}"
    digest = sha256_text(basis).replace("sha256:", "")
    return "candidate_" + digest[:24]


def _existing_memory_index(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = _memory_source(item)
        if key:
            index.setdefault(key, []).append(item)
    return index


def _pattern_evidence(pattern: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    source = str(pattern["source_pattern"])
    target = str(pattern["preferred_target"])
    rejected = [str(item) for item in pattern.get("rejected_variants", []) if item]
    evidence: list[dict[str, Any]] = []
    chapters: set[int] = set()
    rejected_hits = 0
    for row in rows:
        source_hit = _contains(row.get("source_text"), source)
        reference_hit = _contains(row.get("human_reference"), target)
        ai_text = row.get("baseline_output") or row.get("memory_output") or ""
        ai_rejected = any(_contains(ai_text, variant) for variant in rejected)
        ai_missing_preferred = reference_hit and target and not _contains(ai_text, target)
        if not (source_hit or reference_hit or ai_rejected or ai_missing_preferred):
            continue
        if row.get("chapter_id") is not None:
            chapters.add(int(row["chapter_id"]))
        if ai_rejected:
            rejected_hits += 1
        evidence.append(
            {
                "round": row.get("round"),
                "chapter_id": row.get("chapter_id"),
                "sample_id": row.get("sample_id"),
                "source_excerpt": str(row.get("source_text", ""))[:500],
                "ai_output_excerpt": str(ai_text)[:500],
                "human_reference_excerpt": str(row.get("human_reference", ""))[:500],
                "source_hit": source_hit,
                "human_preferred_hit": reference_hit,
                "ai_rejected_variant_hit": ai_rejected,
                "ai_missing_preferred": ai_missing_preferred,
            }
        )
    confidence = min(0.92, 0.45 + 0.1 * len(evidence) + 0.05 * len(chapters) + (0.05 if rejected_hits else 0))
    return {
        "evidence_count": len(evidence),
        "chapter_spread": len(chapters),
        "rejected_variant_hits": rejected_hits,
        "confidence": round(confidence, 3) if evidence else 0.0,
        "evidence": evidence[:20],
    }


def mine_memory_candidates(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
) -> dict[str, Any]:
    project = get_project_by_slug(workspace, project_slug)
    validation_dir = resolve_validation_run(workspace, validation_run)
    if not (validation_dir / "final_validation_summary.json").exists():
        raise ValueError(f"Validation summary not found: {validation_dir}")
    rows = _validation_evidence_rows(validation_dir)
    active_and_pending = _memory_rows(workspace, project_slug, statuses={"active", "pending"})
    existing_index = _existing_memory_index(active_and_pending)
    run_dir = _new_run_dir(memory_candidate_mining_root(workspace), project_slug, "mining")

    candidates: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen_candidate_keys: set[tuple[str, str, str]] = set()

    for pattern in MINING_PATTERNS:
        evidence = _pattern_evidence(pattern, rows)
        if evidence["evidence_count"] <= 0:
            continue
        source = str(pattern["source_pattern"])
        target = str(pattern["preferred_target"])
        existing_rows = existing_index.get(source, [])
        exact_duplicate = [
            item for item in existing_rows if _normalize_text(_memory_target(item)) == _normalize_text(target)
        ]
        if exact_duplicate:
            duplicates.append(
                {
                    "source_pattern": source,
                    "preferred_target": target,
                    "duplicate_memory_ids": [item["id"] for item in exact_duplicate],
                    "merged_evidence_count": evidence["evidence_count"],
                    "chapter_spread": evidence["chapter_spread"],
                    "reason": "duplicate_active_or_pending_memory",
                }
            )
            continue
        conflict_rows = [
            item for item in existing_rows if _normalize_text(_memory_target(item)) != _normalize_text(target)
        ]
        candidate_key = (str(pattern["memory_type"]), source, target)
        if candidate_key in seen_candidate_keys:
            duplicates.append(
                {
                    "source_pattern": source,
                    "preferred_target": target,
                    "duplicate_memory_ids": [],
                    "merged_evidence_count": evidence["evidence_count"],
                    "reason": "duplicate_mined_candidate",
                }
            )
            continue
        seen_candidate_keys.add(candidate_key)
        review_status = "needs_human_review" if conflict_rows else (
            "high_confidence_approve_candidate"
            if evidence["confidence"] >= 0.75 and evidence["evidence_count"] >= 2
            else "needs_human_review"
        )
        candidate = {
            "candidate_id": _candidate_id(pattern, evidence),
            "memory_type": pattern["memory_type"],
            "candidate_type": pattern["candidate_type"],
            "source_pattern": source,
            "preferred_target": target,
            "rejected_variant": (pattern.get("rejected_variants") or [None])[0],
            "rejected_variants": pattern.get("rejected_variants", []),
            "scope": {
                "project_id": project["id"],
                "project_slug": project_slug,
                "domain": project.get("domain"),
                "language_pair": f"{project.get('source_lang')}-{project.get('target_lang')}",
            },
            "confidence": evidence["confidence"],
            "evidence_count": evidence["evidence_count"],
            "chapter_spread": evidence["chapter_spread"],
            "source_excerpts": [item["source_excerpt"] for item in evidence["evidence"][:3]],
            "ai_output_excerpts": [item["ai_output_excerpt"] for item in evidence["evidence"][:3]],
            "human_reference_excerpts": [item["human_reference_excerpt"] for item in evidence["evidence"][:3]],
            "evidence": evidence["evidence"],
            "reason": pattern["reason"],
            "status": "pending_review",
            "review_status": review_status,
            "expected_impact": "reduce_repeated_ai_reference_mismatch",
            "related_existing_memory_id": conflict_rows[0]["id"] if conflict_rows else None,
        }
        if conflict_rows:
            conflicts.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "source_pattern": source,
                    "candidate_preferred_target": target,
                    "conflicting_memory_ids": [item["id"] for item in conflict_rows],
                    "conflicting_targets": [_memory_target(item) for item in conflict_rows],
                    "status": "needs_human_review",
                }
            )
        candidates.append(candidate)

    _write_jsonl(run_dir / "mined_memory_candidates.jsonl", candidates)
    write_json(
        run_dir / "candidate_conflicts.json",
        {
            "schema_version": "memory_candidate_conflicts_v1",
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
        },
    )
    write_json(
        run_dir / "candidate_dedup_report.json",
        {
            "schema_version": "memory_candidate_dedup_report_v1",
            "duplicate_or_merged_count": len(duplicates),
            "duplicates": duplicates,
        },
    )
    with (run_dir / "memory_candidate_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "memory_type",
                "source_pattern",
                "preferred_target",
                "rejected_variant",
                "confidence",
                "evidence_count",
                "chapter_spread",
                "status",
                "review_status",
                "related_existing_memory_id",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    key: candidate.get(key)
                    for key in [
                        "candidate_id",
                        "memory_type",
                        "source_pattern",
                        "preferred_target",
                        "rejected_variant",
                        "confidence",
                        "evidence_count",
                        "chapter_spread",
                        "status",
                        "review_status",
                        "related_existing_memory_id",
                    ]
                }
            )
    _write_text(
        run_dir / "mined_memory_candidates.md",
        "# Mined Memory Candidates\n\n"
        + f"- Validation run: `{validation_dir.name}`\n"
        + f"- Candidate count: `{len(candidates)}`\n"
        + f"- Conflict count: `{len(conflicts)}`\n"
        + f"- Duplicate/merged evidence count: `{len(duplicates)}`\n\n"
        + "| Candidate | Type | Source | Preferred | Confidence | Evidence | Review |\n"
        + "| --- | --- | --- | --- | ---: | ---: | --- |\n"
        + "\n".join(
            f"| {candidate['candidate_id']} | {candidate['candidate_type']} | {candidate['source_pattern']} | "
            f"{candidate['preferred_target']} | {candidate['confidence']} | {candidate['evidence_count']} | "
            f"{candidate['review_status']} |"
            for candidate in candidates
        )
        + "\n",
    )
    evidence_lines = ["# Evidence Pack", ""]
    for candidate in candidates:
        evidence_lines.extend(
            [
                f"## {candidate['candidate_id']} - {candidate['source_pattern']} -> {candidate['preferred_target']}",
                "",
                f"- Confidence: `{candidate['confidence']}`",
                f"- Evidence count: `{candidate['evidence_count']}`",
                f"- Review status: `{candidate['review_status']}`",
                "",
            ]
        )
        for item in candidate["evidence"][:5]:
            evidence_lines.extend(
                [
                    f"### Chapter {item.get('chapter_id')} {item.get('sample_id')}",
                    "",
                    "Source:",
                    "",
                    item.get("source_excerpt", ""),
                    "",
                    "AI output:",
                    "",
                    item.get("ai_output_excerpt", ""),
                    "",
                    "Human reference:",
                    "",
                    item.get("human_reference_excerpt", ""),
                    "",
                ]
            )
    _write_text(run_dir / "evidence_pack.md", "\n".join(evidence_lines) + "\n")

    review_dir = run_dir / "human_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    high_confidence = [
        candidate for candidate in candidates if candidate["review_status"] == "high_confidence_approve_candidate"
    ]
    likely_reject = [candidate for candidate in candidates if candidate["confidence"] < 0.55]
    needs_review = [
        candidate
        for candidate in candidates
        if candidate not in high_confidence and candidate not in likely_reject
    ]
    _write_text(
        review_dir / "human_review_summary.md",
        "# Memory Candidate Human Review\n\n"
        + f"- High confidence approve candidates: `{len(high_confidence)}`\n"
        + f"- Needs review: `{len(needs_review)}`\n"
        + f"- Likely reject: `{len(likely_reject)}`\n"
        + f"- Duplicate/merged evidence: `{len(duplicates)}`\n"
        + f"- Conflicts: `{len(conflicts)}`\n\n"
        + "No candidate has been activated. Use explicit approval commands after review.\n",
    )
    with (review_dir / "candidate_review_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "candidate_id",
                "memory_type",
                "source_pattern",
                "preferred_target",
                "confidence",
                "evidence_count",
                "review_status",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "candidate_id": candidate["candidate_id"],
                    "memory_type": candidate["memory_type"],
                    "source_pattern": candidate["source_pattern"],
                    "preferred_target": candidate["preferred_target"],
                    "confidence": candidate["confidence"],
                    "evidence_count": candidate["evidence_count"],
                    "review_status": candidate["review_status"],
                }
            )
    approve_ids = ",".join(candidate["candidate_id"] for candidate in high_confidence) or "<candidate_ids>"
    reject_ids = ",".join(candidate["candidate_id"] for candidate in likely_reject) or "<candidate_ids>"
    _write_text(
        review_dir / "approve_commands.md",
        "# Approve Commands\n\n"
        f"`nts learn approve-memory --project {project_slug} --run {run_dir} --candidate-ids {approve_ids} --json`\n",
    )
    _write_text(
        review_dir / "reject_commands.md",
        "# Reject Commands\n\n"
        f"`nts learn reject-memory --project {project_slug} --run {run_dir} --candidate-ids {reject_ids} --reason \"not approved\" --json`\n",
    )
    _write_text(review_dir / "evidence_pack.md", (run_dir / "evidence_pack.md").read_text(encoding="utf-8"))

    type_counts = {
        memory_type: sum(1 for candidate in candidates if candidate["memory_type"] == memory_type)
        for memory_type in sorted({candidate["memory_type"] for candidate in candidates})
    }
    return {
        "mining_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "candidate_count": len(candidates),
        "candidate_counts_by_type": type_counts,
        "high_confidence_candidate_count": len(high_confidence),
        "conflict_count": len(conflicts),
        "duplicate_merged_count": len(duplicates),
        "status": "pending_review",
        "report_paths": {
            "jsonl": str(run_dir / "mined_memory_candidates.jsonl"),
            "markdown": str(run_dir / "mined_memory_candidates.md"),
            "review": str(review_dir / "human_review_summary.md"),
            "evidence": str(run_dir / "evidence_pack.md"),
        },
    }


def _read_candidates_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        path = path / "mined_memory_candidates.jsonl"
    if not path.exists():
        raise ValueError(f"Mined candidate JSONL not found: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def simulate_memory_bundle(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    candidate_run: str,
) -> dict[str, Any]:
    validation_dir = resolve_validation_run(workspace, validation_run)
    candidate_path = Path(candidate_run)
    if not candidate_path.exists():
        candidate_path = memory_candidate_mining_root(workspace) / candidate_run
    candidates = _read_candidates_jsonl(candidate_path)
    active_memory = _memory_rows(workspace, project_slug, statuses={"active"})
    selected_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("status") == "pending_review"
        and candidate.get("review_status") == "high_confidence_approve_candidate"
        and not candidate.get("related_existing_memory_id")
    ]
    round_rows = _round_results(validation_dir)
    observed_delta = _average([float(row.get("score_delta") or 0) for row in round_rows])
    predicted_candidate_lift = round(
        sum(float(candidate.get("confidence") or 0) * min(0.35, 0.05 * int(candidate.get("evidence_count") or 0)) for candidate in selected_candidates),
        3,
    )
    run_dir = Path(candidate_path if candidate_path.is_dir() else candidate_path.parent) / f"simulation_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": "simulated_learning_memory_bundle_v1",
        "project_slug": project_slug,
        "validation_run_dir": str(validation_dir),
        "approved_memory_count": len(active_memory),
        "pending_candidate_count": len(selected_candidates),
        "active_memory_ids": [item["id"] for item in active_memory],
        "pending_candidate_ids": [candidate["candidate_id"] for candidate in selected_candidates],
        "checksum": sha256_text(
            json_dumps(
                {
                    "active_memory_ids": [item["id"] for item in active_memory],
                    "pending_candidate_ids": [candidate["candidate_id"] for candidate in selected_candidates],
                }
            )
        ),
        "created_at": utc_now(),
    }
    write_json(run_dir / "simulated_bundle.json", bundle)
    score_delta = {
        "schema_version": "simulated_memory_bundle_score_delta_v1",
        "analysis_mode": "cached_no_api",
        "observed_existing_memory_delta": observed_delta,
        "predicted_candidate_lift": predicted_candidate_lift,
        "predicted_total_delta": round(observed_delta + predicted_candidate_lift, 3),
        "candidate_count": len(selected_candidates),
        "memory_activation_performed": False,
    }
    write_json(run_dir / "simulated_score_delta.json", score_delta)
    _write_text(
        run_dir / "simulated_bundle_report.md",
        "# Simulated Memory Bundle\n\n"
        + f"- Analysis mode: `cached_no_api`\n"
        + f"- Active approved memory count: `{len(active_memory)}`\n"
        + f"- Pending high-confidence candidates included: `{len(selected_candidates)}`\n"
        + f"- Predicted total delta: `{score_delta['predicted_total_delta']}`\n\n"
        + "No memory was activated by this simulation.\n",
    )
    _write_text(
        run_dir / "recommended_approval_set.md",
        "# Recommended Approval Set\n\n"
        + "\n".join(f"- `{candidate['candidate_id']}`: {candidate['source_pattern']} -> {candidate['preferred_target']}" for candidate in selected_candidates)
        + ("\n" if selected_candidates else "No high-confidence non-conflicting candidates found.\n"),
    )
    return {
        "simulation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "candidate_run_dir": str(candidate_path if candidate_path.is_dir() else candidate_path.parent),
        "analysis_mode": "cached_no_api",
        "pending_candidate_count": len(selected_candidates),
        "predicted_total_delta": score_delta["predicted_total_delta"],
        "memory_activation_performed": False,
        "report_paths": {
            "bundle": str(run_dir / "simulated_bundle.json"),
            "delta": str(run_dir / "simulated_score_delta.json"),
            "report": str(run_dir / "simulated_bundle_report.md"),
            "approval_set": str(run_dir / "recommended_approval_set.md"),
        },
    }
