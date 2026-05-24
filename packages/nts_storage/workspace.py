from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from nts_storage.database import initialize_database


WORKSPACE_DIRS = [
    "config",
    "artifacts/raw",
    "artifacts/normalized",
    "artifacts/translated",
    "artifacts/manga",
    "artifacts/exports",
    "artifacts/tmp",
    "logs/runs",
    "cache",
    "reviews",
]


DEFAULT_PROVIDERS_YAML = """providers:
  mock:
    type: mock
    base_url: "mock://local"
    api_key_env: "MOCK_API_KEY"
    api_key_optional: true
"""

DEFAULT_ROUTING_YAML = """tasks:
  language_detect:
    primary:
      provider: mock
      model_class: cheap_text
    policy:
      structured_output: true
      max_cost_usd: 0.001
"""


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    path: Path

    @property
    def db_path(self) -> Path:
        return self.path / "nts.db"

    @property
    def config_dir(self) -> Path:
        return self.path / "config"


def init_workspace(path: Path) -> Workspace:
    workspace_path = path.resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    for rel_path in WORKSPACE_DIRS:
        (workspace_path / rel_path).mkdir(parents=True, exist_ok=True)

    providers_path = workspace_path / "config" / "providers.yaml"
    routing_path = workspace_path / "config" / "routing.yaml"
    if not providers_path.exists():
        providers_path.write_text(DEFAULT_PROVIDERS_YAML, encoding="utf-8")
    if not routing_path.exists():
        routing_path.write_text(DEFAULT_ROUTING_YAML, encoding="utf-8")

    initialize_database(workspace_path / "nts.db")
    return Workspace(workspace_path)


def discover_workspace(explicit: Path | None = None) -> Workspace:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env_workspace = os.getenv("NTS_WORKSPACE")
    if env_workspace:
        candidates.append(Path(env_workspace))
    cwd = Path.cwd()
    candidates.append(cwd / "workspace")
    candidates.append(cwd)
    candidates.extend(cwd.parents)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "nts.db").exists():
            return Workspace(resolved)

    if explicit is not None:
        raise WorkspaceError(f"Workspace is not initialized: {explicit}")
    raise WorkspaceError("Workspace is not initialized. Run `nts init --workspace <path>` first.")

