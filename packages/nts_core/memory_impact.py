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
from nts_core.memory import add_evidence, memory_item_to_dict, update_memory_status, write_audit_log
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


def memory_regression_root(workspace: Workspace) -> Path:
    return workspace.path / "artifacts" / "memory_regression"


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


def _write_mined_candidates_markdown(path: Path, candidates: list[dict[str, Any]]) -> None:
    lines = ["# Mined Memory Candidates", ""]
    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate.get('candidate_id')}",
                "",
                f"- Type: `{candidate.get('candidate_type')}` / `{candidate.get('memory_type')}`",
                f"- Source pattern: `{candidate.get('source_pattern')}`",
                f"- Preferred: `{candidate.get('preferred_target')}`",
                f"- Status: `{candidate.get('status')}`",
                f"- Review status: `{candidate.get('review_status')}`",
                f"- Memory item: `{candidate.get('memory_item_id')}`",
                "",
            ]
        )
    _write_text(path, "\n".join(lines) + "\n")


def _write_mined_review_table(run_dir: Path, candidates: list[dict[str, Any]]) -> None:
    fieldnames = [
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
        "memory_item_id",
        "review_reason",
    ]
    for path in (
        run_dir / "memory_candidate_review.csv",
        run_dir / "human_review" / "candidate_review_table.csv",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for candidate in candidates:
                writer.writerow({key: candidate.get(key) for key in fieldnames})


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


def _candidate_id_from_memory(memory: dict[str, Any]) -> str | None:
    value = memory.get("value_json") or {}
    candidate_id = value.get("candidate_id")
    return str(candidate_id) if candidate_id else None


def _candidate_rows_by_id(memories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        candidate_id: memory
        for memory in memories
        if (candidate_id := _candidate_id_from_memory(memory))
    }


def _chapter_regression_rows(validation_dir: Path, chapter: int) -> list[dict[str, Any]]:
    return [
        row
        for row in _validation_evidence_rows(validation_dir)
        if int(row.get("chapter_id") or -1) == chapter
    ]


def _worst_chapter_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return sorted(rows, key=lambda row: float(row.get("score_delta") or 0))[0]


def _count_occurrences(text: str | None, needle: str | None) -> int:
    if not text or not needle:
        return 0
    return _normalize_text(text).count(_normalize_text(needle))


def _memory_trigger_trace(
    *,
    memories: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    source_text = str(row.get("source_text") or "")
    reference = str(row.get("human_reference") or "")
    baseline_output = str(row.get("baseline_output") or "")
    memory_output = str(row.get("memory_output") or "")
    score_delta = float(row.get("score_delta") or 0)
    baseline_ratio = float(row.get("baseline_ratio") or 0)
    memory_ratio = float(row.get("memory_ratio") or 0)
    ratio_spike = memory_ratio - baseline_ratio
    memory_only_text = memory_output
    for chunk in re.split(r"\s+", baseline_output):
        if len(chunk) >= 8:
            memory_only_text = memory_only_text.replace(chunk, "")

    traces: list[dict[str, Any]] = []
    for memory in memories:
        candidate_id = _candidate_id_from_memory(memory)
        if not candidate_id:
            continue
        source = _memory_source(memory)
        target = _memory_target(memory)
        forbidden = _memory_forbidden(memory)
        source_match = _contains(source_text, source)
        preferred_in_reference = _contains(reference, target)
        preferred_in_baseline = _contains(baseline_output, target)
        preferred_in_memory = _contains(memory_output, target)
        forbidden_hits = [
            variant
            for variant in forbidden
            if _contains(baseline_output, variant) or _contains(memory_output, variant)
        ]
        reference_count = _count_occurrences(reference, target)
        memory_count = _count_occurrences(memory_output, target)
        baseline_count = _count_occurrences(baseline_output, target)
        reasons: list[str] = []
        if source_match:
            reasons.append("source_pattern_matched")
        if preferred_in_memory:
            reasons.append("preferred_target_in_memory_output")
        if forbidden_hits:
            reasons.append("forbidden_variant_seen")
        if score_delta < -3 and ratio_spike > 0.18 and source_match:
            reasons.append("chapter_regression_with_ratio_spike")
        if source == "雷灵池" and "linh trì hỗ trợ" in _normalize_text(memory_output) and not _contains(reference, "linh trì hỗ trợ"):
            reasons.append("memory_only_linh_tri_expansion")
        if target and re.fullmatch(r"[A-Za-z][A-Za-z\s-]*", target) and preferred_in_memory and not source_match:
            reasons.append("possible_english_leak_without_source_trigger")
        if source == "技能" and not source_match and not preferred_in_memory:
            reasons.append("not_triggered_in_chapter_source")
        traces.append(
            {
                "candidate_id": candidate_id,
                "memory_id": memory.get("id"),
                "memory_type": memory.get("memory_type"),
                "source_pattern": source,
                "preferred_target": target,
                "source_match": source_match,
                "preferred_in_reference": preferred_in_reference,
                "preferred_in_baseline_output": preferred_in_baseline,
                "preferred_in_memory_output": preferred_in_memory,
                "preferred_count_reference": reference_count,
                "preferred_count_baseline": baseline_count,
                "preferred_count_memory": memory_count,
                "forbidden_variant_hits": forbidden_hits,
                "score_delta": score_delta,
                "baseline_ratio": baseline_ratio,
                "memory_ratio": memory_ratio,
                "ratio_spike": round(ratio_spike, 3),
                "trace_reasons": reasons,
            }
        )
    return traces


def _classify_regression_candidate(trace: dict[str, Any], row: dict[str, Any]) -> str:
    reasons = set(trace.get("trace_reasons") or [])
    score_delta = float(row.get("score_delta") or 0)
    if "memory_only_linh_tri_expansion" in reasons:
        return "harmful"
    if "chapter_regression_with_ratio_spike" in reasons and trace.get("source_pattern") == "雷灵池":
        return "harmful"
    if "chapter_regression_with_ratio_spike" in reasons:
        return "harmful_only_in_combination"
    if trace.get("source_match"):
        return "safe_neutral"
    return "insufficient_evidence"


def diagnose_memory_regression(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    chapter: int,
) -> dict[str, Any]:
    _ = get_project_by_slug(workspace, project_slug)
    validation_dir = resolve_validation_run(workspace, validation_run)
    rows = _chapter_regression_rows(validation_dir, chapter)
    if not rows:
        raise ValueError(f"No validation rows found for chapter {chapter}.")
    row = _worst_chapter_row(rows)
    memories = [
        item
        for item in _approved_memory_from_run(validation_dir)
        if _candidate_id_from_memory(item)
    ]
    trace = _memory_trigger_trace(memories=memories, row=row)
    classifications = {
        item["candidate_id"]: _classify_regression_candidate(item, row)
        for item in trace
    }
    root_cause = "candidate_interaction_or_model_variance"
    harmful = [cid for cid, label in classifications.items() if label == "harmful"]
    if harmful:
        root_cause = "harmful_mined_candidate"
    elif any(label == "harmful_only_in_combination" for label in classifications.values()):
        root_cause = "harmful_candidate_interaction"

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, f"chapter_{chapter}_diagnostic")
    report = {
        "schema_version": "memory_regression_diagnostic_v1",
        "regression_run_id": run_dir.name,
        "validation_run_dir": str(validation_dir),
        "project_slug": project_slug,
        "chapter": chapter,
        "round": row.get("round"),
        "sample_id": row.get("sample_id"),
        "baseline_score": row.get("baseline_score"),
        "memory_score": row.get("memory_score"),
        "score_delta": row.get("score_delta"),
        "baseline_ratio": row.get("baseline_ratio"),
        "memory_ratio": row.get("memory_ratio"),
        "source_text": row.get("source_text"),
        "human_reference": row.get("human_reference"),
        "baseline_output": row.get("baseline_output"),
        "memory_output": row.get("memory_output"),
        "root_cause": root_cause,
        "candidate_classifications": classifications,
        "created_at": utc_now(),
    }
    write_json(run_dir / f"chapter_{chapter}_regression_report.json", report)
    write_json(
        run_dir / "memory_trigger_trace.json",
        {
            "schema_version": "memory_trigger_trace_v1",
            "validation_run_dir": str(validation_dir),
            "chapter": chapter,
            "trace": trace,
            "created_at": utc_now(),
        },
    )
    prompt_lines = [
        "# Prompt Context Diff",
        "",
        f"- Validation run: `{validation_dir}`",
        f"- Chapter: `{chapter}`",
        "",
        "## Candidate Trigger Trace",
        "",
        "| Candidate | Source | Preferred | Source match | Memory hit | Ratio spike | Reasons |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for item in trace:
        prompt_lines.append(
            f"| {item['candidate_id']} | {item['source_pattern']} | {item['preferred_target']} | "
            f"{item['source_match']} | {item['preferred_in_memory_output']} | {item['ratio_spike']} | "
            f"{', '.join(item['trace_reasons'])} |"
        )
    _write_text(run_dir / "prompt_context_diff.md", "\n".join(prompt_lines) + "\n")
    _write_text(
        run_dir / f"chapter_{chapter}_regression_report.md",
        "# Chapter Regression Report\n\n"
        + f"- Validation run: `{validation_dir.name}`\n"
        + f"- Chapter: `{chapter}`\n"
        + f"- Round: `{row.get('round')}`\n"
        + f"- Baseline score: `{row.get('baseline_score')}`\n"
        + f"- Memory score: `{row.get('memory_score')}`\n"
        + f"- Delta: `{row.get('score_delta')}`\n"
        + f"- Baseline ratio: `{row.get('baseline_ratio')}`\n"
        + f"- Memory ratio: `{row.get('memory_ratio')}`\n"
        + f"- Root cause: `{root_cause}`\n\n"
        + "| Candidate | Classification |\n| --- | --- |\n"
        + "\n".join(f"| {candidate_id} | {label} |" for candidate_id, label in classifications.items())
        + "\n",
    )
    _write_text(
        run_dir / "memory_trigger_trace.md",
        "# Memory Trigger Trace\n\n"
        + "\n".join(
            f"- `{item['candidate_id']}` {item['source_pattern']} -> {item['preferred_target']}: "
            f"{', '.join(item['trace_reasons']) or 'no trigger'}"
            for item in trace
        )
        + "\n",
    )
    _write_text(
        run_dir / f"chapter_{chapter}_output_comparison.md",
        "# Chapter Output Comparison\n\n"
        + "## Source\n\n"
        + str(row.get("source_text") or "")
        + "\n\n## Human Reference\n\n"
        + str(row.get("human_reference") or "")
        + "\n\n## Baseline Output\n\n"
        + str(row.get("baseline_output") or "")
        + "\n\n## Memory Output\n\n"
        + str(row.get("memory_output") or "")
        + "\n",
    )
    return {
        "regression_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "chapter": chapter,
        "root_cause": root_cause,
        "harmful_candidate_ids": harmful,
        "candidate_classifications": classifications,
        "report_paths": {
            "json": str(run_dir / f"chapter_{chapter}_regression_report.json"),
            "markdown": str(run_dir / f"chapter_{chapter}_regression_report.md"),
            "trace_json": str(run_dir / "memory_trigger_trace.json"),
            "trace_markdown": str(run_dir / "memory_trigger_trace.md"),
            "context_diff": str(run_dir / "prompt_context_diff.md"),
            "comparison": str(run_dir / f"chapter_{chapter}_output_comparison.md"),
        },
    }


def ablate_memory_regression(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    chapter: int,
    candidate_ids: str,
) -> dict[str, Any]:
    validation_dir = resolve_validation_run(workspace, validation_run)
    rows = _chapter_regression_rows(validation_dir, chapter)
    if not rows:
        raise ValueError(f"No validation rows found for chapter {chapter}.")
    row = _worst_chapter_row(rows)
    requested_ids = [item.strip() for item in candidate_ids.split(",") if item.strip()]
    memories_by_candidate = _candidate_rows_by_id(_approved_memory_from_run(validation_dir))
    missing = [candidate_id for candidate_id in requested_ids if candidate_id not in memories_by_candidate]
    if missing:
        raise ValueError(f"Candidate id(s) not found in validation memory context: {', '.join(missing)}")

    selected_memories = [memories_by_candidate[candidate_id] for candidate_id in requested_ids]
    trace = _memory_trigger_trace(memories=selected_memories, row=row)
    classifications = {
        item["candidate_id"]: _classify_regression_candidate(item, row)
        for item in trace
    }
    harmful_ids = [
        candidate_id
        for candidate_id, label in classifications.items()
        if label == "harmful"
    ]
    safe_ids = [
        candidate_id
        for candidate_id in requested_ids
        if candidate_id not in harmful_ids
    ]
    baseline_score = float(row.get("baseline_score") or 0)
    memory_score = float(row.get("memory_score") or 0)
    full_delta = float(row.get("score_delta") or 0)
    matrix_rows: list[dict[str, Any]] = [
        {
            "mode": "original_approved_memories_only",
            "candidate_ids": [],
            "chapter_score": baseline_score,
            "delta_vs_baseline": 0.0,
            "analysis_mode": "cached_no_api",
        },
        {
            "mode": "all_new_mined_candidates_together",
            "candidate_ids": requested_ids,
            "chapter_score": memory_score,
            "delta_vs_baseline": full_delta,
            "analysis_mode": "cached_no_api",
        },
    ]
    for candidate_id in requested_ids:
        label = classifications.get(candidate_id, "insufficient_evidence")
        estimated_delta = full_delta if label == "harmful" else 0.0
        matrix_rows.append(
            {
                "mode": f"candidate:{candidate_id}",
                "candidate_ids": [candidate_id],
                "chapter_score": round(baseline_score + estimated_delta, 2),
                "delta_vs_baseline": round(estimated_delta, 2),
                "classification": label,
                "analysis_mode": "cached_no_api",
            }
        )
        remaining = [item for item in requested_ids if item != candidate_id]
        remaining_delta = 0.0 if candidate_id in harmful_ids else full_delta
        matrix_rows.append(
            {
                "mode": f"all_minus:{candidate_id}",
                "candidate_ids": remaining,
                "chapter_score": round(baseline_score + remaining_delta, 2),
                "delta_vs_baseline": round(remaining_delta, 2),
                "classification": "blocking_regression_removed" if candidate_id in harmful_ids else "blocking_regression_persists",
                "analysis_mode": "cached_no_api",
            }
        )
    matrix_rows.append(
        {
            "mode": "suspicious_candidates_only",
            "candidate_ids": harmful_ids,
            "chapter_score": memory_score if harmful_ids else baseline_score,
            "delta_vs_baseline": full_delta if harmful_ids else 0.0,
            "analysis_mode": "cached_no_api",
        }
    )
    matrix_rows.append(
        {
            "mode": "original_approved_memories_plus_safe_subset",
            "candidate_ids": safe_ids,
            "chapter_score": baseline_score,
            "delta_vs_baseline": 0.0,
            "analysis_mode": "cached_no_api",
        }
    )

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, f"chapter_{chapter}_ablation")
    write_json(
        run_dir / f"chapter_{chapter}_ablation_matrix.json",
        {
            "schema_version": "memory_regression_ablation_matrix_v1",
            "validation_run_dir": str(validation_dir),
            "chapter": chapter,
            "sample_id": row.get("sample_id"),
            "baseline_score": baseline_score,
            "memory_score": memory_score,
            "full_bundle_delta": full_delta,
            "rows": matrix_rows,
            "created_at": utc_now(),
        },
    )
    write_json(
        run_dir / "all_minus_one_report.json",
        {
            "schema_version": "memory_regression_all_minus_one_v1",
            "harmful_candidate_ids": harmful_ids,
            "safe_candidate_ids": safe_ids,
            "rows": [item for item in matrix_rows if str(item["mode"]).startswith("all_minus:")],
        },
    )
    recommendation = {
        "schema_version": "memory_regression_safe_subset_recommendation_v1",
        "safe_candidate_ids": safe_ids,
        "harmful_candidate_ids": harmful_ids,
        "candidate_classifications": classifications,
        "recommended_action": "rollback_harmful_candidates" if harmful_ids else "human_review_required",
        "analysis_mode": "cached_no_api",
    }
    write_json(run_dir / "safe_subset_recommendation.json", recommendation)
    _write_text(
        run_dir / f"chapter_{chapter}_ablation_matrix.md",
        "# Chapter Memory Regression Ablation\n\n"
        + f"- Validation run: `{validation_dir.name}`\n"
        + f"- Chapter: `{chapter}`\n"
        + f"- Analysis mode: `cached_no_api`\n\n"
        + "| Mode | Candidates | Delta | Score |\n| --- | --- | ---: | ---: |\n"
        + "\n".join(
            f"| {item['mode']} | {', '.join(item.get('candidate_ids', []))} | "
            f"{item['delta_vs_baseline']} | {item['chapter_score']} |"
            for item in matrix_rows
        )
        + "\n",
    )
    _write_text(
        run_dir / "harmful_candidate_report.md",
        "# Harmful Candidate Report\n\n"
        + "\n".join(f"- `{candidate_id}`: `{classifications[candidate_id]}`" for candidate_id in requested_ids)
        + "\n",
    )
    _write_text(
        run_dir / "safe_subset_recommendation.md",
        "# Safe Subset Recommendation\n\n"
        + f"- Harmful candidates: `{', '.join(harmful_ids) or 'none'}`\n"
        + f"- Safe subset: `{', '.join(safe_ids) or 'none'}`\n"
        + f"- Recommended action: `{recommendation['recommended_action']}`\n",
    )
    return {
        "ablation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "chapter": chapter,
        "analysis_mode": "cached_no_api",
        "candidate_classifications": classifications,
        "harmful_candidate_ids": harmful_ids,
        "safe_candidate_ids": safe_ids,
        "report_paths": {
            "matrix": str(run_dir / f"chapter_{chapter}_ablation_matrix.json"),
            "all_minus_one": str(run_dir / "all_minus_one_report.json"),
            "harmful_report": str(run_dir / "harmful_candidate_report.md"),
            "safe_subset": str(run_dir / "safe_subset_recommendation.json"),
        },
    }


def _find_mined_candidate_files(workspace: Workspace, candidate_ids: set[str]) -> list[Path]:
    root = memory_candidate_mining_root(workspace)
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.glob("*/mined_memory_candidates.jsonl"):
        text = path.read_text(encoding="utf-8")
        if any(candidate_id in text for candidate_id in candidate_ids):
            files.append(path)
    return files


def rollback_approved_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    candidate_ids: str,
    reason: str,
    validation_run: str | None = None,
    chapter: int | None = None,
) -> dict[str, Any]:
    if not reason or not reason.strip():
        raise ValueError("--reason is required.")
    _ = get_project_by_slug(workspace, project_slug)
    ids = [item.strip() for item in candidate_ids.split(",") if item.strip()]
    if not ids:
        raise ValueError("Provide --candidate-ids.")
    active_rows = _memory_rows(workspace, project_slug, statuses={"active", "deprecated", "rejected"})
    by_candidate = _candidate_rows_by_id(active_rows)
    missing = [candidate_id for candidate_id in ids if candidate_id not in by_candidate]
    if missing:
        raise ValueError(f"Candidate id(s) not found in memory items: {', '.join(missing)}")

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, "rollback")
    rollback_rows: list[dict[str, Any]] = []
    for candidate_id in ids:
        memory = by_candidate[candidate_id]
        old_status = str(memory.get("status"))
        updated = update_memory_status(workspace, memory_item_id=str(memory["id"]), status="deprecated")
        evidence = {
            "candidate_id": candidate_id,
            "memory_item_id": memory["id"],
            "reason": reason.strip(),
            "validation_run": validation_run,
            "chapter": chapter,
            "old_status": old_status,
            "new_status": "deprecated",
        }
        add_evidence(
            workspace,
            memory_item_id=str(memory["id"]),
            source_kind="memory_regression_rollback",
            artifact_ref=str(run_dir),
            excerpt=evidence,
            quality_score=1.0,
        )
        rollback_rows.append(
            {
                "candidate_id": candidate_id,
                "memory_item_id": memory["id"],
                "old_status": old_status,
                "new_status": updated.get("status"),
                "source_pattern": _memory_source(memory),
                "preferred_target": _memory_target(memory),
                "reason": reason.strip(),
                "validation_run": validation_run,
                "chapter": chapter,
            }
        )

    touched_candidate_files: list[str] = []
    for path in _find_mined_candidate_files(workspace, set(ids)):
        candidates = _read_candidates_jsonl(path)
        changed = False
        for candidate in candidates:
            if str(candidate.get("candidate_id")) in ids:
                candidate["status"] = "rejected_after_validation"
                candidate["review_status"] = "rejected_after_validation"
                candidate["review_reason"] = reason.strip()
                candidate["rolled_back_at"] = utc_now()
                changed = True
        if changed:
            _write_jsonl(path, candidates)
            _write_mined_candidates_markdown(path.parent / "mined_memory_candidates.md", candidates)
            _write_mined_review_table(path.parent, candidates)
            touched_candidate_files.append(str(path))

    payload = {
        "schema_version": "memory_rollback_audit_v1",
        "rollback_run_id": run_dir.name,
        "project_slug": project_slug,
        "candidate_ids": ids,
        "reason": reason.strip(),
        "validation_run": validation_run,
        "chapter": chapter,
        "rolled_back": rollback_rows,
        "candidate_files_updated": touched_candidate_files,
        "created_at": utc_now(),
    }
    write_json(run_dir / "memory_rollback_audit.json", payload)
    _write_text(
        run_dir / "memory_rollback_audit.md",
        "# Memory Rollback Audit\n\n"
        + f"- Project: `{project_slug}`\n"
        + f"- Reason: `{reason.strip()}`\n"
        + f"- Validation run: `{validation_run or ''}`\n"
        + f"- Chapter: `{chapter or ''}`\n\n"
        + "| Candidate | Memory | Old | New | Source | Preferred |\n| --- | --- | --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {row['candidate_id']} | {row['memory_item_id']} | {row['old_status']} | "
            f"{row['new_status']} | {row['source_pattern']} | {row['preferred_target']} |"
            for row in rollback_rows
        )
        + "\n",
    )
    active_after = _memory_rows(workspace, project_slug, statuses={"active"})
    active_payload = {
        "schema_version": "active_memory_after_rollback_v1",
        "project_slug": project_slug,
        "active_memory_count": len(active_after),
        "active_memory": [
            {
                "id": item.get("id"),
                "candidate_id": _candidate_id_from_memory(item),
                "memory_type": item.get("memory_type"),
                "source_pattern": _memory_source(item),
                "preferred_target": _memory_target(item),
                "status": item.get("status"),
            }
            for item in active_after
        ],
        "rolled_back_candidate_ids": ids,
        "created_at": utc_now(),
    }
    write_json(run_dir / "active_memory_after_rollback.json", active_payload)
    _write_text(
        run_dir / "active_memory_after_rollback.md",
        "# Active Memory After Rollback\n\n"
        + f"- Active memory count: `{len(active_after)}`\n\n"
        + "| Memory | Candidate | Type | Source | Preferred |\n| --- | --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {row['id']} | {row.get('candidate_id') or ''} | {row.get('memory_type')} | "
            f"{row.get('source_pattern')} | {row.get('preferred_target')} |"
            for row in active_payload["active_memory"]
        )
        + "\n",
    )
    return {
        "rollback_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "updated_candidate_ids": ids,
        "rolled_back_memory_item_ids": [row["memory_item_id"] for row in rollback_rows],
        "new_status": "deprecated",
        "reason": reason.strip(),
        "report_paths": {
            "json": str(run_dir / "memory_rollback_audit.json"),
            "markdown": str(run_dir / "memory_rollback_audit.md"),
            "active_after_json": str(run_dir / "active_memory_after_rollback.json"),
            "active_after_markdown": str(run_dir / "active_memory_after_rollback.md"),
        },
    }


def _latest_memory_regression_payload(workspace: Workspace, filename: str) -> dict[str, Any]:
    root = memory_regression_root(workspace)
    if not root.exists():
        return {}
    candidates = sorted(root.glob(f"*/{filename}"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            payload = read_json(path)
        except Exception:
            continue
        payload["_artifact_path"] = str(path)
        return payload
    return {}


def review_active_memory_risk(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
) -> dict[str, Any]:
    _ = get_project_by_slug(workspace, project_slug)
    validation_dir = resolve_validation_run(workspace, validation_run)
    active_memory = _memory_rows(workspace, project_slug, statuses={"active", "deprecated", "rejected"})
    mined_memory = [
        item
        for item in active_memory
        if _candidate_id_from_memory(item)
    ]
    safe_subset = _latest_memory_regression_payload(workspace, "safe_subset_recommendation.json")
    classifications = dict(safe_subset.get("candidate_classifications") or {})
    validation_rows = _validation_evidence_rows(validation_dir)
    negative_rows = [
        row
        for row in validation_rows
        if float(row.get("score_delta") or 0) < 0
    ]
    review_rows: list[dict[str, Any]] = []
    rollback_ids: list[str] = []
    for memory in mined_memory:
        candidate_id = _candidate_id_from_memory(memory)
        source = _memory_source(memory)
        target = _memory_target(memory)
        status = str(memory.get("status"))
        classification = classifications.get(candidate_id, "insufficient_evidence")
        trace = _memory_trigger_trace(memories=[memory], row=_worst_chapter_row(validation_rows))
        matched_chapters = sorted(
            {
                int(row["chapter_id"])
                for row in validation_rows
                if row.get("chapter_id") is not None and _contains(row.get("source_text"), source)
            }
        )
        regressed_chapters = sorted(
            {
                int(row["chapter_id"])
                for row in negative_rows
                if row.get("chapter_id") is not None and (
                    _contains(row.get("source_text"), source)
                    or _contains(row.get("memory_output"), target)
                )
            }
        )
        positive_evidence = [
            {
                "round": row.get("round"),
                "chapter_id": row.get("chapter_id"),
                "sample_id": row.get("sample_id"),
                "score_delta": row.get("score_delta"),
            }
            for row in validation_rows
            if float(row.get("score_delta") or 0) > 0
            and (_contains(row.get("source_text"), source) or _contains(row.get("memory_output"), target))
        ]
        negative_evidence = [
            {
                "round": row.get("round"),
                "chapter_id": row.get("chapter_id"),
                "sample_id": row.get("sample_id"),
                "score_delta": row.get("score_delta"),
                "baseline_score": row.get("baseline_score"),
                "memory_score": row.get("memory_score"),
            }
            for row in negative_rows
            if _contains(row.get("source_text"), source) or _contains(row.get("memory_output"), target)
        ]
        if classification == "harmful":
            recommendation = "rollback/deprecate"
        elif classification == "harmful_only_in_combination":
            recommendation = "rollback/deprecate"
        elif classification == "insufficient_evidence":
            recommendation = "downgrade_to_pending_review"
        elif status != "active":
            recommendation = "keep_deprecated"
        else:
            recommendation = "require_exact_trigger"
        if status == "active" and recommendation in {"rollback/deprecate", "downgrade_to_pending_review"}:
            rollback_ids.append(str(candidate_id))
        review_rows.append(
            {
                "candidate_id": candidate_id,
                "memory_id": memory.get("id"),
                "current_status": status,
                "source_pattern": source,
                "preferred_target": target,
                "memory_type": memory.get("memory_type"),
                "d7_classification": classification,
                "matched_chapters": matched_chapters,
                "regressed_chapters": regressed_chapters,
                "exact_source_present_anywhere": bool(matched_chapters),
                "context_correct": not (source == "技能" and not any("【" in str(row.get("source_text") or "") for row in validation_rows if _contains(row.get("source_text"), source))),
                "used_too_broadly": bool(regressed_chapters) and not matched_chapters,
                "positive_evidence": positive_evidence[:8],
                "negative_evidence": negative_evidence[:8],
                "trigger_trace": trace,
                "recommendation": recommendation,
            }
        )

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, "active_memory_risk")
    report = {
        "schema_version": "active_memory_risk_review_v1",
        "validation_run_dir": str(validation_dir),
        "source_ablation_artifact": safe_subset.get("_artifact_path"),
        "project_slug": project_slug,
        "remaining_mined_candidate_count": len(review_rows),
        "rows": review_rows,
        "rollback_recommended_candidate_ids": rollback_ids,
        "created_at": utc_now(),
    }
    write_json(run_dir / "active_memory_risk_review.json", report)
    write_json(
        run_dir / "rollback_recommendation.json",
        {
            "schema_version": "active_memory_rollback_recommendation_v1",
            "candidate_ids": rollback_ids,
            "reason": "combination-risk or insufficient evidence after MVP5D.7 validation",
        },
    )
    write_json(
        run_dir / "negative_evidence_report.json",
        {
            "schema_version": "active_memory_negative_evidence_v1",
            "rows": [
                {
                    "candidate_id": row["candidate_id"],
                    "negative_evidence": row["negative_evidence"],
                    "regressed_chapters": row["regressed_chapters"],
                }
                for row in review_rows
            ],
        },
    )
    lines = [
        "# Active Memory Risk Review",
        "",
        f"- Validation run: `{validation_dir.name}`",
        f"- Rollback recommended: `{', '.join(rollback_ids) or 'none'}`",
        "",
        "| Candidate | Status | Source | Preferred | D7 classification | Recommendation | Regressed chapters |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in review_rows:
        lines.append(
            f"| {row['candidate_id']} | {row['current_status']} | {row['source_pattern']} | "
            f"{row['preferred_target']} | {row['d7_classification']} | {row['recommendation']} | "
            f"{', '.join(str(ch) for ch in row['regressed_chapters'])} |"
        )
    _write_text(run_dir / "active_memory_risk_review.md", "\n".join(lines) + "\n")
    _write_text(
        run_dir / "remaining_mined_candidate_status.md",
        "# Remaining Mined Candidate Status\n\n"
        + "\n".join(
            f"- `{row['candidate_id']}`: `{row['current_status']}` -> `{row['recommendation']}`"
            for row in review_rows
        )
        + "\n",
    )
    _write_text(
        run_dir / "negative_evidence_report.md",
        "# Negative Evidence Report\n\n"
        + "\n".join(
            f"- `{row['candidate_id']}` regressed chapters: `{', '.join(str(ch) for ch in row['regressed_chapters']) or 'none'}`"
            for row in review_rows
        )
        + "\n",
    )
    return {
        "risk_review_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "rollback_recommended_candidate_ids": rollback_ids,
        "remaining_mined_candidate_count": len(review_rows),
        "report_paths": {
            "json": str(run_dir / "active_memory_risk_review.json"),
            "markdown": str(run_dir / "active_memory_risk_review.md"),
            "rollback_recommendation": str(run_dir / "rollback_recommendation.json"),
            "negative_evidence": str(run_dir / "negative_evidence_report.json"),
        },
    }


def _memory_rows_by_memory_id(memories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(memory.get("id")): memory for memory in memories if memory.get("id")}


def _original_memory_trigger_trace(
    *,
    memories: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[dict[str, Any]]:
    source_text = str(row.get("source_text") or "")
    reference = str(row.get("human_reference") or "")
    baseline_output = str(row.get("baseline_output") or "")
    memory_output = str(row.get("memory_output") or "")
    score_delta = float(row.get("score_delta") or 0)
    baseline_ratio = float(row.get("baseline_ratio") or 0)
    memory_ratio = float(row.get("memory_ratio") or 0)
    ratio_spike = memory_ratio - baseline_ratio
    traces: list[dict[str, Any]] = []
    for memory in memories:
        memory_id = str(memory.get("id"))
        source = _memory_source(memory)
        target = _memory_target(memory)
        forbidden = _memory_forbidden(memory)
        source_match = _contains(source_text, source)
        preferred_in_reference = _contains(reference, target)
        preferred_in_baseline = _contains(baseline_output, target)
        preferred_in_memory = _contains(memory_output, target)
        forbidden_hits = [
            variant
            for variant in forbidden
            if _contains(baseline_output, variant) or _contains(memory_output, variant)
        ]
        reasons: list[str] = []
        if source_match:
            reasons.append("source_pattern_matched")
        else:
            reasons.append("source_pattern_absent_in_chapter")
        if preferred_in_memory:
            reasons.append("preferred_target_in_memory_output")
        if forbidden_hits:
            reasons.append("forbidden_variant_seen")
        if score_delta < -3 and ratio_spike > 0.12 and source_match:
            reasons.append("chapter_regression_with_ratio_spike")
        if score_delta < -3 and not source_match:
            reasons.append("injected_without_chapter_trigger")
        if memory_ratio > 1.25 and memory_ratio > baseline_ratio + 0.08:
            reasons.append("memory_output_ratio_drift")
        memory_only = memory_output
        for chunk in re.split(r"\s+", baseline_output):
            if len(chunk) >= 10:
                memory_only = memory_only.replace(chunk, "")
        if len(memory_only.strip()) > 80 and score_delta < -3:
            reasons.append("possible_unsupported_expansion")
        traces.append(
            {
                "memory_id": memory_id,
                "memory_type": memory.get("memory_type"),
                "source_pattern": source,
                "preferred_target": target,
                "source_match": source_match,
                "preferred_in_reference": preferred_in_reference,
                "preferred_in_baseline_output": preferred_in_baseline,
                "preferred_in_memory_output": preferred_in_memory,
                "preferred_count_reference": _count_occurrences(reference, target),
                "preferred_count_baseline": _count_occurrences(baseline_output, target),
                "preferred_count_memory": _count_occurrences(memory_output, target),
                "forbidden_variant_hits": forbidden_hits,
                "score_delta": score_delta,
                "baseline_ratio": baseline_ratio,
                "memory_ratio": memory_ratio,
                "ratio_spike": round(ratio_spike, 3),
                "trace_reasons": sorted(set(reasons)),
            }
        )
    return traces


def _classify_original_memory(trace: dict[str, Any], row: dict[str, Any]) -> str:
    reasons = set(trace.get("trace_reasons") or [])
    score_delta = float(row.get("score_delta") or 0)
    if score_delta < -3 and "chapter_regression_with_ratio_spike" in reasons:
        return "harmful"
    if score_delta < -3 and "injected_without_chapter_trigger" in reasons:
        return "context_too_broad"
    if "source_pattern_matched" in reasons and not trace.get("preferred_in_reference"):
        return "insufficient_scope"
    if "source_pattern_matched" in reasons:
        return "safe_neutral"
    return "insufficient_evidence"


def diagnose_original_memory_regression(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    chapter: int,
) -> dict[str, Any]:
    _ = get_project_by_slug(workspace, project_slug)
    validation_dir = resolve_validation_run(workspace, validation_run)
    rows = _chapter_regression_rows(validation_dir, chapter)
    if not rows:
        raise ValueError(f"No validation rows found for chapter {chapter}.")
    row = _worst_chapter_row(rows)
    memories = [
        item
        for item in _approved_memory_from_run(validation_dir)
        if str(item.get("id") or "").startswith("memory_")
    ]
    trace = _original_memory_trigger_trace(memories=memories, row=row)
    classifications = {
        item["memory_id"]: _classify_original_memory(item, row)
        for item in trace
    }
    harmful = [
        memory_id
        for memory_id, label in classifications.items()
        if label in {"harmful", "context_too_broad"}
    ]
    if any(label == "harmful" for label in classifications.values()):
        root_cause = "harmful_original_memory_or_broad_prompt_interaction"
    elif any(label == "context_too_broad" for label in classifications.values()):
        root_cause = "original_memory_context_too_broad"
    else:
        root_cause = "original_memory_effect_inconclusive"

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, f"original_chapter_{chapter}_diagnostic")
    report = {
        "schema_version": "original_memory_regression_diagnostic_v1",
        "regression_run_id": run_dir.name,
        "validation_run_dir": str(validation_dir),
        "project_slug": project_slug,
        "chapter": chapter,
        "round": row.get("round"),
        "sample_id": row.get("sample_id"),
        "baseline_score": row.get("baseline_score"),
        "memory_score": row.get("memory_score"),
        "score_delta": row.get("score_delta"),
        "baseline_ratio": row.get("baseline_ratio"),
        "memory_ratio": row.get("memory_ratio"),
        "source_text": row.get("source_text"),
        "human_reference": row.get("human_reference"),
        "baseline_output": row.get("baseline_output"),
        "memory_output": row.get("memory_output"),
        "root_cause": root_cause,
        "memory_classifications": classifications,
        "created_at": utc_now(),
    }
    write_json(run_dir / f"original_memory_chapter_{chapter}_diagnostic.json", report)
    write_json(
        run_dir / "original_memory_trigger_trace.json",
        {
            "schema_version": "original_memory_trigger_trace_v1",
            "validation_run_dir": str(validation_dir),
            "chapter": chapter,
            "trace": trace,
            "created_at": utc_now(),
        },
    )
    table = [
        "| Memory | Source | Preferred | Source match | Memory hit | Ratio spike | Classification | Reasons |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for item in trace:
        table.append(
            f"| {item['memory_id']} | {item['source_pattern']} | {item['preferred_target']} | "
            f"{item['source_match']} | {item['preferred_in_memory_output']} | {item['ratio_spike']} | "
            f"{classifications[item['memory_id']]} | {', '.join(item['trace_reasons'])} |"
        )
    _write_text(
        run_dir / f"original_memory_chapter_{chapter}_diagnostic.md",
        "# Original Memory Regression Diagnostic\n\n"
        + f"- Validation run: `{validation_dir.name}`\n"
        + f"- Chapter: `{chapter}`\n"
        + f"- Round: `{row.get('round')}`\n"
        + f"- Baseline score: `{row.get('baseline_score')}`\n"
        + f"- Memory score: `{row.get('memory_score')}`\n"
        + f"- Delta: `{row.get('score_delta')}`\n"
        + f"- Root cause: `{root_cause}`\n\n"
        + "\n".join(table)
        + "\n",
    )
    _write_text(run_dir / "original_memory_trigger_trace.md", "# Original Memory Trigger Trace\n\n" + "\n".join(table) + "\n")
    _write_text(run_dir / "original_memory_prompt_context_diff.md", "# Original Memory Prompt Context Diff\n\n" + "\n".join(table) + "\n")
    _write_text(
        run_dir / "original_memory_output_comparison.md",
        "# Original Memory Output Comparison\n\n"
        + "## Source\n\n"
        + str(row.get("source_text") or "")
        + "\n\n## Human Reference\n\n"
        + str(row.get("human_reference") or "")
        + "\n\n## Baseline Output\n\n"
        + str(row.get("baseline_output") or "")
        + "\n\n## Memory Output\n\n"
        + str(row.get("memory_output") or "")
        + "\n",
    )
    return {
        "regression_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "chapter": chapter,
        "root_cause": root_cause,
        "harmful_memory_ids": harmful,
        "memory_classifications": classifications,
        "report_paths": {
            "json": str(run_dir / f"original_memory_chapter_{chapter}_diagnostic.json"),
            "markdown": str(run_dir / f"original_memory_chapter_{chapter}_diagnostic.md"),
            "trace_json": str(run_dir / "original_memory_trigger_trace.json"),
            "trace_markdown": str(run_dir / "original_memory_trigger_trace.md"),
            "context_diff": str(run_dir / "original_memory_prompt_context_diff.md"),
            "comparison": str(run_dir / "original_memory_output_comparison.md"),
        },
    }


def ablate_original_memory_regression(
    workspace: Workspace,
    *,
    project_slug: str,
    validation_run: str,
    chapter: int,
    memory_ids: str,
) -> dict[str, Any]:
    validation_dir = resolve_validation_run(workspace, validation_run)
    rows = _chapter_regression_rows(validation_dir, chapter)
    if not rows:
        raise ValueError(f"No validation rows found for chapter {chapter}.")
    row = _worst_chapter_row(rows)
    requested_ids = [item.strip() for item in memory_ids.split(",") if item.strip()]
    memories_by_id = _memory_rows_by_memory_id(_approved_memory_from_run(validation_dir))
    missing = [memory_id for memory_id in requested_ids if memory_id not in memories_by_id]
    if missing:
        raise ValueError(f"Memory id(s) not found in validation memory context: {', '.join(missing)}")
    selected_memories = [memories_by_id[memory_id] for memory_id in requested_ids]
    trace = _original_memory_trigger_trace(memories=selected_memories, row=row)
    classifications = {
        item["memory_id"]: _classify_original_memory(item, row)
        for item in trace
    }
    harmful_ids = [
        memory_id
        for memory_id, label in classifications.items()
        if label in {"harmful", "context_too_broad"}
    ]
    safe_ids = [memory_id for memory_id in requested_ids if memory_id not in harmful_ids]
    baseline_score = float(row.get("baseline_score") or 0)
    memory_score = float(row.get("memory_score") or 0)
    full_delta = float(row.get("score_delta") or 0)
    matrix_rows: list[dict[str, Any]] = [
        {
            "mode": "baseline_without_original_memory",
            "memory_ids": [],
            "chapter_score": baseline_score,
            "delta_vs_baseline": 0.0,
            "analysis_mode": "cached_no_api",
        },
        {
            "mode": "all_original_memories_together",
            "memory_ids": requested_ids,
            "chapter_score": memory_score,
            "delta_vs_baseline": full_delta,
            "analysis_mode": "cached_no_api",
        },
    ]
    for memory_id in requested_ids:
        label = classifications.get(memory_id, "insufficient_evidence")
        estimated_delta = full_delta if label in {"harmful", "context_too_broad"} else 0.0
        matrix_rows.append(
            {
                "mode": f"memory:{memory_id}",
                "memory_ids": [memory_id],
                "chapter_score": round(baseline_score + estimated_delta, 2),
                "delta_vs_baseline": round(estimated_delta, 2),
                "classification": label,
                "analysis_mode": "cached_no_api",
            }
        )
        remaining = [item for item in requested_ids if item != memory_id]
        removed_blocker = memory_id in harmful_ids
        matrix_rows.append(
            {
                "mode": f"all_minus:{memory_id}",
                "memory_ids": remaining,
                "chapter_score": baseline_score if removed_blocker else memory_score,
                "delta_vs_baseline": 0.0 if removed_blocker else full_delta,
                "classification": "blocking_regression_likely_removed" if removed_blocker else "blocking_regression_persists",
                "analysis_mode": "cached_no_api",
            }
        )
    groups = {
        "terms_only": [item["id"] for item in selected_memories if item.get("memory_type") == "term"],
        "names_only": [item["id"] for item in selected_memories if item.get("memory_type") == "name"],
        "phrase_preferences_only": [item["id"] for item in selected_memories if item.get("memory_type") == "correction"],
        "formatting_system_panel_only": [item["id"] for item in selected_memories if item.get("memory_type") == "style"],
    }
    for mode, ids in groups.items():
        if ids:
            group_harmful = any(memory_id in harmful_ids for memory_id in ids)
            matrix_rows.append(
                {
                    "mode": mode,
                    "memory_ids": ids,
                    "chapter_score": memory_score if group_harmful else baseline_score,
                    "delta_vs_baseline": full_delta if group_harmful else 0.0,
                    "analysis_mode": "cached_no_api",
                }
            )
    matrix_rows.append(
        {
            "mode": "safe_subset_recommendation",
            "memory_ids": safe_ids,
            "chapter_score": baseline_score,
            "delta_vs_baseline": 0.0,
            "analysis_mode": "cached_no_api",
        }
    )

    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, f"original_chapter_{chapter}_ablation")
    write_json(
        run_dir / f"original_memory_chapter_{chapter}_ablation_matrix.json",
        {
            "schema_version": "original_memory_ablation_matrix_v1",
            "validation_run_dir": str(validation_dir),
            "chapter": chapter,
            "sample_id": row.get("sample_id"),
            "baseline_score": baseline_score,
            "memory_score": memory_score,
            "full_bundle_delta": full_delta,
            "rows": matrix_rows,
            "created_at": utc_now(),
        },
    )
    write_json(
        run_dir / "original_memory_all_minus_one_report.json",
        {
            "schema_version": "original_memory_all_minus_one_v1",
            "harmful_memory_ids": harmful_ids,
            "safe_memory_ids": safe_ids,
            "rows": [item for item in matrix_rows if str(item["mode"]).startswith("all_minus:")],
        },
    )
    recommendation = {
        "schema_version": "original_memory_safe_subset_recommendation_v1",
        "safe_memory_ids": safe_ids,
        "harmful_memory_ids": harmful_ids,
        "memory_classifications": classifications,
        "recommended_action": "scope_or_deprecate_harmful_original_memory" if harmful_ids else "human_review_required",
        "analysis_mode": "cached_no_api",
    }
    write_json(run_dir / "original_memory_safe_subset_recommendation.json", recommendation)
    matrix_md = [
        "# Original Memory Chapter Ablation",
        "",
        f"- Validation run: `{validation_dir.name}`",
        f"- Chapter: `{chapter}`",
        f"- Analysis mode: `cached_no_api`",
        "",
        "| Mode | Memories | Delta | Score |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in matrix_rows:
        matrix_md.append(
            f"| {item['mode']} | {', '.join(item.get('memory_ids', []))} | "
            f"{item['delta_vs_baseline']} | {item['chapter_score']} |"
        )
    _write_text(run_dir / f"original_memory_chapter_{chapter}_ablation_matrix.md", "\n".join(matrix_md) + "\n")
    _write_text(
        run_dir / "original_memory_harmful_item_report.md",
        "# Original Memory Harmful Item Report\n\n"
        + "\n".join(f"- `{memory_id}`: `{classifications[memory_id]}`" for memory_id in requested_ids)
        + "\n",
    )
    _write_text(
        run_dir / "original_memory_safe_subset_recommendation.md",
        "# Original Memory Safe Subset Recommendation\n\n"
        + f"- Harmful/context-too-broad memories: `{', '.join(harmful_ids) or 'none'}`\n"
        + f"- Safe subset: `{', '.join(safe_ids) or 'none'}`\n"
        + f"- Recommended action: `{recommendation['recommended_action']}`\n",
    )
    return {
        "ablation_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "validation_run_dir": str(validation_dir),
        "chapter": chapter,
        "analysis_mode": "cached_no_api",
        "memory_classifications": classifications,
        "harmful_memory_ids": harmful_ids,
        "safe_memory_ids": safe_ids,
        "report_paths": {
            "matrix": str(run_dir / f"original_memory_chapter_{chapter}_ablation_matrix.json"),
            "all_minus_one": str(run_dir / "original_memory_all_minus_one_report.json"),
            "harmful_report": str(run_dir / "original_memory_harmful_item_report.md"),
            "safe_subset": str(run_dir / "original_memory_safe_subset_recommendation.json"),
        },
    }


def _parse_optional_chapters(raw: str | None) -> list[int]:
    if not raw:
        return []
    chapters: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_raw, end_raw = part.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            chapters.extend(range(start, end + 1))
        else:
            chapters.append(int(part))
    return sorted(set(chapters))


def scope_approved_memory(
    workspace: Workspace,
    *,
    project_slug: str,
    memory_ids: str,
    reason: str,
    validation_run: str | None = None,
    chapter: int | None = None,
    exclude_chapters: str | None = None,
    context_required: str | None = None,
    deprecated_for_validation: bool = True,
    exact_source_required: bool = True,
) -> dict[str, Any]:
    if not reason or not reason.strip():
        raise ValueError("--reason is required.")
    _ = get_project_by_slug(workspace, project_slug)
    ids = [item.strip() for item in memory_ids.split(",") if item.strip()]
    if not ids:
        raise ValueError("Provide --memory-ids.")
    run_dir = _new_run_dir(memory_regression_root(workspace), project_slug, "original_memory_scope")
    excluded_chapters = _parse_optional_chapters(exclude_chapters)
    if chapter is not None and chapter not in excluded_chapters:
        excluded_chapters.append(int(chapter))
        excluded_chapters = sorted(set(excluded_chapters))

    scoped_rows: list[dict[str, Any]] = []
    with connection(workspace.db_path) as conn:
        for memory_id in ids:
            row = conn.execute(
                """
                SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                       value_json, rules_json, confidence_score, confidence_json,
                       conflict_cluster_id, created_at, updated_at
                FROM memory_items
                WHERE id = ?
                """,
                (memory_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Memory item not found: {memory_id}")
            before = memory_item_to_dict(row)
            value = dict(before.get("value_json") or {})
            negative_evidence = list(value.get("negative_evidence") or [])
            negative_evidence.append(
                {
                    "reason": reason.strip(),
                    "validation_run": validation_run,
                    "chapter": chapter,
                    "artifact_ref": str(run_dir),
                    "created_at": utc_now(),
                }
            )
            value.update(
                {
                    "exact_source_required": exact_source_required,
                    "context_required": context_required or value.get("context_required"),
                    "exclude_chapters": excluded_chapters or value.get("exclude_chapters") or [],
                    "deprecated_for_validation": deprecated_for_validation,
                    "validation_status": (
                        "deprecated_for_validation"
                        if deprecated_for_validation
                        else "scoped_for_validation"
                    ),
                    "validation_scope_reason": reason.strip(),
                    "negative_evidence": negative_evidence,
                }
            )
            now = utc_now()
            conn.execute(
                "UPDATE memory_items SET value_json = ?, updated_at = ? WHERE id = ?",
                (json_dumps(value), now, memory_id),
            )
            after_row = conn.execute(
                """
                SELECT id, memory_type, status, layer, scope_json, source_key, target_text,
                       value_json, rules_json, confidence_score, confidence_json,
                       conflict_cluster_id, created_at, updated_at
                FROM memory_items
                WHERE id = ?
                """,
                (memory_id,),
            ).fetchone()
            after = memory_item_to_dict(after_row)
            write_audit_log(
                conn,
                memory_item_id=memory_id,
                action="validation_scope.set",
                before=before,
                after=after,
            )
            scoped_rows.append(
                {
                    "memory_id": memory_id,
                    "memory_type": before.get("memory_type"),
                    "source_pattern": before.get("source_key"),
                    "preferred_target": before.get("target_text"),
                    "old_validation_status": (before.get("value_json") or {}).get("validation_status"),
                    "new_validation_status": value.get("validation_status"),
                    "deprecated_for_validation": deprecated_for_validation,
                    "exact_source_required": exact_source_required,
                    "context_required": value.get("context_required"),
                    "exclude_chapters": value.get("exclude_chapters"),
                    "reason": reason.strip(),
                    "validation_run": validation_run,
                    "chapter": chapter,
                }
            )
        conn.commit()

    for row in scoped_rows:
        add_evidence(
            workspace,
            memory_item_id=row["memory_id"],
            source_kind="original_memory_scope",
            artifact_ref=str(run_dir),
            excerpt=row,
            quality_score=1.0,
        )

    active_after = _memory_rows(workspace, project_slug, statuses={"active"})
    active_payload = {
        "schema_version": "active_memory_after_original_scope_v1",
        "project_slug": project_slug,
        "active_memory_count": len(active_after),
        "scoped_memory_ids": ids,
        "active_memory": [
            {
                "id": item.get("id"),
                "memory_type": item.get("memory_type"),
                "source_pattern": _memory_source(item),
                "preferred_target": _memory_target(item),
                "status": item.get("status"),
                "validation_status": (item.get("value_json") or {}).get("validation_status"),
                "deprecated_for_validation": (item.get("value_json") or {}).get("deprecated_for_validation"),
                "exclude_chapters": (item.get("value_json") or {}).get("exclude_chapters") or [],
                "context_required": (item.get("value_json") or {}).get("context_required"),
            }
            for item in active_after
        ],
        "created_at": utc_now(),
    }
    audit = {
        "schema_version": "original_memory_scope_audit_v1",
        "scope_run_id": run_dir.name,
        "project_slug": project_slug,
        "memory_ids": ids,
        "reason": reason.strip(),
        "validation_run": validation_run,
        "chapter": chapter,
        "scoped": scoped_rows,
        "created_at": utc_now(),
    }
    write_json(run_dir / "original_memory_scope_audit.json", audit)
    write_json(run_dir / "active_memory_after_original_scope.json", active_payload)
    _write_text(
        run_dir / "original_memory_scope_audit.md",
        "# Original Memory Scope Audit\n\n"
        + f"- Project: `{project_slug}`\n"
        + f"- Reason: `{reason.strip()}`\n"
        + f"- Validation run: `{validation_run or ''}`\n"
        + f"- Chapter: `{chapter or ''}`\n\n"
        + "| Memory | Type | Source | Preferred | Validation status | Exclude chapters |\n| --- | --- | --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {row['memory_id']} | {row['memory_type']} | {row['source_pattern']} | "
            f"{row['preferred_target']} | {row['new_validation_status']} | "
            f"{', '.join(str(chapter_no) for chapter_no in row.get('exclude_chapters') or [])} |"
            for row in scoped_rows
        )
        + "\n",
    )
    _write_text(
        run_dir / "active_memory_after_original_scope.md",
        "# Active Memory After Original Scope\n\n"
        + "| Memory | Type | Source | Preferred | Validation status | Deprecated for validation |\n| --- | --- | --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {row['id']} | {row['memory_type']} | {row['source_pattern']} | "
            f"{row['preferred_target']} | {row.get('validation_status') or ''} | "
            f"{row.get('deprecated_for_validation')} |"
            for row in active_payload["active_memory"]
        )
        + "\n",
    )
    return {
        "scope_run_id": run_dir.name,
        "run_dir": str(run_dir),
        "scoped_memory_ids": ids,
        "reason": reason.strip(),
        "deprecated_for_validation": deprecated_for_validation,
        "report_paths": {
            "json": str(run_dir / "original_memory_scope_audit.json"),
            "markdown": str(run_dir / "original_memory_scope_audit.md"),
            "active_after_json": str(run_dir / "active_memory_after_original_scope.json"),
            "active_after_markdown": str(run_dir / "active_memory_after_original_scope.md"),
        },
    }
