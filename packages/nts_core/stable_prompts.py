from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nts_core.eval_harness import read_json, repo_root
from nts_storage.workspace import Workspace


class StablePromptBlocker(ValueError):
    """Raised when production translation is blocked by missing human approval."""


@dataclass(frozen=True)
class StablePromptRecord:
    prompt_id: str | None
    prompt_version: str | None
    source_eval_run_id: str | None
    language_pair: str | None
    domain: str | None
    quality_summary: dict[str, Any]
    stable_gate_summary: dict[str, Any]
    approval_status: str
    approval_path: str | None
    prompt_text: str
    prompt_path: str
    metadata_path: str
    created_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "source_eval_run_id": self.source_eval_run_id,
            "language_pair": self.language_pair,
            "domain": self.domain,
            "quality_summary": self.quality_summary,
            "stable_gate_summary": self.stable_gate_summary,
            "approval_status": self.approval_status,
            "approval_path": self.approval_path,
            "prompt_text": self.prompt_text,
            "prompt_path": self.prompt_path,
            "metadata_path": self.metadata_path,
            "created_at": self.created_at,
        }


def _extract_prompt_text(raw_prompt: str) -> str:
    match = re.search(r"```(?:text)?\s*(.*?)\s*```", raw_prompt, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else raw_prompt.strip()


def _candidate_dirs(workspace: Workspace | None = None) -> list[Path]:
    roots = []
    if workspace is not None:
        roots.append(workspace.path)
    roots.append(repo_root())
    seen: set[Path] = set()
    dirs: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        dirs.append(resolved / "artifacts" / "evaluations")
        dirs.append(resolved / "config" / "prompts")
    return dirs


def _approval_status(directory: Path) -> tuple[str, Path | None]:
    approval = directory / "stable_prompt_approval.json"
    rejection = directory / "stable_prompt_rejection.json"
    if rejection.exists() and (
        not approval.exists() or rejection.stat().st_mtime >= approval.stat().st_mtime
    ):
        return "rejected", rejection
    if approval.exists():
        try:
            payload = read_json(approval)
        except (OSError, ValueError):
            return "approval_invalid", approval
        if payload.get("decision") == "approved":
            return "approved", approval
        return "approval_invalid", approval
    return "unapproved", None


def _metadata_path(prompt_path: Path) -> Path | None:
    adjacent = prompt_path.with_name("stable_prompt_metadata.json")
    if adjacent.exists():
        return adjacent
    config_adjacent = prompt_path.with_name("stable_prompt_metadata.json")
    return config_adjacent if config_adjacent.exists() else None


def _record_from_prompt(prompt_path: Path) -> StablePromptRecord | None:
    metadata_path = _metadata_path(prompt_path)
    if metadata_path is None:
        return None
    try:
        metadata = read_json(metadata_path)
    except (OSError, ValueError):
        return None
    approval_status, approval_path = _approval_status(prompt_path.parent)
    raw_prompt = prompt_path.read_text(encoding="utf-8")
    quality_summary = {
        "quality_gate": metadata.get("quality_gate"),
        "average_score": metadata.get("average_score"),
        "ratio_summary": metadata.get("ratio_summary"),
        "compression_counts": metadata.get("compression_counts"),
    }
    stable_gate_summary = {
        "per_run_scores": metadata.get("per_run_scores", []),
        "per_sample_scores": metadata.get("per_sample_scores", []),
    }
    return StablePromptRecord(
        prompt_id=metadata.get("prompt_id"),
        prompt_version=metadata.get("prompt_version"),
        source_eval_run_id=metadata.get("source_eval_run_id"),
        language_pair=metadata.get("language_pair") or "zh-vi",
        domain=metadata.get("domain") or "novel",
        quality_summary=quality_summary,
        stable_gate_summary=stable_gate_summary,
        approval_status=approval_status,
        approval_path=str(approval_path) if approval_path else None,
        prompt_text=_extract_prompt_text(raw_prompt),
        prompt_path=str(prompt_path),
        metadata_path=str(metadata_path),
        created_at=metadata.get("created_at"),
    )


def discover_stable_prompts(workspace: Workspace | None = None) -> list[StablePromptRecord]:
    prompts: list[StablePromptRecord] = []
    seen: set[Path] = set()
    for directory in _candidate_dirs(workspace):
        if not directory.exists():
            continue
        prompt_paths = (
            [directory / "stable_prompt.md"]
            if directory.name == "prompts"
            else list(directory.glob("**/stable_prompt.md"))
        )
        for prompt_path in prompt_paths:
            resolved = prompt_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            record = _record_from_prompt(resolved)
            if record is not None:
                prompts.append(record)
    return sorted(
        prompts,
        key=lambda record: (
            record.created_at or "",
            Path(record.metadata_path).stat().st_mtime if Path(record.metadata_path).exists() else 0,
        ),
        reverse=True,
    )


def load_approved_stable_prompt(
    workspace: Workspace | None = None,
    *,
    prompt_id: str | None = None,
) -> StablePromptRecord:
    prompts = discover_stable_prompts(workspace)
    if prompt_id:
        prompts = [prompt for prompt in prompts if prompt.prompt_id == prompt_id]
        if not prompts:
            raise StablePromptBlocker(f"Stable prompt not found for prompt_id: {prompt_id}")
    if not prompts:
        raise StablePromptBlocker(
            "No stable_prompt.md with stable_prompt_metadata.json found. "
            "Run stable validation and human approval first."
        )
    approved = [prompt for prompt in prompts if prompt.approval_status == "approved"]
    if approved:
        return approved[0]
    statuses = sorted({prompt.approval_status for prompt in prompts})
    raise StablePromptBlocker(
        "Stable prompt exists but is not approved for production translation. "
        f"Observed approval status: {', '.join(statuses)}. "
        "Run `nts eval review-stable --run <run> --approve --json` first."
    )
